from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction


def public_internal_access_enabled() -> bool:
    return bool(getattr(settings, "COLECTIVOS_INTERNAL_PUBLIC_ACCESS", False))


def _delegated_internal_access_enabled(request) -> bool:
    delegated = getattr(request, "delegated_access", None)
    return bool(
        getattr(delegated, "allowed", False)
        and getattr(request, "inherited_tool_application", "") == "cotizacion_colectivos"
    )


def get_internal_actor(request, *, create: bool = False):
    """Resolve the real user or the isolated non-privileged QA actor.

    The actor is never inferred from database ordering and is never a
    superuser. Creation is allowed only at a mutation boundary.
    """

    if not public_internal_access_enabled() and not _delegated_internal_access_enabled(request):
        user = request.user
        if not user.is_authenticated or not user.is_active:
            raise ImproperlyConfigured("No existe un actor interno autenticado.")
        return user

    username = str(getattr(settings, "COLECTIVOS_TECHNICAL_ACTOR_USERNAME", "")).strip()
    if not username:
        raise ImproperlyConfigured("El actor tecnico de Colectivos no esta configurado.")
    users = get_user_model().objects
    if create:
        with transaction.atomic():
            actor, created = users.get_or_create(
                username=username,
                defaults={"is_active": True, "is_staff": False, "is_superuser": False},
            )
            if created:
                actor.set_unusable_password()
                actor.save(update_fields=("password",))
    else:
        actor = users.filter(username=username).first()
        if actor is None:
            return None
    if (
        not actor.is_active
        or actor.is_staff
        or actor.is_superuser
        or actor.has_usable_password()
    ):
        raise ImproperlyConfigured("El actor tecnico de Colectivos no es seguro.")
    return actor
