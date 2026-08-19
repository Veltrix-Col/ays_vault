from __future__ import annotations

import json
import ast
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from integrations.zoho.exceptions import ZohoAuthorizationError

from cotizacion_colectivos.discovery import (
    PENDING,
    build_relationships,
    build_search_candidates,
)
from cotizacion_colectivos.tests.fakes import (
    CONTACT_FIELDS,
    FakeMetadata,
    FakeRecords,
    FakeZoho,
)


class DiscoveryCommandTests(SimpleTestCase):
    @patch(
        "cotizacion_colectivos.management.commands."
        "colectivos_discover_schema.get_zoho"
    )
    def test_rejects_production_before_getting_facade(self, get_zoho):
        with self.assertRaisesMessage(CommandError, "exclusivamente"):
            call_command("colectivos_discover_schema", profile="production")
        get_zoho.assert_not_called()

    @patch(
        "cotizacion_colectivos.management.commands."
        "colectivos_discover_schema.get_zoho"
    )
    def test_generates_metadata_only_report_for_sandbox(self, get_zoho):
        metadata = FakeMetadata(
            {"Persona_juridica": ZohoAuthorizationError("denegado")}
        )
        facade = FakeZoho(metadata=metadata, records=FakeRecords())
        get_zoho.return_value = facade
        with TemporaryDirectory() as temporary:
            output = StringIO()
            call_command(
                "colectivos_discover_schema",
                profile="sandbox",
                output_dir=temporary,
                stdout=output,
            )
            root = Path(temporary)
            expected = {
                "modules.json",
                "fields.json",
                "relationships.json",
                "search_candidates.json",
                "discovery.md",
            }
            self.assertEqual({item.name for item in root.iterdir()}, expected)
            modules = json.loads((root / "modules.json").read_text("utf-8"))
            self.assertEqual(modules["profile"], "sandbox")
            self.assertEqual(modules["content"], "metadata_only")
            self.assertEqual(modules["failures"]["Persona_juridica"], "authorization")
            combined = "\n".join(
                item.read_text("utf-8") for item in root.iterdir()
            )
            self.assertNotIn("123456789", combined)
            self.assertNotIn("access-token", combined)
            self.assertNotIn("refresh-token", combined)
            self.assertIn(PENDING, combined)
            self.assertIn("No se consultaron registros", output.getvalue())
            self.assertEqual(facade.records.calls, [])
            get_zoho.assert_called_once_with(profile="sandbox")

    def test_candidates_remain_explicitly_unconfirmed(self):
        candidates = build_search_candidates({"Contacts": CONTACT_FIELDS})
        document = candidates["document"][0]
        self.assertEqual(document["api_name"], "Numero_de_documento")
        self.assertEqual(document["status"], PENDING)

    def test_relationships_come_only_from_metadata(self):
        relationships = build_relationships({"Contacts": CONTACT_FIELDS})
        self.assertEqual(len(relationships), 1)
        self.assertEqual(relationships[0]["field"], "Empresa")
        self.assertEqual(
            relationships[0]["lookup"], {"module": "Persona_juridica"}
        )


class PackageBoundaryTests(SimpleTestCase):
    def test_operational_models_are_local_and_migrated(self):
        root = Path(__file__).resolve().parents[1]
        self.assertTrue((root / "models.py").exists())
        self.assertTrue((root / "migrations" / "0001_initial.py").exists())
        source = (root / "models.py").read_text("utf-8")
        self.assertNotIn("zohocrmsdk", source)
        self.assertNotIn("access_token", source)
        self.assertNotIn("refresh_token", source)

    def test_does_not_expose_write_services(self):
        root = Path(__file__).resolve().parents[1]
        exports = (root / "services" / "__init__.py").read_text("utf-8")
        for exported in (
            "CompanySearchService", "PersonSearchService", "EntityDetailService", "PolicyService",
        ):
            self.assertIn(exported, exports)
        self.assertNotIn("GuardedSandboxTaskPublisher", exports)
        self.assertNotIn("GuardedSandboxContactPublisher", exports)

        allowed_publishers = {
            (root / "services" / "task_publisher.py").resolve(),
            (root / "services" / "person_contract.py").resolve(),
        }
        write_calls = []
        direct_http_write = []
        for path in root.rglob("*.py"):
            if "tests" in path.parts:
                continue
            source = path.read_text("utf-8")
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    chain = []
                    current = node.func
                    while isinstance(current, ast.Attribute):
                        chain.append(current.attr)
                        current = current.value
                    if isinstance(current, ast.Name):
                        chain.append(current.id)
                    dotted = ".".join(reversed(chain))
                    if dotted in {"records.create", "records.update"}:
                        write_calls.append(path.resolve())
                    if dotted in {"requests.post", "requests.put", "requests.patch", "requests.delete", "httpx.post", "httpx.put", "httpx.patch", "httpx.delete"}:
                        direct_http_write.append(path)
        self.assertTrue(write_calls)
        self.assertTrue(set(write_calls).issubset(allowed_publishers))
        self.assertEqual(direct_http_write, [])

        task_source = (root / "services" / "task_publisher.py").read_text("utf-8")
        contact_source = (root / "services" / "person_contract.py").read_text("utf-8")
        for source in (task_source, contact_source):
            self.assertIn("sandbox", source)
            self.assertIn("write_enabled", source)
            self.assertIn("confirmation", source)
