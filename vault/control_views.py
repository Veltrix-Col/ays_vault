from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db import connection, transaction
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from .decorators import role_required
from .forms import AccessExceptionForm, HolidayForm, NotificationRecipientForm, PolicyConfigurationForm, ReasonForm, TimelineFilterForm
from .identity import create_alert, has_recent_reauth
from .models import AccessException, AuditEvent, AuditVerificationRun, Holiday, NotificationRecipient, NotificationRecord, PaymentCard, PolicyConfiguration, PolicyEvaluationRun, SecurityAlert, UserDevice, UserProfile
from .notifications import mask_email, retry_notification
from .policies import get_policy, invalidate_policy_cache
from .reporting import apply_timeline_filters, filter_chips, timeline_queryset
from .security import audit, cached_chain_status


OPERATIONAL_ACTIONS = ["VIEW", "REVEAL", "COPY", "COPY_ATTEMPT", "CREATE", "UPDATE", "DEACTIVATE", "DENIED"]


@role_required(UserProfile.ADMIN, UserProfile.LEADER, UserProfile.ANALYST)
def timeline(request):
    form = TimelineFilterForm(request.GET, user=request.user)
    if form.is_valid():
        cleaned = form.cleaned_data
        queryset = apply_timeline_filters(timeline_queryset(request.user), cleaned)
    else:
        cleaned = {}
        queryset = timeline_queryset(request.user).none()
    page_size = int(cleaned.get("page_size") or 50)
    page = Paginator(queryset, page_size).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    advanced_fields = ["method", "path", "device_type", "browser", "operating_system", "session", "alert_status", "policy", "exception", "sensitive_only", "with_alert", "failed_only", "critical_only"]
    advanced_open = bool(cleaned.get("advanced") or any(cleaned.get(name) for name in advanced_fields))
    return render(request, "vault/control/timeline.html", {
        "page": page,
        "filter_form": form,
        "filters": request.GET,
        "compact": cleaned.get("view", "compact") != "detail",
        "active_chips": filter_chips(request.GET, cleaned) if form.is_valid() else [],
        "result_count": page.paginator.count,
        "querystring": params.urlencode(),
        "advanced_open": advanced_open,
        "export_params": [(key, value) for key in request.GET for value in request.GET.getlist(key) if key != "page"],
    })


def _health(now):
    reasons = []
    level = "HEALTHY"
    chain_ok, _ = cached_chain_status()
    if not chain_ok:
        return "CRITICAL", ["Fallo de integridad en la cadena de auditoría."]
    critical = SecurityAlert.objects.filter(severity="CRITICAL").exclude(status__in=["CLOSED", "JUSTIFIED"]).count()
    if critical:
        level = "CRITICAL"; reasons.append(f"{critical} alerta(s) critica(s) abierta(s).")
    last_use = AuditEvent.objects.filter(action__in=["REVEAL", "COPY", "CREATE", "UPDATE", "VIEW"]).order_by("-created_at").first()
    days = (now - last_use.created_at).days if last_use else None
    if days is None or days >= get_policy().inactivity_general_days:
        if level != "CRITICAL": level = "ATTENTION"
        reasons.append(f"{days if days is not None else 'Sin registro de'} dias sin actividad operativa.")
    email_failures = NotificationRecord.objects.filter(result__in=["FAILED", "RETRY"]).count()
    if email_failures:
        if level == "HEALTHY": level = "RISK"
        reasons.append(f"{email_failures} notificación(es) con fallo o reintento.")
    if not reasons: reasons.append("Auditoría, configuración y alertas sin hallazgos activos.")
    return level, reasons


