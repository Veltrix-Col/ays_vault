import hashlib
import hmac
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.sessions.models import Session
from django.db import transaction
from django.utils import timezone
from django_otp import login as otp_login
from django_otp.plugins.otp_totp.models import TOTPDevice

from .crypto import decrypt, encrypt
from .models import MFARecoveryCode, ProtectedOperationContext, ReauthenticationGrant, RevealGrant, SecureSession, SecurityAlert, SensitiveOperationWindow, UserDevice, UserProfile
from .security import audit, client_ip, outside_hours, session_hash
from .policies import get_policy


def _ua_parts(user_agent):
    ua = (user_agent or "")[:300]
    browser = "Edge" if "Edg/" in ua else "Chrome" if "Chrome/" in ua else "Firefox" if "Firefox/" in ua else "Safari" if "Safari/" in ua else "Otro"
    os_name = "Windows" if "Windows" in ua else "Android" if "Android" in ua else "iOS" if "iPhone" in ua or "iPad" in ua else "macOS" if "Mac OS" in ua else "Linux" if "Linux" in ua else "Otro"
    device_type = "Móvil" if any(marker in ua for marker in ("Mobile", "Android", "iPhone")) else "Escritorio"
    return ua, browser, os_name, device_type


def device_fingerprint(user_agent):
    normalized = " ".join((user_agent or "").lower().split())[:300]
    key = settings.FIELD_FINGERPRINT_KEY.encode() if settings.FIELD_FINGERPRINT_KEY else settings.FIELD_ENCRYPTION_KEY.encode()
    return hmac.new(key, f"device:{normalized}".encode(), hashlib.sha256).hexdigest()


def create_alert(request, event, alert_type, severity, affected_user=None, device=None, description="", metadata=None):
    alert, _ = SecurityAlert.objects.update_or_create(event=event, defaults={
        "alert_type": alert_type, "severity": severity, "actor": request.user if request.user.is_authenticated else None,
        "affected_user": affected_user, "ip_address": client_ip(request), "device": device,
        "description": description[:240], "safe_metadata": metadata or {},
    })
    from .notifications import notify_alert_async
    transaction.on_commit(lambda: notify_alert_async(alert))
    return alert


def get_or_register_device(request, user):
    ua, browser, os_name, device_type = _ua_parts(request.META.get("HTTP_USER_AGENT", ""))
    fingerprint = device_fingerprint(ua)
    device, created = UserDevice.objects.get_or_create(user=user, fingerprint_hash=fingerprint, defaults={
        "user_agent": ua, "browser": browser, "operating_system": os_name, "device_type": device_type,
        "friendly_name": f"{browser} en {os_name}", "initial_ip": client_ip(request), "last_ip": client_ip(request),
    })
    if not created:
        previous_ip = device.last_ip
        device.last_ip = client_ip(request)
        if device.status == UserDevice.TRUSTED and device.trusted_until and device.trusted_until < timezone.now():
            device.status = UserDevice.NEW
            device.trusted_until = None
        device.user_agent = ua
        device.save(update_fields=["last_ip", "user_agent", "status", "trusted_until", "last_seen_at"])
        if previous_ip and previous_ip != device.last_ip:
            event = audit(request, "DEVICE_NEW", user=user, risk_level="MEDIUM", metadata={"device_id": device.pk, "new_ip": True})
            create_alert(request, event, "NEW_IP", "MEDIUM", user, device, "Acceso desde una IP nueva.")
    else:
        event = audit(request, "DEVICE_NEW", user=user, risk_level="MEDIUM", metadata={"device_id": device.pk})
        create_alert(request, event, "NEW_DEVICE", "MEDIUM", user, device, "Acceso desde un dispositivo nuevo.")
    return device, created


def confirmed_totp_device(user):
    return TOTPDevice.objects.filter(user=user, confirmed=True).order_by("pk").first()


def verify_totp(user, token):
    device = confirmed_totp_device(user)
    return device if device and device.verify_token(token) else None


def generate_recovery_codes(user, count=10):
    MFARecoveryCode.objects.filter(user=user).delete()
    values = [f"{secrets.token_hex(4)[:4]}-{secrets.token_hex(4)[:4]}".upper() for _ in range(count)]
    MFARecoveryCode.objects.bulk_create([MFARecoveryCode(user=user, code_hash=make_password(value)) for value in values])
    return values


def consume_recovery_code(user, value):
    with transaction.atomic():
        for code in MFARecoveryCode.objects.select_for_update().filter(user=user, used_at__isnull=True):
            if check_password((value or "").strip().upper(), code.code_hash):
                code.used_at = timezone.now()
                code.save(update_fields=["used_at"])
                return True
    return False


def invalidate_authorizations(user, session_identifier=None):
    now = timezone.now()
    grants = ReauthenticationGrant.objects.filter(user=user, invalidated_at__isnull=True)
    if session_identifier:
        grants = grants.filter(session_hash=session_identifier)
    grants.update(invalidated_at=now)
    reveals = RevealGrant.objects.filter(user=user, copied_at__isnull=True)
    if session_identifier:
        reveals = reveals.filter(session_key=session_identifier)
    reveals.delete()
    contexts = ProtectedOperationContext.objects.filter(user=user, closed_at__isnull=True)
    windows = SensitiveOperationWindow.objects.filter(user=user, revoked_at__isnull=True)
    if session_identifier:
        contexts = contexts.filter(session_hash=session_identifier)
        windows = windows.filter(session_hash=session_identifier)
    contexts.update(closed_at=now, close_reason="Autorizaciones de sesión invalidadas")
    windows.update(revoked_at=now, revocation_reason="Autorizaciones de sesión invalidadas")


