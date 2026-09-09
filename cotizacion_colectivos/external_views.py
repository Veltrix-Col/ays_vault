from __future__ import annotations

import logging
import time
import uuid
import json
import re
from pathlib import Path
from dataclasses import asdict

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404, HttpResponse
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .branches import COLLECTIVE_BRANCH_CONFIG, contract_required_ingress_fields
from .forms import AttachmentUploadForm, ExternalOTPForm, ExternalSubmitForm
from .models import AccesoCotizacionIndividual, AdjuntoSolicitudColectivo, CambioSolicitudColectivo, CotizacionIndividual, RenovacionColectiva, RespuestaSolicitudColectivo
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
    response_checksum,
    resolve_token,
    save_response,
    submit_response,
    verify_otp,
)
from vault.crypto import decrypt, encrypt
from vault.notifications import mask_email
from .services.requests import request_snapshot
from .services.functional_groups import consolidate_functional_groups
from .services.renewals import _monthly_period_label
from .services.mappings import (
    INSURED_STATE_CHOICES,
    RELATION_ROLE_CHOICES,
    RELATIONSHIP_CHOICES,
)
from .services.catalogs import CatalogUnavailable, identification_choice_pairs, identification_display_label, subrisk_relationship_choice_pairs
from .filenames import download_filename
from .quotation_forms.catalog import get_branch_schema, get_policy_branch_schema, with_identification_choices, with_relationship_choices
from .quotation_forms.forms import IndividualQuotationForm
from .quotation_forms.security import (
    sign_receipt,
    unsign_policy_context,
    unsign_receipt,
)
from .services.individual_quotations import (
    affiliate_options, clear_pending_uploads, create_individual_quotation,
    restore_pending_uploads, stash_pending_uploads,
)
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


_ACTIONABLE_DRAFT_ACTIONS = (
    CambioSolicitudColectivo.Action.INCLUDE,
    CambioSolicitudColectivo.Action.RETIRE,
    CambioSolicitudColectivo.Action.MODIFY,
)


