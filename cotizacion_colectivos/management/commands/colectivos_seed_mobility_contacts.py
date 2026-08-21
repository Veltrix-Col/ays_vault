from __future__ import annotations

import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError
from integrations.zoho import get_zoho
from integrations.zoho.exceptions import ZohoError
from cotizacion_colectivos.management.commands.zoho_create_test_task import _safe_zoho_diagnostic

from cotizacion_colectivos.services.person_contract import (
    ContactPublicationRejected, ContactPublicationUncertain,
    ContactPublishingDisabled, GuardedSandboxContactPublisher,
    resolve_contact_by_document,
)
from cotizacion_colectivos.services.common import ColectivosServiceError
from integrations.zoho.settings import ZohoSettings


CONFIRMATION = "SANDBOX_MOBILITY_CONTACT_SEED"
CONTACTS = tuple(
    {
        "alias": f"VELTRIX TEST MOVILIDAD {number:03d}",
        "First_Name": "VELTRIX TEST",
        "Last_Name": f"MOVILIDAD {number:03d}",
        "Tipo_ID": "CC",
        "N_mero_de_ID": f"990000001{number:03d}",
        "Date_of_Birth": "1990-01-01",
        "Email": f"veltrix.test.movilidad.{number:03d}@example.test",
        "Phone": f"3000000{number:03d}",
    }
    for number in range(1, 6)
)


def _mask(value: object) -> str:
    text = str(value or "")
    return f"***{text[-4:]}" if len(text) >= 4 else "***"


class Command(BaseCommand):
    help = "Prepara o crea exactamente cinco Contacts sintéticos en Sandbox."

    def add_arguments(self, parser):
        parser.add_argument("--confirm", default="")

    def _guard_profile(self):
        if str(getattr(settings, "ZOHO_ACTIVE_PROFILE", "")).strip().lower() != "sandbox":
            raise CommandError("El perfil activo no es Sandbox.")
        if not getattr(settings, "ZOHO_SANDBOX_WRITE_ENABLED", False):
            raise CommandError("La escritura Sandbox está deshabilitada.")
        if not ZohoSettings.from_django("sandbox").write_enabled:
            raise CommandError("La configuración efectiva de Sandbox no permite WRITE.")

    def handle(self, *args, **options):
        self._guard_profile()
        dry_run = str(options.get("confirm") or "").strip() != CONFIRMATION
        if dry_run:
            self.stdout.write(json.dumps({
                "mode": "dry-run", "profile": "sandbox", "module": "Contacts",
                "planned": len(CONTACTS), "writes": 0,
                "contacts": [
                    {"alias": item["alias"], "document": _mask(item["N_mero_de_ID"]), "status": "Cliente"}
                    for item in CONTACTS
                ],
            }, ensure_ascii=False, indent=2))
            self.stdout.write("NO se realizó WRITE.")
            return
        try:
            publisher = GuardedSandboxContactPublisher(
                confirmation=options["confirm"],
                feature_flag="COLECTIVOS_MOBILITY_CONTACT_SEED_ENABLED",
                confirmation_setting="COLECTIVOS_MOBILITY_CONTACT_SEED_CONFIRMATION",
                expected_confirmation=CONFIRMATION,
            )
        except (ContactPublishingDisabled, ValidationError) as exc:
            raise CommandError(str(exc)) from exc

        try:
            # Direct Sandbox facade: the seed only needs Contacts READ/CREATE;
            # it must not depend on the broader organization preflight.
            zoho = get_zoho(profile="sandbox")
        except ZohoError as exc:
            raise CommandError(_safe_zoho_diagnostic(exc)) from exc

        results = []
        for item in CONTACTS:
            try:
                # Preflight idempotency check; publisher repeats it under its lock.
                existing = resolve_contact_by_document(
                    document=item["N_mero_de_ID"], document_type=item["Tipo_ID"], zoho=zoho
                )
                if existing["status"] == "FOUND":
                    results.append({"alias": item["alias"], "result": "ALREADY_EXISTS", "contact_id": existing.get("record_id", "")})
                    continue
                if existing["status"] != "NOT_FOUND":
                    results.append({"alias": item["alias"], "result": "BLOCKED", "contact_id": ""})
                    continue
                result = publisher.create(item, status="Cliente", zoho=zoho)
                results.append({"alias": item["alias"], "result": "CREATED", "contact_id": result["record_id"]})
            except ContactPublicationUncertain as exc:
                results.append({"alias": item["alias"], "result": "UNCERTAIN", "contact_id": ""})
                self.stdout.write(json.dumps(results, ensure_ascii=False))
                raise CommandError(str(exc)) from exc
            except ContactPublicationRejected:
                results.append({"alias": item["alias"], "result": "BLOCKED", "contact_id": ""})
            except ZohoError as exc:
                results.append({"alias": item["alias"], "result": "BLOCKED", "contact_id": ""})
                raise CommandError(_safe_zoho_diagnostic(exc)) from exc
            except ColectivosServiceError as exc:
                results.append({"alias": item["alias"], "result": "BLOCKED", "contact_id": ""})
                raise CommandError(f"Contacts preflight bloqueado ({exc.code}).") from exc
            except Exception as exc:
                results.append({"alias": item["alias"], "result": "BLOCKED", "contact_id": ""})
                raise CommandError(f"Falló el seed de Contacts ({type(exc).__name__}).") from exc
        self.stdout.write(json.dumps(results, ensure_ascii=False, indent=2))
