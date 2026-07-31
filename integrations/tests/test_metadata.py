from __future__ import annotations

from django.test import SimpleTestCase

from integrations.zoho.exceptions import ZohoInvalidResponseError
from integrations.zoho.metadata import MetadataService


class FakeClient:
    class Config:
        api_base_url = "https://www.zohoapis.com"
        profile = "production"
        environment = "production"

    config = Config()

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, path, **kwargs):
        self.calls.append((path, kwargs))
        return self.payload


class MetadataTests(SimpleTestCase):
    def test_organization_normalizes_optional_fields(self):
        service = MetadataService(
            FakeClient(
                {
                    "org": [
                        {
                            "id": "123",
                            "company_name": "A&S",
                            "country": "Colombia",
                            "time_zone": "America/Bogota",
                            "currency": "COP",
                            "type": "sandbox",
                        }
                    ]
                }
            )
        )
        with self.assertLogs("integrations.zoho", "INFO") as logs:
            organization = service.organization()
        self.assertEqual(organization.organization_id, "123")
        self.assertEqual(organization.timezone, "America/Bogota")
        self.assertEqual(organization.environment, "sandbox")
        diagnostic = " ".join(logs.output)
        self.assertIn("backend=rest", diagnostic)
        self.assertIn("clase_valor=str", diagnostic)
        self.assertIn("valor_normalizado=sandbox", diagnostic)

    def test_modules_support_custom_historic_api_names(self):
        service = MetadataService(
            FakeClient(
                {
                    "modules": [
                        {
                            "api_name": "Opeeraciones",
                            "module_name": "Operaciones",
                            "plural_label": "Operaciones",
                            "singular_label": "Operación",
                            "custom_module": True,
                            "api_supported": True,
                        }
                    ]
                }
            )
        )
        modules = service.modules()
        self.assertEqual(modules[0].api_name, "Opeeraciones")
        self.assertTrue(modules[0].custom_module)

    def test_fields_normalize_lookup_picklist_and_missing_values(self):
        service = MetadataService(
            FakeClient(
                {
                    "fields": [
                        {
                            "api_name": "Asegurado",
                            "field_label": "Asegurado",
                            "data_type": "lookup",
                            "lookup": {"module": {"api_name": "Riesgos1"}},
                            "pick_list_values": [],
                            "operation_type": {"api_update": False},
                        }
                    ]
                }
            )
        )
        field = service.fields("Polizas")[0]
        self.assertEqual(field.lookup["module"]["api_name"], "Riesgos1")
        self.assertTrue(field.read_only)
        self.assertIsNone(field.length)

    def test_invalid_payload_rejected(self):
        with self.assertRaises(ZohoInvalidResponseError):
            MetadataService(FakeClient({"modules": None})).modules()
