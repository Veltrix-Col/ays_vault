"""Servicios de autorización reforzada para datos protegidos.

Este módulo nunca guarda ni recibe valores descifrados. Solo conserva contexto
operativo seguro, ligado al usuario y a la sesión autenticada.
"""

import hashlib
import hmac
import secrets
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import ProtectedOperationContext, SensitiveOperationWindow, SecureSession, UserDevice, UserProfile
from .security import audit, session_hash


INTENT_SESSION_KEY = "protected_operation_intent"
INTENT_TTL_SECONDS = 300
IDENTITY_TTL_SECONDS = 180
WINDOW_TTL_MINUTES = 15
PROTECTED_FIELDS = {"company", "pan", "expiry"}
PROTECTED_ACTIONS = {"reveal", "copy"}


def create_intent(request, card, field_name, action, identity_verified=False):
    if field_name not in PROTECTED_FIELDS or action not in PROTECTED_ACTIONS:
        raise ValueError("Acción protegida inválida.")
    token = secrets.token_urlsafe(32)
    request.session[INTENT_SESSION_KEY] = {
        "token_hash": hashlib.sha256(token.encode()).hexdigest(),
        "card_id": card.pk,
        "field": field_name,
        "action": action,
        "created_at": timezone.now().timestamp(),
        "identity_verified_at": timezone.now().timestamp() if identity_verified else None,
    }
    request.session.modified = True
    return token


def get_intent(request, token, require_identity=False):
    value = request.session.get(INTENT_SESSION_KEY) or {}
    supplied_hash = hashlib.sha256((token or "").encode()).hexdigest()
    if not value or not hmac.compare_digest(value.get("token_hash", ""), supplied_hash):
        return None
    now_timestamp = timezone.now().timestamp()
    if now_timestamp - float(value.get("created_at") or 0) > INTENT_TTL_SECONDS:
        clear_intent(request)
        return None
    if value.get("field") not in PROTECTED_FIELDS or value.get("action") not in PROTECTED_ACTIONS:
        clear_intent(request)
        return None
    if require_identity:
        verified_at = float(value.get("identity_verified_at") or 0)
        if not verified_at or now_timestamp - verified_at > IDENTITY_TTL_SECONDS:
            return None
    return value.copy()


def mark_identity_verified(request, token):
    value = get_intent(request, token)
    if not value:
        return False
    value["identity_verified_at"] = timezone.now().timestamp()
    request.session[INTENT_SESSION_KEY] = value
    request.session.modified = True
    return True


def clear_intent(request):
    request.session.pop(INTENT_SESSION_KEY, None)
    request.session.modified = True


def _operator_is_eligible(request):
    profile = getattr(request.user, "vault_profile", None)
    if not request.user.is_active or not profile or not profile.active:
        return False
    if profile.role not in {UserProfile.LEADER, UserProfile.ANALYST}:
        return False
    record = SecureSession.objects.select_related("device").filter(
        user=request.user,
        session_hash=session_hash(request),
        status=SecureSession.ACTIVE,
        expires_at__gte=timezone.now(),
    ).first()
    return bool(record and (not record.device_id or record.device.status != UserDevice.BLOCKED))


def current_operation_window(request):
    if not _operator_is_eligible(request):
        return None
    now = timezone.now()
    session_identifier = session_hash(request)
    expired = SensitiveOperationWindow.objects.filter(
        user=request.user,
        session_hash=session_identifier,
        revoked_at__isnull=True,
        expires_at__lt=now,
    )
    expired_ids = list(expired.values_list("pk", flat=True))
    expired_count = expired.update(revoked_at=now, revocation_reason="Expiración fija de 15 minutos")
    if expired_count:
        ProtectedOperationContext.objects.filter(
            identity_window_id__in=expired_ids,
            closed_at__isnull=True,
        ).update(closed_at=now, close_reason="Expiración de reautenticación")
        audit(request, "OPERATION_WINDOW_EXPIRED", metadata={"expired_windows": expired_count})
    return SensitiveOperationWindow.objects.filter(
        user=request.user,
        session_hash=session_identifier,
        purpose="protected_data",
        revoked_at__isnull=True,
        expires_at__gte=now,
    ).order_by("-created_at").first()


