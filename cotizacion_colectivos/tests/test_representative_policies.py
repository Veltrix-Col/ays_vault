from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from integrations.zoho.schemas import FieldMetadata, Organization, Page
from integrations.zoho.exceptions import ZohoSDKError

from cotizacion_colectivos.representative_policies import (
    REPRESENTATIVE_POLICIES,
    _field_result,
    _get_full_record,
)


POLICY_ID = "1111111111111111111"
CONTACT_ID = "2222222222222222222"
RISK_ID = "3333333333333333333"
PRIVATE_NAME = "Nombre Personal Reservado"
PRIVATE_DOCUMENT = "9876543210"


FIELDS = {
    "Polizas": (
        FieldMetadata("Name", "Póliza", "text"),
        FieldMetadata("Layout", "Diseño", "layout", read_only=True),
        FieldMetadata("Ramo", "Ramo", "picklist", pick_list_values=({"display_value": "Salud colectivo"},)),
        FieldMetadata("Aseguradora1", "Aseguradora", "picklist", pick_list_values=({"display_value": "Aseguradora de prueba"},)),
        FieldMetadata("Estado_de_la_p_liza", "Estado", "picklist", pick_list_values=({"display_value": "Vigente"},)),
        FieldMetadata("Tomador_principal1", "Tomador", "lookup"),
        FieldMetadata("Modo_de_pago", "Forma de pago", "picklist", pick_list_values=({"display_value": "Fraccionado"},)),
        FieldMetadata("Frecuencia", "Periodicidad", "picklist", pick_list_values=({"display_value": "Mensual"},)),
        FieldMetadata("Valor_prima", "Prima", "currency"),
    ),
    "Riesgos1": (
        FieldMetadata("P_liza", "Número de póliza", "lookup"),
        FieldMetadata("Asegurado", "Asegurado", "lookup"),
        FieldMetadata("Beneficiario", "Beneficiario", "lookup"),
        FieldMetadata("Riesgo", "Riesgo", "lookup"),
        FieldMetadata("Estado", "Estado asegurado", "picklist", pick_list_values=({"display_value": "Activo"},)),
        FieldMetadata("Parentesco", "Parentesco", "picklist", pick_list_values=({"display_value": "Titular"},)),
        FieldMetadata("Pago_EMPLEADO_Sin_IVA", "Pago asegurado", "currency"),
    ),
    "Riesgos": (
        FieldMetadata("Layout", "Diseño", "layout", read_only=True),
        FieldMetadata("Tipo_de_riesgo", "Tipo de riesgo", "picklist", pick_list_values=({"display_value": "Persona"},)),
        FieldMetadata("Valor_prima", "Valor prima", "currency"),
    ),
    "Contacts": (
        FieldMetadata("Tipo_de_persona", "Tipo de persona", "picklist", pick_list_values=({"display_value": "Persona natural"},)),
        FieldMetadata("Tipo_ID", "Tipo ID", "picklist", pick_list_values=({"display_value": "CC"},)),
        FieldMetadata("N_mero_de_ID", "Número ID", "text"),
        FieldMetadata("Full_Name", "Nombre completo", "text", read_only=True),
    ),
}


class FakeMetadata:
    def __init__(self):
        self.calls = []

    def list_fields(self, module):
        self.calls.append(module)
        return FIELDS[module]


class FakeOrganization:
    def get(self):
        return Organization("production-org-secret", "Organización", environment="production")


