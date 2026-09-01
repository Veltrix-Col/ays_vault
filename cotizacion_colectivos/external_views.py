from __future__ import annotations

import logging
import time
import uuid
import json
import re
from dataclasses import asdict

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .branches import COLLECTIVE_BRANCH_CONFIG
from .forms import AttachmentUploadForm, ExternalOTPForm, ExternalSubmitForm
from .models import AccesoCotizacionIndividual, CambioSolicitudColectivo, CotizacionIndividual, RenovacionColectiva, RespuestaSolicitudColectivo
from .services.attachments import store_attachment
from .services.excel_roundtrip import build_novelties_template
from .services.excel_previews import cancel_preview, confirm_preview, create_preview, resolve_preview
from .services.external import (
    EXTERNAL_COOKIE,
    ExternalAccessError,
    issue_otp,
    authorize_direct_access,
    resolve_external_session,
    resolve_no_changes_token,
    resolve_token,
    save_response,
    submit_response,
    verify_otp,
)
from vault.crypto import decrypt
from vault.notifications import mask_email
from .services.requests import request_snapshot
from .services.functional_groups import consolidate_functional_groups
from .services.renewals import _monthly_period_label
from .services.mappings import (
    INSURED_STATE_CHOICES,
    RELATION_ROLE_CHOICES,
    RELATIONSHIP_CHOICES,
)
from .services.catalogs import CatalogUnavailable, identification_choice_pairs
from .filenames import download_filename
from .quotation_forms.catalog import get_branch_schema, get_policy_branch_schema, with_identification_choices
from .quotation_forms.forms import IndividualQuotationForm
from .quotation_forms.security import (
    sign_receipt,
    unsign_policy_context,
    unsign_receipt,
)
from .services.individual_quotations import affiliate_options, create_individual_quotation
from .services.common import ColectivosServiceError
from .services.individual_access import (
    INDIVIDUAL_COOKIE,
    IndividualAccessError,
    access_context,
    consume_individual_access,
    individual_otp_required,
    issue_individual_otp,
    record_individual_direct_access,
    resolve_individual_session,
    resolve_individual_token,
    verify_individual_otp,
)
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
    try:
        identification_choices = identification_choice_pairs()
    except CatalogUnavailable as exc:
        raise signing.BadSignature(str(exc)) from exc
    schema = with_identification_choices(
        get_policy_branch_schema(detail.branch_code, detail.branch_name),
        identification_choices,
    )
    if schema.slug != context.get("branch_slug") or schema.version != context.get("schema_version"):
        raise signing.BadSignature("El formulario ya no corresponde a la póliza.")
    affiliate_key = str(context.get("affiliate_key") or "")
    if affiliate_key and affiliate_key not in {
        option.key for option in affiliate_options(members)
    }:
        raise signing.BadSignature("El afiliado ya no pertenece al contexto.")
    return detail, members, metadata, schema, identification_choices


@never_cache
@require_http_methods(["GET", "POST"])
def individual_quotation(request, token):
    try:
        access = resolve_individual_token(token)
        if individual_otp_required(access):
            resolve_individual_session(request.COOKIES.get(INDIVIDUAL_COOKIE, ""), access)
        else:
            record_individual_direct_access(access)
        context = access_context(access)
        detail, _members, metadata, schema, identification_choices = _individual_workspace(context)
    except IndividualAccessError:
        if request.method != "GET":
            return render(request, "cotizacion_colectivos/external/unavailable.html", status=410)
        try:
            access = resolve_individual_token(token)
            if not individual_otp_required(access):
                return render(request, "cotizacion_colectivos/external/unavailable.html", status=410)
            issue_individual_otp(access)
        except IndividualAccessError:
            return render(request, "cotizacion_colectivos/external/unavailable.html", status=410)
        return render(request, "cotizacion_colectivos/external/verify.html", {
            "form": ExternalOTPForm(),
            "token": token,
            "public_id": "Cotización individual",
            "masked_recipient": mask_email(decrypt(access.encrypted_recipient)),
            "verify_url": reverse("colectivos_external:individual_verify", args=[token]),
        })
    except (ColectivosServiceError, signing.BadSignature, Http404, KeyError, ValueError):
        return render(request, "cotizacion_colectivos/external/unavailable.html", status=410)

    # Repeatable entities are intentionally opt-in.  In particular, Mobility
    # must not present an empty "Vehículo 1" before the client adds one.
    # Repeatable people are explicitly opt-in.  The Salud primary insured is
    # derived from the canonical affiliate fields by the client UI and is
    # serialized on submit; it must not appear as an empty placeholder card.
    initial_items = {group.key: [] for group in schema.repeatables}
    form = IndividualQuotationForm(
        request.POST or None,
        request.FILES or None,
        schema=schema,
        context=context,
        identification_choices=identification_choices,
        initial={"items_payload": json.dumps(initial_items)},
    )
    if request.method == "POST" and form.is_valid():
        creator = get_user_model().objects.filter(
            pk=context.get("creator_id"), is_active=True,
        ).first()
        try:
            with transaction.atomic():
                quotation = create_individual_quotation(
                    schema=schema,
                    cleaned_data=form.cleaned_data,
                    actor=creator,
                    context=context,
                )
                consume_individual_access(access, quotation)
        except (ValidationError, IndividualAccessError) as exc:
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
        "declared_company_field": form["declared_company"] if "declared_company" in form.fields else None,
        "context": context,
        "detail": detail,
    })


