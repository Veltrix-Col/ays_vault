from __future__ import annotations

import httpx
from django.test import SimpleTestCase, override_settings

from integrations.tests.helpers import FakeOAuth, VALID_SETTINGS as VALID, client_factory
from integrations.zoho.client import ZohoClient
from integrations.zoho.exceptions import (
    ZohoAuthenticationError,
    ZohoAuthorizationError,
    ZohoInvalidResponseError,
    ZohoNotFoundError,
    ZohoRateLimitError,
    ZohoTimeoutError,
    ZohoValidationError,
)
from integrations.zoho.settings import ZohoSettings


@override_settings(**VALID)
class ZohoClientTests(SimpleTestCase):
    def build(self, handler, *, oauth=None, sleeps=None):
        return ZohoClient(
            oauth=oauth or FakeOAuth(),
            config=ZohoSettings.from_django(),
            client_factory=client_factory(handler),
            sleeper=(sleeps if sleeps is not None else []).append,
        )

    def test_success_headers_and_json(self):
        def handler(request):
            self.assertEqual(request.headers["Authorization"], "Zoho-oauthtoken access-secret")
            self.assertEqual(request.headers["User-Agent"], "A&S-Banco-Herramientas/1.0")
            return httpx.Response(200, json={"data": []})

        payload = self.build(handler).get(
            "/crm/v8/Contacts", logical_endpoint="records"
        )
        self.assertEqual(payload, {"data": []})

    def test_401_refreshes_once_then_fails(self):
        oauth = FakeOAuth()
        client = self.build(
            lambda _request: httpx.Response(401, json={"code": "INVALID_OAUTHTOKEN"}),
            oauth=oauth,
        )
        with self.assertRaises(ZohoAuthenticationError):
            client.get("/crm/v8/org", logical_endpoint="organization")
        self.assertEqual(oauth.invalidations, 1)

    def test_status_mapping(self):
        cases = {
            403: ZohoAuthorizationError,
            404: ZohoNotFoundError,
            429: ZohoRateLimitError,
        }
        for status, error in cases.items():
            with self.subTest(status=status):
                client = self.build(
                    lambda _request, status=status: httpx.Response(status, json={})
                )
                with self.assertRaises(error):
                    client.get("/crm/v8/org", logical_endpoint="organization")

    def test_500_retries_are_limited(self):
        calls = []
        sleeps = []

        def handler(_request):
            calls.append(1)
            return httpx.Response(500, json={})

        with self.assertRaises(Exception):
            self.build(handler, sleeps=sleeps).get(
                "/crm/v8/org", logical_endpoint="organization"
            )
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(sleeps), 2)

    def test_non_json_success_rejected(self):
        client = self.build(lambda _request: httpx.Response(200, text="<html>"))
        with self.assertRaises(ZohoInvalidResponseError):
            client.get("/crm/v8/org", logical_endpoint="organization")

    def test_timeout_retries_are_limited(self):
        calls = []
        sleeps = []

        def handler(request):
            calls.append(1)
            raise httpx.ReadTimeout("timeout", request=request)

        with self.assertRaises(ZohoTimeoutError):
            self.build(handler, sleeps=sleeps).get(
                "/crm/v8/org", logical_endpoint="organization"
            )
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(sleeps), 2)

    def test_arbitrary_endpoint_and_write_method_rejected(self):
        client = self.build(lambda _request: httpx.Response(200, json={}))
        with self.assertRaises(ZohoValidationError):
            client.get("https://attacker.invalid", logical_endpoint="invalid")
        with self.assertRaises(ZohoValidationError):
            client.request(
                "DELETE", "/crm/v8/Contacts", logical_endpoint="invalid"
            )

    def test_logs_never_contain_token(self):
        with self.assertLogs("integrations.zoho", level="INFO") as captured:
            self.build(lambda _request: httpx.Response(200, json={})).get(
                "/crm/v8/org", logical_endpoint="organization"
            )
        self.assertNotIn("access-secret", "\n".join(captured.output))
