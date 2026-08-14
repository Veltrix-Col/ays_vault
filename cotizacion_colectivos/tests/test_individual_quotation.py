from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from vault.crypto import decrypt

from cotizacion_colectivos.dto import ContactSummary, GroupMember, PolicyDetail
from cotizacion_colectivos.models import (
    AdjuntoCotizacionIndividual,
    CotizacionIndividual,
    NotificacionCotizacionIndividual,
)
from cotizacion_colectivos.quotation_forms.catalog import get_branch_schema
from cotizacion_colectivos.quotation_forms.security import sign_policy_context, sign_receipt
from cotizacion_colectivos.services.individual_quotations import build_policy_context
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

    @staticmethod
    def person(suffix="1"):
        return {
            "name": f"Persona {suffix}", "id_type": "CC",
            "document": f"20000000{suffix}", "birth_date": "1990-01-01",
            "gender": "Femenino", "relationship": "Hijo(a)", "role": "Asegurado",
        }

    @staticmethod
    def vehicle(suffix="1"):
        return {
            "plate": f"ABC12{suffix}", "brand": "Marca", "line": "Línea",
            "model": "2025", "city": "Bogotá", "use": "Familiar",
            "insured_name": f"Asegurado {suffix}", "insured_id_type": "CC",
            "insured_document": f"30000000{suffix}",
        }

    def workspace(self, schema_slug):
        schema = get_branch_schema(schema_slug)
        return policy(), (affiliate(),), {"storage": "database"}, schema

    def test_tool_entry_goes_to_client_search_and_loose_branch_form_is_closed(self):
        response = self.client.get(reverse("public_home"))
        self.assertContains(response, "Cotización Individual")
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

    @patch("cotizacion_colectivos.external_views._individual_workspace")
    @patch("cotizacion_colectivos.zoho.get_zoho")
    def test_health_multiple_people_submit_encrypted_notifies_and_never_calls_zoho(self, get_zoho, workspace):
        workspace.return_value = self.workspace("salud")
        data = {"items_payload": json.dumps({"people": [self.person("1"), self.person("2")]})}
        response = self.client.post(
            reverse("colectivos_external:individual_quotation", args=[self.context_token()]), data,
        )
        self.assertEqual(response.status_code, 302)
        quotation = CotizacionIndividual.objects.get()
        payload = json.loads(decrypt(quotation.encrypted_payload))
        self.assertEqual(len(payload["groups"]["people"]), 2)
        self.assertEqual(payload["fields"]["requester_name"], "Afiliada Demo")
        self.assertNotIn("100000001", quotation.encrypted_payload)
        self.assertTrue(NotificacionCotizacionIndividual.objects.filter(quotation=quotation).exists())
        get_zoho.assert_not_called()

        detail = self.client.get(reverse(
            "cotizacion_colectivos:individual_quotation_detail",
            args=[sign_receipt(quotation.public_id)],
        ))
        self.assertContains(detail, "Nombre del solicitante")
        self.assertContains(detail, "Personas")
        self.assertNotContains(detail, "requester_name")
        self.assertNotContains(detail, "people")

    @patch("cotizacion_colectivos.external_views._individual_workspace")
    def test_mobility_accepts_multiple_vehicles_and_encrypted_attachment(self, workspace):
        workspace.return_value = self.workspace("movilidad")
        uploaded = SimpleUploadedFile(
            "matricula.png", b"\x89PNG\r\n\x1a\nprivate-demo", content_type="image/png",
        )
        response = self.client.post(
            reverse("colectivos_external:individual_quotation", args=[self.context_token(schema_slug="movilidad")]),
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
        vehicle["plate"] = ""
        response = self.client.post(
            reverse("colectivos_external:individual_quotation", args=[self.context_token(schema_slug="movilidad")]),
            {"items_payload": json.dumps({"vehicles": [vehicle]})},
        )
        self.assertEqual(response.status_code, 302)

    @patch("cotizacion_colectivos.external_views._individual_workspace")
    def test_soat_keeps_affiliate_and_insured_separate(self, workspace):
        workspace.return_value = self.workspace("soat")
        data = {
            "affiliate_name": "Afiliado A", "affiliate_id_type": "CC", "affiliate_document": "400000001",
            "insured_name": "Asegurado B", "insured_id_type": "CC", "insured_document": "500000001",
            "items_payload": json.dumps({"vehicles": [self.vehicle()]}),
        }
        response = self.client.post(
            reverse("colectivos_external:individual_quotation", args=[self.context_token(schema_slug="soat")]), data,
        )
        self.assertEqual(response.status_code, 302)
        payload = json.loads(decrypt(CotizacionIndividual.objects.get().encrypted_payload))
        self.assertNotEqual(payload["fields"]["affiliate_document"], payload["fields"]["insured_document"])

    @patch("cotizacion_colectivos.external_views._individual_workspace")
    def test_invalid_repeatable_and_tampered_token_fail_closed(self, workspace):
        workspace.return_value = self.workspace("salud")
        token = self.context_token()
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
            args=[self.context_token(schema_slug="movilidad")],
        )
        response = self.client.get(url)
        self.assertContains(response, "Contexto: Afiliada Demo")
        self.assertContains(response, "data-add-item")
        self.assertNotContains(response, "Seleccionar ramo")
        csrf_client = Client(enforce_csrf_checks=True)
        self.assertEqual(csrf_client.post(url, {"items_payload": "{}"}).status_code, 403)
        css = Path("static/css/colectivos.css").read_text(encoding="utf-8")
        self.assertIn("@media (max-width:620px)", css)