def _prepared_draft_markers(response):
    """Return only functional actions explicitly prepared in the draft.

    Policy records are rendered as context elsewhere in the portal.  A
    ``SIN_CAMBIOS`` marker is a valid representation of that context, but it
    is not a client-prepared novelty and must never contribute to the prepared
    counter or cards.
    """
    if response is None:
        return ()
    return response.changes.filter(
        functional_field="accion",
        action__in=_ACTIONABLE_DRAFT_ACTIONS,
    ).order_by("position", "id")


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
    if schema.slug in {"vida", "salud"}:
        try:
            relationship_choices = subrisk_relationship_choice_pairs()
        except CatalogUnavailable as exc:
            raise signing.BadSignature(str(exc)) from exc
        schema = with_relationship_choices(schema, relationship_choices)
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
    pending_session_key = f"individual_pending_uploads:{access.selector}"
    pending_uploads = tuple(request.session.get(pending_session_key, ()))
    effective_files = request.FILES.copy()
    if pending_uploads:
        for field_name, uploaded in restore_pending_uploads(access_key=access.selector, pending=pending_uploads):
            if field_name and field_name not in effective_files:
                effective_files[field_name] = uploaded
    form = IndividualQuotationForm(
        request.POST or None,
        effective_files or None,
        schema=schema,
        context=context,
        identification_choices=identification_choices,
        initial={"items_payload": json.dumps(initial_items)},
    )
    form_valid = form.is_valid() if request.method == "POST" else False
    validation_messages = []
    if request.method == "POST" and not form_valid:
        # Keep the client-facing failure actionable without exposing payloads,
        # values, stack traces or SDK details.  The browser cannot repopulate
        # file inputs after a 200 response, so make the exact validation stage
        # visible instead of looking like a successful no-op.
        for field_name, errors in form.errors.items():
            label = form.fields[field_name].label if field_name in form.fields else "Información agregada"
            for error in errors:
                validation_messages.append(f"{label}: {error}")
        logger.info(
            "colectivos_individual application=cotizacion_colectivos operation=external_submit "
            "branch=%s result=validation_error stage=form fields=%s files_received=%d "
            "attachment_persisted=0",
            schema.slug,
            ",".join(sorted(str(name) for name in form.errors.keys())) or "unknown",
            len(request.FILES),
        )
        received = []
        pending_fields = {str(item.get("field") or "") for item in pending_uploads}
        for field_name in request.FILES:
            # Cotización Individual only accepts files owned by a concrete
            # person/entity. The former quotation-level upload is retired;
            # do not persist it temporarily during a validation rerender.
            if field_name != "affiliate_document" and not str(field_name).startswith("entity_attachment_"):
                continue
            if field_name in pending_fields:
                continue
            for uploaded in request.FILES.getlist(field_name):
                received.append((field_name, uploaded))
        if received:
            try:
                saved_pending = stash_pending_uploads(access_key=access.selector, uploaded_files=received)
            except ValidationError:
                saved_pending = ()
            if saved_pending:
                request.session[pending_session_key] = [*pending_uploads, *saved_pending]
                request.session.modified = True
    if request.method == "POST" and form_valid:
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
            message = str(exc.message if hasattr(exc, "message") else exc)
            form.add_error(None, message)
            validation_messages.append(message)
            logger.info(
                "colectivos_individual application=cotizacion_colectivos operation=external_submit "
                "branch=%s result=validation_error stage=persistence files_received=%d "
                "attachment_persisted=0",
                schema.slug, len(request.FILES),
            )
        else:
            logger.info(
                "colectivos_individual application=cotizacion_colectivos operation=external_submit "
                "branch=%s items=%d attachments=%d workspace=%s",
                schema.slug,
                quotation.item_count,
                quotation.attachment_count,
                metadata.get("storage", "local"),
            )
            if pending_uploads:
                clear_pending_uploads(pending=pending_uploads)
                request.session.pop(pending_session_key, None)
                request.session.modified = True
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
        "validation_messages": tuple(dict.fromkeys(validation_messages)),
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
        return _clear_external_cookie(render(request, "cotizacion_colectivos/external/unavailable.html", status=410))
    response = render(request, "cotizacion_colectivos/external/verify.html", {
        "form": ExternalOTPForm(),
        "token": token,
        "public_id": access.request.public_id,
        "masked_recipient": mask_email(decrypt(access.encrypted_recipient)),
        "verify_url": reverse("colectivos_external:verify", args=[token]),
    })
    # A link opened in the same browser must not leave the previous request's
    # session active while the new link is being verified.  Clearing the
    # cookie here makes the token/OTP flow authoritative and prevents the
    # tokenless portal URL from rendering an older request's draft.
    return _clear_external_cookie(response)


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
            "contract_fields": contract_required_ingress_fields(request_obj.branch_code, request_obj.branch_name),
        }]
    sections = []
    action_map = {"SIN_CAMBIOS": "SIN_CAMBIOS", "RETIRO": "RETIRAR", "INCLUSION": "INCLUIR"}
    # Requests created before policy-scoped records were introduced can still
    # have a valid encrypted snapshot and records attached only to the parent
    # request (policy=NULL).  Keep the snapshot/remote data authoritative, but
    # recover those records for a single-policy request so the portal does not
    # render an empty group and lose its action keys.
    all_request_rows = None
    for index, policy in enumerate(policies):
        policy_snapshot = snapshots[index] if index < len(snapshots) else {}
        members = policy_snapshot.get("group", []) if isinstance(policy_snapshot, dict) else []
        rows = []
        scoped_records = list(_rows(request_obj).filter(policy=policy))
        if not scoped_records and len(policies) == 1:
            if all_request_rows is None:
                all_request_rows = list(_rows(request_obj))
            # A one-policy request has no ambiguity: legacy parent-scoped
            # records represent exactly this policy and retain their stable
            # public keys for response actions.
            scoped_records = all_request_rows
        for row_index, record in enumerate(scoped_records):
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
            "contract_fields": contract_required_ingress_fields(policy.branch_code, policy.branch_name),
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
    prepared_changes = []
    prepared_edit_rows = []
    if latest:
        actionable_markers = _prepared_draft_markers(latest)
        for change in latest.changes.filter(
            action__in=_ACTIONABLE_DRAFT_ACTIONS,
        ).all():
            if not change.encrypted_new_value:
                continue
            try:
                value = decrypt(change.encrypted_new_value).strip()
            except (TypeError, ValueError):
                value = ""
            if value:
                saved_preview.append({"action": change.get_action_display(), "field": change.functional_field, "value": value})
        for marker in actionable_markers:
            values = {}
            for field_change in latest.changes.filter(position=marker.position, action=marker.action).exclude(functional_field="accion"):
                try:
                    values[field_change.functional_field] = decrypt(field_change.encrypted_new_value or "").strip()
                except (TypeError, ValueError):
                    values[field_change.functional_field] = ""
            def _human_date(value):
                try:
                    return timezone.datetime.fromisoformat(str(value)).strftime("%d/%m/%Y")
                except (TypeError, ValueError):
                    return str(value or "")
            prepared_changes.append({
                "id": marker.id,
                "action": marker.action,
                "label": marker.get_action_display(),
                "values": values,
                "display_name": " ".join(filter(None, (values.get("nombres", ""), values.get("apellidos", "")))),
                "tipo_id": values.get("tipo_id", ""),
                "tipo_id_label": identification_display_label(values.get("tipo_id", "")),
                "documento": values.get("documento", ""),
                "fecha_ingreso": values.get("fecha_ingreso", ""),
                "fecha_ingreso_display": _human_date(values.get("fecha_ingreso", "")),
                "fecha_retiro": values.get("fecha_retiro", ""),
                "fecha_retiro_display": _human_date(values.get("fecha_retiro", "")),
                "attachments": list(marker.attachments.exclude(category="EXCEL_IMPORT")),
            })
            prepared_edit_rows.append({
                "id": marker.id,
                "action": marker.action,
                "email": values.get("correo", values.get("email", "")),
                "phone": values.get("phone", values.get("telefono", "")),
                "birth_date": values.get("fecha_nacimiento", ""),
                "retirement_date": values.get("fecha_retiro", ""),
                "rol": values.get("rol", ""),
                "parentesco": values.get("parentesco", ""),
                "plan": values.get("plan", ""),
                "plate": values.get("placa", ""), "brand": values.get("marca", ""),
                "model": values.get("modelo", ""), "vehicle_class": values.get("clase", ""),
                "city": values.get("ciudad", ""), "use": values.get("tipo_uso", ""),
            })
            marker_branch_code = getattr(getattr(marker, "policy", None), "branch_code", "") or access.request.branch_code
            marker_branch_name = getattr(getattr(marker, "policy", None), "branch_name", "") or access.request.branch_name
            prepared_edit_rows[-1]["contract_fields"] = contract_required_ingress_fields(marker_branch_code, marker_branch_name)
            prepared_edit_rows[-1]["is_mobility"] = str(marker_branch_code).strip() in {"40", "movilidad", "autos"}
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
    relationship_catalog_choices = ()
    relationship_catalog_error = ""
    if any("parentesco" in section.get("contract_fields", ()) for section in policy_sections):
        try:
            relationship_catalog_choices = subrisk_relationship_choice_pairs()
        except CatalogUnavailable:
            relationship_catalog_error = "El catálogo de parentescos no está disponible. Intente nuevamente más tarde."
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
        # The absence of preloaded records is a normal portal state, not a
        # client-facing warning. Keep the snapshot warning intact for audit
        # and internal consumers while omitting only this presentation copy.
        "portal_warnings": tuple(
            warning for warning in (access.request.warnings or ())
            if warning != "La póliza no tiene registros relacionados precargados."
        ),
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
        "relationship_catalog_choices": relationship_catalog_choices,
        "relationship_catalog_error": relationship_catalog_error,
        "insured_state_choices": INSURED_STATE_CHOICES,
        "saved": request.GET.get("saved") == "1",
        "portal_error": request.session.pop("external_portal_error", ""),
        "saved_preview": tuple(saved_preview),
        "prepared_changes": tuple(prepared_changes),
        "prepared_edit_rows": tuple(prepared_edit_rows),
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
        "tipo_uso", "clase", "anio_construccion", "descripcion", "valor_asegurado",
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
    # Each prepared ingress drawer gets a stable ``__N`` suffix after the
    # first one.  The first drawer keeps the historical field names so old
    # clients and saved links remain compatible.
    include_actions = []
    for key in request.POST:
        match = re.fullmatch(r"include_action(?:_(\d+))?(?:__(\d+))?", key)
        if match:
            include_actions.append((key, match.group(1) or "", int(match.group(2) or 1)))
    if not include_actions and request.POST.get("include_action") == "INCLUIR":
        include_actions = [("include_action", "", 1)]
    for action_name, policy_suffix, occurrence in include_actions:
        if request.POST.get(action_name) != "INCLUIR":
            continue
        suffix = policy_suffix
        field_suffix = "" if occurrence == 1 else f"__{occurrence}"
        row = {"record": "", "action": "INCLUIR", "policy": suffix}
        for field in ("tipo_id", "documento", "nombre", "rol", *branch_fields):
            name = f"include_{suffix}_{field}{field_suffix}" if suffix else f"include_{field}{field_suffix}"
            row[field] = request.POST.get(name, "")
        row["include_occurrence"] = occurrence
        rows.append(row)
    return rows


