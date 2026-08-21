"""Ensayo Sandbox de vehículos en ``Riesgos``; no crea relaciones ni subriesgos."""
from __future__ import annotations

import re
import threading
from typing import Mapping

from django.conf import settings
from django.core.exceptions import ValidationError

from integrations.zoho import get_zoho
from integrations.zoho.exceptions import ZohoAPIError, ZohoError, ZohoTimeoutError
from integrations.zoho.settings import ZohoSettings


RISK_MODULE = "Riesgos"
RISK_CONFIRMATION = "SANDBOX_MOBILITY_RISK_SEED"
RISK_FIELDS = frozenset({
    "Name", "Tipo_de_riesgo", "Placa_del_vehiculo", "Marca_Tipo_Caracter_sticas",
    "Modelo", "Clase", "Ciudad", "Tipo_de_uso",
})
RISK_REQUIRED = frozenset({"Name", "Tipo_de_riesgo", "Placa_del_vehiculo"})
RISK_TYPE = "Vehículos"
# Colombian vehicle plates observed in Zoho are alphanumeric and may use
# legacy formats (for example ``PJR76D``) in addition to ``ABC123``.
# Mobility keeps one normalized key for READ de-duplication and CREATE.
_PLATE = re.compile(r"^(?=.*\d)[A-Z0-9]{3,8}$")
_LOCK = threading.Lock()


class RiskPublishingDisabled(RuntimeError):
    pass


class RiskPublicationUncertain(RuntimeError):
    reconciliation_required = True


class RiskPublicationRejected(RuntimeError):
    pass


def normalize_plate(value: object) -> str:
    plate = re.sub(r"[\s-]+", "", str(value or "")).upper()
    if not _PLATE.fullmatch(plate):
        raise ValidationError("La placa de Movilidad debe ser alfanumérica y contener entre 3 y 8 caracteres.")
    return plate


def build_risk_payload(*, name: object, plate: object, model: object,
                       vehicle_class: str = "Autos familiares",
                       brand_reference: str = "VELTRIX TEST",
                       city: str = "Bogotá - Cundinamarca",
                       use: str = "Residencial") -> dict[str, object]:
    normalized_plate = normalize_plate(plate)
    # Confirmed Mobility contract: Riesgos.Name is the normalized plate.
    normalized_name = normalized_plate
    try:
        normalized_model = int(model)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Modelo debe ser un entero.") from exc
    if normalized_model < 1900 or normalized_model > 2100:
        raise ValidationError("Modelo fuera de rango.")
    # Functional Mobility candidates come from the public form and may use
    # values such as ``Caserito`` that are valid in the current Zoho picklist.
    # The publisher validates presence and allowlists the field names; it must
    # not reject a legitimate picklist value merely because it was absent from
    # the original synthetic seed fixtures.
    payload = {
        "Name": normalized_name,
        "Tipo_de_riesgo": RISK_TYPE,
        "Placa_del_vehiculo": normalized_plate,
        "Marca_Tipo_Caracter_sticas": str(brand_reference).strip(),
        "Modelo": normalized_model,
    }
    # Clase, Ciudad and Tipo_de_uso are optional in the confirmed Mobility
    # contract.  Preserve supplied values, but never invent a default for an
    # empty field.
    if str(vehicle_class or "").strip():
        payload["Clase"] = str(vehicle_class).strip()
    if str(city or "").strip():
        payload["Ciudad"] = str(city).strip()
    if str(use or "").strip():
        payload["Tipo_de_uso"] = str(use).strip()
    if set(payload) - RISK_FIELDS or not RISK_REQUIRED.issubset(payload):
        raise ValidationError("El payload Riesgos no coincide con la allowlist.")
    return payload


