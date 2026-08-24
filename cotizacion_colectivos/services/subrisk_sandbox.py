"""Sandbox-only ensayo de escritura para un único registro ``Riesgos1``.

Este módulo no forma parte del flujo productivo de Cotización: construye y
publica únicamente el payload explícito del ensayo Salud, con barreras propias.
"""
from __future__ import annotations

import re
import threading
from datetime import date
from typing import Mapping

from django.conf import settings
from django.core.exceptions import ValidationError

from integrations.zoho import get_zoho
from integrations.zoho.exceptions import ZohoAPIError, ZohoTimeoutError
from integrations.zoho.settings import ZohoSettings
from .write_guards import require_write_guard


SUBRISK_MODULE = "Riesgos1"
SUBRISK_CONFIRMATION = "SANDBOX_SUBRISK_WRITE"
SUBRISK_FIELDS = frozenset({
    "Name", "P_liza", "Contacto_facturaci_n_dividida_colectivas", "Asegurado",
    "Riesgo", "Ramo", "Estado", "Parentesco", "Fecha_ingreso_riesgo", "Plan",
})
SUBRISK_REQUIRED_FIELDS = frozenset({
    "Name", "P_liza", "Contacto_facturaci_n_dividida_colectivas", "Asegurado",
    "Ramo", "Estado", "Parentesco", "Fecha_ingreso_riesgo",
})
MOBILITY_REQUIRED_FIELDS = SUBRISK_REQUIRED_FIELDS | {"Riesgo"}
SUBRISK_ALLOWED_BRANCH = "Salud colectivo"
SUBRISK_ALLOWED_STATUS = "Activo"
SUBRISK_ALLOWED_RELATIONS = frozenset({"Afiliado"})
MOBILITY_SUBRISK_CONFIRMATION = "SANDBOX_MOBILITY_SUBRISK_SEED"
MOBILITY_SUBRISK_BRANCH = "Movilidad colectivo"
MOBILITY_SUBRISK_SCENARIOS = (
    ("TEST-MOV-SUBRISK-001", "4991513000270981010", "4991513000270981010", "4991513000270982008", "VTX001"),
    ("TEST-MOV-SUBRISK-002", "4991513000270984015", "4991513000270984015", "4991513000270990022", "VTX002"),
    ("TEST-MOV-SUBRISK-004-A", "4991513000270978007", "4991513000270978007", "4991513000270981017", "VTX004"),
    ("TEST-MOV-SUBRISK-004-B", "4991513000270978007", "4991513000270978007", "4991513000270978012", "VTX005"),
)
_ZOHO_ID = re.compile(r"^\d{10,30}$")
_WRITE_LOCK = threading.Lock()


class SubriskPublishingDisabled(RuntimeError):
    pass


class SubriskPublicationUncertain(RuntimeError):
    reconciliation_required = True


class SubriskPublicationRejected(RuntimeError):
    pass


def validate_lookup_id(value: object, field: str) -> str:
    """Validate the only lookup representation allowed by this ensayo."""
    if isinstance(value, bool) or value is None:
        raise ValidationError(f"{field}: el ID del lookup es obligatorio.")
    if not isinstance(value, (str, int)):
        raise ValidationError(f"{field}: el ID debe ser texto o entero.")
    normalized = str(value).strip()
    if not _ZOHO_ID.fullmatch(normalized):
        raise ValidationError(f"{field}: el ID Zoho no es válido.")
    return normalized


def _lookup(value: object, field: str) -> dict[str, str]:
    return {"id": validate_lookup_id(value, field)}


def _validate_common_subrisk(*, name: object, policy_id: object,
                             affiliate_contact_id: object, insured_contact_id: object,
                             risk_id: object, entry_date: object, ramo: str,
                             estado: str, parentesco: str) -> tuple[str, dict[str, str], dict[str, str], dict[str, str], dict[str, str], str]:
    normalized_name = str(name or "").strip()
    if not normalized_name or len(normalized_name) > 120 or any(ord(char) < 32 for char in normalized_name):
        raise ValidationError("Name del subriesgo no es válido.")
    if estado != SUBRISK_ALLOWED_STATUS:
        raise ValidationError("Estado del ensayo no es válido.")
    if parentesco not in SUBRISK_ALLOWED_RELATIONS:
        raise ValidationError("Parentesco no está dentro del catálogo del ensayo.")
    if isinstance(entry_date, date):
        normalized_date = entry_date.isoformat()
    else:
        normalized_date = str(entry_date or "").strip()
        try:
            date.fromisoformat(normalized_date)
        except ValueError as exc:
            raise ValidationError("Fecha_ingreso_riesgo debe estar en formato ISO.") from exc
    return (
        normalized_name,
        _lookup(policy_id, "P_liza"),
        _lookup(affiliate_contact_id, "Contacto_facturaci_n_dividida_colectivas"),
        _lookup(insured_contact_id, "Asegurado"),
        _lookup(risk_id, "Riesgo"),
        normalized_date,
    )


