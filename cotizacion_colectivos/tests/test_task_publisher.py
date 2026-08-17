from datetime import timedelta
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from integrations.zoho.exceptions import ZohoSDKError, ZohoTimeoutError
from vault.crypto import encrypt

from cotizacion_colectivos.models import ColectivosTaskOutbox, SolicitudColectivo
from cotizacion_colectivos.services.task_publisher import (
    ColectivosTaskPayload,
    SANDBOX_WRITE_CONFIRMATION,
    SYNTHETIC_TEST_TASK,
    TaskPublicationUncertain,
    TaskPublishingDisabled,
    build_task_record,
    dry_run_outbox,
    enqueue_task,
    get_task_publisher,
    sanitized_dry_run,
)


ENABLED_WRITE = {
    "ZOHO_ACTIVE_PROFILE": "sandbox",
    "ZOHO_SANDBOX_WRITE_ENABLED": True,
    "COLECTIVOS_TASK_PUBLISH_ENABLED": True,
    "COLECTIVOS_TASK_WRITE_CONFIRMATION": SANDBOX_WRITE_CONFIRMATION,
}


def successful_write(record_id="700000000000000001"):
    return SimpleNamespace(records=(SimpleNamespace(
        succeeded=True, record_id=record_id, code="SUCCESS",
    ),))


class TaskPublisherTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user("task-owner")
        self.request = SolicitudColectivo.objects.create(
            source_kind="company", source_reference_hash="a" * 64,
            policy_reference_hash="b" * 64, encrypted_policy_token=encrypt("token"),
            masked_policy_reference="Póliza terminada en 1234", client_label="Cliente",
            branch_code="91", branch_name="Salud colectivo", request_type="ACTUALIZACION",
            assigned_to=self.actor, deadline=timezone.localdate() + timedelta(days=2),
            zoho_profile="sandbox", encrypted_snapshot=encrypt("{}"), created_by=self.actor,
        )
        self.payload = ColectivosTaskPayload(
            request_kind="INCLUSION", source_kind="company", policy_context="masked",
            branch_code="91", local_reference=self.request.public_id,
        )

    def test_dry_run_is_sanitized_and_never_writes(self):
        result = sanitized_dry_run(self.payload, profile="sandbox")
        self.assertEqual(result["writes"], 0)
        self.assertEqual(result["fields"], ("Subject", "tipo_de_solicitud"))
        self.assertNotIn("policy_context", result)

    def test_outbox_is_idempotent_and_payload_is_encrypted(self):
        first = enqueue_task(source=self.request, payload=self.payload)
        second = enqueue_task(source=self.request, payload=self.payload)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(ColectivosTaskOutbox.objects.count(), 1)
        self.assertNotIn(str(self.request.public_id), first.encrypted_payload)
        self.assertEqual(dry_run_outbox(first)["writes"], 0)

    def test_unknown_type_is_rejected(self):
        with self.assertRaises(ValidationError):
            sanitized_dry_run(
                ColectivosTaskPayload("DESCONOCIDO", "company", "", "91", "local"),
                profile="sandbox",
            )

    def test_confirmed_task_kind_mappings(self):
        for kind, expected in (
            ("INCLUSION", "Ingresos"),
            ("RETIRO", "Retiros"),
            ("COTIZACION", "Cotización"),
        ):
            payload = ColectivosTaskPayload(kind, "company", "", "91", "local")
            self.assertEqual(build_task_record(payload)["tipo_de_solicitud"], expected)

    def test_real_collective_flows_do_not_call_the_publisher(self):
        paths = (
            "cotizacion_colectivos/services/external.py",
            "cotizacion_colectivos/services/individual_quotations.py",
            "cotizacion_colectivos/services/requests.py",
            "cotizacion_colectivos/external_views.py",
            "cotizacion_colectivos/views.py",
        )
        for path in paths:
            source = Path(path).read_text(encoding="utf-8")
            self.assertNotIn("get_task_publisher", source, path)
            self.assertNotIn("publish_test_task", source, path)

    @override_settings(**ENABLED_WRITE)
    @patch("cotizacion_colectivos.services.task_publisher.get_zoho")
    def test_enabled_sandbox_calls_create_once_with_exact_synthetic_payload(self, get_zoho):
        create = Mock(return_value=successful_write())
        get_zoho.return_value = SimpleNamespace(records=SimpleNamespace(create=create))

        result = get_task_publisher(
            profile="sandbox", confirmation=SANDBOX_WRITE_CONFIRMATION,
        ).publish_test_task()

        create.assert_called_once_with(module="Tasks", records=(SYNTHETIC_TEST_TASK,))
        self.assertEqual(SYNTHETIC_TEST_TASK, {
            "Subject": "PRUEBA VELTRIX-CV-002 - COTIZACION - NO GESTIONAR",
            "tipo_de_solicitud": "Cotización",
            "rea": "Negocios Bienestar y Beneficios",
            "Observaciones": "Prueba controlada de creación de Task desde A&S Vault. Validación de campos funcionales de Cotización. NO GESTIONAR.",
            "Responsable": "Sara Rua Vargas",
            "Correo_responsable": "sara.rua@segurosays.com",
            "Fecha_de_solicitud_del_cliente": "2026-08-17",
        })
        self.assertEqual(result["record_id"], "700000000000000001")
        self.assertTrue(result["succeeded"])
        self.assertEqual(result["code"], "SUCCESS")

    def test_every_guard_blocks_before_get_zoho(self):
        cases = (
            ({"ZOHO_ACTIVE_PROFILE": "production", "ZOHO_SANDBOX_WRITE_ENABLED": True,
              "COLECTIVOS_TASK_PUBLISH_ENABLED": True,
              "COLECTIVOS_TASK_WRITE_CONFIRMATION": SANDBOX_WRITE_CONFIRMATION}, SANDBOX_WRITE_CONFIRMATION),
            ({"ZOHO_SANDBOX_WRITE_ENABLED": False, "COLECTIVOS_TASK_PUBLISH_ENABLED": True,
              "COLECTIVOS_TASK_WRITE_CONFIRMATION": SANDBOX_WRITE_CONFIRMATION}, SANDBOX_WRITE_CONFIRMATION),
            ({"ZOHO_SANDBOX_WRITE_ENABLED": True, "COLECTIVOS_TASK_PUBLISH_ENABLED": False,
              "COLECTIVOS_TASK_WRITE_CONFIRMATION": SANDBOX_WRITE_CONFIRMATION}, SANDBOX_WRITE_CONFIRMATION),
            ({"ZOHO_SANDBOX_WRITE_ENABLED": True, "COLECTIVOS_TASK_PUBLISH_ENABLED": True,
              "COLECTIVOS_TASK_WRITE_CONFIRMATION": "incorrecta"}, SANDBOX_WRITE_CONFIRMATION),
            (ENABLED_WRITE, "incorrecta"),
        )
        for configured, confirmation in cases:
            with self.subTest(configured=configured, confirmation=confirmation), self.settings(
                **configured
            ), patch("cotizacion_colectivos.services.task_publisher.get_zoho") as get_zoho:
                with self.assertRaises(TaskPublishingDisabled):
                    get_task_publisher(
                        profile="sandbox", confirmation=confirmation,
                    ).publish_test_task()
                get_zoho.assert_not_called()

    @override_settings(**ENABLED_WRITE, ZOHO_PRODUCTION_WRITE_ENABLED=True)
    @patch("cotizacion_colectivos.services.task_publisher.get_zoho")
    def test_production_is_always_rejected_before_sdk(self, get_zoho):
        with self.assertRaises(TaskPublishingDisabled):
            get_task_publisher(
                profile="production", confirmation=SANDBOX_WRITE_CONFIRMATION,
            )
        get_zoho.assert_not_called()

    @override_settings(**ENABLED_WRITE)
    @patch("cotizacion_colectivos.services.task_publisher.get_zoho")
    def test_timeout_is_uncertain_and_never_retried(self, get_zoho):
        create = Mock(side_effect=ZohoTimeoutError("timeout"))
        get_zoho.return_value = SimpleNamespace(records=SimpleNamespace(create=create))
        publisher = get_task_publisher(
            profile="sandbox", confirmation=SANDBOX_WRITE_CONFIRMATION,
        )

        with self.assertRaises(TaskPublicationUncertain) as caught:
            publisher.publish_test_task()

        self.assertTrue(caught.exception.reconciliation_required)
        create.assert_called_once()


