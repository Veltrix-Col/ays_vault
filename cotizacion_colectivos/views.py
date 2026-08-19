from __future__ import annotations

import logging
import json
import time
import unicodedata
import uuid
from urllib.parse import quote
from dataclasses import asdict
from datetime import datetime, timedelta

from django.core.exceptions import ValidationError
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

from .forms import ClientSearchForm, CompanySearchForm, ExternalAccessPrepareForm, IndividualAccessPrepareForm, MultiPolicyRequestForm, OptionalAccessEmailForm, PersonSearchForm, RequestCreateForm, RequestEditForm, RequestFilterForm, RequestTransitionForm, SnapshotRegenerateForm
from .services import CompanySearchService, EntityDetailService, PersonSearchService, PolicyService, UnifiedClientSearchService
from .services.common import ColectivosServiceError, sign_record_id, unsign_record_context
from .excel import build_current_policy_workbook
from .permissions import has_internal_permission, permission_denied_response
from .models import AccesoExternoSolicitudColectivo, AdjuntoSolicitudColectivo, CambioSolicitudColectivo, EventoSolicitudColectivo, NotificacionColectivos, RespuestaSolicitudColectivo, SolicitudColectivo, SolicitudColectivoPoliza
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
from .modes import HUB_MODE, INDIVIDUAL_MODE, INVITATIONS_MODE, resolve_tool_mode
from .service_catalog import branch_workspaces
from .quotation_forms.catalog import get_branch_schema
from .quotation_forms.security import sign_receipt, unsign_receipt
from .services.individual_quotations import (
    affiliate_options,
    build_policy_context,
    accept_individual_quotation,
    resolve_accepted_person,
)
from .services.individual_access import generate_individual_access
from .services.task_responsibles import resolve_task_responsible_email, task_responsible_options
from .services.task_publisher import publish_task_outbox
from .services.person_contract import (
    ContactPublicationRejected, ContactPublicationUncertain, ContactPublishingDisabled,
    get_contacts_publisher,
)
from .models import AccesoCotizacionIndividual, ColectivosTaskOutbox, CotizacionIndividual, NotificacionCotizacionIndividual
from .quotation_forms.catalog import get_policy_branch_schema


logger = logging.getLogger("cotizacion_colectivos")


def _transition_permission(target):
    if target == SolicitudColectivo.Status.APPROVED:
        return "approve_requests"
    if target == SolicitudColectivo.Status.CLOSED:
        return "close_requests"
    if target == SolicitudColectivo.Status.CANCELLED:
        return "cancel_requests"
    return "create_requests"


