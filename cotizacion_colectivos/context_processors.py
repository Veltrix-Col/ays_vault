from __future__ import annotations

from .models import NotificacionColectivos, NotificacionCotizacionIndividual
from .actors import get_internal_actor, public_internal_access_enabled


def colectivos_navigation(request):
    if public_internal_access_enabled():
        actor = get_internal_actor(request, create=False)
        if actor is None:
            return {"colectivos_unread_notifications": 0}
        return {
            "colectivos_unread_notifications": NotificacionColectivos.objects.filter(
                user=actor, read_at__isnull=True, notification_type="CLIENT_RESPONSE",
            ).count() + NotificacionCotizacionIndividual.objects.filter(
                user=actor, read_at__isnull=True,
            ).count()
        }
    if not request.user.is_authenticated or not request.user.is_active:
        return {"colectivos_unread_notifications": 0}
    return {
        "colectivos_unread_notifications": NotificacionColectivos.objects.filter(
            user=request.user, read_at__isnull=True,
            notification_type="CLIENT_RESPONSE",
        ).count() + NotificacionCotizacionIndividual.objects.filter(
            user=request.user, read_at__isnull=True,
        ).count()
    }
