from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import UserProfile
from .security import audit


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


@receiver(user_login_failed)
def record_login_failure(sender, credentials, request, **kwargs):
    if request is not None:
        audit(request, "LOGIN_FAILED", result="FAILED", risk_level="HIGH")
