from __future__ import annotations

import base64
import hashlib
import json
import secrets
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from vault.crypto import decrypt, encrypt

from ..models import RespuestaSolicitudColectivo, VistaPreviaExcelSolicitudColectivo
from .excel_roundtrip import TEMPLATE_VERSION, parse_novelties
from .attachments import store_attachment
from .external import ExternalAccessError, save_response


def _digest(value: str | bytes) -> str:
    if isinstance(value, str):
        value = value.encode()
    return hashlib.sha256(value).hexdigest()


def _preview_root() -> Path:
    root = Path(settings.COLECTIVOS_PRIVATE_ROOT) / "excel_previews"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _stored_path(path: str) -> Path:
    root = _preview_root().resolve()
    target = (root / path).resolve()
    if root not in target.parents:
        raise ExternalAccessError("La ruta temporal no es válida.")
    return target


def _delete(path: str) -> None:
    try:
        _stored_path(path).unlink(missing_ok=True)
    except (OSError, ExternalAccessError):
        pass


def _split_token(token: str) -> tuple[str, str]:
    try:
        selector, secret = token.split(".", 1)
    except ValueError as exc:
        raise ExternalAccessError("La vista previa no es válida.") from exc
    if len(selector) > 32 or len(secret) < 32:
        raise ExternalAccessError("La vista previa no es válida.")
    return selector, secret


def create_preview(*, access, session_cookie: str, uploaded) -> tuple[VistaPreviaExcelSolicitudColectivo, str]:
    preview = parse_novelties(uploaded, access.request)
    uploaded.seek(0)
    raw = uploaded.read()
    checksum = _digest(raw)
    selector, secret = secrets.token_urlsafe(18)[:24], secrets.token_urlsafe(32)
    relative = f"{secrets.token_hex(24)}.enc"
    target = _preview_root() / relative
    ciphertext = encrypt(base64.b64encode(raw).decode())
    temporary = target.with_suffix(".tmp")
    temporary.write_text(ciphertext, encoding="utf-8")
    temporary.replace(target)
    summary = dict(preview.counts)
    summary["ADVERTENCIAS"] = len(preview.warnings)
    try:
        with transaction.atomic():
            stale = list(access.excel_previews.select_for_update().filter(status=VistaPreviaExcelSolicitudColectivo.Status.PENDING))
            for item in stale:
                item.status = item.Status.CANCELLED
                item.consumed_at = timezone.now()
                item.save(update_fields=("status", "consumed_at"))
                transaction.on_commit(lambda path=item.stored_path: _delete(path))
            item = VistaPreviaExcelSolicitudColectivo.objects.create(
                request=access.request,
                access=access,
                selector=selector,
                token_hash=_digest(secret),
                session_hash=_digest(session_cookie),
                stored_path=relative,
                file_checksum=checksum,
                encrypted_payload=encrypt(json.dumps(list(preview.rows), ensure_ascii=False)),
                summary=summary,
                template_version=TEMPLATE_VERSION,
                snapshot_revision=access.request.snapshot_revision,
                expires_at=timezone.now() + timedelta(seconds=settings.COLECTIVOS_EXCEL_PREVIEW_TTL_SECONDS),
            )
    except Exception:
        _delete(relative)
        raise
    return item, f"{selector}.{secret}"


def resolve_preview(*, token: str, access, session_cookie: str, lock: bool = False):
    selector, secret = _split_token(token)
    manager = VistaPreviaExcelSolicitudColectivo.objects.select_for_update() if lock else VistaPreviaExcelSolicitudColectivo.objects
    try:
        item = manager.select_related("request", "response").get(selector=selector, request=access.request, access=access)
    except VistaPreviaExcelSolicitudColectivo.DoesNotExist as exc:
        raise ExternalAccessError("La vista previa no es válida.") from exc
    if not secrets.compare_digest(item.token_hash, _digest(secret)) or not secrets.compare_digest(item.session_hash, _digest(session_cookie)):
        raise ExternalAccessError("La vista previa no es válida.")
    if item.status == item.Status.IMPORTED:
        return item
    if item.status != item.Status.PENDING:
        raise ExternalAccessError("La vista previa ya no está disponible.")
    if item.expires_at <= timezone.now():
        item.status = item.Status.EXPIRED
        item.consumed_at = timezone.now()
        item.save(update_fields=("status", "consumed_at"))
        transaction.on_commit(lambda: _delete(item.stored_path))
        raise ExternalAccessError("La vista previa ha expirado.")
    return item


@transaction.atomic
def confirm_preview(*, token: str, access, session_cookie: str):
    item = resolve_preview(token=token, access=access, session_cookie=session_cookie, lock=True)
    if item.status == item.Status.IMPORTED:
        return item.response
    if item.snapshot_revision != access.request.snapshot_revision or item.template_version != TEMPLATE_VERSION:
        raise ExternalAccessError("La solicitud cambió después de generar la vista previa.")
    try:
        encrypted_file = _stored_path(item.stored_path).read_text(encoding="utf-8")
        raw = base64.b64decode(decrypt(encrypted_file), validate=True)
    except (OSError, ValueError) as exc:
        raise ExternalAccessError("No fue posible verificar el archivo temporal.") from exc
    if not secrets.compare_digest(item.file_checksum, _digest(raw)):
        raise ExternalAccessError("El archivo temporal fue alterado.")
    try:
        rows = json.loads(decrypt(item.encrypted_payload))
    except (TypeError, ValueError) as exc:
        raise ExternalAccessError("No fue posible verificar la vista previa.") from exc
    response = save_response(access=access, rows=rows, observations="", origin=RespuestaSolicitudColectivo.Origin.EXCEL)
    workbook = ContentFile(raw, name="novedades.xlsx")
    workbook.content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    store_attachment(response=response, uploaded=workbook, allow_excel=True)
    item.response = response
    item.status = item.Status.IMPORTED
    item.consumed_at = timezone.now()
    item.encrypted_payload = ""
    item.save(update_fields=("response", "status", "consumed_at", "encrypted_payload"))
    transaction.on_commit(lambda: _delete(item.stored_path))
    return response


@transaction.atomic
def cancel_preview(*, token: str, access, session_cookie: str) -> None:
    item = resolve_preview(token=token, access=access, session_cookie=session_cookie, lock=True)
    if item.status == item.Status.IMPORTED:
        raise ExternalAccessError("La importación ya fue confirmada.")
    item.status = item.Status.CANCELLED
    item.consumed_at = timezone.now()
    item.encrypted_payload = ""
    item.save(update_fields=("status", "consumed_at", "encrypted_payload"))
    transaction.on_commit(lambda: _delete(item.stored_path))
