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
from django.db import transaction
from django.utils import timezone
import json
from datetime import datetime, timedelta, timezone as dt_timezone
from integrations.zoho.exceptions import ZohoAPIError, ZohoTimeoutError

from vault.crypto import decrypt
from ..filenames import build_attachment_filename
from .write_guards import require_write_guard


CANONICAL_RISK_DOCUMENT_TYPE = "vehicle_registration"
LEGACY_RISK_DOCUMENT_TYPES = frozenset({"risk_document"})


def _validate_document_contract(*, module: str, owner_type: str, document_type: str) -> str:
    """Validate the closed document contract and return its canonical type.

    ``risk_document`` is retained solely as an explicit compatibility alias
    for attachments persisted before the vehicle contract was unified.
    """
    if module == "Contacts":
        if owner_type != "contact" or document_type != "identity_document":
            raise ValidationError("El documento no corresponde al destino indicado.")
        return "identity_document"
    if module == "Riesgos":
        if owner_type != "risk":
            raise ValidationError("El documento no corresponde al destino indicado.")
        if document_type == CANONICAL_RISK_DOCUMENT_TYPE:
            return CANONICAL_RISK_DOCUMENT_TYPE
        if document_type in LEGACY_RISK_DOCUMENT_TYPES:
            return CANONICAL_RISK_DOCUMENT_TYPE
        raise ValidationError("El documento no corresponde al destino indicado.")
    raise ValidationError("El documento no corresponde al destino indicado.")


class IndividualAttachmentUncertain(RuntimeError):
    reconciliation_required = True


class IndividualAttachmentBlocked(RuntimeError):
    pass


def publish_attachment(*, attachment, module: str, record_id: str, zoho=None):
    """Reserve locally, call Zoho outside the DB transaction, then finalize."""
    module = str(module or "").strip()
    record_id = str(record_id or "").strip()
    if module not in {"Contacts", "Riesgos"} or not record_id.isdigit() or int(record_id) <= 0:
        raise ValidationError("Destino de documento inválido.")
    metadata = attachment.safe_metadata if isinstance(attachment.safe_metadata, dict) else {}
    _validate_document_contract(
        module=module,
        owner_type=str(metadata.get("owner_type") or "").strip(),
        document_type=str(metadata.get("document_type") or "").strip(),
    )
    _require_attachment_guard()
    reserved = _reserve_upload(attachment=attachment, module=module, record_id=record_id)
    if reserved is None:
        return {"status": "UPLOADED", "attachment_id": _uploaded_id(attachment), "module": module, "record_id": record_id, "request_sent": False}
    return _publish_attachment(attachment=reserved, module=module, record_id=record_id, zoho=zoho)


def _uploaded_id(attachment):
    metadata = attachment.safe_metadata if isinstance(attachment.safe_metadata, dict) else {}
    nested = metadata.get("zoho_attachment") if isinstance(metadata.get("zoho_attachment"), dict) else {}
    return str(nested.get("attachment_id") or metadata.get("zoho_attachment_id") or "")


def _reserve_upload(*, attachment, module, record_id):
    manager = getattr(getattr(attachment, "__class__", None), "objects", None)
    primary_key = getattr(attachment, "pk", None)
    if manager is not None and primary_key:
        with transaction.atomic():
            current = manager.select_for_update().get(pk=primary_key)
            result = _reserve_upload_state(current, module, record_id)
            if not result:
                attachment.safe_metadata = current.safe_metadata
                return None
            return current
    return attachment if _reserve_upload_state(attachment, module, record_id) else None


def _reserve_upload_state(attachment, module, record_id):
    metadata = attachment.safe_metadata if isinstance(attachment.safe_metadata, dict) else {}
    previous = metadata.get("zoho_attachment") if isinstance(metadata.get("zoho_attachment"), dict) else {}
    status = str(previous.get("status") or metadata.get("zoho_status") or "").upper()
    same_target = str(previous.get("module") or metadata.get("zoho_module") or "") == module and str(previous.get("record_id") or metadata.get("zoho_record_id") or "") == record_id
    if status == "UPLOADED" and same_target and _uploaded_id(attachment):
        return False
    if status in {"UNCERTAIN", "RECONCILE_REQUIRED"} and same_target:
        raise IndividualAttachmentUncertain("El documento requiere conciliación antes de reintentar.")
    if status == "UPLOADING" and same_target:
        started = str(metadata.get("zoho_upload_started_at") or "")
        try:
            age = datetime.now(dt_timezone.utc) - datetime.fromisoformat(started)
        except (TypeError, ValueError):
            age = timedelta.max
        if age <= timedelta(minutes=15):
            raise IndividualAttachmentUncertain("El documento ya está en proceso de publicación.")
        _mark_uncertain(attachment, module, record_id)
        raise IndividualAttachmentUncertain("El intento anterior requiere conciliación antes de reintentar.")
    token = timezone.now().isoformat()
    attachment.safe_metadata = {
        **metadata,
        "zoho_module": module,
        "zoho_record_id": record_id,
        "zoho_status": "uploading",
        "zoho_upload_started_at": token,
        "zoho_attachment": {"status": "UPLOADING", "module": module, "record_id": record_id, "started_at": token},
    }
    attachment.save(update_fields=("safe_metadata",))
    return True


