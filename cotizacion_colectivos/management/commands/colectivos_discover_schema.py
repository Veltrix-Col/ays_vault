from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from integrations.zoho import get_zoho
from integrations.zoho.exceptions import ZohoError

from cotizacion_colectivos.constants import DISCOVERY_MODULES, SANDBOX_PROFILE
from cotizacion_colectivos.discovery import (
    atomic_text,
    build_discovery_markdown,
    build_relationships,
    build_search_candidates,
    generated_timestamp,
    serialize_field,
    write_json,
)


class Command(BaseCommand):
    help = "Descubre metadatos para Colectivos exclusivamente en Zoho Sandbox."

    def add_arguments(self, parser):
        parser.add_argument("--profile", default=SANDBOX_PROFILE)
        parser.add_argument(
            "--output-dir", default="artifacts/zoho/colectivos"
        )

    def handle(self, *args, **options):
        profile = options["profile"].strip().lower()
        if profile != SANDBOX_PROFILE:
            raise CommandError("Este comando admite exclusivamente --profile sandbox.")

        try:
            zoho = get_zoho(profile=SANDBOX_PROFILE)
            organization = zoho.organization.get()
            modules = zoho.metadata.list_modules()
        except ZohoError as exc:
            raise CommandError(
                f"No fue posible consultar metadatos de Sandbox ({exc.category})."
            ) from exc

        if zoho.profile != SANDBOX_PROFILE or organization.environment != SANDBOX_PROFILE:
            raise CommandError("Zoho no confirmo el entorno Sandbox solicitado.")

        index = {module.api_name: module for module in modules}
        selected = tuple(
            index[name] for name in DISCOVERY_MODULES if name in index
        )
        fields_by_module = {}
        failures: dict[str, str] = {}
        for module in selected:
            try:
                fields_by_module[module.api_name] = zoho.metadata.list_fields(
                    module.api_name
                )
            except ZohoError as exc:
                failures[module.api_name] = exc.category

        relationships = build_relationships(fields_by_module)
        candidates = build_search_candidates(fields_by_module)
        generated_at = generated_timestamp()
        header = {
            "generated_at": generated_at,
            "profile": SANDBOX_PROFILE,
            "environment": SANDBOX_PROFILE,
            "content": "metadata_only",
        }
        target = Path(options["output_dir"]).expanduser().resolve()
        write_json(
            target / "modules.json",
            {
                **header,
                "modules": [asdict(module) for module in selected],
                "missing_modules": [
                    name for name in DISCOVERY_MODULES if name not in index
                ],
                "failures": failures,
            },
        )
        write_json(
            target / "fields.json",
            {
                **header,
                "fields": {
                    module: [serialize_field(field) for field in fields]
                    for module, fields in sorted(fields_by_module.items())
                },
            },
        )
        write_json(
            target / "relationships.json",
            {**header, "relationships": relationships},
        )
        write_json(
            target / "search_candidates.json",
            {**header, "candidates": candidates},
        )
        atomic_text(
            target / "discovery.md",
            build_discovery_markdown(
                modules=selected,
                fields_by_module=fields_by_module,
                failures=failures,
                relationships=relationships,
                candidates=candidates,
                generated_at=generated_at,
            ),
        )
        self.stdout.write(
            self.style.SUCCESS(
                "Descubrimiento de Sandbox generado: "
                f"{len(selected)} modulos, {len(fields_by_module)} consultados, "
                f"{len(failures)} sin Fields API."
            )
        )
        self.stdout.write(f"Reporte de metadatos: {target}")
        self.stdout.write("No se consultaron registros ni Produccion.")

