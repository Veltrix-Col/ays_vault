from types import SimpleNamespace

from django.forms.models import model_to_dict
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase

from .forms import AccessExceptionForm, NotificationRecipientForm, PolicyConfigurationForm
from .models import PolicyConfiguration, UserProfile


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

    def test_policy_form_uses_spanish_labels_and_visual_choices(self):
        form = PolicyConfigurationForm()
        self.assertEqual(form["timezone_name"].label, "Zona horaria")
        self.assertEqual(form["enabled"].label, "Activar esta política")
        self.assertEqual(form["reauthentication_operations"].field.widget.__class__.__name__, "CheckboxSelectMultiple")
        self.assertNotIn("textarea", str(form["reauthentication_operations"]).lower())

    def test_recipient_and_exception_lists_are_visual_choices(self):
        recipient = NotificationRecipientForm()
        exception = AccessExceptionForm()
        self.assertEqual(recipient["alert_types"].field.widget.__class__.__name__, "CheckboxSelectMultiple")
        self.assertContainsHTML(str(recipient["alert_types"]), "Posible uso paralelo de Excel")
        self.assertEqual(exception["operations"].field.widget.__class__.__name__, "CheckboxSelectMultiple")

    def assertContainsHTML(self, html, text):
        self.assertIn(text, html)

    def test_admin_sidebar_is_grouped_and_does_not_expose_vault(self):
        html = self.render_shell(UserProfile.ADMIN)
        self.assertIn("Configuración de Seguridad", html)
        self.assertIn("Correo y destinatarios", html)
        self.assertNotIn(">Bóveda<", html)

    def test_operational_sidebar_respects_role(self):
        html = self.render_shell(UserProfile.LEADER)
        self.assertIn(">Bóveda<", html)
        self.assertIn("Nueva tarjeta", html)
        self.assertNotIn("Correo y destinatarios", html)

    def test_shell_has_accessible_drawer_and_bounded_logo(self):
        html = self.render_shell(UserProfile.ADMIN)
        self.assertIn('aria-controls="app-sidebar"', html)
        self.assertIn('aria-expanded="false"', html)
        self.assertIn('class="brand-logo-image"', html)
        self.assertIn('class="sidebar-backdrop"', html)

    def test_policy_form_keeps_json_storage_without_raw_json_control(self):
        policy = PolicyConfiguration.objects.create(singleton=1)
        data = model_to_dict(policy)
        data.update({
            "weekday_start": "07:00", "weekday_end": "18:00", "saturday_start": "08:00", "saturday_end": "12:00",
            "reauthentication_operations": ["REVEAL_PAN", "COPY_PAN"], "reason": "Ajuste visual verificado",
        })
        form = PolicyConfigurationForm(data, instance=policy)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(saved.reauthentication_operations, ["REVEAL_PAN", "COPY_PAN"])
