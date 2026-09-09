"""Orquestación operacional READ-only de Excepciones de Facturación."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date
import logging
import time
from typing import Any, TypeVar

from django.conf import settings

from integrations.zoho import get_zoho
from integrations.zoho.exceptions import ZohoError

from cotizacion_colectivos.block2.cobros_faltantes import (
    COQL_PAGE_SIZE,
    _escape_coql,
    _execute_paginated,
    _lookup_id,
    calculate_from_records,
    select_legacy_candidate_records,
)
from cotizacion_colectivos.block2.revision_facturacion import (
    CRMReadError,
    POLICY_FIELDS,
    POLICY_MODULE,
    ReconstructionResult,
    fetch_contact_records,
    fetch_insured_records,
    fetch_note_records,
    fetch_operation_records,
    reconstruct_revision_facturacion,
    select_latest_candidate_policies,
    select_relevant_operation_ids_for_notes,
)

from .domain import BillingException
from .service import build_billing_exceptions as combine_billing_exceptions


logger = logging.getLogger("cotizacion_colectivos")

PRODUCTION_WRITE_FLAGS = (
    "ZOHO_PRODUCTION_WRITE_ENABLED",
    "COLECTIVOS_TASK_PUBLISH_ENABLED",
    "COLECTIVOS_CONTACT_PUBLISH_ENABLED",
    "COLECTIVOS_RISK_PUBLISH_ENABLED",
    "COLECTIVOS_SUBRISK_PUBLISH_ENABLED",
    "COLECTIVOS_ATTACHMENT_PUBLISH_ENABLED",
    "COLECTIVOS_INVITATION_ATTACHMENT_PUBLISH_ENABLED",
)
ALLOWED_PROFILES = frozenset({"sandbox", "production"})
COLLECTIVE_BUSINESS_LINE = "Colectivos"

Progress = Callable[[str], None]
T = TypeVar("T")


class BillingExceptionsOperationalError(RuntimeError):
    def __init__(self, code: str, stage: str, message: str):
        super().__init__(message)
        self.code = code
        self.stage = stage


@dataclass(frozen=True)
class BillingExceptionsVolumes:
    policy_records: int
    revision_policies: int
    cobros_faltantes_policies: int
    operation_records: int
    insured_records: int
    contact_records: int
    note_records: int


@dataclass(frozen=True)
class BillingExceptionsResult:
    profile: str
    as_of: date
    exceptions: tuple[BillingException, ...]
    revision_rows_count: int
    cobros_faltantes_results_count: int
    revision_count: int
    missing_charges_count: int
    exception_counts: tuple[tuple[str, int], ...]
    source_counts: tuple[tuple[str, int], ...]
    revision_diagnostics: tuple[tuple[str, int], ...]
    revision_audit: tuple[tuple[str, int], ...]
    cobros_faltantes_anomalies: tuple[tuple[str, int], ...]
    volumes: BillingExceptionsVolumes


def _enabled(name: str) -> bool:
    value = getattr(settings, name, False)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _validate_request(
    profile: object,
    as_of: object,
    *,
    allow_production_read: bool,
) -> tuple[str, date]:
    normalized_profile = str(profile or "").strip().lower()
    if normalized_profile not in ALLOWED_PROFILES:
        raise BillingExceptionsOperationalError(
            "invalid_profile",
            "parameters",
            "El perfil debe ser sandbox o production.",
        )
    if type(as_of) is not date:
        raise BillingExceptionsOperationalError(
            "invalid_as_of",
            "parameters",
            "as_of debe ser una fecha explícita.",
        )
    if normalized_profile == "production":
        if not allow_production_read:
            raise BillingExceptionsOperationalError(
                "production_confirmation_required",
                "safety",
                "Production requiere confirmación explícita de lectura READ-only.",
            )
        enabled = [name for name in PRODUCTION_WRITE_FLAGS if _enabled(name)]
        if enabled:
            raise BillingExceptionsOperationalError(
                "write_guard_enabled",
                "safety",
                "Los guards WRITE/PUBLISH deben permanecer deshabilitados: "
                + ", ".join(enabled),
            )
    return normalized_profile, as_of


def _safe_zoho_detail(exc: BaseException) -> str:
    return " ".join(
        f"{name}={value}"
        for name, value in (
            ("category", getattr(exc, "category", "unknown") or "unknown"),
            ("operation", getattr(exc, "operation", "unknown") or "unknown"),
            ("backend", getattr(exc, "backend", "unknown") or "unknown"),
            ("status_code", getattr(exc, "status_code", None) or "unknown"),
            ("zoho_code", getattr(exc, "zoho_code", "unknown") or "unknown"),
        )
    )


def _progress(callback: Progress | None, message: str) -> None:
    if callback:
        callback(message)


def fetch_collective_policy_records(
    zoho: Any,
    *,
    progress: Progress | None = None,
) -> list[dict[str, object]]:
    """Obtiene el universo padre sin listas provenientes de baselines."""

    selected = ", ".join(POLICY_FIELDS)
    value = _escape_coql(COLLECTIVE_BUSINESS_LINE)
    query = (
        f"select {selected} from {POLICY_MODULE} "
        f"where L_nea_de_negocio = '{value}'"
    )
    _progress(
        progress,
        f"[READ] {POLICY_MODULE}: universo Colectivos, page_size={COQL_PAGE_SIZE}",
    )
    return _execute_paginated(zoho, query)


def _stable_records(records: Iterable[Mapping[str, object]]) -> list[dict[str, object]]:
    return sorted(
        (dict(record) for record in records),
        key=lambda record: (
            str(record.get("id") or ""),
            str(record.get("Name") or ""),
        ),
    )


def _read_stage(stage: str, operation: Callable[[], T]) -> T:
    try:
        return operation()
    except CRMReadError as exc:
        raise BillingExceptionsOperationalError(
            "zoho_read_error",
            stage,
            f"Falló la lectura de {stage}: {_safe_zoho_detail(exc.cause)}.",
        ) from exc
    except ZohoError as exc:
        raise BillingExceptionsOperationalError(
            "zoho_read_error",
            stage,
            f"Falló la lectura de {stage}: {_safe_zoho_detail(exc)}.",
        ) from exc
    except ValueError as exc:
        raise BillingExceptionsOperationalError(
            "invalid_zoho_contract",
            stage,
            f"Zoho devolvió datos inválidos durante {stage}: {exc}",
        ) from exc


def _resolve_facade(profile: str, supplied: Any | None) -> Any:
    try:
        zoho = supplied if supplied is not None else get_zoho(profile=profile)
        organization = zoho.organization.get()
    except ZohoError as exc:
        raise BillingExceptionsOperationalError(
            "zoho_connection_error",
            "organization",
            f"No fue posible validar Zoho: {_safe_zoho_detail(exc)}.",
        ) from exc
    facade_profile = str(getattr(zoho, "profile", "") or "").strip().lower()
    environment = str(
        getattr(organization, "environment", "") or ""
    ).strip().lower()
    if facade_profile != profile or environment != profile:
        raise BillingExceptionsOperationalError(
            "environment_mismatch",
            "organization",
            "El perfil o entorno reportado por Zoho no coincide con el solicitado.",
        )
    return zoho


def _counter_items(values: Counter[str] | Mapping[str, int]) -> tuple[tuple[str, int], ...]:
    return tuple(sorted((str(name), int(count)) for name, count in values.items()))


def get_billing_exceptions(
    *,
    profile: str,
    as_of: date,
    allow_production_read: bool = False,
    zoho: Any | None = None,
    progress: Progress | None = None,
) -> BillingExceptionsResult:
    """Ejecuta ambos motores desde CRM sin baselines, persistencia ni WRITE."""

    profile, as_of = _validate_request(
        profile, as_of, allow_production_read=allow_production_read
    )
    started = time.monotonic()
    facade = _resolve_facade(profile, zoho)

    policies = _stable_records(
        _read_stage(
            POLICY_MODULE,
            lambda: fetch_collective_policy_records(facade, progress=progress),
        )
    )
    revision_selected, _revision_duplicates = select_latest_candidate_policies(
        policies
    )
    cobros_selected, _cobros_duplicates = select_legacy_candidate_records(
        policies, as_of=as_of
    )
    revision_policy_ids = {
        str(item.get("id") or "") for item in revision_selected.values()
        if str(item.get("id") or "")
    }
    cobros_policy_ids = {
        str(item.get("id") or "") for item in cobros_selected.values()
        if str(item.get("id") or "")
    }
    operation_policy_ids = tuple(sorted(revision_policy_ids | cobros_policy_ids))
    _progress(
        progress,
        "[READ] Universos: "
        f"revision={len(revision_policy_ids)}, "
        f"cobros_faltantes={len(cobros_policy_ids)}, "
        f"union={len(operation_policy_ids)}",
    )

    operations = _stable_records(
        _read_stage(
            "Opeeraciones",
            lambda: fetch_operation_records(
                facade, operation_policy_ids, progress=progress
            ),
        )
    )
    insured = _stable_records(
        _read_stage(
            "Riesgos1",
            lambda: fetch_insured_records(
                facade, sorted(revision_policy_ids), progress=progress
            ),
        )
    )
    contact_ids = tuple(
        sorted(
            {
                _lookup_id(item.get("Tomador_principal1"))
                for item in revision_selected.values()
                if _lookup_id(item.get("Tomador_principal1"))
            }
        )
    )
    contacts = _stable_records(
        _read_stage(
            "Contacts",
            lambda: fetch_contact_records(
                facade, contact_ids, progress=progress
            ),
        )
    )
    relevant_operation_ids = select_relevant_operation_ids_for_notes(
        revision_selected.values(), operations, as_of=as_of
    )
    notes = _stable_records(
        _read_stage(
            "Notes",
            lambda: fetch_note_records(
                facade, relevant_operation_ids, progress=progress
            ),
        )
    )

    try:
        revision_result: ReconstructionResult = reconstruct_revision_facturacion(
            policies, operations, insured, contacts, notes, as_of=as_of
        )
        cobros_results, _duplicates = calculate_from_records(
            policies, operations, as_of=as_of
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise BillingExceptionsOperationalError(
            "reconstruction_error",
            "legacy_engines",
            f"No fue posible reconstruir los motores legacy: {exc}",
        ) from exc

    try:
        exceptions = combine_billing_exceptions(revision_result, cobros_results)
    except (KeyError, TypeError, ValueError) as exc:
        raise BillingExceptionsOperationalError(
            "adaptation_error",
            "billing_exceptions",
            f"No fue posible adaptar los resultados a excepciones: {exc}",
        ) from exc

    exception_counts = Counter(item.exception_type.value for item in exceptions)
    source_counts = Counter(item.source.value for item in exceptions)
    cobros_anomalies = Counter(
        anomaly
        for result in cobros_results.values()
        for anomaly in result.anomalies
    )
    revision_count = source_counts.get("revision_facturacion", 0)
    missing_charges_count = source_counts.get("cobros_faltantes", 0)
    result = BillingExceptionsResult(
        profile=profile,
        as_of=as_of,
        exceptions=exceptions,
        revision_rows_count=len(revision_result.rows),
        cobros_faltantes_results_count=len(cobros_results),
        revision_count=revision_count,
        missing_charges_count=missing_charges_count,
        exception_counts=_counter_items(exception_counts),
        source_counts=_counter_items(source_counts),
        revision_diagnostics=_counter_items(revision_result.diagnostic_counts),
        revision_audit=_counter_items(revision_result.audit_counts),
        cobros_faltantes_anomalies=_counter_items(cobros_anomalies),
        volumes=BillingExceptionsVolumes(
            policy_records=len(policies),
            revision_policies=len(revision_selected),
            cobros_faltantes_policies=len(cobros_results),
            operation_records=len(operations),
            insured_records=len(insured),
            contact_records=len(contacts),
            note_records=len(notes),
        ),
    )
    logger.info(
        "billing_exceptions profile=%s as_of=%s policies=%d operations=%d "
        "insured=%d contacts=%d notes=%d revision=%d missing_charges=%d "
        "exceptions=%d duration_ms=%d",
        profile,
        as_of.isoformat(),
        result.volumes.policy_records,
        result.volumes.operation_records,
        result.volumes.insured_records,
        result.volumes.contact_records,
        result.volumes.note_records,
        result.revision_count,
        result.missing_charges_count,
        len(result.exceptions),
        round((time.monotonic() - started) * 1000),
    )
    return result
