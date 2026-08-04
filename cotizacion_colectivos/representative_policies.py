from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

from django.conf import settings

from integrations.zoho.exceptions import ZohoError
from integrations.zoho.schemas import FieldMetadata

from .discovery import atomic_text, write_json


PRODUCTION_PROFILE = "production"
POLICY_MODULE = "Polizas"
INSURED_MODULE = "Riesgos1"
RISK_MODULE = "Riesgos"
CONTACT_MODULE = "Contacts"
MAX_RELATED_RECORDS = 200
FIELD_CHUNK_SIZE = 40

REPRESENTATIVE_POLICIES = {
    "091000811814": {"slug": "salud_colectivo", "branch": "Salud colectivo", "code": "91"},
    "158140": {"slug": "exequial_colectivo", "branch": "Exequial colectivo", "code": "86"},
    "1000166": {"slug": "hogar_colectivo", "branch": "Hogar colectivo", "code": "28"},
    "083002914855": {"slug": "vida_grupo_deudores", "branch": "Vida grupo deudores", "code": "83"},
    "900000288971": {"slug": "movilidad_colectivo", "branch": "Movilidad colectivo", "code": "40"},
}

CONTACT_PROFILE_FIELDS = (
    "id", "Tipo_de_persona", "Tipo_ID", "N_mero_de_ID", "Full_Name",
    "First_Name", "Last_Name", "Email", "Phone", "Mobile", "Empresa",
)
ROLE_LABELS = ("asegurado", "afiliado", "beneficiario", "tomador", "titular", "asociado")
PAYMENT_HINTS = ("fraccion", "modo_de_pago", "frecuencia", "periodicidad_de_pago", "pago_", "prima", "cuota")
SENSITIVE_HINTS = (
    "nombre", "name", "document", "ident", "nit", "correo", "email", "phone",
    "telefono", "móvil", "mobile", "direccion", "address", "benefici", "observ",
    "link", "archivo", "adjunto",
)
INTERNAL_HINTS = ("owner", "analista", "vendedor", "lider", "comision", "cartera", "auditor")
RENEWAL_HINTS = ("vigencia", "renov", "prima", "pago", "frecuencia", "periodic", "plan", "ramo", "aseguradora", "estado")
NOVELTY_HINTS = ("ingreso", "retiro", "benefici", "parentesco", "plan", "pago", "prima", "observ", "contact", "asegurado")
SAFE_PICKLIST_HINTS = (
    "estado", "ramo", "aseguradora", "tipo_de_riesgo", "tipo_de_persona",
    "tipo_id", "parentesco", "plan", "modo_de_pago", "frecuencia",
    "periodicidad", "medio_de_pago", "renovable", "financia",
)


def _present(value: object) -> bool:
    return value not in (None, "", (), [], {})


def _chunks(values: Iterable[str], size: int = FIELD_CHUNK_SIZE) -> tuple[tuple[str, ...], ...]:
    unique = tuple(dict.fromkeys(values))
    return tuple(tuple(unique[index:index + size]) for index in range(0, len(unique), size))


def _lookup_id(value: object) -> str:
    if isinstance(value, dict):
        return str(value.get("id") or "")
    return ""


def _lookup_shape(value: object) -> str:
    if not _present(value):
        return "empty"
    if not isinstance(value, dict):
        return "unknown"
    has_id = bool(value.get("id"))
    has_name = bool(value.get("name"))
    if has_id and has_name:
        return "id_and_name"
    if has_id:
        return "id_only"
    if has_name:
        return "name_only"
    return "object_without_id_or_name"


