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

from integrations.zoho.exceptions import ZohoTimeoutError
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
            "Subject": "PRUEBA VELTRIX - NO GESTIONAR",
            "tipo_de_solicitud": "Ingresos",
        })
        self.assertEqual(result["record_id"], "700000000000000001")

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
        ])