class FakeSearch:
    def __init__(self):
        self.calls = []

    def by_field(self, **kwargs):
        self.calls.append(("field", kwargs))
        if kwargs["field"] == "Name":
            record = {"id": POLICY_ID, "Name": kwargs["value"], "Ramo": "Salud colectivo"}
        elif kwargs["module"] == "Polizas":
            record = {
                "id": POLICY_ID, "Name": "091000811814", "Layout": {"name": "Colectivos"},
                "Ramo": "Salud colectivo", "Aseguradora1": "Aseguradora de prueba",
                "Estado_de_la_p_liza": "Vigente", "Tomador_principal1": {"id": CONTACT_ID, "name": PRIVATE_NAME},
                "Modo_de_pago": "Fraccionado", "Frecuencia": "Mensual", "Valor_prima": 200000,
            }
        elif kwargs["module"] == "Contacts":
            record = {"id": CONTACT_ID, "Tipo_de_persona": "Persona natural", "Tipo_ID": "CC", "N_mero_de_ID": PRIVATE_DOCUMENT, "Full_Name": PRIVATE_NAME}
        else:
            record = {"id": RISK_ID, "Layout": {"name": "Persona"}, "Tipo_de_riesgo": "Persona", "Valor_prima": 3000}
        return Page(records=(record,), count=1)

    def by_criteria(self, **kwargs):
        self.calls.append(("criteria", kwargs))
        self.assert_read_shape(kwargs)
        return Page(records=({
            "id": "insured-secret-id", "P_liza": {"id": POLICY_ID, "name": "private"},
            "Asegurado": {"id": CONTACT_ID, "name": PRIVATE_NAME},
            "Beneficiario": {"id": CONTACT_ID, "name": PRIVATE_NAME},
            "Riesgo": {"id": RISK_ID, "name": "private risk"}, "Estado": "Activo",
            "Parentesco": "Titular", "Pago_EMPLEADO_Sin_IVA": 12345,
        },), count=1)

    @staticmethod
    def assert_read_shape(kwargs):
        assert kwargs["module"] == "Riesgos1"
        assert kwargs["criteria"] == f"(P_liza:equals:{POLICY_ID})"
        assert kwargs["limit"] <= 200


