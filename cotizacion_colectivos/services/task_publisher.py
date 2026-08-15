"""Outbox y dry-run seguro para Tasks; la publicación real sigue bloqueada por contrato."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping, Protocol

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction

from vault.crypto import decrypt, encrypt

from ..models import ColectivosTaskOutbox


TASK_KIND = {
    "INCLUSION": "Ingresos",
    "RETIRO": "Retiros",
    "COTIZACION": "Cotización",
}
ALLOWED_TASK_FIELDS = frozenset({"Subject", "tipo_de_solicitud"})


class TaskPublishingDisabled(RuntimeError):
    pass


class TaskContractIncomplete(TaskPublishingDisabled):
    pass


@dataclass(frozen=True)
class ColectivosTaskPayload:
    request_kind: str
    source_kind: str
    policy_context: str
    branch_code: str
    local_reference: str
    has_attachments: bool = False


class ColectivosTaskPublisher(Protocol):
    def publish(self, payload: ColectivosTaskPayload) -> Mapping[str, object]: ...


def build_task_record(payload: ColectivosTaskPayload) -> dict[str, str]:
    kind = str(payload.request_kind or "").strip().upper()
    try:
        task_kind = TASK_KIND[kind]
    except KeyError as exc:
        raise ValidationError("El tipo de evento no tiene mapping Tasks confirmado.") from exc
    reference = str(payload.local_reference or "").strip()
    if not reference or len(reference) > 80 or any(ord(char) < 32 for char in reference):
        raise ValidationError("La referencia local no es válida.")
    return {
        "Subject": f"Colectivos · {task_kind} · {reference}"[:255],
        "tipo_de_solicitud": task_kind,
    }


def sanitized_dry_run(payload: ColectivosTaskPayload, *, profile: str) -> dict[str, object]:
    record = build_task_record(payload)
    return {
        "mode": "dry-run",
        "profile": str(profile),
        "module": "Tasks",
        "fields": tuple(record),
        "type": record["tipo_de_solicitud"],
        "subject_length": len(record["Subject"]),
        "has_attachments": bool(payload.has_attachments),
        "writes": 0,
    }


@transaction.atomic
def enqueue_task(*, source, payload: ColectivosTaskPayload, event_version: int = 1):
    if (hasattr(source, "request_type") and hasattr(source, "branch_code")):
        source_kind, source_id = "request", source.pk
        relations = {"request": source, "quotation": None}
    elif hasattr(source, "schema_version") and hasattr(source, "branch_slug"):
        source_kind, source_id = "quotation", source.pk
        relations = {"request": None, "quotation": source}
    else:
        raise ValidationError("El origen de la tarea no es válido.")
    record = build_task_record(payload)
    serialized = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    checksum = hashlib.sha256(serialized.encode()).hexdigest()
    key = hashlib.sha256(
        f"{source_kind}:{source_id}:{payload.request_kind}:{event_version}".encode()
    ).hexdigest()
    item, _created = ColectivosTaskOutbox.objects.get_or_create(
        idempotency_key=key,
        defaults={
            **relations,
            "event_kind": str(payload.request_kind).upper(),
            "event_version": event_version,
            "encrypted_payload": encrypt(serialized),
            "payload_checksum": checksum,
        },
    )
    if item.payload_checksum != checksum:
        raise ValidationError("La clave idempotente ya existe con otro payload.")
    return item


def dry_run_outbox(item: ColectivosTaskOutbox, *, profile: str = "sandbox") -> dict[str, object]:
    try:
        record = json.loads(decrypt(item.encrypted_payload))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValidationError("El payload local no está disponible.") from exc
    if set(record) - ALLOWED_TASK_FIELDS:
        raise ValidationError("El payload contiene campos no autorizados.")
    return {
        "mode": "dry-run", "profile": profile, "module": "Tasks",
        "fields": tuple(record), "type": record.get("tipo_de_solicitud", ""),
        "subject_length": len(str(record.get("Subject") or "")), "writes": 0,
        "outbox": str(item.idempotency_key[:12]),
    }


class DisabledColectivosTaskPublisher:
    enabled = False

    def publish(self, payload: ColectivosTaskPayload) -> Mapping[str, object]:
        del payload
        raise TaskPublishingDisabled("La publicación de tareas Zoho está deshabilitada.")


class GuardedSandboxTaskPublisher:
    """Guardas en capas; no escribe mientras el layout real siga sin confirmar."""

    enabled = False

    def __init__(self, *, profile: str):
        if profile != "sandbox":
            raise TaskPublishingDisabled("Tasks sólo admite Sandbox en esta fase.")
        if not getattr(settings, "COLECTIVOS_TASK_PUBLISH_ENABLED", False):
            raise TaskPublishingDisabled("La publicación Tasks no está habilitada.")
        if getattr(settings, "COLECTIVOS_TASK_WRITE_CONFIRMATION", "") != "SANDBOX_TASK_WRITE":
            raise TaskPublishingDisabled("Falta la confirmación explícita de escritura Sandbox.")
        raise TaskContractIncomplete(
            "El layout y sus reglas obligatorias no están demostrados; no se puede publicar responsablemente."
        )


def get_task_publisher(*, profile: str = "sandbox") -> ColectivosTaskPublisher:
    if getattr(settings, "COLECTIVOS_TASK_PUBLISH_ENABLED", False):
        return GuardedSandboxTaskPublisher(profile=profile)
    return DisabledColectivosTaskPublisher()
