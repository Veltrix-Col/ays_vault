from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


def render_comparison_markdown(comparison: dict[str, Any]) -> str:
    summary = comparison["summary"]
    lines = [
        "# Comparación Zoho A&S",
        "",
        "## Resumen",
        "",
        f"- Perfil izquierdo: `{comparison['left_profile']}`",
        f"- Perfil derecho: `{comparison['right_profile']}`",
        f"- Módulos nuevos: {summary['modules_added']}",
        f"- Módulos ausentes: {summary['modules_removed']}",
        f"- Campos modificados: {summary['fields_changed']}",
        f"- Relaciones diferentes: {summary['relationships_added'] + summary['relationships_removed'] + summary['relationships_changed']}",
        f"- Layouts diferentes: {summary['layouts_added'] + summary['layouts_removed'] + summary['layouts_changed']}",
        f"- Valores picklist diferentes: {summary['picklists_changed']}",
        f"- Cambios críticos: {summary['critical_changes']}",
        "",
        "## Cambios críticos",
        "",
    ]
    if comparison["critical_changes"]:
        for item in comparison["critical_changes"]:
            identity = ".".join(str(value) for value in item.get("identity", ()))
            if not identity and isinstance(item.get("relationship"), dict):
                relationship = item["relationship"]
                identity = ".".join((
                    str(relationship.get("source_module", "")),
                    str(relationship.get("source_field_api_name", "")),
                )).strip(".")
            lines.append(f"- `{identity}`: {item.get('change', 'changed')}.")
    else:
        lines.append("- No se detectaron cambios críticos.")

    by_module: dict[str, list[str]] = defaultdict(list)
    for item in comparison["modules"]["added"]:
        by_module[item.get("api_name", "sin_api_name")].append("Módulo agregado.")
    for item in comparison["modules"]["removed"]:
        by_module[item.get("api_name", "sin_api_name")].append("Módulo eliminado.")
    for item in comparison["modules"]["changed"]:
        by_module[item["identity"][0]].append(
            "Metadata del módulo modificada: " + ", ".join(item["changes"])
        )
    for item in comparison["fields"]:
        module, field = item["identity"]
        by_module[module].append(f"Campo `{field}`: {item['change']}.")
    for category in ("added", "removed", "changed"):
        for item in comparison["relationships"][category]:
            if category == "changed":
                module, field = item["identity"]
            else:
                module = item.get("source_module", "sin_modulo")
                field = item.get("source_field_api_name", "sin_campo")
            by_module[module].append(f"Relación `{field}`: {category}.")
    for item in comparison["picklists"]:
        module, field, value = item["identity"]
        by_module[module].append(
            f"Picklist `{field}` valor `{value}`: {item['change']}."
        )

    lines.extend(["", "## Cambios por módulo", ""])
    if not by_module:
        lines.append("- Los snapshots son semánticamente iguales.")
    else:
        for module in sorted(by_module, key=str.casefold):
            lines.extend([f"### {module}", ""])
            lines.extend(f"- {message}" for message in by_module[module])
            lines.append("")
    lines.extend([
        "## Criterio",
        "",
        "La comparación usa API names e identificadores técnicos disponibles. Los labels visuales no determinan por sí solos la identidad ni la criticidad.",
        "",
    ])
    return "\n".join(lines)


def render_model_markdown(snapshot: dict[str, Any]) -> str:
    modules = snapshot["modules"]
    fields_count = Counter(item["module_api_name"] for item in snapshot["fields"])
    layouts_count = Counter(item["module_api_name"] for item in snapshot["layouts"])
    outgoing = Counter(item["source_module"] for item in snapshot["relationships"])
    incoming = Counter(
        item["target_module_api_name"] for item in snapshot["relationships"]
        if item.get("resolved") and item.get("target_module_api_name")
    )
    subforms = Counter(item["parent_module"] for item in snapshot["subforms"])
    related = Counter(item["source_module"] for item in snapshot["related_lists"])
    manifest = snapshot["manifest"]
    lines = [
        "# Modelo Maestro Zoho A&S",
        "",
        f"- Perfil fuente: `{manifest.get('profile', '')}`",
        f"- Entorno confirmado: `{manifest.get('environment', '')}`",
        f"- Snapshot: `{manifest.get('semantic_digest', '')}`",
        "- Fuente: metadata Zoho exclusivamente; no contiene registros.",
        "",
        "## Inventario técnico",
        "",
        "| Módulo | API Name | ID | Campos | Layouts | Lookups salientes | Relaciones entrantes | Subformularios | Related Lists |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for module in modules:
        api_name = module.get("api_name", "")
        lines.append(
            f"| {module.get('label') or module.get('plural_label') or module.get('module_name') or 'Sin label'} "
            f"| `{api_name}` | `{module.get('id', '')}` | {fields_count[api_name]} "
            f"| {layouts_count[api_name]} | {outgoing[api_name]} | {incoming[api_name]} "
            f"| {subforms[api_name]} | {related[api_name]} |"
        )

    lines.extend(["", "## Dominios funcionales clave", ""])
    functional_names = (
        "Personas", "Empresas", "Pólizas", "Asegurados", "Riesgos",
        "Coberturas", "Siniestros", "Cartera", "Renovaciones",
    )
    normalized_modules = [
        (module, " ".join(str(module.get(key) or "") for key in (
            "label", "plural_label", "singular_label", "module_name"
        )).casefold())
        for module in modules
    ]
    for functional_name in functional_names:
        matches = [
            module for module, labels in normalized_modules
            if functional_name.casefold() in labels
        ]
        lines.extend([f"### {functional_name}", ""])
        if len(matches) == 1:
            candidate = matches[0]
            lines.append(
                f"- Candidato por label de metadata: `{candidate.get('api_name', '')}`."
            )
            lines.append("- Estado: pendiente de confirmación funcional A&S.")
        elif len(matches) > 1:
            candidates = ", ".join(f"`{item.get('api_name', '')}`" for item in matches)
            lines.append(f"- Candidatos de metadata: {candidates}.")
            lines.append("- Estado: pendiente de resolución; no existe asociación inequívoca.")
        else:
            lines.append("- Estado: pendiente de resolución; no existe asociación inequívoca en metadata.")
        lines.append("")

    lines.extend([
        "## Garantías",
        "",
        "- El documento fue generado desde el snapshot indicado.",
        "- No se asumió que Empresa corresponde a `Accounts` o `Persona_juridica`.",
        "- Los destinos de lookup no resueltos permanecen explícitamente pendientes.",
        "- No se consultaron ni incluyeron registros CRM.",
        "",
    ])
    return "\n".join(lines)