@never_cache
@require_http_methods(["POST"])
def save_draft(request):
    try:
        access = _access_from_cookie(request)
        if access.request.branch_code not in COLLECTIVE_BRANCH_CONFIG:
            raise ExternalAccessError("El formulario editable aún no está habilitado para este ramo.")
        rows = _posted_rows(request, access.request)
        relationship_required = False
        relationship_field_posted = any("parentesco" in str(key) for key in request.POST.keys())
        for row in rows:
            if str(row.get("action") or "").strip().upper() != CambioSolicitudColectivo.Action.INCLUDE:
                continue
            policy_key = str(row.get("policy") or "").strip()
            policy = access.request.policies.filter(pk=int(policy_key)).first() if policy_key.isdigit() else None
            branch_code = policy.branch_code if policy is not None else access.request.branch_code
            branch_name = policy.branch_name if policy is not None else access.request.branch_name
            if relationship_field_posted and "parentesco" in contract_required_ingress_fields(branch_code, branch_name):
                relationship_required = True
                break
        relationship_choices = subrisk_relationship_choice_pairs() if relationship_required else None
        response = save_response(
            access=access, rows=rows,
            observations=request.POST.get("client_observations", ""),
            relationship_choices=relationship_choices,
        )
        # A support selected in the ingress drawer is stored only after the
        # corresponding CambioSolicitudColectivo exists.  The change marker is
        # resolved from the just-created response, so no client-supplied id is
        # trusted and the attachment remains scoped to this ingress.
        include_rows = [row for row in rows if row.get("action") == CambioSolicitudColectivo.Action.INCLUDE]
        for row in include_rows:
            policy = row.get("policy")
            occurrence = int(row.get("include_occurrence") or 1)
            field_suffix = "" if occurrence == 1 else f"__{occurrence}"
            field_name = f"include_{policy}_support{field_suffix}" if policy else f"include_support{field_suffix}"
            uploaded = request.FILES.get(field_name)
            if not uploaded:
                continue
            marker = response.changes.filter(
                action=CambioSolicitudColectivo.Action.INCLUDE,
                functional_field="accion",
                policy_id=int(policy) if str(policy).isdigit() else None,
            ).order_by("position", "id")[occurrence - 1:occurrence]
            marker = marker[0] if marker else None
            if marker is None:
                raise ValidationError("No fue posible asociar el soporte al ingreso.")
            store_attachment(response=response, uploaded=uploaded, change=marker)
    except (ExternalAccessError, ValidationError, CatalogUnavailable) as exc:
        message = str(exc.messages[0] if getattr(exc, "messages", None) else exc) or "No fue posible guardar."
        request.session["external_portal_error"] = message
        return redirect(reverse("colectivos_external:portal"))
    return redirect(f"{reverse('colectivos_external:portal')}?saved=1")


