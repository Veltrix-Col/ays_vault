from django.test import SimpleTestCase

from integrations.zoho.exceptions import ZohoValidationError
from integrations.zoho.records import RecordsService


class FakeClient:
    def __init__(self):
        self.params = None
        self.path = ""

    def get(self, path, *, params, logical_endpoint):
        self.path = path
        self.params = params
        return {"data": [{"id": "1"}], "info": {"more_records": True, "page": 2}}


class RecordsTests(SimpleTestCase):
    def test_explicit_fields_and_pagination(self):
        client = FakeClient()
        page = RecordsService(client).list(
            "Ordenes_de_servicio",
            fields=["id", "Name"],
            page=2,
            per_page=50,
        )
        self.assertEqual(client.path, "/crm/v8/Ordenes_de_servicio")
        self.assertEqual(client.params["fields"], "id,Name")
        self.assertTrue(page.more_records)

    def test_invalid_module_field_and_limits(self):
        service = RecordsService(FakeClient())
        for kwargs in (
            {"module_api_name": "../Contacts", "fields": ["id"]},
            {"module_api_name": "Contacts", "fields": ["bad.field"]},
            {"module_api_name": "Contacts", "fields": []},
            {"module_api_name": "Contacts", "fields": ["id"], "per_page": 201},
            {"module_api_name": "Contacts", "fields": ["id"], "page": 0},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ZohoValidationError):
                    service.list(**kwargs)
