from __future__ import annotations

from urllib.parse import urlsplit

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .excepciones_facturacion.persistence import (
    BillingRefreshAlreadyRunning,
    queue_billing_exceptions_refresh,
)
from .excepciones_facturacion.query import filtered_billing_cases
from .models import BillingExceptionRefreshRun, BillingOperationalCase
from .permissions import has_internal_permission, permission_denied_response
from .zoho import get_colectivos_environment


TECHNICAL_CONTEXT_LABELS = {
    "diagnostics": "Diagnósticos",
    "policy_link": "Enlace de la póliza",
    "operation_link": "Enlace de la operación",
    "expected_total": "Total esperado",
    "effective_total": "Total efectivo",
    "effective_cobros": "Cobros efectivos",
    "effective_prorrogas": "Prórrogas efectivas",
    "difference": "Diferencia",
    "anomalies": "Anomalías",
    "missing_expected_installments": "Cuotas esperadas faltantes",
}


def _base_context(request):
    return {
        "zoho_environment": get_colectivos_environment(),
        "can_refresh": has_internal_permission(request, "refresh_billing_exceptions"),
        "can_manage_tasks": has_internal_permission(request, "manage_billing_exception_tasks"),
    }


def _safe_zoho_link(value) -> str:
    candidate = str(value or "").strip()
    parsed = urlsplit(candidate)
    if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.casefold().endswith("zoho.com"):
        return ""
    return candidate


def _case_link(item, name: str) -> str:
    for finding in getattr(item, "all_findings", ()):
        raw_context = finding.context or ()
        entries = raw_context.items() if isinstance(raw_context, dict) else raw_context
        for key, value in entries:
            if key == name:
                return _safe_zoho_link(value)
    return ""


def _quick_card_urls(query):
    urls = {}
    conflicts = {
        "high": "priority", "without_task": "task", "with_task": "task",
        "missing_operation": "finding", "missing_charge": "finding",
    }
    for key in ("active", "high", "without_task", "with_task", "missing_operation", "missing_charge", "multiple"):
        params = query.copy()
        params.pop("page", None)
        params.pop("quick", None)
        params["detection"] = "DETECTED"
        conflict = conflicts.get(key)
        if conflict:
            params.pop(conflict, None)
        if key != "active":
            params["quick"] = key
        urls[key] = f"?{params.urlencode()}"
    return urls


@never_cache
@require_http_methods(["GET"])
def billing_exception_list(request):
    if not has_internal_permission(request, "view_billing_exceptions"):
        return permission_denied_response()
    latest_success = BillingExceptionRefreshRun.objects.filter(
        profile="production", status=BillingExceptionRefreshRun.Status.SUCCESS
    ).first()
    form, queryset = filtered_billing_cases(request.GET or None, snapshot=latest_success)
    page = Paginator(queryset, 25).get_page(request.GET.get("page"))
    latest_run = BillingExceptionRefreshRun.objects.filter(profile="production").first()
    active_run = BillingExceptionRefreshRun.objects.filter(
        profile="production",
        status__in=(BillingExceptionRefreshRun.Status.PENDING, BillingExceptionRefreshRun.Status.RUNNING)
    ).first()
    snapshot_cases = BillingOperationalCase.objects.none()
    if latest_success is not None:
        snapshot_cases = BillingOperationalCase.objects.filter(
            last_run=latest_success,
            detection_status=BillingOperationalCase.DetectionStatus.DETECTED,
        )
    metrics = snapshot_cases.aggregate(
        active=Count("id", distinct=True),
        high=Count("id", filter=Q(local_priority=1), distinct=True),
        without_task=Count("id", filter=Q(zoho_task_id=""), distinct=True),
        with_task=Count("id", filter=~Q(zoho_task_id=""), distinct=True),
        missing_operation=Count(
            "id", filter=Q(exceptions__exception_type="MISSING_OPERATION",
                           exceptions__detection_status="DETECTED"), distinct=True,
        ),
        missing_charge=Count(
            "id", filter=Q(exceptions__exception_type="MISSING_CHARGE",
                           exceptions__detection_status="DETECTED"), distinct=True,
        ),
    )
    metrics["multiple"] = snapshot_cases.annotate(
            total=Count("exceptions", filter=Q(exceptions__detection_status="DETECTED"), distinct=True)
        ).filter(total__gte=2).count()
    for item in page.object_list:
        item.policy_link = _case_link(item, "policy_link")
        item.operation_link = _case_link(item, "operation_link") if item.operation_id else ""
    return render(request, "cotizacion_colectivos/billing_exceptions/list.html", {
        **_base_context(request), "form": form, "page": page, "metrics": metrics,
        "latest_success": latest_success, "latest_run": latest_run, "active_run": active_run,
        "quick_card_urls": _quick_card_urls(request.GET),
        "active_card": form.cleaned_data.get("quick") or (
            "active" if form.cleaned_data.get("detection") == "DETECTED" else ""
        ),
    })


@never_cache
@require_http_methods(["GET"])
def billing_exception_detail(request, case_id):
    if not has_internal_permission(request, "view_billing_exceptions"):
        return permission_denied_response()
    item = get_object_or_404(
        BillingOperationalCase.objects.prefetch_related("exceptions"), pk=case_id
    )
    findings = tuple(item.exceptions.order_by("exception_type", "exception_key"))
    context = {}
    for finding in findings:
        raw_context = finding.context or ()
        entries = raw_context.items() if isinstance(raw_context, dict) else raw_context
        for key, value in entries:
            if not any(part in str(key).casefold() for part in ("token", "secret", "password", "credential")):
                context.setdefault(key, value)
    policy_link = _safe_zoho_link(context.get("policy_link"))
    operation_link = _safe_zoho_link(context.get("operation_link")) if item.operation_id else ""
    note = str(context.pop("note", "") or "").strip()
    observations = str(context.pop("observations", "") or "").strip()
    technical_items = tuple(
        (TECHNICAL_CONTEXT_LABELS.get(key, "Dato técnico"), value)
        for key, value in sorted(context.items())
    )
    return render(request, "cotizacion_colectivos/billing_exceptions/detail.html", {
        **_base_context(request), "item": item, "findings": findings,
        "finding_types": {finding.exception_type for finding in findings},
        "technical_items": technical_items,
        "note": note, "observations": observations,
        "policy_link": policy_link, "operation_link": operation_link,
    })


@never_cache
@require_http_methods(["POST"])
def billing_exception_refresh(request):
    if not has_internal_permission(request, "refresh_billing_exceptions"):
        return permission_denied_response()
    try:
        queue_billing_exceptions_refresh(
            as_of=timezone.localdate(),
            requested_by=request.user if request.user.is_authenticated else None,
        )
        messages.success(request, "Actualización programada. El proceso se ejecutará en segundo plano.")
    except BillingRefreshAlreadyRunning:
        messages.info(request, "Ya existe una actualización pendiente o en curso.")
    return redirect("cotizacion_colectivos:billing_exception_list")
