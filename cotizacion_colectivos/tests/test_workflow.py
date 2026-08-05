from __future__ import annotations

from datetime import timedelta
from io import BytesIO
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import OperationalError
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
from cotizacion_colectivos.services.requests import create_or_reuse_request_from_policy, create_request_from_policy, request_snapshot, transition_request


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
    role="Asegurado", display_name="Persona interna", id_type="CC", document="=2+2",
    masked_document="••••890", state="Activo", entry_date="2026-01-01",
    exit_date="", plan="Plan A", relationship="Titular", risk_summary="",
    economic_values=(("Pago total", "=2+2"),),
)


class FakePolicyService:
    profile = "sandbox"

    def detail(self, token):
        assert token == TOKEN
        return policy_detail()

    def group(self, token, *, source_kind=None):
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

    def test_branch_classification_accepts_confirmed_zoho_alias_and_typography(self):
        self.assertEqual(classify_branch("VG deudores").code, "83")
        self.assertEqual(classify_branch("  SALUD   COLECTIVO  ").code, "91")
        self.assertIsNone(classify_branch("Hogar"))

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
        self.assertTrue(workbook["Información actual"]["E2"].value.startswith("'="))
        self.assertTrue(workbook["Información actual"]["Q2"].value.startswith("'="))
        self.assertIn("Rol relacionado", tuple(cell.value for cell in workbook["Información actual"][1]))


