import smtplib
import json
from datetime import datetime, timedelta, timezone as datetime_timezone
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.core.management.base import SystemCheckError
from django.conf import settings
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from .crypto import encrypt
from .email_config import email_configuration_issues
from .models import AuditEvent, NotificationRecord, PaymentCard, SecureSession, SecurityAlert, UserProfile
from .notifications import (
    EMAIL_BRAND_CID,
    EmailDeliveryError,
    MicrosoftGraphEmailBackend,
    SMTPEmailBackend,
    get_backend,
    send_alert_notification,
)
from .security import audit, session_hash


PASSWORD = "EmailSMTP123!"
BASE_SETTINGS = {
    "DEBUG": True,
    "APP_ENV": "development",
    "FIELD_ENCRYPTION_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    "FIELD_FINGERPRINT_KEY": "email-smtp-tests",
    "DEFAULT_FROM_EMAIL": "alertas@example.invalid",
    "ALERT_EMAIL_FROM": "alertas@example.invalid",
    "EMAIL_TIMEOUT_SECONDS": 10,
    "EMAIL_MAX_RETRIES": 3,
    "EMAIL_CONFIGURATION_ERRORS": [],
}


class TestCommandEmailIsolationTests(SimpleTestCase):
    def test_manage_py_test_uses_only_local_email_backends(self):
        self.assertTrue(settings.RUNNING_TESTS)
        self.assertEqual(
            settings.EMAIL_BACKEND,
            "django.core.mail.backends.locmem.EmailBackend",
        )
        self.assertEqual(settings.ALERT_EMAIL_BACKEND, "console")