def build_subrisk_payload(*, policy_id: object, affiliate_contact_id: object,
                          insured_contact_id: object, subrisk_name: object,
                          entry_date: object, plan: object = "",
                          ramo: str = SUBRISK_ALLOWED_BRANCH,
                          estado: str = SUBRISK_ALLOWED_STATUS,
                          parentesco: str = "Afiliado") -> dict[str, object]:
    """Build the closed, Salud-only payload for exactly one test record."""
    name = str(subrisk_name or "").strip()
    if not name or len(name) > 120 or any(ord(char) < 32 for char in name):
        raise ValidationError("Name del subriesgo no es válido.")
    if ramo != SUBRISK_ALLOWED_BRANCH:
        raise ValidationError("El ensayo sólo admite Salud colectivo.")
    if estado != SUBRISK_ALLOWED_STATUS:
        raise ValidationError("Estado del ensayo no es válido.")
    if parentesco not in SUBRISK_ALLOWED_RELATIONS:
        raise ValidationError("Parentesco no está dentro del catálogo del ensayo.")
    if isinstance(entry_date, date):
        normalized_date = entry_date.isoformat()
    else:
        normalized_date = str(entry_date or "").strip()
        try:
            date.fromisoformat(normalized_date)
        except ValueError as exc:
            raise ValidationError("Fecha_ingreso_riesgo debe estar en formato ISO.") from exc
    payload: dict[str, object] = {
        "Name": name,
        "P_liza": _lookup(policy_id, "P_liza"),
        "Contacto_facturaci_n_dividida_colectivas": _lookup(
            affiliate_contact_id, "Contacto_facturaci_n_dividida_colectivas"
        ),
        "Asegurado": _lookup(insured_contact_id, "Asegurado"),
        "Ramo": ramo,
        "Estado": estado,
        "Parentesco": parentesco,
        "Fecha_ingreso_riesgo": normalized_date,
    }
    if str(plan or "").strip():
        payload["Plan"] = str(plan).strip()
    if set(payload) - SUBRISK_FIELDS or not SUBRISK_REQUIRED_FIELDS.issubset(payload):
        raise ValidationError("El payload del ensayo no coincide con la allowlist.")
    return payload


def build_mobility_subrisk_payload(*, policy_id: object, affiliate_contact_id: object,
                                   insured_contact_id: object, risk_id: object,
                                   subrisk_name: object, entry_date: object,
                                   plan: object = "",
                                   estado: str = SUBRISK_ALLOWED_STATUS,
                                   parentesco: str = "Afiliado") -> dict[str, object]:
    """Build the closed Movilidad contract without widening the Salud builder."""
    name, policy, affiliate, insured, risk, normalized_date = _validate_common_subrisk(
        name=subrisk_name, policy_id=policy_id,
        affiliate_contact_id=affiliate_contact_id, insured_contact_id=insured_contact_id,
        risk_id=risk_id, entry_date=entry_date, ramo=MOBILITY_SUBRISK_BRANCH,
        estado=estado, parentesco=parentesco,
    )
    payload = {
        "Name": name,
        "P_liza": policy,
        "Contacto_facturaci_n_dividida_colectivas": affiliate,
        "Asegurado": insured,
        "Riesgo": risk,
        "Ramo": MOBILITY_SUBRISK_BRANCH,
        "Estado": estado,
        "Parentesco": parentesco,
        "Fecha_ingreso_riesgo": normalized_date,
    }
    if str(plan or "").strip():
        payload["Plan"] = str(plan).strip()
    if set(payload) - SUBRISK_FIELDS or not SUBRISK_REQUIRED_FIELDS.issubset(payload):
        raise ValidationError("El payload Movilidad no coincide con la allowlist.")
    return payload


def resolve_policy_by_number(*, policy_number: str, zoho) -> dict[str, object]:
    """Resolve the policy using the confirmed Polizas.Name field."""
    page = zoho.search.by_criteria(
        module="Polizas", criteria=f"(Name:equals:{policy_number})",
        fields=("id", "Name"), page=1, limit=20,
    )
    records = tuple(getattr(page, "records", ()) or ())
    exact = [r for r in records if str(r.get("Name") or "").strip() == policy_number]
    if not exact:
        return {"status": "NOT_FOUND"}
    if len(exact) > 1:
        return {"status": "AMBIGUOUS", "count": len(exact)}
    return {"status": "FOUND", "record_id": validate_lookup_id(exact[0].get("id"), "Polizas.id")}


