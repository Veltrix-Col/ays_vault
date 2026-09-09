from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import date
from unittest import TestCase

from cotizacion_colectivos.block2.cobros_faltantes import LegacyPolicyResult
from cotizacion_colectivos.block2.revision_facturacion import (
    ReconstructionResult,
    RevisionRow,
    reconstruct_revision_facturacion,
)
from cotizacion_colectivos.excepciones_facturacion import (
    BillingExceptionSource,
    BillingExceptionType,
    build_billing_exceptions,
    exceptions_from_cobros_faltantes,
    exceptions_from_revision_facturacion,
)


AS_OF = date(2026, 9, 7)
POLICY_ID = "4933790000200703052"
OPERATION_ID = "4933790000300000001"


def policy(**overrides):
    value = {
        "id": POLICY_ID,
        "Name": "083005489038",
        "Key": "POL-KEY",
        "L_nea_de_negocio": "Colectivos",
        "Estado_de_la_p_liza": "Vigente",
        "Aseguradora1": {"id": "1", "name": "Aseguradora Uno"},
        "Ramo": "Salud colectivo",
        "Tomador_principal1": {"id": "4933790000000000001"},
        "Modo_de_pago": "Fraccionado",
        "Frecuencia": "Mensual",
        "P_liza_Fecha_de_inicio_vigencia": "2026-01-01",
        "P_liza_Fecha_fin_de_la_vigencia": "2026-12-31",
        "Dia_facturaci_n": 1,
        "Tipo_de_facturaci_n": "Mes corriente",
        "Fecha_2": "2026-09-01",
    }
    value.update(overrides)
    return value


def operation(**overrides):
    value = {
        "id": OPERATION_ID,
        "P_liza": {"id": POLICY_ID},
        "Name": "Cobro 2",
        "Observaciones": "Pendiente",
        "Certificado_Fecha_de_inicio_de_vigencia": "2026-09-01",
        "Fecha_de_expedici_n_de_p_liza": None,
        "N_mero_de_certificado": "CERT-2",
        "Total_a_pagar_en_OP": 100,
    }
    value.update(overrides)
    return value


def revision_result(*, operations=(), policy_record=None):
    return reconstruct_revision_facturacion(
        [policy_record or policy()],
        operations,
        (),
        ({"id": "4933790000000000001", "Full_Name": "Cliente Uno"},),
        (),
        as_of=AS_OF,
    )


def cobros_result(**overrides):
    values = {
        "policy_number": "083005489038",
        "policy_id": POLICY_ID,
        "expected_total": 4,
        "effective_cobros": 3,
        "effective_prorrogas": 0,
        "effective_total": 3,
        "difference": 1,
        "anomalies": ("MISSING_EXPECTED_INSTALLMENT",),
        "missing_expected_installments": ("Fecha_4/2026-08-01",),
    }
    values.update(overrides)
    return LegacyPolicyResult(**values)


def copied_result(result: ReconstructionResult, *rows: RevisionRow):
    return replace(result, rows=tuple(rows))