@override_settings(ZOHO_ACTIVE_PROFILE="sandbox", COLECTIVOS_INTERNAL_PUBLIC_ACCESS=False)
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

    def create_local_request(self, sequence):
        return SolicitudColectivo.objects.create(
            source_kind="company",
            source_reference_hash=f"{sequence:064x}",
            policy_reference_hash=f"{sequence + 1000:064x}",
            encrypted_policy_token="protected-reference",
            masked_policy_reference=f"Referencia {sequence}",
            client_label=f"Cliente {sequence}",
            branch_code="91",
            branch_name="Salud colectivo",
            request_type=SolicitudColectivo.RequestType.UPDATE,
            assigned_to=self.owner,
            deadline=timezone.localdate() + timedelta(days=5),
            zoho_profile="sandbox",
            encrypted_snapshot="protected-snapshot",
            created_by=self.creator,
        )

    def test_request_snapshot_rows_event_without_administrative_notification_or_zoho_write(self):
        item = self.create_request()
        self.assertTrue(item.public_id.startswith("COL-"))
        self.assertNotIn("Nota interna", item.encrypted_internal_notes)
        self.assertEqual(item.records.count(), 1)
        self.assertEqual(EventoSolicitudColectivo.objects.filter(request=item).count(), 1)
        self.assertFalse(
            NotificacionColectivos.objects.filter(request=item, user=self.owner).exists()
        )
        snapshot = request_snapshot(item)
        self.assertEqual(snapshot["policy"]["branch_code"], "91")
        self.assertEqual(snapshot["group"][0]["role"], "Asegurado")

    def test_duplicate_active_request_is_rejected(self):
        self.create_request()
        with self.assertRaisesMessage(ColectivosServiceError, "Ya existe"):
            self.create_request()

    def test_direct_flow_reuses_the_active_request(self):
        existing = self.create_request()
        item, created = create_or_reuse_request_from_policy(
            token=TOKEN,
            source_kind="company",
            actor=self.creator,
            assigned_to=self.owner,
            request_type=SolicitudColectivo.RequestType.UPDATE,
            deadline=timezone.localdate() + timedelta(days=15),
            service=FakePolicyService(),
        )
        self.assertFalse(created)
        self.assertEqual(item.pk, existing.pk)

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
        list_response = self.client.get(reverse("cotizacion_colectivos:request_list"))
        self.assertEqual(list_response.status_code, 200)
        with self.settings(COLECTIVOS_INTERNAL_PUBLIC_ACCESS=True):
            self.assertEqual(
                Client().get(reverse("cotizacion_colectivos:request_list")).status_code,
                200,
            )
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.creator)
        response = csrf_client.post(reverse("cotizacion_colectivos:request_transition", args=[item.public_id]), {"target": SolicitudColectivo.Status.READY})
        self.assertEqual(response.status_code, 403)

    def test_request_list_has_deterministic_visible_order_and_stable_pages(self):
        items = [self.create_local_request(index) for index in range(1, 28)]
        anchor = timezone.now() - timedelta(days=1)
        for index, item in enumerate(items):
            stamp = anchor - timedelta(minutes=index)
            SolicitudColectivo.objects.filter(pk=item.pk).update(
                created_at=stamp,
                updated_at=stamp,
            )

        tied_updated = anchor - timedelta(minutes=1)
        SolicitudColectivo.objects.filter(pk=items[1].pk).update(
            created_at=anchor - timedelta(minutes=1),
            updated_at=tied_updated,
        )
        for item in (items[2], items[3]):
            SolicitudColectivo.objects.filter(pk=item.pk).update(
                created_at=anchor - timedelta(minutes=2),
                updated_at=tied_updated,
            )

        expected = [items[0], items[1], items[3], items[2], *items[4:]]
        self.client.force_login(self.creator)
        first_page = self.client.get(reverse("cotizacion_colectivos:request_list"))
        second_page = self.client.get(
            reverse("cotizacion_colectivos:request_list"),
            {"page": 2},
        )
        first_ids = [item.pk for item in first_page.context["page"].object_list]
        second_ids = [item.pk for item in second_page.context["page"].object_list]

        self.assertEqual(first_ids, [item.pk for item in expected[:25]])
        self.assertEqual(second_ids, [item.pk for item in expected[25:]])
        self.assertFalse(set(first_ids) & set(second_ids))
        self.assertEqual(first_ids + second_ids, [item.pk for item in expected])

    def test_notification_list_orders_by_created_at_and_pk_desc(self):
        item = self.create_local_request(99)
        notifications = [
            NotificacionColectivos.objects.create(
                user=self.creator,
                request=item,
                notification_type="CLIENT_RESPONSE",
                title=f"NotificaciÃ³n {index}",
                message="Mensaje seguro",
                deduplication_key=f"order:{index}",
            )
            for index in range(3)
        ]
        hidden = NotificacionColectivos.objects.create(
            user=self.creator,
            request=item,
            notification_type="ASSIGNED",
            title="Solicitud asignada",
            message="Mensaje histórico",
            deduplication_key="order:hidden-administrative",
        )
        anchor = timezone.now() - timedelta(hours=1)
        NotificacionColectivos.objects.filter(pk=notifications[0].pk).update(
            created_at=anchor,
        )
        NotificacionColectivos.objects.filter(
            pk__in=(notifications[1].pk, notifications[2].pk)
        ).update(created_at=anchor - timedelta(minutes=1))

        self.client.force_login(self.creator)
        response = self.client.get(reverse("cotizacion_colectivos:notification_list"))
        visible_ids = [item.pk for item in response.context["page"].object_list]
        self.assertEqual(
            visible_ids,
            [notifications[0].pk, notifications[2].pk, notifications[1].pk],
        )
        self.assertNotIn(hidden.pk, visible_ids)
        self.assertNotContains(response, "Solicitud asignada")

    def test_administrative_notification_returns_to_informational_list_when_locked(self):
        item = self.create_local_request(100)
        notification = NotificacionColectivos.objects.create(
            user=self.creator,
            request=item,
            notification_type="STATUS",
            title="Solicitud actualizada",
            message="Mensaje seguro",
            deduplication_key="locked:notification",
        )
        self.client.force_login(self.creator)
        locked_queryset = MagicMock()
        locked_queryset.update.side_effect = OperationalError("database is locked")
        with patch.object(NotificacionColectivos.objects, "filter", return_value=locked_queryset):
            with self.assertLogs("cotizacion_colectivos", level="WARNING") as captured:
                response = self.client.post(
                    reverse("cotizacion_colectivos:notification_read", args=[notification.pk])
                )
        self.assertRedirects(
            response,
            reverse("cotizacion_colectivos:notification_list"),
            fetch_redirect_response=False,
        )
        self.assertEqual(locked_queryset.update.call_count, 2)
        self.assertTrue(any("category=sqlite_locked" in line for line in captured.output))
