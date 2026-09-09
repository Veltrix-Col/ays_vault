"""Adaptadores puros desde los motores legacy hacia excepciones comunes."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re

from cotizacion_colectivos.block2.cobros_faltantes import LegacyPolicyResult
from cotizacion_colectivos.block2.revision_facturacion import (
    ReconstructionResult,
    RevisionRow,
)

from .domain import (
    EXCEPTION_CATALOG,
    BillingException,
    BillingExceptionDefinition,
    BillingExceptionSource,
    BillingExceptionType,
    build_exception_key,
)


_ZOHO_RECORD_ID = re.compile(r"\d{10,30}\Z")
_DEFINITIONS = {item.exception_type: item for item in EXCEPTION_CATALOG}


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _record_id_from_link(value: object) -> str | None:
    text = _optional_text(value)
    if not text:
        return None
    candidate = text.rstrip("/").rsplit("/", 1)[-1]
    return candidate if _ZOHO_RECORD_ID.fullmatch(candidate) else None


def _date_value(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _optional_text(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _installment_number(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return int(number) if number == number.to_integral_value() else None


def _is_zero(value: object) -> bool:
    if value in (None, "") or isinstance(value, bool):
        return False
    try:
        return Decimal(str(value).strip().replace(",", "")) == Decimal("0")
    except (InvalidOperation, ValueError):
        return False


def _context(*items: tuple[str, object]) -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted(
            (name, "" if value is None else str(value))
            for name, value in items
        )
    )


def _revision_exception(
    row: RevisionRow,
    exception_type: BillingExceptionType,
    *,
    policy_id: str | None,
    operation_id: str | None,
    installment_number: int | None,
    relevant_date: date | None,
) -> BillingException:
    values = row.values
    policy_number = str(values.get("Póliza") or "").strip()
    if not policy_number:
        raise ValueError("La fila de Revisión de Facturación no contiene Póliza.")
    definition = _DEFINITIONS[exception_type]
    if exception_type is BillingExceptionType.MISSING_OPERATION:
        if installment_number is None or relevant_date is None:
            raise ValueError(
                "La operación faltante no contiene cuota y fecha suficientes "
                "para construir una identidad estable."
            )
        reason = "No existe una operación asociada a la cuota esperada."
        source_reference = (
            f"Polizas:{policy_id or policy_number}:"
            f"Fecha_{installment_number}:{relevant_date.isoformat()}"
        )
    else:
        if not operation_id:
            raise ValueError(
                "La operación existe, pero su ID no está disponible en Link_Op."
            )
        reasons = {
            BillingExceptionType.MISSING_CERTIFICATE:
                "La operación no tiene certificado.",
            BillingExceptionType.MISSING_EXPEDITION_DATE:
                "La operación no tiene fecha de expedición.",
            BillingExceptionType.ZERO_OPERATION_TOTAL:
                "El total a pagar de la operación es cero.",
        }
        reason = reasons[exception_type]
        source_reference = f"Opeeraciones:{operation_id}"
    return BillingException(
        exception_key=build_exception_key(
            source=definition.source,
            exception_type=exception_type,
            policy_id=policy_id,
            policy_number=policy_number,
            operation_id=operation_id,
            installment_number=installment_number,
            relevant_date=relevant_date,
        ),
        exception_type=exception_type,
        source=definition.source,
        rule_code=definition.rule_code,
        reason=reason,
        source_reference=source_reference,
        policy_id=policy_id,
        policy_number=policy_number,
        client_name=_optional_text(values.get("Nombre_Tomador")),
        insurer=_optional_text(values.get("Aseguradora")),
        branch=_optional_text(values.get("Ramo")),
        operation_id=operation_id,
        operation_name=_optional_text(values.get("Nombre operación")),
        installment_number=installment_number,
        relevant_date=relevant_date,
        billing_date=_date_value(values.get("Fecha_Facturacion")),
        context=_context(
            ("diagnostics", ";".join(row.diagnostics)),
            ("policy_link", values.get("Link_Poliza")),
            ("operation_link", values.get("Link_Op")),
            ("observations", values.get("Observaciones")),
            ("note", values.get("Nota_Op")),
        ),
    )


def _revision_row_exceptions(row: RevisionRow) -> tuple[BillingException, ...]:
    values = row.values
    policy_id = _record_id_from_link(values.get("Link_Poliza"))
    operation_id = _record_id_from_link(values.get("Link_Op"))
    installment_number = _installment_number(values.get("Numero_Cuota"))
    installment_date = _date_value(values.get("Fecha_Cuota"))
    operation_date = _date_value(values.get("Fecha Op Inicio Vig"))
    relevant_date = operation_date or installment_date

    if "MISSING_OPERATION" in row.diagnostics:
        return (
            _revision_exception(
                row,
                BillingExceptionType.MISSING_OPERATION,
                policy_id=policy_id,
                operation_id=None,
                installment_number=installment_number,
                relevant_date=relevant_date,
            ),
        )

    active_types: list[BillingExceptionType] = []
    if not _optional_text(values.get("Certificado (recibo, documento, anexo)")):
        active_types.append(BillingExceptionType.MISSING_CERTIFICATE)
    if _date_value(values.get("Fecha Expedición")) is None:
        active_types.append(BillingExceptionType.MISSING_EXPEDITION_DATE)
    if _is_zero(values.get("Total a pagar en OP")):
        active_types.append(BillingExceptionType.ZERO_OPERATION_TOTAL)
    return tuple(
        _revision_exception(
            row,
            exception_type,
            policy_id=policy_id,
            operation_id=operation_id,
            installment_number=installment_number,
            relevant_date=relevant_date,
        )
        for exception_type in active_types
    )


def _deduplicate(
    exceptions: Iterable[BillingException],
) -> tuple[BillingException, ...]:
    """Deduplica únicamente la misma identidad calculada dentro de su fuente."""

    by_key: dict[str, BillingException] = {}
    for exception in exceptions:
        current = by_key.get(exception.exception_key)
        if current is None or repr(exception) < repr(current):
            by_key[exception.exception_key] = exception
    return tuple(by_key[key] for key in sorted(by_key))


def exceptions_from_revision_facturacion(
    result: ReconstructionResult,
) -> tuple[BillingException, ...]:
    return _deduplicate(
        exception
        for row in result.rows
        for exception in _revision_row_exceptions(row)
    )


def _missing_charge_exception(result: LegacyPolicyResult) -> BillingException:
    definition: BillingExceptionDefinition = _DEFINITIONS[
        BillingExceptionType.MISSING_CHARGE
    ]
    policy_id = _optional_text(result.policy_id)
    policy_number = str(result.policy_number or "").strip()
    if not policy_number:
        raise ValueError("El resultado de Cobros Faltantes no contiene póliza.")
    return BillingException(
        exception_key=build_exception_key(
            source=definition.source,
            exception_type=definition.exception_type,
            policy_id=policy_id,
            policy_number=policy_number,
            policy_scope=True,
        ),
        exception_type=definition.exception_type,
        source=definition.source,
        rule_code=definition.rule_code,
        reason=(
            f"Hay {result.difference} cobro(s) esperado(s) más que cobros efectivos."
        ),
        source_reference=f"Polizas:{policy_id or policy_number}:cobros_faltantes",
        policy_id=policy_id,
        policy_number=policy_number,
        context=_context(
            ("expected_total", result.expected_total),
            ("effective_total", result.effective_total),
            ("effective_cobros", result.effective_cobros),
            ("effective_prorrogas", result.effective_prorrogas),
            ("difference", result.difference),
            ("anomalies", ";".join(result.anomalies)),
            (
                "missing_expected_installments",
                ";".join(result.missing_expected_installments),
            ),
        ),
    )


def exceptions_from_cobros_faltantes(
    results: Mapping[str, LegacyPolicyResult] | Iterable[LegacyPolicyResult],
) -> tuple[BillingException, ...]:
    values = results.values() if isinstance(results, Mapping) else results
    return _deduplicate(
        _missing_charge_exception(result)
        for result in values
        if result.difference > 0
    )


def build_billing_exceptions(
    revision_result: ReconstructionResult,
    cobros_faltantes_results: (
        Mapping[str, LegacyPolicyResult] | Iterable[LegacyPolicyResult]
    ),
) -> tuple[BillingException, ...]:
    """Combina fuentes sin asumir equivalencias funcionales entre ellas."""

    return _deduplicate(
        (
            *exceptions_from_revision_facturacion(revision_result),
            *exceptions_from_cobros_faltantes(cobros_faltantes_results),
        )
    )
