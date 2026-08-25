import logging

from axes.signals import user_locked_out
from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.db.models.signals import post_save
from django.dispatch import receiver

from intranet_sso.provisioning import USERNAME_PREFIX as INTRANET_SSO_USERNAME_PREFIX

from .models import UserProfile
from .security import audit
from .security import session_hash
from .models import ProtectedOperationContext, ReauthenticationGrant, RevealGrant, SecureSession, SensitiveOperationWindow
from django.utils import timezone

logger = logging.getLogger("vault.security")


def _is_intranet_sso_account(user) -> bool:
    return bool(user) and str(getattr(user, "username", "")).startswith(INTRANET_SSO_USERNAME_PREFIX)


@receiver(post_save, sender=get_user_model())
def ensure_profile(sender, instance, created, **kwargs):
    # Las cuentas provisionadas desde el SSO de intranet (soat, conciliacion,
    # cotizacion_colectivos) no son usuarios de CardManager: no deben tener
    # perfil de vault ni aparecer en su administracion de usuarios.
    if created and not _is_intranet_sso_account(instance):
        UserProfile.objects.get_or_create(user=instance)


@receiver(user_logged_in)
def record_login(sender, request, user, **kwargs):
    request.session.cycle_key()
    # El login SSO de las apps heredadas (soat, conciliacion,
    # cotizacion_colectivos) no es un evento de CardManager: no debe entrar
    # a su bitacora de auditoria con cadena de hashes.
    if not _is_intranet_sso_account(user):
        audit(request, "LOGIN", user=user)


@receiver(user_logged_out)
def record_logout(sender, request, user, **kwargs):
    if request is not None:
        if not _is_intranet_sso_account(user):
            audit(request, "LOGOUT", user=user)
        if user:
            identifier = session_hash(request)
            now = timezone.now()
            SecureSession.objects.filter(user=user, session_hash=identifier, status=SecureSession.ACTIVE).update(status=SecureSession.REVOKED, revoked_at=now, revocation_reason="Cierre de sesión")
            ReauthenticationGrant.objects.filter(user=user, session_hash=identifier, invalidated_at__isnull=True).update(invalidated_at=now)
            RevealGrant.objects.filter(user=user, session_key=identifier).delete()
            ProtectedOperationContext.objects.filter(user=user, session_hash=identifier, closed_at__isnull=True).update(closed_at=now, close_reason="Cierre de sesión")
            SensitiveOperationWindow.objects.filter(user=user, session_hash=identifier, revoked_at__isnull=True).update(revoked_at=now, revocation_reason="Cierre de sesión")


@receiver(user_login_failed)
def record_login_failure(sender, credentials, request, **kwargs):
    if request is not None:
        audit(request, "LOGIN_FAILED", result="FAILED", risk_level="HIGH")


@receiver(user_locked_out)
def log_axes_lockout(sender, request, username, ip_address, **kwargs):
    logger.warning("Bloqueo de Axes por intentos fallidos: usuario=%s ip=%s", username, ip_address)
