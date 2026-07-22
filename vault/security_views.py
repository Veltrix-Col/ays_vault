from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from .decorators import role_required
from .forms import ReasonForm, ReauthenticationForm
from .identity import create_alert, current_secure_session, generate_recovery_codes, grant_reauthentication, has_recent_reauth, invalidate_authorizations, reset_user_mfa, revoke_session, role_home_name, verify_totp
from .models import AlertTransition, ReauthenticationGrant, SecureSession, SecurityAlert, UserDevice, UserProfile
from .security import audit
from .security import session_hash
from .crypto import encrypt


PURPOSES = {"reveal", "cards_manage", "identity_admin", "session_manage", "device_manage", "alerts_manage", "password_change", "mfa_manage", "policy_admin", "outside_hours"}


def _safe_next(request, fallback="vault:dashboard"):
    target = request.POST.get("next") or request.GET.get("next")
    if target and url_has_allowed_host_and_scheme(target, {request.get_host()}, require_https=request.is_secure()):
        return target
    return reverse(role_home_name(request.user))


@login_required
@require_http_methods(["GET", "POST"])
def reauthenticate(request):
    purpose = request.POST.get("purpose") or request.GET.get("purpose")
    if purpose not in PURPOSES:
        return HttpResponseBadRequest("Finalidad inválida")
    form = ReauthenticationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if request.user.check_password(form.cleaned_data["password"]) and verify_totp(request.user, form.cleaned_data["token"]):
            grant_reauthentication(request, purpose)
            audit(request, "REAUTH_SUCCESS", reason=purpose)
            return redirect(_safe_next(request))
        event = audit(request, "REAUTH_FAILED", reason=purpose, result="FAILED", risk_level="HIGH")
        create_alert(request, event, "REAUTH_FAILED", "HIGH", request.user, current_secure_session(request).device, "Reautenticación fallida.")
        form.add_error(None, "No fue posible reautenticar la operación.")
    return render(request, "vault/security/reauthenticate.html", {"form": form, "purpose": purpose, "next": _safe_next(request)})


@role_required(UserProfile.ADMIN)
def session_list(request):
    return render(request, "vault/security/sessions.html", {"sessions": request.user.vault_sessions.select_related("device", "revoked_by").order_by("-created_at"), "current": current_secure_session(request)})


@role_required(UserProfile.ADMIN)
@require_POST
def session_revoke(request, pk):
    if not has_recent_reauth(request, "session_manage"):
        return redirect(f"{reverse('vault:reauthenticate')}?purpose=session_manage&next={reverse('vault:sessions')}")
    record = get_object_or_404(SecureSession, pk=pk, user=request.user)
    is_current = current_secure_session(request) == record
    revoke_session(record, actor=request.user, reason="Revocación solicitada por el usuario", request=request)
    messages.success(request, "Sesión revocada.")
    if is_current:
        logout(request)
        return redirect("login")
    return redirect("vault:sessions")


@role_required(UserProfile.ADMIN)
@require_POST
def session_revoke_all(request):
    if not has_recent_reauth(request, "session_manage"):
        return redirect(f"{reverse('vault:reauthenticate')}?purpose=session_manage&next={reverse('vault:sessions')}")
    for record in request.user.vault_sessions.filter(status=SecureSession.ACTIVE):
        revoke_session(record, actor=request.user, reason="Revocación masiva solicitada por el usuario", request=request)
    logout(request)
    return redirect("login")


@role_required(UserProfile.ADMIN)
def identity_user_list(request):
    users = get_user_model().objects.select_related("vault_profile").order_by("username")
    query = request.GET.get("q", "").strip()
    if query:
        users = users.filter(username__icontains=query)
    return render(request, "vault/security/identity_users.html", {"users": users, "query": query})


@role_required(UserProfile.ADMIN)
def admin_session_list(request, user_id):
    target = get_object_or_404(get_user_model(), pk=user_id)
    return render(request, "vault/security/admin_sessions.html", {"target": target, "sessions": target.vault_sessions.select_related("device").order_by("-created_at")})


@role_required(UserProfile.ADMIN)
@require_POST
def admin_session_revoke(request, pk):
    if not has_recent_reauth(request, "identity_admin"):
        return redirect(f"{reverse('vault:reauthenticate')}?purpose=identity_admin&next={request.META.get('HTTP_REFERER', reverse('vault:dashboard'))}")
    form = ReasonForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest("Motivo requerido")
    record = get_object_or_404(SecureSession, pk=pk)
    revoke_session(record, actor=request.user, reason=form.cleaned_data["reason"], request=request)
    event = audit(request, "SESSION_REVOKED", reason=form.cleaned_data["reason"], metadata={"target_user_id": record.user_id})
    create_alert(request, event, "ADMIN_SESSION_REVOCATION", "HIGH", record.user, record.device, "Sesión revocada administrativamente.")
    return redirect("vault:admin_sessions", user_id=record.user_id)


