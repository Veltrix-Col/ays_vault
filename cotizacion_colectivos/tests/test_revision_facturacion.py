from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest import TestCase

from cotizacion_colectivos.block2.revision_facturacion import (
    REPORT_COLUMNS,
    aggregate_insured_by_policy,
    calculate_billing_date,
    compare_with_baseline,
    fetch_note_records,
    is_in_accumulated_current_month,
    is_pending_operation,
    reconstruct_revision_facturacion,
    select_relevant_operation_ids_for_notes,
    unpivot_payment_plan,
)


AS_OF = date(2026, 9, 7)
POLICY_ID = "4933790000200703052"


def policy(**overrides):
    value = {
        "id": POLICY_ID,
        "Name": "083005489038",
        "Key": "POL-KEY",
        "L_nea_de_negocio": "Colectivos",
        "Estado_de_la_p_liza": "Vigente",
        "Aseguradora1": {"id": "1", "name": "Aseguradora"},
        "Ramo": "Salud colectivo",
        "Tomador_principal1": {"id": "4933790000000000001", "name": "Tomador lookup"},
        "Modo_de_pago": "Fraccionado",
        "Frecuencia": "Mensual",
        "P_liza_Fecha_de_inicio_vigencia": "2026-08-01",
        "P_liza_Fecha_fin_de_la_vigencia": "2027-08-01",
        "Dia_facturaci_n": 1,
        "Tipo_de_facturaci_n": "Mes corriente",
        "Correo_facturaci_n": "facturacion@example.test",
    }
    value.update(overrides)
    return value


def operation(**overrides):
    value = {
        "id": "4933790000300000001",
        "P_liza": {"id": POLICY_ID, "name": "083005489038"},
        "Name": "Cobro 2",
        "Observaciones": "",
        "Certificado_Fecha_de_inicio_de_vigencia": "2026-09-01",
        "Fecha_de_expedici_n_de_p_liza": None,
        "N_mero_de_certificado": "0",
        "N_mero_de_certificado_Aseguradora": "21571091",
        "Total_a_pagar_en_OP": 0,
        "Saldo_cartera_aseguradora": None,
    }
    value.update(overrides)
    return value


def insured(state="Activo", payment=349762, insured_value=580000000):
    return {
        "id": f"risk-{state}",
        "P_liza": {"id": POLICY_ID, "name": "083005489038"},
        "Estado": state,
        "Pago_total": payment,
        "Valor_asegurado": insured_value,
        "Prima": 100,
    }


def reconstruct(policy_record=None, operations=(), risks=(), contacts=(), notes=()):
    return reconstruct_revision_facturacion(
        [policy_record or policy()], operations, risks, contacts, notes, as_of=AS_OF,
    )


class PaymentPlanTests(TestCase):
    def test_unpivot_fecha_1_through_fecha_12_and_ignore_nulls(self):
        record = policy(**{f"Fecha_{number}": f"2026-{number:02d}-01" for number in range(1, 13)})
        record["Fecha_6"] = None
        installments = unpivot_payment_plan(record)
        self.assertEqual([item.number for item in installments], [1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 12])
        self.assertEqual(installments[0].installment_date, date(2026, 1, 1))
        self.assertEqual(installments[-1].installment_date, date(2026, 12, 1))


