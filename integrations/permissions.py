from __future__ import annotations

from functools import wraps

from django.conf import settings

from vault.decorators import role_required
from vault.models import UserProfile

zoho_admin_required = role_required(UserProfile.ADMIN)


def zoho_setup_access(*, public_methods: set[str]):
    """Permite solo los métodos de configuración publicados explícitamente."""

    allowed_public_methods = frozenset(method.upper() for method in public_methods)

    def decorator(view):
        admin_view = zoho_admin_required(view)

        @wraps(view)
        def wrapped(request, *args, **kwargs):
            public_setup = bool(
                getattr(settings, "ZOHO_PUBLIC_SETUP_ENABLED", False)
            )
            if public_setup and request.method.upper() in allowed_public_methods:
                return view(request, *args, **kwargs)
            return admin_view(request, *args, **kwargs)

        return wrapped

    return decorator
