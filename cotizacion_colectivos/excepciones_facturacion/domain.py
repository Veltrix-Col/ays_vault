"""Contrato inmutable y catálogo de Excepciones de Facturación."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from urllib.parse import quote


class BillingExceptionType(str, Enum):
    MISSING_CHARGE = "MISSING_CHARGE"
    MISSING_OPERATION = "MISSING_OPERATION"
    MISSING_CERTIFICATE = "MISSING_CERTIFICATE"
    MISSING_EXPEDITION_DATE = "MISSING_EXPEDITION_DATE"
    ZERO_OPERATION_TOTAL = "ZERO_OPERATION_TOTAL"


class BillingExceptionSource(str, Enum):
    REVISION_FACTURACION = "revision_facturacion"
    COBROS_FALTANTES = "cobros_faltantes"


@dataclass(frozen=True)
class BillingExceptionDefinition:
    exception_type: BillingExceptionType
    functional_name: str
    source: BillingExceptionSource
    rule_code: str


EXCEPTION_CATALOG = (
    BillingExceptionDefinition(
        BillingExceptionType.MISSING_CHARGE,
        "Cobro faltante",
        BillingExceptionSource.COBROS_FALTANTES,
        "cobros_faltantes.expected_exceeds_effective",
    ),
    BillingExceptionDefinition(
        BillingExceptionType.MISSING_OPERATION,
        "Operación faltante",
        BillingExceptionSource.REVISION_FACTURACION,
        "revision_facturacion.missing_operation",
    ),
    BillingExceptionDefinition(
        BillingExceptionType.MISSING_CERTIFICATE,
        "Certificado faltante",
        BillingExceptionSource.REVISION_FACTURACION,
        "revision_facturacion.missing_certificate",
    ),
    BillingExceptionDefinition(
        BillingExceptionType.MISSING_EXPEDITION_DATE,
        "Fecha de expedición faltante",
        BillingExceptionSource.REVISION_FACTURACION,
        "revision_facturacion.missing_expedition_date",
    ),
    BillingExceptionDefinition(
        BillingExceptionType.ZERO_OPERATION_TOTAL,
        "Total de operación en cero",
        BillingExceptionSource.REVISION_FACTURACION,
        "revision_facturacion.zero_operation_total",
    ),
)

_DEFINITIONS = {item.exception_type: item for item in EXCEPTION_CATALOG}


@dataclass(frozen=True)
class BillingException:
    exception_key: str
    exception_type: BillingExceptionType
    source: BillingExceptionSource
    rule_code: str
    reason: str
    source_reference: str
    policy_number: str
    policy_id: str | None = None
    client_id: str | None = None
    client_name: str | None = None
    insurer: str | None = None
    branch: str | None = None
    analyst: str | None = None
    operation_id: str | None = None
    operation_name: str | None = None
    installment_number: int | None = None
    relevant_date: date | None = None
    billing_date: date | None = None
    context: tuple[tuple[str, str], ...] = ()

    @property
    def functional_name(self) -> str:
        return _DEFINITIONS[self.exception_type].functional_name


def build_exception_key(
    *,
    source: BillingExceptionSource,
    exception_type: BillingExceptionType,
    policy_id: str | None,
    policy_number: str,
    operation_id: str | None = None,
    installment_number: int | None = None,
    relevant_date: date | None = None,
    policy_scope: bool = False,
) -> str:
    """Construye identidad legible sin depender de orden, ejecución o mensajes."""

    policy_identity = f"id:{policy_id}" if policy_id else f"number:{policy_number}"
    if operation_id:
        subject = f"operation:{operation_id}"
    elif installment_number is not None and relevant_date is not None:
        subject = f"installment:{installment_number}@{relevant_date.isoformat()}"
    elif policy_scope:
        subject = "policy-gap"
    else:
        raise ValueError(
            "No hay identidad suficiente para construir la clave de la excepción."
        )
    components = (
        "billing-exception",
        "v1",
        source.value,
        exception_type.value,
        policy_identity,
        subject,
    )
    return "/".join(quote(component, safe="@:-") for component in components)
