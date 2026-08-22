"""Controlled publication of one encrypted individual document.

Entity creation and attachment upload are deliberately independent operations;
an upload failure never rolls back a confirmed Contact/Risk CREATE.
"""
from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone
import json
from integrations.zoho.exceptions import ZohoAPIError, ZohoTimeoutError

from vault.crypto import decrypt


class IndividualAttachmentUncertain(RuntimeError):
    reconciliation_required = True


def publish_attachment(*, attachment, module: str, record_id: str, zoho=None):
    module = str(module or "").strip()
    record_id = str(record_id or "").strip()
    if module not in {"Contacts", "Riesgos"} or not record_id.isdigit() or int(record_id) <= 0:
        raise ValidationError("Destino de documento inválido.")
    root = Path(settings.COLECTIVOS_PRIVATE_ROOT).resolve()
    target = (root / "individual_quotations" / str(attachment.stored_path)).resolve()
    if root not in target.parents or not target.is_file():
        raise ValidationError("El documento no está disponible.")
    try:
        content = base64.b64decode(decrypt(target.read_bytes().decode()).encode())
        stream = BytesIO(content)
        stream.name = Path(attachment.safe_original_name).name
        result = (zoho or _get_zoho()).attachments.upload(
            module=module, record_id=record_id, file=stream,
            filename=stream.name, content_type=attachment.detected_mime,
        )
    except (TimeoutError, ConnectionError, ZohoTimeoutError) as exc:
        _mark_uncertain(attachment, module, record_id)
        raise IndividualAttachmentUncertain("Resultado incierto; requiere conciliación del documento.") from exc
    except ZohoAPIError as exc:
        if getattr(exc, "request_sent", False):
            _mark_uncertain(attachment, module, record_id)
            raise IndividualAttachmentUncertain("Resultado incierto; requiere conciliación del documento.") from exc
        _mark_failed(attachment, module, record_id)
        raise
    attachment.safe_metadata = {
        **(attachment.safe_metadata if isinstance(attachment.safe_metadata, dict) else {}),
        "zoho_module": module,
        "zoho_record_id": record_id,
        "zoho_attachment_id": (result.get("attachment_id") if isinstance(result, dict) else getattr(result, "attachment_id", "")),
        "zoho_status": "uploaded",
        "published_at": timezone.now().isoformat(),
    }
    attachment.save(update_fields=("safe_metadata",))
    return result


def _get_zoho():
    from integrations.zoho import get_zoho
    return get_zoho(profile=str(getattr(settings, "ZOHO_ACTIVE_PROFILE", "sandbox")))


def _mark_uncertain(attachment, module, record_id):
    metadata = attachment.safe_metadata if isinstance(attachment.safe_metadata, dict) else {}
    attachment.safe_metadata = {**metadata, "zoho_module": module, "zoho_record_id": record_id, "zoho_status": "reconcile_required"}
    attachment.save(update_fields=("safe_metadata",))


def _mark_failed(attachment, module, record_id):
    metadata = attachment.safe_metadata if isinstance(attachment.safe_metadata, dict) else {}
    attachment.safe_metadata = {**metadata, "zoho_module": module, "zoho_record_id": record_id, "zoho_status": "failed"}
    attachment.save(update_fields=("safe_metadata",))


def publish_pending_for_person(*, quotation, document: str, record_id: str):
    """Publish at most the locally-owned document for a Contact."""
    payload = _payload(quotation)
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    keys = {"affiliate"} if str(fields.get("requester_document") or "").strip() == str(document).strip() else set()
    for group_key, rows in (payload.get("groups") or {}).items():
        for index, row in enumerate(rows or ()):
            if not isinstance(row, dict):
                continue
            candidate_document = row.get("document") if group_key == "people" else row.get("insured_document")
            if str(candidate_document or "").strip() == str(document).strip():
                key = str(row.get("entity_key") or f"{group_key}-{index}")
                keys.add(key if group_key == "people" else f"{key}-insured")
    return _publish_matching(quotation, keys, "Contacts", record_id)


def publish_pending_for_risk(*, quotation, vehicle_index: int, record_id: str):
    payload = _payload(quotation)
    rows = (payload.get("groups") or {}).get("vehicles") or []
    row = rows[vehicle_index] if vehicle_index < len(rows) and isinstance(rows[vehicle_index], dict) else {}
    key = str(row.get("entity_key") or f"vehicles-{vehicle_index}")
    return _publish_matching(quotation, {key}, "Riesgos", record_id)


def _publish_matching(quotation, keys, module, record_id):
    results = []
    for attachment in quotation.attachments.all():
        metadata = attachment.safe_metadata if isinstance(attachment.safe_metadata, dict) else {}
        if metadata.get("owner_key") not in keys or metadata.get("zoho_status") == "uploaded":
            continue
        results.append(publish_attachment(attachment=attachment, module=module, record_id=record_id))
    return tuple(results)


def _payload(quotation):
    return json.loads(decrypt(quotation.encrypted_payload))
