from __future__ import annotations
import re
from dataclasses import dataclass
from email.utils import parseaddr
from django.db import transaction
from django.conf import settings
from django.utils import timezone
from .models import EmailAuditEvent, EmailException, EmailExceptionMessage, EmailPolicyProfile, InboundEmail

COMMUNICATIONS = "comunicaciones@segurosays.com"
SURA = "aysltda@asesorsura.com"
NOISE = ("outlook reaction", "automatic reply", "out of office", "hoja de vida", "metricool", "ale luya", "instagram", "tiktok", "newsletter", "cumpleaños")

@dataclass(frozen=True)
class Classification:
    organization: str = ""; family: str = ""; event_type: str = ""; exception_reason: str = ""; confidence: float = 0; rule_id: str = ""; policy_profile: str = ""; is_exception: bool = False; correlation_key: str = ""; details: dict | None = None

def classify_inbound_email(email: InboundEmail) -> Classification:
    source, text = email.source_mailbox.lower(), f"{email.subject}\n{email.body_text}\n{email.from_email}".lower()
    profile = "SURA_V1" if source == SURA else "COMUNICACIONES_V1" if source == COMMUNICATIONS else "REVIEW_ONLY"
    if source == SURA:
        return Classification(policy_profile=profile, is_exception=True, exception_reason="Correo de SURA pendiente de revisión: sus políticas aún no están activas.", rule_id="SURA_REVIEW_ONLY", details={"review_only": True})
    if any(item in text for item in NOISE):
        return Classification(policy_profile=profile, rule_id="COMMUNICATIONS_NOISE_V1", confidence=99, details={"noise": True})
    rules = [
        ("BEMSA", "CARTERA_ARRENDAMIENTO", "PAGO_DOBLE", ("pago doble", "doble pago"), "Se detectó un posible pago doble que requiere gestión.", "BEMSA_PAGO_DOBLE_V1", 94),
        ("BEMSA", "CARTERA_ARRENDAMIENTO", "COMPROBANTE_PAGO", ("comprobante de pago", "comprobante pago"), "Se recibió un comprobante de pago que requiere revisión.", "BEMSA_COMPROBANTE_PAGO_V1", 96),
        ("BEMSA", "CARTERA_ARRENDAMIENTO", "SOLICITUD_FACTURA", ("solicitud de factura", "factura"), "Se detectó una solicitud de factura asociada a una póliza.", "BEMSA_SOLICITUD_FACTURA_V1", 90),
        ("ZIKLO_SOLAR", "EMISION_POLIZA", "SOLICITUD_EMISION", ("solicitud de emisión", "emitir póliza"), "Se recibió una solicitud de emisión de póliza.", "ZIKLO_SOLICITUD_EMISION_V1", 91),
        ("METLIFE", "", "COBRO_DOCUMENTOS", ("cobro", "documentos"), "Se recibió documentación relacionada con un cobro que requiere revisión.", "METLIFE_COBRO_DOCUMENTOS_V1", 82),
        ("SURA", "APORTES", "INCONSISTENCIA_PAGO_EMPLEADOR", ("inconsistencia", "aporte"), "Se detectó una inconsistencia de aportes.", "SURA_APORTES_V1", 88),
    ]
    for org, family, event, needles, reason, rule, confidence in rules:
        if any(needle in text for needle in needles):
            match = re.search(r"(?:póliza|poliza|contrato|periodo|período)\s*[:#-]?\s*([a-z0-9/-]{3,40})", text)
            return Classification(org, family, event, reason, confidence, rule, profile, True, match.group(1) if match else "", {"matched_terms": needles})
    return Classification(policy_profile=profile, is_exception=True, exception_reason="Correo recibido sin una regla concluyente; requiere revisión humana.", confidence=35, rule_id="COMMUNICATIONS_REVIEW_V1", details={"review": True})

def record_audit(exception, event_type, actor=None, metadata=None):
    return EmailAuditEvent.objects.create(exception=exception, event_type=event_type, actor=actor, metadata=metadata or {})

@transaction.atomic
def ingest_payload(payload):
    from django.utils.dateparse import parse_datetime
    sender = payload.get("from") if isinstance(payload.get("from"), dict) else {}
    source, message_id = str(payload.get("source_mailbox") or "").strip().lower(), str(payload.get("message_id") or "").strip()
    existing = InboundEmail.objects.filter(source_mailbox=source, external_message_id=message_id).first()
    if existing:
        link = existing.exception_links.select_related("exception").first()
        return existing, link.exception if link else None, False
    attachments = payload.get("attachments") if isinstance(payload.get("attachments"), list) else []
    max_attachment = int(getattr(settings, "EMAIL_EXCEPTIONS_MAX_ATTACHMENT_BYTES", 10 * 1024 * 1024))
    for attachment in attachments:
        if not isinstance(attachment, dict) or int(attachment.get("size") or 0) < 0 or int(attachment.get("size") or 0) > max_attachment:
            raise ValueError("invalid_attachment")
        if ".." in str(attachment.get("filename") or "") or "/" in str(attachment.get("filename") or "") or "\\" in str(attachment.get("filename") or ""):
            raise ValueError("invalid_attachment_name")
    sender_email = parseaddr(str(sender.get("email") or ""))[1][:254]
    email = InboundEmail.objects.create(source_mailbox=source, external_message_id=message_id, conversation_id=str(payload.get("conversation_id") or "")[:500], received_at=parse_datetime(str(payload.get("received_at") or "")) or timezone.now(), from_name=str(sender.get("name") or "")[:255], from_email=sender_email, from_domain=sender_email.split("@")[-1][:255], to=payload.get("to") if isinstance(payload.get("to"), list) else [], cc=payload.get("cc") if isinstance(payload.get("cc"), list) else [], subject=str(payload.get("subject") or "")[:998], body_text=str(payload.get("body_text") or "")[:200000], has_attachments=bool(payload.get("attachments")))
    result = classify_inbound_email(email)
    policy, _ = EmailPolicyProfile.objects.get_or_create(source_mailbox=source, defaults={"code": result.policy_profile, "version": "1", "status": "REVIEW_ONLY", "active": False})
    if source == COMMUNICATIONS:
        policy.code, policy.version, policy.status, policy.active = "COMUNICACIONES_V1", "1", "ACTIVE", True; policy.save(update_fields=("code", "version", "status", "active"))
    if not result.is_exception: return email, None, True
    exception = EmailException.objects.filter(correlation_key=result.correlation_key, status=EmailException.PENDING).first() if result.correlation_key else None
    if not exception:
        exception = EmailException.objects.create(primary_email=email, last_subject=email.subject, organization=result.organization, family=result.family, event_type=result.event_type, exception_reason=result.exception_reason, confidence=result.confidence, policy_profile=policy, rule_id=result.rule_id, correlation_key=result.correlation_key, classification_details=result.details or {})
        record_audit(exception, "EMAIL_RECEIVED", metadata={"email_id": email.pk})
        record_audit(exception, "CLASSIFIED", metadata={"rule_id": result.rule_id, "confidence": result.confidence})
        record_audit(exception, "EXCEPTION_CREATED", metadata={"rule_id": result.rule_id})
    EmailExceptionMessage.objects.get_or_create(exception=exception, email=email)
    record_audit(exception, "EMAIL_LINKED", metadata={"email_id": email.pk})
    return email, exception, True
