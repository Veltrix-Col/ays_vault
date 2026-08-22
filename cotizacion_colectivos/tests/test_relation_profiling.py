from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from integrations.zoho.exceptions import ZohoAPIError
from integrations.zoho.schemas import Organization, Page

from cotizacion_colectivos.constants import (
    INSURED_PROFILE_FIELDS,
    POLICY_PROFILE_FIELDS,
    RISK_PROFILE_FIELDS,
)
from cotizacion_colectivos.relation_profiling import (
    INSURED_SPEC,
    POLICY_SPEC,
    RISK_SPEC,
    _relationship_status,
    run_relation_profile,
)


COMMANDS = {
    "colectivos_profile_policies": (
        "cotizacion_colectivos.management.commands._relation_profile_base.get_zoho",
        POLICY_SPEC,
    ),
    "colectivos_profile_insured": (
        "cotizacion_colectivos.management.commands._relation_profile_base.get_zoho",
        INSURED_SPEC,
    ),
    "colectivos_profile_risks": (
        "cotizacion_colectivos.management.commands._relation_profile_base.get_zoho",
        RISK_SPEC,
    ),
}


DATA = {
    "Contacts": (
        {"id": "contact-secret-1"},
        {"id": "contact-secret-2"},
        {"id": "contact-secret-3"},
    ),
    "Polizas": (
        {
            "id": "policy-secret-1",
            "Name": "POLIZA-PRIVADA-1",
            "Tomador_principal1": {"id": "contact-secret-1", "name": "Persona Privada"},
            "Estado_de_la_p_liza": "Vigente",
            "Ramo": "Vida",
            "Aseguradora1": "Aseguradora",
            "P_liza_Fecha_de_inicio_vigencia": "2026-01-01",
            "P_liza_Fecha_fin_de_la_vigencia": "2026-12-31",
        },
        {
            "id": "policy-secret-2",
            "Name": "POLIZA-PRIVADA-2",
            "Tomador_principal1": {"id": "contact-secret-1", "name": "Persona Privada"},
            "Estado_de_la_p_liza": "Vigente",
            "Ramo": "Vida",
            "Aseguradora1": "Aseguradora",
        },
        {
            "id": "policy-secret-3",
            "Name": "POLIZA-PRIVADA-3",
            "Tomador_principal1": {"id": "contact-secret-2", "name": "Otra Persona"},
            "Estado_de_la_p_liza": "Cancelada",
            "Ramo": "Salud",
            "Aseguradora1": "Aseguradora",
        },
    ),
    "Riesgos": (
        {
            "id": "risk-secret-1",
            "Name": "RIESGO PRIVADO 1",
            "Contratista": {"id": "contact-secret-1", "name": "Persona Privada"},
            "Contratante": {"id": "contact-secret-2", "name": "Otra Persona"},
            "Inmueble": None,
            "Tipo_de_riesgo": "Persona",
            "Fecha_inicio": "2026-01-01",
            "Fecha_fin": "2026-12-31",
        },
        {
            "id": "risk-secret-2",
            "Contratista": {"id": "contact-secret-2", "name": "Otra Persona"},
            "Contratante": {"id": "contact-secret-3", "name": "Tercera Persona"},
            "Tipo_de_riesgo": "Persona",
        },
        {
            "id": "risk-secret-3",
            "Contratista": {"id": "contact-secret-3", "name": "Tercera Persona"},
            "Contratante": {"id": "contact-secret-1", "name": "Persona Privada"},
            "Tipo_de_riesgo": "Inmueble",
        },
    ),
    "Riesgos1": (
        {
            "id": "insured-secret-1",
            "Name": "ASEGURADO PRIVADO 1",
            "P_liza": {"id": "policy-secret-1", "name": "Póliza privada"},
            "Asegurado": {"id": "contact-secret-1", "name": "Persona Privada"},
            "Riesgo": {"id": "risk-secret-1", "name": "Riesgo privado"},
            "Estado": "Activo",
            "Ramo": "Vida",
            "Aseguradora": "Aseguradora",
        },
        {
            "id": "insured-secret-2",
            "P_liza": {"id": "policy-secret-2", "name": "Póliza privada"},
            "Asegurado": {"id": "contact-secret-1", "name": "Persona Privada"},
            "Riesgo": {"id": "risk-secret-2", "name": "Riesgo privado"},
            "Estado": "Activo",
        },
        {
            "id": "insured-secret-3",
            "P_liza": {"id": "policy-secret-3", "name": "Póliza privada"},
            "Asegurado": {"id": "contact-secret-2", "name": "Otra Persona"},
            "Riesgo": {"id": "risk-secret-3", "name": "Riesgo privado"},
            "Estado": "Retirado",
        },
    ),
}


class FakeOrganization:
    def get(self):
        return Organization("sandbox-org", "Sandbox", environment="sandbox")