class FakeRecords:
    def __init__(self):
        self.calls = []

    def get_by_id(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["module"] == "Polizas":
            return {
                "id": POLICY_ID, "Name": "091000811814", "Layout": {"name": "Colectivos"},
                "Ramo": "Salud colectivo", "Aseguradora1": "Aseguradora de prueba",
                "Estado_de_la_p_liza": "Vigente", "Tomador_principal1": {"id": CONTACT_ID, "name": PRIVATE_NAME},
                "Modo_de_pago": "Fraccionado", "Frecuencia": "Mensual", "Valor_prima": 200000,
            }
        if kwargs["module"] == "Contacts":
            return {"id": CONTACT_ID, "Tipo_de_persona": "Persona natural", "Tipo_ID": "CC", "N_mero_de_ID": PRIVATE_DOCUMENT, "Full_Name": PRIVATE_NAME}
        return {"id": RISK_ID, "Layout": {"name": "Persona"}, "Tipo_de_riesgo": "Persona", "Valor_prima": 3000}


class FakeCoql:
    def __init__(self):
        self.calls = []

    def execute(self, query, **kwargs):
        self.calls.append((query, kwargs))
        if "FROM Contacts" in query:
            record = {"id": CONTACT_ID, "Tipo_de_persona": "Persona natural", "Tipo_ID": "CC", "N_mero_de_ID": PRIVATE_DOCUMENT, "Full_Name": PRIVATE_NAME}
        else:
            record = {"id": RISK_ID, "Layout": {"name": "Persona"}, "Tipo_de_riesgo": "Persona", "Valor_prima": 3000}
        return Page(records=(record,), count=1)


class FakeZoho:
    profile = "production"
    environment = "production"
    backend_name = "fake-read-only"
    organization = FakeOrganization()

    def __init__(self):
        self.metadata = FakeMetadata()
        self.search = FakeSearch()
        self.records = FakeRecords()
        self.coql = FakeCoql()


PATCH_TARGET = "cotizacion_colectivos.management.commands.colectivos_profile_representative_policies.get_zoho"


class RepresentativePoliciesTests(SimpleTestCase):
    def test_allowlist_is_exactly_the_five_authorized_policies(self):
        self.assertEqual(set(REPRESENTATIVE_POLICIES), {"091000811814", "158140", "1000166", "083002914855", "900000288971"})

    @patch(PATCH_TARGET)
    def test_rejects_sandbox_missing_confirmation_and_unknown_policy(self, get_zoho):
        with self.assertRaisesMessage(CommandError, "exclusivamente"):
            call_command("colectivos_profile_representative_policies", profile="sandbox", policy="091000811814", allow_production_read=True)
        with self.assertRaisesMessage(CommandError, "--allow-production-read"):
            call_command("colectivos_profile_representative_policies", profile="production", policy="091000811814")
        with self.assertRaisesMessage(CommandError, "allowlist"):
            call_command("colectivos_profile_representative_policies", profile="production", policy="una-poliza-no-autorizada", allow_production_read=True)
        get_zoho.assert_not_called()

    @patch(PATCH_TARGET)
    def test_generates_only_safe_atomic_aggregates_and_no_write_calls(self, get_zoho):
        facade = FakeZoho()
        get_zoho.return_value = facade
        with TemporaryDirectory() as directory, override_settings(BASE_DIR=directory):
            output = StringIO()
            call_command("colectivos_profile_representative_policies", profile="production", policy="091000811814", allow_production_read=True, stdout=output)
            artifact = Path(directory, "artifacts/zoho/colectivos/representative_policies/profile.json")
            docs = Path(directory, "docs/cotizacion_colectivos/representative_policies")
            self.assertTrue(artifact.exists())
            self.assertTrue((docs / "overview.md").exists())
            self.assertTrue((docs / "field_matrix.md").exists())
            self.assertTrue((docs / "excel_mapping.md").exists())
            rendered = artifact.read_text("utf-8") + "".join(path.read_text("utf-8") for path in docs.glob("*.md")) + output.getvalue()
            for secret in (POLICY_ID, CONTACT_ID, RISK_ID, PRIVATE_NAME, PRIVATE_DOCUMENT, "production-org-secret"):
                self.assertNotIn(secret, rendered)
            self.assertIn("Fraccionado", rendered)
            self.assertIn("Pago Mensual", rendered)
            self.assertFalse(any(hasattr(facade, name) for name in ("create", "update", "delete", "upsert", "write")))
            self.assertEqual(set(facade.metadata.calls), {"Polizas", "Riesgos1", "Riesgos", "Contacts"})
            self.assertEqual(facade.records.calls, [])
            self.assertLessEqual(len(facade.coql.calls), 2)
            self.assertTrue(all(call[1]["limit"] <= 50 for call in facade.coql.calls))

    def test_field_classification_reports_coverage_without_original_values(self):
        field = FieldMetadata("Valor_prima", "Prima", "currency")
        result = _field_result(field, [1000, None, 0])
        self.assertEqual(result["populated"], 2)
        self.assertEqual(result["coverage_percent"], 66.7)
        self.assertEqual(result["value_categories"], {"positive": 1, "zero": 1})
        self.assertNotIn("1000", json.dumps(result))

    def test_unsafe_picklist_values_are_not_emitted(self):
        field = FieldMetadata("Ejecutivo", "Ejecutivo asignado", "picklist", pick_list_values=({"display_value": PRIVATE_NAME},))
        result = _field_result(field, [PRIVATE_NAME])
        self.assertEqual(result["value_categories"], {"picklist_present": 1})
        self.assertEqual(result["picklist_values"], [])
        self.assertNotIn(PRIVATE_NAME, json.dumps(result))

    def test_sdk_layout_object_is_reduced_to_safe_category(self):
        field = FieldMetadata("Layout", "Diseño", "layout", read_only=True)
        value = type("SDKLayoutObject", (), {})()
        result = _field_result(field, [value])
        self.assertEqual(result["value_categories"], {"configured": 1})
        self.assertNotIn("SDKLayoutObject", json.dumps(result))

    def test_layout_incompatible_field_isolated_without_losing_safe_fields(self):
        class SelectiveRecords:
            pass

        class SelectiveSearch:
            def by_field(self, **kwargs):
                if "Unsupported_for_layout" in kwargs["fields"]:
                    raise ZohoSDKError("private SDK response")
                return Page(records=({"id": POLICY_ID, "Ramo": "Salud colectivo"},), count=1)

        facade = type("Facade", (), {"records": SelectiveRecords(), "search": SelectiveSearch()})()
        metadata = (
            FieldMetadata("Ramo", "Ramo", "picklist"),
            FieldMetadata("Unsupported_for_layout", "No disponible", "text"),
        )
        record = _get_full_record(facade, "Polizas", POLICY_ID, metadata)
        self.assertEqual(record["Ramo"], "Salud colectivo")
        self.assertEqual(record["__unavailable_fields__"], ("Unsupported_for_layout",))
        self.assertNotIn("private SDK response", json.dumps(record))
