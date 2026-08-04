from __future__ import annotations

import re

from django.core import signing

from integrations.zoho.exceptions import (
    ZohoAuthenticationError,
    ZohoAuthorizationError,
    ZohoConfigurationError,
    ZohoError,
    ZohoInvalidResponseError,
    ZohoNotFoundError,
    ZohoRateLimitError,
    ZohoTimeoutError,
)
from cotizacion_colectivos.zoho import (
    get_colectivos_environment,
    get_colectivos_profile,
    get_colectivos_zoho,
)

DETAIL_SALT = "cotizacion_colectivos.detail.v2"
ZOHO_ID = re.compile(r"^\d{10,30}$")


class ColectivosServiceError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def colectivos_zoho():
    return get_colectivos_zoho()


def escape_criteria_value(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
        .replace(",", "\\,")
        .replace(":", "\\:")
    )


def mask_document(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "Sin documento"
    visible = 3 if len(text) > 4 else 1
    return f"{'•' * max(len(text) - visible, 3)}{text[-visible:]}"


def mask_reference(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "Sin referencia"
    visible = min(4, len(text))
    return f"Referencia terminada en {text[-visible:]}"


def sign_record_id(record_id: object, entity_type: str = "contact", context: dict[str, str] | None = None) -> str:
    value = str(record_id or "").strip()
    if not ZOHO_ID.fullmatch(value):
        raise ColectivosServiceError("invalid_record", "El registro solicitado no es válido.")
    if entity_type not in {"company", "person", "policy", "contact"}:
        raise ColectivosServiceError("invalid_record", "El registro solicitado no es válido.")
    payload = {"id": value, "type": entity_type}
    if context:
        source_id = str(context.get("source_id") or "")
        source_kind = context.get("source_kind")
        if not ZOHO_ID.fullmatch(source_id) or source_kind not in {"company", "person"}:
            raise ColectivosServiceError("invalid_record", "El registro solicitado no es válido.")
        payload["source_id"] = source_id
        payload["source_kind"] = source_kind
    return signing.dumps(payload, salt=DETAIL_SALT, compress=True)


def unsign_record_context(token: str, expected_type: str | None = None) -> dict[str, str]:
    try:
        payload = signing.loads(token, salt=DETAIL_SALT, max_age=900)
    except signing.BadSignature as exc:
        raise ColectivosServiceError("invalid_record", "El registro solicitado no es válido.") from exc
    if not isinstance(payload, dict):
        raise ColectivosServiceError("invalid_record", "El registro solicitado no es válido.")
    value = payload.get("id")
    entity_type = payload.get("type")
    if (
        not isinstance(value, str)
        or not ZOHO_ID.fullmatch(value)
        or entity_type not in {"company", "person", "policy", "contact"}
        or (
            expected_type is not None
            and entity_type not in {expected_type, "contact"}
        )
    ):
        raise ColectivosServiceError("invalid_record", "El registro solicitado no es válido.")
    return payload


def unsign_record_id(token: str, expected_type: str | None = None) -> str:
    return unsign_record_context(token, expected_type)["id"]


def translate_zoho_error(
    exc: ZohoError, profile: str | None = None
) -> ColectivosServiceError:
    environment = get_colectivos_environment() if profile is None else {
        "label": "Producción" if profile == "production" else "Sandbox"
    }
    label = environment["label"]
    if isinstance(exc, ZohoConfigurationError):
        return ColectivosServiceError(
            "configuration", f"{label} no está disponible porque su configuración está incompleta."
        )
    if isinstance(exc, (ZohoAuthenticationError, ZohoAuthorizationError)):
        return ColectivosServiceError("permission", f"{label} rechazó el acceso solicitado.")
    if isinstance(exc, ZohoNotFoundError):
        return ColectivosServiceError("not_found", "El registro solicitado no existe.")
    if isinstance(exc, ZohoTimeoutError):
        return ColectivosServiceError("timeout", f"{label} tardó demasiado en responder.")
    if isinstance(exc, ZohoRateLimitError):
        return ColectivosServiceError("rate_limit", "Se alcanzó temporalmente el límite de consultas.")
    if isinstance(exc, ZohoInvalidResponseError):
        return ColectivosServiceError("invalid_response", f"{label} devolvió una respuesta inválida.")
    return ColectivosServiceError("unavailable", f"El servicio de {label} no está disponible temporalmente.")
