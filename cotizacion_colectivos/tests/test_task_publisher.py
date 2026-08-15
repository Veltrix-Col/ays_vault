from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from vault.crypto import encrypt

from cotizacion_colectivos.models import ColectivosTaskOutbox, SolicitudColectivo
from cotizacion_colectivos.services.task_publisher import (
    ColectivosTaskPayload,
    TaskContractIncomplete,
    TaskPublishingDisabled,
    dry_run_outbox,
    enqueue_task,
    get_task_publisher,
    sanitized_dry_run,
)


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

    def test_disabled_by_default_and_production_always_rejected(self):
        with self.assertRaises(TaskPublishingDisabled):
            get_task_publisher().publish(self.payload)
        with self.settings(COLECTIVOS_TASK_PUBLISH_ENABLED=True, COLECTIVOS_TASK_WRITE_CONFIRMATION="SANDBOX_TASK_WRITE"):
            with self.assertRaises(TaskPublishingDisabled):
                get_task_publisher(profile="production")
            with self.assertRaises(TaskContractIncomplete):
                get_task_publisher(profile="sandbox")
