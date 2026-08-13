from __future__ import annotations

import logging
import time
import uuid
import json
from dataclasses import asdict

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .branches import COLLECTIVE_BRANCH_CONFIG
from .forms import AttachmentUploadForm, ExternalSubmitForm
from .models import CambioSolicitudColectivo, CotizacionIndividual, RespuestaSolicitudColectivo
from .services.attachments import store_attachment
from .services.excel_roundtrip import build_novelties_template
from .services.excel_previews import cancel_preview, confirm_preview, create_preview, resolve_preview
from .services.external import (
    EXTERNAL_COOKIE,
    ExternalAccessError,
    authorize_direct_access,
    resolve_external_session,
    resolve_token,
    save_response,
    submit_response,
)
from .services.requests import request_snapshot
from .services.functional_groups import consolidate_functional_groups
from .services.mappings import (
    CONTACT_ID_TYPE_CHOICES,
    INSURED_STATE_CHOICES,
    RELATION_ROLE_CHOICES,
    RELATIONSHIP_CHOICES,
)
from .filenames import download_filename
from .quotation_forms.catalog import get_branch_schema, get_policy_branch_schema
from .quotation_forms.forms import IndividualQuotationForm
from .quotation_forms.security import (
    sign_receipt,
    unsign_policy_context,
    unsign_receipt,
)
from .services.individual_quotations import affiliate_options, create_individual_quotation
from .services.preparations import load_policy_preparation
from .zoho import get_colectivos_profile


logger = logging.getLogger("cotizacion_colectivos")


def _individual_workspace(context):
    profile = get_colectivos_profile()
    backend = str(getattr(settings, "ZOHO_BACKEND", "sdk")).strip().lower()
    loaded = load_policy_preparation(
        token=str(context["policy_token"]),
        profile=profile,
        backend=backend,
        source_kind=str(context["source_kind"]),
    )
    if loaded is None:
        raise signing.BadSignature("El Workspace ya no está disponible.")
    detail, members, metadata = loaded
    schema = get_policy_branch_schema(detail.branch_code, detail.branch_name)
    if schema.slug != context.get("branch_slug") or schema.version != context.get("schema_version"):
        raise signing.BadSignature("El formulario ya no corresponde a la póliza.")
    if str(context.get("affiliate_key")) not in {
        option.key for option in affiliate_options(members)
    }:
        raise signing.BadSignature("El afiliado ya no pertenece al contexto.")
    return detail, members, metadata, schema


@never_cache
@require_http_methods(["GET", "POST"])
def individual_quotation(request, token):
    try:
        context = unsign_policy_context(token)
        detail, _members, metadata, schema = _individual_workspace(context)
    except (signing.BadSignature, Http404, KeyError, ValueError):
        return render(request, "cotizacion_colectivos/external/unavailable.html", status=410)

    initial_items = {
        group.key: [{} for _ in range(group.minimum)] for group in schema.repeatables
    }
    form = IndividualQuotationForm(
        request.POST or None,
        request.FILES or None,
        schema=schema,
        context=context,
        initial={"items_payload": json.dumps(initial_items)},
    )
    if request.method == "POST" and form.is_valid():
        creator = get_user_model().objects.filter(
            pk=context.get("creator_id"), is_active=True,
        ).first()
        try:
            quotation = create_individual_quotation(
                schema=schema,
                cleaned_data=form.cleaned_data,
                actor=creator,
                context=context,
            )
        except ValidationError as exc:
            form.add_error("attachments", exc.message)
        else:
            logger.info(
                "colectivos_individual application=cotizacion_colectivos operation=external_submit "
                "branch=%s items=%d attachments=%d workspace=%s",
                schema.slug,
                quotation.item_count,
                quotation.attachment_count,
                metadata.get("storage", "local"),
            )
            return redirect(
                "colectivos_external:individual_confirmation",
                token=sign_receipt(quotation.public_id),
            )
    return render(request, "cotizacion_colectivos/individual/form.html", {
        "schema": schema,
        "schema_payload": {
            "repeatables": [asdict(item) for item in schema.repeatables],
            "initial": initial_items,
        },
        "form": form,
        "field_rows": tuple((field, form[field.key]) for field in schema.fields),
        "context": context,
        "detail": detail,
    })


