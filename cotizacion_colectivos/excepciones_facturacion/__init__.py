"""Dominio calculado de Excepciones de Facturación."""

from .domain import (
    EXCEPTION_CATALOG,
    BillingException,
    BillingExceptionDefinition,
    BillingExceptionSource,
    BillingExceptionType,
)
from .service import (
    build_billing_exceptions,
    exceptions_from_cobros_faltantes,
    exceptions_from_revision_facturacion,
)
from .application import (
    BillingExceptionsOperationalError,
    BillingExceptionsResult,
    BillingExceptionsVolumes,
    get_billing_exceptions,
)
from .diagnostics import (
    MultipleFindingsGroup,
    find_multiple_findings,
    sample_exceptions_per_type,
)
from .cases import BillingCase, build_billing_cases, build_case_key, case_priority

__all__ = (
    "EXCEPTION_CATALOG",
    "BillingException",
    "BillingExceptionDefinition",
    "BillingExceptionSource",
    "BillingExceptionType",
    "BillingExceptionsOperationalError",
    "BillingExceptionsResult",
    "BillingExceptionsVolumes",
    "BillingCase",
    "MultipleFindingsGroup",
    "build_billing_exceptions",
    "build_billing_cases",
    "build_case_key",
    "case_priority",
    "exceptions_from_cobros_faltantes",
    "exceptions_from_revision_facturacion",
    "find_multiple_findings",
    "get_billing_exceptions",
    "sample_exceptions_per_type",
)
