from __future__ import annotations

from django.http import HttpResponseForbidden


def has_internal_permission(request, codename: str) -> bool:
    user = request.user
    return bool(
        user.is_authenticated
        and user.is_active
        and (user.is_superuser or user.has_perm(f"cotizacion_colectivos.{codename}"))
    )


def permission_denied_response():
    return HttpResponseForbidden("No tiene permisos para realizar esta acción.")
