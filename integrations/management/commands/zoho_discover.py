from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from integrations.zoho.discovery import DiscoveryService, SnapshotStore
from integrations.zoho.discovery.service import DiscoveryConfigurationError
from integrations.zoho.exceptions import ZohoError


class Command(BaseCommand):
    help = "Genera un snapshot versionado de metadata Zoho CRM en modo solo lectura."

    def add_arguments(self, parser):
        parser.add_argument(
            "--profile", required=True, choices=("sandbox", "production"),
            help="Perfil explícito que será consultado.",
        )
        parser.add_argument("--output-dir", default="docs/zoho")

    def handle(self, *args, **options):
        profile = options["profile"].strip().casefold()
        target = Path(options["output_dir"]).expanduser().resolve()
        try:
            snapshot = DiscoveryService(profile=profile).discover()
            result = SnapshotStore(target).save(snapshot)
        except DiscoveryConfigurationError as exc:
            raise CommandError(str(exc)) from exc
        except ZohoError as exc:
            raise CommandError(
                f"No fue posible completar el discovery ({exc.category})."
            ) from exc
        except (OSError, TypeError, ValueError) as exc:
            raise CommandError(
                "No fue posible completar el discovery de metadata."
            ) from exc
        except Exception as exc:
            raise CommandError(
                "No fue posible completar el discovery de metadata."
            ) from exc
        manifest = snapshot["manifest"]
        self.stdout.write("Zoho discovery v2")
        self.stdout.write(f"Profile: {manifest['profile']}")
        self.stdout.write(f"Environment: {manifest['environment']}")
        self.stdout.write(f"Backend: {manifest['backend']}")
        self.stdout.write("Mode: read-only metadata")
        self.stdout.write("")
        self.stdout.write(f"Modules discovered: {manifest['modules_total']}")
        self.stdout.write(f"Fields OK: {manifest['modules_fields_ok']}")
        self.stdout.write(f"Fields with errors: {manifest['modules_fields_failed']}")
        self.stdout.write(f"Relationships: {manifest['relationships_total']}")
        self.stdout.write(f"Subforms: {manifest['subforms_total']}")
        self.stdout.write(f"Layouts: {manifest['layouts_total']}")
        self.stdout.write("")
        self.stdout.write("Snapshot unchanged:" if not result["changed"] else "Snapshot written:")
        self.stdout.write(str(result["path"]))
        self.stdout.write("No records were requested or stored.")
