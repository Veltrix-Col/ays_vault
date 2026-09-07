"""Shared entity-resolution core for accepted individual sources."""
from __future__ import annotations

from typing import Mapping

from django.core.exceptions import ValidationError

from .person_contract import contact_identity_missing_fields, get_contacts_publisher, resolve_contact_by_document
from .risk_sandbox import build_risk_payload, create_sandbox_risk, resolve_risk_by_plate
from ..branches import LIFE_GROUP_CONTRACTS, LIFE_GROUP_VALUES, canonical_life_group_value, contract_required_ingress_fields
from .subrisk_sandbox import (
    build_life_group_subrisk_payload, build_subrisk_payload,
    build_mobility_subrisk_payload, create_subrisk_sandbox,
    create_mobility_subrisk_sandbox, resolve_mobility_subrisk_relation,
)


def resolve_operational_entities(*, payload: Mapping[str, object], state, profile: str,
                                 confirmation: str, zoho=None,
                                 required: tuple[str, ...] = ("contact",),
                                 branch_name: str = "",
                                 subrisk_confirmation: str = "",
                                 on_contact_resolved=None) -> dict[str, object]:
    """Resolve the common Contact stage against any state-store implementation.

    Risk/Subrisk stages are intentionally injected by the caller so this core
    does not depend on CotizacionIndividual or a particular persistence model.
    """
    result = {"contact": str(getattr(state, "contact_zoho_id", "") or "")}
    branch_code = str(getattr(state, "branch_code", "") or "").strip().lower()
    contract_fields = contract_required_ingress_fields(branch_code, branch_name or payload.get("Ramo") or payload.get("ramo") or "")
    missing_contract_fields = {
        "parentesco": "Parentesco",
        "plate": "Placa",
        "model": "Modelo",
    }
    missing = [missing_contract_fields[field] for field in contract_fields if not str(payload.get(field) or "").strip()]
    if missing:
        raise ValidationError("Completa los siguientes datos antes de registrar el ingreso: " + ", ".join(missing) + ".")
    data = {
        "First_Name": payload.get("first_name") or payload.get("First_Name") or payload.get("nombres") or "",
        "Last_Name": payload.get("last_name") or payload.get("Last_Name") or payload.get("apellidos") or "",
        "Tipo_ID": payload.get("id_type") or payload.get("Tipo_ID") or payload.get("tipo_id") or "",
        "N_mero_de_ID": payload.get("document") or payload.get("N_mero_de_ID") or payload.get("documento") or "",
        "Date_of_Birth": payload.get("birth_date") or payload.get("Date_of_Birth") or "",
        "Email": payload.get("email") or payload.get("correo") or payload.get("correo_electronico") or payload.get("Email") or payload.get("email_address") or "",
        "Phone": payload.get("phone") or payload.get("telefono") or payload.get("telefono_contacto") or payload.get("Phone") or "",
        "Mobile": payload.get("mobile") or payload.get("celular") or payload.get("Mobile") or "",
    }
    # Contacts only requires identity fields for creation.  Email, phone and
    # birth date are optional in the existing Contacts contract and must not
    # block a novelty when they were not supplied by the client.
    missing = contact_identity_missing_fields(data)
    if missing:
        raise ValidationError("Faltan datos para procesar el ingreso: " + ", ".join(missing))
    if not result["contact"]:
        found = resolve_contact_by_document(document=str(data["N_mero_de_ID"]), document_type=str(data["Tipo_ID"]), zoho=zoho)
        if found.get("status") == "FOUND" and found.get("record_id"):
            result["contact"] = str(found["record_id"])
        elif found.get("status") == "NOT_FOUND":
            result["contact"] = str(get_contacts_publisher(profile=profile, confirmation=confirmation).create(data, zoho=zoho, status="Cliente")["record_id"])
        else:
            raise ValidationError("La persona requiere validación antes de crearla.")
        state.contact_zoho_id = result["contact"]
    if result["contact"] and on_contact_resolved is not None:
        on_contact_resolved(result["contact"])
    for name in ("risk", "subrisk"):
        result[name] = str(getattr(state, f"{name}_zoho_id", "") or "")
    life_ramo = canonical_life_group_value(
        branch_name or payload.get("Ramo") or payload.get("ramo") or ""
    )
    contract_blocked = life_ramo in LIFE_GROUP_VALUES and not LIFE_GROUP_CONTRACTS.get(life_ramo, {}).get("write_enabled", False)
    if branch_code in {"86", "exequial"}:
        contract_blocked = True
        blocked_reason = "El contrato Zoho de Exequial está pendiente de validación."
    elif branch_code and branch_code not in {"40", "movilidad", "autos", "91", "salud", "83"} and life_ramo not in LIFE_GROUP_VALUES:
        contract_blocked = True
        blocked_reason = "El contrato Zoho de este ramo está pendiente de validación."
    else:
        blocked_reason = f"El contrato Zoho de {life_ramo} está pendiente de validación."
    if contract_blocked:
        # A contract restriction has precedence over the generic
        # "all required IDs exist" calculation.  Contact progress remains
        # persisted, but the overall ingress cannot be PUBLISHED.
        return {
            "status": "BLOCKED",
            "blocked_reason": blocked_reason,
            "entities": result,
        }
    if "risk" in required and not result["risk"]:
        plate = payload.get("plate") or payload.get("license_plate") or payload.get("Placa_del_vehiculo") or ""
        if not plate:
            raise ValidationError("Faltan datos para procesar el Riesgo: Placa")
        found = resolve_risk_by_plate(plate=str(plate), zoho=zoho, profile=profile)
        if found.get("status") == "FOUND":
            result["risk"] = str(found["record_id"])
        elif found.get("status") == "NOT_FOUND":
            risk_payload = build_risk_payload(
                name=plate, plate=plate, model=payload.get("model") or payload.get("Modelo") or "",
                vehicle_class=payload.get("vehicle_class") or payload.get("Clase") or "",
                brand_reference=payload.get("brand") or payload.get("Marca_Tipo_Caracter_sticas") or "",
                city=payload.get("city") or payload.get("Ciudad") or "",
                use=payload.get("use") or payload.get("Tipo_de_uso") or "",
            )
            result["risk"] = str(create_sandbox_risk(risk_payload, confirmation=confirmation, zoho=zoho, profile=profile, operational=True)["record_id"])
        else:
            raise ValidationError("El Riesgo requiere validación antes de crear.")
        state.risk_zoho_id = result["risk"]
    if "subrisk" in required and not result["subrisk"]:
        policy_id = str(getattr(state, "policy_remote_id", "") or "")
        if not policy_id:
            raise ValidationError("No fue posible resolver la póliza Zoho del ingreso.")
        branch = str(payload.get("branch") or payload.get("schema") or getattr(state, "branch_code", "") or "SALUD").upper()
        if branch == "40":
            branch = "MOVILIDAD"
        elif branch == "91":
            branch = "SALUD"
        if branch in {"MOVILIDAD", "AUTOS"}:
            sub_payload = build_mobility_subrisk_payload(
                policy_id=policy_id,
                affiliate_contact_id=result["contact"],
                insured_contact_id=result["contact"],
                risk_id=result.get("risk", ""),
                subrisk_name=payload.get("name") or payload.get("display_name") or payload.get("nombres") or "Asegurado",
                entry_date=payload.get("entry_date") or payload.get("fecha_ingreso") or "",
                plan=payload.get("plan") or payload.get("Plan") or "",
                parentesco=payload.get("parentesco") or payload.get("Parentesco") or payload.get("relationship") or "Afiliado",
                role=payload.get("rol") or payload.get("role") or "",
            )
        elif life_ramo in LIFE_GROUP_VALUES:
            ramo = str(branch_name or payload.get("Ramo") or payload.get("ramo") or "").strip()
            sub_payload = build_life_group_subrisk_payload(
                policy_id=policy_id,
                affiliate_contact_id=result["contact"],
                insured_contact_id=result["contact"],
                subrisk_name=payload.get("name") or payload.get("display_name") or payload.get("nombres") or "Asegurado",
                entry_date=payload.get("entry_date") or payload.get("fecha_ingreso") or "",
                ramo=ramo,
                plan=payload.get("plan") or payload.get("Plan") or "",
                estado=payload.get("estado") or payload.get("Estado") or "Activo",
                parentesco=payload.get("parentesco") or payload.get("Parentesco") or payload.get("relationship") or "",
                role=payload.get("rol") or payload.get("role") or "",
            )
        elif branch == "SALUD":
            sub_payload = build_subrisk_payload(
                policy_id=policy_id, affiliate_contact_id=result["contact"], insured_contact_id=result["contact"],
                subrisk_name=payload.get("name") or payload.get("display_name") or "Asegurado",
                entry_date=payload.get("entry_date") or payload.get("fecha_ingreso") or "",
                parentesco=payload.get("parentesco") or payload.get("Parentesco") or payload.get("relationship") or "Afiliado",
                role=payload.get("rol") or payload.get("role") or "",
            )
        else:
            raise ValidationError("El contrato de Asegurado para este ramo requiere configuración específica.")
        if branch == "SALUD" or life_ramo in LIFE_GROUP_VALUES:
            existing = None
            if not existing:
                result["subrisk"] = str(create_subrisk_sandbox(
                    sub_payload, profile=profile,
                    confirmation=subrisk_confirmation or confirmation, zoho=zoho,
                )["record_id"])
            state.subrisk_zoho_id = result["subrisk"]
            return {"status": "PUBLISHED" if all(result.get(name) for name in required) else "PROCESSING", "entities": result}
        existing = resolve_mobility_subrisk_relation(policy_id=policy_id, risk_id=result["risk"], affiliate_contact_id=result["contact"], insured_contact_id=result["contact"], zoho=zoho)
        if existing.get("status") == "ALREADY_EXISTS":
            result["subrisk"] = str(existing["record_id"])
        elif existing.get("status") == "NOT_FOUND":
            result["subrisk"] = str(create_mobility_subrisk_sandbox(sub_payload, profile=profile, confirmation=confirmation, zoho=zoho, operational=True)["record_id"])
        else:
            raise ValidationError("El Asegurado requiere validación antes de crear.")
        state.subrisk_zoho_id = result["subrisk"]
    complete = all(result.get(name) for name in required)
    return {"status": "PUBLISHED" if complete else "PROCESSING", "entities": result}
