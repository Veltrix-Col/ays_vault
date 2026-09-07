import json
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.template import engines
from django.test import SimpleTestCase, override_settings
from django.urls import resolve

from cotizacion_colectivos.services.individual_entities import effective_candidate, promote_created_people, resolve_common_people_entities, resolve_mobility_entities, synchronize_risk_insured
from cotizacion_colectivos.services.operational_entities import resolve_operational_entities
from cotizacion_colectivos.services.person_contract import build_contact_payload


class _Page:
    def __init__(self, records=()):
        self.records = tuple(records)


class _Facade:
    def __init__(self, risks=(), contacts=(), subrisks=()):
        self._risks, self._contacts, self._subrisks = risks, contacts, subrisks
        self.records = SimpleNamespace(get_by_id=lambda **kwargs: {"id": "4991513000000000001", "Name": "Póliza QA"})
        self.search = SimpleNamespace(by_criteria=self.search)

    def search(self, *, module, **kwargs):
        if module == "Polizas":
            return _Page(({"id": "4991513000000000001", "Name": "Póliza QA"},))
        if module == "Riesgos":
            return _Page(self._risks)
        if module == "Contacts":
            return _Page(self._contacts)
        return _Page(self._subrisks)


class IndividualEntityResolutionTests(SimpleTestCase):
    @patch("cotizacion_colectivos.services.operational_entities.get_contacts_publisher")
    @patch("cotizacion_colectivos.services.operational_entities.resolve_contact_by_document", return_value={"status": "NOT_FOUND"})
    def test_novelty_contact_create_uses_shared_individual_contract(self, resolve, factory):
        publisher = Mock()
        publisher.create.return_value = {"record_id": "CONTACT-NEW"}
        factory.return_value = publisher
        state = SimpleNamespace(contact_zoho_id="", risk_zoho_id="", subrisk_zoho_id="", policy_remote_id="")
        result = resolve_operational_entities(
            payload={
                "id_type": "CC", "document": "1019059650", "first_name": "Camilo",
                "last_name": "Vargas", "birth_date": "1991-04-19",
            },
            state=state, profile="sandbox", confirmation="CONTACT_CONFIRM",
            required=("contact",),
        )
        self.assertEqual(result["status"], "PUBLISHED")
        factory.assert_called_once_with(profile="sandbox", confirmation="CONTACT_CONFIRM")
        publisher.create.assert_called_once_with(
            {
                "First_Name": "Camilo", "Last_Name": "Vargas", "Tipo_ID": "CC",
                "N_mero_de_ID": "1019059650", "Date_of_Birth": "1991-04-19",
                "Email": "", "Phone": "", "Mobile": "",
            },
            zoho=None,
            status="Cliente",
        )

    @patch("cotizacion_colectivos.services.operational_entities.resolve_contact_by_document", return_value={"status": "FOUND", "record_id": "CONTACT-OPTIONAL"})
    def test_novelty_contact_contract_does_not_require_optional_email_or_phone(self, resolve):
        state = SimpleNamespace(contact_zoho_id="", risk_zoho_id="", subrisk_zoho_id="", policy_remote_id="")
        result = resolve_operational_entities(
            payload={"id_type": "CC", "document": "1019059650", "first_name": "Camilo", "last_name": "Vargas"},
            state=state, profile="sandbox", confirmation="CONTACT_CONFIRM", required=("contact",),
        )
        self.assertEqual(result["status"], "PUBLISHED")
        self.assertEqual(result["entities"]["contact"], "CONTACT-OPTIONAL")

    @patch("cotizacion_colectivos.services.operational_entities.get_contacts_publisher")
    @patch("cotizacion_colectivos.services.operational_entities.resolve_contact_by_document", return_value={"status": "NOT_FOUND"})
    def test_novelty_contact_payload_preserves_email_phone_aliases(self, resolve, factory):
        publisher = Mock()
        publisher.create.return_value = {"record_id": "CONTACT-ALIAS"}
        factory.return_value = publisher
        state = SimpleNamespace(contact_zoho_id="", risk_zoho_id="", subrisk_zoho_id="", policy_remote_id="")
        result = resolve_operational_entities(
            payload={"id_type": "CC", "document": "123456", "first_name": "Ana", "last_name": "Pérez", "correo": "ana@example.test", "telefono": "3000000000"},
            state=state, profile="sandbox", confirmation="CONTACT_CONFIRM", required=("contact",),
        )
        self.assertEqual(result["status"], "PUBLISHED")
        payload = publisher.create.call_args.args[0]
        self.assertEqual(payload["Email"], "ana@example.test")
        self.assertEqual(payload["Phone"], "3000000000")

    @patch("cotizacion_colectivos.services.operational_entities.get_contacts_publisher")
    @patch("cotizacion_colectivos.services.operational_entities.resolve_contact_by_document")
    def test_vg_deudores_missing_parentesco_blocks_before_contact_write(self, resolve, factory):
        state = SimpleNamespace(contact_zoho_id="", risk_zoho_id="", subrisk_zoho_id="", policy_remote_id="4991513000270954040")
        with self.assertRaisesMessage(Exception, "Parentesco"):
            resolve_operational_entities(
                payload={"id_type": "CC", "document": "123456", "first_name": "Ana", "last_name": "Pérez", "entry_date": "2026-08-31"},
                state=state, profile="sandbox", confirmation="CONTACT_CONFIRM", branch_name="VG deudores",
                required=("contact", "subrisk"),
            )
        resolve.assert_not_called()
        factory.assert_not_called()

    @patch("cotizacion_colectivos.services.operational_entities.create_subrisk_sandbox", return_value={"record_id": "4991513000270954999"})
    @patch("cotizacion_colectivos.services.operational_entities.resolve_contact_by_document", return_value={"status": "FOUND", "record_id": "4991513000270954001"})
    def test_novelty_vg_deudores_resolves_contact_and_subrisk_without_risk(self, resolve, create):
        state = SimpleNamespace(contact_zoho_id="", risk_zoho_id="", subrisk_zoho_id="", policy_remote_id="4991513000270954040")
        result = resolve_operational_entities(
            payload={
                "id_type": "CC", "document": "123456", "first_name": "Ana",
                "last_name": "Pérez", "birth_date": "1990-01-01",
                "email": "ana@example.test", "phone": "3000000000",
                "name": "Ana Pérez", "entry_date": "2026-08-31",
                "parentesco": "Afiliado", "plan": "Deudores",
            },
            state=state, profile="sandbox", confirmation="CONTACT_CONFIRM",
            subrisk_confirmation="SUBRISK_CONFIRM", branch_name="VG deudores",
            required=("contact", "subrisk"),
        )
        self.assertEqual(result["status"], "PUBLISHED")
        self.assertEqual(result["entities"]["contact"], "4991513000270954001")
        self.assertEqual(result["entities"]["subrisk"], "4991513000270954999")
        self.assertEqual(result["entities"]["risk"], "")
        create.assert_called_once()
        self.assertEqual(create.call_args.kwargs["confirmation"], "SUBRISK_CONFIRM")

    @patch("cotizacion_colectivos.services.operational_entities.create_subrisk_sandbox")
    @patch("cotizacion_colectivos.services.operational_entities.resolve_contact_by_document", return_value={"status": "FOUND", "record_id": "4991513000270954001"})
    def test_novelty_other_vg_keeps_contact_and_blocks_subrisk_contract(self, resolve, create):
        state = SimpleNamespace(contact_zoho_id="", risk_zoho_id="", subrisk_zoho_id="", policy_remote_id="4991513000270954040")
        result = resolve_operational_entities(
            payload={"id_type": "CC", "document": "123456", "first_name": "Ana", "last_name": "Pérez", "birth_date": "1990-01-01", "email": "ana@example.test", "phone": "3000000000", "name": "Ana Pérez", "entry_date": "2026-08-31", "parentesco": "Afiliado"},
            state=state, profile="sandbox", confirmation="CONTACT_CONFIRM", branch_name="VG patronal",
            # The caller may only require Contact for a non-enabled VG, but
            # the contractual block must still prevent PUBLISHED.
            required=("contact",),
        )
        self.assertEqual(state.contact_zoho_id, "4991513000270954001")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("contrato Zoho de VG patronal", result["blocked_reason"])
        self.assertEqual(result["entities"]["contact"], "4991513000270954001")
        self.assertEqual(result["entities"]["subrisk"], "")
        create.assert_not_called()

    @patch("cotizacion_colectivos.services.operational_entities.create_subrisk_sandbox", return_value={"record_id": "SUBRISK-1"})
    @patch("cotizacion_colectivos.services.operational_entities.resolve_contact_by_document", return_value={"status": "FOUND", "record_id": "4991513000270954001"})
    def test_life_group_human_policy_label_uses_deudores_contract(self, resolve, create):
        state = SimpleNamespace(contact_zoho_id="", risk_zoho_id="", subrisk_zoho_id="", policy_remote_id="4991513000270954040")
        result = resolve_operational_entities(
            payload={"id_type": "CC", "document": "123456", "first_name": "Ana", "last_name": "Pérez", "entry_date": "2026-08-31", "parentesco": "Afiliado"},
            state=state, profile="sandbox", confirmation="CONTACT_CONFIRM",
            branch_name="Vida grupo de deudores", required=("contact", "subrisk"),
        )
        self.assertEqual(result["status"], "PUBLISHED")
        self.assertEqual(result["entities"]["contact"], "4991513000270954001")
        self.assertEqual(result["entities"]["subrisk"], "SUBRISK-1")
        create.assert_called_once()

    @patch("cotizacion_colectivos.services.operational_entities.create_mobility_subrisk_sandbox", return_value={"record_id": "4991513000270954998"})
    @patch("cotizacion_colectivos.services.operational_entities.create_sandbox_risk", return_value={"record_id": "4991513000270954997"})
    @patch("cotizacion_colectivos.services.operational_entities.resolve_risk_by_plate", return_value={"status": "NOT_FOUND"})
    @patch("cotizacion_colectivos.services.operational_entities.resolve_contact_by_document", return_value={"status": "FOUND", "record_id": "4991513000270954001"})
    def test_novelty_mobility_resolves_contact_risk_and_subrisk(self, contact, risk_lookup, risk_create, subrisk_create):
        state = SimpleNamespace(contact_zoho_id="", risk_zoho_id="", subrisk_zoho_id="", policy_remote_id="4991513000270954040", branch_code="40")
        zoho = SimpleNamespace(search=SimpleNamespace(by_criteria=Mock(return_value=SimpleNamespace(records=()))))
        order = []
        risk_lookup.side_effect = lambda **kwargs: (order.append("risk"), {"status": "NOT_FOUND"})[1]
        risk_create.side_effect = lambda *args, **kwargs: (order.append("risk_create"), {"record_id": "4991513000270954997"})[1]
        subrisk_create.side_effect = lambda *args, **kwargs: (order.append("subrisk"), {"record_id": "4991513000270954998"})[1]
        result = resolve_operational_entities(
            payload={"id_type": "CC", "document": "123456", "first_name": "Ana", "last_name": "Pérez", "plate": "ABC123", "model": "2024", "entry_date": "2026-08-31", "branch": "MOVILIDAD"},
            state=state, profile="sandbox", confirmation="CONTACT_CONFIRM", required=("contact", "risk", "subrisk"), branch_name="Movilidad colectivo", zoho=zoho,
            on_contact_resolved=lambda _contact_id: order.append("attachment"),
        )
        self.assertEqual(result["status"], "PUBLISHED")
        self.assertEqual(result["entities"]["risk"], "4991513000270954997")
        self.assertEqual(result["entities"]["subrisk"], "4991513000270954998")
        self.assertEqual(subrisk_create.call_args.kwargs["operational"], True)
        self.assertLess(order.index("attachment"), order.index("risk"))
        self.assertLess(order.index("risk_create"), order.index("subrisk"))

    @patch("cotizacion_colectivos.services.operational_entities.create_subrisk_sandbox")
    @patch("cotizacion_colectivos.services.operational_entities.resolve_contact_by_document", return_value={"status": "FOUND", "record_id": "4991513000270954001"})
    def test_novelty_exequial_resolves_contact_without_subrisk_write(self, contact, subrisk_create):
        state = SimpleNamespace(contact_zoho_id="", risk_zoho_id="", subrisk_zoho_id="", policy_remote_id="4991513000270954040", branch_code="86")
        result = resolve_operational_entities(
            payload={"id_type": "CC", "document": "123456", "first_name": "Ana", "last_name": "Pérez"},
            state=state, profile="sandbox", confirmation="CONTACT_CONFIRM", required=("contact",), branch_name="Exequial colectivo",
        )
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["entities"]["contact"], "4991513000270954001")
        subrisk_create.assert_not_called()

    @patch("cotizacion_colectivos.services.individual_entities.resolve_contact_by_document")
    @patch("cotizacion_colectivos.services.individual_entities.decrypt", side_effect=lambda value: value)
    def test_health_resolves_affiliate_and_multiple_insured_people_without_duplication(self, decrypt, resolve):
        resolve.side_effect = lambda **kwargs: (
            {"status": "FOUND", "record_id": f"CONTACT-{kwargs['document']}"}
            if kwargs["document"] in {"111", "222", "333"}
            else {"status": "NOT_FOUND"}
        )
        quotation = self.person_quotation("salud", [
            {"entity_key": "people-a", "is_requester": True, "document": "111", "first_name": "Afiliado"},
            {"entity_key": "people-b", "is_requester": False, "document": "222", "first_name": "María"},
            {"entity_key": "people-c", "is_requester": False, "document": "333", "first_name": "José"},
        ])
        result = resolve_common_people_entities(quotation=quotation)
        self.assertEqual([item["owner_key"] for item in result["people"]], ["affiliate", "people-b", "people-c"])
        self.assertEqual([item["status"] for item in result["people"]], ["found", "found", "found"])
        self.assertEqual(len({item["document"] for item in result["people"]}), 3)

    @patch("cotizacion_colectivos.services.individual_entities.unsign_record_context", return_value={"id": "4991513000270954040"})
    @patch("cotizacion_colectivos.services.individual_entities.resolve_contact_by_document", return_value={"status": "FOUND", "record_id": "4991513000270954001"})
    @patch("cotizacion_colectivos.services.individual_entities.decrypt", side_effect=lambda value: value)
    def test_health_accepted_prepares_subrisk_after_contact(self, decrypt, resolve, unsign):
        quotation = self.person_quotation("salud", [{"entity_key": "people-health", "is_requester": True, "document": "111", "first_name": "Ana"}])
        payload = json.loads(quotation.encrypted_payload)
        payload["context"] = {"policy_token": "signed-policy"}
        quotation.encrypted_payload = json.dumps(payload)

        result = resolve_common_people_entities(quotation=quotation, include_subrisk=True)

        self.assertEqual(result["people"][0]["remote_id"], "4991513000270954001")
        self.assertEqual(result["subrisks"][0]["status"], "not_found")
        self.assertEqual(result["subrisks"][0]["candidate"]["Ramo"], "Salud colectivo")
        self.assertEqual(result["subrisks"][0]["candidate"]["Asegurado"], {"id": "4991513000270954001"})

    @patch("cotizacion_colectivos.services.individual_entities.resolve_contact_by_document", return_value={"status": "NOT_FOUND"})
    @patch("cotizacion_colectivos.services.individual_entities.decrypt", side_effect=lambda value: value)
    def test_vida_people_owner_keys_resolve_independently(self, decrypt, resolve):
        quotation = self.person_quotation("vida", [
            {"entity_key": "people-juan", "document": "444", "first_name": "Juan"},
            {"entity_key": "people-maria", "document": "555", "first_name": "María"},
        ])
        result = resolve_common_people_entities(quotation=quotation)
        self.assertEqual([item["owner_key"] for item in result["people"]], ["affiliate", "people-juan", "people-maria"])
        self.assertEqual([item["status"] for item in result["people"]], ["not_found"] * 3)
        self.assertEqual(result["people"][2]["candidate"]["N_mero_de_ID"], "555")

    @patch("cotizacion_colectivos.services.individual_entities.build_life_group_subrisk_payload")
    @patch("cotizacion_colectivos.services.individual_entities.unsign_record_context", return_value={"id": "4991513000270954040"})
    @patch("cotizacion_colectivos.services.individual_entities.resolve_contact_by_document", return_value={"status": "FOUND", "record_id": "4991513000270954041"})
    @patch("cotizacion_colectivos.services.individual_entities.decrypt", side_effect=lambda value: value)
    def test_vida_resolution_prepares_policy_subrisk_after_contact(self, decrypt, resolve, unsign, builder):
        quotation = self.person_quotation("vida", [{"entity_key": "people-vida", "document": "444", "first_name": "Juan"}])
        payload = json.loads(quotation.encrypted_payload)
        payload["context"] = {"policy_token": "signed-policy"}
        payload["fields"]["parentesco"] = "Afiliado"
        quotation.encrypted_payload = json.dumps(payload)
        facade = SimpleNamespace(records=SimpleNamespace(get_by_id=Mock(return_value={"id": "4991513000270954040", "Ramo": "VG deudores"})))
        builder.return_value = {"Name": "Juan", "Ramo": "VG deudores"}
        result = resolve_common_people_entities(quotation=quotation, zoho=facade, include_subrisk=True)
        self.assertEqual(result["subrisks"][0]["status"], "not_found")
        self.assertEqual(result["subrisks"][0]["candidate"], {"Name": "Juan", "Ramo": "VG deudores"})
        self.assertEqual(builder.call_args.kwargs["ramo"], "VG deudores")

    @patch("cotizacion_colectivos.services.individual_entities.build_life_group_subrisk_payload")
    @patch("cotizacion_colectivos.services.individual_entities.unsign_record_context", return_value={"id": "4991513000270954040"})
    @patch("cotizacion_colectivos.services.individual_entities.resolve_contact_by_document", return_value={"status": "FOUND", "record_id": "4991513000270954041"})
    @patch("cotizacion_colectivos.services.individual_entities.decrypt", side_effect=lambda value: value)
    def test_vida_persisted_relationship_flows_to_riesgos1(self, decrypt, resolve, unsign, builder):
        quotation = self.person_quotation("vida", [{"entity_key": "people-vida", "document": "444", "first_name": "Juan"}])
        payload = json.loads(quotation.encrypted_payload)
        payload["context"] = {"policy_token": "signed-policy"}
        payload["fields"]["relationship"] = "Hijo"
        quotation.encrypted_payload = json.dumps(payload)
        facade = SimpleNamespace(records=SimpleNamespace(get_by_id=Mock(return_value={"id": "4991513000270954040", "Ramo": "VG deudores"})))
        builder.return_value = {"Name": "Juan", "Ramo": "VG deudores", "Parentesco": "Hijo"}

        result = resolve_common_people_entities(quotation=quotation, zoho=facade, include_subrisk=True)

        self.assertEqual(result["subrisks"][0]["status"], "not_found")
        self.assertEqual(result["subrisks"][0]["candidate"]["Parentesco"], "Hijo")
        self.assertEqual(builder.call_args.kwargs["parentesco"], "Hijo")

    @patch("cotizacion_colectivos.services.individual_entities.resolve_contact_by_document", return_value={"status": "FOUND", "record_id": "4991513000270954041"})
    @patch("cotizacion_colectivos.services.individual_entities.unsign_record_context", return_value={"id": "4991513000270954040"})
    @patch("cotizacion_colectivos.services.individual_entities.decrypt", side_effect=lambda value: value)
    def test_vida_missing_parentesco_keeps_contact_and_blocks_riesgos1(self, decrypt, unsign, resolve):
        quotation = self.person_quotation("vida", [{"entity_key": "people-vida", "document": "444", "first_name": "Juan"}])
        payload = json.loads(quotation.encrypted_payload)
        payload["context"] = {"policy_token": "signed-policy"}
        quotation.encrypted_payload = json.dumps(payload)
        facade = SimpleNamespace(records=SimpleNamespace(get_by_id=Mock(return_value={"id": "4991513000270954040", "Ramo": "VG deudores"})))
        result = resolve_common_people_entities(quotation=quotation, zoho=facade, include_subrisk=True)
        self.assertEqual(result["people"][0]["remote_id"], "4991513000270954041")
        self.assertEqual(result["subrisks"][0]["status"], "blocked")
        self.assertEqual(result["subrisks"][0]["candidate"]["Parentesco"], "")
        self.assertEqual(result["subrisks"][0]["candidate"]["Ramo"], "VG deudores")

    @patch("cotizacion_colectivos.services.individual_entities.build_life_group_subrisk_payload")
    @patch("cotizacion_colectivos.services.individual_entities.resolve_contact_by_document", return_value={"status": "FOUND", "record_id": "4991513000270954041"})
    @patch("cotizacion_colectivos.services.individual_entities.unsign_record_context", return_value={"id": "4991513000270954040"})
    @patch("cotizacion_colectivos.services.individual_entities.decrypt", side_effect=lambda value: value)
    def test_vida_parentesco_correction_prepares_explicit_subrisk_action(self, decrypt, unsign, resolve, builder):
        quotation = self.person_quotation("vida", [{"entity_key": "people-vida", "document": "444", "first_name": "Juan"}])
        payload = json.loads(quotation.encrypted_payload)
        payload["context"] = {"policy_token": "signed-policy"}
        quotation.encrypted_payload = json.dumps(payload)
        quotation.safe_metadata = {"zoho_entity_corrections": {"subrisk:0": {"Parentesco": "Titular"}}}
        facade = SimpleNamespace(records=SimpleNamespace(get_by_id=Mock(return_value={"id": "4991513000270954040", "Ramo": "VG deudores"})))
        builder.return_value = {"Name": "Juan", "Ramo": "VG deudores", "Parentesco": "Titular"}

        result = resolve_common_people_entities(quotation=quotation, zoho=facade, include_subrisk=True)

        self.assertEqual(result["subrisks"][0]["status"], "not_found")
        self.assertEqual(result["subrisks"][0]["candidate"]["Parentesco"], "Titular")
        builder.assert_called_once()

    @patch("cotizacion_colectivos.services.individual_entities.resolve_contact_by_document", return_value={"status": "FOUND", "record_id": "CONTACT-1"})
    @patch("cotizacion_colectivos.services.individual_entities.decrypt", side_effect=lambda value: value)
    def test_exequial_family_documents_keep_distinct_owners(self, decrypt, resolve):
        quotation = self.person_quotation("exequial", [
            {"entity_key": "people-a", "document": "777", "first_name": "Ana"},
            {"entity_key": "people-b", "document": "888", "first_name": "Luis"},
        ])
        result = resolve_common_people_entities(quotation=quotation)
        self.assertEqual({item["owner_key"] for item in result["people"]}, {"affiliate", "people-a", "people-b"})
        self.assertEqual({item["document"] for item in result["people"]}, {"111111", "777", "888"})

    def person_quotation(self, branch, rows):
        payload = {
            "schema": branch,
            "fields": {
                "first_name": "Afiliado", "last_name": "Principal", "requester_id_type": "CC",
                "requester_document": "111111", "requester_birth_date": "1990-01-01",
                "requester_email": "affiliate@example.com", "requester_phone": "3000000000",
            },
            "groups": {"people": rows}, "context": {},
        }
        return SimpleNamespace(
            encrypted_payload=json.dumps(payload), branch_slug=branch, safe_metadata={},
            save=lambda **kwargs: None,
        )

    def test_created_people_lookup_promotes_stale_entity_without_touching_other_role(self):
        entity_people = [
            {"document": "888 989 898", "candidate": {"Tipo_ID": "CC"}, "role": "Persona principal", "status": "not_found"},
            {"document": "888989898", "candidate": {"Tipo_ID": "CC"}, "role": "Asegurado del vehículo", "status": "not_found"},
        ]
        people_lookup = [{
            "document": "888-989-898", "candidate": {"Tipo_ID": "cc"},
            "role": "Persona principal", "status": "found", "created": True,
            "contact_id": "4991513000271052002",
        }]
        promoted, changed = promote_created_people(entity_people, people_lookup)
        self.assertTrue(changed)
        self.assertEqual(promoted[0]["status"], "created")
        self.assertEqual(promoted[0]["remote_id"], "4991513000271052002")
        self.assertFalse(promoted[1].get("created", False))
        self.assertIsNone(promoted[1].get("remote_id"))

    def test_created_canonical_person_promotes_nested_insured_snapshot(self):
        entities = {
            "people": [{"document": "888888", "role": "Asegurado del vehículo", "status": "created", "created": True, "remote_id": "4991513000271057001", "contact_id": "4991513000271057001", "candidate": {"Tipo_ID": "CC"}}],
            "risks": [{"insured_document": "888888", "insured_same_as_affiliate": False, "insured": {"document": "888888", "status": "not_found"}}],
        }
        result, changed = synchronize_risk_insured(entities)
        self.assertTrue(changed)
        self.assertEqual(result["risks"][0]["insured"]["status"], "created")
        self.assertEqual(result["risks"][0]["insured"]["remote_id"], "4991513000271057001")

    @patch("cotizacion_colectivos.services.individual_entities.unsign_record_context", return_value={"id": "4991513000000000001", "type": "policy"})
    @patch("cotizacion_colectivos.services.individual_entities.decrypt")
    def test_subrisk_dependencies_recalculate_after_created_risk_and_insured(self, decrypt, unsign):
        decrypt.side_effect = lambda value: value
        quotation = self.quotation([{"plate": "ABC123", "insured_same_as_requester": False, "insured_document": "888888"}])
        quotation.safe_metadata = {"zoho_entities": {
            "people": [
                {"document": "444444444", "role": "Persona principal", "status": "created", "created": True, "remote_id": "AFF-1", "candidate": {"Tipo_ID": "CC"}},
                {"document": "888888", "role": "Asegurado del vehículo", "status": "created", "created": True, "remote_id": "INS-1", "candidate": {"Tipo_ID": "CC"}},
            ],
            "risks": [{"status": "created", "created": True, "remote_id": "RISK-1"}],
            "subrisks": [{"status": "blocked", "reason": "Faltan el Riesgo / vehículo, el Asegurado."}],
        }}
        facade = _Facade(risks=(), contacts=())
        result = resolve_mobility_entities(quotation=quotation, zoho=facade)
        self.assertEqual(result["subrisks"][0]["status"], "not_found")
        self.assertNotIn("Riesgo / vehículo", result["subrisks"][0].get("reason", ""))
        self.assertNotIn("Asegurado", result["subrisks"][0].get("reason", ""))

    @patch("cotizacion_colectivos.services.individual_entities.resolve_policy_by_number", return_value={"status": "NOT_FOUND"})
    @patch("cotizacion_colectivos.services.individual_entities.decrypt", side_effect=lambda value: value)
    @patch("cotizacion_colectivos.services.individual_entities.unsign_record_context", side_effect=ValueError("invalid policy context"))
    def test_subrisk_blocked_reason_only_reports_unresolved_policy(self, unsign, decrypt, resolve_policy):
        quotation = self.quotation([{"plate": "ABC123", "insured_same_as_requester": False, "insured_document": "888888"}])
        quotation.safe_metadata = {"zoho_entities": {
            "people": [
                {"document": "444444444", "role": "Persona principal", "status": "created", "created": True, "remote_id": "AFF-1", "candidate": {"Tipo_ID": "CC"}},
                {"document": "888888", "role": "Asegurado del vehículo", "status": "created", "created": True, "remote_id": "INS-1", "candidate": {"Tipo_ID": "CC"}},
            ],
            "risks": [{"status": "created", "created": True, "remote_id": "RISK-1", "risk_id": "RISK-1"}],
        }}
        result = resolve_mobility_entities(quotation=quotation, zoho=_Facade())
        self.assertNotEqual(result["policy"]["status"], "found")
        self.assertEqual(result["subrisks"][0]["status"], "blocked")
        reason = result["subrisks"][0].get("reason", "")
        self.assertIn("póliza", reason)
        self.assertNotIn("Riesgo", reason)
        self.assertNotIn("Asegurado", reason)
        self.assertNotIn("Afiliado", reason)

    @override_settings(ZOHO_ACTIVE_PROFILE="sandbox")
    @patch("cotizacion_colectivos.services.individual_entities.resolve_policy_by_number")
    @patch("cotizacion_colectivos.services.individual_entities.unsign_record_context", return_value={"id": "PRODUCTION-ID", "type": "policy"})
    @patch("cotizacion_colectivos.services.individual_entities.decrypt", side_effect=lambda value: value)
    def test_sandbox_policy_uses_logical_number_not_signed_remote_id(self, decrypt, unsign, resolve_policy):
        resolve_policy.return_value = {"status": "FOUND", "record_id": "4991513000270954040"}
        quotation = self.quotation([])
        result = resolve_mobility_entities(quotation=quotation, zoho=_Facade())
        self.assertEqual(result["policy"]["status"], "found")
        self.assertEqual(result["policy"]["remote_id"], "4991513000270954040")
        resolve_policy.assert_called_once()
        self.assertEqual(resolve_policy.call_args.kwargs["policy_number"], "Póliza QA")

    @override_settings(ZOHO_ACTIVE_PROFILE="sandbox")
    @patch("cotizacion_colectivos.services.individual_entities.resolve_mobility_subrisk_relation", return_value={"status": "NOT_FOUND"})
    @patch("cotizacion_colectivos.services.individual_entities.resolve_policy_by_number", return_value={"status": "FOUND", "record_id": "4991513000270954040"})
    @patch("cotizacion_colectivos.services.individual_entities.decrypt", side_effect=lambda value: value)
    @patch("cotizacion_colectivos.services.individual_entities.unsign_record_context", return_value={"id": "PRODUCTION-ID", "type": "policy"})
    def test_confirmed_subrisk_id_survives_follow_up_not_found(self, unsign, decrypt, resolve_policy, relation):
        quotation = self.quotation([{"plate": "ABC123", "insured_same_as_requester": False, "insured_document": "888888"}])
        quotation.safe_metadata = {"zoho_entities": {
            "people": [
                {"document": "444444444", "role": "Persona principal", "status": "created", "created": True, "remote_id": "AFF-1", "candidate": {"Tipo_ID": "CC"}},
                {"document": "888888", "role": "Asegurado del vehículo", "status": "created", "created": True, "remote_id": "INS-1", "candidate": {"Tipo_ID": "CC"}},
            ],
            "risks": [{"status": "created", "created": True, "remote_id": "RISK-1", "risk_id": "RISK-1"}],
            "subrisks": [{"status": "created", "created": True, "remote_id": "SUB-1", "riesgos1_id": "SUB-1", "index": 0, "candidate": {
                "P_liza": {"id": "4991513000270954040"}, "Riesgo": {"id": "RISK-1"},
                "Contacto_facturaci_n_dividida_colectivas": {"id": "AFF-1"}, "Asegurado": {"id": "INS-1"},
            }}],
        }}
        result = resolve_mobility_entities(quotation=quotation, zoho=_Facade())
        subrisk = result["subrisks"][0]
        self.assertEqual(subrisk["status"], "created")
        self.assertEqual(subrisk["remote_id"], "SUB-1")
        self.assertEqual(subrisk["riesgos1_id"], "SUB-1")

    def test_effective_candidate_is_separate_overlay(self):
        original = {"First_Name": "Original", "Last_Name": "Vargas", "Tipo_ID": "CC"}
        effective = effective_candidate(original, {"First_Name": "Corregido", "N_mero_de_ID": "999"})
        self.assertEqual(effective["First_Name"], "Corregido")
        self.assertEqual(original, {"First_Name": "Original", "Last_Name": "Vargas", "Tipo_ID": "CC"})

    def test_effective_candidate_keeps_original_date_when_correction_only_changes_name(self):
        original = {
            "First_Name": "Camilo", "Last_Name": "Vargas", "Tipo_ID": "CC",
            "N_mero_de_ID": "444444444", "Date_of_Birth": "2011-06-08",
            "Email": "camilo@example.com", "Phone": "3000000000",
        }
        effective = effective_candidate(original, {
            "first_name": "Camilo corregido", "last_name": "",
            "birth_date": "", "email": "",
        })
        self.assertEqual(effective["First_Name"], "Camilo corregido")
        self.assertEqual(effective["Date_of_Birth"], "2011-06-08")
        self.assertEqual(effective["Email"], "camilo@example.com")
        self.assertEqual(effective["Phone"], "3000000000")

    def quotation(self, vehicles):
        payload = {"schema": "movilidad", "fields": {
            "first_name": "Camilo", "last_name": "Vargas", "requester_id_type": "CC",
            "requester_document": "444444444", "requester_birth_date": "2000-01-01",
            "requester_email": "camilo@example.com", "requester_phone": "3000000000",
        }, "groups": {"vehicles": vehicles}, "context": {"policy_token": "token", "policy_label": "Póliza QA"}}
        return SimpleNamespace(encrypted_payload=json.dumps(payload), branch_slug="movilidad", safe_metadata={}, submitted_at=datetime.now(timezone.utc), save=lambda **kwargs: None)

    @patch("cotizacion_colectivos.services.individual_entities.unsign_record_context", return_value={"id": "4991513000000000001", "type": "policy"})
    @patch("cotizacion_colectivos.services.individual_entities.decrypt")
    def test_multiple_vehicles_reuse_one_requester_and_keep_vehicle_risks(self, decrypt, unsign):
        decrypt.side_effect = lambda value: value
        facade = _Facade(risks=(), contacts=())
        result = resolve_mobility_entities(quotation=self.quotation([
            {"plate": "ABC123", "insured_same_as_requester": True},
            {"plate": "XYZ789", "insured_same_as_requester": True},
        ]), zoho=facade)
        self.assertEqual(len(result["people"]), 1)
        self.assertEqual(len(result["risks"]), 2)
        self.assertTrue(all(item["status"] == "not_found" for item in result["risks"]))

    @patch("cotizacion_colectivos.services.individual_entities.unsign_record_context", return_value={"id": "4991513000000000001", "type": "policy"})
    @patch("cotizacion_colectivos.services.individual_entities.decrypt")
    def test_valid_not_found_risk_is_create_ready_without_contacts(self, decrypt, unsign):
        decrypt.side_effect = lambda value: value
        result = resolve_mobility_entities(quotation=self.quotation([{
            "risk_name": "ABC123", "plate": "ABC123", "model": "2026",
            "brand": "Marca", "line": "Referencia", "class": "Autos familiares",
            "city": "Bogotá", "use": "Residencial", "insured_same_as_requester": True,
        }]), zoho=_Facade(risks=(), contacts=()))
        risk = result["risks"][0]
        self.assertEqual(risk["status"], "not_found")
        self.assertEqual(risk["missing_fields"], [])
        self.assertEqual(risk["candidate"]["Placa_del_vehiculo"], "ABC123")
        self.assertEqual(risk["candidate"]["Name"], "ABC123")
        self.assertEqual(risk["candidate"]["Tipo_de_riesgo"], "Vehículos")

    @patch("cotizacion_colectivos.services.individual_entities.unsign_record_context", return_value={"id": "4991513000000000001", "type": "policy"})
    @patch("cotizacion_colectivos.services.individual_entities.decrypt")
    def test_mobility_name_always_follows_normalized_plate(self, decrypt, unsign):
        decrypt.side_effect = lambda value: value
        quotation = self.quotation([{"plate": "pjr-76d", "model": "2026", "insured_same_as_requester": True}])
        quotation.safe_metadata = {"zoho_entity_corrections": {"risk:0": {"Name": "incorrecto"}}}
        result = resolve_mobility_entities(quotation=quotation, zoho=_Facade())
        candidate = result["risks"][0]["candidate"]
        self.assertEqual(candidate["Placa_del_vehiculo"], "PJR76D")
        self.assertEqual(candidate["Name"], "PJR76D")

    @patch("cotizacion_colectivos.services.individual_entities.unsign_record_context", return_value={"id": "4991513000000000001", "type": "policy"})
    @patch("cotizacion_colectivos.services.individual_entities.decrypt")
    def test_vehicle_without_plate_remains_visible_as_blocked_risk(self, decrypt, unsign):
        decrypt.side_effect = lambda value: value
        result = resolve_mobility_entities(quotation=self.quotation([{
            "brand": "Prueba Marca", "model": "2026", "city": "Medellín", "use": "Caserito",
            "insured_same_as_requester": True,
        }]), zoho=_Facade())
        self.assertEqual(len(result["risks"]), 1)
        self.assertEqual(result["risks"][0]["status"], "blocked")
        self.assertEqual(result["risks"][0]["candidate"]["Placa_del_vehiculo"], "")
        self.assertIn("Complete la placa", result["risks"][0]["reason"])

    @patch("cotizacion_colectivos.services.individual_entities.unsign_record_context", return_value={"id": "4991513000000000001", "type": "policy"})
    @patch("cotizacion_colectivos.services.individual_entities.decrypt")
    def test_resolved_affiliate_keeps_payload_birth_date_after_name_correction(self, decrypt, unsign):
        decrypt.side_effect = lambda value: value
        quotation = self.quotation([{"plate": "ABC123", "insured_same_as_requester": True}])
        quotation.safe_metadata = {"person_corrections": {
            "444444444": {"First_Name": "Camilo corregido", "Last_Name": ""},
        }}
        result = resolve_mobility_entities(quotation=quotation, zoho=_Facade())
        affiliate = result["people"][0]
        self.assertEqual(affiliate["candidate"]["First_Name"], "Camilo corregido")
        self.assertEqual(affiliate["candidate"]["Date_of_Birth"], "2000-01-01")
        self.assertNotIn("Date_of_Birth", affiliate["missing_fields"])
        payload = build_contact_payload(affiliate["candidate"], status="Cliente")
        self.assertEqual(payload["Date_of_Birth"], date(2000, 1, 1))

    @patch("cotizacion_colectivos.services.individual_entities.unsign_record_context", return_value={"id": "4991513000000000001", "type": "policy"})
    @patch("cotizacion_colectivos.services.individual_entities.decrypt")
    def test_found_risk_and_existing_subrisk_are_read_only_states(self, decrypt, unsign):
        decrypt.side_effect = lambda value: value
        facade = _Facade(
            risks=({"id": "4991513000000000002", "Placa_del_vehiculo": "ABC123", "Name": "ABC123"},),
            contacts=({"id": "4991513000000000003", "N_mero_de_ID": "444444444", "Tipo_ID": "CC", "Full_Name": "Camilo Vargas"},),
        )
        result = resolve_mobility_entities(quotation=self.quotation([{"plate": "ABC123", "insured_same_as_requester": True}]), zoho=facade)
        self.assertEqual(result["people"][0]["status"], "found")
        self.assertEqual(result["risks"][0]["status"], "found")
        self.assertEqual(result["subrisks"][0]["status"], "not_found")

    @patch("cotizacion_colectivos.services.individual_entities.unsign_record_context", return_value={"id": "4991513000000000001", "type": "policy"})
    @patch("cotizacion_colectivos.services.individual_entities.decrypt")
    def test_distinct_vehicle_insured_is_an_independent_candidate(self, decrypt, unsign):
        decrypt.side_effect = lambda value: value
        facade = _Facade(contacts=())
        result = resolve_mobility_entities(quotation=self.quotation([{
            "plate": "ABC123", "insured_same_as_requester": False,
            "insured_first_name": "María", "insured_last_name": "Asegurada",
            "insured_id_type": "CC", "insured_document": "555555555",
            "insured_email": "maria@example.com", "insured_phone": "3110000000",
        }]), zoho=facade)
        self.assertEqual({person["document"] for person in result["people"]}, {"444444444", "555555555"})
        self.assertFalse(result["risks"][0]["insured_same_as_affiliate"])
        self.assertEqual(result["risks"][0]["insured"]["document"], "555555555")

    @patch("cotizacion_colectivos.services.individual_entities.unsign_record_context", return_value={"id": "4991513000000000001", "type": "policy"})
    @patch("cotizacion_colectivos.services.individual_entities.decrypt")
    def test_entity_corrections_are_effective_without_mutating_payload(self, decrypt, unsign):
        decrypt.side_effect = lambda value: value
        quotation = self.quotation([{"plate": "OLD123", "insured_same_as_requester": True}])
        quotation.safe_metadata = {"zoho_entity_corrections": {"risk:0": {
            "Placa_del_vehiculo": "NEW123", "Modelo": "2026",
        }}}
        original_payload = quotation.encrypted_payload
        result = resolve_mobility_entities(quotation=quotation, zoho=_Facade())
        self.assertEqual(result["risks"][0]["candidate"]["Placa_del_vehiculo"], "NEW123")
        self.assertEqual(result["risks"][0]["candidate"]["Modelo"], "2026")
        self.assertEqual(quotation.encrypted_payload, original_payload)

    @patch("cotizacion_colectivos.services.individual_entities.unsign_record_context", return_value={"id": "4991513000000000001", "type": "policy"})
    @patch("cotizacion_colectivos.services.individual_entities.decrypt")
    def test_confirmed_create_ids_survive_a_follow_up_reconcile(self, decrypt, unsign):
        decrypt.side_effect = lambda value: value
        quotation = self.quotation([{"plate": "ABC123", "insured_same_as_requester": True}])
        quotation.safe_metadata = {"zoho_entities": {
            "people": [{"document": "444444444", "status": "created", "created": True, "remote_id": "4991513000000000003", "contact_id": "4991513000000000003"}],
            "risks": [{"status": "created", "created": True, "remote_id": "4991513000000000002"}],
        }}
        result = resolve_mobility_entities(quotation=quotation, zoho=_Facade(risks=(), contacts=()))
        self.assertEqual(result["people"][0]["status"], "created")
        self.assertEqual(result["people"][0]["remote_id"], "4991513000000000003")
        self.assertEqual(result["risks"][0]["status"], "created")
        self.assertEqual(result["risks"][0]["remote_id"], "4991513000000000002")