@never_cache
@require_http_methods(["POST"])
def individual_verify(request, token):
    try:
        access = resolve_individual_token(token)
        if not individual_otp_required(access):
            return render(request, "cotizacion_colectivos/external/unavailable.html", status=410)
    except IndividualAccessError:
        return render(request, "cotizacion_colectivos/external/unavailable.html", status=410)
    form = ExternalOTPForm(request.POST)
    error = ""
    if form.is_valid():
        try:
            cookie = verify_individual_otp(access, form.cleaned_data["code"])
        except IndividualAccessError:
            if access.otp_expires_at and access.otp_expires_at <= timezone.now():
                error = "El código de verificación venció. Solicite uno nuevo."
            else:
                error = "El código no es válido o superó los intentos permitidos."
        else:
            response = redirect("colectivos_external:individual_quotation", token=token)
            response.set_cookie(
                INDIVIDUAL_COOKIE, cookie,
                max_age=settings.COLECTIVOS_EXTERNAL_SESSION_TTL_SECONDS,
                secure=not settings.DEBUG, httponly=True, samesite="Lax",
                path="/solicitudes/colectivos/externa/cotizacion-individual/",
            )
            return response
    return render(request, "cotizacion_colectivos/external/verify.html", {
        "form": form,
        "error": error,
        "token": token,
        "public_id": "Cotización individual",
        "masked_recipient": mask_email(decrypt(access.encrypted_recipient)),
        "verify_url": reverse("colectivos_external:individual_verify", args=[token]),
    }, status=400)


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
        issue_otp(access)
    except ExternalAccessError:
        return render(request, "cotizacion_colectivos/external/unavailable.html", status=410)
    return render(request, "cotizacion_colectivos/external/verify.html", {
        "form": ExternalOTPForm(),
        "token": token,
        "public_id": access.request.public_id,
        "masked_recipient": mask_email(decrypt(access.encrypted_recipient)),
        "verify_url": reverse("colectivos_external:verify", args=[token]),
    })


@never_cache
@require_http_methods(["POST"])
def verify(request, token):
    try:
        access = resolve_token(token)
    except ExternalAccessError:
        return render(request, "cotizacion_colectivos/external/unavailable.html", status=410)
    form = ExternalOTPForm(request.POST)
    error = ""
    if form.is_valid():
        try:
            cookie = verify_otp(access, form.cleaned_data["code"])
        except ExternalAccessError:
            error = "El código no es válido, expiró o superó los intentos permitidos."
        else:
            return _set_external_cookie(redirect("colectivos_external:portal"), cookie)
    return render(request, "cotizacion_colectivos/external/verify.html", {
        "form": form,
        "error": error,
        "token": token,
        "public_id": access.request.public_id,
        "masked_recipient": mask_email(decrypt(access.encrypted_recipient)),
        "verify_url": reverse("colectivos_external:verify", args=[token]),
    }, status=400)


@never_cache
@require_http_methods(["GET"])
def no_changes_entry(request, token):
    try:
        cycle = resolve_no_changes_token(token)
    except ExternalAccessError:
        return render(request, "cotizacion_colectivos/external/unavailable.html", status=410)
    if cycle.status == RenovacionColectiva.Status.RESPONDED:
        return render(request, "cotizacion_colectivos/external/no_changes_submitted.html", {"cycle": cycle})
    return render(request, "cotizacion_colectivos/external/no_changes_confirm.html", {
        "cycle": cycle,
        "monthly_period_label": _monthly_period_label(cycle.monthly_period),
        "token": token,
    })