RESPONSE_FIELD_LABELS = {
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
    return render(request, "cotizacion_colectivos/index.html", {
        "form": ClientSearchForm(),
        "colectivos_mode": tool_mode,
        **_environment_context(),
    })


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
                task_responsibles = task_responsible_options()
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
        responsible_options = task_responsible_options()
        choices = [(item.actual_value, item.display_value) for item in responsible_options]
        email_form = IndividualAccessPrepareForm(request.POST)
        email_form.fields["responsible"].choices = choices
        if not email_form.is_valid():
            raise ValidationError("Seleccione un responsable y un correo válido para proteger el acceso con OTP.")
        responsible = next(item for item in responsible_options if item.actual_value == email_form.cleaned_data["responsible"])
        responsible_email = resolve_task_responsible_email(responsible)
        schema, _context_token, payload = build_policy_context(
            policy_token=token,
            detail=detail,
            members=members,
            affiliate_key=affiliate_key,
            creator_id=actor.pk,
        )
        payload.update({
            "task_responsible": responsible.actual_value,
            "task_responsible_display": responsible.display_value,
            "task_responsible_email": responsible_email,
            "task_area": "Negocios Bienestar y Beneficios",
        })
        generated = generate_individual_access(
            context=payload,
            actor=actor,
            recipient=email_form.cleaned_data["recipient"],
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
    queryset = SolicitudColectivo.objects.select_related("assigned_to").prefetch_related(
        "policies",
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
            access.inbox_person_label = context.get("affiliate_label") or "Persona nueva"
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
            access.has_unread_response = bool(quotation and quotation.pk in unread_quotation_ids)
            access.inbox_detail_url = reverse(
                "cotizacion_colectivos:individual_expedient",
                args=[sign_receipt(quotation.public_id)],
            ) if quotation else ""
            individual_entries.append(access)
            operational_entries.append(access)

    operational_entries.sort(key=lambda item: (
        0 if item.inbox_requires_attention else 1,
        -item.inbox_last_activity.timestamp(),
        item.inbox_kind,
        -item.pk,
    ))
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
        fields = []
        for change in latest_response.changes.all():
            try:
                value = decrypt(change.encrypted_new_value) if change.encrypted_new_value else ""
            except ValueError:
                value = "Información no disponible"
            fields.append({
                "label": RESPONSE_FIELD_LABELS.get(
                    change.functional_field,
                    change.functional_field.replace("_", " ").strip().capitalize() or "Información",
                ),
                "value": value or "Sin dato",
                "action": change.get_action_display(),
                "record": (
                    f"Registro {change.original_record.original_position}"
                    if change.original_record_id else "Inclusión"
                ),
            })
        response_summary = {
            "item": latest_response,
            "fields": tuple(fields),
            "observations": observations,
            "attachment_count": latest_response.attachments.count(),
        }
    latest_outbox = item.task_outbox.order_by("-updated_at", "-pk").first()
    zoho_tasks = []
    for outbox in item.task_outbox.order_by("event_kind", "-updated_at", "-pk"):
        remote_id = ""
        if outbox.status == outbox.Status.PUBLISHED and outbox.encrypted_remote_id:
            try:
                remote_id = decrypt(outbox.encrypted_remote_id)
            except ValueError:
                remote_id = "No disponible"
        zoho_tasks.append({
            "outbox_id": outbox.pk,
            "kind": outbox.event_kind,
            "type": {"INCLUSION": "Ingresos", "RETIRO": "Retiros", "COTIZACION": "Cotización"}.get(outbox.event_kind, outbox.event_kind),
            "status": outbox.get_status_display(),
            "task_id": remote_id,
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
        **_environment_context(),
    })


@never_cache
@require_http_methods(["POST"])
def request_publish_task(request, public_id, outbox_id):
    if not has_internal_permission(request, "approve_requests"):
        return permission_denied_response()
    item = get_object_or_404(SolicitudColectivo, public_id=public_id)
    outbox = get_object_or_404(ColectivosTaskOutbox, pk=outbox_id, request=item)
    if outbox.status == outbox.Status.PUBLISHED:
        messages.info(request, "La Task ya fue publicada.")
    else:
        publish_task_outbox(outbox.pk)
        outbox.refresh_from_db()
        if outbox.status == outbox.Status.PUBLISHED:
            messages.success(request, "Task publicada correctamente en Zoho Sandbox.")
        elif outbox.status == outbox.Status.RECONCILE:
            messages.warning(request, "Resultado incierto: requiere conciliación antes de reintentar.")
        else:
            messages.error(request, "La Task no pudo publicarse; revise el estado del expediente.")
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
    response = FileResponse(target.open("rb"), content_type=attachment.detected_mime, as_attachment=True, filename=f"soporte{attachment.extension}")
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
    actor = get_internal_actor(request, create=True)
    quotation = accept_individual_quotation(quotation=quotation, actor=actor)
    try:
        resolve_accepted_person(quotation=quotation)
        messages.success(request, "Cotización aceptada; se verificó la persona en Zoho.")
    except Exception:
        messages.warning(request, "Cotización aceptada; no fue posible completar la consulta de persona.")
    return redirect("cotizacion_colectivos:individual_expedient", token=sign_receipt(quotation.public_id))


@never_cache
@require_http_methods(["POST"])
def individual_create_person(request, token):
    if not has_internal_permission(request, "approve_responses"):
        return permission_denied_response()
    try:
        public_id = unsign_receipt(token)
        quotation = CotizacionIndividual.objects.get(public_id=public_id)
        payload = json.loads(decrypt(quotation.encrypted_payload))
        fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
        first_name = str(fields.get("First_Name") or fields.get("first_name") or "").strip()
        last_name = str(fields.get("Last_Name") or fields.get("last_name") or "").strip()
        data = {
            "First_Name": first_name,
            "Last_Name": last_name,
            "Tipo_ID": str(fields.get("Tipo_ID") or fields.get("tipo_id") or "").strip(),
            "N_mero_de_ID": str(fields.get("N_mero_de_ID") or fields.get("documento") or "").strip(),
            "Date_of_Birth": fields.get("Date_of_Birth") or fields.get("fecha_nacimiento") or "",
            "Email": fields.get("Email") or fields.get("correo") or "",
            "Mobile": fields.get("Mobile") or fields.get("celular") or "",
            "Phone": fields.get("Phone") or fields.get("telefono") or "",
        }
        result = get_contacts_publisher(
            profile=str(getattr(settings, "ZOHO_ACTIVE_PROFILE", "sandbox")),
            confirmation=str(getattr(settings, "COLECTIVOS_CONTACT_WRITE_CONFIRMATION", "")),
        ).create(data)
        metadata = dict(quotation.safe_metadata or {})
        metadata["person_lookup"] = {"status": "found", "created": True, "detail_token": sign_record_id(result["record_id"], "person")}
        quotation.safe_metadata = metadata
        quotation.save(update_fields=("safe_metadata",))
        messages.success(request, "Persona creada correctamente en Zoho Sandbox.")
    except (signing.BadSignature, CotizacionIndividual.DoesNotExist, ValueError, json.JSONDecodeError):
        raise Http404("Respuesta no encontrada")
    except (ContactPublicationUncertain, ContactPublishingDisabled, ContactPublicationRejected, ValidationError) as exc:
        messages.warning(request, str(exc))
    return redirect("cotizacion_colectivos:individual_expedient", token=token)


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
    field_labels["declared_company"] = "Empresa a la cual pertenece"
    group_schemas = {item.key: item for item in schema.repeatables}
    display_fields = tuple(
        (field_labels.get(key, "Información"), value)
        for key, value in payload.get("fields", {}).items()
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
                tuple((labels.get(key, "Información"), value) for key, value in row.items())
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
    acceptance = safe_metadata.get("acceptance") if isinstance(safe_metadata.get("acceptance"), dict) else {}
    person_lookup = safe_metadata.get("person_lookup") if isinstance(safe_metadata.get("person_lookup"), dict) else {}
    if person_lookup.get("status") == "not_found":
        person_lookup = dict(person_lookup)
        fields_payload = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
        person_lookup["has_complete_data"] = bool(
            (fields_payload.get("First_Name") or fields_payload.get("first_name"))
            and (fields_payload.get("Last_Name") or fields_payload.get("last_name"))
            and (fields_payload.get("Tipo_ID") or fields_payload.get("tipo_id"))
            and (fields_payload.get("N_mero_de_ID") or fields_payload.get("documento"))
        )
    access = quotation.external_access
    try:
        recipient = decrypt(access.encrypted_recipient)
    except (TypeError, ValueError):
        recipient = "No disponible"
    outboxes = tuple(quotation.task_outbox.all())
    latest_outbox = max(outboxes, key=lambda row: (row.updated_at, row.pk)) if outboxes else None
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
        {"label": "Respuesta recibida", "at": quotation.submitted_at},
    ]
    if latest_outbox:
        history.append({
            "label": f"Zoho Task: {latest_outbox.get_status_display()}",
            "at": latest_outbox.updated_at,
        })
    history.sort(key=lambda row: row["at"], reverse=True)
    return render(request, "cotizacion_colectivos/individual/detail.html", {
        "quotation": quotation,
        "schema": schema,
        "access": access,
        "individual_context": context,
        "recipient": recipient,
        "declared_company": payload.get("fields", {}).get("declared_company", ""),
        "display_fields": display_fields,
        "display_groups": tuple(display_groups),
        "latest_outbox": latest_outbox,
        "remote_task_id": remote_task_id,
        "history": tuple(history),
        "acceptance": acceptance,
        "person_lookup": person_lookup,
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
