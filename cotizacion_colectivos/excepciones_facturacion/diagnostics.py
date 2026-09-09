"""Selecciones diagnósticas puras sobre excepciones ya calculadas."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from .domain import BillingException, BillingExceptionType


@dataclass(frozen=True)
class MultipleFindingsGroup:
    """Hallazgos de tipos distintos sobre la misma operación o póliza."""

    subject_type: str
    subject_id: str
    policy_number: str
    exceptions: tuple[BillingException, ...]


def _stable_exceptions(
    exceptions: Iterable[BillingException],
) -> tuple[BillingException, ...]:
    return tuple(sorted(exceptions, key=lambda item: item.exception_key))


def sample_exceptions_per_type(
    exceptions: Iterable[BillingException],
    *,
    limit: int,
) -> tuple[BillingException, ...]:
    """Selecciona hasta ``limit`` hallazgos por tipo en orden estable."""

    if limit < 0:
        raise ValueError("limit no puede ser negativo.")
    by_type: dict[BillingExceptionType, list[BillingException]] = defaultdict(list)
    for exception in _stable_exceptions(exceptions):
        by_type[exception.exception_type].append(exception)
    return tuple(
        exception
        for exception_type in sorted(by_type, key=lambda item: item.value)
        for exception in by_type[exception_type][:limit]
    )


def find_multiple_findings(
    exceptions: Iterable[BillingException],
) -> tuple[MultipleFindingsGroup, ...]:
    """Agrupa tipos distintos por operación o, si no existe, por póliza."""

    grouped: dict[tuple[str, str], list[BillingException]] = defaultdict(list)
    for exception in _stable_exceptions(exceptions):
        if exception.operation_id:
            subject = ("operation", exception.operation_id)
        else:
            policy_identity = exception.policy_id or exception.policy_number
            subject = ("policy", policy_identity)
        grouped[subject].append(exception)

    result = []
    for (subject_type, subject_id), items in grouped.items():
        if len({item.exception_type for item in items}) < 2:
            continue
        stable_items = _stable_exceptions(items)
        result.append(
            MultipleFindingsGroup(
                subject_type=subject_type,
                subject_id=subject_id,
                policy_number=stable_items[0].policy_number,
                exceptions=stable_items,
            )
        )
    return tuple(
        sorted(
            result,
            key=lambda group: (
                group.subject_type,
                group.subject_id,
                group.policy_number,
            ),
        )
    )
