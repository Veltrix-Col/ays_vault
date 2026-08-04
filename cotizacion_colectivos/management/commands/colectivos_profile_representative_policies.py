from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from integrations.zoho import get_zoho
from integrations.zoho.exceptions import ZohoError

from cotizacion_colectivos.representative_policies import (
    PRODUCTION_PROFILE,
    REPRESENTATIVE_POLICIES,
    run_representative_policy_profile,
    save_representative_policy_profile,
)


class Command(BaseCommand):
    help = "Radiografía segura de cinco pólizas representativas autorizadas."

    def add_arguments(self, parser):
        parser.add_argument("--profile", required=True)
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--policy")
        group.add_argument("--all", action="store_true")
        parser.add_argument("--allow-production-read", action="store_true")

    def handle(self, *args, **options):
        profile = str(options["profile"] or "").strip().lower()
        if profile != PRODUCTION_PROFILE:
            raise CommandError("Este diagnóstico admite exclusivamente --profile production.")
        if not options["allow_production_read"]:
            raise CommandError("Debe confirmar con --allow-production-read.")
        policy = str(options.get("policy") or "").strip()
        if policy and policy not in REPRESENTATIVE_POLICIES:
            raise CommandError("La póliza solicitada no pertenece a la allowlist autorizada.")
        selected = tuple(REPRESENTATIVE_POLICIES) if options["all"] else (policy,)
        try:
            zoho = get_zoho(profile=PRODUCTION_PROFILE)
            organization = zoho.organization.get()
            if zoho.profile != PRODUCTION_PROFILE or organization.environment != PRODUCTION_PROFILE:
                raise CommandError("Zoho no confirmó el entorno Production solicitado.")
            result = run_representative_policy_profile(zoho, selected)
        except CommandError:
            raise
        except ZohoError as exc:
            raise CommandError(f"No fue posible completar la radiografía ({exc.category}).") from exc
        save_representative_policy_profile(result)
        self.stdout.write("Radiografía agregada de pólizas representativas:")
        for item in result["policies"]:
            self.stdout.write(f"- {item['branch']}: {item['status']}; coincidencias={item['matches']}; Riesgos1={item.get('insured', {}).get('processed', 0)}; Riesgos={item.get('risks', {}).get('processed', 0)}")
        self.stdout.write("Solo Production, solo lectura; no se almacenaron valores personales ni respuestas crudas.")
