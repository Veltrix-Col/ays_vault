from __future__ import annotations

import time
import uuid

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.core import signing
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .assertions import (
    ExpiredAssertion,
    InvalidAssertion,
    ReplayedAssertion,
    WrongAudienceAssertion,
    verify_intranet_assertion,
)
from .delegated_access import validate_intranet_session, SESSION_SALT, _cookie_name
from .models import ConsumedAssertion

_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVATE_PEM = _PRIVATE_KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode()
PUBLIC_PEM = _PRIVATE_KEY.public_key().public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
).decode()

_OTHER_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_PRIVATE_PEM = _OTHER_PRIVATE_KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode()

AUDIENCE = "bh.seguros.com"
ISSUER = "seguros.com"


def make_token(*, private_key=PRIVATE_PEM, sub="empleado@seguros.com", aud=AUDIENCE, iss=ISSUER, ttl=60, jti=None, iat_offset=0):
    now = int(time.time()) + iat_offset
    claims = {"sub": sub, "aud": aud, "iss": iss, "iat": now, "exp": now + ttl, "jti": jti or uuid.uuid4().hex}
    return jwt.encode(claims, private_key, algorithm="RS256")


SSO_SETTINGS = dict(
    INTRANET_SSO_PUBLIC_KEY=PUBLIC_PEM,
    INTRANET_SSO_AUDIENCE=AUDIENCE,
    INTRANET_SSO_ISSUER=ISSUER,
    INTRANET_SSO_ASSERTION_MAX_AGE=60,
    INTRANET_SSO_AUTHORIZE_URL="https://seguros.com/wp-json/intranet-sso/v1/authorize",
    INTRANET_SSO_SESSION_MAX_AGE=2700,
)


@override_settings(**SSO_SETTINGS)
class VerifyIntranetAssertionTests(TestCase):
    def test_valid_token_is_accepted_and_consumed_once(self):
        token = make_token()
        identity = verify_intranet_assertion(token)
        self.assertEqual(identity.subject, "empleado@seguros.com")
        self.assertEqual(ConsumedAssertion.objects.count(), 1)

    def test_replayed_token_is_rejected(self):
        token = make_token()
        verify_intranet_assertion(token)
        with self.assertRaises(ReplayedAssertion):
            verify_intranet_assertion(token)

    def test_expired_token_is_rejected(self):
        token = make_token(ttl=-5)
        with self.assertRaises(ExpiredAssertion):
            verify_intranet_assertion(token)

    def test_wrong_audience_is_rejected(self):
        token = make_token(aud="otra-app.seguros.com")
        with self.assertRaises(WrongAudienceAssertion):
            verify_intranet_assertion(token)

    def test_wrong_issuer_is_rejected(self):
        token = make_token(iss="impostor.example")
        with self.assertRaises(InvalidAssertion):
            verify_intranet_assertion(token)

    def test_signature_from_untrusted_key_is_rejected(self):
        token = make_token(private_key=OTHER_PRIVATE_PEM)
        with self.assertRaises(InvalidAssertion):
            verify_intranet_assertion(token)

    def test_overlong_lifetime_is_rejected_even_if_not_yet_expired(self):
        token = make_token(ttl=3600)
        with self.assertRaises(InvalidAssertion):
            verify_intranet_assertion(token)

    def test_missing_subject_is_rejected(self):
        token = make_token(sub="")
        with self.assertRaises(InvalidAssertion):
            verify_intranet_assertion(token)


