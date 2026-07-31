from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

from django.test import TestCase, override_settings
from django.urls import reverse

from integrations.tests.helpers import VALID_SETTINGS


BASE_CSP = (
    "default-src 'self'; "
    "img-src 'self' data:; "
    "style-src 'self'; "
    "script-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'none'; "
    "form-action 'self'"
)
ZOHO_CSP = f"{BASE_CSP} https://accounts.zoho.com"


@override_settings(
    **{**VALID_SETTINGS, "ZOHO_PUBLIC_SETUP_ENABLED": True}
)
class ZohoContentSecurityPolicyTests(TestCase):
    def assert_base_policy_is_unchanged(self, policy):
        self.assertIn("default-src 'self'", policy)
        self.assertIn("img-src 'self' data:", policy)
        self.assertIn("style-src 'self'", policy)
        self.assertIn("script-src 'self'", policy)
        self.assertIn("object-src 'none'", policy)
        self.assertIn("base-uri 'self'", policy)
        self.assertIn("frame-ancestors 'none'", policy)
        self.assertNotIn("'unsafe-inline'", policy)
        self.assertNotIn("'unsafe-eval'", policy)
        self.assertNotIn("*.zoho.com", policy)
        self.assertNotIn("form-action *", policy)
        self.assertNotIn("form-action https:", policy)

    def test_status_has_exact_zoho_form_action(self):
        response = self.client.get(reverse("integrations:zoho_status"))
        self.assertEqual(response.status_code, 200)
        policy = response["Content-Security-Policy"]
        self.assertEqual(policy, ZOHO_CSP)
        self.assert_base_policy_is_unchanged(policy)
        self.assertContains(
            response,
            f'action="{reverse("integrations:zoho_connect")}"',
        )
        self.assertContains(response, 'method="post"')

    def test_connect_redirect_has_exact_policy_and_authorized_host(self):
        oauth = Mock()
        oauth.generate_state.return_value = "safe-state"
        oauth.authorization_url.return_value = (
            "https://accounts.zoho.com/oauth/v2/auth?state=safe-state"
        )
        with patch(
            "integrations.views.ZohoServices.build",
            return_value=SimpleNamespace(oauth=oauth),
        ):
            response = self.client.post(reverse("integrations:zoho_connect"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Content-Security-Policy"], ZOHO_CSP)
        destination = urlsplit(response["Location"])
        self.assertEqual(destination.scheme, "https")
        self.assertEqual(destination.hostname, "accounts.zoho.com")
        self.assertEqual(destination.path, "/oauth/v2/auth")
        self.assertNotIn("client-secret", response["Location"])
        self.assertNotIn("refresh-secret", response["Location"])

    def test_callback_has_exact_zoho_form_action(self):
        response = self.client.get(reverse("integrations:zoho_callback"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Content-Security-Policy"], ZOHO_CSP)
        self.assert_base_policy_is_unchanged(
            response["Content-Security-Policy"]
        )

    def test_non_zoho_routes_keep_exact_base_policy(self):
        cases = (
            ("/", 200),
            ("/vault/", 302),
            ("/soat/", 200),
            ("/route-that-does-not-exist/", 404),
        )
        for path, expected_status in cases:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, expected_status)
                policy = response["Content-Security-Policy"]
                self.assertEqual(policy, BASE_CSP)
                self.assertNotIn("https://accounts.zoho.com", policy)
                self.assert_base_policy_is_unchanged(policy)

    def test_similar_integration_path_does_not_receive_exception(self):
        response = self.client.get("/integrations/zoho/status/extra/")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response["Content-Security-Policy"], BASE_CSP)
        self.assertNotIn(
            "https://accounts.zoho.com",
            response["Content-Security-Policy"],
        )