def _publish_attachment(*, attachment, module: str, record_id: str, zoho=None):
    module = str(module or "").strip()
    record_id = str(record_id or "").strip()
    if module not in {"Contacts", "Riesgos"} or not record_id.isdigit() or int(record_id) <= 0:
        raise ValidationError("Destino de documento inválido.")
    metadata = attachment.safe_metadata if isinstance(attachment.safe_metadata, dict) else {}
    owner_type = str(metadata.get("owner_type") or "").strip()
    document_type = str(metadata.get("document_type") or "").strip()
    _validate_document_contract(module=module, owner_type=owner_type, document_type=document_type)
    previous = metadata.get("zoho_attachment") if isinstance(metadata.get("zoho_attachment"), dict) else {}
    root = Path(settings.COLECTIVOS_PRIVATE_ROOT).resolve()
    target = (root / "individual_quotations" / str(attachment.stored_path)).resolve()
    if root not in target.parents or not target.is_file():
        raise ValidationError("El documento no está disponible.")
    try:
        content = base64.b64decode(decrypt(target.read_bytes().decode()).encode())
        stream = BytesIO(content)
        metadata = attachment.safe_metadata if isinstance(attachment.safe_metadata, dict) else {}
        stream.name = build_attachment_filename(
            document_type=document_type,
            original_filename=attachment.safe_original_name,
            identification_type=metadata.get("identification_type", ""),
            identification_number=metadata.get("identification_number", ""),
            plate=metadata.get("plate", ""),
            policy_number=metadata.get("policy_number", ""),
            detail=metadata.get("filename_detail", ""),
        )
        result = (zoho or _get_zoho()).attachments.upload(
            module=module, record_id=record_id, file=stream,
            filename=stream.name, content_type=attachment.detected_mime,
        )
    except (TimeoutError, ConnectionError, ZohoTimeoutError) as exc:
        if getattr(exc, "request_sent", None) is False:
            _mark_failed(attachment, module, record_id)
            raise
        _mark_uncertain(attachment, module, record_id)
        raise IndividualAttachmentUncertain("Resultado incierto; requiere conciliación del documento.") from exc
    except ZohoAPIError as exc:
        if getattr(exc, "request_sent", False):
            _mark_uncertain(attachment, module, record_id)
            raise IndividualAttachmentUncertain("Resultado incierto; requiere conciliación del documento.") from exc
        _mark_failed(attachment, module, record_id)
        raise
    attachment.safe_metadata = {
        **metadata,
        "zoho_module": module,
        "zoho_record_id": record_id,
        "zoho_attachment_id": (result.get("attachment_id") if isinstance(result, dict) else getattr(result, "attachment_id", "")),
        "zoho_status": "uploaded",
        "zoho_upload_started_at": "",
        "published_at": timezone.now().isoformat(),
        "zoho_attachment": {"status": "UPLOADED", "module": module, "record_id": record_id, "attachment_id": (result.get("attachment_id") if isinstance(result, dict) else getattr(result, "attachment_id", "")), "published_at": timezone.now().isoformat()},
    }
    attachment.save(update_fields=("safe_metadata",))
    return result


def _require_attachment_guard():
    profile = str(getattr(settings, "ZOHO_ACTIVE_PROFILE", "sandbox"))
    require_write_guard(
        entity="attachment", profile=profile,
        confirmation=str(
            getattr(settings, "COLECTIVOS_SANDBOX_ATTACHMENT_WRITE_CONFIRMATION", "")
            if profile == "sandbox"
            else getattr(settings, "COLECTIVOS_PRODUCTION_ATTACHMENT_WRITE_CONFIRMATION", "")
        ), feature_flag="COLECTIVOS_ATTACHMENT_PUBLISH_ENABLED",
        legacy_setting="COLECTIVOS_ATTACHMENT_WRITE_CONFIRMATION",
        disabled_error=IndividualAttachmentBlocked,
    )


