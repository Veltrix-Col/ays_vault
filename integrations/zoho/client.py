from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Mapping
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin

import httpx

from .constants import API_VERSION, USER_AGENT
from .exceptions import (
    ZohoAPIError,
    ZohoAuthenticationError,
    ZohoAuthorizationError,
    ZohoInvalidResponseError,
    ZohoNotFoundError,
    ZohoRateLimitError,
    ZohoTimeoutError,
    ZohoValidationError,
)
from .oauth import ZohoOAuthService
from .redaction import safe_zoho_error
from .settings import ZohoSettings

logger = logging.getLogger("integrations.zoho")
SAFE_REQUEST_ID = re.compile(r"[^A-Za-z0-9._-]")


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    value = response.headers.get("Retry-After", "").strip()
    if value:
        try:
            return min(10.0, max(0.0, float(value)))
        except ValueError:
            try:
                return min(
                    10.0,
                    max(0.0, parsedate_to_datetime(value).timestamp() - time.time()),
                )
            except (TypeError, ValueError, OverflowError):
                pass
    return min(4.0, 0.5 * (2**attempt))


class ZohoClient:
    """Cliente HTTP restringido a endpoints CRM V8 y operaciones de lectura."""

    def __init__(
        self,
        oauth: ZohoOAuthService | None = None,
        config: ZohoSettings | None = None,
        client_factory: Callable[..., httpx.Client] = httpx.Client,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config or ZohoSettings.from_django()
        self.oauth = oauth or ZohoOAuthService(config=self.config)
        self.client_factory = client_factory
        self.sleeper = sleeper

    def get(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        logical_endpoint: str,
        module: str = "",
    ) -> dict[str, Any]:
        return self.request(
            "GET",
            path,
            params=params,
            logical_endpoint=logical_endpoint,
            module=module,
        )

    def post_read(
        self,
        path: str,
        *,
        json: Mapping[str, Any],
        logical_endpoint: str,
        module: str = "",
    ) -> dict[str, Any]:
        return self.request(
            "POST",
            path,
            json=json,
            logical_endpoint=logical_endpoint,
            module=module,
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
        logical_endpoint: str,
        module: str = "",
    ) -> dict[str, Any]:
        if method not in {"GET", "POST"}:
            raise ZohoValidationError("La operación solicitada no está permitida.")
        expected_prefix = f"/crm/{API_VERSION}/"
        if not path.startswith(expected_prefix) or "//" in path:
            raise ZohoValidationError("El endpoint solicitado no está permitido.")

        auth_retried = False
        max_attempts = self.config.max_retries + 1
        for attempt in range(max_attempts):
            token = self.oauth.get_access_token()
            url = urljoin(f"{token.api_domain}/", path.lstrip("/"))
            started = time.monotonic()
            try:
                with self.client_factory(
                    timeout=self.config.timeout_seconds,
                    headers={
                        "Authorization": f"Zoho-oauthtoken {token.value}",
                        "Accept": "application/json",
                        "User-Agent": USER_AGENT,
                    },
                ) as client:
                    response = client.request(method, url, params=params, json=json)
            except httpx.TimeoutException as exc:
                logger.warning(
                    "Zoho timeout endpoint=%s intento=%s",
                    logical_endpoint,
                    attempt + 1,
                )
                if attempt + 1 < max_attempts:
                    self.sleeper(min(4.0, 0.5 * (2**attempt)))
                    continue
                raise ZohoTimeoutError(
                    "Zoho no respondió dentro del tiempo permitido."
                ) from exc
            except httpx.HTTPError as exc:
                if attempt + 1 < max_attempts:
                    self.sleeper(min(4.0, 0.5 * (2**attempt)))
                    continue
                raise ZohoAPIError(
                    "No fue posible conectar con Zoho."
                ) from exc

            duration_ms = int((time.monotonic() - started) * 1000)
            request_id = SAFE_REQUEST_ID.sub(
                "", response.headers.get("X-ZOHO-REQUEST-ID", "")
            )[:80]
            logger.info(
                "Zoho endpoint=%s status=%s duracion_ms=%s request_id=%s",
                logical_endpoint,
                response.status_code,
                duration_ms,
                request_id or "no-disponible",
            )
            if response.status_code == 401 and not auth_retried:
                auth_retried = True
                self.oauth.invalidate_access_token()
                continue
            if response.status_code in {429} or response.status_code >= 500:
                if attempt + 1 < max_attempts:
                    logger.warning(
                        "Zoho reintento endpoint=%s status=%s intento=%s",
                        logical_endpoint,
                        response.status_code,
                        attempt + 2,
                    )
                    self.sleeper(_retry_delay(response, attempt))
                    continue
            return self._decode(
                response,
                request_id=request_id,
                logical_endpoint=logical_endpoint,
                module=module,
            )
        raise ZohoAPIError("No fue posible completar la consulta a Zoho.")

    @staticmethod
    def _decode(
        response: httpx.Response,
        *,
        request_id: str,
        logical_endpoint: str = "",
        module: str = "",
    ) -> dict[str, Any]:
        status = response.status_code
        error = _safe_response_error(response) if status >= 400 else None
        error_kwargs = {
            "status_code": status,
            "request_id": request_id,
            "zoho_code": error.code if error else "",
            "zoho_status": error.status if error else "",
            "detail_keys": error.detail_keys if error else (),
            "backend": "rest",
            "operation": logical_endpoint,
            "module": module,
            "request_sent": True,
        }
        if status == 401:
            raise ZohoAuthenticationError(
                error.message if error else "Zoho rechazó el token de acceso.",
                **error_kwargs,
            )
        if status == 403:
            raise ZohoAuthorizationError(
                error.message
                if error
                else "La cuenta conectada no tiene permiso para esta consulta.",
                **error_kwargs,
            )
        if status == 404:
            raise ZohoNotFoundError(
                error.message
                if error
                else "El recurso solicitado no está disponible en Zoho.",
                **error_kwargs,
            )
        if status == 429:
            raise ZohoRateLimitError(
                error.message if error else "Zoho limitó temporalmente las consultas.",
                **error_kwargs,
            )
        if status >= 500:
            raise ZohoAPIError(
                error.message if error else "Zoho presentó un error temporal.",
                **error_kwargs,
            )
        if status >= 400:
            raise ZohoAPIError(
                error.message if error else "Zoho rechazó la consulta.",
                **error_kwargs,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ZohoInvalidResponseError(
                "Zoho devolvió una respuesta que no es JSON.",
                status_code=status,
                request_id=request_id,
            ) from exc
        if not isinstance(payload, dict):
            raise ZohoInvalidResponseError(
                "Zoho devolvió una estructura inesperada.",
                status_code=status,
                request_id=request_id,
            )
        return payload


def _safe_response_error(response: httpx.Response):
    try:
        payload = response.json()
    except ValueError:
        payload = None
    return safe_zoho_error(payload)