class BillingExceptionDomainTests(TestCase):
    def test_transforms_revision_row_with_available_domain_fields(self):
        exceptions = exceptions_from_revision_facturacion(
            revision_result(operations=(operation(),))
        )
        self.assertEqual(len(exceptions), 1)
        item = exceptions[0]
        self.assertEqual(item.exception_type, BillingExceptionType.MISSING_EXPEDITION_DATE)
        self.assertEqual(item.policy_id, POLICY_ID)
        self.assertEqual(item.operation_id, OPERATION_ID)
        self.assertEqual(item.client_name, "Cliente Uno")
        self.assertEqual(item.billing_date, AS_OF.replace(day=1))

    def test_transforms_cobros_faltantes_as_policy_level_gap(self):
        item = exceptions_from_cobros_faltantes({"policy": cobros_result()})[0]
        self.assertEqual(item.exception_type, BillingExceptionType.MISSING_CHARGE)
        self.assertEqual(item.source, BillingExceptionSource.COBROS_FALTANTES)
        self.assertIsNone(item.installment_number)
        self.assertIn(("difference", "1"), item.context)
        self.assertIn(("effective_prorrogas", "0"), item.context)

    def test_operation_with_one_reason_produces_one_exception(self):
        exceptions = exceptions_from_revision_facturacion(
            revision_result(operations=(operation(),))
        )
        self.assertEqual(
            [item.exception_type for item in exceptions],
            [BillingExceptionType.MISSING_EXPEDITION_DATE],
        )

    def test_operation_with_multiple_reasons_produces_one_exception_per_finding(self):
        result = revision_result(
            operations=(
                operation(
                    N_mero_de_certificado=None,
                    Fecha_de_expedici_n_de_p_liza=None,
                    Total_a_pagar_en_OP=0,
                ),
            )
        )
        self.assertEqual(
            {item.exception_type for item in exceptions_from_revision_facturacion(result)},
            {
                BillingExceptionType.MISSING_CERTIFICATE,
                BillingExceptionType.MISSING_EXPEDITION_DATE,
                BillingExceptionType.ZERO_OPERATION_TOTAL,
            },
        )

    def test_absent_operation_only_produces_missing_operation(self):
        exceptions = exceptions_from_revision_facturacion(revision_result())
        self.assertEqual(len(exceptions), 1)
        self.assertEqual(exceptions[0].exception_type, BillingExceptionType.MISSING_OPERATION)
        self.assertIsNone(exceptions[0].operation_id)

    def test_exception_key_does_not_depend_on_reason_text(self):
        item = exceptions_from_revision_facturacion(
            revision_result(operations=(operation(),))
        )[0]
        self.assertEqual(item.exception_key, replace(item, reason="Otro texto").exception_key)

    def test_equal_executions_generate_equal_identity(self):
        first = exceptions_from_revision_facturacion(
            revision_result(operations=(operation(),))
        )
        second = exceptions_from_revision_facturacion(
            revision_result(operations=(operation(),))
        )
        self.assertEqual(first, second)

    def test_two_installments_of_same_type_do_not_collide(self):
        result = revision_result(
            policy_record=policy(Fecha_2="2026-08-01", Fecha_3="2026-09-01")
        )
        exceptions = exceptions_from_revision_facturacion(result)
        self.assertEqual(len(exceptions), 2)
        self.assertEqual(len({item.exception_key for item in exceptions}), 2)

    def test_two_exception_types_on_same_operation_do_not_collide(self):
        result = revision_result(
            operations=(operation(Fecha_de_expedici_n_de_p_liza=None, Total_a_pagar_en_OP=0),)
        )
        exceptions = exceptions_from_revision_facturacion(result)
        self.assertEqual(len(exceptions), 2)
        self.assertEqual(len({item.exception_key for item in exceptions}), 2)

    def test_prorroga_uses_operation_identity_and_operation_date(self):
        prorroga = operation(
            Name="Modificación",
            Observaciones="Prórroga septiembre",
            Certificado_Fecha_de_inicio_de_vigencia="2026-09-20",
            Fecha_de_expedici_n_de_p_liza=None,
        )
        exceptions = exceptions_from_revision_facturacion(
            revision_result(operations=(prorroga,), policy_record=policy(Fecha_2=None, Dia_facturaci_n=20))
        )
        self.assertEqual(len(exceptions), 1)
        self.assertEqual(exceptions[0].operation_id, OPERATION_ID)
        self.assertIsNone(exceptions[0].installment_number)
        self.assertEqual(exceptions[0].relevant_date, date(2026, 9, 20))

    def test_policy_present_in_both_engines_preserves_both_sources(self):
        combined = build_billing_exceptions(revision_result(), [cobros_result()])
        self.assertEqual(len(combined), 2)
        self.assertEqual({item.source for item in combined}, set(BillingExceptionSource))

    def test_duplicate_source_rows_with_same_identity_are_safely_deduplicated(self):
        result = revision_result(operations=(operation(),))
        duplicate = copied_result(result, result.rows[0], result.rows[0])
        self.assertEqual(len(exceptions_from_revision_facturacion(duplicate)), 1)

    def test_ambiguous_overlap_between_engines_is_not_deduplicated(self):
        combined = build_billing_exceptions(revision_result(), [cobros_result()])
        self.assertEqual(
            {item.exception_type for item in combined},
            {BillingExceptionType.MISSING_OPERATION, BillingExceptionType.MISSING_CHARGE},
        )

    def test_optional_context_fields_may_be_absent(self):
        row = RevisionRow(
            values={
                "Póliza": "POL-SIN-CONTEXTO",
                "Numero_Cuota": 2,
                "Fecha_Cuota": date(2026, 9, 1),
            },
            diagnostics=("MISSING_OPERATION",),
        )
        base = revision_result()
        item = exceptions_from_revision_facturacion(copied_result(base, row))[0]
        self.assertIsNone(item.policy_id)
        self.assertIsNone(item.client_name)
        self.assertIsNone(item.insurer)
        self.assertIsNone(item.operation_id)

    def test_transformation_does_not_mutate_engine_results(self):
        revision = revision_result(operations=(operation(),))
        cobros = {"policy": cobros_result()}
        revision_values = deepcopy(revision.rows[0].values)
        cobros_before = dict(cobros)
        build_billing_exceptions(revision, cobros)
        self.assertEqual(revision.rows[0].values, revision_values)
        self.assertEqual(cobros, cobros_before)
