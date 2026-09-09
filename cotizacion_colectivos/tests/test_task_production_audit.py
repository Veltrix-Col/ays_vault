from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from integrations.zoho.schemas import FieldMetadata, ModuleMetadata, Organization, Page

from cotizacion_colectivos.excepciones_facturacion.application import PRODUCTION_WRITE_FLAGS
from cotizacion_colectivos.task_production_audit import audit_tasks_contract


def facade(*, write_enabled=False):
    fields = (
        FieldMetadata("Subject", "Asunto", "text", required=True, system_mandatory=True, length=255),
        FieldMetadata("Status", "Estado", "picklist", pick_list_values=(
            {"actual_value": "No iniciado", "display_value": "No iniciado", "active": True, "sequence_number": 1},
            {"actual_value": "Completed", "display_value": "Completada", "active": True, "sequence_number": 2},
        )),
        FieldMetadata("Owner", "Propietario", "ownerlookup", lookup={"module": {"api_name": "users"}}),
        FieldMetadata("tipo_de_solicitud", "Tipo de solicitud", "picklist", custom_field=True, pick_list_values=(
            {"actual_value": "Cotización", "display_value": "Cotización"},
        )),
        FieldMetadata("Observaciones", "Observaciones", "textarea", custom_field=True),
        FieldMetadata("Read_Only", "Calculado", "text", read_only=True),
        FieldMetadata("Modified_Time", "Modificado", "datetime", read_only=True),
    )
    zoho = SimpleNamespace(
        profile="production", backend_name="rest", config=SimpleNamespace(write_enabled=write_enabled),
        organization=SimpleNamespace(get=Mock(return_value=Organization("org", "Company", environment="production"))),
        metadata=SimpleNamespace(
            list_modules=Mock(return_value=(ModuleMetadata("Tasks", "Tasks", "Tareas", "Tarea"),)),
            list_fields=Mock(return_value=fields),
        ),
        records=SimpleNamespace(list=Mock(return_value=Page(records=(
            {"Status": "No iniciado", "Owner": {"id": "sensitive", "$se_module": "users"}, "Subject": "PII"},
        ), count=1))),
    )
    zoho.records.create = Mock()
    zoho.records.update = Mock()
    zoho.attachments = SimpleNamespace(upload=Mock())
    return zoho


@override_settings(**{flag: False for flag in PRODUCTION_WRITE_FLAGS})
class TaskProductionAuditTests(SimpleTestCase):
    def test_audit_detects_picklists_lookups_and_mandatory_flags(self):
        zoho = facade()
        result = audit_tasks_contract(zoho, audited_at=datetime(2026, 9, 7, tzinfo=UTC))
        self.assertEqual(result["mandatory_fields"], ["Subject"])
        self.assertEqual(result["system_mandatory_fields"], ["Subject"])
        self.assertEqual(result["read_only_fields"], ["Modified_Time", "Read_Only"])
        self.assertEqual(result["lookups"][0]["lookup_target"], "users")
        self.assertEqual(result["picklists"][0]["api_name"], "Status")
        self.assertTrue(result["picklists"][0]["picklist_values"][0]["active"])

    def test_serialization_is_deterministic_and_sanitized(self):
        zoho = facade()
        when = datetime(2026, 9, 7, tzinfo=UTC)
        first = audit_tasks_contract(zoho, audited_at=when)
        second = audit_tasks_contract(facade(), audited_at=when)
        rendered = json.dumps(first, ensure_ascii=False, sort_keys=True)
        self.assertEqual(rendered, json.dumps(second, ensure_ascii=False, sort_keys=True))
        self.assertNotIn("sensitive", rendered)
        self.assertNotIn("PII", rendered)

    def test_audit_is_four_reads_and_zero_writes(self):
        zoho = facade()
        result = audit_tasks_contract(zoho)
        self.assertEqual(result["audit"]["production_reads"], 4)
        self.assertEqual(result["audit"]["production_writes"], 0)
        zoho.records.create.assert_not_called()
        zoho.records.update.assert_not_called()
        zoho.attachments.upload.assert_not_called()
        self.assertEqual(zoho.records.list.call_count, 1)

    def test_rejects_non_production_and_write_enabled_facades(self):
        sandbox = facade()
        sandbox.profile = "sandbox"
        with self.assertRaises(ValueError):
            audit_tasks_contract(sandbox)
        with self.assertRaises(ValueError):
            audit_tasks_contract(facade(write_enabled=True))

    def test_publisher_comparison_uses_production_metadata(self):
        result = audit_tasks_contract(facade())
        comparison = result["publisher"]["comparison"]
        by_name = {item["field"]: item for item in comparison}
        self.assertTrue(by_name["Subject"]["compatible"])
        self.assertFalse(by_name["Correo_responsable"]["production_exists"])
        self.assertFalse(by_name["Correo_responsable"]["compatible"])
        configured = {
            item["field"]: item
            for item in result["publisher"]["configured_value_compatibility"]
        }
        self.assertEqual(configured["tipo_de_solicitud"]["confirmed"], ["Cotización"])

    def test_status_contract_uses_actual_and_display_values(self):
        result = audit_tasks_contract(facade())["status_contract"]
        self.assertEqual(result["open"]["actual_value"], "No iniciado")
        self.assertEqual(result["resolved"]["actual_value"], "Completed")
        self.assertEqual(result["resolved"]["display_value"], "Completada")

    @patch("cotizacion_colectivos.management.commands.colectivos_audit_tasks_production.get_zoho")
    def test_command_requires_confirmation_and_uses_read_only_rest_facade(self, get_zoho):
        with self.assertRaisesMessage(CommandError, "--allow-production-read"):
            call_command("colectivos_audit_tasks_production", profile="production")
        get_zoho.assert_not_called()
        get_zoho.return_value = facade()
        with TemporaryDirectory() as directory:
            call_command(
                "colectivos_audit_tasks_production", profile="production",
                allow_production_read=True, output_dir=directory,
            )
            self.assertTrue((Path(directory) / "tasks_contract.json").exists())
            self.assertTrue((Path(directory) / "tasks_contract.md").exists())
        get_zoho.assert_called_once_with(profile="production", backend="rest")
