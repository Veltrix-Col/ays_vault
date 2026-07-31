from __future__ import annotations

import json
from io import StringIO
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from integrations.zoho.exceptions import ZohoAPIError, ZohoAuthenticationError
from integrations.zoho.schemas import FieldMetadata, ModuleMetadata, Organization


def fake_services():
    organization = Organization(
        "org-1", "Seguros A&S", country="Colombia",
        timezone="America/Bogota", currency="COP",
        data_center="www.zohoapis.com",
    )
    modules = (
        ModuleMetadata("Contacts", "Contacts", "Personas", "Persona", api_supported=True),
        ModuleMetadata("Polizas", "Polizas", "Pólizas", "Póliza", custom_module=True),
    )
    field = FieldMetadata(
        "Asegurado", "Asegurado", "lookup",
        lookup={"module": {"api_name": "Contacts"}},
    )
    metadata = SimpleNamespace(
        organization=lambda: organization,
        modules=lambda: modules,
        fields=lambda _module: (field,),
    )
    return SimpleNamespace(metadata=metadata)


def fake_facade():
    services = fake_services()
    return SimpleNamespace(
        backend_name="sdk",
        organization=SimpleNamespace(get=services.metadata.organization),
        metadata=SimpleNamespace(
            list_modules=services.metadata.modules,
            list_fields=services.metadata.fields,
        ),
    )


class ZohoCommandTests(SimpleTestCase):
    def test_backend_info_is_safe(self):
        output = StringIO()
        with patch(
            "integrations.management.commands.zoho_backend_info.get_zoho",
            return_value=fake_facade(),
        ):
            call_command("zoho_backend_info", stdout=output)
        value = output.getvalue()
        self.assertIn("Backend: SDK", value)
        self.assertIn("CRM API: V8", value)
        self.assertIn("Modo: solo lectura", value)
        self.assertNotIn("refresh-token", value)
        self.assertNotIn("client-secret", value)

    def test_check_connection_safe_output(self):
        output = StringIO()
        with patch(
            "integrations.management.commands.zoho_check_connection.get_zoho",
            return_value=fake_facade(),
        ):
            call_command("zoho_check_connection", stdout=output)
        value = output.getvalue()
        self.assertIn("Conexión Zoho: OK", value)
        self.assertIn("Modo: solo lectura", value)
        self.assertNotIn("token", value.lower())

    def test_check_connection_nonzero_on_failure(self):
        with patch(
            "integrations.management.commands.zoho_check_connection.get_zoho",
            side_effect=ZohoAuthenticationError("Autenticación rechazada."),
        ):
            with self.assertRaises(CommandError):
                call_command("zoho_check_connection", stdout=StringIO())

    def test_schema_export_is_valid_safe_and_atomic(self):
        with TemporaryDirectory() as directory:
            with patch(
                "integrations.management.commands.zoho_export_schema.get_zoho",
                return_value=fake_facade(),
            ):
                call_command("zoho_export_schema", output_dir=directory, stdout=StringIO())
            paths = {
                name: __import__("pathlib").Path(directory) / name
                for name in ("modules.json", "fields.json", "relationships.json", "schema.md")
            }
            self.assertTrue(all(path.exists() for path in paths.values()))
            modules = json.loads(paths["modules.json"].read_text(encoding="utf-8"))
            self.assertEqual(len(modules["modules"]), 2)
            all_content = "\n".join(
                path.read_text(encoding="utf-8") for path in paths.values()
            )
            self.assertNotIn("refresh", all_content.lower())
            self.assertNotIn("access_token", all_content.lower())
            self.assertNotIn("primary_email", all_content)
            self.assertFalse(list(__import__("pathlib").Path(directory).glob("*.tmp")))

    def test_schema_module_filter_and_no_relationships(self):
        with TemporaryDirectory() as directory:
            with patch(
                "integrations.management.commands.zoho_export_schema.get_zoho",
                return_value=fake_facade(),
            ):
                call_command(
                    "zoho_export_schema",
                    module="Polizas",
                    output_dir=directory,
                    no_relationships=True,
                    stdout=StringIO(),
                )
            data = json.loads(
                (__import__("pathlib").Path(directory) / "modules.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual([item["api_name"] for item in data["modules"]], ["Polizas"])

    def test_schema_export_summarizes_classified_omissions(self):
        facade = fake_facade()
        facade.metadata.list_fields = lambda _module: (_ for _ in ()).throw(
            ZohoAPIError(
                "El módulo no está admitido para esta operación.",
                status_code=400,
                zoho_code="INVALID_MODULE",
                backend="rest",
            )
        )
        output = StringIO()
        errors = StringIO()
        with TemporaryDirectory() as directory, patch(
            "integrations.management.commands.zoho_export_schema.get_zoho",
            return_value=facade,
        ):
            call_command(
                "zoho_export_schema",
                output_dir=directory,
                stdout=output,
                stderr=errors,
            )
        self.assertIn("- unsupported_module: 2", output.getvalue())
        self.assertNotIn("No fue posible consultar", errors.getvalue())
