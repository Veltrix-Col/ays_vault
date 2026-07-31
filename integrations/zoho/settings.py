from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from django.conf import settings as django_settings

from .constants import DEFAULT_SCOPES
from .exceptions import ZohoConfigurationError

logger = logging.getLogger("integrations.zoho")

ALLOWED_PROFILES = ("production", "sandbox", "qa", "demo", "future")
SDK_ENVIRONMENTS = {"production", "sandbox"}
BACKENDS = {"sdk", "rest"}
SDK_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
ACCOUNTS_BASE_URL = "https://accounts.zoho.com"
API_BASE_URLS = {
    "production": "https://www.zohoapis.com",
    "sandbox": "https://sandbox.zohoapis.com",
}
_legacy_warnings: set[str] = set()
_legacy_warning_lock = threading.Lock()


def normalize_profile(value: object | None) -> str:
    profile = str(
        value
        if value is not None
        else getattr(django_settings, "ZOHO_ACTIVE_PROFILE", "production")
    ).strip().lower()
    if profile not in ALLOWED_PROFILES:
        raise ZohoConfigurationError("El perfil de Zoho solicitado no es válido.")
    return profile


def _validate_redirect_uri(value: str) -> str:
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    local_http = parsed.scheme == "http" and host in {"localhost", "127.0.0.1"}
    development = str(getattr(django_settings, "APP_ENV", "development")).lower() in {
        "development",
        "dev",
        "test",
        "testing",
    }
    if parsed.scheme != "https" and not (development and local_http):
        raise ZohoConfigurationError("La URI de retorno de Zoho debe usar HTTPS.")
    if parsed.username or parsed.password or parsed.fragment:
        raise ZohoConfigurationError("La URI de retorno de Zoho no es válida.")
    return value


def _positive_number(raw: object, name: str, *, maximum: float) -> float:
    try:
        value = float(str(raw))
    except (TypeError, ValueError) as exc:
        raise ZohoConfigurationError(f"{name} debe ser un número válido.") from exc
    if value <= 0 or value > maximum:
        raise ZohoConfigurationError(f"{name} está fuera del rango permitido.")
    return value


def _profile_value(profile: str, suffix: str, default: object = "") -> object:
    return getattr(django_settings, f"ZOHO_{profile.upper()}_{suffix}", default)


def _profile_credentials(profile: str) -> tuple[str, str, str, bool]:
    client_id = str(_profile_value(profile, "CLIENT_ID", "")).strip()
    client_secret = str(_profile_value(profile, "CLIENT_SECRET", ""))
    refresh_token = str(_profile_value(profile, "REFRESH_TOKEN", ""))
    prefixed = bool(client_id or client_secret or refresh_token)
    if profile != "production" or prefixed:
        return client_id, client_secret, refresh_token, False
    legacy_values = (
        str(getattr(django_settings, "ZOHO_CLIENT_ID", "")).strip(),
        str(getattr(django_settings, "ZOHO_CLIENT_SECRET", "")),
        str(getattr(django_settings, "ZOHO_REFRESH_TOKEN", "")),
    )
    legacy = bool(
        any(legacy_values) or getattr(django_settings, "ZOHO_ENABLED", False)
    )
    if any(legacy_values):
        _warn_legacy_configuration()
    return (*legacy_values, legacy)


def _warn_legacy_configuration() -> None:
    with _legacy_warning_lock:
        if "production" in _legacy_warnings:
            return
        _legacy_warnings.add("production")
    logger.warning(
        "Zoho perfil=production configuracion=heredada "
        "accion=migrar_a_variables_por_perfil"
    )


def _profile_environment(profile: str) -> str:
    default = profile if profile in {"production", "sandbox"} else ""
    return str(_profile_value(profile, "ENVIRONMENT", default)).strip().lower()


def _profile_resource_path(profile: str, *, legacy: bool = False) -> str:
    if legacy:
        return str(
            getattr(django_settings, "ZOHO_SDK_RESOURCE_PATH", "runtime/zoho_sdk")
        ).strip()
    return str(
        _profile_value(
            profile,
            "SDK_RESOURCE_PATH",
            f"runtime/zoho_sdk/{profile}",
        )
    ).strip()


