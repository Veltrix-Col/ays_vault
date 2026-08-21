from __future__ import annotations

import json

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from integrations.zoho import get_zoho
from integrations.zoho.exceptions import ZohoError

from cotizacion_colectivos.management.commands.zoho_create_test_task import _safe_zoho_diagnostic
from cotizacion_colectivos.services.risk_sandbox import (
    RISK_CONFIRMATION, RiskPublicationRejected, RiskPublicationUncertain,
    RiskPublishingDisabled, build_risk_payload, create_sandbox_risk,
    resolve_risk_by_plate, risk_dry_run,
)


ITEMS = (
    ("VELTRIX TEST VEH 001", "VTX001", 2024),
    ("VELTRIX TEST VEH 002", "VTX002", 2024),
    ("VELTRIX TEST VEH 003", "VTX003", 2024),
    ("VELTRIX TEST VEH 004-A", "VTX004", 2025),
    ("VELTRIX TEST VEH 004-B", "VTX005", 2025),
)


class Command(BaseCommand):
    help = "Prepara o crea exactamente cinco Riesgos sintéticos de Movilidad en Sandbox."

    def add_arguments(self, parser):
        parser.add_argument("--confirm", default="")

    def _guard(self):
        if str(getattr(settings, "ZOHO_ACTIVE_PROFILE", "")).lower() != "sandbox":
            raise CommandError("El perfil activo no es Sandbox.")
        if not getattr(settings, "ZOHO_SANDBOX_WRITE_ENABLED", False):
            raise CommandError("La escritura Sandbox está deshabilitada.")

    def handle(self, *args, **options):
        self._guard()
        payloads = tuple(build_risk_payload(name=n, plate=p, model=m) for n, p, m in ITEMS)
        if str(options.get("confirm") or "").strip() != RISK_CONFIRMATION:
            self.stdout.write(json.dumps(risk_dry_run(payloads), ensure_ascii=False, indent=2))
            self.stdout.write("NO se realizó WRITE.")
            return
        if not getattr(settings, "COLECTIVOS_MOBILITY_RISK_SEED_ENABLED", False):
            raise CommandError("El seed de Riesgos está deshabilitado.")
        try:
            zoho = get_zoho(profile="sandbox")
        except ZohoError as exc:
            raise CommandError(_safe_zoho_diagnostic(exc)) from exc
        results = []
        for payload in payloads:
            try:
                existing = resolve_risk_by_plate(
                    plate=payload["Placa_del_vehiculo"], zoho=zoho
                )
                if existing["status"] == "FOUND":
                    results.append({
                        "alias": payload["Name"], "result": "ALREADY_EXISTS",
                        "risk_id": existing.get("record_id", ""),
                    })
                    continue
                if existing["status"] == "AMBIGUOUS":
                    results.append({"alias": payload["Name"], "result": "BLOCKED", "risk_id": ""})
                    continue
                result = create_sandbox_risk(payload, confirmation=options["confirm"], zoho=zoho)
                results.append({"alias": payload["Name"], "result": "CREATED", "risk_id": result["record_id"]})
            except RiskPublicationUncertain as exc:
                raise CommandError(str(exc)) from exc
            except (RiskPublishingDisabled, RiskPublicationRejected, ValidationError) as exc:
                results.append({"alias": payload["Name"], "result": "BLOCKED", "risk_id": ""})
                raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(results, ensure_ascii=False, indent=2))
