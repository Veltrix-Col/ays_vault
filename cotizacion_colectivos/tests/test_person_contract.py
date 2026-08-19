from types import SimpleNamespace
from unittest.mock import Mock

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from cotizacion_colectivos.services.person_contract import (
    ContactsDryRunPublisher,
    build_contact_payload,
    resolve_contact_by_document,
)


class PersonContractTests(SimpleTestCase):
    def test_payload_allowlist_requires_last_name_and_excludes_full_name_and_group(self):
        payload = build_contact_payload({
            "First_Name": "Ana", "Last_Name": "Pérez", "Tipo_ID": "CC",
            "N_mero_de_ID": "123", "Full_Name": "Ana Pérez",
            "Grupo_econ_mico": "No enviar", "Tratamiento_de_datos": "No",
        })
        self.assertEqual(payload["Tipo_de_persona"], "Persona natural")
        self.assertEqual(payload["Estado"], "Prospecto")
        self.assertNotIn("Full_Name", payload)
        self.assertNotIn("Grupo_econ_mico", payload)

    def test_resolver_distinguishes_found_type_mismatch_and_ambiguous(self):
        search = Mock()
        facade = SimpleNamespace(search=search)
        search.by_criteria.return_value = SimpleNamespace(records=({
            "id": "700000000000000001", "Full_Name": "Ana Pérez", "N_mero_de_ID": "123", "Tipo_ID": "CC",
        },))
        self.assertEqual(resolve_contact_by_document(document="123", document_type="CC", zoho=facade)["status"], "FOUND")
        self.assertEqual(resolve_contact_by_document(document="123", document_type="CE", zoho=facade)["status"], "TYPE_MISMATCH")
        search.by_criteria.return_value = SimpleNamespace(records=tuple([
            {"id": "700000000000000001", "N_mero_de_ID": "123", "Tipo_ID": "CC"},
            {"id": "700000000000000002", "N_mero_de_ID": "123", "Tipo_ID": "CC"},
        ]))
        self.assertEqual(resolve_contact_by_document(document="123", document_type="CC", zoho=facade)["status"], "AMBIGUOUS")

    def test_dry_run_never_writes_and_invalid_input_is_blocked(self):
        result = ContactsDryRunPublisher().dry_run({"Last_Name": "Pérez", "Tipo_ID": "CC", "N_mero_de_ID": "123"})
        self.assertEqual(result["writes"], 0)
        with self.assertRaises(ValidationError):
            build_contact_payload({"First_Name": "Sólo nombre", "Tipo_ID": "CC", "N_mero_de_ID": "123"})
