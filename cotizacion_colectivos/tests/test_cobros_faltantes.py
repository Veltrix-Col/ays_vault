from __future__ import annotations

from datetime import date
from tempfile import TemporaryDirectory
from unittest import TestCase

import pandas as pd
from openpyxl import Workbook

from cotizacion_colectivos.block2.cobros_faltantes import (
    COQL_IN_BATCH_SIZE,
    COQL_PAGE_SIZE,
    calculate_from_records,
    calculate_legacy_policy,
    fetch_operation_records,
    fetch_policy_records,
    read_baseline,
    select_legacy_candidate_records,
    select_latest_policy_records,
)


AS_OF = date(2026, 9, 7)


def policy(**overrides):
    value = {
        "id": "100",
        "Name": "POL-1",
        "Estado_de_la_p_liza": "Vigente",
        "Modo_de_pago": "Fraccionado",
        "Cambio_de_intermediario": "No",
        "Ramo": "Vida grupo",
        "P_liza_Fecha_de_inicio_vigencia": "2026-01-01",
        "P_liza_Fecha_fin_de_la_vigencia": "2026-12-31",
    }
    value.update(overrides)
    return value


def operation(name, operation_date, observations=""):
    return {
        "id": f"op-{name}-{operation_date}",
        "P_liza": {"id": "100", "name": "POL-1"},
        "Name": name,
        "Observaciones": observations,
        "Certificado_Fecha_de_inicio_de_vigencia": operation_date,
    }


class LegacyCobrosFaltantesTests(TestCase):
    def calculate(self, policy_record=None, operations=()):
        return calculate_legacy_policy(policy_record or policy(), operations, as_of=AS_OF)

    def test_fecha_1_does_not_count_as_expected(self):
        result = self.calculate(policy(Fecha_1="2026-09-01"))
        self.assertEqual(result.expected_total, 0)

    def test_fourteen_day_anticipation_window(self):
        result = self.calculate(
            policy(Fecha_2="2026-09-20", Fecha_3="2026-09-21", Fecha_4="2026-09-22")
        )
        self.assertEqual(result.expected_total, 2)

    def test_installment_exactly_as_of_plus_fourteen_days_counts(self):
        result = self.calculate(policy(Fecha_2="2026-09-21"))
        self.assertEqual(result.expected_total, 1)

    def test_installment_as_of_plus_fifteen_days_does_not_count(self):
        result = self.calculate(policy(Fecha_2="2026-09-22"))
        self.assertEqual(result.expected_total, 0)

    def test_future_cobro_inside_policy_term_counts_in_legacy(self):
        result = self.calculate(operations=[operation("Cobro 10", "2026-10-01")])
        self.assertEqual(result.effective_cobros, 1)
        self.assertIn("FUTURE_COBRO_COUNTED", result.anomalies)


class _Page:
    def __init__(self, records=(), more_records=False):
        self.records = tuple(records)
        self.more_records = more_records


class _Coql:
    def __init__(self, pages=None):
        self.calls = []
        self.pages = list(pages or [_Page()])

    def execute(self, query, *, offset, limit):
        self.calls.append({"query": query, "offset": offset, "limit": limit})
        return self.pages.pop(0)


class _Zoho:
    def __init__(self, pages=None):
        self.coql = _Coql(pages)