@never_cache
@require_http_methods(["POST"])
def edit_draft_change(request):
    """Edit one prepared ingress in the current external draft only."""
    try:
        access = _access_from_cookie(request)
        response = access.request.responses.filter(status=RespuestaSolicitudColectivo.Status.DRAFT).order_by("-version").first()
        change = response.changes.filter(
            pk=request.POST.get("change_id"),
            action__in=(CambioSolicitudColectivo.Action.INCLUDE, CambioSolicitudColectivo.Action.RETIRE),
            functional_field="accion",
        ).first() if response else None
        if change is None:
            raise ValidationError("La novedad preparada no está disponible.")
        fields = ("tipo_id", "documento", "nombres", "apellidos", "fecha_nacimiento", "fecha_ingreso", "fecha_retiro", "correo", "email", "phone", "rol", "parentesco", "plan", "observaciones")
        values = {
            field: str(request.POST.get(field) or "").strip()[:500]
            for field in fields if field in request.POST
        }
        branch_code = getattr(getattr(change, "policy", None), "branch_code", "") or access.request.branch_code
        branch_name = getattr(getattr(change, "policy", None), "branch_name", "") or access.request.branch_name
        required_fields = contract_required_ingress_fields(branch_code, branch_name)
        if change.action == CambioSolicitudColectivo.Action.INCLUDE and "parentesco" in required_fields and "parentesco" in request.POST:
            if not values.get("parentesco"):
                raise ValidationError("El parentesco es obligatorio para este ingreso.")
            if values["parentesco"] not in dict(subrisk_relationship_choice_pairs()):
                raise ValidationError("El parentesco seleccionado no es válido.")
        if change.action == CambioSolicitudColectivo.Action.RETIRE:
            if not values.get("fecha_retiro"):
                raise ValidationError("Indique la fecha de retiro.")
        elif not values.get("tipo_id") or not values.get("documento", "").isdigit() or not values.get("nombres") or not values.get("apellidos") or not values.get("fecha_ingreso"):
            raise ValidationError("Complete tipo de identificación, documento, nombres, apellidos y fecha de ingreso.")
        for field, value in values.items():
            sibling = response.changes.filter(
                action=change.action,
                functional_field=field,
                position=change.position,
            ).first()
            if value:
                existing_value = ""
                if sibling is not None and sibling.encrypted_new_value:
                    try:
                        existing_value = decrypt(sibling.encrypted_new_value).strip()
                    except (TypeError, ValueError):
                        existing_value = ""
                if sibling is not None and existing_value == value:
                    continue
                if sibling is None:
                    sibling = CambioSolicitudColectivo(response=response, policy=change.policy, action=change.action, functional_field=field, position=change.position, checksum="")
                sibling.encrypted_new_value = encrypt(value)
                sibling.encrypted_branch_payload = change.encrypted_branch_payload
                sibling.checksum = response_checksum([{
                    "action": change.action,
                    "field": field,
                    "position": change.position,
                    "policy_position": change.policy.position if change.policy else 0,
                    "value": value,
                }])
                sibling.save()
            elif sibling is not None:
                sibling.delete()
    except (ExternalAccessError, ValidationError, CatalogUnavailable) as exc:
        message = str(exc.messages[0] if getattr(exc, "messages", None) else exc) or "No fue posible editar la novedad."
        request.session["external_portal_error"] = message
        return redirect(reverse("colectivos_external:portal"))
    return redirect("colectivos_external:portal")


