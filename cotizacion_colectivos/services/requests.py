from __future__ import annotations

import hashlib
import json
import time
from datetime import date

from django.conf import settings
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.crypto import salted_hmac

from cotizacion_colectivos.models import (
    EventoSolicitudColectivo,
    NotificacionColectivos,
    SolicitudColectivo,
    SolicitudColectivoPoliza,
    SolicitudColectivoRegistro,
)
from vault.crypto import decrypt, encrypt

from .common import DETAIL_SALT, ZOHO_ID, ColectivosServiceError, sign_record_id, unsign_record_context
from .policies import PolicyService
from cotizacion_colectivos.adjustments import (
    ADJUSTMENT_CATALOG_VERSION,
    BRANCH_ADJUSTMENTS,
    validate_adjustment_codes,
)


def _hash_reference(value: object) -> str:
    return salted_hmac("cotizacion_colectivos.reference.v1", str(value or ""), secret=settings.SECRET_KEY).hexdigest()


def _store_policy_reference(token: str) -> str:
    context = unsign_record_context(token, "policy")
    return json.dumps(
        {
            "id": context["id"],
            "source_id": context.get("source_id", ""),
            "source_kind": context.get("source_kind", ""),
        },
        sort_keys=True,
    )


def _restore_policy_token(stored: str) -> str:
    try:
        context = json.loads(stored)
    except (TypeError, json.JSONDecodeError):
        try:
            context = signing.loads(stored, salt=DETAIL_SALT)
        except signing.BadSignature as exc:
            raise ValidationError("La referencia protegida de la póliza no es válida.") from exc
    if not isinstance(context, dict) or not ZOHO_ID.fullmatch(str(context.get("id") or "")):
        raise ValidationError("La referencia protegida de la póliza no es válida.")
    source_id = str(context.get("source_id") or "")
    source_kind = context.get("source_kind")
    signed_context = None
    if source_id or source_kind:
        if not ZOHO_ID.fullmatch(source_id) or source_kind not in {"company", "person"}:
            raise ValidationError("La referencia protegida de la póliza no es válida.")
        signed_context = {"source_id": source_id, "source_kind": source_kind}
    return sign_record_id(context["id"], "policy", signed_context)


def request_reference_hashes(*, token: str, source_kind: str, holder: str = "") -> tuple[str, str]:
    context = unsign_record_context(token, "policy")
    if source_kind not in {"company", "person"}:
        raise ColectivosServiceError("invalid_record", "El origen de la solicitud no es válido.")
    token_source = context.get("source_kind")
    if token_source and token_source != source_kind:
        raise ColectivosServiceError("invalid_record", "El origen de la solicitud no coincide con la ficha.")
    source_reference = context.get("source_id") or f"{source_kind}:{holder}"
    return _hash_reference(context["id"]), _hash_reference(source_reference)


def source_reference_hash(*, token: str, source_kind: str) -> str:
    context = unsign_record_context(token, source_kind)
    if context.get("type") not in {source_kind, "contact"}:
        raise ColectivosServiceError("invalid_record", "El origen de la solicitud no es válido.")
    return _hash_reference(context["id"])


def _date(value: str):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _policy_warnings(detail, members) -> list[str]:
    warnings = list(detail.warnings)
    total = len(members)
    if not total:
        warnings.append("La póliza no tiene registros relacionados precargados.")
        return list(dict.fromkeys(warnings))
    missing_name = sum(str(item.display_name or "").strip() in {"", "Información protegida"} for item in members)
    missing_document = sum(not str(item.document or "").strip() for item in members)
    if missing_name:
        warnings.append(f"Nombres incompletos en {missing_name} de {total} registros ({missing_name * 100 // total}%).")
    if missing_document:
        warnings.append(f"Identificación incompleta en {missing_document} de {total} registros ({missing_document * 100 // total}%).")
    return list(dict.fromkeys(warnings))


