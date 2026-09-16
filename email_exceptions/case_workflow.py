"""Transactional lifecycle operations for ExceptionCase."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from .models import CaseActivity, ExceptionCase
from .permissions import can_operate


ALLOWED_TRANSITIONS = {
    ExceptionCase.Status.OPEN: frozenset({ExceptionCase.Status.IN_PROGRESS, ExceptionCase.Status.WAITING}),
    ExceptionCase.Status.PENDING: frozenset({ExceptionCase.Status.IN_PROGRESS}),
    ExceptionCase.Status.IN_PROGRESS: frozenset({ExceptionCase.Status.WAITING, ExceptionCase.Status.RESOLVED}),
    ExceptionCase.Status.WAITING: frozenset({ExceptionCase.Status.IN_PROGRESS, ExceptionCase.Status.RESOLVED}),
    ExceptionCase.Status.RESOLVED: frozenset({ExceptionCase.Status.CLOSED, ExceptionCase.Status.IN_PROGRESS}),
    ExceptionCase.Status.CLOSED: frozenset({ExceptionCase.Status.IN_PROGRESS}),
}


class CaseTransitionError(ValidationError):
    """A requested lifecycle transition is not valid."""


def _require_operator(actor):
    if not can_operate(actor):
        raise PermissionDenied("El usuario no puede operar casos de Excepciones de Correo.")


def _event_type(previous, target):
    if target == ExceptionCase.Status.RESOLVED:
        return CaseActivity.EventType.RESOLVED
    if target == ExceptionCase.Status.CLOSED:
        return CaseActivity.EventType.CLOSED
    if previous in {ExceptionCase.Status.RESOLVED, ExceptionCase.Status.CLOSED} and target == ExceptionCase.Status.IN_PROGRESS:
        return CaseActivity.EventType.REOPENED
    return CaseActivity.EventType.STATUS_CHANGED


def transition_case(case_id, target_status, *, actor, reason="", comment="", using="default"):
    """Apply one validated transition and its activity atomically.

    The case is re-read under a row lock so a stale object from a view cannot
    bypass the current persisted state. PENDING remains legacy and can only be
    advanced to IN_PROGRESS; it is never a destination for new transitions.
    """
    with transaction.atomic(using=using):
        _require_operator(actor)
        target_status = str(target_status or "").strip().upper()
        valid_statuses = {value for value, _label in ExceptionCase.Status.choices}
        if target_status not in valid_statuses or target_status == ExceptionCase.Status.PENDING:
            raise CaseTransitionError("Estado de destino no permitido.")

        case = ExceptionCase.objects.using(using).select_for_update().get(pk=case_id)
        previous = case.status
        if target_status == previous:
            raise CaseTransitionError("La transición al mismo estado no está permitida.")
        if target_status not in ALLOWED_TRANSITIONS.get(previous, frozenset()):
            raise CaseTransitionError("Transición de estado no permitida.")

        reason = str(reason or "").strip()
        comment = str(comment or "").strip()
        reopening = previous in {ExceptionCase.Status.RESOLVED, ExceptionCase.Status.CLOSED} and target_status == ExceptionCase.Status.IN_PROGRESS
        if target_status == ExceptionCase.Status.RESOLVED and not reason:
            raise CaseTransitionError("La resolución requiere un motivo.")
        if reopening and not reason:
            raise CaseTransitionError("La reapertura requiere un motivo.")

        now = timezone.now()
        case.status = target_status
        if target_status == ExceptionCase.Status.RESOLVED:
            case.resolved_at = now
            case.closed_at = None
        elif target_status == ExceptionCase.Status.CLOSED:
            case.closed_at = now
        elif reopening:
            case.resolved_at = None
            case.closed_at = None
        case.updated_at = now
        case.save(using=using, update_fields=("status", "resolved_at", "closed_at", "updated_at"))

        metadata = {}
        if reason:
            metadata["reason"] = reason
        if comment:
            metadata["comment"] = comment
        activity = CaseActivity.objects.using(using).create(
            case=case,
            event_type=_event_type(previous, target_status),
            actor=actor,
            from_status=previous,
            to_status=target_status,
            metadata=metadata,
        )
        return case, activity
