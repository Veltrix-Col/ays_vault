from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from integrations.zoho import get_zoho
from integrations.zoho.exceptions import ZohoError

from cotizacion_colectivos.constants import (
    CONTACTS_PROFILE_FIELDS,
    CONTACTS_PROFILE_MAX_RECORDS,
    CONTACTS_PROFILE_MODULE,
    CONTACTS_PROFILE_PAGE_SIZE,
    SANDBOX_PROFILE,
)
from cotizacion_colectivos.discovery import atomic_text
from cotizacion_colectivos.profiling import (
    ContactsProfileAccumulator,
    render_contacts_profile_markdown,
)


REPORT_PATH = Path("docs/cotizacion_colectivos/contacts_profile_analysis.md")


class Command(BaseCommand):
    help = "Genera un perfil agregado y seguro de Contacts exclusivamente en Sandbox."

    def add_arguments(self, parser):
        parser.add_argument("--profile", default=SANDBOX_PROFILE)
        parser.add_argument("--allow-real-read", action="store_true")

    def handle(self, *args, **options):
        profile = options["profile"].strip().lower()
        if profile != SANDBOX_PROFILE:
            raise CommandError("Este diagnostico admite exclusivamente --profile sandbox.")
        if not options["allow_real_read"]:
            raise CommandError("Debe confirmar la lectura con --allow-real-read.")

        try:
            zoho = get_zoho(profile=SANDBOX_PROFILE)
            organization = zoho.organization.get()
        except ZohoError as exc:
            raise CommandError(f"No fue posible validar Sandbox ({exc.category}).") from exc
        if zoho.profile != SANDBOX_PROFILE or organization.environment != SANDBOX_PROFILE:
            raise CommandError("Zoho no confirmo el entorno Sandbox solicitado.")

        accumulator = ContactsProfileAccumulator()
        page_number = 1
        pages_processed = 0
        complete = False
        stop_reason = ""

        while accumulator.processed < CONTACTS_PROFILE_MAX_RECORDS:
            limit = min(
                CONTACTS_PROFILE_PAGE_SIZE,
                CONTACTS_PROFILE_MAX_RECORDS - accumulator.processed,
            )
            try:
                page = zoho.records.list(
                    module=CONTACTS_PROFILE_MODULE,
                    fields=CONTACTS_PROFILE_FIELDS,
                    page=page_number,
                    limit=limit,
                )
            except ZohoError as exc:
                if accumulator.processed == 0:
                    raise CommandError(
                        f"No fue posible perfilar Contacts ({exc.category})."
                    ) from exc
                stop_reason = f"api_error_{exc.category}"
                break

            pages_processed += 1
            for record in page.records[:limit]:
                accumulator.consume(record)
            if not page.more_records:
                complete = True
                break
            if not page.records:
                stop_reason = "pagina_vacia_con_continuacion"
                break
            page_number += 1
        else:
            stop_reason = "limite_defensivo_alcanzado"

        result = accumulator.result(
            complete=complete,
            pages=pages_processed,
            stop_reason=stop_reason,
        )
        target = Path(settings.BASE_DIR) / REPORT_PATH
        atomic_text(target, render_contacts_profile_markdown(result))

        person_counts = result["person_counts"]
        self.stdout.write("Perfil agregado de Contacts en Zoho Sandbox:")
        self.stdout.write(f"- Registros procesados: {result['processed']}")
        self.stdout.write(f"- Resultado: {'completo' if complete else 'parcial'}")
        self.stdout.write(f"- Persona natural: {person_counts.get('natural', 0)}")
        self.stdout.write(f"- Persona juridica: {person_counts.get('legal', 0)}")
        self.stdout.write(f"- Informe agregado: {REPORT_PATH.as_posix()}")
        self.stdout.write("No se almacenaron registros ni se consulto Produccion.")
