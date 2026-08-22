import json
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.template import engines
from django.test import SimpleTestCase
from django.urls import resolve

from cotizacion_colectivos.services.individual_entities import effective_candidate, promote_created_people, resolve_mobility_entities, synchronize_risk_insured
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
        self.assertEqual(payload["Date_of_Birth"], "2000-01-01")

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
        self.assertIn("Datos para Zoho", self.template)
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

    def test_policy_data_uses_human_language_without_changing_subrisk_contract(self):
        javascript = (Path(__file__).parents[2] / "static" / "js" / "colectivos-detail.js").read_text(encoding="utf-8")
        self.assertIn("Datos para Zoho", self.template)
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
        self.assertIn('Creando vehículo en Zoho…', javascript)
        self.assertIn('Agregando a la póliza…', javascript)

    def test_operational_workspace_uses_main_width_and_risk_create_condition(self):
        css = (Path(__file__).parents[2] / "static" / "css" / "colectivos.css").read_text(encoding="utf-8")
        self.assertIn(".request-workspace-layout{grid-template-columns:minmax(0,1.8fr) minmax(380px,1fr)}", css)
        self.assertIn("@media(max-width:1024px){.request-workspace-layout{grid-template-columns:1fr}", css)
        self.assertIn(".request-action-sidebar{grid-template-columns:1fr", css)
        self.assertIn(".request-action-sidebar .zoho-semantic-workspace", css)
        self.assertIn('risk.status == "not_found" and not risk.missing_fields', self.template)
        self.assertIn('type="submit" class="button-link button-link--primary">Crear riesgo en Zoho', self.template)
