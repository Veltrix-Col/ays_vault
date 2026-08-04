from __future__ import annotations

import logging
import time
import uuid
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.shortcuts import get_object_or_404, redirect
from django.core.paginator import Paginator
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .forms import CompanySearchForm, ExternalAccessPrepareForm, PersonSearchForm, RequestCreateForm, RequestEditForm, RequestFilterForm, RequestTransitionForm, SnapshotRegenerateForm
from .services import CompanySearchService, EntityDetailService, PersonSearchService, PolicyService
from .services.common import ColectivosServiceError, unsign_record_context
from .excel import build_current_policy_workbook
from .permissions import has_internal_permission, permission_denied_response
from .models import AdjuntoSolicitudColectivo, NotificacionColectivos, RespuestaSolicitudColectivo, SolicitudColectivo
from .services.requests import create_request_from_policy, regenerate_request_snapshot, request_snapshot, transition_request, update_draft_request
from .services.external import ExternalAccessError, generate_access, send_invitation
from .services.excel_roundtrip import (
    build_approved_consolidated,
    build_comparison,
    build_novelties_template,
    build_response_workbook,
)
from .services.review import finalize_review, record_reviews
from pathlib import Path
from django.conf import settings
from django.http import FileResponse
from vault.security import audit
from vault.crypto import decrypt
from .zoho import get_colectivos_environment


logger = logging.getLogger("cotizacion_colectivos")


def _environment_context():
    return {"zoho_environment": get_colectivos_environment()}


def _error_status(exc):
    if exc.code == "permission":
        return 403
    if exc.code in {"invalid_record", "not_found"}:
        return 404
    return 503


@never_cache
@require_http_methods(["GET"])
def index(request):
    return render(request, "cotizacion_colectivos/index.html", {
        "company_form": CompanySearchForm(auto_id="id_company_%s"),
        "person_form": PersonSearchForm(auto_id="id_person_%s"),
        **_environment_context(),
    })


def _search(request, *, form_class, service_class, entity_kind):
    environment = get_colectivos_environment()
    form = form_class(request.POST or None)
    results, error, status = None, "", 200
    if request.method == "POST" and form.is_valid():
        started = time.monotonic()
        correlation = uuid.uuid4().hex
        error_category = "none"
        try:
            query = form.cleaned_data["query"]
            results = service_class().search(query)
            if query.isdigit():
                form = form_class()
        except ColectivosServiceError as exc:
            error_category = exc.code
            error, status = exc.message, _error_status(exc)
        except Exception:
            error_category = "unknown"
            error = f"{environment['label']} no está disponible temporalmente. Intente nuevamente más tarde."
            status = 503
        logger.info(
            "colectivos_search application=cotizacion_colectivos entity=%s operation=search duration_ms=%d results=%d error=%s user_id=%s profile=%s correlation=%s",
            entity_kind,
            round((time.monotonic() - started) * 1000),
            len(results or ()),
            error_category,
            request.user.pk,
            environment["profile"],
            correlation,
        )
    return render(request, "cotizacion_colectivos/search.html", {
        "form": form, "results": results, "error": error, "entity_kind": entity_kind,
        "zoho_environment": environment,
    }, status=status)


@never_cache
@require_http_methods(["GET", "POST"])
def company_search(request):
    return _search(request, form_class=CompanySearchForm, service_class=CompanySearchService, entity_kind="company")


@never_cache
@require_http_methods(["GET", "POST"])
def person_search(request):
    return _search(request, form_class=PersonSearchForm, service_class=PersonSearchService, entity_kind="person")


def _detail(request, token, *, method, entity_kind):
    environment = get_colectivos_environment()
    started = time.monotonic()
    correlation = uuid.uuid4().hex
    error_category = "none"
    try:
        detail = getattr(EntityDetailService(), method)(token)
    except ColectivosServiceError as exc:
        error_category = exc.code
        if exc.code in {"invalid_record", "not_found"}:
            _log_detail(request, entity_kind, environment, started, error_category, correlation, 0)
            raise Http404("Registro no encontrado") from exc
        _log_detail(request, entity_kind, environment, started, error_category, correlation, 0)
        return render(request, "cotizacion_colectivos/detail_error.html", {
            "message": exc.message, "zoho_environment": environment,
        }, status=_error_status(exc))
    except Exception:
        _log_detail(request, entity_kind, environment, started, "unknown", correlation, 0)
        return render(
            request,
            "cotizacion_colectivos/detail_error.html",
            {"message": "No fue posible consultar la información relacionada. Intente nuevamente más tarde.", "zoho_environment": environment},
            status=503,
        )
    _log_detail(request, entity_kind, environment, started, error_category, correlation, 1)
    return render(request, "cotizacion_colectivos/detail.html", {
        "detail": detail, "entity_kind": entity_kind, "zoho_environment": environment,
    })