def _safe_category(value: object, data_type: str) -> object:
    if not _present(value):
        return "empty"
    kind = data_type.casefold()
    if kind in {"lookup", "ownerlookup"}:
        return _lookup_shape(value)
    if kind in {"subform", "multiselectlookup", "multiselectpicklist"}:
        return {"kind": "collection", "count": len(value) if isinstance(value, (list, tuple)) else 1}
    if kind == "layout":
        if isinstance(value, dict):
            return str(value.get("name") or "configured")[:80]
        if isinstance(value, str):
            return value[:80] if value and all(char.isprintable() for char in value) else "configured"
        return "configured"
    if kind in {"picklist", "boolean"}:
        text = str(value).strip()
        return text[:80] if text and all(char.isprintable() for char in text) else "other"
    if kind in {"currency", "double", "integer", "long", "percent", "formula"}:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return "numeric_present"
        return "zero" if number == 0 else "positive" if number > 0 else "negative"
    if kind in {"date", "datetime"}:
        return f"{kind}_present"
    return f"{kind or 'value'}_present"


def _field_policy(field: FieldMetadata) -> dict[str, object]:
    text = f"{field.api_name} {field.field_label}".casefold()
    sensitive = any(hint in text for hint in SENSITIVE_HINTS)
    internal = any(hint in text for hint in INTERNAL_HINTS)
    system = field.read_only or field.data_type.casefold() in {"formula", "autonumber"}
    if system:
        origin, client_editable, internal_editable = "Sistema", "no", "no"
    elif internal:
        origin, client_editable, internal_editable = "A&S", "no", "sí"
    elif any(hint in text for hint in NOVELTY_HINTS):
        origin, client_editable, internal_editable = "Cliente/A&S", "pendiente", "sí"
    else:
        origin, client_editable, internal_editable = "Zoho/A&S", "no", "sí"
    return {
        "sensitive": sensitive,
        "renewals": any(hint in text for hint in RENEWAL_HINTS),
        "novelties": any(hint in text for hint in NOVELTY_HINTS),
        "client_visible": "enmascarado" if sensitive else "sí",
        "client_editable": client_editable,
        "ays_editable": internal_editable,
        "origin": origin,
        "business_note": "Propuesta; requiere aprobación funcional" if client_editable == "pendiente" else "Clasificación técnica preliminar",
    }


def _field_result(field: FieldMetadata, values: list[object]) -> dict[str, object]:
    populated = [value for value in values if _present(value)]
    categories = Counter()
    field_key = f"{field.api_name} {field.field_label}".casefold()
    safe_picklist = any(hint in field_key for hint in SAFE_PICKLIST_HINTS)
    for value in populated:
        category = (
            _safe_category(value, field.data_type)
            if field.data_type.casefold() != "picklist" or safe_picklist
            else "picklist_present"
        )
        key = str(category) if not isinstance(category, str) else category
        categories[key] += 1
    result = {
        "label": field.field_label,
        "api_name": field.api_name,
        "type": field.data_type,
        "populated": len(populated),
        "empty": len(values) - len(populated),
        "coverage_percent": round((len(populated) / len(values) * 100), 1) if values else 0.0,
        "value_categories": dict(sorted(categories.items())),
        "picklist_values": sorted({str(item.get("display_value") or item.get("actual_value") or "") for item in field.pick_list_values if safe_picklist and (item.get("display_value") or item.get("actual_value"))}),
    }
    result.update(_field_policy(field))
    return result


def _profile_fields(records: list[dict[str, object]], metadata: tuple[FieldMetadata, ...]) -> list[dict[str, object]]:
    results = []
    for field in metadata:
        result = _field_result(field, [record.get(field.api_name) for record in records])
        result["unavailable_records"] = sum(
            field.api_name in record.get("__unavailable_fields__", ()) for record in records
        )
        results.append(result)
    return results


def _get_full_record(zoho, module: str, record_id: str, metadata: tuple[FieldMetadata, ...]) -> dict[str, object]:
    merged: dict[str, object] = {"id": record_id}
    names = tuple(field.api_name for field in metadata if field.api_name != "id")
    unavailable: set[str] = set()

    def fetch(chunk: tuple[str, ...]) -> None:
        try:
            page = zoho.search.by_field(
                module=module, field="id", value=record_id,
                fields=("id", *chunk), page=1, limit=2,
            )
        except ZohoError:
            if len(chunk) == 1:
                unavailable.add(chunk[0])
                return
            middle = len(chunk) // 2
            fetch(chunk[:middle])
            fetch(chunk[middle:])
            return
        if len(page.records) != 1:
            unavailable.update(chunk)
            return
        merged.update(page.records[0])

    for chunk in _chunks(names):
        fetch(chunk)
    if unavailable:
        merged["__unavailable_fields__"] = tuple(sorted(unavailable))
    return merged


