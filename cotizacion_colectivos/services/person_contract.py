"""Contrato local de resolución y futura creación de personas en Contacts.

La resolución es READ-only; el publisher sólo valida barreras y ofrece dry-run.
"""
from __future__ import annotations

import re
import unicodedata
import threading
from dataclasses import dataclass
from datetime import date
from typing import Mapping

from django.conf import settings
from django.core.exceptions import ValidationError

from integrations.zoho.exceptions import ZohoError
from integrations.zoho import get_zoho
from integrations.zoho.exceptions import ZohoAPIError, ZohoTimeoutError
from integrations.zoho.settings import ZohoSettings

from .common import colectivos_zoho, escape_criteria_value, sign_record_id, translate_zoho_error


CONTACT_FIELDS = frozenset({
    "First_Name", "Last_Name", "Tipo_de_persona", "Tipo_ID", "N_mero_de_ID",
    "Date_of_Birth", "Email", "Mobile", "Phone", "Estado", "Tratamiento_de_datos",
})
CONTACT_REQUIRED = frozenset({"Last_Name", "Tipo_de_persona", "Tipo_ID", "N_mero_de_ID"})
PERSON_STATUS = {"FOUND", "NOT_FOUND", "AMBIGUOUS", "TYPE_MISMATCH", "INVALID_INPUT"}


def _fold(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in text if not unicodedata.combining(char)).strip().casefold()


def build_contact_payload(data: Mapping[str, object]) -> dict[str, object]:
    """Build only the confirmed Contacts allowlist; never infer consent."""
    last_name = str(data.get("Last_Name") or "").strip()
    id_type = str(data.get("Tipo_ID") or "").strip()
    document = str(data.get("N_mero_de_ID") or "").strip()
    if not last_name or not id_type or not document:
        raise ValidationError("Faltan datos para crear persona.")
    payload: dict[str, object] = {
        "Last_Name": last_name,
        "Tipo_de_persona": "Persona natural",
        "Tipo_ID": id_type,
        "N_mero_de_ID": document,
        "Estado": "Prospecto",
    }
    for field in ("First_Name", "Email", "Mobile", "Phone", "Date_of_Birth"):
        value = data.get(field)
        if value not in (None, ""):
            payload[field] = value.isoformat() if isinstance(value, date) else str(value).strip()
    if data.get("Tratamiento_de_datos") in {"Si", "No"}:
        payload["Tratamiento_de_datos"] = data["Tratamiento_de_datos"]
    return payload


def resolve_contact_by_document(*, document: str, document_type: str, zoho=None) -> dict[str, object]:
    document = str(document or "").strip()
    document_type = str(document_type or "").strip()
    if not document or not document_type or not re.fullmatch(r"[0-9A-Za-z.-]{3,40}", document):
        return {"status": "INVALID_INPUT"}
    try:
        facade = zoho or colectivos_zoho()
        page = facade.search.by_criteria(
            module="Contacts",
            criteria=f"(N_mero_de_ID:equals:{escape_criteria_value(document)})",
            fields=("id", "Full_Name", "First_Name", "Last_Name", "N_mero_de_ID", "Tipo_ID", "Estado"),
            page=1,
            limit=20,
        )
    except ZohoError as exc:
        raise translate_zoho_error(exc) from exc
    records = tuple(getattr(page, "records", ()) or ())
    exact = [item for item in records if str(item.get("N_mero_de_ID") or "").strip() == document]
    if not exact:
        return {"status": "NOT_FOUND"}
    typed = [item for item in exact if str(item.get("Tipo_ID") or "").strip() == document_type]
    if not typed:
        return {"status": "TYPE_MISMATCH"}
    if len(typed) != 1:
        return {"status": "AMBIGUOUS", "count": len(typed)}
    item = typed[0]
    return {
        "status": "FOUND",
        "display_name": str(item.get("Full_Name") or item.get("Last_Name") or "Persona"),
        "state": str(item.get("Estado") or ""),
        "detail_token": sign_record_id(item.get("id"), "person") if item.get("id") else "",
    }


def resolve_inclusion_person(*, document: str, document_type: str, zoho=None) -> dict[str, object]:
    """Shared resolver entry point for future INCLUIR handling; RETIRO never calls it."""
    return resolve_contact_by_document(document=document, document_type=document_type, zoho=zoho)


