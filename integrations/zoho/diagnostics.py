from __future__ import annotations

from ays_zoho_sdk.diagnostics import (
    ErrorSnapshot,
    ModuleDiagnostic,
    ModuleDiagnosticCategory,
    ModuleDiagnosticsService as _ModuleDiagnosticsService,
    classify_diagnostic,
    classify_failure,
    recommendation_for,
)

from .settings import ZohoSettings

__all__ = [
    "ErrorSnapshot",
    "ModuleDiagnostic",
    "ModuleDiagnosticCategory",
    "ModuleDiagnosticsService",
    "classify_diagnostic",
    "classify_failure",
    "recommendation_for",
]


class ModuleDiagnosticsService(_ModuleDiagnosticsService):
    @classmethod
    def build(cls, profile: str | None = None) -> "ModuleDiagnosticsService":
        config = ZohoSettings.from_django(profile).validate(
            require_refresh_token=True
        )
        from ays_zoho_sdk.backends.rest import RESTBackend
        from ays_zoho_sdk.backends.sdk import SDKBackend

        rest = RESTBackend(config=config)
        return cls(sdk=SDKBackend(config=config, rest_fallback=rest), rest=rest)
