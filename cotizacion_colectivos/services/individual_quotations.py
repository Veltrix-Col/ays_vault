from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
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
from ..quotation_forms.catalog import get_policy_branch_schema
from ..quotation_forms.security import sign_policy_context


def _normalized(value: object) -> str:
    import unicodedata
    return "".join(
        character for character in unicodedata.normalize("NFKD", str(value or "").casefold())
        if not unicodedata.combining(character)
    )


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
        "requester_name": str(requester_name or (selected.label if selected else "")),
        "requester_id_type": str(requester_id_type or (selected.id_type if selected else "")),
        "requester_document": str(requester_document or ""),
        "requester_email": str(getattr(matching, "email", "") or ""),
        "requester_phone": str(
            getattr(matching, "mobile", "") or getattr(matching, "phone", "") or ""
        ),
        "collective_context": str(detail.holder or detail.source_name or ""),
    }
    payload = {
        "context_version": 1,
        "policy_token": str(policy_token),
        "source_kind": str(detail.source_kind or "company"),
        "affiliate_key": selected.key if selected else "",
        "affiliate_role": selected.role if selected else "Persona nueva",
        "branch_slug": schema.slug,
        "schema_version": schema.version,
        "creator_id": int(creator_id),
        "policy_label": str(
            detail.full_reference or detail.masked_reference or "Póliza colectiva"
        ),
        "branch_name": str(detail.branch_name),
        "affiliate_label": selected.label if selected else "Persona nueva",
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
        validated.append({"uploaded": uploaded, "extension": extension, "mime": detected, "size": size})
    if total > settings.COLECTIVOS_ATTACHMENT_TOTAL_BYTES:
        raise ValidationError("Los archivos superan el límite total permitido.")
    return tuple(validated)


@transaction.atomic
def create_individual_quotation(*, schema, cleaned_data, actor, context=None):
    files = validate_attachments(cleaned_data.get("attachments") or [])
    captured_fields = {field.key: cleaned_data.get(field.key, "") for field in schema.fields}
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
    quotation = CotizacionIndividual.objects.create(
        branch_code=schema.code,
        branch_slug=schema.slug,
        schema_version=schema.version,
        encrypted_payload=protected,
        payload_checksum=hashlib.sha256(protected.encode()).hexdigest(),
        context_hash=context_hash,
        item_count=sum(len(items) for items in cleaned_data["normalized_items"].values()),
        attachment_count=len(files),
        created_by=actor,
    )
    task_context = context or {}
    task_fields = captured_fields
    root = (Path(settings.COLECTIVOS_PRIVATE_ROOT) / "individual_quotations").resolve()
    root.mkdir(parents=True, exist_ok=True)
    created_paths = []
    try:
        for item in files:
            uploaded = item["uploaded"]
            content = uploaded.read()
            uploaded.seek(0)
            encrypted = encrypt(base64.b64encode(content).decode()).encode()
            internal_name = f"{secrets.token_hex(32)}.enc"
            target = (root / internal_name).resolve()
            if root not in target.parents:
                raise ValidationError("La ruta de almacenamiento no es válida.")
            temporary = target.with_suffix(".tmp")
            with temporary.open("xb") as stream:
                stream.write(encrypted)
            os.replace(temporary, target)
            created_paths.append(target)
            AdjuntoCotizacionIndividual.objects.create(
                quotation=quotation,
                safe_original_name=f"soporte{item['extension']}",
                internal_name=internal_name,
                extension=item["extension"],
                detected_mime=item["mime"],
                size=item["size"],
                checksum=hashlib.sha256(content).hexdigest(),
                stored_path=internal_name,
                safe_metadata={"encrypted": True, "antivirus": "not_configured"},
            )
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
            has_attachments=bool(files),
            subject=" · ".join(filter(None, (
                "Cotización", str(task_context.get("branch_name") or schema.name),
                str(task_context.get("affiliate_label") or task_fields.get("nombre") or task_fields.get("name") or ""),
                str(task_fields.get("placa") or task_fields.get("plate") or ""),
            ))),
            area=str(task_context.get("task_area") or ""),
            observations=_individual_task_observations(task_context, task_fields, cleaned_data.get("normalized_items") or {}),
            responsible=str(task_context.get("task_responsible") or ""),
            responsible_email=str(task_context.get("task_responsible_email") or ""),
            requested_date=str(quotation.created_at.date() if quotation.created_at else timezone.localdate()),
        ),
        event_version=1,
    )
    # La respuesta ya quedó dentro de la transacción; publicar sólo después del
    # commit evita Tasks huérfanas si falla el guardado local o un adjunto.
    transaction.on_commit(lambda outbox_id=outbox.pk: publish_task_outbox(outbox_id))
    return quotation


def _individual_task_observations(context, fields, groups) -> str:
    lines = [f"Solicitud de cotización individual - {context.get('branch_name') or 'ramo no informado'}." ]
    for key, label in (("nombre", "Asegurado"), ("documento", "Documento"), ("declared_company", "Empresa")):
        value = str(fields.get(key) or context.get(key) or "").strip()
        if value:
            lines.append(f"{label}: {value}")
    labels = {
        "plate": "Placa", "placa": "Placa", "brand": "Marca", "line": "Referencia",
        "model": "Modelo", "city": "Ciudad", "name": "Asegurado", "document": "Documento",
        "insured_name": "Asegurado", "insured_document": "Documento",
    }
    technical = {"public_id", "token", "otp", "hash", "id", "source_id", "creator_id"}
    for rows in groups.values() if isinstance(groups, dict) else ():
        for row in rows if isinstance(rows, list) else ():
            if not isinstance(row, dict):
                continue
            values = " · ".join(
                f"{labels.get(key, key.replace('_', ' ').capitalize())}: {value}"
                for key, value in row.items()
                if key not in technical and str(value or "").strip()
            )
            if values:
                lines.append(values)
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


def resolve_accepted_person(*, quotation: CotizacionIndividual, person_service=None) -> dict[str, object]:
    payload = json.loads(decrypt(quotation.encrypted_payload))
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    document = str(context.get("requester_document") or fields.get("documento") or "").strip()
    if not document:
        result = {"status": "pending_identifier"}
    else:
        results = tuple((person_service or PersonSearchService()).search(document))
        if len(results) == 1:
            person = results[0]
            result = {
                "status": "found",
                "display_name": person.full_name,
                "masked_document": person.masked_document,
                "detail_token": person.detail_token,
            }
        elif not results:
            result = {"status": "not_found"}
        else:
            result = {"status": "ambiguous", "count": len(results)}
    quotation.refresh_from_db(fields=("safe_metadata",))
    metadata = dict(quotation.safe_metadata or {})
    metadata["person_lookup"] = result
    CotizacionIndividual.objects.filter(pk=quotation.pk).update(safe_metadata=metadata)
    return result
