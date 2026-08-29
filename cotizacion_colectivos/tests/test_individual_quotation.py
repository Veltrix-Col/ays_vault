from __future__ import annotations

import json
import base64
import hashlib
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils.datastructures import MultiValueDict

from vault.crypto import decrypt
from vault.crypto import encrypt

from cotizacion_colectivos.dto import ContactSummary, GroupMember, PolicyDetail
from cotizacion_colectivos.models import (
    AccesoCotizacionIndividual,
    AdjuntoCotizacionIndividual,
    CotizacionIndividual,
    ColectivosTaskOutbox,
    NotificacionCotizacionIndividual,
)
from cotizacion_colectivos.quotation_forms.catalog import get_branch_schema, with_identification_choices
from cotizacion_colectivos.quotation_forms.forms import IndividualQuotationForm
from cotizacion_colectivos.quotation_forms.security import sign_policy_context, sign_receipt, unsign_policy_context
from cotizacion_colectivos.services.individual_access import IndividualAccessError, generate_individual_access, issue_individual_otp
from cotizacion_colectivos.services.individual_quotations import (
    accept_individual_quotation,
    build_policy_context,
    create_individual_quotation,
    resolve_accepted_person,
)
from cotizacion_colectivos.services.individual_quotations import _individual_task_observations
from cotizacion_colectivos.services.person_contract import build_contact_payload
from cotizacion_colectivos.services.individual_attachment_publisher import (
    IndividualAttachmentBlocked,
    IndividualAttachmentUncertain,
    _validate_document_contract,
    publish_attachment,
)
from cotizacion_colectivos.filenames import build_attachment_filename
from cotizacion_colectivos.views import _HIDDEN_RESPONSE_KEYS, _attachment_can_publish, _attachment_document_status, _human_response_value
from cotizacion_colectivos.services.common import sign_record_id


POLICY_TOKEN = sign_record_id(
    "4234567890123456789",
    "policy",
    context={"source_id": "5234567890123456789", "source_kind": "company"},
)

TEST_IDENTIFICATION_CHOICES = (("PAS", "Pasaporte"), ("CC", "Cédula"))
_BASE_GET_BRANCH_SCHEMA = get_branch_schema


