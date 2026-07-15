from datetime import time
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone
from .models import AuditEvent, SecurityAlert

def client_ip(request):
    return (request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() or request.META.get("REMOTE_ADDR"))

def outside_hours():
    now = timezone.localtime()
    start = time.fromisoformat(settings.OFFICE_START)
    end = time.fromisoformat(settings.OFFICE_END)
    current = now.time().replace(tzinfo=None)
    return now.weekday() >= 5 or not (start <= current <= end)

def audit(request, action, card=None, field_name="", reason="", metadata=None):
    event = AuditEvent.objects.create(
        user=request.user if request.user.is_authenticated else None,
        action=action, card=card, field_name=field_name, reason=reason,
        ip_address=client_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:300],
        outside_office_hours=outside_hours(), metadata=metadata or {},
    )
    if event.outside_office_hours and action in {"LOGIN", "REVEAL", "COPY", "CREATE", "UPDATE", "DEACTIVATE"}:
        SecurityAlert.objects.create(event=event)
        if settings.ALERT_EMAIL:
            body = "\n".join([
                f"Usuario: {event.user}",
                f"Acción: {event.get_action_display()}",
                f"Tarjeta: **** {card.last4 if card else 'N/A'}",
                f"Fecha: {timezone.localtime(event.created_at)}",
                f"IP: {event.ip_address}",
                f"Motivo: {reason}",
            ])
            send_mail(
                f"[A&S Bóveda] Evento fuera de horario: {event.get_action_display()}",
                body, settings.DEFAULT_FROM_EMAIL, [settings.ALERT_EMAIL], fail_silently=True,
            )
    return event
