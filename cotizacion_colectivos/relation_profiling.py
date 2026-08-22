from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from django.conf import settings

from integrations.zoho.exceptions import ZohoError

from .constants import (
    INSURED_PROFILE_FIELDS,
    POLICY_PROFILE_FIELDS,
    RELATION_PROFILE_MAX_RECORDS,
    RELATION_PROFILE_PAGE_SIZE,
    RISK_PROFILE_FIELDS,
)
from .discovery import atomic_text, write_json
from .profiling import classify_lookup_structure, is_present


ARTIFACT_DIR = Path("artifacts/zoho/colectivos/relation_profiles")
RELATIONS_REPORT = Path("docs/cotizacion_colectivos/relations_analysis.md")


@dataclass(frozen=True)
class ProfileSpec:
    name: str
    module: str
    fields: tuple[str, ...]
    lookup_targets: dict[str, str]
    distributions: tuple[str, ...]
    multiplicity_fields: tuple[str, ...]


POLICY_SPEC = ProfileSpec(
    name="policies",
    module="Polizas",
    fields=POLICY_PROFILE_FIELDS,
    lookup_targets={"Tomador_principal1": "Contacts"},
    distributions=("Estado_de_la_p_liza",),
    multiplicity_fields=("Tomador_principal1",),
)
INSURED_SPEC = ProfileSpec(
    name="insured",
    module="Riesgos1",
    fields=INSURED_PROFILE_FIELDS,
    lookup_targets={
        "P_liza": "Polizas",
        "Asegurado": "Contacts",
        "Riesgo": "Riesgos",
    },
    distributions=("Estado",),
    multiplicity_fields=("P_liza", "Asegurado"),
)
RISK_SPEC = ProfileSpec(
    name="risks",
    module="Riesgos",
    fields=RISK_PROFILE_FIELDS,
    lookup_targets={"Contratista": "Contacts", "Contratante": "Contacts"},
    distributions=("Tipo_de_riesgo",),
    multiplicity_fields=("Contratista", "Contratante"),
)


def _digest(key: bytes, value: object) -> bytes | None:
    if not is_present(value):
        return None
    return hmac.new(key, str(value).strip().encode("utf-8"), hashlib.sha256).digest()


def _lookup_id(value: object) -> object | None:
    return value.get("id") if isinstance(value, dict) else None


def _safe_distribution_value(value: object) -> str:
    if not is_present(value):
        return "empty"
    text = str(value).strip()
    return text if len(text) <= 80 and all(char.isprintable() for char in text) else "other"


def collect_id_hashes(zoho, module: str, key: bytes) -> tuple[set[bytes], dict[str, object]]:
    identifiers: set[bytes] = set()
    page_number = 1
    processed = 0
    pages = 0
    complete = False
    while processed < RELATION_PROFILE_MAX_RECORDS:
        limit = min(RELATION_PROFILE_PAGE_SIZE, RELATION_PROFILE_MAX_RECORDS - processed)
        page = zoho.records.list(module=module, fields=("id",), page=page_number, limit=limit)
        pages += 1
        for record in page.records[:limit]:
            processed += 1
            digest = _digest(key, record.get("id"))
            if digest is not None:
                identifiers.add(digest)
        if not page.more_records:
            complete = True
            break
        if not page.records:
            break
        page_number += 1
    return identifiers, {
        "processed": processed,
        "pages": pages,
        "complete": complete,
    }


def _relationship_status(*, with_id: int, matched: int, unmatched: int) -> str:
    if with_id == 0:
        return "No confirmada"
    if matched == 0:
        return "Rechazada"
    if unmatched == 0 and matched >= 3:
        return "Confirmada"
    return "Parcialmente confirmada"