@never_cache
@require_http_methods(["POST"])
def remove_draft_change(request):
    """Remove one prepared ingress from the current draft, never history."""
    try:
        access = _access_from_cookie(request)
        response = access.request.responses.filter(status=RespuestaSolicitudColectivo.Status.DRAFT).order_by("-version").first()
        change = response.changes.filter(
            pk=request.POST.get("change_id"), action__in=(CambioSolicitudColectivo.Action.INCLUDE, CambioSolicitudColectivo.Action.RETIRE),
            functional_field="accion",
        ).first() if response else None
        if change is None:
            raise ValidationError("La novedad preparada no está disponible.")
        root = Path(settings.COLECTIVOS_PRIVATE_ROOT).resolve()
        for attachment in response.attachments.filter(change=change):
            target = (root / attachment.stored_path).resolve()
            if root in target.parents:
                target.unlink(missing_ok=True)
            attachment.delete()
        response.changes.filter(action=change.action, position=change.position).delete()
    except (ExternalAccessError, ValidationError):
        return HttpResponse("No fue posible quitar la novedad.", status=400)
    return redirect("colectivos_external:portal")


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
        posted_has_changes = has_prepared_changes
        existing_draft = access.request.responses.filter(
            status=RespuestaSolicitudColectivo.Status.DRAFT,
        ).order_by("-version").first()
        # Prepared changes may have been persisted by the ingress drawer or
        # Excel confirmation in an earlier request.  The final POST does not
        # need to replay every generated field; use that draft as the source
        # of truth when the browser submits no row controls.
        if not posted_has_changes and existing_draft and existing_draft.changes.filter(
            action__in={"INCLUIR", "RETIRAR", "MODIFICAR"},
        ).exists():
            has_prepared_changes = True
        logger.info(
            "external_submit parsed_rows=%d changed_rows=%d no_changes=%s",
            len(rows), sum(1 for row in rows if str(row.get("action", "")).strip().upper() in {"INCLUIR", "RETIRAR", "MODIFICAR"}), no_changes,
        )
        if not has_prepared_changes and not no_changes:
            raise ExternalAccessError("Registre al menos una novedad o confirme que no tiene novedades para este periodo.")
        relationship_choices = None
        if any("parentesco" in str(key) for key in request.POST.keys()):
            for row in rows:
                if str(row.get("action") or "").strip().upper() != CambioSolicitudColectivo.Action.INCLUDE:
                    continue
                policy_key = str(row.get("policy") or "").strip()
                policy = access.request.policies.filter(pk=int(policy_key)).first() if policy_key.isdigit() else None
                branch_code = policy.branch_code if policy is not None else access.request.branch_code
                branch_name = policy.branch_name if policy is not None else access.request.branch_name
                if "parentesco" in contract_required_ingress_fields(branch_code, branch_name):
                    relationship_choices = subrisk_relationship_choice_pairs()
                    break
        # The final POST is the source of truth.  Persist the prepared rows
        # immediately before submission so both automatic and manually-issued
        # links share the same contract and never depend on an intermediate
        # "save draft" click.
        if not posted_has_changes and existing_draft and existing_draft.changes.filter(
            action__in={"INCLUIR", "RETIRAR", "MODIFICAR"},
        ).exists():
            response = existing_draft
        else:
            response = save_response(access=access, rows=rows, observations="", relationship_choices=relationship_choices)
        submit_response(
            access=access,
            response=response,
            declaration=form.cleaned_data["declaration"],
            no_changes=no_changes,
        )
    except (ExternalAccessError, CatalogUnavailable) as exc:
        message = str(exc.messages[0] if getattr(exc, "messages", None) else exc) or "La respuesta no está lista para enviar."
        if isinstance(exc, CatalogUnavailable) or "parentesco" in message.casefold():
            request.session["external_portal_error"] = message
            return redirect(reverse("colectivos_external:portal"))
        return HttpResponse(message, status=400)
    return _clear_external_cookie(render(request, "cotizacion_colectivos/external/submitted.html", {"public_id": access.request.public_id}))


