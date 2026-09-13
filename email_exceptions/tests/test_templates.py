from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from email_exceptions.models import EmailException, EmailExceptionMessage, InboundEmail


class EmailExceptionsTemplateTests(TestCase):
    def setUp(self):
        email = InboundEmail.objects.create(source_mailbox="comunicaciones@segurosays.com", external_message_id="template-test-1", received_at="2026-09-12T10:32:00Z", from_name="Remitente sintético", from_email="pagos@bemsa.com.co", subject="Comprobante de pago BEMSA", body_text="Contenido sintético.")
        self.exception = EmailException.objects.create(primary_email=email, last_subject=email.subject, organization="BEMSA", family="CARTERA_ARRENDAMIENTO", event_type="COMPROBANTE_PAGO", exception_reason="Se recibió un comprobante de pago que requiere revisión.", rule_id="BEMSA_COMPROBANTE_PAGO_V1")
        EmailExceptionMessage.objects.create(exception=self.exception, email=email)

    def test_list_uses_operational_banco_layout_and_filters(self):
        response = self.client.get(reverse("email_exceptions:list"), {"q": "BEMSA", "status": "PENDING", "organization": "BEMSA"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/static/css/email-exceptions.css")
        self.assertContains(response, 'class="email-exceptions-page"')
        self.assertContains(response, "Banco de Herramientas")
        self.assertContains(response, "Operaciones")
        self.assertContains(response, f'href="{reverse("public_home")}"')
        self.assertContains(response, f'href="{reverse("area_home", args=["operaciones"])}"')
        self.assertNotContains(response, "CardManager")
        self.assertContains(response, "metric-grid four")
        self.assertContains(response, "email-exception-table")
        self.assertContains(response, "Correos para gestionar")
        self.assertContains(response, "Aplicar filtros")

    def test_detail_uses_module_stylesheet_and_operational_ui(self):
        response = self.client.get(reverse("email_exceptions:detail", args=[self.exception.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/static/css/email-exceptions.css")
        self.assertContains(response, 'class="email-exceptions-page"')
        self.assertContains(response, "Banco de Herramientas")
        self.assertContains(response, "Operaciones")
        self.assertContains(response, f'href="{reverse("area_home", args=["operaciones"])}"')
        self.assertContains(response, "Correo recibido")
        self.assertContains(response, "Clasificación")

    @override_settings(EMAIL_EXCEPTIONS_ZOHO_TASK_WRITE_ENABLED=True)
    def test_detail_uses_readable_timeline_and_collapsed_actions(self):
        response = self.client.get(reverse("email_exceptions:detail", args=[self.exception.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/static/css/email-exceptions.css")
        self.assertContains(response, "Correo recibido")
        self.assertContains(response, "Crear tarea en Zoho")
        self.assertContains(response, "<details class=\"email-action-disclosure\">", html=False)
        self.assertContains(response, "Información técnica")
        self.assertNotContains(response, "EMAIL_RECEIVED")

    def test_non_email_page_does_not_load_email_exceptions_stylesheet(self):
        response = self.client.get(reverse("public_home"))
        self.assertNotContains(response, "email-exceptions.css")

    def test_module_styles_are_scoped_to_a_light_operational_surface(self):
        stylesheet = (settings.BASE_DIR / "static" / "css" / "email-exceptions.css").read_text(encoding="utf-8")
        self.assertIn(".email-exceptions-page", stylesheet)
        self.assertIn("background: #fff", stylesheet)
        self.assertNotIn("body {", stylesheet)
        self.assertNotIn(":root", stylesheet)
        self.assertNotIn("linear-gradient", stylesheet)
