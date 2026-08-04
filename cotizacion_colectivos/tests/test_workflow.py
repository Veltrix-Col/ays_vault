from __future__ import annotations

from datetime import timedelta
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from cotizacion_colectivos.branches import (
    COLLECTIVE_BRANCH_CONFIG,
    BranchConfigurationError,
    classify_branch,
    validate_branch_config,
)
from cotizacion_colectivos.dto import GroupMember, PolicyDetail
from cotizacion_colectivos.excel import build_current_policy_workbook
from cotizacion_colectivos.models import EventoSolicitudColectivo, NotificacionColectivos, SolicitudColectivo
from cotizacion_colectivos.services.common import ColectivosServiceError, sign_record_id, unsign_record_id
from cotizacion_colectivos.services.requests import create_request_from_policy, request_snapshot, transition_request


POLICY_ID = "4234567890123456789"
TOKEN = sign_record_id(POLICY_ID, "policy")


def policy_detail(**overrides):
    values = {
        "detail_token": TOKEN,
        "masked_reference": "Referencia terminada en 3456",
        "branch_code": "91",
        "branch_name": "Salud colectivo",
        "classification": "confirmed",
        "insurer": "Aseguradora",
        "state": "Vigente",
        "holder": "Empresa autorizada",
        "start_date": "2026-01-01",
        "end_date": "2026-12-31",
        "renewable": "Sí",
        "payment_mode": "Fraccionado",
        "frequency": "Mensual",
        "installments": "12",
        "first_installment_date": "2026-01-01",
        "payment_calendar": (),
        "insured": (),
        "risks": (),
        "active_count": 1,
        "excluded_count": 0,
    }
    values.update(overrides)
    return PolicyDetail(**values)


MEMBER = GroupMember(
    role="Asegurado", display_name="Persona interna", id_type="CC",
    masked_document="••••890", state="Activo", entry_date="2026-01-01",
    exit_date="", plan="Plan A", relationship="Titular", risk_summary="",
    economic_values=(("Pago total", "100"),),
)


class FakePolicyService:
    profile = "sandbox"

    def detail(self, token):
        assert token == TOKEN
        return policy_detail()

    def group(self, token):
        return self.detail(token), (MEMBER,)

    def _relations(self, policy_id):
        assert policy_id == POLICY_ID
        return ((
            {"Asegurado": {"id": "5234567890123456789"}, "Estado": "Activo", "Pago_total": "=2+2"},
        ), False)

    def _batch(self, module, fields, ids):
        if module == "Contacts":
            return {"5234567890123456789": {"id": "5234567890123456789", "Tipo_ID": "CC", "N_mero_de_ID": "001234", "Full_Name": "=FORMULA"}}
        return {}


class BranchAndTokenTests(SimpleTestCase):
    def test_five_closed_branches_and_shared_exequial_rule(self):
        self.assertEqual(set(COLLECTIVE_BRANCH_CONFIG), {"91", "86", "28", "83", "40"})
        self.assertEqual(classify_branch("Exequial colectivo").code, "86")
        self.assertIsNone(classify_branch("Exequial individual"))

    def test_duplicate_branch_value_is_rejected(self):
        duplicate = dict(COLLECTIVE_BRANCH_CONFIG)
        duplicate["91"] = duplicate["86"]
        with self.assertRaises(BranchConfigurationError):
            validate_branch_config(duplicate)

    def test_typed_tokens_prevent_cross_entity_idor(self):
        company = sign_record_id("1234567890123456789", "company")
        self.assertEqual(unsign_record_id(company, "company"), "1234567890123456789")
        with self.assertRaises(ColectivosServiceError):
            unsign_record_id(company, "person")

    def test_excel_is_formula_safe_textual_and_has_three_sheets(self):
        content = build_current_policy_workbook(TOKEN, FakePolicyService())
        workbook = load_workbook(BytesIO(content), data_only=False)
        self.assertEqual(workbook.sheetnames, ["Información actual", "Información de póliza", "Metadatos"])
        self.assertEqual(workbook["Metadatos"].sheet_state, "hidden")
        self.assertEqual(workbook["Información actual"]["E2"].number_format, "@")
        self.assertTrue(workbook["Información actual"]["F2"].value.startswith("'="))
        self.assertTrue(workbook["Información actual"]["Q2"].value.startswith("'="))


@override_settings(ZOHO_ACTIVE_PROFILE="sandbox")
class RequestWorkflowTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.creator = User.objects.create_superuser("workflow-admin", "workflow@example.test", "Password123!")
        self.owner = User.objects.create_user("workflow-owner", password="Password123!")

    def create_request(self):
        return create_request_from_policy(
            token=TOKEN, source_kind="company", actor=self.creator, assigned_to=self.owner,
            request_type=SolicitudColectivo.RequestType.UPDATE,
            deadline=timezone.localdate() + timedelta(days=5), internal_notes="Nota interna",
            service=FakePolicyService(),
        )

    def test_request_snapshot_rows_event_notification_and_no_zoho_write(self):
        item = self.create_request()
        self.assertTrue(item.public_id.startswith("COL-"))
        self.assertNotIn("Nota interna", item.encrypted_internal_notes)
        self.assertEqual(item.records.count(), 1)
        self.assertEqual(EventoSolicitudColectivo.objects.filter(request=item).count(), 1)
        self.assertEqual(NotificacionColectivos.objects.filter(request=item, user=self.owner).count(), 1)
        snapshot = request_snapshot(item)
        self.assertEqual(snapshot["policy"]["branch_code"], "91")
        self.assertEqual(snapshot["group"][0]["role"], "Asegurado")

    def test_duplicate_active_request_is_rejected(self):
        self.create_request()
        with self.assertRaisesMessage(ColectivosServiceError, "Ya existe"):
            self.create_request()

    def test_state_machine_rejects_jump_and_records_valid_transition(self):
        item = self.create_request()
        with self.assertRaises(ValidationError):
            item.transition_to(SolicitudColectivo.Status.APPROVED)
        item = transition_request(request=item, target=SolicitudColectivo.Status.READY, actor=self.creator)
        self.assertEqual(item.status, SolicitudColectivo.Status.READY)
        self.assertEqual(item.events.filter(event_type="STATUS_CHANGED").count(), 1)

    def test_internal_views_require_permission_and_csrf(self):
        item = self.create_request()
        self.assertEqual(self.client.get(reverse("cotizacion_colectivos:request_list")).status_code, 403)
        self.client.force_login(self.creator)
        self.assertEqual(self.client.get(reverse("cotizacion_colectivos:request_list")).status_code, 200)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.creator)
        response = csrf_client.post(reverse("cotizacion_colectivos:request_transition", args=[item.public_id]), {"target": SolicitudColectivo.Status.READY})
        self.assertEqual(response.status_code, 403)