class IndividualMobilityWorkspaceTemplateTests(SimpleTestCase):
    def setUp(self):
        template_path = Path(__file__).parents[2] / "templates" / "cotizacion_colectivos" / "individual" / "detail.html"
        self.template = template_path.read_text(encoding="utf-8")

    def test_workspace_uses_role_language_and_real_dialogs(self):
        self.assertIn("<h2>Afiliado</h2>", self.template)
        self.assertIn("Editar afiliado", self.template)
        self.assertIn('id="affiliate-edit-', self.template)
        self.assertIn('id="risk-edit-', self.template)
        self.assertNotIn('id="subrisk-edit-', self.template)
        self.assertIn("Asegurado:", self.template)
        self.assertIn("mismo afiliado", self.template)
        self.assertIn("Información operativa", self.template)
        self.assertNotIn("Información técnica", self.template)
        for technical in ("policy_remote_id", "SDK", "serializer", "RECONCILE_REQUIRED"):
            self.assertNotIn(technical, self.template)
        self.assertIn("Fecha de nacimiento", self.template)

    def test_vehicle_and_association_forms_are_not_details_editors(self):
        self.assertNotIn("Editar datos propuestos", self.template)
        self.assertNotIn("<details><summary>Editar", self.template)
        self.assertIn("Placa_del_vehiculo", self.template)
        self.assertIn("Marca_Tipo_Caracter_sticas", self.template)
        self.assertNotIn('id="subrisk-edit-', self.template)
        self.assertNotIn("Fecha_ingreso_riesgo", self.template)
        self.assertIn("Datos de póliza", self.template)
        self.assertIn("Agregar a la póliza", self.template)
        self.assertIn("Pendiente de placa", self.template)
        self.assertIn("Complete la placa para buscar o crear este vehículo en Zoho.", self.template)
        self.assertIn('data-dialog-open="risk-edit-', self.template)

    def test_zero_km_without_plate_uses_pending_plate_copy(self):
        javascript = (Path(__file__).parents[2] / "static" / "js" / "colectivos-detail.js").read_text(encoding="utf-8")
        self.assertIn("Pendiente de placa", javascript)
        self.assertIn("Complete la placa para buscar o crear este vehículo en Zoho.", javascript)

    def test_document_preview_opens_protected_endpoint_in_new_tab(self):
        partial = (Path(__file__).parents[2] / "templates" / "cotizacion_colectivos" / "individual" / "_document_owner.html").read_text(encoding="utf-8")
        detail = self.template
        self.assertIn('target="_blank"', partial)
        self.assertIn('rel="noopener noreferrer"', partial)
        self.assertNotIn("data-document-preview-dialog", detail)
        self.assertNotIn("<iframe", (Path(__file__).parents[2] / "static" / "js" / "colectivos-detail.js").read_text(encoding="utf-8"))

    def test_vehicle_editor_uses_shared_class_and_use_choices(self):
        self.assertIn('name="Clase"><option value="">Seleccione una clase</option>', self.template)
        self.assertIn('name="Tipo_de_uso"><option value="">Seleccione un tipo de uso</option>', self.template)
        self.assertIn('vehicle_class_choices', self.template)
        self.assertIn('vehicle_use_choices', self.template)
        self.assertIn('data-zero-km="{{ risk.zero_km }}"', self.template)

    def test_vehicle_edit_uses_existing_loading_feedback(self):
        loading = (Path(__file__).parents[2] / "static" / "js" / "colectivos-loading.js").read_text(encoding="utf-8")
        self.assertIn("/entidad/", loading)
        self.assertIn("Actualizando vehículo…", loading)

    def test_effective_data_is_moved_inside_entity_cards_and_insured_edit_is_unconditional(self):
        javascript = (Path(__file__).parents[2] / "static" / "js" / "colectivos-detail.js").read_text(encoding="utf-8")
        self.assertIn('document.querySelectorAll(".zoho-effective-summary").forEach((node) => node.remove())', javascript)
        self.assertIn('className = "entity-effective-summary"', javascript)
        self.assertIn('insuredTrigger.dataset.dialogOpen = `insured-edit-${index}`', javascript)
        self.assertIn('data-dialog-open="insured-edit-', self.template)
        self.assertIn("Editar asegurado", self.template)
        self.assertNotIn("zoho-effective-summary", self.template)

    def test_insured_create_form_action_is_not_contaminated_by_markup(self):
        self.assertNotIn("individual_create_person' token=individual_token %}'>{% csrf_token", self.template)
        self.assertIn('individual_create_person\' token=individual_token %}">{% csrf_token', self.template)
        self.assertNotIn('action="\'><input', self.template)

    def test_rendered_insured_create_form_has_clean_person_create_action(self):
        class FormParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.forms = []
                self.current = None

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == "form":
                    self.current = {"action": attrs.get("action", ""), "inputs": {}}
                    self.forms.append(self.current)
                elif tag == "input" and self.current is not None:
                    self.current["inputs"][attrs.get("name", "")] = attrs.get("value", "")

            def handle_endtag(self, tag):
                if tag == "form":
                    self.current = None

        html = engines["django"].get_template("cotizacion_colectivos/individual/detail.html").render({
            "individual_token": "test-token",
            "schema": SimpleNamespace(name="Movilidad"),
            "individual_context": {}, "acceptance": {"status": "accepted"},
            "people_lookup": ({"role": "Persona principal", "status": "not_found", "has_complete_data": False, "document": "111", "candidate": {
                "First_Name": "Afiliado", "Last_Name": "QA", "Tipo_ID": "CC",
                "N_mero_de_ID": "111",
                "Date_of_Birth": "2000-01-01", "Email": "affiliate@example.com",
                "Phone": "3000000000", "Mobile": "3000000000",
            }},),
            "zoho_entities": {"branch": "movilidad", "risks": [{
                "status": "not_found", "candidate": {
                    "Placa_del_vehiculo": "ABC123", "Marca_Tipo_Caracter_sticas": "Marca",
                    "Modelo": "2026", "Clase": "", "Ciudad": "Bogotá", "Tipo_de_uso": "Caserito",
                },
                "insured_same_as_affiliate": False,
                "insured": {"status": "not_found", "has_complete_data": True, "document": "222", "candidate": {
                    "First_Name": "Asegurado", "Last_Name": "QA", "Tipo_ID": "CC",
                    "N_mero_de_ID": "222",
                    "Date_of_Birth": "2001-01-01", "Email": "insured@example.com",
                    "Phone": "3110000000", "Mobile": "3110000000",
                }},
            }], "subrisks": [], "policy": {}},
        })
        parser = FormParser()
        parser.feed(html)
        insured_forms = [form for form in parser.forms if form["inputs"].get("document") == "222"]
        create_forms = [form for form in insured_forms if form["action"].endswith("/persona/crear/")]
        self.assertEqual(len(create_forms), 1)
        action = create_forms[0]["action"]
        self.assertTrue(action.endswith("/persona/crear/"), action)
        self.assertEqual(resolve(action).url_name, "individual_create_person")
        for forbidden in ("<", ">", "input", "%3E"):
            self.assertNotIn(forbidden, action.lower())

    def test_rendered_created_risk_with_pending_document_exposes_publish_action(self):
        attachment = SimpleNamespace(
            pk=2,
            owner_label="Vehículo",
            safe_original_name="VELTRIX_TEST_ATTACHMENT.pdf",
            detected_mime="application/pdf",
            size=128,
            safe_metadata={
                "owner_type": "risk",
                "owner_key": "vehicles-048d3af6-06b3-48cc-bed2-ab9f979472c3",
                "risk_key": "vehicles-048d3af6-06b3-48cc-bed2-ab9f979472c3",
            },
            document_status="pending",
            can_publish=True,
            structured_owner=True,
        )
        affiliate_document = SimpleNamespace(
            pk=3, owner_label="Afiliado", safe_original_name="affiliate-id.pdf",
            detected_mime="application/pdf", size=64,
            safe_metadata={"owner_type": "contact", "owner_key": "affiliate", "document_type": "identity_document"},
            document_status="pending", can_publish=True, structured_owner=True,
        )
        insured_document = SimpleNamespace(
            pk=4, owner_label="Asegurado", safe_original_name="insured-id.pdf",
            detected_mime="application/pdf", size=64,
            safe_metadata={"owner_type": "contact", "owner_key": "vehicles-048d3af6-06b3-48cc-bed2-ab9f979472c3-insured", "document_type": "identity_document"},
            document_status="pending", can_publish=True, structured_owner=True,
        )
        html = engines["django"].get_template("cotizacion_colectivos/individual/detail.html").render({
            "individual_token": "test-token",
            "schema": SimpleNamespace(name="Movilidad"),
            "individual_context": {},
            "acceptance": {"status": "accepted"},
            "people_lookup": ({"role": "Afiliado", "display_name": "Afiliado QA", "status": "found", "document": "111", "candidate": {
                "First_Name": "Afiliado", "Last_Name": "QA", "Tipo_ID": "CC", "N_mero_de_ID": "111",
                "Date_of_Birth": "1990-01-01", "Email": "affiliate@example.com", "Phone": "3000000000", "Mobile": "3000000000",
            }, "documents": (affiliate_document,)},),
            "individual_attachments": (attachment, affiliate_document, insured_document),
            "zoho_entities": {
                "branch": "movilidad",
                "risks": [{
                    "status": "created", "remote_id": "4991513000271052016",
                    "owner_key": "vehicles-048d3af6-06b3-48cc-bed2-ab9f979472c3",
                    "risk_key": "vehicles-048d3af6-06b3-48cc-bed2-ab9f979472c3",
                    "documents": (attachment,),
                    "candidate": {"Placa_del_vehiculo": "AAA111"},
                    "insured_same_as_affiliate": False,
                    "insured": {"status": "found", "document": "222", "candidate": {
                        "First_Name": "Asegurado", "Last_Name": "QA", "Tipo_ID": "CC", "N_mero_de_ID": "222",
                        "Date_of_Birth": "1991-01-01", "Email": "insured@example.com", "Phone": "3110000000", "Mobile": "3110000000",
                    }, "documents": (insured_document,)},
                }],
                "subrisks": [], "policy": {},
            },
        })
        self.assertIn("Pendiente de publicar", html)
        self.assertIn("Adjuntar documento en Zoho", html)
        class DocumentParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.vehicle_depth = 0
                self.documents = 0
                self.preview_links = []
                self.visible_names = []
                self._capture_name = False
                self._capture_depth = 0

            def handle_starttag(self, tag, attrs):
                attributes = dict(attrs)
                classes = set((attributes.get("class") or "").split())
                if "vehicle-entity-card" in classes:
                    self.vehicle_depth += 1
                if "entity-document" in classes:
                    self.documents += 1
                if tag == "a" and "/adjuntos/" in attributes.get("href", ""):
                    self.preview_links.append(attributes)
                if tag == "a" and "/adjuntos/2/preview/" in attributes.get("href", ""):
                    self._capture_name = True
                    self._capture_depth = 1

            def handle_endtag(self, tag):
                if tag == "a" and self._capture_name:
                    self._capture_depth -= 1
                    if self._capture_depth == 0:
                        self._capture_name = False
                if tag == "article" and self.vehicle_depth:
                    self.vehicle_depth -= 1

            def handle_data(self, data):
                if self._capture_name and data.strip():
                    self.visible_names.append(data.strip())

        parser = DocumentParser()
        parser.feed(html)
        self.assertEqual(parser.documents, 3)  # risk, affiliate and nested insured cards
        risk_links = [link for link in parser.preview_links if "/adjuntos/2/preview/" in link.get("href", "")]
        self.assertEqual(len(risk_links), 1)
        self.assertEqual(parser.visible_names.count("VELTRIX_TEST_ATTACHMENT.pdf"), 1)
        affiliate_links = [link for link in parser.preview_links if "/adjuntos/3/preview/" in link.get("href", "")]
        self.assertEqual(len(affiliate_links), 1)
        for link in parser.preview_links:
            self.assertEqual(link.get("target"), "_blank")
            self.assertEqual(link.get("rel"), "noopener noreferrer")

    def test_policy_data_uses_human_language_without_changing_subrisk_contract(self):
        javascript = (Path(__file__).parents[2] / "static" / "js" / "colectivos-detail.js").read_text(encoding="utf-8")
        self.assertIn("Información operativa", self.template)
        self.assertNotIn("Datos para Zoho", self.template)
        self.assertIn("Datos de póliza", self.template)
        self.assertNotIn('id="subrisk-edit-', self.template)
        self.assertIn('id="affiliate-edit-', self.template)
        self.assertIn('id="risk-edit-', self.template)
        self.assertIn('data-dialog-open="insured-edit-', self.template)
        self.assertIn("Agregar a la póliza", self.template)
        self.assertIn("Agregado a la póliza", self.template)
        self.assertIn("Pendiente de placa", self.template)
        self.assertNotIn("Asociación a póliza", self.template)
        self.assertNotIn("Editar asociación", self.template)
        self.assertNotIn("Asociar a esta póliza", self.template)
        self.assertIn('heading.textContent = "Datos de póliza"', javascript)
        self.assertIn('association.querySelectorAll("[data-dialog-open^=\'subrisk-edit-\']").forEach((trigger) => trigger.remove())', javascript)
        self.assertIn('button.textContent = "Agregar a la póliza"', javascript)
        self.assertIn('Agregado a la póliza', javascript)
        self.assertIn('association.querySelectorAll("[data-dialog-open^=\'subrisk-edit-\']").forEach((trigger) => trigger.remove())', javascript)
        self.assertIn('Creando afiliado en Zoho…', javascript)
        self.assertIn('Creando riesgo en Zoho…', javascript)
        self.assertIn('Agregando a la póliza…', javascript)

    def test_operational_workspace_uses_main_width_and_risk_create_condition(self):
        css = (Path(__file__).parents[2] / "static" / "css" / "colectivos.css").read_text(encoding="utf-8")
        self.assertIn(".request-workspace-layout{grid-template-columns:minmax(0,1.8fr) minmax(380px,1fr)}", css)
        self.assertIn("@media(max-width:1024px){.request-workspace-layout{grid-template-columns:1fr}", css)
        self.assertIn(".request-action-sidebar{grid-template-columns:1fr", css)
        self.assertIn(".request-action-sidebar .zoho-semantic-workspace", css)
        self.assertIn('risk.status == "not_found" and not risk.missing_fields', self.template)
        self.assertIn('type="submit" class="button-link button-link--primary">Crear riesgo en Zoho', self.template)