def resolve_risk_by_plate(*, plate: str, zoho=None) -> dict[str, object]:
    normalized = normalize_plate(plate)
    facade = zoho or get_zoho(profile="sandbox")
    page = facade.search.by_criteria(
        module=RISK_MODULE,
        criteria=f"(Placa_del_vehiculo:equals:{normalized})",
        fields=("id", "Name", "Tipo_de_riesgo", "Placa_del_vehiculo"),
        page=1,
        limit=20,
    )
    records = tuple(getattr(page, "records", ()) or ())
    exact = []
    for record in records:
        try:
            if normalize_plate(record.get("Placa_del_vehiculo")) == normalized:
                exact.append(record)
        except ValidationError:
            # Ignore malformed/non-vehicle rows returned by a broad Zoho search.
            continue
    if not exact:
        return {"status": "NOT_FOUND"}
    if len(exact) > 1:
        return {"status": "AMBIGUOUS", "count": len(exact)}
    return {"status": "FOUND", "record_id": str(exact[0].get("id") or "")}


def create_sandbox_risk(payload: Mapping[str, object], *, confirmation: str,
                        zoho=None) -> dict[str, object]:
    if str(getattr(settings, "ZOHO_ACTIVE_PROFILE", "")).lower() != "sandbox":
        raise RiskPublishingDisabled("Riesgos sólo admite Sandbox.")
    if not getattr(settings, "COLECTIVOS_MOBILITY_RISK_SEED_ENABLED", False):
        raise RiskPublishingDisabled("El seed de Riesgos está deshabilitado.")
    if str(getattr(settings, "COLECTIVOS_MOBILITY_RISK_SEED_CONFIRMATION", "")) != RISK_CONFIRMATION:
        raise RiskPublishingDisabled("Falta la confirmación configurada del seed.")
    if str(confirmation or "").strip() != RISK_CONFIRMATION:
        raise RiskPublishingDisabled("Falta la confirmación explícita del seed.")
    if not ZohoSettings.from_django("sandbox").write_enabled:
        raise RiskPublishingDisabled("La escritura Sandbox está deshabilitada.")
    if set(payload) - RISK_FIELDS or not RISK_REQUIRED.issubset(payload):
        raise ValidationError("El payload Riesgos no coincide con la allowlist.")
    normalized = build_risk_payload(
        name=payload["Name"], plate=payload["Placa_del_vehiculo"], model=payload["Modelo"],
        vehicle_class=payload.get("Clase", ""), brand_reference=payload.get("Marca_Tipo_Caracter_sticas", ""),
        city=payload.get("Ciudad", ""), use=payload.get("Tipo_de_uso", ""),
    )
    with _LOCK:
        duplicate = resolve_risk_by_plate(plate=normalized["Placa_del_vehiculo"], zoho=zoho)
        if duplicate["status"] != "NOT_FOUND":
            raise RiskPublicationRejected("La placa requiere resolución antes de crear.")
        try:
            result = (zoho or get_zoho(profile="sandbox")).records.create(
                module=RISK_MODULE, records=(normalized,)
            )
        except ZohoTimeoutError as exc:
            raise RiskPublicationUncertain("Resultado incierto; requiere conciliación manual.") from exc
        except ZohoAPIError as exc:
            if getattr(exc, "request_sent", None) is True and (getattr(exc, "status_code", 0) or 0) >= 500:
                raise RiskPublicationUncertain("Resultado incierto; requiere conciliación manual.") from exc
            raise
    records = tuple(getattr(result, "records", ()) or ())
    if len(records) != 1 or not getattr(records[0], "succeeded", False):
        raise RiskPublicationRejected("Zoho rechazó el Riesgo.")
    record_id = str(getattr(records[0], "record_id", "") or "")
    if not record_id:
        raise RiskPublicationRejected("Zoho no devolvió ID del Riesgo.")
    return {"module": RISK_MODULE, "record_id": record_id, "succeeded": True}


def risk_dry_run(items: tuple[Mapping[str, object], ...]) -> dict[str, object]:
    return {
        "mode": "dry-run", "profile": "sandbox", "module": RISK_MODULE,
        "planned": len(items), "writes": 0,
        "records": [{"name": item["Name"], "plate": item["Placa_del_vehiculo"],
                     "type": item["Tipo_de_riesgo"], "model": item["Modelo"]} for item in items],
    }
