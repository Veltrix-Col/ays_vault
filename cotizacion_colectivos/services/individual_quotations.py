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

from vault.crypto import encrypt

from ..models import (
    AdjuntoCotizacionIndividual,
    CotizacionIndividual,
    NotificacionCotizacionIndividual,
)
from ..quotation_forms.catalog import get_policy_branch_schema
from ..quotation_forms.security import sign_policy_context


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
    selected = options.get(str(affiliate_key or ""))
    if selected is None:
        raise ValidationError("Seleccione un afiliado válido para esta póliza.")

    matching = next((
        member for member in members
        if selected.key in {member.associate_key, member.insured_key}
    ), None)
    is_associate = bool(matching and matching.associate_key == selected.key)
    requester_name = (
        matching.associate_name if is_associate and matching else
        matching.insured_name if matching else selected.label
    )
    requester_id_type = (
        matching.associate_id_type if is_associate and matching else
        matching.insured_id_type if matching else selected.id_type
    )
    requester_document = (
        matching.associate_document if is_associate and matching else
        matching.insured_document if matching else ""
    )
    values = {
        "requester_name": str(requester_name or selected.label),
        "requester_id_type": str(requester_id_type or selected.id_type),
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
        "affiliate_key": selected.key,
        "affiliate_role": selected.role,
        "branch_slug": schema.slug,
        "schema_version": schema.version,
        "creator_id": int(creator_id),
        "policy_label": str(detail.masked_reference or "Póliza colectiva"),
        "branch_name": str(detail.branch_name),
        "affiliate_label": selected.label,
        **values,
    }
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
    public_payload = {
        "schema": schema.slug,
        "schema_version": schema.version,
        "fields": {field.key: cleaned_data.get(field.key, "") for field in schema.fields},
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
    return quotation
