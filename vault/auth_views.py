import base64
from datetime import timedelta

import segno
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.db.models import F
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST
from django_otp.plugins.otp_totp.models import TOTPDevice

from .forms import MFAEnrollmentForm, OTPVerificationForm, PasswordLoginForm
from .identity import confirmed_totp_device, consume_recovery_code, create_alert, establish_secure_session, generate_recovery_codes, get_or_register_device, verify_totp
from .models import UserDevice, UserProfile
from .security import audit


PREAUTH_TTL = 300


def _set_preauth(request, user):
    request.session.flush()
    request.session["preauth_user_id"] = user.pk
    request.session["preauth_at"] = timezone.now().timestamp()
    request.session.set_expiry(PREAUTH_TTL)


def _preauth_user(request):
    user_id = request.session.get("preauth_user_id")
    created = request.session.get("preauth_at", 0)
    if not user_id or timezone.now().timestamp() - created > PREAUTH_TTL:
        request.session.flush()
        return None
    return get_user_model().objects.filter(pk=user_id, is_active=True).select_related("vault_profile").first()


def _complete_login(request, user, otp_device, device):
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    establish_secure_session(request, user, otp_device, device)
    request.session.set_expiry(settings.SESSION_INACTIVITY_SECONDS)


def _mfa_failure(request, user, device):
    UserProfile.objects.filter(pk=user.vault_profile.pk).update(mfa_failed_attempts=F("mfa_failed_attempts") + 1)
    user.vault_profile.refresh_from_db()
    event = audit(request, "MFA_FAILED", user=user, result="FAILED", risk_level="HIGH", metadata={"device_id": device.pk})
    if user.vault_profile.mfa_failed_attempts >= settings.MFA_FAILURE_LIMIT:
        user.vault_profile.mfa_status = UserProfile.MFA_BLOCKED
        user.vault_profile.save(update_fields=["mfa_status"])
        create_alert(request, event, "MFA_BLOCKED", "CRITICAL", user, device, "MFA bloqueado por intentos fallidos.")


@never_cache
@require_http_methods(["GET", "POST"])
def password_login(request):
    if request.user.is_authenticated:
        return redirect("vault:dashboard")
    form = PasswordLoginForm(request.POST or None, request=request)
    if request.method == "POST" and form.is_valid():
        user = form.user
        profile = getattr(user, "vault_profile", None)
        if not profile or not profile.active or not profile.role:
            form.add_error(None, "No fue posible completar el acceso.")
        else:
            device, _ = get_or_register_device(request, user)
            if device.status == UserDevice.BLOCKED:
                event = audit(request, "DEVICE_BLOCKED", user=user, result="DENIED", risk_level="CRITICAL", metadata={"device_id": device.pk})
                create_alert(request, event, "BLOCKED_DEVICE_LOGIN", "CRITICAL", user, device, "Intento de acceso bloqueado.")
                form.add_error(None, "No fue posible completar el acceso.")
            else:
                _set_preauth(request, user)
                audit(request, "PASSWORD_OK", user=user)
                audit(request, "MFA_REQUIRED", user=user)
                if profile.mfa_status in {UserProfile.MFA_NOT_CONFIGURED, UserProfile.MFA_PENDING, UserProfile.MFA_RECOVERY}:
                    profile.mfa_status = UserProfile.MFA_PENDING
                    profile.save(update_fields=["mfa_status"])
                    return redirect("mfa_enroll")
                if profile.mfa_status == UserProfile.MFA_ACTIVE:
                    return redirect("mfa_verify")
                form.add_error(None, "No fue posible completar el acceso.")
    return render(request, "registration/login.html", {"form": form})


@never_cache
@require_http_methods(["GET", "POST"])
def mfa_verify(request):
    user = _preauth_user(request)
    if not user or user.vault_profile.mfa_status != UserProfile.MFA_ACTIVE:
        return redirect("login")
    device, _ = get_or_register_device(request, user)
    if device.status == UserDevice.BLOCKED:
        return redirect("login")
    form = OTPVerificationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        otp_device = verify_totp(user, form.cleaned_data.get("token")) if form.cleaned_data.get("token") else confirmed_totp_device(user) if consume_recovery_code(user, form.cleaned_data.get("recovery_code")) else None
        if otp_device:
            recovery_used = bool(form.cleaned_data.get("recovery_code"))
            user.vault_profile.mfa_failed_attempts = 0
            user.vault_profile.save(update_fields=["mfa_failed_attempts"])
            _complete_login(request, user, otp_device, device)
            event = audit(request, "MFA_RECOVERY_USED" if recovery_used else "MFA_SUCCESS", user=user, risk_level="HIGH" if recovery_used else "LOW")
            if recovery_used:
                create_alert(request, event, "RECOVERY_CODE_USED", "HIGH", user, device, "Se utilizó un código de recuperación.")
            return redirect("vault:dashboard")
        _mfa_failure(request, user, device)
        form.add_error(None, "No fue posible validar el segundo factor.")
    return render(request, "registration/mfa_verify.html", {"form": form})


@never_cache
@require_http_methods(["GET", "POST"])
def mfa_enroll(request):
    user = _preauth_user(request)
    if not user or user.vault_profile.mfa_status not in {UserProfile.MFA_PENDING, UserProfile.MFA_NOT_CONFIGURED, UserProfile.MFA_RECOVERY}:
        return redirect("login")
    device, created = TOTPDevice.objects.get_or_create(user=user, confirmed=False, defaults={"name": "A&S Vault"})
    if created:
        audit(request, "MFA_ENROLL_START", user=user)
    form = MFAEnrollmentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if user.check_password(form.cleaned_data["password"]) and device.verify_token(form.cleaned_data["token"]):
            TOTPDevice.objects.filter(user=user, confirmed=True).delete()
            device.confirmed = True; device.save(update_fields=["confirmed"])
            user.vault_profile.mfa_status = UserProfile.MFA_ACTIVE
            user.vault_profile.mfa_enabled = True
            user.vault_profile.mfa_failed_attempts = 0
            user.vault_profile.mfa_changed_at = timezone.now()
            user.vault_profile.save(update_fields=["mfa_status", "mfa_enabled", "mfa_failed_attempts", "mfa_changed_at"])
            codes = generate_recovery_codes(user)
            browser_device, _ = get_or_register_device(request, user)
            _complete_login(request, user, device, browser_device)
            request.session["recovery_codes_pending"] = True
            audit(request, "MFA_ENROLL_COMPLETE", user=user)
            return render(request, "registration/recovery_codes.html", {"codes": codes, "first_display": True})
        form.add_error(None, "No fue posible confirmar el enrolamiento.")
    manual_key = base64.b32encode(device.bin_key).decode().rstrip("=")
    qr_data_uri = segno.make(device.config_url).svg_data_uri(scale=5)
    return render(request, "registration/mfa_enroll.html", {"form": form, "manual_key": manual_key, "qr_data_uri": qr_data_uri})


@login_required
@require_http_methods(["GET", "POST"])
def recovery_codes_confirm(request):
    if request.method == "POST":
        request.session.pop("recovery_codes_pending", None)
        return redirect("vault:dashboard")
    return render(request, "registration/recovery_codes.html", {"codes": [], "first_display": False})


@require_POST
def secure_logout(request):
    logout(request)
    messages.info(request, "Sesión cerrada de forma segura.")
    return redirect("login")