@never_cache
@require_http_methods(["POST"])
def upload_attachment(request):
    form = AttachmentUploadForm(request.POST, request.FILES)
    try:
        access = _access_from_cookie(request)
        response = access.request.responses.filter(status=RespuestaSolicitudColectivo.Status.DRAFT).first()
        change = None
        change_id = str(request.POST.get("change_id", "")).strip()
        if change_id:
            change = response.changes.filter(pk=change_id, action=CambioSolicitudColectivo.Action.INCLUDE, functional_field="accion").first() if response else None
            if change is None:
                raise ValidationError("La persona seleccionada no está disponible.")
        if not form.is_valid() or not response:
            raise ValidationError("No fue posible cargar el archivo.")
        metadata = {}
        if change:
            try:
                payload = json.loads(decrypt(change.encrypted_branch_payload or "{}"))
                metadata["functional_key"] = str(payload.get("functional_key") or "")
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        attachment = store_attachment(response=response, uploaded=form.cleaned_data["attachment"], change=change, safe_metadata_extra=metadata)
        if change:
            previous = list(response.attachments.filter(change=change, category="SOPORTE").exclude(pk=attachment.pk))
            root = Path(settings.COLECTIVOS_PRIVATE_ROOT).resolve()
            for old in previous:
                target = (root / old.stored_path).resolve()
                if root in target.parents:
                    target.unlink(missing_ok=True)
                old.delete()
    except (ExternalAccessError, ValidationError):
        return HttpResponse("No fue posible cargar el archivo.", status=400)
    return redirect("colectivos_external:portal")


@never_cache
@require_http_methods(["GET"])
def download_imported_excel(request, attachment_id):
    try:
        access = _access_from_cookie(request)
        attachment = access.request.attachments.get(pk=attachment_id, category="EXCEL_IMPORT")
        root = Path(settings.COLECTIVOS_PRIVATE_ROOT).resolve()
        target = (root / attachment.stored_path).resolve()
        if root not in target.parents or not target.is_file():
            raise ExternalAccessError("El archivo no está disponible.")
        response = FileResponse(target.open("rb"), content_type=attachment.detected_mime)
        response["Content-Disposition"] = f'attachment; filename="{attachment.safe_original_name}"'
        response["Cache-Control"] = "private, no-store"
        return response
    except (ExternalAccessError, AdjuntoSolicitudColectivo.DoesNotExist):
        return HttpResponse("El archivo no está disponible.", status=404)


