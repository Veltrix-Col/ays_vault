from __future__ import annotations

from .models import NotificacionColectivos


def colectivos_navigation(request):
    if not request.user.is_authenticated or not request.user.is_active:
        return {"colectivos_unread_notifications": 0}
    return {
        "colectivos_unread_notifications": NotificacionColectivos.objects.filter(
            user=request.user, read_at__isnull=True
        ).count()
    }