@never_cache
@require_http_methods(["GET"])
def individual_confirmation(request, token):
    try:
        public_id = unsign_receipt(token)
        quotation = CotizacionIndividual.objects.only(
            "public_id", "branch_slug", "branch_code", "item_count",
            "attachment_count", "submitted_at",
        ).get(public_id=public_id)
    except (signing.BadSignature, CotizacionIndividual.DoesNotExist, ValueError) as exc:
        raise Http404("Confirmación no encontrada") from exc
    return render(request, "cotizacion_colectivos/individual/confirmation.html", {
        "quotation": quotation,
        "schema": get_branch_schema(quotation.branch_slug),
    })


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
        cookie = authorize_direct_access(access)
    except ExternalAccessError:
        return render(request, "cotizacion_colectivos/external/unavailable.html", status=410)
    return _set_external_cookie(redirect("colectivos_external:portal"), cookie)


@never_cache
@require_http_methods(["POST"])
def verify(request, token):
    return render(request, "cotizacion_colectivos/external/unavailable.html", status=410)


def _rows(request_obj):
    return request_obj.records.select_related("policy").only("public_key", "policy", "role", "initial_status", "entry_date", "exit_date", "plan", "economic_values", "encrypted_branch_payload").order_by("original_position")


def _display_rows(request_obj, snapshot):
    members = snapshot.get("group", []) if isinstance(snapshot, dict) else []
    result = []
    for index, record in enumerate(_rows(request_obj)):
        member = members[index] if index < len(members) and isinstance(members[index], dict) else {}
        result.append({
            "public_key": record.public_key,
            "role": record.role,
            "display_name": member.get("display_name", ""),
            "id_type": member.get("id_type", ""),
            "masked_document": member.get("masked_document", ""),
            "initial_status": record.initial_status,
            "plan": record.plan,
            "entry_date": record.entry_date,
            "exit_date": record.exit_date,
            "relationship": member.get("relationship", ""),
            "risk_summary": member.get("risk_summary", ""),
            "risk_attributes": member.get("risk_attributes", {}),
            "economic_values": dict(record.economic_values or {}),
            "email": member.get("email", ""),
            "phone": member.get("phone", ""),
            "mobile": member.get("mobile", ""),
            "associate_name": member.get("associate_name", ""),
            "associate_id_type": member.get("associate_id_type", ""),
            "associate_masked_document": member.get("associate_masked_document", ""),
            "insured_name": member.get("insured_name", ""),
            "insured_id_type": member.get("insured_id_type", ""),
            "insured_masked_document": member.get("insured_masked_document", ""),
            "beneficiary_name": member.get("beneficiary_name", ""),
            "beneficiary_id_type": member.get("beneficiary_id_type", ""),
            "beneficiary_masked_document": member.get("beneficiary_masked_document", ""),
            "associate_key": member.get("associate_key", ""),
            "insured_key": member.get("insured_key", ""),
            "beneficiary_key": member.get("beneficiary_key", ""),
            "risk_key": member.get("risk_key", ""),
        })
    return result


def _external_functional_groups(rows, *, branch_code):
    """Añade datos de lectura ya persistidos sin cambiar la consolidación funcional."""
    groups, warnings = consolidate_functional_groups(rows, branch_code=branch_code)
    if branch_code not in {"28", "40"}:
        return groups, warnings
    economics_by_risk = {}
    for row in rows:
        risk_key = str(row.get("risk_key") or "")
        if not risk_key:
            continue
        values = economics_by_risk.setdefault(risk_key, {})
        for label, value in dict(row.get("economic_values") or {}).items():
            if value not in (None, ""):
                values.setdefault(label, value)
    for group in groups:
        principal = group.get("principal", {})
        principal["economic_values"] = economics_by_risk.get(
            str(principal.get("key") or ""), {}
        )
    return groups, warnings


