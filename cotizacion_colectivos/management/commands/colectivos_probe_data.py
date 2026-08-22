from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from integrations.zoho import get_zoho
from integrations.zoho.exceptions import ZohoError, ZohoValidationError
from integrations.zoho.records import validate_api_name

from cotizacion_colectivos.constants import (
    DEFAULT_PROBE_LIMIT,
    MAX_PROBE_LIMIT,
    SANDBOX_PROFILE,
)
from cotizacion_colectivos.probe import summarize_records


class Command(BaseCommand):
    help = "Muestrea de forma enmascarada hasta 10 registros de Zoho Sandbox."

    def add_arguments(self, parser):
        parser.add_argument("--profile", default=SANDBOX_PROFILE)
        parser.add_argument("--module", required=True)
        parser.add_argument("--fields", nargs="+", required=True)
        parser.add_argument("--limit", type=int, default=DEFAULT_PROBE_LIMIT)
        parser.add_argument("--allow-real-read", action="store_true")

    def handle(self, *args, **options):
        profile = options["profile"].strip().lower()
        if profile != SANDBOX_PROFILE:
            raise CommandError("El muestreo admite exclusivamente --profile sandbox.")
        if not options["allow_real_read"]:
            raise CommandError("Debe confirmar la lectura con --allow-real-read.")
        limit = options["limit"]
        if limit < 1 or limit > MAX_PROBE_LIMIT:
            raise CommandError(f"--limit debe estar entre 1 y {MAX_PROBE_LIMIT}.")

        try:
            module = validate_api_name(options["module"].strip(), label="modulo")
            fields = tuple(
                validate_api_name(field.strip().rstrip(","), label="campo")
                for raw in options["fields"]
                for field in raw.split(",")
                if field.strip().rstrip(",")
            )
        except ZohoValidationError as exc:
            raise CommandError("El modulo o los campos solicitados no son validos.") from exc
        if not fields:
            raise CommandError("Debe indicar al menos un campo.")
        if len(fields) != len(set(fields)):
            raise CommandError("No repita campos en --fields.")

        try:
            zoho = get_zoho(profile=SANDBOX_PROFILE)
            organization = zoho.organization.get()
            modules = zoho.metadata.list_modules()
        except ZohoError as exc:
            raise CommandError(
                f"No fue posible validar Sandbox ({exc.category})."
            ) from exc
        if zoho.profile != SANDBOX_PROFILE or organization.environment != SANDBOX_PROFILE:
            raise CommandError("Zoho no confirmo el entorno Sandbox solicitado.")

        module_index = {item.api_name: item for item in modules}
        if module not in module_index:
            raise CommandError("El modulo solicitado no existe en Modules API.")
        try:
            metadata = zoho.metadata.list_fields(module)
        except ZohoError as exc:
            raise CommandError(
                f"No fue posible validar los campos solicitados ({exc.category})."
            ) from exc
        allowed_fields = {field.api_name for field in metadata}
        if any(field not in allowed_fields for field in fields):
            raise CommandError("Uno o mas campos no existen en Fields API.")

        try:
            page = zoho.records.list(
                module=module,
                fields=fields,
                page=1,
                limit=limit,
            )
        except ZohoError as exc:
            raise CommandError(
                f"No fue posible realizar el muestreo ({exc.category})."
            ) from exc
        summaries = summarize_records(page.records[:limit], fields)
        self.stdout.write("Muestreo seguro de Zoho Sandbox (valores resumidos):")
        for position, record in enumerate(summaries, start=1):
            self.stdout.write(f"Registro {position}:")
            for field in fields:
                self.stdout.write(f"- {field}: {record[field]}")
        if not summaries:
            self.stdout.write("Sin registros en la muestra.")
        self.stdout.write("No se almacenaron registros ni se consulto Produccion.")
