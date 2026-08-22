from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from integrations.zoho.exceptions import ZohoError
from cotizacion_colectivos.management.commands.zoho_create_test_task import (
    _safe_zoho_diagnostic,
)

from cotizacion_colectivos.services.person_contract import (
    ContactPublicationRejected,
    ContactPublicationUncertain,
    ContactPublishingDisabled,
    get_contacts_publisher,
)


def _safe_contact_diagnostic(exc: ZohoError) -> str:
    """Reuse the allowlisted SDK metadata diagnostic without exposing payloads."""
    return _safe_zoho_diagnostic(exc).replace(
        "No se creó la Task (", "No se creó el Contact (", 1,
    )


class Command(BaseCommand):
    help = "Crea exactamente un Contact sintético en Sandbox mediante barreras explícitas."

    def add_arguments(self, parser):
        parser.add_argument("--profile", required=True)
        parser.add_argument("--confirm", default="")

    def handle(self, *args, **options):
        profile = str(options["profile"] or "").strip().lower()
        if profile != "sandbox":
            raise CommandError("Este comando admite exclusivamente --profile sandbox.")
        try:
            publisher = get_contacts_publisher(profile=profile, confirmation=options.get("confirm", ""))
            result = publisher.create({
                "First_Name": "Veltrix QA",
                "Last_Name": "CV CONTACT 001",
                "Tipo_ID": "CC",
                "N_mero_de_ID": "990000001",
                "Email": "veltrix.qa.contact001@example.test",
            })
        except ContactPublicationUncertain as exc:
            raise CommandError("Contact no confirmado; requiere conciliación.") from exc
        except ZohoError as exc:
            raise CommandError(_safe_contact_diagnostic(exc)) from exc
        except (ContactPublishingDisabled, ContactPublicationRejected) as exc:
            raise CommandError(f"No se creó el Contact ({type(exc).__name__}).") from exc
        self.stdout.write(self.style.SUCCESS("Contact creado correctamente"))
        self.stdout.write(f"Profile: sandbox")
        self.stdout.write(f"Module: {result['module']}")
        self.stdout.write(f"Record ID: {result['record_id']}")
        self.stdout.write(f"Succeeded: {result['succeeded']}")
        self.stdout.write(f"Code: {result.get('code') or 'unknown'}")
