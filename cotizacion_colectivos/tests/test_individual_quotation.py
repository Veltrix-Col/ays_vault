from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from vault.crypto import decrypt

from cotizacion_colectivos.models import AdjuntoCotizacionIndividual, CotizacionIndividual
from cotizacion_colectivos.quotation_forms.catalog import BRANCH_SCHEMAS
from cotizacion_colectivos.quotation_forms.security import sign_context, unsign_context


class IndividualQuotationTests(TestCase):
    def setUp(self):
        self.private = tempfile.TemporaryDirectory()
        self.addCleanup(self.private.cleanup)
        override = self.settings(COLECTIVOS_PRIVATE_ROOT=Path(self.private.name))
        override.enable()
        self.addCleanup(override.disable)

    def requester(self):
        return {
            "requester_name": "Persona Demo",
            "requester_id_type": "CC",
            "requester_document": "100000001",
            "requester_email": "demo@example.test",
            "requester_phone": "+57 300 000 0000",
            "collective_context": "Colectiva Demo",
        }

    def person(self, suffix="1"):
        return {
            "name": f"Persona {suffix}", "id_type": "CC", "document": f"10000000{suffix}",
            "birth_date": "1990-01-01", "gender": "Femenino",
            "relationship": "Hijo(a)", "role": "Asegurado",
        }

    def vehicle(self, suffix="1"):
        return {
            "plate": f"ABC12{suffix}", "brand": "Marca", "line": "Línea",
            "model": "2025", "city": "Bogotá", "use": "Familiar",
            "insured_name": f"Asegurado {suffix}", "insured_id_type": "CC",
            "insured_document": f"20000000{suffix}",
        }

    def test_tool_is_visible_and_catalog_has_five_parameterized_branches(self):
        response = self.client.get(reverse("public_home"))
        self.assertContains(response, "Cotización Individual")
        response = self.client.get(reverse("cotizacion_colectivos:individual_index"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual({item.slug for item in BRANCH_SCHEMAS}, {"movilidad", "salud", "vida", "exequial", "soat"})
        self.assertTrue(all(isinstance(item.documents, tuple) for item in BRANCH_SCHEMAS))
        for item in BRANCH_SCHEMAS:
            self.assertContains(response, item.name)

    @patch("cotizacion_colectivos.zoho.get_zoho")
    def test_health_multiple_people_submit_locally_encrypted_without_zoho(self, get_zoho):
        data = self.requester() | {"items_payload": json.dumps({"people": [self.person("1"), self.person("2")]})}
        response = self.client.post(reverse("cotizacion_colectivos:individual_form", args=["salud"]), data)
        self.assertEqual(response.status_code, 302)
        quotation = CotizacionIndividual.objects.get()
        payload = json.loads(decrypt(quotation.encrypted_payload))
        self.assertEqual(len(payload["groups"]["people"]), 2)
        self.assertEqual(quotation.item_count, 2)
        self.assertNotIn("100000001", quotation.encrypted_payload)
        get_zoho.assert_not_called()

    def test_mobility_accepts_multiple_vehicles_and_encrypted_attachment(self):
        uploaded = SimpleUploadedFile("matricula.png", b"\x89PNG\r\n\x1a\n" + b"private-demo", content_type="image/png")
        data = self.requester() | {
            "items_payload": json.dumps({"vehicles": [self.vehicle("1"), self.vehicle("2")]}),
            "attachments": uploaded,
        }
        response = self.client.post(reverse("cotizacion_colectivos:individual_form", args=["movilidad"]), data)
        self.assertEqual(response.status_code, 302)
        quotation = CotizacionIndividual.objects.get()
        self.assertEqual(quotation.item_count, 2)
        attachment = AdjuntoCotizacionIndividual.objects.get()
        stored = (Path(self.private.name) / "individual_quotations" / attachment.stored_path).read_bytes()
        self.assertNotIn(b"private-demo", stored)
        self.assertTrue(attachment.safe_metadata["encrypted"])

    def test_soat_keeps_affiliate_and_insured_separate(self):
        data = self.requester() | {
            "affiliate_name": "Afiliado Demo", "affiliate_id_type": "CC", "affiliate_document": "300000001",
            "insured_name": "Asegurado Distinto", "insured_id_type": "CC", "insured_document": "400000001",
            "items_payload": json.dumps({"vehicles": [self.vehicle()]})
        }
        response = self.client.post(reverse("cotizacion_colectivos:individual_form", args=["soat"]), data)
        self.assertEqual(response.status_code, 302)
        payload = json.loads(decrypt(CotizacionIndividual.objects.get().encrypted_payload))
        self.assertNotEqual(payload["fields"]["affiliate_document"], payload["fields"]["insured_document"])

    def test_invalid_or_missing_repeatable_data_is_rejected(self):
        data = self.requester() | {"items_payload": json.dumps({"people": []})}
        response = self.client.post(reverse("cotizacion_colectivos:individual_form", args=["salud"]), data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Agregue entre 1 y 20")
        self.assertFalse(CotizacionIndividual.objects.exists())

    def test_invalid_email_and_attachment_type_do_not_persist_anything(self):
        invalid_email = self.requester() | {
            "requester_email": "not-an-email",
            "items_payload": json.dumps({"people": [self.person()]}),
        }
        response = self.client.post(
            reverse("cotizacion_colectivos:individual_form", args=["salud"]),
            invalid_email,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ingrese una dirección de correo electrónico válida")

        unsafe = SimpleUploadedFile(
            "support.pdf.exe", b"MZ-not-a-document", content_type="application/octet-stream"
        )
        invalid_file = self.requester() | {
            "items_payload": json.dumps({"people": [self.person()]}),
            "attachments": unsafe,
        }
        response = self.client.post(
            reverse("cotizacion_colectivos:individual_form", args=["salud"]),
            invalid_file,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "tipo no permitido")
        self.assertFalse(CotizacionIndividual.objects.exists())
        self.assertFalse(AdjuntoCotizacionIndividual.objects.exists())

    def test_submit_log_contains_only_aggregate_values(self):
        data = self.requester() | {
            "items_payload": json.dumps({"people": [self.person()]})
        }
        with self.assertLogs("cotizacion_colectivos", level="INFO") as captured:
            response = self.client.post(
                reverse("cotizacion_colectivos:individual_form", args=["vida"]), data
            )
        self.assertEqual(response.status_code, 302)
        output = " ".join(captured.output)
        for sensitive in (
            "Persona Demo", "100000001", "demo@example.test", "+57 300 000 0000"
        ):
            self.assertNotIn(sensitive, output)
        self.assertIn("branch=vida", output)
        self.assertIn("items=1", output)

    def test_context_is_opaque_signed_and_altered_token_is_rejected(self):
        token = sign_context(entity_kind="company", entity_token="signed-detail-token", label="Colectiva Demo")
        self.assertNotIn("Colectiva Demo", token)
        self.assertEqual(unsign_context(token)["entity_kind"], "company")
        url = reverse("cotizacion_colectivos:individual_form", args=["vida"])
        response = self.client.get(url, {"context": token})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Colectiva Demo")
        altered = token[:-1] + ("a" if token[-1] != "a" else "b")
        self.assertEqual(self.client.get(url, {"context": altered}).status_code, 404)

    def test_confirmation_is_signed_no_store_and_anti_idor(self):
        data = self.requester() | {"items_payload": json.dumps({"people": [self.person()]})}
        response = self.client.post(reverse("cotizacion_colectivos:individual_form", args=["exequial"]), data)
        confirmation = self.client.get(response["Location"])
        self.assertEqual(confirmation.status_code, 200)
        self.assertContains(confirmation, "Solicitud recibida")
        self.assertIn("no-cache", confirmation["Cache-Control"])
        self.assertNotContains(confirmation, "100000001")
        altered = response["Location"][:-2] + "aa/"
        self.assertEqual(self.client.get(altered).status_code, 404)

    def test_submit_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        data = self.requester() | {"items_payload": json.dumps({"people": [self.person()]})}
        self.assertEqual(client.post(reverse("cotizacion_colectivos:individual_form", args=["salud"]), data).status_code, 403)

    def test_template_exposes_progressive_controls_and_mobile_css(self):
        response = self.client.get(reverse("cotizacion_colectivos:individual_form", args=["movilidad"]))
        self.assertContains(response, "data-add-item")
        self.assertContains(response, "data-item-dialog")
        self.assertContains(response, "colectivos-individual.js")
        css = Path("static/css/colectivos.css").read_text(encoding="utf-8")
        self.assertIn("@media (max-width:620px)", css)
