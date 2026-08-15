from __future__ import annotations

from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from integrations.zoho.schemas import Organization, Page


RAW_COMPANY_ID = "600000000000001"
RAW_TASK_ID = "600000000000002"
RAW_EMAIL = "persona@example.com"


class FakeOrganization:
    def get(self):
        return Organization("sandbox-org", "Sandbox", environment="sandbox")


class FakeSearch:
    def __init__(self):
        self.calls = []

    def by_field(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["module"] == "Contacts" and kwargs["field"] == "Nombre_comercial":
            return Page(records=({
                "id": RAW_COMPANY_ID,
                "Nombre_comercial": "Fonconstruimos",
                "Empresa": {"id": "600000000000010", "name": "Dato sensible", "$se_module": "Accounts"},
            },), count=1)
        if kwargs["module"] == "Tasks":
            return Page(records=({
                "id": RAW_TASK_ID,
                "Subject": "Asunto con información personal",
                "tipo_de_solicitud": kwargs["value"],
                "What_Id": {"id": "600000000000020", "name": "Póliza real", "$se_module": "Polizas"},
                "Who_Id": {"id": "600000000000030", "name": "Persona real", "$se_module": "Contacts"},
                "Owner": {"id": "600000000000040", "name": "Propietario real"},
                "Responsable": "Responsable real",
                "Status": "No iniciada",
                "rea": "Colectivos",
                "Due_Date": "2026-08-20",
                "Correo_del_solicitante": RAW_EMAIL,
                "ID_Tomador": "600000000000050",
                "ID_asegurado": "600000000000060",
                "ID_Riesgos1_task": "600000000000070",
                "N_mero_p_liza": "POLIZA-PRIVADA",
            },), count=1)
        return Page(records=(), count=0)


def fake_zoho():
    return SimpleNamespace(
        profile="sandbox",
        organization=FakeOrganization(),
        search=FakeSearch(),
    )


PATCH = "cotizacion_colectivos.management.commands.colectivos_discover_task_contract.get_zoho"


class TaskContractDiscoveryTests(SimpleTestCase):
    @patch(PATCH)
    def test_rejects_production_before_building_facade(self, get_zoho):
        with self.assertRaisesMessage(CommandError, "exclusivamente"):
            call_command("colectivos_discover_task_contract", profile="production", allow_real_read=True)
        get_zoho.assert_not_called()

    @patch(PATCH)
    def test_requires_explicit_real_read_confirmation(self, get_zoho):
        with self.assertRaisesMessage(CommandError, "--allow-real-read"):
            call_command("colectivos_discover_task_contract", profile="sandbox")
        get_zoho.assert_not_called()

    @patch(PATCH)
    def test_samples_one_task_per_kind_and_redacts_payload(self, get_zoho):
        facade = fake_zoho()
        get_zoho.return_value = facade
        output = StringIO()
        call_command(
            "colectivos_discover_task_contract",
            profile="sandbox",
            allow_real_read=True,
            stdout=output,
        )
        rendered = output.getvalue()
        for secret in (
            RAW_COMPANY_ID,
            RAW_TASK_ID,
            RAW_EMAIL,
            "Dato sensible",
            "Persona real",
            "Responsable real",
            "POLIZA-PRIVADA",
        ):
            self.assertNotIn(secret, rendered)
        task_calls = [call for call in facade.search.calls if call["module"] == "Tasks"]
        self.assertEqual([call["value"] for call in task_calls], ["Ingresos", "Retiros", "Cotización"])
        self.assertTrue(all(call["limit"] == 1 and call["page"] == 1 for call in task_calls))
        self.assertIn('"read_total": 8', rendered)
        self.assertIn('"writes": 0', rendered)
        get_zoho.assert_called_once_with(profile="sandbox")
