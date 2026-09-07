from __future__ import annotations

from django.http import HttpResponseForbidden
from django.conf import settings


# Generar/regenerar el enlace externo de una solicitud no exige nada mas alla
# de haber llegado a esta vista: a diferencia de CardManager (vault de
# tarjetas), Colectivos no tiene su propio login de Django -- el unico gate
# es TrustedIntranetAccessMiddleware (SSO delegado de intranet), que ya
# bloqueo cualquier request que no viniera validada antes de que el codigo de
# la vista se ejecute. El enlace resultante se protege con su propio OTP por
# correo y no otorga acceso de vuelta al aplicativo.
_SSO_ONLY_CODENAMES = {"generate_external_access", "regenerate_external_access"}


def has_internal_permission(request, codename: str) -> bool:
    if getattr(settings, "COLECTIVOS_INTERNAL_PUBLIC_ACCESS", False):
        return True
    if codename in _SSO_ONLY_CODENAMES:
        return True
    # El middleware de intranet ya aprovisiona y loguea un User Django
    # dedicado (sso__..., sin permisos ni grupos) antes de llegar aca, asi
    # que ese usuario nunca pasa el has_perm de abajo. El resultado delegado
    # que valido el middleware ES el boundary de autorizacion para el resto
    # de Colectivos -- sin este bypass, cualquier accion fuera de
    # _SSO_ONLY_CODENAMES le devuelve 403 a un visitante SSO legitimo.
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
