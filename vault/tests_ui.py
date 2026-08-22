from types import SimpleNamespace
from pathlib import Path

from django.contrib.staticfiles import finders
from django.template import Context, Template
from django.template.loader import render_to_string
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from .forms import AccessExceptionForm, NotificationRecipientForm, PolicyConfigurationForm
from .models import NotificationRecipient, PolicyConfiguration, UserProfile


class _TemplateUser:
    is_authenticated = True
    username = "persona.prueba"
    is_superuser = False

    def get_full_name(self):
        return "Persona de prueba"


class _TemplateProfile:
    def __init__(self, role):
        self.role = role
        self.can_view_cards = role in {UserProfile.LEADER, UserProfile.ANALYST}
        self.can_manage_cards = role == UserProfile.LEADER

    def get_role_display(self):
        return dict(UserProfile.ROLES)[self.role]


class InterfaceSpanishAndResponsiveTests(TestCase):
    def render_shell(self, role):
        request = RequestFactory().get("/")
        request.resolver_match = SimpleNamespace(url_name="dashboard")
        return render_to_string("base.html", {"request": request, "user": _TemplateUser(), "vault_profile": _TemplateProfile(role)})

    def test_policy_form_exposes_only_schedule_fields(self):
        form = PolicyConfigurationForm()
        self.assertEqual(form["timezone_name"].label, "Zona horaria")
        self.assertEqual(
            list(form.fields),
            [
                "timezone_name", "weekday_start", "weekday_end", "saturday_enabled",
                "saturday_start", "saturday_end", "sunday_enabled",
                "outside_hours_behavior", "reason",
            ],
        )
        self.assertNotIn("reauthentication_operations", form.fields)
        self.assertNotIn("inactivity_login_days", form.fields)
        self.assertNotIn("alert_review_hours", form.fields)
        self.assertNotIn("enabled", form.fields)

    def test_recipient_form_only_contains_name_and_email(self):
        recipient = NotificationRecipientForm()
        exception = AccessExceptionForm()
        self.assertEqual(list(recipient.fields), ["name", "email"])
        self.assertNotIn("alert_types", recipient.fields)
        self.assertNotIn("minimum_severity", recipient.fields)
        self.assertNotIn("delivery_mode", recipient.fields)
        self.assertEqual(exception["operations"].field.widget.__class__.__name__, "CheckboxSelectMultiple")
        self.assertNotIn("reason", recipient.fields)

    def test_recipient_form_trims_values_rejects_html_and_active_duplicates(self):
        valid = NotificationRecipientForm({"name": "  Control principal  ", "email": "  ADMIN@EXAMPLE.INVALID  "})
        self.assertTrue(valid.is_valid(), valid.errors)
        self.assertEqual(valid.cleaned_data["name"], "Control principal")
        self.assertEqual(valid.cleaned_data["email"], "admin@example.invalid")
        NotificationRecipient.objects.create(name="Existente", email="admin@example.invalid", active=True)
        duplicate = NotificationRecipientForm({"name": "Otro", "email": "ADMIN@example.invalid"})
        self.assertFalse(duplicate.is_valid())
        self.assertIn("email", duplicate.errors)
        unsafe = NotificationRecipientForm({"name": "<b>Administrador</b>", "email": "nuevo@example.invalid"})
        self.assertFalse(unsafe.is_valid())
        self.assertIn("name", unsafe.errors)

    def assertContainsHTML(self, html, text):
        self.assertIn(text, html)

    def test_admin_sidebar_is_grouped_and_does_not_expose_vault(self):
        html = self.render_shell(UserProfile.ADMIN)
        self.assertIn(">Centro de Control<", html)
        self.assertNotIn(">Resumen<", html)
        self.assertIn(">Horarios<", html)
        self.assertNotIn("Configuración de Seguridad", html)
        self.assertIn("Correo y destinatarios", html)
        self.assertNotIn(">Bóveda<", html)

    def test_operational_sidebar_respects_role(self):
        html = self.render_shell(UserProfile.LEADER)
        self.assertIn(">Bóveda<", html)
        self.assertNotIn("Resumen operativo", html)
        self.assertNotIn("Nueva tarjeta", html)
        self.assertNotIn("Línea de tiempo", html)
        self.assertNotIn("Informes", html)
        self.assertNotIn("Sesiones", html)
        self.assertNotIn("Dispositivos", html)
        self.assertNotIn("Correo y destinatarios", html)
        analyst_html = self.render_shell(UserProfile.ANALYST)
        self.assertIn(">Bóveda<", analyst_html)
        self.assertNotIn("Resumen personal", analyst_html)

    def test_shell_has_accessible_drawer_and_bounded_logo(self):
        html = self.render_shell(UserProfile.ADMIN)
        self.assertIn('aria-controls="app-sidebar"', html)
        self.assertIn('aria-expanded="false"', html)
        self.assertIn('class="cardmanager-brand"', html)
        self.assertIn(
            "/static/img/branding/cardmanager/Logo-CardManager-CO-BLANCO.png",
            html,
        )
        self.assertIn(
            "/static/img/branding/cardmanager/Logo-CardManager-COLOR.png",
            html,
        )
        self.assertIn('class="sidebar-backdrop"', html)

    def test_login_uses_joint_cardmanager_brand_without_changing_flow(self):
        response = Client().get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "/static/img/branding/cardmanager/Logo-CardManager-CO-COLOR.png",
        )
        self.assertContains(
            response,
            'alt="CardManager"',
        )
        self.assertIsNotNone(
            finders.find("img/branding/cardmanager/Logo-CardManager-CO-COLOR.png")
        )

    def test_administrative_copy_javascript_is_local_and_scoped_to_visible_values(self):
        source = (
            Path(__file__).resolve().parents[1] / "static" / "js" / "vault.js"
        ).read_text(encoding="utf-8")
        start = source.index('document.querySelectorAll("[data-copy-visible]")')
        end = source.index('const sidebar = document.querySelector("#app-sidebar")')
        handler = source[start:end]
        self.assertIn("button.dataset.copyVisible", handler)
        self.assertIn("navigator.clipboard.writeText(value)", handler)
        self.assertIn('document.execCommand("copy")', handler)
        self.assertIn("`${label} copiado`", handler)
        self.assertNotIn("fetch(", handler)
        self.assertNotIn("protectedMeta", handler)
        self.assertNotIn("data-field", handler)
        self.assertNotIn("pan", handler.lower())
        self.assertNotIn("expiry", handler.lower())
        self.assertNotIn("code", handler.lower())

    def test_favicon_reference_and_root_route_are_valid(self):
        html = self.render_shell(UserProfile.ADMIN)
        self.assertIn('/static/img/branding/favicon.ico', html)
        response = Client().get('/favicon.ico')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.url, '/static/img/branding/favicon.ico')

    def test_internal_event_codes_have_spanish_labels(self):
        template = Template("{% load vault_ui %}{{ value|alert_type_label }}")
        expected = {
            "SESSION_REPLACED": "Sesión reemplazada",
            "REPORT_EXPORT": "Exportación de informe",
            "LOGIN": "Inicio de sesión",
        }
        for value, label in expected.items():
            self.assertEqual(template.render(Context({"value": value})), label)

    def test_policy_form_does_not_modify_obsolete_security_fields(self):
        policy = PolicyConfiguration.objects.create(
            singleton=1,
            reauthentication_operations=["REVEAL_PAN", "COPY_PAN"],
            inactivity_login_days=44,
        )
        data = {
            "timezone_name": "America/Bogota",
            "weekday_start": "07:00", "weekday_end": "18:00", "saturday_start": "08:00", "saturday_end": "12:00",
            "saturday_enabled": "on", "outside_hours_behavior": "ALLOW_ALERT",
            "reason": "Ajuste de jornada autorizado",
        }
        form = PolicyConfigurationForm(data, instance=policy)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(saved.reauthentication_operations, ["REVEAL_PAN", "COPY_PAN"])
        self.assertEqual(saved.inactivity_login_days, 44)
