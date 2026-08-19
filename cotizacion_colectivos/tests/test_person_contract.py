from types import SimpleNamespace
from unittest.mock import Mock

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from integrations.zoho.exceptions import ZohoSDKError

from cotizacion_colectivos.services.person_contract import (
    ContactsDryRunPublisher,
    build_contact_payload,
    resolve_contact_by_document,
)


class PersonContractTests(SimpleTestCase):
    def test_contact_command_diagnostic_is_sanitized_and_allowlisted(self):
        from cotizacion_colectivos.management.commands.zoho_create_test_contact import _safe_contact_diagnostic

        diagnostic = _safe_contact_diagnostic(ZohoSDKError(
            "access-token-secret refresh-token-secret person@example.test 123456789",
            status_code=401, detail_keys=("api_name", "Email"), detail_field="Email",
            detail_accepted_type="str", detail_given_type="str", detail_class="Record",
            detail_index=0, backend="sdk", operation="records.create", module="Contacts",
            sdk_exception_class="SDKException", sdk_code="AUTHENTICATION_ERROR",
            zoho_code="INVALID_TOKEN", zoho_status="error", request_sent=None,
        ))
        self.assertIn("category=sdk", diagnostic)
        self.assertIn("module=Contacts", diagnostic)
        self.assertNotIn("access-token-secret", diagnostic)
        self.assertNotIn("refresh-token-secret", diagnostic)
        self.assertNotIn("person@example.test", diagnostic)
        self.assertNotIn("123456789", diagnostic)

    def test_contact_command_diagnostic_uses_unknown_for_unavailable_values(self):
        from cotizacion_colectivos.management.commands.zoho_create_test_contact import _safe_contact_diagnostic

        diagnostic = _safe_contact_diagnostic(ZohoSDKError("secret"))
        self.assertIn("status_code=unknown", diagnostic)
        self.assertIn("request_sent=unknown", diagnostic)
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