@never_cache
@require_http_methods(["POST"])
def no_changes_confirm(request, token):
    try:
        cycle = resolve_no_changes_token(token)
        if cycle.status == RenovacionColectiva.Status.RESPONDED:
            return render(request, "cotizacion_colectivos/external/no_changes_submitted.html", {"cycle": cycle})
        if cycle.status not in {RenovacionColectiva.Status.SENT, RenovacionColectiva.Status.ALERT} or not cycle.access_id:
            raise ExternalAccessError("Este enlace ya no está disponible.")
        access = cycle.access
        # authorize_direct_access returns a signed session cookie.  The
        # persistence services require the actual ORM access instance, so
        # keep the model reference and refresh it after authorization.
        authorize_direct_access(access)
        access.refresh_from_db()
        response = save_response(access=access, rows=[], observations="")
        submit_response(access=access, response=response, declaration=True, no_changes=True)
    except ExternalAccessError:
        return render(request, "cotizacion_colectivos/external/unavailable.html", status=410)
    return render(request, "cotizacion_colectivos/external/no_changes_submitted.html", {"cycle": cycle})


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
            "allowed_actions": ("SIN_CAMBIOS", "RETIRAR", "INCLUIR"),
            "allows_include": True,
            "functional_groups": groups,
            "grouping_warnings": grouping_warnings,
            "branch_code": request_obj.branch_code,
        }]
    sections = []
    action_map = {"SIN_CAMBIOS": "SIN_CAMBIOS", "RETIRO": "RETIRAR", "INCLUSION": "INCLUIR"}
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
    saved_preview = []
    if latest:
        for change in latest.changes.all():
            if not change.encrypted_new_value:
                continue
            try:
                value = decrypt(change.encrypted_new_value).strip()
            except (TypeError, ValueError):
                value = ""
            if value:
                saved_preview.append({"action": change.get_action_display(), "field": change.functional_field, "value": value})
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
    # A read-only snapshot without policy-scoped inclusion controls must stay
    # renderable even when Zoho metadata is unavailable.  Load the catalog
    # only when this portal actually exposes a policy inclusion form.
    if access.request.policies.exists():
        try:
            identification_choices = identification_choice_pairs()
        except CatalogUnavailable:
            return _clear_external_cookie(render(request, "cotizacion_colectivos/external/unavailable.html", status=503))
    else:
        identification_choices = ()
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
        "contact_id_types": identification_choices,
        "relation_roles": RELATION_ROLE_CHOICES,
        "relationship_choices": RELATIONSHIP_CHOICES,
        "insured_state_choices": INSURED_STATE_CHOICES,
        "saved": request.GET.get("saved") == "1",
        "saved_preview": tuple(saved_preview),
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
        "plan", "parentesco", "fecha_nacimiento", "fecha_efectiva", "fecha_ingreso",
        "fecha_retiro", "motivo", "observaciones", "ciudad", "direccion",
        "tipo_uso", "anio_construccion", "descripcion", "valor_asegurado",
        "vehiculo", "placa", "marca", "modelo", "estado", "nombres", "apellidos",
        "email", "phone",
    )
    rows = []
    functional_keys = tuple(
        key.removeprefix("action_entity_")
        for key in request.POST
        if key.startswith("action_entity_")
        and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", key.removeprefix("action_entity_"))
    )
    for key in functional_keys:
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
    if not functional_keys:
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
    return redirect(f"{reverse('colectivos_external:portal')}?saved=1")


@never_cache
@require_http_methods(["POST"])
def submit(request):
    form = ExternalSubmitForm(request.POST)
    try:
        access = _access_from_cookie(request)
        if not form.is_valid():
            raise ExternalAccessError("La respuesta no está lista para enviar.")
        no_changes = bool(form.cleaned_data.get("no_changes"))
        if no_changes:
            raise ExternalAccessError("La confirmación sin novedades debe realizarse desde el enlace del correo.")
        rows = _posted_rows(request, access.request)
        has_prepared_changes = any(
            str(row.get("action", "")).strip().upper() in {"INCLUIR", "RETIRAR", "MODIFICAR"}
            for row in rows
        )
        logger.info(
            "external_submit parsed_rows=%d changed_rows=%d no_changes=%s",
            len(rows), sum(1 for row in rows if str(row.get("action", "")).strip().upper() in {"INCLUIR", "RETIRAR", "MODIFICAR"}), no_changes,
        )
        if not has_prepared_changes and not no_changes:
            raise ExternalAccessError("Registre al menos una novedad o confirme que no tiene novedades para este periodo.")
        # The final POST is the source of truth.  Persist the prepared rows
        # immediately before submission so both automatic and manually-issued
        # links share the same contract and never depend on an intermediate
        # "save draft" click.
        response = save_response(access=access, rows=rows, observations="")
        submit_response(
            access=access,
            response=response,
            declaration=form.cleaned_data["declaration"],
            no_changes=no_changes,
        )
    except ExternalAccessError as exc:
        message = str(exc.messages[0] if exc.messages else "La respuesta no está lista para enviar.")
        return HttpResponse(message, status=400)
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