def _batch_records_by_ids(
    zoho,
    module: str,
    record_ids: set[str],
    metadata: tuple[FieldMetadata, ...],
) -> list[dict[str, object]]:
    clean_ids = tuple(sorted(identifier for identifier in record_ids if identifier.isdigit() and 10 <= len(identifier) <= 30))[:MAX_RELATED_RECORDS]
    if not clean_ids:
        return []
    merged: dict[str, dict[str, object]] = {identifier: {"id": identifier} for identifier in clean_ids}
    field_chunks = _chunks(tuple(field.api_name for field in metadata if field.api_name != "id"))
    for id_chunk in _chunks(clean_ids, 50):
        quoted_ids = ",".join(f"'{identifier}'" for identifier in id_chunk)
        for fields in field_chunks:
            selected = ",".join(("id", *fields))
            query = f"SELECT {selected} FROM {module} WHERE id in ({quoted_ids})"
            page = zoho.coql.execute(query, offset=0, limit=len(id_chunk))
            for record in page.records:
                identifier = str(record.get("id") or "")
                if identifier in merged:
                    merged[identifier].update(record)
    return list(merged.values())


def _search_records(zoho, module: str, criteria: str, metadata: tuple[FieldMetadata, ...]) -> tuple[list[dict[str, object]], bool]:
    names = tuple(field.api_name for field in metadata if field.api_name != "id")
    chunks = _chunks(names)
    records: list[dict[str, object]] = []
    page_number = 1
    complete = True
    while len(records) < MAX_RELATED_RECORDS:
        limit = min(200, MAX_RELATED_RECORDS - len(records))
        page_maps: dict[str, dict[str, object]] = {}
        more_records = False
        for index, chunk in enumerate(chunks):
            page = zoho.search.by_criteria(module=module, criteria=criteria, fields=("id", *chunk), page=page_number, limit=limit)
            if index == 0:
                more_records = page.more_records
            for record in page.records:
                record_id = str(record.get("id") or "")
                if record_id:
                    page_maps.setdefault(record_id, {"id": record_id}).update(record)
        records.extend(page_maps.values())
        if not more_records:
            break
        if not page_maps:
            complete = False
            break
        page_number += 1
    else:
        complete = False
    return records[:MAX_RELATED_RECORDS], complete


def _metadata_map(zoho) -> dict[str, tuple[FieldMetadata, ...]]:
    return {module: zoho.metadata.list_fields(module) for module in (POLICY_MODULE, INSURED_MODULE, RISK_MODULE, CONTACT_MODULE)}


def _payment_candidates(module: str, fields: list[dict[str, object]]) -> list[dict[str, object]]:
    candidates = []
    for field in fields:
        text = str(field["api_name"]).casefold()
        if any(hint in text for hint in PAYMENT_HINTS):
            candidates.append({"module": module, **field})
    return candidates