class FixedRecords:
    def __init__(self, *, failure_module=""):
        self.calls = []
        self.failure_module = failure_module

    def list(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["module"] == self.failure_module:
            raise ZohoAPIError("respuesta privada que no debe salir")
        records = DATA[kwargs["module"]]
        return Page(records=records, count=len(records), more_records=False)


class FakeZoho:
    profile = "sandbox"
    environment = "sandbox"
    organization = FakeOrganization()

    def __init__(self, records=None):
        self.records = records or FixedRecords()


class RelationProfilingUnitTests(SimpleTestCase):
    def test_relationship_classification_is_conservative(self):
        self.assertEqual(_relationship_status(with_id=0, matched=0, unmatched=0), "No confirmada")
        self.assertEqual(_relationship_status(with_id=3, matched=0, unmatched=3), "Rechazada")
        self.assertEqual(_relationship_status(with_id=2, matched=2, unmatched=0), "Parcialmente confirmada")
        self.assertEqual(_relationship_status(with_id=3, matched=3, unmatched=0), "Confirmada")
        self.assertEqual(_relationship_status(with_id=3, matched=2, unmatched=1), "Parcialmente confirmada")

    def test_policy_profile_confirms_ids_without_retaining_values(self):
        result = run_relation_profile(FakeZoho(), POLICY_SPEC)
        relation = result["relationships"]["Tomador_principal1"]
        self.assertEqual(relation["status"], "Confirmada")
        self.assertEqual(relation["matched"], 3)
        self.assertEqual(result["multiplicity"]["Tomador_principal1"]["keys_with_multiple_records"], 1)
        rendered = json.dumps(result, ensure_ascii=False)
        for secret in ("contact-secret", "policy-secret", "Persona Privada", "POLIZA-PRIVADA"):
            self.assertNotIn(secret, rendered)

    def test_insured_profile_validates_all_three_lookup_targets(self):
        result = run_relation_profile(FakeZoho(), INSURED_SPEC)
        self.assertEqual(
            {field: item["status"] for field, item in result["relationships"].items()},
            {"P_liza": "Confirmada", "Asegurado": "Confirmada", "Riesgo": "Confirmada"},
        )
        self.assertEqual(result["multiplicity"]["Asegurado"]["keys_with_multiple_records"], 1)

    def test_risk_profile_matches_contract_parties_to_contacts(self):
        result = run_relation_profile(FakeZoho(), RISK_SPEC)
        self.assertEqual(result["relationships"]["Contratista"]["status"], "Confirmada")
        self.assertEqual(result["relationships"]["Contratante"]["status"], "Confirmada")


class RelationProfilingCommandTests(SimpleTestCase):
    def test_all_commands_reject_production_and_require_confirmation(self):
        for command, (patch_target, _) in COMMANDS.items():
            with self.subTest(command=command), patch(patch_target) as get_zoho:
                with self.assertRaisesMessage(CommandError, "exclusivamente"):
                    call_command(command, profile="production", allow_real_read=True)
                get_zoho.assert_not_called()
            with self.subTest(command=f"{command}-confirm"), patch(patch_target) as get_zoho:
                with self.assertRaisesMessage(CommandError, "--allow-real-read"):
                    call_command(command, profile="sandbox")
                get_zoho.assert_not_called()

    def test_commands_use_only_fixed_modules_fields_and_write_safe_aggregates(self):
        for command, (patch_target, spec) in COMMANDS.items():
            records = FixedRecords()
            with self.subTest(command=command), TemporaryDirectory() as directory, override_settings(
                BASE_DIR=directory
            ), patch(patch_target, return_value=FakeZoho(records)):
                output = StringIO()
                call_command(
                    command,
                    profile="sandbox",
                    allow_real_read=True,
                    stdout=output,
                )
                artifact = Path(
                    directory,
                    "artifacts/zoho/colectivos/relation_profiles",
                    f"{spec.name}.json",
                )
                report = Path(directory, "docs/cotizacion_colectivos/relations_analysis.md")
                self.assertTrue(artifact.exists())
                self.assertTrue(report.exists())
                rendered = artifact.read_text(encoding="utf-8") + report.read_text(encoding="utf-8") + output.getvalue()
                for secret in (
                    "contact-secret",
                    "policy-secret",
                    "risk-secret",
                    "Persona Privada",
                    "POLIZA-PRIVADA",
                ):
                    self.assertNotIn(secret, rendered)
                target_call = next(call for call in records.calls if call["module"] == spec.module)
                self.assertEqual(target_call["fields"], spec.fields)
                self.assertLessEqual(target_call["limit"], 200)

    @patch("cotizacion_colectivos.management.commands._relation_profile_base.get_zoho")
    def test_api_failure_is_translated_without_private_response(self, get_zoho):
        get_zoho.return_value = FakeZoho(FixedRecords(failure_module="Contacts"))
        with TemporaryDirectory() as directory, override_settings(BASE_DIR=directory):
            with self.assertRaisesMessage(CommandError, "No fue posible completar") as raised:
                call_command(
                    "colectivos_profile_policies",
                    profile="sandbox",
                    allow_real_read=True,
                )
        self.assertNotIn("respuesta privada", str(raised.exception))

    def test_fixed_field_sets_exclude_sensitive_contact_data(self):
        self.assertEqual(POLICY_SPEC.fields, POLICY_PROFILE_FIELDS)
        self.assertEqual(INSURED_SPEC.fields, INSURED_PROFILE_FIELDS)
        self.assertEqual(RISK_SPEC.fields, RISK_PROFILE_FIELDS)
        rendered = " ".join(POLICY_SPEC.fields + INSURED_SPEC.fields + RISK_SPEC.fields).lower()
        for forbidden in ("email", "phone", "mobile", "documento", "n_mero_de_id"):
            self.assertNotIn(forbidden, rendered)
