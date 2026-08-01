from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

from integrations.zoho.schemas import FieldMetadata, ModuleMetadata

from .constants import DISCOVERY_MODULES

PENDING = "Pendiente de validacion funcional"

_DOCUMENT_HINTS = ("nit", "cedula", "document", "identificacion", "identification")
_NAME_HINTS = ("razon", "social", "nombre", "name", "empresa", "company")
_EMAIL_HINTS = ("correo", "email", "e_mail")
_PHONE_HINTS = ("telefono", "phone", "movil", "mobile", "celular")


def atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def write_json(path: Path, value: object) -> None:
    atomic_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
    )


def serialize_field(field: FieldMetadata) -> dict[str, object]:
    return asdict(field)


def build_relationships(
    fields_by_module: dict[str, tuple[FieldMetadata, ...]],
) -> list[dict[str, object]]:
    relationships: list[dict[str, object]] = []
    for module, fields in sorted(fields_by_module.items()):
        for field in fields:
            if not field.lookup and not field.related_details:
                continue
            relationships.append(
                {
                    "module": module,
                    "field": field.api_name,
                    "field_label": field.field_label,
                    "data_type": field.data_type,
                    "lookup": field.lookup,
                    "related_details": field.related_details,
                }
            )
    return relationships


def build_search_candidates(
    fields_by_module: dict[str, tuple[FieldMetadata, ...]],
) -> dict[str, list[dict[str, object]]]:
    candidates: dict[str, list[dict[str, object]]] = {
        "document": [],
        "name": [],
        "email": [],
        "phone": [],
    }
    hints = {
        "document": _DOCUMENT_HINTS,
        "name": _NAME_HINTS,
        "email": _EMAIL_HINTS,
        "phone": _PHONE_HINTS,
    }
    for module, fields in sorted(fields_by_module.items()):
        for field in fields:
            searchable_text = f"{field.api_name} {field.field_label}".casefold()
            for category, category_hints in hints.items():
                matched = tuple(
                    hint for hint in category_hints if hint in searchable_text
                )
                if not matched:
                    continue
                candidates[category].append(
                    {
                        "module": module,
                        "api_name": field.api_name,
                        "label": field.field_label,
                        "data_type": field.data_type,
                        "unique": field.unique,
                        "required": field.required or field.system_mandatory,
                        "evidence": list(matched),
                        "status": PENDING,
                    }
                )
    return candidates


def build_discovery_markdown(
    *,
    modules: Iterable[ModuleMetadata],
    fields_by_module: dict[str, tuple[FieldMetadata, ...]],
    failures: dict[str, str],
    relationships: list[dict[str, object]],
    candidates: dict[str, list[dict[str, object]]],
    generated_at: str,
) -> str:
    module_index = {module.api_name: module for module in modules}
    lines = [
        "# Descubrimiento Cotizacion - Colectivos",
        "",
        f"- Generado: {generated_at}",
        "- Perfil fijo: sandbox",
        "- Contenido: metadatos exclusivamente; no contiene registros.",
        "- Estado funcional: mapeos pendientes de validacion.",
        "",
        "## Modulos inspeccionados",
        "",
    ]
    for api_name in DISCOVERY_MODULES:
        module = module_index.get(api_name)
        if module is None:
            lines.append(f"- `{api_name}`: no aparece en Modules API.")
            continue
        if api_name in failures:
            lines.append(
                f"- `{api_name}` — {module.plural_label or module.module_name}: "
                f"metadatos de campos no disponibles ({failures[api_name]})."
            )
            continue
        lines.append(
            f"- `{api_name}` — {module.plural_label or module.module_name}: "
            f"{len(fields_by_module.get(api_name, ())) } campos."
        )

    lines.extend(
        [
            "",
            "## Conclusiones obligatorias",
            "",
            f"1. Modulo de empresas: {PENDING}.",
            f"2. Modulo de individuos: {PENDING}.",
            f"3. Campo NIT: {PENDING}.",
            f"4. Campo cedula: {PENDING}.",
            f"5. Campos de nombre: {PENDING}.",
            f"6. Razon social: {PENDING}.",
            f"7. Campos unicos: revisar `search_candidates.json`; {PENDING}.",
            f"8. Campos apropiados para busqueda: {PENDING}.",
            f"9. Modulo de polizas: `Polizas` aparece en metadata; su uso funcional esta {PENDING.lower()}.",
            f"10. Empresa-polizas: {PENDING}.",
            f"11. Individuo-polizas: {PENDING}.",
            f"12. Asegurados: {PENDING}.",
            f"13. Riesgos: {PENDING}.",
            f"14. Ramo: {PENDING}.",
            f"15. Aseguradora: {PENDING}.",
            f"16. Vigencia: {PENDING}.",
            f"17. Relaciones directas: {len(relationships)} candidatas de metadata; {PENDING}.",
            f"18. Relaciones que requieren COQL: {PENDING}.",
            f"19. Relaciones no claras: todas las no demostradas por lookups o related_details.",
            "20. Diferencias con Produccion: fuera de alcance; no se consulto Produccion.",
            "",
            "## Candidatos",
            "",
        ]
    )
    for category in ("document", "name", "email", "phone"):
        lines.append(f"### {category}")
        lines.append("")
        values = candidates.get(category, [])
        if not values:
            lines.append(f"- {PENDING}.")
        else:
            for item in values:
                lines.append(
                    f"- `{item['module']}.{item['api_name']}` — "
                    f"{item['label']} ({item['data_type']}); candidato, no confirmado."
                )
        lines.append("")
    return "\n".join(lines)


def generated_timestamp() -> str:
    return datetime.now(UTC).isoformat()