def _profile_endpoint(profile: str, suffix: str, *, legacy: bool = False) -> str:
    if legacy:
        return str(getattr(django_settings, f"ZOHO_{suffix}", "")).strip()
    return str(_profile_value(profile, suffix, "")).strip()


def _validate_profile_isolation(selected: "ZohoSettings") -> None:
    paths: dict[Path, str] = {}
    refresh_tokens: dict[str, str] = {}
    base_dir = Path(django_settings.BASE_DIR).resolve()
    for profile in ALLOWED_PROFILES:
        _, _, refresh_token, legacy = _profile_credentials(profile)
        resource = _profile_resource_path(profile, legacy=legacy)
        if resource:
            raw = Path(resource).expanduser()
            resolved = raw.resolve() if raw.is_absolute() else (base_dir / raw).resolve()
            previous = paths.get(resolved)
            if previous and previous != profile:
                raise ZohoConfigurationError(
                    "Dos perfiles Zoho no pueden compartir el resource path."
                )
            paths[resolved] = profile
        if refresh_token:
            previous = refresh_tokens.get(refresh_token)
            if previous and previous != profile:
                raise ZohoConfigurationError(
                    "Dos perfiles Zoho no pueden compartir el refresh token."
                )
            refresh_tokens[refresh_token] = profile
    if not selected.sdk_resource_path:
        raise ZohoConfigurationError("El resource path del perfil Zoho es obligatorio.")


