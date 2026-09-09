from datetime import date

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from cotizacion_colectivos.models import BillingOperationalCase, BillingTechnicalException
from cotizacion_colectivos.services.billing_exception_tasks import (
    BILLING_TASK_FIELDS, build_billing_exception_task, enqueue_billing_exception_task,
)
from cotizacion_colectivos.services.task_publisher import publish_task_outbox


class BillingTaskBuilderTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.case = BillingOperationalCase.objects.create(
            case_key="billing-case/v1/operation/id:1/operation:2", kind="OPERATION",
            policy_id="1", policy_number="POL-1", client_name="Cliente", insurer="ASEG",
            branch="VIDA", operation_id="2", operation_name="Cobro 1", local_priority=2,
            first_detected_at=now, last_detected_at=now,
        )
        for key, kind in (("a", "MISSING_CERTIFICATE"), ("b", "ZERO_OPERATION_TOTAL")):
            finding = BillingTechnicalException.objects.create(
                exception_key=key, source="revision_facturacion", exception_type=kind,
                rule_code="rule", reason="reason", source_reference="source", policy_number="POL-1",
                operation_id="2", first_detected_at=now, last_detected_at=now,
            )
            self.case.exceptions.add(finding)

    def test_closed_payload_and_exact_values(self):
        record = build_billing_exception_task(
            self.case, responsible="Analista", responsible_email="a@example.com",
            allowed_responsibles=("Analista",), allowed_branches=("VIDA",),
            allowed_insurers=("ASEG",),
        )
        self.assertLessEqual(set(record), BILLING_TASK_FIELDS)
        self.assertEqual(record["tipo_de_solicitud"], "Facturación")
        self.assertIs(record["Caso_de_excepci_n"], True)
        self.assertEqual(record["rea"], "Cartera")
        self.assertEqual(record["Aseguradora1"], ["ASEG"])
        self.assertNotIn("Priority", record)
        self.assertIn("Certificado faltante", record["Motivo_de_excepci_n"])

    def test_optional_responsible_and_exact_validation(self):
        record = build_billing_exception_task(self.case)
        self.assertNotIn("Responsable", record)
        with self.assertRaises(ValidationError):
            build_billing_exception_task(self.case, responsible="Inventado", allowed_responsibles=("Real",))

    def test_outbox_is_idempotent_and_rejects_case_with_task(self):
        record = build_billing_exception_task(self.case)
        self.assertEqual(enqueue_billing_exception_task(self.case, record=record).pk,
                         enqueue_billing_exception_task(self.case, record=record).pk)
        self.case.zoho_task_id = "123456789012"
        self.case.save(update_fields=("zoho_task_id",))
        with self.assertRaises(ValidationError):
            enqueue_billing_exception_task(self.case, record=record)

    def test_shared_publisher_path_updates_case_only_after_confirmed_create(self):
        from unittest.mock import Mock, patch
        from django.test import override_settings

        record = build_billing_exception_task(self.case)
        outbox = enqueue_billing_exception_task(self.case, record=record)
        publisher = Mock()
        publisher.publish_billing_exception.return_value = {"record_id": "123456789012"}
        with override_settings(COLECTIVOS_TASK_PUBLISH_ENABLED=True, ZOHO_ACTIVE_PROFILE="sandbox"):
            with patch("cotizacion_colectivos.services.task_publisher.get_task_publisher", return_value=publisher):
                publish_task_outbox(outbox.pk)
        self.case.refresh_from_db()
        outbox.refresh_from_db()
        self.assertEqual(self.case.zoho_task_id, "123456789012")
        self.assertEqual(outbox.status, outbox.Status.PUBLISHED)
        publisher.publish_billing_exception.assert_called_once_with(record)
