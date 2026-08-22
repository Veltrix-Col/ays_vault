from __future__ import annotations

import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from integrations.zoho import get_zoho
from integrations.zoho.exceptions import ZohoError

from cotizacion_colectivos.management.commands.zoho_create_test_task import _safe_zoho_diagnostic
from cotizacion_colectivos.services.subrisk_sandbox import (
    MOBILITY_SUBRISK_CONFIRMATION, MOBILITY_SUBRISK_SCENARIOS,
    SubriskPublicationRejected, SubriskPublishingDisabled,
    SubriskPublicationUncertain,
    build_mobility_subrisk_payload, create_mobility_subrisk_sandbox,
    resolve_mobility_subrisk_relation, resolve_policy_by_number,
    resolve_reference_by_id,
)


POLICY_NUMBER = "040006434488"
POLICY_FIELDS = ("id", "Name")
CONTACT_FIELDS = ("id", "Full_Name")
RISK_FIELDS = ("id", "Name", "Placa_del_vehiculo", "Tipo_de_riesgo")
MOBILITY_SCENARIO_ALIASES = frozenset(item[0] for item in MOBILITY_SUBRISK_SCENARIOS)


def _mask(value: object) -> str:
    text = str(value or "")
    return f"***{text[-4:]}" if len(text) > 4 else "***"


