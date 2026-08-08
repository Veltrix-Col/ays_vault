from __future__ import annotations

import logging
import time
import unicodedata
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta

from django.core.exceptions import ValidationError
from django.db import OperationalError, transaction
from django.db.models import Count, Prefetch, Q
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.shortcuts import get_object_or_404, redirect
from django.core.paginator import Paginator
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from django.template.loader import render_to_string

from .forms import CompanySearchForm, ExternalAccessPrepareForm, MultiPolicyRequestForm, OptionalAccessEmailForm, PersonSearchForm, RequestCreateForm, RequestEditForm, RequestFilterForm, RequestTransitionForm, SnapshotRegenerateForm
from .services import CompanySearchService, EntityDetailService, PersonSearchService, PolicyService
from .services.common import ColectivosServiceError, sign_record_id, unsign_record_context
from .excel import build_current_policy_workbook
from .permissions import has_internal_permission, permission_denied_response
from .models import AccesoExternoSolicitudColectivo, AdjuntoSolicitudColectivo, CambioSolicitudColectivo, EventoSolicitudColectivo, NotificacionColectivos, RespuestaSolicitudColectivo, SolicitudColectivo, SolicitudColectivoPoliza
from .dto import RequestPolicyOption
from .services.requests import create_or_reuse_request_from_policy, create_request_from_policies, create_request_from_policy, regenerate_request_snapshot, request_reference_hashes, request_snapshot, source_reference_hash, transition_request, update_draft_request
from .services.external import ActiveAccessExistsError, ExternalAccessError, GeneratedAccess, generate_access, resolve_token, revoke_access, send_invitation, send_optional_invitation
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
        timings = {
            "facade_ms": 0, "organization_ms": 0, "metadata_ms": 0,
            "search_ms": 0, "coql_ms": 0, "mapper_ms": 0,
            "dedup_ms": 0, "dto_ms": 0,
        }
        try:
            query = form.cleaned_data["query"]
            service = service_class()
            results = service.search(query)
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
        "zoho_environment": environment,
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
    return render(request, "cotizacion_colectivos/detail.html", {
        "detail": detail, "entity_kind": entity_kind, "entity_token": token,
        "related_requests": _client_requests(
            request, token=token, entity_kind=entity_kind, environment=environment,
        ),
        "zoho_environment": environment,
        **extra,
    })


def _normalized_choice(value):
    return "".join(
        character for character in unicodedata.normalize("NFKD", str(value or "").strip().casefold())
        if not unicodedata.combining(character)
    )


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
def policy_detail(request, token):
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
        "has_generable": any(item.status in {"ready", "incomplete"} for item in previews),
        "preparation_metadata": metadata, **_environment_context(),
    })


@never_cache
@require_http_methods(["POST"])
def policy_invitation_download(request, token):
    if not has_internal_permission(request, "export_excel"):
        return permission_denied_response()
    try:
        content, filename, content_type, errors = generate_invitation_templates(token)
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
    queryset = SolicitudColectivo.objects.select_related("assigned_to").prefetch_related("policies").annotate(
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
    item = get_object_or_404(
        SolicitudColectivo.objects.select_related("assigned_to", "created_by").prefetch_related(
            Prefetch(
                "policies",
                queryset=SolicitudColectivoPoliza.objects.annotate(
                    change_count=Count("changes", distinct=True)
                ).prefetch_related("records"),
            )
        ),
        public_id=public_id,
    )
    try:
        snapshot = request_snapshot(item)
    except ValidationError:
        snapshot = None
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
    return render(request, "cotizacion_colectivos/request_detail.html", {
        "item": item, "snapshot": snapshot, "transition_form": RequestTransitionForm(), "edit_form": edit_form,
        "snapshot_form": SnapshotRegenerateForm(), "access_summary": access_summary, "policy_token": policy_token,
        "can_edit": has_internal_permission(request, "edit_requests"),
        "can_prepare": has_internal_permission(request, "create_requests"),
        "can_generate_access": has_internal_permission(request, "generate_external_access"),
        "can_regenerate_access": has_internal_permission(request, "regenerate_external_access"),
        "can_revoke_access": has_internal_permission(request, "revoke_external_access"),
        "can_send_requests": has_internal_permission(request, "send_requests"),
        "can_approve": has_internal_permission(request, "approve_requests"), **_environment_context(),
    })


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
        return HttpResponse("Transición inválida.", status=400)
    target = form.cleaned_data["target"]
    permission = "approve_requests" if target == SolicitudColectivo.Status.APPROVED else ("close_requests" if target == SolicitudColectivo.Status.CLOSED else ("cancel_requests" if target == SolicitudColectivo.Status.CANCELLED else "create_requests"))
    if not has_internal_permission(request, permission):
        return permission_denied_response()
    try:
        transition_request(request=item, target=target, actor=get_internal_actor(request, create=True))
    except ValidationError:
        return HttpResponse("La transición no está permitida.", status=400)
    audit(request, "UPDATE", reason="Estado de expediente Colectivos actualizado.", metadata={"request_id": item.public_id, "target": target})
    return redirect("cotizacion_colectivos:request_detail", public_id=item.public_id)


@never_cache
@require_http_methods(["GET"])
def notification_list(request):
    if not has_internal_permission(request, "manage_notifications"):
        return permission_denied_response()
    actor = get_internal_actor(request, create=False)
    queryset = (
        NotificacionColectivos.objects.none()
        if actor is None
        else NotificacionColectivos.objects.filter(
            user=actor, notification_type="CLIENT_RESPONSE",
        ).select_related("request")
    )
    queryset = queryset.order_by("-created_at", "-pk")
    page = Paginator(queryset, 25).get_page(request.GET.get("page"))
    return render(request, "cotizacion_colectivos/notifications.html", {"page": page, **_environment_context()})


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
    return redirect("cotizacion_colectivos:notification_list")


@never_cache
@require_http_methods(["GET"])
def response_detail(request, public_id, version):
    if not has_internal_permission(request, "view_responses"):
        return permission_denied_response()
    item = get_object_or_404(SolicitudColectivo, public_id=public_id)
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
    return redirect("cotizacion_colectivos:notification_list")
