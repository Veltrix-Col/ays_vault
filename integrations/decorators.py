from __future__ import annotations

from functools import wraps

from django.conf import settings
from django.views.decorators.cache import never_cache


def secure_integration_view(view):
    """Evita cachear respuestas administrativas de integración."""

    @never_cache
    @wraps(view)
    def wrapped(*args, **kwargs):
        response = view(*args, **kwargs)
        if getattr(settings, "ZOHO_PUBLIC_SETUP_ENABLED", False):
            response["Cache-Control"] = "no-store, private"
            response["Pragma"] = "no-cache"
            response.setdefault("X-Content-Type-Options", "nosniff")
        return response

    return wrapped
