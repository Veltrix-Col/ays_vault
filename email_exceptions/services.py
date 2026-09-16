from __future__ import annotations
import re
import unicodedata
import hashlib
from dataclasses import dataclass
from email.utils import parseaddr
from html.parser import HTMLParser
from django.db import transaction
from django.conf import settings
from django.utils import timezone
from .models import CaseMessage, EmailAuditEvent, EmailException, EmailExceptionMessage, EmailPolicyProfile, ExceptionCase, InboundEmail

COMMUNICATIONS = "comunicaciones@segurosays.com"
SURA = "aysltda@asesorsura.com"
NOISE = (
    "outlook reaction", "automatic reply", "out of office", "hoja de vida",
    "metricool", "ale luya", "instagram", "tiktok", "newsletter", "cumpleaños",
    "boletín informativo", "boletin informativo", "reunión comercial",
    "reunion comercial", "reunión de seguimiento", "reunion de seguimiento",
)

ACTION_TYPES = frozenset({
    "NONE", "REVIEW", "SEND_DOCUMENTS", "CORRECT_INFORMATION",
    "EXPAND_ATTACHMENT", "FOLLOW_UP", "RESCHEDULE", "LEGALIZE_PAYMENT",
    "REQUEST_INFORMATION", "RECONCILE_PAYMENT",
})

@dataclass(frozen=True)
class Classification:
    organization: str = ""
    family: str = ""
    event_type: str = ""
    exception_reason: str = ""
    confidence: float = 0
    rule_id: str = ""
    policy_profile: str = ""
    message_outcome: str = "UNCLEAR"
    action_required: bool = False
    action_type: str = "NONE"
    scope: str = "UNKNOWN"
    correlation_key: str = ""
    details: dict | None = None

    @property
    def current_message_is_exception(self):
        return self.message_outcome in {"FAILURE", "PENDING_ACTION"} and self.action_required

    @property
    def is_exception(self):
        """Whether the current message is semantically an exception."""
        return self.current_message_is_exception

    @property
    def eligible_for_email_exception(self):
        """Whether the message may become an individual queue item."""
        return self.current_message_is_exception and self.scope == "CASE"


class _EmailHTMLToText(HTMLParser):
    _BLOCK_TAGS = {"address", "article", "aside", "blockquote", "div", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header", "li", "main", "ol", "p", "pre", "section", "table", "tr", "ul"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.ignored_depth = 0
        self.link_href = None
        self.link_text_start = None

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in {"style", "script", "noscript", "template"}:
            self.ignored_depth += 1
            return
        if self.ignored_depth:
            return
        if tag == "br" or tag in self._BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "a":
            self.link_href = dict(attrs).get("href")
            self.link_text_start = len(self.parts)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"style", "script", "noscript", "template"} and self.ignored_depth:
            self.ignored_depth -= 1
            return
        if self.ignored_depth:
            return
        if tag == "a" and self.link_href:
            href = self.link_href.strip()
            if href.lower().startswith(("http://", "https://", "mailto:")):
                link_text = "".join(self.parts[self.link_text_start:]).strip()
                if href not in link_text:
                    self.parts.append(f" ({href})")
            self.link_href = None
            self.link_text_start = None
        if tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.ignored_depth:
            self.parts.append(data)


def normalize_email_body(value):
    parser = _EmailHTMLToText()
    parser.feed(str(value or ""))
    parser.close()
    text = "".join(parser.parts).replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    normalized = []
    blank = False
    for line in lines:
        if line:
            normalized.append(line)
            blank = False
        elif not blank and normalized:
            normalized.append("")
            blank = True
    return "\n".join(normalized).strip()


def _fold(value):
    value = unicodedata.normalize("NFKD", str(value or "").lower())
    return "".join(char for char in value if not unicodedata.combining(char))


