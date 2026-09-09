"""Lifecycle persistente del snapshot operacional de Excepciones de Facturación."""

from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Callable

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from cotizacion_colectivos.models import (
    BillingExceptionRefreshRun,
    BillingOperationalCase,
    BillingTechnicalException,
)

from .application import BillingExceptionsResult, get_billing_exceptions
from .cases import BillingCase, build_billing_cases


class BillingRefreshAlreadyRunning(RuntimeError):
    pass


ORPHANED_REFRESH_AFTER = timedelta(minutes=15)
ORPHANED_REFRESH_SAFE_ERROR = "worker: orphaned_running_timeout"


def recover_orphaned_billing_refreshes(
    *, now=None, stale_after: timedelta = ORPHANED_REFRESH_AFTER,
) -> tuple[int, ...]:
    """Marca como fallidos únicamente runs RUNNING que excedieron el umbral."""

    if stale_after <= timedelta(0):
        raise ValueError("stale_after debe ser positivo.")
    recovered_at = now or timezone.now()
    cutoff = recovered_at - stale_after
    recovered_ids = []
    with transaction.atomic():
        stale_runs = BillingExceptionRefreshRun.objects.select_for_update().filter(
            profile="production",
            status=BillingExceptionRefreshRun.Status.RUNNING,
        ).filter(
            Q(started_at__lt=cutoff)
            | Q(started_at__isnull=True, requested_at__lt=cutoff)
        )
        for run in stale_runs:
            origin = run.started_at or run.requested_at
            run.status = BillingExceptionRefreshRun.Status.FAILED
            run.finished_at = recovered_at
            run.duration_ms = max(0, round((recovered_at - origin).total_seconds() * 1000))
            run.safe_error = ORPHANED_REFRESH_SAFE_ERROR
            run.save(update_fields=("status", "finished_at", "duration_ms", "safe_error"))
            recovered_ids.append(run.pk)
    return tuple(recovered_ids)


def queue_billing_exceptions_refresh(*, as_of: date, requested_by=None) -> BillingExceptionRefreshRun:
    if type(as_of) is not date:
        raise ValueError("as_of debe ser una fecha explícita.")
    try:
        with transaction.atomic():
            if BillingExceptionRefreshRun.objects.select_for_update().filter(
                profile="production",
                status__in=(BillingExceptionRefreshRun.Status.PENDING, BillingExceptionRefreshRun.Status.RUNNING),
            ).exists():
                raise BillingRefreshAlreadyRunning("Ya existe una actualización pendiente o en curso.")
            return BillingExceptionRefreshRun.objects.create(
                profile="production", as_of=as_of, requested_by=requested_by,
            )
    except IntegrityError as exc:
        raise BillingRefreshAlreadyRunning("Ya existe una actualización pendiente o en curso.") from exc


def _technical_defaults(exception, *, run, detected_at):
    return {
        "source": exception.source.value,
        "exception_type": exception.exception_type.value,
        "rule_code": exception.rule_code,
        "reason": exception.reason,
        "source_reference": exception.source_reference,
        "policy_id": exception.policy_id or "",
        "policy_number": exception.policy_number,
        "client_name": exception.client_name or "",
        "insurer": exception.insurer or "",
        "branch": exception.branch or "",
        "analyst": exception.analyst or "",
        "operation_id": exception.operation_id or "",
        "operation_name": exception.operation_name or "",
        "installment_number": exception.installment_number,
        "relevant_date": exception.relevant_date,
        "billing_date": exception.billing_date,
        "context": [list(item) for item in exception.context],
        "detection_status": BillingTechnicalException.DetectionStatus.DETECTED,
        "last_detected_at": detected_at,
        "last_run": run,
    }


def _case_defaults(case: BillingCase, *, run, detected_at):
    kind = {
        "operation": BillingOperationalCase.Kind.OPERATION,
        "missing-operation": BillingOperationalCase.Kind.MISSING_OPERATION,
        "policy-gap": BillingOperationalCase.Kind.POLICY_GAP,
    }[case.kind]
    return {
        "kind": kind,
        "policy_id": case.policy_id or "",
        "policy_number": case.policy_number,
        "client_name": case.client_name or "",
        "insurer": case.insurer or "",
        "branch": case.branch or "",
        "analyst": case.analyst or "",
        "operation_id": case.operation_id or "",
        "operation_name": case.operation_name or "",
        "installment_number": case.installment_number,
        "relevant_date": case.relevant_date,
        "billing_date": case.billing_date,
        "local_priority": case.priority,
        "detection_status": BillingOperationalCase.DetectionStatus.DETECTED,
        "last_detected_at": detected_at,
        "last_run": run,
    }


