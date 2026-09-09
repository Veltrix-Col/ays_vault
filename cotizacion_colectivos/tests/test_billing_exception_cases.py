from datetime import date

from django.test import SimpleTestCase

from cotizacion_colectivos.excepciones_facturacion.cases import build_billing_cases
from cotizacion_colectivos.excepciones_facturacion.domain import (
    BillingException, BillingExceptionSource, BillingExceptionType,
)


def finding(kind, *, operation_id=None, installment=None, relevant=None, suffix="x"):
    return BillingException(
        exception_key=f"billing-exception/v1/{suffix}/{kind.value}", exception_type=kind,
        source=(BillingExceptionSource.COBROS_FALTANTES if kind is BillingExceptionType.MISSING_CHARGE
                else BillingExceptionSource.REVISION_FACTURACION),
        rule_code="rule", reason="reason", source_reference="source", policy_id="10",
        policy_number="POL-1", operation_id=operation_id, operation_name="Cobro 1" if operation_id else None,
        installment_number=installment, relevant_date=relevant,
    )


class BillingCaseTests(SimpleTestCase):
    def test_groups_operation_findings_and_is_deterministic(self):
        items = (
            finding(BillingExceptionType.MISSING_CERTIFICATE, operation_id="20", suffix="b"),
            finding(BillingExceptionType.ZERO_OPERATION_TOTAL, operation_id="20", suffix="a"),
        )
        first = build_billing_cases(items)
        second = build_billing_cases(reversed(items))
        self.assertEqual(first, second)
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0].case_key, "billing-case/v1/operation/id:10/operation:20")
        self.assertEqual(first[0].priority, 2)

    def test_missing_operation_and_missing_charge_never_cross_deduplicate(self):
        day = date(2026, 9, 1)
        cases = build_billing_cases((
            finding(BillingExceptionType.MISSING_OPERATION, installment=2, relevant=day, suffix="op"),
            finding(BillingExceptionType.MISSING_CHARGE, suffix="gap"),
        ))
        self.assertEqual(len(cases), 2)
        self.assertTrue(all(item.priority == 1 for item in cases))
        self.assertIn("missing-operation", cases[0].case_key + cases[1].case_key)
        self.assertIn("policy-gap", cases[0].case_key + cases[1].case_key)

    def test_stable_order_places_missing_dates_last_within_priority(self):
        dated = finding(BillingExceptionType.MISSING_OPERATION, installment=1,
                        relevant=date(2026, 1, 1), suffix="dated")
        missing = finding(BillingExceptionType.MISSING_CHARGE, suffix="undated")
        self.assertEqual(build_billing_cases((missing, dated))[0].exceptions[0], dated)
