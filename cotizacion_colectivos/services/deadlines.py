from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from vault.crypto import decrypt
from vault.notifications import send_notification

from ..models import (
    AccesoExternoSolicitudColectivo,
    EventoSolicitudColectivo,
    SolicitudColectivo,
    VistaPreviaExcelSolicitudColectivo,
)
from .excel_previews import _delete
from .external import _notify


@dataclass
class DeadlineResult:
    accesses_expired: int = 0
    otp_expired: int = 0
    previews_expired: int = 0
    external_sessions_expired: int = 0
    requests_near_due: int = 0
    requests_expired: int = 0
    reminders_sent: int = 0
    expiration_notices_sent: int = 0
    cancellation_notices_sent: int = 0

    def safe_dict(self):
        return asdict(self)


def _email(access, *, kind: str, subject: str, message: str, key: str, dry_run: bool) -> bool:
    if dry_run or not settings.COLECTIVOS_DEADLINE_EMAIL_ENABLED:
        return False
    record = send_notification(
        notification_type=kind,
        recipient=decrypt(access.encrypted_recipient),
        subject=subject,
        text_body=message,
        html_body=f"<p>{message}</p>",
        idempotency_key=key,
    )
    return record.result == "SENT"


def process_deadlines(*, now=None, limit=None, dry_run=False) -> DeadlineResult:
    result = DeadlineResult()
    if not settings.COLECTIVOS_DEADLINES_ENABLED:
        return result
    now = now or timezone.now()
    if timezone.is_naive(now):
        now = timezone.make_aware(now)
    today = timezone.localtime(now).date()
    limit = min(limit or settings.COLECTIVOS_DEADLINE_BATCH_LIMIT, settings.COLECTIVOS_DEADLINE_BATCH_LIMIT)

    expired_accesses = list(AccesoExternoSolicitudColectivo.objects.filter(
        status__in=[AccesoExternoSolicitudColectivo.Status.ACTIVE, AccesoExternoSolicitudColectivo.Status.VERIFIED],
        expires_at__lte=now,
    ).order_by("pk")[:limit])
    result.accesses_expired = len(expired_accesses)
    expired_otps = list(AccesoExternoSolicitudColectivo.objects.filter(otp_expires_at__lte=now).exclude(otp_hash="").order_by("pk")[:limit])
    result.otp_expired = len(expired_otps)
    expired_previews = list(VistaPreviaExcelSolicitudColectivo.objects.filter(status=VistaPreviaExcelSolicitudColectivo.Status.PENDING, expires_at__lte=now).order_by("pk")[:limit])
    result.previews_expired = len(expired_previews)

    expirable = [SolicitudColectivo.Status.READY, SolicitudColectivo.Status.SENT, SolicitudColectivo.Status.OPENED, SolicitudColectivo.Status.CORRECTION]
    expired_requests = list(SolicitudColectivo.objects.filter(status__in=expirable, deadline__lt=today).order_by("pk")[:limit])
    result.requests_expired = len(expired_requests)
    near_until = today + timedelta(days=settings.COLECTIVOS_DEADLINE_REMINDER_DAYS)
    near_requests = list(SolicitudColectivo.objects.filter(
        status__in=[SolicitudColectivo.Status.SENT, SolicitudColectivo.Status.OPENED, SolicitudColectivo.Status.CORRECTION],
        deadline__gte=today, deadline__lte=near_until,
    ).order_by("pk")[:limit])
    result.requests_near_due = len(near_requests)
    cancelled = list(SolicitudColectivo.objects.filter(status=SolicitudColectivo.Status.CANCELLED).order_by("pk")[:limit])

    if dry_run:
        return result

    with transaction.atomic():
        for access in expired_accesses:
            access.status = access.Status.EXPIRED
            access.otp_hash = ""
            access.otp_expires_at = None
            access.save(update_fields=("status", "otp_hash", "otp_expires_at"))
        for access in expired_otps:
            access.otp_hash = ""
            access.otp_expires_at = None
            access.save(update_fields=("otp_hash", "otp_expires_at"))
        for preview in expired_previews:
            preview.status = preview.Status.EXPIRED
            preview.consumed_at = now
            preview.encrypted_payload = ""
            preview.save(update_fields=("status", "consumed_at", "encrypted_payload"))
            transaction.on_commit(lambda path=preview.stored_path: _delete(path))
        for request in expired_requests:
            # An internal due date must not invalidate an external link before
            # its own elapsed 48-hour TTL.  The link is the client contract;
            # the request deadline remains an operational reminder.
            if request.external_accesses.filter(
                status__in=[AccesoExternoSolicitudColectivo.Status.ACTIVE, AccesoExternoSolicitudColectivo.Status.VERIFIED],
                expires_at__gt=now,
            ).exists():
                continue
            request.transition_to(request.Status.EXPIRED)
            request.save(update_fields=("status", "updated_at"))
            request.external_accesses.filter(status__in=[AccesoExternoSolicitudColectivo.Status.ACTIVE, AccesoExternoSolicitudColectivo.Status.VERIFIED]).update(status=AccesoExternoSolicitudColectivo.Status.EXPIRED, otp_hash="", otp_expires_at=None)
            EventoSolicitudColectivo.objects.create(request=request, event_type="REQUEST_EXPIRED", new_status=request.status, safe_metadata={"deadline": str(request.deadline)})
            _notify(request, "REQUEST_EXPIRED", "Solicitud vencida", f"La solicitud {request.public_id} venció.", str(request.deadline))

    for request in near_requests:
        access = request.external_accesses.filter(status__in=[AccesoExternoSolicitudColectivo.Status.ACTIVE, AccesoExternoSolicitudColectivo.Status.VERIFIED]).order_by("-created_at").first()
        if access and _email(access, kind="COLECTIVOS_DEADLINE_REMINDER", subject=f"Recordatorio · {request.public_id}", message=f"La solicitud {request.public_id} vence el {request.deadline}.", key=f"colectivos-reminder:{request.pk}:{request.deadline}", dry_run=False):
            result.reminders_sent += 1
            _notify(request, "DEADLINE_REMINDER", "Solicitud próxima a vencer", f"La solicitud {request.public_id} está próxima a vencer.", str(request.deadline))
    for request in expired_requests:
        access = request.external_accesses.order_by("-created_at").first()
        if access and _email(access, kind="COLECTIVOS_REQUEST_EXPIRED", subject=f"Solicitud vencida · {request.public_id}", message=f"La solicitud {request.public_id} ha vencido.", key=f"colectivos-expired:{request.pk}:{request.deadline}", dry_run=False):
            result.expiration_notices_sent += 1
    for request in cancelled:
        access = request.external_accesses.order_by("-created_at").first()
        if access and _email(access, kind="COLECTIVOS_REQUEST_CANCELLED", subject=f"Solicitud cancelada · {request.public_id}", message=f"La solicitud {request.public_id} fue cancelada.", key=f"colectivos-cancelled:{request.pk}", dry_run=False):
            result.cancellation_notices_sent += 1
    return result
