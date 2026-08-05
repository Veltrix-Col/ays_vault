from __future__ import annotations

from unittest.mock import patch
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from cotizacion_colectivos.dto import CompanySearchResult
from vault.crypto import encrypt
from vault.models import SecureSession
from vault.security import session_hash


@override_settings(ZOHO_ACTIVE_PROFILE="sandbox")
class ColectivosViewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser("colectivos-admin", "admin@example.test", "Password123!")
        self.regular = User.objects.create_user("colectivos-user", password="Password123!")
        for user in (self.admin, self.regular):
            TOTPDevice.objects.create(user=user, name="Test", confirmed=True)

    def authenticated_client(self, user, *, csrf=False):
        client = Client(enforce_csrf_checks=csrf, REMOTE_ADDR="10.0.0.8", HTTP_USER_AGENT="Colectivos Test")
        client.force_login(user)
        session = client.session
        device = TOTPDevice.objects.get(user=user)
        session["otp_device_id"] = device.persistent_id
        session.save()
        now = timezone.now()
        SecureSession.objects.create(
            user=user,
            session_hash=session_hash(session.session_key),
            encrypted_session_key=encrypt(session.session_key),
            last_activity_at=now,
            expires_at=now + timedelta(minutes=10),
            initial_ip="10.0.0.8",
            last_ip="10.0.0.8",
            user_agent="Colectivos Test",
            status=SecureSession.ACTIVE,
            mfa_completed=True,
            mfa_completed_at=now,
        )
        return client

    def test_anonymous_and_user_without_vault_mfa_can_open_local_tool(self):
        url = reverse("cotizacion_colectivos:index")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        client = Client()
        client.force_login(self.regular)
        response = client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("otp_device_id", client.session)

    def test_inactive_superuser_does_not_create_a_vault_bypass(self):
        self.admin.is_active = False
        self.admin.save(update_fields=["is_active"])
        client = Client()
        client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(client.get(reverse("cotizacion_colectivos:index")).status_code, 200)
        self.assertEqual(client.get(reverse("vault:dashboard")).status_code, 302)

    def test_superuser_sees_only_the_two_direct_search_forms(self):
        client = self.authenticated_client(self.admin)
        response = client.get(reverse("cotizacion_colectivos:index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "NIT o nombre de empresa")
        self.assertContains(response, "Cédula o nombre del individuo")
        self.assertContains(response, 'id="id_company_query"')
        self.assertContains(response, 'id="id_person_query"')
        self.assertContains(response, 'name="query"', count=2)
        self.assertNotContains(response, "disabled")
        self.assertNotContains(response, "Próximamente")
        self.assertNotContains(response, "Solicitudes")
        self.assertContains(response, "Sandbox · Solo lectura")

    @override_settings(ZOHO_ACTIVE_PROFILE="production")
    def test_production_badge_is_derived_from_configuration(self):
        response = self.client.get(reverse("cotizacion_colectivos:index"))
        self.assertContains(response, "Producción · Solo lectura")
        self.assertContains(response, "environment-badge--production")
        self.assertNotContains(response, "Sandbox · Solo lectura")

    def test_browser_cannot_select_the_profile(self):
        response = self.client.get(
            reverse("cotizacion_colectivos:index"), {"profile": "production"}
        )
        self.assertContains(response, "Sandbox · Solo lectura")
        self.assertNotContains(response, "Producción · Solo lectura")

    def test_numeric_prefix_shorter_than_three_is_rejected(self):
        client = self.authenticated_client(self.admin)
        response = client.post(reverse("cotizacion_colectivos:company_search"), {"query": "90"})
        self.assertContains(response, "al menos 3 dígitos")
        self.assertEqual(response["Cache-Control"], "max-age=0, no-cache, no-store, must-revalidate, private")

    @patch("cotizacion_colectivos.views.CompanySearchService")
    def test_company_search_renders_masked_results(self, service):
        service.return_value.search.return_value = (
            CompanySearchResult("signed-token", "Empresa Segura", "•••••••567", "Activo"),
        )
        client = self.authenticated_client(self.admin)
        response = client.post(reverse("cotizacion_colectivos:company_search"), {"query": "9001234567"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Empresa Segura")
        self.assertContains(response, "•••••••567")
        self.assertNotContains(response, "9001234567")

    @patch("cotizacion_colectivos.views.CompanySearchService")
    def test_search_log_contains_metrics_but_not_document(self, service):
        service.return_value.search.return_value = ()
        client = self.authenticated_client(self.admin)
        with self.assertLogs("cotizacion_colectivos", level="INFO") as captured:
            client.post(reverse("cotizacion_colectivos:company_search"), {"query": "9001234567"})
        output = " ".join(captured.output)
        self.assertIn("entity=company", output)
        self.assertIn("application=cotizacion_colectivos", output)
        self.assertIn("operation=search", output)
        self.assertIn("profile=sandbox", output)
        self.assertNotIn("9001234567", output)

    @patch("cotizacion_colectivos.views.CompanySearchService")
    def test_unknown_search_error_returns_safe_message(self, service):
        service.return_value.search.side_effect = RuntimeError("secret endpoint and token")
        client = self.authenticated_client(self.admin)
        response = client.post(reverse("cotizacion_colectivos:company_search"), {"query": "Empresa"})
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "Sandbox no está disponible", status_code=503)
        self.assertNotContains(response, "secret", status_code=503)

    @override_settings(ZOHO_ACTIVE_PROFILE="production")
    @patch("cotizacion_colectivos.views.CompanySearchService")
    def test_production_error_and_log_are_sanitized(self, service):
        service.return_value.search.side_effect = RuntimeError("private token detail")
        with self.assertLogs("cotizacion_colectivos", level="INFO") as captured:
            response = self.client.post(
                reverse("cotizacion_colectivos:company_search"), {"query": "Empresa"}
            )
        self.assertContains(response, "Producción no está disponible", status_code=503)
        output = " ".join(captured.output)
        self.assertIn("profile=production", output)
        self.assertNotIn("private token", output)

    @patch("cotizacion_colectivos.views.CompanySearchService")
    def test_empty_short_and_zero_results(self, service):
        client = self.authenticated_client(self.admin)
        url = reverse("cotizacion_colectivos:company_search")
        self.assertContains(client.post(url, {"query": ""}), "Ingrese un criterio")
        self.assertContains(client.post(url, {"query": "ab"}), "al menos 3 caracteres")
        service.return_value.search.return_value = ()
        self.assertContains(client.post(url, {"query": "Nada"}), "Sin resultados")

    def test_post_without_csrf_is_rejected(self):
        client = self.authenticated_client(self.admin, csrf=True)
        response = client.post(reverse("cotizacion_colectivos:company_search"), {"query": "Empresa"})
        self.assertEqual(response.status_code, 403)

    def test_invalid_detail_token_returns_404_without_calling_zoho(self):
        client = self.authenticated_client(self.admin)
        with self.assertLogs("cotizacion_colectivos", level="INFO") as captured, patch(
            "cotizacion_colectivos.views.EntityDetailService"
        ) as service:
            from cotizacion_colectivos.services.common import ColectivosServiceError

            service.return_value.company.side_effect = ColectivosServiceError("invalid_record", "inválido")
            response = client.get(reverse("cotizacion_colectivos:company_detail", args=["bad-token"]))
        self.assertEqual(response.status_code, 404)
        output = " ".join(captured.output)
        self.assertIn("operation=detail", output)
        self.assertIn("profile=sandbox", output)
        self.assertNotIn("bad-token", output)

    def test_portal_contains_authorized_link_and_soat_remains(self):
        response = self.client.get(reverse("public_home"))
        self.assertContains(response, "Cotización – Colectivos")
        self.assertContains(response, reverse("cotizacion_colectivos:index"))
        self.assertContains(response, "Gestión SOAT")
