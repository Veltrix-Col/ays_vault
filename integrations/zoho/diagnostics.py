from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .backends.rest import RESTBackend
from .backends.sdk import SDKBackend
from .exceptions import ZohoError
from .schemas import ModuleMetadata
from .settings import ZohoSettings


class ModuleDiagnosticCategory(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED_MODULE = "unsupported_module"
    DISABLED_MODULE = "disabled_module"
    INTERNAL_MODULE = "internal_module"
    VIRTUAL_MODULE = "virtual_module"
    FIELDS_NOT_AVAILABLE = "fields_not_available"
    PERMISSION_DENIED = "permission_denied"
    SCOPE_INSUFFICIENT = "scope_insufficient"
    INVALID_API_NAME = "invalid_api_name"
    NOT_FOUND = "not_found"
    TEMPORARY_ERROR = "temporary_error"
    SDK_INCOMPATIBILITY = "sdk_incompatibility"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ErrorSnapshot:
    backend: str
    http_status: int | None
    zoho_code: str
    message: str
    zoho_status: str
    detail_keys: tuple[str, ...]
    request_id: str
    exception_class: str
    sdk_code: str
    request_sent: bool | None

    @classmethod
    def from_exception(cls, exc: ZohoError, *, backend: str) -> "ErrorSnapshot":
        return cls(
            backend=exc.backend or backend,
            http_status=exc.status_code,
            zoho_code=exc.zoho_code,
            message=str(exc),
            zoho_status=exc.zoho_status,
            detail_keys=exc.detail_keys,
            request_id=exc.request_id,
            exception_class=exc.sdk_exception_class,
            sdk_code=exc.sdk_code,
            request_sent=exc.request_sent,
        )


@dataclass(frozen=True)
class ModuleDiagnostic:
    module: ModuleMetadata
    sdk_fields: int | None
    rest_fields: int | None
    sdk_error: ErrorSnapshot | None
    rest_error: ErrorSnapshot | None
    classification: ModuleDiagnosticCategory
    recommendation: str

    @property
    def attempted_backends(self) -> tuple[str, ...]:
        backends = ["SDK"]
        if self.rest_fields is not None or self.rest_error is not None:
            backends.append("REST")
        return tuple(backends)

    @property
    def preferred_error(self) -> ErrorSnapshot | None:
        return self.rest_error or self.sdk_error


class ModuleDiagnosticsService:
    """Diagnóstico de metadatos; nunca consulta registros ni ejecuta escrituras."""

    def __init__(self, *, sdk: SDKBackend, rest: RESTBackend) -> None:
        self.sdk = sdk
        self.rest = rest

    @classmethod
    def build(cls, profile: str | None = None) -> "ModuleDiagnosticsService":
        config = ZohoSettings.from_django(profile).validate(
            require_refresh_token=True
        )
        rest = RESTBackend(config=config)
        return cls(
            sdk=SDKBackend(config=config, rest_fallback=rest),
            rest=rest,
        )

    def diagnose(self, module: ModuleMetadata) -> ModuleDiagnostic:
        sdk_fields: int | None = None
        rest_fields: int | None = None
        sdk_error: ErrorSnapshot | None = None
        rest_error: ErrorSnapshot | None = None

        try:
            sdk_fields = len(self.sdk.list_fields_sdk_only(module.api_name))
        except ZohoError as exc:
            sdk_error = ErrorSnapshot.from_exception(exc, backend="sdk")

        if sdk_error is not None or sdk_fields == 0:
            try:
                rest_fields = len(self.rest.list_fields(module.api_name))
            except ZohoError as exc:
                rest_error = ErrorSnapshot.from_exception(exc, backend="rest")

        category = classify_diagnostic(
            module,
            sdk_fields=sdk_fields,
            rest_fields=rest_fields,
            sdk_error=sdk_error,
            rest_error=rest_error,
        )
        return ModuleDiagnostic(
            module=module,
            sdk_fields=sdk_fields,
            rest_fields=rest_fields,
            sdk_error=sdk_error,
            rest_error=rest_error,
            classification=category,
            recommendation=recommendation_for(category),
        )


def classify_failure(
    module: ModuleMetadata,
    exc: ZohoError,
) -> ModuleDiagnosticCategory:
    snapshot = ErrorSnapshot.from_exception(exc, backend=exc.backend or "unknown")
    return _classify_metadata_or_error(module, snapshot)


def classify_diagnostic(
    module: ModuleMetadata,
    *,
    sdk_fields: int | None,
    rest_fields: int | None,
    sdk_error: ErrorSnapshot | None,
    rest_error: ErrorSnapshot | None,
) -> ModuleDiagnosticCategory:
    if sdk_fields and sdk_fields > 0:
        return ModuleDiagnosticCategory.SUPPORTED
    if rest_fields and rest_fields > 0:
        if sdk_error is not None or sdk_fields == 0:
            return ModuleDiagnosticCategory.SDK_INCOMPATIBILITY
        return ModuleDiagnosticCategory.SUPPORTED
    metadata_category = _metadata_category(module)
    if metadata_category in {
        ModuleDiagnosticCategory.DISABLED_MODULE,
        ModuleDiagnosticCategory.INTERNAL_MODULE,
        ModuleDiagnosticCategory.VIRTUAL_MODULE,
    }:
        return metadata_category
    error = rest_error or sdk_error
    if error is not None:
        return _classify_metadata_or_error(module, error)
    if metadata_category is not None:
        return metadata_category
    if sdk_fields == 0 and rest_fields == 0:
        return ModuleDiagnosticCategory.FIELDS_NOT_AVAILABLE
    return ModuleDiagnosticCategory.UNKNOWN


def recommendation_for(category: ModuleDiagnosticCategory) -> str:
    return {
        ModuleDiagnosticCategory.SUPPORTED: "consultar",
        ModuleDiagnosticCategory.SDK_INCOMPATIBILITY: "usar fallback REST e investigar SDK",
        ModuleDiagnosticCategory.UNSUPPORTED_MODULE: "omitir",
        ModuleDiagnosticCategory.DISABLED_MODULE: "revisar habilitación o licencia",
        ModuleDiagnosticCategory.INTERNAL_MODULE: "omitir",
        ModuleDiagnosticCategory.VIRTUAL_MODULE: "omitir",
        ModuleDiagnosticCategory.FIELDS_NOT_AVAILABLE: "omitir",
        ModuleDiagnosticCategory.PERMISSION_DENIED: "revisar permisos del autorizador",
        ModuleDiagnosticCategory.SCOPE_INSUFFICIENT: "revisar scopes de solo lectura",
        ModuleDiagnosticCategory.INVALID_API_NAME: "revisar api_name reportado por Zoho",
        ModuleDiagnosticCategory.NOT_FOUND: "revisar disponibilidad del módulo",
        ModuleDiagnosticCategory.TEMPORARY_ERROR: "reintentar más tarde",
        ModuleDiagnosticCategory.UNKNOWN: "investigar sin ampliar permisos",
    }[category]


def _metadata_category(
    module: ModuleMetadata,
) -> ModuleDiagnosticCategory | None:
    generated = module.generated_type.strip().lower()
    status = module.status.strip().lower()
    if "internal" in generated:
        return ModuleDiagnosticCategory.INTERNAL_MODULE
    if "virtual" in generated:
        return ModuleDiagnosticCategory.VIRTUAL_MODULE
    if status in {"disabled", "inactive", "unavailable"}:
        return ModuleDiagnosticCategory.DISABLED_MODULE
    if not module.api_supported:
        return ModuleDiagnosticCategory.UNSUPPORTED_MODULE
    return None


def _classify_metadata_or_error(
    module: ModuleMetadata,
    error: ErrorSnapshot,
) -> ModuleDiagnosticCategory:
    metadata = _metadata_category(module)
    if metadata in {
        ModuleDiagnosticCategory.DISABLED_MODULE,
        ModuleDiagnosticCategory.INTERNAL_MODULE,
        ModuleDiagnosticCategory.VIRTUAL_MODULE,
    }:
        return metadata
    code = (error.zoho_code or error.sdk_code).upper()
    message = error.message.lower()
    if code in {"OAUTH_SCOPE_MISMATCH", "SCOPE_MISMATCH", "INVALID_SCOPE"}:
        return ModuleDiagnosticCategory.SCOPE_INSUFFICIENT
    if code in {"NO_PERMISSION", "FORBIDDEN", "AUTHORIZATION_FAILED"}:
        return ModuleDiagnosticCategory.PERMISSION_DENIED
    if code == "INVALID_MODULE":
        return ModuleDiagnosticCategory.UNSUPPORTED_MODULE
    if code in {"NOT_SUPPORTED", "FIELDS_NOT_AVAILABLE", "NO_FIELDS"}:
        return ModuleDiagnosticCategory.FIELDS_NOT_AVAILABLE
    if code in {"INVALID_API_NAME", "INVALID_URL_PATTERN"}:
        return ModuleDiagnosticCategory.INVALID_API_NAME
    if code in {"NOT_FOUND", "RESOURCE_NOT_FOUND"} or error.http_status == 404:
        return ModuleDiagnosticCategory.NOT_FOUND
    if (
        error.http_status == 429
        or (error.http_status is not None and error.http_status >= 500)
        or code in {"TOO_MANY_REQUESTS", "INTERNAL_ERROR", "SERVICE_UNAVAILABLE"}
    ):
        return ModuleDiagnosticCategory.TEMPORARY_ERROR
    if error.http_status == 403:
        if "alcance oauth" in message:
            return ModuleDiagnosticCategory.SCOPE_INSUFFICIENT
        return ModuleDiagnosticCategory.PERMISSION_DENIED
    if "no está habilitado" in message:
        return ModuleDiagnosticCategory.DISABLED_MODULE
    if metadata is not None:
        return metadata
    return ModuleDiagnosticCategory.UNKNOWN
