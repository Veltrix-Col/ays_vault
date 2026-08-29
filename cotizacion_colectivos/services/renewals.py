from __future__ import annotations

import hashlib
import logging
import calendar
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from django.conf import settings
from django.core.cache import cache
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.core.mail import EmailMultiAlternatives, get_connection
from django.template.loader import render_to_string

from vault.crypto import decrypt, encrypt
from vault.notifications import mask_email

from .common import ColectivosServiceError, colectivos_zoho, sign_record_id, translate_zoho_error
from .external import GeneratedAccess, generate_access, generate_no_changes_token
from .requests import create_or_reuse_request_from_policy
from .policies import PolicyService
from ..models import RenovacionColectiva, SolicitudColectivo
from .operational_settings import monthly_renewals_enabled
from integrations.zoho.exceptions import ZohoError

logger = logging.getLogger("cotizacion_colectivos")
RENEWAL_BRANCH_VALUES = ("VG deudores", "VG patronal", "AP colectivo")
RENEWAL_SYNC_CACHE_KEY = "cotizacion_colectivos:renewals:last_sync:v2"

POLICY_FIELDS = (
    "id", "Name", "Ramo", "L_nea_de_negocio", "Tomador_principal1",
    "Estado_de_la_p_liza", "Frecuencia", "Correo_gesti_n_comercial",
    "P_liza_Fecha_fin_de_la_vigencia", "Aseguradora1", "Vendedor",
)
ALLOWED_BRANCHES = {"vg deudores", "vg patronal", "ap colectivo"}
ACTIVE_POLICY_VALUES = {"vigente"}


@dataclass(frozen=True)
class RenewalPolicy:
    remote_id: str
    token: str
    policy: str
    client: str
    branch: str
    expiry_date: date | None
    email: str
    policy_status: str
    payment_frequency: str
    monthly_period: str
    scheduled_for: date
    seller: str = ""


def _date(value):
    if isinstance(value, date):
        return value
    text = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _collective(value) -> bool:
    return str(value or "").strip().casefold() in {"colectivo", "colectivos", "colectiva"}


def _fold(value) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", str(value or "")) if not unicodedata.combining(ch)).strip().casefold()


def _allowed_branch(value) -> bool:
    return _fold(value) in ALLOWED_BRANCHES


def last_business_day(year: int, month: int) -> date:
    """Último lunes-viernes del mes.

    PENDIENTE CONFIRMACIÓN NEGOCIO: definir si también deben excluirse los
    festivos colombianos; por ahora sólo se excluyen sábado y domingo.
    """
    result = date(year, month, calendar.monthrange(year, month)[1])
    while result.weekday() >= 5:
        result -= timedelta(days=1)
    return result


def next_month_period(value: date) -> str:
    """Return the YYYY-MM period requested by an end-of-month send."""
    month = value.month + 1
    year = value.year
    if month == 13:
        year += 1
        month = 1
    return f"{year:04d}-{month:02d}"


