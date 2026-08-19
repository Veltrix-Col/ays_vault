from __future__ import annotations

import json
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from vault.crypto import decrypt

from cotizacion_colectivos.dto import ContactSummary, GroupMember, PolicyDetail
from cotizacion_colectivos.models import (
    AccesoCotizacionIndividual,
    AdjuntoCotizacionIndividual,
    CotizacionIndividual,
    ColectivosTaskOutbox,
    NotificacionCotizacionIndividual,
)
from cotizacion_colectivos.quotation_forms.catalog import get_branch_schema
from cotizacion_colectivos.quotation_forms.forms import IndividualQuotationForm
from cotizacion_colectivos.quotation_forms.security import sign_policy_context, sign_receipt, unsign_policy_context
from cotizacion_colectivos.services.individual_access import generate_individual_access, issue_individual_otp
from cotizacion_colectivos.services.individual_quotations import (
    accept_individual_quotation,
    build_policy_context,
    create_individual_quotation,
    resolve_accepted_person,
)
from cotizacion_colectivos.services.person_contract import build_contact_payload
from cotizacion_colectivos.services.common import sign_record_id


POLICY_TOKEN = sign_record_id(
    "4234567890123456789",
    "policy",
    context={"source_id": "5234567890123456789", "source_kind": "company"},
)


def policy(branch_code="91", branch_name="Salud colectivo"):
    return PolicyDetail(
        detail_token=POLICY_TOKEN,
        masked_reference="Póliza terminada en 1814",
        full_reference="POLIZA-PRUEBA",
        branch_code=branch_code,
        branch_name=branch_name,
        classification="confirmed",
        insurer="Aseguradora",
        state="Vigente",
        holder="Colectiva Demo",
        start_date="2026-01-01",
        end_date="2027-01-01",
        renewable="Sí",
        payment_mode="Contado",
        frequency="Anual",
        installments="1",
        first_installment_date="",
        payment_calendar=(),
        insured=(),
        risks=(),
        active_count=1,
        excluded_count=0,
        source_kind="company",
        source_name="Colectiva Demo",
        source_summary=ContactSummary(
            person_type="Persona jurídica",
            id_type="NIT",
            masked_document="•••001",
            state="Cliente",
        ),
    )


def affiliate():
    return GroupMember(
        role="Afiliado",
        display_name="Afiliada Demo",
        id_type="CC",
        masked_document="•••001",
        document="100000001",
        state="Activo",
        entry_date="",
        exit_date="",
        plan="",
        relationship="Titular",
        risk_summary="",
        email="demo@example.test",
        mobile="3000000000",
        associate_name="Afiliada Demo",
        associate_id_type="CC",
        associate_document="100000001",
        associate_masked_document="•••001",
        associate_key="affiliate-hmac-key",
    )


