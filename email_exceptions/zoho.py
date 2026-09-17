from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Mapping
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
from cotizacion_colectivos.services.common import colectivos_zoho
from cotizacion_colectivos.zoho import cached_metadata_fields

from .models import CaseActivity, ExceptionCase, EmailException, ZohoTaskCreation
from .services import record_audit


def responsible_options():
    return task_responsible_options(collective_only=False)


def ramo_options():
    try:
        facade = colectivos_zoho()
        field = next((item for item in cached_metadata_fields(facade, "Tasks")
                      if str(getattr(item, "api_name", "")) == "Ramo"), None)
    except Exception as exc:
        raise ValidationError("No fue posible cargar los ramos confirmados de Zoho.") from exc
    if field is None:
        raise ValidationError("No fue posible cargar los ramos confirmados de Zoho.")
    values, seen = [], set()
    for item in getattr(field, "pick_list_values", ()) or ():
        if isinstance(item, Mapping):
            actual = item.get("actual_value") or item.get("display_value")
            display = item.get("display_value") or actual
        else:
            actual = getattr(item, "actual_value", None) or getattr(item, "display_value", None)
            display = getattr(item, "display_value", None) or actual
        actual = str(actual or "").strip()
        display = str(display or "").strip()
        if actual and display and actual not in {"None", "-None-"} and actual not in seen:
            seen.add(actual)
            values.append((actual, display))
    if not values:
        raise ValidationError("No hay ramos disponibles para seleccionar.")
    return tuple(values)


def _plain(value, limit=None):
    text = unicodedata.normalize("NFKC", str(value or "")).replace("\r", " ").replace("\n", " ")
    text = re.sub(r"<[^>]*>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] if limit else text


def build_case_task_subject(case: ExceptionCase) -> str:
    message = case.messages.select_related("email").order_by("email__received_at", "pk").first()
    reference = _plain(case.organization or case.case_key, 70)
    subject = _plain(getattr(getattr(message, "email", None), "subject", ""), 150)
    value = f"Excepción de correo - {reference}"
    if subject:
        value += f" - {subject}"
    return value[:255]


def build_case_task_observations(case: ExceptionCase) -> str:
    message = case.messages.select_related("email").order_by("email__received_at", "pk").first()
    exception = case.exceptions.order_by("pk").first()
    sections = [f"Caso de excepción: {_plain(case.organization or case.case_key, 180)}"]
    if case.organization:
        sections.append(f"Organización: {_plain(case.organization, 120)}")
    subject = _plain(getattr(getattr(message, "email", None), "subject", ""), 300)
    if subject:
        sections.append(f"Asunto: {subject}")
    reason = _plain(getattr(exception, "exception_reason", ""), 700)
    if reason:
        sections.append(f"Motivo de excepción: {reason}")
    if case.next_action:
        sections.append(f"Próxima acción: {_plain(case.next_action, 500)}")
    sections.append(f"Referencia interna: {_plain(case.case_key, 255)}")
    return "\n".join(sections)[:2000]


def _case_task_fingerprint(case, responsible, ramo, subject, observations):
    payload = "|".join((case.case_key, subject, responsible, ramo, "true", observations))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _case_task_existing(case, *, using="default"):
    task = ZohoTaskCreation.objects.using(using).filter(case=case).first()
    return task or ZohoTaskCreation.objects.using(using).filter(exception__case=case).first()


def create_case_task(*, case: ExceptionCase, responsible: str, ramo: str, actor, using="default"):
    """Persist a case-level intent, then publish once through the shared guard."""
    if not task_creation_available():
        raise ValidationError("La escritura de Tasks de Excepciones está deshabilitada.")
    profile = str(getattr(settings, "ZOHO_ACTIVE_PROFILE", "sandbox")).strip().lower()
    confirmation = configured_confirmation("task", profile, legacy_setting="COLECTIVOS_TASK_WRITE_CONFIRMATION")
    subject = build_case_task_subject(case)
    observations = build_case_task_observations(case)
    fingerprint = _case_task_fingerprint(case, responsible, ramo, subject, observations)
    with transaction.atomic(using=using):
        locked_case = ExceptionCase.objects.using(using).select_for_update().get(pk=case.pk)
        if locked_case.scope != ExceptionCase.Scope.CASE:
            raise ValidationError("Solo los casos operativos pueden tener una Task Zoho.")
        task = _case_task_existing(locked_case, using=using)
        if task and task.technical_status in {"CREATED", "REQUESTED", "RECONCILE"}:
            return task
        if task is None:
            task = ZohoTaskCreation.objects.using(using).create(
                case=locked_case, subject=subject, description=observations,
                responsible=responsible, ramo=ramo, fingerprint=fingerprint,
                requested_by=actor, created_by=actor, technical_status="REQUESTED",
            )
        else:
            if task.case_id is None:
                task.case = locked_case
            task.subject, task.description = subject, observations
            task.responsible, task.ramo, task.fingerprint = responsible, ramo, fingerprint
            task.requested_by, task.technical_status, task.error_category = actor, "REQUESTED", ""
            task.save(using=using, update_fields=("case", "subject", "description", "responsible", "ramo", "fingerprint", "requested_by", "technical_status", "error_category", "updated_at"))
        CaseActivity.objects.using(using).create(case=locked_case, event_type=CaseActivity.EventType.TASK_CREATE_REQUESTED, actor=actor, metadata={"responsable": responsible, "ramo": ramo, "fingerprint": fingerprint[:12]})
    record = {"Subject": subject, "Responsable": responsible, "Ramo": ramo, "Caso_de_excepci_n": True, "Observaciones": observations}
    remote_id = ""
    try:
        result = get_task_publisher(profile=profile, confirmation=confirmation, feature_flag="EMAIL_EXCEPTIONS_ZOHO_TASK_WRITE_ENABLED").publish_email_exception(record)
    except TaskPublicationUncertain:
        status, category, event = "RECONCILE", "UNCERTAIN", CaseActivity.EventType.TASK_RECONCILE_REQUIRED
    except Exception as exc:
        status, category, event = "FAILED", type(exc).__name__[:80], CaseActivity.EventType.TASK_CREATE_FAILED
    else:
        remote_id = str(result.get("record_id") or "").strip()
        if not remote_id:
            status, category, event = "FAILED", "MISSING_REMOTE_ID", CaseActivity.EventType.TASK_CREATE_FAILED
        else:
            status, category, event = "CREATED", "", CaseActivity.EventType.TASK_CREATED
    with transaction.atomic(using=using):
        task = ZohoTaskCreation.objects.using(using).select_for_update().get(pk=task.pk)
        task.task_id = remote_id if status == "CREATED" else task.task_id
        task.task_url = f"https://crm.zoho.com/crm/tab/Tasks/{task.task_id}" if task.task_id else ""
        task.technical_status, task.error_category = status, category
        task.save(using=using, update_fields=("task_id", "task_url", "technical_status", "error_category", "updated_at"))
        CaseActivity.objects.using(using).create(case_id=task.case_id, event_type=event, actor=actor, metadata={"responsable": responsible, "ramo": ramo, **({"task_id": task.task_id} if task.task_id else {}), **({"category": category} if category else {})})
    return task


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
