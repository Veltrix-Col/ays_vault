"""Adaptador Django para la configuración de ays_zoho_sdk.

Esta es la única pieza de la integración que conoce tanto Django como el
SDK: traduce ``django.conf.settings`` a la fuente de configuración
desacoplada que espera la librería externa.
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings as django_settings

from ays_zoho_sdk.settings import (
    ALLOWED_PROFILES,
    AttrSettingsSource,
    SettingsSource,
    ZohoSettings as _ZohoSettings,
    normalize_profile as _normalize_profile,
    reset_settings_warnings_for_tests,
)

__all__ = [
    "ALLOWED_PROFILES",
    "SettingsSource",
    "ZohoSettings",
    "normalize_profile",
    "reset_settings_warnings_for_tests",
    "validate_api_domain",
]


def _django_source() -> AttrSettingsSource:
    return AttrSettingsSource(django_settings)


class ZohoSettings(_ZohoSettings):
    @classmethod
    def from_django(cls, profile: str | None = None) -> "ZohoSettings":
        return cls.from_env(
            profile,
            source=_django_source(),
            base_dir=Path(django_settings.BASE_DIR),
        )


def normalize_profile(value: object | None = None) -> str:
    return _normalize_profile(value, source=_django_source())


def validate_api_domain(value: str, *, profile: str | None = None) -> str:
    return ZohoSettings.from_django(profile).validate_api_domain(value)