class BillingDateTests(TestCase):
    def test_day_at_or_before_billing_day_for_all_types(self):
        self.assertEqual(calculate_billing_date("2026-09-01", 5, "Mes corriente"), date(2026, 9, 5))
        self.assertEqual(calculate_billing_date("2026-09-01", 5, "Mes vencido"), date(2026, 10, 5))
        self.assertEqual(calculate_billing_date("2026-09-01", 5, "Mes anticipado"), date(2026, 8, 5))

    def test_day_after_billing_day_for_all_types(self):
        self.assertEqual(calculate_billing_date("2026-09-20", 5, "Mes corriente"), date(2026, 10, 5))
        self.assertEqual(calculate_billing_date("2026-09-20", 5, "Mes vencido"), date(2026, 11, 5))
        self.assertEqual(calculate_billing_date("2026-09-20", 5, "Mes anticipado"), date(2026, 9, 5))

    def test_month_shift_crosses_year_boundary(self):
        self.assertEqual(calculate_billing_date("2026-12-20", 5, "Mes vencido"), date(2027, 2, 5))
        self.assertEqual(calculate_billing_date("2026-01-01", 5, "Mes anticipado"), date(2025, 12, 5))

    def test_invalid_day_is_not_silently_clamped(self):
        self.assertIsNone(calculate_billing_date("2026-02-01", 30, "Mes corriente"))


