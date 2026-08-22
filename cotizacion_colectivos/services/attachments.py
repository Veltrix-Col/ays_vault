from __future__ import annotations

import hashlib
import os
import secrets
import zipfile
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from ..models import AdjuntoSolicitudColectivo, EventoSolicitudColectivo, RespuestaSolicitudColectivo

MIME_BY_EXTENSION = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


def _detected(extension: str, header: bytes) -> str:
    if extension == ".pdf" and header.startswith(b"%PDF-"):
        return MIME_BY_EXTENSION[extension]
    if extension in {".jpg", ".jpeg"} and header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if extension == ".png" and header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if extension == ".xlsx" and header.startswith(b"PK"):
        return MIME_BY_EXTENSION[extension]
    return ""


@transaction.atomic
def store_attachment(*, response: RespuestaSolicitudColectivo, uploaded, allow_excel: bool = False) -> AdjuntoSolicitudColectivo:
    name = Path(uploaded.name or "").name
    extension = Path(name).suffix.casefold()
    if extension not in MIME_BY_EXTENSION or (extension == ".xlsx" and not allow_excel) or name.casefold().endswith((".xlsm", ".html", ".svg", ".exe")):
        raise ValidationError("El tipo de archivo no está permitido.")
    if name.count(".") > 1:
        raise ValidationError("No se permiten archivos con doble extensión.")
    size = int(getattr(uploaded, "size", 0))
    if size <= 0 or size > settings.COLECTIVOS_ATTACHMENT_MAX_BYTES:
        raise ValidationError("El tamaño del archivo no está permitido.")
    total = response.request.attachments.aggregate(total=Sum("size"))["total"] or 0
    if total + size > settings.COLECTIVOS_ATTACHMENT_TOTAL_BYTES:
        raise ValidationError("Se alcanzó el límite total de adjuntos.")
    header = uploaded.read(16)
    uploaded.seek(0)
    detected = _detected(extension, header)
    declared = str(getattr(uploaded, "content_type", ""))
    if not detected or (declared and declared not in {detected, "application/octet-stream"}):
        raise ValidationError("El contenido del archivo no coincide con su tipo.")
    if extension == ".xlsx":
        try:
            with zipfile.ZipFile(uploaded) as archive:
                names = {item.casefold() for item in archive.namelist()}
                if any("vbaproject" in item or item.endswith(".bin") for item in names):
                    raise ValidationError("El archivo contiene macros u objetos no permitidos.")
        except zipfile.BadZipFile as exc:
            raise ValidationError("El archivo XLSX no es válido.") from exc
        finally:
            uploaded.seek(0)
    content = uploaded.read()
    uploaded.seek(0)
    checksum = hashlib.sha256(content).hexdigest()
    internal_name = f"{secrets.token_hex(24)}{extension}"
    root = Path(settings.COLECTIVOS_PRIVATE_ROOT).resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = (root / internal_name).resolve()
    if root not in target.parents:
        raise ValidationError("La ruta de almacenamiento no es válida.")
    temporary = target.with_suffix(target.suffix + ".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(content)
        os.replace(temporary, target)
        attachment = AdjuntoSolicitudColectivo.objects.create(
            request=response.request, response=response, safe_original_name=f"soporte{extension}", internal_name=internal_name,
            extension=extension, detected_mime=detected, size=size, checksum=checksum,
            stored_path=str(target.relative_to(root)), safe_metadata={"antivirus": "not_configured"},
        )
    except Exception:
        temporary.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise
    EventoSolicitudColectivo.objects.create(request=response.request, event_type="ATTACHMENT_UPLOADED", origin="EXTERNO", safe_metadata={"size": size, "type": extension})
    return attachment