def create_operation_window(request):
    """Crea una ventana fija de identidad; nunca almacena justificación operativa."""
    if not _operator_is_eligible(request):
        return None
    now = timezone.now()
    identifier = session_hash(request)
    with transaction.atomic():
        previous = SensitiveOperationWindow.objects.select_for_update().filter(
            user=request.user,
            session_hash=identifier,
            revoked_at__isnull=True,
        )
        previous_ids = list(previous.values_list("pk", flat=True))
        previous.update(revoked_at=now, revocation_reason="Reemplazada por una nueva reautenticación")
        if previous_ids:
            ProtectedOperationContext.objects.filter(
                identity_window_id__in=previous_ids,
                closed_at__isnull=True,
            ).update(closed_at=now, close_reason="Reautenticación reemplazada")
        window = SensitiveOperationWindow.objects.create(
            user=request.user,
            session_hash=identifier,
            purpose="protected_data",
            expires_at=now + timedelta(minutes=WINDOW_TTL_MINUTES),
        )
        audit(
            request,
            "OPERATION_AUTHORIZED",
            metadata={"window_id": str(window.public_id), "expires_in_minutes": WINDOW_TTL_MINUTES},
        )
    return window


def current_operation_context(request, card):
    """Devuelve solo el contexto abierto de esta tarjeta, sesión y ventana vigente."""
    window = current_operation_window(request)
    if not window:
        return None
    now = timezone.now()
    ProtectedOperationContext.objects.filter(
        user=request.user,
        session_hash=session_hash(request),
        closed_at__isnull=True,
        expires_at__lt=now,
    ).update(closed_at=now, close_reason="Expiración del contexto")
    return ProtectedOperationContext.objects.filter(
        identity_window=window,
        user=request.user,
        session_hash=session_hash(request),
        card=card,
        closed_at__isnull=True,
        expires_at__gte=now,
    ).order_by("-created_at").first()


def create_operation_context(request, window, card, reason, internal_reference):
    """Crea una justificación para una tarjeta y sustituye cualquier contexto anterior."""
    if not _operator_is_eligible(request):
        return None
    now = timezone.now()
    identifier = session_hash(request)
    with transaction.atomic():
        locked_window = SensitiveOperationWindow.objects.select_for_update().filter(
            pk=window.pk,
            user=request.user,
            session_hash=identifier,
            purpose="protected_data",
            revoked_at__isnull=True,
            expires_at__gte=now,
        ).first()
        if not locked_window:
            return None
        ProtectedOperationContext.objects.select_for_update().filter(
            user=request.user,
            session_hash=identifier,
            closed_at__isnull=True,
        ).update(closed_at=now, close_reason="Sustituido por una nueva operación")
        context = ProtectedOperationContext.objects.create(
            identity_window=locked_window,
            user=request.user,
            session_hash=identifier,
            card=card,
            reason=reason,
            internal_reference=internal_reference,
            expires_at=locked_window.expires_at,
        )
        audit(
            request,
            "OP_CONTEXT_CONFIRMED",
            card,
            reason=reason,
            metadata={
                "context_id": str(context.public_id),
                "window_id": str(locked_window.public_id),
                "reference": internal_reference,
            },
        )
    return context


def close_operation_contexts(request, reason="Operación finalizada"):
    if not request.user.is_authenticated:
        return 0
    return ProtectedOperationContext.objects.filter(
        user=request.user,
        session_hash=session_hash(request),
        closed_at__isnull=True,
    ).update(closed_at=timezone.now(), close_reason=reason[:120])