def valid_minimal_pdf_bytes():
    objects = (
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] /Contents 4 0 R >>",
        b"<< /Length 0 >>\nstream\n\nendstream",
    )
    body = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, value in enumerate(objects, 1):
        offsets.append(len(body))
        body.extend(f"{index} 0 obj\n".encode())
        body.extend(value)
        body.extend(b"\nendobj\n")
    xref_offset = len(body)
    body.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    body.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        body.extend(f"{offset:010d} 00000 n \n".encode())
    body.extend(f"trailer\n<< /Root 1 0 R /Size {len(objects) + 1} >>\nstartxref\n{xref_offset}\n%%EOF\n".encode())
    return bytes(body)


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
    def test_human_response_formatter_preserves_boolean_semantics(self):
        self.assertEqual(_human_response_value("is_requester", True), "Sí")
        self.assertEqual(_human_response_value("is_requester", False), "No")
        self.assertEqual(_human_response_value("is_requester", ""), "Sin información")
        self.assertEqual(_human_response_value("is_requester", None), "Sin información")

    def test_entity_key_is_internal_not_a_human_response_value(self):
        self.assertEqual(_human_response_value("entity_key", "vehicles-stable-1"), "vehicles-stable-1")
        self.assertIn("entity_key", _HIDDEN_RESPONSE_KEYS)

    def setUp(self):
        self.private = tempfile.TemporaryDirectory()
        self.addCleanup(self.private.cleanup)
        override = self.settings(COLECTIVOS_PRIVATE_ROOT=Path(self.private.name))
        override.enable()
        self.addCleanup(override.disable)
        self.actor = get_user_model().objects.create_user(
            username="individual-owner", password="safe-test-password",
        )
        # Direct form tests intentionally use the same explicit catalog
        # injection as the production view.  This keeps their fixtures close
        # to a request carrying Contacts.Tipo_ID metadata without making the
        # form query Zoho or reintroducing a hardcoded production catalog.
        self._schema_patch = patch(
            "cotizacion_colectivos.tests.test_individual_quotation.get_branch_schema",
            side_effect=lambda slug: with_identification_choices(
                _BASE_GET_BRANCH_SCHEMA(slug), TEST_IDENTIFICATION_CHOICES,
            ),
        )
        self._schema_patch.start()
        self.addCleanup(self._schema_patch.stop)

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
        self.assertLess(generated.access.otp_expires_at, generated.access.expires_at)
        self.assertNotEqual(generated.access.otp_hash, "654321")
        self.assertNotIn("654321", repr(generated.access.__dict__))
        self.assertNotIn("654321", repr(notification_logger.mock_calls))

    @patch("cotizacion_colectivos.external_views._individual_workspace")
    def test_individual_access_without_otp_opens_form_directly_and_persists_decision(self, workspace):
        workspace.return_value = self.workspace("movilidad")
        context = unsign_policy_context(self.context_token(schema_slug="movilidad"))
        generated = generate_individual_access(
            context=context, actor=self.actor, recipient="", otp_required=False,
        )
        self.assertIs(generated.access.safe_metadata["otp_required"], False)
        response = self.client.get(reverse("colectivos_external:individual_quotation", args=[generated.token]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Código de verificación")
        self.assertContains(response, "Información para cotizar")
        generated.access.refresh_from_db()
        self.assertFalse(generated.access.otp_hash)
        self.assertGreaterEqual(generated.access.access_count, 1)

    def test_invalid_individual_token_is_rejected_even_when_otp_is_disabled(self):
        response = self.client.get(
            reverse("colectivos_external:individual_quotation", args=["invalid.token-value"]),
        )
        self.assertEqual(response.status_code, 410)

    def test_individual_access_with_otp_requires_a_valid_recipient(self):
        context = unsign_policy_context(self.context_token())
        with self.assertRaises(IndividualAccessError):
            generate_individual_access(context=context, actor=self.actor, recipient="", otp_required=True)

    @override_settings(COLECTIVOS_EXTERNAL_LINK_TTL_SECONDS=172800)
    def test_individual_link_ttl_is_elapsed_48_hours(self):
        context = unsign_policy_context(self.context_token())
        generated = generate_individual_access(context=context, actor=self.actor, recipient="demo@example.test")
        self.assertAlmostEqual(
            (generated.access.expires_at - generated.access.created_at).total_seconds(),
            172800,
            delta=2,
        )

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
            "first_name": "Persona", "last_name": str(suffix), "id_type": "CC",
            "document": f"20000000{suffix}", "birth_date": "1990-01-01",
            "email": f"persona{suffix}@example.test", "phone": "3000000000",
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
            "model": "2025", "class": "Automovil", "city": "Bogotá", "use": "Familiar",
            "insured_name": f"Asegurado {suffix}", "insured_id_type": "CC",
            "insured_document": f"30000000{suffix}",
        }

    def workspace(self, schema_slug):
        schema = get_branch_schema(schema_slug)
        return (
            policy(), (affiliate(),), {"storage": "database"}, schema,
            TEST_IDENTIFICATION_CHOICES,
        )

    def test_mobility_vehicle_class_is_an_allowlisted_required_select_and_plate_stays_visible(self):
        schema = get_branch_schema("movilidad")
        vehicle = next(group for group in schema.repeatables if group.key == "vehicles")
        fields = {field.key: field for field in vehicle.fields}
        self.assertEqual(fields["class"].kind, "choice")
        self.assertTrue(fields["class"].required)
        self.assertEqual(fields["class"].choices, (
            "Automovil", "Camioneta", "Motocicleta", "Camiones y transporte de carga",
            "Transporte publico pasajeros", "Vehiculos especiales",
        ))
        self.assertEqual(fields["use"].kind, "choice")
        self.assertEqual(fields["use"].choices, ("Familiar", "Comercial"))
        self.assertFalse(fields["plate"].required)
        self.assertFalse(fields["plate"].show_when)

    def test_public_mobility_form_uses_placeholders_and_entity_multipart_inputs(self):
        schema = get_branch_schema("movilidad")
        context = unsign_policy_context(self.context_token(schema_slug="movilidad"))
        form = IndividualQuotationForm(schema=schema, context=context)
        self.assertEqual(form.fields["first_name"].widget.attrs["placeholder"], "Ej. Juan Carlos")
        self.assertEqual(form.fields["requester_document"].widget.attrs["placeholder"], "Ej. 1030123456")
        self.assertEqual(form.fields["requester_email"].widget.attrs["placeholder"], "Ej. usuario@correo.com")
        self.assertNotIn("placeholder", form.fields["requester_id_type"].widget.attrs)
        javascript = (Path(__file__).parents[2] / "static" / "js" / "colectivos-individual.js").read_text(encoding="utf-8")
        template = (Path(__file__).parents[2] / "templates" / "cotizacion_colectivos" / "individual" / "form.html").read_text(encoding="utf-8")
        self.assertIn("entity_attachment_${row.entity_key}", javascript)
        self.assertIn('enctype="multipart/form-data"', template)

    @patch("cotizacion_colectivos.external_views._individual_workspace")
    def test_rendered_mobility_form_exposes_canonical_vehicle_choices_without_fake_vehicle(self, workspace):
        workspace.return_value = self.workspace("movilidad")
        token = self.access_token(schema_slug="movilidad")
        response = self.client.get(reverse("colectivos_external:individual_quotation", args=[token]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Camioneta")
        self.assertContains(response, "Familiar")
        self.assertContains(response, "Comercial")
        self.assertContains(response, "+ Agregar vehículo")
        self.assertNotContains(response, "Vehículo 1")
        self.assertNotContains(response, "Se precarga cuando el enlace se genera desde un cliente.")

    @patch("cotizacion_colectivos.external_views._individual_workspace")
    def test_health_form_does_not_render_empty_primary_person_card(self, workspace):
        workspace.return_value = self.workspace("salud")
        response = self.client.get(reverse("colectivos_external:individual_quotation", args=[self.access_token()]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Persona 1")
        javascript = (Path(__file__).parents[2] / "static" / "js" / "colectivos-individual.js").read_text(encoding="utf-8")
        self.assertIn('schema.slug === "salud"', javascript)
        self.assertIn('Asegurado principal', javascript)
        self.assertIn('serialized.people = [requester, ...serialized.people.filter(row => !row.is_requester)]', javascript)

    def test_entity_multipart_file_is_parsed_with_its_stable_owner_key(self):
        schema = get_branch_schema("movilidad")
        context = unsign_policy_context(self.context_token(schema_slug="movilidad"))
        vehicle = self.vehicle()
        vehicle["entity_key"] = "vehicles-stable-1"
        uploaded = SimpleUploadedFile("matricula.pdf", valid_minimal_pdf_bytes(), content_type="application/pdf")
        form = IndividualQuotationForm(
            schema=schema, context=context,
            data={"items_payload": json.dumps({"vehicles": [vehicle]})},
            files=MultiValueDict({"entity_attachment_vehicles-stable-1": [uploaded]}),
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data["entity_attachments"]["vehicles-stable-1"].name,
            "matricula.pdf",
        )

    def test_dynamic_vehicle_editor_preserves_entity_key_on_edit(self):
        javascript = (Path(__file__).parents[2] / "static" / "js" / "colectivos-individual.js").read_text(encoding="utf-8")
        self.assertIn("const previous = activeIndex === null ? null : groups[activeGroup][activeIndex];", javascript)
        self.assertIn("row.entity_key = previous?.entity_key || newEntityKey", javascript)

    def test_mobility_vehicle_class_rejects_empty_or_unknown_values_server_side(self):
        schema = get_branch_schema("movilidad")
        context = unsign_policy_context(self.context_token(schema_slug="movilidad"))
        base = self.vehicle()
        for value in ("", "Vehículo inventado"):
            form = IndividualQuotationForm(
                schema=schema, context=context,
                data={"items_payload": json.dumps({"vehicles": [{**base, "class": value}]})},
            )
            self.assertFalse(form.is_valid())
            self.assertIn("Clase", str(form.errors))

    def test_mobility_vehicle_use_rejects_values_outside_the_public_contract(self):
        schema = get_branch_schema("movilidad")
        context = unsign_policy_context(self.context_token(schema_slug="movilidad"))
        vehicle = self.vehicle()
        vehicle["use"] = "Caserito"
        form = IndividualQuotationForm(
            schema=schema, context=context,
            data={"items_payload": json.dumps({"vehicles": [vehicle]})},
        )
        self.assertFalse(form.is_valid())
        self.assertIn("Uso", str(form.errors))

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

    @patch("cotizacion_colectivos.external_views._individual_workspace")
    def test_external_guide_is_client_facing_and_branch_specific(self, workspace):
        workspace.return_value = self.workspace("movilidad")
        token = self.access_token(schema_slug="movilidad")
        response = self.client.get(reverse("colectivos_external:individual_quotation", args=[token]))
        self.assertContains(response, "Antes de comenzar")
        self.assertContains(response, "Indique los datos del asegurado de cada vehículo cuando sea diferente al afiliado.")
        self.assertNotContains(response, "Outbox")

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

    def test_existing_affiliate_prefill_preserves_structured_contact_fields(self):
        member = replace(
            affiliate(),
            first_name="VELTRIX TEST",
            last_name="MOVILIDAD 001",
            birth_date="1990-01-01",
            associate_first_name="VELTRIX TEST",
            associate_last_name="MOVILIDAD 001",
            associate_birth_date="1990-01-01",
            document="990000001001",
            associate_document="990000001001",
        )
        schema, _token, context = build_policy_context(
            policy_token=POLICY_TOKEN,
            detail=policy(branch_code="40", branch_name="Movilidad colectivo"),
            members=(member,),
            affiliate_key="affiliate-hmac-key",
            creator_id=self.actor.pk,
        )
        self.assertEqual(schema.slug, "movilidad")
        self.assertEqual(context["first_name"], "VELTRIX TEST")
        self.assertEqual(context["last_name"], "MOVILIDAD 001")
        self.assertEqual(context["requester_id_type"], "CC")
        self.assertEqual(context["requester_document"], "990000001001")
        self.assertEqual(context["requester_birth_date"], "1990-01-01")
        self.assertEqual(context["requester_email"], "demo@example.test")
        self.assertEqual(context["requester_phone"], "3000000000")
        form = IndividualQuotationForm(
            schema=schema, context=context,
            initial={"items_payload": json.dumps({"vehicles": [{}]})},
        )
        self.assertEqual(form["first_name"].value(), "VELTRIX TEST")
        self.assertEqual(form["last_name"].value(), "MOVILIDAD 001")
        self.assertEqual(form["requester_birth_date"].value(), "1990-01-01")

    def test_existing_affiliate_prefill_does_not_invent_partial_contact_values(self):
        member = replace(
            affiliate(),
            associate_first_name="VELTRIX TEST",
            associate_last_name="",
            associate_birth_date="",
        )
        _schema, _token, context = build_policy_context(
            policy_token=POLICY_TOKEN,
            detail=policy(), members=(member,), affiliate_key="affiliate-hmac-key",
            creator_id=self.actor.pk,
        )
        self.assertEqual(context["first_name"], "VELTRIX TEST")
        self.assertEqual(context["last_name"], "")
        self.assertEqual(context["requester_birth_date"], "")
        self.assertEqual(context["requester_email"], "demo@example.test")

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
        self.assertEqual(context["affiliate_label"], "Nuevo afiliado")
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
        missing = IndividualQuotationForm(
            data, schema=schema, context=context,
            identification_choices=TEST_IDENTIFICATION_CHOICES,
        )
        self.assertFalse(missing.is_valid())
        self.assertIn("declared_company", missing.errors)
        valid = IndividualQuotationForm(
            {**data, "declared_company": "Constructora Ejemplo S.A.S."},
            schema=schema, context=context,
            identification_choices=TEST_IDENTIFICATION_CHOICES,
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

    def test_vida_uses_minimal_people_contract_and_no_empty_primary_row(self):
        schema = get_branch_schema("vida")
        people = next(group for group in schema.repeatables if group.key == "people")
        self.assertEqual(people.minimum, 0)
        self.assertEqual(
            {field.key for field in people.fields},
            {"is_requester", "first_name", "last_name", "id_type", "document", "birth_date", "email", "phone"},
        )
        form = IndividualQuotationForm(
            data={
                "first_name": "Camilo", "last_name": "Vargas", "requester_id_type": "CC",
                "requester_document": "444444444", "requester_birth_date": "1990-01-01",
                "requester_email": "camilo@example.test", "requester_phone": "3000000000",
                "items_payload": json.dumps({"people": []}),
            }, schema=schema, context={},
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["normalized_items"]["people"], [])

    def test_vida_different_primary_and_additional_people_keep_independent_keys(self):
        schema = get_branch_schema("vida")
        form = IndividualQuotationForm(
            data={
                "first_name": "Afiliado", "last_name": "Principal", "requester_id_type": "CC",
                "requester_document": "444444444", "requester_birth_date": "1990-01-01",
                "requester_email": "affiliate@example.test", "requester_phone": "3000000000",
                "items_payload": json.dumps({"people": [
                    {"entity_key": "people-primary", "is_requester": False, "first_name": "María", "last_name": "Uno", "id_type": "CC", "document": "555555555", "birth_date": "1991-01-01", "email": "maria@example.test", "phone": "3000000001"},
                    {"entity_key": "people-additional", "is_requester": False, "first_name": "José", "last_name": "Dos", "id_type": "CC", "document": "666666666", "birth_date": "1992-01-01", "email": "jose@example.test", "phone": "3000000002"},
                ]}),
            }, schema=schema, context={},
        )
        self.assertTrue(form.is_valid(), form.errors)
        rows = form.cleaned_data["normalized_items"]["people"]
        self.assertEqual([row["entity_key"] for row in rows], ["people-primary", "people-additional"])

    def test_mobility_same_requester_checkbox_copies_structured_identity_to_vehicle(self):
        schema = get_branch_schema("movilidad")
        vehicle_keys = {field.key for field in schema.repeatables[0].fields}
        self.assertNotIn("insured_name", vehicle_keys)
        self.assertIn("insured_first_name", vehicle_keys)
        self.assertIn("insured_last_name", vehicle_keys)
        form = IndividualQuotationForm(
            data={
                "first_name": "Camilo 2", "last_name": "Vargas 2", "requester_id_type": "CC",
                "requester_document": "444444444", "requester_birth_date": "1990-01-01",
                "requester_email": "camilo@example.test", "requester_phone": "3000000000",
                "items_payload": json.dumps({"vehicles": [{**self.vehicle("1"), "insured_same_as_requester": True}]}),
            }, schema=schema, context={},
        )
        self.assertTrue(form.is_valid(), form.errors)
        vehicle = form.cleaned_data["normalized_items"]["vehicles"][0]
        self.assertTrue(vehicle["insured_same_as_requester"])
        self.assertEqual(vehicle["insured_first_name"], "Camilo 2")
        self.assertEqual(vehicle["insured_last_name"], "Vargas 2")
        self.assertEqual(vehicle["insured_document"], "444444444")

    def test_health_requester_checkbox_cannot_be_selected_twice(self):
        schema = get_branch_schema("salud")
        form = IndividualQuotationForm(
            data={
                "first_name": "Camilo", "last_name": "Vargas", "requester_id_type": "CC",
                "requester_document": "444444444", "requester_birth_date": "1990-01-01",
                "requester_email": "camilo@example.test", "requester_phone": "3000000000",
                "items_payload": json.dumps({"people": [
                    {"is_requester": True, "employment_relationship": "Empleado", "relationship": "Titular", "currently_health_insured": "No"},
                    {"is_requester": True, "employment_relationship": "Grupo familiar", "relationship": "Cónyuge", "currently_health_insured": "No"},
                ]}),
            }, schema=schema, context={},
        )
        self.assertFalse(form.is_valid())
        self.assertTrue(any("sólo puede agregarse una vez" in str(error) for error in form.errors["items_payload"]))

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

    @patch("cotizacion_colectivos.services.individual_quotations.PersonSearchService")
    def test_mobility_new_relation_adds_only_explicit_different_insured(self, service):
        schema = get_branch_schema("movilidad")
        context = unsign_policy_context(self.context_token(schema_slug="movilidad"))
        quotation = create_individual_quotation(
            schema=schema,
            cleaned_data={
                "first_name": "Camilo 2", "last_name": "Vargas 2", "requester_id_type": "CC",
                "requester_document": "444444444", "requester_birth_date": "1990-01-01",
                "requester_email": "camilo@example.test", "requester_phone": "3000000000",
                "normalized_items": {"vehicles": [
                    {"insured_same_as_requester": True, "insured_document": "444444444"},
                    {"insured_same_as_requester": True, "insured_document": "444444444"},
                    {"insured_same_as_requester": False, "insured_first_name": "Ana", "insured_last_name": "Diferente",
                     "insured_id_type": "CC", "insured_document": "1019059650", "insured_birth_date": "1991-01-01",
                     "insured_email": "ana@example.test", "insured_phone": "3110000000", "insured_name": "Ana Diferente"},
                ]}, "attachments": [],
            }, actor=self.actor, context=context,
        )
        service.return_value.search.return_value = ()
        resolve_accepted_person(quotation=quotation)
        quotation.refresh_from_db()
        self.assertEqual([item["document"] for item in quotation.safe_metadata["people_lookup"]], ["444444444", "1019059650"])
        self.assertEqual(quotation.safe_metadata["people_lookup"][1]["missing_fields"], [])

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
    @patch("cotizacion_colectivos.views.resolve_accepted_person")
    def test_decision_rejection_is_reactivatable_and_accepted_is_terminal(self, resolve_person, workspace):
        workspace.return_value = self.workspace("salud")
        resolve_person.return_value = {"status": "found"}
        self.client.post(
            reverse("colectivos_external:individual_quotation", args=[self.access_token()]),
            {"items_payload": json.dumps({"people": [self.person("1")]})},
        )
        quotation = CotizacionIndividual.objects.get()
        original_encrypted = quotation.encrypted_payload
        token = sign_receipt(quotation.public_id)
        self.client.force_login(self.actor)

        pending = self.client.get(reverse("cotizacion_colectivos:individual_expedient", args=[token]))
        self.assertContains(pending, "Cotización aceptada")
        self.assertContains(pending, 'data-loading-message="Verificando información en Zoho…"')
        self.assertContains(pending, "Cotización rechazada")
        rendered = pending.content.decode()
        self.assertGreater(rendered.index("individual-decision-card"), rendered.index("zoho-operational-card"))

        rejected = self.client.post(reverse("cotizacion_colectivos:individual_reject", args=[token]))
        self.assertEqual(rejected.status_code, 302)
        quotation.refresh_from_db()
        self.assertEqual(quotation.safe_metadata["acceptance"]["status"], "rejected")
        self.assertEqual(quotation.safe_metadata["acceptance"]["rejected_by"], self.actor.pk)
        self.assertEqual(quotation.encrypted_payload, original_encrypted)
        rejected_detail = self.client.get(reverse("cotizacion_colectivos:individual_expedient", args=[token]))
        self.assertContains(rejected_detail, "Cotización rechazada")
        self.assertNotContains(rejected_detail, "Crear afiliado en Zoho")

        self.client.post(reverse("cotizacion_colectivos:individual_reactivate", args=[token]))
        quotation.refresh_from_db()
        self.assertEqual(quotation.safe_metadata["acceptance"]["status"], "pending")
        self.assertEqual(quotation.safe_metadata["acceptance"]["rejected_by"], self.actor.pk)
        self.assertEqual(quotation.safe_metadata["acceptance"]["reactivated_by"], self.actor.pk)

        self.client.post(reverse("cotizacion_colectivos:individual_accept", args=[token]))
        quotation.refresh_from_db()
        self.assertEqual(quotation.safe_metadata["acceptance"]["status"], "accepted")
        self.client.post(reverse("cotizacion_colectivos:individual_reject", args=[token]))
        quotation.refresh_from_db()
        self.assertEqual(quotation.safe_metadata["acceptance"]["status"], "accepted")

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

    def test_task_observations_are_humanized_in_spanish(self):
        text = _individual_task_observations(
            {"branch_name": "Movilidad colectivo", "collective_context": "Fonconstruimos"},
            {"first_name": "Camilo", "last_name": "Vargas", "requester_id_type": "CC", "requester_document": "123456"},
            {"vehicles": [{"zero_km": "Sí", "brand": "Prueba", "line": "La última 2", "model": "2026", "currently_insured": "No"}]},
        )
        self.assertIn("Solicitante:", text)
        self.assertIn("Vehículo 1:", text)
        self.assertIn("Cero kilómetros: Sí", text)
        self.assertIn("Referencia: La última 2", text)
        self.assertNotIn("requester_id_type", text)
        self.assertNotIn("zero_km", text)

    def test_health_task_observations_map_all_person_fields_to_spanish(self):
        text = _individual_task_observations(
            {"branch_name": "Salud colectivo", "collective_context": "Colectiva Demo"},
            {"first_name": "P", "last_name": "Uno", "requester_id_type": "CC", "requester_document": "11111111111"},
            {"people": [
                {"use_requester": "Sí", "first_name": "P Uno", "last_name": "Uno", "id_type": "CC",
                 "document": "11111111111", "birth_date": "2011-06-08", "email": "uno@example.test",
                 "phone": "3000000000", "gender": "Masculino", "employment_relationship": "Empleado",
                 "relationship": "Parcero", "currently_health_insured": "No", "plan_interest": "Básico"},
                {"use_requester": "No", "first_name": "P Dos", "last_name": "Dos", "id_type": "CC",
                 "document": "2222222222", "birth_date": "2011-06-08", "email": "dos@example.test",
                 "phone": "3110000000", "gender": "Masculino", "employment_relationship": "Grupo familiar",
                 "relationship": "Parcero", "currently_health_insured": "Sí", "current_health_insurer": "Sura",
                 "current_health_policy_end": "2026-08-31", "plan_interest": "Básico"},
            ]},
        )
        for label in ("Nombres", "Apellidos", "Tipo de identificación", "Fecha de nacimiento", "Correo electrónico", "Teléfono"):
            self.assertIn(label + ":", text)
        for technical in ("first_name:", "last_name:", "id_type:", "birth_date:", "email:", "phone:",
                          "use_requester:", "currently_health_insured:", "employment_relationship:",
                          "relationship:", "plan_interest:"):
            self.assertNotIn(technical, text)

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
        quotation = CotizacionIndividual.objects.get()
        payload = json.loads(decrypt(quotation.encrypted_payload))
        self.assertEqual(payload["groups"]["vehicles"][0]["plate"], "")

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
        self.assertContains(response, "Afiliado: Afiliada Demo")
        self.assertContains(response, "data-add-item")
        self.assertNotContains(response, "Seleccionar ramo")
        csrf_client = Client(enforce_csrf_checks=True)
        self.assertEqual(csrf_client.post(url, {"items_payload": "{}"}).status_code, 403)
        css = Path("static/css/colectivos.css").read_text(encoding="utf-8")
        self.assertIn("@media (max-width:620px)", css)

    def test_entity_document_is_encrypted_and_scoped_to_vehicle(self):
        schema = get_branch_schema("movilidad")
        vehicle = self.vehicle()
        vehicle["entity_key"] = "vehicles-stable-1"
        uploaded = SimpleUploadedFile(
            "tarjeta.pdf", b"%PDF-1.4 synthetic", content_type="application/pdf"
        )
        quotation = create_individual_quotation(
            schema=schema,
            cleaned_data={
                "items_payload": json.dumps({"vehicles": [vehicle]}),
                "attachments": [],
                "entity_attachments": {"vehicles-stable-1": uploaded},
                "normalized_items": {"vehicles": [vehicle]},
                **{field.key: "" for field in schema.fields},
            },
            actor=self.actor,
            context={"affiliate_key": "", "branch_name": schema.name},
        )
        attachment = quotation.attachments.get()
        self.assertEqual(attachment.safe_metadata["owner_role"], "risk")
        self.assertEqual(attachment.safe_metadata["owner_type"], "risk")
        self.assertEqual(attachment.safe_metadata["owner_key"], "vehicles-stable-1")
        self.assertEqual(attachment.safe_metadata["risk_key"], "vehicles-stable-1")
        self.assertEqual(attachment.safe_metadata["document_type"], "vehicle_registration")
        self.assertEqual(attachment.safe_metadata["field_key"], "vehicle_registration")
        stored = (Path(self.private.name) / "individual_quotations" / attachment.stored_path).read_bytes()
        self.assertNotIn(b"%PDF-1.4 synthetic", stored)

    def test_vehicle_document_contract_is_canonical(self):
        self.assertEqual(
            _validate_document_contract(
                module="Riesgos", owner_type="risk", document_type="vehicle_registration"
            ),
            "vehicle_registration",
        )

    def test_valid_pdf_bytes_survive_attachment_storage_encoding_byte_for_byte(self):
        original = valid_minimal_pdf_bytes()
        self.assertTrue(original.startswith(b"%PDF-"))
        restored = base64.b64decode(decrypt(encrypt(base64.b64encode(original).decode())).encode())
        self.assertEqual(hashlib.sha256(restored).hexdigest(), hashlib.sha256(original).hexdigest())
        self.assertEqual(restored, original)

    def test_zoho_attachment_filename_is_canonical_and_deterministic(self):
        self.assertEqual(
            build_attachment_filename(
                document_type="identity_document", identification_type="CC",
                identification_number="1019059650", original_filename="original.PDF",
            ),
            "CEDULA_CC_1019059650.pdf",
        )
        self.assertEqual(
            build_attachment_filename(
                document_type="identity_document", identification_type="PAS",
                identification_number="AB123456", original_filename="doc.pdf",
            ),
            "PASAPORTE_PAS_AB123456.pdf",
        )
        self.assertEqual(
            build_attachment_filename(
                document_type="vehicle_registration", plate="abc123",
                original_filename="tarjeta.PdF",
            ),
            "TARJETA_PROPIEDAD_ABC123.pdf",
        )
        self.assertEqual(
            build_attachment_filename(
                document_type="identity_document", identification_type="CE",
                identification_number="á/ 10:19", original_filename="x.JpG",
            ),
            "CEDULA_EXTRANJERIA_CE_A_10_19.jpg",
        )

    def test_legacy_vehicle_document_contract_remains_controlled(self):
        self.assertEqual(
            _validate_document_contract(
                module="Riesgos", owner_type="risk", document_type="risk_document"
            ),
            "vehicle_registration",
        )

    def test_contact_identity_document_contract_remains_exact(self):
        self.assertEqual(
            _validate_document_contract(
                module="Contacts", owner_type="contact", document_type="identity_document"
            ),
            "identity_document",
        )

    def test_arbitrary_risk_document_type_is_rejected(self):
        with self.assertRaises(ValidationError):
            _validate_document_contract(
                module="Riesgos", owner_type="risk", document_type="arbitrary"
            )

    def test_pending_document_is_publishable_for_created_or_found_remote_risk(self):
        attachment = SimpleNamespace(safe_metadata={"document_type": "vehicle_registration"})
        self.assertEqual(_attachment_document_status(attachment), "pending")
        self.assertTrue(_attachment_can_publish(attachment, "4991513000271052016"))

    def test_document_publication_state_blocks_uploaded_or_uncertain(self):
        uploaded = SimpleNamespace(safe_metadata={"zoho_attachment": {"status": "UPLOADED"}})
        uncertain = SimpleNamespace(safe_metadata={"zoho_attachment": {"status": "RECONCILE_REQUIRED"}})
        self.assertFalse(_attachment_can_publish(uploaded, "4991513000271052016"))
        self.assertFalse(_attachment_can_publish(uncertain, "4991513000271052016"))
        self.assertFalse(_attachment_can_publish(SimpleNamespace(safe_metadata={}), ""))

    @override_settings(
        ZOHO_ACTIVE_PROFILE="sandbox",
        COLECTIVOS_ATTACHMENT_PUBLISH_ENABLED=True,
        COLECTIVOS_SANDBOX_ATTACHMENT_WRITE_CONFIRMATION="incorrecta",
        COLECTIVOS_ATTACHMENT_WRITE_CONFIRMATION="",
    )
    def test_attachment_publish_requires_its_own_sandbox_guard(self):
        attachment = SimpleNamespace(
            stored_path="missing.enc",
            safe_original_name="document.pdf",
            detected_mime="application/pdf",
            safe_metadata={"owner_type": "risk", "document_type": "vehicle_registration", "owner_key": "vehicles-a"},
        )
        with patch("cotizacion_colectivos.services.individual_attachment_publisher._get_zoho") as get_zoho:
            with self.assertRaises(IndividualAttachmentBlocked):
                publish_attachment(attachment=attachment, module="Riesgos", record_id="4991513000000000001")
            get_zoho.assert_not_called()

    @override_settings(
        ZOHO_ACTIVE_PROFILE="sandbox",
        COLECTIVOS_ATTACHMENT_PUBLISH_ENABLED=True,
        COLECTIVOS_SANDBOX_ATTACHMENT_WRITE_CONFIRMATION="SANDBOX_ATTACHMENT_WRITE",
    )
    def test_uploaded_attachment_metadata_is_idempotent_without_sdk_call(self):
        attachment = SimpleNamespace(
            safe_metadata={
                "owner_type": "contact", "document_type": "identity_document",
                "zoho_status": "uploaded", "zoho_module": "Contacts",
                "zoho_record_id": "4991513000000000001",
                "zoho_attachment_id": "4991513000000000002",
            },
        )
        with patch("cotizacion_colectivos.services.individual_attachment_publisher._get_zoho") as get_zoho:
            result = publish_attachment(
                attachment=attachment, module="Contacts", record_id="4991513000000000001"
            )
        self.assertEqual(result["status"], "UPLOADED")
        self.assertEqual(result["attachment_id"], "4991513000000000002")
        get_zoho.assert_not_called()

    @override_settings(
        ZOHO_ACTIVE_PROFILE="sandbox",
        COLECTIVOS_ATTACHMENT_PUBLISH_ENABLED=True,
        COLECTIVOS_SANDBOX_ATTACHMENT_WRITE_CONFIRMATION="SANDBOX_ATTACHMENT_WRITE",
    )
    def test_attachment_in_progress_requires_reconciliation_and_does_not_retry(self):
        attachment = SimpleNamespace(
            safe_metadata={
                "owner_type": "contact", "document_type": "identity_document",
                "zoho_status": "uploading", "zoho_module": "Contacts",
                "zoho_record_id": "4991513000000000001",
                "zoho_upload_started_at": "2099-01-01T00:00:00+00:00",
                "zoho_attachment": {"status": "UPLOADING", "module": "Contacts", "record_id": "4991513000000000001"},
            },
        )
        with patch("cotizacion_colectivos.services.individual_attachment_publisher._get_zoho") as get_zoho:
            with self.assertRaises(IndividualAttachmentUncertain):
                publish_attachment(attachment=attachment, module="Contacts", record_id="4991513000000000001")
        get_zoho.assert_not_called()

    @override_settings(
        COLECTIVOS_PRIVATE_ROOT=".",
        ZOHO_ACTIVE_PROFILE="sandbox",
        COLECTIVOS_ATTACHMENT_PUBLISH_ENABLED=True,
        COLECTIVOS_SANDBOX_ATTACHMENT_WRITE_CONFIRMATION="SANDBOX_ATTACHMENT_WRITE",
    )
    def test_attachment_publisher_changes_only_zoho_filename(self):
        from cotizacion_colectivos.services.individual_attachment_publisher import _publish_attachment

        original = valid_minimal_pdf_bytes()
        target = Path(self.private.name) / "individual_quotations" / "publisher-test.enc"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(encrypt(base64.b64encode(original).decode()).encode())
        attachment = SimpleNamespace(
            stored_path="publisher-test.enc", safe_original_name="cliente.PDF",
            detected_mime="application/pdf", safe_metadata={
                "owner_type": "contact", "document_type": "identity_document",
                "identification_type": "CC", "identification_number": "1019059650",
            }, save=Mock(),
        )
        upload = Mock(return_value={"attachment_id": "4991513000000000002"})
        zoho = SimpleNamespace(attachments=SimpleNamespace(upload=upload))
        with patch("cotizacion_colectivos.services.individual_attachment_publisher.settings.COLECTIVOS_PRIVATE_ROOT", self.private.name):
            result = _publish_attachment(
                attachment=attachment, module="Contacts", record_id="4991513000000000001", zoho=zoho,
            )
        uploaded = upload.call_args.kwargs
        self.assertEqual(uploaded["filename"], "CEDULA_CC_1019059650.pdf")
        self.assertEqual(uploaded["file"].getvalue(), original)
        self.assertEqual(uploaded["content_type"], "application/pdf")
        self.assertEqual(uploaded["module"], "Contacts")
        self.assertEqual(uploaded["record_id"], "4991513000000000001")
        self.assertEqual(result["attachment_id"], "4991513000000000002")

    @patch("cotizacion_colectivos.external_views._individual_workspace")
    def test_new_form_has_no_global_upload_and_prefilled_affiliate_has_no_identity_upload(self, workspace):
        workspace.return_value = self.workspace("movilidad")
        response = self.client.get(
            reverse("colectivos_external:individual_quotation", args=[self.access_token(schema_slug="movilidad")])
        )
        self.assertNotContains(response, 'name="attachments"')
        self.assertNotContains(response, 'name="affiliate_document"')