@dataclass(frozen=True)
class ContactsDryRunPublisher:
    profile: str = "sandbox"

    def __post_init__(self):
        if self.profile != "sandbox":
            raise ValidationError("Contacts sólo admite Sandbox en esta fase.")

    def dry_run(self, data: Mapping[str, object]) -> dict[str, object]:
        payload = build_contact_payload(data)
        return {
            "mode": "dry-run", "profile": self.profile, "module": "Contacts",
            "fields": tuple(payload), "document_type": payload["Tipo_ID"],
            "has_email": bool(payload.get("Email")), "has_mobile": bool(payload.get("Mobile")),
            "has_birth_date": bool(payload.get("Date_of_Birth")), "writes": 0,
        }


class ContactPublicationUncertain(RuntimeError):
    reconciliation_required = True


class ContactPublicationRejected(RuntimeError):
    pass


class ContactPublishingDisabled(RuntimeError):
    pass


_CONTACT_WRITE_LOCK = threading.Lock()


class GuardedSandboxContactPublisher:
    """Single guarded Contacts.CREATE entry point; never retries uncertain writes."""

    def __init__(self, *, confirmation: str):
        if str(getattr(settings, "ZOHO_ACTIVE_PROFILE", "")).strip().lower() != "sandbox":
            raise ContactPublishingDisabled("Contacts sólo admite Sandbox en esta fase.")
        if not getattr(settings, "COLECTIVOS_CONTACT_PUBLISH_ENABLED", False):
            raise ContactPublishingDisabled("Contacts CREATE está deshabilitado.")
        if str(getattr(settings, "COLECTIVOS_CONTACT_WRITE_CONFIRMATION", "")).strip() != "SANDBOX_CONTACT_WRITE":
            raise ContactPublishingDisabled("Falta la confirmación configurada de Contacts Sandbox.")
        if str(confirmation or "").strip() != "SANDBOX_CONTACT_WRITE":
            raise ContactPublishingDisabled("Falta la confirmación explícita de Contacts Sandbox.")
        config = ZohoSettings.from_django("sandbox")
        if not config.write_enabled:
            raise ContactPublishingDisabled("La escritura Sandbox está deshabilitada.")

    def create(self, data: Mapping[str, object], *, zoho=None) -> dict[str, object]:
        payload = build_contact_payload(data)
        with _CONTACT_WRITE_LOCK:
            existing = resolve_contact_by_document(
                document=str(payload["N_mero_de_ID"]),
                document_type=str(payload["Tipo_ID"]),
                zoho=zoho,
            )
            if existing["status"] == "FOUND":
                raise ContactPublicationRejected("La persona ya existe en Contacts.")
            if existing["status"] != "NOT_FOUND":
                raise ContactPublicationRejected("La persona requiere validación antes de crearla.")
            try:
                result = (zoho or get_zoho(profile="sandbox")).records.create(
                    module="Contacts", records=(payload,),
                )
            except ZohoTimeoutError as exc:
                raise ContactPublicationUncertain("Resultado incierto; requiere conciliación en Contacts.") from exc
            except ZohoAPIError as exc:
                if getattr(exc, "request_sent", None) is True and (getattr(exc, "status_code", 0) or 0) >= 500:
                    raise ContactPublicationUncertain("Resultado incierto; requiere conciliación en Contacts.") from exc
                raise
        records = tuple(getattr(result, "records", ()) or ())
        if len(records) != 1 or not getattr(records[0], "succeeded", False):
            code = str(getattr(records[0], "code", "WRITE_REJECTED") if records else "WRITE_REJECTED")[:40]
            raise ContactPublicationRejected(f"Contacts rechazó la creación ({code}).")
        record_id = str(getattr(records[0], "record_id", "") or "").strip()
        if not record_id:
            raise ContactPublicationRejected("Contacts no devolvió un identificador válido.")
        return {"module": "Contacts", "record_id": record_id, "succeeded": True, "code": str(getattr(records[0], "code", "") or "")}


def get_contacts_publisher(*, profile: str = "sandbox", confirmation: str = ""):
    """Factory for the guarded publisher; production is rejected before facade access."""
    if profile != "sandbox" or str(getattr(settings, "ZOHO_ACTIVE_PROFILE", "")).lower() != "sandbox":
        raise ValidationError("Contacts WRITE está bloqueado fuera de Sandbox.")
    return GuardedSandboxContactPublisher(confirmation=confirmation)
