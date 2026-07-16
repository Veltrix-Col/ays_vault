import hashlib
import json
import secrets
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import AuditChainState, AuditEvent, AuditVerificationRun, RevealGrant, SecurityAlert


def client_ip(request):
    # REMOTE_ADDR is authoritative unless a trusted reverse proxy sanitizes it.
    return request.META.get("REMOTE_ADDR") if request is not None else None


def outside_hours(user=None, operation="ACCESS", at=None):
    from .policies import evaluate_access_policy
    role = getattr(getattr(user, "vault_profile", None), "role", "") if user else ""
    return not evaluate_access_policy(user=user, role=role, operation=operation, at=at).within_schedule


def _canonical(event):
    return json.dumps({
        "sequence": event.sequence,
        "user_id": event.user_id,
        "actor_role": event.actor_role,
        "action": event.action,
        "card_id": event.card_id,
        "field_name": event.field_name,
        "reason": event.reason,
        "ip_address": str(event.ip_address or ""),
        "user_agent": event.user_agent,
        "session_key": event.session_key,
        "path": event.path,
        "method": event.method,
        "result": event.result,
        "risk_level": event.risk_level,
        "outside_office_hours": event.outside_office_hours,
        "metadata": event.metadata,
        "previous_hash": event.previous_hash,
        "created_at": event.created_at.isoformat(),
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _event_hash(event):
    return hashlib.sha256(_canonical(event).encode()).hexdigest()


def session_hash(request_or_key):
    raw = request_or_key.session.session_key if hasattr(request_or_key, "session") else request_or_key
    return hashlib.sha256((raw or "").encode()).hexdigest() if raw else ""


def audit(request, action, card=None, field_name="", reason="", metadata=None, result="SUCCESS", risk_level="LOW", user=None, occurred_at=None):
    request_user = getattr(request, "user", None)
    actor = user if user is not None else (request_user if request_user and request_user.is_authenticated else None)
    profile = getattr(actor, "vault_profile", None) if actor else None
    is_outside = outside_hours(actor, action)
    if is_outside and action in {"LOGIN", "REVEAL", "COPY", "CREATE", "UPDATE", "DEACTIVATE"}:
        risk_level = "HIGH"

    with transaction.atomic():
        state, _ = AuditChainState.objects.select_for_update().get_or_create(singleton=1)
        event = AuditEvent.objects.create(
            sequence=state.last_sequence + 1,
            user=actor,
            actor_role=getattr(profile, "role", ""),
            action=action,
            card=card,
            field_name=field_name,
            reason=reason,
            ip_address=client_ip(request),
            user_agent=(request.META.get("HTTP_USER_AGENT", "")[:300] if request is not None else "system"),
            session_key=session_hash(request) if request is not None else "",
            path=((request.path or "")[:300] if request is not None else "management-command"),
            method=(request.method or "" if request is not None else "SYSTEM"),
            result=result,
            risk_level=risk_level,
            outside_office_hours=is_outside,
            metadata=metadata or {},
            previous_hash=state.last_hash,
            event_hash="0" * 64,
        )
        if occurred_at is not None:
            AuditEvent.objects.filter(pk=event.pk).update(created_at=occurred_at)
            event.created_at = occurred_at
        event.event_hash = _event_hash(event)
        AuditEvent.objects.filter(pk=event.pk).update(event_hash=event.event_hash)
        state.last_sequence = event.sequence
        state.last_hash = event.event_hash
        state.save(update_fields=["last_sequence", "last_hash"])

    should_create_alert = (
        is_outside and action in {"LOGIN", "REVEAL", "COPY", "CREATE", "UPDATE", "DEACTIVATE"}
    ) or (result != "SUCCESS" and action != "REPORT_EXPORT")
    if should_create_alert:
        SecurityAlert.objects.get_or_create(event=event, defaults={"alert_type": action, "severity": "HIGH" if risk_level in {"HIGH", "CRITICAL"} else "MEDIUM", "actor": actor, "affected_user": actor, "ip_address": event.ip_address, "description": f"Evento de seguridad: {event.get_action_display()}"})
        alert = SecurityAlert.objects.get(event=event)
        from .notifications import notify_alert_async
        transaction.on_commit(lambda: notify_alert_async(alert))
    return event


def create_reveal_grant(request, card, field_name, reason):
    token = secrets.token_urlsafe(32)
    RevealGrant.objects.create(
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        user=request.user,
        card=card,
        field_name=field_name,
        reason=reason,
        session_key=session_hash(request),
        expires_at=timezone.now() + timedelta(seconds=25),
    )
    return token


def consume_copy_grant(request, card, token):
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with transaction.atomic():
        grant = RevealGrant.objects.select_for_update().filter(
            token_hash=token_hash,
            user=request.user,
            card=card,
            session_key=session_hash(request),
            copied_at__isnull=True,
            expires_at__gte=timezone.now(),
        ).first()
        if not grant:
            return None
        grant.copied_at = timezone.now()
        grant.save(update_fields=["copied_at"])
        return grant


def verify_audit_chain(since_sequence=0, since_hash=""):
    """Verifica la cadena desde el evento since_sequence+1. Sin argumentos, verifica desde el inicio."""
    expected_previous = since_hash
    expected_sequence = since_sequence + 1
    for event in AuditEvent.objects.filter(sequence__gt=since_sequence).order_by("sequence"):
        if event.sequence != expected_sequence or event.previous_hash != expected_previous or event.event_hash != _event_hash(event):
            return False, event.sequence
        expected_previous = event.event_hash
        expected_sequence += 1
    state = AuditChainState.objects.filter(singleton=1).first()
    if state and (state.last_sequence != expected_sequence - 1 or state.last_hash != expected_previous):
        return False, state.last_sequence
    return True, expected_sequence - 1


def refresh_chain_verification(source="COMMAND"):
    """Reverifica solo los eventos posteriores al último checkpoint válido y persiste el resultado."""
    last_run = AuditVerificationRun.objects.filter(valid=True).order_by("-checked_at").first()
    since_sequence = last_run.position if last_run else 0
    since_hash = AuditEvent.objects.filter(sequence=since_sequence).values_list("event_hash", flat=True).first() or "" if since_sequence else ""
    valid, position = verify_audit_chain(since_sequence=since_sequence, since_hash=since_hash)
    AuditVerificationRun.objects.create(valid=valid, position=position, source=source)
    return valid, position


def cached_chain_status(max_age_seconds=300):
    """Devuelve el último resultado de integridad conocido, reverificando de forma incremental si está vencido."""
    last_run = AuditVerificationRun.objects.order_by("-checked_at").first()
    if last_run and (timezone.now() - last_run.checked_at).total_seconds() < max_age_seconds:
        return last_run.valid, last_run.position
    return refresh_chain_verification(source="CACHE")
