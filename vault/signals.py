import logging

from axes.signals import user_locked_out
from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import UserProfile
from .security import audit
from .security import session_hash
from .models import ReauthenticationGrant, RevealGrant, SecureSession
from django.utils import timezone

logger = logging.getLogger("vault.security")


@receiver(post_save, sender=get_user_model())
def ensure_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.get_or_create(user=instance)


@receiver(user_logged_in)
def record_login(sender, request, user, **kwargs):
    request.session.cycle_key()
    audit(request, "LOGIN", user=user)


@receiver(user_logged_out)
def record_logout(sender, request, user, **kwargs):
    if request is not None:
        audit(request, "LOGOUT", user=user)
        if user:
            identifier = session_hash(request)
            SecureSession.objects.filter(user=user, session_hash=identifier, status=SecureSession.ACTIVE).update(status=SecureSession.REVOKED, revoked_at=timezone.now(), revocation_reason="Cierre de sesión")
            ReauthenticationGrant.objects.filter(user=user, session_hash=identifier, invalidated_at__isnull=True).update(invalidated_at=timezone.now())
            RevealGrant.objects.filter(user=user, session_key=identifier).delete()


@receiver(user_login_failed)
def record_login_failure(sender, credentials, request, **kwargs):
    if request is not None:
        audit(request, "LOGIN_FAILED", result="FAILED", risk_level="HIGH")


@receiver(user_locked_out)
def log_axes_lockout(sender, request, username, ip_address, **kwargs):
    logger.warning("Bloqueo de Axes por intentos fallidos: usuario=%s ip=%s", username, ip_address)