def _read_policy_records(facade, *, diagnostics=None, allow_broad_fallback=False):
    """Read only the collective subsets; never sweep Polizas interactively."""
    records_by_id = {}
    diagnostics = diagnostics if diagnostics is not None else {}
    diagnostics.update({"method": "search.by_criteria", "requests": 0, "pages": 0, "fallback": False, "rate_limit": False})
    for index, value in enumerate(RENEWAL_BRANCH_VALUES):
        criteria = f"(Ramo:equals:{value})"
        try:
            diagnostics["requests"] += 1
            page = facade.search.by_criteria(
                module="Polizas", criteria=criteria, fields=POLICY_FIELDS,
                page=1, limit=200,
            )
        except Exception as exc:
            if isinstance(exc, ZohoError):
                if getattr(exc, "category", "") == "rate_limit" or exc.__class__.__name__ == "ZohoRateLimitError":
                    diagnostics["rate_limit"] = True
                raise
            raise
        diagnostics["pages"] += 1
        for record in getattr(page, "records", ()) or ():
            record_id = str(record.get("id") or "").strip()
            if record_id:
                records_by_id[record_id] = record
        # The confirmed canonical value is the fast path.  Probe the legacy
        # variants only when that query is empty, avoiding three requests on
        # every normal page load.
        if index == len(RENEWAL_BRANCH_VALUES) - 1:
            break
    if records_by_id or not allow_broad_fallback:
        return list(records_by_id.values())

    # Manual diagnosis may opt into a bounded fallback.  The web request
    # never does so, preventing an accidental full-module scan.
    diagnostics["method"] = "records.list"
    diagnostics["fallback"] = True
    for page_number in range(1, 3):
        diagnostics["requests"] += 1
        page = facade.records.list(module="Polizas", fields=POLICY_FIELDS, page=page_number, limit=200)
        diagnostics["pages"] += 1
        for record in getattr(page, "records", ()) or ():
            record_id = str(record.get("id") or "").strip()
            if record_id:
                records_by_id[record_id] = record
        if not getattr(page, "more_records", False):
            break
    return list(records_by_id.values())


def _map_record(record: dict, *, today: date, window: int) -> RenewalPolicy | None:
    remote_id = str(record.get("id") or "").strip()
    expiry = _date(record.get("P_liza_Fecha_fin_de_la_vigencia"))
    status = _fold(record.get("Estado_de_la_p_liza"))
    frequency = _fold(record.get("Frecuencia"))
    if not remote_id or status not in ACTIVE_POLICY_VALUES or frequency != "mensual" or not _allowed_branch(record.get("Ramo")):
        return None
    token = sign_record_id(remote_id, "policy")
    period = next_month_period(today)
    holder = record.get("Tomador_principal1")
    client = holder.get("name") if isinstance(holder, dict) else holder
    return RenewalPolicy(
        remote_id=remote_id,
        token=token,
        policy=str(record.get("Name") or "Sin número de póliza"),
        client=str(client or "Cliente sin nombre"),
        branch=str(record.get("Ramo") or "Ramo sin clasificar"),
        expiry_date=expiry,
        email=str(record.get("Correo_gesti_n_comercial") or "").strip(),
        policy_status=str(record.get("Estado_de_la_p_liza") or ""),
        payment_frequency=str(record.get("Frecuencia") or ""),
        monthly_period=period,
        scheduled_for=last_business_day(today.year, today.month),
        seller=str(record.get("Vendedor") or "").strip(),
    )


def list_collective_renewals(*, zoho=None, today=None, window=None, diagnostics=None) -> tuple[RenewalPolicy, ...]:
    """Read Polizas and apply only the monthly eligibility contract."""
    today = today or timezone.localdate()
    window = int(window if window is not None else getattr(settings, "COLECTIVOS_RENEWAL_WINDOW_DAYS", 30))
    facade = zoho or colectivos_zoho()
    # Date filtering remains defensive because Zoho date criteria vary by
    # account and is repeated locally after parsing.
    records = _read_policy_records(facade, diagnostics=diagnostics)
    result = [mapped for record in records if (mapped := _map_record(record, today=today, window=window))]
    return tuple(sorted(result, key=lambda item: (item.expiry_date or date.max, item.policy)))