class RevisionReconstructionTests(TestCase):
    def test_audit_counts_separate_candidates_temporal_survivors_and_final_rows(self):
        current_prorroga = operation(
            id="4933790000300000002", Name="Modificación",
            Observaciones="Prórroga septiembre",
            Certificado_Fecha_de_inicio_de_vigencia="2026-09-20",
        )
        future_prorroga = operation(
            id="4933790000300000003", Name="Modificación",
            Observaciones="Prórroga octubre",
            Certificado_Fecha_de_inicio_de_vigencia="2026-10-20",
        )
        result = reconstruct(
            policy(Fecha_2="2026-09-01", Fecha_3="2026-10-01", Dia_facturaci_n=20),
            [current_prorroga, future_prorroga],
        )
        self.assertEqual(
            result.audit_counts,
            {
                "MISSING_OPERATION_CANDIDATES": 2,
                "MISSING_OPERATION_FINAL_ROWS": 1,
                "MISSING_OPERATION_TEMPORAL_SURVIVORS": 1,
                "PLAN_INSTALLMENT_CANDIDATES": 2,
                "PRORROGA_CANDIDATES": 2,
                "PRORROGA_FINAL_ROWS": 1,
                "PRORROGA_TEMPORAL_SURVIVORS": 1,
            },
        )

    def test_note_fetch_is_reduced_to_reportable_operations(self):
        reportable = operation(id="4933790000300000001")
        complete = operation(
            id="4933790000300000002",
            Fecha_de_expedici_n_de_p_liza="2026-09-02",
            N_mero_de_certificado="CERT-2",
            Total_a_pagar_en_OP=100,
        )
        current_prorroga = operation(
            id="4933790000300000003",
            Name="Modificación",
            Observaciones="Prórroga septiembre",
            Certificado_Fecha_de_inicio_de_vigencia="2026-09-20",
        )
        future_prorroga = operation(
            id="4933790000300000004",
            Name="Modificación",
            Observaciones="Prórroga octubre",
            Certificado_Fecha_de_inicio_de_vigencia="2026-10-20",
        )
        ids = select_relevant_operation_ids_for_notes(
            [policy(Fecha_2="2026-09-01", Dia_facturaci_n=20)],
            [reportable, complete, current_prorroga, future_prorroga],
            as_of=AS_OF,
        )
        self.assertEqual(
            ids,
            ("4933790000300000001", "4933790000300000003"),
        )

    def test_missing_operation_preserves_plan_installment(self):
        result = reconstruct(policy(Fecha_2="2026-09-01"))
        self.assertEqual(len(result.rows), 1)
        row = result.rows[0]
        self.assertEqual(row.values["Numero_Cuota"], 2)
        self.assertEqual(row.values["Nombre operación"], "")
        self.assertIn("MISSING_OPERATION", row.diagnostics)

    def test_previous_year_installment_is_excluded_even_if_billing_date_falls_in_current_year(self):
        result = reconstruct(
            policy(
                Fecha_2="2025-12-30",
                Dia_facturaci_n=17,
                Tipo_de_facturaci_n="Mes corriente",
            )
        )

        self.assertEqual(len(result.rows), 0)
        self.assertEqual(result.audit_counts["MISSING_OPERATION_CANDIDATES"], 1)
        self.assertEqual(result.audit_counts.get("MISSING_OPERATION_TEMPORAL_SURVIVORS", 0), 0)
        self.assertEqual(result.audit_counts.get("MISSING_OPERATION_FINAL_ROWS", 0), 0)

    def test_operation_with_exact_date_is_associated(self):
        result = reconstruct(policy(Fecha_2="2026-09-01"), [operation()])
        self.assertEqual(len(result.rows), 1)
        self.assertEqual(result.rows[0].values["Nombre operación"], "Cobro 2")
        self.assertEqual(result.rows[0].values["Fecha Op Inicio Vig"], date(2026, 9, 1))

    def test_normal_modification_does_not_enter_plan_branch(self):
        modification = operation(Name="Modificación", Observaciones="Cambio normal")
        result = reconstruct(policy(Fecha_2="2026-09-01"), [modification])
        self.assertEqual(len(result.rows), 1)
        self.assertEqual(result.rows[0].values["Nombre operación"], "")
        self.assertIn("MISSING_OPERATION", result.rows[0].diagnostics)

    def test_prorroga_enters_second_branch_case_insensitively_with_or_without_accent(self):
        for observations in ("Prorroga mes 2", "AJUSTE PRÓRROGA MES 2"):
            with self.subTest(observations=observations):
                result = reconstruct(
                    policy(Name="900000594834", Dia_facturaci_n=20),
                    [operation(Name="Modificación", Observaciones=observations, Certificado_Fecha_de_inicio_de_vigencia="2026-09-20")],
                )
                self.assertEqual(len(result.rows), 1)
                self.assertIsNone(result.rows[0].values["Numero_Cuota"])
                self.assertEqual(result.rows[0].values["Fecha_Facturacion"], date(2026, 9, 20))
                self.assertIn("PRORROGA", result.rows[0].diagnostics)
                self.assertIn("FUTURE_IN_CURRENT_MONTH", result.rows[0].diagnostics)

    def test_multiple_operations_preserve_sql_multiplicity(self):
        result = reconstruct(
            policy(Fecha_2="2026-09-01"),
            [operation(id="4933790000300000001"), operation(id="4933790000300000002", Name="Cobro alterno")],
        )
        self.assertEqual(len(result.rows), 2)
        self.assertTrue(all("MULTIPLE_OPERATIONS_FOR_INSTALLMENT" in row.diagnostics for row in result.rows))

    def test_multiple_notes_duplicate_operation_rows(self):
        notes = [
            {"id": "n1", "Parent_Id": {"id": "4933790000300000001"}, "Note_Content": "Nota 1"},
            {"id": "n2", "Parent_Id": {"id": "4933790000300000001"}, "Note_Content": "Nota 2"},
        ]
        result = reconstruct(policy(Fecha_2="2026-09-01"), [operation()], notes=notes)
        self.assertEqual([row.values["Nota_Op"] for row in result.rows], ["Nota 1", "Nota 2"])
        self.assertTrue(all("MULTIPLE_NOTES" in row.diagnostics for row in result.rows))

    def test_complete_operation_is_excluded(self):
        complete = operation(
            Fecha_de_expedici_n_de_p_liza="2026-09-02",
            N_mero_de_certificado="CERT-1",
            Total_a_pagar_en_OP=100,
        )
        result = reconstruct(policy(Fecha_2="2026-09-01"), [complete])
        self.assertEqual(result.rows, ())

    def test_pending_filter_is_an_or_and_string_zero_certificate_is_not_assumed_missing(self):
        complete = operation(
            Fecha_de_expedici_n_de_p_liza="2026-09-02",
            N_mero_de_certificado="0",
            Total_a_pagar_en_OP=100,
        )
        self.assertFalse(is_pending_operation(complete))
        self.assertTrue(is_pending_operation({**complete, "N_mero_de_certificado": None}))
        self.assertTrue(is_pending_operation({**complete, "Fecha_de_expedici_n_de_p_liza": None}))
        self.assertTrue(is_pending_operation({**complete, "Total_a_pagar_en_OP": "0"}))

    def test_month_filter_is_cumulative_and_does_not_filter_current_month_by_day(self):
        self.assertTrue(is_in_accumulated_current_month("2026-09-20", as_of=AS_OF))
        self.assertTrue(is_in_accumulated_current_month("2026-01-01", as_of=AS_OF))
        self.assertFalse(is_in_accumulated_current_month("2026-10-20", as_of=AS_OF))
        self.assertFalse(is_in_accumulated_current_month("2025-09-20", as_of=AS_OF))

    def test_manual_missing_operation_case_keeps_january_through_may(self):
        manual_policy_id = "4933790000257810063"
        record = policy(
            id=manual_policy_id, Name="091000800684", Estado_de_la_p_liza="Vencida",
            P_liza_Fecha_de_inicio_vigencia="2025-09-01",
            P_liza_Fecha_fin_de_la_vigencia="2026-09-01", Dia_facturaci_n=5,
            Fecha_5="2026-01-01", Fecha_6="2026-02-01", Fecha_7="2026-03-01",
            Fecha_8="2026-04-01", Fecha_9="2026-05-01", Fecha_10="2026-06-01",
        )
        completed_cobro_10 = operation(
            P_liza={"id": manual_policy_id, "name": "091000800684"},
            Name="Cobro 10",
            Certificado_Fecha_de_inicio_de_vigencia="2026-06-01",
            Fecha_de_expedici_n_de_p_liza="2026-06-02",
            N_mero_de_certificado="CERT-10",
            Total_a_pagar_en_OP=100,
        )
        result = reconstruct(record, [completed_cobro_10])
        self.assertEqual([row.values["Numero_Cuota"] for row in result.rows], [5, 6, 7, 8, 9])
        self.assertEqual(result.rows[0].values["Fecha_Facturacion"], date(2026, 1, 5))

    def test_risk_aggregate_and_difference_cartera(self):
        risks = [insured("Activo", 300000, 500000000), insured("Excluido con cobro", 49762, 80000000), insured("Excluido", 999, 999)]
        aggregate = aggregate_insured_by_policy(risks)[POLICY_ID]
        self.assertEqual(aggregate.payment_total, 349762)
        self.assertEqual(aggregate.insured_value_total, 580000000)
        result = reconstruct(policy(Fecha_2="2026-09-01"), [operation()], risks=risks)
        row = result.rows[0].values
        self.assertEqual(row["Pago total (Con IVA) Asegurados"], 349762)
        self.assertEqual(row["Total Valor Asegurado"], 580000000)
        self.assertEqual(row["Diferencia_Cartera"], 349762)

    def test_missing_insured_and_balance_produce_zero_difference_like_baseline(self):
        result = reconstruct(policy(Fecha_2="2026-09-01"), [operation()])
        row = result.rows[0]
        self.assertIsNone(row.values["Pago total (Con IVA) Asegurados"])
        self.assertIsNone(row.values["Saldo cartera aseguradora"])
        self.assertEqual(row.values["Diferencia_Cartera"], 0)
        self.assertIn("NO_INSURED_TOTAL", row.diagnostics)

    def test_plan_date_outside_policy_term_is_diagnostic_only(self):
        result = reconstruct(policy(Fecha_2="2026-01-01", Dia_facturaci_n=1))
        self.assertEqual(len(result.rows), 1)
        self.assertIn("PLAN_DATE_OUTSIDE_POLICY_TERM", result.rows[0].diagnostics)