@override_settings(**SSO_SETTINGS)
class ValidateIntranetSessionTests(TestCase):
    def _request(self, cookie_value=None):
        client = Client()
        if cookie_value is not None:
            client.cookies[_cookie_name()] = cookie_value
        response = client.get(reverse("public_home"))
        return response.wsgi_request

    def test_no_cookie_offers_a_challenge_redirect(self):
        result = validate_intranet_session(request=self._request(), application="soat")
        self.assertFalse(result.allowed)
        self.assertEqual(result.category, "no_session")
        self.assertIsNotNone(result.challenge_redirect)
        self.assertIn("wp-json/intranet-sso", result.challenge_redirect)

    def test_valid_cookie_is_allowed_and_exposes_the_subject_email(self):
        value = signing.dumps({"sub": "empleado@seguros.com", "aud": AUDIENCE}, salt=SESSION_SALT)
        result = validate_intranet_session(request=self._request(value), application="soat")
        self.assertTrue(result.allowed)
        self.assertEqual(result.subject, "empleado@seguros.com")

    def test_expired_cookie_offers_a_challenge_redirect(self):
        value = signing.dumps({"sub": "empleado@seguros.com", "aud": AUDIENCE}, salt=SESSION_SALT)
        with override_settings(INTRANET_SSO_SESSION_MAX_AGE=-1):
            result = validate_intranet_session(request=self._request(value), application="soat")
        self.assertEqual(result.category, "expired_token")
        self.assertIsNotNone(result.challenge_redirect)

    def test_tampered_cookie_is_rejected_without_redirect(self):
        result = validate_intranet_session(request=self._request("tampered-value"), application="soat")
        self.assertEqual(result.category, "invalid_token")
        self.assertIsNone(result.challenge_redirect)

    def test_cookie_for_a_different_audience_is_rejected(self):
        value = signing.dumps({"sub": "empleado@seguros.com", "aud": "otra-app.seguros.com"}, salt=SESSION_SALT)
        result = validate_intranet_session(request=self._request(value), application="soat")
        self.assertEqual(result.category, "wrong_audience")
        self.assertIsNone(result.challenge_redirect)


@override_settings(**SSO_SETTINGS)
class CallbackViewTests(TestCase):
    def test_valid_assertion_sets_cookie_and_redirects_to_next(self):
        token = make_token()
        response = self.client.get(reverse("intranet_sso:callback"), {"sso_token": token, "next": "/soat/"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/soat/")
        self.assertIn(_cookie_name(), response.cookies)

    def test_missing_token_is_rejected(self):
        response = self.client.get(reverse("intranet_sso:callback"), {"next": "/soat/"})
        self.assertEqual(response.status_code, 400)
        self.assertNotIn(_cookie_name(), response.cookies)

    def test_invalid_token_is_rejected(self):
        response = self.client.get(reverse("intranet_sso:callback"), {"sso_token": "not-a-jwt", "next": "/soat/"})
        self.assertEqual(response.status_code, 400)
        self.assertNotIn(_cookie_name(), response.cookies)

    def test_open_redirect_attempt_falls_back_to_public_home(self):
        token = make_token()
        response = self.client.get(
            reverse("intranet_sso:callback"),
            {"sso_token": token, "next": "https://evil.example/phish"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("public_home"))

    def test_protocol_relative_next_falls_back_to_public_home(self):
        token = make_token()
        response = self.client.get(
            reverse("intranet_sso:callback"),
            {"sso_token": token, "next": "//evil.example/phish"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("public_home"))


@override_settings(DEBUG=False, RUNNING_TESTS=False, TOOLS_ACCESS_MODE="trusted_intranet", **SSO_SETTINGS)
class MiddlewareChallengeRedirectTests(TestCase):
    @override_settings(TOOLS_DELEGATED_ACCESS_VALIDATOR="intranet_sso.delegated_access.validate_intranet_session")
    def test_first_visit_without_cookie_redirects_to_intranet_login(self):
        response = self.client.get(reverse("soat:upload"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("wp-json/intranet-sso", response["Location"])

    @override_settings(TOOLS_DELEGATED_ACCESS_VALIDATOR="intranet_sso.delegated_access.validate_intranet_session")
    def test_valid_delegated_cookie_grants_access(self):
        value = signing.dumps({"sub": "empleado@seguros.com", "aud": AUDIENCE}, salt=SESSION_SALT)
        self.client.cookies[_cookie_name()] = value
        response = self.client.get(reverse("soat:upload"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.wsgi_request.delegated_access.subject, "empleado@seguros.com")