def diagnose_renewal_source(*, zoho=None, today=None, window=None, limit=20):
    """Return safe counts for a read-only production/sandbox diagnosis."""
    today = today or timezone.localdate()
    window = int(window if window is not None else getattr(settings, "COLECTIVOS_RENEWAL_WINDOW_DAYS", 30))
    diagnostics = {}
    records = _read_policy_records(zoho or colectivos_zoho(), diagnostics=diagnostics, allow_broad_fallback=True)
    collective = [record for record in records if _allowed_branch(record.get("Ramo"))]
    valid_dates = [record for record in collective if _date(record.get("P_liza_Fecha_fin_de_la_vigencia"))]
    horizon_30 = [record for record in valid_dates if today <= _date(record.get("P_liza_Fecha_fin_de_la_vigencia")) <= today + timedelta(days=30)]
    horizon_window = [record for record in valid_dates if today <= _date(record.get("P_liza_Fecha_fin_de_la_vigencia")) <= today + timedelta(days=window)]
    diagnostics.update({
        "total_records": len(records),
        "collective_records": len(collective),
        "valid_expiry_records": len(valid_dates),
        "next_30_days": len(horizon_30),
        "next_window_days": len(horizon_window),
        "examples": [
            {
                "policy": str(record.get("Name") or "")[:80],
                "branch": str(record.get("Ramo") or "")[:80],
                "expiry": _date(record.get("P_liza_Fecha_fin_de_la_vigencia")),
                "client": str((record.get("Tomador_principal1") or {}).get("name", "") if isinstance(record.get("Tomador_principal1"), dict) else record.get("Tomador_principal1") or "")[:80],
            }
            for record in horizon_window[:max(0, int(limit))]
        ],
    })
    return diagnostics


def _cycle_key(item: RenewalPolicy) -> str:
    return f"{item.remote_id}:{item.monthly_period}"


def sync_renewal_cycles(*, zoho=None, today=None, window=None) -> tuple[RenovacionColectiva, ...]:
    if zoho is None and cache.get(RENEWAL_SYNC_CACHE_KEY):
        return tuple(RenovacionColectiva.objects.filter(
            line_of_business="Colectivo",
            monthly_period=next_month_period(today or timezone.localdate()),
        ).order_by("expiry_date", "pk"))
    cycles = []
    automation_enabled = monthly_renewals_enabled()
    for item in list_collective_renewals(zoho=zoho, today=today, window=window):
        cycle_key = _cycle_key(item)
        defaults = {
            "policy_remote_id": item.remote_id, "policy_token": encrypt(item.token),
            "masked_policy": item.policy, "client_label": item.client,
            "branch_name": item.branch, "line_of_business": "Colectivo",
            "expiry_date": item.expiry_date, "monthly_period": item.monthly_period,
            "policy_status": item.policy_status, "payment_frequency": item.payment_frequency,
            "seller_label": item.seller,
        }
        with transaction.atomic():
            cycle = RenovacionColectiva.objects.select_for_update().filter(cycle_key=cycle_key).first()
            if cycle is None:
                cycle = RenovacionColectiva.objects.create(
                    cycle_key=cycle_key, scheduled_for=item.scheduled_for, **defaults,
                )
                created = True
            else:
                for field, value in defaults.items():
                    setattr(cycle, field, value)
                cycle.save(update_fields=tuple(defaults) + ("updated_at",))
                created = False
        if created:
            cycle.automation_eligible = automation_enabled
            cycle.save(update_fields=("automation_eligible", "updated_at"))
        # Before the first send Zoho remains authoritative for the recipient.
        # Once a cycle has been sent, the stored address is historical evidence
        # and must not be overwritten by later master-data changes.
        if cycle.status == RenovacionColectiva.Status.PROGRAMMED and cycle.sent_at is None:
            email = str(item.email or "").strip()
            cycle.encrypted_recipient = encrypt(email)
            cycle.recipient_hash = hashlib.sha256(email.casefold().encode()).hexdigest() if email else ""
            cycle.save(update_fields=("encrypted_recipient", "recipient_hash", "updated_at"))
        cycles.append(cycle)
    if zoho is None:
        cache.set(RENEWAL_SYNC_CACHE_KEY, True, timeout=getattr(settings, "COLECTIVOS_RENEWAL_READ_CACHE_SECONDS", 60))
    return tuple(cycles)


