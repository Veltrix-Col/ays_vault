from __future__ import annotations

import hashlib
import json
from datetime import date

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.crypto import salted_hmac

from cotizacion_colectivos.models import (
    EventoSolicitudColectivo,
    NotificacionColectivos,
    SolicitudColectivo,
    SolicitudColectivoRegistro,
)
from vault.crypto import decrypt, encrypt

from .common import ColectivosServiceError, unsign_record_context
from .policies import PolicyService


def _hash_reference(value: object) -> str:
    return salted_hmac("cotizacion_colectivos.reference.v1", str(value or ""), secret=settings.SECRET_KEY).hexdigest()


def _date(value: str):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _snapshot_payload(detail, members, profile: str) -> dict[str, object]:
    return {
        "version": 1,
        "profile": profile,
        "policy": {
            "reference": detail.masked_reference,
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
                "state": item.state,
                "entry_date": item.entry_date,
                "exit_date": item.exit_date,
                "plan": item.plan,
                "relationship": item.relationship,
                "risk_summary": item.risk_summary,
                "economic_values": dict(item.economic_values),
            }
            for item in members
        ],
        "warnings": list(detail.warnings),
    }


def _replace_records(request: SolicitudColectivo, members) -> None:
    request.records.all().delete()
    for position, member in enumerate(members, 1):
        safe_payload = {
            "display_name": member.display_name,
            "id_type": member.id_type,
            "masked_document": member.masked_document,
            "relationship": member.relationship,
            "risk_summary": member.risk_summary,
        }
        checksum = hashlib.sha256(json.dumps(safe_payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        SolicitudColectivoRegistro.objects.create(
            request=request,
            element_type=SolicitudColectivoRegistro.ElementType.BENEFICIARY if "Beneficiario" in member.role else SolicitudColectivoRegistro.ElementType.PERSON,
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
        )


@transaction.atomic
def create_request_from_policy(*, token: str, source_kind: str, actor, assigned_to, request_type: str, deadline, internal_notes: str = "", is_test: bool = False, service: PolicyService | None = None) -> SolicitudColectivo:
    if source_kind not in {"company", "person"}:
        raise ColectivosServiceError("invalid_record", "El origen de la solicitud no es válido.")
    service = service or PolicyService()
    detail, members = service.group(token)
    if detail.classification != "confirmed" or not detail.branch_code:
        raise ColectivosServiceError("invalid_record", "La póliza no tiene una clasificación segura.")
    token_context = unsign_record_context(token, "policy")
    policy_id = token_context["id"]
    token_source_kind = token_context.get("source_kind")
    if token_source_kind and token_source_kind != source_kind:
        raise ColectivosServiceError("invalid_record", "El origen de la solicitud no coincide con la ficha.")
    source_reference = token_context.get("source_id") or f"{source_kind}:{detail.holder}"
    policy_hash = _hash_reference(policy_id)
    active_states = tuple(state for state in SolicitudColectivo.Status.values if state not in {SolicitudColectivo.Status.CLOSED, SolicitudColectivo.Status.CANCELLED})
    if SolicitudColectivo.objects.filter(policy_reference_hash=policy_hash, request_type=request_type, status__in=active_states).exists():
        raise ColectivosServiceError("duplicate", "Ya existe una solicitud activa del mismo tipo para esta póliza.")
    payload = _snapshot_payload(detail, members, service.profile)
    request = SolicitudColectivo.objects.create(
        source_kind=source_kind,
        source_reference_hash=_hash_reference(source_reference),
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
        encrypted_snapshot=encrypt(json.dumps(payload, ensure_ascii=False, sort_keys=True)),
        record_count=len(members),
        warnings=list(detail.warnings),
        encrypted_internal_notes=encrypt(internal_notes.strip()),
        is_test=is_test,
        created_by=actor,
    )
    _replace_records(request, members)
    EventoSolicitudColectivo.objects.create(request=request, actor=actor, event_type="CREATED", new_status=request.status, safe_metadata={"records": request.record_count, "branch": request.branch_code})
    NotificacionColectivos.objects.get_or_create(
        user=assigned_to,
        deduplication_key=f"assigned:{request.uuid}",
        defaults={"request": request, "notification_type": "ASSIGNED", "title": "Solicitud asignada", "message": f"Se le asignó la solicitud {request.public_id}.", "priority": "NORMAL"},
    )
    return request


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
    try:
        token = decrypt(locked.encrypted_policy_token)
    except ValueError as exc:
        raise ValidationError("La referencia protegida de la póliza no es válida.") from exc
    service = service or PolicyService()
    detail, members = service.group(token)
    if service.profile != locked.zoho_profile or detail.branch_code != locked.branch_code:
        raise ValidationError("El origen actual no coincide con el expediente.")
    locked.encrypted_snapshot = encrypt(json.dumps(_snapshot_payload(detail, members, service.profile), ensure_ascii=False, sort_keys=True))
    locked.snapshot_revision += 1
    locked.record_count = len(members)
    locked.warnings = list(detail.warnings)
    locked.save(update_fields=("encrypted_snapshot", "snapshot_revision", "record_count", "warnings", "updated_at"))
    _replace_records(locked, members)
    EventoSolicitudColectivo.objects.create(request=locked, actor=actor, event_type="SNAPSHOT_REGENERATED", safe_metadata={"revision": locked.snapshot_revision, "records": locked.record_count})
    return locked


def request_snapshot(request: SolicitudColectivo) -> dict[str, object]:
    try:
        value = json.loads(decrypt(request.encrypted_snapshot))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValidationError("El snapshot no supera la validación de integridad.") from exc
    if not isinstance(value, dict) or value.get("version") != request.snapshot_version:
        raise ValidationError("La versión del snapshot no es válida.")
    return value