def _log_detail(request, entity_kind, environment, started, error_category, correlation, results):
    logger.info(
        "colectivos_detail application=cotizacion_colectivos entity=%s operation=detail duration_ms=%d results=%d error=%s user_id=%s profile=%s correlation=%s",
        entity_kind,
        round((time.monotonic() - started) * 1000),
        results,
        error_category,
        request.user.pk,
        environment["profile"],
        correlation,
    )


@never_cache
@require_http_methods(["GET"])
def company_detail(request, token):
    return _detail(request, token, method="company", entity_kind="company")


@never_cache
@require_http_methods(["GET"])
def person_detail(request, token):
    return _detail(request, token, method="person", entity_kind="person")


@never_cache
@require_http_methods(["GET"])
def policy_detail(request, token):
    environment = get_colectivos_environment()
    try:
        detail = PolicyService().detail(token)
    except ColectivosServiceError as exc:
        if exc.code in {"invalid_record", "not_found"}:
            raise Http404("Póliza no encontrada") from exc
        return render(request, "cotizacion_colectivos/detail_error.html", {"message": exc.message, "zoho_environment": environment}, status=_error_status(exc))
    token_context = unsign_record_context(token, "policy")
    source_kind = token_context.get("source_kind") or "company"
    return render(request, "cotizacion_colectivos/policy_detail.html", {"detail": detail, "source_kind": source_kind, "zoho_environment": environment, "can_export": has_internal_permission(request, "export_excel"), "can_create": has_internal_permission(request, "create_requests")})


@never_cache
@require_http_methods(["GET"])
def policy_group(request, token):
    environment = get_colectivos_environment()
    try:
        detail, members = PolicyService().group(token)
    except ColectivosServiceError as exc:
        if exc.code in {"invalid_record", "not_found"}:
            raise Http404("Póliza no encontrada") from exc
        return render(request, "cotizacion_colectivos/detail_error.html", {"message": exc.message, "zoho_environment": environment}, status=_error_status(exc))
    return render(request, "cotizacion_colectivos/policy_group.html", {"detail": detail, "members": members, "zoho_environment": environment})