def set_renewal_selection(*, cycle_id: int, selected: bool, recipient: str | None = None) -> RenovacionColectiva:
    with transaction.atomic():
        cycle = RenovacionColectiva.objects.select_for_update().get(pk=cycle_id)
        if cycle.status in {RenovacionColectiva.Status.SENT, RenovacionColectiva.Status.RESPONDED, RenovacionColectiva.Status.ALERT}:
            return cycle
        cycle.selected = bool(selected)
        cycle.status = RenovacionColectiva.Status.PROGRAMMED if selected else RenovacionColectiva.Status.CANCELLED
        if recipient is not None:
            cycle.encrypted_recipient = encrypt(recipient.strip())
            cycle.recipient_hash = hashlib.sha256(recipient.strip().casefold().encode()).hexdigest() if recipient.strip() else ""
        cycle.save(update_fields=("selected", "status", "encrypted_recipient", "recipient_hash", "updated_at"))
        return cycle


def _actor_for_batch():
    username = str(getattr(settings, "COLECTIVOS_TECHNICAL_ACTOR_USERNAME", "")).strip()
    User = get_user_model()
    actor = User.objects.filter(username=username, is_active=True, is_staff=False, is_superuser=False).first()
    if actor is None:
        raise ColectivosServiceError("configuration", "El actor operativo de Colectivos no está configurado.")
    return actor


def _renewal_email_settings():
    password = str(getattr(settings, "COLECTIVOS_RENEWAL_EMAIL_PASSWORD", "") or "")
    if not password:
        raise ColectivosServiceError("configuration", "El correo de renovaciones no está configurado.")
    username = str(getattr(settings, "COLECTIVOS_RENEWAL_EMAIL_USER", "") or "").strip()
    from_email = str(getattr(settings, "COLECTIVOS_RENEWAL_EMAIL_FROM", "") or "").strip()
    if not username or not from_email:
        raise ColectivosServiceError("configuration", "El remitente del correo de renovaciones no está configurado.")
    return {
        "host": getattr(settings, "COLECTIVOS_RENEWAL_EMAIL_HOST", ""),
        "port": getattr(settings, "COLECTIVOS_RENEWAL_EMAIL_PORT", 587),
        "username": username,
        "password": password,
        "use_tls": getattr(settings, "COLECTIVOS_RENEWAL_EMAIL_USE_TLS", True),
        "from_email": from_email,
    }


def _monthly_period_label(period):
    try:
        year, month = str(period).split("-")
        months = ("Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre")
        return f"{months[int(month) - 1]} {year}"
    except (ValueError, IndexError):
        return str(period or "periodo solicitado")


def _send_renewal_email(
    *, cycle, url: str, reminder: bool = False, expires_at=None,
    request_obj=None, recipient: str | None = None, no_changes_url: str | None = None,
):
    cfg = _renewal_email_settings()
    template = "cotizacion_colectivos/email/renewal_reminder.html" if reminder else "cotizacion_colectivos/email/renewal_initial.html"
    if no_changes_url is None:
        no_changes_token = generate_no_changes_token(cycle=cycle, request_obj=request_obj)
        no_changes_url = f"{settings.COLECTIVOS_EXTERNAL_BASE_URL}/solicitudes/colectivos/externa/sin-novedades/{no_changes_token}/"
    html = render_to_string(template, {"cycle": cycle, "url": url, "no_changes_url": no_changes_url, "monthly_period_label": _monthly_period_label(cycle.monthly_period), "expires_at": expires_at or cycle.link_expires_at})
    recipient_email = str(recipient if recipient is not None else cycle.recipient_email).strip()
    message = EmailMultiAlternatives(
        subject=f"A&S | {'Recordatorio de novedades' if reminder else 'Reporte mensual de novedades'} – {cycle.client_label} – {_monthly_period_label(cycle.monthly_period)}",
        body="A&S solicita reportar las novedades de su póliza colectiva.",
        from_email=cfg["from_email"], to=[recipient_email],
        connection=get_connection(
            backend="django.core.mail.backends.smtp.EmailBackend",
            host=cfg["host"], port=cfg["port"], username=cfg["username"],
            password=cfg["password"], use_tls=cfg["use_tls"], fail_silently=False,
        ),
    )
    message.attach_alternative(html, "text/html")
    if message.send(fail_silently=False) != 1:
        raise ColectivosServiceError("delivery", "No fue posible enviar la notificación.")


