"""Transactional lifecycle operations for ExceptionCase."""

from django.contrib.auth import get_user_model
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


class CaseOperationError(ValidationError):
    """A case assignment, follow-up, or note operation is invalid."""


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


def get_assignable_operators(*, using="default"):
    """Return active identities that can receive an internal case."""
    user_model = get_user_model()
    return user_model.objects.using(using).filter(
        is_active=True,
    ).order_by("last_name", "first_name", "username")


def _require_assignee(assignee_id, *, using):
    try:
        assignee = get_user_model().objects.using(using).get(pk=assignee_id)
    except (TypeError, ValueError, get_user_model().DoesNotExist) as exc:
        raise CaseOperationError("El responsable seleccionado no existe.") from exc
    if not assignee.is_active:
        raise CaseOperationError("El responsable debe estar activo.")
    return assignee


def _assignment_activity(*, case, actor, event_type, from_user_id, to_user_id, using):
    return CaseActivity.objects.using(using).create(
        case=case,
        event_type=event_type,
        actor_id=actor.pk,
        from_status=case.status,
        to_status=case.status,
        metadata={"from_user_id": from_user_id, "to_user_id": to_user_id},
    )


def assign_case(case_id, *, actor, assignee_id, using="default"):
    with transaction.atomic(using=using):
        _require_operator(actor)
        assignee = _require_assignee(assignee_id, using=using)
        case = ExceptionCase.objects.using(using).select_for_update().get(pk=case_id)
        previous_id = case.assigned_to_id
        if previous_id == assignee.pk:
            return case, None
        now = timezone.now()
        case.assigned_to = assignee
        case.assigned_at = now
        case.save(using=using, update_fields=("assigned_to", "assigned_at", "updated_at"))
        event_type = CaseActivity.EventType.ASSIGNED if previous_id is None else CaseActivity.EventType.REASSIGNED
        activity = _assignment_activity(case=case, actor=actor, event_type=event_type, from_user_id=previous_id, to_user_id=assignee.pk, using=using)
        return case, activity


def take_case(case_id, *, actor, using="default"):
    with transaction.atomic(using=using):
        _require_operator(actor)
        case = ExceptionCase.objects.using(using).select_for_update().get(pk=case_id)
        if case.assigned_to_id == actor.pk:
            return case, None
        if case.assigned_to_id is not None:
            raise CaseOperationError("El caso ya está asignado a otro operador.")
        now = timezone.now()
        case.assigned_to_id = actor.pk
        case.assigned_at = now
        case.save(using=using, update_fields=("assigned_to", "assigned_at", "updated_at"))
        activity = _assignment_activity(case=case, actor=actor, event_type=CaseActivity.EventType.ASSIGNED, from_user_id=None, to_user_id=actor.pk, using=using)
        return case, activity


def unassign_case(case_id, *, actor, using="default"):
    with transaction.atomic(using=using):
        _require_operator(actor)
        case = ExceptionCase.objects.using(using).select_for_update().get(pk=case_id)
        previous_id = case.assigned_to_id
        if previous_id is None:
            return case, None
        case.assigned_to = None
        case.assigned_at = None
        case.save(using=using, update_fields=("assigned_to", "assigned_at", "updated_at"))
        activity = _assignment_activity(case=case, actor=actor, event_type=CaseActivity.EventType.UNASSIGNED, from_user_id=previous_id, to_user_id=None, using=using)
        return case, activity


def update_case_follow_up(case_id, *, actor, next_action, follow_up_at, using="default"):
    with transaction.atomic(using=using):
        _require_operator(actor)
        case = ExceptionCase.objects.using(using).select_for_update().get(pk=case_id)
        next_action = str(next_action or "").strip()
        if len(next_action) > 500:
            raise CaseOperationError("La próxima acción no puede superar 500 caracteres.")
        if case.status in {ExceptionCase.Status.RESOLVED, ExceptionCase.Status.CLOSED} and (next_action or follow_up_at is not None):
            raise CaseOperationError("Un caso resuelto o cerrado no admite seguimiento activo.")
        previous_action = case.next_action
        previous_follow_up = case.follow_up_at
        if previous_action == next_action and previous_follow_up == follow_up_at:
            return case, None
        case.next_action = next_action
        case.follow_up_at = follow_up_at
        case.save(using=using, update_fields=("next_action", "follow_up_at", "updated_at"))
        activity = CaseActivity.objects.using(using).create(
            case=case,
            event_type=CaseActivity.EventType.FOLLOW_UP_UPDATED,
            actor_id=actor.pk,
            from_status=case.status,
            to_status=case.status,
            metadata={
                "previous_next_action": previous_action,
                "new_next_action": next_action,
                "previous_follow_up_at": previous_follow_up.isoformat() if previous_follow_up else None,
                "new_follow_up_at": follow_up_at.isoformat() if follow_up_at else None,
            },
        )
        return case, activity


def add_case_note(case_id, *, actor, note, using="default"):
    with transaction.atomic(using=using):
        _require_operator(actor)
        case = ExceptionCase.objects.using(using).select_for_update().get(pk=case_id)
        note = str(note or "").strip()
        if not note:
            raise CaseOperationError("La nota interna no puede estar vacía.")
        if len(note) > 4000:
            raise CaseOperationError("La nota interna no puede superar 4.000 caracteres.")
        activity = CaseActivity.objects.using(using).create(
            case=case,
            event_type=CaseActivity.EventType.NOTE_ADDED,
            actor_id=actor.pk,
            from_status=case.status,
            to_status=case.status,
            metadata={"note": note},
        )
        return case, activity


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
            case.next_action = ""
            case.follow_up_at = None
        elif target_status == ExceptionCase.Status.CLOSED:
            case.closed_at = now
            case.next_action = ""
            case.follow_up_at = None
        elif reopening:
            case.resolved_at = None
            case.closed_at = None
        case.updated_at = now
        case.save(using=using, update_fields=("status", "resolved_at", "closed_at", "next_action", "follow_up_at", "updated_at"))

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
