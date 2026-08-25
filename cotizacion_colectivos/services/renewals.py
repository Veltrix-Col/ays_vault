from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import date, timedelta

from django.conf import settings
from django.core.cache import cache
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.db.models import Q

from vault.crypto import decrypt, encrypt
from vault.notifications import mask_email

from .common import ColectivosServiceError, colectivos_zoho, sign_record_id, translate_zoho_error
from .external import GeneratedAccess, generate_access, send_invitation
from .requests import create_or_reuse_request_from_policy
from .policies import PolicyService
from ..models import RenovacionColectiva, SolicitudColectivo
from integrations.zoho.exceptions import ZohoError

logger = logging.getLogger("cotizacion_colectivos")
RENEWAL_LINE_VALUES = ("Colectivo", "Colectivos", "Colectiva")
RENEWAL_SYNC_CACHE_KEY = "cotizacion_colectivos:renewals:last_sync:v2"

POLICY_FIELDS = (
    "id", "Name", "Ramo", "L_nea_de_negocio", "Tomador_principal1",
    "P_liza_Fecha_fin_de_la_vigencia", "Aseguradora1",
)


@dataclass(frozen=True)
class RenewalPolicy:
    remote_id: str
    token: str
    policy: str
    client: str
    branch: str
    expiry_date: date
    email: str
    scheduled_for: date


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


def _read_policy_records(facade, *, diagnostics=None, allow_broad_fallback=False):
    """Read only the collective subsets; never sweep Polizas interactively."""
    records_by_id = {}
    diagnostics = diagnostics if diagnostics is not None else {}
    diagnostics.update({"method": "search.by_criteria", "requests": 0, "pages": 0, "fallback": False, "rate_limit": False})
    for index, value in enumerate(RENEWAL_LINE_VALUES):
        criteria = f"(L_nea_de_negocio:equals:{value})"
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
        if index == len(RENEWAL_LINE_VALUES) - 1 or (index == 0 and records_by_id):
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
    if not remote_id or not _collective(record.get("L_nea_de_negocio")) or expiry is None:
        return None
    if expiry < today or expiry > today + timedelta(days=window):
        return None
    token = sign_record_id(remote_id, "policy")
    holder = record.get("Tomador_principal1")
    client = holder.get("name") if isinstance(holder, dict) else holder
    return RenewalPolicy(
        remote_id=remote_id,
        token=token,
        policy=str(record.get("Name") or "Sin número de póliza"),
        client=str(client or "Cliente sin nombre"),
        branch=str(record.get("Ramo") or "Ramo sin clasificar"),
        expiry_date=expiry,
        email=str(record.get("Email") or record.get("Correo_electr_nico_afiliado") or "").strip(),
        scheduled_for=expiry - timedelta(days=30),
    )


def list_collective_renewals(*, zoho=None, today=None, window=None, diagnostics=None) -> tuple[RenewalPolicy, ...]:
    """Read the consolidated Polizas source and apply the collective/date filter."""
    today = today or timezone.localdate()
    window = int(window if window is not None else getattr(settings, "COLECTIVOS_RENEWAL_WINDOW_DAYS", 30))
    facade = zoho or colectivos_zoho()
    # Date filtering remains defensive because Zoho date criteria vary by
    # account and is repeated locally after parsing.
    records = _read_policy_records(facade, diagnostics=diagnostics)
    # Reuse the same Contacts email source as the manual policy flow, but do
    # it as one consolidated lookup instead of one remote call per row.
    contact_ids = {
        str(record.get("Tomador_principal1", {}).get("id"))
        for record in records
        if isinstance(record.get("Tomador_principal1"), dict) and record["Tomador_principal1"].get("id")
    }
    contact_emails = {}
    if contact_ids:
        try:
            ids = ",".join(f"'{value}'" for value in sorted(contact_ids))
            page = facade.coql.execute(f"select id,Email from Contacts where id in ({ids}) limit 200")
            contact_emails = {str(item.get("id")): str(item.get("Email") or "").strip() for item in page.records}
        except (AttributeError, ZohoError):
            contact_emails = {}
    for record in records:
        lookup = record.get("Tomador_principal1")
        if isinstance(lookup, dict):
            record["Email"] = contact_emails.get(str(lookup.get("id") or ""), "")
    result = [mapped for record in records if (mapped := _map_record(record, today=today, window=window))]
    return tuple(sorted(result, key=lambda item: (item.expiry_date, item.policy)))


def diagnose_renewal_source(*, zoho=None, today=None, window=None, limit=20):
    """Return safe counts for a read-only production/sandbox diagnosis."""
    today = today or timezone.localdate()
    window = int(window if window is not None else getattr(settings, "COLECTIVOS_RENEWAL_WINDOW_DAYS", 30))
    diagnostics = {}
    records = _read_policy_records(zoho or colectivos_zoho(), diagnostics=diagnostics, allow_broad_fallback=True)
    collective = [record for record in records if _collective(record.get("L_nea_de_negocio"))]
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
    return f"{item.remote_id}:{item.expiry_date.isoformat()}"