def _valid_recipient(value):
    try:
        validate_email(str(value or "").strip())
        return True
    except ValidationError:
        return False


def process_renewal_cycles(*, now=None, limit=None, dry_run=False, cycle_id=None, force_due=False) -> dict[str, int]:
    if force_due and cycle_id is None:
        raise ColectivosServiceError("validation", "force_due requiere un cycle_id específico.")
    if force_due and str(getattr(settings, "ZOHO_ACTIVE_PROFILE", "")).strip().casefold() != "sandbox":
        raise ColectivosServiceError("configuration", "force_due sólo está permitido en Sandbox.")
    if not dry_run and not monthly_renewals_enabled():
        return {"processed": 0, "sent": 0, "reminders": 0, "errors": 0, "no_email": 0, "disabled": 1}
    now = now or timezone.now()
    today = timezone.localtime(now).date()
    active_period = next_month_period(today)
    limit = int(limit or getattr(settings, "COLECTIVOS_RENEWAL_BATCH_LIMIT", 100))
    if cycle_id is not None:
        target = RenovacionColectiva.objects.get(pk=cycle_id)
        initial = [target] if (
            target.status == RenovacionColectiva.Status.PROGRAMMED
            and target.automation_eligible
            and target.monthly_period == active_period
            and (force_due or target.scheduled_for <= today)
        ) else []
        reminders = []
    else:
        initial = list(RenovacionColectiva.objects.filter(
            status=RenovacionColectiva.Status.PROGRAMMED,
            automation_eligible=True,
            monthly_period=active_period,
            scheduled_for__lte=today,
        ).order_by("scheduled_for", "pk")[:limit])
        reminders = list(RenovacionColectiva.objects.filter(
            status__in=(RenovacionColectiva.Status.SENT, RenovacionColectiva.Status.ALERT),
            reminder_due_at__lte=now, reminder_sent_at__isnull=True,
            link_expires_at__gt=now, responded_at__isnull=True,
        ).order_by("reminder_due_at", "pk")[:limit])
    result = {"processed": 0, "sent": 0, "reminders": 0, "errors": 0, "no_email": 0}
    if dry_run:
        result["processed"] = len(initial) + len(reminders)
        result["sent"] = len(initial)
        result["reminders"] = len(reminders)
        result["no_email"] = sum(not _valid_recipient(item.recipient_email) for item in initial)
        return result
    for candidate, reminder in [*((item, False) for item in initial), *((item, True) for item in reminders)]:
        with transaction.atomic():
            cycle = RenovacionColectiva.objects.select_for_update().get(pk=candidate.pk)
            expected = (RenovacionColectiva.Status.SENT, RenovacionColectiva.Status.ALERT) if reminder else (RenovacionColectiva.Status.PROGRAMMED,)
            if cycle.status not in expected or (reminder and (cycle.reminder_sent_at or not cycle.link_expires_at or cycle.link_expires_at <= now)):
                continue
            if not _valid_recipient(cycle.recipient_email):
                cycle.status = RenovacionColectiva.Status.ERROR
                cycle.error_code = "MISSING_COMMERCIAL_EMAIL"
                cycle.safe_error = "Sin correo comercial válido."
                cycle.save(update_fields=("status", "error_code", "safe_error", "updated_at"))
                result["errors"] += 1
                result["no_email"] += 1
                continue
            previous_status = cycle.status
            cycle.status = RenovacionColectiva.Status.PROCESSING
            cycle.send_attempts += 1
            cycle.save(update_fields=("status", "send_attempts", "updated_at"))
        result["processed"] += 1
        try:
            if reminder:
                token = decrypt(cycle.encrypted_access_token)
                _send_renewal_email(cycle=cycle, url=f"{settings.COLECTIVOS_EXTERNAL_BASE_URL}/solicitudes/colectivos/externa/{token}/", reminder=True, expires_at=cycle.link_expires_at)
                RenovacionColectiva.objects.filter(pk=cycle.pk).update(status=previous_status, reminder_sent_at=timezone.now(), last_activity_at=timezone.now(), updated_at=timezone.now())
                result["reminders"] += 1
            else:
                actor = _actor_for_batch()
                token = decrypt(cycle.policy_token)
                request, _ = create_or_reuse_request_from_policy(token=token, source_kind="company", actor=actor, assigned_to=actor, request_type=SolicitudColectivo.RequestType.RENEWAL, deadline=cycle.expiry_date or today + timedelta(days=8), service=PolicyService())
                generated = generate_access(request=request, actor=actor, recipient=cycle.recipient_email, regenerate=False, ttl_seconds=int(getattr(settings, "COLECTIVOS_RENEWAL_LINK_TTL_DAYS", 8)) * 86400)
                _send_renewal_email(cycle=cycle, url=generated.url, expires_at=generated.access.expires_at, request_obj=request)
                sent_at = timezone.now()
                status = RenovacionColectiva.Status.SENT
                RenovacionColectiva.objects.filter(pk=cycle.pk).update(request=request, access=generated.access, encrypted_access_token=encrypt(generated.token), status=status, sent_at=sent_at, link_expires_at=generated.access.expires_at, reminder_due_at=sent_at + timedelta(days=int(getattr(settings, "COLECTIVOS_RENEWAL_REMINDER_DAYS", 3))), last_sent_at=sent_at, last_activity_at=sent_at, safe_error="", updated_at=sent_at)
                result["sent"] += 1
        except Exception:
            RenovacionColectiva.objects.filter(pk=cycle.pk).update(status=RenovacionColectiva.Status.ERROR, error_code="PROCESSING_ERROR", safe_error="No fue posible generar o enviar el acceso.", updated_at=timezone.now())
            result["errors"] += 1
    return result


