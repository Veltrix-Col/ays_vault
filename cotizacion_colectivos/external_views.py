from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .forms import AttachmentUploadForm, ExternalOTPForm, ExternalSubmitForm
from .models import CambioSolicitudColectivo, RespuestaSolicitudColectivo
from .services.attachments import store_attachment
from .services.excel_roundtrip import build_novelties_template
from .services.excel_previews import cancel_preview, confirm_preview, create_preview, resolve_preview
from .services.external import (
    EXTERNAL_COOKIE,
    ExternalAccessError,
    authorize_token_only,
    issue_otp,
    resolve_external_session,
    resolve_token,
    save_response,
    submit_response,
    verify_otp,
)
from .services.requests import request_snapshot


def _set_external_cookie(response, value: str):
    response.set_cookie(
        EXTERNAL_COOKIE, value, max_age=settings.COLECTIVOS_EXTERNAL_SESSION_TTL_SECONDS,
        secure=not settings.DEBUG, httponly=True, samesite="Lax", path="/solicitudes/colectivos/externa/",
    )
    return response


def _clear_external_cookie(response):
    response.delete_cookie(EXTERNAL_COOKIE, path="/solicitudes/colectivos/externa/", samesite="Lax")
    return response


def _access_from_cookie(request):
    return resolve_external_session(request.COOKIES.get(EXTERNAL_COOKIE, ""))


@never_cache
@require_http_methods(["GET"])
def entry(request, token):
    try:
        access = resolve_token(token)
        if settings.COLECTIVOS_EXTERNAL_ACCESS_VERIFICATION == "token_only":
            return _set_external_cookie(redirect("colectivos_external:portal"), authorize_token_only(access))
        if not access.otp_hash or not access.otp_expires_at or access.otp_expires_at <= timezone.now():
            issue_otp(access)
    except ExternalAccessError:
        return render(request, "cotizacion_colectivos/external/unavailable.html", status=410)
    return render(request, "cotizacion_colectivos/external/verify.html", {"form": ExternalOTPForm(), "token": token, "public_id": access.request.public_id})


@never_cache
@require_http_methods(["POST"])
def verify(request, token):
    form = ExternalOTPForm(request.POST)
    try:
        access = resolve_token(token)
        if not form.is_valid():
            raise ExternalAccessError("No fue posible validar el acceso.")
        cookie = verify_otp(access, form.cleaned_data["code"])
    except ExternalAccessError:
        return render(request, "cotizacion_colectivos/external/verify.html", {"form": ExternalOTPForm(), "token": token, "error": "No fue posible validar el acceso."}, status=400)
    return _set_external_cookie(redirect("colectivos_external:portal"), cookie)


def _rows(request_obj):
    return request_obj.records.only("public_key", "role", "initial_status", "entry_date", "exit_date", "plan", "encrypted_branch_payload").order_by("original_position")


@never_cache
@require_http_methods(["GET"])
def portal(request):
    try:
        access = _access_from_cookie(request)
        snapshot = request_snapshot(access.request)
    except (ExternalAccessError, ValidationError):
        return _clear_external_cookie(render(request, "cotizacion_colectivos/external/unavailable.html", status=403))
    latest = access.request.responses.filter(status=RespuestaSolicitudColectivo.Status.DRAFT).prefetch_related("changes").first()
    return render(request, "cotizacion_colectivos/external/portal.html", {"item": access.request, "snapshot": snapshot, "records": _rows(access.request), "latest": latest, "submit_form": ExternalSubmitForm(), "attachment_form": AttachmentUploadForm(), "health_enabled": access.request.branch_code == "91"})


def _posted_rows(request, request_obj):
    rows = []
    for record in _rows(request_obj):
        key = str(record.public_key)
        rows.append({"record": key, "action": request.POST.get(f"action_{key}", "SIN_CAMBIOS"), "plan": request.POST.get(f"plan_{key}", ""), "parentesco": request.POST.get(f"parentesco_{key}", ""), "fecha_efectiva": request.POST.get(f"fecha_efectiva_{key}", ""), "fecha_ingreso": request.POST.get(f"fecha_ingreso_{key}", ""), "fecha_retiro": request.POST.get(f"fecha_retiro_{key}", ""), "motivo": request.POST.get(f"motivo_{key}", ""), "observaciones": request.POST.get(f"observaciones_{key}", "")})
    if request.POST.get("include_action") == "INCLUIR":
        rows.append({"record": "", "action": "INCLUIR", "tipo_id": request.POST.get("include_tipo_id", ""), "documento": request.POST.get("include_documento", ""), "nombre": request.POST.get("include_nombre", ""), "rol": request.POST.get("include_rol", ""), "plan": request.POST.get("include_plan", ""), "parentesco": request.POST.get("include_parentesco", ""), "fecha_efectiva": request.POST.get("include_fecha_efectiva", ""), "motivo": request.POST.get("include_motivo", ""), "observaciones": request.POST.get("include_observaciones", "")})
    return rows