class ComparisonTests(TestCase):
    def test_comparison_treats_blank_strings_and_none_as_equivalent_in_key_and_values(self):
        reconstructed = reconstruct(policy(Fecha_2="2026-09-01"))
        crm_row = reconstructed.rows[0]
        baseline = dict(crm_row.values)
        baseline["Fecha Op Inicio Vig"] = ""
        baseline["Fecha Expedición"] = ""
        comparison = compare_with_baseline([baseline], [crm_row])
        self.assertEqual(comparison.summary.matched_rows, 1)
        self.assertEqual(comparison.differences, ())

    def test_tag_normalization_ignores_separator_spaces_but_preserves_order(self):
        reconstructed = reconstruct(
            policy(Fecha_2="2026-09-01", Tag=[{"name": "Enviar contabilidad"}, {"name": "FE"}])
        )
        crm_row = reconstructed.rows[0]
        self.assertEqual(crm_row.values["Etiquetas"], "Enviar contabilidad,FE")
        equivalent = dict(crm_row.values)
        equivalent["Etiquetas"] = "Enviar contabilidad, FE"
        self.assertEqual(
            compare_with_baseline([equivalent], [crm_row]).summary.matched_rows,
            1,
        )
        reordered = dict(crm_row.values)
        reordered["Etiquetas"] = "FE,Enviar contabilidad"
        self.assertEqual(
            compare_with_baseline([reordered], [crm_row]).summary.different_rows,
            1,
        )

    def test_integer_installment_key_uses_plain_notation(self):
        reconstructed = reconstruct(
            policy(
                Fecha_10="2026-07-01",
                Fecha_11="2026-08-01",
                Fecha_12="2026-09-01",
            )
        )
        comparison = compare_with_baseline([], reconstructed.rows)
        keys = "\n".join(row["KEY"] for row in comparison.differences)
        for number in (10, 11, 12):
            self.assertIn(f'"{number}"', keys)
        self.assertNotIn("E+", keys)

    def test_comparison_preserves_duplicate_rows_as_a_multiset(self):
        reconstructed = reconstruct(policy(Fecha_2="2026-09-01"), [operation()])
        duplicate = reconstructed.rows[0]
        comparison = compare_with_baseline(
            [dict(duplicate.values), dict(duplicate.values)],
            [duplicate, duplicate],
        )
        self.assertEqual(comparison.summary.matched_rows, 2)
        self.assertEqual(comparison.summary.duplicate_keys, 1)
        self.assertEqual(comparison.differences, ())

    def test_comparison_is_multiset_based_and_reports_column_differences(self):
        reconstructed = reconstruct(policy(Fecha_2="2026-09-01"), [operation()])
        crm_row = reconstructed.rows[0]
        baseline = dict(crm_row.values)
        baseline["Observaciones"] = "Valor Analytics"
        comparison = compare_with_baseline([baseline], [crm_row])
        self.assertEqual(comparison.summary.different_rows, 1)
        self.assertEqual(comparison.differences[0]["DIFFERING_COLUMNS"], "Observaciones")

    def test_report_shape_keeps_all_twenty_nine_columns(self):
        reconstructed = reconstruct(policy(Fecha_2="2026-09-01"))
        self.assertEqual(tuple(reconstructed.rows[0].values), REPORT_COLUMNS)
        self.assertEqual(len(REPORT_COLUMNS), 29)


class FetchInstrumentationTests(TestCase):
    def test_note_batches_report_safe_context_without_printing_ids(self):
        calls = []
        messages = []

        class Coql:
            @staticmethod
            def execute(query, *, offset, limit):
                calls.append((query, offset, limit))
                return SimpleNamespace(records=(), more_records=False)

        zoho = SimpleNamespace(coql=Coql())
        operation_ids = [f"49337900003{number:08d}" for number in range(101)]
        fetch_note_records(zoho, operation_ids, progress=messages.append)

        self.assertEqual(len(calls), 2)
        self.assertIn("batch 1/2, filter=Parent_Id, values=100", messages[0])
        self.assertIn("batch 2/2, filter=Parent_Id, values=1", messages[2])
        self.assertFalse(any(operation_ids[0] in message for message in messages))
