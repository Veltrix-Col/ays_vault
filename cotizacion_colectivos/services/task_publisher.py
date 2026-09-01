"""Outbox local y publicación manual, estrictamente protegida, de Tasks (sandbox o production)."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Mapping, Protocol

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction

from integrations.zoho import get_zoho
from integrations.zoho.exceptions import ZohoAPIError, ZohoError, ZohoTimeoutError
from integrations.zoho.settings import ZohoSettings
from vault.crypto import decrypt, encrypt
from ..zoho import cached_metadata_fields
from .common import colectivos_zoho

from ..models import ColectivosTaskOutbox


logger = logging.getLogger("cotizacion_colectivos")
from .write_guards import configured_confirmation, require_write_guard


TASK_KIND = {
    "INCLUSION": "Ingresos",
    "RETIRO": "Retiros",
    "COTIZACION": "Cotización",
}
NOVELTIES_TASK_AREA = "Negocios Bienestar y Beneficios"
NOVELTIES_ANALYST_REQUEST = "Si"
BASE_TASK_FIELDS = frozenset({"Subject", "tipo_de_solicitud"})
CONFIRMED_TASK_FIELDS = frozenset({
    "Subject", "tipo_de_solicitud", "rea", "Observaciones", "Responsable",
    "Correo_responsable", "Fecha_de_solicitud_del_cliente", "Solicitud_a_analista", "Vendedor",
})
ALLOWED_TASK_FIELDS = CONFIRMED_TASK_FIELDS
TEST_TASK_ALLOWED_FIELDS = CONFIRMED_TASK_FIELDS
SANDBOX_WRITE_CONFIRMATION = "SANDBOX_TASK_WRITE"
PRODUCTION_WRITE_CONFIRMATION = "PRODUCTION_TASK_WRITE"
# Tasks admite sandbox y production; cada perfil exige su propia confirmacion
# explicita (ver _expected_confirmation) para que habilitar uno no habilite el otro.
_WRITABLE_PROFILES = ("sandbox", "production")


def _expected_confirmation(profile: str) -> str:
    return f"{profile.upper()}_TASK_WRITE"
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


def read_published_task(task_id: str, *, zoho=None) -> dict[str, object] | None:
    """Read current Task fields; callers retain local state if Zoho is unavailable."""
    value = str(task_id or "").strip()
    if not value.isdigit() or not 10 <= len(value) <= 30:
        return None
    try:
        record = (zoho or colectivos_zoho()).records.get_by_id(
            module="Tasks", record_id=value,
            fields=("id", "Subject", "Responsable", "Estado"),
        )
    except Exception:
        return None
    return record if isinstance(record, dict) else None


@dataclass(frozen=True)
class ColectivosTaskPayload:
    request_kind: str
    source_kind: str
    policy_context: str
    branch_code: str
    local_reference: str
    has_attachments: bool = False
    subject: str = ""
    area: str = ""
    observations: str = ""
    responsible: str = ""
    responsible_email: str = ""
    requested_date: str = ""
    seller: str = ""
    analyst_request: str = ""


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
    record = {
        "Subject": (str(payload.subject or "").strip() or f"Colectivos · {task_kind} · {reference}")[:255],
        "tipo_de_solicitud": task_kind,
    }
    optional = {
        "rea": payload.area,
        "Observaciones": payload.observations,
        "Responsable": payload.responsible,
        "Correo_responsable": payload.responsible_email,
        "Fecha_de_solicitud_del_cliente": payload.requested_date,
        "Solicitud_a_analista": payload.analyst_request,
    }
    record.update({key: str(value).strip() for key, value in optional.items() if str(value or "").strip()})
    seller = str(payload.seller or "").strip()
    if seller:
        try:
            fields = cached_metadata_fields(colectivos_zoho(), "Tasks")
            task_field = next((
                field for field in fields
                if (field.get("api_name") if isinstance(field, dict) else getattr(field, "api_name", "")) == "Vendedor"
            ), None)
            values = set()
            pick_values = (
                task_field.get("pick_list_values") if isinstance(task_field, dict)
                else getattr(task_field, "pick_list_values", ())
            ) if task_field is not None else ()
            for item in pick_values or ():
                actual = item.get("actual_value") if isinstance(item, dict) else getattr(item, "actual_value", "")
                actual = str(actual or "").strip()
                if actual and actual not in {"None", "-None-"}:
                    values.add(actual)
            if seller in values:
                record["Vendedor"] = seller
        except Exception:
            pass
    return record


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


class GuardedTaskPublisher:
    """Único punto de escritura Tasks, cerrado por barreras independientes.

    Admite sandbox y production. Cada perfil exige coincidencia exacta con
    ZOHO_ACTIVE_PROFILE, su propio write_enabled (ZOHO_{PERFIL}_WRITE_ENABLED)
    y su propia confirmacion explicita (ver _expected_confirmation) -- ninguna
    de las barreras se comparte entre perfiles.
    """

    enabled = True

    def __init__(self, *, profile: str, confirmation: str):
        if profile not in _WRITABLE_PROFILES:
            raise TaskPublishingDisabled("Tasks sólo admite los perfiles habilitados (sandbox, production).")
        require_write_guard(
            entity="task", profile=profile, confirmation=confirmation,
            feature_flag="COLECTIVOS_TASK_PUBLISH_ENABLED",
            legacy_setting="COLECTIVOS_TASK_WRITE_CONFIRMATION",
            disabled_error=TaskPublishingDisabled,
        )
        self.profile = profile

    def publish(self, payload: ColectivosTaskPayload) -> Mapping[str, object]:
        return self._create_one(build_task_record(payload))

    def publish_test_task(self) -> Mapping[str, object]:
        return self._create_one(SYNTHETIC_TEST_TASK, allowed_fields=TEST_TASK_ALLOWED_FIELDS)

    def _create_one(
        self, record: Mapping[str, object], *, allowed_fields: frozenset[str] = ALLOWED_TASK_FIELDS,
    ) -> Mapping[str, object]:
        normalized = dict(record)
        if not BASE_TASK_FIELDS.issubset(normalized) or set(normalized) - allowed_fields:
            raise ValidationError("El payload Tasks no coincide con el contrato autorizado.")
        try:
            # La fachada vuelve a comprobar write_enabled antes de construir el POST.
            result = get_zoho(profile=self.profile).records.create(
                module="Tasks",
                records=(normalized,),
            )
        except ZohoTimeoutError as exc:
            raise TaskPublicationUncertain(
                f"Resultado incierto: no reintente; requiere conciliación manual en Zoho ({self.profile})."
            ) from exc
        except ZohoAPIError as exc:
            if getattr(exc, "request_sent", None) is True and (
                getattr(exc, "status_code", None) or 0
            ) >= 500:
                raise TaskPublicationUncertain(
                    f"Resultado incierto: no reintente; requiere conciliación manual en Zoho ({self.profile})."
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
    if profile not in _WRITABLE_PROFILES:
        raise TaskPublishingDisabled("Tasks sólo admite los perfiles habilitados (sandbox, production).")
    if getattr(settings, "COLECTIVOS_TASK_PUBLISH_ENABLED", False):
        return GuardedTaskPublisher(profile=profile, confirmation=confirmation)
    return DisabledColectivosTaskPublisher()


def publish_task_outbox(outbox_id: int) -> None:
    """Publish one already-persisted intent, without retries or cross-profile writes."""
    if not getattr(settings, "COLECTIVOS_TASK_PUBLISH_ENABLED", False):
        logger.warning("task_publish_skipped outbox_id=%s skip_reason=feature_disabled", outbox_id)
        return
    with transaction.atomic():
        item = ColectivosTaskOutbox.objects.select_for_update().get(pk=outbox_id)
        if item.status != item.Status.PENDING:
            logger.info("task_publish_skipped outbox_id=%s skip_reason=status_%s", outbox_id, item.status)
            return
        item.attempts += 1
        item.save(update_fields=("attempts", "updated_at"))
        try:
            record = json.loads(decrypt(item.encrypted_payload))
            analyst_request = str(record.get("Solicitud_a_analista") or "").strip()
            if not analyst_request and item.request_id and item.request.request_type != "COTIZACION":
                analyst_request = NOVELTIES_ANALYST_REQUEST
            payload = ColectivosTaskPayload(
                request_kind=item.event_kind,
                source_kind="quotation" if item.quotation_id else "request",
                policy_context="",
                branch_code="",
                local_reference=str(getattr(item.quotation, "public_id", "") or getattr(item.request, "public_id", "")),
                subject=str(record.get("Subject") or ""),
                area=str(record.get("rea") or ""),
                observations=str(record.get("Observaciones") or ""),
                responsible=str(record.get("Responsable") or ""),
                responsible_email=str(record.get("Correo_responsable") or ""),
                requested_date=str(record.get("Fecha_de_solicitud_del_cliente") or ""),
                seller=str(record.get("Vendedor") or ""),
                analyst_request=analyst_request,
            )
            logger.info(
                "task_publish_payload outbox_id=%s source=%s has_analyst_request=%s has_area=%s",
                outbox_id, "quotation" if item.quotation_id else "novelties",
                bool(analyst_request), bool(str(record.get("rea") or "").strip()),
            )
            result = get_task_publisher(
                profile=str(getattr(settings, "ZOHO_ACTIVE_PROFILE", "sandbox")),
                confirmation=configured_confirmation(
                    "task",
                    str(getattr(settings, "ZOHO_ACTIVE_PROFILE", "sandbox")),
                    legacy_setting="COLECTIVOS_TASK_WRITE_CONFIRMATION",
                ),
            ).publish(payload)
        except TaskPublicationUncertain:
            item.status = item.Status.RECONCILE
            item.safe_error_code = "UNCERTAIN"
            item.save(update_fields=("status", "safe_error_code", "updated_at"))
            return
        except (TaskPublishingDisabled, TaskPublicationRejected, ZohoError, ValidationError) as exc:
            item.status = item.Status.BLOCKED
            item.safe_error_code = str(
                getattr(exc, "category", None) or getattr(exc, "code", None) or "PUBLISH_BLOCKED"
            )[:40]
            item.save(update_fields=("status", "safe_error_code", "updated_at"))
            return
        remote_id = str(result.get("record_id") or "").strip()
        if not remote_id:
            item.status = item.Status.BLOCKED
            item.safe_error_code = "MISSING_REMOTE_ID"
            item.save(update_fields=("status", "safe_error_code", "updated_at"))
            return
        item.status = item.Status.PUBLISHED
        item.encrypted_remote_id = encrypt(remote_id)
        item.safe_error_code = ""
        item.save(update_fields=("status", "encrypted_remote_id", "safe_error_code", "updated_at"))
