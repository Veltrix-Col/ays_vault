from datetime import date
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings

from cotizacion_colectivos.excepciones_facturacion.persistence import (
    BillingRefreshAlreadyRunning,
)
from cotizacion_colectivos.models import BillingExceptionRefreshRun


COMMAND_MODULE = (
    "cotizacion_colectivos.management.commands."
    "colectivos_queue_billing_exceptions_refresh"
)
AS_OF = date(2026, 9, 11)


class BillingExceptionQueueCommandTests(TestCase):
    @patch(f"{COMMAND_MODULE}.timezone.localdate", return_value=AS_OF)
    def test_creates_scheduler_pending_with_local_date(self, localdate):
        output = StringIO()

        call_command("colectivos_queue_billing_exceptions_refresh", stdout=output)

        run = BillingExceptionRefreshRun.objects.get()
        self.assertEqual(run.profile, "production")
        self.assertEqual(run.as_of, AS_OF)
        self.assertEqual(run.status, BillingExceptionRefreshRun.Status.PENDING)
        self.assertIsNone(run.requested_by)
        localdate.assert_called_once_with()
        rendered = output.getvalue()
        self.assertIn(f"Run: {run.pk}", rendered)
        self.assertIn("Fecha: 2026-09-11", rendered)
        self.assertIn("Profile: production", rendered)
        self.assertIn("Estado: PENDING", rendered)
        self.assertIn("Production WRITE: 0", rendered)

    def test_existing_pending_exits_successfully_without_duplicate(self):
        existing = BillingExceptionRefreshRun.objects.create(
            profile="production",
            as_of=AS_OF,
            status=BillingExceptionRefreshRun.Status.PENDING,
        )
        output = StringIO()

        call_command("colectivos_queue_billing_exceptions_refresh", stdout=output)

        self.assertEqual(BillingExceptionRefreshRun.objects.count(), 1)
        self.assertEqual(BillingExceptionRefreshRun.objects.get(), existing)
        self.assertIn("No se creó una nueva", output.getvalue())

    def test_existing_running_exits_successfully_without_duplicate(self):
        existing = BillingExceptionRefreshRun.objects.create(
            profile="production",
            as_of=AS_OF,
            status=BillingExceptionRefreshRun.Status.RUNNING,
        )
        output = StringIO()

        call_command("colectivos_queue_billing_exceptions_refresh", stdout=output)

        self.assertEqual(BillingExceptionRefreshRun.objects.count(), 1)
        self.assertEqual(BillingExceptionRefreshRun.objects.get(), existing)
        self.assertIn("No se creó una nueva", output.getvalue())

    @patch(f"{COMMAND_MODULE}.queue_billing_exceptions_refresh")
    @patch(f"{COMMAND_MODULE}.timezone.localdate", return_value=AS_OF)
    def test_reuses_shared_queue_contract(self, _localdate, queue):
        queue.return_value = SimpleNamespace(
            pk=7,
            as_of=AS_OF,
            profile="production",
            status="PENDING",
        )

        call_command(
            "colectivos_queue_billing_exceptions_refresh",
            stdout=StringIO(),
        )

        queue.assert_called_once_with(as_of=AS_OF, requested_by=None)

    @patch(f"{COMMAND_MODULE}.queue_billing_exceptions_refresh")
    def test_concurrent_queue_conflict_is_an_idempotent_success(self, queue):
        queue.side_effect = BillingRefreshAlreadyRunning(
            "Ya existe una actualización pendiente o en curso."
        )
        output = StringIO()

        call_command("colectivos_queue_billing_exceptions_refresh", stdout=output)

        self.assertIn("No se creó una nueva", output.getvalue())

    @override_settings(
        ZOHO_PRODUCTION_WRITE_ENABLED=True,
        COLECTIVOS_TASK_PUBLISH_ENABLED=True,
        COLECTIVOS_CONTACT_PUBLISH_ENABLED=True,
        COLECTIVOS_RISK_PUBLISH_ENABLED=True,
        COLECTIVOS_SUBRISK_PUBLISH_ENABLED=True,
        COLECTIVOS_ATTACHMENT_PUBLISH_ENABLED=True,
        COLECTIVOS_INVITATION_ATTACHMENT_PUBLISH_ENABLED=True,
    )
    @patch(
        "cotizacion_colectivos.services.task_publisher.publish_task_outbox",
        side_effect=AssertionError("publisher no permitido"),
    )
    @patch(
        "integrations.zoho.get_zoho",
        side_effect=AssertionError("Zoho no permitido"),
    )
    @patch(
        "cotizacion_colectivos.excepciones_facturacion.persistence."
        "execute_billing_exceptions_refresh",
        side_effect=AssertionError("executor no permitido"),
    )
    def test_does_not_execute_refresh_query_zoho_or_publish(
        self,
        execute,
        get_zoho,
        publish,
    ):
        call_command(
            "colectivos_queue_billing_exceptions_refresh",
            stdout=StringIO(),
        )

        execute.assert_not_called()
        get_zoho.assert_not_called()
        publish.assert_not_called()

    def test_database_constraint_allows_only_one_active_production_run(self):
        BillingExceptionRefreshRun.objects.create(
            profile="production",
            as_of=AS_OF,
            status=BillingExceptionRefreshRun.Status.PENDING,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            BillingExceptionRefreshRun.objects.create(
                profile="production",
                as_of=AS_OF,
                status=BillingExceptionRefreshRun.Status.RUNNING,
            )

        self.assertEqual(
            BillingExceptionRefreshRun.objects.filter(
                profile="production",
                status__in=(
                    BillingExceptionRefreshRun.Status.PENDING,
                    BillingExceptionRefreshRun.Status.RUNNING,
                ),
            ).count(),
            1,
        )
