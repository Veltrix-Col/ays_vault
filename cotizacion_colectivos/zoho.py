from __future__ import annotations

import logging
import time

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured

from integrations.zoho import get_zoho
from integrations.zoho.exceptions import ZohoConfigurationError, ZohoError


ALLOWED_PROFILES = frozenset({"sandbox", "production"})
PROFILE_UI = {
    "sandbox": {"label": "Sandbox", "css_class": "sandbox"},
    "production": {"label": "Producción", "css_class": "production"},
}
logger = logging.getLogger("cotizacion_colectivos")


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


def _cache_identity(profile: str, facade) -> str:
    backend = str(getattr(facade, "backend_name", getattr(settings, "ZOHO_BACKEND", "sdk")))
    return f"{profile}:{backend.strip().lower()}"


def get_colectivos_zoho(*, timings: dict[str, int] | None = None):
    """Build and validate the configured facade without cross-profile fallback."""

    profile = get_colectivos_profile()
    facade_started = time.monotonic()
    facade = get_zoho(profile=profile)
    facade_ms = round((time.monotonic() - facade_started) * 1000)
    if timings is not None:
        timings["facade_ms"] = facade_ms
    if facade.profile != profile or facade.environment != profile:
        raise ZohoConfigurationError(
            "El perfil Zoho resuelto no coincide con Cotización – Colectivos."
        )
    cache_key = f"colectivos:organization:v1:{_cache_identity(profile, facade)}"
    reported_environment = cache.get(cache_key)
    if reported_environment == profile:
        if timings is not None:
            timings["organization_ms"] = 0
            timings["organization_cache_hit"] = 1
        logger.info(
            "colectivos_organization application=cotizacion_colectivos "
            "operation=organization duration_ms=0 cache=hit error=none profile=%s",
            profile,
        )
        return facade

    started = time.monotonic()
    try:
        organization = facade.organization.get()
    except ZohoError as exc:
        logger.warning(
            "colectivos_organization application=cotizacion_colectivos "
            "operation=organization duration_ms=%d error=%s profile=%s",
            round((time.monotonic() - started) * 1000),
            getattr(exc, "category", "unknown"),
            profile,
        )
        raise
    organization_ms = round((time.monotonic() - started) * 1000)
    if timings is not None:
        timings["organization_ms"] = organization_ms
        timings["organization_cache_hit"] = 0
    logger.info(
        "colectivos_organization application=cotizacion_colectivos "
        "operation=organization duration_ms=%d cache=miss error=none profile=%s",
        organization_ms,
        profile,
    )
    if organization.environment != profile:
        raise ZohoConfigurationError(
            "El entorno reportado por Zoho no coincide con Cotización – Colectivos."
        )
    cache.set(
        cache_key,
        profile,
        timeout=getattr(settings, "COLECTIVOS_ORGANIZATION_CACHE_TTL_SECONDS", 300),
    )
    return facade


def cached_metadata_modules(facade):
    profile = normalize_colectivos_profile(getattr(facade, "profile", get_colectivos_profile()))
    key = f"colectivos:metadata:modules:v1:{_cache_identity(profile, facade)}"
    value = cache.get(key)
    if value is None:
        value = tuple(facade.metadata.list_modules())
        cache.set(key, value, getattr(settings, "COLECTIVOS_METADATA_CACHE_TTL_SECONDS", 1800))
    return value


def cached_metadata_fields(facade, module: str):
    profile = normalize_colectivos_profile(getattr(facade, "profile", get_colectivos_profile()))
    key = f"colectivos:metadata:fields:v1:{_cache_identity(profile, facade)}:{module}"
    value = cache.get(key)
    if value is None:
        value = tuple(facade.metadata.list_fields(module))
        cache.set(key, value, getattr(settings, "COLECTIVOS_METADATA_CACHE_TTL_SECONDS", 1800))
    return value
