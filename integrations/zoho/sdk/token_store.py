from __future__ import annotations

import threading


class InMemorySDKTokenStore:
    """TokenStore oficial sin persistencia en disco ni base de datos."""

    def __init__(self, refresh_token: str) -> None:
        self._refresh_token = refresh_token
        self._token = None
        self._lock = threading.RLock()

    def find_token(self, token):
        with self._lock:
            return self._token or token

    def find_token_by_id(self, token_id):
        with self._lock:
            if self._token is not None and self._token.get_id() == token_id:
                return self._token
            return None

    def save_token(self, token) -> None:
        with self._lock:
            # El SDK puede rotar el access token, pero el refresh token permanente
            # continúa siendo exclusivamente el valor recibido desde el entorno.
            token.set_refresh_token(self._refresh_token)
            self._token = token

    def delete_token(self, token_id) -> None:
        with self._lock:
            self._token = None

    def get_tokens(self):
        with self._lock:
            return [self._token] if self._token is not None else []

    def delete_tokens(self) -> None:
        with self._lock:
            self._token = None


def build_sdk_token_store(refresh_token: str):
    """Crea, de forma perezosa, el adaptador exigido por el SDK oficial."""
    from zohocrmsdk.src.com.zoho.api.authenticator.store.token_store import (
        TokenStore as OfficialTokenStore,
    )

    delegate = InMemorySDKTokenStore(refresh_token)

    class SDKTokenStoreAdapter(OfficialTokenStore):
        def find_token(self, token):
            return delegate.find_token(token)

        def find_token_by_id(self, token_id):
            return delegate.find_token_by_id(token_id)

        def save_token(self, token):
            delegate.save_token(token)

        def delete_token(self, token_id):
            delegate.delete_token(token_id)

        def get_tokens(self):
            return delegate.get_tokens()

        def delete_tokens(self):
            delegate.delete_tokens()

    return SDKTokenStoreAdapter()
