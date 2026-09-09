from datetime import date
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from cotizacion_colectivos.models import BillingExceptionRefreshRun


class BillingExceptionWorkerCommandTests(TestCase):
    @patch(
        "cotizacion_colectivos.management.commands."
        "colectivos_billing_exceptions_worker.recover_orphaned_billing_refreshes",
        return_value=(),
    )
    @patch(
        "cotizacion_colectivos.management.commands."
        "colectivos_billing_exceptions_worker.execute_billing_exceptions_refresh",
    )
    def test_once_processes_oldest_pending_with_existing_executor(self, execute, _recover):
        older = BillingExceptionRefreshRun.objects.create(
            profile="production", as_of=date(2026, 9, 8), status="PENDING",
        )
        execute.return_value = older
        output = StringIO()

        call_command("colectivos_billing_exceptions_worker", once=True, stdout=output)

        execute.assert_called_once_with(older.pk)
        self.assertIn("Production WRITE: 0", output.getvalue())

    @patch(
        "cotizacion_colectivos.management.commands."
        "colectivos_billing_exceptions_worker.recover_orphaned_billing_refreshes",
        return_value=(),
    )
    @patch(
        "cotizacion_colectivos.management.commands."
        "colectivos_billing_exceptions_worker.execute_billing_exceptions_refresh",
    )
    def test_once_does_not_execute_without_pending(self, execute, _recover):
        output = StringIO()
        call_command("colectivos_billing_exceptions_worker", once=True, stdout=output)
        execute.assert_not_called()
        self.assertIn("No hay actualizaciones pendientes", output.getvalue())

    @patch(
        "cotizacion_colectivos.management.commands."
        "colectivos_billing_exceptions_worker.recover_orphaned_billing_refreshes",
        return_value=(),
    )
    @patch(
        "cotizacion_colectivos.management.commands."
        "colectivos_billing_exceptions_worker.execute_billing_exceptions_refresh",
    )
    def test_concurrent_claim_is_not_processed_twice(self, execute, _recover):
        run = BillingExceptionRefreshRun.objects.create(
            profile="production", as_of=date(2026, 9, 8), status="PENDING",
        )

        def claimed_elsewhere(run_id):
            BillingExceptionRefreshRun.objects.filter(pk=run_id).update(status="RUNNING")
            raise ValueError("El run ya fue iniciado o finalizado.")

        execute.side_effect = claimed_elsewhere
        output = StringIO()
        call_command("colectivos_billing_exceptions_worker", once=True, stdout=output)
        execute.assert_called_once_with(run.pk)
        self.assertIn("reclamado por otro consumidor", output.getvalue())

    @patch(
        "cotizacion_colectivos.management.commands."
        "colectivos_billing_exceptions_worker.recover_orphaned_billing_refreshes",
        return_value=(),
    )
    @patch(
        "cotizacion_colectivos.management.commands."
        "colectivos_billing_exceptions_worker.execute_billing_exceptions_refresh",
    )
    def test_once_reports_failed_execution_cleanly(self, execute, _recover):
        run = BillingExceptionRefreshRun.objects.create(
            profile="production", as_of=date(2026, 9, 8), status="PENDING",
        )

        def failed(run_id):
            BillingExceptionRefreshRun.objects.filter(pk=run_id).update(
                status="FAILED", safe_error="refresh: test_error",
            )
            raise RuntimeError("sensitive detail")

        execute.side_effect = failed
        with self.assertRaisesMessage(CommandError, "se conservó la última información válida"):
            call_command("colectivos_billing_exceptions_worker", once=True)
        run.refresh_from_db()
        self.assertEqual(run.status, BillingExceptionRefreshRun.Status.FAILED)

    def test_poll_interval_must_be_positive(self):
        with self.assertRaisesMessage(CommandError, "mayor o igual a 1"):
            call_command("colectivos_billing_exceptions_worker", once=True, poll_seconds=0)