class Command(BaseCommand):
    help = "Ensayo controlado de cuatro Riesgos1 de Movilidad en Sandbox (dry-run por defecto)."

    def add_arguments(self, parser):
        parser.add_argument("--entry-date", default="2026-08-21")
        parser.add_argument("--confirm", default="")
        parser.add_argument("--only", default="", help="Ensaya únicamente un alias cerrado de Movilidad.")

    def _guard(self):
        if str(getattr(settings, "ZOHO_ACTIVE_PROFILE", "")).lower() != "sandbox":
            raise CommandError("El perfil activo no es Sandbox.")
        if not getattr(settings, "ZOHO_SANDBOX_WRITE_ENABLED", False):
            raise CommandError("La escritura Sandbox está deshabilitada.")

    def _resolve(self, zoho, entry_date: str, only: str = ""):
        policy = resolve_policy_by_number(policy_number=POLICY_NUMBER, zoho=zoho)
        if policy["status"] != "FOUND":
            return policy, ()
        policy_id = policy["record_id"]
        prepared = []
        scenarios = tuple(item for item in MOBILITY_SUBRISK_SCENARIOS if not only or item[0] == only)
        for alias, affiliate_id, insured_id, risk_id, plate in scenarios:
            contact_a = resolve_reference_by_id(module="Contacts", record_id=affiliate_id, zoho=zoho, fields=CONTACT_FIELDS)
            contact_i = resolve_reference_by_id(module="Contacts", record_id=insured_id, zoho=zoho, fields=CONTACT_FIELDS)
            risk = resolve_reference_by_id(module="Riesgos", record_id=risk_id, zoho=zoho, fields=RISK_FIELDS)
            if contact_a["status"] != "FOUND" or contact_i["status"] != "FOUND" or risk["status"] != "FOUND":
                prepared.append({"alias": alias, "plate": plate, "policy_id": _mask(policy_id),
                                 "affiliate_id": _mask(affiliate_id), "insured_id": _mask(insured_id),
                                 "risk_id": _mask(risk_id), "result": "BLOCKED"})
                continue
            risk_record = risk["record"]
            if str(risk_record.get("Placa_del_vehiculo") or "").replace("-", "").replace(" ", "").upper() != plate:
                prepared.append({"alias": alias, "plate": plate, "policy_id": _mask(policy_id),
                                 "affiliate_id": _mask(affiliate_id), "insured_id": _mask(insured_id),
                                 "risk_id": _mask(risk_id), "result": "BLOCKED"})
                continue
            payload = build_mobility_subrisk_payload(
                policy_id=policy_id, affiliate_contact_id=affiliate_id,
                insured_contact_id=insured_id, risk_id=risk_id,
                subrisk_name=alias, entry_date=entry_date,
            )
            relation = resolve_mobility_subrisk_relation(
                policy_id=policy_id, risk_id=risk_id,
                affiliate_contact_id=affiliate_id, insured_contact_id=insured_id, zoho=zoho,
            )
            prepared.append({"alias": alias, "plate": plate, "policy_id": _mask(policy_id),
                             "affiliate_id": _mask(affiliate_id), "insured_id": _mask(insured_id),
                             "payload": payload, "relation": relation,
                             "result": "ALREADY_EXISTS" if relation["status"] == "ALREADY_EXISTS" else
                                       "AMBIGUOUS" if relation["status"] == "AMBIGUOUS" else "NOT_FOUND",
                             "risk_id": _mask(risk_id)})
        return policy, tuple(prepared)

    def handle(self, *args, **options):
        self._guard()
        only = str(options.get("only") or "").strip()
        if only and only not in MOBILITY_SCENARIO_ALIASES:
            raise CommandError("--only debe ser uno de los escenarios de Movilidad definidos.")
        planned = 1 if only else len(MOBILITY_SUBRISK_SCENARIOS)
        try:
            zoho = get_zoho(profile="sandbox")
            policy, prepared = self._resolve(zoho, options["entry_date"], only=only)
        except ZohoError as exc:
            raise CommandError(_safe_zoho_diagnostic(exc).replace("Task", "Riesgos1")) from exc
        if policy.get("status") != "FOUND":
            self.stdout.write(json.dumps({"profile": "sandbox", "module": "Riesgos1",
                                          "policy": POLICY_NUMBER, "policy_resolution": policy,
                                          "planned": planned, "writes": 0}, ensure_ascii=False, indent=2))
            self.stdout.write("NO se realizó WRITE.")
            return
        if str(options.get("confirm") or "").strip() != MOBILITY_SUBRISK_CONFIRMATION:
            self.stdout.write(json.dumps({"profile": "sandbox", "module": "Riesgos1",
                                          "policy": POLICY_NUMBER, "policy_id": _mask(policy["record_id"]),
                                          "planned": planned, "writes": 0,
                                          "records": [{k: item.get(k, "") for k in
                                                       ("alias", "policy_id", "affiliate_id", "insured_id", "risk_id", "result")}
                                                      for item in prepared]}, ensure_ascii=False, indent=2))
            self.stdout.write("NO se realizó WRITE.")
            return
        if not getattr(settings, "COLECTIVOS_MOBILITY_SUBRISK_SEED_ENABLED", False):
            raise CommandError("El seed Movilidad está deshabilitado.")
        if str(getattr(settings, "COLECTIVOS_MOBILITY_SUBRISK_SEED_CONFIRMATION", "")) != MOBILITY_SUBRISK_CONFIRMATION:
            raise CommandError("Falta la confirmación configurada del seed Movilidad.")
        if not getattr(settings, "COLECTIVOS_SUBRISK_PUBLISH_ENABLED", False):
            raise CommandError("El publisher general de Riesgos1 está deshabilitado.")
        results = []
        for item in prepared:
            if item["result"] != "NOT_FOUND":
                results.append({k: item.get(k, "") for k in
                                ("alias", "policy_id", "affiliate_id", "insured_id", "risk_id", "result")}
                               | {"riesgos1_id": ""})
                continue
            try:
                result = create_mobility_subrisk_sandbox(item["payload"], confirmation=options["confirm"], zoho=zoho)
                results.append({k: item.get(k, "") for k in
                                ("alias", "policy_id", "affiliate_id", "insured_id", "risk_id")}
                               | {"result": "CREATED", "riesgos1_id": _mask(result["record_id"])})
            except SubriskPublicationUncertain as exc:
                raise CommandError(str(exc)) from exc
            except (SubriskPublishingDisabled, SubriskPublicationRejected) as exc:
                raise CommandError(str(exc)) from exc
            except ZohoError as exc:
                # Preserve the publisher's sanitized SDK diagnostics.  In
                # particular, do not let a raw SDK exception (or payload) leak
                # through the management command.
                raise CommandError(_safe_zoho_diagnostic(exc).replace("Task", "Riesgos1")) from exc
        self.stdout.write(json.dumps({"profile": "sandbox", "module": "Riesgos1", "planned": planned,
                                      "writes": sum(r["result"] == "CREATED" for r in results),
                                      "records": results}, ensure_ascii=False, indent=2))
