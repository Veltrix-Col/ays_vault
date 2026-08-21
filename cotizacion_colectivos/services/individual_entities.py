"""Read-only entity orchestration for accepted Mobility quotations.

This module deliberately keeps the client's encrypted response immutable.  It
stores only a compact, operational snapshot in ``safe_metadata`` and delegates
all writes to the existing guarded publishers.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Mapping

from django.conf import settings
from django.utils import timezone

from vault.crypto import decrypt

from .common import colectivos_zoho, unsign_record_context
from .person_contract import resolve_contact_by_document, contact_missing_fields, PersonCandidate
from .risk_sandbox import normalize_plate
from .subrisk_sandbox import resolve_mobility_subrisk_relation, resolve_policy_by_number

PERSON_PROPOSAL_FIELDS = frozenset({"First_Name", "Last_Name", "Tipo_ID", "N_mero_de_ID", "Date_of_Birth", "Email", "Mobile", "Phone"})
RISK_PROPOSAL_FIELDS = frozenset({"Name", "Tipo_de_riesgo", "Placa_del_vehiculo", "Marca_Tipo_Caracter_sticas", "Modelo", "Clase", "Ciudad", "Tipo_de_uso"})
SUBRISK_PROPOSAL_FIELDS = frozenset({"Name", "Ramo", "Estado", "Parentesco", "Fecha_ingreso_riesgo", "Plan"})
_CORRECTION_ALIASES = {
    "first_name": "First_Name", "firstName": "First_Name",
    "last_name": "Last_Name", "lastName": "Last_Name",
    "id_type": "Tipo_ID", "document_type": "Tipo_ID", "requester_id_type": "Tipo_ID",
    "document": "N_mero_de_ID", "requester_document": "N_mero_de_ID",
    "birth_date": "Date_of_Birth", "date_of_birth": "Date_of_Birth",
    "fecha_nacimiento": "Date_of_Birth", "requester_birth_date": "Date_of_Birth",
    "email": "Email", "requester_email": "Email",
    "phone": "Phone", "mobile": "Mobile", "requester_phone": "Phone",
}


def _person_identity_key(item: Mapping[str, object]) -> tuple[str, str]:
    candidate = item.get("candidate") if isinstance(item.get("candidate"), Mapping) else item
    id_type = str(candidate.get("Tipo_ID") or candidate.get("id_type") or "").strip().upper()
    document = str(item.get("document") or candidate.get("N_mero_de_ID") or candidate.get("document") or "")
    document = "".join(character for character in document if character.isalnum()).upper()
    return id_type, document


def promote_created_people(entity_people, people_lookup):
    """Carry confirmed local Contact CREATE evidence into entity snapshots.

    ``people_lookup`` is the operational record written by the CREATE view;
    ``zoho_entities.people`` may still contain a stale NOT_FOUND from a READ
    performed before Zoho indexed the Contact.  Promotion is restricted to an
    exact identity match and, when present, the same functional role.
    """
    promoted = [dict(item) for item in (entity_people or ()) if isinstance(item, Mapping)]
    confirmed = {}
    for item in (people_lookup or ()):
        if not isinstance(item, Mapping) or not item.get("created") or not item.get("contact_id"):
            continue
        key = _person_identity_key(item)
        if key[0] and key[1]:
            confirmed[key] = item
    changed = False
    for item in promoted:
        source = confirmed.get(_person_identity_key(item))
        if not source:
            continue
        source_role = str(source.get("role") or "").strip()
        item_role = str(item.get("role") or "").strip()
        if source_role and item_role and source_role != item_role:
            continue
        contact_id = str(source["contact_id"])
        updates = {
            "status": "created", "created": True,
            "contact_id": contact_id, "remote_id": contact_id,
            "has_complete_data": True, "missing_fields": [],
        }
        if source.get("created_at"):
            updates["created_at"] = source["created_at"]
        if any(item.get(key) != value for key, value in updates.items()):
            item.update(updates)
            changed = True
    return promoted, changed


def synchronize_risk_insured(entities: Mapping[str, object]) -> tuple[dict[str, object], bool]:
    """Make each nested risk insured snapshot reflect canonical people state."""
    result = dict(entities or {})
    people = [dict(item) for item in (result.get("people") or ()) if isinstance(item, Mapping)]
    by_identity = {_person_identity_key(item): item for item in people}
    by_document = {}
    for identity, item in by_identity.items():
        if identity[1]:
            by_document.setdefault(identity[1], []).append(item)
    risks = [dict(item) for item in (result.get("risks") or ()) if isinstance(item, Mapping)]
    changed = False
    for risk in risks:
        if risk.get("insured_same_as_affiliate"):
            continue
        nested = risk.get("insured") if isinstance(risk.get("insured"), Mapping) else {}
        document = str(risk.get("insured_document") or nested.get("document") or "")
        identity = (_person_identity_key(nested)[0], "".join(c for c in document if c.isalnum()).upper())
        person = by_identity.get(identity)
        if person is None and not identity[0] and identity[1]:
            matches = by_document.get(identity[1], ())
            if len(matches) == 1:
                person = matches[0]
        if person is not None:
            person_role = str(person.get("role") or "").strip()
            nested_role = str(nested.get("role") or "").strip()
            if person_role and nested_role and person_role != nested_role:
                person = None
        if person is not None and risk.get("insured") != person:
            risk["insured"] = dict(person)
            changed = True
    if changed:
        result["risks"] = risks
    return result, changed


def effective_candidate(original: Mapping[str, object], corrections: Mapping[str, object] | None = None, *, allowed: frozenset[str] = PERSON_PROPOSAL_FIELDS) -> dict[str, object]:
    """Single source of truth for what the UI proposes and a publisher receives."""
    result = dict(original or {})
    if isinstance(corrections, Mapping):
        for raw_key, value in corrections.items():
            key = _CORRECTION_ALIASES.get(raw_key, raw_key)
            if key not in allowed or value is None:
                continue
            # Correction forms often submit untouched optional controls as an
            # empty string.  An empty value is not an explicit correction and
            # must never erase a valid value from the client response.
            if isinstance(value, str) and not value.strip():
                continue
            result[key] = value
    return result


def effective_person_candidate(original: Mapping[str, object], corrections: Mapping[str, object] | None = None) -> dict[str, object]:
    """Canonical effective Contact candidate used by render, READ and CREATE."""
    return effective_candidate(original, corrections, allowed=PERSON_PROPOSAL_FIELDS)


def _rows(payload, key):
    groups = payload.get("groups") if isinstance(payload.get("groups"), dict) else {}
    rows = groups.get(key, ())
    return [dict(row) for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _truthy(value):
    return value in {True, 1, "1", "Sí", "Si", "sí", "si", "true", "True"}


def _requester(fields, context):
    document = str(fields.get("requester_document") or context.get("requester_document") or "").strip()
    data = {
        "First_Name": fields.get("first_name") or context.get("first_name") or "",
        "Last_Name": fields.get("last_name") or context.get("last_name") or "",
        "Tipo_ID": fields.get("requester_id_type") or context.get("requester_id_type") or "",
        "N_mero_de_ID": document,
        "Date_of_Birth": fields.get("requester_birth_date") or context.get("requester_birth_date") or "",
        "Email": fields.get("requester_email") or context.get("requester_email") or "",
        "Phone": fields.get("requester_phone") or context.get("requester_phone") or "",
        "Mobile": fields.get("requester_phone") or context.get("requester_phone") or "",
        "role": "Persona principal",
    }
    data["label"] = " ".join(filter(None, (data["First_Name"], data["Last_Name"]))) or "Solicitante"
    return document, data


def _candidate(data, role):
    candidate = PersonCandidate(
        first_name=str(data.get("First_Name") or "").strip(),
        last_name=str(data.get("Last_Name") or "").strip(),
        document_type=str(data.get("Tipo_ID") or "").strip(),
        document=str(data.get("N_mero_de_ID") or "").strip(),
        date_of_birth=data.get("Date_of_Birth") or "",
        email=str(data.get("Email") or "").strip(),
        phone=str(data.get("Phone") or data.get("Mobile") or "").strip(),
        mobile=str(data.get("Mobile") or data.get("Phone") or "").strip(),
        role=role, source="individual_quotation",
    )
    return candidate


def _resolve_person(data, zoho):
    candidate = _candidate(data, data.get("role") or "Persona")
    result = resolve_contact_by_document(document=candidate.document, document_type=candidate.document_type, zoho=zoho)
    return {
        "status": str(result.get("status", "ERROR")).lower(),
        "document": candidate.document,
        "display_name": data.get("label") or candidate.first_name or "Persona",
        "role": candidate.role,
        "candidate": candidate.as_contact_data(),
        "missing_fields": list(contact_missing_fields(candidate)),
        "has_complete_data": not contact_missing_fields(candidate),
        **({"remote_id": result["record_id"]} if result.get("record_id") else {}),
    }


def _risk_row(row):
    plate = str(row.get("plate") or row.get("license_plate") or row.get("Placa_del_vehiculo") or "").strip()
    try:
        plate = normalize_plate(plate)
    except Exception:
        plate = ""
    brand = row.get("brand") or row.get("make") or ""
    line = row.get("line") or row.get("reference") or ""
    return {
        # Movilidad contract: the Zoho risk key is always the normalized plate.
        "Name": plate,
        "Tipo_de_riesgo": "Vehículos", "Placa_del_vehiculo": plate,
        "Marca_Tipo_Caracter_sticas": " ".join(filter(None, (str(brand).strip(), str(line).strip()))),
        "Modelo": row.get("model") or row.get("Modelo") or "",
        "Clase": row.get("vehicle_class") or row.get("class") or row.get("Clase") or "",
        "Ciudad": row.get("city") or row.get("Ciudad") or "",
        "Tipo_de_uso": row.get("use") or row.get("Tipo_de_uso") or "",
    }


def resolve_mobility_entities(*, quotation, zoho=None):
    """Resolve Policy, Contacts, Risks and Riesgos1 without performing writes."""
    payload = json.loads(decrypt(quotation.encrypted_payload))
    branch = str(payload.get("schema") or quotation.branch_slug or "").casefold()
    if branch != "movilidad":
        return {"status": "unsupported", "branch": branch, "people": [], "risks": [], "subrisks": []}
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    policy_id = ""
    policy_error = ""
    try:
        policy_context = unsign_record_context(
            context.get("policy_token") or context.get("entity_token"),
            expected_type="policy",
        )
        policy_id = str(policy_context.get("id") or "").strip()
        if not policy_id:
            policy_error = "No fue posible validar la póliza de origen."
    except Exception:
        policy_error = "No fue posible validar la póliza de origen."
    try:
        facade = zoho or colectivos_zoho()
    except Exception:
        facade = None
    if facade is None and not policy_error:
        policy_error = "No fue posible consultar Zoho en este momento."
    policy_label = str(context.get("policy_label") or context.get("policy_number") or "").strip()
    active_profile = str(getattr(settings, "ZOHO_ACTIVE_PROFILE", "sandbox")).strip().lower()
    policy_status = "found"
    policy_remote_id = policy_id
    if active_profile == "sandbox":
        # Zoho IDs are environment-specific.  Resolve Sandbox policies by the
        # signed logical policy number, never by a Production record ID.
        if not policy_label:
            policy_status = "error"
            policy_error = "No fue posible validar la póliza de origen."
        elif facade is None:
            policy_status = "error"
            policy_error = "No fue posible consultar Zoho en este momento."
        else:
            try:
                resolved_policy = resolve_policy_by_number(policy_number=policy_label, zoho=facade)
                resolved_status = str(resolved_policy.get("status") or "").upper()
                if resolved_status == "FOUND":
                    policy_remote_id = str(resolved_policy.get("record_id") or "").strip()
                    policy_status = "found" if policy_remote_id else "error"
                    if not policy_remote_id:
                        policy_error = "No fue posible consultar la póliza de origen."
                elif resolved_status == "NOT_FOUND":
                    policy_status = "not_found"
                    policy_error = "La póliza de origen no está disponible en Sandbox."
                elif resolved_status == "AMBIGUOUS":
                    policy_status = "ambiguous"
                    policy_error = "La póliza de origen tiene varias coincidencias en Sandbox."
                else:
                    policy_status = "error"
                    policy_error = "No fue posible consultar la póliza de origen."
            except Exception:
                policy_status = "error"
                policy_error = "No fue posible consultar la póliza de origen."
    elif policy_id and facade is not None:
        try:
            policy_record = facade.records.get_by_id(module="Polizas", record_id=policy_id, fields=("id", "Name", "Ramo"))
            if policy_record:
                policy_label = policy_record.get("Name") if isinstance(policy_record, Mapping) else policy_label
            else:
                policy_status = "not_found"
                policy_error = "La póliza de origen no está disponible."
        except Exception:
            policy_status = "error"
            policy_error = "No fue posible consultar la póliza de origen."
    elif not policy_id:
        policy_status = "error"
    policy = ({"status": policy_status, "display_name": policy_label, "error": policy_error}
              if policy_status != "found" else
              {"status": "found", "remote_id": policy_remote_id, "display_name": policy_label})
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    requester_document, requester = _requester(fields, context)
    people_data = {requester_document: requester} if requester_document else {}
    vehicles = _rows(payload, "vehicles")
    existing_entities = (quotation.safe_metadata or {}).get("zoho_entities")
    existing_entities = existing_entities if isinstance(existing_entities, Mapping) else {}
    corrections = (quotation.safe_metadata or {}).get("zoho_entity_corrections") or {}
    for row in vehicles:
        if not _truthy(row.get("insured_same_as_requester")):
            document = str(row.get("insured_document") or "").strip()
            if document and document != requester_document:
                people_data.setdefault(document, {
                    "First_Name": row.get("insured_first_name") or "", "Last_Name": row.get("insured_last_name") or "",
                    "Tipo_ID": row.get("insured_id_type") or "", "N_mero_de_ID": document,
                    "Date_of_Birth": row.get("insured_birth_date") or "", "Email": row.get("insured_email") or "",
                    "Phone": row.get("insured_phone") or "", "Mobile": row.get("insured_phone") or "",
                    "label": "Asegurado del vehículo", "role": "Asegurado del vehículo",
                })
    person_corrections = (quotation.safe_metadata or {}).get("person_corrections") or {}
    if isinstance(person_corrections, dict):
        for document, correction in person_corrections.items():
            if str(document) in people_data and isinstance(correction, Mapping):
                people_data[str(document)] = effective_person_candidate(people_data[str(document)], correction)
    people = []
    for data in people_data.values():
        try:
            people.append(_resolve_person(data, facade))
        except Exception:
            candidate = _candidate(data, data.get("role") or "Persona")
            people.append({
                "status": "error", "document": candidate.document,
                "display_name": data.get("label") or candidate.first_name or "Persona",
                "role": candidate.role, "candidate": candidate.as_contact_data(),
                "missing_fields": list(contact_missing_fields(candidate)),
                "has_complete_data": not contact_missing_fields(candidate),
            })
    for person in people:
        if isinstance(person_corrections, dict) and person.get("document") in {str(key) for key in person_corrections}:
            person["edited_by_analyst"] = True
    # A confirmed CREATE is operational evidence.  Preserve it across the
    # next READ/reconcile even when Zoho's search index has not caught up yet.
    existing_people = {
        _person_identity_key(item): item
        for item in (existing_entities.get("people") or ())
        if isinstance(item, Mapping)
    }
    for item in (quotation.safe_metadata or {}).get("people_lookup") or ():
        if not isinstance(item, Mapping) or not item.get("created") or not item.get("contact_id"):
            continue
        identity = _person_identity_key(item)
        if identity[0] and identity[1] and identity not in existing_people:
            existing_people[identity] = item
    existing_people_by_document = {}
    for identity, item in existing_people.items():
        if identity[1]:
            existing_people_by_document.setdefault(identity[1], []).append(item)
    for person in people:
        previous = existing_people.get(_person_identity_key(person))
        if previous is None:
            document_matches = existing_people_by_document.get(_person_identity_key(person)[1], ())
            # Legacy snapshots may lack Tipo_ID on either side.  Fall back to
            # the document only when the type is absent on one side (or equal
            # on both), the match is unambiguous, and the role agrees.
            current_type = _person_identity_key(person)[0]
            candidates = [
                item for item in document_matches
                if not current_type
                or not _person_identity_key(item)[0]
                or _person_identity_key(item)[0] == current_type
            ]
            if len(candidates) == 1:
                candidate = candidates[0]
                source_role = str(candidate.get("role") or "").strip()
                person_role = str(person.get("role") or "").strip()
                if not source_role or not person_role or source_role == person_role:
                    previous = candidate
        if previous and previous.get("created") and previous.get("remote_id"):
            person.update({
                "status": "created", "created": True,
                "remote_id": previous["remote_id"],
                "contact_id": previous.get("contact_id") or previous["remote_id"],
                "created_at": previous.get("created_at"),
                "has_complete_data": True, "missing_fields": [],
            })
        elif previous and previous.get("created") and previous.get("contact_id"):
            contact_id = str(previous["contact_id"])
            person.update({
                "status": "created", "created": True,
                "remote_id": contact_id, "contact_id": contact_id,
                "has_complete_data": True, "missing_fields": [],
            })
    by_doc = {item["document"]: item for item in people}
    risks, subrisks = [], []
    for index, row in enumerate(vehicles):
        proposed = _risk_row(row)
        proposed = effective_candidate(proposed, corrections.get(f"risk:{index}", {}) if isinstance(corrections, dict) else {}, allowed=RISK_PROPOSAL_FIELDS)
        # Corrections may contain legacy ``Name`` data, but it must never
        # diverge from the canonical Mobility plate.
        if proposed.get("Placa_del_vehiculo"):
            try:
                proposed["Placa_del_vehiculo"] = normalize_plate(proposed["Placa_del_vehiculo"])
                proposed["Name"] = proposed["Placa_del_vehiculo"]
                proposed["Tipo_de_riesgo"] = "Vehículos"
            except Exception:
                proposed["Placa_del_vehiculo"] = ""
                proposed["Name"] = ""
        if not proposed["Placa_del_vehiculo"]:
            risk = {"status": "blocked", "reason": "Complete la placa para buscar o crear este vehículo en Zoho.", "index": index, "candidate": proposed}
        else:
            try:
                if facade is None:
                    raise RuntimeError("Zoho facade unavailable")
                page = facade.search.by_criteria(module="Riesgos", criteria=f"(Placa_del_vehiculo:equals:{proposed['Placa_del_vehiculo']})", fields=("id", "Name", "Placa_del_vehiculo", "Marca_Tipo_Caracter_sticas", "Modelo"), page=1, limit=20)
                records = tuple(getattr(page, "records", ()) or ())
                exact = [record for record in records if str(record.get("Placa_del_vehiculo") or "").replace("-", "").replace(" ", "").upper() == proposed["Placa_del_vehiculo"]]
                if len(exact) == 1:
                    risk = {"status": "found", "remote_id": str(exact[0].get("id") or ""), "index": index, "candidate": proposed, "record": dict(exact[0])}
                elif len(exact) > 1:
                    risk = {"status": "ambiguous", "index": index, "candidate": proposed}
                else:
                    risk = {"status": "not_found", "index": index, "candidate": proposed, "missing_fields": [field for field in ("Name", "Placa_del_vehiculo", "Modelo") if not str(proposed.get(field) or "").strip()]}
            except Exception:
                risk = {"status": "blocked", "reason": "No fue posible buscar este vehículo en Zoho.", "index": index, "candidate": proposed}
        previous_risks = existing_entities.get("risks") or ()
        previous_risk = previous_risks[index] if index < len(previous_risks) and isinstance(previous_risks[index], Mapping) else None
        if previous_risk and previous_risk.get("created") and (previous_risk.get("remote_id") or previous_risk.get("risk_id")):
            risk_id = str(previous_risk.get("remote_id") or previous_risk.get("risk_id"))
            risk.update({"status": "created", "created": True, "remote_id": risk_id, "risk_id": risk_id})
        risks.append(risk)
        if isinstance(corrections, dict) and corrections.get(f"risk:{index}"):
            risk["edited_by_analyst"] = True
        insured_doc = requester_document if _truthy(row.get("insured_same_as_requester")) else str(row.get("insured_document") or requester_document).strip()
        risk["insured_document"] = insured_doc
        risk["insured_same_as_affiliate"] = insured_doc == requester_document
        if not risk["insured_same_as_affiliate"]:
            risk["insured"] = by_doc.get(insured_doc) or {"document": insured_doc, "status": "not_found"}
        affiliate = by_doc.get(requester_document)
        insured = by_doc.get(insured_doc)
        # Recalculate all dependency IDs from the final promoted snapshots.
        policy_id_final = str(policy.get("remote_id") or policy.get("id") or "").strip()
        affiliate_id = str((affiliate or {}).get("remote_id") or (affiliate or {}).get("contact_id") or "").strip()
        risk_id = str(risk.get("remote_id") or risk.get("risk_id") or "").strip()
        if risk.get("insured_same_as_affiliate"):
            insured_id = affiliate_id
        else:
            insured_id = str((insured or {}).get("remote_id") or (insured or {}).get("contact_id") or "").strip()
        policy_ready = policy.get("status") in {"found", "created"} and bool(policy_id_final)
        previous_subrisks = existing_entities.get("subrisks") or ()
        previous_subrisk = previous_subrisks[index] if index < len(previous_subrisks) and isinstance(previous_subrisks[index], Mapping) else None
        previous_subrisk_id = str((previous_subrisk or {}).get("remote_id") or (previous_subrisk or {}).get("riesgos1_id") or "").strip()
        previous_candidate = (previous_subrisk or {}).get("candidate") if isinstance((previous_subrisk or {}).get("candidate"), Mapping) else {}
        def _previous_lookup_id(field):
            value = previous_candidate.get(field)
            return str(value.get("id") if isinstance(value, Mapping) else value or "").strip()
        previous_matches = (
            previous_subrisk_id
            and (not _previous_lookup_id("P_liza") or _previous_lookup_id("P_liza") == policy_id_final)
            and (not _previous_lookup_id("Riesgo") or _previous_lookup_id("Riesgo") == risk_id)
            and (not _previous_lookup_id("Contacto_facturaci_n_dividida_colectivas") or _previous_lookup_id("Contacto_facturaci_n_dividida_colectivas") == affiliate_id)
            and (not _previous_lookup_id("Asegurado") or _previous_lookup_id("Asegurado") == insured_id)
        )
        if policy_ready and affiliate_id and risk_id and insured_id:
            try:
                relation = resolve_mobility_subrisk_relation(policy_id=policy_id_final, risk_id=risk_id, affiliate_contact_id=affiliate_id, insured_contact_id=insured_id, zoho=facade)
                relation_status = relation["status"].lower()
            except Exception:
                relation = {}
                relation_status = "blocked"
            if previous_matches and relation_status in {"not_found", "blocked"}:
                relation_status = "created"
            sub = {"status": relation_status, "index": index, "candidate": {"Name": proposed.get("Name", ""), "P_liza": {"id": policy_id_final}, "Contacto_facturaci_n_dividida_colectivas": {"id": affiliate_id}, "Asegurado": {"id": insured_id}, "Riesgo": {"id": risk_id}, "Ramo": "Movilidad colectivo", "Estado": "Activo", "Parentesco": "Afiliado", "Fecha_ingreso_riesgo": quotation.submitted_at.date().isoformat() if quotation.submitted_at else date.today().isoformat()}}
            sub_corrections = corrections.get(f"subrisk:{index}", {}) if isinstance(corrections, dict) else {}
            if isinstance(sub_corrections, dict):
                sub["candidate"] = effective_candidate(sub["candidate"], sub_corrections, allowed=SUBRISK_PROPOSAL_FIELDS)
            if relation.get("record_id"):
                sub["remote_id"] = relation["record_id"]
                sub["riesgos1_id"] = relation["record_id"]
            elif previous_matches:
                sub["remote_id"] = previous_subrisk_id
                sub["riesgos1_id"] = previous_subrisk_id
                sub["created"] = True
        else:
            missing = []
            if not policy_ready:
                missing.append("la póliza de origen")
            if not risk_id:
                missing.append("el Riesgo / vehículo")
            if not affiliate_id:
                missing.append("el Afiliado")
            if not insured_id:
                missing.append("el Asegurado")
            sub = {"status": "blocked", "index": index, "reason": "Faltan " + ", ".join(missing) + "."}
        subrisks.append(sub)
        if isinstance(corrections, dict) and corrections.get(f"subrisk:{index}"):
            sub["edited_by_analyst"] = True
    metadata = dict(quotation.safe_metadata or {})
    final_entities, _ = synchronize_risk_insured({"branch": "movilidad", "policy": policy, "people": people, "risks": risks, "subrisks": subrisks})
    final_entities["resolved_at"] = timezone.now().isoformat()
    metadata["zoho_entities"] = final_entities
    quotation.safe_metadata = metadata
    quotation.save(update_fields=("safe_metadata",))
    return metadata["zoho_entities"]
