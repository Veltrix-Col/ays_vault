from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from cotizacion_colectivos.services.task_publisher import (
    TaskPublicationUncertain,
    get_task_publisher,
)
from cotizacion_colectivos.services.write_guards import configured_confirmation
from cotizacion_colectivos.services.write_guards import expected_confirmation
from integrations.zoho.settings import ZohoSettings
from cotizacion_colectivos.services.task_responsibles import task_responsible_options

from .models import EmailException, ZohoTaskCreation
from .services import record_audit


def responsible_options():
    return task_responsible_options(collective_only=False)


def task_creation_available() -> bool:
    """Fail-closed local guard check; does not instantiate a Zoho facade."""
    if not getattr(settings, "EMAIL_EXCEPTIONS_ZOHO_TASK_WRITE_ENABLED", False):
        return False
    profile = str(getattr(settings, "ZOHO_ACTIVE_PROFILE", "sandbox")).strip().lower()
    if profile not in {"sandbox", "production"}:
        return False
    try:
        if not ZohoSettings.from_django(profile).write_enabled:
            return False
        confirmation = configured_confirmation(
            "task", profile, legacy_setting="COLECTIVOS_TASK_WRITE_CONFIRMATION",
        )
        expected = expected_confirmation(
            "task", profile, legacy_setting="COLECTIVOS_TASK_WRITE_CONFIRMATION",
        )
        return bool(expected and confirmation == expected)
    except Exception:
        return False


def create_exception_task(*, exception: EmailException, responsible: str,
                          subject: str, due_date, description: str, actor):
    """Create exactly one Task through the existing guarded publisher.

    The active Zoho profile is selected by deployment configuration. The
    existing profile write guard and confirmation remain authoritative; the
    module flag is an additional independent gate.
    """
    if not getattr(settings, "EMAIL_EXCEPTIONS_ZOHO_TASK_WRITE_ENABLED", False):
        raise ValidationError("La escritura de Tasks de Excepciones está deshabilitada.")
    profile = str(getattr(settings, "ZOHO_ACTIVE_PROFILE", "sandbox")).strip().lower()
    if profile not in {"sandbox", "production"}:
        raise ValidationError("El perfil Zoho no está habilitado para esta escritura.")
    confirmation = configured_confirmation(
        "task", profile, legacy_setting="COLECTIVOS_TASK_WRITE_CONFIRMATION",
    )
    with transaction.atomic():
        locked = EmailException.objects.select_for_update().get(pk=exception.pk)
        existing = ZohoTaskCreation.objects.filter(exception=locked).first()
        if existing and existing.task_id:
            return existing
        task = ZohoTaskCreation.objects.create(
            exception=locked, subject=subject, due_date=due_date,
            description=description, zoho_owner_name=responsible,
            created_by=actor, technical_status="REQUESTED",
        )
        record = {
            "Subject": subject[:255], "tipo_de_solicitud": "Facturación",
            "Caso_de_excepci_n": True,
            "Motivo_de_excepci_n": locked.exception_reason[:2000],
            "rea": "Operaciones", "Observaciones": description[:2000],
            "Responsable": responsible,
        }
        if due_date:
            record["Fecha_de_vencimiento"] = due_date.isoformat()
        record_audit(locked, "TASK_CREATE_REQUESTED", actor, {"subject": subject, "profile": profile})
        try:
            publisher = get_task_publisher(
                profile=profile,
                confirmation=confirmation,
                feature_flag="EMAIL_EXCEPTIONS_ZOHO_TASK_WRITE_ENABLED",
            )
            result = publisher.publish_email_exception(record)
        except Exception as exc:
            task.technical_status = "FAILED"
            task.save(update_fields=("technical_status",))
            record_audit(locked, "TASK_CREATE_FAILED", actor, {"error": type(exc).__name__})
            raise
        task.task_id = str(result.get("record_id") or "")
        task.task_url = f"https://crm.zoho.com/crm/tab/Tasks/{task.task_id}" if task.task_id else ""
        task.technical_status = "CREATED"
        task.save(update_fields=("task_id", "task_url", "technical_status"))
        locked.status = EmailException.MANAGED
        locked.handled_by, locked.handled_at = actor, timezone.now()
        locked.save(update_fields=("status", "handled_by", "handled_at"))
        record_audit(locked, "TASK_CREATED", actor, {"task_id": task.task_id, "task_url": task.task_url})
        return task
