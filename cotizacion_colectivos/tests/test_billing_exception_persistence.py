from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone

from cotizacion_colectivos.excepciones_facturacion.application import (
    BillingExceptionsResult, BillingExceptionsVolumes,
)
from cotizacion_colectivos.excepciones_facturacion.domain import (
    BillingException, BillingExceptionSource, BillingExceptionType,
)
from cotizacion_colectivos.excepciones_facturacion.persistence import (
    ORPHANED_REFRESH_SAFE_ERROR,
    execute_billing_exceptions_refresh,
    queue_billing_exceptions_refresh,
    recover_orphaned_billing_refreshes,
)
from cotizacion_colectivos.excepciones_facturacion.query import filtered_billing_cases
from cotizacion_colectivos.models import (
    BillingExceptionRefreshRun, BillingOperationalCase, BillingTechnicalException,
)


def exception(key="one"):
    return BillingException(
        exception_key=f"billing-exception/v1/{key}",
        exception_type=BillingExceptionType.MISSING_CERTIFICATE,
        source=BillingExceptionSource.REVISION_FACTURACION, rule_code="rule", reason="reason",
        source_reference="source", policy_id="10", policy_number="POL-1",
        operation_id="20", operation_name="Cobro 1", relevant_date=date(2026, 9, 1),
    )


def result(*items):
    return BillingExceptionsResult(
        profile="production", as_of=date(2026, 9, 7), exceptions=tuple(items),
        revision_rows_count=len(items), cobros_faltantes_results_count=0,
        revision_count=len(items), missing_charges_count=0,
        exception_counts=(("MISSING_CERTIFICATE", len(items)),) if items else (),
        source_counts=(("revision_facturacion", len(items)),) if items else (),
        revision_diagnostics=(), revision_audit=(), cobros_faltantes_anomalies=(),
        volumes=BillingExceptionsVolumes(0, 0, 0, 0, 0, 0, 0),
    )


