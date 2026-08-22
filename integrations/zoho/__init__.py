"""Integración Zoho de AyS Vault: envuelve ays_zoho_sdk con configuración Django."""

from __future__ import annotations

from ays_zoho_sdk import ZohoFacade, ZohoServices
from ays_zoho_sdk.factory import get_zoho as _get_zoho
from ays_zoho_sdk.factory import reset_zoho_for_tests

from .settings import ZohoSettings

__all__ = ["ZohoFacade", "ZohoServices", "get_zoho", "reset_zoho_for_tests"]


def get_zoho(
    *,
    profile: str | None = None,
    backend: str | None = None,
) -> ZohoFacade:
    config = ZohoSettings.from_django(profile)
    return _get_zoho(config=config, backend=backend)
