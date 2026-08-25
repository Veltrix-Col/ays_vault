from __future__ import annotations

import hashlib
import re

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import IntranetPrincipal

USERNAME_PREFIX = "sso__"
_UNSAFE_USERNAME_CHARS = re.compile(r"[^\w.@+-]")


def _normalize_subject(subject: str) -> str:
    return subject.strip().casefold()


def _safe_username(normalized_subject: str) -> str:
    cleaned = _UNSAFE_USERNAME_CHARS.sub("_", normalized_subject)
    username = f"{USERNAME_PREFIX}{cleaned}"
    if len(username) <= 150:
        return username
    # El subject no cabe entero en los 150 caracteres de username: se trunca
    # y se agrega un hash corto para no colisionar con otro subject que
    # trunque igual.
    digest = hashlib.sha256(normalized_subject.encode()).hexdigest()[:10]
    budget = 150 - len(USERNAME_PREFIX) - len(digest) - 1
    return f"{USERNAME_PREFIX}{cleaned[:budget]}_{digest}"


def get_or_create_intranet_user(subject: str):
    """Devuelve el User Django ligado a esta identidad SSO, provisionandolo si hace falta.

    Nunca reutiliza ni cruza cuentas de CardManager (vault): la cuenta
    resultante siempre es nueva, con contrasena inutilizable, sin staff ni
    superusuario.
    """
    normalized = _normalize_subject(subject)
    principal = IntranetPrincipal.objects.select_related("user").filter(subject=normalized).first()
    if principal is not None:
        IntranetPrincipal.objects.filter(pk=principal.pk).update(last_seen_at=timezone.now())
        return principal.user

    User = get_user_model()
    username = _safe_username(normalized)
    try:
        with transaction.atomic():
            user = User.objects.create(
                username=username,
                email=normalized if "@" in normalized else "",
                is_active=True,
                is_staff=False,
                is_superuser=False,
            )
            user.set_unusable_password()
            user.save(update_fields=["password"])
            IntranetPrincipal.objects.create(user=user, subject=normalized)
    except IntegrityError:
        # Carrera: otra request provisiono la misma identidad en paralelo.
        return IntranetPrincipal.objects.select_related("user").get(subject=normalized).user
    return user
