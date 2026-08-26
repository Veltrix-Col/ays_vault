from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import SimpleTestCase, override_settings

from cotizacion_colectivos.services.risk_sandbox import (
    build_risk_payload, normalize_plate, resolve_risk_by_plate,
)


class RiskSandboxTests(SimpleTestCase):
    def test_builder_uses_confirmed_vehicle_fields(self):
        payload = build_risk_payload(name="VELTRIX TEST VEH 001", plate="vtx-001", model=2024)
        self.assertEqual(payload["Name"], "VTX001")
        self.assertEqual(payload["Tipo_de_riesgo"], "Vehículos")
        self.assertEqual(payload["Placa_del_vehiculo"], "VTX001")
        self.assertEqual(payload["Modelo"], 2024)
        self.assertEqual(set(payload), {
            "Name", "Tipo_de_riesgo", "Placa_del_vehiculo", "Marca_Tipo_Caracter_sticas",
            "Modelo", "Clase", "Ciudad", "Tipo_de_uso",
        })

    def test_plate_normalization_and_validation(self):
        self.assertEqual(normalize_plate(" vtx-001 "), "VTX001")
        self.assertEqual(normalize_plate(" 000 "), "000")
        self.assertEqual(normalize_plate("pjr-76d"), "PJR76D")
        with self.assertRaises(ValidationError):
            normalize_plate("ABC")

    @override_settings(ZOHO_ACTIVE_PROFILE="sandbox", ZOHO_SANDBOX_WRITE_ENABLED=True)
    def test_default_command_is_five_record_dry_run(self):
        output = StringIO()
        call_command("colectivos_seed_mobility_risks", stdout=output)
        text = output.getvalue()
        self.assertIn('"module": "Riesgos"', text)
        self.assertIn('"planned": 5', text)
        self.assertIn('"writes": 0', text)

    def test_plate_resolution_distinguishes_not_found_found_and_ambiguous(self):
        facade = Mock()
        facade.search.by_criteria.return_value = SimpleNamespace(records=())
        self.assertEqual(resolve_risk_by_plate(plate="VTX001", zoho=facade)["status"], "NOT_FOUND")

        facade.search.by_criteria.return_value = SimpleNamespace(records=(
            {"id": "4991513000270118607", "Placa_del_vehiculo": "vtx-001"},
        ))
        found = resolve_risk_by_plate(plate="VTX001", zoho=facade)
        self.assertEqual(found["status"], "FOUND")
        self.assertEqual(found["record_id"], "4991513000270118607")

        facade.search.by_criteria.return_value = SimpleNamespace(records=(
            {"id": "4991513000270118607", "Placa_del_vehiculo": "VTX001"},
            {"id": "4991513000270118608", "Placa_del_vehiculo": "VTX001"},
        ))
        self.assertEqual(resolve_risk_by_plate(plate="VTX001", zoho=facade)["status"], "AMBIGUOUS")

    def test_builder_does_not_accept_relationship_fields(self):
        payload = build_risk_payload(name="VELTRIX TEST VEH 001", plate="VTX001", model=2024)
        self.assertNotIn("P_liza", payload)
        self.assertNotIn("Asegurado", payload)

    def test_builder_accepts_current_mobility_picklist_values_from_public_form(self):
        payload = build_risk_payload(
            name="000333", plate="000333", model="2026",
            brand_reference="Prueba Marca La ultima", city="Medellín",
            use="Caserito",
        )
        self.assertEqual(payload["Name"], "000333")
        self.assertEqual(payload["Tipo_de_uso"], "Caserito")
        self.assertNotIn("Contacto_facturaci_n_dividida_colectivas", payload)

    def test_optional_mobility_risk_fields_are_omitted_when_empty(self):
        payload = build_risk_payload(
            name="000333", plate="000333", model="2026",
            brand_reference="Prueba Marca La ultima", vehicle_class="",
            city="", use="Caserito",
        )
        self.assertNotIn("Clase", payload)
        self.assertNotIn("Ciudad", payload)
        self.assertEqual(payload["Tipo_de_uso"], "Caserito")