def profile_representative_policy(zoho, policy_number: str, metadata: dict[str, tuple[FieldMetadata, ...]]) -> dict[str, object]:
    spec = REPRESENTATIVE_POLICIES[policy_number]
    matches = zoho.search.by_field(module=POLICY_MODULE, field="Name", value=policy_number, fields=("id", "Name", "Layout", "Ramo", "Aseguradora1", "Estado_de_la_p_liza"), page=1, limit=2)
    if len(matches.records) != 1:
        return {"policy_number": policy_number, **spec, "found": bool(matches.records), "matches": len(matches.records), "status": "not_found" if not matches.records else "ambiguous"}
    policy_id = str(matches.records[0].get("id") or "")
    policy = _get_full_record(zoho, POLICY_MODULE, policy_id, metadata[POLICY_MODULE])
    policy_fields = _profile_fields([policy], metadata[POLICY_MODULE])

    insured_records, insured_complete = _search_records(zoho, INSURED_MODULE, f"(P_liza:equals:{policy_id})", metadata[INSURED_MODULE])
    insured_fields = _profile_fields(insured_records, metadata[INSURED_MODULE])
    status_counts = Counter(str(record.get("Estado") or "empty")[:80] for record in insured_records)

    role_fields = tuple(field for field in metadata[INSURED_MODULE] if field.data_type.casefold() == "lookup" and any(label in field.field_label.casefold() for label in ROLE_LABELS))
    role_ids: dict[str, set[str]] = {field.api_name: set() for field in role_fields}
    role_shapes: dict[str, Counter] = {field.api_name: Counter() for field in role_fields}
    risk_ids: set[str] = set()
    multiple_roles = 0
    for record in insured_records:
        current_roles = []
        for field in role_fields:
            value = record.get(field.api_name)
            role_shapes[field.api_name][_lookup_shape(value)] += 1
            identifier = _lookup_id(value)
            if identifier:
                role_ids[field.api_name].add(identifier)
                current_roles.append(identifier)
        if len(current_roles) != len(set(current_roles)):
            multiple_roles += 1
        risk_id = _lookup_id(record.get("Riesgo"))
        if risk_id:
            risk_ids.add(risk_id)

    contact_ids = set().union(*role_ids.values()) if role_ids else set()
    contact_fields = tuple(field for field in metadata[CONTACT_MODULE] if field.api_name in CONTACT_PROFILE_FIELDS)
    contacts = _batch_records_by_ids(zoho, CONTACT_MODULE, contact_ids, contact_fields)
    risks = _batch_records_by_ids(zoho, RISK_MODULE, risk_ids, metadata[RISK_MODULE])

    policy_summary = {name: _safe_category(policy.get(name), next((field.data_type for field in metadata[POLICY_MODULE] if field.api_name == name), "text")) for name in ("Layout", "Ramo", "Aseguradora1", "Estado_de_la_p_liza", "P_liza_Fecha_de_inicio_vigencia", "P_liza_Fecha_fin_de_la_vigencia", "Tomador_principal1")}
    result = {
        "policy_number": policy_number,
        **spec,
        "found": True,
        "matches": 1,
        "status": "profiled",
        "profile": zoho.profile,
        "backend": zoho.backend_name,
        "policy_summary": policy_summary,
        "policy_fields": policy_fields,
        "insured": {
            "processed": len(insured_records), "complete": insured_complete,
            "status_counts": dict(sorted(status_counts.items())),
            "fields": insured_fields,
            "role_structures": {field: dict(sorted(counts.items())) for field, counts in role_shapes.items()},
            "role_related_contacts": {field: len(values) for field, values in role_ids.items()},
            "records_with_same_contact_in_multiple_roles": multiple_roles,
            "with_risk": sum(1 for record in insured_records if _lookup_id(record.get("Riesgo"))),
        },
        "contacts": {"processed": len(contacts), "fields": _profile_fields(contacts, contact_fields)},
        "risks": {"processed": len(risks), "fields": _profile_fields(risks, metadata[RISK_MODULE])},
    }
    result["payment_fractional_candidates"] = _payment_candidates(POLICY_MODULE, policy_fields) + _payment_candidates(INSURED_MODULE, insured_fields)
    return result


def run_representative_policy_profile(zoho, selected: Iterable[str]) -> dict[str, object]:
    metadata = _metadata_map(zoho)
    policies = [profile_representative_policy(zoho, number, metadata) for number in selected]
    return {
        "profile": PRODUCTION_PROFILE,
        "environment": PRODUCTION_PROFILE,
        "backend": zoho.backend_name,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "content": "safe_aggregates_only",
        "policies": policies,
    }


