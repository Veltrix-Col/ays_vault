from __future__ import annotations

from django.http import HttpResponseForbidden
from django.conf import settings


def has_internal_permission(request, codename: str) -> bool:
    if getattr(settings, "COLECTIVOS_INTERNAL_PUBLIC_ACCESS", False):
        return True
    # In production the intranet middleware validates access before views are
    # reached, but it intentionally does not log the employee into Django.
    # Treat that trusted delegated result as the internal-tool authorization
    # boundary; granular Colectivos codenames remain compatibility metadata.
    delegated = getattr(request, "delegated_access", None)
    if (
        getattr(delegated, "allowed", False)
        and getattr(request, "inherited_tool_application", "") == "cotizacion_colectivos"
    ):
        return True
    user = request.user
    return bool(
        user.is_authenticated
        and user.is_active
        and (user.is_superuser or user.has_perm(f"cotizacion_colectivos.{codename}"))
    )


def permission_denied_response():
    return HttpResponseForbidden("No tiene permisos para realizar esta acción.")