class ReadOnlyExtractionTests(TestCase):
    def test_policy_in_queries_use_batches_of_at_most_one_hundred(self):
        zoho = _Zoho([_Page(), _Page()])
        fetch_policy_records(zoho, (f"POL-{number}" for number in range(COQL_IN_BATCH_SIZE + 1)))
        self.assertEqual(len(zoho.coql.calls), 2)
        self.assertTrue(all(" from Polizas where Name in (" in call["query"] for call in zoho.coql.calls))
        self.assertTrue(all(call["limit"] == COQL_PAGE_SIZE for call in zoho.coql.calls))

    def test_coql_pages_use_offsets_of_two_hundred_until_more_records_is_false(self):
        zoho = _Zoho([_Page([{"id": "1"}], True), _Page([{"id": "2"}], False)])
        records = fetch_policy_records(zoho, ["POL-1"])
        self.assertEqual([call["offset"] for call in zoho.coql.calls], [0, COQL_PAGE_SIZE])
        self.assertEqual([record["id"] for record in records], ["1", "2"])

    def test_operations_query_uses_correct_module_and_policy_lookup(self):
        zoho = _Zoho()
        fetch_operation_records(zoho, ["1000000000000000001", "1000000000000000002"])
        query = zoho.coql.calls[0]["query"]
        self.assertIn(
            " from Opeeraciones where P_liza in ('1000000000000000001', '1000000000000000002')",
            query,
        )
        self.assertIn("select id, P_liza, Name", query)

    def test_baseline_reader_accepts_the_accented_headers_and_preserves_text_policy(self):
        with TemporaryDirectory() as directory:
            path = f"{directory}/baseline.xlsx"
            workbook = Workbook()
            worksheet = workbook.active
            worksheet.append(["p.Póliza", "Cobros efectivos", "Cobros esperados"])
            worksheet.append(["091000813197", 2, 8])
            workbook.save(path)
            workbook.close()
            rows = read_baseline(path)
        self.assertEqual(rows[0].policy_number, "091000813197")
        self.assertEqual((rows[0].effective, rows[0].expected), (2, 8))

    def test_calculation_accepts_dataframes_without_zoho(self):
        policy_frame = pd.DataFrame(
            [
                policy(
                    id="1000000000000000001",
                    Fecha_2="2026-09-01",
                )
            ]
        )
        operation_frame = pd.DataFrame(
            [
                {
                    **operation("Cobro 2", "2026-09-01"),
                    "P_liza": {"id": "1000000000000000001", "name": "POL-1"},
                }
            ]
        )
        results, duplicates = calculate_from_records(policy_frame, operation_frame, as_of=AS_OF)
        self.assertEqual(results["POL-1"].effective_total, 1)
        self.assertEqual(results["POL-1"].expected_total, 1)
        self.assertEqual(duplicates, {})