def _policy_sections(request_obj, snapshot):
    snapshots = snapshot.get("policies") if isinstance(snapshot, dict) else None
    snapshots = snapshots if isinstance(snapshots, list) and snapshots else [snapshot]
    policies = list(request_obj.policies.all())
    if not policies:
        rows = _display_rows(request_obj, snapshot)
        groups, grouping_warnings = _external_functional_groups(
            rows, branch_code=request_obj.branch_code,
        )
        return [{
            "policy": None, "snapshot": snapshots[0], "rows": rows,
            "allowed_actions": ("SIN_CAMBIOS", "MODIFICAR", "RETIRAR", "INCLUIR"),
            "functional_groups": groups,
            "grouping_warnings": grouping_warnings,
            "branch_code": request_obj.branch_code,
        }]
    sections = []
    action_map = {"SIN_CAMBIOS": "SIN_CAMBIOS", "MODIFICACION": "MODIFICAR", "RETIRO": "RETIRAR", "INCLUSION": "INCLUIR"}
    for index, policy in enumerate(policies):
        policy_snapshot = snapshots[index] if index < len(snapshots) else {}
        members = policy_snapshot.get("group", []) if isinstance(policy_snapshot, dict) else []
        rows = []
        for row_index, record in enumerate(_rows(request_obj).filter(policy=policy)):
            member = members[row_index] if row_index < len(members) and isinstance(members[row_index], dict) else {}
            rows.append({
                "public_key": record.public_key, "role": record.role,
                "display_name": member.get("display_name", ""),
                "id_type": member.get("id_type", ""),
                "masked_document": member.get("masked_document", ""),
                "initial_status": record.initial_status, "plan": record.plan,
                "entry_date": record.entry_date, "exit_date": record.exit_date,
                "relationship": member.get("relationship", ""),
                "risk_summary": member.get("risk_summary", ""),
                "risk_attributes": member.get("risk_attributes", {}),
                "economic_values": dict(record.economic_values or {}),
                "email": member.get("email", ""),
                "phone": member.get("phone", ""),
                "mobile": member.get("mobile", ""),
                "associate_name": member.get("associate_name", ""),
                "associate_id_type": member.get("associate_id_type", ""),
                "associate_masked_document": member.get("associate_masked_document", ""),
                "insured_name": member.get("insured_name", ""),
                "insured_id_type": member.get("insured_id_type", ""),
                "insured_masked_document": member.get("insured_masked_document", ""),
                "beneficiary_name": member.get("beneficiary_name", ""),
                "beneficiary_id_type": member.get("beneficiary_id_type", ""),
                "beneficiary_masked_document": member.get("beneficiary_masked_document", ""),
                "associate_key": member.get("associate_key", ""),
                "insured_key": member.get("insured_key", ""),
                "beneficiary_key": member.get("beneficiary_key", ""),
                "risk_key": member.get("risk_key", ""),
            })
        groups, grouping_warnings = _external_functional_groups(
            rows, branch_code=policy.branch_code,
        )
        sections.append({
            "policy": policy,
            "snapshot": policy_snapshot,
            "rows": rows,
            "allowed_actions": tuple(action_map[value] for value in policy.enabled_adjustments if value in action_map),
            "allows_include": "INCLUSION" in policy.enabled_adjustments,
            "functional_groups": groups,
            "grouping_warnings": grouping_warnings,
            "branch_code": policy.branch_code,
        })
    return sections