def persist_successful_billing_refresh(
    run: BillingExceptionRefreshRun,
    result: BillingExceptionsResult,
) -> BillingExceptionRefreshRun:
    """Aplica un snapshot completo; no se llama para resultados fallidos/parciales."""

    detected_at = timezone.now()
    cases = build_billing_cases(result.exceptions)
    with transaction.atomic():
        locked_run = BillingExceptionRefreshRun.objects.select_for_update().get(pk=run.pk)
        if locked_run.status != BillingExceptionRefreshRun.Status.RUNNING:
            raise ValueError("El run no está en estado RUNNING.")
        technical_by_key = {}
        current_exception_keys = []
        for exception in result.exceptions:
            item = BillingTechnicalException.objects.select_for_update().filter(
                exception_key=exception.exception_key
            ).first()
            defaults = _technical_defaults(exception, run=locked_run, detected_at=detected_at)
            if item is None:
                item = BillingTechnicalException.objects.create(
                    exception_key=exception.exception_key,
                    first_detected_at=detected_at,
                    **defaults,
                )
            else:
                if item.detection_status == BillingTechnicalException.DetectionStatus.NOT_DETECTED:
                    item.reappearance_count += 1
                item.detection_count += 1
                for name, value in defaults.items():
                    setattr(item, name, value)
                item.save()
            technical_by_key[exception.exception_key] = item
            current_exception_keys.append(exception.exception_key)
        stale_exceptions = BillingTechnicalException.objects.filter(
            detection_status=BillingTechnicalException.DetectionStatus.DETECTED
        )
        if current_exception_keys:
            stale_exceptions = stale_exceptions.exclude(exception_key__in=current_exception_keys)
        stale_exception_count = stale_exceptions.update(
            detection_status=BillingTechnicalException.DetectionStatus.NOT_DETECTED,
            last_run=locked_run,
        )

        current_case_keys = []
        for case in cases:
            item = BillingOperationalCase.objects.select_for_update().filter(case_key=case.case_key).first()
            defaults = _case_defaults(case, run=locked_run, detected_at=detected_at)
            if item is None:
                item = BillingOperationalCase.objects.create(
                    case_key=case.case_key,
                    first_detected_at=detected_at,
                    **defaults,
                )
            else:
                if item.detection_status == BillingOperationalCase.DetectionStatus.NOT_DETECTED:
                    item.reappearance_count += 1
                item.detection_count += 1
                for name, value in defaults.items():
                    setattr(item, name, value)
                item.save()
            item.exceptions.add(*(technical_by_key[exception.exception_key] for exception in case.exceptions))
            current_case_keys.append(case.case_key)
        stale_cases = BillingOperationalCase.objects.filter(
            detection_status=BillingOperationalCase.DetectionStatus.DETECTED
        )
        if current_case_keys:
            stale_cases = stale_cases.exclude(case_key__in=current_case_keys)
        stale_case_count = stale_cases.update(
            detection_status=BillingOperationalCase.DetectionStatus.NOT_DETECTED,
            last_run=locked_run,
        )

        locked_run.status = BillingExceptionRefreshRun.Status.SUCCESS
        locked_run.finished_at = detected_at
        locked_run.duration_ms = max(
            0, round((detected_at - locked_run.started_at).total_seconds() * 1000)
        ) if locked_run.started_at else 0
        locked_run.counts = {
            "technical_exceptions": len(result.exceptions),
            "operational_cases": len(cases),
            "revision_exceptions": result.revision_count,
            "missing_charges": result.missing_charges_count,
            "not_detected_exceptions": stale_exception_count,
            "not_detected_cases": stale_case_count,
            "by_type": dict(result.exception_counts),
        }
        locked_run.safe_error = ""
        locked_run.save()
        return locked_run


def execute_billing_exceptions_refresh(
    run_id: int,
    *,
    zoho=None,
    loader: Callable[..., BillingExceptionsResult] = get_billing_exceptions,
) -> BillingExceptionRefreshRun:
    with transaction.atomic():
        run = BillingExceptionRefreshRun.objects.select_for_update().get(pk=run_id)
        if run.status != BillingExceptionRefreshRun.Status.PENDING:
            raise ValueError("El run ya fue iniciado o finalizado.")
        run.status = BillingExceptionRefreshRun.Status.RUNNING
        run.started_at = timezone.now()
        run.safe_error = ""
        run.save(update_fields=("status", "started_at", "safe_error"))
    started = time.monotonic()
    try:
        result = loader(
            profile="production", as_of=run.as_of,
            allow_production_read=True, zoho=zoho,
        )
        return persist_successful_billing_refresh(run, result)
    except Exception as exc:
        finished = timezone.now()
        code = str(getattr(exc, "code", exc.__class__.__name__) or "refresh_error")[:80]
        stage = str(getattr(exc, "stage", "refresh") or "refresh")[:80]
        BillingExceptionRefreshRun.objects.filter(pk=run.pk).update(
            status=BillingExceptionRefreshRun.Status.FAILED,
            finished_at=finished,
            duration_ms=round((time.monotonic() - started) * 1000),
            safe_error=f"{stage}: {code}"[:240],
        )
        raise