def renewal_dashboard_counts(*, today=None) -> dict[str, int]:
    today = today or timezone.localdate()
    RenovacionColectiva.objects.filter(
        status=RenovacionColectiva.Status.SENT,
        expiry_date__lte=today + timedelta(days=getattr(settings, "COLECTIVOS_RENEWAL_ALERT_DAYS", 10)),
        expiry_date__gte=today,
    ).update(status=RenovacionColectiva.Status.ALERT, updated_at=timezone.now())
    rows = RenovacionColectiva.objects.filter(line_of_business="Colectivo")
    upcoming = upcoming_cycles(today=today)
    counts = {"upcoming": upcoming.count(), "programmed": upcoming.filter(status=RenovacionColectiva.Status.PROGRAMMED).count(), "sent": rows.filter(status=RenovacionColectiva.Status.SENT).count(), "responded": rows.filter(status=RenovacionColectiva.Status.RESPONDED).count(), "alert": rows.filter(status=RenovacionColectiva.Status.ALERT).count(), "error": rows.filter(status=RenovacionColectiva.Status.ERROR).count()}
    return counts


def renewal_search(queryset, value: str):
    value = str(value or "").strip()
    if not value:
        return queryset
    email_hash = hashlib.sha256(value.casefold().encode()).hexdigest()
    return queryset.filter(
        Q(masked_policy__icontains=value)
        | Q(client_label__icontains=value)
        | Q(branch_name__icontains=value)
        | Q(recipient_hash=email_hash)
    )


def upcoming_cycles(*, query="", filter_name="all", today=None):
    today = today or timezone.localdate()
    queryset = RenovacionColectiva.objects.select_related("request").filter(
        line_of_business="Colectivo",
        monthly_period=next_month_period(today),
        status=RenovacionColectiva.Status.PROGRAMMED,
    )
    return renewal_search(queryset.order_by("expiry_date", "pk"), query)