def profile_module(zoho, spec: ProfileSpec, references: dict[str, set[bytes]], key: bytes) -> dict[str, object]:
    processed = 0
    pages = 0
    complete = False
    coverage = {field: Counter() for field in spec.fields if field != "id"}
    lookup_structures = {field: Counter() for field in spec.lookup_targets}
    lookup_matches = {
        field: Counter({"nonempty": 0, "with_id": 0, "matched": 0, "unmatched": 0})
        for field in spec.lookup_targets
    }
    distributions = {field: Counter() for field in spec.distributions}
    multiplicities: dict[str, Counter[bytes]] = {
        field: Counter() for field in spec.multiplicity_fields
    }
    page_number = 1

    while processed < RELATION_PROFILE_MAX_RECORDS:
        limit = min(RELATION_PROFILE_PAGE_SIZE, RELATION_PROFILE_MAX_RECORDS - processed)
        page = zoho.records.list(
            module=spec.module,
            fields=spec.fields,
            page=page_number,
            limit=limit,
        )
        pages += 1
        for record in page.records[:limit]:
            processed += 1
            for field, counts in coverage.items():
                counts["populated" if is_present(record.get(field)) else "empty"] += 1
            for field, target_module in spec.lookup_targets.items():
                value = record.get(field)
                lookup_structures[field][classify_lookup_structure(value)] += 1
                if is_present(value):
                    lookup_matches[field]["nonempty"] += 1
                lookup_digest = _digest(key, _lookup_id(value))
                if lookup_digest is not None:
                    lookup_matches[field]["with_id"] += 1
                    if lookup_digest in references[target_module]:
                        lookup_matches[field]["matched"] += 1
                    else:
                        lookup_matches[field]["unmatched"] += 1
                    if field in multiplicities:
                        multiplicities[field][lookup_digest] += 1
            for field, counts in distributions.items():
                counts[_safe_distribution_value(record.get(field))] += 1
        if not page.more_records:
            complete = True
            break
        if not page.records:
            break
        page_number += 1

    relationships = {}
    for field, target in spec.lookup_targets.items():
        counts = lookup_matches[field]
        relationships[field] = {
            "target": target,
            "nonempty": counts["nonempty"],
            "with_id": counts["with_id"],
            "matched": counts["matched"],
            "unmatched": counts["unmatched"],
            "status": _relationship_status(
                with_id=counts["with_id"],
                matched=counts["matched"],
                unmatched=counts["unmatched"],
            ),
        }

    return {
        "profile": "sandbox",
        "environment": "sandbox",
        "module": spec.module,
        "processed": processed,
        "pages": pages,
        "complete": complete,
        "fields": list(spec.fields),
        "coverage": {
            field: {"populated": counts["populated"], "empty": counts["empty"]}
            for field, counts in coverage.items()
        },
        "lookup_structures": {
            field: dict(sorted(counts.items())) for field, counts in lookup_structures.items()
        },
        "relationships": relationships,
        "distributions": {
            field: dict(sorted(counts.items())) for field, counts in distributions.items()
        },
        "multiplicity": {
            field: {
                "related_keys": len(counts),
                "keys_with_multiple_records": sum(1 for count in counts.values() if count > 1),
                "maximum_records_per_key": max(counts.values(), default=0),
            }
            for field, counts in multiplicities.items()
        },
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def run_relation_profile(zoho, spec: ProfileSpec) -> dict[str, object]:
    key = secrets.token_bytes(32)
    targets = set(spec.lookup_targets.values())
    references = {}
    reference_summary = {}
    for module in sorted(targets):
        identifiers, summary = collect_id_hashes(zoho, module, key)
        references[module] = identifiers
        reference_summary[module] = summary
    result = profile_module(zoho, spec, references, key)
    result["reference_modules"] = reference_summary
    return result


def artifact_path(spec: ProfileSpec) -> Path:
    return Path(settings.BASE_DIR) / ARTIFACT_DIR / f"{spec.name}.json"


def save_relation_profile(spec: ProfileSpec, result: dict[str, object]) -> None:
    write_json(artifact_path(spec), result)
    render_relations_report()


def load_relation_profiles() -> dict[str, dict[str, object]]:
    results = {}
    for spec in (POLICY_SPEC, INSURED_SPEC, RISK_SPEC):
        path = artifact_path(spec)
        if path.exists():
            results[spec.name] = json.loads(path.read_text(encoding="utf-8"))
    return results


def _table_counts(values: dict[str, int]) -> str:
    if not values:
        return "| Sin valores | 0 |"
    return "\n".join(f"| {key} | {value} |" for key, value in values.items())


def _profile_section(name: str, result: dict[str, object] | None) -> str:
    if not result:
        return f"## {name}\n\nPendiente de ejecutar el perfilador autorizado."
    coverage = "\n".join(
        f"| `{field}` | {counts['populated']} | {counts['empty']} |"
        for field, counts in result["coverage"].items()
    )
    lookups = []
    for field, structures in result["lookup_structures"].items():
        relation = result["relationships"][field]
        lookups.append(
            f"### `{field}` → `{relation['target']}`\n\n"
            f"- Clasificación: **{relation['status']}**.\n"
            f"- Lookup con ID: {relation['with_id']}.\n"
            f"- Coincidencias internas: {relation['matched']}.\n"
            f"- Sin coincidencia: {relation['unmatched']}.\n\n"
            f"| Estructura | Cantidad |\n|---|---:|\n{_table_counts(structures)}"
        )
    distributions = []
    for field, values in result["distributions"].items():
        distributions.append(
            f"### Distribución `{field}`\n\n| Valor | Cantidad |\n|---|---:|\n{_table_counts(values)}"
        )
    return f"""## {name}

- Módulo: `{result['module']}`.
- Procesados: **{result['processed']}**.
- Resultado: **{'Completo' if result['complete'] else 'Parcial'}**.

### Cobertura

| Campo | Poblado | Vacío |
|---|---:|---:|
{coverage}

{chr(10).join(lookups)}

{chr(10).join(distributions)}
"""


def render_relations_report() -> None:
    profiles = load_relation_profiles()
    def relation(profile_name: str, field: str) -> dict[str, object]:
        return profiles.get(profile_name, {}).get("relationships", {}).get(
            field,
            {"status": "No confirmada", "matched": 0, "with_id": 0, "unmatched": 0},
        )

    policy_contact = relation("policies", "Tomador_principal1")
    insured_policy = relation("insured", "P_liza")
    insured_contact = relation("insured", "Asegurado")
    insured_risk = relation("insured", "Riesgo")
    contractor_contact = relation("risks", "Contratista")
    contracting_contact = relation("risks", "Contratante")
    relations = []
    for profile in profiles.values():
        for field, relation in profile.get("relationships", {}).items():
            relations.append(
                (profile["module"], field, relation["target"], relation["status"])
            )
    decisions = "\n".join(
        f"| `{source}.{field}` → `{target}` | {status} |"
        for source, field, target, status in relations
    ) or "| Pendiente | No confirmada |"
    report = f"""# Análisis agregado de relaciones de Cotización – Colectivos

## 1. Alcance y seguridad

Perfil exclusivo `sandbox`, operaciones de solo lectura, campos cerrados y comparación de identificadores mediante HMAC efímeros. No se conservaron IDs, nombres, documentos, pólizas, hashes ni respuestas originales.

## 2. Decisiones de relación

| Relación técnica | Clasificación |
|---|---|
{decisions}

{_profile_section('3. Pólizas', profiles.get('policies'))}

{_profile_section('4. Asegurados / Riesgos1', profiles.get('insured'))}

{_profile_section('5. Riesgos', profiles.get('risks'))}

## 6. Relaciones de negocio requeridas

La clasificación funcional final debe derivarse de las coincidencias anteriores:

1. Contact empresa → Pólizas: usar `Polizas.Tomador_principal1` solo si queda confirmada.
2. Contact individuo → Pólizas: misma relación técnica, condicionada al tipo del Contact.
3. Pólizas → Riesgos1/Asegurados: usar `Riesgos1.P_liza` solo si queda confirmada.
4. Contacts → Riesgos1/Asegurados: usar `Riesgos1.Asegurado` solo si queda confirmada.
5. Riesgos1/Asegurados → Riesgos: usar `Riesgos1.Riesgo` solo si queda confirmada.
6. Contacts → Riesgos: evaluar `Riesgos.Contratista` y `Riesgos.Contratante` por separado.
7. Empresa → individuos relacionados: no confirmada por el lookup `Contacts.Empresa` en el perfil previo; pendiente de validación funcional.
8. Individuo → empresa: no confirmada por el lookup `Contacts.Empresa` en el perfil previo; pendiente de validación funcional.

### Clasificación funcional final

| Relación | Clasificación | Evidencia |
|---|---|---|
| Contact empresa → Pólizas | {policy_contact['status']} | `{policy_contact['matched']}` coincidencias entre `{policy_contact['with_id']}` lookups con ID; la evidencia no separa por sí sola el tipo del Contact. |
| Contact individuo → Pólizas | {policy_contact['status']} | Comparte la relación técnica anterior y requiere validación por segmento. |
| Pólizas → Riesgos1/Asegurados | {insured_policy['status']} | `{insured_policy['matched']}` coincidencias y `{insured_policy['unmatched']}` inconsistencias. |
| Contacts → Riesgos1/Asegurados | {insured_contact['status']} | `{insured_contact['matched']}` coincidencias y `{insured_contact['unmatched']}` inconsistencias. |
| Riesgos1/Asegurados → Riesgos | {insured_risk['status']} | `{insured_risk['matched']}` coincidencias y `{insured_risk['unmatched']}` inconsistencias. |
| Contacts → Riesgos por Contratista | {contractor_contact['status']} | `{contractor_contact['matched']}` coincidencias y `{contractor_contact['unmatched']}` inconsistencias. |
| Contacts → Riesgos por Contratante | {contracting_contact['status']} | `{contracting_contact['matched']}` coincidencias y `{contracting_contact['unmatched']}` inconsistencias. |
| Empresa → individuos relacionados | No confirmada | `Contacts.Empresa` estuvo vacío en el perfil agregado de Contacts. |
| Individuo → empresa | No confirmada | `Contacts.Empresa` estuvo vacío en el perfil agregado de Contacts. |

## 7. Limitaciones

- Las coincidencias prueban identidad técnica de IDs, no semántica comercial adicional.
- No se consultaron registros fuera de los módulos cerrados de cada perfilador.
- No se siguieron relaciones ni se escribió en Zoho.
- Una relación parcial o no confirmada no debe exponerse como funcional.

## 8. Recomendación de implementación

Implementar únicamente relaciones clasificadas como **Confirmada**. Las demás deben mostrarse como “Pendiente de validación” o permanecer ausentes.
"""
    atomic_text(Path(settings.BASE_DIR) / RELATIONS_REPORT, report)
