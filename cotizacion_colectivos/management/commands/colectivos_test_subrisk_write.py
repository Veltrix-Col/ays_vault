from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError
from integrations.zoho.exceptions import ZohoError

from cotizacion_colectivos.management.commands.zoho_create_test_task import _safe_zoho_diagnostic
from cotizacion_colectivos.services.subrisk_sandbox import (
    SUBRISK_CONFIRMATION, SubriskPublicationRejected, SubriskPublicationUncertain,
    SubriskPublishingDisabled, build_subrisk_payload, create_subrisk_sandbox,
    sanitized_subrisk_dry_run,
)


class Command(BaseCommand):
    help = "Ensayo controlado de un único Riesgos1 en Sandbox (dry-run por defecto)."

    def add_arguments(self, parser):
        parser.add_argument("--profile", default="sandbox")
        parser.add_argument("--policy-id", required=True)
        parser.add_argument("--affiliate-contact-id", required=True)
        parser.add_argument("--insured-contact-id", required=True)
        parser.add_argument("--subrisk-name", required=True)
        parser.add_argument("--entry-date", required=True)
        parser.add_argument("--plan", default="")
        parser.add_argument("--confirm", default="")

    def handle(self, *args, **options):
        profile = str(options["profile"] or "").strip().lower()
        if profile != "sandbox":
            raise CommandError("Este comando admite exclusivamente --profile sandbox.")
        try:
            payload = build_subrisk_payload(
                policy_id=options["policy_id"],
                affiliate_contact_id=options["affiliate_contact_id"],
                insured_contact_id=options["insured_contact_id"],
                subrisk_name=options["subrisk_name"], entry_date=options["entry_date"],
                plan=options.get("plan", ""),
            )
        except ValidationError as exc:
            raise CommandError(str(exc)) from exc
        if str(options.get("confirm") or "").strip() != SUBRISK_CONFIRMATION:
            self.stdout.write(json.dumps(sanitized_subrisk_dry_run(payload, profile=profile), ensure_ascii=False, indent=2))
            self.stdout.write("NO se realizó WRITE.")
            return
        try:
            result = create_subrisk_sandbox(payload, profile=profile, confirmation=options["confirm"])
        except SubriskPublicationUncertain as exc:
            raise CommandError(str(exc)) from exc
        except ZohoError as exc:
            raise CommandError(_safe_zoho_diagnostic(exc).replace("No se creó la Task", "No se creó el Riesgos1", 1)) from exc
        except (SubriskPublishingDisabled, SubriskPublicationRejected) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS("CREATE confirmado"))
        self.stdout.write("Module: Riesgos1")
        self.stdout.write(f"Remote ID: {result['record_id']}")
        self.stdout.write(f"Subriesgo: {payload['Name']}")
        self.stdout.write("Profile: sandbox")