class TestTaskCommandTests(TestCase):
    def command_error_for(self, exc):
        publisher = Mock()
        publisher.publish_test_task.side_effect = exc
        with patch(
            "cotizacion_colectivos.management.commands.zoho_create_test_task.get_task_publisher",
            return_value=publisher,
        ) as factory, self.assertRaises(CommandError) as caught:
            call_command(
                "zoho_create_test_task", profile="sandbox",
                confirm=SANDBOX_WRITE_CONFIRMATION,
            )
        factory.assert_called_once_with(
            profile="sandbox", confirmation=SANDBOX_WRITE_CONFIRMATION,
        )
        publisher.publish_test_task.assert_called_once_with()
        return str(caught.exception)

    def test_sdk_error_diagnostic_contains_only_allowlisted_metadata(self):
        diagnostic = self.command_error_for(ZohoSDKError(
            "texto remoto access-token-secret refresh-token-secret client-secret-value "
            "authorization-code-secret payload-secret",
            status_code=422,
            request_id="request-id-secret",
            zoho_code="INVALID_DATA",
            zoho_status="error",
            detail_keys=("api_name", "expected_data_type"),
            detail_field="Fecha_de_solicitud_del_cliente",
            detail_accepted_type="Date",
            detail_given_type="str",
            detail_class="Record",
            detail_index=0,
            backend="sdk",
            operation="records.create",
            module="Tasks",
            sdk_exception_class="SDKException",
            sdk_code="MANDATORY_NOT_FOUND",
            request_sent=None,
        ))

        self.assertEqual(
            diagnostic,
            "No se creó la Task (category=sdk; status_code=422; backend=sdk; "
            "operation=records.create; module=Tasks; sdk_exception_class=SDKException; "
            "sdk_code=MANDATORY_NOT_FOUND; zoho_code=INVALID_DATA; zoho_status=error; "
            "detail_keys=[api_name, expected_data_type]; field=Fecha_de_solicitud_del_cliente; "
            "accepted_type=Date; given_type=str; class=Record; index=0; request_sent=unknown).",
        )
        for secret in (
            "access-token-secret", "refresh-token-secret", "client-secret-value",
            "authorization-code-secret", "payload-secret", "request-id-secret",
            "texto remoto",
        ):
            self.assertNotIn(secret, diagnostic)

    def test_sdk_error_diagnostic_marks_absent_values_as_unknown(self):
        diagnostic = self.command_error_for(ZohoSDKError(
            "refresh-token-secret",
            status_code=None,
            backend="sdk",
            operation="records.create",
            module="Tasks",
            sdk_exception_class="SDKException",
            sdk_code="",
            request_sent=None,
        ))

        self.assertEqual(
            diagnostic,
            "No se creó la Task (category=sdk; status_code=unknown; backend=sdk; "
            "operation=records.create; module=Tasks; sdk_exception_class=SDKException; "
            "sdk_code=unknown; zoho_code=unknown; zoho_status=unknown; "
            "detail_keys=none; field=unknown; accepted_type=unknown; given_type=unknown; "
            "class=unknown; index=unknown; request_sent=unknown).",
        )
        self.assertNotIn("refresh-token-secret", diagnostic)

    def test_sdk_error_preserves_request_sent_tristate_without_inference(self):
        for value, expected in ((True, "true"), (False, "false"), (None, "unknown")):
            with self.subTest(request_sent=value):
                diagnostic = self.command_error_for(ZohoSDKError(
                    "safe normalized message",
                    backend="sdk",
                    operation="records.create",
                    module="Tasks",
                    sdk_exception_class="SDKException",
                    request_sent=value,
                ))
                self.assertIn(f"request_sent={expected}", diagnostic)

    def test_uncertain_result_behavior_is_unchanged_and_not_retried(self):
        message = (
            "Resultado incierto: no reintente; requiere conciliación manual en Zoho Sandbox."
        )
        diagnostic = self.command_error_for(TaskPublicationUncertain(message))
        self.assertEqual(diagnostic, message)

    def test_missing_confirmation_fails_without_publisher(self):
        with patch(
            "cotizacion_colectivos.management.commands.zoho_create_test_task.get_task_publisher"
        ) as factory, self.assertRaises(CommandError):
            call_command("zoho_create_test_task", profile="sandbox")
        factory.assert_not_called()

    def test_incorrect_confirmation_fails_without_create(self):
        with self.settings(**ENABLED_WRITE), patch(
            "cotizacion_colectivos.services.task_publisher.get_zoho"
        ) as get_zoho, self.assertRaises(CommandError):
            call_command(
                "zoho_create_test_task", profile="sandbox", confirm="incorrecta",
            )
        get_zoho.assert_not_called()

    def test_production_fails_without_publisher(self):
        with patch(
            "cotizacion_colectivos.management.commands.zoho_create_test_task.get_task_publisher"
        ) as factory, self.assertRaises(CommandError):
            call_command(
                "zoho_create_test_task", profile="production",
                confirm=SANDBOX_WRITE_CONFIRMATION,
            )
        factory.assert_not_called()

    def test_correct_sandbox_command_invokes_publisher_once(self):
        publisher = Mock()
        publisher.publish_test_task.return_value = {
            "profile": "sandbox", "module": "Tasks", "record_id": "7001",
        }
        stdout = StringIO()
        with patch(
            "cotizacion_colectivos.management.commands.zoho_create_test_task.get_task_publisher",
            return_value=publisher,
        ) as factory:
            call_command(
                "zoho_create_test_task", profile="sandbox",
                confirm=SANDBOX_WRITE_CONFIRMATION, stdout=stdout,
            )

        factory.assert_called_once_with(
            profile="sandbox", confirmation=SANDBOX_WRITE_CONFIRMATION,
        )
        publisher.publish_test_task.assert_called_once_with()
        self.assertEqual(stdout.getvalue().splitlines(), [
            "Task creada correctamente",
            "Profile: sandbox",
            "Module: Tasks",
            "Record ID: 7001",
            "Succeeded: unknown",
            "Code: unknown",
        ])
