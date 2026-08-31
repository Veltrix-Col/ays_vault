from __future__ import annotations

import logging
import json
import hashlib
import time
import unicodedata
import uuid
import base64
import re
from urllib.parse import quote
from dataclasses import asdict
from datetime import datetime, timedelta

from django.core.exceptions import ValidationError
from django import forms as django_forms
from django.contrib import messages
from django.core import signing
from django.db import OperationalError, transaction
from django.db.models import Count, Prefetch, Q
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.core.paginator import Paginator
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from django.template.loader import render_to_string

from .forms import ClientSearchForm, CompanySearchForm, ExternalAccessPrepareForm, IndividualAccessPrepareForm, MultiPolicyRequestForm, NoveltyEditForm, OptionalAccessEmailForm, PersonCompletionForm, PersonSearchForm, RequestCreateForm, RequestEditForm, RequestFilterForm, RequestTransitionForm, SnapshotRegenerateForm
from .services import CompanySearchService, EntityDetailService, PersonSearchService, PolicyService, UnifiedClientSearchService
from .services.common import ColectivosServiceError, sign_record_id, unsign_record_context
from .services.catalogs import CatalogUnavailable, identification_choice_pairs
from .excel import build_current_policy_workbook
from .permissions import has_internal_permission, permission_denied_response
from .models import AccesoExternoSolicitudColectivo, AdjuntoSolicitudColectivo, CambioSolicitudColectivo, EventoSolicitudColectivo, NotificacionColectivos, RenovacionColectiva, RespuestaSolicitudColectivo, SolicitudColectivo, SolicitudColectivoPoliza
from .services.operational_settings import monthly_renewals_enabled, set_monthly_renewals_enabled
from .dto import ClientSearchResult, RequestPolicyOption
from .services.requests import create_or_reuse_request_from_policy, create_request_from_policies, create_request_from_policy, regenerate_request_snapshot, request_reference_hashes, request_snapshot, source_reference_hash, transition_request, update_draft_request
from .services.external import ActiveAccessExistsError, ExternalAccessError, GeneratedAccess, generate_access, resolve_token, revoke_access, send_invitation, send_optional_invitation, update_access_recipient
from .services.excel_roundtrip import (
    build_approved_consolidated,
    build_comparison,
    build_novelties_template,
    build_response_workbook,
)
from .services.review import finalize_review, record_reviews
from .services.preparations import load_builder_preparation, store_builder_preparation
from .services.invitation_templates import (
    generate_invitation_templates,
    preview_invitation_templates,
    sign_branch_invitation_context,
)
from pathlib import Path
from django.conf import settings
from django.http import FileResponse
from vault.security import audit
from vault.crypto import decrypt
from vault.notifications import mask_email
from .zoho import get_colectivos_environment
from .actors import get_internal_actor, public_internal_access_enabled
from .filenames import download_filename
from .modes import HUB_MODE, INDIVIDUAL_MODE, INVITATIONS_MODE, NOVELTIES_MODE, resolve_tool_mode
from .service_catalog import branch_workspaces
from .quotation_forms.catalog import VEHICLE_CLASS_CHOICES, VEHICLE_USE_CHOICES, get_branch_schema
from .quotation_forms.security import sign_receipt, unsign_receipt
from .services.individual_quotations import (
    affiliate_options,
    build_policy_context,
    accept_individual_quotation,
    resolve_accepted_person,
    update_quotation_responsible,
)
from .services.individual_access import generate_individual_access, individual_otp_required
from .services.task_responsibles import resolve_task_responsible_email, task_responsible_options
from .services.task_publisher import publish_task_outbox, read_published_task
from .services.person_contract import (
    ContactPublicationRejected, ContactPublicationUncertain, ContactPublishingDisabled,
    contact_missing_fields, get_contacts_publisher,
)
from .services.individual_entities import effective_candidate, promote_created_people, resolve_common_people_entities, resolve_mobility_entities, synchronize_risk_insured
from .services.risk_sandbox import create_sandbox_risk, RiskPublicationUncertain, RiskPublishingDisabled, RiskPublicationRejected
from .services.subrisk_sandbox import create_mobility_subrisk_sandbox, SubriskPublicationUncertain, SubriskPublishingDisabled, SubriskPublicationRejected
from .services.individual_attachment_publisher import IndividualAttachmentBlocked, IndividualAttachmentUncertain, publish_attachment, publish_pending_for_person, publish_pending_for_risk, reconcile_attachment
from .services.invitation_attachment_publisher import prepare_invitation_attachment
from .services.write_guards import configured_confirmation
from integrations.zoho.exceptions import ZohoError
from .models import AdjuntoCotizacionIndividual, AccesoCotizacionIndividual, ColectivosTaskOutbox, CotizacionIndividual, NotificacionCotizacionIndividual, RenovacionColectiva
from .quotation_forms.catalog import get_policy_branch_schema
from .services.renewals import sync_renewal_cycles, set_renewal_selection, process_renewal_cycles, renewal_dashboard_counts, upcoming_cycles, tracking_cycles, resend_renewal_access, next_month_period


logger = logging.getLogger("cotizacion_colectivos")

_MONTH_NAMES_ES = (
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
)


def _monthly_period_label(period):
    """Human presentation for YYYY-MM without relying on OS locale."""
    match = re.fullmatch(r"(\d{4})-(0[1-9]|1[0-2])", str(period or "").strip())
    if not match:
        return "Sin periodo"
    return f"{_MONTH_NAMES_ES[int(match.group(2)) - 1]} {match.group(1)}"


def _renewal_response_label(cycle):
    if cycle.status != RenovacionColectiva.Status.RESPONDED or not cycle.access_id:
        return ""
    response = RespuestaSolicitudColectivo.objects.filter(
        access_id=cycle.access_id,
        status=RespuestaSolicitudColectivo.Status.SUBMITTED,
    ).order_by("-version").first()
    if not response:
        return ""
    return "Sin novedades" if (response.safe_metadata or {}).get("response_type") == "NO_CHANGES" else "Con novedades"


def _attachment_document_status(attachment):
    metadata = attachment.safe_metadata if isinstance(attachment.safe_metadata, dict) else {}
    nested = metadata.get("zoho_attachment") if isinstance(metadata.get("zoho_attachment"), dict) else {}
    status = str(nested.get("status") or metadata.get("zoho_status") or "").strip().lower()
    if status == "uploaded":
        return "uploaded"
    if status in {"reconcile_required", "uncertain"}:
        return "reconcile_required"
    if status == "failed":
        return "failed"
    return "pending"


def _attachment_can_publish(attachment, remote_id):
    return _attachment_document_status(attachment) not in {"uploaded", "reconcile_required"} and bool(str(remote_id or "").strip())


def _transition_permission(target):
    if target == SolicitudColectivo.Status.APPROVED:
        return "approve_requests"
    if target == SolicitudColectivo.Status.CLOSED:
        return "close_requests"
    if target == SolicitudColectivo.Status.CANCELLED:
        return "cancel_requests"
    return "create_requests"


RESPONSE_FIELD_LABELS = {
    "nombres": "Nombres",
    "apellidos": "Apellidos",
    "tipo_id": "Tipo de identificación",
    "documento": "Número de identificación",
    "nombre": "Nombre completo",
    "rol": "Rol",
    "plan": "Plan",
    "parentesco": "Parentesco",
    "fecha_nacimiento": "Fecha de nacimiento",
    "fecha_efectiva": "Fecha efectiva",
    "fecha_ingreso": "Fecha de ingreso",
    "fecha_retiro": "Fecha de retiro",
    "motivo": "Motivo",
    "observaciones": "Observaciones",
    "ciudad": "Ciudad",
    "direccion": "Dirección",
    "tipo_uso": "Tipo de uso",
    "anio_construccion": "Año de construcción",
    "descripcion": "Descripción",
    "valor_asegurado": "Valor asegurado",
    "vehiculo": "Vehículo",
    "placa": "Placa",
    "marca": "Marca",
    "modelo": "Modelo",
    "estado": "Estado",
}


def _environment_context(request=None, mode=None):
    context = {"zoho_environment": get_colectivos_environment()}
    if request is not None:
        context["colectivos_mode"] = resolve_tool_mode(request, mode)
    return context


def _error_status(exc):
    if exc.code == "permission":
        return 403
    if exc.code in {"invalid_record", "not_found"}:
        return 404
    return 503


@never_cache
@require_http_methods(["GET"])
def index(request, mode=None):
    tool_mode = resolve_tool_mode(request, mode or HUB_MODE)
    context = {
        "form": ClientSearchForm(),
        "colectivos_mode": tool_mode,
        **_environment_context(),
    }
    if tool_mode.code == "novelties":
        renewal_sync_error = ""
        try:
            cycles = sync_renewal_cycles()
        except Exception as exc:
            logger.exception("colectivos_renewals_read_failed error=%s", type(exc).__name__)
            renewal_sync_error = "No fue posible actualizar las próximas renovaciones desde Zoho."
            cycles = tuple(RenovacionColectiva.objects.filter(monthly_period=next_month_period(timezone.localdate()), line_of_business="Colectivo").order_by("scheduled_for", "pk"))
        tab = "upcoming"
        renewal_rows = upcoming_cycles(query=request.GET.get("q"))
        renewal_rows = tuple(renewal_rows)
        for renewal_row in renewal_rows:
            renewal_row.monthly_period_label = _monthly_period_label(renewal_row.monthly_period)
            renewal_row.response_type_label = _renewal_response_label(renewal_row)
            if renewal_row.response_type_label:
                status_display = renewal_row.get_status_display()
                renewal_row.get_status_display = lambda status_display=status_display, label=renewal_row.response_type_label: f"{status_display} · {label}"
        context.update({
            "renewal_cycles": renewal_rows,
            "renewal_tab": tab,
            "renewal_query": request.GET.get("q", ""),
            "renewal_filter": request.GET.get("filter", "all"),
            "renewal_status": request.GET.get("status", "all"),
            "renewal_target_period": next_month_period(timezone.localdate()),
            "renewal_target_period_label": _monthly_period_label(next_month_period(timezone.localdate())),
            "renewal_dashboard": renewal_dashboard_counts(),
            "renewal_window_days": getattr(settings, "COLECTIVOS_RENEWAL_WINDOW_DAYS", 30),
            "renewal_sync_error": renewal_sync_error,
            "monthly_renewals_enabled": monthly_renewals_enabled(),
            "can_manage_notifications": has_internal_permission(request, "manage_notifications"),
            "can_edit_renewal_schedule": has_internal_permission(request, "view_requests"),
            "can_manage_renewal_automation": has_internal_permission(request, "view_requests"),
        })
    return render(request, "cotizacion_colectivos/index.html", context)


@never_cache
@require_http_methods(["POST"])
def monthly_renewals_toggle(request):
    if not has_internal_permission(request, "view_requests"):
        return permission_denied_response()
    enabled = str(request.POST.get("enabled") or "").lower() in {"1", "true", "on", "yes"}
    set_monthly_renewals_enabled(enabled=enabled, actor=get_internal_actor(request, create=True))
    messages.success(request, "Automatización mensual activada." if enabled else "Automatización mensual desactivada.")
    next_url = request.POST.get("next") or ""
    allowed_next = {
        reverse("cotizacion_colectivos:novelties_index"),
        reverse("cotizacion_colectivos:renewal_tracking"),
    }
    return redirect(next_url if next_url in allowed_next else "cotizacion_colectivos:novelties_index")


@never_cache
@require_http_methods(["POST"])
def renewal_toggle(request, cycle_id):
    if not has_internal_permission(request, "create_requests"):
        return permission_denied_response()
    try:
        cycle = RenovacionColectiva.objects.get(pk=cycle_id)
        selected = str(request.POST.get("selected") or "").lower() in {"1", "true", "on", "yes"}
        set_renewal_selection(cycle_id=cycle.pk, selected=selected, recipient=request.POST.get("recipient"))
    except RenovacionColectiva.DoesNotExist:
        raise Http404("Programación no encontrada")
    return redirect("cotizacion_colectivos:novelties_index")


@never_cache
@require_http_methods(["POST"])
def renewal_schedule_update(request, cycle_id):
    """Update only the scheduled date of an unsent programmed cycle."""
    if not has_internal_permission(request, "view_requests"):
        return permission_denied_response()
    date_field = django_forms.DateField(input_formats=["%Y-%m-%d"], required=True)
    try:
        scheduled_for = date_field.clean(request.POST.get("scheduled_for"))
    except ValidationError:
        messages.error(request, "Selecciona una fecha válida.")
        return redirect("cotizacion_colectivos:novelties_index")
    with transaction.atomic():
        try:
            cycle = RenovacionColectiva.objects.select_for_update().get(pk=cycle_id)
        except RenovacionColectiva.DoesNotExist:
            raise Http404("Programación no encontrada")
        if (
            cycle.status != RenovacionColectiva.Status.PROGRAMMED
            or cycle.sent_at is not None
            or cycle.responded_at is not None
        ):
            messages.error(request, "Este envío ya no puede reprogramarse.")
            return redirect("cotizacion_colectivos:novelties_index")
        previous_date = cycle.scheduled_for
        cycle.scheduled_for = scheduled_for
        cycle.save(update_fields=("scheduled_for", "updated_at"))
    audit(
        request,
        "UPDATE",
        reason="renewal_schedule_updated",
        metadata={"cycle_id": cycle_id, "previous_date": str(previous_date), "scheduled_for": str(scheduled_for)},
    )
    messages.success(request, "Fecha de envío actualizada.")
    return redirect("cotizacion_colectivos:novelties_index")


@never_cache
@require_http_methods(["GET"])
def renewal_tracking(request):
    if not has_internal_permission(request, "view_requests"):
        return permission_denied_response()
    rows = tracking_cycles(query=request.GET.get("q"), status=request.GET.get("status", "all"))
    rows = tuple(rows)
    for renewal_row in rows:
        renewal_row.monthly_period_label = _monthly_period_label(renewal_row.monthly_period)
        renewal_row.response_type_label = _renewal_response_label(renewal_row)
        if renewal_row.response_type_label:
            status_display = renewal_row.get_status_display()
            renewal_row.get_status_display = lambda status_display=status_display, label=renewal_row.response_type_label: f"{status_display} · {label}"
    return render(request, "cotizacion_colectivos/renewal_tracking.html", {
        "renewal_cycles": rows,
        "renewal_query": request.GET.get("q", ""),
        "renewal_status": request.GET.get("status", "all"),
        "renewal_tab": "tracking",
        "monthly_renewals_enabled": monthly_renewals_enabled(),
        "can_manage_notifications": has_internal_permission(request, "manage_notifications"),
        "can_manage_renewal_automation": has_internal_permission(request, "view_requests"),
        "colectivos_mode": resolve_tool_mode(request, NOVELTIES_MODE),
        **_environment_context(),
    })


@never_cache
@require_http_methods(["POST"])
def renewal_resend(request, cycle_id):
    if not has_internal_permission(request, "create_requests"):
        return permission_denied_response()
    try:
        resend_renewal_access(cycle_id=cycle_id, recipient=request.POST.get("recipient", ""))
        messages.success(request, "Se generó y envió un nuevo acceso de Novedades.")
    except (RenovacionColectiva.DoesNotExist, ColectivosServiceError) as exc:
        messages.error(request, getattr(exc, "message", "No fue posible reenviar el acceso."))
    return redirect("cotizacion_colectivos:renewal_tracking")


@never_cache
@require_http_methods(["GET"])
def individual_quotation_index(request):
    resolve_tool_mode(request, INDIVIDUAL_MODE)
    return redirect("cotizacion_colectivos:individual_client_search")


@never_cache
@require_http_methods(["GET", "POST"])
def individual_quotation_form(request, branch_slug):
    # Compatibility route: an individual quotation must now start from a
    # client, policy and confirmed affiliate. It never accepts a loose ramo.
    resolve_tool_mode(request, INDIVIDUAL_MODE)
    if request.method == "POST":
        raise Http404("La cotización individual requiere una póliza.")
    return redirect("cotizacion_colectivos:individual_client_search")


@never_cache
@require_http_methods(["GET"])
def individual_quotation_confirmation(request, token):
    resolve_tool_mode(request, INDIVIDUAL_MODE)
    try:
        public_id = unsign_receipt(token)
        quotation = CotizacionIndividual.objects.only(
            "public_id", "branch_slug", "branch_code", "item_count", "attachment_count", "submitted_at"
        ).get(public_id=public_id)
    except (signing.BadSignature, CotizacionIndividual.DoesNotExist, ValueError) as exc:
        raise Http404("Confirmación no encontrada") from exc
    return render(request, "cotizacion_colectivos/individual/confirmation.html", {
        "quotation": quotation,
        "schema": get_branch_schema(quotation.branch_slug),
        "colectivos_mode": resolve_tool_mode(request, INDIVIDUAL_MODE),
        **_environment_context(),
    })


@never_cache
@require_http_methods(["GET", "POST"])
def client_search(request, mode=None):
    environment = get_colectivos_environment()
    tool_mode = resolve_tool_mode(request, mode or HUB_MODE)
    form = ClientSearchForm(request.POST or None)
    results, error, status = None, "", 200
    if request.method == "POST" and form.is_valid():
        started = time.monotonic()
        correlation = uuid.uuid4().hex
        error_category = "none"
        timings = {
            "facade_ms": 0, "organization_ms": 0, "metadata_ms": 0,
            "search_ms": 0, "coql_ms": 0, "mapper_ms": 0,
            "dedup_ms": 0, "dto_ms": 0,
        }
        try:
            service = UnifiedClientSearchService()
            results = service.search(form.cleaned_data["query"])
            timings.update(service.timings)
        except ColectivosServiceError as exc:
            error_category = exc.code
            error, status = exc.message, _error_status(exc)
        except Exception:
            error_category = "unknown"
            error = f"{environment['label']} no está disponible temporalmente. Intente nuevamente más tarde."
            status = 503
        logger.info(
            "colectivos_search application=cotizacion_colectivos entity=client operation=search "
            "facade_ms=%d organization_ms=%d search_ms=%d duration_ms=%d results=%d "
            "error=%s actor=%s profile=%s correlation=%s",
            timings["facade_ms"], timings["organization_ms"], timings["search_ms"],
            round((time.monotonic() - started) * 1000), len(results or ()),
            error_category, "technical" if public_internal_access_enabled() else "authenticated",
            environment["profile"], correlation,
        )
    return render(request, "cotizacion_colectivos/search.html", {
        "form": form,
        "results": results,
        "error": error,
        "entity_kind": "client",
        "colectivos_mode": tool_mode,
        "zoho_environment": environment,
    }, status=status)


@never_cache
@require_http_methods(["GET"])
def client_detail(request, entity_kind, token, mode=None):
    resolve_tool_mode(request, mode or HUB_MODE)
    if entity_kind == "company":
        return _detail(request, token, method="company", entity_kind="company")
    if entity_kind == "person":
        return _detail(request, token, method="person", entity_kind="person")
    raise Http404("Cliente no encontrado")


def _search(request, *, form_class, service_class, entity_kind):
    environment = get_colectivos_environment()
    tool_mode = resolve_tool_mode(request)
    form = form_class(request.POST or None)
    results, error, status = None, "", 200
    if request.method == "POST" and form.is_valid():
        started = time.monotonic()
        correlation = uuid.uuid4().hex
        error_category = "none"
        timings = {
            "facade_ms": 0, "organization_ms": 0, "metadata_ms": 0,
            "search_ms": 0, "coql_ms": 0, "mapper_ms": 0,
            "dedup_ms": 0, "dto_ms": 0,
        }
        try:
            query = form.cleaned_data["query"]
            service = service_class()
            raw_results = service.search(query)
            if entity_kind == "company":
                results = tuple(ClientSearchResult(
                    item.detail_token, "company", item.display_name, "Empresa", "NIT",
                    item.masked_document, item.state, item.document,
                ) for item in raw_results)
            else:
                results = tuple(ClientSearchResult(
                    item.detail_token, "person", item.full_name, "Persona", "Documento",
                    item.masked_document, item.state, item.document,
                ) for item in raw_results)
            timings.update(service.timings)
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
            "colectivos_search application=cotizacion_colectivos entity=%s operation=search "
            "facade_ms=%d organization_ms=%d metadata_ms=%d search_ms=%d coql_ms=%d "
            "mapper_ms=%d dedup_ms=%d dto_ms=%d duration_ms=%d results=%d error=%s "
            "actor=%s profile=%s correlation=%s",
            entity_kind,
            timings["facade_ms"], timings["organization_ms"], timings["metadata_ms"],
            timings["search_ms"], timings["coql_ms"], timings["mapper_ms"],
            timings["dedup_ms"], timings["dto_ms"],
            round((time.monotonic() - started) * 1000),
            len(results or ()),
            error_category,
            "technical" if public_internal_access_enabled() else "authenticated",
            environment["profile"],
            correlation,
        )
    context = {
        "form": form, "results": results, "error": error, "entity_kind": entity_kind,
        "zoho_environment": environment, "colectivos_mode": tool_mode,
    }
    template_started = time.monotonic()
    html = render_to_string("cotizacion_colectivos/search.html", context, request=request)
    template_ms = round((time.monotonic() - template_started) * 1000)
    render_started = time.monotonic()
    response = HttpResponse(html, status=status)
    render_ms = round((time.monotonic() - render_started) * 1000)
    logger.info(
        "colectivos_render application=cotizacion_colectivos entity=%s operation=render "
        "template_ms=%d render_ms=%d profile=%s",
        entity_kind, template_ms, render_ms, environment["profile"],
    )
    return response


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
    tool_mode = resolve_tool_mode(request)
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
            "colectivos_mode": tool_mode,
        }, status=_error_status(exc))
    except Exception:
        _log_detail(request, entity_kind, environment, started, "unknown", correlation, 0)
        return render(
            request,
            "cotizacion_colectivos/detail_error.html",
            {"message": "No fue posible consultar la información relacionada. Intente nuevamente más tarde.", "zoho_environment": environment, "colectivos_mode": tool_mode},
            status=503,
        )
    _log_detail(request, entity_kind, environment, started, error_category, correlation, 1)
    return _render_client_detail(request, detail=detail, token=token, entity_kind=entity_kind, environment=environment)


