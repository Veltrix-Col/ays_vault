from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from django.utils import timezone


_INVALID = re.compile(r"[^A-Za-z0-9_-]+")

_ATTACHMENT_TYPE_LABELS = {
    "vehicle_registration": "TARJETA_PROPIEDAD",
    "risk_document": "TARJETA_PROPIEDAD",
    "support_document": "SOPORTE",
}
_IDENTIFICATION_LABELS = {
    "CC": "CEDULA",
    "CE": "CEDULA_EXTRANJERIA",
    "PAS": "PASAPORTE",
    "PASAPORTE": "PASAPORTE",
    "NIT": "NIT",
    "TI": "TARJETA_IDENTIDAD",
    "RC": "REGISTRO_CIVIL",
}


def safe_filename_part(value: object, *, fallback: str = "Sin_nombre", limit: int = 48) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    result = _INVALID.sub("_", ascii_value.strip()).strip("_-")
    return (result or fallback)[:limit].rstrip("_-") or fallback


def build_attachment_filename(
    *,
    document_type: object,
    original_filename: object,
    identification_type: object = "",
    identification_number: object = "",
    plate: object = "",
    policy_number: object = "",
    detail: object = "",
) -> str:
    """Build the deterministic Zoho-facing filename without changing storage."""

    document_key = str(document_type or "").strip().casefold()
    if document_key == "identity_document":
        type_label = _IDENTIFICATION_LABELS.get(
            str(identification_type or "").strip().upper(), "DOCUMENTO_IDENTIDAD"
        )
        identifier = "_".join(
            part for part in (
                safe_filename_part(identification_type, fallback="ID"),
                safe_filename_part(identification_number, fallback="SIN_NUMERO"),
            ) if part
        ).upper()
    else:
        type_label = _ATTACHMENT_TYPE_LABELS.get(
            document_key, safe_filename_part(document_type, fallback="DOCUMENTO").upper()
        )
        identifier = safe_filename_part(
            plate or policy_number,
            fallback=safe_filename_part(Path(original_filename).stem, fallback="SIN_IDENTIFICADOR"),
        ).upper()
    parts = [type_label, identifier]
    if detail:
        parts.append(safe_filename_part(detail).upper())
    suffix = Path(str(original_filename or "")).suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,8}", suffix):
        suffix = ".bin"
    return "_".join(parts) + suffix


def download_filename(
    prefix: str,
    *,
    origin: object,
    request_id: object = "",
    version: int | None = None,
    branch: object = "",
    masked_reference: object = "",
) -> str:
    parts = [safe_filename_part(prefix), safe_filename_part(origin)]
    if branch:
        parts.append(safe_filename_part(branch))
    if masked_reference:
        digits = "".join(re.findall(r"\d", str(masked_reference)))
        if digits:
            parts.append(digits[-4:])
    if request_id:
        parts.append(safe_filename_part(request_id))
    if version is not None:
        parts.append(f"V{int(version)}")
    parts.append(timezone.localtime().strftime("%Y%m%d_%H%M%S"))
    return "_".join(parts)[:180].rstrip("_") + ".xlsx"