def resolve_reference_by_id(*, module: str, record_id: str, zoho,
                            fields: tuple[str, ...] = ("id", "Name")) -> dict[str, object]:
    page = zoho.search.by_criteria(
        module=module, criteria=f"(id:equals:{record_id})", fields=fields,
        page=1, limit=20,
    )
    records = tuple(getattr(page, "records", ()) or ())
    if len(records) != 1:
        return {"status": "NOT_FOUND" if not records else "AMBIGUOUS", "count": len(records)}
    if str(records[0].get("id") or "") != str(record_id):
        return {"status": "NOT_FOUND"}
    return {"status": "FOUND", "record": records[0]}


def resolve_mobility_subrisk_relation(*, policy_id: str, risk_id: str,
                                      affiliate_contact_id: str, insured_contact_id: str,
                                      zoho) -> dict[str, object]:
    page = zoho.search.by_criteria(
        module=SUBRISK_MODULE,
        criteria=f"(P_liza:equals:{policy_id}and(Riesgo:equals:{risk_id}))",
        fields=("id", "P_liza", "Riesgo", "Contacto_facturaci_n_dividida_colectivas", "Asegurado"),
        page=1, limit=20,
    )
    records = tuple(getattr(page, "records", ()) or ())
    def lookup_id(record: Mapping[str, object], field: str) -> str:
        value = record.get(field)
        return str(value.get("id") if isinstance(value, Mapping) else value or "")
    exact = [r for r in records if lookup_id(r, "P_liza") == policy_id and lookup_id(r, "Riesgo") == risk_id
             and lookup_id(r, "Contacto_facturaci_n_dividida_colectivas") == affiliate_contact_id
             and lookup_id(r, "Asegurado") == insured_contact_id]
    if len(exact) > 1:
        return {"status": "AMBIGUOUS", "count": len(exact)}
    if exact:
        return {"status": "ALREADY_EXISTS", "record_id": str(exact[0].get("id") or "")}
    return {"status": "NOT_FOUND"}


def masked_remote_id(value: object) -> str:
    text = str(value or "")
    return f"***{text[-4:]}" if len(text) >= 4 else "***"


def sanitized_subrisk_dry_run(payload: Mapping[str, object], *, profile: str) -> dict[str, object]:
    return {
        "mode": "dry-run", "profile": profile, "module": SUBRISK_MODULE,
        "operation": "records.create", "fields": tuple(payload),
        "lookups": {
            key: masked_remote_id(value.get("id"))
            for key, value in payload.items()
            if isinstance(value, Mapping) and "id" in value
        },
        "payload": {
            key: ("<lookup>" if isinstance(value, Mapping) else value)
            for key, value in payload.items()
        },
        "writes": 0,
    }


def create_subrisk_sandbox(payload: Mapping[str, object], *, profile: str,
                           confirmation: str, zoho=None) -> dict[str, object]:
    """Perform at most one guarded Sandbox CREATE; never retries or falls back."""
    require_write_guard(
        entity="subrisk", profile=profile, confirmation=confirmation,
        feature_flag="COLECTIVOS_SUBRISK_PUBLISH_ENABLED",
        legacy_setting="COLECTIVOS_SUBRISK_WRITE_CONFIRMATION",
        disabled_error=SubriskPublishingDisabled,
    )
    if set(payload) - SUBRISK_FIELDS or not SUBRISK_REQUIRED_FIELDS.issubset(payload):
        raise ValidationError("El payload del ensayo no coincide con la allowlist.")
    for field in ("P_liza", "Contacto_facturaci_n_dividida_colectivas", "Asegurado"):
        if not isinstance(payload.get(field), Mapping) or set(payload[field]) != {"id"}:
            raise ValidationError(f"{field}: use exactamente {{'id': '<ID Zoho>'}}.")
    normalized = build_subrisk_payload(
        policy_id=payload["P_liza"]["id"],
        affiliate_contact_id=payload["Contacto_facturaci_n_dividida_colectivas"]["id"],
        insured_contact_id=payload["Asegurado"]["id"],
        subrisk_name=payload["Name"], entry_date=payload["Fecha_ingreso_riesgo"],
        plan=payload.get("Plan", ""), ramo=payload["Ramo"],
        estado=payload["Estado"], parentesco=payload["Parentesco"],
    )
    with _WRITE_LOCK:
        try:
                result = (zoho or get_zoho(profile=profile)).records.create(
                module=SUBRISK_MODULE, records=(normalized,)
            )
        except ZohoTimeoutError as exc:
            raise SubriskPublicationUncertain("Resultado incierto; requiere conciliación manual.") from exc
        except ZohoAPIError as exc:
            if getattr(exc, "request_sent", None) is True and (getattr(exc, "status_code", 0) or 0) >= 500:
                raise SubriskPublicationUncertain("Resultado incierto; requiere conciliación manual.") from exc
            raise
    records = tuple(getattr(result, "records", ()) or ())
    if len(records) != 1 or not getattr(records[0], "succeeded", False):
        raise SubriskPublicationRejected("Zoho rechazó el ensayo Riesgos1.")
    record_id = str(getattr(records[0], "record_id", "") or "").strip()
    if not _ZOHO_ID.fullmatch(record_id):
        raise SubriskPublicationRejected("Zoho no devolvió un identificador válido.")
    return {"module": SUBRISK_MODULE, "record_id": record_id, "succeeded": True,
            "code": str(getattr(records[0], "code", "") or "")}


