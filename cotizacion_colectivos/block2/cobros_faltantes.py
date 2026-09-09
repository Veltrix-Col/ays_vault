"""Reconstruccion READ-only del informe legacy de cobros faltantes.

Este modulo separa deliberadamente la extraccion de Zoho del calculo. Las
funciones de negocio reciben diccionarios y no conocen Django ni realizan
operaciones remotas, de modo que la regla legacy pueda auditarse en pruebas.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from openpyxl import load_workbook


POLICY_MODULE = "Polizas"
OPERATIONS_MODULE = "Opeeraciones"
COQL_PAGE_SIZE = 200
COQL_IN_BATCH_SIZE = 100

POLICY_FIELDS = (
    "id",
    "Name",
    "Key",
    "L_nea_de_negocio",
    "Estado_de_la_p_liza",
    "Aseguradora1",
    "Ramo",
    "Tomador_principal1",
    "Analista",
    "L_der_Comercial",
    "Modo_de_pago",
    "Frecuencia",
    "Cambio_de_intermediario",
    "P_liza_Fecha_de_inicio_vigencia",
    "P_liza_Fecha_fin_de_la_vigencia",
    *(f"Fecha_{number}" for number in range(1, 13)),
)

OPERATION_FIELDS = (
    "id",
    "P_liza",
    "Name",
    "Observaciones",
    "Certificado_Fecha_de_inicio_de_vigencia",
    "Fecha_de_expedici_n_de_p_liza",
    "N_mero_de_certificado",
    "N_mero_de_certificado_Aseguradora",
    "Total_a_pagar_en_OP",
    "Saldo_cartera_aseguradora",
    "Modified_Time",
)

BASELINE_POLICY = "p.Poliza"
BASELINE_EFFECTIVE = "Cobros efectivos"
BASELINE_EXPECTED = "Cobros esperados"

OUTPUT_COLUMNS = (
    "POLIZA",
    "CRM_EFECTIVOS",
    "CRM_ESPERADOS",
    "ANALYTICS_EFECTIVOS",
    "ANALYTICS_ESPERADOS",
    "MATCH_EFECTIVOS",
    "MATCH_ESPERADOS",
    "MATCH_TOTAL",
    "DIFERENCIA_CRM",
    "ANOMALIAS",
)

_EXCLUDED_PAYMENT_MODES = {"contado", "financiado"}
_EXCLUDED_BRANCHES = {"arl", "renta educativa", "renta pensional"}
_VALID_STATES = {"vigente", "vencida"}
_INSTALLMENT_NUMBER = re.compile(r"\bcobro\s*(\d+)\b", re.IGNORECASE)
_ZOHO_ID = re.compile(r"\d{10,30}\Z")


@dataclass(frozen=True)
class BaselineRow:
    policy_number: str
    effective: int
    expected: int


@dataclass(frozen=True)
class LegacyPolicyResult:
    policy_number: str
    policy_id: str
    expected_total: int
    effective_cobros: int
    effective_prorrogas: int
    effective_total: int
    difference: int
    anomalies: tuple[str, ...]
    missing_expected_installments: tuple[str, ...] = ()


@dataclass(frozen=True)
class ValidationSummary:
    baseline_rows: int
    matched: int
    different: int
    missing_in_crm: int
    duplicate_policy_records: int
    policies_with_anomalies: int


def _plain_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, Mapping):
        value = value.get("name") or value.get("Name") or value.get("value") or ""
    return str(value).strip()


def _fold(value: object) -> str:
    decomposed = unicodedata.normalize("NFKD", _plain_text(value))
    return "".join(character for character in decomposed if not unicodedata.combining(character)).casefold()


def _to_date(value: object) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _policy_number(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, Decimal) and value == value.to_integral_value():
        return str(int(value))
    text = str(value).strip()
    return text[:-2] if text.endswith(".0") and text[:-2].isdigit() else text


def _count(value: object, *, column: str, row_number: int) -> int:
    if value in (None, ""):
        raise ValueError(f"Fila {row_number}: {column} esta vacio.")
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Fila {row_number}: {column} no es numerico.") from exc
    if number != number.to_integral_value() or number < 0:
        raise ValueError(f"Fila {row_number}: {column} debe ser un entero no negativo.")
    return int(number)


def _normalized_header(value: object) -> str:
    return _fold(value).replace(" ", "")


def read_baseline(path: str | Path) -> list[BaselineRow]:
    """Lee el primer worksheet sin modificar el libro ni recalcular formulas."""
    baseline_path = Path(path)
    workbook = load_workbook(baseline_path, read_only=True, data_only=True)
    try:
        worksheet = workbook.active
        rows = worksheet.iter_rows(values_only=True)
        try:
            headers = next(rows)
        except StopIteration as exc:
            raise ValueError("El baseline no contiene encabezados.") from exc
        indexes = {_normalized_header(header): index for index, header in enumerate(headers)}
        required = {
            BASELINE_POLICY: _normalized_header("p.Poliza"),
            BASELINE_EFFECTIVE: _normalized_header(BASELINE_EFFECTIVE),
            BASELINE_EXPECTED: _normalized_header(BASELINE_EXPECTED),
        }
        missing = [display for display, key in required.items() if key not in indexes]
        if missing:
            raise ValueError(f"Faltan columnas requeridas: {', '.join(missing)}.")

        result: list[BaselineRow] = []
        for row_number, row in enumerate(rows, start=2):
            policy = _policy_number(row[indexes[required[BASELINE_POLICY]]])
            effective_value = row[indexes[required[BASELINE_EFFECTIVE]]]
            expected_value = row[indexes[required[BASELINE_EXPECTED]]]
            if not policy and effective_value in (None, "") and expected_value in (None, ""):
                continue
            if not policy:
                raise ValueError(f"Fila {row_number}: p.Poliza esta vacio.")
            result.append(
                BaselineRow(
                    policy_number=policy,
                    effective=_count(effective_value, column=BASELINE_EFFECTIVE, row_number=row_number),
                    expected=_count(expected_value, column=BASELINE_EXPECTED, row_number=row_number),
                )
            )
        return result
    finally:
        workbook.close()


def _escape_coql(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _chunks(values: Sequence[str], size: int = COQL_IN_BATCH_SIZE) -> Iterator[Sequence[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _execute_paginated(zoho: Any, query: str) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    offset = 0
    while True:
        page = zoho.coql.execute(query, offset=offset, limit=COQL_PAGE_SIZE)
        records.extend(page.records)
        if not page.more_records:
            break
        offset += COQL_PAGE_SIZE
    return records


def fetch_policy_records(zoho: Any, policy_numbers: Iterable[str]) -> list[dict[str, object]]:
    """Consulta Polizas por Name, en lotes COQL de hasta cien valores."""
    unique = sorted({_policy_number(value) for value in policy_numbers if _policy_number(value)})
    records: list[dict[str, object]] = []
    selected = ", ".join(POLICY_FIELDS)
    for batch in _chunks(unique):
        values = ", ".join(f"'{_escape_coql(value)}'" for value in batch)
        query = f"select {selected} from {POLICY_MODULE} where Name in ({values})"
        records.extend(_execute_paginated(zoho, query))
    return records


def _lookup_id(value: object) -> str:
    if isinstance(value, Mapping):
        return str(value.get("id") or value.get("ID") or "").strip()
    return str(value or "").strip()


def _record_iter(value: Iterable[Mapping[str, object]] | Any) -> Iterable[Mapping[str, object]]:
    """Acepta tanto secuencias de mappings como un DataFrame de pandas."""
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            records = to_dict(orient="records")
        except TypeError:
            records = None
        if isinstance(records, list):
            return records
    return value


def fetch_operation_records(zoho: Any, policy_ids: Iterable[str]) -> list[dict[str, object]]:
    """Consulta Opeeraciones por el lookup P_liza y conserva el lookup e id."""
    supplied = {str(value).strip() for value in policy_ids if str(value).strip()}
    invalid = sorted(value for value in supplied if not _ZOHO_ID.fullmatch(value))
    if invalid:
        raise ValueError("Zoho devolvio uno o mas IDs de poliza invalidos.")
    unique = sorted(supplied)
    records: list[dict[str, object]] = []
    selected = ", ".join(OPERATION_FIELDS)
    for batch in _chunks(unique):
        values = ", ".join(f"'{_escape_coql(value)}'" for value in batch)
        query = f"select {selected} from {OPERATIONS_MODULE} where P_liza in ({values})"
        records.extend(_execute_paginated(zoho, query))
    return records


def select_latest_policy_records(
    records: Iterable[Mapping[str, object]],
) -> tuple[dict[str, dict[str, object]], dict[str, int]]:
    """Selecciona la vigencia con inicio mas reciente para cada Name."""
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for raw in _record_iter(records):
        record = dict(raw)
        name = _policy_number(record.get("Name"))
        if name:
            grouped[name].append(record)

    selected: dict[str, dict[str, object]] = {}
    duplicates: dict[str, int] = {}
    for name, candidates in grouped.items():
        selected[name] = max(
            candidates,
            key=lambda item: _to_date(item.get("P_liza_Fecha_de_inicio_vigencia")) or date.min,
        )
        if len(candidates) > 1:
            duplicates[name] = len(candidates)
    return selected, duplicates


def is_legacy_candidate(policy: Mapping[str, object], *, as_of: date) -> bool:
    start = _to_date(policy.get("P_liza_Fecha_de_inicio_vigencia"))
    return (
        _fold(policy.get("Estado_de_la_p_liza")) in _VALID_STATES
        and _fold(policy.get("Modo_de_pago")) not in _EXCLUDED_PAYMENT_MODES
        and start is not None
        and start <= as_of
        and _fold(policy.get("Cambio_de_intermediario")) != "si"
        and _fold(policy.get("Ramo")) not in _EXCLUDED_BRANCHES
    )


def select_legacy_candidate_records(
    records: Iterable[Mapping[str, object]], *, as_of: date
) -> tuple[dict[str, dict[str, object]], dict[str, int]]:
    """Aplica los filtros del informe antes de elegir la ultima vigencia."""
    materialized = [dict(record) for record in _record_iter(records)]
    _all_selected, duplicates = select_latest_policy_records(materialized)
    eligible = [record for record in materialized if is_legacy_candidate(record, as_of=as_of)]
    selected, _eligible_duplicates = select_latest_policy_records(eligible)
    return selected, duplicates


def _operation_is_in_policy_term(
    operation: Mapping[str, object], *, start: date | None, end: date | None
) -> tuple[bool, date | None]:
    operation_date = _to_date(operation.get("Certificado_Fecha_de_inicio_de_vigencia"))
    return (
        operation_date is not None
        and start is not None
        and end is not None
        and start <= operation_date <= end,
        operation_date,
    )


def calculate_legacy_policy(
    policy: Mapping[str, object],
    operations: Iterable[Mapping[str, object]],
    *,
    as_of: date,
) -> LegacyPolicyResult:
    """Aplica literalmente la regla legacy, dejando anomalías como metadata."""
    start = _to_date(policy.get("P_liza_Fecha_de_inicio_vigencia"))
    end = _to_date(policy.get("P_liza_Fecha_fin_de_la_vigencia"))
    threshold = as_of + timedelta(days=14)
    anomalies: set[str] = set()

    all_plan_dates = [
        (f"Fecha_{number}", _to_date(policy.get(f"Fecha_{number}")))
        for number in range(1, 13)
    ]
    expected_dates = [(field, value) for field, value in all_plan_dates[1:] if value is not None and value <= threshold]
    expected_total = len(expected_dates)

    if start and any(value is not None and value < start for _, value in all_plan_dates):
        anomalies.add("PLAN_BEFORE_POLICY_START")
    if end and any(value is not None and value > end for _, value in all_plan_dates):
        anomalies.add("PLAN_AFTER_POLICY_END")
    date_counts = Counter(value for _, value in all_plan_dates if value is not None)
    if any(count > 1 for count in date_counts.values()):
        anomalies.add("DUPLICATE_EXPECTED_DATE")

    effective_cobros = 0
    effective_prorrogas = 0
    nominal_cobros: set[int] = set()
    operation_list = list(_record_iter(operations))
    for operation in operation_list:
        name = _plain_text(operation.get("Name"))
        folded_name = _fold(name)
        match = _INSTALLMENT_NUMBER.search(name)
        if match:
            nominal_cobros.add(int(match.group(1)))
        in_term, operation_date = _operation_is_in_policy_term(operation, start=start, end=end)
        if "cobro" in folded_name and in_term:
            effective_cobros += 1
            if operation_date and operation_date > as_of:
                anomalies.add("FUTURE_COBRO_COUNTED")
        if (
            folded_name == "modificacion"
            and "prorroga" in _fold(operation.get("Observaciones"))
            and in_term
        ):
            effective_prorrogas += 1

    missing = tuple(
        f"{field}/{value.isoformat()}"
        for field, value in expected_dates
        if int(field.split("_")[1]) not in nominal_cobros
    )
    if missing:
        anomalies.add("MISSING_EXPECTED_INSTALLMENT")
    if effective_prorrogas:
        anomalies.add("PRORROGA_COUNTED")

    effective_total = effective_cobros + effective_prorrogas
    return LegacyPolicyResult(
        policy_number=_policy_number(policy.get("Name")),
        policy_id=str(policy.get("id") or ""),
        expected_total=expected_total,
        effective_cobros=effective_cobros,
        effective_prorrogas=effective_prorrogas,
        effective_total=effective_total,
        difference=expected_total - effective_total,
        anomalies=tuple(sorted(anomalies)),
        missing_expected_installments=missing,
    )


def calculate_from_records(
    policies: Iterable[Mapping[str, object]],
    operations: Iterable[Mapping[str, object]],
    *,
    as_of: date,
) -> tuple[dict[str, LegacyPolicyResult], dict[str, int]]:
    selected, duplicates = select_legacy_candidate_records(policies, as_of=as_of)
    operations_by_policy: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for operation in _record_iter(operations):
        operations_by_policy[_lookup_id(operation.get("P_liza"))].append(operation)

    results: dict[str, LegacyPolicyResult] = {}
    for name, policy in selected.items():
        policy_id = str(policy.get("id") or "")
        result = calculate_legacy_policy(policy, operations_by_policy.get(policy_id, ()), as_of=as_of)
        if name in duplicates:
            result = replace(result, anomalies=tuple(sorted((*result.anomalies, "MULTIPLE_POLICY_RECORDS"))))
        results[name] = result
    return results, duplicates


def build_comparison(
    baseline: Iterable[BaselineRow],
    crm_results: Mapping[str, LegacyPolicyResult],
    *,
    duplicate_policy_records: Mapping[str, int] | None = None,
) -> tuple[list[dict[str, object]], ValidationSummary]:
    rows: list[dict[str, object]] = []
    matched = different = missing = 0
    for baseline_row in baseline:
        result = crm_results.get(baseline_row.policy_number)
        if result is None:
            missing += 1
            rows.append(
                {
                    "POLIZA": baseline_row.policy_number,
                    "CRM_EFECTIVOS": "",
                    "CRM_ESPERADOS": "",
                    "ANALYTICS_EFECTIVOS": baseline_row.effective,
                    "ANALYTICS_ESPERADOS": baseline_row.expected,
                    "MATCH_EFECTIVOS": False,
                    "MATCH_ESPERADOS": False,
                    "MATCH_TOTAL": False,
                    "DIFERENCIA_CRM": "",
                    "ANOMALIAS": "MISSING_IN_CRM",
                }
            )
            continue
        match_effective = result.effective_total == baseline_row.effective
        match_expected = result.expected_total == baseline_row.expected
        match_total = match_effective and match_expected
        if match_total:
            matched += 1
        else:
            different += 1
        rows.append(
            {
                "POLIZA": baseline_row.policy_number,
                "CRM_EFECTIVOS": result.effective_total,
                "CRM_ESPERADOS": result.expected_total,
                "ANALYTICS_EFECTIVOS": baseline_row.effective,
                "ANALYTICS_ESPERADOS": baseline_row.expected,
                "MATCH_EFECTIVOS": match_effective,
                "MATCH_ESPERADOS": match_expected,
                "MATCH_TOTAL": match_total,
                "DIFERENCIA_CRM": result.difference,
                "ANOMALIAS": ";".join(result.anomalies),
            }
        )
    baseline_count = len(rows)
    summary = ValidationSummary(
        baseline_rows=baseline_count,
        matched=matched,
        different=different,
        missing_in_crm=missing,
        duplicate_policy_records=len(duplicate_policy_records or {}),
        policies_with_anomalies=sum(bool(result.anomalies) for result in crm_results.values()),
    )
    return rows, summary


def write_comparison_csv(path: str | Path, rows: Iterable[Mapping[str, object]]) -> None:
    with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