class IndividualQuotationTests(TestCase):
    def setUp(self):
        self.private = tempfile.TemporaryDirectory()
        self.addCleanup(self.private.cleanup)
        override = self.settings(COLECTIVOS_PRIVATE_ROOT=Path(self.private.name))
        override.enable()
        self.addCleanup(override.disable)
        self.actor = get_user_model().objects.create_user(
            username="individual-owner", password="safe-test-password",
        )

    def context_token(self, *, schema_slug="salud"):
        schema = get_branch_schema(schema_slug)
        return sign_policy_context({
            "context_version": 1,
            "policy_token": POLICY_TOKEN,
            "source_kind": "company",
            "affiliate_key": "affiliate-hmac-key",
            "branch_slug": schema.slug,
            "schema_version": schema.version,
            "creator_id": self.actor.pk,
            "policy_label": "Póliza terminada en 1814",
            "branch_name": schema.name,
            "affiliate_label": "Afiliada Demo",
            "requester_name": "Afiliada Demo",
            "requester_id_type": "CC",
            "requester_document": "100000001",
            "requester_email": "demo@example.test",
            "requester_phone": "3000000000",
            "collective_context": "Colectiva Demo",
            "locked_fields": (
                "requester_name", "requester_id_type", "requester_document",
                "requester_email", "requester_phone", "collective_context",
            ),
        })

    def access_token(self, *, schema_slug="salud"):
        context = unsign_policy_context(self.context_token(schema_slug=schema_slug))
        generated = generate_individual_access(
            context=context, actor=self.actor, recipient="demo@example.test",
        )
        with patch("cotizacion_colectivos.services.individual_access.secrets.randbelow", return_value=123456):
            entry = self.client.get(reverse(
                "colectivos_external:individual_quotation", args=[generated.token],
            ))
        self.assertEqual(entry.status_code, 200)
        verified = self.client.post(
            reverse("colectivos_external:individual_verify", args=[generated.token]),
            {"code": "123456"},
        )
        self.assertEqual(verified.status_code, 302)
        return generated.token

    def test_individual_email_is_multipart_ready_and_delivers_the_real_otp(self):
        context = unsign_policy_context(self.context_token())
        generated = generate_individual_access(
            context=context, actor=self.actor, recipient="demo@example.test",
        )
        backend = Mock(name="individual_otp_backend")
        backend.name = "smtp"
        backend.send.return_value = "accepted"
        with patch("cotizacion_colectivos.services.individual_access.secrets.randbelow", return_value=654321), patch(
            "vault.notifications.get_backend", return_value=backend,
        ), patch("vault.notifications.logger") as notification_logger:
            self.assertTrue(issue_individual_otp(generated.access))

        subject, text_body, html_body, recipient = backend.send.call_args.args
        self.assertEqual(recipient, "demo@example.test")
        self.assertIn("654321", text_body)
        self.assertIn("654321", html_body)
        self.assertNotIn("[CÓDIGO OMITIDO]", text_body + html_body)
        self.assertIn("Código de verificación", subject + html_body)
        generated.access.refresh_from_db()
        self.assertEqual(generated.access.otp_expires_at, generated.access.expires_at)
        self.assertNotEqual(generated.access.otp_hash, "654321")
        self.assertNotIn("654321", repr(generated.access.__dict__))
        self.assertNotIn("654321", repr(notification_logger.mock_calls))

    def test_individual_otp_uses_edited_email_instead_of_zoho_original(self):
        original = "original-zoho@example.test"
        edited = "edited-access@example.test"
        context = unsign_policy_context(self.context_token())
        context["requester_email"] = original
        generated = generate_individual_access(
            context=context, actor=self.actor, recipient=edited,
        )
        backend = Mock(name="individual_edited_recipient_backend")
        backend.name = "smtp"
        backend.send.return_value = "accepted"
        with patch(
            "cotizacion_colectivos.services.individual_access.secrets.randbelow",
            return_value=445566,
        ), patch("vault.notifications.get_backend", return_value=backend):
            self.assertTrue(issue_individual_otp(generated.access))
        self.assertEqual(backend.send.call_args.args[3], edited)
        self.assertNotEqual(backend.send.call_args.args[3], original)

    @staticmethod
    def person(suffix="1"):
        return {
            "name": f"Persona {suffix}", "id_type": "CC",
            "document": f"20000000{suffix}", "birth_date": "1990-01-01",
            "gender": "Femenino", "relationship": "Hijo(a)", "role": "Asegurado",
            "employment_relationship": "Grupo familiar",
            "currently_health_insured": "No",
            "current_health_insurer": "",
            "current_health_policy_end": "",
            "plan_interest": "",
        }

    @staticmethod
    def vehicle(suffix="1"):
        return {
            "zero_km": "No", "plate": f"ABC12{suffix}", "brand": "Marca", "line": "Línea",
            "model": "2025", "city": "Bogotá", "use": "Familiar",
            "insured_name": f"Asegurado {suffix}", "insured_id_type": "CC",
            "insured_document": f"30000000{suffix}",
        }

    def workspace(self, schema_slug):
        schema = get_branch_schema(schema_slug)
        return policy(), (affiliate(),), {"storage": "database"}, schema

    def test_tool_entry_goes_to_client_search_and_loose_branch_form_is_closed(self):
        response = self.client.get(reverse("public_home"))
        self.assertContains(response, "Colectivos")
        entry = self.client.get(reverse("cotizacion_colectivos:individual_index"))
        self.assertRedirects(entry, reverse("cotizacion_colectivos:individual_client_search"))
        loose = self.client.get(reverse("cotizacion_colectivos:individual_form", args=["salud"]))
        self.assertRedirects(loose, reverse("cotizacion_colectivos:individual_client_search"))
        self.assertEqual(
            self.client.post(reverse("cotizacion_colectivos:individual_form", args=["salud"])).status_code,
            404,
        )

    def test_policy_context_derives_branch_and_uses_hmac_affiliate(self):
        schema, token, context = build_policy_context(
            policy_token=POLICY_TOKEN,
            detail=policy(),
            members=(affiliate(),),
            affiliate_key="affiliate-hmac-key",
            creator_id=self.actor.pk,
        )
        self.assertEqual(schema.slug, "salud")
        self.assertNotIn("4234567890123456789", token)
        self.assertNotIn("100000001", token)
        self.assertEqual(context["affiliate_key"], "affiliate-hmac-key")

    def test_policy_context_allows_a_new_person_without_losing_policy_context(self):
        schema, token, context = build_policy_context(
            policy_token=POLICY_TOKEN,
            detail=policy(),
            members=(affiliate(),),
            affiliate_key="",
            creator_id=self.actor.pk,
        )
        self.assertEqual(schema.slug, "salud")
        self.assertEqual(context["affiliate_key"], "")
        self.assertEqual(context["affiliate_label"], "Persona nueva")
        self.assertEqual(context["collective_context"], "Colectiva Demo")
        self.assertNotIn("4234567890123456789", token)

    def test_fonconstruimos_requires_a_sanitized_declared_company_without_lookup(self):
        fonco_policy = replace(
            policy(), holder="Fondo de Empleados Construimos Sueños",
            source_name="Fonconstruimos",
        )
        schema, _token, context = build_policy_context(
            policy_token=POLICY_TOKEN, detail=fonco_policy, members=(affiliate(),),
            affiliate_key="affiliate-hmac-key", creator_id=self.actor.pk,
        )
        self.assertTrue(context["requires_declared_company"])
        data = {"items_payload": json.dumps({"people": [self.person()]})}
        missing = IndividualQuotationForm(data, schema=schema, context=context)
        self.assertFalse(missing.is_valid())
        self.assertIn("declared_company", missing.errors)
        valid = IndividualQuotationForm(
            {**data, "declared_company": "Constructora Ejemplo S.A.S."},
            schema=schema, context=context,
        )
        self.assertTrue(valid.is_valid(), valid.errors)
        self.assertEqual(valid.cleaned_data["declared_company"], "Constructora Ejemplo S.A.S.")

    def test_health_mvp_has_continuity_fields_and_no_medical_declaration(self):
        schema = get_branch_schema("salud")
        keys = {field.key for field in schema.repeatables[0].fields}
        self.assertTrue({
            "employment_relationship", "currently_health_insured",
            "current_health_insurer", "current_health_policy_end",
        }.issubset(keys))
        serialized = " ".join(field.label.casefold() for field in schema.repeatables[0].fields)
        self.assertNotIn("declaración de asegurabilidad", serialized)
        self.assertNotIn("historia clínica", serialized)

    def test_current_individual_form_uses_structured_name_fields_without_duplicate_full_name(self):
        schema = get_branch_schema("salud")
        context = unsign_policy_context(self.context_token())
        form = IndividualQuotationForm(schema=schema, context=context)
        self.assertIn("first_name", form.fields)
        self.assertIn("last_name", form.fields)
        self.assertNotIn("requester_name", form.fields)

    def test_new_person_requires_all_canonical_requester_fields(self):
        schema = get_branch_schema("movilidad")
        form = IndividualQuotationForm(
            data={
                "first_name": "Camilo 2", "last_name": "Vargas 2",
                "requester_id_type": "CC", "requester_document": "999999999",
                "requester_birth_date": "2009-02-10",
                "requester_email": "c.vargas0419@example.com",
                "requester_phone": "3186235929", "collective_context": "Demo",
                "items_payload": json.dumps({"vehicles": [self.vehicle("1")]}),
            }, schema=schema, context={},
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["first_name"], "Camilo 2")
        incomplete = dict(form.data)
        incomplete["requester_email"] = ""
        self.assertFalse(IndividualQuotationForm(incomplete, schema=schema, context={}).is_valid())

    @patch("cotizacion_colectivos.services.individual_quotations.PersonSearchService")
    def test_new_person_payload_reaches_contacts_without_missing_identity(self, service):
        schema = get_branch_schema("movilidad")
        context = unsign_policy_context(self.context_token(schema_slug="movilidad"))
        quotation = create_individual_quotation(
            schema=schema,
            cleaned_data={
                "first_name": "Camilo 2", "last_name": "Vargas 2",
                "requester_id_type": "CC", "requester_document": "999999999",
                "requester_birth_date": "2009-02-10",
                "requester_email": "c.vargas0419@example.com",
                "requester_phone": "3186235929",
                "normalized_items": {"vehicles": [self.vehicle("1"), self.vehicle("2"), self.vehicle("3")]},
                "attachments": [],
            }, actor=self.actor, context=context,
        )
        service.return_value.search.return_value = ()
        result = resolve_accepted_person(quotation=quotation)
        candidate = result["candidate"]
        self.assertEqual(result["missing_fields"], ())
        self.assertEqual(candidate["First_Name"], "Camilo 2")
        self.assertEqual(candidate["Last_Name"], "Vargas 2")
        contact = build_contact_payload(candidate)
        self.assertEqual(contact["N_mero_de_ID"], "999999999")
        self.assertEqual(contact["Tipo_ID"], "CC")
        self.assertEqual(contact["Date_of_Birth"], "2009-02-10")
        self.assertEqual(contact["Email"], "c.vargas0419@example.com")
        self.assertEqual(contact["Phone"], "3186235929")

    def test_health_first_person_can_use_requester_without_repeating_identity_fields(self):
        schema = get_branch_schema("salud")
        form = IndividualQuotationForm(
            data={
                "first_name": "Camilo", "last_name": "Vargas", "requester_id_type": "CC",
                "requester_document": "444444444", "requester_birth_date": "1990-01-01",
                "requester_email": "camilo@example.test", "requester_phone": "3000000000",
                "collective_context": "Colectiva Demo",
                "items_payload": json.dumps({"people": [{
                    "use_requester": "Sí", "employment_relationship": "Empleado",
                    "relationship": "Titular", "currently_health_insured": "No",
                }]}),
            }, schema=schema, context={},
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["normalized_items"]["people"][0]["document"], "444444444")

    @patch("cotizacion_colectivos.external_views._individual_workspace")
    @patch("cotizacion_colectivos.zoho.get_zoho")
    def test_health_multiple_people_submit_encrypted_notifies_and_never_calls_zoho(self, get_zoho, workspace):
        workspace.return_value = self.workspace("salud")
        data = {"items_payload": json.dumps({"people": [self.person("1"), self.person("2")]})}
        response = self.client.post(
            reverse("colectivos_external:individual_quotation", args=[self.access_token()]), data,
        )
        self.assertEqual(response.status_code, 302)
        quotation = CotizacionIndividual.objects.get()
        payload = json.loads(decrypt(quotation.encrypted_payload))
        self.assertEqual(len(payload["groups"]["people"]), 2)
        self.assertEqual(payload["fields"]["requester_name"], "Afiliada Demo")
        self.assertNotIn("100000001", quotation.encrypted_payload)
        self.assertTrue(NotificacionCotizacionIndividual.objects.filter(quotation=quotation).exists())
        access = AccesoCotizacionIndividual.objects.get(quotation=quotation)
        self.assertEqual(access.status, access.Status.USED)
        get_zoho.assert_not_called()

        detail = self.client.get(reverse(
            "cotizacion_colectivos:individual_expedient",
            args=[sign_receipt(quotation.public_id)],
        ))
        self.assertContains(detail, "Nombre del solicitante")
        self.assertContains(detail, "Personas")
        self.assertNotContains(detail, "requester_name")
        self.assertNotContains(detail, "people")

    @patch("cotizacion_colectivos.external_views._individual_workspace")
    def test_response_creates_one_quote_task_and_acceptance_person_states_are_local(self, workspace):
        workspace.return_value = self.workspace("salud")
        response = self.client.post(
            reverse("colectivos_external:individual_quotation", args=[self.access_token()]),
            {"items_payload": json.dumps({"people": [self.person("1")]})},
        )
        self.assertEqual(response.status_code, 302)
        quotation = CotizacionIndividual.objects.get()
        self.assertEqual(quotation.task_outbox.count(), 1)
        self.assertEqual(quotation.task_outbox.get().event_kind, "COTIZACION")
        self.assertEqual(
            json.loads(decrypt(quotation.task_outbox.get().encrypted_payload))["Fecha_de_solicitud_del_cliente"],
            quotation.submitted_at.date().isoformat(),
        )
        legacy_detail = self.client.get(reverse(
            "cotizacion_colectivos:individual_expedient",
            args=[sign_receipt(quotation.public_id)],
        ))
        self.assertEqual(legacy_detail.status_code, 200)
        self.assertContains(legacy_detail, "Responsable no disponible en este registro legado.")
        accept_individual_quotation(quotation=quotation, actor=self.actor)
        accept_individual_quotation(quotation=quotation, actor=self.actor)
        self.assertEqual(quotation.task_outbox.count(), 1)
        person = SimpleNamespace(
            full_name="Afiliada Demo", masked_document="•••001", detail_token="signed-person-token",
        )
        with patch("cotizacion_colectivos.services.individual_quotations.PersonSearchService") as service:
            service.return_value.search.return_value = (person,)
            result = resolve_accepted_person(quotation=quotation)
        self.assertEqual(result["status"], "found")
        quotation.refresh_from_db()
        self.assertEqual(quotation.safe_metadata["acceptance"]["status"], "accepted")
        self.assertEqual(quotation.safe_metadata["person_lookup"]["status"], "found")

    @patch("cotizacion_colectivos.external_views._individual_workspace")
    def test_person_lookup_does_not_auto_select_missing_or_ambiguous_results(self, workspace):
        workspace.return_value = self.workspace("salud")
        self.client.post(
            reverse("colectivos_external:individual_quotation", args=[self.access_token()]),
            {"items_payload": json.dumps({"people": [self.person("1")]})},
        )
        quotation = CotizacionIndividual.objects.get()
        accept_individual_quotation(quotation=quotation, actor=self.actor)
        with patch("cotizacion_colectivos.services.individual_quotations.PersonSearchService") as service:
            service.return_value.search.return_value = ()
            self.assertEqual(resolve_accepted_person(quotation=quotation)["status"], "not_found")
            service.return_value.search.return_value = (SimpleNamespace(full_name="A", masked_document="•••001", detail_token="a"), SimpleNamespace(full_name="B", masked_document="•••002", detail_token="b"))
            self.assertEqual(resolve_accepted_person(quotation=quotation)["status"], "ambiguous")

    @patch("cotizacion_colectivos.services.individual_quotations.PersonSearchService")
    def test_structured_requester_fields_reach_person_contract_without_false_missing(self, service):
        schema = get_branch_schema("salud")
        context = unsign_policy_context(self.context_token())
        quotation = create_individual_quotation(
            schema=schema,
            cleaned_data={
                "first_name": "Camilo 2", "last_name": "Vargas 2",
                "requester_id_type": "CC", "requester_document": "11111111",
                "requester_birth_date": "1990-01-01", "requester_email": "camilo@example.test", "requester_phone": "3000000000",
                "collective_context": "Colectiva Demo", "normalized_items": {"people": []},
                "attachments": [],
            },
            actor=self.actor, context=context,
        )
        service.return_value.search.return_value = ()
        result = resolve_accepted_person(quotation=quotation)
        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["missing_fields"], ())
        self.assertTrue(result["has_complete_data"])

    @patch("cotizacion_colectivos.services.individual_quotations.PersonSearchService")
    def test_mobility_uses_requester_as_primary_and_does_not_let_vehicle_overwrite_it(self, service):
        schema = get_branch_schema("movilidad")
        context = unsign_policy_context(self.context_token(schema_slug="movilidad"))
        context.update({
            "requester_name": "Camilo 2 Vargas 2", "requester_id_type": "CC",
            "requester_document": "444444444", "requester_birth_date": "1990-01-01",
            "requester_email": "camilo@example.test", "requester_phone": "3000000000",
        })
        quotation = create_individual_quotation(
            schema=schema,
            cleaned_data={
                "first_name": "Camilo 2", "last_name": "Vargas 2",
                "requester_id_type": "CC", "requester_document": "444444444",
                "requester_birth_date": "1990-01-01", "requester_email": "camilo@example.test",
                "requester_phone": "3000000000", "normalized_items": {"vehicles": [
                    {"insured_name": "Camilo Asegurado", "insured_id_type": "CC", "insured_document": "1019059650", "insured_is_different": "Sí"},
                    {"insured_name": "Camilo 2 Vargas 2", "insured_id_type": "CC", "insured_document": "444444444"},
                ]}, "attachments": [],
            }, actor=self.actor, context=context,
        )
        service.return_value.search.return_value = ()
        result = resolve_accepted_person(quotation=quotation)
        quotation.refresh_from_db()
        people = quotation.safe_metadata["people_lookup"]
        self.assertEqual([item["document"] for item in people], ["444444444", "1019059650"])
        self.assertEqual(people[0]["display_name"], "Camilo 2 Vargas 2")
        self.assertEqual(people[0]["role"], "Persona principal")
        self.assertEqual(result["document"], "444444444")
        self.assertEqual(people[0]["missing_fields"], [])
        self.assertIn("Apellidos", people[1]["missing_fields"])

    @patch("cotizacion_colectivos.services.individual_quotations.PersonSearchService")
    def test_mobility_multiple_vehicles_reuse_primary_unless_insured_document_differs(self, service):
        schema = get_branch_schema("movilidad")
        context = unsign_policy_context(self.context_token(schema_slug="movilidad"))
        quotation = create_individual_quotation(
            schema=schema,
            cleaned_data={
                "first_name": "Camilo", "last_name": "Vargas", "requester_id_type": "CC",
                "requester_document": "444444444", "normalized_items": {"vehicles": [
                    {"insured_document": "444444444", "insured_id_type": "CC", "insured_name": "Camilo Vargas"},
                    {"insured_document": "444444444", "insured_id_type": "CC", "insured_name": "Camilo Vargas"},
                ]}, "attachments": [],
            }, actor=self.actor, context=context,
        )
        service.return_value.search.return_value = ()
        resolve_accepted_person(quotation=quotation)
        quotation.refresh_from_db()
        self.assertEqual([item["document"] for item in quotation.safe_metadata["people_lookup"]], ["444444444"])

    @patch("cotizacion_colectivos.services.individual_quotations.PersonSearchService")
    def test_mobility_different_vehicle_insured_is_ignored_without_explicit_marker(self, service):
        schema = get_branch_schema("movilidad")
        context = unsign_policy_context(self.context_token(schema_slug="movilidad"))
        quotation = create_individual_quotation(
            schema=schema,
            cleaned_data={
                "first_name": "Camilo", "last_name": "Vargas", "requester_id_type": "CC",
                "requester_document": "444444444", "normalized_items": {"vehicles": [
                    {"insured_document": "1019059650", "insured_id_type": "CC", "insured_name": "Asegurado distinto"},
                ]}, "attachments": [],
            }, actor=self.actor, context=context,
        )
        service.return_value.search.return_value = ()
        resolve_accepted_person(quotation=quotation)
        quotation.refresh_from_db()
        self.assertEqual([item["document"] for item in quotation.safe_metadata["people_lookup"]], ["444444444"])

    @patch("cotizacion_colectivos.services.individual_quotations.PersonSearchService")
    def test_health_first_person_can_reuse_requester_and_second_is_independent(self, service):
        schema = get_branch_schema("salud")
        context = unsign_policy_context(self.context_token())
        quotation = create_individual_quotation(
            schema=schema,
            cleaned_data={
                "first_name": "Camilo", "last_name": "Vargas", "requester_id_type": "CC",
                "requester_document": "444444444", "requester_birth_date": "1990-01-01", "requester_email": "camilo@example.test",
                "normalized_items": {"people": [
                    {"use_requester": "Sí"},
                    {"first_name": "Maria", "last_name": "Gomez", "id_type": "CC", "document": "555555555", "birth_date": "1992-03-04", "email": "maria@example.test", "phone": "3110000000"},
                ]}, "attachments": [],
            }, actor=self.actor, context=context,
        )
        service.return_value.search.return_value = ()
        resolve_accepted_person(quotation=quotation)
        quotation.refresh_from_db()
        people = quotation.safe_metadata["people_lookup"]
        self.assertEqual([item["document"] for item in people], ["444444444", "555555555"])
        self.assertEqual(people[0]["missing_fields"], [])
        self.assertEqual(people[1]["missing_fields"], [])

    @patch("cotizacion_colectivos.services.individual_quotations.publish_task_outbox")
    def test_complete_responsible_schedules_quote_task_once(self, publish):
        schema = get_branch_schema("salud")
        context = unsign_policy_context(self.context_token())
        context.update({
            "task_responsible": "Sara Rua Vargas",
            "task_responsible_display": "Sara Rua Vargas",
            "task_responsible_email": "sara@example.test",
            "task_area": "Negocios Bienestar y Beneficios",
        })
        with self.captureOnCommitCallbacks(execute=True):
            quotation = create_individual_quotation(
                schema=schema,
                cleaned_data={"first_name": "Camilo", "last_name": "Vargas", "requester_id_type": "CC", "requester_document": "11111111", "normalized_items": {"people": []}, "attachments": []},
                actor=self.actor, context=context,
            )
        publish.assert_called_once_with(quotation.task_outbox.get().pk)

    def _pending_responsible_quotation(self):
        schema = get_branch_schema("salud")
        context = unsign_policy_context(self.context_token())
        return create_individual_quotation(
            schema=schema,
            cleaned_data={
                "first_name": "Persona", "last_name": "Pendiente",
                "requester_id_type": "CC", "requester_document": "11111111",
                "normalized_items": {"people": []}, "attachments": [],
            },
            actor=self.actor, context=context,
        )

    def test_responsible_correction_reuses_outbox_and_publishes_once(self):
        quotation = self._pending_responsible_quotation()
        outbox = quotation.task_outbox.get(event_kind="COTIZACION")
        option = SimpleNamespace(actual_value="Ana Maria Duque", display_value="Ana Maria Duque Bran")
        with patch("cotizacion_colectivos.views.has_internal_permission", return_value=True), patch(
            "cotizacion_colectivos.views.task_responsible_options", return_value=(option,)
        ), patch(
            "cotizacion_colectivos.views.resolve_task_responsible_email", return_value="ana@example.test"
        ), patch("cotizacion_colectivos.views.publish_task_outbox") as publish:
            response = self.client.post(
                reverse("cotizacion_colectivos:individual_update_responsible", args=[sign_receipt(str(quotation.public_id))]),
                {"responsible": option.actual_value},
            )
        self.assertEqual(response.status_code, 302)
        publish.assert_called_once_with(outbox.pk)
        self.assertEqual(CotizacionIndividual.objects.get(pk=quotation.pk).task_outbox.get().pk, outbox.pk)
        self.assertEqual(CotizacionIndividual.objects.get(pk=quotation.pk).task_outbox.count(), 1)
        outbox.refresh_from_db()
        self.assertEqual(outbox.status, outbox.Status.PENDING)
        self.assertEqual(outbox.safe_error_code, "")
        self.assertEqual(json.loads(decrypt(outbox.encrypted_payload))["Correo_responsable"], "ana@example.test")

    def test_responsible_without_email_keeps_outbox_blocked_without_publish(self):
        quotation = self._pending_responsible_quotation()
        outbox = quotation.task_outbox.get(event_kind="COTIZACION")
        option = SimpleNamespace(actual_value="Sin Correo", display_value="Sin Correo")
        with patch("cotizacion_colectivos.views.has_internal_permission", return_value=True), patch(
            "cotizacion_colectivos.views.task_responsible_options", return_value=(option,)
        ), patch(
            "cotizacion_colectivos.views.resolve_task_responsible_email", side_effect=ValidationError("sin correo")
        ), patch("cotizacion_colectivos.views.publish_task_outbox") as publish:
            response = self.client.post(
                reverse("cotizacion_colectivos:individual_update_responsible", args=[sign_receipt(str(quotation.public_id))]),
                {"responsible": option.actual_value},
            )
        self.assertEqual(response.status_code, 302)
        publish.assert_not_called()
        outbox.refresh_from_db()
        self.assertEqual(outbox.status, outbox.Status.PENDING)
        self.assertEqual(outbox.safe_error_code, "RESPONSIBLE_EMAIL_PENDING")

    def test_published_outbox_is_not_republished_when_responsible_changes(self):
        quotation = self._pending_responsible_quotation()
        outbox = quotation.task_outbox.get(event_kind="COTIZACION")
        outbox.status = outbox.Status.PUBLISHED
        outbox.save(update_fields=("status",))
        option = SimpleNamespace(actual_value="Ana Maria Duque", display_value="Ana Maria Duque Bran")
        with patch("cotizacion_colectivos.views.has_internal_permission", return_value=True), patch(
            "cotizacion_colectivos.views.task_responsible_options", return_value=(option,)
        ), patch(
            "cotizacion_colectivos.views.resolve_task_responsible_email", return_value="ana@example.test"
        ), patch("cotizacion_colectivos.views.publish_task_outbox") as publish:
            response = self.client.post(
                reverse("cotizacion_colectivos:individual_update_responsible", args=[sign_receipt(str(quotation.public_id))]),
                {"responsible": option.actual_value},
            )
        self.assertEqual(response.status_code, 302)
        publish.assert_not_called()

    def test_unrelated_blocked_outbox_is_not_retried_by_responsible_change(self):
        quotation = self._pending_responsible_quotation()
        outbox = quotation.task_outbox.get(event_kind="COTIZACION")
        outbox.status = outbox.Status.BLOCKED
        outbox.safe_error_code = "AUTHENTICATION"
        outbox.save(update_fields=("status", "safe_error_code"))
        option = SimpleNamespace(actual_value="Ana Maria Duque", display_value="Ana Maria Duque Bran")
        with patch("cotizacion_colectivos.views.has_internal_permission", return_value=True), patch(
            "cotizacion_colectivos.views.task_responsible_options", return_value=(option,)
        ), patch(
            "cotizacion_colectivos.views.resolve_task_responsible_email", return_value="ana@example.test"
        ), patch("cotizacion_colectivos.views.publish_task_outbox") as publish:
            response = self.client.post(
                reverse("cotizacion_colectivos:individual_update_responsible", args=[sign_receipt(str(quotation.public_id))]),
                {"responsible": option.actual_value},
            )
        self.assertEqual(response.status_code, 302)
        publish.assert_not_called()
        outbox.refresh_from_db()
        self.assertEqual(outbox.status, outbox.Status.BLOCKED)
        self.assertEqual(outbox.safe_error_code, "AUTHENTICATION")

    @patch("cotizacion_colectivos.external_views._individual_workspace")
    def test_mobility_accepts_multiple_vehicles_and_encrypted_attachment(self, workspace):
        workspace.return_value = self.workspace("movilidad")
        uploaded = SimpleUploadedFile(
            "matricula.png", b"\x89PNG\r\n\x1a\nprivate-demo", content_type="image/png",
        )
        response = self.client.post(
            reverse("colectivos_external:individual_quotation", args=[self.access_token(schema_slug="movilidad")]),
            {"items_payload": json.dumps({"vehicles": [self.vehicle("1"), self.vehicle("2")]}), "attachments": uploaded},
        )
        self.assertEqual(response.status_code, 302)
        attachment = AdjuntoCotizacionIndividual.objects.get()
        stored = (Path(self.private.name) / "individual_quotations" / attachment.stored_path).read_bytes()
        self.assertNotIn(b"private-demo", stored)

    @patch("cotizacion_colectivos.external_views._individual_workspace")
    def test_new_vehicle_can_be_quoted_without_a_fictitious_plate(self, workspace):
        workspace.return_value = self.workspace("movilidad")
        vehicle = self.vehicle()
        vehicle["zero_km"] = "Sí"
        vehicle["plate"] = ""
        response = self.client.post(
            reverse("colectivos_external:individual_quotation", args=[self.access_token(schema_slug="movilidad")]),
            {"items_payload": json.dumps({"vehicles": [vehicle]})},
        )
        self.assertEqual(response.status_code, 302)

    @patch("cotizacion_colectivos.external_views._individual_workspace")
    def test_non_zero_km_vehicle_requires_plate_server_side(self, workspace):
        workspace.return_value = self.workspace("movilidad")
        vehicle = self.vehicle()
        vehicle["plate"] = ""
        response = self.client.post(
            reverse("colectivos_external:individual_quotation", args=[self.access_token(schema_slug="movilidad")]),
            {"items_payload": json.dumps({"vehicles": [vehicle]})},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "es obligatoria cuando el vehículo no es 0 km")

    @patch("cotizacion_colectivos.external_views._individual_workspace")
    def test_soat_keeps_affiliate_and_insured_separate(self, workspace):
        workspace.return_value = self.workspace("soat")
        data = {
            "affiliate_name": "Afiliado A", "affiliate_id_type": "CC", "affiliate_document": "400000001",
            "insured_name": "Asegurado B", "insured_id_type": "CC", "insured_document": "500000001",
            "items_payload": json.dumps({"vehicles": [self.vehicle()]}),
        }
        response = self.client.post(
            reverse("colectivos_external:individual_quotation", args=[self.access_token(schema_slug="soat")]), data,
        )
        self.assertEqual(response.status_code, 302)
        payload = json.loads(decrypt(CotizacionIndividual.objects.get().encrypted_payload))
        self.assertNotEqual(payload["fields"]["affiliate_document"], payload["fields"]["insured_document"])

    @patch("cotizacion_colectivos.external_views._individual_workspace")
    def test_invalid_repeatable_and_tampered_token_fail_closed(self, workspace):
        workspace.return_value = self.workspace("salud")
        token = self.access_token()
        invalid = self.client.post(
            reverse("colectivos_external:individual_quotation", args=[token]),
            {"items_payload": json.dumps({"people": []})},
        )
        self.assertEqual(invalid.status_code, 200)
        self.assertContains(invalid, "Agregue entre 1 y 20")
        altered = token[:-1] + ("a" if token[-1] != "a" else "b")
        self.assertEqual(
            self.client.get(reverse("colectivos_external:individual_quotation", args=[altered])).status_code,
            410,
        )

    @patch("cotizacion_colectivos.external_views._individual_workspace")
    def test_external_form_is_contextual_responsive_and_csrf_protected(self, workspace):
        workspace.return_value = self.workspace("movilidad")
        url = reverse(
            "colectivos_external:individual_quotation",
            args=[self.access_token(schema_slug="movilidad")],
        )
        response = self.client.get(url)
        self.assertContains(response, "Contexto: Afiliada Demo")
        self.assertContains(response, "data-add-item")
        self.assertNotContains(response, "Seleccionar ramo")
        csrf_client = Client(enforce_csrf_checks=True)
        self.assertEqual(csrf_client.post(url, {"items_payload": "{}"}).status_code, 403)
        css = Path("static/css/colectivos.css").read_text(encoding="utf-8")
        self.assertIn("@media (max-width:620px)", css)