def _get_zoho():
    from integrations.zoho import get_zoho
    return get_zoho(profile=str(getattr(settings, "ZOHO_ACTIVE_PROFILE", "sandbox")))


def _mark_uncertain(attachment, module, record_id):
    metadata = attachment.safe_metadata if isinstance(attachment.safe_metadata, dict) else {}
    attachment.safe_metadata = {**metadata, "zoho_module": module, "zoho_record_id": record_id, "zoho_status": "reconcile_required", "zoho_attachment": {"status": "RECONCILE_REQUIRED", "module": module, "record_id": record_id}}
    attachment.save(update_fields=("safe_metadata",))


def _mark_failed(attachment, module, record_id):
    metadata = attachment.safe_metadata if isinstance(attachment.safe_metadata, dict) else {}
    attachment.safe_metadata = {**metadata, "zoho_module": module, "zoho_record_id": record_id, "zoho_status": "failed", "zoho_attachment": {"status": "FAILED", "module": module, "record_id": record_id}}
    attachment.save(update_fields=("safe_metadata",))


def _mark_status(attachment, status, module, record_id):
    metadata = attachment.safe_metadata if isinstance(attachment.safe_metadata, dict) else {}
    attachment.safe_metadata = {**metadata, "zoho_status": status.lower(), "zoho_attachment": {"status": status, "module": module, "record_id": record_id}}
    attachment.save(update_fields=("safe_metadata",))


def reconcile_attachment(*, attachment, module: str, record_id: str, zoho=None):
    """Reconcile one uncertain upload without issuing another upload."""
    if module not in {"Contacts", "Riesgos"} or not str(record_id).isdigit() or int(record_id) <= 0:
        raise ValidationError("Destino de documento inválido.")
    profile = str(getattr(settings, "ZOHO_ACTIVE_PROFILE", "sandbox"))
    confirmation = (
        getattr(settings, "COLECTIVOS_SANDBOX_ATTACHMENT_WRITE_CONFIRMATION", "")
        if profile == "sandbox" else getattr(settings, "COLECTIVOS_PRODUCTION_ATTACHMENT_WRITE_CONFIRMATION", "")
    )
    require_write_guard(
        entity="attachment", profile=profile,
        confirmation=str(confirmation),
        feature_flag="COLECTIVOS_ATTACHMENT_PUBLISH_ENABLED", legacy_setting="COLECTIVOS_ATTACHMENT_WRITE_CONFIRMATION",
        disabled_error=IndividualAttachmentBlocked,
    )
    listed = (zoho or _get_zoho()).attachments.list(module=module, record_id=str(record_id))
    filename = Path(attachment.safe_original_name).name
    size = int(getattr(attachment, "size", 0) or 0)
    matches = [item for item in (listed or ()) if str(item.get("file_name") or item.get("name") or "") == filename and int(item.get("size") or 0) == size]
    if len(matches) != 1:
        _mark_uncertain(attachment, module, str(record_id))
        return {"status": "RECONCILE_REQUIRED", "matches": len(matches), "request_sent": False}
    item = matches[0]
    attachment_id = str(item.get("attachment_id") or item.get("id") or "")
    metadata = attachment.safe_metadata if isinstance(attachment.safe_metadata, dict) else {}
    attachment.safe_metadata = {**metadata, "zoho_status": "uploaded", "zoho_upload_started_at": "", "zoho_attachment": {"status": "UPLOADED", "module": module, "record_id": str(record_id), "attachment_id": attachment_id, "reconciled_at": timezone.now().isoformat()}}
    attachment.save(update_fields=("safe_metadata",))
    return {"status": "UPLOADED", "attachment_id": attachment_id, "module": module, "record_id": str(record_id), "request_sent": False}


def publish_pending_for_person(*, quotation, document: str, record_id: str, owner_key: str = ""):
    """Publish at most the locally-owned document for a Contact."""
    payload = _payload(quotation)
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    keys = {str(owner_key).strip()} if str(owner_key or "").strip() else ({"affiliate"} if str(fields.get("requester_document") or "").strip() == str(document).strip() else set())
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
