from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from integrations.zoho.discovery import (
    compare_snapshots,
    load_snapshot,
    render_comparison_markdown,
    render_model_markdown,
)
from integrations.zoho.discovery.storage import atomic_text, write_json


class Command(BaseCommand):
    help = "Compara dos snapshots Zoho locales sin inicializar la integración."

    def add_arguments(self, parser):
        parser.add_argument("--left", required=True, choices=("sandbox", "production"))
        parser.add_argument("--right", required=True, choices=("sandbox", "production"))
        parser.add_argument("--root", default="docs/zoho")

    def handle(self, *args, **options):
        left_profile = options["left"]
        right_profile = options["right"]
        if left_profile == right_profile:
            raise CommandError("Los perfiles de comparación deben ser diferentes.")
        root = Path(options["root"]).expanduser().resolve()
        try:
            left = load_snapshot(root / left_profile / "latest")
            right = load_snapshot(root / right_profile / "latest")
        except (OSError, KeyError, ValueError) as exc:
            raise CommandError("No existen snapshots locales válidos para comparar.") from exc
        if left["manifest"].get("profile") != left_profile:
            raise CommandError("El snapshot izquierdo pertenece a otro perfil.")
        if right["manifest"].get("profile") != right_profile:
            raise CommandError("El snapshot derecho pertenece a otro perfil.")
        comparison = compare_snapshots(left, right)
        comparison_dir = root / "comparison"
        comparison_name = f"{left_profile}_vs_{right_profile}"
        write_json(comparison_dir / f"{comparison_name}.json", comparison)
        atomic_text(
            comparison_dir / f"{comparison_name}.md",
            render_comparison_markdown(comparison),
        )
        model_source = right if right_profile == "production" else left
        atomic_text(root / "MODEL.md", render_model_markdown(model_source))
        summary = comparison["summary"]
        self.stdout.write(self.style.SUCCESS("Comparación local Zoho generada."))
        self.stdout.write(f"Modules added: {summary['modules_added']}")
        self.stdout.write(f"Modules removed: {summary['modules_removed']}")
        self.stdout.write(f"Field changes: {summary['fields_changed']}")
        self.stdout.write(f"Critical changes: {summary['critical_changes']}")
        self.stdout.write(str(comparison_dir))
        self.stdout.write("No Zoho facade was initialized.")