@role_required(UserProfile.ADMIN)
def control_center(request):
    now = timezone.now()
    chain_ok, chain_position = cached_chain_status()
    try:
        connection.ensure_connection(); db_ok = connection.is_usable()
    except Exception:
        db_ok = False
    users = get_user_model().objects.filter(is_active=True, vault_profile__active=True).select_related("vault_profile")
    adoption = []
    for user in users:
        events = AuditEvent.objects.filter(user=user)
        login = events.filter(action="LOGIN").order_by("-created_at").first()
        reveal = events.filter(action="REVEAL").order_by("-created_at").first()
        copy = events.filter(action="COPY", result="SUCCESS").order_by("-created_at").first()
        adoption.append({"user": user, "last_login": login, "days_since_login": (now - login.created_at).days if login else None, "last_reveal": reveal, "last_copy": copy, "cards_30": events.filter(action="VIEW", created_at__gte=now-timedelta(days=30)).values("card_id").distinct().count(), "recognized_devices": user.vault_devices.filter(status=UserDevice.TRUSTED).count()})
    latest = lambda actions: AuditEvent.objects.filter(action__in=actions).order_by("-created_at").first()
    open_alerts = SecurityAlert.objects.exclude(status__in=["CLOSED", "JUSTIFIED"])
    health, health_reasons = _health(now)
    context = {
        "chain_ok": chain_ok, "chain_position": chain_position, "db_ok": db_ok,
        "policy": get_policy(), "health": health, "health_reasons": health_reasons,
        "mfa_total": users.filter(vault_profile__mfa_enabled=True).count(), "active_users": users.count(),
        "open_alerts": open_alerts, "alert_counts": {value: open_alerts.filter(severity=value).count() for value in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]},
        "overdue_alerts": open_alerts.filter(due_at__lt=now).count(), "recent": AuditEvent.objects.select_related("user", "card")[:25],
        "adoption": adoption, "days_without_use": (now - latest(OPERATIONAL_ACTIONS).created_at).days if latest(OPERATIONAL_ACTIONS) else None,
        "last_audit": latest([choice[0] for choice in AuditEvent.ACTIONS]), "last_login": latest(["LOGIN"]), "last_copy": latest(["COPY"]),
        "last_reveal": latest(["REVEAL"]), "last_card": latest(["CREATE"]), "last_verification": AuditVerificationRun.objects.order_by("-checked_at").first(),
        "last_email_success": NotificationRecord.objects.filter(result="SENT").order_by("-sent_at").first(), "last_email_failure": NotificationRecord.objects.filter(result__in=["FAILED", "RETRY"]).order_by("-created_at").first(),
        "last_alert": SecurityAlert.objects.order_by("-created_at").first(), "last_run": PolicyEvaluationRun.objects.order_by("-started_at").first(),
        "exceptions": AccessException.objects.filter(status=AccessException.ACTIVE, ends_at__gte=now).order_by("ends_at")[:10],
        "holidays": Holiday.objects.filter(date__gte=timezone.localdate()).order_by("date")[:8],
        "cards_never_consulted": PaymentCard.objects.filter(active=True).exclude(auditevent__action="VIEW").count(),
        "cards_recently_consulted": PaymentCard.objects.filter(active=True, auditevent__action="VIEW", auditevent__created_at__gte=now-timedelta(days=30)).distinct().count(),
        "usage_7": AuditEvent.objects.filter(action__in=OPERATIONAL_ACTIONS, created_at__gte=now-timedelta(days=7)).count(),
        "usage_30": AuditEvent.objects.filter(action__in=OPERATIONAL_ACTIONS, created_at__gte=now-timedelta(days=30)).count(),
        "usage_90": AuditEvent.objects.filter(action__in=OPERATIONAL_ACTIONS, created_at__gte=now-timedelta(days=90)).count(),
    }
    return render(request, "vault/control/dashboard.html", context)


@role_required(UserProfile.ADMIN)
@require_http_methods(["GET", "POST"])
def policy_settings(request):
    policy = get_policy()
    form = PolicyConfigurationForm(request.POST or None, instance=policy)
    if request.method == "POST":
        if not has_recent_reauth(request, "policy_admin"):
            return redirect(f"{reverse('vault:reauthenticate')}?purpose=policy_admin&next={request.path}")
        if form.is_valid():
            old = {name: str(getattr(policy, name)) for name in form.changed_data if name != "reason"}
            with transaction.atomic():
                updated = form.save(commit=False); updated.updated_by = request.user; updated.save()
                new = {name: str(getattr(updated, name)) for name in form.changed_data if name != "reason"}
                event = audit(request, "POLICY_CHANGED", reason=form.cleaned_data["reason"], risk_level="HIGH", metadata={"changed_fields": [name for name in form.changed_data if name != "reason"], "old": old, "new": new})
                create_alert(request, event, "POLICY_CHANGED", "HIGH", request.user, description="Configuración central modificada.")
            invalidate_policy_cache(); messages.success(request, "Configuración actualizada y auditada.")
            return redirect("vault:policy_settings")
    return render(request, "vault/control/policies.html", {"form": form, "policy": policy})


@role_required(UserProfile.ADMIN)
@require_http_methods(["GET", "POST"])
def exception_list(request):
    form = AccessExceptionForm(request.POST or None)
    if request.method == "POST":
        if not has_recent_reauth(request, "policy_admin"):
            return redirect(f"{reverse('vault:reauthenticate')}?purpose=policy_admin&next={request.path}")
        if form.is_valid():
            exception = form.save(commit=False); exception.created_by = request.user; exception.save()
            event = audit(request, "EXCEPTION_CREATED", reason=exception.reason, risk_level="HIGH", metadata={"exception_id": exception.pk, "type": exception.exception_type, "user_id": exception.user_id, "role": exception.role, "starts_at": exception.starts_at.isoformat(), "ends_at": exception.ends_at.isoformat()})
            alert = create_alert(request, event, "EXCEPTION_CREATED", "HIGH", exception.user, description="Excepción administrativa creada.")
            alert.access_exception = exception; alert.save(update_fields=["access_exception"])
            invalidate_policy_cache(); messages.success(request, "Excepción creada y auditada.")
            return redirect("vault:exceptions")
    return render(request, "vault/control/exceptions.html", {"form": form, "exceptions": AccessException.objects.select_related("user", "created_by", "revoked_by").order_by("-starts_at")})


