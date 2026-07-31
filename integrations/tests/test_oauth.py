from __future__ import annotations

import time
from urllib.parse import parse_qs, urlsplit

import httpx
from django.test import SimpleTestCase, override_settings

from integrations.tests.helpers import VALID_SETTINGS as VALID, client_factory
from integrations.zoho.exceptions import (
    ZohoAuthenticationError,
    ZohoConfigurationError,
    ZohoInvalidResponseError,
    ZohoTimeoutError,
)
from integrations.zoho.oauth import (
    CANDIDATE_ORGANIZATION_URL,
    ZohoOAuthService,
)
from integrations.zoho.schemas import AccessToken
from integrations.zoho.settings import ZohoSettings
from integrations.zoho.token_store import EnvironmentTokenStore


def oauth_handler(
    token_payload,
    *,
    environment="production",
    organization_id="org-production",
    organization_status=200,
):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json=token_payload)
        if request.method == "GET" and str(request.url) == CANDIDATE_ORGANIZATION_URL:
            return httpx.Response(
                organization_status,
                json={
                    "org": [
                        {
                            "id": organization_id,
                            "company_name": "Test organization",
                            "type": environment,
                        }
                    ]
                },
            )
        raise AssertionError(f"Solicitud inesperada: {request.method} {request.url}")

    return handler


