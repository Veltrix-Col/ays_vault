from __future__ import annotations

import re
import unicodedata

from django.utils import timezone


_INVALID = re.compile(r"[^A-Za-z0-9_-]+")


def safe_filename_part(value: object, *, fallback: str = "Sin_nombre", limit: int = 48) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    result = _INVALID.sub("_", ascii_value.strip()).strip("_-")
    return (result or fallback)[:limit].rstrip("_-") or fallback


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
