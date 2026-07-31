from __future__ import annotations

from django.test import SimpleTestCase

from integrations.zoho.coql import CoqlService
from integrations.zoho.exceptions import ZohoValidationError


class FakeClient:
    def __init__(self, payload=None):
        self.payload = payload or {"data": [], "info": {"more_records": False}}
        self.json = None

    def post_read(self, path, *, json, logical_endpoint):
        self.json = json
        return self.payload


class CoqlTests(SimpleTestCase):
    def test_select_adds_defensive_limit(self):
        client = FakeClient({"data": [{"id": "1"}], "info": {"count": 1}})
        page = CoqlService(client).query(
            "select id from Contacts where id is not null", limit=100
        )
        self.assertEqual(page.count, 1)
        self.assertTrue(client.json["select_query"].endswith("limit 0, 100"))

    def test_existing_safe_limit_is_preserved(self):
        client = FakeClient()
        CoqlService(client).query("SELECT id FROM Contacts LIMIT 10")
        self.assertEqual(client.json["select_query"], "SELECT id FROM Contacts LIMIT 10")

    def test_empty_write_multi_statement_and_oversize_rejected(self):
        invalid = (
            "",
            "DELETE FROM Contacts",
            "SELECT id FROM Contacts; DELETE FROM Contacts",
            "SELECT id FROM Contacts -- comment",
            "SELECT id FROM Contacts LIMIT 2001",
        )
        for query in invalid:
            with self.subTest(query=query):
                with self.assertRaises(ZohoValidationError):
                    CoqlService(FakeClient()).query(query)

    def test_pagination_bounds(self):
        with self.assertRaises(ZohoValidationError):
            CoqlService(FakeClient()).query("SELECT id FROM Contacts", limit=2001)