def create_mobility_subrisk_sandbox(payload: Mapping[str, object], *,
                                    confirmation: str, zoho=None,
                                    profile: str | None = None,
                                    operational: bool = False) -> dict[str, object]:
    """Guarded CREATE for the explicit Movilidad ensayo only."""
    profile = str(profile or getattr(settings, "ZOHO_ACTIVE_PROFILE", "sandbox")).strip().lower()
    require_write_guard(
        entity="subrisk", profile=profile, confirmation=confirmation,
        feature_flag="COLECTIVOS_SUBRISK_PUBLISH_ENABLED",
        legacy_setting=("COLECTIVOS_SUBRISK_WRITE_CONFIRMATION" if operational else "COLECTIVOS_MOBILITY_SUBRISK_SEED_CONFIRMATION"),
        expected_override=("" if operational else MOBILITY_SUBRISK_CONFIRMATION),
        disabled_error=SubriskPublishingDisabled,
    )
    if not operational and not getattr(settings, "COLECTIVOS_MOBILITY_SUBRISK_SEED_ENABLED", False):
        raise SubriskPublishingDisabled("El seed Movilidad está deshabilitado.")
    if set(payload) - SUBRISK_FIELDS or not MOBILITY_REQUIRED_FIELDS.issubset(payload):
        raise ValidationError("El payload Movilidad no coincide con la allowlist.")
    for field in ("P_liza", "Contacto_facturaci_n_dividida_colectivas", "Asegurado", "Riesgo"):
        if not isinstance(payload.get(field), Mapping) or set(payload[field]) != {"id"}:
            raise ValidationError(f"{field}: use exactamente {{'id': '<ID Zoho>'}}.")
    normalized = build_mobility_subrisk_payload(
        policy_id=payload["P_liza"]["id"],
        affiliate_contact_id=payload["Contacto_facturaci_n_dividida_colectivas"]["id"],
        insured_contact_id=payload["Asegurado"]["id"], risk_id=payload["Riesgo"]["id"],
        subrisk_name=payload["Name"], entry_date=payload["Fecha_ingreso_riesgo"],
        plan=payload.get("Plan", ""), estado=payload["Estado"], parentesco=payload["Parentesco"],
    )
    with _WRITE_LOCK:
        duplicate = resolve_mobility_subrisk_relation(
            policy_id=normalized["P_liza"]["id"], risk_id=normalized["Riesgo"]["id"],
            affiliate_contact_id=normalized["Contacto_facturaci_n_dividida_colectivas"]["id"],
            insured_contact_id=normalized["Asegurado"]["id"], zoho=zoho or get_zoho(profile=profile),
        )
        if duplicate["status"] != "NOT_FOUND":
            raise SubriskPublicationRejected("La relación Movilidad ya existe o es ambigua.")
        try:
            result = (zoho or get_zoho(profile=profile)).records.create(
                module=SUBRISK_MODULE, records=(normalized,)
            )
        except ZohoTimeoutError as exc:
            raise SubriskPublicationUncertain("Resultado incierto; requiere conciliación manual.") from exc
        except ZohoAPIError as exc:
            if getattr(exc, "request_sent", None) is True and (getattr(exc, "status_code", 0) or 0) >= 500:
                raise SubriskPublicationUncertain("Resultado incierto; requiere conciliación manual.") from exc
            raise
    records = tuple(getattr(result, "records", ()) or ())
    if len(records) != 1 or not getattr(records[0], "succeeded", False):
        raise SubriskPublicationRejected("Zoho rechazó el Riesgos1 de Movilidad.")
    record_id = str(getattr(records[0], "record_id", "") or "").strip()
    if not _ZOHO_ID.fullmatch(record_id):
        raise SubriskPublicationRejected("Zoho no devolvió un identificador válido.")
    return {"module": SUBRISK_MODULE, "record_id": record_id, "succeeded": True,
            "code": str(getattr(records[0], "code", "") or "")}
