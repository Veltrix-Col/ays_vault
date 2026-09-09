"""Agrupación determinística de hallazgos técnicos en casos operativos.

Reglas de identidad ``billing-case/v1``:

* hallazgo con operación: póliza + operation_id;
* MISSING_OPERATION: póliza + número de cuota + fecha relevante;
* MISSING_CHARGE: póliza + marcador estable policy-gap.

MISSING_OPERATION y MISSING_CHARGE nunca comparten caso.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote

from .domain import BillingException, BillingExceptionType


@dataclass(frozen=True)
class BillingCase:
    case_key: str
    kind: str
    policy_id: str | None
    policy_number: str
    client_name: str | None
    insurer: str | None
    branch: str | None
    analyst: str | None
    operation_id: str | None
    operation_name: str | None
    installment_number: int | None
    relevant_date: date | None
    billing_date: date | None
    priority: int
    exceptions: tuple[BillingException, ...]

    @property
    def is_multiple(self) -> bool:
        return len(self.exceptions) > 1


def _policy_identity(exception: BillingException) -> str:
    return f"id:{exception.policy_id}" if exception.policy_id else f"number:{exception.policy_number}"


def build_case_key(exception: BillingException) -> tuple[str, str]:
    policy = _policy_identity(exception)
    if exception.operation_id:
        kind, subject = "operation", f"operation:{exception.operation_id}"
    elif exception.exception_type is BillingExceptionType.MISSING_OPERATION:
        if exception.installment_number is None or exception.relevant_date is None:
            raise ValueError("MISSING_OPERATION no contiene cuota y fecha para construir case_key.")
        kind = "missing-operation"
        subject = f"installment:{exception.installment_number}@{exception.relevant_date.isoformat()}"
    elif exception.exception_type is BillingExceptionType.MISSING_CHARGE:
        kind, subject = "policy-gap", "policy-gap"
    else:
        raise ValueError("El hallazgo no contiene identidad suficiente para un caso operativo.")
    components = ("billing-case", "v1", kind, policy, subject)
    return "/".join(quote(item, safe="@:-") for item in components), kind


def case_priority(exceptions: Iterable[BillingException]) -> int:
    values = tuple(exceptions)
    if any(item.exception_type in {
        BillingExceptionType.MISSING_OPERATION,
        BillingExceptionType.MISSING_CHARGE,
    } for item in values):
        return 1
    if len(values) > 1:
        return 2
    return 3


def build_billing_cases(exceptions: Iterable[BillingException]) -> tuple[BillingCase, ...]:
    grouped: dict[tuple[str, str], list[BillingException]] = defaultdict(list)
    kinds: dict[str, str] = {}
    for exception in sorted(exceptions, key=lambda item: item.exception_key):
        key, kind = build_case_key(exception)
        grouped[(key, exception.policy_number)].append(exception)
        kinds[key] = kind

    cases = []
    for (key, policy_number), findings in grouped.items():
        stable = tuple(sorted(findings, key=lambda item: item.exception_key))
        representative = stable[0]
        dates = sorted(item.relevant_date for item in stable if item.relevant_date)
        billing_dates = sorted(item.billing_date for item in stable if item.billing_date)
        cases.append(BillingCase(
            case_key=key,
            kind=kinds[key],
            policy_id=representative.policy_id,
            policy_number=policy_number,
            client_name=next((item.client_name for item in stable if item.client_name), None),
            insurer=next((item.insurer for item in stable if item.insurer), None),
            branch=next((item.branch for item in stable if item.branch), None),
            analyst=next((item.analyst for item in stable if item.analyst), None),
            operation_id=representative.operation_id,
            operation_name=next((item.operation_name for item in stable if item.operation_name), None),
            installment_number=representative.installment_number,
            relevant_date=dates[0] if dates else None,
            billing_date=billing_dates[0] if billing_dates else None,
            priority=case_priority(stable),
            exceptions=stable,
        ))
    return tuple(sorted(cases, key=lambda item: (
        item.priority,
        item.relevant_date or date.max,
        item.policy_number,
        item.case_key,
    )))
