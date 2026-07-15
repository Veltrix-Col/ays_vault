import hashlib
import json
import secrets
from datetime import time, timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from .models import AuditChainState, AuditEvent, RevealGrant, SecurityAlert


def client_ip(request):
    # REMOTE_ADDR is authoritative unless a trusted reverse proxy sanitizes it.
    return request.META.get("REMOTE_ADDR")


def outside_hours():
    now = timezone.localtime()
    start = time.fromisoformat(settings.OFFICE_START)
    end = time.fromisoformat(settings.OFFICE_END)
    current = now.time().replace(tzinfo=None)
    return now.weekday() >= 5 or not (start <= current <= end)


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


def audit(request, action, card=None, field_name="", reason="", metadata=None, result="SUCCESS", risk_level="LOW", user=None):
    actor = user if user is not None else (request.user if request.user.is_authenticated else None)
    profile = getattr(actor, "vault_profile", None) if actor else None
    is_outside = outside_hours()
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
            user_agent=request.META.get("HTTP_USER_AGENT", "")[:300],
            session_key=session_hash(request),
            path=(request.path or "")[:300],
            method=request.method or "",
            result=result,
            risk_level=risk_level,
            outside_office_hours=is_outside,
            metadata=metadata or {},
            previous_hash=state.last_hash,
            event_hash="0" * 64,
        )
        event.event_hash = _event_hash(event)
        AuditEvent.objects.filter(pk=event.pk).update(event_hash=event.event_hash)
        state.last_sequence = event.sequence
        state.last_hash = event.event_hash
        state.save(update_fields=["last_sequence", "last_hash"])

    if (is_outside and action in {"LOGIN", "REVEAL", "COPY", "CREATE", "UPDATE", "DEACTIVATE"}) or result != "SUCCESS":
        SecurityAlert.objects.get_or_create(event=event, defaults={"alert_type": action, "severity": "HIGH" if risk_level in {"HIGH", "CRITICAL"} else "MEDIUM", "actor": actor, "affected_user": actor, "ip_address": event.ip_address, "description": f"Evento de seguridad: {event.get_action_display()}"})
        if settings.ALERT_EMAIL:
            send_mail(
                f"[A&S Vault] Alerta de seguridad: {event.get_action_display()}",
                "\n".join([
                    f"Usuario: {event.user or 'No identificado'}",
                    f"Acción: {event.get_action_display()}",
                    f"Tarjeta: **** {card.last4 if card else 'N/A'}",
                    f"Fecha: {timezone.localtime(event.created_at)}",
                    f"IP: {event.ip_address}",
                    f"Resultado: {event.result}",
                ]),
                settings.DEFAULT_FROM_EMAIL,
                [settings.ALERT_EMAIL],
                fail_silently=True,
            )
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


def verify_audit_chain():
    expected_previous = ""
    expected_sequence = 1
    for event in AuditEvent.objects.order_by("sequence"):
        if event.sequence != expected_sequence or event.previous_hash != expected_previous or event.event_hash != _event_hash(event):
            return False, event.sequence
        expected_previous = event.event_hash
        expected_sequence += 1
    state = AuditChainState.objects.filter(singleton=1).first()
    if state and (state.last_sequence != expected_sequence - 1 or state.last_hash != expected_previous):
        return False, state.last_sequence
    return True, expected_sequence - 1
