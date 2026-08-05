from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from config.application_access import DelegatedAccessResult
from cotizacion_colectivos.dto import CompanyDetail, ContactSummary
from cotizacion_colectivos.services.common import ColectivosServiceError, sign_record_id
from vault.models import AuditEvent


def allow_delegated_access(*, request, application):
    return DelegatedAccessResult(True, "validated_by_test_double")


def reject_invalid_token(*, request, application):
    return DelegatedAccessResult(False, "invalid_token")


def reject_expired_token(*, request, application):
    return DelegatedAccessResult(False, "expired_token")


def reject_wrong_audience(*, request, application):
    return DelegatedAccessResult(False, "wrong_audience")


LOCAL = {"DEBUG": True, "TOOLS_ACCESS_MODE": "local_public", "COLECTIVOS_INTERNAL_PUBLIC_ACCESS": True}
TRUSTED = {"DEBUG": False, "TOOLS_ACCESS_MODE": "trusted_intranet", "COLECTIVOS_INTERNAL_PUBLIC_ACCESS": False}


@override_settings(**LOCAL)
class LocalApplicationAccessTests(TestCase):
    def test_anonymous_opens_portal_soat_and_colectivos(self):
        for url in (
            reverse("public_home"),
            reverse("soat:upload"),
            reverse("cotizacion_colectivos:index"),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_anonymous_colectivos_post_keeps_csrf(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post(
            reverse("cotizacion_colectivos:company_search"), {"query": "Empresa"}
        )
        self.assertEqual(response.status_code, 403)

    @patch("cotizacion_colectivos.views.EntityDetailService")
    def test_anonymous_signed_detail_uses_existing_safe_flow(self, service):
        signed_token = sign_record_id("1234567890123456789")
        service.return_value.company.return_value = CompanyDetail(
            display_name="Empresa segura",
            legal_name="Razón segura",
            id_type="NIT",
            masked_document="••••567",
            state="Activo",
            summary=ContactSummary("Persona jurídica", "NIT", "••••567", "Activo"),
            policies=(),
            direct_policies=(),
            insured=(),
            risks=(),
        )
        response = self.client.get(
            reverse("cotizacion_colectivos:company_detail", args=[signed_token])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "••••567")
        service.return_value.company.assert_called_once_with(signed_token)

    @patch("cotizacion_colectivos.views.EntityDetailService")
    def test_altered_detail_token_is_rejected(self, service):
        service.return_value.company.side_effect = ColectivosServiceError(
            "invalid_record", "inválido"
        )
        response = self.client.get(
            reverse("cotizacion_colectivos:company_detail", args=["altered"])
        )
        self.assertEqual(response.status_code, 404)

    def test_tools_do_not_create_vault_authentication_or_mfa(self):
        before = AuditEvent.objects.count()
        response = self.client.get(reverse("cotizacion_colectivos:index"))
        self.client.get(reverse("soat:upload"))
        self.assertFalse(response.wsgi_request.user.is_authenticated)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertNotIn("otp_device_id", self.client.session)
        self.assertEqual(AuditEvent.objects.count(), before)
        self.assertIn("no-store", response["Cache-Control"])

    def test_vault_still_requires_login_and_mfa(self):
        self.assertEqual(self.client.get(reverse("vault:dashboard")).status_code, 302)
        user = get_user_model().objects.create_user("no-mfa", password="Password123!")
        client = Client()
        client.force_login(user)
        response = client.get(reverse("vault:dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])


@override_settings(**TRUSTED)
class TrustedIntranetAccessTests(TestCase):
    def test_missing_validator_fails_closed_for_inherited_tools(self):
        for url in (
            reverse("public_home"),
            reverse("soat:upload"),
            reverse("cotizacion_colectivos:index"),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 403)
                self.assertNotIn("Location", response)
        vault_response = self.client.get(reverse("vault:dashboard"))
        self.assertEqual(vault_response.status_code, 302)
        self.assertIn(reverse("login"), vault_response["Location"])

    @override_settings(
        TOOLS_DELEGATED_ACCESS_VALIDATOR=(
            "config.tests_application_access.allow_delegated_access"
        )
    )
    def test_approved_validator_allows_only_inherited_tools(self):
        self.assertEqual(self.client.get(reverse("soat:upload")).status_code, 200)
        self.assertEqual(
            self.client.get(reverse("cotizacion_colectivos:index")).status_code, 200
        )
        self.assertEqual(self.client.get(reverse("public_home")).status_code, 200)
        vault_response = self.client.get(reverse("vault:dashboard"))
        self.assertEqual(vault_response.status_code, 302)
        self.assertIn(reverse("login"), vault_response["Location"])
        self.assertNotIn("otp_device_id", self.client.session)

    def test_client_header_or_query_cannot_enable_access(self):
        response = self.client.get(
            reverse("cotizacion_colectivos:index") + "?trusted=true",
            HTTP_X_TRUSTED_ACCESS="true",
            HTTP_AUTHORIZATION="Bearer invented",
        )
        self.assertEqual(response.status_code, 403)

    def test_invalid_expired_and_wrong_audience_are_rejected(self):
        validators = (
            "reject_invalid_token",
            "reject_expired_token",
            "reject_wrong_audience",
        )
        for validator in validators:
            with self.subTest(validator=validator), override_settings(
                TOOLS_DELEGATED_ACCESS_VALIDATOR=(
                    f"config.tests_application_access.{validator}"
                )
            ):
                self.assertEqual(
                    self.client.get(reverse("cotizacion_colectivos:index")).status_code,
                    403,
                )

    @override_settings(
        TOOLS_DELEGATED_ACCESS_VALIDATOR=(
            "config.tests_application_access.reject_invalid_token"
        )
    )
    def test_rejection_log_is_sanitized(self):
        marker = "token-value-must-not-be-logged"
        with self.assertLogs("application_access", level="WARNING") as captured:
            self.client.get(
                reverse("soat:upload"), HTTP_AUTHORIZATION=f"Bearer {marker}"
            )
        output = " ".join(captured.output)
        self.assertIn("application=soat", output)
        self.assertIn("category=invalid_token", output)
        self.assertNotIn(marker, output)

    @override_settings(TOOLS_ACCESS_MODE="invalid")
    def test_invalid_access_mode_is_rejected(self):
        self.assertEqual(self.client.get(reverse("soat:upload")).status_code, 403)


@override_settings(DEBUG=False, RUNNING_TESTS=False, TOOLS_ACCESS_MODE="local_public")
class UnsafeLocalModeTests(TestCase):
    def test_local_public_is_rejected_outside_debug(self):
        self.assertEqual(self.client.get(reverse("soat:upload")).status_code, 403)
