from time import monotonic
from urllib.parse import quote

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from .decorators import role_required
from .forms import ReportRequestForm, TimelineFilterForm
from .identity import create_alert
from .models import ReportExport, UserProfile
from .reporting import (
    ReportValidationError,
    allowed_report_types,
    build_excel,
    build_pdf,
    build_report,
    report_filename,
    safe_filter_summary,
)
from .security import audit, client_ip


REPORT_CARDS = {
    "TIMELINE": "Trazabilidad cronológica con objetos y motivos protegidos.",
    "ALERTS": "Estado, responsables, vencimientos y cierres de alertas autorizadas.",
    "ACCESS": "Ingresos, MFA, dispositivos y accesos fuera de horario.",
    "ADOPTION": "Uso real del sistema y señales que requieren validar adopción.",
    "CARDS": "Inventario operativo sin PAN ni fecha de vencimiento.",
    "HEALTH": "Estado explicable de auditoría, MFA, alertas y tareas programadas.",
}


@require_GET
@never_cache
@role_required(UserProfile.ADMIN, UserProfile.LEADER, UserProfile.ANALYST)
def report_center(request):
    allowed = allowed_report_types(request.user)
    labels = dict(ReportExport.TYPES)
    history = ReportExport.objects.select_related("user")
    if request.user.vault_profile.role != UserProfile.ADMIN:
        history = history.filter(user=request.user)
    cards = []
    for report_type in allowed:
        cards.append({
            "type": report_type,
            "name": labels[report_type],
            "description": REPORT_CARDS[report_type],
            "last": history.filter(report_type=report_type, result="SUCCESS").first(),
        })
    form = ReportRequestForm(allowed_types=allowed, initial={"orientation": "auto", "detail": "detail"})
    return render(request, "vault/reports/center.html", {"report_cards": cards, "history": history[:50], "report_form": form})


def _filters_for_export(request, report_type):
    if report_type == "TIMELINE":
        form = TimelineFilterForm(request.POST, user=request.user)
        if not form.is_valid():
            return None, form
        return form.cleaned_data.copy(), form
    allowed = allowed_report_types(request.user)
    data = request.POST.copy()
    data["report_type"] = report_type
    data["export_format"] = request.resolver_match.kwargs["export_format"]
    form = ReportRequestForm(data, allowed_types=allowed)
    if not form.is_valid():
        return None, form
    return {"date_from": form.cleaned_data.get("date_from"), "date_to": form.cleaned_data.get("date_to")}, form


@require_POST
@never_cache
@role_required(UserProfile.ADMIN, UserProfile.LEADER, UserProfile.ANALYST)
def export_report(request, report_type, export_format):
    report_type = report_type.upper()
    export_format = export_format.upper()
    if report_type not in allowed_report_types(request.user) or export_format not in {"XLSX", "PDF"}:
        raise PermissionDenied
    filters, form = _filters_for_export(request, report_type)
    if filters is None:
        error_text = " ".join(message for errors in form.errors.values() for message in errors)
        return HttpResponseBadRequest(f"No fue posible generar el informe: {error_text}")

    limit = settings.REPORT_XLSX_MAX_ROWS if export_format == "XLSX" else settings.REPORT_PDF_MAX_ROWS
    filters["_row_limit"] = limit
    safe_filters = safe_filter_summary(filters)
    record = ReportExport.objects.create(
        report_type=report_type,
        export_format=export_format,
        user=request.user,
        actor_role=request.user.vault_profile.role,
        safe_filters=safe_filters,
        ip_address=client_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:300],
    )
    started = monotonic()
    try:
        data = build_report(report_type, request.user, filters)
        if len(data.rows) > limit:
            record.record_count = len(data.rows)
            record.result = "LIMITED"
            record.safe_error = "ROW_LIMIT_EXCEEDED"
            record.duration_ms = int((monotonic() - started) * 1000)
            record.finished_at = timezone.now()
            record.save(update_fields=["record_count", "result", "safe_error", "duration_ms", "finished_at"])
            audit(request, "REPORT_EXPORT", reason="Exportación rechazada por límite de registros.", metadata={"report_type": report_type, "format": export_format, "filters": safe_filters, "limit": limit}, result="DENIED", risk_level="MEDIUM")
            return HttpResponseBadRequest(f"El informe supera el límite seguro de {limit} registros. Reduzca el periodo o aplique más filtros.")

        filename = report_filename(data, export_format)
        if export_format == "XLSX":
            content = build_excel(data, request.user, request.user.vault_profile.role)
            content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        else:
            orientation = request.POST.get("orientation", "auto")
            if orientation not in {"auto", "portrait", "landscape"}:
                orientation = "auto"
            content = build_pdf(data, request.user, request.user.vault_profile.role, orientation=orientation, base_url=request.build_absolute_uri("/"))
            content_type = "application/pdf"

        record.record_count = len(data.rows)
        record.result = "SUCCESS"
        record.duration_ms = int((monotonic() - started) * 1000)
        record.filename = filename
        record.finished_at = timezone.now()
        record.save(update_fields=["record_count", "result", "duration_ms", "filename", "finished_at"])
        event = audit(request, "REPORT_EXPORT", reason="Informe generado de forma segura.", metadata={"report_type": report_type, "format": export_format, "filters": safe_filters, "records": len(data.rows)}, risk_level="LOW")
        if len(data.rows) >= settings.REPORT_LARGE_EXPORT_ALERT_THRESHOLD:
            create_alert(request, event, "LARGE_REPORT_EXPORT", "HIGH", affected_user=request.user, description="Se generó un informe con un volumen inusual.", metadata={"report_type": report_type, "format": export_format, "records": len(data.rows)})
        response = HttpResponse(content, content_type=content_type)
        response["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(filename)}"
        response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response["Pragma"] = "no-cache"
        response["X-Content-Type-Options"] = "nosniff"
        return response
    except (ReportValidationError, PermissionDenied):
        record.result = "FAILED"
        record.safe_error = "PERMISSION_OR_SCOPE_ERROR"
        record.duration_ms = int((monotonic() - started) * 1000)
        record.finished_at = timezone.now()
        record.save(update_fields=["result", "safe_error", "duration_ms", "finished_at"])
        audit(request, "REPORT_EXPORT", reason="Exportación rechazada por alcance.", metadata={"report_type": report_type, "format": export_format}, result="DENIED", risk_level="MEDIUM")
        raise PermissionDenied
    except Exception:
        record.result = "FAILED"
        record.safe_error = "RENDERING_ERROR"
        record.duration_ms = int((monotonic() - started) * 1000)
        record.finished_at = timezone.now()
        record.save(update_fields=["result", "safe_error", "duration_ms", "finished_at"])
        audit(request, "REPORT_EXPORT", reason="La generación del informe falló de forma segura.", metadata={"report_type": report_type, "format": export_format}, result="FAILED", risk_level="HIGH")
        messages.error(request, "No fue posible generar el informe. El intento quedó registrado sin exponer detalles técnicos.")
        return redirect("vault:report_center")