@override_settings(**VALID)
class ZohoOAuthTests(SimpleTestCase):
    def setUp(self):
        self.store = EnvironmentTokenStore()
        self.config = ZohoSettings.from_django()

    def test_authorization_url_has_offline_scopes_and_state(self):
        service = ZohoOAuthService(self.config, self.store)
        url = service.authorization_url("state-value")
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(query["state"], ["state-value"])
        self.assertEqual(query["access_type"], ["offline"])
        self.assertEqual(query["response_type"], ["code"])
        self.assertIn("ZohoCRM.coql.READ", query["scope"][0])
        self.assertNotIn(".ALL", query["scope"][0])

    def test_exchange_caches_tokens_without_exposing_them(self):
        def handler(request):
            self.assertNotIn("client-secret", str(request.url))
            if request.method == "GET":
                self.assertEqual(str(request.url), CANDIDATE_ORGANIZATION_URL)
                return httpx.Response(
                    200,
                    json={"org": [{"id": "1", "type": "production"}]},
                )
            return httpx.Response(
                200,
                json={
                    "access_token": "new-access",
                    "refresh_token": "new-refresh",
                    "expires_in": 3600,
                    "api_domain": "https://www.zohoapis.com",
                },
            )

        service = ZohoOAuthService(
            self.config, self.store, client_factory(handler)
        )
        token = service.exchange_code("one-time-code")
        self.assertEqual(token.value, "new-access")
        self.assertEqual(self.store.get_refresh_token(), "new-refresh")
        self.assertEqual(
            service.consume_received_refresh_token(),
            "new-refresh",
        )
        self.assertEqual(service.consume_received_refresh_token(), "")

    def test_refresh_reuses_cached_access_token(self):
        cached = AccessToken(
            "cached-access",
            time.time() + 3600,
            "https://www.zohoapis.com",
        )
        self.store.set_access_token(cached)

        def forbidden(_request):
            raise AssertionError("No debe llamar a Zoho con token vigente")

        service = ZohoOAuthService(
            self.config, self.store, client_factory(forbidden)
        )
        self.assertIs(service.get_access_token(), cached)

    def test_refresh_success(self):
        self.store.commit_oauth_tokens(
            refresh_token="runtime-refresh",
            access_token=AccessToken(
                "expired-access",
                time.time() - 60,
                "https://www.zohoapis.com",
            ),
        )

        def handler(request):
            if request.method == "POST":
                return httpx.Response(
                    200,
                    json={
                        "access_token": "refreshed",
                        "expires_in": 3600,
                        "api_domain": "https://www.zohoapis.com",
                    },
                )
            self.assertEqual(str(request.url), CANDIDATE_ORGANIZATION_URL)
            return httpx.Response(
                200,
                json={"org": [{"id": "1", "type": "production"}]},
            )

        token = ZohoOAuthService(
            self.config, self.store, client_factory(handler)
        ).get_access_token()
        self.assertEqual(token.value, "refreshed")

    def test_oauth_error_is_generic_and_secret_free(self):
        def handler(_request):
            return httpx.Response(
                400,
                json={"error": "invalid_client", "client_secret": "client-secret"},
            )

        service = ZohoOAuthService(
            self.config, self.store, client_factory(handler)
        )
        with self.assertRaises(ZohoAuthenticationError) as caught:
            service.exchange_code("one-time-code")
        self.assertNotIn("client-secret", str(caught.exception))
        self.assertNotIn("one-time-code", str(caught.exception))

    @override_settings(
        ZOHO_ACTIVE_PROFILE="sandbox",
        ZOHO_ENABLED=False,
        ZOHO_SANDBOX_ENABLED=True,
        ZOHO_SANDBOX_CLIENT_ID="sandbox-client-id",
        ZOHO_SANDBOX_CLIENT_SECRET="sandbox-client-secret",
        ZOHO_SANDBOX_REFRESH_TOKEN="",
        ZOHO_SANDBOX_ENVIRONMENT="sandbox",
        ZOHO_SANDBOX_ACCOUNTS_BASE_URL="https://accounts.zoho.com",
        ZOHO_SANDBOX_API_BASE_URL="https://sandbox.zohoapis.com",
        ZOHO_SANDBOX_SDK_RESOURCE_PATH="runtime/zoho_sdk/sandbox",
    )
    def test_domain_mismatch_emits_only_safe_diagnostic(self):
        config = ZohoSettings.from_django("sandbox")
        store = EnvironmentTokenStore(config)

        def handler(_request):
            return httpx.Response(
                200,
                json={
                    "access_token": "access-token-must-not-appear",
                    "refresh_token": "refresh-token-must-not-appear",
                    "expires_in": 3600,
                    "api_domain": (
                        "https://www.zohoapis.com"
                        "?authorization_code=must-not-appear"
                    ),
                },
            )

        service = ZohoOAuthService(
            config,
            store,
            client_factory(handler),
        )
        with self.assertLogs("integrations.zoho", "WARNING") as logs:
            with self.assertRaisesMessage(
                ZohoConfigurationError,
                "Zoho devolvio un dominio API no permitido.",
            ):
                service.exchange_code("authorization-code-must-not-appear")

        diagnostic = " ".join(logs.output)
        self.assertIn("perfil_esperado=sandbox", diagnostic)
        self.assertIn(
            "dominio_oauth_esperado=https://accounts.zoho.com",
            diagnostic,
        )
        self.assertIn(
            "dominio_oauth_recibido=https://accounts.zoho.com",
            diagnostic,
        )
        self.assertIn(
            "dominio_api_esperado=https://sandbox.zohoapis.com",
            diagnostic,
        )
        self.assertIn(
            "dominio_api_recibido=https://www.zohoapis.com",
            diagnostic,
        )
        self.assertIn("data_center_sdk=no_detectado", diagnostic)
        self.assertIn("environment_configurado=sandbox", diagnostic)
        self.assertIn("environment_detectado=no_inferido", diagnostic)
        self.assertIn(
            "endpoint_intercambio=https://accounts.zoho.com/oauth/v2/token",
            diagnostic,
        )
        self.assertIn("razon=query_no_permitida", diagnostic)
        self.assertIn(
            "recomendacion=verificar_respuesta_oauth_y_accounts_data_center",
            diagnostic,
        )
        for secret in (
            "access-token-must-not-appear",
            "refresh-token-must-not-appear",
            "authorization-code-must-not-appear",
            "sandbox-client-secret",
            "must-not-appear",
        ):
            self.assertNotIn(secret, diagnostic)
        self.assertEqual(store.get_refresh_token(), "")
        self.assertIsNone(store.get_access_token())
        self.assertEqual(service.consume_received_refresh_token(), "")

    def test_production_rejects_sandbox_candidate_without_mutating_store(self):
        original_refresh = self.store.get_refresh_token()

        handler = oauth_handler(
            {
                "access_token": "sandbox-access-candidate",
                "refresh_token": "sandbox-refresh-candidate",
                "expires_in": 3600,
                "api_domain": "https://www.zohoapis.com",
            },
            environment="sandbox",
            organization_id="org-sandbox",
        )

        service = ZohoOAuthService(
            self.config,
            self.store,
            client_factory(handler),
        )
        with self.assertLogs("integrations.zoho", "WARNING") as logs:
            with self.assertRaises(ZohoConfigurationError):
                service.exchange_code("sandbox-code-candidate")

        self.assertEqual(self.store.get_refresh_token(), original_refresh)
        self.assertIsNone(self.store.get_access_token())
        self.assertEqual(service.consume_received_refresh_token(), "")
        combined = " ".join(logs.output)
        self.assertIn("perfil_esperado=production", combined)
        self.assertIn("environment_reportado=sandbox", combined)
        self.assertNotIn("sandbox-access-candidate", combined)
        self.assertNotIn("sandbox-refresh-candidate", combined)
        self.assertNotIn("sandbox-code-candidate", combined)

    @override_settings(
        ZOHO_ACTIVE_PROFILE="sandbox",
        ZOHO_ENABLED=False,
        ZOHO_SANDBOX_ENABLED=True,
        ZOHO_SANDBOX_CLIENT_ID="sandbox-client-id",
        ZOHO_SANDBOX_CLIENT_SECRET="sandbox-client-secret",
        ZOHO_SANDBOX_REFRESH_TOKEN="",
        ZOHO_SANDBOX_ENVIRONMENT="sandbox",
        ZOHO_SANDBOX_ACCOUNTS_BASE_URL="https://accounts.zoho.com",
        ZOHO_SANDBOX_API_BASE_URL="https://sandbox.zohoapis.com",
        ZOHO_SANDBOX_SDK_RESOURCE_PATH="runtime/zoho_sdk/sandbox",
    )
    def test_valid_sandbox_candidate_commits_both_tokens_after_validation(self):
        config = ZohoSettings.from_django("sandbox")
        store = EnvironmentTokenStore(config)

        handler = oauth_handler(
            {
                "access_token": "validated-sandbox-access",
                "refresh_token": "validated-sandbox-refresh",
                "expires_in": 3600,
                "api_domain": "https://www.zohoapis.com",
            },
            environment="sandbox",
            organization_id="org-sandbox",
        )

        service = ZohoOAuthService(config, store, client_factory(handler))
        token = service.exchange_code("valid-sandbox-code")

        self.assertEqual(token.value, "validated-sandbox-access")
        self.assertEqual(token.api_domain, "https://sandbox.zohoapis.com")
        self.assertEqual(store.get_access_token().value, token.value)
        self.assertEqual(
            store.get_refresh_token(),
            "validated-sandbox-refresh",
        )
        self.assertEqual(
            service.consume_received_refresh_token(),
            "validated-sandbox-refresh",
        )

    def test_arbitrary_api_domain_is_rejected_before_organization_request(self):
        organization_called = False

        def handler(request):
            nonlocal organization_called
            if request.method == "GET":
                organization_called = True
                raise AssertionError("Organization API no debe ejecutarse")
            return httpx.Response(
                200,
                json={
                    "access_token": "candidate-access",
                    "refresh_token": "candidate-refresh",
                    "expires_in": 3600,
                    "api_domain": "https://zoho.attacker.invalid",
                },
            )

        store = EnvironmentTokenStore(self.config)
        service = ZohoOAuthService(
            self.config,
            store,
            client_factory(handler),
        )
        with self.assertLogs("integrations.zoho", "WARNING"):
            with self.assertRaises(ZohoConfigurationError):
                service.exchange_code("candidate-code")

        self.assertFalse(organization_called)
        self.assertIsNone(store.get_access_token())
        self.assertEqual(store.get_refresh_token(), self.config.refresh_token)

    @override_settings(
        ZOHO_PRODUCTION_EXPECTED_ORG_ID="expected-org",
    )
    def test_expected_organization_id_match_is_accepted(self):
        config = ZohoSettings.from_django("production")
        store = EnvironmentTokenStore(config)
        handler = oauth_handler(
            {
                "access_token": "matching-access",
                "refresh_token": "matching-refresh",
                "expires_in": 3600,
                "api_domain": "https://www.zohoapis.com",
            },
            environment="production",
            organization_id="expected-org",
        )

        ZohoOAuthService(config, store, client_factory(handler)).exchange_code(
            "matching-code"
        )

        self.assertEqual(store.get_refresh_token(), "matching-refresh")

    @override_settings(
        ZOHO_PRODUCTION_EXPECTED_ORG_ID="expected-org",
    )
    def test_expected_organization_id_mismatch_is_rejected(self):
        config = ZohoSettings.from_django("production")
        store = EnvironmentTokenStore(config)
        original_refresh = store.get_refresh_token()
        handler = oauth_handler(
            {
                "access_token": "wrong-org-access",
                "refresh_token": "wrong-org-refresh",
                "expires_in": 3600,
                "api_domain": "https://www.zohoapis.com",
            },
            environment="production",
            organization_id="different-org",
        )

        with self.assertLogs("integrations.zoho", "WARNING") as logs:
            with self.assertRaises(ZohoConfigurationError):
                ZohoOAuthService(
                    config,
                    store,
                    client_factory(handler),
                ).exchange_code("wrong-org-code")

        self.assertEqual(store.get_refresh_token(), original_refresh)
        self.assertIsNone(store.get_access_token())
        self.assertNotIn("expected-org", " ".join(logs.output))
        self.assertNotIn("different-org", " ".join(logs.output))

    def test_candidate_is_not_published_during_organization_validation(self):
        store = EnvironmentTokenStore(self.config)

        def handler(request):
            if request.method == "POST":
                return httpx.Response(
                    200,
                    json={
                        "access_token": "local-access-candidate",
                        "refresh_token": "local-refresh-candidate",
                        "expires_in": 3600,
                        "api_domain": "https://www.zohoapis.com",
                    },
                )
            self.assertIsNone(store.get_access_token())
            self.assertEqual(store.get_refresh_token(), self.config.refresh_token)
            return httpx.Response(
                200,
                json={"org": [{"id": "1", "type": "production"}]},
            )

        ZohoOAuthService(
            self.config,
            store,
            client_factory(handler),
        ).exchange_code("local-code")
        self.assertEqual(store.get_refresh_token(), "local-refresh-candidate")

    def test_organization_timeout_rejects_candidates(self):
        store = EnvironmentTokenStore(self.config)
        original_refresh = store.get_refresh_token()

        def handler(request):
            if request.method == "POST":
                return httpx.Response(
                    200,
                    json={
                        "access_token": "timeout-access",
                        "refresh_token": "timeout-refresh",
                        "expires_in": 3600,
                        "api_domain": "https://www.zohoapis.com",
                    },
                )
            raise httpx.ReadTimeout("timeout", request=request)

        with self.assertRaises(ZohoTimeoutError):
            ZohoOAuthService(
                self.config,
                store,
                client_factory(handler),
            ).exchange_code("timeout-code")
        self.assertEqual(store.get_refresh_token(), original_refresh)
        self.assertIsNone(store.get_access_token())

    def test_organization_api_error_rejects_candidates(self):
        store = EnvironmentTokenStore(self.config)
        original_refresh = store.get_refresh_token()
        handler = oauth_handler(
            {
                "access_token": "error-access",
                "refresh_token": "error-refresh",
                "expires_in": 3600,
                "api_domain": "https://www.zohoapis.com",
            },
            organization_status=503,
        )

        with self.assertRaises(ZohoAuthenticationError):
            ZohoOAuthService(
                self.config,
                store,
                client_factory(handler),
            ).exchange_code("error-code")
        self.assertEqual(store.get_refresh_token(), original_refresh)
        self.assertIsNone(store.get_access_token())

    def test_missing_or_unknown_organization_type_rejects_candidates(self):
        for reported_type in (None, "unknown-environment"):
            with self.subTest(reported_type=reported_type):
                store = EnvironmentTokenStore(self.config)

                def handler(request):
                    if request.method == "POST":
                        return httpx.Response(
                            200,
                            json={
                                "access_token": "invalid-type-access",
                                "refresh_token": "invalid-type-refresh",
                                "expires_in": 3600,
                                "api_domain": "https://www.zohoapis.com",
                            },
                        )
                    return httpx.Response(
                        200,
                        json={"org": [{"id": "1", "type": reported_type}]},
                    )

                with self.assertRaises(ZohoInvalidResponseError):
                    ZohoOAuthService(
                        self.config,
                        store,
                        client_factory(handler),
                    ).exchange_code("invalid-type-code")
                self.assertIsNone(store.get_access_token())

    def test_non_json_token_response(self):
        service = ZohoOAuthService(
            self.config,
            self.store,
            client_factory(lambda _request: httpx.Response(200, text="not-json")),
        )
        with self.assertRaises(ZohoInvalidResponseError):
            service.exchange_code("code")

    @override_settings(**{**VALID, "ZOHO_REFRESH_TOKEN": ""})
    def test_missing_refresh_token(self):
        service = ZohoOAuthService(ZohoSettings.from_django(), EnvironmentTokenStore())
        with self.assertRaisesMessage(Exception, "renovación"):
            service.get_access_token()