@dataclass(frozen=True)
class ZohoSettings:
    profile: str
    environment: str
    enabled: bool
    public_setup_enabled: bool
    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)
    redirect_uri: str
    accounts_base_url: str
    api_base_url: str
    scopes: tuple[str, ...]
    refresh_token: str = field(repr=False)
    timeout_seconds: float
    max_retries: int
    backend: str
    sdk_resource_path: str
    sdk_log_level: str
    expected_org_id: str = field(default="", repr=False)
    legacy_configuration: bool = False

    @classmethod
    def from_django(cls, profile: str | None = None) -> "ZohoSettings":
        selected = normalize_profile(profile)
        client_id, client_secret, refresh_token, legacy = _profile_credentials(
            selected
        )
        raw_scopes = getattr(django_settings, "ZOHO_OAUTH_SCOPES", "")
        scopes = tuple(
            item.strip() for item in str(raw_scopes).split(",") if item.strip()
        ) or DEFAULT_SCOPES
        timeout = _positive_number(
            getattr(django_settings, "ZOHO_REQUEST_TIMEOUT_SECONDS", "15"),
            "ZOHO_REQUEST_TIMEOUT_SECONDS",
            maximum=120,
        )
        retries = _positive_number(
            getattr(django_settings, "ZOHO_MAX_RETRIES", "2"),
            "ZOHO_MAX_RETRIES",
            maximum=5,
        )
        backend = str(getattr(django_settings, "ZOHO_BACKEND", "sdk")).strip().lower()
        if backend not in BACKENDS:
            raise ZohoConfigurationError("ZOHO_BACKEND debe ser sdk o rest.")
        sdk_log_level = str(
            getattr(django_settings, "ZOHO_SDK_LOG_LEVEL", "INFO")
        ).strip().upper()
        if sdk_log_level not in SDK_LOG_LEVELS:
            raise ZohoConfigurationError("ZOHO_SDK_LOG_LEVEL no es válido.")
        if legacy:
            enabled = bool(getattr(django_settings, "ZOHO_ENABLED", False))
        else:
            enabled = bool(_profile_value(selected, "ENABLED", False))
        value = cls(
            profile=selected,
            environment=_profile_environment(selected),
            enabled=enabled,
            public_setup_enabled=bool(
                getattr(django_settings, "ZOHO_PUBLIC_SETUP_ENABLED", False)
            ),
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=str(
                getattr(django_settings, "ZOHO_REDIRECT_URI", "")
            ).strip(),
            accounts_base_url=_profile_endpoint(
                selected,
                "ACCOUNTS_BASE_URL",
                legacy=legacy,
            ),
            api_base_url=_profile_endpoint(
                selected,
                "API_BASE_URL",
                legacy=legacy,
            ),
            scopes=scopes,
            refresh_token=refresh_token,
            timeout_seconds=timeout,
            max_retries=int(retries),
            backend=backend,
            sdk_resource_path=_profile_resource_path(selected, legacy=legacy),
            sdk_log_level=sdk_log_level,
            expected_org_id=str(
                _profile_value(selected, "EXPECTED_ORG_ID", "")
            ).strip(),
            legacy_configuration=legacy,
        )
        _validate_profile_isolation(value)
        return value

    @property
    def configured(self) -> bool:
        return bool(
            self.client_id
            and self.client_secret
            and self.redirect_uri
            and self.accounts_base_url
            and self.api_base_url
            and self.scopes
            and self.sdk_resource_path
        )

    @property
    def data_center(self) -> str:
        return urlsplit(self.api_base_url).hostname or ""

    def validate(self, *, require_refresh_token: bool = False) -> "ZohoSettings":
        if not self.enabled:
            raise ZohoConfigurationError(
                "La integración Zoho está deshabilitada para el perfil."
            )
        if not self.configured:
            raise ZohoConfigurationError("La configuración del perfil Zoho está incompleta.")
        if self.profile == "production" and self.environment != "production":
            raise ZohoConfigurationError(
                "El perfil production debe usar el entorno SDK production."
            )
        if self.profile == "sandbox" and self.environment != "sandbox":
            raise ZohoConfigurationError(
                "El perfil sandbox debe usar el entorno SDK sandbox."
            )
        if self.environment not in SDK_ENVIRONMENTS:
            raise ZohoConfigurationError("El entorno SDK del perfil Zoho no es válido.")
        if self.accounts_base_url.rstrip("/") != ACCOUNTS_BASE_URL:
            raise ZohoConfigurationError(
                "El perfil Zoho debe usar el Accounts Data Center .com."
            )
        expected_api = API_BASE_URLS[self.environment]
        if self.api_base_url.rstrip("/") != expected_api:
            raise ZohoConfigurationError(
                "La URL API no corresponde al entorno del perfil Zoho."
            )
        _validate_redirect_uri(self.redirect_uri)
        if any(not scope.endswith(".READ") for scope in self.scopes):
            raise ZohoConfigurationError(
                "Los permisos configurados para Zoho deben ser exclusivamente de lectura."
            )
        if require_refresh_token and not self.refresh_token:
            raise ZohoConfigurationError(
                "No existe un token de renovación configurado para el perfil Zoho."
            )
        return self

    def validate_api_domain(self, value: str) -> str:
        parsed = urlsplit(value)
        expected = urlsplit(API_BASE_URLS[self.environment])
        if (
            parsed.scheme != "https"
            or parsed.hostname != expected.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ZohoConfigurationError(
                "El dominio API recibido no corresponde al perfil Zoho."
            )
        return value.rstrip("/")

    def resolve_sdk_resource_path(self, *, create: bool = False) -> Path:
        raw = Path(self.sdk_resource_path).expanduser()
        base_dir = Path(django_settings.BASE_DIR).resolve()
        target = raw.resolve() if raw.is_absolute() else (base_dir / raw).resolve()
        if not raw.is_absolute() and target != base_dir and base_dir not in target.parents:
            raise ZohoConfigurationError("ZOHO_SDK_RESOURCE_PATH sale del proyecto.")
        if create:
            try:
                target.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise ZohoConfigurationError(
                    "No fue posible preparar el resource path de Zoho."
                ) from exc
            if not target.is_dir():
                raise ZohoConfigurationError(
                    "ZOHO_SDK_RESOURCE_PATH no es un directorio."
                )
        return target


def validate_api_domain(value: str, *, profile: str | None = None) -> str:
    return ZohoSettings.from_django(profile).validate_api_domain(value)


def reset_settings_warnings_for_tests() -> None:
    with _legacy_warning_lock:
        _legacy_warnings.clear()
