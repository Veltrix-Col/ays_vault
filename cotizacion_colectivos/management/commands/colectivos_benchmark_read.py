from __future__ import annotations

import os
import time
from collections import defaultdict

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from integrations.zoho import get_zoho

from cotizacion_colectivos.representative_policies import REPRESENTATIVE_POLICIES
from cotizacion_colectivos.services.common import escape_criteria_value, sign_record_id
from cotizacion_colectivos.services.mappings import CONTACT_SEARCH_FIELDS, POLICY_DETAIL_FIELDS
from cotizacion_colectivos.services.policies import PolicyService


class Command(BaseCommand):
    help = "Compara lecturas SDK/REST de Colectivos sin imprimir datos funcionales."

    def add_arguments(self, parser):
        parser.add_argument("--profile", required=True, choices=("production",))
        parser.add_argument("--policy", choices=tuple(REPRESENTATIVE_POLICIES), default=next(iter(REPRESENTATIVE_POLICIES)))
        parser.add_argument("--allow-production-read", action="store_true")

    def handle(self, *args, **options):
        if not options["allow_production_read"]:
            raise CommandError("Debe confirmar la lectura de Production.")
        if getattr(settings, "ZOHO_ACTIVE_PROFILE", "") != "production":
            raise CommandError("ZOHO_ACTIVE_PROFILE debe ser production para evitar una medición ambigua.")

        policy_reference = options["policy"]
        company_document = os.getenv("COLECTIVOS_BENCHMARK_COMPANY_DOCUMENT", "").strip()
        person_document = os.getenv("COLECTIVOS_BENCHMARK_PERSON_DOCUMENT", "").strip()
        results: dict[str, dict[str, list[int | None]]] = defaultdict(lambda: defaultdict(list))

        for backend in ("sdk", "rest"):
            for _run in range(3):
                facade, elapsed = self._timed(lambda: get_zoho(profile="production", backend=backend))
                results[backend]["get_zoho"].append(elapsed)
                _, elapsed = self._timed(facade.organization.get)
                results[backend]["organization"].append(elapsed)
                _, elapsed = self._timed(lambda: tuple(facade.metadata.list_fields("Contacts")))
                results[backend]["metadata"].append(elapsed)
                results[backend]["empresa_exacta"].append(
                    self._contact_search(facade, company_document, "Persona jurídica", "NIT", include_id_type=False) if company_document else None
                )
                results[backend]["individuo_exacto"].append(
                    self._contact_search(facade, person_document, "Persona natural", "CC") if person_document else None
                )
                page, elapsed = self._timed(lambda: facade.search.by_criteria(
                    module="Polizas",
                    criteria=f"(Name:equals:{escape_criteria_value(policy_reference)})",
                    fields=POLICY_DETAIL_FIELDS,
                    page=1,
                    limit=2,
                ))
                results[backend]["localizar_poliza"].append(elapsed)
                if len(page.records) != 1:
                    raise CommandError("La póliza autorizada no produjo una coincidencia inequívoca.")
                policy_id = str(page.records[0].get("id") or "")
                token = sign_record_id(policy_id, "policy")
                service = PolicyService(zoho=facade)
                _, elapsed = self._timed(lambda: service.detail(token))
                results[backend]["detalle_poliza"].append(elapsed)
                _, elapsed = self._timed(lambda: service.group(token))
                results[backend]["grupo_actual"].append(elapsed)
                policy_id = ""
                token = ""

        self.stdout.write("Benchmark seguro de solo lectura (milisegundos)")
        self.stdout.write("Operación | SDK frío | SDK caliente 2/3 | REST frío | REST caliente 2/3")
        for operation in (
            "get_zoho", "organization", "metadata", "empresa_exacta",
            "individuo_exacto", "localizar_poliza", "detalle_poliza", "grupo_actual",
        ):
            sdk = results["sdk"][operation]
            rest = results["rest"][operation]
            self.stdout.write(
                f"{operation} | {self._display(sdk[0])} | {self._warm(sdk)} | "
                f"{self._display(rest[0])} | {self._warm(rest)}"
            )
        self.stdout.write("No se imprimieron documentos, nombres, IDs, cuerpos, tokens ni criterios con valores.")
        self.stdout.write("Tiempos internos de lock, refresh y parsing no son expuestos por la facade; no se infieren.")

    @staticmethod
    def _timed(call):
        started = time.perf_counter()
        result = call()
        return result, round((time.perf_counter() - started) * 1000)

    def _contact_search(self, facade, document: str, person_type: str, id_type: str, *, include_id_type: bool = True) -> int:
        if not document.isdigit():
            raise CommandError("Los documentos de benchmark deben ser numéricos y permanecer solo en variables locales.")
        fixed = [f"(Tipo_de_persona:equals:{person_type})"]
        if include_id_type:
            fixed.append(f"(Tipo_ID:equals:{id_type})")
        fixed.append(f"(N_mero_de_ID:equals:{escape_criteria_value(document)})")
        criteria = f"({'and'.join(fixed)})"
        _, elapsed = self._timed(lambda: facade.search.by_criteria(
            module="Contacts", criteria=criteria, fields=CONTACT_SEARCH_FIELDS, page=1, limit=2,
        ))
        return elapsed

    @staticmethod
    def _display(value):
        return "omitido" if value is None else str(value)

    def _warm(self, values):
        available = [value for value in values[1:] if value is not None]
        return "omitido" if not available else "/".join(str(value) for value in available)
