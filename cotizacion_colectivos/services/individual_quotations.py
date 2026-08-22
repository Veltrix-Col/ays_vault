from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from vault.crypto import decrypt, encrypt

from ..models import (
    AdjuntoCotizacionIndividual,
    CotizacionIndividual,
    NotificacionCotizacionIndividual,
)
from .task_publisher import ColectivosTaskPayload, enqueue_task, publish_task_outbox
from .search import PersonSearchService
from .person_contract import PersonCandidate, contact_missing_fields
from ..quotation_forms.catalog import get_policy_branch_schema
from ..quotation_forms.security import sign_policy_context


logger = logging.getLogger("cotizacion_colectivos")


def _normalized(value: object) -> str:
    import unicodedata
    return "".join(
        character for character in unicodedata.normalize("NFKD", str(value or "").casefold())
        if not unicodedata.combining(character)
    )


def _date_input_value(value: object) -> str:
    """Normalize supported Zoho date representations for an HTML date input."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return raw
    if re.match(r"^\d{4}-\d{2}-\d{2}(?:T|\s)", raw):
        return raw[:10]
    match = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", raw)
    if match:
        day, month, year = match.groups()
        return f"{year}-{month}-{day}"
    return raw


@dataclass(frozen=True)
class AffiliateOption:
    key: str
    label: str
    role: str
    id_type: str = ""
    masked_document: str = ""


def affiliate_options(members) -> tuple[AffiliateOption, ...]:
    """Return deduplicated HMAC-backed principals; names are display only."""

    options: dict[str, AffiliateOption] = {}
    for member in members:
        candidates = (
            (
                member.associate_key,
                member.associate_name,
                "Afiliado",
                member.associate_id_type,
                member.associate_masked_document,
            ),
            (
                member.insured_key,
                member.insured_name,
                "Asegurado",
                member.insured_id_type,
                member.insured_masked_document,
            ),
        )
        for key, label, role, id_type, masked_document in candidates:
            key = str(key or "")
            if key and key not in options:
                options[key] = AffiliateOption(
                    key=key,
                    label=str(label or "Información protegida"),
                    role=role,
                    id_type=str(id_type or ""),
                    masked_document=str(masked_document or ""),
                )
        if member.associate_key:
            # A confirmed affiliate is the principal for this relationship.
            continue
    affiliates = tuple(item for item in options.values() if item.role == "Afiliado")
    return affiliates or tuple(options.values())


def build_policy_context(*, policy_token, detail, members, affiliate_key, creator_id):
    schema = get_policy_branch_schema(detail.branch_code, detail.branch_name)
    options = {item.key: item for item in affiliate_options(members)}
    normalized_key = str(affiliate_key or "")
    selected = options.get(normalized_key)
    if normalized_key and selected is None:
        raise ValidationError("Seleccione un afiliado válido para esta póliza.")

    matching = next((
        member for member in members
        if selected and selected.key in {member.associate_key, member.insured_key}
    ), None) if selected else None
    is_associate = bool(selected and matching and matching.associate_key == selected.key)
    requester_name = (
        matching.associate_name if is_associate and matching else
        matching.insured_name if matching else selected.label if selected else ""
    )
    requester_id_type = (
        matching.associate_id_type if is_associate and matching else
        matching.insured_id_type if matching else selected.id_type if selected else ""
    )
    requester_document = (
        matching.associate_document if is_associate and matching else
        matching.insured_document if matching else ""
    )
    values = {
        "first_name": str(
            (matching.associate_first_name if is_associate and matching else matching.first_name if matching else "") or ""
        ),
        "last_name": str(
            (matching.associate_last_name if is_associate and matching else matching.last_name if matching else "") or ""
        ),
        "requester_name": str(requester_name or (selected.label if selected else "")),
        "requester_id_type": str(requester_id_type or (selected.id_type if selected else "")),
        "requester_document": str(requester_document or ""),
        "requester_email": str(getattr(matching, "email", "") or ""),
        "requester_phone": str(
            getattr(matching, "mobile", "") or getattr(matching, "phone", "") or ""
        ),
        "requester_birth_date": _date_input_value(
            matching.associate_birth_date if is_associate and matching else matching.birth_date if matching else ""
        ),
        "collective_context": str(detail.holder or detail.source_name or ""),
    }
    payload = {
        "context_version": 1,
        "policy_token": str(policy_token),
        "source_kind": str(detail.source_kind or "company"),
        "affiliate_key": selected.key if selected else "",
        "affiliate_role": selected.role if selected else "Nuevo afiliado",
        "branch_slug": schema.slug,
        "schema_version": schema.version,
        "creator_id": int(creator_id),
        "policy_label": str(
            detail.full_reference or detail.masked_reference or "Póliza colectiva"
        ),
        "branch_name": str(detail.branch_name),
        "affiliate_label": selected.label if selected else "Nuevo afiliado",
        **values,
    }
    fund_evidence = " ".join((
        str(detail.holder or ""), str(detail.source_name or ""),
        str(values.get("collective_context") or ""),
    ))
    normalized_fund = _normalized(fund_evidence)
    payload["requires_declared_company"] = (
        "fonconstruimos" in normalized_fund
        or "fondo de empleados construimos suenos" in normalized_fund
    )
    payload["locked_fields"] = tuple(key for key, value in values.items() if value)
    return schema, sign_policy_context(payload), payload


MIME_BY_EXTENSION = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}


def _detect(extension: str, header: bytes) -> str:
    if extension == ".pdf" and header.startswith(b"%PDF-"):
        return MIME_BY_EXTENSION[extension]
    if extension in {".jpg", ".jpeg"} and header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if extension == ".png" and header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    return ""


def validate_attachments(uploaded_files) -> tuple[dict, ...]:
    if len(uploaded_files) > 10:
        raise ValidationError("Puede adjuntar máximo 10 archivos.")
    total = 0
    validated = []
    for uploaded in uploaded_files:
        name = Path(uploaded.name or "").name
        extension = Path(name).suffix.casefold()
        if extension not in MIME_BY_EXTENSION or name.count(".") > 1:
            raise ValidationError("Uno de los archivos tiene un tipo no permitido.")
        size = int(getattr(uploaded, "size", 0))
        total += size
        if size <= 0 or size > settings.COLECTIVOS_ATTACHMENT_MAX_BYTES:
            raise ValidationError("Uno de los archivos supera el tamaño permitido.")
        header = uploaded.read(16)
        uploaded.seek(0)
        detected = _detect(extension, header)
        declared = str(getattr(uploaded, "content_type", ""))
        if not detected or (declared and declared not in {detected, "application/octet-stream"}):
            raise ValidationError("El contenido de uno de los archivos no coincide con su tipo.")
        validated.append({"uploaded": uploaded, "extension": extension, "mime": detected, "size": size, "name": name[:80]})
    if total > settings.COLECTIVOS_ATTACHMENT_TOTAL_BYTES:
        raise ValidationError("Los archivos superan el límite total permitido.")
    return tuple(validated)


def _store_individual_file(*, quotation, item, owner_role="legacy", owner_key="legacy", document_type="support_document", field_key="", risk_key=""):
    """Encrypt and persist one file with an explicit local functional owner."""
    uploaded = item["uploaded"]
    content = uploaded.read()
    uploaded.seek(0)
    encrypted = encrypt(base64.b64encode(content).decode()).encode()
    internal_name = f"{secrets.token_hex(32)}.enc"
    root = (Path(settings.COLECTIVOS_PRIVATE_ROOT) / "individual_quotations").resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = (root / internal_name).resolve()
    if root not in target.parents:
        raise ValidationError("La ruta de almacenamiento no es válida.")
    temporary = target.with_suffix(".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(encrypted)
        os.replace(temporary, target)
        return AdjuntoCotizacionIndividual.objects.create(
            quotation=quotation,
            safe_original_name=str(item.get("name") or f"soporte{item['extension']}")[:80],
            internal_name=internal_name,
            extension=item["extension"],
            detected_mime=item["mime"],
            size=item["size"],
            checksum=hashlib.sha256(content).hexdigest(),
            stored_path=internal_name,
            category=document_type[:32].upper(),
            safe_metadata={
                "encrypted": True,
                "antivirus": "not_configured",
                "owner_type": "risk" if owner_role == "risk" else "contact" if owner_role in {"affiliate", "insured"} else "legacy",
                "owner_role": str(owner_role)[:24],
                "owner_key": str(owner_key)[:80],
                "document_type": str(document_type)[:32],
                "field_key": str(field_key or document_type)[:64],
                **({"risk_key": str(risk_key)[:80]} if risk_key else {}),
            },
        ), target
    except Exception:
        temporary.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise


@transaction.atomic
def create_individual_quotation(*, schema, cleaned_data, actor, context=None):
    files = validate_attachments(cleaned_data.get("attachments") or [])
    entity_files = dict(cleaned_data.get("entity_attachments") or {})
    normalized_items = cleaned_data["normalized_items"]
    allowed_owners = {"affiliate"}
    row_owners = {}
    for group_key, rows in normalized_items.items():
        for index, row in enumerate(rows or ()):
            if not isinstance(row, dict):
                continue
            explicit_entity_key = str(row.get("entity_key") or "").strip()
            entity_key = explicit_entity_key or f"{group_key}-{index}"
            if not explicit_entity_key:
                fallback_keys = {entity_key}
                if group_key == "vehicles" and not row.get("insured_same_as_requester"):
                    fallback_keys.add(f"{entity_key}-insured")
                if fallback_keys.intersection(entity_files):
                    raise ValidationError("El documento requiere una clave estable de entidad.")
            same_person = bool(row.get("is_requester")) if group_key == "people" else False
            row_owners[entity_key] = (
                "risk" if group_key == "vehicles" else "insured",
                # Canonical contract for new Mobility vehicle documents.  The
                # publisher accepts the legacy value explicitly for historical
                # rows, but new captures must use the Zoho-facing name.
                "vehicle_registration" if group_key == "vehicles" else "identity_document",
                same_person,
            )
            if group_key == "vehicles" or not same_person:
                allowed_owners.add(entity_key)
            if group_key == "vehicles" and not row.get("insured_same_as_requester"):
                row_owners[f"{entity_key}-insured"] = ("insured", "identity_document", False)
                allowed_owners.add(f"{entity_key}-insured")
    unknown = set(entity_files) - allowed_owners
    if unknown:
        raise ValidationError("El propietario del documento no corresponde a esta cotización.")
    if "affiliate" in entity_files and (context or {}).get("affiliate_key"):
        raise ValidationError("El afiliado precargado no requiere un documento adicional.")
    captured_fields = {field.key: cleaned_data.get(field.key, "") for field in schema.fields}
    # Mantener una copia de lectura para expedientes históricos; el formulario
    # nuevo ya no muestra este nombre redundante.
    if context and context.get("requester_name"):
        captured_fields["requester_name"] = str(context["requester_name"])[:180]
    if (context or {}).get("requires_declared_company"):
        captured_fields["declared_company"] = cleaned_data.get("declared_company", "")
    public_payload = {
        "schema": schema.slug,
        "schema_version": schema.version,
        "fields": captured_fields,
        "groups": cleaned_data["normalized_items"],
        "context": context or {},
    }
    serialized = json.dumps(public_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    protected = encrypt(serialized)
    context_material = ":".join((
        str((context or {}).get("policy_token") or (context or {}).get("entity_token") or ""),
        str((context or {}).get("affiliate_key") or ""),
    ))
    context_hash = hashlib.sha256(context_material.encode()).hexdigest() if context else ""
    task_context = context or {}
    quotation = CotizacionIndividual.objects.create(
        branch_code=schema.code,
        branch_slug=schema.slug,
        schema_version=schema.version,
        encrypted_payload=protected,
        payload_checksum=hashlib.sha256(protected.encode()).hexdigest(),
        context_hash=context_hash,
        item_count=sum(len(items) for items in normalized_items.values()),
        attachment_count=len(files) + len(entity_files),
        created_by=actor,
        safe_metadata={
            "task_responsible": str(task_context.get("task_responsible") or "")[:120],
            "task_responsible_display": str(task_context.get("task_responsible_display") or "")[:120],
            "task_responsible_email": str(task_context.get("task_responsible_email") or "")[:254],
            "task_responsible_email_status": "resolved" if task_context.get("task_responsible_email") else "pending",
        },
    )
    task_fields = captured_fields
    root = (Path(settings.COLECTIVOS_PRIVATE_ROOT) / "individual_quotations").resolve()
    root.mkdir(parents=True, exist_ok=True)
    created_paths = []
    try:
        for item in files:
            _, target = _store_individual_file(quotation=quotation, item=item)
            created_paths.append(target)
        for owner_key, uploaded in entity_files.items():
            validated = validate_attachments((uploaded,))
            role, document_type, duplicate = ("affiliate", "identity_document", False) if owner_key == "affiliate" else row_owners[owner_key]
            if duplicate:
                continue
            _, target = _store_individual_file(
                quotation=quotation,
                item=validated[0],
                owner_role=role,
                owner_key=owner_key,
                document_type=document_type,
                field_key=document_type,
                risk_key=owner_key if role == "risk" else "",
            )
            created_paths.append(target)
        if actor is not None:
            NotificacionCotizacionIndividual.objects.get_or_create(
                user=actor,
                deduplication_key=f"individual:{quotation.public_id}",
                defaults={
                    "quotation": quotation,
                    "message": (
                        "El cliente respondió la cotización individual de "
                        f"{str((context or {}).get('policy_label') or 'una póliza colectiva')[:120]}."
                    ),
                },
            )
    except Exception:
        for path in created_paths:
            path.unlink(missing_ok=True)
        raise
    outbox = enqueue_task(
        source=quotation,
        payload=ColectivosTaskPayload(
            request_kind="COTIZACION",
            source_kind="quotation",
            policy_context=str(task_context.get("policy_label") or ""),
            branch_code=str(schema.code),
            local_reference=str(quotation.public_id),
            has_attachments=bool(files or entity_files),
            subject=" · ".join(filter(None, (
                "Cotización", str(task_context.get("branch_name") or schema.name),
                str(task_context.get("affiliate_label") or task_fields.get("nombre") or task_fields.get("name") or ""),
                str(task_fields.get("placa") or task_fields.get("plate") or ""),
            ))),
            area=str(task_context.get("task_area") or ""),
            observations=_individual_task_observations(task_context, task_fields, cleaned_data.get("normalized_items") or {}),
            responsible=str(task_context.get("task_responsible") or ""),
            responsible_email=str(task_context.get("task_responsible_email") or ""),
            requested_date=str(quotation.submitted_at.date() if quotation.submitted_at else timezone.localdate()),
        ),
        event_version=1,
    )
    # La respuesta siempre queda persistida. Una Task de Cotización no se
    # publica con un contrato incompleto: el analista podrá resolver luego el
    # responsable/correo desde el expediente y publicar de forma controlada.
    if not (str(task_context.get("task_responsible") or "").strip() and
            str(task_context.get("task_responsible_email") or "").strip()):
        outbox.safe_error_code = "RESPONSIBLE_EMAIL_PENDING"
        outbox.save(update_fields=("safe_error_code", "updated_at"))
    else:
        transaction.on_commit(lambda outbox_id=outbox.pk: publish_task_outbox(outbox_id))
    return quotation


def _individual_task_observations(context, fields, groups) -> str:
    branch = str(context.get("branch_name") or "ramo no informado").replace("/ Autos", "")
    lines = ["Solicitud de cotización individual", f"Ramo: {branch}"]
    collective = str(fields.get("collective_context") or context.get("collective_context") or "").strip()
    if collective:
        lines.append(f"Colectiva: {collective}")
    first = str(fields.get("first_name") or "").strip()
    last = str(fields.get("last_name") or "").strip()
    if first or last:
        lines.extend(("", "Solicitante:", f"Nombre: {' '.join(filter(None, (first, last)))}"))
    for key, label in (("requester_id_type", "Tipo de identificación"), ("requester_document", "Documento"),
                       ("requester_birth_date", "Fecha de nacimiento"), ("requester_email", "Correo"),
                       ("requester_phone", "Teléfono")):
        value = str(fields.get(key) or "").strip()
        if value:
            lines.append(f"{label}: {value}")
    labels = {
        "first_name": "Nombres", "last_name": "Apellidos", "id_type": "Tipo de identificación",
        "birth_date": "Fecha de nacimiento", "email": "Correo electrónico", "phone": "Teléfono",
        "zero_km": "Cero kilómetros", "plate": "Placa", "placa": "Placa", "brand": "Marca",
        "line": "Referencia", "model": "Modelo", "city": "Ciudad", "use": "Uso",
        "armored": "Blindado", "currently_insured": "Actualmente asegurado", "insured_name": "Asegurado",
        "insured_id_type": "Tipo de identificación del asegurado", "insured_document": "Documento del asegurado",
        "insured_is_different": "Asegurado diferente", "insured_same_as_requester": "El asegurado es el mismo solicitante",
        "is_requester": "Esta persona es el solicitante", "displacement": "Cilindraje", "name": "Nombre",
        "document": "Documento", "gender": "Género", "relationship": "Parentesco o relación",
        "employment_relationship": "Vínculo con el fondo", "currently_health_insured": "Cobertura de salud vigente",
        "current_health_insurer": "Aseguradora actual", "current_health_policy_end": "Fin de cobertura actual",
        "plan_interest": "Plan o interés", "use_requester": "Usa datos del solicitante",
    }
    technical = {"public_id", "token", "otp", "hash", "id", "source_id", "creator_id"}
    group_labels = {"vehicles": "Vehículo", "people": "Persona", "insured": "Persona"}
    for group_key, rows in groups.items() if isinstance(groups, dict) else ():
        for index, row in enumerate(rows if isinstance(rows, list) else (), 1):
            if not isinstance(row, dict):
                continue
            lines.extend(("", f"{group_labels.get(group_key, 'Elemento')} {index}:"))
            for key, value in row.items():
                label = labels.get(key)
                if key not in technical and label and str(value or "").strip():
                    lines.append(f"{label}: {value}")
    return "\n".join(lines)[:2000]


@transaction.atomic
def accept_individual_quotation(*, quotation: CotizacionIndividual, actor) -> CotizacionIndividual:
    locked = CotizacionIndividual.objects.select_for_update().get(pk=quotation.pk)
    metadata = dict(locked.safe_metadata or {})
    acceptance = dict(metadata.get("acceptance") or {})
    if acceptance.get("status") != "accepted":
        acceptance.update({
            "status": "accepted",
            "accepted_at": timezone.now().isoformat(),
            "accepted_by": int(actor.pk) if actor is not None else None,
        })
        metadata["acceptance"] = acceptance
        locked.safe_metadata = metadata
        locked.save(update_fields=("safe_metadata",))
    return locked


def update_quotation_responsible(*, quotation: CotizacionIndividual, option, email: str) -> CotizacionIndividual:
    """Update only the pending Task contract, never the client's response."""
    email = str(email or "").strip()
    if email and ("@" not in email or len(email) > 254):
        raise ValidationError("El correo del responsable no es válido.")
    with transaction.atomic():
        locked = CotizacionIndividual.objects.select_for_update().get(pk=quotation.pk)
        metadata = dict(locked.safe_metadata or {})
        metadata["task_responsible"] = str(getattr(option, "actual_value", "") or "").strip()
        metadata["task_responsible_display"] = str(getattr(option, "display_value", "") or "").strip()
        metadata["task_responsible_email"] = email
        metadata["task_responsible_email_status"] = "resolved" if email else "pending"
        locked.safe_metadata = metadata
        locked.save(update_fields=("safe_metadata",))
        outbox = locked.task_outbox.filter(event_kind="COTIZACION").order_by("-pk").first()
        responsible_block = outbox is not None and outbox.safe_error_code == "RESPONSIBLE_EMAIL_PENDING"
        if outbox is not None and (
            outbox.status == outbox.Status.PENDING
            or (outbox.status == outbox.Status.BLOCKED and responsible_block)
        ):
            record = json.loads(decrypt(outbox.encrypted_payload))
            record["Responsable"] = metadata["task_responsible"]
            record["Correo_responsable"] = email
            serialized = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            outbox.encrypted_payload = encrypt(serialized)
            outbox.payload_checksum = hashlib.sha256(serialized.encode()).hexdigest()
            outbox.status = outbox.Status.PENDING
            outbox.safe_error_code = "" if email else "RESPONSIBLE_EMAIL_PENDING"
            outbox.save(update_fields=("encrypted_payload", "payload_checksum", "status", "safe_error_code", "updated_at"))
        logger.info(
            "individual_responsible_updated quotation_id=%s outbox_existing=%s email_resolved=%s",
            str(locked.public_id), bool(outbox), bool(email),
        )
        return locked


