"""Guardas comunes para escrituras Colectivos por perfil Zoho."""
from __future__ import annotations

from django.conf import settings
from integrations.zoho.settings import ZohoSettings


PROFILE_CONFIRMATIONS = {
    "sandbox": {
        "task": "SANDBOX_TASK_WRITE",
        "contact": "SANDBOX_CONTACT_WRITE",
        "risk": "SANDBOX_RISK_WRITE",
        "subrisk": "SANDBOX_SUBRISK_WRITE",
        "attachment": "SANDBOX_ATTACHMENT_WRITE",
    },
    "production": {
        "task": "PRODUCTION_TASK_WRITE",
        "contact": "PRODUCTION_CONTACT_WRITE",
        "risk": "PRODUCTION_RISK_WRITE",
        "subrisk": "PRODUCTION_SUBRISK_WRITE",
        "attachment": "PRODUCTION_ATTACHMENT_WRITE",
    },
}


def active_profile() -> str:
    return str(getattr(settings, "ZOHO_ACTIVE_PROFILE", "sandbox")).strip().lower()


def confirmation_setting_name(entity: str, profile: str) -> str:
    return f"COLECTIVOS_{profile.upper()}_{entity.upper()}_WRITE_CONFIRMATION"


def expected_confirmation(
    entity: str,
    profile: str,
    *,
    legacy_setting: str = "",
    expected_override: str = "",
) -> str:
    profile = str(profile or "").strip().lower()
    if profile not in PROFILE_CONFIRMATIONS or entity not in PROFILE_CONFIRMATIONS[profile]:
        return ""
    canonical = PROFILE_CONFIRMATIONS[profile][entity]
    required = (
        str(expected_override or "").strip()
        if profile == "sandbox" and expected_override
        else canonical
    )
    if not required:
        required = canonical
    if expected_override and profile != "sandbox":
        return ""
    if expected_override and legacy_setting:
        configured = str(getattr(settings, legacy_setting, "") or "").strip()
        return required if configured == required else ""
    configured = str(
        getattr(settings, confirmation_setting_name(entity, profile), "")
        or ""
    ).strip()
    # The legacy Sandbox setting remains a supported explicit override.  This
    # matters when both names exist in settings (for example, an environment
    # still exporting the old variable): an incorrect legacy confirmation must
    # fail closed rather than silently inheriting a valid canonical default.
    if profile == "sandbox" and legacy_setting:
        legacy = str(getattr(settings, legacy_setting, "") or "").strip()
        if legacy:
            configured = legacy
    if configured:
        return required if configured == required else ""
    # Backward compatibility is intentionally read-only and only applies to
    # the historic Sandbox variables. Production never accepts a Sandbox value.
    if profile == "sandbox" and legacy_setting:
        legacy = str(getattr(settings, legacy_setting, "") or "").strip()
        if legacy == required:
            return required
    return ""


def configured_confirmation(entity: str, profile: str, *, legacy_setting: str = "") -> str:
    return expected_confirmation(entity, profile, legacy_setting=legacy_setting)


def require_write_guard(
    *,
    entity: str,
    profile: str,
    confirmation: str,
    feature_flag: str,
    legacy_setting: str = "",
    expected_override: str = "",
    disabled_error: type[Exception] = RuntimeError,
) -> str:
    profile = str(profile or "").strip().lower()
    if profile not in PROFILE_CONFIRMATIONS:
        raise disabled_error("El perfil Zoho no está habilitado para esta escritura.")
    if active_profile() != profile:
        raise disabled_error("El perfil Zoho activo no coincide con la escritura solicitada.")
    # Validate an explicitly supplied legacy Sandbox confirmation before any
    # profile facade/configuration work.  A stale or incorrect legacy value
    # must never be masked by a canonical environment default.
    if profile == "sandbox" and legacy_setting:
        legacy = str(getattr(settings, legacy_setting, "") or "").strip()
        if legacy and legacy != PROFILE_CONFIRMATIONS[profile][entity]:
            raise disabled_error("La confirmación de escritura no coincide con el perfil activo.")
    config = ZohoSettings.from_django(profile)
    if not config.write_enabled:
        raise disabled_error(f"La escritura del perfil {profile} está deshabilitada.")
    if not getattr(settings, feature_flag, False):
        raise disabled_error("La publicación está deshabilitada.")
    expected = expected_confirmation(
        entity, profile, legacy_setting=legacy_setting,
        expected_override=expected_override,
    )
    if not expected or str(confirmation or "").strip() != expected:
        raise disabled_error("La confirmación de escritura no coincide con el perfil activo.")
    return expected