def tracking_cycles(*, query="", status="all"):
    queryset = RenovacionColectiva.objects.filter(
        line_of_business="Colectivo",
        status__in=(RenovacionColectiva.Status.SENT, RenovacionColectiva.Status.RESPONDED, RenovacionColectiva.Status.ALERT, RenovacionColectiva.Status.ERROR),
    )
    if status in {RenovacionColectiva.Status.SENT, RenovacionColectiva.Status.RESPONDED, RenovacionColectiva.Status.ALERT, RenovacionColectiva.Status.ERROR}:
        queryset = queryset.filter(status=status)
    return renewal_search(queryset.order_by("-last_activity_at", "-updated_at", "-pk"), query)


def resend_renewal_access(*, cycle_id: int, recipient: str):
    if not monthly_renewals_enabled():
        raise ColectivosServiceError("disabled", "La automatización mensual de Colectivos está desactivada.")
    normalized = str(recipient or "").strip()
    try:
        validate_email(normalized)
    except ValidationError as exc:
        raise ColectivosServiceError("validation", "Indique un correo válido para reenviar el acceso.") from exc
    with transaction.atomic():
        cycle = RenovacionColectiva.objects.select_for_update().get(pk=cycle_id)
        if cycle.status == RenovacionColectiva.Status.RESPONDED:
            raise ColectivosServiceError("invalid_state", "La renovación ya fue respondida y no admite reenvío.")
        if cycle.status == RenovacionColectiva.Status.PROCESSING:
            raise ColectivosServiceError("in_progress", "Ya existe un reenvío en curso para esta renovación.")
        if cycle.line_of_business != "Colectivo":
            raise ColectivosServiceError("invalid_record", "La renovación no pertenece a Colectivos.")
        cycle.status = RenovacionColectiva.Status.PROCESSING
        cycle.send_attempts += 1
        cycle.encrypted_recipient = encrypt(normalized)
        cycle.recipient_hash = hashlib.sha256(normalized.casefold().encode()).hexdigest()
        cycle.save(update_fields=("status", "send_attempts", "encrypted_recipient", "recipient_hash", "updated_at"))
    try:
        actor = _actor_for_batch()
        token = decrypt(cycle.policy_token)
        request, _ = create_or_reuse_request_from_policy(
            token=token, source_kind="company", actor=actor, assigned_to=actor,
            request_type=SolicitudColectivo.RequestType.RENEWAL,
            deadline=cycle.expiry_date or timezone.localdate() + timedelta(days=8), service=PolicyService(),
        )
        generated = generate_access(request=request, actor=actor, recipient=normalized, regenerate=True, ttl_seconds=int(getattr(settings, "COLECTIVOS_RENEWAL_LINK_TTL_DAYS", 8)) * 86400)
        _send_renewal_email(cycle=cycle, url=generated.url, request_obj=request)
        now = timezone.now()
        status = RenovacionColectiva.Status.ALERT if cycle.expiry_date <= now.date() + timedelta(days=getattr(settings, "COLECTIVOS_RENEWAL_ALERT_DAYS", 10)) else RenovacionColectiva.Status.SENT
        RenovacionColectiva.objects.filter(pk=cycle.pk).update(
            request=request, access=generated.access, status=status,
            sent_at=now, link_expires_at=generated.access.expires_at, reminder_due_at=now + timedelta(days=int(getattr(settings, "COLECTIVOS_RENEWAL_REMINDER_DAYS", 3))), reminder_sent_at=None, encrypted_access_token=encrypt(generated.token), last_sent_at=now, last_activity_at=now,
            updated_at=now, safe_error="",
        )
        return RenovacionColectiva.objects.get(pk=cycle.pk)
    except Exception as exc:
        RenovacionColectiva.objects.filter(pk=cycle.pk).update(status=RenovacionColectiva.Status.ERROR, error_code="RESEND_ERROR", safe_error="No fue posible reenviar el acceso.", updated_at=timezone.now())
        if isinstance(exc, ColectivosServiceError):
            raise
        raise ColectivosServiceError("delivery", "No fue posible reenviar el acceso.") from exc
