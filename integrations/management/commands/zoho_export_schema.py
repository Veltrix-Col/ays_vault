from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from integrations.zoho import get_zoho
from integrations.zoho.constants import API_VERSION
from integrations.zoho.diagnostics import (
    ModuleDiagnosticCategory,
    classify_failure,
)
from integrations.zoho.exceptions import ZohoError
from integrations.zoho.records import validate_api_name


def _atomic_text(path: Path, content: str) -> None:
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


def _json(path: Path, value: object) -> None:
    _atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


class Command(BaseCommand):
    help = "Exporta metadatos de Zoho CRM sin registros ni datos personales."

    def add_arguments(self, parser):
        parser.add_argument("--profile", default="")
        parser.add_argument("--module", dest="module", default="")
        parser.add_argument("--output-dir", default="artifacts/zoho")
        parser.add_argument("--no-relationships", action="store_true")

    def handle(self, *args, **options):
        target = Path(options["output_dir"]).expanduser().resolve()
        profile = options["profile"].strip() or None
        module_filter = options["module"].strip()
        if module_filter:
            validate_api_name(module_filter, label="módulo")
        try:
            zoho = get_zoho(profile=profile)
            organization = zoho.organization.get()
            modules = zoho.metadata.list_modules()
        except ZohoError as exc:
            raise CommandError(
                f"No fue posible consultar el esquema de Zoho ({exc.category})."
            ) from exc

        selected = [
            module
            for module in modules
            if not module_filter or module.api_name == module_filter
        ]
        if module_filter and not selected:
            raise CommandError("El módulo solicitado no existe en los metadatos de Zoho.")

        fields: dict[str, list[dict[str, object]]] = {}
        relationships: list[dict[str, object]] = []
        failures: list[dict[str, str]] = []
        for module in sorted(selected, key=lambda item: item.api_name.casefold()):
            try:
                module_fields = zoho.metadata.list_fields(module.api_name)
            except ZohoError as exc:
                classification = classify_failure(module, exc)
                failures.append(
                    {
                        "module": module.api_name,
                        "category": exc.category,
                        "classification": classification.value,
                        "zoho_code": exc.zoho_code,
                    }
                )
                if classification in {
                    ModuleDiagnosticCategory.PERMISSION_DENIED,
                    ModuleDiagnosticCategory.SCOPE_INSUFFICIENT,
                    ModuleDiagnosticCategory.TEMPORARY_ERROR,
                    ModuleDiagnosticCategory.UNKNOWN,
                }:
                    self.stderr.write(
                        f"No fue posible consultar {module.api_name} "
                        f"({classification.value})."
                    )
                continue
            fields[module.api_name] = [asdict(item) for item in module_fields]
            if not options["no_relationships"]:
                for item in module_fields:
                    if item.lookup or item.related_details:
                        relationships.append(
                            {
                                "module": module.api_name,
                                "field": item.api_name,
                                "lookup": item.lookup,
                                "related_details": item.related_details,
                            }
                        )

        generated_at = datetime.now(UTC).isoformat()
        selected_profile = getattr(zoho, "profile", profile or "production")
        selected_environment = getattr(zoho, "environment", "production")
        header = {
            "generated_at": generated_at,
            "api_version": API_VERSION,
            "profile": selected_profile,
            "environment": selected_environment,
            "organization": {
                "organization_id": organization.organization_id,
                "company_name": organization.company_name,
                "country": organization.country,
                "timezone": organization.timezone,
                "currency": organization.currency,
                "data_center": organization.data_center,
            },
            "failures": failures,
        }
        module_data = {
            **header,
            "modules": [asdict(item) for item in selected],
        }
        field_data = {**header, "fields": fields}
        relationship_data = {**header, "relationships": relationships}
        _json(target / "modules.json", module_data)
        _json(target / "fields.json", field_data)
        _json(target / "relationships.json", relationship_data)
        _atomic_text(
            target / "schema.md",
            self._markdown(header, selected, fields, relationships),
        )
        counts = Counter(item["classification"] for item in failures)
        summary = [
            f"Esquema Zoho exportado ({zoho.backend_name.upper()}):",
            f"- Perfil: {selected_profile}",
            f"- Entorno: {selected_environment}",
            f"- Módulos encontrados: {len(selected)}",
            f"- Consultados: {len(fields)}",
            f"- Omitidos: {len(failures)}",
            f"- Campos exportados: {sum(len(items) for items in fields.values())}",
        ]
        if failures:
            summary.extend(["", "Omisiones:"])
            for category in ModuleDiagnosticCategory:
                if category in {
                    ModuleDiagnosticCategory.SUPPORTED,
                    ModuleDiagnosticCategory.SDK_INCOMPATIBILITY,
                }:
                    continue
                summary.append(f"- {category.value}: {counts[category.value]}")
        self.stdout.write(self.style.SUCCESS("\n".join(summary)))

    @staticmethod
    def _markdown(header, modules, fields, relationships) -> str:
        lines = [
            "# Esquema Zoho CRM",
            "",
            f"- Generado: {header['generated_at']}",
            f"- API: CRM {header['api_version'].upper()}",
            f"- Organización: {header['organization']['company_name'] or 'Sin nombre'}",
            "- Contenido: metadatos exclusivamente; no incluye registros.",
            "",
            "## Módulos",
            "",
        ]
        for module in sorted(modules, key=lambda item: item.api_name.casefold()):
            lines.append(
                f"- `{module.api_name}` — {module.plural_label or module.module_name} "
                f"({len(fields.get(module.api_name, []))} campos)"
            )
        lines.extend(["", "## Relaciones", ""])
        if relationships:
            for relation in relationships:
                lines.append(f"- `{relation['module']}.{relation['field']}`")
        else:
            lines.append("- No se detectaron relaciones o fueron omitidas.")
        if header["failures"]:
            lines.extend(["", "## Consultas parciales", ""])
            for failure in header["failures"]:
                lines.append(
                    f"- `{failure['module']}`: {failure['category']}"
                )
        lines.append("")
        return "\n".join(lines)