def _field_table(fields: list[dict[str, object]], *, populated_only: bool = True) -> str:
    rows = []
    for field in fields:
        if populated_only and not field["populated"]:
            continue
        rows.append(f"| {field['label']} | `{field['api_name']}` | {field['type']} | {field['populated']} | {field['coverage_percent']}% | {field['client_visible']} | {field['client_editable']} | {field['ays_editable']} | {field['origin']} |")
    return "\n".join(rows) or "| Pendiente | — | — | 0 | 0% | — | — | — | — |"


def _policy_document(policy: dict[str, object]) -> str:
    if not policy.get("found") or policy.get("matches") != 1:
        return f"# {policy['branch']}\n\nPóliza autorizada `{policy['policy_number']}`: **{policy['status']}** ({policy['matches']} coincidencias). No se eligió un registro arbitrariamente.\n"
    summary = policy["policy_summary"]
    return f"""# {policy['branch']}

## Identificación técnica

- Póliza autorizada: `{policy['policy_number']}`.
- Perfil: `production`; backend: `{policy['backend']}`; solo lectura.
- Layout: {summary['Layout']}; ramo: {summary['Ramo']}; aseguradora: {summary['Aseguradora1']}.
- Estado: {summary['Estado_de_la_p_liza']}; vigencia inicio/fin: {summary['P_liza_Fecha_de_inicio_vigencia']} / {summary['P_liza_Fecha_fin_de_la_vigencia']}.
- Tomador: estructura `{summary['Tomador_principal1']}`; no se conserva ni publica su valor.

## Campos poblados de Polizas

| Label | API name | Tipo | Poblado | Cobertura | Cliente ve | Cliente edita | A&S edita | Origen |
|---|---|---|---:|---:|---|---|---|---|
{_field_table(policy['policy_fields'])}

## Asegurados (`Riesgos1`)

- Procesados: **{policy['insured']['processed']}** ({'completo' if policy['insured']['complete'] else 'parcial'}).
- Estados agregados: {policy['insured']['status_counts']}.
- Con riesgo relacionado: {policy['insured']['with_risk']}.
- Contactos relacionados verificados: {policy['contacts']['processed']}.

| Label | API name | Tipo | Poblado | Cobertura | Cliente ve | Cliente edita | A&S edita | Origen |
|---|---|---|---:|---:|---|---|---|---|
{_field_table(policy['insured']['fields'])}

## Riesgos

- Riesgos vinculados por `Riesgos1.Riesgo`: **{policy['risks']['processed']}**.

| Label | API name | Tipo | Poblado | Cobertura | Cliente ve | Cliente edita | A&S edita | Origen |
|---|---|---|---:|---:|---|---|---|---|
{_field_table(policy['risks']['fields'])}

## Pago fraccionado

Los candidatos se derivan de metadata y cobertura real. `Polizas.Modo_de_pago` y `Polizas.Frecuencia` son los conceptos principales a comprobar; importes por cuota y pagos de `Riesgos1` se mantienen separados. La editabilidad por cliente queda pendiente de decisión funcional de A&S.
"""


def _overview(result: dict[str, object]) -> str:
    rows = "\n".join(f"| `{p['policy_number']}` | {p['branch']} | {p['status']} | {p.get('insured', {}).get('processed', 0)} | {p.get('risks', {}).get('processed', 0)} |" for p in result["policies"])
    return f"""# Radiografía de pólizas representativas de Colectivos

## Alcance

Cinco pólizas autorizadas, perfil `production`, consultas cerradas de solo lectura mediante `integrations.zoho.get_zoho`. Los artefactos conservan únicamente metadata, cobertura, conteos y categorías; no contienen IDs, nombres, documentos, teléfonos, correos ni respuestas crudas.

| Póliza autorizada | Ramo esperado | Resultado | Riesgos1 | Riesgos |
|---|---|---|---:|---:|
{rows}

## Fuentes locales

- `Novedades Junio_Fonconstruimos.xlsx`: cuatro secciones (nuevos descuentos, modificaciones, retiros y devoluciones). Columnas base: asociado, asegurado, póliza, ramo, aseguradora, pago/descuento y observaciones.
- `MT-CA-01 Matriz de Ramos (1).xlsx`: códigos 91 Salud colectivo, 86 Exequial colectivo, 28 Hogar colectivo, 83 Vida grupo deudores y 40 Movilidad colectivo. Se usó solo como referencia funcional, nunca para localizar registros Zoho.
"""