def _client_requests(request, *, token, entity_kind, environment):
    related_requests = SolicitudColectivo.objects.none()
    if has_internal_permission(request, "view_requests"):
        try:
            source_hash = source_reference_hash(token=token, source_kind=entity_kind)
        except ColectivosServiceError:
            pass
        else:
            related_requests = SolicitudColectivo.objects.filter(
                source_kind=entity_kind,
                source_reference_hash=source_hash,
                zoho_profile=environment["profile"],
            ).prefetch_related("policies").annotate(
                active_access_count=Count(
                    "external_accesses",
                    filter=Q(
                        external_accesses__status__in=(
                            AccesoExternoSolicitudColectivo.Status.ACTIVE,
                            AccesoExternoSolicitudColectivo.Status.VERIFIED,
                        ),
                        external_accesses__expires_at__gt=timezone.now(),
                    ),
                    distinct=True,
                ),
                response_count=Count("responses", distinct=True),
            ).order_by("-updated_at", "-created_at", "-pk")[:10]
    return related_requests


def _render_client_detail(request, *, detail, token, entity_kind, environment=None, **extra):
    environment = environment or get_colectivos_environment()
    mode = resolve_tool_mode(request)
    return render(request, "cotizacion_colectivos/detail.html", {
        "detail": detail, "entity_kind": entity_kind, "entity_token": token,
        "related_requests": _client_requests(
            request, token=token, entity_kind=entity_kind, environment=environment,
        ),
        "zoho_environment": environment,
        "colectivos_mode": mode,
        "branch_workspaces": branch_workspaces(
            detail.branches,
            service_code=mode.code if mode.code != HUB_MODE else None,
        ),
        **extra,
    })


def _normalized_choice(value):
    return "".join(
        character for character in unicodedata.normalize("NFKD", str(value or "").strip().casefold())
        if not unicodedata.combining(character)
    )


def _short_invitation_mailto(*, branch_name, client_name, insurer=None, recipient=""):
    audience = f" para {insurer}" if insurer else ""
    subject = f"Solicitud de cotización · {branch_name} · {client_name}"
    body = (
        "Buenos días,\n\n"
        f"Compartimos formatos{audience} para solicitud de cotización del ramo "
        f"{branch_name} correspondiente a {client_name}.\n\n"
        "Quedamos atentos.\n\nA&S Seguros"
    )
    return "mailto:" + quote(recipient or "", safe="@") + "?subject=" + quote(
        subject, safe="",
    ) + "&body=" + quote(body, safe="")


def _invitation_page_context(detail, previews, metadata, *, active_policies=()):
    client_name = detail.holder or "Cliente"
    actions = []
    for preview in previews:
        if preview.status not in {"ready", "ready_manual"}:
            continue
        action = next(
            (item for item in actions if item["insurer_code"] == preview.template.insurer_code),
            None,
        )
        if action is None:
            action = {
                "insurer_code": preview.template.insurer_code,
                "insurer_name": preview.template.insurer_name,
                "recipient": preview.template.recipient_email,
                "previews": [], "missing_required": set(), "output_files": 0,
                # Empty means the complete insurer output (XLSX or ZIP), not
                # one arbitrarily selected template.
                "template_code": "",
                "mailto_url": _short_invitation_mailto(
                    branch_name=detail.branch_name, client_name=client_name,
                    insurer=preview.template.insurer_name,
                    recipient=preview.template.recipient_email,
                ),
            }
            actions.append(action)
        action["previews"].append(preview)
        action["missing_required"].update(preview.missing_required)
        action["output_files"] += preview.output_files
    for action in actions:
        action["previews"] = tuple(action["previews"])
        action["missing_required"] = tuple(sorted(action["missing_required"]))
    groups = tuple(metadata.get("operational_groups") or ())
    policy_items = []
    operational_rows = []
    if active_policies:
        groups_by_token = {item.get("policy_token"): item for item in groups}
        missing = set(metadata.get("missing_workspaces") or ())
        for index, policy in enumerate(active_policies, start=1):
            reference = policy.full_reference or policy.masked_reference
            group = groups_by_token.get(policy.detail_token)
            key = f"policy-{index}"
            rows = tuple(group.get("rows") or ()) if group else ()
            policy_items.append({
                "key": key, "policy": policy, "reference": reference,
                "workspace_available": group is not None,
                "record_count": len(rows) if group is not None else None,
                "workspace_missing": reference in missing or group is None,
            })
            operational_rows.extend({
                **row, "policy_key": key, "policy_reference": reference,
            } for row in rows)
    else:
        for index, group in enumerate(groups, start=1):
            key = f"policy-{index}"
            operational_rows.extend({
                **row, "policy_key": key,
                "policy_reference": group.get("policy_reference", ""),
            } for row in group.get("rows") or ())
    complete = bool(metadata.get("complete", True))
    return {
        "actions": tuple(actions),
        "operational_groups": groups,
        "operational_rows": tuple(operational_rows),
        "branch_policy_items": tuple(policy_items),
        "missing_workspaces": tuple(metadata.get("missing_workspaces") or ()),
        "workspace_complete": complete,
        "has_generable": bool(actions),
        "mailto_url": _short_invitation_mailto(
            branch_name=detail.branch_name, client_name=client_name,
        ),
    }


@never_cache
@require_http_methods(["GET"])
def branch_detail(request, entity_kind, token, branch_code):
    if entity_kind not in {"company", "person"}:
        raise Http404("Cliente no encontrado")
    tool_mode = resolve_tool_mode(request, INVITATIONS_MODE)
    try:
        detail = getattr(EntityDetailService(), entity_kind)(token)
    except ColectivosServiceError as exc:
        if exc.code in {"invalid_record", "not_found"}:
            raise Http404("Cliente no encontrado") from exc
        return render(request, "cotizacion_colectivos/detail_error.html", {
            "message": exc.message, "colectivos_mode": tool_mode,
            **_environment_context(),
        }, status=_error_status(exc))
    branch = next((item for item in detail.branches if item.code == branch_code), None)
    if branch is None:
        raise Http404("Ramo no encontrado")
    active_policies = tuple(
        policy for policy in branch.policies
        if _normalized_choice(policy.state) in {"vigente", "activa", "activo"}
    )
    client_name = detail.display_name if entity_kind == "company" else detail.full_name
    consolidated_token = ""
    invitation_context = {
        "actions": (), "operational_rows": (), "branch_policy_items": tuple({
            "key": f"policy-{index}", "policy": policy,
            "reference": policy.full_reference or policy.masked_reference,
            "workspace_available": False, "record_count": None,
            "workspace_missing": True,
        } for index, policy in enumerate(active_policies, start=1)),
        "missing_workspaces": (), "workspace_complete": False,
        "has_generable": False, "mailto_url": "",
    }
    if active_policies:
        consolidated_token = sign_branch_invitation_context(
            policy_tokens=(policy.detail_token for policy in active_policies),
            branch_code=branch.code, holder=client_name,
            policy_references=(
                policy.full_reference or policy.masked_reference
                for policy in active_policies
            ),
        )
        try:
            invitation_detail, previews, metadata = preview_invitation_templates(
                consolidated_token, consolidated=True,
            )
            invitation_context = {
                **_invitation_page_context(
                    invitation_detail, previews, metadata,
                    active_policies=active_policies,
                ),
                "previews": previews,
            }
        except ColectivosServiceError as exc:
            if exc.code not in {"workspace_unavailable"}:
                raise
            invitation_context["missing_workspaces"] = tuple(
                policy.full_reference or policy.masked_reference
                for policy in active_policies
            )
    return render(request, "cotizacion_colectivos/branch_detail.html", {
        "detail": detail, "entity_kind": entity_kind, "entity_token": token,
        "branch": branch, "active_policies": active_policies,
        "consolidated_token": consolidated_token, "colectivos_mode": tool_mode,
        **invitation_context,
        **_environment_context(),
    })


def _builder_policies(detail):
    available, unavailable = [], []
    seen = set()
    for branch in detail.branches:
        if not branch.code:
            continue
        for policy in branch.policies:
            try:
                policy_id = unsign_record_context(policy.detail_token, "policy")["id"]
            except ColectivosServiceError:
                continue
            if policy_id in seen:
                continue
            seen.add(policy_id)
            option = RequestPolicyOption(
                detail_token=policy.detail_token,
                masked_reference=policy.masked_reference,
                branch_code=branch.code,
                branch_name=branch.name,
                insurer=policy.insurer,
                state=policy.state,
                start_date=policy.start_date,
                end_date=policy.end_date,
                related_count=branch.insured_count,
                warnings=branch.warnings,
                renewable=policy.renewable,
            )
            state = _normalized_choice(policy.state)
            if policy.layout_category != "collective":
                unavailable.append((option, "No corresponde a un diseño colectivo confirmado."))
            elif state in {"vigente", "activa", "activo"} or (
                state in {"vencida", "vencido"}
                and _normalized_choice(policy.renewable) in {"si", "true", "1", "renovable"}
            ):
                available.append(option)
            else:
                reason = {
                    "cancelada": "Cancelada.", "cancelado": "Cancelada.",
                    "anulada": "Anulada.", "anulado": "Anulada.",
                    "vencida": "Vencida sin regla de renovación confirmada.",
                    "vencido": "Vencida sin regla de renovación confirmada.",
                }.get(state, "Estado no confirmado como operable.")
                unavailable.append((option, reason))
    return tuple(available), tuple(unavailable)


