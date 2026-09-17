from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.utils import timezone
from django.core.exceptions import ValidationError

from email_exceptions.models import CaseActivity, CaseMessage, EmailException, ExceptionCase, InboundEmail, ZohoTaskCreation
from email_exceptions.forms import CreateCaseTaskForm
from email_exceptions.zoho import area_options, build_case_task_observations, build_case_task_subject, create_case_task, create_exception_task, responsible_options
from cotizacion_colectivos.services.task_publisher import PRODUCTION_WRITE_CONFIRMATION, TaskPublicationUncertain


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

    def case_for_task(self):
        now = timezone.now()
        case = ExceptionCase.objects.create(case_key="claim:TASK-1", organization="BEMSA", family="CARTERA", event_type="PAGO", opened_at=now, last_activity_at=now)
        CaseMessage.objects.create(case=case, email=self.exception.primary_email, role=CaseMessage.Role.OPENING, correlation_method=CaseMessage.CorrelationMethod.NEW_CASE, correlation_confidence=CaseMessage.CorrelationConfidence.LOW, correlation_reason="new")
        self.exception.case = case
        self.exception.save(update_fields=("case",))
        return case

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


class CaseLevelZohoTaskTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="case-operator", password="safe-password-123")
        now = timezone.now()
        self.email = InboundEmail.objects.create(source_mailbox="comunicaciones@segurosays.com", external_message_id="case-task-1", received_at=now, subject="Revisar comprobante")
        self.case = ExceptionCase.objects.create(case_key="claim:CASE-TASK", organization="BEMSA", family="CARTERA", event_type="COMPROBANTE_PAGO", opened_at=now, last_activity_at=now, next_action="Solicitar soporte")
        CaseMessage.objects.create(case=self.case, email=self.email, role=CaseMessage.Role.OPENING, correlation_method=CaseMessage.CorrelationMethod.NEW_CASE, correlation_confidence=CaseMessage.CorrelationConfidence.LOW, correlation_reason="new")
        self.exception = EmailException.objects.create(primary_email=self.email, case=self.case, last_subject=self.email.subject, exception_reason="Validar comprobante", status=EmailException.PENDING)

    def settings(self):
        return dict(EMAIL_EXCEPTIONS_ZOHO_TASK_WRITE_ENABLED=True, ZOHO_ACTIVE_PROFILE="sandbox", ZOHO_SANDBOX_WRITE_ENABLED=True, COLECTIVOS_TASK_PUBLISH_ENABLED=True)

    @patch("email_exceptions.zoho.cached_metadata_fields")
    @patch("email_exceptions.zoho.colectivos_zoho")
    def test_area_options_accepts_dict_picklist_values(self, facade, metadata):
        metadata.return_value = (SimpleNamespace(api_name="rea", pick_list_values=(
            {"actual_value": "Autos", "display_value": "Autos"},
            {"actual_value": "Vida", "display_value": "Vida"},
            {"actual_value": "-None-", "display_value": "-None-"},
            {"actual_value": "", "display_value": ""},
            {"actual_value": None, "display_value": None},
        )),)
        self.assertEqual(area_options(), (("Autos", "Autos"), ("Vida", "Vida")))

    @patch("email_exceptions.zoho.cached_metadata_fields")
    @patch("email_exceptions.zoho.colectivos_zoho")
    def test_area_options_accepts_object_values_and_preserves_value_label(self, facade, metadata):
        metadata.return_value = (SimpleNamespace(api_name="rea", pick_list_values=(
            SimpleNamespace(actual_value="AUTO", display_value="Automóviles"),
        )),)
        self.assertEqual(area_options(), (("AUTO", "Automóviles"),))

    @patch("email_exceptions.zoho.cached_metadata_fields", return_value=())
    @patch("email_exceptions.zoho.colectivos_zoho")
    def test_area_options_fails_closed_without_field(self, facade, metadata):
        with self.assertRaises(ValidationError):
            area_options()

    @patch("email_exceptions.zoho.cached_metadata_fields")
    @patch("email_exceptions.zoho.colectivos_zoho")
    def test_area_options_fails_closed_without_usable_values(self, facade, metadata):
        metadata.return_value = (SimpleNamespace(api_name="rea", pick_list_values=(
            {"actual_value": "-None-", "display_value": "-None-"},
        )),)
        with self.assertRaises(ValidationError):
            area_options()

    @override_settings(EMAIL_EXCEPTIONS_ZOHO_TASK_WRITE_ENABLED=True, ZOHO_ACTIVE_PROFILE="sandbox", ZOHO_SANDBOX_WRITE_ENABLED=True, COLECTIVOS_TASK_PUBLISH_ENABLED=True)
    @patch("email_exceptions.zoho.task_creation_available", return_value=True)
    @patch("email_exceptions.zoho.get_task_publisher")
    def test_case_task_uses_minimal_payload_and_is_idempotent(self, get_publisher, available):
        publisher = Mock()
        publisher.publish_email_exception.return_value = {"record_id": "1234567890"}
        get_publisher.return_value = publisher
        task = create_case_task(case=self.case, responsible="Ana", area="Operaciones", actor=self.user)
        again = create_case_task(case=self.case, responsible="Ana", area="Operaciones", actor=self.user)
        payload = publisher.publish_email_exception.call_args.args[0]
        self.assertEqual(set(payload), {"Subject", "Responsable", "rea", "Caso_de_excepci_n", "Observaciones"})
        self.assertEqual(payload["rea"], "Operaciones")
        self.assertEqual(task.pk, again.pk)
        self.assertEqual(ZohoTaskCreation.objects.filter(case=self.case).count(), 1)
        self.assertEqual(task.technical_status, "CREATED")
        self.assertEqual(task.area, "Operaciones")
        self.assertTrue(task.fingerprint)
        self.assertEqual(CaseActivity.objects.filter(case=self.case, event_type=CaseActivity.EventType.TASK_CREATED).count(), 1)
        requested = CaseActivity.objects.get(case=self.case, event_type=CaseActivity.EventType.TASK_CREATE_REQUESTED)
        self.assertEqual(requested.metadata["area"], "Operaciones")
        self.assertNotIn("ramo", requested.metadata)

    def test_case_task_form_uses_area_and_not_ramo(self):
        form = CreateCaseTaskForm()
        self.assertEqual(set(form.fields), {"responsible", "area"})
        self.assertTrue(form.fields["responsible"].required)
        self.assertTrue(form.fields["area"].required)

    @override_settings(EMAIL_EXCEPTIONS_ZOHO_TASK_WRITE_ENABLED=True, ZOHO_ACTIVE_PROFILE="sandbox", ZOHO_SANDBOX_WRITE_ENABLED=True, COLECTIVOS_TASK_PUBLISH_ENABLED=True)
    @patch("email_exceptions.zoho.task_creation_available", return_value=True)
    @patch("email_exceptions.zoho.get_task_publisher")
    def test_uncertain_result_requires_reconciliation_and_does_not_retry(self, get_publisher, available):
        publisher = Mock()
        publisher.publish_email_exception.side_effect = TaskPublicationUncertain("uncertain")
        get_publisher.return_value = publisher
        task = create_case_task(case=self.case, responsible="Ana", area="Operaciones", actor=self.user)
        create_case_task(case=self.case, responsible="Ana", area="Operaciones", actor=self.user)
        task.refresh_from_db()
        self.assertEqual(task.technical_status, "RECONCILE")
        publisher.publish_email_exception.assert_called_once()
        self.assertTrue(CaseActivity.objects.filter(case=self.case, event_type=CaseActivity.EventType.TASK_RECONCILE_REQUIRED).exists())

    def test_builders_are_plain_text_and_bounded(self):
        self.email.subject = "<script>alert(1)</script>" + "x" * 300
        self.email.save(update_fields=("subject",))
        self.assertLessEqual(len(build_case_task_subject(self.case)), 255)
        observations = build_case_task_observations(self.case)
        self.assertLessEqual(len(observations), 2000)
        self.assertNotIn("<script>", observations)

    @override_settings(DEBUG=True, RUNNING_TESTS=True, TOOLS_ACCESS_MODE="local_public")
    @patch("email_exceptions.views.create_case_task")
    @patch("email_exceptions.views.area_options", return_value=(("Operaciones", "Operaciones"),))
    @patch("email_exceptions.views.responsible_options", return_value=(SimpleNamespace(actual_value="ana", display_value="Ana"),))
    @patch("email_exceptions.views.task_creation_available", return_value=True)
    def test_case_endpoint_is_post_only_and_validates_catalog_choices(self, available, responsables, ramos, publish):
        self.user.user_permissions.add(Permission.objects.get(content_type__app_label="email_exceptions", codename="operate_email_exceptions"))
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(f"/operaciones/excepciones-correo/casos/{self.case.pk}/create-task/").status_code, 405)
        response = self.client.post(f"/operaciones/excepciones-correo/casos/{self.case.pk}/create-task/", {"responsible": "ana", "area": "Operaciones"})
        self.assertEqual(response.status_code, 302)
        publish.assert_called_once_with(case=self.case, actor=self.user, responsible="ana", area="Operaciones")

    @override_settings(EMAIL_EXCEPTIONS_ZOHO_TASK_WRITE_ENABLED=True, ZOHO_ACTIVE_PROFILE="sandbox", ZOHO_SANDBOX_WRITE_ENABLED=True, COLECTIVOS_TASK_PUBLISH_ENABLED=True)
    @patch("email_exceptions.zoho.task_creation_available", return_value=True)
    @patch("email_exceptions.zoho.get_task_publisher")
    def test_certain_rejection_is_failed_and_retryable(self, get_publisher, available):
        publisher = Mock()
        publisher.publish_email_exception.side_effect = [
            RuntimeError("rejected"),
            {"record_id": "1234567890"},
        ]
        get_publisher.return_value = publisher
        task = create_case_task(case=self.case, responsible="Ana", area="Operaciones", actor=self.user)
        task.refresh_from_db()
        self.assertEqual(task.technical_status, "FAILED")
        self.assertEqual(task.error_category, "RuntimeError")

        retried = create_case_task(case=self.case, responsible="Ana", area="Operaciones", actor=self.user)
        retried.refresh_from_db()
        self.assertEqual(retried.pk, task.pk)
        self.assertEqual(ZohoTaskCreation.objects.filter(case=self.case).count(), 1)
        self.assertEqual(retried.technical_status, "CREATED")
        self.assertEqual(retried.task_id, "1234567890")
        self.assertEqual(publisher.publish_email_exception.call_count, 2)