@override_settings(**BASE_SETTINGS)
class EmailConfigurationTests(SimpleTestCase):
    def codes(self):
        return {issue.code for issue in email_configuration_issues()}

    @override_settings(ALERT_EMAIL_BACKEND="console", EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend")
    def test_console_needs_no_credentials_in_development(self):
        self.assertEqual(self.codes(), set())

    @override_settings(
        ALERT_EMAIL_BACKEND="smtp",
        EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
        EMAIL_HOST="smtp.office365.com",
        EMAIL_PORT=587,
        EMAIL_USE_TLS=True,
        EMAIL_USE_SSL=False,
        EMAIL_HOST_USER="",
        EMAIL_HOST_PASSWORD="",
    )
    def test_smtp_requires_user_and_application_password(self):
        self.assertIn("vault.EEMAIL008", self.codes())

    @override_settings(ALERT_EMAIL_BACKEND="unknown")
    def test_unknown_backend_fails_safely(self):
        issues = email_configuration_issues()
        self.assertIn("vault.EEMAIL002", {item.code for item in issues})
        self.assertNotIn("secret", " ".join(item.message for item in issues).lower())
        with self.assertRaises(SystemCheckError):
            call_command("check")

    @override_settings(
        ALERT_EMAIL_BACKEND="smtp",
        EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
        EMAIL_HOST="smtp.office365.com",
        EMAIL_PORT=70000,
        EMAIL_USE_TLS=True,
        EMAIL_USE_SSL=True,
        EMAIL_HOST_USER="alertas@example.invalid",
        EMAIL_HOST_PASSWORD="fake-app-password",
    )
    def test_invalid_port_and_simultaneous_tls_ssl_are_rejected(self):
        issues = email_configuration_issues()
        self.assertTrue({"vault.EEMAIL009", "vault.EEMAIL010"}.issubset({item.code for item in issues}))
        self.assertNotIn("fake-app-password", " ".join(item.message for item in issues))

    @override_settings(ALERT_EMAIL_BACKEND="console", EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend", APP_ENV="production", DEBUG=False)
    def test_production_rejects_console(self):
        self.assertIn("vault.EEMAIL006", self.codes())

    @override_settings(
        ALERT_EMAIL_BACKEND="graph",
        MS_GRAPH_TENANT_ID="tenant",
        MS_GRAPH_CLIENT_ID="client",
        MS_GRAPH_CLIENT_SECRET="fake-secret",
        MS_GRAPH_SENDER="alertas@example.invalid",
    )
    def test_graph_remains_available(self):
        self.assertIsInstance(get_backend(), MicrosoftGraphEmailBackend)
        self.assertNotIn("vault.EEMAIL013", self.codes())

    @override_settings(
        ALERT_EMAIL_BACKEND="graph",
        MS_GRAPH_TENANT_ID="tenant",
        MS_GRAPH_CLIENT_ID="client",
        MS_GRAPH_CLIENT_SECRET="fake-secret",
        MS_GRAPH_SENDER="alertas@example.invalid",
    )
    @patch("vault.notifications.urllib.request.urlopen")
    @patch("msal.ConfidentialClientApplication")
    def test_graph_backend_still_uses_oauth_and_configured_timeout(self, app_class, urlopen):
        app_class.return_value.acquire_token_for_client.return_value = {"access_token": "fake-access-token"}
        response = Mock(status=202, headers={"request-id": "request-1"})
        urlopen.return_value.__enter__.return_value = response
        external_id = MicrosoftGraphEmailBackend().send("Asunto", "Texto", "<p>Texto</p>", "admin@example.invalid")
        self.assertEqual(external_id, "request-1")
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 10)

    @override_settings(
        ALERT_EMAIL_BACKEND="graph",
        MS_GRAPH_TENANT_ID="tenant",
        MS_GRAPH_CLIENT_ID="client",
        MS_GRAPH_CLIENT_SECRET="fake-secret",
        MS_GRAPH_SENDER="alertas@example.invalid",
    )
    @patch("vault.notifications.urllib.request.urlopen")
    @patch("msal.ConfidentialClientApplication")
    def test_graph_embeds_cardmanager_logo_only_when_html_references_it(
        self, app_class, urlopen
    ):
        app_class.return_value.acquire_token_for_client.return_value = {
            "access_token": "fake-access-token"
        }
        urlopen.return_value.__enter__.return_value = Mock(
            status=202, headers={"request-id": "request-2"}
        )
        MicrosoftGraphEmailBackend().send(
            "Asunto",
            "Texto",
            f'<img src="cid:{EMAIL_BRAND_CID}" alt="CardManager">',
            "admin@example.invalid",
        )
        payload = json.loads(urlopen.call_args.args[0].data.decode())
        attachment = payload["message"]["attachments"][0]
        self.assertEqual(attachment["contentId"], EMAIL_BRAND_CID)
        self.assertTrue(attachment["isInline"])
        self.assertEqual(attachment["contentType"], "image/png")
        self.assertTrue(attachment["contentBytes"])


@override_settings(
    **BASE_SETTINGS,
    ALERT_EMAIL_BACKEND="smtp",
    EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
    EMAIL_HOST="smtp.office365.com",
    EMAIL_PORT=587,
    EMAIL_USE_TLS=True,
    EMAIL_USE_SSL=False,
    EMAIL_HOST_USER="alertas@example.invalid",
    EMAIL_HOST_PASSWORD="fake-application-password",
)
class SMTPDeliveryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("smtp.user", password=PASSWORD)
        cls.user.vault_profile.role = UserProfile.LEADER
        cls.user.vault_profile.active = cls.user.vault_profile.mfa_enabled = True
        cls.user.vault_profile.save()
        cls.card = PaymentCard(company_name="Empresa S.A.S.", client_name="Cliente", cardholder_name="Titular", brand="VISA", purpose="Prueba", created_by=cls.user)
        cls.card.set_pan("4111111111111111")
        cls.card.set_expiry("12/29")
        cls.card.set_code("CODIGO-PROTEGIDO-SMTP")
        cls.card.save()

    def make_alert(self, description="Evento seguro"):
        event = audit(None, "ACCESS", user=self.user, card=self.card)
        return SecurityAlert.objects.create(event=event, alert_type="CRITICAL_ALERT", severity="CRITICAL", affected_user=self.user, description=description)

    @patch("vault.notifications.get_connection")
    def test_smtp_uses_sender_tls_timeout_and_configured_recipient(self, get_connection_mock):
        connection = Mock()
        connection.send_messages.return_value = 1
        get_connection_mock.return_value = connection
        record = send_alert_notification(self.make_alert(), "admin@example.invalid")
        self.assertEqual(record.result, NotificationRecord.SENT)
        self.assertEqual(record.backend, "smtp")
        self.assertTrue(AuditEvent.objects.filter(action="EMAIL_SENT").exists())
        kwargs = get_connection_mock.call_args.kwargs
        self.assertEqual(kwargs["host"], "smtp.office365.com")
        self.assertEqual(kwargs["port"], 587)
        self.assertEqual(kwargs["username"], "alertas@example.invalid")
        self.assertEqual(kwargs["password"], "fake-application-password")
        self.assertTrue(kwargs["use_tls"])
        self.assertFalse(kwargs["use_ssl"])
        self.assertEqual(kwargs["timeout"], 10)
        message = connection.send_messages.call_args.args[0][0]
        self.assertEqual(message.from_email, "alertas@example.invalid")
        self.assertEqual(message.to, ["admin@example.invalid"])
        html_body = message.alternatives[0].content
        self.assertIn(f"cid:{EMAIL_BRAND_CID}", html_body)
        inline_images = [
            attachment
            for attachment in message.attachments
            if attachment.get("Content-ID") == f"<{EMAIL_BRAND_CID}>"
        ]
        self.assertEqual(len(inline_images), 1)
        self.assertEqual(inline_images[0].get_content_type(), "image/png")

    def test_transient_failure_is_retried_only_to_configured_limit(self):
        backend = Mock()
        backend.name = "smtp"
        backend.send.side_effect = [EmailDeliveryError("SMTP_CONNECTION_ERROR", retryable=True), "external-id"]
        with patch("vault.notifications.get_backend", return_value=backend):
            record = send_alert_notification(self.make_alert(), "admin@example.invalid")
        self.assertEqual(record.result, NotificationRecord.SENT)
        self.assertEqual(record.attempts, 2)
        self.assertEqual(backend.send.call_count, 2)

    def test_transient_failure_stops_at_maximum_attempts(self):
        backend = Mock()
        backend.name = "smtp"
        backend.send.side_effect = EmailDeliveryError("SMTP_CONNECTION_ERROR", retryable=True)
        with patch("vault.notifications.get_backend", return_value=backend):
            record = send_alert_notification(self.make_alert(), "admin@example.invalid")
        self.assertEqual(record.result, NotificationRecord.FAILED)
        self.assertEqual(record.attempts, 3)
        self.assertIsNone(record.next_attempt_at)
        self.assertEqual(backend.send.call_count, 3)

    def test_authentication_failure_is_not_retried(self):
        backend = Mock()
        backend.name = "smtp"
        backend.send.side_effect = EmailDeliveryError("SMTP_AUTHENTICATION_FAILED", retryable=False)
        with patch("vault.notifications.get_backend", return_value=backend):
            record = send_alert_notification(self.make_alert(), "admin@example.invalid")
        self.assertEqual(record.result, NotificationRecord.FAILED)
        self.assertEqual(record.attempts, 1)
        self.assertEqual(record.safe_error_code, "SMTP_AUTHENTICATION_FAILED")
        backend.send.assert_called_once()
        self.assertTrue(AuditEvent.objects.filter(action="EMAIL_FAILED").exists())

    def test_raw_smtp_authentication_error_becomes_safe_code(self):
        connection = Mock()
        connection.send_messages.side_effect = smtplib.SMTPAuthenticationError(535, b"authentication failed")
        with patch("vault.notifications.get_connection", return_value=connection):
            with self.assertRaises(EmailDeliveryError) as raised:
                SMTPEmailBackend().send("Asunto", "Texto", "<p>Texto</p>", "admin@example.invalid")
        self.assertEqual(raised.exception.safe_code, "SMTP_AUTHENTICATION_FAILED")
        self.assertFalse(raised.exception.retryable)

    @override_settings(EMAIL_HOST_PASSWORD="never-log-this-password")
    def test_credentials_and_protected_data_are_redacted_from_mail_and_logs(self):
        alert = self.make_alert("CODIGO-PROTEGIDO-SMTP 4111111111111111 12/29 123456 never-log-this-password")
        backend = Mock()
        backend.name = "smtp"
        backend.send.side_effect = RuntimeError("never-log-this-password")
        with self.assertLogs("vault.notifications", level="WARNING") as captured:
            with patch("vault.notifications.get_backend", return_value=backend):
                record = send_alert_notification(alert, "admin@example.invalid")
        sent_arguments = " ".join(str(value) for value in backend.send.call_args.args)
        combined_logs = " ".join(captured.output)
        for protected in ("CODIGO-PROTEGIDO-SMTP", "4111111111111111", "12/29", "123456", "never-log-this-password"):
            self.assertNotIn(protected, sent_arguments)
            self.assertNotIn(protected, combined_logs)
        self.assertEqual(record.safe_error_code, "EMAIL_DELIVERY_ERROR")

    def test_alert_email_preserves_full_colombia_date_but_redacts_card_data(self):
        event = audit(
            None,
            "LOGIN",
            user=self.user,
            card=self.card,
            reason="Acceso extraordinario",
        )
        event_time = datetime(2026, 7, 30, 0, 48, 28, tzinfo=datetime_timezone.utc)
        AuditEvent.objects.filter(pk=event.pk).update(created_at=event_time)
        event.refresh_from_db()
        alert, _ = SecurityAlert.objects.get_or_create(
            event=event,
            defaults={
                "alert_type": "OUTSIDE_HOURS",
                "severity": "HIGH",
                "affected_user": self.user,
                "description": "Acceso extraordinario",
            },
        )
        alert.recommendation = (
            "Revisar 4111111111111111, vencimiento 12/29, "
            "código CODIGO-PROTEGIDO-SMTP y referencia 123456."
        )
        alert.save(update_fields=["recommendation"])
        SecurityAlert.objects.filter(pk=alert.pk).update(created_at=event_time)
        alert.refresh_from_db()
        backend = Mock()
        backend.name = "smtp"
        backend.send.return_value = "accepted"
        with patch("vault.notifications.get_backend", return_value=backend):
            record = send_alert_notification(alert, "admin@example.invalid")

        self.assertEqual(record.result, NotificationRecord.SENT)
        subject, text_body, html_body, recipient = backend.send.call_args.args
        self.assertEqual(recipient, "admin@example.invalid")
        self.assertIn("Fecha: 29/07/2026", text_body)
        self.assertIn("Hora de Colombia: 19:48:28", text_body)
        self.assertIn("29/07/2026 19:48:28 (Colombia)", html_body)
        for protected in (
            "4111111111111111",
            "12/29",
            "CODIGO-PROTEGIDO-SMTP",
            "123456",
        ):
            self.assertNotIn(protected, subject + text_body + html_body)

    def test_expiry_redaction_does_not_mistake_complete_dates_for_expiry(self):
        from .notifications import _redact_email_content

        for timestamp in (
            "29/07/2026 19:48:28",
            "01/12/2026 07:05:09",
            "31/01/2030 23:59:59",
        ):
            self.assertIn(timestamp, _redact_email_content(timestamp))
        for protected in ("12/29", "01/2030"):
            self.assertNotIn(protected, _redact_email_content(protected))


@override_settings(
    **BASE_SETTINGS,
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    ALERT_EMAIL_BACKEND="console",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class EmailTestViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin = User.objects.create_user("email.admin", password=PASSWORD)
        cls.leader = User.objects.create_user("email.leader", password=PASSWORD)
        cls.analyst = User.objects.create_user("email.analyst", password=PASSWORD)
        for user, role in ((cls.admin, UserProfile.ADMIN), (cls.leader, UserProfile.LEADER), (cls.analyst, UserProfile.ANALYST)):
            user.vault_profile.role = role
            user.vault_profile.active = user.vault_profile.mfa_enabled = True
            user.vault_profile.save()
            TOTPDevice.objects.create(user=user, confirmed=True)

    def authenticated_client(self, user, enforce_csrf=False):
        client = Client(enforce_csrf_checks=enforce_csrf, REMOTE_ADDR="10.0.0.8", HTTP_USER_AGENT="Email Test Browser")
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
            user_agent="Email Test Browser",
            status=SecureSession.ACTIVE,
            mfa_completed=True,
            mfa_completed_at=now,
        )
        return client

    def test_only_admin_can_open_email_configuration_and_local_backend_is_not_success(self):
        for user in (self.leader, self.analyst):
            client = self.authenticated_client(user)
            self.assertEqual(client.get(reverse("vault:recipients")).status_code, 403)
            self.assertEqual(client.post(reverse("vault:email_test"), {"recipient": "admin@example.invalid"}).status_code, 403)
        client = self.authenticated_client(self.admin)
        page = client.get(reverse("vault:recipients"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Enviar correo de prueba")
        self.assertContains(page, "Correo electrónico destino")
        self.assertNotContains(page, "<details")
        alerts_before = SecurityAlert.objects.count()
        response = client.post(
            reverse("vault:email_test"),
            {
                "recipient": "admin@example.invalid",
                "operation_id": "00000000-0000-0000-0000-000000000001",
            },
            follow=True,
        )
        self.assertContains(response, "El backend actual no realiza envíos externos.")
        self.assertEqual(len(mail.outbox), 0)
        record = NotificationRecord.objects.get(notification_type="EMAIL_TEST")
        self.assertEqual(record.result, NotificationRecord.FAILED)
        self.assertEqual(record.safe_error_code, "EMAIL_BACKEND_LOCAL")
        self.assertTrue(AuditEvent.objects.filter(user=self.admin, action="EMAIL_FAILED").exists())
        self.assertEqual(SecurityAlert.objects.count(), alerts_before)

    def test_email_test_requires_post_and_csrf(self):
        client = self.authenticated_client(self.admin, enforce_csrf=True)
        self.assertEqual(client.get(reverse("vault:email_test")).status_code, 405)
        self.assertEqual(client.post(reverse("vault:email_test"), {"recipient": "admin@example.invalid"}).status_code, 403)
        client.get(reverse("vault:recipients"))
        token = client.cookies["csrftoken"].value
        response = client.post(
            reverse("vault:email_test"),
            {"recipient": "admin@example.invalid", "csrfmiddlewaretoken": token},
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 302)

    @override_settings(
        ALERT_EMAIL_BACKEND="smtp",
        EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
    )
    def test_external_success_uses_exact_form_recipient_and_safe_content(self):
        client = self.authenticated_client(self.admin)
        alerts_before = SecurityAlert.objects.count()
        backend = Mock(name="smtp-backend")
        backend.name = "smtp"
        backend.send.return_value = "provider-message-id"
        with patch("vault.notifications.get_backend", return_value=backend):
            response = client.post(
                reverse("vault:email_test"),
                {
                    "recipient": "destino.exacto@example.invalid",
                    "operation_id": "00000000-0000-0000-0000-000000000002",
                },
                follow=True,
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Correo de prueba enviado correctamente a destino.exacto@example.invalid.",
        )
        backend.send.assert_called_once()
        subject, text_body, html_body, recipient = backend.send.call_args.args
        self.assertEqual(recipient, "destino.exacto@example.invalid")
        self.assertEqual(subject, "[PRUEBA] CardManager | Envío de prueba")
        rendered = subject + text_body + html_body
        self.assertIn("correo de prueba enviado desde CardManager", rendered)
        self.assertIn("No corresponde a una alerta real", rendered)
        self.assertNotIn("Escenario", rendered)
        self.assertNotRegex(rendered, r"(?<!\d)\d{13,19}(?!\d)")
        self.assertEqual(SecurityAlert.objects.count(), alerts_before)
        event = AuditEvent.objects.filter(user=self.admin, action="EMAIL_SENT").latest("sequence")
        self.assertNotIn("test_scenario", event.metadata)
        record = NotificationRecord.objects.get(notification_type="EMAIL_TEST")
        self.assertEqual(record.result, NotificationRecord.SENT)
        self.assertEqual(record.backend, "smtp")
        self.assertEqual(record.external_id, "provider-message-id")

    @override_settings(
        ALERT_EMAIL_BACKEND="smtp",
        EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
        ALERT_EMAIL="otro@example.invalid",
        ALERT_EMAIL_ADMIN="configurado@example.invalid",
    )
    @patch("vault.notifications.get_connection")
    def test_smtp_zero_is_failed_and_never_uses_configured_recipients(self, connection_factory):
        connection = Mock()
        connection.send_messages.return_value = 0
        connection_factory.return_value = connection
        client = self.authenticated_client(self.admin)
        response = client.post(
            reverse("vault:email_test"),
            {
                "recipient": "destino.formulario@example.invalid",
                "operation_id": "00000000-0000-0000-0000-000000000003",
            },
            follow=True,
        )
        self.assertContains(response, "No fue posible enviar el correo de prueba.")
        record = NotificationRecord.objects.get(notification_type="EMAIL_TEST")
        self.assertEqual(record.result, NotificationRecord.FAILED)
        self.assertEqual(record.safe_error_code, "SMTP_EMAIL_NOT_SENT")
        for call in connection.send_messages.call_args_list:
            message = call.args[0][0]
            self.assertEqual(message.to, ["destino.formulario@example.invalid"])
        self.assertTrue(AuditEvent.objects.filter(user=self.admin, action="EMAIL_FAILED").exists())

    @override_settings(
        ALERT_EMAIL_BACKEND="smtp",
        EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
    )
    def test_smtp_exception_is_failed_with_safe_user_message(self):
        client = self.authenticated_client(self.admin)
        backend = Mock()
        backend.name = "smtp"
        backend.send.side_effect = EmailDeliveryError("SMTP_AUTHENTICATION_FAILED")
        with patch("vault.notifications.get_backend", return_value=backend):
            response = client.post(
                reverse("vault:email_test"),
                {
                    "recipient": "admin@example.invalid",
                    "operation_id": "00000000-0000-0000-0000-000000000004",
                },
                follow=True,
            )
        self.assertContains(response, "No fue posible enviar el correo de prueba.")
        self.assertNotContains(response, "SMTP_AUTHENTICATION_FAILED")
        self.assertEqual(
            NotificationRecord.objects.get(notification_type="EMAIL_TEST").safe_error_code,
            "SMTP_AUTHENTICATION_FAILED",
        )

    @override_settings(
        ALERT_EMAIL_BACKEND="console",
        EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend",
    )
    def test_console_backend_explains_that_no_external_delivery_occurred(self):
        response = self.authenticated_client(self.admin).post(
            reverse("vault:email_test"),
            {
                "recipient": "admin@example.invalid",
                "operation_id": "00000000-0000-0000-0000-000000000005",
            },
            follow=True,
        )
        self.assertContains(
            response,
            "El backend de correo está configurado en modo consola. "
            "No se realizó un envío externo.",
        )

    def test_invalid_recipient_has_specific_message(self):
        response = self.authenticated_client(self.admin).post(
            reverse("vault:email_test"),
            {"recipient": "no-es-un-correo"},
            follow=True,
        )
        self.assertContains(response, "El correo electrónico ingresado no es válido.")
        self.assertFalse(NotificationRecord.objects.exists())

    @override_settings(
        ALERT_EMAIL_BACKEND="smtp",
        EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
    )
    def test_repeated_operation_id_is_idempotent(self):
        client = self.authenticated_client(self.admin)
        backend = Mock()
        backend.name = "smtp"
        backend.send.return_value = "accepted"
        payload = {
            "recipient": "admin@example.invalid",
            "operation_id": "00000000-0000-0000-0000-000000000006",
        }
        with patch("vault.notifications.get_backend", return_value=backend):
            client.post(reverse("vault:email_test"), payload)
            client.post(reverse("vault:email_test"), payload)
        backend.send.assert_called_once()
        self.assertEqual(NotificationRecord.objects.filter(notification_type="EMAIL_TEST").count(), 1)
