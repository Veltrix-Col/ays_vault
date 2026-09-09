from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured

from .models import NotificacionColectivos, NotificacionCotizacionIndividual
from .actors import get_internal_actor
from .permissions import has_internal_permission


def colectivos_navigation(request):
    navigation = {
        "novedades": has_internal_permission(request, "view_requests"),
        "individual": (
            has_internal_permission(request, "view_requests")
            or has_internal_permission(request, "create_individual_quotation")
        ),
        "invitations": has_internal_permission(request, "view_requests"),
        "inbox": (
            has_internal_permission(request, "view_requests")
        ),
        "billing_exceptions": has_internal_permission(request, "view_billing_exceptions"),
    }
    # Use the same actor boundary as Colectivos views.  In Production a
    # valid Intranet/SSO request may carry an AnonymousUser while
    # ``delegated_access`` identifies the already-authorized internal actor.
    # ``create=False`` keeps this read-only context processor from provisioning
    # actors or weakening the access gate.
    try:
        actor = get_internal_actor(request, create=False)
    except ImproperlyConfigured:
        actor = None
    if actor is None:
        return {"colectivos_unread_notifications": 0, "colectivos_navigation": navigation}
    return {
        "colectivos_unread_notifications": NotificacionColectivos.objects.filter(
            user=actor, read_at__isnull=True,
            notification_type="CLIENT_RESPONSE",
        ).count() + NotificacionCotizacionIndividual.objects.filter(
            user=actor, read_at__isnull=True,
        ).count(),
        "colectivos_navigation": navigation,
    }
