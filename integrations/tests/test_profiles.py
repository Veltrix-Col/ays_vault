from __future__ import annotations

import hashlib
import time
from io import StringIO
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from django.core.management import call_command
from django.contrib.sessions.models import Session
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from zohocrmsdk.src.com.zoho.crm.api.util.choice import Choice

from ays_zoho_sdk.facade import ZohoFacade
from ays_zoho_sdk.backends.sdk import SDKBackend
from ays_zoho_sdk.oauth import CANDIDATE_ORGANIZATION_URL, ZohoOAuthService
from ays_zoho_sdk.sdk.initializer import initialize_sdk, reset_sdk_for_tests

from integrations.tests.helpers import VALID_SETTINGS, client_factory
from integrations.zoho import reset_zoho_for_tests
from integrations.zoho.exceptions import ZohoConfigurationError
from integrations.zoho.schemas import AccessToken, Organization
from integrations.zoho.settings import ZohoSettings
from integrations.zoho.token_store import get_token_store


MULTI = {
    **VALID_SETTINGS,
    "ZOHO_ENABLED": False,
    "ZOHO_ACTIVE_PROFILE": "production",
    "ZOHO_PRODUCTION_ENABLED": True,
    "ZOHO_PRODUCTION_CLIENT_ID": "production-client",
    "ZOHO_PRODUCTION_CLIENT_SECRET": "production-secret",
    "ZOHO_PRODUCTION_REFRESH_TOKEN": "production-refresh",
    "ZOHO_PRODUCTION_ENVIRONMENT": "production",
    "ZOHO_PRODUCTION_ACCOUNTS_BASE_URL": "https://accounts.zoho.com",
    "ZOHO_PRODUCTION_API_BASE_URL": "https://www.zohoapis.com",
    "ZOHO_PRODUCTION_SDK_RESOURCE_PATH": "runtime/zoho_sdk/production",
    "ZOHO_SANDBOX_ENABLED": True,
    "ZOHO_SANDBOX_CLIENT_ID": "sandbox-client",
    "ZOHO_SANDBOX_CLIENT_SECRET": "sandbox-secret",
    "ZOHO_SANDBOX_REFRESH_TOKEN": "sandbox-refresh",
    "ZOHO_SANDBOX_ENVIRONMENT": "sandbox",
    "ZOHO_SANDBOX_ACCOUNTS_BASE_URL": "https://accounts.zoho.com",
    "ZOHO_SANDBOX_API_BASE_URL": "https://sandbox.zohoapis.com",
    "ZOHO_SANDBOX_SDK_RESOURCE_PATH": "runtime/zoho_sdk/sandbox",
    "ZOHO_QA_ENABLED": False,
    "ZOHO_QA_CLIENT_ID": "",
    "ZOHO_QA_CLIENT_SECRET": "",
    "ZOHO_QA_REFRESH_TOKEN": "",
    "ZOHO_QA_ENVIRONMENT": "",
    "ZOHO_QA_ACCOUNTS_BASE_URL": "",
    "ZOHO_QA_API_BASE_URL": "",
    "ZOHO_QA_SDK_RESOURCE_PATH": "runtime/zoho_sdk/qa",
    "ZOHO_DEMO_ENABLED": False,
    "ZOHO_DEMO_CLIENT_ID": "",
    "ZOHO_DEMO_CLIENT_SECRET": "",
    "ZOHO_DEMO_REFRESH_TOKEN": "",
    "ZOHO_DEMO_ENVIRONMENT": "",
    "ZOHO_DEMO_ACCOUNTS_BASE_URL": "",
    "ZOHO_DEMO_API_BASE_URL": "",
    "ZOHO_DEMO_SDK_RESOURCE_PATH": "runtime/zoho_sdk/demo",
    "ZOHO_FUTURE_ENABLED": False,
    "ZOHO_FUTURE_CLIENT_ID": "",
    "ZOHO_FUTURE_CLIENT_SECRET": "",
    "ZOHO_FUTURE_REFRESH_TOKEN": "",
    "ZOHO_FUTURE_ENVIRONMENT": "",
    "ZOHO_FUTURE_ACCOUNTS_BASE_URL": "",
    "ZOHO_FUTURE_API_BASE_URL": "",
    "ZOHO_FUTURE_SDK_RESOURCE_PATH": "runtime/zoho_sdk/future",
}


