from __future__ import annotations

from .models import NotificacionColectivos, NotificacionCotizacionIndividual
from .actors import get_internal_actor, public_internal_access_enabled
from .permissions import has_internal_permission


def colectivos_navigation(request):
    navigation = {
        "novedades": has_internal_permission(request, "view_requests"),
        "individual": (
            has_internal_permission(request, "view_individual_quotation")
            or has_internal_permission(request, "create_individual_quotation")
        ),
        "invitations": has_internal_permission(request, "view_requests"),
        "inbox": (
            has_internal_permission(request, "view_requests")
            or has_internal_permission(request, "view_individual_quotation")
        ),
    }
    if public_internal_access_enabled():
        actor = get_internal_actor(request, create=False)
        if actor is None:
            return {"colectivos_unread_notifications": 0, "colectivos_navigation": navigation}
        return {
            "colectivos_unread_notifications": NotificacionColectivos.objects.filter(
                user=actor, read_at__isnull=True, notification_type="CLIENT_RESPONSE",
            ).count() + NotificacionCotizacionIndividual.objects.filter(
                user=actor, read_at__isnull=True,
            ).count(),
            "colectivos_navigation": navigation,
        }
    if not request.user.is_authenticated or not request.user.is_active:
        return {"colectivos_unread_notifications": 0, "colectivos_navigation": navigation}
    return {
        "colectivos_unread_notifications": NotificacionColectivos.objects.filter(
            user=request.user, read_at__isnull=True,
            notification_type="CLIENT_RESPONSE",
        ).count() + NotificacionCotizacionIndividual.objects.filter(
            user=request.user, read_at__isnull=True,
        ).count(),
        "colectivos_navigation": navigation,
    }
