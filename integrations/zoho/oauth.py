from __future__ import annotations

import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit

import httpx

from .constants import USER_AGENT
from .exceptions import (
    ZohoAuthenticationError,
    ZohoConfigurationError,
    ZohoInvalidResponseError,
    ZohoTimeoutError,
)
from .normalization import normalize_organization_environment, safe_value_class
from .schemas import AccessToken
from .settings import ZohoSettings
from .token_store import TokenStore, get_token_store

logger = logging.getLogger("integrations.zoho")
CANDIDATE_ORGANIZATION_URL = "https://www.zohoapis.com/crm/v8/org"
ALLOWED_OAUTH_API_DOMAINS = {
    "www.zohoapis.com",
    "sandbox.zohoapis.com",
}


@dataclass(frozen=True)
class _CandidateOrganization:
    environment: str
    organization_id: str
    company_name: str
    environment_value_class: str


class ZohoOAuthService:
    def __init__(
        self,
        config: ZohoSettings | None = None,
        token_store: TokenStore | None = None,
        client_factory: Callable[..., httpx.Client] = httpx.Client,
    ) -> None:
        self.config = config or ZohoSettings.from_django()
        self.token_store = token_store or get_token_store(config=self.config)
        self.client_factory = client_factory
        self._received_refresh_token = ""
        self._last_oauth_endpoint = ""
        self._last_oauth_response_domain = ""

    @staticmethod
    def generate_state() -> str:
        return secrets.token_urlsafe(32)

    def authorization_url(self, state: str) -> str:
        self.config.validate()
        if not state:
            raise ZohoConfigurationError("No fue posible iniciar la autorización segura.")
        query = urlencode(
            {
                "scope": ",".join(self.config.scopes),
                "client_id": self.config.client_id,
                "response_type": "code",
                "access_type": "offline",
                "prompt": "consent",
                "redirect_uri": self.config.redirect_uri,
                "state": state,
            }
        )
        return f"{self.config.accounts_base_url}/oauth/v2/auth?{query}"

    def exchange_code(self, code: str) -> AccessToken:
        self._received_refresh_token = ""
        if not code:
            raise ZohoAuthenticationError("Zoho no devolvió un código de autorización.")
        payload = self._token_request(
            {
                "grant_type": "authorization_code",
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "redirect_uri": self.config.redirect_uri,
                "code": code,
            }
        )
        candidate_refresh_token = str(payload.get("refresh_token") or "")
        if (
            not candidate_refresh_token
            and not self.token_store.get_refresh_token()
        ):
            raise ZohoAuthenticationError(
                "Zoho no entregó un token de renovación. Revise el consentimiento offline."
            )
        try:
            candidate_access_token = self._validated_access_token(payload)
        except (ZohoConfigurationError, ZohoInvalidResponseError):
            # Ningún candidato se ha publicado todavía en el TokenStore.
            self.discard_candidate_tokens()
            raise
        self.token_store.commit_oauth_tokens(
            refresh_token=candidate_refresh_token,
            access_token=candidate_access_token,
        )
        self._received_refresh_token = candidate_refresh_token
        return candidate_access_token

    def consume_received_refresh_token(self) -> str:
        value = self._received_refresh_token
        self._received_refresh_token = ""
        return value

    def discard_candidate_tokens(self) -> None:
        self._received_refresh_token = ""

    def get_access_token(self) -> AccessToken:
        cached = self.token_store.get_access_token()
        if cached:
            return cached
        with self.token_store.refresh_guard():
            cached = self.token_store.get_access_token()
            if cached:
                return cached
            refresh_token = self.token_store.get_refresh_token()
            self.config.validate()
            if not refresh_token:
                raise ZohoConfigurationError(
                    "No existe un token de renovación configurado para Zoho."
                )
            payload = self._token_request(
                {
                    "grant_type": "refresh_token",
                    "client_id": self.config.client_id,
                    "client_secret": self.config.client_secret,
                    "refresh_token": refresh_token,
                }
            )
            token = self._validated_access_token(payload)
            self.token_store.set_access_token(token)
            return token

    def invalidate_access_token(self) -> None:
        self.token_store.invalidate_access_token()

    def _token_request(self, data: dict[str, str]) -> dict[str, object]:
        self.config.validate()
        endpoint = f"{self.config.accounts_base_url}/oauth/v2/token"
        self._last_oauth_endpoint = _safe_endpoint(endpoint)
        self._last_oauth_response_domain = "no_disponible"
        try:
            with self.client_factory(
                timeout=self.config.timeout_seconds,
                headers={"User-Agent": USER_AGENT},
            ) as client:
                response = client.post(
                    endpoint,
                    data=data,
                )
                request = getattr(response, "request", None)
                self._last_oauth_response_domain = _safe_origin(
                    str(getattr(request, "url", "") or "")
                )
        except httpx.TimeoutException as exc:
            raise ZohoTimeoutError(
                "Zoho no respondió dentro del tiempo permitido."
            ) from exc
        except httpx.HTTPError as exc:
            raise ZohoAuthenticationError(
                "No fue posible conectar con el servicio de autorización de Zoho."
            ) from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise ZohoInvalidResponseError(
                "Zoho devolvió una respuesta de autorización inválida.",
                status_code=response.status_code,
            ) from exc
        if response.status_code >= 400 or not isinstance(payload, dict) or payload.get("error"):
            raise ZohoAuthenticationError(
                "Zoho rechazó la autorización.",
                status_code=response.status_code,
            )
        return payload

    def _validated_access_token(
        self,
        payload: dict[str, object],
    ) -> AccessToken:
        value = str(payload.get("access_token") or "")
        if not value:
            raise ZohoInvalidResponseError(
                "Zoho no devolvió un token de acceso válido."
            )
        try:
            expires_in = max(60, int(payload.get("expires_in") or 3600))
        except (TypeError, ValueError) as exc:
            raise ZohoInvalidResponseError(
                "Zoho devolvió una expiración de token inválida."
            ) from exc
        received_api_domain = str(payload.get("api_domain") or "")
        try:
            _validate_oauth_api_domain(received_api_domain)
        except ZohoConfigurationError:
            self._log_domain_mismatch(received_api_domain)
            raise
        token = AccessToken(
            value=value,
            expires_at=time.time() + expires_in,
            # api_domain describe el intercambio, no necesariamente el entorno.
            # Las operaciones usan siempre la URL cerrada del perfil validado.
            api_domain=self.config.api_base_url.rstrip("/"),
        )
        organization = self._validate_candidate_token_environment(value)
        self._validate_candidate_organization(organization)
        return token

    def _validate_candidate_token_environment(
        self,
        candidate_access_token: str,
    ) -> _CandidateOrganization:
        """Identifica el entorno sin publicar el token candidato."""
        try:
            with self.client_factory(
                timeout=self.config.timeout_seconds,
                headers={
                    "Authorization": f"Zoho-oauthtoken {candidate_access_token}",
                    "Accept": "application/json",
                    "User-Agent": USER_AGENT,
                },
            ) as client:
                response = client.get(CANDIDATE_ORGANIZATION_URL)
        except httpx.TimeoutException as exc:
            raise ZohoTimeoutError(
                "Zoho no respondio al validar el entorno de la autorizacion."
            ) from exc
        except httpx.HTTPError as exc:
            raise ZohoAuthenticationError(
                "No fue posible validar el entorno de la autorizacion de Zoho."
            ) from exc
        if response.status_code >= 400:
            raise ZohoAuthenticationError(
                "Zoho rechazo la validacion del entorno de la autorizacion.",
                status_code=response.status_code,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ZohoInvalidResponseError(
                "Zoho devolvio una organizacion invalida durante la autorizacion.",
                status_code=response.status_code,
            ) from exc
        records = payload.get("org") if isinstance(payload, dict) else None
        if (
            not isinstance(records, list)
            or not records
            or not isinstance(records[0], dict)
        ):
            raise ZohoInvalidResponseError(
                "Zoho no devolvio una organizacion valida durante la autorizacion."
            )
        raw = records[0]
        raw_environment = raw.get("type")
        environment = normalize_organization_environment(raw_environment)
        return _CandidateOrganization(
            environment=environment,
            organization_id=str(raw.get("id") or "").strip(),
            company_name=str(
                raw.get("company_name") or raw.get("alias") or ""
            ).strip(),
            environment_value_class=safe_value_class(raw_environment),
        )

    def _validate_candidate_organization(
        self,
        organization: _CandidateOrganization,
    ) -> None:
        if organization.environment != self.config.environment:
            logger.warning(
                "Zoho validacion_oauth=environment_mismatch "
                "perfil_esperado=%s backend=rest_validation clase_valor=%s "
                "environment_reportado=%s comparacion=no_coincide",
                self.config.profile,
                organization.environment_value_class,
                organization.environment,
            )
            raise ZohoConfigurationError(
                "El entorno de la organizacion no corresponde al perfil Zoho."
            )
        expected_org_id = self.config.expected_org_id
        if expected_org_id and organization.organization_id != expected_org_id:
            logger.warning(
                "Zoho validacion_oauth=organization_mismatch "
                "perfil_esperado=%s environment_reportado=%s",
                self.config.profile,
                organization.environment,
            )
            raise ZohoConfigurationError(
                "La organizacion autorizada no corresponde al perfil Zoho."
            )
        logger.info(
            "Zoho validacion_oauth=ok perfil=%s backend=rest_validation "
            "clase_valor=%s environment_reportado=%s "
            "comparacion=coincide org_validada=si",
            self.config.profile,
            organization.environment_value_class,
            organization.environment,
        )

    def _log_domain_mismatch(self, received_api_domain: str) -> None:
        # Importación perezosa: consultar el diagnóstico no inicializa el SDK.
        from .sdk.initializer import sdk_runtime_diagnostics

        _detected_environment, sdk_data_center = sdk_runtime_diagnostics(
            self.config.profile
        )
        logger.warning(
            "Zoho diagnostico_oauth=api_domain_invalid "
            "perfil_esperado=%s dominio_oauth_esperado=%s "
            "dominio_oauth_recibido=%s dominio_api_esperado=%s "
            "dominio_api_recibido=%s data_center_sdk=%s "
            "environment_configurado=%s environment_detectado=no_inferido "
            "endpoint_intercambio=%s razon=%s recomendacion=%s",
            self.config.profile,
            _safe_origin(self.config.accounts_base_url),
            self._last_oauth_response_domain,
            _safe_origin(self.config.api_base_url),
            _safe_origin(received_api_domain),
            sdk_data_center,
            self.config.environment,
            self._last_oauth_endpoint,
            _api_domain_rejection_reason(received_api_domain),
            "verificar_respuesta_oauth_y_accounts_data_center",
        )


def _safe_origin(value: str) -> str:
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not host:
        return "no_disponible"
    try:
        parsed_port = parsed.port
    except ValueError:
        return "no_disponible"
    port = f":{parsed_port}" if parsed_port else ""
    return f"{parsed.scheme.lower()}://{host}{port}"


def _safe_endpoint(value: str) -> str:
    parsed = urlsplit(value)
    origin = _safe_origin(value)
    if origin == "no_disponible":
        return origin
    return f"{origin}{parsed.path or '/'}"


def _validate_oauth_api_domain(value: str) -> str:
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ZohoConfigurationError(
            "Zoho devolvio un dominio API no permitido."
        ) from exc
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() not in ALLOWED_OAUTH_API_DOMAINS
        or port is not None
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ZohoConfigurationError(
            "Zoho devolvio un dominio API no permitido."
        )
    return value.rstrip("/")


def _api_domain_rejection_reason(received: str) -> str:
    actual = urlsplit(received)
    reasons = []
    if actual.scheme != "https":
        reasons.append("scheme_no_https")
    if (actual.hostname or "").lower() not in ALLOWED_OAUTH_API_DOMAINS:
        reasons.append("host_no_permitido")
    try:
        actual_port = actual.port
    except ValueError:
        actual_port = "invalido"
    if actual_port is not None:
        reasons.append("puerto_no_permitido")
    if actual.username or actual.password:
        reasons.append("userinfo_no_permitido")
    if actual.query:
        reasons.append("query_no_permitida")
    if actual.fragment:
        reasons.append("fragment_no_permitido")
    if actual.path not in {"", "/"}:
        reasons.append("path_no_permitido")
    if not reasons:
        reasons.append("origen_no_canonico")
    return ",".join(reasons)