def resolve_accepted_person(*, quotation: CotizacionIndividual, person_service=None) -> dict[str, object]:
    """Resolve each explicit document candidate without assuming one person per quote."""
    payload = json.loads(decrypt(quotation.encrypted_payload))
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    schema_slug = str(payload.get("schema") or quotation.branch_slug or "").strip().casefold()
    candidates = []
    candidate_data = {}
    requester_document = str(
        fields.get("requester_document") or context.get("requester_document")
        or fields.get("N_mero_de_ID") or ""
    ).strip()
    requester_data = {
        "label": str(
            " ".join(filter(None, (
                fields.get("first_name") or fields.get("First_Name"),
                fields.get("last_name") or fields.get("Last_Name"),
            )))
            or context.get("requester_name") or fields.get("requester_name") or "Solicitante"
        ),
        "First_Name": fields.get("first_name") or fields.get("First_Name") or fields.get("requester_first_name") or context.get("first_name") or "",
        "Last_Name": fields.get("last_name") or fields.get("Last_Name") or fields.get("requester_last_name") or context.get("last_name") or "",
        "Tipo_ID": fields.get("requester_id_type") or fields.get("Tipo_ID") or context.get("requester_id_type") or context.get("Tipo_ID") or "",
        "N_mero_de_ID": requester_document,
        "Date_of_Birth": fields.get("requester_birth_date") or fields.get("Date_of_Birth") or context.get("requester_birth_date") or "",
        "Email": fields.get("requester_email") or fields.get("Email") or context.get("requester_email") or "",
        "Mobile": fields.get("requester_phone") or fields.get("Mobile") or fields.get("Phone") or context.get("requester_phone") or "",
        "Phone": fields.get("Phone") or fields.get("requester_phone") or context.get("requester_phone") or "",
        "role": "Persona principal",
    }

    def add_candidate(document, data):
        document = str(document or "").strip()
        if not document:
            return
        if document not in candidates:
            candidates.append(document)
        candidate_data.setdefault(document, {}).update(data)
        candidate_data[document].setdefault("N_mero_de_ID", document)

    # Movilidad siempre empieza por la identidad general del formulario. Los
    # vehículos son riesgos; sólo un asegurado explícitamente distinto se
    # agrega como candidato adicional y nunca sustituye al principal.
    if schema_slug == "movilidad":
        if requester_document:
            add_candidate(requester_document, requester_data)
        groups = payload.get("groups") if isinstance(payload.get("groups"), dict) else {}
        for row in groups.get("vehicles", ()) if isinstance(groups.get("vehicles"), list) else ():
            if not isinstance(row, dict):
                continue
            insured_document = str(row.get("insured_document") or "").strip()
            # El contrato nuevo expresa la relación positivamente. El alias
            # anterior sólo se interpreta para snapshots históricos.
            has_new_relation = "insured_same_as_requester" in row
            same_as_requester = row.get("insured_same_as_requester") in {True, 1, "1", "Sí", "Si", "sí", "si", "true", "True"}
            explicit_different = (
                (has_new_relation and not same_as_requester)
                or (not has_new_relation and row.get("insured_is_different") in {True, 1, "1", "Sí", "Si", "sí", "si", "true", "True"})
            )
            if not explicit_different or not insured_document or insured_document == requester_document:
                continue
            add_candidate(insured_document, {
                "label": str(row.get("insured_name") or " ".join(filter(None, (row.get("insured_first_name"), row.get("insured_last_name")))) or "Asegurado del vehículo"),
                "First_Name": row.get("insured_first_name") or row.get("first_name") or "",
                "Last_Name": row.get("insured_last_name") or row.get("last_name") or "",
                "Tipo_ID": row.get("insured_id_type") or row.get("id_type") or "",
                "N_mero_de_ID": insured_document,
                "Date_of_Birth": row.get("insured_birth_date") or row.get("birth_date") or "",
                "Email": row.get("insured_email") or row.get("email") or "",
                "Mobile": row.get("insured_mobile") or row.get("insured_phone") or row.get("mobile") or row.get("phone") or "",
                "Phone": row.get("insured_phone") or row.get("phone") or "",
                "role": "Asegurado del vehículo",
            })
    elif schema_slug == "salud":
        # Salud puede tener varias personas. La primera puede declarar que
        # reutiliza la identidad general; las siguientes son independientes.
        groups = payload.get("groups") if isinstance(payload.get("groups"), dict) else {}
        rows = groups.get("people", ()) if isinstance(groups.get("people"), list) else ()
        if requester_document:
            add_candidate(requester_document, requester_data)
        person_offset = 1 if requester_document else 0
        for position, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                continue
            use_requester = row.get("is_requester", row.get("use_requester")) in {True, 1, "1", "Sí", "Si", "sí", "si", "true", "True"}
            if position == 1 and use_requester and requester_document:
                add_candidate(requester_document, {**requester_data, "role": "Persona 1 · solicitante"})
                continue
            document = row.get("document") or row.get("N_mero_de_ID")
            add_candidate(document, {
                "label": str(row.get("name") or " ".join(filter(None, (row.get("first_name"), row.get("last_name")))) or "Persona"),
                "First_Name": row.get("first_name") or row.get("First_Name") or "",
                "Last_Name": row.get("last_name") or row.get("Last_Name") or "",
                "Tipo_ID": row.get("id_type") or row.get("Tipo_ID") or "",
                "N_mero_de_ID": document,
                "Date_of_Birth": row.get("birth_date") or row.get("Date_of_Birth") or "",
                "Email": row.get("email") or "",
                "Mobile": row.get("mobile") or row.get("phone") or "",
                "Phone": row.get("phone") or "",
                "role": f"Persona {position + person_offset}",
            })
    else:
        # Mantener la semántica histórica para Vida, Exequial y SOAT. No
        # reinterpretar nombres antiguos ni aplicar reglas de Movilidad.
        if requester_document:
            add_candidate(requester_document, requester_data)
        for value in (fields.get("documento"), fields.get("document")):
            add_candidate(value, {"N_mero_de_ID": str(value or "").strip(), "role": "Persona relacionada"})
    groups = payload.get("groups") if isinstance(payload.get("groups"), dict) else {}
    if schema_slug not in {"movilidad", "salud"}:
        for rows in groups.values():
            for row in rows if isinstance(rows, list) else ():
                if not isinstance(row, dict):
                    continue
                for key in ("documento", "document", "insured_document", "id_number"):
                    value = str(row.get(key) or "").strip()
                    if value:
                        add_candidate(value, {
                            "label": str(row.get("name") or row.get("insured_name") or "Persona"),
                            "First_Name": row.get("first_name") or row.get("insured_first_name") or "",
                            "Last_Name": row.get("last_name") or row.get("insured_last_name") or "",
                            "Tipo_ID": row.get("id_type") or row.get("insured_id_type") or "",
                            "N_mero_de_ID": value,
                            "Email": row.get("email") or "",
                            "Mobile": row.get("mobile") or row.get("phone") or "",
                            "role": "Persona relacionada",
                        })
    quotation.refresh_from_db(fields=("safe_metadata",))
    corrections = (quotation.safe_metadata or {}).get("person_corrections", {})
    if isinstance(corrections, dict):
        for document, correction in corrections.items():
            if isinstance(correction, dict):
                candidate_data.setdefault(str(document), {}).update(correction)
    service = person_service or PersonSearchService()
    lookups = []
    for document in candidates:
        results = tuple(service.search(document))
        if len(results) == 1:
            person = results[0]
            lookups.append({"status": "found", "document": document, "display_name": person.full_name, "masked_document": person.masked_document, "detail_token": person.detail_token})
        elif not results:
            data = candidate_data.get(document, {"N_mero_de_ID": document})
            candidate = PersonCandidate(
                first_name=str(data.get("First_Name") or "").strip(),
                last_name=str(data.get("Last_Name") or "").strip(),
                document_type=str(data.get("Tipo_ID") or "").strip(),
                document=str(data.get("N_mero_de_ID") or document).strip(),
                date_of_birth=data.get("Date_of_Birth") or "",
                email=str(data.get("Email") or "").strip(),
                phone=str(data.get("Phone") or "").strip(),
                mobile=str(data.get("Mobile") or "").strip(),
                role=str(data.get("role") or "Persona"),
                source="individual_quotation",
            )
            missing = contact_missing_fields(candidate)
            lookups.append({
                "status": "not_found", "document": document,
                "display_name": data.get("label", "Persona"),
                "role": candidate.role, "missing_fields": missing,
                "has_complete_data": not missing,
                "candidate": candidate.as_metadata(),
            })
        else:
            lookups.append({"status": "ambiguous", "document": document, "count": len(results)})
    result = lookups[0] if lookups else {"status": "pending_identifier"}
    quotation.refresh_from_db(fields=("safe_metadata",))
    metadata = dict(quotation.safe_metadata or {})
    metadata["person_lookup"] = result
    metadata["people_lookup"] = lookups
    CotizacionIndividual.objects.filter(pk=quotation.pk).update(safe_metadata=metadata)
    return result