@never_cache
@require_http_methods(["POST"])
def save_draft(request):
    try:
        access = _access_from_cookie(request)
        if access.request.branch_code != "91":
            raise ExternalAccessError("El formulario editable aún no está habilitado para este ramo.")
        save_response(access=access, rows=_posted_rows(request, access.request), observations=request.POST.get("client_observations", ""))
    except ExternalAccessError as exc:
        return HttpResponse(str(exc.messages[0] if exc.messages else "No fue posible guardar."), status=400)
    return redirect("colectivos_external:portal")


@never_cache
@require_http_methods(["POST"])
def submit(request):
    form = ExternalSubmitForm(request.POST)
    try:
        access = _access_from_cookie(request)
        response = access.request.responses.filter(status=RespuestaSolicitudColectivo.Status.DRAFT).first()
        if not form.is_valid() or not response:
            raise ExternalAccessError("La respuesta no está lista para enviar.")
        submit_response(access=access, response=response, declaration=form.cleaned_data["declaration"])
    except ExternalAccessError:
        return HttpResponse("La respuesta no está lista para enviar.", status=400)
    return _clear_external_cookie(render(request, "cotizacion_colectivos/external/submitted.html", {"public_id": access.request.public_id}))


@never_cache
@require_http_methods(["POST"])
def upload_attachment(request):
    form = AttachmentUploadForm(request.POST, request.FILES)
    try:
        access = _access_from_cookie(request)
        response = access.request.responses.filter(status=RespuestaSolicitudColectivo.Status.DRAFT).first()
        if not form.is_valid() or not response:
            raise ValidationError("No fue posible cargar el archivo.")
        store_attachment(response=response, uploaded=form.cleaned_data["attachment"])
    except (ExternalAccessError, ValidationError):
        return HttpResponse("No fue posible cargar el archivo.", status=400)
    return redirect("colectivos_external:portal")


@never_cache
@require_http_methods(["POST"])
def download_template(request):
    try:
        access = _access_from_cookie(request)
        content = build_novelties_template(access.request)
    except ExternalAccessError:
        return HttpResponse("Acceso no disponible.", status=403)
    response = HttpResponse(content, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f'attachment; filename="novedades-{access.request.public_id}.xlsx"'
    response["Cache-Control"] = "no-store, private"
    return response


@never_cache
@require_http_methods(["POST"])
def upload_excel(request):
    uploaded = request.FILES.get("workbook")
    try:
        access = _access_from_cookie(request)
        if not uploaded:
            raise ValidationError("Debe seleccionar un archivo.")
        item, token = create_preview(access=access, session_cookie=request.COOKIES.get(EXTERNAL_COOKIE, ""), uploaded=uploaded)
    except (ExternalAccessError, ValidationError):
        return HttpResponse("El archivo no supera la validación.", status=400)
    return redirect("colectivos_external:excel_preview", token=token)


@never_cache
@require_http_methods(["GET"])
def excel_preview(request, token):
    try:
        access = _access_from_cookie(request)
        item = resolve_preview(token=token, access=access, session_cookie=request.COOKIES.get(EXTERNAL_COOKIE, ""))
    except ExternalAccessError:
        return HttpResponse("La vista previa no está disponible.", status=403)
    return render(request, "cotizacion_colectivos/external/excel_preview.html", {"item": item, "token": token})


@never_cache
@require_http_methods(["POST"])
def confirm_excel_preview(request, token):
    try:
        access = _access_from_cookie(request)
        confirm_preview(token=token, access=access, session_cookie=request.COOKIES.get(EXTERNAL_COOKIE, ""))
    except (ExternalAccessError, ValidationError):
        return HttpResponse("No fue posible confirmar la importación.", status=400)
    return redirect("colectivos_external:portal")


@never_cache
@require_http_methods(["POST"])
def cancel_excel_preview(request, token):
    try:
        access = _access_from_cookie(request)
        cancel_preview(token=token, access=access, session_cookie=request.COOKIES.get(EXTERNAL_COOKIE, ""))
    except ExternalAccessError:
        return HttpResponse("No fue posible cancelar la vista previa.", status=400)
    return redirect("colectivos_external:portal")