@never_cache
@require_http_methods(["GET"])
def portal(request):
    total_started = time.monotonic()
    correlation = request.headers.get("X-Correlation-ID", "").strip()
    if not correlation or len(correlation) > 64:
        correlation = uuid.uuid4().hex
    try:
        access = _access_from_cookie(request)
        snapshot_started = time.monotonic()
        snapshot = request_snapshot(access.request)
        snapshot_ms = round((time.monotonic() - snapshot_started) * 1000)
    except (ExternalAccessError, ValidationError):
        return _clear_external_cookie(render(request, "cotizacion_colectivos/external/unavailable.html", status=403))
    response_query_started = time.monotonic()
    latest = access.request.responses.filter(status=RespuestaSolicitudColectivo.Status.DRAFT).prefetch_related("changes").first()
    branch = COLLECTIVE_BRANCH_CONFIG.get(access.request.branch_code)
    response_query_ms = round((time.monotonic() - response_query_started) * 1000)
    grouping_started = time.monotonic()
    policy_sections = _policy_sections(access.request, snapshot)
    grouping_ms = round((time.monotonic() - grouping_started) * 1000)
    warning_count = sum(len(section["grouping_warnings"]) for section in policy_sections)
    if warning_count:
        logger.warning(
            "colectivos_functional_grouping application=cotizacion_colectivos "
            "operation=external_portal category=inconsistent_relationships warnings=%d "
            "profile=%s correlation=%s",
            warning_count, access.request.zoho_profile, correlation,
        )
    context = {
        "item": access.request,
        "snapshot": snapshot,
        "records": _display_rows(access.request, snapshot),
        "policy_sections": policy_sections,
        "latest": latest,
        "submit_form": ExternalSubmitForm(),
        "attachment_form": AttachmentUploadForm(),
        "branch": branch,
        "editable_branch": branch is not None,
        "contact_channel": settings.COLECTIVOS_EXTERNAL_CONTACT_CHANNEL,
        "contact_id_types": CONTACT_ID_TYPE_CHOICES,
        "relation_roles": RELATION_ROLE_CHOICES,
        "relationship_choices": RELATIONSHIP_CHOICES,
        "insured_state_choices": INSURED_STATE_CHOICES,
    }
    template_started = time.monotonic()
    html = render_to_string("cotizacion_colectivos/external/portal.html", context, request=request)
    template_ms = round((time.monotonic() - template_started) * 1000)
    render_started = time.monotonic()
    response = HttpResponse(html)
    render_ms = round((time.monotonic() - render_started) * 1000)
    logger.info(
        "colectivos_external_portal application=cotizacion_colectivos operation=client_workspace "
        "profile=%s snapshot_source=persisted snapshot_ms=%d response_query_ms=%d "
        "grouping_ms=%d template_ms=%d render_ms=%d total_ms=%d",
        access.request.zoho_profile, snapshot_ms, response_query_ms, grouping_ms,
        template_ms, render_ms, round((time.monotonic() - total_started) * 1000),
    )
    return response


def _posted_rows(request, request_obj):
    branch_fields = (
        "plan", "parentesco", "fecha_efectiva", "fecha_ingreso",
        "fecha_retiro", "motivo", "observaciones", "ciudad", "direccion",
        "tipo_uso", "anio_construccion", "descripcion", "valor_asegurado",
        "vehiculo", "placa", "marca", "modelo", "estado",
    )
    rows = []
    functional_keys = tuple(
        key.removeprefix("action_entity_")
        for key in request.POST
        if key.startswith("action_entity_")
    )
    for key in functional_keys:
        if len(key) != 64 or any(character not in "0123456789abcdef" for character in key):
            continue
        source_records = tuple(filter(None, request.POST.get(f"source_records_{key}", "").split(",")))
        row = {
            "record": source_records[0] if source_records else "",
            "records": source_records,
            "policy": request.POST.get(f"policy_{key}", ""),
            "action": request.POST.get(f"action_entity_{key}", "SIN_CAMBIOS"),
            "functional_key": key,
        }
        row.update({field: request.POST.get(f"{field}_entity_{key}", "") for field in branch_fields})
        rows.append(row)
    if functional_keys:
        return rows
    for record in _rows(request_obj):
        key = str(record.public_key)
        row = {"record": key, "action": request.POST.get(f"action_{key}", "SIN_CAMBIOS")}
        row.update({field: request.POST.get(f"{field}_{key}", "") for field in branch_fields})
        rows.append(row)
    policies = list(request_obj.policies.all())
    if not policies and request.POST.get("include_action") == "INCLUIR":
        policies = [None]
    for policy in policies:
        suffix = str(policy.pk) if policy else ""
        action_name = f"include_action_{suffix}" if suffix else "include_action"
        if request.POST.get(action_name) != "INCLUIR":
            continue
        row = {"record": "", "action": "INCLUIR", "policy": suffix}
        for field in ("tipo_id", "documento", "nombre", "rol", *branch_fields):
            name = f"include_{suffix}_{field}" if suffix else f"include_{field}"
            row[field] = request.POST.get(name, "")
        rows.append(row)
    return rows


@never_cache
@require_http_methods(["POST"])
def save_draft(request):
    try:
        access = _access_from_cookie(request)
        if access.request.branch_code not in COLLECTIVE_BRANCH_CONFIG:
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
        if not form.is_valid():
            raise ExternalAccessError("La respuesta no está lista para enviar.")
        if response is None:
            response = save_response(
                access=access,
                rows=[],
                observations=request.POST.get("client_observations", ""),
            )
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
    response["Content-Disposition"] = f'attachment; filename="{download_filename("Novedades", origin=access.request.client_label, request_id=access.request.public_id)}"'
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
