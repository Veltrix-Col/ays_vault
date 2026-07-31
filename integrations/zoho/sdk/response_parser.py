from __future__ import annotations

from typing import Any

from ..exceptions import (
    ZohoAuthenticationError,
    ZohoAuthorizationError,
    ZohoInvalidResponseError,
    ZohoNotFoundError,
    ZohoRateLimitError,
    ZohoSDKError,
    ZohoError,
)
from ..redaction import safe_zoho_error


def response_object(response, *, operation: str, module: str = ""):
    if response is None:
        raise ZohoInvalidResponseError(
            f"Zoho no devolvió respuesta para {operation}."
        )
    status = int(response.get_status_code())
    if status == 204:
        return None
    value = response.get_object()
    if status >= 400 or _is_api_exception(value):
        _raise_api_error(value, status, operation=operation, module=module)
    if value is None:
        raise ZohoInvalidResponseError(
            f"Zoho devolvió una respuesta vacía para {operation}.",
            status_code=status,
        )
    return value


def getter(value: object, name: str, default: Any = None) -> Any:
    method = getattr(value, f"get_{name}", None)
    if not callable(method):
        return default
    result = method()
    return default if result is None else result


def record_dict(record: object) -> dict[str, object]:
    values = getter(record, "key_values", {})
    if not isinstance(values, dict):
        raise ZohoInvalidResponseError("Zoho devolvió un registro no válido.")
    return {str(key): _plain(value) for key, value in values.items()}


def _plain(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    choice = getattr(value, "get_value", None)
    if callable(choice):
        return _plain(choice())
    key_values = getattr(value, "get_key_values", None)
    if callable(key_values):
        return _plain(key_values())
    return str(value)


def _is_api_exception(value: object) -> bool:
    return value is not None and value.__class__.__name__.lower() == "apiexception"


def normalize_sdk_exception(
    value: BaseException,
    *,
    operation: str,
    module: str = "",
    request_sent: bool | None = None,
) -> ZohoError:
    """Normaliza una excepción del SDK sin conservar su texto o detalles remotos."""
    status = _status_code(value)
    safe = _safe_sdk_error(value)
    exception_class = value.__class__.__name__[:80]
    kwargs = _error_kwargs(
        safe,
        status,
        operation=operation,
        module=module,
        exception_class=exception_class,
        request_sent=request_sent,
        sdk_code=safe.code,
        api_code="",
    )
    code = safe.code
    if status == 401 or "AUTH" in code or "TOKEN" in code:
        return ZohoAuthenticationError(safe.message, **kwargs)
    if status == 403 or "SCOPE" in code or "PERMISSION" in code:
        return ZohoAuthorizationError(safe.message, **kwargs)
    if status == 404 or "NOT_FOUND" in code:
        return ZohoNotFoundError(safe.message, **kwargs)
    if status == 429 or "LIMIT" in code:
        return ZohoRateLimitError(safe.message, **kwargs)
    return ZohoSDKError(safe.message, **kwargs)


def _raise_api_error(
    value: object,
    status: int,
    *,
    operation: str,
    module: str,
) -> None:
    safe = _safe_sdk_error(value)
    code = safe.code
    kwargs = _error_kwargs(
        safe,
        status,
        operation=operation,
        module=module,
        exception_class=value.__class__.__name__[:80] if value is not None else "",
        request_sent=True,
        sdk_code="",
        api_code=safe.code,
    )
    if status == 401 or "AUTH" in code or "TOKEN" in code:
        raise ZohoAuthenticationError(
            safe.message, **kwargs
        )
    if status == 403 or "SCOPE" in code or "PERMISSION" in code:
        raise ZohoAuthorizationError(
            safe.message,
            **kwargs,
        )
    if status == 404 or "NOT_FOUND" in code:
        raise ZohoNotFoundError(
            safe.message,
            **kwargs,
        )
    if status == 429 or "LIMIT" in code:
        raise ZohoRateLimitError(
            safe.message, **kwargs
        )
    raise ZohoSDKError(safe.message, **kwargs)


def _safe_sdk_error(value: object):
    details = getter(value, "details", {})
    payload = {
        "code": _sdk_scalar(getter(value, "code", "")),
        "message": _sdk_scalar(getter(value, "message", "")),
        "status": _sdk_scalar(getter(value, "status", "")),
        "details": details if isinstance(details, dict) else {},
    }
    return safe_zoho_error(payload)


def _sdk_scalar(value: object) -> object:
    choice = getattr(value, "get_value", None)
    return choice() if callable(choice) else value


def _status_code(value: object) -> int | None:
    for name in ("status_code", "http_status_code"):
        raw = getter(value, name, None)
        try:
            if raw not in (None, ""):
                return int(raw)
        except (TypeError, ValueError):
            continue
    return None


def _error_kwargs(
    safe,
    status: int | None,
    *,
    operation: str,
    module: str,
    exception_class: str,
    request_sent: bool | None,
    sdk_code: str,
    api_code: str,
) -> dict[str, object]:
    return {
        "status_code": status,
        "zoho_code": api_code,
        "zoho_status": safe.status,
        "detail_keys": safe.detail_keys,
        "backend": "sdk",
        "operation": operation,
        "module": module,
        "sdk_exception_class": exception_class,
        "sdk_code": sdk_code,
        "request_sent": request_sent,
    }