def _matrix(result: dict[str, object]) -> str:
    functional = [
        ("Tipo ID asociado", CONTACT_MODULE, "Tipo_ID"), ("ID asociado", CONTACT_MODULE, "N_mero_de_ID"),
        ("Nombre asociado", CONTACT_MODULE, "Full_Name"), ("Tipo ID asegurado", CONTACT_MODULE, "Tipo_ID"),
        ("ID asegurado", CONTACT_MODULE, "N_mero_de_ID"), ("Nombre asegurado", CONTACT_MODULE, "Full_Name"),
        ("Parentesco", INSURED_MODULE, "Parentesco"), ("Póliza", POLICY_MODULE, "Name"),
        ("Ramo", POLICY_MODULE, "Ramo"), ("Aseguradora", POLICY_MODULE, "Aseguradora1"),
        ("Estado", INSURED_MODULE, "Estado"), ("Vigencia", POLICY_MODULE, "P_liza_Fecha_de_inicio_vigencia / P_liza_Fecha_fin_de_la_vigencia"),
        ("Pago fraccionado", POLICY_MODULE, "Modo_de_pago"), ("Forma de pago", POLICY_MODULE, "Modo_de_pago"),
        ("Periodicidad", POLICY_MODULE, "Frecuencia"), ("Prima", POLICY_MODULE, "Valor_prima"),
        ("Pago mensual", INSURED_MODULE, "Pago_total_Seg_n_la_forma_de_pago_Valor_asegura"),
        ("Descuento empleado", INSURED_MODULE, "Pago_EMPLEADO_Sin_IVA"),
        ("Fecha ingreso", INSURED_MODULE, "Fecha_ingreso_riesgo"), ("Fecha retiro", INSURED_MODULE, "Fecha_salida_riesgo"),
        ("Plan", INSURED_MODULE, "Plan"), ("Beneficiarios", INSURED_MODULE, "Beneficiarios / Beneficiario"),
        ("Riesgo", INSURED_MODULE, "Riesgo"), ("Valor asegurado", INSURED_MODULE, "Valor_asegurado"),
        ("Observaciones", INSURED_MODULE, "Observaciones"), ("Tipo de novedad", "Solicitud futura", "no existe"),
        ("Fecha efectiva", "Solicitud futura", "dato del cliente"), ("Adjuntos", "Solicitud futura", "dato del cliente"),
    ]
    branches = [p["slug"] for p in result["policies"]]
    header = "| Campo funcional | Módulo | API name | " + " | ".join(branches) + " | Cliente edita | A&S edita | Origen |\n|---|---|---|" + "|".join(["---"] * len(branches)) + "|---|---|---|"
    rows = []
    for label, module, api_name in functional:
        coverage = []
        for policy in result["policies"]:
            pools = policy.get("policy_fields", []) + policy.get("insured", {}).get("fields", []) + policy.get("contacts", {}).get("fields", []) + policy.get("risks", {}).get("fields", [])
            names = {item["api_name"]: item for item in pools}
            candidates = [part.strip() for part in api_name.split("/")]
            item = next((names.get(candidate) for candidate in candidates if candidate in names), None)
            coverage.append("poblado" if item and item["populated"] else "no poblado" if item else "pendiente de confirmar")
        rows.append(f"| {label} | {module} | `{api_name}` | " + " | ".join(coverage) + " | pendiente | sí | Zoho/Cliente/A&S |")
    return "# Matriz consolidada por ramo\n\n" + header + "\n" + "\n".join(rows) + "\n"


