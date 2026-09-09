"""Auditoría sanitizada y estrictamente READ-only del contrato Production Tasks."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, Iterable, Mapping

from integrations.zoho.discovery.normalization import safe_value

from .services.task_publisher import (
    ALLOWED_TASK_FIELDS,
    BASE_TASK_FIELDS,
    CONFIRMED_TASK_FIELDS,
    NOVELTIES_ANALYST_REQUEST,
    NOVELTIES_TASK_AREA,
    SYNTHETIC_TEST_TASK,
    TASK_KIND,
)


NO_DEMOSTRADO = "NO DEMOSTRADO"
MODULE = "Tasks"
MAX_SAMPLE = 10
PRIORITY_FIELDS = (
    "id", "Subject", "Status", "Priority", "Due_Date", "Owner", "What_Id",
    "Who_Id", "Description", "Observaciones", "tipo_de_solicitud", "rea",
    "Responsable", "Correo_responsable", "Fecha_de_solicitud_del_cliente",
    "Solicitud_a_analista", "Vendedor", "Modified_Time", "Layout",
)
RELEVANT_TERMS = (
    "poliza", "operacion", "cliente", "aseguradora", "ramo", "analista",
    "vendedor", "responsable", "seguimiento", "cierre", "resolucion", "area",
    "solicitud",
)
EXPECTED_TYPES = {
    "Subject": {"text"},
    "tipo_de_solicitud": {"picklist", "text"},
    "rea": {"picklist", "text"},
    "Observaciones": {"textarea", "text"},
    "Responsable": {"picklist", "text", "lookup", "userlookup", "ownerlookup"},
    "Correo_responsable": {"email", "text"},
    "Fecha_de_solicitud_del_cliente": {"date", "datetime"},
    "Solicitud_a_analista": {"picklist", "boolean", "text"},
    "Vendedor": {"picklist", "text"},
}


def _text(value: object) -> str:
    return str(value or "").strip()


def _normalized(value: object) -> str:
    import unicodedata

    return "".join(
        character for character in unicodedata.normalize("NFKD", _text(value).casefold())
        if not unicodedata.combining(character)
    ).replace("_", " ")


def _lookup_target(value: object) -> str:
    if not isinstance(value, Mapping):
        return NO_DEMOSTRADO
    module = value.get("module")
    if isinstance(module, Mapping):
        target = module.get("api_name") or module.get("module_name") or module.get("name")
    else:
        target = value.get("api_name") or value.get("module_api_name") or module
    return _text(target) or NO_DEMOSTRADO


def _picklist_values(values: Iterable[Mapping[str, object]]) -> list[dict[str, object]]:
    normalized = []
    for position, item in enumerate(values):
        normalized.append({
            "display_value": _text(item.get("display_value")),
            "actual_value": _text(item.get("actual_value") or item.get("value")),
            "active": bool(item["active"]) if "active" in item else NO_DEMOSTRADO,
            "sequence": item.get("sequence_number", position),
            "default": item.get("default", NO_DEMOSTRADO),
        })
    return normalized


def normalize_task_fields(fields: Iterable[object]) -> list[dict[str, object]]:
    result = []
    for item in fields:
        raw = asdict(item) if hasattr(item, "__dataclass_fields__") else dict(item)
        data_type = _text(raw.get("data_type")).casefold()
        lookup = safe_value(raw.get("lookup") or raw.get("related_details") or {})
        picklists = raw.get("pick_list_values") or ()
        required = bool(raw.get("required"))
        system_mandatory = bool(raw.get("system_mandatory"))
        read_only = bool(raw.get("read_only"))
        result.append({
            "api_name": _text(raw.get("api_name")),
            "label": _text(raw.get("field_label") or raw.get("display_label")),
            "type": data_type,
            "type_category": _type_category(data_type),
            "required": required,
            "system_mandatory": system_mandatory,
            "read_only": read_only,
            "writable_by_metadata": not read_only,
            "editable": NO_DEMOSTRADO,
            "visible": NO_DEMOSTRADO,
            "custom": bool(raw.get("custom_field")),
            "unique": bool(raw.get("unique")),
            "length": raw.get("length"),
            "default": NO_DEMOSTRADO,
            "allows_empty_by_mandatory_flags": not (required or system_mandatory),
            "picklist_values": _picklist_values(picklists),
            "lookup": lookup,
            "lookup_target": _lookup_target(lookup),
        })
    return sorted(result, key=lambda field: field["api_name"].casefold())


def _type_category(data_type: str) -> str:
    compact = data_type.replace("_", "")
    if compact in {"text", "textarea", "email", "date", "datetime", "boolean"}:
        return compact
    if compact in {"picklist", "multiselectpicklist"}:
        return compact
    if compact in {"lookup", "ownerlookup", "userlookup"}:
        return "lookup"
    if compact in {"integer", "bigint", "double", "decimal", "currency", "percent"}:
        return "numeric"
    return data_type or "unknown"


def _sample_fields(fields: list[dict[str, object]]) -> tuple[str, ...]:
    available = {str(field["api_name"]): field for field in fields}
    selected = [name for name in PRIORITY_FIELDS if name in available]
    for name, field in sorted(available.items(), key=lambda pair: pair[0].casefold()):
        searchable = _normalized(f"{name} {field['label']}")
        if name not in selected and any(term in searchable for term in RELEVANT_TERMS):
            selected.append(name)
    return tuple(selected[:50])


def _summarize_records(
    records: Iterable[Mapping[str, object]], fields: Iterable[dict[str, object]],
) -> dict[str, object]:
    records = tuple(records)
    by_name = {str(field["api_name"]): field for field in fields}
    result: dict[str, object] = {}
    for name in _sample_fields(list(fields)):
        field = by_name[name]
        values = [record.get(name) for record in records]
        populated = [value for value in values if value not in (None, "", [], {})]
        summary: dict[str, object] = {
            "populated": len(populated), "empty": len(records) - len(populated),
        }
        category = field["type_category"]
        if category in {"picklist", "multiselectpicklist", "boolean"} and name not in {
            "Responsable", "Vendedor",
        }:
            observed = Counter(
                str(item) for value in populated
                for item in (value if isinstance(value, list) else [value])
            )
            summary["observed_values"] = dict(sorted(observed.items()))
        elif category == "lookup":
            modules = Counter(
                _text(value.get("$se_module") or value.get("module")) or NO_DEMOSTRADO
                for value in populated if isinstance(value, Mapping)
            )
            summary["observed_target_modules"] = dict(sorted(modules.items()))
        elif name == "Layout":
            summary["observed_layouts"] = sorted({
                _text(value.get("name")) for value in populated
                if isinstance(value, Mapping) and _text(value.get("name"))
            })
        result[name] = summary
    return result


def _publisher_comparison(fields: list[dict[str, object]]) -> list[dict[str, object]]:
    by_name = {str(field["api_name"]): field for field in fields}
    result = []
    for name in sorted(ALLOWED_TASK_FIELDS | BASE_TASK_FIELDS):
        field = by_name.get(name)
        compatible = bool(
            field
            and not field["read_only"]
            and str(field["type"]).casefold() in EXPECTED_TYPES.get(name, {str(field["type"]).casefold()})
        )
        result.append({
            "field": name,
            "base": name in BASE_TASK_FIELDS,
            "confirmed": name in CONFIRMED_TASK_FIELDS,
            "allowed": name in ALLOWED_TASK_FIELDS,
            "production_exists": bool(field),
            "production_type": field["type"] if field else NO_DEMOSTRADO,
            "required": field["required"] if field else NO_DEMOSTRADO,
            "system_mandatory": field["system_mandatory"] if field else NO_DEMOSTRADO,
            "read_only": field["read_only"] if field else NO_DEMOSTRADO,
            "compatible": compatible,
        })
    return result


def _configured_value_compatibility(
    fields: list[dict[str, object]],
) -> list[dict[str, object]]:
    configured = {
        "tipo_de_solicitud": sorted(set(TASK_KIND.values())),
        "rea": [NOVELTIES_TASK_AREA],
        "Solicitud_a_analista": [NOVELTIES_ANALYST_REQUEST],
        "Responsable": [_text(SYNTHETIC_TEST_TASK.get("Responsable"))],
    }
    by_name = {str(field["api_name"]): field for field in fields}
    result = []
    for name, values in configured.items():
        production = {
            item["actual_value"]
            for item in by_name.get(name, {}).get("picklist_values", [])
        }
        result.append({
            "field": name,
            "configured_values": values,
            "confirmed": sorted(set(values) & production),
            "missing": sorted(set(values) - production),
        })
    return result


def audit_tasks_contract(zoho: Any, *, audited_at: datetime | None = None) -> dict[str, object]:
    if _text(getattr(zoho, "profile", "")).casefold() != "production":
        raise ValueError("La auditoría admite exclusivamente Production.")
    if bool(getattr(getattr(zoho, "config", None), "write_enabled", False)):
        raise ValueError("Production WRITE debe permanecer deshabilitado.")
    reads = Counter()
    reads["organization.get"] += 1
    organization = zoho.organization.get()
    if _text(getattr(organization, "environment", "")).casefold() != "production":
        raise ValueError("Zoho no confirmó el entorno Production.")
    reads["metadata.list_modules"] += 1
    modules = zoho.metadata.list_modules()
    module = next((item for item in modules if _text(getattr(item, "api_name", "")) == MODULE), None)
    if module is None:
        raise ValueError("Tasks no existe en metadata Production.")
    reads["metadata.list_fields.Tasks"] += 1
    fields = normalize_task_fields(zoho.metadata.list_fields(MODULE))
    if not fields:
        raise ValueError("Tasks no devolvió metadata de campos.")

    selected = _sample_fields(fields)
    order = "API default (recency not demonstrated)"
    reads["records.list.Tasks.sample"] += 1
    page = zoho.records.list(
        module=MODULE, fields=selected, page=1, limit=MAX_SAMPLE
    )
    records = tuple(page.records[:MAX_SAMPLE])

    picklists = [field for field in fields if field["picklist_values"]]
    lookups = [field for field in fields if field["type_category"] == "lookup" or field["lookup"]]
    mandatory = [field["api_name"] for field in fields if field["required"]]
    system_mandatory = [field["api_name"] for field in fields if field["system_mandatory"]]
    read_only = [field["api_name"] for field in fields if field["read_only"]]
    by_name = {str(field["api_name"]): field for field in fields}
    task_kind_values = {
        item["actual_value"] for item in by_name.get("tipo_de_solicitud", {}).get("picklist_values", [])
    }
    mapped_values = set(TASK_KIND.values())
    now = (audited_at or datetime.now(UTC)).astimezone(UTC)
    sample_summary = _summarize_records(records, fields)
    status_field = by_name.get("Status", {})
    status_values = {
        item["actual_value"]: item["display_value"]
        for item in status_field.get("picklist_values", [])
    }
    return {
        "audit": {
            "audited_at": now.isoformat(), "profile": "production", "module": MODULE,
            "mode": "READ-only", "backend": _text(getattr(zoho, "backend_name", "")),
            "production_reads": sum(reads.values()), "production_read_operations": dict(sorted(reads.items())),
            "production_writes": 0, "create": 0, "update": 0, "delete": 0, "attachments": 0,
        },
        "module_metadata": safe_value(asdict(module) if hasattr(module, "__dataclass_fields__") else module),
        "field_count": len(fields), "fields": fields,
        "picklists": picklists, "lookups": lookups,
        "mandatory_fields": mandatory, "system_mandatory_fields": system_mandatory,
        "read_only_fields": read_only,
        "layouts": {"status": NO_DEMOSTRADO, "reason": "La fachada instalada no expone list_layouts().", "items": []},
        "task_sample": {
            "requested": MAX_SAMPLE, "returned": len(records), "ordering": order,
            "raw_records_persisted": 0, "summary": sample_summary,
        },
        "status_contract": {
            "open": {
                "actual_value": "No iniciado" if "No iniciado" in status_values else NO_DEMOSTRADO,
                "display_value": status_values.get("No iniciado", NO_DEMOSTRADO),
                "observed_in_sample": "No iniciado" in sample_summary.get("Status", {}).get("observed_values", {}),
            },
            "resolved": {
                "actual_value": "Completed" if "Completed" in status_values else NO_DEMOSTRADO,
                "display_value": status_values.get("Completed", NO_DEMOSTRADO),
                "observed_in_sample": status_values.get("Completed") in sample_summary.get("Status", {}).get("observed_values", {}),
            },
            "note": (
                "Son los estados estándar abierto/completado demostrados por metadata y muestra. "
                "El significado funcional de otros estados y transiciones es NO DEMOSTRADO."
            ),
        },
        "relationship_capabilities": {
            "contact": "Who_Id -> Contacts (demostrado por metadata)",
            "owner_user": "Owner es ownerlookup; módulo destino exacto NO DEMOSTRADO",
            "policy": "What_Id es polimórfico (se_module); Polizas como destino permitido NO DEMOSTRADO",
            "operation": "What_Id es polimórfico (se_module); Opeeraciones como destino permitido NO DEMOSTRADO",
        },
        "publisher": {
            "base_fields": sorted(BASE_TASK_FIELDS), "confirmed_fields": sorted(CONFIRMED_TASK_FIELDS),
            "allowed_fields": sorted(ALLOWED_TASK_FIELDS), "task_kind": dict(sorted(TASK_KIND.items())),
            "synthetic_test_fields": sorted(SYNTHETIC_TEST_TASK), "comparison": _publisher_comparison(fields),
            "task_kind_picklist": {
                "confirmed": sorted(mapped_values & task_kind_values),
                "missing_in_production": sorted(mapped_values - task_kind_values),
                "production_only": sorted(task_kind_values - mapped_values),
            },
            "configured_area_value": NOVELTIES_TASK_AREA,
            "configured_analyst_request_value": NOVELTIES_ANALYST_REQUEST,
            "configured_value_compatibility": _configured_value_compatibility(fields),
        },
    }


def render_tasks_contract_markdown(contract: Mapping[str, object]) -> str:
    audit = contract["audit"]
    lines = [
        "# Contrato Zoho CRM Tasks — Production", "",
        "## Resumen ejecutivo", "",
        f"Auditoría `{audit['mode']}` de `{audit['module']}` en `{audit['profile']}`. ",
        f"Se encontraron **{contract['field_count']} campos**. No se persistieron registros ni PII.", "",
        "## Contrato real de campos", "",
        "| API name | Label | Tipo | Required | System mandatory | Read-only | Writable metadata | Custom | Longitud |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for field in contract["fields"]:
        lines.append(
            f"| `{field['api_name']}` | {field['label']} | `{field['type']}` | {field['required']} | "
            f"{field['system_mandatory']} | {field['read_only']} | {field['writable_by_metadata']} | "
            f"{field['custom']} | {field['length'] if field['length'] is not None else NO_DEMOSTRADO} |"
        )
    lines += ["", "## Picklists", ""]
    for field in contract["picklists"]:
        lines += [f"### `{field['api_name']}` — {field['label']}", ""]
        for value in field["picklist_values"]:
            lines.append(
                f"- `{value['actual_value']}` — {value['display_value']} "
                f"(active: {value['active']}; order: {value['sequence']}; default: {value['default']})"
            )
        lines.append(f"- Admite vacío según flags mandatory: {field['allows_empty_by_mandatory_flags']}")
        lines.append("")
    lines += ["## Lookups", "", "| Campo | Label | Tipo | Destino | Required | Writable |", "|---|---|---|---|---:|---:|"]
    for field in contract["lookups"]:
        lines.append(
            f"| `{field['api_name']}` | {field['label']} | `{field['type']}` | "
            f"{field['lookup_target']} | {field['required']} | {field['writable_by_metadata']} |"
        )
    lines += [
        "", "## Obligatoriedad", "",
        f"- API mandatory: {', '.join(f'`{x}`' for x in contract['mandatory_fields']) or 'ninguno informado'}",
        f"- System mandatory: {', '.join(f'`{x}`' for x in contract['system_mandatory_fields']) or 'ninguno informado'}",
        "- Layout required y reglas condicionales: **NO DEMOSTRADO** mediante la fachada instalada.", "",
        "## Layouts", "", f"**{contract['layouts']['status']}**: {contract['layouts']['reason']}", "",
        "## Muestra sanitizada de Tasks", "",
        f"Se leyeron {contract['task_sample']['returned']} Tasks en una consulta, orden: {contract['task_sample']['ordering']}. ",
        "Sólo se conservaron conteos, valores categóricos y módulos destino; no IDs, nombres, emails, asuntos ni textos.", "",
    ]
    for name, summary in contract["task_sample"]["summary"].items():
        lines.append(f"- `{name}`: `{summary}`")
    lines += ["", "## Comparación con publisher actual", "", "| Campo | Base | Confirmado | Permitido | Existe | Tipo | Required | Read-only | Compatible |", "|---|---:|---:|---:|---:|---|---:|---:|---:|"]
    for item in contract["publisher"]["comparison"]:
        lines.append(
            f"| `{item['field']}` | {item['base']} | {item['confirmed']} | {item['allowed']} | "
            f"{item['production_exists']} | `{item['production_type']}` | {item['required']} | "
            f"{item['read_only']} | {item['compatible']} |"
        )
    kinds = contract["publisher"]["task_kind_picklist"]
    lines += [
        "", "### Valores del publisher frente a Production", "",
        f"- Confirmados: {', '.join(kinds['confirmed']) or 'ninguno'}",
        f"- Faltantes en Production: {', '.join(kinds['missing_in_production']) or 'ninguno'}",
        f"- Sólo en Production: {', '.join(kinds['production_only']) or 'ninguno'}", "",
        "### Valores configurados en código", "",
    ]
    for item in contract["publisher"]["configured_value_compatibility"]:
        lines.append(
            f"- `{item['field']}`: confirmados={item['confirmed']}; faltantes={item['missing']}"
        )
    status = contract["status_contract"]
    relations = contract["relationship_capabilities"]
    lines += [
        "", "## Estado abierto y resuelto", "",
        f"- Abierta: API `{status['open']['actual_value']}`, visible `{status['open']['display_value']}`, "
        f"observada en muestra: {status['open']['observed_in_sample']}.",
        f"- Resuelta/completada: API `{status['resolved']['actual_value']}`, visible `{status['resolved']['display_value']}`, "
        f"observada en muestra: {status['resolved']['observed_in_sample']}.",
        f"- {status['note']}", "",
        "## Relaciones demostradas", "",
        f"- Contact: {relations['contact']}.",
        f"- Owner/User: {relations['owner_user']}.",
        f"- Póliza: {relations['policy']}.",
        f"- Operación: {relations['operation']}.", "",
        "## Contrato recomendado para Excepciones de Facturación", "",
        "- Obligatorio Zoho: `Subject` (system mandatory). No hay campos con `required=true` en metadata global.",
        "- Recomendados: `Subject`; `Status=No iniciado`; `Observaciones`; `rea=Negocios Bienestar y Beneficios`; "
        "`Priority` usando un actual_value confirmado; y `tipo_de_solicitud` después de decidir entre valores existentes.",
        "- Opcionales: `Responsable` o `Owner`, `Due_Date`, `Fecha_de_solicitud_del_cliente`, "
        "`Correo_responsable`, `Ramo`, `Aseguradora1`, `N_mero_p_liza` y `Who_Id` cuando haya Contact confirmado.",
        "- No usar todavía: `What_Id` para Polizas/Opeeraciones, campos read-only, lookups sin destino demostrado "
        "o valores picklist nuevos.",
        "- No se propone crear el valor `Excepciones de Facturación`; sólo podrá usarse si ya aparece en metadata.", "",
        "## Pendientes no demostrables", "",
        "- Campos required por layout y reglas condicionales.",
        "- Editable/visible separados de read-only: la fachada no los expone.",
        "- Defaults de campo y, cuando no aparezcan en cada valor, estado active/default de picklists.",
        "- Layout que debe usar Excepciones de Facturación.",
        "- La relación funcional definitiva con póliza u operación cuando metadata no resuelva el destino.", "",
        "## Seguridad y operaciones", "",
        f"- Production READ: {audit['production_reads']}",
        "- Production WRITE: 0", "- CREATE: 0", "- UPDATE: 0", "- DELETE: 0", "- Attachments: 0", "",
    ]
    return "\n".join(lines)