@never_cache
@require_http_methods(["GET", "POST"])
def request_builder(request, source_kind, token):
    if source_kind not in {"company", "person"}:
        raise Http404("Origen no válido")
    if not has_internal_permission(request, "create_requests") or not has_internal_permission(request, "generate_external_access"):
        return permission_denied_response()
    environment = get_colectivos_environment()
    backend = str(getattr(settings, "ZOHO_BACKEND", "sdk")).strip().lower()
    prepared = load_builder_preparation(
        token=token, profile=environment["profile"], backend=backend,
        source_kind=source_kind,
    ) if request.method == "POST" else None
    detail = None
    unavailable_policies = ()
    pending = ()
    if prepared is not None:
        policies = tuple(
            RequestPolicyOption(**{**item, "warnings": tuple(item.get("warnings", ()))})
            for item in prepared["policies"]
        )
        client_label = str(prepared.get("client_label") or "Cliente sin etiqueta")
    else:
        try:
            detail = getattr(EntityDetailService(), source_kind)(token)
        except ColectivosServiceError as exc:
            if exc.code in {"invalid_record", "not_found"}:
                raise Http404("Registro no encontrado") from exc
            return render(request, "cotizacion_colectivos/detail_error.html", {
                "message": exc.message, "zoho_environment": environment,
            }, status=_error_status(exc))
        policies, unavailable_policies = _builder_policies(detail)
        pending = tuple(branch for branch in detail.branches if not branch.code)
        client_label = detail.display_name if source_kind == "company" else detail.full_name
        store_builder_preparation(
            token=token, profile=environment["profile"], backend=backend,
            source_kind=source_kind, client_label=client_label,
            policies=tuple(asdict(policy) for policy in policies),
        )
    form = MultiPolicyRequestForm(
        request.POST or None,
        policies=policies,
        initial={"deadline": timezone.localdate() + timedelta(days=settings.COLECTIVOS_EXTERNAL_LINK_DAYS), "is_test": True},
    )
    if request.method == "POST" and form.is_valid():
        actor = get_internal_actor(request, create=True)
        try:
            item = create_request_from_policies(
                selections=form.cleaned_data["selections"],
                source_kind=source_kind,
                actor=actor,
                assigned_to=actor,
                request_type=form.cleaned_data["request_type"],
                deadline=form.cleaned_data["deadline"],
                client_label=client_label,
                internal_notes=form.cleaned_data["internal_notes"],
                is_test=form.cleaned_data["is_test"],
            )
            regenerate = item.external_accesses.filter(
                status__in=(
                    AccesoExternoSolicitudColectivo.Status.ACTIVE,
                    AccesoExternoSolicitudColectivo.Status.VERIFIED,
                ),
                expires_at__gt=timezone.now(),
            ).exists()
            created = not item.external_accesses.exists()
            generated = generate_access(request=item, actor=actor, regenerate=regenerate)
        except (ColectivosServiceError, ExternalAccessError, ValidationError) as exc:
            form.add_error(None, getattr(exc, "message", str(exc)))
        else:
            audit(request, "CREATE" if created else "UPDATE", reason="Expediente multipóliza Colectivos creado.", metadata={"request_id": item.public_id, "policies": item.policies.count(), "records": item.record_count})
            if detail is not None:
                return _render_client_detail(request, detail=detail, token=token, entity_kind=source_kind, **{
                    "fresh_request": item,
                    "generated_url": generated.url,
                    "generated_token": generated.token,
                    "expires_at": generated.access.expires_at,
                    "success_message": "Solicitud creada correctamente.",
                })
            return render(request, "cotizacion_colectivos/generated_access.html", {
                "item": item,
                "generated_url": generated.url,
                "generated_token": generated.token,
                "expires_at": generated.access.expires_at,
                "success_message": "Solicitud creada correctamente.",
                "email_form": OptionalAccessEmailForm(), "created": created,
                "regenerated": regenerate, "zoho_environment": environment,
            })
    builder_options = [
        {**option, "selector": form[option["policy_field"]], "adjustment_selector": form[option["adjustment_field"]]}
        for option in form.policy_options
    ]
    return render(request, "cotizacion_colectivos/request_builder.html", {
        "form": form,
        "detail": detail,
        "entity_kind": source_kind,
        "policies": builder_options,
        "unavailable_policies": unavailable_policies,
        "pending_branches": pending,
        "zoho_environment": environment,
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


def _policy_access_panel(policy_requests):
    if not policy_requests:
        return {"status": "No generado", "item": None, "access": None}
    reusable = {
        SolicitudColectivo.Status.DRAFT,
        SolicitudColectivo.Status.READY,
        SolicitudColectivo.Status.SENT,
        SolicitudColectivo.Status.OPENED,
        SolicitudColectivo.Status.CORRECTION,
    }
    item = next(
        (candidate for candidate in policy_requests if candidate.status in reusable),
        policy_requests[0],
    )
    access = max(item.external_accesses.all(), key=lambda value: value.created_at, default=None)
    responded = {
        SolicitudColectivo.Status.ANSWERED,
        SolicitudColectivo.Status.REVIEW,
        SolicitudColectivo.Status.APPROVED,
        SolicitudColectivo.Status.PENDING_ZOHO,
        SolicitudColectivo.Status.LOADED_ZOHO,
        SolicitudColectivo.Status.CLOSED,
    }
    if item.status in responded or (access and access.status == access.Status.USED):
        status = "Respondido"
    elif access is None:
        status = "No generado"
    elif access.status in {access.Status.ACTIVE, access.Status.VERIFIED}:
        status = "Vigente" if access.expires_at > timezone.now() else "Vencido"
    elif access.status == access.Status.EXPIRED:
        status = "Vencido"
    elif access.status == access.Status.REVOKED:
        status = "Revocado"
    else:
        status = "No generado"
    return {"status": status, "item": item, "access": access}


EVENT_LABELS = {
    "CREATED": "Solicitud creada",
    "EXTERNAL_ACCESS_CREATED": "Enlace generado",
    "EXTERNAL_ACCESS_REGENERATED": "Nuevo enlace generado",
    "EXTERNAL_ACCESS_REVOKED": "Enlace revocado",
    "EXTERNAL_INVITATION_SENT": "Invitación enviada",
    "EXTERNAL_LINK_OPENED": "Cliente abrió el enlace",
    "EXTERNAL_DRAFT_SAVED": "Cliente guardó cambios",
    "EXTERNAL_RESPONSE_SUBMITTED": "Cliente respondió",
    "REVIEW_UPDATED": "Analista revisó la respuesta",
    "CORRECTION_REQUESTED": "Corrección solicitada",
    "STATUS_CHANGED": "Estado actualizado",
    "RESPONSE_APPROVED": "Respuesta aprobada",
}


def _workspace_activity(policy_requests, preparation_metadata=None):
    activity = []
    for event in (preparation_metadata or {}).get("safe_timeline", ()):
        try:
            created_at = datetime.fromisoformat(str(event.get("at") or ""))
        except (TypeError, ValueError):
            continue
        activity.append({
            "label": (
                "Información actualizada desde Zoho"
                if event.get("type") == "ZOHO_REFRESH"
                else "Workspace preparado desde Zoho"
            ),
            "created_at": created_at,
            "status": "Snapshot local actualizado",
            "request": "",
        })
    for item in policy_requests:
        for event in item.events.all():
            activity.append({
                "label": EVENT_LABELS.get(event.event_type, "Actividad registrada"),
                "created_at": event.created_at,
                "status": event.new_status or item.get_status_display(),
                "request": item.public_id,
            })
    return tuple(sorted(activity, key=lambda value: value["created_at"], reverse=True)[:40])


def _policy_workspace_context(request, *, token, service, detail, members=(), extra_context=None):
    """Build the policy workspace without exposing SDK objects or raw CRM data."""
    context_started = time.monotonic()
    token_context = unsign_record_context(token, "policy")
    source_kind = token_context.get("source_kind") or "company"
    can_view_requests = has_internal_permission(request, "view_requests")
    can_create_requests = has_internal_permission(request, "create_requests")
    builder_token = ""
    policy_requests = []
    active_update = None
    active_renewal = None
    if can_view_requests or can_create_requests:
        try:
            policy_hash, source_hash = request_reference_hashes(
                token=token, source_kind=source_kind, holder=detail.holder,
            )
        except ColectivosServiceError as exc:
            raise Http404("Póliza no encontrada") from exc
        related_queryset = SolicitudColectivo.objects.filter(
            Q(policy_reference_hash=policy_hash) | Q(policies__policy_reference_hash=policy_hash),
            source_reference_hash=source_hash,
            zoho_profile=service.profile,
        ).distinct()
        related_items = list(
            related_queryset.select_related("assigned_to").prefetch_related(
                "policies", "external_accesses", "events", "responses",
            ).order_by("-updated_at", "-created_at", "-pk")[:50]
        )
        exact_policy_items = []
        for item in related_items:
            item_policy_hashes = {
                policy.policy_reference_hash for policy in item.policies.all()
            }
            if not item_policy_hashes and item.policy_reference_hash:
                item_policy_hashes = {item.policy_reference_hash}
            if item_policy_hashes == {policy_hash}:
                exact_policy_items.append(item)
        if can_view_requests:
            policy_requests = exact_policy_items[:20]
            for item in policy_requests:
                access = max(
                    item.external_accesses.all(),
                    key=lambda value: value.created_at,
                    default=None,
                )
                item.access_display = _access_status_display(access)
        if can_create_requests:
            terminal = {
                SolicitudColectivo.Status.CLOSED,
                SolicitudColectivo.Status.CANCELLED,
                SolicitudColectivo.Status.EXPIRED,
            }
            active_items = [
                item for item in exact_policy_items if item.status not in terminal
            ]
            active_update = next((
                item for item in active_items
                if item.request_type == SolicitudColectivo.RequestType.UPDATE
            ), None)
            active_renewal = next((
                item for item in active_items
                if item.request_type == SolicitudColectivo.RequestType.RENEWAL
            ), None)
    if token_context.get("source_id") and source_kind in {"company", "person"}:
        builder_token = sign_record_id(token_context["source_id"], source_kind)
    mode = resolve_tool_mode(request)
    individual_schema = None
    individual_affiliates = ()
    task_responsibles = ()
    if mode.code == INDIVIDUAL_MODE:
        try:
            individual_schema = get_policy_branch_schema(detail.branch_code, detail.branch_name)
        except Http404:
            pass
        else:
            individual_affiliates = affiliate_options(members)
            try:
                task_responsibles = task_responsible_options(collective_only=True)
            except (ValidationError, ColectivosServiceError):
                task_responsibles = ()
    context = {
        "detail": detail,
        # Signed record tokens are short-lived capabilities and must come from
        # the current route, never from an encrypted snapshot restored later.
        "policy_token": token,
        **_environment_context(),
        "can_export": has_internal_permission(request, "export_excel"),
        "can_create": can_create_requests,
        "builder_token": builder_token,
        "source_detail_token": builder_token,
        "source_display_name": detail.source_name or "Ficha del cliente",
        "source_kind": source_kind,
        "can_view_requests": can_view_requests,
        "can_view_responses": has_internal_permission(request, "view_responses"),
        "policy_requests": policy_requests,
        "active_update": active_update,
        "active_renewal": active_renewal,
        "access_panel": _policy_access_panel(policy_requests),
        "can_generate_access": can_create_requests and has_internal_permission(
            request, "generate_external_access",
        ),
        "can_revoke_access": has_internal_permission(request, "revoke_external_access"),
        "workspace_activity": _workspace_activity(
            policy_requests, getattr(service, "preparation_metadata", {}),
        ),
        "workspace_response_count": sum(len(tuple(item.responses.all())) for item in policy_requests),
        "preparation_status": getattr(service, "preparation_status", "disabled"),
        "preparation_metadata": getattr(service, "preparation_metadata", {}),
        "workspace_members": members,
        "functional_groups": getattr(service, "preparation_metadata", {}).get(
            "functional_groups", (),
        ),
        "colectivos_mode": mode,
        "individual_schema": individual_schema,
        "individual_affiliates": individual_affiliates,
        "task_responsibles": task_responsibles,
    }
    context.update(extra_context or {})
    if hasattr(service, "timings"):
        service.timings["workspace_context_ms"] = round(
            (time.monotonic() - context_started) * 1000
        )
    return context


def _render_policy_workspace(
    request, *, token, service, detail, started, members=None, extra_context=None,
):
    if members is None:
        members = getattr(service, "last_members", None)
        if members is None:
            token_context = unsign_record_context(token, "policy")
            _detail, members = service.group(
                token, source_kind=token_context.get("source_kind"),
            )
    context = _policy_workspace_context(
        request, token=token, service=service, detail=detail, members=members,
        extra_context=extra_context,
    )
    template_started = time.monotonic()
    html = render_to_string("cotizacion_colectivos/policy_detail.html", context, request=request)
    template_ms = round((time.monotonic() - template_started) * 1000)
    response_started = time.monotonic()
    response = HttpResponse(html)
    render_ms = round((time.monotonic() - response_started) * 1000)
    logger.info(
        "colectivos_workspace application=cotizacion_colectivos operation=policy_workspace "
        "profile=%s cache=%s remote_queries=%d snapshot_restore_ms=%d "
        "context_ms=%d template_ms=%d render_ms=%d total_ms=%d",
        service.profile, getattr(service, "preparation_status", "disabled"),
        getattr(service, "timings", {}).get("remote_queries", 0),
        getattr(service, "timings", {}).get("snapshot_validation_ms", 0),
        getattr(service, "timings", {}).get("workspace_context_ms", 0),
        template_ms, render_ms, round((time.monotonic() - started) * 1000),
    )
    return response


def _render_existing_policy_access(
    request, *, item, access, policy_token, request_type, service, detail,
    correlation, started,
):
    logger.info(
        "colectivos_access_generation application=cotizacion_colectivos "
        "operation=generate_access profile=%s backend=%s cache=%s "
        "result=active_reused correlation=%s total_ms=%d",
        getattr(service, "profile", "unknown"),
        getattr(service, "backend", "unknown"),
        getattr(service, "preparation_status", "unavailable"),
        correlation,
        round((time.monotonic() - started) * 1000),
    )
    return _render_policy_workspace(
        request, token=policy_token, service=service, detail=detail, started=started,
        extra_context={"existing_access_notice": True, "generated_item": item},
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
def policy_detail(request, token, mode=None):
    resolve_tool_mode(request, mode)
    environment = get_colectivos_environment()
    total_started = time.monotonic()
    service = PolicyService()
    try:
        token_context = unsign_record_context(token, "policy")
        detail, members = service.group(
            token, source_kind=token_context.get("source_kind"),
        )
    except ColectivosServiceError as exc:
        if exc.code in {"invalid_record", "not_found"}:
            raise Http404("Póliza no encontrada") from exc
        return render(request, "cotizacion_colectivos/detail_error.html", {
            "message": exc.message,
            "zoho_environment": environment,
            "workspace_refresh_token": token if exc.code == "invalid_response" else "",
        }, status=_error_status(exc))
    return _render_policy_workspace(
        request, token=token, service=service, detail=detail, members=members,
        started=total_started,
    )


@never_cache
@require_http_methods(["POST"])
def policy_individual_access(request, token):
    resolve_tool_mode(request, INDIVIDUAL_MODE)
    if not has_internal_permission(request, "create_individual_quotation"):
        return permission_denied_response()
    started = time.monotonic()
    service = PolicyService()
    try:
        token_context = unsign_record_context(token, "policy")
        detail, members = service.group(
            token, source_kind=token_context.get("source_kind"),
        )
        options = affiliate_options(members)
        affiliate_key = str(request.POST.get("affiliate_key") or "")
        actor = get_internal_actor(request, create=True)
        responsible_options = task_responsible_options(collective_only=True)
        choices = [(item.actual_value, item.display_value) for item in responsible_options]
        email_form = IndividualAccessPrepareForm(request.POST)
        email_form.fields["responsible"].choices = choices
        if not email_form.is_valid():
            raise ValidationError("Revise el responsable y, si solicita verificación, el correo para el código.")
        otp_required = bool(email_form.cleaned_data.get("otp_required"))
        responsible_value = str(email_form.cleaned_data.get("responsible") or "").strip()
        responsible = next(
            (item for item in responsible_options if item.actual_value == responsible_value),
            None,
        )
        responsible_email = ""
        responsible_error = ""
        if responsible is not None:
            try:
                responsible_email = resolve_task_responsible_email(responsible)
            except ValidationError as exc:
                # El correo del responsable es un dato interno de la Task; no
                # puede impedir que el cliente reciba su enlace/OTP.
                responsible_error = str(exc)
        schema, _context_token, payload = build_policy_context(
            policy_token=token,
            detail=detail,
            members=members,
            affiliate_key=affiliate_key,
            creator_id=actor.pk,
        )
        payload.update({
            "task_responsible": responsible.actual_value if responsible else "",
            "task_responsible_display": responsible.display_value if responsible else "",
            "task_responsible_email": responsible_email,
            "task_area": "Negocios Bienestar y Beneficios",
            "otp_required": otp_required,
        })
        generated = generate_individual_access(
            context=payload,
            actor=actor,
            recipient=email_form.cleaned_data["recipient"],
            otp_required=otp_required,
        )
    except (ColectivosServiceError, ValidationError, Http404) as exc:
        if isinstance(exc, ColectivosServiceError) and exc.code in {"invalid_record", "not_found"}:
            raise Http404("Póliza no encontrada") from exc
        if getattr(service, "last_detail", None) is not None:
            return _render_policy_workspace(
                request,
                token=token,
                service=service,
                detail=service.last_detail,
                members=service.last_members,
                started=started,
                extra_context={
                    "individual_access_error": str(getattr(exc, "message", exc)),
                    "task_responsibles": locals().get("responsible_options", ()),
                    "responsible_warning": locals().get("responsible_error", ""),
                    "individual_otp_required": bool(request.POST.get("otp_required")),
                },
            )
        return render(request, "cotizacion_colectivos/detail_error.html", {
            "message": "No fue posible generar el enlace de cotización individual.",
            **_environment_context(request, INDIVIDUAL_MODE),
        }, status=400)
    external_url = request.build_absolute_uri(reverse(
        "colectivos_external:individual_quotation", args=[generated.token],
    ))
    logger.info(
        "colectivos_individual application=cotizacion_colectivos operation=generate_link "
        "profile=%s branch=%s cache=%s remote_queries=%d total_ms=%d",
        service.profile,
        schema.slug,
        service.preparation_status,
        service.timings.get("remote_queries", 0),
        round((time.monotonic() - started) * 1000),
    )
    return _render_policy_workspace(
        request,
        token=token,
        service=service,
        detail=detail,
        members=members,
        started=started,
        extra_context={
            "individual_generated_url": external_url,
            "individual_generated_affiliate": next(
                (item for item in options if item.key == affiliate_key), None,
            ),
            "individual_generated_for_new_person": not affiliate_key,
            "responsible_warning": responsible_error,
            "individual_otp_required": otp_required,
        },
    )


@never_cache
@require_http_methods(["POST"])
def policy_refresh_preparation(request, token):
    if not has_internal_permission(request, "view_requests"):
        return permission_denied_response()
    try:
        context = unsign_record_context(token, "policy")
        PolicyService().group(token, source_kind=context.get("source_kind"), refresh=True)
    except ColectivosServiceError as exc:
        if exc.code in {"invalid_record", "not_found"}:
            raise Http404("Póliza no encontrada") from exc
        return render(request, "cotizacion_colectivos/detail_error.html", {
            "message": exc.message, **_environment_context(),
        }, status=_error_status(exc))
    return redirect("cotizacion_colectivos:policy_detail", token=token)


@never_cache
@require_http_methods(["POST"])
def policy_generate_access(request, token, request_type=None):
    if not has_internal_permission(request, "create_requests") or not has_internal_permission(request, "generate_external_access"):
        return permission_denied_response()
    request_type = request_type or request.POST.get("request_type", "")
    if request_type not in {SolicitudColectivo.RequestType.UPDATE, SolicitudColectivo.RequestType.RENEWAL}:
        raise Http404("Tipo de solicitud no válido")
    force_new = request.POST.get("force_new") == "1"
    email_form = OptionalAccessEmailForm({"recipient": request.POST.get("recipient", "")})
    if not email_form.is_valid():
        return render(request, "cotizacion_colectivos/detail_error.html", {
            "message": "Debe indicar un correo válido para proteger el acceso con OTP.",
            **_environment_context(),
        }, status=400)
    recipient = email_form.cleaned_data["recipient"]
    total_started = time.monotonic()
    correlation = uuid.uuid4().hex
    service = None
    created = False
    try:
        context = unsign_record_context(token, "policy")
        source_kind = context.get("source_kind") or "company"
        actor = get_internal_actor(request, create=True)
        service = PolicyService()
        request_started = time.monotonic()
        item, created = create_or_reuse_request_from_policy(
            token=token,
            source_kind=source_kind,
            actor=actor,
            assigned_to=actor,
            request_type=request_type,
            deadline=timezone.localdate() + timedelta(days=settings.COLECTIVOS_EXTERNAL_LINK_DAYS),
            service=service,
        )
        # The prepared policy is reused here; no second remote traversal is
        # needed in the normal path. It keeps the analyst in the workspace.
        detail = service.detail(token)
        if hasattr(service, "timings"):
            service.timings["request_creation_ms"] = round((time.monotonic() - request_started) * 1000)
        live_access = item.external_accesses.filter(
            status__in=[
                AccesoExternoSolicitudColectivo.Status.ACTIVE,
                AccesoExternoSolicitudColectivo.Status.VERIFIED,
            ],
            expires_at__gt=timezone.now(),
        ).order_by("-created_at").first()
        if live_access is not None and not force_new:
            update_access_recipient(access=live_access, actor=actor, recipient=recipient)
            return _render_existing_policy_access(
                request, item=item, access=live_access, policy_token=token,
                request_type=request_type, service=service, detail=detail,
                correlation=correlation, started=total_started,
            )
        regenerate = force_new and live_access is not None
        access_started = time.monotonic()
        try:
            generated = generate_access(
                request=item,
                actor=actor,
                recipient=recipient,
                regenerate=regenerate,
            )
        except ActiveAccessExistsError:
            live_access = item.external_accesses.filter(
                status__in=[
                    AccesoExternoSolicitudColectivo.Status.ACTIVE,
                    AccesoExternoSolicitudColectivo.Status.VERIFIED,
                ],
                expires_at__gt=timezone.now(),
            ).order_by("-created_at").first()
            if live_access is None:
                raise
            update_access_recipient(access=live_access, actor=actor, recipient=recipient)
            return _render_existing_policy_access(
                request, item=item, access=live_access, policy_token=token,
                request_type=request_type, service=service, detail=detail,
                correlation=correlation, started=total_started,
            )
        if hasattr(service, "timings"):
            service.timings["access_creation_ms"] = round((time.monotonic() - access_started) * 1000)
    except (ColectivosServiceError, ExternalAccessError, ValidationError) as exc:
        logger.warning(
            "colectivos_access_generation application=cotizacion_colectivos operation=generate_access "
            "profile=%s backend=%s cache=%s result=error category=%s correlation=%s total_ms=%d",
            getattr(service, "profile", "unknown"), getattr(service, "backend", "unknown"),
            getattr(service, "preparation_status", "unavailable"),
            getattr(exc, "code", None) or exc.__class__.__name__, correlation,
            round((time.monotonic() - total_started) * 1000),
        )
        return render(request, "cotizacion_colectivos/detail_error.html", {
            "message": "No fue posible generar el enlace. Intente nuevamente más tarde.",
            **_environment_context(),
        }, status=400)
    timings = getattr(service, "timings", {})
    logger.info(
        "colectivos_access_generation application=cotizacion_colectivos operation=generate_access "
        "profile=%s backend=%s cache=%s result=ok created=%s records=%d correlation=%s "
        "organization_ms=%d policy_lookup_ms=%d risks1_query_ms=%d contacts_query_ms=%d "
        "risks_query_ms=%d grouping_ms=%d snapshot_validation_ms=%d snapshot_serialization_ms=%d "
        "database_insert_ms=%d registro_bulk_create_ms=%d request_creation_ms=%d "
        "access_creation_ms=%d total_ms=%d",
        service.profile, getattr(service, "backend", "test"),
        getattr(service, "preparation_status", "disabled"), created,
        item.record_count, correlation,
        timings.get("organization_ms", 0), timings.get("policy_lookup_ms", 0),
        timings.get("risks1_query_ms", 0), timings.get("contacts_query_ms", 0),
        timings.get("risks_query_ms", 0), timings.get("grouping_ms", 0),
        timings.get("snapshot_validation_ms", 0), timings.get("snapshot_serialization_ms", 0),
        timings.get("database_insert_ms", 0), timings.get("registro_bulk_create_ms", 0),
        timings.get("request_creation_ms", 0), timings.get("access_creation_ms", 0),
        round((time.monotonic() - total_started) * 1000),
    )
    audit(
        request,
        "CREATE" if created else "UPDATE",
        reason="Enlace externo directo de Colectivos generado.",
        metadata={"request_id": item.public_id, "request_type": request_type, "regenerated": regenerate},
    )
    return _render_policy_workspace(
        request, token=token, service=service, detail=detail, started=total_started,
        extra_context={
            "generated_url": generated.url,
            "generated_token": generated.token,
            "generated_expires_at": generated.access.expires_at,
            "generated_at": generated.access.created_at,
            "generated_item": item,
            "generated_created": created,
            "generated_regenerated": regenerate,
            "generated_recipient": recipient,
        },
    )


@never_cache
@require_http_methods(["POST"])
def policy_revoke_access(request, token):
    """Revoke only the live access belonging to this signed policy context."""
    if not has_internal_permission(request, "revoke_external_access"):
        return permission_denied_response()
    try:
        context = unsign_record_context(token, "policy")
        source_kind = context.get("source_kind") or "company"
        service = PolicyService()
        detail = service.detail(token)
        policy_hash, source_hash = request_reference_hashes(
            token=token, source_kind=source_kind, holder=detail.holder,
        )
        item = SolicitudColectivo.objects.filter(
            Q(policy_reference_hash=policy_hash) | Q(policies__policy_reference_hash=policy_hash),
            source_reference_hash=source_hash,
            zoho_profile=service.profile,
            external_accesses__status__in=(
                AccesoExternoSolicitudColectivo.Status.ACTIVE,
                AccesoExternoSolicitudColectivo.Status.VERIFIED,
            ),
            external_accesses__expires_at__gt=timezone.now(),
        ).distinct().order_by("-updated_at", "-created_at", "-pk").first()
        if item is None:
            raise ExternalAccessError("No existe un acceso vigente para revocar.")
        revoke_access(request=item, actor=get_internal_actor(request, create=True))
    except (ColectivosServiceError, ExternalAccessError):
        return HttpResponse("No existe un acceso vigente para revocar.", status=400)
    audit(
        request, "UPDATE", reason="Acceso externo de Colectivos revocado desde la póliza.",
        metadata={"request_id": item.public_id},
    )
    return redirect("cotizacion_colectivos:policy_detail", token=token)


@never_cache
@require_http_methods(["GET"])
def policy_group(request, token):
    environment = get_colectivos_environment()
    try:
        context = unsign_record_context(token, "policy")
        detail, members = PolicyService().group(token, source_kind=context.get("source_kind"))
    except ColectivosServiceError as exc:
        if exc.code in {"invalid_record", "not_found"}:
            raise Http404("Póliza no encontrada") from exc
        return render(request, "cotizacion_colectivos/detail_error.html", {"message": exc.message, "zoho_environment": environment}, status=_error_status(exc))
    return render(request, "cotizacion_colectivos/policy_group.html", {
        "detail": detail, "members": members, "policy_token": token,
        "zoho_environment": environment,
    })


@never_cache
@require_http_methods(["POST"])
def policy_excel(request, token):
    if not has_internal_permission(request, "export_excel"):
        return permission_denied_response()
    started = time.monotonic()
    service = PolicyService()
    try:
        content, detail = build_current_policy_workbook(
            token, service=service, include_detail=True,
        )
    except ColectivosServiceError as exc:
        if exc.code in {"invalid_record", "not_found"}:
            raise Http404("Póliza no encontrada") from exc
        return HttpResponse("No fue posible generar el archivo.", status=503)
    response = HttpResponse(content, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    context = unsign_record_context(token, "policy")
    prefix = "Grupo_Relacionado" if context.get("source_kind") == "person" else "Grupo_Actual"
    response["Content-Disposition"] = f'attachment; filename="{download_filename(prefix, origin=detail.holder, branch=detail.branch_name, masked_reference=detail.masked_reference)}"'
    response["Cache-Control"] = "no-store, private"
    response["Pragma"] = "no-cache"
    audit(request, "REPORT_EXPORT", reason="Exportación de grupo actual Colectivos.", metadata={"application": "cotizacion_colectivos", "format": "xlsx"})
    logger.info(
        "colectivos_workspace application=cotizacion_colectivos operation=excel "
        "profile=%s cache=%s remote_queries=%d excel_ms=%d",
        service.profile, service.preparation_status,
        service.timings.get("remote_queries", 0),
        round((time.monotonic() - started) * 1000),
    )
    return response


@never_cache
@require_http_methods(["GET"])
def policy_invitation_preview(request, token):
    if not has_internal_permission(request, "export_excel"):
        return permission_denied_response()
    tool_mode = resolve_tool_mode(request, INVITATIONS_MODE)
    try:
        detail, previews, metadata = preview_invitation_templates(token)
    except ColectivosServiceError as exc:
        if exc.code in {"invalid_record", "not_found"}:
            raise Http404("Póliza no encontrada") from exc
        return render(request, "cotizacion_colectivos/detail_error.html", {
            "message": exc.message, **_environment_context(),
        }, status=409)
    return render(request, "cotizacion_colectivos/invitation_preview.html", {
        "detail": detail, "previews": previews, "policy_token": token,
        "preparation_metadata": metadata,
        "colectivos_mode": tool_mode,
        **_invitation_page_context(detail, previews, metadata),
        **_environment_context(),
    })


@never_cache
@require_http_methods(["GET"])
def branch_invitation_preview(request, token):
    if not has_internal_permission(request, "export_excel"):
        return permission_denied_response()
    try:
        detail, previews, metadata = preview_invitation_templates(token, consolidated=True)
    except ColectivosServiceError as exc:
        if exc.code in {"invalid_record", "not_found"}:
            raise Http404("Ramo no encontrado") from exc
        return render(request, "cotizacion_colectivos/detail_error.html", {
            "message": exc.message, **_environment_context(),
        }, status=409)
    return render(request, "cotizacion_colectivos/invitation_preview.html", {
        "detail": detail, "previews": previews,
        "consolidated": True, "consolidated_token": token,
        "preparation_metadata": metadata,
        "colectivos_mode": resolve_tool_mode(request, INVITATIONS_MODE),
        **_invitation_page_context(detail, previews, metadata),
        **_environment_context(),
    })


@never_cache
@require_http_methods(["POST"])
def branch_invitation_download(request, token):
    if not has_internal_permission(request, "export_excel"):
        return permission_denied_response()
    try:
        content, filename, content_type, errors = generate_invitation_templates(
            token, template_code=str(request.POST.get("template_code") or ""),
            insurer_code=str(request.POST.get("insurer_code") or ""),
            consolidated=True,
        )
    except ColectivosServiceError as exc:
        if exc.code in {"invalid_record", "not_found"}:
            raise Http404("Ramo no encontrado") from exc
        return render(request, "cotizacion_colectivos/detail_error.html", {
            "message": exc.message, **_environment_context(),
        }, status=409)
    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Cache-Control"] = "no-store, private"
    response["X-Content-Type-Options"] = "nosniff"
    if errors:
        response["X-Colectivos-Template-Warnings"] = str(len(errors))
    return response


@never_cache
@require_http_methods(["POST"])
def policy_invitation_download(request, token):
    if not has_internal_permission(request, "export_excel"):
        return permission_denied_response()
    try:
        content, filename, content_type, errors = generate_invitation_templates(
            token, template_code=str(request.POST.get("template_code") or ""),
            insurer_code=str(request.POST.get("insurer_code") or ""),
        )
    except ColectivosServiceError as exc:
        if exc.code in {"invalid_record", "not_found"}:
            raise Http404("Póliza no encontrada") from exc
        return render(request, "cotizacion_colectivos/detail_error.html", {
            "message": exc.message, **_environment_context(),
        }, status=409)
    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Cache-Control"] = "no-store, private"
    response["Pragma"] = "no-cache"
    response["X-Content-Type-Options"] = "nosniff"
    if errors:
        response["X-Colectivos-Template-Warnings"] = str(len(errors))
    audit(
        request, "REPORT_EXPORT",
        reason="Exportación de plantillas de invitación Colectivos.",
        metadata={"application": "cotizacion_colectivos", "format": "zip" if content_type == "application/zip" else "xlsx", "generated": 1 if content_type != "application/zip" else 2, "warnings": len(errors)},
    )
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
    requested_type = request.GET.get("type", "")
    if requested_type not in {SolicitudColectivo.RequestType.UPDATE, SolicitudColectivo.RequestType.RENEWAL}:
        requested_type = SolicitudColectivo.RequestType.UPDATE
    form = RequestCreateForm(request.POST or None, public_access=public_internal_access_enabled(), initial={"source_kind": token_context.get("source_kind") or "company", "request_type": requested_type, "assigned_to": request.user, "deadline": timezone.localdate() + timedelta(days=10)})
    error = ""
    existing_request = None
    if request.method == "POST" and form.is_valid():
        try:
            item = create_request_from_policy(
                token=token, source_kind=form.cleaned_data["source_kind"], actor=get_internal_actor(request, create=True),
                assigned_to=(get_internal_actor(request, create=True) if public_internal_access_enabled() else form.cleaned_data["assigned_to"]), request_type=form.cleaned_data["request_type"],
                deadline=form.cleaned_data["deadline"], internal_notes=form.cleaned_data["internal_notes"],
                is_test=form.cleaned_data["is_test"], service=service,
            )
        except ColectivosServiceError as exc:
            error = exc.message
            if exc.code == "duplicate":
                try:
                    detail_for_hash = service.detail(token)
                    policy_hash, source_hash = request_reference_hashes(token=token, source_kind=form.cleaned_data["source_kind"], holder=detail_for_hash.holder)
                    existing_request = SolicitudColectivo.objects.filter(
                        Q(policy_reference_hash=policy_hash) | Q(policies__policy_reference_hash=policy_hash),
                        source_reference_hash=source_hash, zoho_profile=service.profile,
                        request_type=form.cleaned_data["request_type"],
                    ).exclude(status__in=[SolicitudColectivo.Status.CLOSED, SolicitudColectivo.Status.CANCELLED, SolicitudColectivo.Status.EXPIRED]).distinct().order_by("-updated_at").first()
                except ColectivosServiceError:
                    existing_request = None
        else:
            audit(request, "CREATE", reason="Expediente Colectivos creado.", metadata={"request_id": item.public_id, "branch": item.branch_code, "records": item.record_count})
            return redirect("cotizacion_colectivos:request_detail", public_id=item.public_id)
    try:
        policy = service.detail(token)
    except ColectivosServiceError as exc:
        if exc.code in {"invalid_record", "not_found"}:
            raise Http404("Póliza no encontrada") from exc
        return render(request, "cotizacion_colectivos/detail_error.html", {"message": exc.message, "zoho_environment": environment}, status=_error_status(exc))
    return render(request, "cotizacion_colectivos/request_form.html", {"form": form, "policy": policy, "error": error, "existing_request": existing_request, "zoho_environment": environment})


@never_cache
@require_http_methods(["GET"])
def request_list(request):
    if not has_internal_permission(request, "view_requests"):
        return permission_denied_response()
    form = RequestFilterForm(request.GET, public_access=public_internal_access_enabled())
    # The filter uses the same values sent to Tasks.Responsable.  Options are
    # loaded once (metadata is cached), never once per inbox row; local user
    # assignments remain available for legacy request tasks.
    responsible_choices = [("", "Todos"), ("__pending__", "Pendiente de selección")]
    if not public_internal_access_enabled():
        try:
            responsible_choices.extend(
                (option.actual_value, option.display_value)
                for option in task_responsible_options(collective_only=True)
            )
        except Exception:
            pass
    if not public_internal_access_enabled():
        try:
            responsible_choices.extend(
                (f"__user:{user.pk}", user.get_full_name() or user.username)
                for user in get_user_model().objects.filter(is_active=True).order_by("username")
            )
        except Exception:
            pass
    try:
        for access in AccesoCotizacionIndividual.objects.only("encrypted_context", "safe_metadata").iterator(chunk_size=200):
            try:
                context = json.loads(decrypt(access.encrypted_context))
            except (TypeError, ValueError, json.JSONDecodeError):
                context = {}
            safe_access_metadata = access.safe_metadata or {}
            actual = str(context.get("task_responsible") or safe_access_metadata.get("task_responsible") or "").strip()
            display = str(context.get("task_responsible_display") or safe_access_metadata.get("task_responsible_display") or actual).strip()
            if actual and display:
                responsible_choices.append((actual, display))
    except Exception:
        pass
    requested_responsible = str(request.GET.get("task_responsible") or "").strip()
    if requested_responsible and requested_responsible not in {value for value, _label in responsible_choices}:
        # Keep an unknown submitted value valid so it produces an empty result,
        # rather than silently dropping every other filter.
        responsible_choices.append((requested_responsible, requested_responsible))
    form.fields["task_responsible"].choices = tuple(dict.fromkeys(responsible_choices))
    queryset = SolicitudColectivo.objects.select_related("assigned_to").prefetch_related(
        "policies",
        Prefetch(
            "task_outbox",
            queryset=ColectivosTaskOutbox.objects.order_by("-updated_at", "-pk"),
            to_attr="inbox_task_outboxes",
        ),
        Prefetch(
            "external_accesses",
            queryset=AccesoExternoSolicitudColectivo.objects.order_by("-created_at", "-pk"),
            to_attr="ordered_accesses",
        ),
    ).annotate(
        policy_count=Count("policies", distinct=True),
        active_access_count=Count(
            "external_accesses",
            filter=Q(
                external_accesses__status__in=(
                    AccesoExternoSolicitudColectivo.Status.ACTIVE,
                    AccesoExternoSolicitudColectivo.Status.VERIFIED,
                ),
                external_accesses__expires_at__gt=timezone.now(),
            ),
            distinct=True,
        ),
    ).all()
    if form.is_valid():
        data = form.cleaned_data
        if data["query"]:
            term = data["query"].strip()
            protected_matches = []
            normalized_term = _normalized_choice(term)
            if normalized_term:
                for candidate in SolicitudColectivo.objects.only(
                    "pk", "encrypted_snapshot", "snapshot_version",
                ).iterator(chunk_size=200):
                    try:
                        searchable = json.dumps(
                            request_snapshot(candidate), ensure_ascii=False, sort_keys=True,
                        )
                    except ValidationError:
                        continue
                    if normalized_term in _normalized_choice(searchable):
                        protected_matches.append(candidate.pk)
            queryset = queryset.filter(
                Q(public_id__icontains=term)
                | Q(client_label__icontains=term)
                | Q(masked_policy_reference__icontains=term)
                | Q(branch_name__icontains=term)
                | Q(pk__in=protected_matches)
            )
        if data["status"]:
            queryset = queryset.filter(status=data["status"])
        if data["source_kind"]:
            queryset = queryset.filter(source_kind=data["source_kind"])
        if data["branch"]:
            queryset = queryset.filter(Q(branch_code=data["branch"].strip()) | Q(policies__branch_code=data["branch"].strip())).distinct()
        if data["request_type"]:
            queryset = queryset.filter(request_type=data["request_type"])
        if data.get("assigned_to"):
            queryset = queryset.filter(assigned_to=data["assigned_to"])
        if data.get("task_responsible"):
            selected_responsible = data["task_responsible"]
            if selected_responsible.startswith("__user:"):
                queryset = queryset.filter(assigned_to_id=selected_responsible.split(":", 1)[1])
        if data["created_from"]:
            queryset = queryset.filter(created_at__date__gte=data["created_from"])
        if data["created_to"]:
            queryset = queryset.filter(created_at__date__lte=data["created_to"])
        if data["deadline_from"]:
            queryset = queryset.filter(deadline__gte=data["deadline_from"])
        if data["deadline_to"]:
            queryset = queryset.filter(deadline__lte=data["deadline_to"])
        if data.get("assigned_to_me"):
            queryset = queryset.filter(assigned_to=request.user)
        if data["warning"]:
            queryset = queryset.exclude(warnings=[])
    queryset = queryset.order_by("-updated_at", "-created_at", "-pk")
    actor = get_internal_actor(request, create=False)
    unread_request_ids = set()
    if actor is not None and has_internal_permission(request, "manage_notifications"):
        unread_request_ids = set(
            NotificacionColectivos.objects.filter(
                user=actor, read_at__isnull=True, notification_type="CLIENT_RESPONSE",
            ).values_list("request_id", flat=True)
        )
    operational_entries = []
    for item in queryset:
        _attach_request_operational_context(item)
        item.has_unread_response = item.pk in unread_request_ids
        item.inbox_kind = "request"
        item.inbox_policy_reference = item.primary_policy_reference
        item.inbox_branch_name = item.primary_branch_name
        item.inbox_client_label = item.client_label
        item.inbox_type_label = item.get_request_type_display()
        item.inbox_person_label = ""
        item.inbox_public_id = item.public_id
        item.inbox_last_activity = item.updated_at
        item.inbox_deadline = item.deadline
        item.inbox_status_label = item.get_status_display()
        item.inbox_status_tone = item.status_tone
        item.inbox_requires_attention = item.status == SolicitudColectivo.Status.ANSWERED
        item.inbox_access_summary = item.current_access_status
        item.inbox_access_opened = item.current_access_opened
        item.inbox_otp_verified = item.current_access_otp_verified
        item.inbox_detail_url = reverse(
            "cotizacion_colectivos:request_detail", args=[item.public_id],
        )
        selected_responsible = data.get("task_responsible") if form.is_valid() else ""
        if selected_responsible and not selected_responsible.startswith("__user:"):
            task_values = []
            for outbox in getattr(item, "inbox_task_outboxes", ()):
                try:
                    task_values.append(str(json.loads(decrypt(outbox.encrypted_payload)).get("Responsable") or "").strip())
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
            if selected_responsible == "__pending__":
                if any(task_values):
                    continue
            elif selected_responsible not in task_values:
                continue
        operational_entries.append(item)

    individual_entries = []
    if actor is not None and has_internal_permission(request, "view_individual_quotation"):
        unread_quotation_ids = set(
            NotificacionCotizacionIndividual.objects.filter(
                user=actor, read_at__isnull=True,
            ).values_list("quotation_id", flat=True)
        )
        accesses = AccesoCotizacionIndividual.objects.filter(created_by=actor).select_related(
            "quotation"
        ).prefetch_related("quotation__task_outbox").order_by("-created_at", "-pk")
        for access in accesses:
            try:
                context = json.loads(decrypt(access.encrypted_context))
            except (TypeError, ValueError, json.JSONDecodeError):
                context = {}
            quotation = access.quotation
            branch_code = quotation.branch_code if quotation else ""
            branch_name = (
                context.get("branch_name") or access.safe_metadata.get("branch") or "Ramo"
            )
            if quotation:
                status_code = SolicitudColectivo.Status.ANSWERED
                status_label = "Respondido"
            elif access.status == access.Status.EXPIRED:
                status_code, status_label = SolicitudColectivo.Status.EXPIRED, "Vencido"
            elif access.status == access.Status.REVOKED:
                status_code, status_label = SolicitudColectivo.Status.CANCELLED, "Revocado"
            elif access.first_access_at or access.status == access.Status.VERIFIED:
                status_code, status_label = SolicitudColectivo.Status.OPENED, "Abierto por cliente"
            else:
                status_code, status_label = SolicitudColectivo.Status.SENT, "Enlace activo"

            data = form.cleaned_data if form.is_valid() else {}
            searchable = " ".join(str(value or "") for value in (
                context.get("policy_label"), branch_code, branch_name,
                context.get("collective_context"), context.get("affiliate_label"),
                context.get("requester_name"), str(quotation.public_id) if quotation else "",
            ))
            if data.get("query") and _normalized_choice(data["query"]) not in _normalized_choice(searchable):
                continue
            if data.get("status") and data["status"] != status_code:
                continue
            if data.get("source_kind") and data["source_kind"] != context.get("source_kind"):
                continue
            if data.get("branch") and _normalized_choice(data["branch"]) not in _normalized_choice(
                " ".join((branch_code, branch_name, str(access.safe_metadata.get("branch") or "")))
            ):
                continue
            if data.get("request_type") and data["request_type"] != SolicitudColectivo.RequestType.QUOTE:
                continue
            if data.get("assigned_to") and data["assigned_to"].pk != access.created_by_id:
                continue
            selected_responsible = data.get("task_responsible")
            if selected_responsible:
                safe_access_metadata = access.safe_metadata or {}
                stored_responsible = str(
                    context.get("task_responsible")
                    or safe_access_metadata.get("task_responsible")
                    or ""
                ).strip()
                if selected_responsible == "__pending__":
                    if stored_responsible:
                        continue
                elif not selected_responsible.startswith("__user:") and stored_responsible != selected_responsible:
                    continue
            if data.get("created_from") and access.created_at.date() < data["created_from"]:
                continue
            if data.get("created_to") and access.created_at.date() > data["created_to"]:
                continue
            if data.get("deadline_from") and access.expires_at.date() < data["deadline_from"]:
                continue
            if data.get("deadline_to") and access.expires_at.date() > data["deadline_to"]:
                continue
            if data.get("assigned_to_me") and access.created_by_id != request.user.pk:
                continue
            if data.get("warning"):
                continue

            outboxes = tuple(quotation.task_outbox.all()) if quotation else ()
            latest_outbox = max(outboxes, key=lambda row: (row.updated_at, row.pk)) if outboxes else None
            requires_attention = bool(quotation) and (
                latest_outbox is None
                or latest_outbox.status != ColectivosTaskOutbox.Status.PUBLISHED
            )
            activity_candidates = tuple(value for value in (
                quotation.submitted_at if quotation else None,
                latest_outbox.updated_at if latest_outbox else None,
                access.last_access_at,
                access.otp_used_at,
                access.first_access_at,
                access.created_at,
            ) if value is not None)
            access.inbox_kind = "individual"
            access.inbox_policy_reference = context.get("policy_label") or "Póliza colectiva"
            access.inbox_branch_name = branch_name
            access.inbox_client_label = context.get("collective_context") or "Cliente sin etiqueta"
            access.inbox_type_label = "Cotización Individual"
            access.inbox_person_label = context.get("affiliate_label") or "Nuevo afiliado"
            access.inbox_public_id = str(quotation.public_id) if quotation else ""
            access.inbox_last_activity = max(activity_candidates)
            access.inbox_deadline = access.expires_at
            access.inbox_status_label = status_label
            access.inbox_status_tone = (
                "attention" if requires_attention else
                "success" if latest_outbox and latest_outbox.status == ColectivosTaskOutbox.Status.PUBLISHED else
                "opened" if status_code == SolicitudColectivo.Status.OPENED else
                "muted" if status_code in {SolicitudColectivo.Status.EXPIRED, SolicitudColectivo.Status.CANCELLED} else
                "neutral"
            )
            access.inbox_requires_attention = requires_attention
            access.inbox_access_summary = access.get_status_display()
            access.inbox_access_opened = bool(access.first_access_at)
            access.inbox_otp_verified = bool(access.otp_used_at)
            access.inbox_otp_required = individual_otp_required(access)
            access.has_unread_response = bool(quotation and quotation.pk in unread_quotation_ids)
            access.inbox_detail_url = reverse(
                "cotizacion_colectivos:individual_expedient",
                args=[sign_receipt(quotation.public_id)],
            ) if quotation else ""
            individual_entries.append(access)
            operational_entries.append(access)

    sort_mode = str(request.GET.get("sort") or "recent").strip().lower()
    if sort_mode == "oldest":
        operational_entries.sort(key=lambda item: (item.inbox_last_activity.timestamp(), item.pk))
    elif sort_mode == "attention":
        operational_entries.sort(key=lambda item: (
            0 if item.inbox_requires_attention else 1,
            -item.inbox_last_activity.timestamp(), item.pk,
        ))
    else:
        # La bandeja es cronológica por defecto; la prioridad de gestión se
        # expresa en estado/acento, no reordena silenciosamente la actividad.
        operational_entries.sort(key=lambda item: (-item.inbox_last_activity.timestamp(), -item.pk))
    page = Paginator(operational_entries, 25).get_page(request.GET.get("page"))
    query = request.GET.copy()
    query.pop("page", None)
    return render(
        request,
        "cotizacion_colectivos/request_list.html",
        {
            "form": form,
            "page": page,
            "filter_query": query.urlencode(),
            "sort_mode": sort_mode,
            "unread_count": len(unread_request_ids) + sum(
                1 for item in individual_entries if item.has_unread_response
            ),
            "can_manage_notifications": has_internal_permission(request, "manage_notifications"),
            **_environment_context(),
        },
    )


def _snapshot_policy_rows(snapshot):
    snapshots = snapshot.get("policies") if isinstance(snapshot, dict) else None
    if not isinstance(snapshots, list):
        snapshots = [snapshot]
    rows = []
    for value in snapshots:
        policy = value.get("policy", {}) if isinstance(value, dict) else {}
        if isinstance(policy, dict):
            rows.append(policy)
    return tuple(rows)


def _attach_request_operational_context(item):
    try:
        snapshot = request_snapshot(item)
    except ValidationError:
        snapshot = {}
    snapshot_policies = _snapshot_policy_rows(snapshot)
    persisted_policies = list(item.policies.all())
    operational_policies = []
    for index, policy in enumerate(persisted_policies):
        protected = snapshot_policies[index] if index < len(snapshot_policies) else {}
        operational_policies.append({
            "reference": protected.get("reference") or policy.masked_policy_reference,
            "branch_name": protected.get("branch_name") or policy.branch_name,
            "insurer": policy.insurer,
            "status": policy.policy_status,
            "record_count": policy.record_count,
        })
    if not operational_policies:
        protected = snapshot_policies[0] if snapshot_policies else {}
        operational_policies.append({
            "reference": protected.get("reference") or item.masked_policy_reference,
            "branch_name": protected.get("branch_name") or item.branch_name,
            "insurer": protected.get("insurer") or "",
            "status": protected.get("state") or "",
            "record_count": item.record_count,
        })
    item.operational_policies = tuple(operational_policies)
    item.primary_policy_reference = operational_policies[0]["reference"]
    item.primary_branch_name = operational_policies[0]["branch_name"]
    access = item.ordered_accesses[0] if getattr(item, "ordered_accesses", ()) else None
    item.current_access_status = _access_status_display(access)
    item.current_access_opened = bool(access and access.first_access_at)
    item.current_access_otp_verified = bool(access and access.otp_used_at)
    item.status_tone = {
        item.Status.ANSWERED: "attention",
        item.Status.REVIEW: "attention",
        item.Status.OPENED: "opened",
        item.Status.CORRECTION: "warning",
        item.Status.EXPIRED: "muted",
        item.Status.CANCELLED: "muted",
        item.Status.PENDING_ZOHO: "warning",
        item.Status.LOADED_ZOHO: "success",
        item.Status.CLOSED: "success",
    }.get(item.status, "neutral")


@never_cache
@require_http_methods(["GET"])
def request_detail(request, public_id):
    if not has_internal_permission(request, "view_requests"):
        return permission_denied_response()
    item = get_object_or_404(
        SolicitudColectivo.objects.select_related("assigned_to", "created_by").prefetch_related(
            Prefetch(
                "policies",
                queryset=SolicitudColectivoPoliza.objects.annotate(
                    change_count=Count("changes", distinct=True)
                ).prefetch_related("records"),
            ),
            Prefetch(
                "responses",
                queryset=RespuestaSolicitudColectivo.objects.prefetch_related(
                    "changes__policy", "changes__original_record", "attachments",
                ).order_by("-version"),
                to_attr="ordered_responses",
            ),
            "events",
            "task_outbox",
        ),
        public_id=public_id,
    )
    try:
        snapshot = request_snapshot(item)
    except ValidationError:
        snapshot = None
    _attach_request_operational_context(item)
    for index, policy in enumerate(item.policies.all()):
        if index < len(item.operational_policies):
            policy.operational_reference = item.operational_policies[index]["reference"]
    try:
        notes = decrypt(item.encrypted_internal_notes) if item.encrypted_internal_notes else ""
    except ValueError:
        notes = ""
    edit_form = RequestEditForm(public_access=public_internal_access_enabled(), initial={"assigned_to": item.assigned_to, "deadline": item.deadline, "internal_notes": notes})
    access = item.external_accesses.order_by("-created_at").first()
    access_summary = None
    if access:
        if has_internal_permission(request, "view_personal_data"):
            try:
                recipient = mask_email(decrypt(access.encrypted_recipient))
            except ValueError:
                recipient = "***"
        else:
            recipient = "Oculto por permisos"
        access_summary = {
            "status": _access_status_display(access), "recipient": recipient, "created_at": access.created_at,
            "expires_at": access.expires_at, "first_access_at": access.first_access_at,
            "last_access_at": access.last_access_at, "access_count": access.access_count,
            "token_valid": access.status in {access.Status.ACTIVE, access.Status.VERIFIED} and access.expires_at > timezone.now(),
            "sent": bool(access.sent_at),
        }
    policy_token = ""
    try:
        candidate = decrypt(item.encrypted_policy_token)
        unsign_record_context(candidate, "policy")
        policy_token = candidate
    except (ValueError, ColectivosServiceError):
        pass
    client_url = ""
    policy_url = ""
    insured_url = ""
    if policy_token:
        policy_url = reverse("cotizacion_colectivos:policy_detail", args=[policy_token])
        insured_url = reverse("cotizacion_colectivos:policy_group", args=[policy_token])
        try:
            token_context = unsign_record_context(policy_token, "policy")
            source_kind = token_context.get("source_kind")
            source_id = token_context.get("source_id")
            if source_kind in {"company", "person"} and source_id:
                client_url = reverse(
                    "cotizacion_colectivos:client_detail",
                    args=[source_kind, sign_record_id(source_id, source_kind)],
                )
        except ColectivosServiceError:
            pass
    can_view_responses = has_internal_permission(request, "view_responses")
    latest_response = next((
        response for response in getattr(item, "ordered_responses", ())
        if response.status in {
            RespuestaSolicitudColectivo.Status.SUBMITTED,
            RespuestaSolicitudColectivo.Status.APPROVED,
        }
    ), None)
    response_summary = None
    if latest_response is not None and can_view_responses:
        try:
            observations = (
                decrypt(latest_response.encrypted_client_observations)
                if latest_response.encrypted_client_observations else ""
            )
        except ValueError:
            observations = ""
        novelties = {}
        operational_edits = (latest_response.safe_metadata or {}).get("operational_edits") or {}
        for change in latest_response.changes.all():
            if change.action not in {
                CambioSolicitudColectivo.Action.INCLUDE,
                CambioSolicitudColectivo.Action.RETIRE,
                CambioSolicitudColectivo.Action.MODIFY,
            }:
                continue
            try:
                value = decrypt(change.encrypted_new_value) if change.encrypted_new_value else ""
            except ValueError:
                value = "Información no disponible"
            key = (change.action, change.original_record_id or f"include-{change.position}")
            novelty = novelties.setdefault(key, {
                "action": change.get_action_display(),
                "action_code": change.action,
                "record": "Inclusión",
                "label": "Registro seleccionado",
                "values": [],
                "change_id": None,
                "edit_values": {},
            })
            if change.functional_field == "accion":
                novelty["change_id"] = change.pk
                edit_values = operational_edits.get(str(change.pk), {})
                novelty["edit_values"] = dict(edit_values.get("fields") or {}) if isinstance(edit_values, dict) else {}
                continue
            if novelty["change_id"] is None:
                # Historical responses may predate the explicit action marker;
                # the first functional change is still a server-side anchor.
                novelty["change_id"] = change.pk
            if change.original_record_id:
                novelty["record"] = f"Registro {change.original_record.original_position}"
                payload = {}
                try:
                    payload_source = (
                        change.original_record.encrypted_branch_payload
                        if change.original_record and change.original_record.encrypted_branch_payload
                        else change.encrypted_branch_payload
                    )
                    payload = json.loads(decrypt(payload_source)) if payload_source else {}
                    novelty["label"] = (
                        payload.get("display_name")
                        or payload.get("name")
                        or payload.get("Nombre")
                        or payload.get("full_name")
                        or ({
                            "PERSONA": "Persona",
                            "BENEFICIARIO": "Beneficiario",
                            "VEHICULO": "Vehículo",
                            "INMUEBLE": "Inmueble",
                        }.get(str(change.original_record.element_type or ""), "Registro recibido"))
                    )
                except (TypeError, ValueError, json.JSONDecodeError):
                    novelty["label"] = "Registro recibido"
                # Enrich the human card from the persisted snapshot only;
                # never perform a per-row Zoho lookup.
                for source_key, label in (
                    ("documento", "Documento"), ("document", "Documento"),
                    ("N_mero_de_ID", "Documento"), ("tipo_id", "Tipo de identificación"),
                    ("placa", "Placa"), ("marca", "Marca"), ("modelo", "Modelo"),
                    ("clase", "Clase"), ("observaciones", "Observaciones"),
                ):
                    candidate = payload.get(source_key)
                    if candidate not in (None, "") and not any(item["label"] == label for item in novelty["values"]):
                        novelty["values"].append({"label": label, "value": candidate})
            elif change.encrypted_branch_payload:
                try:
                    payload = json.loads(decrypt(change.encrypted_branch_payload))
                except (TypeError, ValueError, json.JSONDecodeError):
                    payload = {}
                for source_key, label in (
                    ("documento", "Documento"), ("document", "Documento"),
                    ("N_mero_de_ID", "Documento"), ("tipo_id", "Tipo de identificación"),
                    ("placa", "Placa"), ("marca", "Marca"), ("modelo", "Modelo"),
                    ("clase", "Clase"), ("observaciones", "Observaciones"),
                ):
                    candidate = payload.get(source_key)
                    if candidate not in (None, "") and not any(item["label"] == label for item in novelty["values"]):
                        novelty["values"].append({"label": label, "value": candidate})
            edit_value = novelty.get("edit_values", {}).get(change.functional_field)
            display_value = edit_value if edit_value is not None else value
            if change.functional_field == "observaciones" and change.encrypted_observation:
                try:
                    display_value = decrypt(change.encrypted_observation) or display_value
                except (TypeError, ValueError):
                    pass
            # Empty optional fields are not useful in the human response card.
            # Keep the underlying change intact for audit/review workflows.
            if display_value in (None, ""):
                continue
            novelty["edit_values"].setdefault(change.functional_field, value or "")
            if change.functional_field == "observaciones" and change.encrypted_observation:
                novelty["edit_values"]["observaciones"] = display_value
            novelty["values"].append({
                "label": RESPONSE_FIELD_LABELS.get(
                    change.functional_field,
                    change.functional_field.replace("_", " ").strip().capitalize() or "Información",
                ),
                "value": display_value,
            })
        novelties = tuple(item for item in novelties.values() if item.get("values"))
        response_summary = {
            "item": latest_response,
            "fields": novelties,
            "observations": observations,
            "attachment_count": latest_response.attachments.count(),
            "response_type": (latest_response.safe_metadata or {}).get("response_type", "CHANGES"),
        }
    latest_outbox = item.task_outbox.order_by("-updated_at", "-pk").first()
    task_responsibles = ()
    task_responsibles_error = ""
    if latest_outbox and latest_outbox.status == latest_outbox.Status.PENDING:
        try:
            task_responsibles = task_responsible_options(collective_only=True)
        except (ValidationError, ColectivosServiceError):
            task_responsibles_error = "No fue posible cargar los responsables del área Colectivos."
    zoho_tasks = []
    for outbox in item.task_outbox.order_by("event_kind", "-updated_at", "-pk"):
        remote_id = ""
        if outbox.status == outbox.Status.PUBLISHED and outbox.encrypted_remote_id:
            try:
                remote_id = decrypt(outbox.encrypted_remote_id)
            except ValueError:
                remote_id = "No disponible"
        task_record = read_published_task(remote_id) if remote_id else None
        local_responsible = ""
        try:
            local_responsible = str(json.loads(decrypt(outbox.encrypted_payload)).get("Responsable") or "").strip()
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
        remote_responsible = task_record.get("Responsable") if isinstance(task_record, dict) else ""
        if isinstance(remote_responsible, dict):
            remote_responsible = remote_responsible.get("name") or remote_responsible.get("id") or ""
        zoho_tasks.append({
            "outbox_id": outbox.pk,
            "kind": outbox.event_kind,
            "type": {"INCLUSION": "Ingresos", "RETIRO": "Retiros", "COTIZACION": "Cotización"}.get(outbox.event_kind, outbox.event_kind),
            "status": outbox.get_status_display(),
            "task_id": remote_id,
            "responsible": str(remote_responsible or local_responsible).strip(),
            "remote_state": str(task_record.get("Estado") or "").strip() if isinstance(task_record, dict) else "",
            "last_attempt": outbox.updated_at if outbox.attempts else None,
            "attempts": outbox.attempts,
            "safe_error": outbox.safe_error_code,
        })
    zoho_summary = {
        "status": latest_outbox.get_status_display() if latest_outbox else "No preparada",
        "last_attempt": latest_outbox.updated_at if latest_outbox and latest_outbox.attempts else None,
        "attempts": latest_outbox.attempts if latest_outbox else 0,
        "safe_error": latest_outbox.safe_error_code if latest_outbox else "",
        "task_id": "",
        "contract_ready": False,
    }
    if latest_outbox and latest_outbox.status == latest_outbox.Status.PUBLISHED and latest_outbox.encrypted_remote_id:
        try:
            zoho_summary["task_id"] = decrypt(latest_outbox.encrypted_remote_id)
        except ValueError:
            zoho_summary["task_id"] = "No disponible"
    has_request_actions = bool(
        (response_summary and response_summary["item"].attachments.exists())
        or (item.status == "BORRADOR" and has_internal_permission(request, "create_requests"))
        or (
            (not access_summary or not access_summary.get("token_valid"))
            and item.status in {"LISTA_PARA_ENVIAR", "REQUIERE_CORRECCION"}
            and has_internal_permission(request, "generate_external_access")
        )
        or (access_summary and access_summary.get("token_valid") and has_internal_permission(request, "regenerate_external_access"))
        or (access_summary and access_summary.get("token_valid") and has_internal_permission(request, "revoke_external_access"))
        or any(task["status"] in {"Pendiente", "Requiere conciliación", "Bloqueada"} for task in zoho_tasks)
        or item.request_type == "COTIZACION"
    )
    allowed_targets = tuple(
        target for target in SolicitudColectivo.TRANSITIONS.get(item.status, set())
        if has_internal_permission(request, _transition_permission(target))
    )
    return render(request, "cotizacion_colectivos/request_detail.html", {
        "item": item, "snapshot": snapshot,
        "transition_form": RequestTransitionForm(
            current_status=item.status, allowed_targets=allowed_targets,
        ), "edit_form": edit_form,
        "snapshot_form": SnapshotRegenerateForm(), "access_summary": access_summary, "policy_token": policy_token,
        "client_url": client_url, "policy_url": policy_url, "insured_url": insured_url,
        "can_edit": has_internal_permission(request, "edit_requests"),
        "can_prepare": has_internal_permission(request, "create_requests"),
        "can_generate_access": has_internal_permission(request, "generate_external_access"),
        "can_regenerate_access": has_internal_permission(request, "regenerate_external_access"),
        "can_revoke_access": has_internal_permission(request, "revoke_external_access"),
        "can_send_requests": has_internal_permission(request, "send_requests"),
        "can_approve": has_internal_permission(request, "approve_requests"),
        "can_view_responses": can_view_responses,
        "response_summary": response_summary,
        "zoho_summary": zoho_summary,
        "zoho_tasks": tuple(zoho_tasks),
        "task_responsibles": task_responsibles,
        "task_responsibles_error": task_responsibles_error,
        "has_request_actions": has_request_actions,
        **_environment_context(),
    })


@never_cache
@require_http_methods(["POST"])
def response_novelty_edit(request, public_id, version, change_id):
    """Persist an auditable local correction for one response novelty."""
    if not (
        has_internal_permission(request, "edit_requests")
        or has_internal_permission(request, "approve_responses")
    ):
        return permission_denied_response()
    response = get_object_or_404(
        RespuestaSolicitudColectivo.objects.select_related("request"),
        request__public_id=public_id,
        version=version,
        status__in=(RespuestaSolicitudColectivo.Status.SUBMITTED, RespuestaSolicitudColectivo.Status.APPROVED),
    )
    anchor = get_object_or_404(
        CambioSolicitudColectivo,
        pk=change_id,
        response=response,
        action__in=(CambioSolicitudColectivo.Action.INCLUDE, CambioSolicitudColectivo.Action.RETIRE, CambioSolicitudColectivo.Action.MODIFY),
    )
    anchor = response.changes.filter(
        action=anchor.action,
        original_record_id=anchor.original_record_id,
        position=anchor.position,
        functional_field="accion",
    ).first() or anchor
    allowed_by_action = {
        CambioSolicitudColectivo.Action.RETIRE: {"fecha_retiro", "fecha_efectiva", "observaciones"},
        CambioSolicitudColectivo.Action.INCLUDE: {"fecha_ingreso", "fecha_efectiva", "nombres", "apellidos", "documento", "tipo_id", "rol", "parentesco", "estado", "plan", "observaciones"},
        CambioSolicitudColectivo.Action.MODIFY: {"fecha_efectiva", "plan", "parentesco", "estado", "observaciones"},
    }[anchor.action]
    form = NoveltyEditForm(request.POST)
    if not form.is_valid():
        messages.warning(request, "La corrección de la novedad no es válida.")
        return redirect("cotizacion_colectivos:request_detail", public_id=public_id)
    fields = {
        key: (value.isoformat() if hasattr(value, "isoformat") else str(value or "").strip())
        for key, value in form.cleaned_data.items()
        if key in allowed_by_action
    }
    if anchor.action == CambioSolicitudColectivo.Action.RETIRE and not (fields.get("fecha_retiro") or fields.get("fecha_efectiva")):
        messages.warning(request, "La novedad de retiro requiere una fecha solicitada.")
        return redirect("cotizacion_colectivos:request_detail", public_id=public_id)
    metadata = dict(response.safe_metadata or {})
    edits = dict(metadata.get("operational_edits") or {})
    edits[str(anchor.pk)] = {
        "fields": fields,
        "updated_at": timezone.now().isoformat(),
        "updated_by": int(get_internal_actor(request, create=True).pk),
    }
    metadata["operational_edits"] = edits
    response.safe_metadata = metadata
    response.save(update_fields=("safe_metadata", "updated_at"))

    # Keep the pending local Task intent aligned with the operational version;
    # a published remote Task is never rewritten here.
    event_kind = {"INCLUIR": "INCLUSION", "RETIRAR": "RETIRO", "MODIFICAR": "MODIFICACION"}.get(anchor.action, "")
    outbox = response.request.task_outbox.filter(event_kind=event_kind, status=ColectivosTaskOutbox.Status.PENDING).order_by("-pk").first()
    if outbox is not None:
        try:
            record = json.loads(decrypt(outbox.encrypted_payload))
            date_value = fields.get("fecha_retiro") or fields.get("fecha_ingreso") or fields.get("fecha_efectiva")
            if date_value:
                record["Fecha_de_solicitud_del_cliente"] = date_value
            if fields.get("observaciones"):
                record["Observaciones"] = fields["observaciones"]
            serialized = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            outbox.encrypted_payload = encrypt(serialized)
            outbox.payload_checksum = hashlib.sha256(serialized.encode()).hexdigest()
            outbox.save(update_fields=("encrypted_payload", "payload_checksum", "updated_at"))
        except (TypeError, ValueError, json.JSONDecodeError):
            logger.warning("response_novelty_task_sync_failed request_id=%s response_id=%s", public_id, response.pk)
    messages.success(request, "Novedad corregida localmente. La respuesta original permanece disponible para auditoría.")
    return redirect("cotizacion_colectivos:request_detail", public_id=public_id)


@never_cache
@require_http_methods(["POST"])
def request_publish_task(request, public_id, outbox_id):
    if not has_internal_permission(request, "approve_requests"):
        return permission_denied_response()
    item = get_object_or_404(SolicitudColectivo, public_id=public_id)
    outbox = get_object_or_404(ColectivosTaskOutbox, pk=outbox_id, request=item)
    if outbox.status == outbox.Status.PUBLISHED:
        messages.info(request, "La Tarea ya fue publicada.")
    else:
        try:
            if item.request_type == "COTIZACION":
                options = task_responsible_options(collective_only=True)
                selected = next(
                    (option for option in options if option.actual_value == str(request.POST.get("responsible") or "").strip()),
                    None,
                )
                if selected is None:
                    raise ValidationError("Seleccione un responsable válido del área Colectivos.")
                responsible_email = resolve_task_responsible_email(selected)
                record = json.loads(decrypt(outbox.encrypted_payload))
                record["Responsable"] = selected.actual_value
                record["Correo_responsable"] = responsible_email
                serialized = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                outbox.encrypted_payload = encrypt(serialized)
                outbox.payload_checksum = hashlib.sha256(serialized.encode()).hexdigest()
                outbox.save(update_fields=("encrypted_payload", "payload_checksum", "updated_at"))
            publish_task_outbox(outbox.pk)
        except (ValidationError, ColectivosServiceError) as exc:
            messages.error(request, str(exc))
            return redirect("cotizacion_colectivos:request_detail", public_id=item.public_id)
        outbox.refresh_from_db()
        if outbox.status == outbox.Status.PUBLISHED:
            messages.success(request, "Tarea publicada correctamente en Zoho Sandbox.")
        elif outbox.status == outbox.Status.RECONCILE:
            messages.warning(request, "Resultado incierto: requiere conciliación antes de reintentar.")
        elif not getattr(settings, "COLECTIVOS_TASK_PUBLISH_ENABLED", False):
            messages.warning(request, "La publicación de Tareas está deshabilitada por configuración.")
        else:
            messages.error(request, "La Tarea no pudo publicarse; revise el estado del expediente.")
    return redirect("cotizacion_colectivos:request_detail", public_id=item.public_id)


def _access_status_display(access) -> str:
    if access is None:
        return "No generado"
    if access.status in {access.Status.ACTIVE, access.Status.VERIFIED} and access.expires_at <= timezone.now():
        return access.Status.EXPIRED.label
    return access.get_status_display()


@never_cache
@require_http_methods(["POST"])
def request_edit(request, public_id):
    item = get_object_or_404(SolicitudColectivo, public_id=public_id)
    if not has_internal_permission(request, "edit_requests"):
        return permission_denied_response()
    form = RequestEditForm(request.POST, public_access=public_internal_access_enabled())
    if not form.is_valid():
        return HttpResponse("Los datos del borrador no son válidos.", status=400)
    try:
        actor = get_internal_actor(request, create=True)
        update_draft_request(request=item, actor=actor, assigned_to=(actor if public_internal_access_enabled() else form.cleaned_data["assigned_to"]), deadline=form.cleaned_data["deadline"], internal_notes=form.cleaned_data["internal_notes"])
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
        regenerate_request_snapshot(request=item, actor=get_internal_actor(request, create=True))
    except (ColectivosServiceError, ValidationError):
        return HttpResponse("No fue posible regenerar el snapshot.", status=400)
    audit(request, "UPDATE", reason="Snapshot de expediente Colectivos regenerado.", metadata={"request_id": item.public_id})
    return redirect("cotizacion_colectivos:request_detail", public_id=item.public_id)


@never_cache
@require_http_methods(["GET", "POST"])
def request_external_access(request, public_id, regenerate=False):
    item = get_object_or_404(SolicitudColectivo.objects.select_related("assigned_to"), public_id=public_id)
    _attach_request_operational_context(item)
    permission = "regenerate_external_access" if regenerate else "generate_external_access"
    if not has_internal_permission(request, permission):
        return permission_denied_response()
    form = ExternalAccessPrepareForm(request.POST or None, initial={"deadline": item.deadline})
    error = ""
    if request.method == "POST" and form.is_valid():
        send_now = form.cleaned_data["send_now"]
        if send_now and not has_internal_permission(request, "send_requests"):
            return permission_denied_response()
        try:
            with transaction.atomic():
                item.deadline = form.cleaned_data["deadline"]
                item.save(update_fields=("deadline", "updated_at"))
                generated = generate_access(
                    request=item, actor=get_internal_actor(request, create=True), recipient=form.cleaned_data["recipient"],
                    contact_name=form.cleaned_data["contact_name"], intro=form.cleaned_data["intro"],
                    instructions=form.cleaned_data["instructions"], regenerate=regenerate,
                )
                if send_now:
                    send_invitation(generated)
        except ExternalAccessError as exc:
            error = exc.messages[0] if exc.messages else "No fue posible preparar el acceso."
        else:
            audit(request, "UPDATE", reason="Acceso externo de Colectivos generado.", metadata={"request_id": item.public_id, "regenerated": regenerate, "invitation_sent": send_now})
            return render(request, "cotizacion_colectivos/external_access_form.html", {"item": item, "form": None, "generated_url": generated.url, "invitation_sent": send_now, "regenerate": regenerate, **_environment_context()})
    return render(request, "cotizacion_colectivos/external_access_form.html", {"item": item, "form": form, "error": error, "regenerate": regenerate, "can_send": has_internal_permission(request, "send_requests"), **_environment_context()})


@never_cache
@require_http_methods(["POST"])
def request_external_access_email(request, public_id):
    if not has_internal_permission(request, "send_requests"):
        return permission_denied_response()
    item = get_object_or_404(SolicitudColectivo, public_id=public_id)
    form = OptionalAccessEmailForm(request.POST)
    token = request.POST.get("access_token", "")
    try:
        if not form.is_valid():
            raise ExternalAccessError("El correo no es válido.")
        access = resolve_token(token)
        if access.request_id != item.pk:
            raise ExternalAccessError("El acceso no corresponde a la solicitud.")
        generated = GeneratedAccess(
            access=access,
            token=token,
            url=f"{settings.COLECTIVOS_EXTERNAL_BASE_URL}/solicitudes/colectivos/externa/{token}/",
        )
        send_optional_invitation(generated=generated, recipient=form.cleaned_data["recipient"])
    except ExternalAccessError:
        EventoSolicitudColectivo.objects.create(
            request=item,
            event_type="EMAIL_ERROR",
            safe_metadata={"category": "delivery", "purpose": "optional_invitation"},
        )
        return HttpResponse("No fue posible enviar el correo. El enlace sigue vigente.", status=400)
    audit(request, "UPDATE", reason="Enlace externo de Colectivos enviado por correo.", metadata={"request_id": item.public_id})
    return render(request, "cotizacion_colectivos/generated_access.html", {
        "item": item,
        "generated_url": generated.url,
        "generated_token": token,
        "expires_at": access.expires_at,
        "email_form": OptionalAccessEmailForm(),
        "invitation_sent": True,
        **_environment_context(),
    })


@never_cache
@require_http_methods(["POST"])
def request_external_access_quick_regenerate(request, public_id):
    if not has_internal_permission(request, "regenerate_external_access"):
        return permission_denied_response()
    item = get_object_or_404(SolicitudColectivo, public_id=public_id)
    try:
        generated = generate_access(
            request=item,
            actor=get_internal_actor(request, create=True),
            regenerate=True,
        )
    except ExternalAccessError:
        return HttpResponse("No fue posible regenerar el enlace.", status=400)
    audit(request, "UPDATE", reason="Enlace externo de Colectivos regenerado.", metadata={"request_id": item.public_id})
    return render(request, "cotizacion_colectivos/generated_access.html", {
        "item": item,
        "generated_url": generated.url,
        "generated_token": generated.token,
        "expires_at": generated.access.expires_at,
        "email_form": OptionalAccessEmailForm(),
        "regenerated": True,
        **_environment_context(),
    })


@never_cache
@require_http_methods(["POST"])
def request_external_access_revoke(request, public_id):
    item = get_object_or_404(SolicitudColectivo, public_id=public_id)
    if not has_internal_permission(request, "revoke_external_access"):
        return permission_denied_response()
    try:
        revoke_access(request=item, actor=get_internal_actor(request, create=True))
    except ExternalAccessError:
        return HttpResponse("No existe un acceso vigente para revocar.", status=400)
    audit(request, "UPDATE", reason="Acceso externo de Colectivos revocado.", metadata={"request_id": item.public_id})
    return redirect("cotizacion_colectivos:request_detail", public_id=item.public_id)


@never_cache
@require_http_methods(["POST"])
def request_novelties_template(request, public_id):
    item = get_object_or_404(SolicitudColectivo, public_id=public_id)
    if not has_internal_permission(request, "export_excel"):
        return permission_denied_response()
    response = HttpResponse(build_novelties_template(item), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f'attachment; filename="{download_filename("Novedades", origin=item.client_label, request_id=item.public_id)}"'
    response["Cache-Control"] = "no-store, private"
    return response


@never_cache
@require_http_methods(["GET", "POST"])
def response_review(request, public_id, version):
    item = get_object_or_404(SolicitudColectivo, public_id=public_id)
    _attach_request_operational_context(item)
    response = get_object_or_404(
        RespuestaSolicitudColectivo.objects.prefetch_related(
            Prefetch(
                "changes",
                queryset=CambioSolicitudColectivo.objects.select_related("policy", "original_record").prefetch_related("reviews"),
            ),
            "attachments",
        ),
        request=item,
        version=version,
    )
    if not has_internal_permission(request, "review_responses"):
        return permission_denied_response()
    policy_references = {
        policy.pk: row["reference"]
        for policy, row in zip(item.policies.all(), item.operational_policies)
    }
    for change in response.changes.all():
        if change.policy_id:
            change.policy.operational_reference = policy_references.get(
                change.policy_id, change.policy.masked_policy_reference,
            )
    error = ""
    if request.method == "POST":
        decisions = {}
        for change in response.changes.all():
            decision = request.POST.get(f"decision_{change.pk}", "")
            if decision:
                decisions[change.pk] = {"decision": decision, "approved_value": request.POST.get(f"approved_{change.pk}", ""), "internal_observation": request.POST.get(f"internal_{change.pk}", ""), "client_observation": request.POST.get(f"client_{change.pk}", "")}
        try:
            reviewer = get_internal_actor(request, create=True)
            record_reviews(response=response, reviewer=reviewer, decisions=decisions)
            action = request.POST.get("finalize", "")
            if action:
                required = "approve_responses" if action == "approve" else "request_corrections"
                if not has_internal_permission(request, required):
                    return permission_denied_response()
                finalize_review(response=response, reviewer=reviewer, action=action)
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
    response["Content-Disposition"] = f'attachment; filename="{download_filename("Comparativo", origin=item.client_label, request_id=item.public_id, version=version)}"'
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
    response["Content-Disposition"] = f'attachment; filename="{download_filename("Respuesta", origin=item.client_label, request_id=item.public_id, version=version)}"'
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
    response["Content-Disposition"] = f'attachment; filename="{download_filename("Consolidado_Aprobado", origin=item.client_label, request_id=item.public_id, version=version)}"'
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
    response = FileResponse(
        target.open("rb"), content_type=attachment.detected_mime,
        as_attachment=True, filename=attachment.safe_original_name or f"soporte{attachment.extension}",
    )
    response["Cache-Control"] = "no-store, private"
    return response


@never_cache
@require_http_methods(["POST"])
def request_transition(request, public_id):
    item = get_object_or_404(SolicitudColectivo, public_id=public_id)
    form = RequestTransitionForm(request.POST)
    if not form.is_valid():
        messages.warning(request, "Esta transición no está disponible para el estado actual.")
        logger.warning(
            "colectivos_transition operation=status_change result=rejected "
            "category=invalid_target request_id=%s current_status=%s",
            item.public_id, item.status,
        )
        return redirect("cotizacion_colectivos:request_detail", public_id=item.public_id)
    target = form.cleaned_data["target"]
    permission = _transition_permission(target)
    if not has_internal_permission(request, permission):
        return permission_denied_response()
    try:
        transition_request(request=item, target=target, actor=get_internal_actor(request, create=True))
    except ValidationError:
        messages.warning(request, "Esta transición no está disponible para el estado actual.")
        logger.warning(
            "colectivos_transition operation=status_change result=rejected "
            "category=domain_transition request_id=%s current_status=%s target=%s",
            item.public_id, item.status, target,
        )
        return redirect("cotizacion_colectivos:request_detail", public_id=item.public_id)
    audit(request, "UPDATE", reason="Estado de expediente Colectivos actualizado.", metadata={"request_id": item.public_id, "target": target})
    return redirect("cotizacion_colectivos:request_detail", public_id=item.public_id)


@never_cache
@require_http_methods(["GET"])
def notification_list(request):
    if not has_internal_permission(request, "manage_notifications"):
        return permission_denied_response()
    return redirect("cotizacion_colectivos:request_list")


@never_cache
@require_http_methods(["POST"])
def notification_read(request, notification_id):
    if not has_internal_permission(request, "manage_notifications"):
        return permission_denied_response()
    item = get_object_or_404(NotificacionColectivos, pk=notification_id, user=get_internal_actor(request, create=True))
    for attempt in range(2):
        try:
            NotificacionColectivos.objects.filter(pk=item.pk, read_at__isnull=True).update(read_at=timezone.now())
            break
        except OperationalError as exc:
            locked = "database is locked" in str(exc).casefold()
            if locked and attempt == 0:
                time.sleep(0.05)
                continue
            logger.warning(
                "colectivos_notification application=cotizacion_colectivos operation=mark_read "
                "result=skipped category=%s notification_id=%s",
                "sqlite_locked" if locked else "database_unavailable",
                item.pk,
            )
            break
    if item.notification_type == "CLIENT_RESPONSE":
        response = item.request.responses.filter(
            status=RespuestaSolicitudColectivo.Status.SUBMITTED,
        ).order_by("-version").first()
        if response is not None:
            return redirect(
                "cotizacion_colectivos:response_detail",
                public_id=item.request.public_id,
                version=response.version,
            )
    return redirect("cotizacion_colectivos:request_list")


@never_cache
@require_http_methods(["POST"])
def individual_notification_read(request, notification_id):
    if not has_internal_permission(request, "manage_notifications"):
        return permission_denied_response()
    item = get_object_or_404(
        NotificacionCotizacionIndividual,
        pk=notification_id,
        user=get_internal_actor(request, create=True),
    )
    if item.read_at is None:
        item.read_at = timezone.now()
        item.save(update_fields=("read_at",))
    return redirect(
        "cotizacion_colectivos:individual_expedient",
        token=sign_receipt(item.quotation.public_id),
    )


@never_cache
@require_http_methods(["POST"])
def individual_accept(request, token):
    if not has_internal_permission(request, "approve_responses"):
        return permission_denied_response()
    try:
        public_id = unsign_receipt(token)
        quotation = CotizacionIndividual.objects.get(public_id=public_id)
    except (signing.BadSignature, CotizacionIndividual.DoesNotExist, ValueError) as exc:
        raise Http404("Respuesta no encontrada") from exc
    if _individual_acceptance_status(quotation) == "rejected":
        messages.warning(request, "La cotización está rechazada; debe reactivarse antes de aceptarla.")
        return redirect("cotizacion_colectivos:individual_expedient", token=sign_receipt(quotation.public_id))
    actor = request.user if request.user.is_authenticated else get_internal_actor(request, create=True)
    quotation = accept_individual_quotation(quotation=quotation, actor=actor)
    try:
        resolve_accepted_person(quotation=quotation)
        entity_result = resolve_mobility_entities(quotation=quotation) if quotation.branch_slug == "movilidad" else resolve_common_people_entities(quotation=quotation)
        if entity_result and entity_result.get("status") == "error":
            messages.warning(request, "Cotización aceptada; no fue posible resolver las entidades de Movilidad.")
        else:
            messages.success(request, "Cotización aceptada; se verificaron las entidades en Zoho.")
    except Exception:
        messages.warning(request, "Cotización aceptada; no fue posible completar la consulta de persona.")
    return redirect("cotizacion_colectivos:individual_expedient", token=sign_receipt(quotation.public_id))


@never_cache
@require_http_methods(["POST"])
def individual_reject(request, token):
    if not has_internal_permission(request, "approve_responses"):
        return permission_denied_response()
    try:
        public_id = unsign_receipt(token)
        actor = request.user if request.user.is_authenticated else get_internal_actor(request, create=True)
        with transaction.atomic():
            quotation = CotizacionIndividual.objects.select_for_update().get(public_id=public_id)
            status = _individual_acceptance_status(quotation)
            if status != "pending":
                raise ValidationError("La cotización ya tiene una decisión y no puede rechazarse.")
            metadata = dict(quotation.safe_metadata or {})
            acceptance = dict(metadata.get("acceptance") or {})
            acceptance.update({"status": "rejected", "rejected_at": timezone.now().isoformat(), "rejected_by": int(actor.pk) if actor is not None else None})
            metadata["acceptance"] = acceptance
            quotation.safe_metadata = metadata
            quotation.save(update_fields=("safe_metadata",))
            audit(request, "UPDATE", reason="Cotización Individual rechazada.", metadata={"quotation_id": str(quotation.public_id), "status": "rejected"})
        messages.success(request, "La cotización fue rechazada y quedó conservada para consulta y trazabilidad.")
    except (signing.BadSignature, CotizacionIndividual.DoesNotExist, ValueError):
        raise Http404("Respuesta no encontrada")
    except ValidationError as exc:
        messages.warning(request, str(exc))
    return redirect("cotizacion_colectivos:individual_expedient", token=token)


@never_cache
@require_http_methods(["POST"])
def individual_reactivate(request, token):
    if not has_internal_permission(request, "approve_responses"):
        return permission_denied_response()
    try:
        public_id = unsign_receipt(token)
        actor = request.user if request.user.is_authenticated else get_internal_actor(request, create=True)
        with transaction.atomic():
            quotation = CotizacionIndividual.objects.select_for_update().get(public_id=public_id)
            if _individual_acceptance_status(quotation) != "rejected":
                raise ValidationError("Sólo una cotización rechazada puede reactivarse.")
            metadata = dict(quotation.safe_metadata or {})
            acceptance = dict(metadata.get("acceptance") or {})
            acceptance.update({"status": "pending", "reactivated_at": timezone.now().isoformat(), "reactivated_by": int(actor.pk) if actor is not None else None})
            metadata["acceptance"] = acceptance
            quotation.safe_metadata = metadata
            quotation.save(update_fields=("safe_metadata",))
            audit(request, "UPDATE", reason="Cotización Individual reactivada.", metadata={"quotation_id": str(quotation.public_id), "status": "pending"})
        messages.success(request, "La cotización fue reactivada y quedó pendiente de decisión.")
    except (signing.BadSignature, CotizacionIndividual.DoesNotExist, ValueError):
        raise Http404("Respuesta no encontrada")
    except ValidationError as exc:
        messages.warning(request, str(exc))
    return redirect("cotizacion_colectivos:individual_expedient", token=token)


def _individual_acceptance_status(quotation):
    metadata = quotation.safe_metadata if isinstance(quotation.safe_metadata, dict) else {}
    acceptance = metadata.get("acceptance") if isinstance(metadata.get("acceptance"), dict) else {}
    return str(acceptance.get("status") or "pending").strip().lower()


def _ensure_individual_can_operate_zoho(quotation):
    if _individual_acceptance_status(quotation) != "accepted":
        raise ValidationError("La cotización debe estar aceptada para operar en Zoho.")


def _ensure_individual_can_manage_task(quotation):
    """Task assignment/publication predates the decision gate.

    Pending responses may still have a COTIZACION outbox that needs a
    responsible or controlled publication.  Rejected quotations remain
    closed to any new operation.
    """
    if _individual_acceptance_status(quotation) == "rejected":
        raise ValidationError("La cotización rechazada no permite gestionar la Task.")


@never_cache
@require_http_methods(["POST"])
def individual_create_person(request, token):
    if not has_internal_permission(request, "approve_responses"):
        return permission_denied_response()
    try:
        public_id = unsign_receipt(token)
        quotation = CotizacionIndividual.objects.get(public_id=public_id)
        _ensure_individual_can_operate_zoho(quotation)
        resolve_accepted_person(quotation=quotation)
        if quotation.branch_slug == "movilidad":
            resolve_mobility_entities(quotation=quotation)
        else:
            resolve_common_people_entities(quotation=quotation)
        quotation.refresh_from_db(fields=("safe_metadata",))
        people = quotation.safe_metadata.get("people_lookup") or ()
        entities = quotation.safe_metadata.get("zoho_entities") or {}
        if entities.get("people"):
            people = entities.get("people")
        document_hint = str(request.POST.get("document") or (people[0].get("document") if people else "")).strip()
        selected = next((item for item in people if str(item.get("document") or "") == document_hint), None)
        if selected and (selected.get("created") or selected.get("contact_id") or selected.get("remote_id")):
            raise ValidationError("El afiliado/asegurado ya está resuelto en Zoho; no se permite crear otro Contact.")
        data = dict(selected.get("candidate") or {}) if isinstance(selected, dict) else {}
        if not data:
            raise ValidationError("No se encontró un candidato Persona válido para crear.")
        result = get_contacts_publisher(
            profile=str(getattr(settings, "ZOHO_ACTIVE_PROFILE", "sandbox")),
            confirmation=configured_confirmation(
                "contact", str(getattr(settings, "ZOHO_ACTIVE_PROFILE", "sandbox")),
                legacy_setting="COLECTIVOS_CONTACT_WRITE_CONFIRMATION",
            ),
        ).create(data, status="Cliente")
        metadata = dict(quotation.safe_metadata or {})
        contact_id = result["record_id"]
        created_at = timezone.now().isoformat()
        people = [dict(item) for item in (metadata.get("people_lookup") or ()) if isinstance(item, dict)]
        selected_document = str(data.get("N_mero_de_ID") or document_hint).strip()
        for person in people:
            if str(person.get("document") or "") == selected_document:
                person.update({
                    "status": "found", "created": True, "created_at": created_at,
                    "contact_id": contact_id, "remote_id": contact_id,
                    "detail_token": sign_record_id(contact_id, "person"),
                    "has_complete_data": True, "missing_fields": [],
                })
        metadata["people_lookup"] = people
        metadata["person_lookup"] = next(
            (item for item in people if str(item.get("document") or "") == selected_document),
            {"status": "found", "created": True, "contact_id": contact_id,
             "created_at": created_at, "detail_token": sign_record_id(contact_id, "person")},
        )
        # Keep the same operational result in the entity snapshot consumed by
        # the Mobility workspace.  The client response remains encrypted and
        # untouched; subsequent GETs can render the confirmed CREATE without
        # depending on an immediate READ reconciliation.
        entity_people = [dict(item) for item in (entities.get("people") or ()) if isinstance(item, dict)]
        for person in entity_people:
            if str(person.get("document") or person.get("candidate", {}).get("N_mero_de_ID") or "") == selected_document:
                person.update({
                    "status": "found", "created": True, "created_at": created_at,
                    "contact_id": contact_id, "remote_id": contact_id,
                    "has_complete_data": True, "missing_fields": [],
                })
        if entity_people:
            entities["people"] = entity_people
            metadata["zoho_entities"] = entities
        quotation.safe_metadata = metadata
        quotation.save(update_fields=("safe_metadata",))
        entity_label = "Asegurado" if str(selected.get("role") or "").lower().startswith("asegur") else "Afiliado"
        try:
            publish_pending_for_person(
                quotation=quotation, document=selected_document, record_id=contact_id,
                owner_key=str(selected.get("owner_key") or ""),
            )
            messages.success(request, f"{entity_label} creado y documento procesado en Zoho Sandbox.")
        except (IndividualAttachmentBlocked, IndividualAttachmentUncertain, ValidationError, ZohoError) as exc:
            messages.warning(request, f"{entity_label} creado. El documento requiere atención: {exc}")
    except (signing.BadSignature, CotizacionIndividual.DoesNotExist, ValueError, json.JSONDecodeError):
        raise Http404("Respuesta no encontrada")
    except ContactPublicationUncertain:
        messages.warning(request, "El resultado de la creación no pudo confirmarse. Requiere conciliación.")
    except ZohoError:
        messages.warning(request, "Zoho no pudo crear la persona. Revise los datos o intente nuevamente.")
    except (ContactPublishingDisabled, ContactPublicationRejected, ValidationError) as exc:
        messages.warning(request, str(exc))
    return redirect("cotizacion_colectivos:individual_expedient", token=token)


@never_cache
@require_http_methods(["POST"])
def individual_complete_person(request, token):
    """Store operational person corrections separately from the client response."""
    if not has_internal_permission(request, "approve_responses"):
        return permission_denied_response()
    try:
        public_id = unsign_receipt(token)
        quotation = CotizacionIndividual.objects.get(public_id=public_id)
        _ensure_individual_can_operate_zoho(quotation)
        try:
            identification_choices = identification_choice_pairs()
        except CatalogUnavailable as exc:
            messages.warning(request, str(exc))
            return redirect("cotizacion_colectivos:individual_expedient", token=token)
        form = PersonCompletionForm(request.POST, identification_choices=identification_choices)
        if not form.is_valid():
            for error in form.errors.values():
                messages.warning(request, " ".join(str(item) for item in error))
            return redirect("cotizacion_colectivos:individual_expedient", token=token)
        data = form.cleaned_data
        payload = json.loads(decrypt(quotation.encrypted_payload))
        fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
        document = str(data.get("document") or fields.get("requester_document") or "").strip()
        if not document:
            messages.warning(request, "Indique el número de identificación para guardar los datos.")
            return redirect("cotizacion_colectivos:individual_expedient", token=token)
        metadata = dict(quotation.safe_metadata or {})
        corrections = dict(metadata.get("person_corrections") or {})
        corrections[document] = {
            "label": "Persona completada por analista",
            "First_Name": str(data.get("first_name") or "").strip(),
            "Last_Name": str(data.get("last_name") or "").strip(),
            "Tipo_ID": str(data.get("id_type") or "").strip(),
            "N_mero_de_ID": document,
            "Email": str(data.get("email") or "").strip(),
            "Mobile": str(data.get("mobile") or "").strip(),
            "Phone": str(data.get("phone") or "").strip(),
            "Date_of_Birth": data.get("birth_date").isoformat() if data.get("birth_date") else "",
            **({"Tratamiento_de_datos": data["consent"]} if data.get("consent") in {"Si", "No"} else {}),
            "updated_at": timezone.now().isoformat(),
            "updated_by": int(get_internal_actor(request, create=True).pk),
        }
        metadata["person_corrections"] = corrections
        quotation.safe_metadata = metadata
        quotation.save(update_fields=("safe_metadata",))
        resolve_accepted_person(quotation=quotation)
        if quotation.branch_slug == "movilidad":
            resolve_mobility_entities(quotation=quotation)
        messages.success(request, "Datos de la persona guardados; se volvió a validar en Zoho.")
    except (signing.BadSignature, CotizacionIndividual.DoesNotExist, ValueError, json.JSONDecodeError):
        raise Http404("Respuesta no encontrada")
    except ValidationError as exc:
        messages.warning(request, str(exc))
    return redirect("cotizacion_colectivos:individual_expedient", token=token)


def _individual_entity_quotation(token):
    return CotizacionIndividual.objects.get(public_id=unsign_receipt(token))


@never_cache
@require_http_methods(["POST"])
def individual_update_entity(request, token, entity, vehicle_index):
    if not has_internal_permission(request, "approve_responses"):
        return permission_denied_response()
    if entity not in {"risk", "subrisk"}:
        raise Http404("Entidad no encontrada")
    try:
        quotation = _individual_entity_quotation(token)
        _ensure_individual_can_operate_zoho(quotation)
        metadata = dict(quotation.safe_metadata or {})
        entities = metadata.get("zoho_entities") if isinstance(metadata.get("zoho_entities"), dict) else {}
        bucket = list(entities.get("risks" if entity == "risk" else "subrisks") or [])
        if vehicle_index < 0 or vehicle_index >= len(bucket):
            raise ValidationError("Vehículo no encontrado.")
        allowed = {"Name", "Placa_del_vehiculo", "Marca_Tipo_Caracter_sticas", "Modelo", "Clase", "Ciudad", "Tipo_de_uso"} if entity == "risk" else {"Name", "Ramo", "Parentesco", "Fecha_ingreso_riesgo", "Estado", "Plan"}
        if entity == "risk":
            proposed_class = str(request.POST.get("Clase") or "").strip()
            proposed_use = str(request.POST.get("Tipo_de_uso") or "").strip()
            if proposed_class and proposed_class not in VEHICLE_CLASS_CHOICES:
                raise ValidationError("Seleccione una Clase válida.")
            if proposed_use and proposed_use not in VEHICLE_USE_CHOICES:
                raise ValidationError("Seleccione un Uso válido.")
            payload = json.loads(decrypt(quotation.encrypted_payload))
            rows = (payload.get("groups") or {}).get("vehicles") or []
            source_row = rows[vehicle_index] if 0 <= vehicle_index < len(rows) and isinstance(rows[vehicle_index], dict) else {}
            if str(source_row.get("zero_km") or "").strip() == "No" and not str(request.POST.get("Placa_del_vehiculo") or "").strip():
                raise ValidationError("La placa es obligatoria cuando el vehículo no es 0 km.")
        corrections = dict(metadata.get("zoho_entity_corrections") or {})
        corrections[f"{entity}:{vehicle_index}"] = {
            key: str(request.POST.get(key) or "").strip() for key in allowed if key in request.POST
        }
        corrections[f"{entity}:{vehicle_index}"]["updated_at"] = timezone.now().isoformat()
        metadata["zoho_entity_corrections"] = corrections
        quotation.safe_metadata = metadata
        quotation.save(update_fields=("safe_metadata",))
        resolve_mobility_entities(quotation=quotation)
        messages.success(request, "Datos propuestos guardados y nuevamente validados.")
    except (signing.BadSignature, CotizacionIndividual.DoesNotExist, ValueError, json.JSONDecodeError):
        raise Http404("Respuesta no encontrada")
    except ValidationError as exc:
        messages.warning(request, str(exc))
    return redirect("cotizacion_colectivos:individual_expedient", token=token)


@never_cache
@require_http_methods(["POST"])
def individual_create_risk(request, token, vehicle_index):
    if not has_internal_permission(request, "approve_responses"):
        return permission_denied_response()
    try:
        quotation = _individual_entity_quotation(token)
        _ensure_individual_can_operate_zoho(quotation)
        entities = (quotation.safe_metadata or {}).get("zoho_entities") or {}
        risks = list(entities.get("risks") or [])
        if vehicle_index < 0 or vehicle_index >= len(risks):
            raise ValidationError("Vehículo no encontrado.")
        item = risks[vehicle_index]
        if item.get("status") != "not_found":
            raise ValidationError("El Riesgo requiere resolución antes de crear.")
        active_profile = str(getattr(settings, "ZOHO_ACTIVE_PROFILE", "sandbox"))
        result = create_sandbox_risk(
            item.get("candidate") or {}, profile=active_profile,
            operational=True,
            confirmation=configured_confirmation(
                "risk", active_profile,
                legacy_setting="COLECTIVOS_RISK_WRITE_CONFIRMATION",
            ),
        )
        created_at = timezone.now().isoformat()
        item.update({"status": "created", "created": True, "remote_id": result["record_id"], "risk_id": result["record_id"], "created_at": created_at})
        for key in ("error", "error_code"):
            item.pop(key, None)
        metadata = dict(quotation.safe_metadata or {}); metadata["zoho_entities"]["risks"] = risks
        quotation.safe_metadata = metadata; quotation.save(update_fields=("safe_metadata",))
        try:
            publish_pending_for_risk(quotation=quotation, vehicle_index=vehicle_index, record_id=result["record_id"])
            messages.success(request, "Riesgo creado y documento procesado en Zoho Sandbox.")
        except (IndividualAttachmentBlocked, IndividualAttachmentUncertain, ValidationError, ZohoError) as exc:
            messages.warning(request, f"Riesgo creado. El documento requiere atención: {exc}")
    except (RiskPublishingDisabled, RiskPublicationRejected, ValidationError) as exc:
        code = "BLOCKED" if isinstance(exc, RiskPublishingDisabled) else "REJECTED" if isinstance(exc, RiskPublicationRejected) else "VALIDATION"
        if 'item' in locals():
            error_message = (
                "La creación del Riesgo está bloqueada por la configuración de escritura de Sandbox."
                if code == "BLOCKED" else str(exc)
            )
            item.update({"error_code": code, "error": error_message})
            metadata = dict(quotation.safe_metadata or {})
            entities = dict(metadata.get("zoho_entities") or {})
            current_risks = list(entities.get("risks") or [])
            if 0 <= vehicle_index < len(current_risks):
                current_risks[vehicle_index] = item
                entities["risks"] = current_risks
                metadata["zoho_entities"] = entities
                quotation.safe_metadata = metadata
                quotation.save(update_fields=("safe_metadata",))
            logger.warning("individual_create_risk result=%s module=Riesgos vehicle_index=%s", code, vehicle_index)
        messages.warning(request, item.get("error") if 'item' in locals() else str(exc))
    except RiskPublicationUncertain:
        if 'item' in locals():
            item.update({"error_code": "RECONCILE_REQUIRED", "error": "Resultado incierto; requiere conciliación manual."})
            metadata = dict(quotation.safe_metadata or {})
            entities = dict(metadata.get("zoho_entities") or {})
            current_risks = list(entities.get("risks") or [])
            if 0 <= vehicle_index < len(current_risks):
                current_risks[vehicle_index] = item
                entities["risks"] = current_risks
                metadata["zoho_entities"] = entities
                quotation.safe_metadata = metadata
                quotation.save(update_fields=("safe_metadata",))
            logger.warning("individual_create_risk result=RECONCILE_REQUIRED module=Riesgos vehicle_index=%s", vehicle_index)
        messages.warning(request, "El resultado del Riesgo requiere conciliación en Zoho.")
    except ZohoError:
        if 'item' in locals():
            item.update({"error_code": "ZOHO_ERROR", "error": "Zoho no pudo crear el Riesgo."})
            metadata = dict(quotation.safe_metadata or {})
            entities = dict(metadata.get("zoho_entities") or {})
            current_risks = list(entities.get("risks") or [])
            if 0 <= vehicle_index < len(current_risks):
                current_risks[vehicle_index] = item
                entities["risks"] = current_risks
                metadata["zoho_entities"] = entities
                quotation.safe_metadata = metadata
                quotation.save(update_fields=("safe_metadata",))
            logger.warning("individual_create_risk result=ERROR module=Riesgos vehicle_index=%s", vehicle_index)
        messages.warning(request, "Zoho no pudo crear el Riesgo. Revise los datos y el estado operativo.")
    except (signing.BadSignature, CotizacionIndividual.DoesNotExist, ValueError, json.JSONDecodeError):
        raise Http404("Respuesta no encontrada")
    return redirect(f"{reverse('cotizacion_colectivos:individual_expedient', kwargs={'token': token})}#risk-{vehicle_index}")


@never_cache
@require_http_methods(["POST"])
def individual_create_subrisk(request, token, vehicle_index):
    if not has_internal_permission(request, "approve_responses"):
        return permission_denied_response()
    try:
        quotation = _individual_entity_quotation(token)
        _ensure_individual_can_operate_zoho(quotation)
        entities = (quotation.safe_metadata or {}).get("zoho_entities") or {}
        subrisks = list(entities.get("subrisks") or [])
        if vehicle_index < 0 or vehicle_index >= len(subrisks):
            raise ValidationError("Vehículo no encontrado.")
        item = subrisks[vehicle_index]
        if item.get("status") != "not_found" or item.get("created") or item.get("remote_id") or item.get("riesgos1_id"):
            raise ValidationError("La asociación requiere resolución antes de crear.")
        active_profile = str(getattr(settings, "ZOHO_ACTIVE_PROFILE", "sandbox"))
        result = create_mobility_subrisk_sandbox(
            item.get("candidate") or {}, profile=active_profile,
            confirmation=configured_confirmation(
                "subrisk", active_profile,
                legacy_setting="COLECTIVOS_SUBRISK_WRITE_CONFIRMATION",
            ),
            operational=True,
        )
        confirmed_id = str(result["record_id"])
        item.update({"status": "created", "created": True, "remote_id": confirmed_id, "riesgos1_id": confirmed_id, "created_at": timezone.now().isoformat()})
        metadata = dict(quotation.safe_metadata or {}); metadata["zoho_entities"]["subrisks"] = subrisks
        quotation.safe_metadata = metadata; quotation.save(update_fields=("safe_metadata",))
        messages.success(request, "Riesgo asociado a la póliza en Zoho Sandbox.")
    except (SubriskPublishingDisabled, SubriskPublicationRejected, ValidationError) as exc:
        item["last_error"] = str(exc)[:180]
        item["last_error_code"] = exc.__class__.__name__[:40]
        metadata = dict(quotation.safe_metadata or {})
        metadata.setdefault("zoho_entities", {})["subrisks"] = subrisks
        quotation.safe_metadata = metadata
        quotation.save(update_fields=("safe_metadata",))
        messages.warning(request, str(exc))
    except SubriskPublicationUncertain:
        item["last_error"] = "Resultado incierto; requiere conciliación en Zoho."
        item["last_error_code"] = "UNCERTAIN"
        metadata = dict(quotation.safe_metadata or {})
        metadata.setdefault("zoho_entities", {})["subrisks"] = subrisks
        quotation.safe_metadata = metadata
        quotation.save(update_fields=("safe_metadata",))
        messages.warning(request, "La asociación requiere conciliación en Zoho.")
    except ZohoError:
        item["last_error"] = "Zoho no pudo asociar el riesgo."
        item["last_error_code"] = "ZOHO_ERROR"
        metadata = dict(quotation.safe_metadata or {})
        metadata.setdefault("zoho_entities", {})["subrisks"] = subrisks
        quotation.safe_metadata = metadata
        quotation.save(update_fields=("safe_metadata",))
        messages.warning(request, "Zoho no pudo asociar el Riesgo. Revise los datos y el estado operativo.")
    except (signing.BadSignature, CotizacionIndividual.DoesNotExist, ValueError, json.JSONDecodeError):
        raise Http404("Respuesta no encontrada")
    return redirect("cotizacion_colectivos:individual_expedient", token=token)


@never_cache
@require_http_methods(["POST"])
def individual_update_responsible(request, token):
    if not has_internal_permission(request, "approve_responses"):
        return permission_denied_response()
    try:
        quotation = CotizacionIndividual.objects.get(public_id=unsign_receipt(token))
        _ensure_individual_can_manage_task(quotation)
        options = task_responsible_options(collective_only=True)
        option = next((item for item in options if item.actual_value == str(request.POST.get("responsible") or "").strip()), None)
        if option is None:
            raise ValidationError("Seleccione un responsable válido.")
        try:
            email = resolve_task_responsible_email(option)
        except ValidationError:
            # El correo es auxiliar de la Task; si no está disponible, se
            # conserva el responsable y se publica sin fabricar ese dato.
            email = ""
        update_quotation_responsible(quotation=quotation, option=option, email=email)
        outbox = quotation.task_outbox.filter(event_kind="COTIZACION").order_by("-pk").first()
        publish_scheduled = False
        publish_result = "none"
        if outbox is not None and outbox.status == outbox.Status.PENDING:
            publish_scheduled = True
            publish_task_outbox(outbox.pk)
            outbox.refresh_from_db()
            if outbox.status == outbox.Status.PUBLISHED:
                publish_result = "published"
            elif outbox.status == outbox.Status.RECONCILE:
                publish_result = "reconcile"
            elif outbox.status == outbox.Status.BLOCKED:
                publish_result = "blocked"
            else:
                publish_result = "none"
        elif outbox is not None and outbox.status == outbox.Status.RECONCILE:
            publish_result = "reconcile"
        elif outbox is not None and outbox.status == outbox.Status.PUBLISHED:
            publish_result = "published"
        elif outbox is not None and outbox.status == outbox.Status.BLOCKED:
            publish_result = "blocked"
        logger.info(
            "individual_responsible_publish quotation_id=%s outbox_existing=%s responsible_resolved=%s email_resolved=%s publish_scheduled=%s publish_result=%s",
            str(quotation.public_id), bool(outbox), bool(getattr(option, "actual_value", "")), bool(email), publish_scheduled, publish_result,
        )
        if publish_result == "published":
            messages.success(request, "Responsable actualizado y Tarea publicada correctamente.")
        elif publish_result == "reconcile":
            messages.warning(request, "Responsable actualizado; la Tarea requiere conciliación y no se reintentará automáticamente.")
        elif publish_result == "blocked":
            messages.warning(request, "Responsable actualizado; la Tarea permanece bloqueada por una causa operativa previa.")
        else:
            messages.success(request, "Responsable actualizado; la Tarea quedó lista para publicación controlada.")
    except (signing.BadSignature, CotizacionIndividual.DoesNotExist, ValueError):
        raise Http404("Respuesta no encontrada")
    except ValidationError as exc:
        messages.warning(request, str(exc))
    return redirect("cotizacion_colectivos:individual_expedient", token=token)


@never_cache
@require_http_methods(["POST"])
def individual_publish_task(request, token):
    if not has_internal_permission(request, "approve_responses"):
        return permission_denied_response()
    try:
        quotation = CotizacionIndividual.objects.get(public_id=unsign_receipt(token))
        _ensure_individual_can_manage_task(quotation)
        outbox = quotation.task_outbox.filter(event_kind="COTIZACION").order_by("-pk").first()
        if outbox is None:
            messages.warning(request, "Todavía no existe una intención de Tarea para publicar.")
        else:
            publish_task_outbox(outbox.pk)
            outbox.refresh_from_db()
            if outbox.status == outbox.Status.PUBLISHED:
                messages.success(request, "Tarea publicada correctamente.")
            elif outbox.status == outbox.Status.RECONCILE:
                messages.warning(request, "La publicación quedó en conciliación; no se reintentará automáticamente.")
            else:
                messages.warning(request, "La Tarea no pudo publicarse; revise el estado operativo.")
    except (signing.BadSignature, CotizacionIndividual.DoesNotExist, ValueError):
        raise Http404("Respuesta no encontrada")
    return redirect("cotizacion_colectivos:individual_expedient", token=token)


_HIDDEN_RESPONSE_KEYS = frozenset({"entity_key"})


def _human_response_value(key, value):
    """Format client answers without exposing stable technical identifiers."""
    if isinstance(value, bool):
        return "Sí" if value else "No"
    if value is None or value == "":
        return "Sin información"
    return value


@never_cache
@require_http_methods(["GET"])
def individual_expedient(request, token):
    if not has_internal_permission(request, "view_individual_quotation"):
        return permission_denied_response()
    try:
        public_id = unsign_receipt(token)
        quotation = CotizacionIndividual.objects.select_related(
            "external_access"
        ).prefetch_related("attachments", "task_outbox", "notifications").get(public_id=public_id)
        payload = json.loads(decrypt(quotation.encrypted_payload))
    except (signing.BadSignature, CotizacionIndividual.DoesNotExist, ValueError, json.JSONDecodeError) as exc:
        raise Http404("Respuesta no encontrada") from exc
    schema = get_branch_schema(quotation.branch_slug)
    field_labels = {item.key: item.label for item in schema.fields}
    # Sólo aparece al leer respuestas históricas; el formulario nuevo ya no
    # lo ofrece porque usa Nombres y Apellidos separados.
    field_labels.setdefault("requester_name", "Nombre del solicitante")
    field_labels["declared_company"] = "Empresa a la cual pertenece"
    group_schemas = {item.key: item for item in schema.repeatables}
    display_fields = tuple(
        (field_labels.get(key, "Información"), _human_response_value(key, value))
        for key, value in payload.get("fields", {}).items()
        if key not in _HIDDEN_RESPONSE_KEYS
    )
    display_groups = []
    for group_key, rows in payload.get("groups", {}).items():
        group_schema = group_schemas.get(group_key)
        labels = (
            {item.key: item.label for item in group_schema.fields}
            if group_schema else {}
        )
        display_groups.append({
            "label": group_schema.plural if group_schema else "Información relacionada",
            "rows": tuple(
                tuple(
                    (labels.get(key, "Información"), _human_response_value(key, value))
                    for key, value in row.items()
                    if key not in _HIDDEN_RESPONSE_KEYS
                )
                for row in rows
            ),
        })
    context = dict(payload.get("context")) if isinstance(payload.get("context"), dict) else {}
    # Contextos firmados anteriores no tienen todavía la metadata de Task.
    # Normalizar aquí evita que el template trate claves opcionales como obligatorias.
    for optional_key in (
        "task_responsible", "task_responsible_display", "task_responsible_email",
        "task_area",
    ):
        context.setdefault(optional_key, "")
    safe_metadata = quotation.safe_metadata or {}
    individual_attachments = tuple(quotation.attachments.all())
    documents_by_owner = {}
    for attachment in individual_attachments:
        metadata = attachment.safe_metadata if isinstance(attachment.safe_metadata, dict) else {}
        attachment.owner_label = {"affiliate": "Afiliado", "insured": "Asegurado", "risk": "Vehículo"}.get(str(metadata.get("owner_role") or ""), "Documento histórico")
        attachment.document_status = _attachment_document_status(attachment)
        attachment.structured_owner = (
            str(metadata.get("owner_type") or "") in {"contact", "risk", "request"}
            and bool(str(metadata.get("owner_key") or "").strip())
        )
        attachment.is_request_support = (
            str(metadata.get("owner_type") or "") == "request"
            and str(metadata.get("document_type") or "") == "support_document"
        )
        documents_by_owner.setdefault(str(metadata.get("owner_key") or "legacy"), []).append(attachment)
    acceptance = safe_metadata.get("acceptance") if isinstance(safe_metadata.get("acceptance"), dict) else {}
    decision_status = _individual_acceptance_status(quotation)
    person_lookup = safe_metadata.get("person_lookup") if isinstance(safe_metadata.get("person_lookup"), dict) else {}
    people_lookup = [dict(item) for item in (safe_metadata.get("people_lookup") or ()) if isinstance(item, dict)]
    if not people_lookup and person_lookup:
        people_lookup = [dict(person_lookup)]
    corrections = safe_metadata.get("person_corrections") if isinstance(safe_metadata.get("person_corrections"), dict) else {}
    for person in people_lookup:
        if person.get("status") == "not_found":
            correction = corrections.get(str(person.get("document") or ""), {})
            person.setdefault("missing_fields", tuple())
            candidate = dict(person.get("candidate") or {})
            if isinstance(correction, dict):
                candidate = effective_candidate(candidate, correction)
            if candidate:
                candidate.setdefault("N_mero_de_ID", person.get("document") or "")
                person["candidate"] = candidate
                person["missing_fields"] = contact_missing_fields(candidate)
            person["has_complete_data"] = not person.get("missing_fields")
    zoho_entities = safe_metadata.get("zoho_entities") if isinstance(safe_metadata.get("zoho_entities"), dict) else {}
    # Recalcular siempre las entidades de Movilidad al abrir un expediente
    # aceptado. Esto vuelve a evaluar subriesgos después de que un Contact o
    # Riesgo haya sido creado, sin ejecutar ningún WRITE desde GET.
    vehicle_rows = payload.get("groups", {}).get("vehicles", ()) if isinstance(payload.get("groups"), dict) else ()
    expected_vehicle_count = len(vehicle_rows) if isinstance(vehicle_rows, list) else 0
    entities_incomplete = (
        not zoho_entities.get("people")
        or not zoho_entities.get("risks")
        or (expected_vehicle_count and len(zoho_entities.get("risks") or ()) < expected_vehicle_count)
        or (expected_vehicle_count and len(zoho_entities.get("subrisks") or ()) < expected_vehicle_count)
    )
    if acceptance.get("status") == "accepted" and quotation.branch_slug == "movilidad":
        try:
            zoho_entities = resolve_mobility_entities(quotation=quotation)
        except Exception:
            logger.warning("individual_entities_resolution_failed quotation_id=%s", quotation.public_id)
    elif acceptance.get("status") == "accepted" and quotation.branch_slug in {"salud", "vida", "exequial"}:
        try:
            zoho_entities = resolve_common_people_entities(quotation=quotation)
            safe_metadata = quotation.safe_metadata or {}
        except Exception:
            logger.warning("individual_people_resolution_failed quotation_id=%s", quotation.public_id)
    if zoho_entities.get("people"):
        promoted_people, promoted = promote_created_people(
            zoho_entities.get("people"),
            safe_metadata.get("people_lookup") or (),
        )
        if promoted:
            zoho_entities["people"] = promoted_people
            safe_metadata["zoho_entities"] = zoho_entities
            quotation.safe_metadata = safe_metadata
            quotation.save(update_fields=("safe_metadata",))
    zoho_entities, insured_synced = synchronize_risk_insured(zoho_entities)
    if insured_synced:
        safe_metadata["zoho_entities"] = zoho_entities
        quotation.safe_metadata = safe_metadata
        quotation.save(update_fields=("safe_metadata",))
    if zoho_entities.get("people"):
        people_lookup = [dict(item) for item in zoho_entities.get("people", ()) if isinstance(item, dict)]
    if people_lookup:
        person_lookup = people_lookup[0]
    for person in people_lookup:
        owner_keys = [str(person.get("owner_key") or "")]
        owner_keys.extend(str(key) for key in (person.get("owner_keys") or ()) if key)
        if person.get("role") == "Afiliado":
            owner_keys.append("affiliate")
        person["documents"] = tuple(
            document
            for key in dict.fromkeys(owner_keys)
            for document in documents_by_owner.get(key, ())
        )
        person_remote_id = str(person.get("remote_id") or person.get("contact_id") or "").strip()
        for document in person["documents"]:
            document.can_publish = _attachment_can_publish(document, person_remote_id)
    if isinstance(vehicle_rows, list):
        for index, risk in enumerate(zoho_entities.get("risks") or ()):
            if not isinstance(risk, dict):
                continue
            row = vehicle_rows[index] if index < len(vehicle_rows) and isinstance(vehicle_rows[index], dict) else {}
            owner_key = str(risk.get("owner_key") or risk.get("risk_key") or row.get("entity_key") or f"vehicles-{index}")
            risk["owner_key"] = owner_key
            risk["risk_key"] = owner_key
            risk["zero_km"] = str(row.get("zero_km") or "").strip()
            risk["documents"] = tuple(documents_by_owner.get(owner_key, ()))
            risk_remote_id = str(risk.get("remote_id") or risk.get("risk_id") or "").strip()
            for document in risk["documents"]:
                document.can_publish = _attachment_can_publish(document, risk_remote_id)
            insured = risk.get("insured") if isinstance(risk.get("insured"), dict) else None
            if insured is not None:
                insured["documents"] = tuple(documents_by_owner.get(f"{owner_key}-insured", ()))
                insured_remote_id = str(insured.get("remote_id") or insured.get("contact_id") or "").strip()
                for document in insured["documents"]:
                    document.can_publish = _attachment_can_publish(document, insured_remote_id)
    # Legacy/unowned documents remain visible but cannot be published. The
    # structured owner loops above set this from the final server-side entity
    # snapshot and its remote ID.
    for attachment in individual_attachments:
        metadata = attachment.safe_metadata if isinstance(attachment.safe_metadata, dict) else {}
        owner_key = str(metadata.get("owner_key") or "")
        owner_type = str(metadata.get("owner_type") or "")
        # Keep an explicit false default for legacy/unowned documents. For
        # structured documents, the owner loops above already calculated the
        # value from the final entity snapshot and its remote ID.
        if not hasattr(attachment, "can_publish"):
            attachment.can_publish = False
    created_people = tuple(
        person for person in people_lookup
        if person.get("created") or (person.get("status") == "found" and person.get("contact_id"))
    )
    access = quotation.external_access
    try:
        recipient = decrypt(access.encrypted_recipient)
    except (TypeError, ValueError):
        recipient = "No disponible"
    outboxes = tuple(quotation.task_outbox.all())
    latest_outbox = max(outboxes, key=lambda row: (row.updated_at, row.pk)) if outboxes else None
    task_responsibles = ()
    if latest_outbox and not (context.get("task_responsible") or safe_metadata.get("task_responsible")):
        try:
            task_responsibles = task_responsible_options(collective_only=True)
        except Exception:
            task_responsibles = ()
    remote_task_id = ""
    if latest_outbox and latest_outbox.encrypted_remote_id:
        try:
            remote_task_id = decrypt(latest_outbox.encrypted_remote_id)
        except (TypeError, ValueError):
            remote_task_id = ""
    history = [
        {"label": "Enlace generado", "at": access.created_at},
        *([{"label": "Enlace abierto", "at": access.first_access_at}] if access.first_access_at else []),
        *([{"label": "OTP verificado", "at": access.otp_used_at}] if access.otp_used_at else []),
        *([{"label": "Acceso directo mediante enlace firmado", "at": access.first_access_at}]
          if access.first_access_at and not individual_otp_required(access) else []),
        {"label": "Respuesta recibida", "at": quotation.submitted_at},
    ]
    if latest_outbox:
        history.append({
            "label": f"Zoho Tarea: {latest_outbox.get_status_display()}",
            "at": latest_outbox.updated_at,
        })
    history.sort(key=lambda row: row["at"], reverse=True)
    # The human response section is legacy-only; structured documents are
    # rendered beside their owner cards and must not appear twice.
    quotation._prefetched_objects_cache["attachments"] = list(
        attachment for attachment in individual_attachments
        if not getattr(attachment, "structured_owner", False)
    )
    support_attachments = tuple(
        attachment for attachment in individual_attachments
        if getattr(attachment, "is_request_support", False)
    )
    return render(request, "cotizacion_colectivos/individual/detail.html", {
        "quotation": quotation,
        "schema": schema,
        "access": access,
        "individual_context": context,
        "task_responsible_display": str(safe_metadata.get("task_responsible_display") or ""),
        "task_responsible_email": str(safe_metadata.get("task_responsible_email") or ""),
        "recipient": recipient,
        "declared_company": payload.get("fields", {}).get("declared_company", ""),
        "display_fields": display_fields,
        "display_groups": tuple(display_groups),
        "latest_outbox": latest_outbox,
        "remote_task_id": remote_task_id,
        "history": tuple(history),
        "acceptance": acceptance,
        "decision_status": decision_status,
        "decision_pending": decision_status == "pending",
        "is_rejected": decision_status == "rejected",
        "can_reject": decision_status == "pending",
        "can_reactivate": decision_status == "rejected",
        "can_operate_zoho": decision_status == "accepted",
        "person_lookup": person_lookup,
        "people_lookup": tuple(people_lookup),
        "created_people": created_people,
        "zoho_entities": zoho_entities,
        "vehicle_class_choices": VEHICLE_CLASS_CHOICES,
        "vehicle_use_choices": VEHICLE_USE_CHOICES,
        "individual_attachments": individual_attachments,
        "support_attachments": support_attachments,
        "task_responsibles": task_responsibles,
        "individual_token": token,
        "person_creation_blocked": True,
        "policy_creation_blocked": True,
        "colectivos_mode": resolve_tool_mode(request, INDIVIDUAL_MODE),
        **_environment_context(),
    })


@never_cache
@require_http_methods(["GET"])
def individual_quotation_detail(request, token):
    """Compatibility route; the operational expediente is the sole UI."""
    return redirect("cotizacion_colectivos:individual_expedient", token=token)


@never_cache
@require_http_methods(["GET"])
def individual_attachment_download(request, token, attachment_id):
    """Serve one encrypted individual quotation file after scoped permission checks."""
    if not has_internal_permission(request, "download_attachments"):
        return permission_denied_response()
    try:
        public_id = unsign_receipt(token)
        quotation = CotizacionIndividual.objects.get(public_id=public_id)
        attachment = AdjuntoCotizacionIndividual.objects.get(pk=attachment_id, quotation=quotation)
        root = Path(settings.COLECTIVOS_PRIVATE_ROOT).resolve()
        target = (root / "individual_quotations" / attachment.stored_path).resolve()
        if root not in target.parents or not target.is_file():
            raise Http404("Adjunto no disponible")
        encoded = decrypt(target.read_bytes().decode())
        content = base64.b64decode(encoded.encode())
    except (signing.BadSignature, CotizacionIndividual.DoesNotExist, AdjuntoCotizacionIndividual.DoesNotExist, ValueError, TypeError) as exc:
        raise Http404("Adjunto no disponible") from exc
    allowed_preview_types = {"application/pdf", "image/jpeg", "image/png", "image/webp"}
    content_type = str(attachment.detected_mime or "").lower()
    if content_type not in allowed_preview_types:
        return HttpResponse("Este archivo no puede previsualizarse.", status=415, content_type="text/plain")
    response = HttpResponse(content, content_type=content_type)
    safe_name = re.sub(r'["\r\n]', "", Path(attachment.safe_original_name or f"documento{attachment.extension}").name)
    response["Content-Disposition"] = f'inline; filename="{safe_name}"'
    response["Cache-Control"] = "no-store, private"
    response["Pragma"] = "no-cache"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@never_cache
@require_http_methods(["POST"])
def policy_invitation_attach(request, token):
    """Attach a generated insurer invitation to this policy in Zoho."""
    # Functional access follows the general Colectivos workspace permission;
    # Zoho WRITE remains independently protected by the attachment guards.
    if not has_internal_permission(request, "view_requests"):
        return permission_denied_response()
    try:
        result = prepare_invitation_attachment(
            token=token,
            insurer_code=str(request.POST.get("insurer_code") or ""),
            template_code=str(request.POST.get("template_code") or ""),
        )
    except (ColectivosServiceError, ValidationError, IndividualAttachmentBlocked, IndividualAttachmentUncertain) as exc:
        messages.error(request, str(getattr(exc, "message", "No fue posible adjuntar el archivo.")))
        return redirect("cotizacion_colectivos:policy_invitation_preview", token=token)
    messages.success(request, "Archivo adjuntado a la póliza." if result.get("status") != "UPLOADED" else "El archivo ya estaba adjuntado.")
    return redirect("cotizacion_colectivos:policy_invitation_preview", token=token)


@never_cache
@require_http_methods(["POST"])
def individual_attachment_remove(request, token, attachment_id):
    if not has_internal_permission(request, "approve_responses"):
        return permission_denied_response()
    try:
        public_id = unsign_receipt(token)
        quotation = CotizacionIndividual.objects.get(public_id=public_id)
        attachment = AdjuntoCotizacionIndividual.objects.get(pk=attachment_id, quotation=quotation)
        metadata = attachment.safe_metadata if isinstance(attachment.safe_metadata, dict) else {}
        if metadata.get("zoho_status") == "uploaded":
            messages.warning(request, "El documento ya está publicado en Zoho y no puede eliminarse desde aquí.")
        else:
            root = (Path(settings.COLECTIVOS_PRIVATE_ROOT) / "individual_quotations").resolve()
            target = (root / attachment.stored_path).resolve()
            if root not in target.parents:
                raise Http404("Adjunto no disponible")
            target.unlink(missing_ok=True)
            attachment.delete()
            messages.success(request, "Documento retirado de la cotización.")
    except (signing.BadSignature, CotizacionIndividual.DoesNotExist, AdjuntoCotizacionIndividual.DoesNotExist, ValueError):
        raise Http404("Adjunto no disponible")
    return redirect("cotizacion_colectivos:individual_expedient", token=token)


@never_cache
@require_http_methods(["POST"])
def individual_attachment_publish(request, token, attachment_id):
    """Explicitly publish a pending document to its already-resolved owner."""
    if not has_internal_permission(request, "approve_responses"):
        return permission_denied_response()
    try:
        quotation = _individual_entity_quotation(token)
        _ensure_individual_can_operate_zoho(quotation)
        attachment = quotation.attachments.get(pk=attachment_id)
        metadata = attachment.safe_metadata if isinstance(attachment.safe_metadata, dict) else {}
        if metadata.get("zoho_status") == "uploaded":
            raise ValidationError("El documento ya está publicado en Zoho.")
        entities = (quotation.safe_metadata or {}).get("zoho_entities") or {}
        owner_key = str(metadata.get("owner_key") or "")
        module = "Contacts"
        remote_id = ""
        if owner_key == "affiliate":
            person = next(iter(entities.get("people") or ()), {})
            remote_id = str(person.get("remote_id") or person.get("contact_id") or "")
        elif owner_key.startswith("people-"):
            person = next((item for item in (entities.get("people") or ())
                           if str(item.get("owner_key") or "") == owner_key
                           or owner_key in (item.get("owner_keys") or ())), {})
            remote_id = str(person.get("remote_id") or person.get("contact_id") or "")
        elif owner_key.startswith("vehicles-") and owner_key.endswith("-insured"):
            base_key = owner_key[:-8]
            payload = json.loads(decrypt(quotation.encrypted_payload))
            rows = (payload.get("groups") or {}).get("vehicles") or []
            index = next((position for position, row in enumerate(rows) if str((row or {}).get("entity_key") or f"vehicles-{position}") == base_key), -1)
            risk = (entities.get("risks") or [])[index] if index >= 0 and index < len(entities.get("risks") or []) else {}
            insured = risk.get("insured") or {}
            remote_id = str(insured.get("remote_id") or insured.get("contact_id") or "")
        elif owner_key.startswith("vehicles-"):
            payload = json.loads(decrypt(quotation.encrypted_payload))
            rows = (payload.get("groups") or {}).get("vehicles") or []
            index = next((position for position, row in enumerate(rows) if str((row or {}).get("entity_key") or f"vehicles-{position}") == owner_key), -1)
            risk = (entities.get("risks") or [])[index] if index >= 0 and index < len(entities.get("risks") or []) else {}
            module = "Riesgos"
            remote_id = str(risk.get("remote_id") or risk.get("risk_id") or "")
        if not remote_id:
            raise ValidationError("Primero debe resolver la entidad en Zoho.")
        publish_attachment(attachment=attachment, module=module, record_id=remote_id)
        messages.success(request, "Documento publicado en Zoho.")
    except (ValidationError, IndividualAttachmentUncertain, IndividualAttachmentBlocked, ZohoError) as exc:
        messages.warning(request, str(exc))
    return redirect("cotizacion_colectivos:individual_expedient", token=token)


@never_cache
@require_http_methods(["POST"])
def individual_attachment_reconcile(request, token, attachment_id):
    """Reconcile an uncertain attachment without retrying its upload."""
    if not has_internal_permission(request, "approve_responses"):
        return permission_denied_response()
    try:
        quotation = _individual_entity_quotation(token)
        attachment = quotation.attachments.get(pk=attachment_id)
        metadata = attachment.safe_metadata if isinstance(attachment.safe_metadata, dict) else {}
        target = metadata.get("zoho_attachment") if isinstance(metadata.get("zoho_attachment"), dict) else {}
        module = str(target.get("module") or metadata.get("zoho_module") or "")
        record_id = str(target.get("record_id") or metadata.get("zoho_record_id") or "")
        if module not in {"Contacts", "Riesgos"} or not record_id.isdigit():
            raise ValidationError("No hay un destino válido para reconciliar.")
        reconcile_attachment(attachment=attachment, module=module, record_id=record_id)
        messages.success(request, "Documento conciliado con Zoho.")
    except (ValidationError, IndividualAttachmentUncertain, IndividualAttachmentBlocked, ZohoError) as exc:
        messages.warning(request, str(exc))
    return redirect("cotizacion_colectivos:individual_expedient", token=token)


@never_cache
@require_http_methods(["GET"])
def response_detail(request, public_id, version):
    if not has_internal_permission(request, "view_responses"):
        return permission_denied_response()
    item = get_object_or_404(SolicitudColectivo, public_id=public_id)
    _attach_request_operational_context(item)
    response = get_object_or_404(
        RespuestaSolicitudColectivo.objects.prefetch_related("changes"),
        request=item,
        version=version,
        status=RespuestaSolicitudColectivo.Status.SUBMITTED,
    )
    observations = (
        decrypt(response.encrypted_client_observations)
        if response.encrypted_client_observations
        else ""
    )
    return render(request, "cotizacion_colectivos/response_detail.html", {
        "item": item,
        "response": response,
        "observations": observations,
        **_environment_context(),
    })


@never_cache
@require_http_methods(["POST"])
def notifications_read_all(request):
    if not has_internal_permission(request, "manage_notifications"):
        return permission_denied_response()
    NotificacionColectivos.objects.filter(
        user=get_internal_actor(request, create=True),
        read_at__isnull=True,
        notification_type="CLIENT_RESPONSE",
    ).update(read_at=timezone.now())
    NotificacionCotizacionIndividual.objects.filter(
        user=get_internal_actor(request, create=True),
        read_at__isnull=True,
    ).update(read_at=timezone.now())
    return redirect("cotizacion_colectivos:request_list")
