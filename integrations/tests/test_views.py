from __future__ import annotations

import hashlib
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, modify_settings, override_settings
from django.urls import reverse

from integrations.tests.helpers import VALID_SETTINGS as VALID
from integrations.zoho.exceptions import ZohoAuthenticationError
from vault.models import UserProfile


@modify_settings(
    MIDDLEWARE={"remove": ["vault.middleware.SecureSessionMiddleware"]}
)
@override_settings(**VALID)
class ZohoViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_user("zoho.admin", password="Test123456!")
        cls.admin.vault_profile.role = UserProfile.ADMIN
        cls.admin.vault_profile.active = True
        cls.admin.vault_profile.save()
        cls.leader = get_user_model().objects.create_user("zoho.leader", password="Test123456!")
        cls.leader.vault_profile.role = UserProfile.LEADER
        cls.leader.vault_profile.active = True
        cls.leader.vault_profile.save()

    def test_anonymous_redirect_and_non_admin_forbidden(self):
        response = self.client.get(reverse("integrations:zoho_status"))
        self.assertEqual(response.status_code, 302)
        self.client.force_login(self.leader)
        response = self.client.get(reverse("integrations:zoho_status"))
        self.assertEqual(response.status_code, 403)

    def test_admin_status_is_safe(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("integrations:zoho_status"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Solo lectura")
        self.assertNotContains(response, "client-secret")
        self.assertNotContains(response, "refresh-secret")

    def test_connect_is_post_only_and_builds_fixed_redirect(self):
        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.get(reverse("integrations:zoho_connect")).status_code, 405
        )
        oauth = Mock()
        oauth.generate_state.return_value = "safe-state"
        oauth.authorization_url.return_value = "https://accounts.zoho.com/oauth/v2/auth?fixed=1"
        with patch(
            "integrations.views.ZohoServices.build",
            return_value=SimpleNamespace(oauth=oauth),
        ):
            response = self.client.post(reverse("integrations:zoho_connect"))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("https://accounts.zoho.com/"))
        stored = self.client.session["zoho_oauth_state"]
        self.assertEqual(
            stored["digest"], hashlib.sha256(b"safe-state").hexdigest()
        )
        self.assertNotEqual(stored["digest"], "safe-state")

    def test_callback_rejects_missing_tampered_and_expired_state(self):
        self.client.force_login(self.admin)
        callback = reverse("integrations:zoho_callback")
        self.assertEqual(self.client.get(callback).status_code, 302)
        for created_at, supplied in ((time.time(), "wrong"), (time.time() - 700, "state")):
            session = self.client.session
            session["zoho_oauth_state"] = {
                "digest": hashlib.sha256(b"state").hexdigest(),
                "profile": "production",
                "profile_digest": hashlib.sha256(
                    b"state:production"
                ).hexdigest(),
                "created_at": created_at,
                "session_digest": hashlib.sha256(
                    session.session_key.encode("utf-8")
                ).hexdigest(),
                "user_id": self.admin.pk,
            }
            session.save()
            with patch("integrations.views.ZohoServices.build") as build:
                response = self.client.get(callback, {"state": supplied, "code": "secret-code"})
            self.assertEqual(response.status_code, 302)
            build.assert_not_called()

    def test_callback_exchanges_code_without_rendering_tokens(self):
        self.client.force_login(self.admin)
        session = self.client.session
        session["zoho_oauth_state"] = {
            "digest": hashlib.sha256(b"state").hexdigest(),
            "profile": "production",
            "profile_digest": hashlib.sha256(
                b"state:production"
            ).hexdigest(),
            "created_at": time.time(),
            "session_digest": hashlib.sha256(
                session.session_key.encode("utf-8")
            ).hexdigest(),
            "user_id": self.admin.pk,
        }
        session.save()
        oauth = Mock()
        with patch(
            "integrations.views.ZohoServices.build",
            return_value=SimpleNamespace(oauth=oauth),
        ):
            response = self.client.get(
                reverse("integrations:zoho_callback"),
                {"state": "state", "code": "secret-code"},
            )
        self.assertRedirects(
            response, reverse("integrations:zoho_status"), fetch_redirect_response=False
        )
        oauth.exchange_code.assert_called_once_with("secret-code")
        self.assertNotIn("secret-code", response.content.decode())

    def test_explicit_connection_check_and_safe_failure(self):
        self.client.force_login(self.admin)
        organization = SimpleNamespace(
            company_name="Seguros A&S", timezone="America/Bogota", currency="COP"
        )
        service = SimpleNamespace(
            metadata=SimpleNamespace(organization=Mock(return_value=organization))
        )
        with patch("integrations.views.ZohoServices.build", return_value=service):
            response = self.client.post(
                reverse("integrations:zoho_status"), {"action": "check"}
            )
        self.assertContains(response, "Seguros A&amp;S")
        service.metadata.organization.assert_called_once()

        service.metadata.organization.side_effect = ZohoAuthenticationError(
            "Zoho rechazó la autorización."
        )
        with patch("integrations.views.ZohoServices.build", return_value=service):
            response = self.client.post(
                reverse("integrations:zoho_status"), {"action": "check"}
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "rechazó")

    def test_csrf_is_enforced_for_connect(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        response = client.post(reverse("integrations:zoho_connect"))
        self.assertEqual(response.status_code, 403)


@override_settings(**{**VALID, "ZOHO_PUBLIC_SETUP_ENABLED": True})
class ZohoPublicSetupViewTests(TestCase):
    def _begin_oauth(self, client=None):
        client = client or self.client
        oauth = Mock()
        oauth.generate_state.return_value = "anonymous-state"
        oauth.authorization_url.return_value = (
            "https://accounts.zoho.com/oauth/v2/auth?fixed=1"
        )
        with patch(
            "integrations.views.ZohoServices.build",
            return_value=SimpleNamespace(oauth=oauth),
        ):
            response = client.post(reverse("integrations:zoho_connect"))
        self.assertEqual(response.status_code, 302)
        return client

    def test_public_status_is_safe_and_shows_connection_check_with_token(self):
        with patch("integrations.views.ZohoServices.build") as build:
            response = self.client.get(reverse("integrations:zoho_status"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Modo temporal")
        self.assertContains(response, "Client ID configurado")
        self.assertContains(response, "Client Secret configurado")
        self.assertContains(response, "Redirect URI")
        self.assertContains(response, "Probar")
        self.assertNotContains(response, "client-id")
        self.assertNotContains(response, "client-secret")
        self.assertNotContains(response, "refresh-secret")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("private", response["Cache-Control"])
        self.assertEqual(response["Pragma"], "no-cache")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        build.assert_not_called()

    def test_public_status_post_with_csrf_checks_fixed_organization_endpoint(self):
        client = Client(enforce_csrf_checks=True)
        get_response = client.get(reverse("integrations:zoho_status"))
        csrf_token = client.cookies["csrftoken"].value
        organization = SimpleNamespace(
            company_name="Confidential organization",
            timezone="America/Bogota",
            currency="COP",
        )
        organization_call = Mock(return_value=organization)
        service = SimpleNamespace(
            metadata=SimpleNamespace(organization=organization_call)
        )
        with patch("integrations.views.ZohoServices.build", return_value=service):
            response = client.post(
                reverse("integrations:zoho_status"),
                {"action": "check"},
                HTTP_X_CSRFTOKEN=csrf_token,
            )
        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "solo lectura con Zoho")
        organization_call.assert_called_once_with()
        self.assertNotContains(response, "Confidential organization")
        self.assertNotContains(response, "client-id")
        self.assertNotContains(response, "client-secret")
        self.assertNotContains(response, "refresh-secret")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["Pragma"], "no-cache")

    def test_public_status_post_without_csrf_is_forbidden(self):
        client = Client(enforce_csrf_checks=True)
        with patch("integrations.views.ZohoServices.build") as build:
            response = client.post(
                reverse("integrations:zoho_status"),
                {"action": "check"},
            )
        self.assertEqual(response.status_code, 403)
        build.assert_not_called()

    def test_public_status_rejects_arbitrary_action_without_external_call(self):
        with patch("integrations.views.ZohoServices.build") as build:
            response = self.client.post(
                reverse("integrations:zoho_status"),
                {
                    "action": "coql",
                    "endpoint": "https://attacker.invalid/",
                    "query": "SELECT * FROM Contacts",
                },
            )
        self.assertEqual(response.status_code, 302)
        build.assert_not_called()

    def test_public_connect_is_post_only_and_binds_anonymous_session(self):
        self.assertEqual(
            self.client.get(reverse("integrations:zoho_connect")).status_code,
            405,
        )
        self._begin_oauth()
        session = self.client.session
        stored = session["zoho_oauth_state"]
        self.assertIsNotNone(session.session_key)
        self.assertEqual(stored["user_id"], None)
        self.assertEqual(
            stored["session_digest"],
            hashlib.sha256(session.session_key.encode("utf-8")).hexdigest(),
        )
        self.assertNotEqual(stored["digest"], "anonymous-state")
        self.assertEqual(stored["profile"], "production")

    def test_public_connect_preserves_csrf(self):
        client = Client(enforce_csrf_checks=True)
        response = client.get(reverse("integrations:zoho_status"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            client.post(reverse("integrations:zoho_connect")).status_code,
            403,
        )
        csrf_token = client.cookies["csrftoken"].value
        oauth = Mock()
        oauth.generate_state.return_value = "anonymous-state"
        oauth.authorization_url.return_value = (
            "https://accounts.zoho.com/oauth/v2/auth?fixed=1"
        )
        with patch(
            "integrations.views.ZohoServices.build",
            return_value=SimpleNamespace(oauth=oauth),
        ):
            response = client.post(
                reverse("integrations:zoho_connect"),
                HTTP_X_CSRFTOKEN=csrf_token,
            )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("https://accounts.zoho.com/"))

    def test_public_callback_accepts_valid_state_once(self):
        self._begin_oauth()
        oauth = Mock()
        callback = reverse("integrations:zoho_callback")
        with patch(
            "integrations.views.ZohoServices.build",
            return_value=SimpleNamespace(oauth=oauth),
        ):
            response = self.client.get(
                callback,
                {"state": "anonymous-state", "code": "authorization-code"},
            )
        self.assertEqual(response.status_code, 302)
        oauth.exchange_code.assert_called_once_with("authorization-code")
        self.assertNotIn("authorization-code", response.content.decode())

        with patch("integrations.views.ZohoServices.build") as build:
            reused = self.client.get(
                callback,
                {"state": "anonymous-state", "code": "authorization-code"},
            )
        self.assertEqual(reused.status_code, 302)
        build.assert_not_called()

    def test_public_callback_rejects_missing_invalid_and_expired_state(self):
        callback = reverse("integrations:zoho_callback")
        with patch("integrations.views.ZohoServices.build") as build:
            self.client.get(callback, {"code": "authorization-code"})
        build.assert_not_called()

        self._begin_oauth()
        with patch("integrations.views.ZohoServices.build") as build:
            self.client.get(
                callback, {"state": "tampered", "code": "authorization-code"}
            )
        build.assert_not_called()

        self._begin_oauth()
        session = self.client.session
        stored = session["zoho_oauth_state"]
        stored["created_at"] = time.time() - 601
        session["zoho_oauth_state"] = stored
        session.save()
        with patch("integrations.views.ZohoServices.build") as build:
            self.client.get(
                callback,
                {"state": "anonymous-state", "code": "authorization-code"},
            )
        build.assert_not_called()

    def test_public_state_cannot_cross_anonymous_sessions(self):
        first = self._begin_oauth(Client())
        stolen_state = dict(first.session["zoho_oauth_state"])
        second = Client()
        second_session = second.session
        second_session["zoho_oauth_state"] = stolen_state
        second_session.save()
        with patch("integrations.views.ZohoServices.build") as build:
            response = second.get(
                reverse("integrations:zoho_callback"),
                {"state": "anonymous-state", "code": "authorization-code"},
            )
        self.assertEqual(response.status_code, 302)
        build.assert_not_called()

    def test_only_three_zoho_setup_routes_exist(self):
        for path in (
            "/integrations/zoho/coql/",
            "/integrations/zoho/modules/",
            "/integrations/zoho/fields/",
            "/integrations/zoho/records/",
            "/integrations/zoho/export/",
        ):
            self.assertEqual(self.client.get(path).status_code, 404)


@modify_settings(
    MIDDLEWARE={"remove": ["vault.middleware.SecureSessionMiddleware"]}
)
@override_settings(**VALID)
class ZohoDisabledRegressionTests(TestCase):
    def test_anonymous_callback_and_vault_remain_protected(self):
        self.assertEqual(
            self.client.get(reverse("integrations:zoho_callback")).status_code,
            302,
        )
        vault_response = self.client.get("/vault/")
        self.assertEqual(vault_response.status_code, 302)

    def test_soat_keeps_its_existing_public_behavior(self):
        self.assertEqual(self.client.get("/soat/").status_code, 200)

    def test_anonymous_cannot_check_connection_when_public_mode_is_disabled(self):
        with patch("integrations.views.ZohoServices.build") as build:
            response = self.client.post(
                reverse("integrations:zoho_status"),
                {"action": "check"},
            )
        self.assertEqual(response.status_code, 302)
        build.assert_not_called()
