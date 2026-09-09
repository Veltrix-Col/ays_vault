from __future__ import annotations

from datetime import date
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from cotizacion_colectivos.excepciones_facturacion import (
    BillingException,
    BillingExceptionSource,
    BillingExceptionType,
    BillingExceptionsResult,
    BillingExceptionsVolumes,
)
from cotizacion_colectivos.excepciones_facturacion.application import (
    PRODUCTION_WRITE_FLAGS,
)


COMMAND = (
    "cotizacion_colectivos.management.commands."
    "colectivos_billing_exceptions"
)


def empty_result(profile="sandbox"):
    return BillingExceptionsResult(
        profile=profile,
        as_of=date(2026, 9, 7),
        exceptions=(),
        revision_rows_count=0,
        cobros_faltantes_results_count=0,
        revision_count=0,
        missing_charges_count=0,
        exception_counts=(),
        source_counts=(),
        revision_diagnostics=(),
        revision_audit=(),
        cobros_faltantes_anomalies=(),
        volumes=BillingExceptionsVolumes(0, 0, 0, 0, 0, 0, 0),
    )


def diagnostic_result(profile="sandbox"):
    base = {
        "source": BillingExceptionSource.REVISION_FACTURACION,
        "rule_code": "test.rule",
        "reason": "Hallazgo verificable",
        "policy_number": "POL-1",
        "policy_id": "4933790000200703052",
        "client_name": "Cliente Uno",
        "insurer": "Aseguradora Uno",
        "branch": "Salud colectivo",
        "operation_id": "4933790000300000001",
        "operation_name": "Cobro 2",
        "installment_number": 2,
        "relevant_date": date(2026, 9, 1),
        "billing_date": date(2026, 9, 7),
        "context": (
            ("diagnostics", "MISSING_CERTIFICATE"),
            ("operation_link", "https://crm.example/operation/1"),
            ("access_token", "must-not-be-printed"),
        ),
    }
    first = BillingException(
        exception_key="key-certificate",
        exception_type=BillingExceptionType.MISSING_CERTIFICATE,
        source_reference="Opeeraciones:4933790000300000001",
        **base,
    )
    second = BillingException(
        exception_key="key-zero",
        exception_type=BillingExceptionType.ZERO_OPERATION_TOTAL,
        source_reference="Opeeraciones:4933790000300000001",
        **base,
    )
    return BillingExceptionsResult(
        profile=profile,
        as_of=date(2026, 9, 7),
        exceptions=(second, first),
        revision_rows_count=1,
        cobros_faltantes_results_count=0,
        revision_count=2,
        missing_charges_count=0,
        exception_counts=(("MISSING_CERTIFICATE", 1), ("ZERO_OPERATION_TOTAL", 1)),
        source_counts=(("revision_facturacion", 2),),
        revision_diagnostics=(),
        revision_audit=(),
        cobros_faltantes_anomalies=(),
        volumes=BillingExceptionsVolumes(1, 1, 0, 1, 0, 1, 0),
    )


@override_settings(**{flag: False for flag in PRODUCTION_WRITE_FLAGS})
class BillingExceptionsCommandTests(SimpleTestCase):
    def test_command_requires_no_baseline_and_prints_operational_summary(self):
        stdout = StringIO()
        with patch(f"{COMMAND}.get_billing_exceptions", return_value=empty_result()):
            call_command(
                "colectivos_billing_exceptions",
                profile="sandbox",
                as_of="2026-09-07",
                sample_limit=0,
                stdout=stdout,
            )
        output = stdout.getvalue()
        self.assertIn("Excepciones de Facturación", output)
        self.assertIn("Total exceptions: 0", output)
        self.assertNotIn("baseline", output.casefold())

    def test_command_forwards_explicit_as_of_and_production_confirmation(self):
        with patch(f"{COMMAND}.get_billing_exceptions", return_value=empty_result("production")) as service:
            call_command(
                "colectivos_billing_exceptions",
                profile="production",
                as_of="2026-09-07",
                allow_production_read=True,
                sample_limit=0,
            )
        self.assertEqual(service.call_args.kwargs["as_of"], date(2026, 9, 7))
        self.assertTrue(service.call_args.kwargs["allow_production_read"])

    def test_command_rejects_production_without_confirmation_before_zoho(self):
        with self.assertRaisesMessage(CommandError, "confirmación explícita"):
            call_command(
                "colectivos_billing_exceptions",
                profile="production",
                as_of="2026-09-07",
                sample_limit=0,
            )

    def test_command_rejects_invalid_date_and_unbounded_sample(self):
        with self.assertRaisesMessage(CommandError, "YYYY-MM-DD"):
            call_command(
                "colectivos_billing_exceptions",
                profile="sandbox",
                as_of="07-09-2026",
            )
        with self.assertRaisesMessage(CommandError, "entre 0 y 20"):
            call_command(
                "colectivos_billing_exceptions",
                profile="sandbox",
                as_of="2026-09-07",
                sample_limit=21,
            )

    def test_stratified_and_multi_findings_are_local_and_detailed(self):
        stdout = StringIO()
        result = diagnostic_result("production")
        with patch(
            f"{COMMAND}.get_billing_exceptions", return_value=result
        ) as service:
            call_command(
                "colectivos_billing_exceptions",
                profile="production",
                as_of="2026-09-07",
                allow_production_read=True,
                sample_per_type=1,
                show_multi_findings=True,
                multi_findings_limit=1,
                stdout=stdout,
            )
        service.assert_called_once()
        output = stdout.getvalue()
        self.assertIn("max 1 per exception type", output)
        self.assertIn("exception_key: key-certificate", output)
        self.assertIn("operation_name: Cobro 2", output)
        self.assertIn("operation_link: https://crm.example/operation/1", output)
        self.assertIn("Multiple findings: 1 group(s), showing 1", output)
        self.assertNotIn("must-not-be-printed", output)
        self.assertNotIn("access_token", output)

    def test_new_diagnostic_options_do_not_bypass_production_guard(self):
        with self.assertRaisesMessage(CommandError, "confirmación explícita"):
            call_command(
                "colectivos_billing_exceptions",
                profile="production",
                as_of="2026-09-07",
                sample_per_type=2,
                show_multi_findings=True,
            )

    def test_new_diagnostic_limits_are_bounded(self):
        with self.assertRaisesMessage(CommandError, "--sample-per-type"):
            call_command(
                "colectivos_billing_exceptions",
                profile="sandbox",
                as_of="2026-09-07",
                sample_per_type=21,
            )
        with self.assertRaisesMessage(CommandError, "--multi-findings-limit"):
            call_command(
                "colectivos_billing_exceptions",
                profile="sandbox",
                as_of="2026-09-07",
                multi_findings_limit=51,
            )
