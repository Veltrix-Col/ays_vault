from __future__ import annotations

import threading

from .backends import RESTBackend, SDKBackend
from .facade import ZohoFacade
from .settings import ZohoSettings

_lock = threading.RLock()
_facades: dict[tuple[str, str], ZohoFacade] = {}


def get_zoho(
    *,
    profile: str | None = None,
    backend: str | None = None,
) -> ZohoFacade:
    config = ZohoSettings.from_django(profile).validate(require_refresh_token=True)
    selected = (backend or config.backend).strip().lower()
    if selected not in {"sdk", "rest"}:
        # Se reutiliza la validación y el mensaje seguro central.
        from .exceptions import ZohoConfigurationError

        raise ZohoConfigurationError("ZOHO_BACKEND debe ser sdk o rest.")
    with _lock:
        cache_key = (config.profile, selected)
        facade = _facades.get(cache_key)
        if facade is None:
            rest = RESTBackend(config=config)
            implementation = rest if selected == "rest" else SDKBackend(
                config=config, rest_fallback=rest
            )
            facade = ZohoFacade(implementation, config=config)
            _facades[cache_key] = facade
        return facade


def reset_zoho_for_tests(profile: str | None = None) -> None:
    with _lock:
        if profile is None:
            _facades.clear()
        else:
            for key in tuple(_facades):
                if key[0] == profile:
                    _facades.pop(key, None)
    from .sdk.initializer import reset_sdk_for_tests
    from .settings import reset_settings_warnings_for_tests
    from .token_store import reset_token_stores_for_tests

    reset_sdk_for_tests(profile)
    reset_token_stores_for_tests(profile)
    reset_settings_warnings_for_tests()