@never_cache
@require_http_methods(["GET"])
def download_external_attachment(request, attachment_id):
    try:
        access = _access_from_cookie(request)
        attachment = access.request.attachments.get(pk=attachment_id)
        root = Path(settings.COLECTIVOS_PRIVATE_ROOT).resolve()
        target = (root / attachment.stored_path).resolve()
        if root not in target.parents or not target.is_file():
            raise ExternalAccessError("El archivo no está disponible.")
        response = FileResponse(target.open("rb"), content_type=attachment.detected_mime)
        response["Content-Disposition"] = f'attachment; filename="{attachment.safe_original_name}"'
        response["Cache-Control"] = "private, no-store"
        return response
    except (ExternalAccessError, AdjuntoSolicitudColectivo.DoesNotExist):
        return HttpResponse("El archivo no está disponible.", status=404)


@never_cache
@require_http_methods(["POST"])
def download_template(request):
    try:
        access = _access_from_cookie(request)
        content = build_novelties_template(
            access.request,
            identification_choices=identification_choice_pairs(),
        )
    except (ExternalAccessError, CatalogUnavailable):
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
        item, token = create_preview(
            access=access,
            session_cookie=request.COOKIES.get(EXTERNAL_COOKIE, ""),
            uploaded=uploaded,
            identification_choices=identification_choice_pairs(),
        )
    except (ExternalAccessError, CatalogUnavailable):
        return HttpResponse("El archivo no está disponible para validación.", status=400)
    except ValidationError as exc:
        messages = list(getattr(exc, "messages", ()) or ("El archivo no supera la validación.",))
        return render(request, "cotizacion_colectivos/external/excel_validation_error.html", {"errors": messages}, status=400)
    return redirect("colectivos_external:excel_preview", token=token)


@never_cache
@require_http_methods(["POST"])
def remove_excel_import(request):
    """Remove only the pending Excel import from the current draft.

    Changes are removed only when their signed payload records origin=EXCEL;
    manually prepared web changes are therefore preserved.
    """
    try:
        access = _access_from_cookie(request)
        with transaction.atomic():
            response = access.request.responses.select_for_update().filter(
                status=RespuestaSolicitudColectivo.Status.DRAFT,
            ).order_by("-version").first()
            if response is None:
                raise ExternalAccessError("No hay una importación pendiente.")
            excel_changes = []
            for change in response.changes.all():
                try:
                    payload = json.loads(decrypt(change.encrypted_branch_payload or "{}"))
                except (TypeError, ValueError, json.JSONDecodeError):
                    payload = {}
                if payload.get("origin") == RespuestaSolicitudColectivo.Origin.EXCEL:
                    excel_changes.append(change)
            if excel_changes:
                response.changes.filter(pk__in=[change.pk for change in excel_changes]).delete()
            response.attachments.filter(category="EXCEL_IMPORT").delete()
    except (ExternalAccessError, ValidationError):
        return HttpResponse("No fue posible quitar la importación.", status=400)
    return redirect("colectivos_external:portal")


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
    except ExternalAccessError as exc:
        message = str(exc.messages[0] if getattr(exc, "messages", ()) else "")
        safe_messages = {
            "La sesión externa no es válida.": "Tu sesión de verificación no es válida. Vuelve a ingresar desde el enlace para continuar.",
            "La sesión externa no está disponible.": "Este acceso ya no está disponible. Comunícate con tu contacto en A&S.",
            "La solicitud no admite cambios.": "Esta solicitud ya no admite cambios.",
        }
        return render(
            request,
            "cotizacion_colectivos/external/excel_validation_error.html",
            {"errors": [safe_messages.get(message, "No pudimos continuar con la importación. Vuelve al formulario e intenta nuevamente.")]},
            status=400,
        )
    except ValidationError as exc:
        messages = list(getattr(exc, "messages", ()) or ("No pudimos cargar las novedades del archivo. Intenta nuevamente.",))
        return render(request, "cotizacion_colectivos/external/excel_validation_error.html", {"errors": messages}, status=400)
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
