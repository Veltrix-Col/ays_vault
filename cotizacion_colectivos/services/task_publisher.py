"""Outbox local y publicación manual, estrictamente protegida, de Tasks Sandbox."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping, Protocol

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction

from integrations.zoho import get_zoho
from integrations.zoho.exceptions import ZohoAPIError, ZohoTimeoutError
from integrations.zoho.settings import ZohoSettings
from vault.crypto import decrypt, encrypt

from ..models import ColectivosTaskOutbox


TASK_KIND = {
    "INCLUSION": "Ingresos",
    "RETIRO": "Retiros",
    "COTIZACION": "Cotización",
}
ALLOWED_TASK_FIELDS = frozenset({"Subject", "tipo_de_solicitud"})
TEST_TASK_ALLOWED_FIELDS = frozenset({
    "Subject", "tipo_de_solicitud", "rea", "Observaciones", "Responsable",
    "Correo_responsable", "Fecha_de_solicitud_del_cliente",
})
SANDBOX_WRITE_CONFIRMATION = "SANDBOX_TASK_WRITE"
SYNTHETIC_TEST_TASK = {
    "Subject": "PRUEBA VELTRIX-CV-003 - COTIZACION - NO GESTIONAR",
    "tipo_de_solicitud": "Cotización",
    "rea": "Negocios Bienestar y Beneficios",
    "Observaciones": "Prueba controlada de creación de Task desde A&S Vault. Validación de campos funcionales de Cotización. NO GESTIONAR.",
    "Responsable": "Sara Rua Vargas",
    "Correo_responsable": "sara.rua@segurosays.com",
    "Fecha_de_solicitud_del_cliente": "2026-08-17",
}


class TaskPublishingDisabled(RuntimeError):
    pass


class TaskPublicationRejected(RuntimeError):
    pass


class TaskPublicationUncertain(RuntimeError):
    """El request pudo llegar a Zoho y exige conciliación antes de otro intento."""

    reconciliation_required = True


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

    def publish_test_task(self) -> Mapping[str, object]: ...


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

    def publish_test_task(self) -> Mapping[str, object]:
        raise TaskPublishingDisabled("La publicación de tareas Zoho está deshabilitada.")


class GuardedSandboxTaskPublisher:
    """Único punto de escritura Tasks, cerrado por barreras independientes."""

    enabled = True

    def __init__(self, *, profile: str, confirmation: str):
        if profile != "sandbox":
            raise TaskPublishingDisabled("Tasks sólo admite Sandbox en esta fase.")
        if str(getattr(settings, "ZOHO_ACTIVE_PROFILE", "")).strip().lower() != "sandbox":
            raise TaskPublishingDisabled("El perfil Zoho activo no es Sandbox.")
        zoho_config = ZohoSettings.from_django(profile)
        if not zoho_config.write_enabled:
            raise TaskPublishingDisabled("La escritura del perfil Sandbox está deshabilitada.")
        if not getattr(settings, "COLECTIVOS_TASK_PUBLISH_ENABLED", False):
            raise TaskPublishingDisabled("La publicación Tasks no está habilitada.")
        if getattr(settings, "COLECTIVOS_TASK_WRITE_CONFIRMATION", "") != SANDBOX_WRITE_CONFIRMATION:
            raise TaskPublishingDisabled("La configuración no confirma la escritura Sandbox.")
        if str(confirmation or "").strip() != SANDBOX_WRITE_CONFIRMATION:
            raise TaskPublishingDisabled("Falta la confirmación explícita de escritura Sandbox.")
        self.profile = profile

    def publish(self, payload: ColectivosTaskPayload) -> Mapping[str, object]:
        return self._create_one(build_task_record(payload))

    def publish_test_task(self) -> Mapping[str, object]:
        return self._create_one(SYNTHETIC_TEST_TASK, allowed_fields=TEST_TASK_ALLOWED_FIELDS)

    def _create_one(
        self, record: Mapping[str, object], *, allowed_fields: frozenset[str] = ALLOWED_TASK_FIELDS,
    ) -> Mapping[str, object]:
        normalized = dict(record)
        if set(normalized) != allowed_fields:
            raise ValidationError("El payload Tasks no coincide con el contrato autorizado.")
        try:
            # La fachada vuelve a comprobar write_enabled antes de construir el POST.
            result = get_zoho(profile=self.profile).records.create(
                module="Tasks",
                records=(normalized,),
            )
        except ZohoTimeoutError as exc:
            raise TaskPublicationUncertain(
                "Resultado incierto: no reintente; requiere conciliación manual en Zoho Sandbox."
            ) from exc
        except ZohoAPIError as exc:
            if getattr(exc, "request_sent", None) is True and (
                getattr(exc, "status_code", None) or 0
            ) >= 500:
                raise TaskPublicationUncertain(
                    "Resultado incierto: no reintente; requiere conciliación manual en Zoho Sandbox."
                ) from exc
            raise

        records = tuple(getattr(result, "records", ()))
        if len(records) != 1:
            raise TaskPublicationRejected("Zoho no devolvió un resultado individual válido.")
        item = records[0]
        if not getattr(item, "succeeded", False) or not str(
            getattr(item, "record_id", "") or ""
        ).strip():
            code = str(getattr(item, "code", "") or "WRITE_REJECTED")[:40]
            raise TaskPublicationRejected(f"Zoho rechazó la Task ({code}).")
        return {
            "profile": self.profile,
            "module": "Tasks",
            "record_id": str(item.record_id),
            "succeeded": bool(getattr(item, "succeeded", False)),
            "code": str(getattr(item, "code", "") or ""),
        }


def get_task_publisher(
    *, profile: str = "sandbox", confirmation: str = "",
) -> ColectivosTaskPublisher:
    if profile != "sandbox":
        raise TaskPublishingDisabled("Tasks sólo admite Sandbox en esta fase.")
    if getattr(settings, "COLECTIVOS_TASK_PUBLISH_ENABLED", False):
        return GuardedSandboxTaskPublisher(profile=profile, confirmation=confirmation)
    return DisabledColectivosTaskPublisher()
