from __future__ import annotations

from collections.abc import Iterable


class ZohoError(Exception):
    """Base segura para errores de integración."""

    category = "zoho_error"

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        request_id: str = "",
        zoho_code: str = "",
        zoho_status: str = "",
        detail_keys: Iterable[str] = (),
        backend: str = "",
        operation: str = "",
        module: str = "",
        sdk_exception_class: str = "",
        sdk_code: str = "",
        request_sent: bool | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.request_id = request_id
        self.zoho_code = zoho_code
        self.zoho_status = zoho_status
        self.detail_keys = tuple(detail_keys)
        self.backend = backend
        self.operation = operation
        self.module = module
        self.sdk_exception_class = sdk_exception_class
        self.sdk_code = sdk_code
        self.request_sent = request_sent


class ZohoConfigurationError(ZohoError):
    category = "configuration"


class ZohoAuthenticationError(ZohoError):
    category = "authentication"


class ZohoAuthorizationError(ZohoError):
    category = "authorization"


class ZohoNotFoundError(ZohoError):
    category = "not_found"


class ZohoRateLimitError(ZohoError):
    category = "rate_limit"


class ZohoTimeoutError(ZohoError):
    category = "timeout"


class ZohoAPIError(ZohoError):
    category = "api"


class ZohoInvalidResponseError(ZohoError):
    category = "invalid_response"


class ZohoValidationError(ZohoError):
    category = "validation"


class ZohoSDKError(ZohoError):
    category = "sdk"


class ZohoReadOnlyViolation(ZohoError):
    category = "read_only_violation"