@never_cache
@require_http_methods(["POST"])
def policy_excel(request, token):
    if not has_internal_permission(request, "export_excel"):
        return permission_denied_response()
    try:
        content = build_current_policy_workbook(token)
    except ColectivosServiceError as exc:
        if exc.code in {"invalid_record", "not_found"}:
            raise Http404("Póliza no encontrada") from exc
        return HttpResponse("No fue posible generar el archivo.", status=503)
    response = HttpResponse(content, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = 'attachment; filename="grupo-actual-colectivos.xlsx"'
    response["Cache-Control"] = "no-store, private"
    response["Pragma"] = "no-cache"
    audit(request, "REPORT_EXPORT", reason="Exportación de grupo actual Colectivos.", metadata={"application": "cotizacion_colectivos", "format": "xlsx"})
    return response


@never_cache
@require_http_methods(["GET", "POST"])
def request_create(request, token):
    if not has_internal_permission(request, "create_requests"):
        return permission_denied_response()
    environment = get_colectivos_environment()
    service = PolicyService()
    try:
        token_context = unsign_record_context(token, "policy")
    except ColectivosServiceError as exc:
        raise Http404("Póliza no encontrada") from exc
    form = RequestCreateForm(request.POST or None, initial={"source_kind": token_context.get("source_kind") or "company", "assigned_to": request.user, "deadline": timezone.localdate() + timedelta(days=10)})
    error = ""
    if request.method == "POST" and form.is_valid():
        try:
            item = create_request_from_policy(
                token=token, source_kind=form.cleaned_data["source_kind"], actor=request.user,
                assigned_to=form.cleaned_data["assigned_to"], request_type=form.cleaned_data["request_type"],
                deadline=form.cleaned_data["deadline"], internal_notes=form.cleaned_data["internal_notes"],
                is_test=form.cleaned_data["is_test"], service=service,
            )
        except ColectivosServiceError as exc:
            error = exc.message
        else:
            audit(request, "CREATE", reason="Expediente Colectivos creado.", metadata={"request_id": item.public_id, "branch": item.branch_code, "records": item.record_count})
            return redirect("cotizacion_colectivos:request_detail", public_id=item.public_id)
    try:
        policy = service.detail(token)
    except ColectivosServiceError as exc:
        if exc.code in {"invalid_record", "not_found"}:
            raise Http404("Póliza no encontrada") from exc
        return render(request, "cotizacion_colectivos/detail_error.html", {"message": exc.message, "zoho_environment": environment}, status=_error_status(exc))
    return render(request, "cotizacion_colectivos/request_form.html", {"form": form, "policy": policy, "error": error, "zoho_environment": environment})


@never_cache
@require_http_methods(["GET"])
def request_list(request):
    if not has_internal_permission(request, "view_requests"):
        return permission_denied_response()
    form = RequestFilterForm(request.GET)
    queryset = SolicitudColectivo.objects.select_related("assigned_to").all()
    if form.is_valid():
        data = form.cleaned_data
        if data["query"]:
            term = data["query"].strip()
            queryset = queryset.filter(
                Q(public_id__icontains=term)
                | Q(client_label__icontains=term)
                | Q(masked_policy_reference__icontains=term)
            )
        if data["status"]:
            queryset = queryset.filter(status=data["status"])
        if data["source_kind"]:
            queryset = queryset.filter(source_kind=data["source_kind"])
        if data["branch"]:
            queryset = queryset.filter(branch_code=data["branch"].strip())
        if data["request_type"]:
            queryset = queryset.filter(request_type=data["request_type"])
        if data["assigned_to"]:
            queryset = queryset.filter(assigned_to=data["assigned_to"])
        if data["created_from"]:
            queryset = queryset.filter(created_at__date__gte=data["created_from"])
        if data["created_to"]:
            queryset = queryset.filter(created_at__date__lte=data["created_to"])
        if data["deadline_from"]:
            queryset = queryset.filter(deadline__gte=data["deadline_from"])
        if data["deadline_to"]:
            queryset = queryset.filter(deadline__lte=data["deadline_to"])
        if data["assigned_to_me"]:
            queryset = queryset.filter(assigned_to=request.user)
        if data["warning"]:
            queryset = queryset.exclude(warnings=[])
    page = Paginator(queryset, 25).get_page(request.GET.get("page"))
    query = request.GET.copy()
    query.pop("page", None)
    return render(
        request,
        "cotizacion_colectivos/request_list.html",
        {"form": form, "page": page, "filter_query": query.urlencode(), **_environment_context()},
    )


@never_cache
@require_http_methods(["GET"])
def request_detail(request, public_id):
    if not has_internal_permission(request, "view_requests"):
        return permission_denied_response()
    item = get_object_or_404(SolicitudColectivo.objects.select_related("assigned_to", "created_by"), public_id=public_id)
    try:
        snapshot = request_snapshot(item)
    except ValidationError:
        snapshot = None
    try:
        notes = decrypt(item.encrypted_internal_notes) if item.encrypted_internal_notes else ""
    except ValueError:
        notes = ""
    edit_form = RequestEditForm(initial={"assigned_to": item.assigned_to, "deadline": item.deadline, "internal_notes": notes})
    return render(request, "cotizacion_colectivos/request_detail.html", {"item": item, "snapshot": snapshot, "transition_form": RequestTransitionForm(), "edit_form": edit_form, "snapshot_form": SnapshotRegenerateForm(), "can_edit": has_internal_permission(request, "edit_requests"), "can_approve": has_internal_permission(request, "approve_requests"), **_environment_context()})


@never_cache
@require_http_methods(["POST"])
def request_edit(request, public_id):
    item = get_object_or_404(SolicitudColectivo, public_id=public_id)
    if not has_internal_permission(request, "edit_requests"):
        return permission_denied_response()
    form = RequestEditForm(request.POST)
    if not form.is_valid():
        return HttpResponse("Los datos del borrador no son válidos.", status=400)
    try:
        update_draft_request(request=item, actor=request.user, assigned_to=form.cleaned_data["assigned_to"], deadline=form.cleaned_data["deadline"], internal_notes=form.cleaned_data["internal_notes"])
    except ValidationError:
        return HttpResponse("El borrador no puede editarse en su estado actual.", status=400)
    audit(request, "UPDATE", reason="Borrador de expediente Colectivos actualizado.", metadata={"request_id": item.public_id})
    return redirect("cotizacion_colectivos:request_detail", public_id=item.public_id)


@never_cache
@require_http_methods(["POST"])
def request_regenerate_snapshot(request, public_id):
    item = get_object_or_404(SolicitudColectivo, public_id=public_id)
    if not has_internal_permission(request, "edit_requests"):
        return permission_denied_response()
    form = SnapshotRegenerateForm(request.POST)
    if not form.is_valid():
        return HttpResponse("Debe confirmar la regeneración del snapshot.", status=400)
    try:
        regenerate_request_snapshot(request=item, actor=request.user)
    except (ColectivosServiceError, ValidationError):
        return HttpResponse("No fue posible regenerar el snapshot.", status=400)
    audit(request, "UPDATE", reason="Snapshot de expediente Colectivos regenerado.", metadata={"request_id": item.public_id})
    return redirect("cotizacion_colectivos:request_detail", public_id=item.public_id)


@never_cache
@require_http_methods(["GET", "POST"])
def request_external_access(request, public_id, regenerate=False):
    item = get_object_or_404(SolicitudColectivo.objects.select_related("assigned_to"), public_id=public_id)
    permission = "regenerate_external_access" if regenerate else "generate_external_access"
    if not has_internal_permission(request, permission) or not has_internal_permission(request, "send_requests"):
        return permission_denied_response()
    form = ExternalAccessPrepareForm(request.POST or None, initial={"deadline": item.deadline})
    error = ""
    if request.method == "POST" and form.is_valid():
        item.deadline = form.cleaned_data["deadline"]
        item.save(update_fields=("deadline", "updated_at"))
        try:
            generated = generate_access(
                request=item, actor=request.user, recipient=form.cleaned_data["recipient"],
                contact_name=form.cleaned_data["contact_name"], intro=form.cleaned_data["intro"],
                instructions=form.cleaned_data["instructions"], regenerate=regenerate,
            )
            send_invitation(generated)
        except ExternalAccessError as exc:
            error = exc.messages[0] if exc.messages else "No fue posible preparar el acceso."
        else:
            audit(request, "UPDATE", reason="Acceso externo de Colectivos generado y enviado.", metadata={"request_id": item.public_id, "regenerated": regenerate})
            return redirect("cotizacion_colectivos:request_detail", public_id=item.public_id)
    return render(request, "cotizacion_colectivos/external_access_form.html", {"item": item, "form": form, "error": error, "regenerate": regenerate, **_environment_context()})


@never_cache
@require_http_methods(["POST"])
def request_novelties_template(request, public_id):
    item = get_object_or_404(SolicitudColectivo, public_id=public_id)
    if not has_internal_permission(request, "export_excel"):
        return permission_denied_response()
    response = HttpResponse(build_novelties_template(item), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f'attachment; filename="novedades-{item.public_id}.xlsx"'
    response["Cache-Control"] = "no-store, private"
    return response


@never_cache
@require_http_methods(["GET", "POST"])
def response_review(request, public_id, version):
    item = get_object_or_404(SolicitudColectivo, public_id=public_id)
    response = get_object_or_404(RespuestaSolicitudColectivo.objects.prefetch_related("changes__reviews", "attachments"), request=item, version=version)
    if not has_internal_permission(request, "review_responses"):
        return permission_denied_response()
    error = ""
    if request.method == "POST":
        decisions = {}
        for change in response.changes.all():
            decision = request.POST.get(f"decision_{change.pk}", "")
            if decision:
                decisions[change.pk] = {"decision": decision, "approved_value": request.POST.get(f"approved_{change.pk}", ""), "internal_observation": request.POST.get(f"internal_{change.pk}", ""), "client_observation": request.POST.get(f"client_{change.pk}", "")}
        try:
            record_reviews(response=response, reviewer=request.user, decisions=decisions)
            action = request.POST.get("finalize", "")
            if action:
                required = "approve_responses" if action == "approve" else "request_corrections"
                if not has_internal_permission(request, required):
                    return permission_denied_response()
                finalize_review(response=response, reviewer=request.user, action=action)
        except ValidationError as exc:
            error = exc.messages[0]
        else:
            audit(request, "UPDATE", reason="Revisión de respuesta Colectivos actualizada.", metadata={"request_id": item.public_id, "version": version})
            return redirect("cotizacion_colectivos:response_review", public_id=item.public_id, version=version)
    return render(request, "cotizacion_colectivos/response_review.html", {"item": item, "response": response, "error": error, **_environment_context()})


@never_cache
@require_http_methods(["POST"])
def response_comparison(request, public_id, version):
    item = get_object_or_404(SolicitudColectivo, public_id=public_id)
    response_obj = get_object_or_404(RespuestaSolicitudColectivo, request=item, version=version)
    if not has_internal_permission(request, "export_comparison"):
        return permission_denied_response()
    response = HttpResponse(build_comparison(response_obj), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f'attachment; filename="comparativo-{item.public_id}-v{version}.xlsx"'
    response["Cache-Control"] = "no-store, private"
    return response


@never_cache
@require_http_methods(["POST"])
def response_export(request, public_id, version):
    item = get_object_or_404(SolicitudColectivo, public_id=public_id)
    response_obj = get_object_or_404(RespuestaSolicitudColectivo, request=item, version=version)
    if not has_internal_permission(request, "export_response"):
        return permission_denied_response()
    response = HttpResponse(build_response_workbook(response_obj), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f'attachment; filename="respuesta-{item.public_id}-v{version}.xlsx"'
    response["Cache-Control"] = "no-store, private"
    return response


@never_cache
@require_http_methods(["POST"])
def response_approved_export(request, public_id, version):
    item = get_object_or_404(SolicitudColectivo, public_id=public_id)
    response_obj = get_object_or_404(RespuestaSolicitudColectivo, request=item, version=version)
    if not has_internal_permission(request, "export_approved"):
        return permission_denied_response()
    try:
        content = build_approved_consolidated(response_obj)
    except ValidationError:
        return HttpResponse("El consolidado no está disponible.", status=409)
    response = HttpResponse(content, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f'attachment; filename="consolidado-{item.public_id}-v{version}.xlsx"'
    response["Cache-Control"] = "no-store, private"
    return response


@never_cache
@require_http_methods(["GET"])
def attachment_download(request, public_id, attachment_id):
    item = get_object_or_404(SolicitudColectivo, public_id=public_id)
    attachment = get_object_or_404(AdjuntoSolicitudColectivo, pk=attachment_id, request=item)
    if not has_internal_permission(request, "download_attachments"):
        return permission_denied_response()
    root = Path(settings.COLECTIVOS_PRIVATE_ROOT).resolve()
    target = (root / attachment.stored_path).resolve()
    if root not in target.parents or not target.is_file():
        raise Http404("Adjunto no disponible")
    response = FileResponse(target.open("rb"), content_type=attachment.detected_mime, as_attachment=True, filename=f"soporte{attachment.extension}")
    response["Cache-Control"] = "no-store, private"
    return response


@never_cache
@require_http_methods(["POST"])
def request_transition(request, public_id):
    item = get_object_or_404(SolicitudColectivo, public_id=public_id)
    form = RequestTransitionForm(request.POST)
    if not form.is_valid():
        return HttpResponse("Transición inválida.", status=400)
    target = form.cleaned_data["target"]
    permission = "approve_requests" if target == SolicitudColectivo.Status.APPROVED else ("close_requests" if target == SolicitudColectivo.Status.CLOSED else ("cancel_requests" if target == SolicitudColectivo.Status.CANCELLED else "create_requests"))
    if not has_internal_permission(request, permission):
        return permission_denied_response()
    try:
        transition_request(request=item, target=target, actor=request.user)
    except ValidationError:
        return HttpResponse("La transición no está permitida.", status=400)
    audit(request, "UPDATE", reason="Estado de expediente Colectivos actualizado.", metadata={"request_id": item.public_id, "target": target})
    return redirect("cotizacion_colectivos:request_detail", public_id=item.public_id)


@never_cache
@require_http_methods(["GET"])
def notification_list(request):
    if not has_internal_permission(request, "manage_notifications"):
        return permission_denied_response()
    page = Paginator(NotificacionColectivos.objects.filter(user=request.user).select_related("request"), 25).get_page(request.GET.get("page"))
    return render(request, "cotizacion_colectivos/notifications.html", {"page": page, **_environment_context()})


@never_cache
@require_http_methods(["POST"])
def notification_read(request, notification_id):
    if not has_internal_permission(request, "manage_notifications"):
        return permission_denied_response()
    item = get_object_or_404(NotificacionColectivos, pk=notification_id, user=request.user)
    item.read_at = timezone.now()
    item.save(update_fields=("read_at",))
    return redirect("cotizacion_colectivos:request_detail", public_id=item.request.public_id)


@never_cache
@require_http_methods(["POST"])
def notifications_read_all(request):
    if not has_internal_permission(request, "manage_notifications"):
        return permission_denied_response()
    NotificacionColectivos.objects.filter(user=request.user, read_at__isnull=True).update(read_at=timezone.now())
    return redirect("cotizacion_colectivos:notification_list")
