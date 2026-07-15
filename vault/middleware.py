from .security import audit
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.utils import timezone
from datetime import timedelta

from .identity import current_secure_session, invalidate_authorizations, revoke_session
from .models import SecureSession, UserDevice
from .policies import get_policy


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'")
        response.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
        if request.path.startswith("/vault/"):
            response.setdefault("Cache-Control", "no-store, no-cache, must-revalidate, private")
            response.setdefault("Pragma", "no-cache")
        return response


class AuditAccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.user.is_authenticated and request.path.startswith("/vault/") and response.status_code in {401, 403}:
            audit(request, "DENIED", result="DENIED", risk_level="HIGH", metadata={"status_code": response.status_code})
        return response


class SecureSessionMiddleware:
    PUBLIC_PATHS = {"/login/", "/login/mfa/", "/mfa/enroll/", "/logout/"}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.user.is_authenticated:
            return self.get_response(request)
        if request.path.startswith("/static/"):
            return self.get_response(request)
        if request.path in self.PUBLIC_PATHS:
            return self.get_response(request)
        if request.session.get("recovery_codes_pending") and request.path != "/mfa/recovery-codes/":
            return redirect("recovery_codes_confirm")
        if not request.user.is_verified():
            logout(request)
            return redirect("login")
        record = current_secure_session(request)
        if not record:
            logout(request)
            return redirect("login")
        now = timezone.now()
        inactivity_seconds = get_policy().session_inactivity_minutes * 60
        if record.status != SecureSession.ACTIVE or record.last_activity_at < now - timedelta(seconds=inactivity_seconds):
            audit(request, "SESSION_EXPIRED", reason="Expiración por inactividad", metadata={"secure_session_id": record.pk})
            record.status = SecureSession.EXPIRED
            record.revoked_at = now
            record.revocation_reason = "Expiración por inactividad"
            record.save(update_fields=["status", "revoked_at", "revocation_reason"])
            invalidate_authorizations(request.user, record.session_hash)
            logout(request)
            messages.info(request, "La sesión expiró por inactividad.")
            return redirect("login")
        if record.device_id and record.device.status == UserDevice.BLOCKED:
            revoke_session(record, actor=request.user, reason="Dispositivo bloqueado")
            logout(request)
            return redirect("login")
        if record.last_activity_at < now - timedelta(seconds=settings.SESSION_ACTIVITY_THROTTLE_SECONDS):
            record.last_activity_at = now
            record.expires_at = now + timedelta(seconds=inactivity_seconds)
            record.last_ip = request.META.get("REMOTE_ADDR")
            record.save(update_fields=["last_activity_at", "expires_at", "last_ip"])
        return self.get_response(request)