@role_required(UserProfile.ADMIN)
@require_POST
def exception_revoke(request, pk):
    if not has_recent_reauth(request, "policy_admin"):
        return redirect(f"{reverse('vault:reauthenticate')}?purpose=policy_admin&next={reverse('vault:exceptions')}")
    form = ReasonForm(request.POST)
    if not form.is_valid(): return HttpResponseBadRequest("Motivo requerido")
    exception = get_object_or_404(AccessException, pk=pk, status=AccessException.ACTIVE)
    exception.status = AccessException.REVOKED; exception.revoked_at = timezone.now(); exception.revoked_by = request.user; exception.revocation_reason = form.cleaned_data["reason"]
    exception.save(update_fields=["status", "revoked_at", "revoked_by", "revocation_reason"])
    event = audit(request, "EXCEPTION_REVOKED", reason=form.cleaned_data["reason"], risk_level="HIGH", metadata={"exception_id": exception.pk})
    create_alert(request, event, "EXCEPTION_REVOKED", "HIGH", exception.user, description="Excepción administrativa revocada.")
    invalidate_policy_cache(); return redirect("vault:exceptions")


@role_required(UserProfile.ADMIN)
@require_http_methods(["GET", "POST"])
def holiday_settings(request):
    form = HolidayForm(request.POST or None)
    if request.method == "POST":
        if not has_recent_reauth(request, "policy_admin"):
            return redirect(f"{reverse('vault:reauthenticate')}?purpose=policy_admin&next={request.path}")
        if form.is_valid():
            holiday, created = Holiday.objects.update_or_create(date=form.cleaned_data["date"], defaults={"name": form.cleaned_data["name"], "national": form.cleaned_data["national"], "internal": form.cleaned_data["internal"], "working_day": form.cleaned_data["working_day"], "created_by": request.user})
            event = audit(request, "POLICY_CHANGED", reason=form.cleaned_data["reason"], risk_level="HIGH", metadata={"holiday_id": holiday.pk, "date": holiday.date.isoformat(), "working_day": holiday.working_day, "created": created})
            create_alert(request, event, "HOLIDAY_CHANGED", "MEDIUM", request.user, description="Calendario laboral modificado.")
            invalidate_policy_cache(); messages.success(request, "Festivo guardado y auditado.")
            return redirect("vault:holidays")
    return render(request, "vault/control/holidays.html", {"form": form, "holidays": Holiday.objects.order_by("-date")[:100]})


@role_required(UserProfile.ADMIN)
@require_http_methods(["GET", "POST"])
def recipient_settings(request):
    form = NotificationRecipientForm(request.POST or None)
    if request.method == "POST":
        if not has_recent_reauth(request, "policy_admin"):
            return redirect(f"{reverse('vault:reauthenticate')}?purpose=policy_admin&next={request.path}")
        if form.is_valid():
            recipient = form.save(commit=False); recipient.updated_by = request.user; recipient.save()
            event = audit(request, "POLICY_CHANGED", reason=form.cleaned_data["reason"], risk_level="HIGH", metadata={"recipient_id": recipient.pk, "recipient": mask_email(recipient.email), "changed_fields": list(form.changed_data)})
            create_alert(request, event, "RECIPIENT_CHANGED", "HIGH", request.user, description="Configuración de destinatarios modificada.")
            messages.success(request, "Destinatario guardado y auditado.")
            return redirect("vault:recipients")
    return render(request, "vault/control/recipients.html", {"form": form, "recipients": NotificationRecipient.objects.order_by("name"), "notifications": NotificationRecord.objects.select_related("alert")[:100]})


@role_required(UserProfile.ADMIN)
@require_POST
def notification_retry(request, pk):
    if not has_recent_reauth(request, "policy_admin"):
        return redirect(f"{reverse('vault:reauthenticate')}?purpose=policy_admin&next={reverse('vault:recipients')}")
    form = ReasonForm(request.POST)
    if not form.is_valid(): return HttpResponseBadRequest("Motivo requerido")
    record = get_object_or_404(NotificationRecord, pk=pk, result__in=["FAILED", "RETRY"])
    retry_notification(record)
    audit(request, "EMAIL_SENT" if record.result == "SENT" else "EMAIL_FAILED", reason=form.cleaned_data["reason"], metadata={"notification_id": record.pk, "result": record.result})
    return redirect("vault:recipients")