def _snapshot_payload(detail, members, profile: str, adjustments=()) -> dict[str, object]:
    return {
        "version": 1,
        "profile": profile,
        "policy": {
            # La referencia completa permanece dentro del snapshot cifrado y
            # sólo se presenta en superficies internas autorizadas.
            "reference": detail.full_reference or detail.masked_reference,
            "branch_code": detail.branch_code,
            "branch_name": detail.branch_name,
            "insurer": detail.insurer,
            "state": detail.state,
            "holder": detail.holder,
            "start_date": detail.start_date,
            "end_date": detail.end_date,
            "payment_mode": detail.payment_mode,
            "frequency": detail.frequency,
        },
        "group": [
            {
                "role": item.role,
                "display_name": item.display_name,
                "id_type": item.id_type,
                "masked_document": item.masked_document,
                "document": item.document,
                "state": item.state,
                "entry_date": item.entry_date,
                "exit_date": item.exit_date,
                "plan": item.plan,
                "relationship": item.relationship,
                "risk_summary": item.risk_summary,
                "risk_attributes": dict(item.risk_attributes),
                "email": item.email,
                "phone": item.phone,
                "mobile": item.mobile,
                "economic_values": dict(item.economic_values),
                "associate_name": item.associate_name,
                "associate_id_type": item.associate_id_type,
                "associate_document": item.associate_document,
                "associate_masked_document": item.associate_masked_document,
                "insured_name": item.insured_name,
                "insured_id_type": item.insured_id_type,
                "insured_document": item.insured_document,
                "insured_masked_document": item.insured_masked_document,
                "beneficiary_name": item.beneficiary_name,
                "beneficiary_id_type": item.beneficiary_id_type,
                "beneficiary_document": item.beneficiary_document,
                "beneficiary_masked_document": item.beneficiary_masked_document,
                "associate_key": item.associate_key,
                "insured_key": item.insured_key,
                "beneficiary_key": item.beneficiary_key,
                "risk_key": item.risk_key,
            }
            for item in members
        ],
        "warnings": _policy_warnings(detail, members),
        "enabled_adjustments": list(adjustments),
    }


