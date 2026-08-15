from __future__ import annotations

from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from integrations.zoho.schemas import FieldMetadata, Organization, Page


RAW_IDS = ("600000000000001", "600000000000002", "600000000000003")


class FakeOrganization:
    def get(self):
        return Organization("sandbox-org", "Sandbox", environment="sandbox")


class FakeMetadata:
    def list_fields(self, module):
        assert module == "Accounts"
        return (FieldMetadata(api_name="Account_Name", field_label="Cuenta", data_type="text"),)


class FakeRecords:
    def __init__(self):
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        return Page(records=({"id": RAW_IDS[0]},), count=1)

    def get_by_id(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "id": kwargs["record_id"],
            "Empresa": {"id": RAW_IDS[0], "name": "Empresa privada", "$se_module": "Accounts"},
            "Grupo_econ_mico": {},
            "Tipo_de_persona": "Persona jurídica",
        }


class FakeSearch:
    def __init__(self):
        self.calls = []

    def by_field(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["module"] == "Contacts" and kwargs["field"] == "Grupo_econ_mico":
            return Page(records=({
                "id": RAW_IDS[1],
                "Grupo_econ_mico": {"id": RAW_IDS[2], "name": "Fonconstruimos", "$se_module": "Contacts"},
                "Empresa": {"id": RAW_IDS[0], "name": "Empresa privada", "$se_module": "Accounts"},
            },), count=1)
        return Page(records=(), count=0)


def fake_zoho():
    return SimpleNamespace(
        profile="sandbox",
        organization=FakeOrganization(),
        metadata=FakeMetadata(),
        records=FakeRecords(),
        search=FakeSearch(),
    )


PATCH = "cotizacion_colectivos.management.commands.colectivos_discover_fonconstruimos.get_zoho"


class FonconstruimosDiscoveryTests(SimpleTestCase):
    @patch(PATCH)
    def test_rejects_production_before_facade(self, get_zoho):
        with self.assertRaisesMessage(CommandError, "exclusivamente"):
            call_command("colectivos_discover_fonconstruimos", profile="production", allow_real_read=True)
        get_zoho.assert_not_called()

    @patch(PATCH)
    def test_requires_confirmation(self, get_zoho):
        with self.assertRaisesMessage(CommandError, "--allow-real-read"):
            call_command("colectivos_discover_fonconstruimos", profile="sandbox")
        get_zoho.assert_not_called()

    @patch(PATCH)
    def test_is_bounded_read_only_and_redacts_ids_and_names(self, get_zoho):
        facade = fake_zoho()
        get_zoho.return_value = facade
        output = StringIO()
        call_command(
            "colectivos_discover_fonconstruimos",
            profile="sandbox",
            allow_real_read=True,
            stdout=output,
        )
        rendered = output.getvalue()
        for secret in (*RAW_IDS, "Empresa privada"):
            self.assertNotIn(secret, rendered)
        self.assertEqual(len(facade.search.calls), 9)
        self.assertTrue(all(call["limit"] == 5 and call["page"] == 1 for call in facade.search.calls))
        self.assertEqual(
            facade.records.calls,
            [{"module": "Accounts", "fields": ("id",), "page": 1, "limit": 1}],
        )
        self.assertIn('"read_total": 12', rendered)
        self.assertIn('"writes": 0', rendered)
        get_zoho.assert_called_once_with(profile="sandbox")

    @patch("cotizacion_colectivos.management.commands.colectivos_resolve_fonconstruimos_insured.get_zoho")
    def test_insured_resolution_uses_only_confirmed_relation_and_redacts(self, get_zoho):
        facade = fake_zoho()
        facade.search.by_field = lambda **kwargs: Page(records=({
            "id": RAW_IDS[1],
            "Asegurado": {"id": RAW_IDS[2], "name": "Persona privada"},
        },), count=1)
        get_zoho.return_value = facade
        output = StringIO()
        call_command(
            "colectivos_resolve_fonconstruimos_insured",
            profile="sandbox",
            allow_real_read=True,
            stdout=output,
        )
        rendered = output.getvalue()
        for secret in (*RAW_IDS, "Persona privada", "Empresa privada"):
            self.assertNotIn(secret, rendered)
        self.assertIn('"confirmed_relation": "Riesgos1.Asegurado->Contacts"', rendered)
        self.assertIn('"read_total": 3', rendered)
        self.assertIn('"writes": 0', rendered)