def _current_and_historical_text(subject, body):
    """Keep a deterministic current-message slice separate from quoted text."""
    body = str(body or "")
    separator = re.search(
        r"(?:^|\n)\s*(?:-{3,}\s*(?:mensaje anterior|original message|forwarded message)\s*-{3,}|mensaje anterior:|original message:|forwarded message:|de: .*?\benviado:)",
        body,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if separator:
        current, historical = body[:separator.start()], body[separator.start():]
    else:
        lines = body.splitlines()
        current_lines, historical_lines = [], []
        for line in lines:
            if line.lstrip().startswith(">"):
                historical_lines.append(line)
            else:
                current_lines.append(line)
        current, historical = "\n".join(current_lines), "\n".join(historical_lines)
    return _fold(f"{subject}\n{current}"), _fold(historical)


ORGANIZATION_ALIASES = {
    "BEMSA": ("bemsa", "pagosarrendamientos@bemsa.com.co", "seguros mundial"),
    "ZIKLO_SOLAR": ("ziklo", "ziklo solar"),
    "METLIFE": ("metlife",),
    "SURA": ("sura", "asesorsura.com"),
}


def _infer_organization(text):
    return next(
        (name for name, signals in ORGANIZATION_ALIASES.items() if any(signal in text for signal in signals)),
        "",
    )


def _scope(text):
    if re.search(r"\b(listado|lote|consolidado|multiple|múltiples|120\s+p[oó]lizas|varias\s+p[oó]lizas)\b", text):
        return "BATCH"
    if re.search(r"\b(contingencia|plataforma|servicio\s+de\s+emisi[oó]n|indisponible|intermitencia)\b", text):
        return "PLATFORM"
    return "CASE"


def _classification(*, organization, family="", event_type="", reason="", confidence=0,
                    rule_id="", profile="", outcome="UNCLEAR", action_required=False,
                    action_type="NONE", scope="UNKNOWN", correlation_key="", signals=(),
                    current_text="", historical_text=""):
    return Classification(
        organization=organization,
        family=family,
        event_type=event_type,
        exception_reason=reason,
        confidence=confidence,
        rule_id=rule_id,
        policy_profile=profile,
        message_outcome=outcome,
        action_required=action_required,
        action_type=action_type,
        scope=scope,
        correlation_key=correlation_key,
        details={
            "organization": organization,
            "matched_signals": list(signals),
            "current_text": current_text,
            "historical_text_present": bool(historical_text),
        },
    )


def classify_inbound_email(email: InboundEmail) -> Classification:
    source = email.source_mailbox.lower()
    profile = "SURA_V1" if source == SURA else "COMUNICACIONES_V1" if source == COMMUNICATIONS else "REVIEW_ONLY"
    current_text, historical_text = _current_and_historical_text(email.subject, email.body_text)
    sender_text = _fold(f"{email.from_name}\n{email.from_email}")
    text = f"{current_text}\n{sender_text}"
    organization = _infer_organization(f"{text}\n{historical_text}")
    scope = _scope(text)
    correlation = re.search(r"(?:poliza|contrato|periodo)\s*[:#-]?\s*([a-z0-9/-]{3,40})", text)
    correlation_key = correlation.group(1) if correlation else ""

    # Scope is evaluated before event rules so batches/platform incidents never
    # become individual operational exceptions.
    if scope == "BATCH":
        return _classification(organization=organization, profile=profile, outcome="FAILURE", action_required=True, action_type="EXPAND_ATTACHMENT", scope=scope, reason="Mensaje masivo con incidencia; requiere expansión por lote, no gestión individual.", rule_id="SCOPE_BATCH_V631", current_text=current_text, historical_text=historical_text)
    if scope == "PLATFORM":
        return _classification(organization=organization, profile=profile, outcome="FAILURE", action_required=True, action_type="REVIEW", scope=scope, reason="Incidente transversal de plataforma; no se crea excepción individual.", rule_id="SCOPE_PLATFORM_V631", current_text=current_text, historical_text=historical_text)

    # Current success/resolution wins over quoted historical failures.
    success_signals = (
        "procesado correctamente", "aplicado correctamente", "operacion exitosa",
        "solicitud resuelta", "firma remota completada exitosamente", "firma completada",
        "firma exitosa", "firmada exitosamente", "pago legalizado",
        "legalizacion completada", "legalizado correctamente",
        "pago aplicado", "poliza emitida correctamente", "seguro ha sido renovado",
        "fue solucionado", "fue solucionada", "quedo solucionado", "quedo solucionada",
    )
    if any(signal in current_text for signal in success_signals):
        return _classification(organization=organization, profile=profile, outcome="SUCCESS", scope=scope, reason="El mensaje actual confirma resolución o procesamiento exitoso.", rule_id="SUCCESS_CURRENT_V631", signals=tuple(signal for signal in success_signals if signal in current_text), current_text=current_text, historical_text=historical_text)

    failure_rules = (
        ("reembolso", "rechazad", "REEMBOLSO", "REEMBOLSO_RECHAZADO", "REIMBURSEMENT_REJECTED_V631", "Reembolso rechazado; requiere revisión.", "REVIEW"),
        ("inconsistencia", "aporte", "APORTES", "INCONSISTENCIA_PAGO_EMPLEADOR", "CONTRIBUTION_INCONSISTENCY_V631", "Inconsistencia de aportes; requiere gestión.", "CORRECT_INFORMATION"),
        ("operacion poliza nueva", "inconsist", "EMISION_POLIZA", "INCONSISTENCIA", "EMISSION_INCONSISTENCY_V631", "Operación de póliza nueva con inconsistencias; requiere corrección.", "CORRECT_INFORMATION"),
        ("factura no recibida", "", "CARTERA_ARRENDAMIENTO", "SOLICITUD_FACTURA", "INVOICE_MISSING_V631", "Factura no recibida; requiere gestión.", "REQUEST_INFORMATION"),
        ("no hemos recibido las facturas", "", "CARTERA_ARRENDAMIENTO", "SOLICITUD_FACTURA", "INVOICE_MISSING_V631", "Facturas no recibidas; requiere gestión.", "REQUEST_INFORMATION"),
        ("facturas pendientes", "", "CARTERA_ARRENDAMIENTO", "SOLICITUD_FACTURA", "INVOICE_MISSING_V631", "Facturas pendientes; requiere gestión.", "REQUEST_INFORMATION"),
        ("solicitud de factura", "", "CARTERA_ARRENDAMIENTO", "SOLICITUD_FACTURA", "INVOICE_MISSING_V631", "Solicitud de factura; requiere gestión.", "REQUEST_INFORMATION"),
        ("solicitud de facturas", "", "CARTERA_ARRENDAMIENTO", "SOLICITUD_FACTURA", "INVOICE_MISSING_V631", "Solicitud de facturas; requiere gestión.", "REQUEST_INFORMATION"),
        ("falta factura", "", "CARTERA_ARRENDAMIENTO", "SOLICITUD_FACTURA", "INVOICE_MISSING_V631", "Falta una factura; requiere gestión.", "REQUEST_INFORMATION"),
        ("pago duplicado", "", "CARTERA_ARRENDAMIENTO", "PAGO_DOBLE", "PAYMENT_DUPLICATE_V631", "Pago duplicado; requiere conciliación.", "RECONCILE_PAYMENT"),
        ("pago doble", "", "CARTERA_ARRENDAMIENTO", "PAGO_DOBLE", "PAYMENT_DUPLICATE_V631", "Pago doble; requiere conciliación.", "RECONCILE_PAYMENT"),
        ("doble pago", "", "CARTERA_ARRENDAMIENTO", "PAGO_DOBLE", "PAYMENT_DUPLICATE_V631", "Pago doble; requiere conciliación.", "RECONCILE_PAYMENT"),
        ("cobro duplicado", "", "CARTERA_ARRENDAMIENTO", "PAGO_DOBLE", "PAYMENT_DUPLICATE_V631", "Cobro duplicado; requiere conciliación.", "RECONCILE_PAYMENT"),
        ("emision", "inconsist", "EMISION_POLIZA", "INCONSISTENCIA", "EMISSION_INCONSISTENCY_V631", "Emisión con inconsistencia; requiere corrección.", "CORRECT_INFORMATION"),
        ("firma", "rechazad", "FIRMA", "FIRMA_RECHAZADA", "SIGNATURE_FAILURE_V631", "Firma rechazada; requiere seguimiento.", "FOLLOW_UP"),
        ("firma", "error", "FIRMA", "FIRMA_ERROR", "SIGNATURE_FAILURE_V631", "Error de firma; requiere seguimiento.", "FOLLOW_UP"),
        ("pago", "no aplicado", "PAGO", "PAGO_NO_APLICADO", "PAYMENT_NOT_APPLIED_V631", "Pago no aplicado; requiere conciliación.", "RECONCILE_PAYMENT"),
    )
    for first, second, family, event, rule, reason, action_type in failure_rules:
        if first in current_text and (not second or second in current_text):
            signals = tuple(signal for signal in (first, second) if signal)
            return _classification(organization=organization, family=family, event_type=event, profile=profile, outcome="FAILURE", action_required=True, action_type=action_type, scope=scope, reason=reason, confidence=95, rule_id=rule, correlation_key=correlation_key, signals=signals, current_text=current_text, historical_text=historical_text)

    if "cobro" in current_text and any(signal in current_text for signal in ("documento", "documentos", "documentacion")) and any(signal in current_text for signal in ("pendiente", "falta", "solicitud", "revision")):
        return _classification(organization=organization, family="COBRO", event_type="COBRO_DOCUMENTOS", profile=profile, outcome="PENDING_ACTION", action_required=True, action_type="REVIEW", scope=scope, reason="Cobro con documentación pendiente que requiere revisión.", confidence=88 if organization == "METLIFE" else 84, rule_id="COLLECTION_DOCUMENTATION_V631", correlation_key=correlation_key, signals=("cobro", "documentos"), current_text=current_text, historical_text=historical_text)

    pending_rules = (
        (("legalizacion", "legalizar"), "PAGO", "PAGO_PENDIENTE_LEGALIZACION", "PAYMENT_LEGALIZATION_V631", "Pago pendiente de legalización; requiere actuación.", "LEGALIZE_PAYMENT"),
        (("corregir la informacion", "corregir informacion", "informacion inconsistente", "información inconsistente"), "EMISION_POLIZA", "CORRECCION_INFORMACION", "CORRECT_INFORMATION_V631", "Se requiere corregir información para continuar.", "CORRECT_INFORMATION"),
        (("documentacion faltante", "documentacion pendiente", "documentos faltantes", "documentos pendientes", "falta documentacion"), "DOCUMENTACION", "DOCUMENTACION_FALTANTE", "DOCUMENTATION_MISSING_V631", "Falta documentación para continuar la gestión.", "SEND_DOCUMENTS"),
        (("reagendar", "reagendamiento"), "PETICION", "REAGENDAMIENTO", "RESCHEDULE_REQUIRED_V631", "La gestión requiere reagendamiento.", "RESCHEDULE"),
    )
    for signals, family, event, rule, reason, action_type in pending_rules:
        matched = tuple(signal for signal in signals if signal in current_text)
        if matched:
            return _classification(organization=organization, family=family, event_type=event, profile=profile, outcome="PENDING_ACTION", action_required=True, action_type=action_type, scope=scope, reason=reason, confidence=90, rule_id=rule, correlation_key=correlation_key, signals=matched, current_text=current_text, historical_text=historical_text)

    complement_signals = (
        "gestion de complementos", "solicitud de complemento",
        "solicitud de complementos", "complemento requerido",
        "complementos requeridos", "se requiere complemento",
        "se requieren complementos", "informacion adicional requerida",
        "documentacion adicional requerida", "documentos adicionales requeridos",
    )
    matched = tuple(signal for signal in complement_signals if signal in current_text)
    if matched:
        return _classification(organization=organization, family="DOCUMENTACION", event_type="COMPLEMENTO_REQUERIDO", profile=profile, outcome="PENDING_ACTION", action_required=True, action_type="SEND_DOCUMENTS", scope=scope, reason="Se requiere información o documentación adicional para continuar la gestión.", confidence=90, rule_id="COMPLEMENT_REQUIRED_V631", correlation_key=correlation_key, signals=matched, current_text=current_text, historical_text=historical_text)

    noise_signals = (
        "notificacion de actividad en sharefile", "newsletter", "boletin informativo",
        "reunion comercial", "reunion de seguimiento", "automatic reply", "out of office",
        "hoja de vida", "candidatura", "resumen diario de reacciones", "notificacion de actividad",
    )
    if any(signal in current_text for signal in noise_signals) or any(item in text for item in NOISE):
        return _classification(organization=organization, profile=profile, outcome="NOISE", scope=scope, reason="Comunicación automática, informativa o no operativa.", rule_id="COMMUNICATIONS_NOISE_V631", signals=tuple(signal for signal in noise_signals if signal in current_text), current_text=current_text, historical_text=historical_text)

    if "comprobante de pago" in current_text or "comprobante pago" in current_text:
        supporting = ("poliza", "periodo", "arrendamiento", "aseguradora", "bemsa", "seguros mundial")
        matched = tuple(signal for signal in supporting if signal in current_text)
        if matched:
            return _classification(organization=organization, family="CARTERA_ARRENDAMIENTO", event_type="COMPROBANTE_PAGO", profile=profile, outcome="PENDING_ACTION", action_required=True, action_type="REVIEW", scope=scope, reason="Comprobante contextual que requiere revisión operativa.", confidence=96 if organization == "BEMSA" else 91, rule_id="PAYMENT_RECEIPT_CONTEXTUAL_V631", correlation_key=correlation_key, signals=("comprobante de pago", *matched), current_text=current_text, historical_text=historical_text)

    if ("solicitud de emision" in current_text or "solicitud de emisión" in current_text or "emitir poliza" in current_text or "emitir póliza" in current_text) and any(signal in current_text for signal in ("poliza", "póliza", "ziklo")):
        return _classification(organization=organization, family="EMISION_POLIZA", event_type="SOLICITUD_EMISION", profile=profile, outcome="PENDING_ACTION", action_required=True, action_type="REVIEW", scope=scope, reason="Solicitud de emisión de póliza que requiere gestión.", confidence=93 if organization == "ZIKLO_SOLAR" else 88, rule_id="POLICY_ISSUANCE_REQUEST_V631", correlation_key=correlation_key, signals=("solicitud", "emision", "poliza"), current_text=current_text, historical_text=historical_text)

    informational_signals = (
        "adjunto factura", "presentamos el recibo", "recibo del seguro",
        "factura contabilizada", "adjunto documentos", "documentos adjuntos",
        "para su informacion", "consulta sobre cobro",
    )
    if any(signal in current_text for signal in informational_signals):
        return _classification(organization=organization, profile=profile, outcome="INFORMATIONAL", scope=scope, reason="Comunicación informativa sin actuación operativa requerida.", rule_id="INFORMATIONAL_V631", current_text=current_text, historical_text=historical_text)

    return _classification(organization=organization, profile=profile, outcome="UNCLEAR", scope=scope, reason="No existe evidencia suficiente para determinar una excepción operativa.", rule_id="UNCLEAR_V631", current_text=current_text, historical_text=historical_text)

def record_audit(exception, event_type, actor=None, metadata=None, *, using=None):
    manager = EmailAuditEvent.objects.using(using) if using else EmailAuditEvent.objects
    return manager.create(exception=exception, event_type=event_type, actor=actor, metadata=metadata or {})

def canonical_case_key(email, references, *, force_unique=False):
    """Return the stable identity used by live ingestion and replay-shaped cases."""
    from .correlation import STRONG_FIELDS

    parts = [
        f"{field}:{getattr(references, field)}"
        for field in STRONG_FIELDS
        if getattr(references, field, "")
    ]
    if parts:
        base = "|".join(parts)
    elif email.conversation_id:
        digest = hashlib.sha256(str(email.conversation_id).encode("utf-8")).hexdigest()
        base = f"conversation:{digest}"
    else:
        identity = f"{email.source_mailbox}\x00{email.external_message_id}"
        base = f"message:{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"
    if force_unique:
        identity = f"{email.source_mailbox}\x00{email.external_message_id}"
        base = f"{base}|message:{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"
    return base


def _case_retrieval(email, classification, references, *, using=None, retrieval_context=None):
    if retrieval_context is None:
        from .candidate_retrieval_session import CandidateRetrievalSession
        retrieval_context = CandidateRetrievalSession(using=using or "default")
    result = retrieval_context.retrieve(email, functional_references=references, classification=classification)
    seen = set()
    candidates = []
    for case in (*result.candidates, *result.conflict_candidates):
        identity = case.pk
        if identity not in seen:
            seen.add(identity)
            candidates.append(case)
    return tuple(candidates)


def _case_message_role(classification, *, is_new):
    if is_new:
        return CaseMessage.Role.OPENING
    if classification.message_outcome == "SUCCESS":
        return CaseMessage.Role.RESOLUTION
    if classification.message_outcome == "INFORMATIONAL":
        return CaseMessage.Role.CONTEXT
    return CaseMessage.Role.FOLLOW_UP


def _persist_case_message(case, email, classification, decision, *, is_new, using=None):
    confidence = decision.confidence or CaseMessage.CorrelationConfidence.LOW
    manager = CaseMessage.objects if using in (None, "default") else CaseMessage.objects.using(using)
    return manager.get_or_create(
        case=case,
        email=email,
        defaults={
            "role": _case_message_role(classification, is_new=is_new),
            "correlation_method": decision.method,
            "correlation_confidence": confidence,
            "correlation_reason": decision.reason,
        },
    )


def _schedule_candidate_index_update(retrieval_context, case, email, *, case_created, using):
    """Publish persisted evidence to the execution index only after commit."""
    if retrieval_context is None or case is None:
        return

    def update_index(context=retrieval_context, persisted_case=case, persisted_email=email, created=case_created):
        if created:
            context.register_case(persisted_case)
        context.register_message(persisted_case, persisted_email)

    transaction.on_commit(update_index, using=using)


def ingest_payload(payload, *, using="default", retrieval_context=None):
    with transaction.atomic(using=using):
        return _ingest_payload(payload, using=using, retrieval_context=retrieval_context)


def _ingest_payload(payload, *, using="default", retrieval_context=None):
    from django.utils.dateparse import parse_datetime
    sender = payload.get("from") if isinstance(payload.get("from"), dict) else {}
    source, message_id = str(payload.get("source_mailbox") or "").strip().lower(), str(payload.get("message_id") or "").strip()
    manager = InboundEmail.objects.using(using)
    existing = manager.filter(source_mailbox=source, external_message_id=message_id).first()
    if existing:
        link = EmailExceptionMessage.objects.using(using).select_related("exception").filter(email=existing).first()
        return existing, link.exception if link else None, False
    attachments = payload.get("attachments") if isinstance(payload.get("attachments"), list) else []
    max_attachment = int(getattr(settings, "EMAIL_EXCEPTIONS_MAX_ATTACHMENT_BYTES", 10 * 1024 * 1024))
    for attachment in attachments:
        if not isinstance(attachment, dict) or int(attachment.get("size") or 0) < 0 or int(attachment.get("size") or 0) > max_attachment:
            raise ValueError("invalid_attachment")
        if ".." in str(attachment.get("filename") or "") or "/" in str(attachment.get("filename") or "") or "\\" in str(attachment.get("filename") or ""):
            raise ValueError("invalid_attachment_name")
    sender_email = parseaddr(str(sender.get("email") or ""))[1][:254]
    email, created = manager.get_or_create(
        source_mailbox=source,
        external_message_id=message_id,
        defaults={
            "conversation_id": str(payload.get("conversation_id") or "")[:500],
            "received_at": parse_datetime(str(payload.get("received_at") or "")) or timezone.now(),
            "from_name": str(sender.get("name") or "")[:255],
            "from_email": sender_email,
            "from_domain": sender_email.split("@")[-1][:255],
            "to": payload.get("to") if isinstance(payload.get("to"), list) else [],
            "cc": payload.get("cc") if isinstance(payload.get("cc"), list) else [],
            "subject": str(payload.get("subject") or "")[:998],
            "body_text": normalize_email_body(str(payload.get("body_text") or "")[:200000]),
            "has_attachments": bool(payload.get("attachments")),
        },
    )
    if not created:
        link = EmailExceptionMessage.objects.using(using).select_related("exception").filter(email=email).first()
        return email, link.exception if link else None, False
    try:
        result = classify_inbound_email(email)
    except Exception:
        email.classification_status = InboundEmail.CLASSIFICATION_ERROR
        email.classification_reason = "No fue posible completar la clasificación."
        email.processed_at = timezone.now()
        email.save(using=using, update_fields=("classification_status", "classification_rule_id", "classification_reason", "processed_at"))
        return email, None, True
    policy, _ = EmailPolicyProfile.objects.using(using).get_or_create(source_mailbox=source, defaults={"code": result.policy_profile, "version": "1", "status": "REVIEW_ONLY", "active": False})
    if source == COMMUNICATIONS:
        policy.code, policy.version, policy.status, policy.active = "COMUNICACIONES_V1", "1", "ACTIVE", True; policy.save(using=using, update_fields=("code", "version", "status", "active"))
    from .correlation import correlate_email_to_case, extract_functional_references

    references = extract_functional_references(email)
    candidates = _case_retrieval(email, result, references, using=using, retrieval_context=retrieval_context)
    decision = correlate_email_to_case(email, result, candidates)
    high_match = decision.matched and decision.confidence == CaseMessage.CorrelationConfidence.HIGH and decision.case is not None
    eligible = result.eligible_for_email_exception
    case = decision.case if high_match else None
    case_created = False
    if case is None and eligible:
        case, case_created = ExceptionCase.objects.using(using).get_or_create(
            case_key=canonical_case_key(
                email,
                references,
                force_unique=decision.reason == "ambiguous_multiple_candidates",
            ),
            defaults={
                "organization": result.organization,
                "family": result.family,
                "event_type": result.event_type,
                "status": ExceptionCase.Status.OPEN,
                "action_type": result.action_type,
                "scope": ExceptionCase.Scope.CASE,
                "opened_at": email.received_at,
                "last_activity_at": email.received_at,
            },
        )
    if case is not None and (high_match or eligible):
        _persist_case_message(case, email, result, decision, is_new=case_created, using=using)
        if not case_created:
            case.last_activity_at = email.received_at
            case.save(using=using, update_fields=("last_activity_at", "updated_at"))
    if not eligible:
        email.classification_status = InboundEmail.CLASSIFICATION_NO_MATCH
        email.classification_rule_id = ""
        email.classification_reason = result.exception_reason
        email.processed_at = timezone.now()
        email.save(using=using, update_fields=("classification_status", "classification_rule_id", "classification_reason", "processed_at"))
        if case is not None:
            _schedule_candidate_index_update(
                retrieval_context, case, email, case_created=case_created, using=using
            )
        return email, None, True
    email.classification_status = InboundEmail.CLASSIFICATION_EXCEPTION
    email.classification_rule_id = result.rule_id
    email.classification_reason = result.exception_reason
    email.processed_at = timezone.now()
    email.save(using=using, update_fields=("classification_status", "classification_rule_id", "classification_reason", "processed_at"))
    exception = EmailException.objects.using(using).filter(correlation_key=result.correlation_key, case=case, status=EmailException.PENDING).first() if result.correlation_key else None
    if not exception:
        exception = EmailException.objects.using(using).create(primary_email=email, case=case, last_subject=email.subject, organization=result.organization, family=result.family, event_type=result.event_type, exception_reason=result.exception_reason, confidence=result.confidence, policy_profile=policy, rule_id=result.rule_id, correlation_key=result.correlation_key, classification_details=result.details or {})
        record_audit(exception, "EMAIL_RECEIVED", metadata={"email_id": email.pk}, using=using)
        record_audit(exception, "CLASSIFIED", metadata={"rule_id": result.rule_id, "confidence": result.confidence}, using=using)
        record_audit(exception, "EXCEPTION_CREATED", metadata={"rule_id": result.rule_id}, using=using)
    if exception.case_id is None and case is not None:
        exception.case = case
        exception.save(using=using, update_fields=("case",))
    EmailExceptionMessage.objects.using(using).get_or_create(exception=exception, email=email)
    record_audit(exception, "EMAIL_LINKED", metadata={"email_id": email.pk}, using=using)
    _schedule_candidate_index_update(
        retrieval_context, case, email, case_created=case_created, using=using
    )
    return email, exception, True
