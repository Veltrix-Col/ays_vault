from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

SENSITIVE_KEY = re.compile(
    r"(authorization|token|secret|password|code|cookie|email|phone|document|identity)",
    re.IGNORECASE,
)
SAFE_TECHNICAL_VALUE = re.compile(r"[^A-Za-z0-9._-]")
SAFE_DETAIL_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,79}$")


@dataclass(frozen=True)
class SafeZohoError:
    """Subconjunto técnico permitido de una respuesta de error de Zoho."""

    code: str = ""
    message: str = "Zoho rechazó la consulta."
    status: str = ""
    detail_keys: tuple[str, ...] = ()


_SAFE_ERROR_MESSAGES = {
    "INVALID_MODULE": "El módulo no está admitido para esta operación.",
    "NOT_SUPPORTED": "La operación no está admitida para este módulo.",
    "NO_PERMISSION": "La cuenta conectada no tiene permiso para esta consulta.",
    "FORBIDDEN": "La cuenta conectada no tiene permiso para esta consulta.",
    "OAUTH_SCOPE_MISMATCH": "El alcance OAuth no autoriza esta consulta.",
    "INVALID_API_NAME": "El nombre API no es válido para esta operación.",
    "NOT_FOUND": "El recurso solicitado no está disponible en Zoho.",
    "TOO_MANY_REQUESTS": "Zoho limitó temporalmente las consultas.",
    "INTERNAL_ERROR": "Zoho presentó un error temporal.",
}


def redact_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    """Redacta claves sensibles sin serializar cuerpos personales completos."""
    return {
        str(key): "[REDACTED]" if SENSITIVE_KEY.search(str(key)) else safe_scalar(item)
        for key, item in value.items()
    }


def safe_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= 80 else f"{value[:40]}…"
    return f"<{type(value).__name__}>"


def mask_identifier(value: str) -> str:
    clean = (value or "").strip()
    if len(clean) < 9:
        return "configurado" if clean else "no configurado"
    return f"{clean[:4]}…{clean[-4:]}"


def safe_zoho_error(value: object) -> SafeZohoError:
    """Extrae solo metadatos técnicos permitidos; nunca conserva valores de details."""
    if not isinstance(value, Mapping):
        return SafeZohoError()
    code = _technical_value(value.get("code"))
    status = _technical_value(value.get("status"))
    details = value.get("details")
    detail_keys = ()
    if isinstance(details, Mapping):
        detail_keys = tuple(
            sorted(
                {
                    str(key)
                    for key in details
                    if SAFE_DETAIL_KEY.fullmatch(str(key))
                }
            )
        )
    return SafeZohoError(
        code=code,
        message=_safe_error_message(code, value.get("message")),
        status=status,
        detail_keys=detail_keys,
    )


def _technical_value(value: object) -> str:
    return SAFE_TECHNICAL_VALUE.sub("", str(value or "").strip())[:80].upper()


def _safe_error_message(code: str, raw_message: object) -> str:
    if code in _SAFE_ERROR_MESSAGES:
        return _SAFE_ERROR_MESSAGES[code]
    normalized = " ".join(str(raw_message or "").lower().split())
    # Solo se reconocen conceptos cerrados. El texto remoto nunca se devuelve.
    if "scope" in normalized:
        return "El alcance OAuth no autoriza esta consulta."
    if "permission" in normalized or "not authorized" in normalized:
        return "La cuenta conectada no tiene permiso para esta consulta."
    if "not supported" in normalized or "unsupported" in normalized:
        return "La operación no está admitida para este módulo."
    if "not enabled" in normalized or "disabled" in normalized:
        return "El módulo no está habilitado en la organización."
    if "temporar" in normalized or "try again" in normalized:
        return "Zoho presentó un error temporal."
    return "Zoho rechazó la consulta."
