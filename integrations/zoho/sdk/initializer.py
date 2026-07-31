from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from collections.abc import Callable, Iterator
from urllib.parse import urlsplit

from ..exceptions import ZohoConfigurationError, ZohoSDKError
from ..settings import ZohoSettings
from .token_store import build_sdk_token_store

logger = logging.getLogger("integrations.zoho")


@dataclass(frozen=True)
class SDKRuntime:
    profile: str
    environment: str
    data_center: str
    token_store: object
    resource_path: str


_lock = threading.RLock()
_runtimes: dict[str, SDKRuntime] = {}
_active_profile: str | None = None


def initialize_sdk(config: ZohoSettings | None = None) -> SDKRuntime:
    """Activa un perfil del singleton oficial conservando stores separados."""
    global _active_profile
    cfg = (config or ZohoSettings.from_django()).validate(require_refresh_token=True)
    with _lock:
        runtime = _runtimes.get(cfg.profile)
        if runtime is not None and _active_profile == cfg.profile:
            return runtime
        resource_path = cfg.resolve_sdk_resource_path(create=True)
        try:
            from zohocrmsdk.src.com.zoho.api.authenticator.oauth_token import OAuthToken
            from zohocrmsdk.src.com.zoho.crm.api.initializer import Initializer
            from zohocrmsdk.src.com.zoho.crm.api.sdk_config import SDKConfig

            store = (
                runtime.token_store
                if runtime is not None
                else build_sdk_token_store(cfg.refresh_token)
            )
            sdk_logger = logging.getLogger("SDKLogger")
            sdk_logger.handlers.clear()
            sdk_logger.addHandler(logging.NullHandler())
            sdk_logger.propagate = False
            token = OAuthToken(
                client_id=cfg.client_id,
                client_secret=cfg.client_secret,
                refresh_token=cfg.refresh_token,
                redirect_url=cfg.redirect_uri,
                api_domain=cfg.api_base_url,
            )
            # El SDK mantiene un singleton global. El cambio de perfil solo puede
            # ocurrir bajo _lock y cada operación conserva este lock completo.
            Initializer.initializer = None
            sdk_environment = _environment(cfg)
            Initializer.initialize(
                environment=sdk_environment,
                token=token,
                store=store,
                sdk_config=SDKConfig(
                    auto_refresh_fields=False,
                    pick_list_validation=False,
                    connect_timeout=cfg.timeout_seconds,
                    read_timeout=cfg.timeout_seconds,
                    update_api_domain=False,
                ),
                resource_path=str(resource_path),
                logger=None,
            )
        except ZohoConfigurationError:
            raise
        except Exception:
            logger.error(
                "Zoho backend=sdk operacion=initialize perfil=%s categoria=sdk",
                cfg.profile,
            )
            raise ZohoSDKError("No fue posible inicializar el SDK de Zoho.") from None
        runtime = SDKRuntime(
            cfg.profile,
            cfg.environment,
            urlsplit(str(getattr(sdk_environment, "url", ""))).hostname or "",
            store,
            str(resource_path),
        )
        _runtimes[cfg.profile] = runtime
        _active_profile = cfg.profile
        logger.info(
            "Zoho backend=sdk operacion=initialize perfil=%s entorno=%s estado=ok",
            cfg.profile,
            cfg.environment,
        )
        return runtime


@contextmanager
def sdk_execution_context(
    config: ZohoSettings,
    initializer: Callable[[ZohoSettings], object] = initialize_sdk,
) -> Iterator[None]:
    """Impide que otro hilo cambie el singleton SDK durante una consulta."""
    with _lock:
        initializer(config)
        yield


def _environment(config: ZohoSettings):
    from zohocrmsdk.src.com.zoho.crm.api import dc

    if config.environment == "production":
        return dc.USDataCenter.PRODUCTION()
    if config.environment == "sandbox":
        return dc.USDataCenter.SANDBOX()
    raise ZohoConfigurationError("El entorno SDK del perfil Zoho no es válido.")


def reset_sdk_for_tests(profile: str | None = None) -> None:
    """Reinicia runtimes por perfil; uso exclusivo de pruebas."""
    global _active_profile
    with _lock:
        if profile is None:
            _runtimes.clear()
            _active_profile = None
        else:
            _runtimes.pop(profile, None)
            if _active_profile == profile:
                _active_profile = None
        try:
            from zohocrmsdk.src.com.zoho.crm.api.initializer import Initializer

            if profile is None or _active_profile is None:
                Initializer.initializer = None
        except ImportError:
            pass


def sdk_runtime_diagnostics(profile: str) -> tuple[str, str]:
    """Devuelve únicamente entorno y host no sensibles del runtime activo."""
    with _lock:
        runtime = _runtimes.get(profile)
        if runtime is None:
            return "no_inicializado", "no_detectado"
        return runtime.environment, runtime.data_center or "no_detectado"