def _replace_records(request: SolicitudColectivo, members, *, policy=None, start_position=1, clear=True, metrics=None) -> int:
    if clear:
        request.records.all().delete()
    position = start_position
    pending_records = []
    for member in members:
        safe_payload = {
            "display_name": member.display_name,
            "id_type": member.id_type,
            "masked_document": member.masked_document,
            "document": member.document,
            "relationship": member.relationship,
            "risk_summary": member.risk_summary,
            "risk_attributes": dict(member.risk_attributes),
            "email": member.email,
            "phone": member.phone,
            "mobile": member.mobile,
            "associate_name": member.associate_name,
            "associate_id_type": member.associate_id_type,
            "associate_document": member.associate_document,
            "associate_masked_document": member.associate_masked_document,
            "insured_name": member.insured_name,
            "insured_id_type": member.insured_id_type,
            "insured_document": member.insured_document,
            "insured_masked_document": member.insured_masked_document,
            "beneficiary_name": member.beneficiary_name,
            "beneficiary_id_type": member.beneficiary_id_type,
            "beneficiary_document": member.beneficiary_document,
            "beneficiary_masked_document": member.beneficiary_masked_document,
            "associate_key": member.associate_key,
            "insured_key": member.insured_key,
            "beneficiary_key": member.beneficiary_key,
            "risk_key": member.risk_key,
        }
        checksum = hashlib.sha256(json.dumps(safe_payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if "Beneficiario" in member.role:
            element_type = SolicitudColectivoRegistro.ElementType.BENEFICIARY
        elif member.role == "Registro relacionado" and member.risk_summary:
            element_type = SolicitudColectivoRegistro.ElementType.RISK
        else:
            element_type = SolicitudColectivoRegistro.ElementType.PERSON
        pending_records.append(SolicitudColectivoRegistro(
            request=request,
            policy=policy,
            element_type=element_type,
            role=member.role[:40],
            external_reference_hash=_hash_reference(f"{position}:{member.role}:{member.masked_document}"),
            initial_status=member.state[:80],
            entry_date=_date(member.entry_date),
            exit_date=_date(member.exit_date),
            plan=member.plan[:120],
            economic_values=dict(member.economic_values),
            encrypted_branch_payload=encrypt(json.dumps(safe_payload, ensure_ascii=False, sort_keys=True)),
            original_position=position,
            checksum=checksum,
            active="activo" in member.state.casefold(),
        ))
        position += 1
    bulk_started = time.monotonic()
    if pending_records:
        SolicitudColectivoRegistro.objects.bulk_create(pending_records, batch_size=500)
    if metrics is not None:
        metrics["registro_bulk_create_ms"] = metrics.get("registro_bulk_create_ms", 0) + round(
            (time.monotonic() - bulk_started) * 1000
        )
    return position


@transaction.atomic
def create_request_from_policy(*, token: str, source_kind: str, actor, assigned_to, request_type: str, deadline, internal_notes: str = "", is_test: bool = False, service: PolicyService | None = None) -> SolicitudColectivo:
    if source_kind not in {"company", "person"}:
        raise ColectivosServiceError("invalid_record", "El origen de la solicitud no es válido.")
    service = service or PolicyService()
    detail, members = service.group(token, source_kind=source_kind)
    if detail.classification != "confirmed" or detail.branch_code not in BRANCH_ADJUSTMENTS:
        raise ColectivosServiceError("invalid_record", "La póliza no tiene una clasificación segura.")
    policy_hash, source_hash = request_reference_hashes(token=token, source_kind=source_kind, holder=detail.holder)
    active_states = (
        SolicitudColectivo.Status.DRAFT,
        SolicitudColectivo.Status.READY,
        SolicitudColectivo.Status.SENT,
        SolicitudColectivo.Status.OPENED,
        SolicitudColectivo.Status.CORRECTION,
    )
    active_candidates = SolicitudColectivo.objects.filter(
        Q(policy_reference_hash=policy_hash) | Q(policies__policy_reference_hash=policy_hash),
        source_reference_hash=source_hash,
        zoho_profile=service.profile,
        request_type=request_type,
        status__in=active_states,
        deadline__gt=timezone.localdate(),
        assigned_to__isnull=False,
    ).exclude(encrypted_snapshot="").distinct().prefetch_related("policies")
    if any(
        (
            {policy.policy_reference_hash for policy in candidate.policies.all()}
            or {candidate.policy_reference_hash}
        )
        == {policy_hash}
        for candidate in active_candidates
    ):
        raise ColectivosServiceError("duplicate", "Ya existe una solicitud activa del mismo tipo para esta póliza.")
    persistence_started = time.monotonic()
    adjustment_codes = BRANCH_ADJUSTMENTS[detail.branch_code]
    payload = _snapshot_payload(detail, members, service.profile, adjustment_codes)
    encrypted_payload = encrypt(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    request = SolicitudColectivo.objects.create(
        source_kind=source_kind,
        source_reference_hash=source_hash,
        policy_reference_hash=policy_hash,
        encrypted_policy_token=encrypt(token),
        masked_policy_reference=detail.masked_reference,
        client_label=detail.holder or "Cliente sin etiqueta",
        branch_code=detail.branch_code,
        branch_name=detail.branch_name,
        request_type=request_type,
        assigned_to=assigned_to,
        deadline=deadline,
        zoho_profile=service.profile,
        encrypted_snapshot=encrypted_payload,
        record_count=len(members),
        warnings=list(payload["warnings"]),
        encrypted_internal_notes=encrypt(internal_notes.strip()),
        is_test=is_test,
        created_by=actor,
    )
    policy = SolicitudColectivoPoliza.objects.create(
        request=request,
        policy_reference_hash=policy_hash,
        encrypted_policy_token=encrypt(_store_policy_reference(token)),
        masked_policy_reference=detail.masked_reference,
        branch_code=detail.branch_code,
        branch_name=detail.branch_name,
        insurer=detail.insurer,
        policy_status=detail.state,
        start_date=_date(detail.start_date),
        end_date=_date(detail.end_date),
        parameter_version=ADJUSTMENT_CATALOG_VERSION,
        enabled_adjustments=list(adjustment_codes),
        encrypted_snapshot=encrypted_payload,
        snapshot_checksum=hashlib.sha256(encrypted_payload.encode()).hexdigest(),
        record_count=len(members),
        warnings=list(payload["warnings"]),
        position=1,
    )
    metrics = getattr(service, "timings", None)
    _replace_records(request, members, policy=policy, metrics=metrics)
    EventoSolicitudColectivo.objects.create(request=request, actor=actor, event_type="CREATED", new_status=request.status, safe_metadata={"records": request.record_count, "branch": request.branch_code})
    # La entidad persiste el snapshot y la trazabilidad del enlace, pero la
    # generación directa no crea trabajo administrativo para el analista.
    if metrics is not None:
        metrics["database_insert_ms"] = round((time.monotonic() - persistence_started) * 1000)
    return request


def create_request_from_policies(
    *,
    selections,
    source_kind: str,
    actor,
    assigned_to,
    request_type: str,
    deadline,
    client_label: str,
    internal_notes: str = "",
    is_test: bool = False,
    service: PolicyService | None = None,
) -> SolicitudColectivo:
    """Crea un expediente y un snapshot independiente por póliza seleccionada."""
    if source_kind not in {"company", "person"}:
        raise ColectivosServiceError("invalid_record", "El origen de la solicitud no es válido.")
    selected = tuple(selections)
    if not selected or len(selected) > 10:
        raise ColectivosServiceError("invalid_record", "Seleccione entre una y diez pólizas.")
    service = service or PolicyService()
    prepared = []
    source_id = ""
    seen_hashes: set[str] = set()
    for position, selection in enumerate(selected, 1):
        token = str(selection.get("token") or "")
        context = unsign_record_context(token, "policy")
        if context.get("source_kind") and context.get("source_kind") != source_kind:
            raise ColectivosServiceError("invalid_record", "Una póliza no pertenece al origen seleccionado.")
        current_source = str(context.get("source_id") or "")
        if not current_source or (source_id and current_source != source_id):
            raise ColectivosServiceError("invalid_record", "Las pólizas no pertenecen a la misma entidad.")
        source_id = current_source
        detail, members = service.group(token, source_kind=source_kind)
        if detail.classification != "confirmed" or detail.branch_code not in BRANCH_ADJUSTMENTS:
            raise ColectivosServiceError("invalid_record", "Una póliza no tiene clasificación colectiva segura.")
        try:
            adjustment_codes = validate_adjustment_codes(detail.branch_code, selection.get("adjustments") or ())
        except ValueError as exc:
            raise ColectivosServiceError("invalid_record", str(exc)) from exc
        policy_hash = _hash_reference(context["id"])
        if policy_hash in seen_hashes:
            raise ColectivosServiceError("invalid_record", "La selección contiene una póliza repetida.")
        seen_hashes.add(policy_hash)
        snapshot = _snapshot_payload(detail, members, service.profile, adjustment_codes)
        prepared.append((position, token, policy_hash, detail, members, adjustment_codes, snapshot))

    return _persist_prepared_request(
        prepared=prepared,
        seen_hashes=seen_hashes,
        source_id=source_id,
        source_kind=source_kind,
        actor=actor,
        assigned_to=assigned_to,
        request_type=request_type,
        deadline=deadline,
        client_label=client_label,
        internal_notes=internal_notes,
        is_test=is_test,
        profile=service.profile,
        metrics=getattr(service, "timings", None),
    )


@transaction.atomic
def _persist_prepared_request(
    *, prepared, seen_hashes, source_id, source_kind, actor, assigned_to,
    request_type, deadline, client_label, internal_notes, is_test, profile, metrics=None,
):
    """Persiste en una transacción breve datos ya obtenidos de Zoho."""
    source_hash = _hash_reference(source_id)
    active_states = (
        SolicitudColectivo.Status.DRAFT,
        SolicitudColectivo.Status.READY,
        SolicitudColectivo.Status.SENT,
        SolicitudColectivo.Status.OPENED,
        SolicitudColectivo.Status.CORRECTION,
    )
    candidates = SolicitudColectivo.objects.filter(
        source_reference_hash=source_hash,
        zoho_profile=profile,
        request_type=request_type,
        status__in=active_states,
    ).prefetch_related("policies")
    for candidate in candidates:
        candidate_hashes = {policy.policy_reference_hash for policy in candidate.policies.all()}
        if not candidate_hashes and candidate.policy_reference_hash:
            candidate_hashes = {candidate.policy_reference_hash}
        if candidate_hashes == seen_hashes:
            return candidate

    persistence_started = time.monotonic()
    first = prepared[0]
    policy_snapshots = [item[6] for item in prepared]
    aggregate_warnings = list(dict.fromkeys(warning for item in prepared for warning in item[6]["warnings"]))
    aggregate_payload = {
        **policy_snapshots[0],
        "policies": policy_snapshots,
        "warnings": aggregate_warnings,
    }
    encrypted_aggregate = encrypt(json.dumps(aggregate_payload, ensure_ascii=False, sort_keys=True))
    request = SolicitudColectivo.objects.create(
        source_kind=source_kind,
        source_reference_hash=source_hash,
        policy_reference_hash=first[2],
        encrypted_policy_token=encrypt(first[1]),
        masked_policy_reference=first[3].masked_reference,
        client_label=(client_label.strip() or first[3].holder or "Cliente sin etiqueta")[:180],
        branch_code=first[3].branch_code,
        branch_name=first[3].branch_name,
        request_type=request_type,
        assigned_to=assigned_to,
        deadline=deadline,
        zoho_profile=profile,
        encrypted_snapshot=encrypted_aggregate,
        record_count=sum(len(item[4]) for item in prepared),
        warnings=aggregate_warnings,
        encrypted_internal_notes=encrypt(internal_notes.strip()),
        is_test=is_test,
        created_by=actor,
    )
    next_position = 1
    for position, token, policy_hash, detail, members, adjustment_codes, snapshot in prepared:
        encrypted_snapshot = encrypt(json.dumps(snapshot, ensure_ascii=False, sort_keys=True))
        policy = SolicitudColectivoPoliza.objects.create(
            request=request,
            policy_reference_hash=policy_hash,
            encrypted_policy_token=encrypt(_store_policy_reference(token)),
            masked_policy_reference=detail.masked_reference,
            branch_code=detail.branch_code,
            branch_name=detail.branch_name,
            insurer=detail.insurer,
            policy_status=detail.state,
            start_date=_date(detail.start_date),
            end_date=_date(detail.end_date),
            parameter_version=ADJUSTMENT_CATALOG_VERSION,
            enabled_adjustments=list(adjustment_codes),
            encrypted_snapshot=encrypted_snapshot,
            snapshot_checksum=hashlib.sha256(encrypted_snapshot.encode()).hexdigest(),
            record_count=len(members),
            warnings=list(snapshot["warnings"]),
            position=position,
        )
        next_position = _replace_records(
            request,
            members,
            policy=policy,
            start_position=next_position,
            clear=position == 1,
            metrics=metrics,
        )
    EventoSolicitudColectivo.objects.create(
        request=request,
        actor=actor,
        event_type="CREATED",
        new_status=request.status,
        safe_metadata={"records": request.record_count, "policies": len(prepared)},
    )
    if metrics is not None:
        metrics["database_insert_ms"] = round((time.monotonic() - persistence_started) * 1000)
    return request


def create_or_reuse_request_from_policy(
    *,
    token: str,
    source_kind: str,
    actor,
    assigned_to,
    request_type: str,
    deadline,
    service: PolicyService | None = None,
) -> tuple[SolicitudColectivo, bool]:
    """Return the active request for a policy/type or create its safe snapshot.

    This orchestration is intentionally internal. It never trusts a profile,
    module, branch or assignee supplied by the browser.
    """

    service = service or PolicyService()
    context = unsign_record_context(token, "policy")
    token_source = context.get("source_kind")
    if source_kind not in {"company", "person"} or (token_source and token_source != source_kind):
        raise ColectivosServiceError("invalid_record", "El origen de la solicitud no coincide con la ficha.")
    policy_hash = _hash_reference(context["id"])
    source_id = str(context.get("source_id") or "")
    reusable = {
        SolicitudColectivo.Status.DRAFT,
        SolicitudColectivo.Status.READY,
        SolicitudColectivo.Status.SENT,
        SolicitudColectivo.Status.OPENED,
        SolicitudColectivo.Status.CORRECTION,
    }
    if source_id:
        source_hash = _hash_reference(source_id)
    else:
        detail = service.detail(token)
        _policy_hash, source_hash = request_reference_hashes(
            token=token, source_kind=source_kind, holder=detail.holder,
        )

    def find_existing():
        candidates = SolicitudColectivo.objects.filter(
            Q(policy_reference_hash=policy_hash) | Q(policies__policy_reference_hash=policy_hash),
            source_reference_hash=source_hash,
            zoho_profile=service.profile,
            request_type=request_type,
            status__in=reusable,
            deadline__gt=timezone.localdate(),
            assigned_to__isnull=False,
        ).distinct().prefetch_related("policies").order_by("-updated_at")
        for candidate in candidates:
            if not candidate.encrypted_snapshot:
                continue
            candidate_hashes = {
                policy.policy_reference_hash for policy in candidate.policies.all()
            }
            if not candidate_hashes and candidate.policy_reference_hash:
                candidate_hashes = {candidate.policy_reference_hash}
            if candidate_hashes == {policy_hash}:
                return candidate
        return None

    existing = find_existing()
    if existing:
        service.preparation_status = "active_request_reused"
        return existing, False

    # Prepare remote data before opening the database critical section. The
    # second call made by create_request_from_policy is a local encrypted-cache
    # hit in normal operation.
    service.group(token, source_kind=source_kind)
    with transaction.atomic():
        # The technical actor is a stable, per-environment serialization point
        # for initial request creation. It closes the double-click race without
        # keeping a remote Zoho call inside the lock.
        actor.__class__.objects.select_for_update().get(pk=actor.pk)
        existing = find_existing()
        if existing:
            service.preparation_status = "active_request_reused"
            return existing, False
        created = create_request_from_policy(
            token=token,
            source_kind=source_kind,
            actor=actor,
            assigned_to=assigned_to,
            request_type=request_type,
            deadline=deadline,
            service=service,
        )
        return created, True


@transaction.atomic
def transition_request(*, request: SolicitudColectivo, target: str, actor) -> SolicitudColectivo:
    locked = SolicitudColectivo.objects.select_for_update().get(pk=request.pk)
    previous = locked.status
    locked.transition_to(target)
    locked.save(update_fields=("status", "closed_at", "updated_at"))
    EventoSolicitudColectivo.objects.create(request=locked, actor=actor, event_type="STATUS_CHANGED", previous_status=previous, new_status=target)
    NotificacionColectivos.objects.get_or_create(
        user=locked.assigned_to,
        deduplication_key=f"status:{locked.uuid}:{target}",
        defaults={"request": locked, "notification_type": "STATUS", "title": "Estado actualizado", "message": f"La solicitud {locked.public_id} cambió de estado.", "priority": "NORMAL"},
    )
    return locked


@transaction.atomic
def update_draft_request(*, request: SolicitudColectivo, actor, assigned_to, deadline, internal_notes: str) -> SolicitudColectivo:
    locked = SolicitudColectivo.objects.select_for_update().get(pk=request.pk)
    if locked.status != SolicitudColectivo.Status.DRAFT:
        raise ValidationError("Solo puede editarse un expediente en borrador.")
    previous_owner = locked.assigned_to_id
    locked.assigned_to = assigned_to
    locked.deadline = deadline
    locked.encrypted_internal_notes = encrypt(internal_notes.strip())
    locked.save(update_fields=("assigned_to", "deadline", "encrypted_internal_notes", "updated_at"))
    EventoSolicitudColectivo.objects.create(
        request=locked,
        actor=actor,
        event_type="ASSIGNED" if previous_owner != assigned_to.pk else "DRAFT_UPDATED",
        safe_metadata={"owner_changed": previous_owner != assigned_to.pk},
    )
    if previous_owner != assigned_to.pk:
        NotificacionColectivos.objects.get_or_create(
            user=assigned_to,
            deduplication_key=f"assigned:{locked.uuid}:{assigned_to.pk}",
            defaults={"request": locked, "notification_type": "ASSIGNED", "title": "Solicitud asignada", "message": f"Se le asignó la solicitud {locked.public_id}.", "priority": "NORMAL"},
        )
    return locked


@transaction.atomic
def regenerate_request_snapshot(*, request: SolicitudColectivo, actor, service: PolicyService | None = None) -> SolicitudColectivo:
    locked = SolicitudColectivo.objects.select_for_update().get(pk=request.pk)
    if locked.status != SolicitudColectivo.Status.DRAFT:
        raise ValidationError("El snapshot solo puede regenerarse en borrador.")
    service = service or PolicyService()
    if service.profile != locked.zoho_profile:
        raise ValidationError("El perfil actual no coincide con el expediente.")
    policies = list(SolicitudColectivoPoliza.objects.select_for_update().filter(request=locked, active=True).order_by("position"))
    if not policies:
        try:
            token = decrypt(locked.encrypted_policy_token)
        except ValueError as exc:
            raise ValidationError("La referencia protegida de la póliza no es válida.") from exc
        detail, members = service.group(token, source_kind=locked.source_kind)
        if detail.branch_code != locked.branch_code:
            raise ValidationError("El origen actual no coincide con el expediente.")
        payload = _snapshot_payload(detail, members, service.profile)
        locked.encrypted_snapshot = encrypt(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        locked.record_count = len(members)
        locked.warnings = list(detail.warnings)
        _replace_records(locked, members)
    else:
        prepared = []
        for policy in policies:
            try:
                token = _restore_policy_token(decrypt(policy.encrypted_policy_token))
            except ValueError as exc:
                raise ValidationError("Una referencia protegida de póliza no es válida.") from exc
            detail, members = service.group(token, source_kind=locked.source_kind)
            if detail.branch_code != policy.branch_code:
                raise ValidationError("Una póliza ya no coincide con el expediente.")
            snapshot = _snapshot_payload(detail, members, service.profile, policy.enabled_adjustments)
            prepared.append((policy, detail, members, snapshot))
        aggregate_warnings = list(dict.fromkeys(warning for _, _, _, snapshot in prepared for warning in snapshot["warnings"]))
        aggregate = {**prepared[0][3], "policies": [item[3] for item in prepared], "warnings": aggregate_warnings}
        locked.encrypted_snapshot = encrypt(json.dumps(aggregate, ensure_ascii=False, sort_keys=True))
        locked.record_count = sum(len(item[2]) for item in prepared)
        locked.warnings = aggregate_warnings
        next_position = 1
        for index, (policy, detail, members, snapshot) in enumerate(prepared):
            encrypted_snapshot = encrypt(json.dumps(snapshot, ensure_ascii=False, sort_keys=True))
            policy.insurer = detail.insurer
            policy.policy_status = detail.state
            policy.start_date = _date(detail.start_date)
            policy.end_date = _date(detail.end_date)
            policy.encrypted_snapshot = encrypted_snapshot
            policy.snapshot_checksum = hashlib.sha256(encrypted_snapshot.encode()).hexdigest()
            policy.record_count = len(members)
            policy.warnings = list(snapshot["warnings"])
            policy.save(update_fields=("insurer", "policy_status", "start_date", "end_date", "encrypted_snapshot", "snapshot_checksum", "record_count", "warnings"))
            next_position = _replace_records(locked, members, policy=policy, start_position=next_position, clear=index == 0)
    locked.snapshot_revision += 1
    locked.save(update_fields=("encrypted_snapshot", "snapshot_revision", "record_count", "warnings", "updated_at"))
    EventoSolicitudColectivo.objects.create(request=locked, actor=actor, event_type="SNAPSHOT_REGENERATED", safe_metadata={"revision": locked.snapshot_revision, "records": locked.record_count, "policies": len(policies) or 1})
    return locked


def request_snapshot(request: SolicitudColectivo) -> dict[str, object]:
    try:
        value = json.loads(decrypt(request.encrypted_snapshot))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValidationError("El snapshot no supera la validación de integridad.") from exc
    if not isinstance(value, dict) or value.get("version") != request.snapshot_version:
        raise ValidationError("La versión del snapshot no es válida.")
    return value
