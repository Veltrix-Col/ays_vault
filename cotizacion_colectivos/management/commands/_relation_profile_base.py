from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from integrations.zoho import get_zoho
from integrations.zoho.exceptions import ZohoError

from cotizacion_colectivos.constants import SANDBOX_PROFILE
from cotizacion_colectivos.relation_profiling import (
    ProfileSpec,
    run_relation_profile,
    save_relation_profile,
)


class RelationProfileCommand(BaseCommand):
    spec: ProfileSpec

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
            if zoho.profile != SANDBOX_PROFILE or organization.environment != SANDBOX_PROFILE:
                raise CommandError("Zoho no confirmo el entorno Sandbox solicitado.")
            result = run_relation_profile(zoho, self.spec)
        except CommandError:
            raise
        except ZohoError as exc:
            raise CommandError(
                f"No fue posible completar el diagnostico ({exc.category})."
            ) from exc
        save_relation_profile(self.spec, result)
        self.stdout.write(f"Perfil agregado {self.spec.module} en Sandbox:")
        self.stdout.write(f"- Registros procesados: {result['processed']}")
        self.stdout.write(f"- Resultado: {'completo' if result['complete'] else 'parcial'}")
        for field, relation in result["relationships"].items():
            self.stdout.write(f"- {field} -> {relation['target']}: {relation['status']}")
        self.stdout.write("No se almacenaron registros ni se consulto Produccion.")