class LegacyCobrosFaltantesAdditionalTests(TestCase):
    def calculate(self, policy_record=None, operations=()):
        return calculate_legacy_policy(policy_record or policy(), operations, as_of=AS_OF)

    def test_cobro_before_policy_start_does_not_count(self):
        result = self.calculate(operations=[operation("Cobro 1", "2025-12-31")])
        self.assertEqual(result.effective_total, 0)

    def test_cobro_after_policy_end_does_not_count(self):
        result = self.calculate(operations=[operation("Cobro 13", "2027-01-01")])
        self.assertEqual(result.effective_total, 0)

    def test_nueva_does_not_count_even_if_observations_say_cobro(self):
        result = self.calculate(operations=[operation("Nueva", "2026-02-01", "cobro 1")])
        self.assertEqual(result.effective_total, 0)

    def test_name_containing_cobro_counts(self):
        result = self.calculate(operations=[operation("Ajuste Cobro especial", "2026-02-01")])
        self.assertEqual(result.effective_cobros, 1)

    def test_prorroga_without_accent_counts(self):
        result = self.calculate(operations=[operation("Modificación", "2026-02-01", "Prorroga acordada")])
        self.assertEqual(result.effective_prorrogas, 1)
        self.assertIn("PRORROGA_COUNTED", result.anomalies)

    def test_prorroga_with_accent_counts(self):
        result = self.calculate(operations=[operation("Modificación", "2026-02-01", "PRÓRROGA")])
        self.assertEqual(result.effective_prorrogas, 1)

    def test_latest_policy_term_is_selected_for_duplicate_name(self):
        selected, duplicates = select_latest_policy_records(
            [
                policy(id="old", Name="DUP", P_liza_Fecha_de_inicio_vigencia="2025-01-01"),
                policy(id="new", Name="DUP", P_liza_Fecha_de_inicio_vigencia="2026-01-01"),
            ]
        )
        self.assertEqual(selected["DUP"]["id"], "new")
        self.assertEqual(duplicates, {"DUP": 2})

    def test_candidate_filters_run_before_latest_term_selection(self):
        selected, duplicates = select_legacy_candidate_records(
            [
                policy(id="eligible", Name="DUP", P_liza_Fecha_de_inicio_vigencia="2026-01-01"),
                policy(id="future", Name="DUP", P_liza_Fecha_de_inicio_vigencia="2027-01-01"),
            ],
            as_of=AS_OF,
        )
        self.assertEqual(selected["DUP"]["id"], "eligible")
        self.assertEqual(duplicates, {"DUP": 2})

    def test_golden_case_153927(self):
        record = policy(
            Name="153927",
            P_liza_Fecha_de_inicio_vigencia="2026-02-12",
            P_liza_Fecha_fin_de_la_vigencia="2027-01-01",
        )
        record.update(
            Fecha_1="2026-02-12", Fecha_2="2026-03-12", Fecha_3="2026-04-12",
            Fecha_4="2026-05-12", Fecha_5="2026-06-12", Fecha_6="2026-07-12",
            Fecha_7="2026-08-12", Fecha_8="2026-09-12",
        )
        operations = [
            operation("Nueva", "2026-02-12"), operation("Cobro 2", "2026-03-12"),
            operation("Cobro 3", "2026-04-12"), operation("Cobro 4", "2026-05-12"),
            operation("Cobro 5", "2026-06-12"), operation("Cobro 6", "2026-07-12"),
            operation("Cobro 8", "2026-09-12"),
        ]
        result = self.calculate(record, operations)
        self.assertEqual((result.expected_total, result.effective_total, result.difference), (7, 6, 1))
        self.assertEqual(result.missing_expected_installments, ("Fecha_7/2026-08-12",))

    def test_golden_case_091000813197(self):
        record = policy(
            Name="091000813197",
            P_liza_Fecha_de_inicio_vigencia="2026-08-01",
            P_liza_Fecha_fin_de_la_vigencia="2027-08-01",
            Fecha_1="2026-08-01", Fecha_2="2026-02-01", Fecha_3="2026-03-01",
            Fecha_4="2026-04-01", Fecha_5="2026-05-01", Fecha_6="2026-06-01",
            Fecha_7="2026-07-01", Fecha_8="2026-08-01", Fecha_9="2026-09-01",
            Fecha_10="2026-10-01",
        )
        operations = [operation(f"Cobro {number}", f"2026-{number:02d}-01") for number in range(2, 9)]
        operations.extend([operation("Renovación", "2026-08-01"), operation("Cobro 10", "2026-10-01")])
        result = self.calculate(record, operations)
        self.assertEqual((result.expected_total, result.effective_total), (8, 2))
        self.assertIn("PLAN_BEFORE_POLICY_START", result.anomalies)
        self.assertIn("FUTURE_COBRO_COUNTED", result.anomalies)

    def test_golden_case_34220_391006(self):
        record = policy(
            Name="34220-391006",
            P_liza_Fecha_de_inicio_vigencia="2026-05-01",
            Fecha_1="2026-05-01", Fecha_2="2026-06-01", Fecha_3="2026-07-01",
            Fecha_4="2026-08-01", Fecha_5="2026-09-01", Fecha_6="2026-10-01",
        )
        result = self.calculate(
            record,
            [operation("Nueva", "2026-05-01"), operation("Cobro 6", "2026-10-01")],
        )
        self.assertEqual((result.expected_total, result.effective_total, result.difference), (4, 1, 3))
        self.assertIn("FUTURE_COBRO_COUNTED", result.anomalies)
    read_baseline,
