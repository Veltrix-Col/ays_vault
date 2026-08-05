from __future__ import annotations

import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from cotizacion_colectivos.excel import build_current_policy_workbook
from cotizacion_colectivos.representative_policies import REPRESENTATIVE_POLICIES
from cotizacion_colectivos.services.common import escape_criteria_value, sign_record_id
from cotizacion_colectivos.services.mappings import POLICY_DETAIL_FIELDS
from cotizacion_colectivos.services.policies import PolicyService
from cotizacion_colectivos.services.preparations import invalidate_policy_preparation
from cotizacion_colectivos.zoho import get_colectivos_zoho


class Command(BaseCommand):
    help = "Mide hidratación remota y navegación local del Workspace sin exponer datos."

    def add_arguments(self, parser):
        parser.add_argument("--profile", required=True, choices=("production",))
        parser.add_argument(
            "--policy", choices=tuple(REPRESENTATIVE_POLICIES),
            default=next(iter(REPRESENTATIVE_POLICIES)),
        )
        parser.add_argument("--allow-production-read", action="store_true")

    def handle(self, *args, **options):
        if not options["allow_production_read"]:
            raise CommandError("Debe confirmar la lectura de Production.")
        if getattr(settings, "ZOHO_ACTIVE_PROFILE", "") != "production":
            raise CommandError("ZOHO_ACTIVE_PROFILE debe ser production.")

        environment_timings = {}
        facade = get_colectivos_zoho(timings=environment_timings)
        locate_started = time.perf_counter()
        page = facade.search.by_criteria(
            module="Polizas",
            criteria=f"(Name:equals:{escape_criteria_value(options['policy'])})",
            fields=POLICY_DETAIL_FIELDS,
            page=1,
            limit=2,
        )
        locate_ms = self._elapsed(locate_started)
        if len(page.records) != 1:
            raise CommandError("La póliza autorizada no produjo una coincidencia inequívoca.")
        token = sign_record_id(str(page.records[0].get("id") or ""), "policy")

        remote = PolicyService(zoho=facade, use_preparation=True)
        remote.timings.update(environment_timings)
        if not environment_timings.get("organization_cache_hit", 0):
            remote.timings["organization_queries"] = 1
            remote.timings["remote_queries"] = 1
        initial_started = time.perf_counter()
        detail, members = remote.group(token, refresh=True)
        initial_ms = self._elapsed(initial_started)
        invalidate_policy_preparation(
            token=token, profile="production", backend=remote.backend,
            source_kind=None,
        )

        local = PolicyService()
        restore_started = time.perf_counter()
        local_detail, local_members = local.group(token)
        restore_ms = self._elapsed(restore_started)
        warm_started = time.perf_counter()
        local.group(token)
        warm_ms = self._elapsed(warm_started)
        excel_started = time.perf_counter()
        build_current_policy_workbook(token, service=local)
        excel_ms = self._elapsed(excel_started)

        if detail.branch_code != local_detail.branch_code or len(members) != len(local_members):
            raise CommandError("El Workspace local no coincide con la hidratación remota.")
        self.stdout.write("Benchmark seguro del Workspace (milisegundos)")
        self.stdout.write(f"localización_controlada={locate_ms}")
        self.stdout.write(f"hidratación_remota={initial_ms}")
        for key in (
            "facade_ms", "organization_ms", "policy_lookup_ms", "policy_search_ms", "risks1_query_ms",
            "contacts_query_ms", "risks_query_ms", "dto_ms", "grouping_ms",
            "snapshot_serialization_ms", "encryption_ms", "workspace_persistence_ms",
            "remote_queries", "records_queries", "search_queries", "coql_queries",
            "insured_pages", "contacts_batches", "risks_batches",
        ):
            self.stdout.write(f"{key}={remote.timings.get(key, 0)}")
        self.stdout.write(f"restauración_base_local={restore_ms}")
        self.stdout.write(f"lectura_cache_local={warm_ms}")
        self.stdout.write(f"excel_desde_workspace={excel_ms}")
        self.stdout.write(f"consultas_zoho_despues_de_hidratar={local.timings.get('remote_queries', 0)}")
        self.stdout.write("No se imprimieron pólizas, IDs, nombres, documentos, tokens ni respuestas.")
        token = ""

    @staticmethod
    def _elapsed(started):
        return round((time.perf_counter() - started) * 1000)