class BillingPersistenceTests(TestCase):
    def run_with(self, *items):
        run = queue_billing_exceptions_refresh(as_of=date(2026, 9, 7))
        return execute_billing_exceptions_refresh(run.pk, loader=lambda **kwargs: result(*items))

    def test_first_and_identical_refresh_are_idempotent(self):
        self.run_with(exception())
        case = BillingOperationalCase.objects.get()
        case.zoho_task_id = "123456789012"
        case.save(update_fields=("zoho_task_id",))
        self.run_with(exception())
        self.assertEqual(BillingTechnicalException.objects.count(), 1)
        case.refresh_from_db()
        self.assertEqual(case.detection_count, 2)
        self.assertEqual(case.zoho_task_id, "123456789012")

    def test_disappearance_and_reappearance_preserve_history(self):
        self.run_with(exception())
        case = BillingOperationalCase.objects.get()
        original_trace = (
            case.first_detected_at, case.last_detected_at,
            case.detection_count, case.reappearance_count,
        )
        empty_run = self.run_with()
        tech = BillingTechnicalException.objects.get()
        self.assertEqual(tech.detection_status, tech.DetectionStatus.NOT_DETECTED)
        case.refresh_from_db()
        self.assertEqual(BillingOperationalCase.objects.count(), 1)
        self.assertEqual(case.detection_status, "NOT_DETECTED")
        self.assertEqual(
            (case.first_detected_at, case.last_detected_at,
             case.detection_count, case.reappearance_count),
            original_trace,
        )
        _form, active_cases = filtered_billing_cases(snapshot=empty_run)
        self.assertEqual(active_cases.count(), 0)
        self.run_with(exception())
        tech.refresh_from_db()
        case.refresh_from_db()
        self.assertEqual(tech.detection_status, tech.DetectionStatus.DETECTED)
        self.assertEqual(tech.reappearance_count, 1)
        self.assertEqual(case.detection_status, "DETECTED")
        self.assertEqual(case.reappearance_count, 1)
        self.assertEqual(case.detection_count, 2)

    def test_failed_refresh_does_not_mark_snapshot_missing(self):
        self.run_with(exception())
        run = queue_billing_exceptions_refresh(as_of=date(2026, 9, 7))
        with self.assertRaises(RuntimeError):
            execute_billing_exceptions_refresh(
                run.pk, loader=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("secret payload")),
            )
        self.assertEqual(BillingTechnicalException.objects.get().detection_status, "DETECTED")
        run.refresh_from_db()
        self.assertEqual(run.status, BillingExceptionRefreshRun.Status.FAILED)
        self.assertNotIn("secret payload", run.safe_error)

    def test_running_younger_than_fifteen_minutes_is_not_recovered(self):
        now = timezone.now()
        run = queue_billing_exceptions_refresh(as_of=date(2026, 9, 8))
        BillingExceptionRefreshRun.objects.filter(pk=run.pk).update(
            status=BillingExceptionRefreshRun.Status.RUNNING,
            started_at=now - timedelta(minutes=14),
        )
        self.assertEqual(recover_orphaned_billing_refreshes(now=now), ())
        run.refresh_from_db()
        self.assertEqual(run.status, BillingExceptionRefreshRun.Status.RUNNING)

    def test_orphaned_running_is_failed_without_changing_latest_success(self):
        success = self.run_with(exception())
        case = BillingOperationalCase.objects.get()
        original_trace = (
            case.detection_status, case.first_detected_at, case.last_detected_at,
            case.detection_count, case.reappearance_count, case.last_run_id,
        )
        now = timezone.now()
        orphan = queue_billing_exceptions_refresh(as_of=date(2026, 9, 8))
        BillingExceptionRefreshRun.objects.filter(pk=orphan.pk).update(
            status=BillingExceptionRefreshRun.Status.RUNNING,
            started_at=now - timedelta(minutes=16),
        )

        self.assertEqual(recover_orphaned_billing_refreshes(now=now), (orphan.pk,))
        orphan.refresh_from_db()
        case.refresh_from_db()
        self.assertEqual(orphan.status, BillingExceptionRefreshRun.Status.FAILED)
        self.assertEqual(orphan.finished_at, now)
        self.assertEqual(orphan.safe_error, ORPHANED_REFRESH_SAFE_ERROR)
        self.assertEqual(
            (case.detection_status, case.first_detected_at, case.last_detected_at,
             case.detection_count, case.reappearance_count, case.last_run_id),
            original_trace,
        )
        self.assertEqual(
            BillingExceptionRefreshRun.objects.filter(status="SUCCESS").first(), success,
        )

        next_run = queue_billing_exceptions_refresh(as_of=date(2026, 9, 8))
        completed = execute_billing_exceptions_refresh(
            next_run.pk, loader=lambda **kwargs: result(exception()),
        )
        self.assertEqual(completed.status, BillingExceptionRefreshRun.Status.SUCCESS)

    def test_run_cannot_be_processed_twice(self):
        run = queue_billing_exceptions_refresh(as_of=date(2026, 9, 8))
        execute_billing_exceptions_refresh(run.pk, loader=lambda **kwargs: result())
        with self.assertRaisesMessage(ValueError, "ya fue iniciado o finalizado"):
            execute_billing_exceptions_refresh(run.pk, loader=lambda **kwargs: result())

    def test_executor_keeps_production_explicitly_read_only(self):
        received = {}

        def loader(**kwargs):
            received.update(kwargs)
            return result()

        run = queue_billing_exceptions_refresh(as_of=date(2026, 9, 8))
        execute_billing_exceptions_refresh(run.pk, loader=loader)
        self.assertEqual(received["profile"], "production")
        self.assertEqual(received["as_of"], date(2026, 9, 8))
        self.assertIs(received["allow_production_read"], True)
        self.assertIsNone(received["zoho"])
