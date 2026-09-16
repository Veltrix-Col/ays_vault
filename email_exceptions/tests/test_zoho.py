from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.utils import timezone

from email_exceptions.models import EmailException, InboundEmail, ZohoTaskCreation
from email_exceptions.zoho import create_exception_task
from cotizacion_colectivos.services.task_publisher import PRODUCTION_WRITE_CONFIRMATION


class EmailExceptionZohoTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="operator", password="safe-password-123")
        email = InboundEmail.objects.create(
            source_mailbox="comunicaciones@segurosays.com",
            external_message_id="message-1", received_at=timezone.now(),
            subject="Comprobante de pago", body_text="Revisar pago.",
        )
        self.exception = EmailException.objects.create(
            primary_email=email, last_subject=email.subject,
            organization="BEMSA", family="CARTERA_ARRENDAMIENTO",
            event_type="COMPROBANTE_PAGO", exception_reason="Revisar pago.",
            status=EmailException.PENDING,
        )

    @override_settings(
        EMAIL_EXCEPTIONS_ENABLED=True,
        EMAIL_EXCEPTIONS_INBOUND_ENABLED=True,
        EMAIL_EXCEPTIONS_INBOUND_TOKEN="token",
        EMAIL_EXCEPTIONS_ZOHO_TASK_WRITE_ENABLED=True,
        ZOHO_ACTIVE_PROFILE="sandbox",
        COLECTIVOS_TASK_PUBLISH_ENABLED=True,
        ZOHO_SANDBOX_WRITE_ENABLED=True,
    )
    @patch("email_exceptions.zoho.get_task_publisher")
    def test_sandbox_creation_persists_task_and_manages_exception(self, get_publisher):
        publisher = SimpleNamespace(publish_email_exception=lambda record: {"record_id": "1234567890"})
        get_publisher.return_value = publisher
        task = create_exception_task(
            exception=self.exception, responsible="Ana Operaciones",
            subject="BEMSA - Revisar comprobante", due_date=date(2026, 9, 20),
            description="Detalle seguro", actor=self.user,
        )
        self.assertEqual(task.task_id, "1234567890")
        self.assertIn(task.task_id, task.task_url)
        self.exception.refresh_from_db()
        self.assertEqual(self.exception.status, EmailException.MANAGED)
        self.assertEqual(task.technical_status, "CREATED")
        self.assertEqual(task.subject, "BEMSA - Revisar comprobante")

    @override_settings(EMAIL_EXCEPTIONS_ZOHO_TASK_WRITE_ENABLED=False)
    def test_module_guard_blocks_creation(self):
        with self.assertRaisesMessage(Exception, "deshabilitada"):
            create_exception_task(
                exception=self.exception, responsible="Ana",
                subject="Subject", due_date=None, description="Description", actor=self.user,
            )

    @override_settings(
        EMAIL_EXCEPTIONS_ZOHO_TASK_WRITE_ENABLED=False,
        ZOHO_ACTIVE_PROFILE="production",
        ZOHO_PRODUCTION_ENABLED=True,
        ZOHO_PRODUCTION_WRITE_ENABLED=True,
    )
    def test_production_global_write_does_not_enable_email_exceptions(self):
        with self.assertRaisesMessage(Exception, "deshabilitada"):
            create_exception_task(
                exception=self.exception, responsible="Ana",
                subject="Subject", due_date=None, description="Description", actor=self.user,
            )

    @override_settings(
        EMAIL_EXCEPTIONS_ZOHO_TASK_WRITE_ENABLED=True,
        ZOHO_ACTIVE_PROFILE="production",
        ZOHO_PRODUCTION_ENABLED=True,
        ZOHO_PRODUCTION_WRITE_ENABLED=True,
        COLECTIVOS_TASK_PUBLISH_ENABLED=False,
        COLECTIVOS_PRODUCTION_TASK_WRITE_CONFIRMATION="incorrecta",
    )
    @patch("cotizacion_colectivos.services.task_publisher.get_zoho")
    def test_production_email_write_rejects_invalid_confirmation(self, get_zoho):
        with self.assertRaises(Exception):
            create_exception_task(
                exception=self.exception, responsible="Ana",
                subject="Subject", due_date=None, description="Description", actor=self.user,
            )
        get_zoho.assert_not_called()

    @override_settings(
        EMAIL_EXCEPTIONS_ZOHO_TASK_WRITE_ENABLED=True,
        ZOHO_ACTIVE_PROFILE="production",
        ZOHO_PRODUCTION_ENABLED=True,
        ZOHO_PRODUCTION_WRITE_ENABLED=True,
        COLECTIVOS_TASK_PUBLISH_ENABLED=False,
        COLECTIVOS_PRODUCTION_TASK_WRITE_CONFIRMATION=PRODUCTION_WRITE_CONFIRMATION,
    )
    @patch("cotizacion_colectivos.services.task_publisher.get_zoho")
    def test_production_email_write_uses_shared_publisher_with_mock(self, get_zoho):
        item = SimpleNamespace(record_id="700000000000000099", succeeded=True, code="SUCCESS")
        get_zoho.return_value = SimpleNamespace(records=SimpleNamespace(create=Mock(return_value=SimpleNamespace(records=(item,)))))
        task = create_exception_task(
            exception=self.exception, responsible="Ana",
            subject="Subject", due_date=None, description="Description", actor=self.user,
        )
        self.assertEqual(task.task_id, "700000000000000099")
        get_zoho.assert_called_once_with(profile="production")

    @override_settings(
        EMAIL_EXCEPTIONS_ENABLED=True,
        EMAIL_EXCEPTIONS_INBOUND_ENABLED=True,
        EMAIL_EXCEPTIONS_INBOUND_TOKEN="token",
        EMAIL_EXCEPTIONS_ZOHO_TASK_WRITE_ENABLED=True,
        ZOHO_ACTIVE_PROFILE="production",
        ZOHO_PRODUCTION_ENABLED=True,
        ZOHO_PRODUCTION_WRITE_ENABLED=True,
        COLECTIVOS_TASK_PUBLISH_ENABLED=True,
        COLECTIVOS_PRODUCTION_TASK_WRITE_CONFIRMATION=PRODUCTION_WRITE_CONFIRMATION,
    )
    @patch("email_exceptions.zoho.get_task_publisher")
    @patch("email_exceptions.views.ingest_payload")
    def test_inbound_never_calls_task_publisher(self, ingest, get_publisher):
        ingest.return_value = (self.exception.primary_email, self.exception, True)
        response = self.client.post(
            "/operaciones/excepciones-correo/api/inbound/",
            data='{"source_mailbox":"comunicaciones@segurosays.com","message_id":"m2","subject":"x"}',
            content_type="application/json",
            HTTP_X_EMAIL_EXCEPTIONS_TOKEN="token",
        )
        self.assertEqual(response.status_code, 201)
        get_publisher.assert_not_called()

    @override_settings(DEBUG=True, RUNNING_TESTS=True, TOOLS_ACCESS_MODE="local_public")
    @patch("email_exceptions.views.create_exception_task")
    def test_unauthorized_user_cannot_create_task(self, create_task):
        response = self.client.post("/operaciones/excepciones-correo/1/create-task/", data={})
        self.assertEqual(response.status_code, 403)
        create_task.assert_not_called()

    @override_settings(DEBUG=True, RUNNING_TESTS=True, TOOLS_ACCESS_MODE="local_public")
    @patch("email_exceptions.views.create_exception_task")
    @patch("email_exceptions.views.responsible_options", return_value=[SimpleNamespace(actual_value="owner", display_value="Owner")])
    @patch("email_exceptions.views.task_creation_available", return_value=True)
    def test_authorized_operator_can_submit_task_action(self, task_available, responsible_options, create_task):
        self.user.user_permissions.add(Permission.objects.get(
            content_type__app_label="email_exceptions",
            codename="operate_email_exceptions",
        ))
        self.client.force_login(self.user)
        response = self.client.post(
            "/operaciones/excepciones-correo/1/create-task/",
            {"responsible": "owner", "subject": "Subject", "description": "Description"},
        )
        self.assertEqual(response.status_code, 302)
        create_task.assert_called_once()

    @override_settings(DEBUG=True, RUNNING_TESTS=True, TOOLS_ACCESS_MODE="local_public")
    @patch("email_exceptions.views.create_exception_task")
    def test_get_never_produces_write(self, create_task):
        self.user.user_permissions.add(Permission.objects.get(
            content_type__app_label="email_exceptions",
            codename="view_email_exceptions_operational",
        ))
        self.client.force_login(self.user)
        response = self.client.get("/operaciones/excepciones-correo/1/")
        self.assertEqual(response.status_code, 200)
        create_task.assert_not_called()
