from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from integrations.zoho import get_zoho
from integrations.zoho.exceptions import ZohoConfigurationError


ALLOWED_PROFILES = frozenset({"sandbox", "production"})
PROFILE_UI = {
    "sandbox": {"label": "Sandbox", "css_class": "sandbox"},
    "production": {"label": "Producción", "css_class": "production"},
}


def normalize_colectivos_profile(value: object) -> str:
    profile = str(value or "").strip().lower()
    if profile not in ALLOWED_PROFILES:
        raise ImproperlyConfigured(
            "ZOHO_ACTIVE_PROFILE debe ser sandbox o production."
        )
    return profile


def get_colectivos_profile() -> str:
    return normalize_colectivos_profile(getattr(settings, "ZOHO_ACTIVE_PROFILE", "sandbox"))


def get_colectivos_environment() -> dict[str, str]:
    profile = get_colectivos_profile()
    return {"profile": profile, **PROFILE_UI[profile]}


def get_colectivos_zoho():
    """Build and validate the configured facade without cross-profile fallback."""

    profile = get_colectivos_profile()
    facade = get_zoho(profile=profile)
    if facade.profile != profile or facade.environment != profile:
        raise ZohoConfigurationError(
            "El perfil Zoho resuelto no coincide con Cotización – Colectivos."
        )
    organization = facade.organization.get()
    if organization.environment != profile:
        raise ZohoConfigurationError(
            "El entorno reportado por Zoho no coincide con Cotización – Colectivos."
        )
    return facade