@role_required(UserProfile.ADMIN)
@require_POST
def admin_session_revoke_all(request, user_id):
    if not has_recent_reauth(request, "identity_admin"):
        return redirect(f"{reverse('vault:reauthenticate')}?purpose=identity_admin&next={reverse('vault:admin_sessions', args=[user_id])}")
    form = ReasonForm(request.POST)
    if not form.is_valid(): return HttpResponseBadRequest("Motivo requerido")
    target = get_object_or_404(get_user_model(), pk=user_id)
    for record in target.vault_sessions.filter(status=SecureSession.ACTIVE): revoke_session(record, actor=request.user, reason=form.cleaned_data["reason"], request=request)
    event = audit(request, "SESSION_REVOKED", reason=form.cleaned_data["reason"], metadata={"target_user_id": target.pk, "mass": True})
    create_alert(request, event, "ADMIN_MASS_REVOCATION", "HIGH", target, description="Todas las sesiones fueron revocadas administrativamente.")
    return redirect("vault:admin_sessions", user_id=target.pk)


@role_required(UserProfile.ADMIN)
def device_list(request):
    return render(request, "vault/security/devices.html", {"devices": request.user.vault_devices.order_by("-last_seen_at")})


@role_required(UserProfile.ADMIN)
@require_POST
def device_action(request, pk, action):
    if not has_recent_reauth(request, "device_manage"):
        return redirect(f"{reverse('vault:reauthenticate')}?purpose=device_manage&next={reverse('vault:devices')}")
    device = get_object_or_404(UserDevice, pk=pk, user=request.user)
    if action == "trust":
        device.status = UserDevice.TRUSTED; device.trusted_until = timezone.now() + timedelta(days=90)
        device.save(update_fields=["status", "trusted_until"])
        audit(request, "DEVICE_TRUSTED", metadata={"device_id": device.pk})
    elif action == "block":
        device.status = UserDevice.BLOCKED; device.blocked_at = timezone.now(); device.blocked_by = request.user; device.block_reason = "Bloqueo solicitado por el usuario"
        device.save(update_fields=["status", "blocked_at", "blocked_by", "block_reason"])
        for record in device.sessions.filter(status=SecureSession.ACTIVE): revoke_session(record, actor=request.user, reason="Dispositivo bloqueado")
        event = audit(request, "DEVICE_BLOCKED", risk_level="HIGH", metadata={"device_id": device.pk})
        create_alert(request, event, "DEVICE_BLOCKED", "HIGH", request.user, device, "Dispositivo bloqueado.")
    else:
        return HttpResponseBadRequest("Acción inválida")
    return redirect("vault:devices")


@role_required(UserProfile.ADMIN)
def admin_device_list(request, user_id):
    target = get_object_or_404(get_user_model(), pk=user_id)
    return render(request, "vault/security/admin_devices.html", {"target": target, "devices": target.vault_devices.order_by("-last_seen_at")})


@role_required(UserProfile.ADMIN)
@require_POST
def admin_device_unblock(request, pk):
    if not has_recent_reauth(request, "identity_admin"):
        return redirect(f"{reverse('vault:reauthenticate')}?purpose=identity_admin&next={request.META.get('HTTP_REFERER', reverse('vault:identity_users'))}")
    form = ReasonForm(request.POST)
    if not form.is_valid(): return HttpResponseBadRequest("Motivo requerido")
    device = get_object_or_404(UserDevice, pk=pk, status=UserDevice.BLOCKED)
    device.status = UserDevice.NEW; device.blocked_at = None; device.blocked_by = None; device.block_reason = ""
    device.save(update_fields=["status", "blocked_at", "blocked_by", "block_reason"])
    audit(request, "DEVICE_UNBLOCKED", reason=form.cleaned_data["reason"], metadata={"device_id": device.pk, "target_user_id": device.user_id})
    return redirect("vault:admin_devices", user_id=device.user_id)


@role_required(UserProfile.ADMIN)
@require_http_methods(["GET", "POST"])
def admin_mfa_reset(request, user_id):
    target = get_object_or_404(get_user_model(), pk=user_id)
    form = ReasonForm(request.POST or None)
    if request.method == "POST":
        if not has_recent_reauth(request, "identity_admin"):
            return redirect(f"{reverse('vault:reauthenticate')}?purpose=identity_admin&next={request.path}")
        if form.is_valid():
            reset_user_mfa(request, target, form.cleaned_data["reason"])
            messages.success(request, "MFA reiniciado y sesiones revocadas.")
            return redirect("vault:dashboard")
    return render(request, "vault/security/admin_mfa_reset.html", {"target": target, "form": form})


@login_required
@require_http_methods(["GET", "POST"])
def recovery_codes_regenerate(request):
    if not has_recent_reauth(request, "mfa_manage"):
        return redirect(f"{reverse('vault:reauthenticate')}?purpose=mfa_manage&next={request.path}")
    if request.method == "POST":
        codes = generate_recovery_codes(request.user)
        event = audit(request, "MFA_RECOVERY_REGENERATED")
        create_alert(request, event, "RECOVERY_CODES_REGENERATED", "HIGH", request.user, getattr(current_secure_session(request), "device", None), "Códigos de recuperación regenerados.")
        return render(request, "registration/recovery_codes.html", {"codes": codes, "first_display": True})
    return render(request, "vault/security/recovery_regenerate.html")