def _excel_mapping() -> str:
    return """# Cruce con `Novedades Junio_Fonconstruimos.xlsx`

| Columna actual | Fuente candidata | Clasificación |
|---|---|---|
| Id asociado | `Contacts.Tipo_ID` + `Contacts.N_mero_de_ID` | Precargada, siempre enmascarada en UI |
| Nombre asociado | `Contacts.Full_Name` | Precargada |
| Id Asegurado | `Riesgos1.Asegurado → Contacts.N_mero_de_ID` | Precargada, enmascarada |
| Nombre Asegurado | `Riesgos1.Asegurado → Contacts.Full_Name` | Precargada |
| Póliza | `Polizas.Name` | Precargada, solo lectura |
| Ramo | `Polizas.Ramo` / `Riesgos1.Ramo` | Precargada; validar coherencia |
| Aseguradora | `Polizas.Aseguradora1` / `Riesgos1.Aseguradora` | Precargada |
| Pago Mensual (Con IVA) Asegurado | campos de pago `Riesgos1` | Pendiente escoger campo por ramo y validar IVA |
| Descuento Mensual Empleado | `Riesgos1.Pago_EMPLEADO_Sin_IVA` candidato | Pendiente: el Excel pide descuento y el campo Zoho declara sin IVA |
| Observaciones | `Riesgos1.Observaciones` / solicitud futura | Editable por cliente, revisión A&S pendiente |

Campos que debe añadir la plantilla futura: Tipo ID asociado, Tipo ID asegurado, Fecha efectiva, Tipo de novedad, Motivo, Estado de revisión, Valor anterior y Valor nuevo.

## Flujo propuesto

1. Excel actual: snapshot precargado desde Zoho.
2. Plantilla de novedades: columnas editables separadas de los valores vigentes.
3. Excel respondido: entrada del cliente sin sobrescribir el snapshot.
4. Comparativo: valor anterior/nuevo y validaciones.
5. Consolidado aprobado: decisión y campos internos de A&S.
"""


def _branch_parameterization() -> str:
    rows = "\n".join(f"| {v['code']} | {v['branch']} | Colectivo | `{v['slug']}` |" for v in REPRESENTATIVE_POLICIES.values())
    return f"""# Propuesta de parametrización de ramos

| Código matriz | Nombre funcional | Área | Clave propuesta |
|---|---|---|---|
{rows}

```python
COLLECTIVE_BRANCH_CONFIG = {{
    # Completar layouts, valores Zoho y campos obligatorios solo después de
    # aprobar la evidencia de cada radiografía.
}}
```

La Matriz de Ramos no se usó ni debe usarse para buscar registros en Zoho.
"""


def save_representative_policy_profile(result: dict[str, object]) -> None:
    base = Path(settings.BASE_DIR)
    artifact_dir = base / "artifacts/zoho/colectivos/representative_policies"
    docs_dir = base / "docs/cotizacion_colectivos/representative_policies"
    write_json(artifact_dir / "profile.json", result)
    atomic_text(docs_dir / "overview.md", _overview(result))
    for policy in result["policies"]:
        atomic_text(docs_dir / f"{policy['slug']}.md", _policy_document(policy))
    atomic_text(docs_dir / "field_matrix.md", _matrix(result))
    atomic_text(docs_dir / "relationship_matrix.md", "# Matriz de relaciones\n\n" + "\n".join(f"- `{p['policy_number']}`: Polizas → Riesgos1 por lookup `P_liza`; contactos por lookups de rol; riesgos por `Riesgos1.Riesgo`. Resultado: {p['status']}." for p in result["policies"]) + "\n")
    atomic_text(docs_dir / "excel_mapping.md", _excel_mapping())
    atomic_text(docs_dir / "branch_parameterization.md", _branch_parameterization())
    atomic_text(docs_dir / "pending_questions.md", "# Preguntas pendientes\n\n- Aprobar editabilidad cliente/A&S por campo.\n- Confirmar el campo de pago mensual con IVA por ramo.\n- Confirmar si `Modo_de_pago=Fraccionado` es la autoridad funcional para todos los ramos.\n- Definir tipos de novedad, fechas efectivas, adjuntos y estados de revisión en el futuro modelo de solicitudes.\n- Confirmar subformularios, notas, tareas, siniestros y operaciones antes de consultarlos; esta radiografía no siguió módulos no confirmados.\n")
