from __future__ import annotations

from dataclasses import replace
from datetime import date

from django.test import SimpleTestCase

from cotizacion_colectivos.excepciones_facturacion import (
    BillingException,
    BillingExceptionSource,
    BillingExceptionType,
    find_multiple_findings,
    sample_exceptions_per_type,
)


def exception(
    key: str,
    exception_type: BillingExceptionType,
    *,
    policy_id: str = "policy-1",
    operation_id: str | None = None,
) -> BillingException:
    return BillingException(
        exception_key=key,
        exception_type=exception_type,
        source=BillingExceptionSource.REVISION_FACTURACION,
        rule_code="test.rule",
        reason="Hallazgo",
        source_reference=f"test:{key}",
        policy_number="POL-1",
        policy_id=policy_id,
        operation_id=operation_id,
        relevant_date=date(2026, 9, 7),
    )


class BillingExceptionDiagnosticsTests(SimpleTestCase):
    def test_stratified_sample_selects_each_exception_type(self):
        values = (
            exception("z", BillingExceptionType.MISSING_OPERATION),
            exception("b", BillingExceptionType.MISSING_CERTIFICATE),
            exception("a", BillingExceptionType.MISSING_CERTIFICATE),
        )
        sample = sample_exceptions_per_type(values, limit=1)
        self.assertEqual(
            [(item.exception_type.value, item.exception_key) for item in sample],
            [("MISSING_CERTIFICATE", "a"), ("MISSING_OPERATION", "z")],
        )

    def test_stratified_sample_applies_limit_per_type(self):
        values = tuple(
            exception(f"certificate-{index}", BillingExceptionType.MISSING_CERTIFICATE)
            for index in range(4)
        ) + tuple(
            exception(f"operation-{index}", BillingExceptionType.MISSING_OPERATION)
            for index in range(3)
        )
        sample = sample_exceptions_per_type(values, limit=2)
        self.assertEqual(len(sample), 4)

    def test_stratified_sample_order_does_not_depend_on_input_order(self):
        values = (
            exception("c", BillingExceptionType.MISSING_OPERATION),
            exception("a", BillingExceptionType.MISSING_CERTIFICATE),
            exception("b", BillingExceptionType.MISSING_OPERATION),
        )
        first = sample_exceptions_per_type(values, limit=2)
        second = sample_exceptions_per_type(reversed(values), limit=2)
        self.assertEqual(first, second)

    def test_multiple_findings_groups_distinct_types_on_same_operation(self):
        base = exception(
            "a", BillingExceptionType.MISSING_CERTIFICATE, operation_id="operation-1"
        )
        values = (
            replace(base, exception_key="c"),
            base,
            replace(
                base,
                exception_key="b",
                exception_type=BillingExceptionType.ZERO_OPERATION_TOTAL,
            ),
            exception(
                "other",
                BillingExceptionType.MISSING_EXPEDITION_DATE,
                operation_id="operation-2",
            ),
        )
        groups = find_multiple_findings(values)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].subject_id, "operation-1")
        self.assertEqual([item.exception_key for item in groups[0].exceptions], ["a", "b", "c"])

    def test_multiple_policy_scoped_types_are_grouped_without_mutation(self):
        values = (
            exception("b", BillingExceptionType.MISSING_CHARGE),
            exception("a", BillingExceptionType.MISSING_OPERATION),
        )
        before = tuple(values)
        groups = find_multiple_findings(values)
        self.assertEqual(groups[0].subject_type, "policy")
        self.assertEqual(values, before)

    def test_negative_sample_limit_is_rejected(self):
        with self.assertRaises(ValueError):
            sample_exceptions_per_type((), limit=-1)
