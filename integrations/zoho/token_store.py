from __future__ import annotations

import threading
import time
from typing import Protocol

from .constants import TOKEN_EXPIRY_MARGIN_SECONDS
from .schemas import AccessToken
from .settings import ZohoSettings

REFRESH_TOKEN_DELIVERY_MAX_AGE_SECONDS = 600


class TokenStore(Protocol):
    def get_refresh_token(self) -> str: ...
    def get_access_token(self) -> AccessToken | None: ...
    def set_access_token(self, value: AccessToken) -> None: ...
    def commit_oauth_tokens(
        self, *, refresh_token: str, access_token: AccessToken
    ) -> None: ...
    def invalidate_access_token(self) -> None: ...
    def stage_refresh_token_delivery(
        self, session_binding: str, value: str
    ) -> None: ...
    def consume_refresh_token_delivery(self, session_binding: str) -> str: ...
    def discard_refresh_token_delivery(self, session_binding: str) -> None: ...
    def refresh_guard(self): ...


class EnvironmentTokenStore:
    """Almacén no persistente respaldado por secretos del entorno."""

    def __init__(self, config: ZohoSettings | None = None) -> None:
        self.config = config or ZohoSettings.from_django()
        self.profile = self.config.profile
        self._lock = threading.RLock()
        self._runtime_refresh_token = ""
        self._runtime_refresh_token_validated = False
        self._access_token: AccessToken | None = None
        self._refresh_token_deliveries: dict[str, tuple[str, float]] = {}
        self._refresh_lock = threading.Lock()

    def get_refresh_token(self) -> str:
        with self._lock:
            runtime = (
                self._runtime_refresh_token
                if getattr(self, "_runtime_refresh_token_validated", False)
                else ""
            )
            return runtime or self.config.refresh_token

    def get_access_token(self) -> AccessToken | None:
        with self._lock:
            token = self._access_token
            if token and token.expires_at > time.time() + TOKEN_EXPIRY_MARGIN_SECONDS:
                return token
            self._access_token = None
            return None

    def set_access_token(self, value: AccessToken) -> None:
        with self._lock:
            self._access_token = value

    def commit_oauth_tokens(
        self,
        *,
        refresh_token: str,
        access_token: AccessToken,
    ) -> None:
        """Publica atómicamente únicamente tokens OAuth ya validados."""
        with self._lock:
            if refresh_token:
                self._runtime_refresh_token = refresh_token
                self._runtime_refresh_token_validated = True
            self._access_token = access_token

    def invalidate_access_token(self) -> None:
        with self._lock:
            self._access_token = None

    def stage_refresh_token_delivery(
        self, session_binding: str, value: str
    ) -> None:
        if not session_binding or not value:
            return
        now = time.time()
        with self._lock:
            self._refresh_token_deliveries = {
                key: item
                for key, item in self._refresh_token_deliveries.items()
                if now - item[1] <= REFRESH_TOKEN_DELIVERY_MAX_AGE_SECONDS
            }
            self._refresh_token_deliveries[session_binding] = (value, now)

    def consume_refresh_token_delivery(self, session_binding: str) -> str:
        if not session_binding:
            return ""
        with self._lock:
            item = self._refresh_token_deliveries.pop(session_binding, None)
        if not item:
            return ""
        value, created_at = item
        if time.time() - created_at > REFRESH_TOKEN_DELIVERY_MAX_AGE_SECONDS:
            return ""
        return value

    def discard_refresh_token_delivery(self, session_binding: str) -> None:
        if not session_binding:
            return
        with self._lock:
            self._refresh_token_deliveries.pop(session_binding, None)

    def refresh_guard(self):
        return self._refresh_lock


_stores_lock = threading.RLock()
_TOKEN_STORES: dict[str, EnvironmentTokenStore] = {}


def get_token_store(
    profile: str | None = None,
    *,
    config: ZohoSettings | None = None,
) -> EnvironmentTokenStore:
    cfg = config or ZohoSettings.from_django(profile)
    with _stores_lock:
        store = _TOKEN_STORES.get(cfg.profile)
        if store is None:
            store = EnvironmentTokenStore(cfg)
            _TOKEN_STORES[cfg.profile] = store
        return store


def reset_token_stores_for_tests(profile: str | None = None) -> None:
    with _stores_lock:
        if profile is None:
            _TOKEN_STORES.clear()
        else:
            _TOKEN_STORES.pop(profile, None)