def role_home_name(user):
    profile = getattr(user, "vault_profile", None)
    return "vault:control_center" if profile and profile.role == UserProfile.ADMIN else "vault:card_list"


def _delete_django_session(record):
    try:
        Session.objects.filter(session_key=decrypt(record.encrypted_session_key)).delete()
    except ValueError:
        pass


def revoke_session(record, actor=None, reason="Revocación", request=None, action="SESSION_REVOKED"):
    if record.status != SecureSession.ACTIVE:
        return
    with transaction.atomic():
        record.status = SecureSession.REVOKED
        record.revoked_at = timezone.now()
        record.revoked_by = actor
        record.revocation_reason = reason[:240]
        record.save(update_fields=["status", "revoked_at", "revoked_by", "revocation_reason"])
        _delete_django_session(record)
        invalidate_authorizations(record.user, record.session_hash)
    if request:
        audit(request, action, user=actor or record.user, reason=reason, metadata={"target_user_id": record.user_id, "secure_session_id": record.pk})


def establish_secure_session(request, user, otp_device, device):
    otp_login(request, otp_device)
    if request.session.session_key is None:
        request.session.save()
    current_hash = session_hash(request)
    with transaction.atomic():
        previous = list(SecureSession.objects.select_for_update().filter(user=user, status=SecureSession.ACTIVE).exclude(session_hash=current_hash))
        policy = get_policy()
        if policy.new_session_policy == "ALLOW_LIMIT":
            keep_count = max(policy.maximum_sessions - 1, 0)
            previous = sorted(previous, key=lambda item: item.last_activity_at, reverse=True)[keep_count:]
        for record in previous:
            revoke_session(record, actor=user, reason="Reemplazada por un nuevo inicio de sesión", request=request, action="SESSION_REPLACED")
        if previous:
            event = audit(request, "SESSION_REPLACED", user=user, risk_level="HIGH", metadata={"replaced_count": len(previous)})
            create_alert(request, event, "SESSION_REPLACED", "HIGH", user, device, "Una nueva sesión revocó sesiones anteriores.")
        invalidate_authorizations(user)
        now = timezone.now()
        record, _ = SecureSession.objects.update_or_create(session_hash=current_hash, defaults={
            "user": user, "encrypted_session_key": encrypt(request.session.session_key), "device": device,
            "last_activity_at": now, "expires_at": now + timedelta(minutes=get_policy().session_inactivity_minutes),
            "initial_ip": client_ip(request), "last_ip": client_ip(request), "user_agent": device.user_agent,
            "browser": device.browser, "operating_system": device.operating_system, "device_type": device.device_type,
            "friendly_name": device.friendly_name, "status": SecureSession.ACTIVE, "mfa_completed": True,
            "mfa_completed_at": now, "new_device": device.status == UserDevice.NEW,
            "outside_office_hours": outside_hours(), "trust_level": "MEDIUM" if device.status == UserDevice.TRUSTED else "LOW",
        })
    audit(request, "SESSION_CREATED", user=user, metadata={"secure_session_id": record.pk, "device_id": device.pk})
    return record


def current_secure_session(request):
    return SecureSession.objects.filter(user=request.user, session_hash=session_hash(request), status=SecureSession.ACTIVE).first()


def has_recent_reauth(request, purpose):
    return ReauthenticationGrant.objects.filter(user=request.user, session_hash=session_hash(request), purpose=purpose, invalidated_at__isnull=True, expires_at__gte=timezone.now()).exists()


def grant_reauthentication(request, purpose):
    now = timezone.now()
    ReauthenticationGrant.objects.filter(user=request.user, session_hash=session_hash(request), purpose=purpose, invalidated_at__isnull=True).update(invalidated_at=now)
    grant = ReauthenticationGrant.objects.create(user=request.user, session_hash=session_hash(request), purpose=purpose, expires_at=now + timedelta(minutes=get_policy().reauthentication_minutes))
    SecureSession.objects.filter(user=request.user, session_hash=session_hash(request)).update(last_reauthenticated_at=now)
    return grant


def reset_user_mfa(request, target_user, reason):
    with transaction.atomic():
        TOTPDevice.objects.filter(user=target_user).delete()
        MFARecoveryCode.objects.filter(user=target_user).delete()
        invalidate_authorizations(target_user)
        for record in SecureSession.objects.select_for_update().filter(user=target_user, status=SecureSession.ACTIVE):
            revoke_session(record, actor=request.user, reason="Reinicio administrativo de MFA")
        target_user.vault_devices.filter(status=UserDevice.TRUSTED).update(status=UserDevice.NEW, trusted_until=None)
        profile = target_user.vault_profile
        profile.mfa_status = UserProfile.MFA_PENDING
        profile.mfa_enabled = False
        profile.mfa_failed_attempts = 0
        profile.mfa_changed_at = timezone.now()
        profile.save(update_fields=["mfa_status", "mfa_enabled", "mfa_failed_attempts", "mfa_changed_at"])
    event = audit(request, "MFA_RESET", reason=reason, metadata={"target_user_id": target_user.pk})
    create_alert(request, event, "MFA_RESET", "HIGH", target_user, description="MFA reiniciado administrativamente.")
