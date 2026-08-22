from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from integrations.zoho import get_zoho
from integrations.zoho.exceptions import ZohoError

from cotizacion_colectivos.task_contract_discovery import DirectedTaskDiscovery


class Command(BaseCommand):
    help = "Explora de forma dirigida y sanitizada el contrato Tasks en Sandbox."

    def add_arguments(self, parser):
        parser.add_argument("--profile", default="sandbox")
        parser.add_argument("--allow-real-read", action="store_true")

    def handle(self, *args, **options):
        profile = str(options["profile"] or "").strip().lower()
        if profile != "sandbox":
            raise CommandError("Este diagnóstico admite exclusivamente --profile sandbox.")
        if not options["allow_real_read"]:
            raise CommandError("Debe confirmar la lectura con --allow-real-read.")
        try:
            result = DirectedTaskDiscovery(get_zoho(profile="sandbox")).discover()
        except (ZohoError, ValueError) as exc:
            category = getattr(exc, "category", "environment")
            raise CommandError(f"No fue posible completar la lectura Sandbox ({category}).") from exc
        self.stdout.write(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        self.stdout.write("No se persistieron respuestas, no se escribió en Zoho y no se consultó Producción.")

