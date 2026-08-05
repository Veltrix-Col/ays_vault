from __future__ import annotations

from collections import OrderedDict

from django.core.signing import salted_hmac


ROLE_FIELDS = (
    ("Afiliado", "associate"),
    ("Asegurado", "insured"),
    ("Beneficiario", "beneficiary"),
)


def _entity(row, prefix: str, role: str) -> dict[str, object] | None:
    key = str(row.get(f"{prefix}_key") or "")
    legacy = not key and str(row.get("role") or "") == role and row.get("public_key")
    if legacy:
        key = salted_hmac(
            f"colectivos.functional.legacy.{prefix}.v1",
            str(row["public_key"]),
        ).hexdigest()
    if not key:
        return None
    display_name = row.get(f"{prefix}_name")
    id_type = row.get(f"{prefix}_id_type")
    masked_document = row.get(f"{prefix}_masked_document")
    if legacy:
        display_name = display_name or row.get("display_name")
        id_type = id_type or row.get("id_type")
        masked_document = masked_document or row.get("masked_document")
    return {
        "key": key,
        "display_name": str(display_name or "Información protegida"),
        "id_type": str(id_type or ""),
        "masked_document": str(masked_document or ""),
        "roles": {role},
        "source_record_keys": set(),
        "relationships": set(filter(None, (str(row.get("relationship") or ""),))),
        "state": str(row.get("initial_status") or "Sin estado"),
        "plan": str(row.get("plan") or ""),
        "entry_date": row.get("entry_date"),
        "exit_date": row.get("exit_date"),
        "risk_keys": set(filter(None, (str(row.get("risk_key") or ""),))),
        "economic_values": dict(row.get("economic_values") or {}),
        "novelties": 0,
    }


def _merge(target: dict[str, object], candidate: dict[str, object], warnings: list[str]) -> None:
    if (
        target["display_name"] not in {"", "Información protegida"}
        and candidate["display_name"] not in {"", "Información protegida"}
        and target["display_name"] != candidate["display_name"]
    ):
        warnings.append("Una misma referencia técnica tiene datos descriptivos contradictorios.")
    target["roles"].update(candidate["roles"])
    target["relationships"].update(candidate["relationships"])
    target["risk_keys"].update(candidate["risk_keys"])
    for key, value in candidate.get("economic_values", {}).items():
        target["economic_values"].setdefault(key, value)
    for field in ("display_name", "id_type", "masked_document", "state", "plan", "entry_date", "exit_date"):
        if not target.get(field) and candidate.get(field):
            target[field] = candidate[field]


def consolidate_functional_groups(rows, *, branch_code: str = "") -> tuple[tuple[dict[str, object], ...], tuple[str, ...]]:
    """Consolida por referencias HMAC; nunca usa nombres como clave de unión."""
    entities: OrderedDict[str, dict[str, object]] = OrderedDict()
    principal_links: OrderedDict[str, set[str]] = OrderedDict()
    warnings: list[str] = []
    risk_entities: OrderedDict[str, dict[str, object]] = OrderedDict()

    for row in rows:
        row_entities = {}
        for role, prefix in ROLE_FIELDS:
            candidate = _entity(row, prefix, role)
            if candidate is None:
                continue
            row_entities[prefix] = candidate
            existing = entities.get(candidate["key"])
            if existing is None:
                entities[candidate["key"]] = candidate
                existing = candidate
            else:
                _merge(existing, candidate, warnings)
            if str(row.get("role") or "") == role:
                existing["source_record_keys"].add(str(row["public_key"]))

        risk_key = str(row.get("risk_key") or "")
        if risk_key:
            risk = risk_entities.setdefault(risk_key, {
                "key": risk_key, "summary": str(row.get("risk_summary") or "Riesgo relacionado"),
                "attributes": dict(row.get("risk_attributes") or {}),
                "source_record_keys": set(),
            })
            risk["source_record_keys"].add(str(row["public_key"]))

        principal = row_entities.get("associate") or row_entities.get("insured")
        if principal is None and row_entities:
            principal = next(iter(row_entities.values()))
        if principal is not None:
            links = principal_links.setdefault(principal["key"], set())
            links.update(value["key"] for value in row_entities.values() if value["key"] != principal["key"])
        elif risk_key:
            principal_links.setdefault(risk_key, set())

        if row_entities.get("beneficiary") and not row_entities.get("associate"):
            warnings.append("Se encontró un beneficiario sin afiliado principal confirmado.")

    groups = []
    used = set()
    risk_first = branch_code in {"28", "40"}
    if risk_first and risk_entities:
        for risk in risk_entities.values():
            groups.append({
                "principal": {
                    "key": risk["key"], "display_name": risk["summary"],
                    "roles": ("Riesgo",), "source_record_keys": tuple(sorted(risk["source_record_keys"])),
                    "state": "", "plan": "", "masked_document": "", "id_type": "",
                    "relationships": (), "risk_keys": (),
                },
                "members": (), "risks": (risk,), "related_count": 1,
                "action_label": "Ver inmueble" if branch_code == "28" else "Ver vehículos",
            })
    else:
        adjacency = {key: set() for key in entities}
        for principal_key, dependent_keys in principal_links.items():
            adjacency.setdefault(principal_key, set()).update(dependent_keys)
            for dependent_key in dependent_keys:
                adjacency.setdefault(dependent_key, set()).add(principal_key)
        for principal_key in entities:
            if principal_key in used:
                continue
            component = []
            pending = [principal_key]
            while pending:
                current = pending.pop()
                if current in used or current not in entities:
                    continue
                used.add(current)
                component.append(current)
                pending.extend(sorted(adjacency.get(current, ()), reverse=True))
            principal = entities[component[0]]
            members = tuple(entities[key] for key in component[1:])
            risk_keys = set(principal["risk_keys"])
            for member in members:
                risk_keys.update(member["risk_keys"])
            risks = tuple(
                risk_entities[key] for key in sorted(risk_keys) if key in risk_entities
            )
            groups.append({
                "principal": principal, "members": members, "risks": risks,
                "related_count": len(members) + len(risks),
                "action_label": "Ver grupo" if branch_code in {"91", "86"} else "Ver asegurados",
            })

    for group in groups:
        for entity in (group["principal"], *group["members"]):
            entity["roles"] = tuple(sorted(entity["roles"]))
            entity["relationships"] = tuple(sorted(entity["relationships"]))
            entity["source_record_keys"] = tuple(sorted(entity["source_record_keys"]))
            entity["risk_keys"] = tuple(sorted(entity.get("risk_keys", ())))
        for risk in group["risks"]:
            risk["source_record_keys"] = tuple(sorted(risk["source_record_keys"]))
    return tuple(groups), tuple(dict.fromkeys(warnings))
