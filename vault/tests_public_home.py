from django.conf import settings
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from django.urls import resolve, reverse

from portal.views import public_home
from vault.forms import MFAEnrollmentForm, OTPVerificationForm


class PublicHomeTests(TestCase):
    def test_root_is_public_home_and_vault_uses_protected_entry(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(resolve("/").func, public_home)
        self.assertTemplateUsed(response, "portal/home.html")
        self.assertFalse(response.has_header("Location"))
        self.assertContains(response, "Portal de Aplicaciones A&amp;S", html=False)
        self.assertContains(response, "Seleccione el módulo al que desea acceder.")
        self.assertContains(response, f'href="{reverse("vault:dashboard")}"')
        self.assertEqual(reverse("login"), "/login/")
        self.assertEqual(reverse("vault:dashboard"), "/vault/")

    def test_root_remains_portal_for_authenticated_user(self):
        user = get_user_model().objects.create_user("portal.autenticado")
        self.client.force_login(user)
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "portal/home.html")
        self.assertFalse(response.has_header("Location"))
        self.assertContains(response, "Portal de Aplicaciones A&amp;S", html=False)
        self.assertContains(response, "A&amp;S Vault")

    def test_root_ignores_invalid_session_cookie(self):
        self.client.cookies[settings.SESSION_COOKIE_NAME] = "invalid-or-revoked-session"
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "portal/home.html")
        self.assertFalse(response.has_header("Location"))

    def test_vault_entry_uses_existing_authentication_guard(self):
        response = self.client.get(reverse("vault:dashboard"))
        self.assertRedirects(
            response,
            f'{reverse("login")}?next={reverse("vault:dashboard")}',
            fetch_redirect_response=False,
        )

    def test_other_vault_route_remains_protected(self):
        response = self.client.get(reverse("vault:card_list"))
        self.assertRedirects(
            response,
            f'{reverse("login")}?next={reverse("vault:card_list")}',
            fetch_redirect_response=False,
        )

    def test_catalog_renders_two_cards_without_system_information(self):
        response = self.client.get("/")
        self.assertContains(response, 'class="application-card', count=2)
        self.assertContains(response, "A&amp;S Vault")
        self.assertContains(response, "Gestión SOAT")
        self.assertNotContains(response, "Centro de Control")
        self.assertNotContains(response, "Correo y destinatarios")
        self.assertNotContains(response, "Cerrar sesión")

    @override_settings(SOAT_APP_URL="")
    def test_soat_is_disabled_when_url_is_not_configured(self):
        response = self.client.get("/")
        self.assertContains(response, "Acceso no configurado")
        self.assertNotContains(response, 'href=""')

    @override_settings(SOAT_APP_URL="https://soat.example.invalid/access")
    def test_soat_uses_configured_http_url(self):
        response = self.client.get("/")
        self.assertContains(response, 'href="https://soat.example.invalid/access"')
        self.assertContains(response, 'rel="noopener noreferrer"')

    @override_settings(SOAT_APP_URL="javascript:alert(1)")
    def test_soat_rejects_unsafe_configured_url(self):
        response = self.client.get("/")
        self.assertContains(response, "Acceso no configurado")
        self.assertNotContains(response, "javascript:")

    def test_authentication_and_blocked_access_screens_return_to_portal(self):
        contexts = {
            "registration/login.html": {"form": None},
            "registration/mfa_verify.html": {"form": OTPVerificationForm()},
            "registration/mfa_enroll.html": {
                "form": MFAEnrollmentForm(),
                "manual_key": "",
                "qr_data_uri": "",
            },
            "registration/recovery_codes.html": {"codes": [], "first_display": False},
            "vault/access_denied.html": {},
        }
        for template_name, context in contexts.items():
            with self.subTest(template=template_name):
                content = render_to_string(template_name, context)
                self.assertIn('href="/"', content)
                self.assertIn("Volver al Portal de Aplicaciones", content)