def _alert_queryset(user):
    profile = user.vault_profile
    queryset = SecurityAlert.objects.select_related("event", "actor", "affected_user", "device")
    if profile.role == UserProfile.ADMIN:
        return queryset
    if profile.role == UserProfile.LEADER:
        return queryset.filter(affected_user=user) | queryset.filter(alert_type__in=["COPY", "REVEAL", "OUTSIDE_HOURS", "CRITICAL_BLOCKED"])
    return queryset.filter(affected_user=user)


@role_required(UserProfile.ADMIN)
def alert_list(request):
    return render(request, "vault/security/alerts.html", {"alerts": _alert_queryset(request.user).order_by("-created_at")[:250]})


@role_required(UserProfile.ADMIN)
@require_http_methods(["GET", "POST"])
def alert_detail(request, pk):
    alert = get_object_or_404(_alert_queryset(request.user), pk=pk)
    if request.method == "POST":
        if not has_recent_reauth(request, "alerts_manage"):
            return redirect(f"{reverse('vault:reauthenticate')}?purpose=alerts_manage&next={request.path}")
        action = request.POST.get("action", "transition")
        status = request.POST.get("status")
        note = request.POST.get("review_note", "").strip()
        if action == "assign":
            assigned_id = request.POST.get("assigned_to")
            alert.assigned_to = get_object_or_404(get_user_model(), pk=assigned_id) if assigned_id else request.user
            alert.save(update_fields=["assigned_to"])
            AlertTransition.objects.create(alert=alert, from_status=alert.status, to_status=alert.status, action="ASSIGN", comment=note, actor=request.user)
            audit(request, "ALERT_REVIEWED", metadata={"alert_id": alert.pk, "action": "assign", "assigned_to": alert.assigned_to_id})
            return redirect("vault:alert_detail", pk=alert.pk)
        if status not in dict(SecurityAlert.STATUSES) or status in {"JUSTIFIED", "CLOSED"} and len(note) < 5:
            return HttpResponseBadRequest("Estado o comentario inválido")
        escalation = request.POST.get("escalated_to", "").strip()
        if status == "ESCALATED" and not escalation:
            return HttpResponseBadRequest("Escalamiento requiere destinatario o grupo")
        previous = alert.status
        alert.status = status; alert.review_note = note; alert.reviewed_at = timezone.now(); alert.reviewed_by = request.user
        if status == "ESCALATED": alert.escalated_to = escalation
        if status == "CLOSED": alert.closed_at = timezone.now()
        elif previous == "CLOSED": alert.closed_at = None
        alert.save(update_fields=["status", "review_note", "reviewed_at", "reviewed_by", "closed_at", "escalated_to"])
        AlertTransition.objects.create(alert=alert, from_status=previous, to_status=status, action=action.upper()[:20], comment=note, actor=request.user)
        audit_action = "ALERT_CLOSED" if status == "CLOSED" else "ALERT_ESCALATED" if status == "ESCALATED" else "ALERT_REOPENED" if status == "REOPENED" else "ALERT_REVIEWED"
        audit(request, audit_action, reason=note, metadata={"alert_id": alert.pk, "status": status, "previous_status": previous, "escalated_to": bool(escalation)})
        return redirect("vault:alert_detail", pk=alert.pk)
    assignees = get_user_model().objects.filter(vault_profile__role=UserProfile.ADMIN, vault_profile__active=True)
    return render(request, "vault/security/alert_detail.html", {"alert": alert, "assignees": assignees})


@login_required
@require_http_methods(["GET", "POST"])
def password_change(request):
    if not has_recent_reauth(request, "password_change"):
        return redirect(f"{reverse('vault:reauthenticate')}?purpose=password_change&next={request.path}")
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        current = current_secure_session(request)
        user = form.save(); update_session_auth_hash(request, user)
        if current:
            current.session_hash = session_hash(request)
            current.encrypted_session_key = encrypt(request.session.session_key)
            current.save(update_fields=["session_hash", "encrypted_session_key"])
        for record in user.vault_sessions.filter(status=SecureSession.ACTIVE).exclude(pk=getattr(current, "pk", None)):
            revoke_session(record, actor=user, reason="Cambio de contraseña")
        invalidate_authorizations(user)
        user.vault_devices.filter(status=UserDevice.TRUSTED).update(status=UserDevice.NEW, trusted_until=None)
        user.vault_profile.password_changed_at = timezone.now(); user.vault_profile.save(update_fields=["password_changed_at"])
        event = audit(request, "PASSWORD_CHANGED")
        create_alert(request, event, "PASSWORD_CHANGED", "MEDIUM", user, getattr(current, "device", None), "Contraseña modificada.")
        messages.success(request, "Contraseña actualizada; otras sesiones fueron revocadas.")
        return redirect(role_home_name(request.user))
    return render(request, "vault/security/password_change.html", {"form": form})
