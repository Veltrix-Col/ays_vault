from __future__ import annotations

import hashlib
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.contrib.sessions.models import Session
from django.test import Client, TestCase, modify_settings, override_settings
from django.urls import reverse

from integrations.tests.helpers import VALID_SETTINGS
from integrations.zoho.exceptions import ZohoAuthenticationError
from integrations.zoho.schemas import AccessToken
from integrations.zoho.settings import ZohoSettings
from integrations.zoho.token_store import EnvironmentTokenStore
from vault.models import UserProfile


LOCAL_DELIVERY_SETTINGS = {
    **VALID_SETTINGS,
    "DEBUG": True,
    "ZOHO_ENABLED": True,
    "ZOHO_PUBLIC_SETUP_ENABLED": True,
}


@override_settings(**LOCAL_DELIVERY_SETTINGS)
class RefreshTokenDeliveryTests(TestCase):
    secret = "1000.local-refresh-token-secret"
    authorization_code = "one-time-authorization-code"

    def setUp(self):
        self.store = EnvironmentTokenStore(ZohoSettings.from_django())

    def _prepare_state(self, client: Client, *, user_id=None):
        session = client.session
        session.save()
        session_key = str(session.session_key)
        session["zoho_oauth_state"] = {
            "digest": hashlib.sha256(b"valid-state").hexdigest(),
            "profile": "production",
            "profile_digest": hashlib.sha256(
                b"valid-state:production"
            ).hexdigest(),
            "created_at": time.time(),
            "session_digest": hashlib.sha256(
                session_key.encode("utf-8")
            ).hexdigest(),
            "user_id": user_id,
        }
        session.save()
        return session_key

    def _successful_callback(self, client=None, *, user_id=None):
        client = client or self.client
        session_key = self._prepare_state(client, user_id=user_id)
        oauth = Mock()
        oauth.exchange_code.return_value = AccessToken(
            "access-token-that-must-not-render",
            time.time() + 3600,
            "https://www.zohoapis.com",
        )
        oauth.consume_received_refresh_token.return_value = self.secret
        service = SimpleNamespace(oauth=oauth)
        with (
            patch("integrations.views.ZohoServices.build", return_value=service),
            patch("integrations.views.get_token_store", return_value=self.store),
            self.assertLogs("integrations.zoho", level="INFO") as logs,
        ):
            response = client.get(
                reverse("integrations:zoho_callback"),
                {
                    "state": "valid-state",
                    "code": self.authorization_code,
                },
            )
        self.assertEqual(response.status_code, 302)
        oauth.exchange_code.assert_called_once_with(self.authorization_code)
        return client, session_key, response, logs.output

    def _status(self, client=None):
        client = client or self.client
        with patch(
            "integrations.views.get_token_store",
            return_value=self.store,
        ):
            return client.get(reverse("integrations:zoho_status"))

    def test_first_status_load_shows_token_and_second_load_does_not(self):
        _, _, callback, _ = self._successful_callback()
        first = self._status()
        second = self._status()

        self.assertContains(first, "Refresh token generado")
        self.assertContains(first, self.secret)
        self.assertContains(first, "ZOHO_PRODUCTION_REFRESH_TOKEN")
        self.assertContains(first, "No lo comparta")
        self.assertNotContains(first, 'value="' + self.secret + '"')
        self.assertNotContains(first, "access-token-that-must-not-render")
        self.assertNotContains(first, "client-secret")
        self.assertNotContains(first, self.authorization_code)
        self.assertNotContains(second, self.secret)
        self.assertNotContains(second, "Refresh token generado")
        self.assertNotIn(self.secret, callback["Location"])

    def test_another_session_cannot_consume_delivery(self):
        owner, _, _, _ = self._successful_callback()
        other = Client()
        other_response = self._status(other)
        owner_response = self._status(owner)

        self.assertNotContains(other_response, self.secret)
        self.assertContains(owner_response, self.secret)

    @override_settings(DEBUG=False)
    def test_debug_false_never_stages_or_displays_token(self):
        _, session_key, _, _ = self._successful_callback()
        response = self._status()
        binding = hashlib.sha256(session_key.encode("utf-8")).hexdigest()

        self.assertNotContains(response, self.secret)
        self.assertEqual(
            self.store.consume_refresh_token_delivery(binding),
            "",
        )

    @override_settings(ZOHO_ENABLED=False)
    def test_disabled_integration_never_stages_or_displays_token(self):
        session_key = self._prepare_state(self.client)
        oauth = Mock()
        with (
            patch(
                "integrations.views.ZohoServices.build",
                return_value=SimpleNamespace(oauth=oauth),
            ),
            patch("integrations.views.get_token_store", return_value=self.store),
        ):
            self.client.get(
                reverse("integrations:zoho_callback"),
                {"state": "valid-state", "code": self.authorization_code},
            )
        oauth.exchange_code.assert_not_called()
        response = self._status()
        binding = hashlib.sha256(session_key.encode("utf-8")).hexdigest()

        self.assertNotContains(response, self.secret)
        self.assertEqual(
            self.store.consume_refresh_token_delivery(binding),
            "",
        )

    @modify_settings(
        MIDDLEWARE={"remove": ["vault.middleware.SecureSessionMiddleware"]}
    )
    @override_settings(ZOHO_PUBLIC_SETUP_ENABLED=False)
    def test_public_mode_disabled_never_stages_or_displays_token(self):
        admin = get_user_model().objects.create_user(
            "zoho.delivery.admin",
            password="Test123456!",
        )
        admin.vault_profile.role = UserProfile.ADMIN
        admin.vault_profile.active = True
        admin.vault_profile.save()
        self.client.force_login(admin)

        _, session_key, _, _ = self._successful_callback(
            user_id=admin.pk
        )
        response = self._status()
        binding = hashlib.sha256(session_key.encode("utf-8")).hexdigest()

        self.assertNotContains(response, self.secret)
        self.assertEqual(
            self.store.consume_refresh_token_delivery(binding),
            "",
        )

    def test_failed_callback_does_not_create_delivery(self):
        session_key = self._prepare_state(self.client)
        oauth = Mock()
        oauth.exchange_code.side_effect = ZohoAuthenticationError(
            "Zoho rechazó la autorización."
        )
        service = SimpleNamespace(oauth=oauth)
        with (
            patch("integrations.views.ZohoServices.build", return_value=service),
            patch("integrations.views.get_token_store", return_value=self.store),
        ):
            callback = self.client.get(
                reverse("integrations:zoho_callback"),
                {"state": "valid-state", "code": self.authorization_code},
            )
        response = self._status()
        binding = hashlib.sha256(session_key.encode("utf-8")).hexdigest()

        self.assertEqual(callback.status_code, 302)
        self.assertNotContains(response, self.secret)
        self.assertEqual(
            self.store.consume_refresh_token_delivery(binding),
            "",
        )
        oauth.consume_received_refresh_token.assert_not_called()

    def test_secret_is_not_in_logs_messages_redirect_cookies_or_database(self):
        _, _, response, logs = self._successful_callback()
        message_values = [
            str(message) for message in get_messages(response.wsgi_request)
        ]

        self.assertNotIn(self.secret, "\n".join(logs))
        self.assertNotIn(self.authorization_code, "\n".join(logs))
        self.assertNotIn(self.secret, "\n".join(message_values))
        self.assertNotIn(self.secret, response["Location"])
        self.assertNotIn(self.authorization_code, response["Location"])
        for morsel in response.cookies.values():
            self.assertNotIn(self.secret, morsel.value)
        for session in Session.objects.all():
            self.assertNotIn(self.secret, session.session_data)
            self.assertNotIn(self.secret, repr(session.get_decoded()))

    def test_connection_check_still_uses_mocked_organization_service(self):
        self._successful_callback()
        self._status()
        organization_call = Mock(
            return_value=SimpleNamespace(
                company_name="Hidden organization",
                timezone="America/Bogota",
                currency="COP",
            )
        )
        service = SimpleNamespace(
            metadata=SimpleNamespace(organization=organization_call)
        )
        with (
            patch("integrations.views.ZohoServices.build", return_value=service),
            patch("integrations.views.get_token_store", return_value=self.store),
        ):
            response = self.client.post(
                reverse("integrations:zoho_status"),
                {"action": "check"},
            )

        self.assertEqual(response.status_code, 200)
        organization_call.assert_called_once_with()
        self.assertNotContains(response, "Hidden organization")