def profile_oauth_handler(token_payload, *, environment, organization_id):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json=token_payload)
        if request.method == "GET":
            if str(request.url) != CANDIDATE_ORGANIZATION_URL:
                raise AssertionError(f"Endpoint inesperado: {request.url}")
            return httpx.Response(
                200,
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
        raise AssertionError(f"Metodo inesperado: {request.method}")

    return handler


@override_settings(**MULTI)
class ZohoSDKProfileIsolationTests(SimpleTestCase):
    def tearDown(self):
        reset_sdk_for_tests()

    def test_sdk_uses_official_environment_and_resource_per_profile(self):
        production_environment = object()
        sandbox_environment = object()
        initialized = []

        with TemporaryDirectory() as directory, override_settings(
            ZOHO_PRODUCTION_SDK_RESOURCE_PATH=f"{directory}/production",
            ZOHO_SANDBOX_SDK_RESOURCE_PATH=f"{directory}/sandbox",
        ):
            production = ZohoSettings.from_django("production")
            sandbox = ZohoSettings.from_django("sandbox")
            with patch(
                "zohocrmsdk.src.com.zoho.crm.api.dc.USDataCenter.PRODUCTION",
                return_value=production_environment,
            ), patch(
                "zohocrmsdk.src.com.zoho.crm.api.dc.USDataCenter.SANDBOX",
                return_value=sandbox_environment,
            ), patch(
                "zohocrmsdk.src.com.zoho.crm.api.initializer.Initializer.initialize",
                side_effect=lambda **kwargs: initialized.append(kwargs),
            ):
                production_runtime = initialize_sdk(production)
                sandbox_runtime = initialize_sdk(sandbox)

        self.assertEqual(len(initialized), 2)
        self.assertIs(initialized[0]["environment"], production_environment)
        self.assertIs(initialized[1]["environment"], sandbox_environment)
        self.assertNotEqual(
            production_runtime.resource_path,
            sandbox_runtime.resource_path,
        )
        self.assertIsNot(
            production_runtime.token_store,
            sandbox_runtime.token_store,
        )


@override_settings(
    **{**MULTI, "DEBUG": True, "ZOHO_PUBLIC_SETUP_ENABLED": True}
)
class ZohoProfileOAuthTests(TestCase):
    def tearDown(self):
        reset_zoho_for_tests()

    def test_state_binds_sandbox_and_callback_uses_only_stored_profile(self):
        oauth = Mock()
        oauth.generate_state.return_value = "sandbox-state"
        oauth.authorization_url.return_value = (
            "https://accounts.zoho.com/oauth/v2/auth?state=sandbox-state"
        )
        with patch(
            "integrations.views.ZohoServices.build",
            return_value=SimpleNamespace(oauth=oauth),
        ) as build:
            response = self.client.post(
                reverse("integrations:zoho_connect"),
                {"profile": "sandbox"},
            )
        self.assertEqual(response.status_code, 302)
        config = build.call_args.kwargs["config"]
        self.assertEqual(config.profile, "sandbox")
        stored = self.client.session["zoho_oauth_state"]
        self.assertEqual(stored["profile"], "sandbox")
        self.assertEqual(
            stored["profile_digest"],
            hashlib.sha256(b"sandbox-state:sandbox").hexdigest(),
        )

        callback_oauth = Mock()
        callback_oauth.consume_received_refresh_token.return_value = (
            "sandbox-refresh-generated"
        )
        with patch(
            "integrations.views.ZohoServices.build",
            return_value=SimpleNamespace(oauth=callback_oauth),
        ) as callback_build:
            self.client.get(
                reverse("integrations:zoho_callback"),
                {
                    "state": "sandbox-state",
                    "code": "authorization-code",
                    "profile": "production",
                },
            )
        self.assertEqual(
            callback_build.call_args.kwargs["config"].profile,
            "sandbox",
        )
        callback_oauth.exchange_code.assert_called_once_with("authorization-code")
        delivery = self.client.get(reverse("integrations:zoho_status"))
        self.assertContains(delivery, "sandbox-refresh-generated")
        self.assertContains(delivery, "Perfil: <strong>sandbox</strong>")
        self.assertNotContains(
            self.client.get(reverse("integrations:zoho_status")),
            "sandbox-refresh-generated",
        )

    def test_tampered_profile_binding_is_rejected(self):
        session = self.client.session
        session.save()
        state = "sandbox-state"
        session["zoho_oauth_state"] = {
            "digest": hashlib.sha256(state.encode()).hexdigest(),
            "profile": "production",
            "profile_digest": hashlib.sha256(
                b"sandbox-state:sandbox"
            ).hexdigest(),
            "created_at": __import__("time").time(),
            "session_digest": hashlib.sha256(
                session.session_key.encode()
            ).hexdigest(),
            "user_id": None,
        }
        session.save()
        with patch("integrations.views.ZohoServices.build") as build:
            response = self.client.get(
                reverse("integrations:zoho_callback"),
                {"state": state, "code": "authorization-code"},
            )
        self.assertEqual(response.status_code, 302)
        build.assert_not_called()

    def test_invalid_profile_post_does_not_build_oauth(self):
        with patch("integrations.views.ZohoServices.build") as build:
            response = self.client.post(
                reverse("integrations:zoho_connect"),
                {"profile": "attacker"},
            )
        self.assertEqual(response.status_code, 302)
        build.assert_not_called()

    @override_settings(
        ZOHO_ACTIVE_PROFILE="sandbox",
        ZOHO_SANDBOX_REFRESH_TOKEN="",
    )
    def test_rejected_sandbox_callback_keeps_tokens_uncommitted_and_ui_closed(self):
        production_config = ZohoSettings.from_django("production")
        sandbox_config = ZohoSettings.from_django("sandbox")
        production_store = get_token_store(config=production_config)
        sandbox_store = get_token_store(config=sandbox_config)
        production_refresh = production_store.get_refresh_token()

        session = self.client.session
        session.save()
        state = "sandbox-rejected-state"
        session["zoho_oauth_state"] = {
            "digest": hashlib.sha256(state.encode()).hexdigest(),
            "profile": "sandbox",
            "profile_digest": hashlib.sha256(
                f"{state}:sandbox".encode()
            ).hexdigest(),
            "created_at": time.time(),
            "session_digest": hashlib.sha256(
                session.session_key.encode()
            ).hexdigest(),
            "user_id": None,
        }
        session.save()

        handler = profile_oauth_handler(
            {
                "access_token": "rejected-access-candidate",
                "refresh_token": "rejected-refresh-candidate",
                "expires_in": 3600,
                "api_domain": "https://www.zohoapis.com",
            },
            environment="production",
            organization_id="production-org",
        )

        oauth = ZohoOAuthService(
            sandbox_config,
            sandbox_store,
            client_factory(handler),
        )
        with patch(
            "integrations.views.ZohoServices.build",
            return_value=SimpleNamespace(oauth=oauth),
        ), self.assertLogs("integrations.zoho", "WARNING") as logs:
            callback = self.client.get(
                reverse("integrations:zoho_callback"),
                {"state": state, "code": "rejected-code-candidate"},
            )

        status = self.client.get(reverse("integrations:zoho_status"))
        combined_logs = " ".join(logs.output)
        serialized_sessions = " ".join(
            repr(item.get_decoded()) for item in Session.objects.all()
        )

        self.assertEqual(callback.status_code, 302)
        self.assertEqual(sandbox_store.get_refresh_token(), "")
        self.assertIsNone(sandbox_store.get_access_token())
        self.assertEqual(production_store.get_refresh_token(), production_refresh)
        self.assertContains(status, "Autorización pendiente")
        self.assertContains(status, "Autorizar sandbox")
        self.assertNotContains(status, "Probar conexión sandbox")
        self.assertNotIn("zoho_refresh_delivery_profile", self.client.session)
        for secret in (
            "rejected-access-candidate",
            "rejected-refresh-candidate",
            "rejected-code-candidate",
        ):
            self.assertNotIn(secret, combined_logs)
            self.assertNotIn(secret, serialized_sessions)
            self.assertNotIn(secret, status.content.decode())
            self.assertNotIn(secret, callback["Location"])
            for morsel in callback.cookies.values():
                self.assertNotIn(secret, morsel.value)

        reset_zoho_for_tests("sandbox")
        clean_store = get_token_store(
            config=ZohoSettings.from_django("sandbox")
        )
        self.assertEqual(clean_store.get_refresh_token(), "")
        self.assertIsNone(clean_store.get_access_token())

    def test_failed_production_oauth_does_not_modify_validated_sandbox(self):
        production_config = ZohoSettings.from_django("production")
        sandbox_config = ZohoSettings.from_django("sandbox")
        production_store = get_token_store(config=production_config)
        sandbox_store = get_token_store(config=sandbox_config)
        production_refresh = production_store.get_refresh_token()
        sandbox_access = AccessToken(
            "validated-sandbox-access",
            time.time() + 3600,
            "https://sandbox.zohoapis.com",
        )
        sandbox_store.commit_oauth_tokens(
            refresh_token="validated-sandbox-refresh",
            access_token=sandbox_access,
        )

        handler = profile_oauth_handler(
            {
                "access_token": "wrong-production-access",
                "refresh_token": "wrong-production-refresh",
                "expires_in": 3600,
                "api_domain": "https://www.zohoapis.com",
            },
            environment="sandbox",
            organization_id="sandbox-org",
        )

        oauth = ZohoOAuthService(
            production_config,
            production_store,
            client_factory(handler),
        )
        with self.assertLogs("ays_zoho_sdk", "WARNING"):
            with self.assertRaises(ZohoConfigurationError):
                oauth.exchange_code("wrong-production-code")

        self.assertEqual(
            sandbox_store.get_refresh_token(),
            "validated-sandbox-refresh",
        )
        self.assertIs(sandbox_store.get_access_token(), sandbox_access)
        self.assertEqual(production_store.get_refresh_token(), production_refresh)
        self.assertIsNone(production_store.get_access_token())

    def test_valid_sandbox_oauth_commits_only_sandbox_profile(self):
        production_config = ZohoSettings.from_django("production")
        sandbox_config = ZohoSettings.from_django("sandbox")
        production_store = get_token_store(config=production_config)
        sandbox_store = get_token_store(config=sandbox_config)
        production_refresh = production_store.get_refresh_token()

        handler = profile_oauth_handler(
            {
                "access_token": "accepted-sandbox-access",
                "refresh_token": "accepted-sandbox-refresh",
                "expires_in": 3600,
                "api_domain": "https://www.zohoapis.com",
            },
            environment="sandbox",
            organization_id="sandbox-org",
        )

        oauth = ZohoOAuthService(
            sandbox_config,
            sandbox_store,
            client_factory(handler),
        )
        oauth.exchange_code("accepted-sandbox-code")

        self.assertEqual(
            sandbox_store.get_refresh_token(),
            "accepted-sandbox-refresh",
        )
        self.assertEqual(
            sandbox_store.get_access_token().value,
            "accepted-sandbox-access",
        )
        self.assertEqual(production_store.get_refresh_token(), production_refresh)
        self.assertIsNone(production_store.get_access_token())

    def test_valid_production_oauth_commits_only_production_profile(self):
        production_config = ZohoSettings.from_django("production")
        sandbox_config = ZohoSettings.from_django("sandbox")
        production_store = get_token_store(config=production_config)
        sandbox_store = get_token_store(config=sandbox_config)
        sandbox_refresh = sandbox_store.get_refresh_token()

        handler = profile_oauth_handler(
            {
                "access_token": "accepted-production-access",
                "refresh_token": "accepted-production-refresh",
                "expires_in": 3600,
                "api_domain": "https://www.zohoapis.com",
            },
            environment="production",
            organization_id="production-org",
        )

        oauth = ZohoOAuthService(
            production_config,
            production_store,
            client_factory(handler),
        )
        oauth.exchange_code("accepted-production-code")

        self.assertEqual(
            production_store.get_refresh_token(),
            "accepted-production-refresh",
        )
        self.assertEqual(
            production_store.get_access_token().value,
            "accepted-production-access",
        )
        self.assertEqual(sandbox_store.get_refresh_token(), sandbox_refresh)
        self.assertIsNone(sandbox_store.get_access_token())


@override_settings(**MULTI)
class ZohoProfileCommandTests(SimpleTestCase):
    def tearDown(self):
        reset_zoho_for_tests()

    def test_backend_info_accepts_sandbox_profile(self):
        facade = SimpleNamespace(
            backend_name="sdk",
            organization=SimpleNamespace(
                get=lambda: Organization(
                    "1",
                    "Pruebas AYS",
                    data_center="sandbox.zohoapis.com",
                    environment="sandbox",
                )
            ),
        )
        output = StringIO()
        with patch(
            "integrations.management.commands.zoho_backend_info.get_zoho",
            return_value=facade,
        ) as factory:
            call_command(
                "zoho_backend_info",
                profile="sandbox",
                stdout=output,
            )
        factory.assert_called_once_with(profile="sandbox")
        self.assertIn("Perfil: sandbox", output.getvalue())
        self.assertIn("Entorno: sandbox", output.getvalue())

    def test_check_connection_accepts_normalized_sandbox_environment(self):
        config = ZohoSettings.from_django("sandbox")
        organization_item = SimpleNamespace(
            get_type=lambda: Choice("sandbox"),
            get_id=lambda: "1",
            get_company_name=lambda: "Pruebas AYS",
            get_alias=lambda: "",
            get_primary_email=lambda: "",
            get_country=lambda: "",
            get_time_zone=lambda: "America/Bogota",
            get_timezone=lambda: "",
            get_currency=lambda: "COP",
            get_iso_code=lambda: "COP",
        )
        sdk_response = SimpleNamespace(
            get_status_code=lambda: 200,
            get_object=lambda: SimpleNamespace(
                get_org=lambda: [organization_item]
            ),
        )
        backend = SDKBackend(
            config=config,
            rest_fallback=Mock(),
            initializer=lambda _config: None,
        )
        facade = ZohoFacade(backend, config=config)
        output = StringIO()
        with patch(
            "integrations.management.commands.zoho_check_connection.get_zoho",
            return_value=facade,
        ) as factory, patch(
            "zohocrmsdk.src.com.zoho.crm.api.org.org_operations.OrgOperations.get_organization",
            return_value=sdk_response,
        ), self.assertLogs("ays_zoho_sdk", "INFO") as logs:
            call_command(
                "zoho_check_connection",
                profile="sandbox",
                stdout=output,
            )
        factory.assert_called_once_with(profile="sandbox")
        self.assertIn("Zoho: OK", output.getvalue())
        self.assertIn("Entorno reportado: sandbox", output.getvalue())
        diagnostic = " ".join(logs.output)
        self.assertIn("clase_valor=Choice", diagnostic)
        self.assertNotIn("<Choice object at", diagnostic)

    def test_invalid_profile_command_is_rejected_before_external_call(self):
        with patch(
            "integrations.management.commands.zoho_check_connection.get_zoho"
        ) as factory:
            with self.assertRaises(ZohoConfigurationError):
                call_command(
                    "zoho_check_connection",
                    profile="attacker",
                    stdout=StringIO(),
                )
        factory.assert_not_called()