def sync_renewal_cycles(*, zoho=None, today=None, window=None) -> tuple[RenovacionColectiva, ...]:
    if zoho is None and cache.get(RENEWAL_SYNC_CACHE_KEY):
        return tuple(RenovacionColectiva.objects.filter(
            line_of_business="Colectivo",
            expiry_date__gte=today or timezone.localdate(),
            expiry_date__lte=(today or timezone.localdate()) + timedelta(days=window if window is not None else getattr(settings, "COLECTIVOS_RENEWAL_WINDOW_DAYS", 30)),
        ).order_by("expiry_date", "pk"))
    cycles = []
    for item in list_collective_renewals(zoho=zoho, today=today, window=window):
        cycle, _ = RenovacionColectiva.objects.update_or_create(
            cycle_key=_cycle_key(item),
            defaults={
                "policy_remote_id": item.remote_id, "policy_token": encrypt(item.token),
                "masked_policy": item.policy, "client_label": item.client,
                "branch_name": item.branch, "line_of_business": "Colectivo",
                "expiry_date": item.expiry_date, "scheduled_for": item.scheduled_for,
            },
        )
        if item.email and not cycle.recipient_hash:
            cycle.encrypted_recipient = encrypt(item.email)
            cycle.recipient_hash = hashlib.sha256(item.email.casefold().encode()).hexdigest()
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


def process_renewal_cycles(*, now=None, limit=None, dry_run=False) -> dict[str, int]:
    now = now or timezone.now()
    today = timezone.localtime(now).date()
    limit = int(limit or getattr(settings, "COLECTIVOS_RENEWAL_BATCH_LIMIT", 100))
    due = RenovacionColectiva.objects.filter(selected=True, status=RenovacionColectiva.Status.PROGRAMMED, scheduled_for__lte=today).order_by("scheduled_for", "pk")[:limit]
    result = {"processed": 0, "sent": 0, "errors": 0}
    if dry_run:
        result["processed"] = due.count()
        return result
    for candidate in due:
        with transaction.atomic():
            cycle = RenovacionColectiva.objects.select_for_update().get(pk=candidate.pk)
            if not cycle.selected or cycle.status != RenovacionColectiva.Status.PROGRAMMED:
                continue
            cycle.status = RenovacionColectiva.Status.PROCESSING
            cycle.send_attempts += 1
            cycle.save(update_fields=("status", "send_attempts", "updated_at"))
        result["processed"] += 1
        try:
            actor = _actor_for_batch()
            token = decrypt(cycle.policy_token)
            request, _ = create_or_reuse_request_from_policy(
                token=token, source_kind="company", actor=actor, assigned_to=actor,
                request_type=SolicitudColectivo.RequestType.RENEWAL,
                deadline=cycle.expiry_date, service=PolicyService(),
            )
            generated = generate_access(request=request, actor=actor, recipient=decrypt(cycle.encrypted_recipient), regenerate=False)
            send_invitation(generated)
            with transaction.atomic():
                cycle = RenovacionColectiva.objects.select_for_update().get(pk=cycle.pk)
                cycle.request = request
                cycle.access = generated.access
                cycle.status = RenovacionColectiva.Status.ALERT if cycle.expiry_date <= timezone.localdate() + timedelta(days=getattr(settings, "COLECTIVOS_RENEWAL_ALERT_DAYS", 10)) else RenovacionColectiva.Status.SENT
                cycle.sent_at = timezone.now()
                cycle.last_sent_at = cycle.sent_at
                cycle.last_activity_at = cycle.sent_at
                cycle.save(update_fields=("request", "access", "status", "sent_at", "last_sent_at", "last_activity_at", "updated_at"))
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
    queryset = RenovacionColectiva.objects.filter(
        line_of_business="Colectivo",
        expiry_date__gte=today,
        expiry_date__lte=today + timedelta(days=getattr(settings, "COLECTIVOS_RENEWAL_WINDOW_DAYS", 30)),
    ).exclude(status__in=(RenovacionColectiva.Status.SENT, RenovacionColectiva.Status.RESPONDED, RenovacionColectiva.Status.ALERT))
    if filter_name == "unselected":
        queryset = queryset.filter(selected=False)
    elif filter_name == "programmed":
        queryset = queryset.filter(status=RenovacionColectiva.Status.PROGRAMMED)
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
            deadline=cycle.expiry_date, service=PolicyService(),
        )
        generated = generate_access(request=request, actor=actor, recipient=normalized, regenerate=True)
        send_invitation(generated)
        now = timezone.now()
        status = RenovacionColectiva.Status.ALERT if cycle.expiry_date <= now.date() + timedelta(days=getattr(settings, "COLECTIVOS_RENEWAL_ALERT_DAYS", 10)) else RenovacionColectiva.Status.SENT
        RenovacionColectiva.objects.filter(pk=cycle.pk).update(
            request=request, access=generated.access, status=status,
            sent_at=now, last_sent_at=now, last_activity_at=now,
            updated_at=now, safe_error="",
        )
        return RenovacionColectiva.objects.get(pk=cycle.pk)
    except Exception as exc:
        RenovacionColectiva.objects.filter(pk=cycle.pk).update(status=RenovacionColectiva.Status.ERROR, error_code="RESEND_ERROR", safe_error="No fue posible reenviar el acceso.", updated_at=timezone.now())
        if isinstance(exc, ColectivosServiceError):
            raise
        raise ColectivosServiceError("delivery", "No fue posible reenviar el acceso.") from exc
