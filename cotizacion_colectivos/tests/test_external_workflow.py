from __future__ import annotations

import io
import json
import zipfile
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import resolve, reverse
from django.utils import timezone
from openpyxl import Workbook, load_workbook

from vault.crypto import decrypt, encrypt

from cotizacion_colectivos.models import (
    AccesoExternoSolicitudColectivo,
    AdjuntoSolicitudColectivo,
    CambioSolicitudColectivo,
    ColectivosTaskOutbox,
    NovedadIngresoZoho,
    RespuestaSolicitudColectivo,
    RevisionSolicitudColectivo,
    SolicitudColectivo,
    SolicitudColectivoPoliza,
    SolicitudColectivoRegistro,
    NotificacionColectivos,
    RenovacionColectiva,
)
from cotizacion_colectivos.services.attachments import store_attachment
from cotizacion_colectivos.services.excel_roundtrip import (
    build_approved_consolidated,
    build_novelties_template,
    build_response_workbook,
    parse_novelties,
)
from cotizacion_colectivos.services.external import (
    ExternalAccessError,
    generate_access,
    issue_otp,
    generate_no_changes_token,
    ensure_novelty_ingress_items,
    resolve_external_session,
    resolve_token,
    save_response,
    submit_response,
    update_access_recipient,
    verify_otp,
)
from cotizacion_colectivos.tests.fakes import mark_novelties_actions
from cotizacion_colectivos.services.review import finalize_review, record_reviews
from cotizacion_colectivos.views import _publish_novelty_ingress_documents
from cotizacion_colectivos.external_views import _policy_sections


@override_settings(
    COLECTIVOS_EXTERNAL_LINK_TTL_SECONDS=3600,
    COLECTIVOS_EXTERNAL_LINK_MAX_TTL_SECONDS=7200,
    COLECTIVOS_EXTERNAL_OTP_MAX_ATTEMPTS=3,
    COLECTIVOS_EXTERNAL_OTP_TTL_SECONDS=600,
)
class ExternalWorkflowTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            "external-admin", "external-admin@example.test", "Password123!"
        )
        self.request = SolicitudColectivo.objects.create(
            source_kind="company",
            source_reference_hash="a" * 64,
            policy_reference_hash="b" * 64,
            encrypted_policy_token=encrypt("opaque-internal-token"),
            masked_policy_reference="Póliza terminada en 1234",
            client_label="Cliente de prueba",
            branch_code="91",
            branch_name="VG deudores",
            request_type=SolicitudColectivo.RequestType.UPDATE,
            status=SolicitudColectivo.Status.READY,
            assigned_to=self.admin,
            deadline=timezone.localdate() + timedelta(days=10),
            zoho_profile="sandbox",
            encrypted_snapshot=encrypt('{"version": 1, "policy": {}, "group": [], "warnings": []}'),
            created_by=self.admin,
        )
        self.record = SolicitudColectivoRegistro.objects.create(
            request=self.request,
            element_type=SolicitudColectivoRegistro.ElementType.PERSON,
            role="Asegurado",
            external_reference_hash="c" * 64,
            initial_status="Activo",
            plan="Plan vigente",
            original_position=1,
            checksum="d" * 64,
        )

    def access(self):
        return generate_access(
            request=self.request,
            actor=self.admin,
            recipient="cliente@example.test",
        )

    @patch("cotizacion_colectivos.views.publish_attachment", return_value={"status": "UPLOADED", "attachment_id": "att-1"})
    @patch("cotizacion_colectivos.views.novelty_ingress_attachments")
    def test_novelty_person_document_is_published_to_contact(self, documents, publisher):
        document = SimpleNamespace(safe_metadata={"functional_key": "ing-1"}, save=Mock())
        documents.return_value = (document,)
        item = SimpleNamespace(request=object(), item_key="ing-1")
        result = _publish_novelty_ingress_documents(
            item,
            {"rol": "Asegurado", "id_type": "CC", "document": "123"},
            "4991513000000000001",
        )
        self.assertEqual(result[0]["status"], "UPLOADED")
        publisher.assert_called_once_with(
            attachment=document, module="Contacts", record_id="4991513000000000001",
        )
        self.assertEqual(document.safe_metadata["document_type"], "identity_document")
        self.assertEqual(document.safe_metadata["owner_type"], "contact")

    @patch("cotizacion_colectivos.views.publish_attachment", return_value={"status": "FAILED"})
    @patch("cotizacion_colectivos.views.novelty_ingress_attachments")
    def test_novelty_person_document_failed_result_is_not_treated_as_success(self, documents, publisher):
        document = SimpleNamespace(safe_metadata={}, save=Mock())
        documents.return_value = (document,)
        item = SimpleNamespace(request=object(), item_key="ing-1")
        with self.assertRaisesMessage(Exception, "no pudo publicarse"):
            _publish_novelty_ingress_documents(item, {"id_type": "CC", "document": "123"}, "4991513000000000001")

    def test_ingress_materialization_recovers_policy_id_from_stored_reference(self):
        policy_id = "4991513000270954040"
        stored_reference = json.dumps(
            {"id": policy_id, "source_id": "4991513000270000001", "source_kind": "company"},
            sort_keys=True,
        )
        policy = SolicitudColectivoPoliza.objects.create(
            request=self.request,
            policy_reference_hash="p" * 64,
            encrypted_policy_token=encrypt(stored_reference),
            masked_policy_reference="Póliza terminada en 4040",
            branch_code="83",
            branch_name="VG deudores",
            encrypted_snapshot=encrypt("{}"),
            snapshot_checksum="q" * 64,
            position=1,
        )
        response = RespuestaSolicitudColectivo.objects.create(
            request=self.request,
            version=1,
            status=RespuestaSolicitudColectivo.Status.SUBMITTED,
            origin=RespuestaSolicitudColectivo.Origin.WEB,
            checksum="r" * 64,
        )
        CambioSolicitudColectivo.objects.create(
            response=response,
            policy=policy,
            action=CambioSolicitudColectivo.Action.INCLUDE,
            functional_field="accion",
            encrypted_new_value=encrypt("INCLUIR"),
            position=1,
            encrypted_branch_payload=encrypt(json.dumps({"functional_key": "ing-1"})),
            checksum="s" * 64,
        )
        for field, value in (("nombres", "Ana"), ("apellidos", "Pérez"), ("tipo_id", "CC"),
                             ("documento", "123456"), ("correo", "ana@example.test"),
                             ("phone", "3000000000")):
            CambioSolicitudColectivo.objects.create(
                response=response, policy=policy,
                action=CambioSolicitudColectivo.Action.INCLUDE,
                functional_field=field, encrypted_new_value=encrypt(value),
                position=1, checksum="t" * 64,
            )

        items = ensure_novelty_ingress_items(self.request)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].policy_remote_id, policy_id)
        self.assertEqual(NovedadIngresoZoho.objects.get().policy_remote_id, policy_id)
        self.assertEqual(json.loads(decrypt(items[0].encrypted_payload))["ramo"], "VG deudores")
        materialized_payload = json.loads(decrypt(items[0].encrypted_payload))
        self.assertEqual(materialized_payload["email"], "ana@example.test")
        self.assertEqual(materialized_payload["phone"], "3000000000")
        # The stored reference is resolved locally; this path does not need a
        # textual Polizas search or any Zoho write.
        self.assertEqual(items[0].item_key, "ing-1")
        items[0].policy_remote_id = ""
        items[0].save(update_fields=("policy_remote_id",))
        refreshed = ensure_novelty_ingress_items(self.request)[0]
        self.assertEqual(refreshed.policy_remote_id, policy_id)

    def _excel_response_with_original(self, name="Novedades Septiembre A&S.xlsx"):
        access = self.verified_access()
        response = save_response(
            access=access, rows=[], observations="",
            origin=RespuestaSolicitudColectivo.Origin.EXCEL,
        )
        workbook = io.BytesIO()
        from openpyxl import Workbook
        book = Workbook()
        book.active["A1"] = "Novedades"
        book.save(workbook)
        workbook.seek(0)
        uploaded = SimpleUploadedFile(
            name, workbook.read(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        raw = uploaded.read()
        uploaded.seek(0)
        attachment = store_attachment(response=response, uploaded=uploaded, allow_excel=True)
        return response, attachment, raw

    def test_original_excel_download_preserves_name_mime_and_bytes(self):
        _response_obj, attachment, raw = self._excel_response_with_original()
        downloaded = self.client.get(reverse(
            "cotizacion_colectivos:attachment_download",
            args=[self.request.public_id, attachment.pk],
        ))
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(downloaded["Content-Type"], attachment.detected_mime)
        self.assertIn("Novedades Septiembre A&S.xlsx", downloaded["Content-Disposition"])
        self.assertEqual(b"".join(downloaded.streaming_content), raw)

    def test_original_excel_download_rejects_attachment_from_another_request(self):
        _response_obj, attachment, _raw = self._excel_response_with_original()
        other = SolicitudColectivo.objects.create(
            source_kind="company", source_reference_hash="e" * 64,
            policy_reference_hash="f" * 64,
            encrypted_policy_token=encrypt("other-token"),
            masked_policy_reference="Otra póliza", client_label="Otro cliente",
            branch_code="91", branch_name="Salud colectivo",
            request_type=SolicitudColectivo.RequestType.UPDATE,
            status=SolicitudColectivo.Status.READY, assigned_to=self.admin,
            deadline=timezone.localdate() + timedelta(days=10), zoho_profile="sandbox",
            encrypted_snapshot=encrypt('{"version": 1, "policy": {}, "group": [], "warnings": []}'),
            created_by=self.admin,
        )
        response = self.client.get(reverse(
            "cotizacion_colectivos:attachment_download",
            args=[other.public_id, attachment.pk],
        ))
        self.assertEqual(response.status_code, 404)

    def test_ingress_support_survives_new_draft_version_by_functional_key(self):
        access = self.verified_access()
        row = {
            "record": "", "policy": "", "action": "INCLUIR",
            "tipo_id": "CC", "documento": "123456", "nombres": "Ana",
            "apellidos": "Prueba", "rol": "Asegurado",
            "fecha_ingreso": "2026-09-01", "functional_key": "ingreso-a",
        }
        with patch("cotizacion_colectivos.services.external.identification_type_values", return_value=frozenset({"CC"})):
            first = save_response(access=access, rows=[row], observations="")
        change = first.changes.filter(functional_field="accion").get()
        uploaded = SimpleUploadedFile("soporte.pdf", b"%PDF-1.4\n", content_type="application/pdf")
        attachment = store_attachment(response=first, uploaded=uploaded, change=change, safe_metadata_extra={"functional_key": "ingreso-a"})
        with patch("cotizacion_colectivos.services.external.identification_type_values", return_value=frozenset({"CC"})):
            second = save_response(access=access, rows=[row], observations="")
        new_change = second.changes.filter(functional_field="accion").get()
        attachment.refresh_from_db()
        self.assertNotEqual(change.pk, new_change.pk)
        self.assertEqual(attachment.change_id, new_change.pk)
        self.assertEqual(attachment.response_id, second.pk)
        self.assertEqual(attachment.safe_metadata.get("functional_key"), "ingreso-a")
        self.assertEqual(AdjuntoSolicitudColectivo.objects.filter(checksum=attachment.checksum).count(), 1)

    def enter_with_otp(self, generated):
        with patch("cotizacion_colectivos.services.external.secrets.randbelow", return_value=123456):
            entry = self.client.get(
                reverse("colectivos_external:entry", args=[generated.token])
            )
        self.assertEqual(entry.status_code, 200)
        self.assertContains(entry, "Verifique su acceso")
        verified = self.client.post(
            reverse("colectivos_external:verify", args=[generated.token]),
            {"code": "123456"},
        )
        self.assertEqual(verified.status_code, 302)
        self.assertIn("colectivos_external_session", verified.cookies)
        return verified

    def test_static_portal_route_is_not_interpreted_as_a_token(self):
        match = resolve(reverse("colectivos_external:portal"))
        self.assertEqual(match.url_name, "portal")

    def test_reused_access_persists_edited_recipient_for_the_next_otp(self):
        original = "original@example.test"
        edited = "edited@example.test"
        generated = generate_access(
            request=self.request, actor=self.admin, recipient=original,
        )
        generated.access.otp_hash = "pending-for-original-recipient"
        generated.access.otp_expires_at = timezone.now() + timedelta(minutes=5)
        generated.access.save(update_fields=("otp_hash", "otp_expires_at"))

        self.assertTrue(update_access_recipient(
            access=generated.access, actor=self.admin, recipient=edited,
        ))
        generated.access.refresh_from_db()
        self.assertEqual(decrypt(generated.access.encrypted_recipient), edited)
        self.assertEqual(generated.access.otp_hash, "")

        backend = Mock(name="edited_recipient_backend")
        backend.name = "smtp"
        backend.send.return_value = "accepted"
        with patch(
            "cotizacion_colectivos.services.external.secrets.randbelow", return_value=112233,
        ), patch("vault.notifications.get_backend", return_value=backend):
            self.assertTrue(issue_otp(generated.access))
        self.assertEqual(backend.send.call_args.args[3], edited)
        self.assertNotEqual(backend.send.call_args.args[3], original)

    def test_external_portal_requires_otp_and_uses_isolated_cookie_without_django_login(self):
        generated = self.access()
        self.request.status = self.request.Status.SENT
        self.request.save(update_fields=("status",))
        self.enter_with_otp(generated)
        portal_response = self.client.get(reverse("colectivos_external:portal"))
        self.assertEqual(portal_response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(portal_response["Cache-Control"], "max-age=0, no-cache, no-store, must-revalidate, private")
        self.assertContains(portal_response, "data-row-filter", html=False)
        self.assertContains(portal_response, "data-progress-count", html=False)
        self.assertContains(portal_response, "js/colectivos-external.js", html=False)
        self.assertContains(portal_response, "Mi póliza")
        self.assertContains(portal_response, "Mis pólizas y mi grupo")
        self.assertContains(portal_response, "Póliza")
        self.assertContains(portal_response, "Ramo")
        self.assertNotContains(portal_response, "Fecha límite")
        self.assertNotContains(portal_response, "Renovación")
        self.assertContains(portal_response, "Buscar dentro de mi información")
        self.assertNotContains(portal_response, "Riesgos1")
        self.assertNotContains(portal_response, "lookups")

    @patch(
        "cotizacion_colectivos.external_views._policy_sections",
        return_value=({"grouping_warnings": ("relación inconsistente",)},),
    )
    def test_portal_warning_log_uses_request_correlation_without_model_attribute(self, _sections):
        generated = self.access()
        self.enter_with_otp(generated)
        with self.assertLogs("cotizacion_colectivos", level="WARNING") as captured:
            portal = self.client.get(
                reverse("colectivos_external:portal"),
                HTTP_X_CORRELATION_ID="qa-safe-correlation",
            )
        self.assertEqual(portal.status_code, 200)
        self.assertIn("correlation=qa-safe-correlation", " ".join(captured.output))
        self.assertNotIn("Cliente de prueba", " ".join(captured.output))

    def test_client_can_confirm_without_creating_a_draft_first(self):
        generated = self.access()
        self.enter_with_otp(generated)
        response = self.client.post(
            reverse("colectivos_external:submit"),
            {"declaration": "on", "client_observations": ""},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Registre al menos una novedad o confirme que no tiene novedades para este periodo.", response.content.decode())
        self.assertFalse(self.request.responses.exists())
        self.assertFalse(ColectivosTaskOutbox.objects.exists())
        self.request.refresh_from_db()
        self.assertNotEqual(self.request.status, self.request.Status.ANSWERED)
        self.assertFalse(NotificacionColectivos.objects.filter(notification_type="CLIENT_RESPONSE").exists())

    @patch("cotizacion_colectivos.services.external.enqueue_task")
    def test_novelties_submit_builds_task_contract_from_persisted_policy_seller(self, enqueue):
        self.request.encrypted_snapshot = encrypt(json.dumps({
            "version": 1,
            "policy": {"seller": "Fonconstruimos"},
            "group": [],
            "warnings": [],
        }))
        self.request.save(update_fields=("encrypted_snapshot",))
        access = self.verified_access()
        response = save_response(
            access=access,
            rows=[{"record": str(self.record.public_key), "action": "RETIRAR", "fecha_retiro": "2026-09-01"}],
            observations="",
        )
        submit_response(access=access, response=response, declaration=True)
        payload = enqueue.call_args.kwargs["payload"]
        self.assertEqual(payload.area, "Negocios Bienestar y Beneficios")
        self.assertEqual(payload.analyst_request, "Si")
        self.assertEqual(payload.seller, "Fonconstruimos")
        self.assertEqual(payload.responsible, "")
        self.assertEqual(payload.responsible_email, "")

    def test_explicit_no_changes_submits_empty_response_and_closes_access(self):
        access = self.verified_access()
        response = save_response(access=access, rows=[], observations="")
        submitted = submit_response(access=access, response=response, declaration=True, no_changes=True)
        self.assertEqual(submitted.status, RespuestaSolicitudColectivo.Status.SUBMITTED)
        self.assertEqual(submitted.safe_metadata.get("response_type"), "NO_CHANGES")
        self.request.refresh_from_db()
        self.assertEqual(self.request.status, self.request.Status.ANSWERED)
        access.refresh_from_db()
        self.assertEqual(access.status, access.Status.USED)
        self.assertFalse(ColectivosTaskOutbox.objects.exists())
        notification = NotificacionColectivos.objects.get(notification_type="CLIENT_RESPONSE")
        self.assertIn("sin novedades", notification.message.lower())

    def test_renewal_no_changes_signed_link_get_is_read_only_and_post_needs_no_otp(self):
        generated = self.access()
        self.request.status = self.request.Status.SENT
        self.request.save(update_fields=("status",))
        cycle = RenovacionColectiva.objects.create(
            cycle_key="renewal-email-test:2026-09",
            policy_remote_id="4991513000270954040", policy_token=encrypt("policy-token"),
            masked_policy="1234", client_label="Cliente de prueba", branch_name="VG deudores",
            monthly_period="2026-09", policy_status="Vigente", payment_frequency="Mensual",
            encrypted_recipient=encrypt("cliente@example.test"), recipient_hash="e" * 64,
            scheduled_for=timezone.localdate(), status=RenovacionColectiva.Status.SENT,
            request=self.request, access=generated.access,
            link_expires_at=timezone.now() + timedelta(days=3),
        )
        token = generate_no_changes_token(cycle=cycle)
        entry = self.client.get(reverse("colectivos_external:no_changes_entry", args=[token]))
        self.assertEqual(entry.status_code, 200)
        cycle.refresh_from_db()
        self.assertEqual(cycle.status, RenovacionColectiva.Status.SENT)
        submitted = self.client.post(reverse("colectivos_external:no_changes_confirm", args=[token]))
        self.assertEqual(submitted.status_code, 200, submitted.content[:500])
        cycle.refresh_from_db()
        self.request.refresh_from_db()
        self.assertEqual(cycle.status, RenovacionColectiva.Status.RESPONDED)
        self.assertEqual(self.request.status, self.request.Status.ANSWERED)
        self.assertEqual(self.request.responses.get(status=RespuestaSolicitudColectivo.Status.SUBMITTED).safe_metadata["response_type"], "NO_CHANGES")

    def test_no_changes_marks_linked_renewal_responded_and_stops_reminder(self):
        access = self.verified_access()
        cycle = RenovacionColectiva.objects.create(
            cycle_key="renewal-test:2026-09",
            policy_remote_id="4991513000270954040",
            policy_token=encrypt("policy-token"),
            masked_policy="1234",
            client_label="Cliente de prueba",
            branch_name="VG deudores",
            monthly_period="2026-09",
            policy_status="Vigente",
            payment_frequency="Mensual",
            encrypted_recipient=encrypt("cliente@example.test"),
            recipient_hash="e" * 64,
            scheduled_for=timezone.localdate(),
            status=RenovacionColectiva.Status.SENT,
            access=access,
            reminder_due_at=timezone.now() - timedelta(days=1),
        )
        response = save_response(access=access, rows=[], observations="")
        submit_response(access=access, response=response, declaration=True, no_changes=True)
        cycle.refresh_from_db()
        self.assertEqual(cycle.status, RenovacionColectiva.Status.RESPONDED)
        self.assertIsNotNone(cycle.responded_at)
        self.assertIsNone(cycle.reminder_sent_at)

    def test_empty_response_without_explicit_no_changes_is_rejected(self):
        access = self.verified_access()
        response = save_response(access=access, rows=[], observations="")
        with self.assertRaisesMessage(ExternalAccessError, "Registre al menos una novedad o confirme que no tiene novedades para este periodo."):
            submit_response(access=access, response=response, declaration=True)

    def test_changes_cannot_be_submitted_as_no_changes(self):
        access = self.verified_access()
        response = save_response(
            access=access,
            rows=[{"record": str(self.record.public_key), "action": "RETIRAR", "fecha_retiro": "2026-09-01"}],
            observations="",
        )
        with self.assertRaisesMessage(ExternalAccessError, "Ha registrado novedades."):
            submit_response(access=access, response=response, declaration=True, no_changes=True)

    @patch("cotizacion_colectivos.services.external._send_submission_receipt")
    def test_valid_novelty_submit_never_sends_automatic_response_receipt(self, receipt):
        access = self.verified_access()
        response = save_response(
            access=access,
            rows=[{"record": str(self.record.public_key), "action": "RETIRAR", "fecha_retiro": "2026-09-01"}],
            observations="",
        )
        submit_response(access=access, response=response, declaration=True)
        receipt.assert_not_called()

    def test_otp_endpoint_rejects_an_unissued_code(self):
        generated = self.access()
        response = self.client.post(
            reverse("colectivos_external:verify", args=[generated.token]),
            {"code": "123456"},
        )
        self.assertEqual(response.status_code, 400)
        generated.access.refresh_from_db()
        self.assertFalse(generated.access.otp_hash)

    def test_all_confirmed_branches_have_editable_portal(self):
        expected_field = {code: "include_fecha_nacimiento" for code in ("91", "86", "28", "83", "40")}
        for index, branch_code in enumerate(expected_field):
            with self.subTest(branch=branch_code):
                self.request.branch_code = branch_code
                self.request.status = self.request.Status.READY
                self.request.save(update_fields=("branch_code", "status"))
                generated = generate_access(
                    request=self.request,
                    actor=self.admin,
                    recipient="cliente@example.test",
                    regenerate=index > 0,
                )
                self.enter_with_otp(generated)
                portal = self.client.get(reverse("colectivos_external:portal"))
                self.assertContains(portal, "Mis pólizas y mi grupo")
                self.assertContains(portal, expected_field[branch_code])
                saved = self.client.post(reverse("colectivos_external:save_draft"), {})
                self.assertEqual(saved.status_code, 302)

    def test_portal_uses_metadata_backed_identification_and_minimal_ingress(self):
        generated = self.access()
        self.request.status = self.request.Status.SENT
        self.request.save(update_fields=("status",))
        self.enter_with_otp(generated)
        portal = self.client.get(reverse("colectivos_external:portal"))
        self.assertContains(portal, 'name="include_rol" value="Asegurado"', html=False)
        self.assertContains(portal, '<select name="include_tipo_id">', html=False)
        self.assertContains(portal, 'name="include_fecha_nacimiento"', html=False)
        self.assertContains(portal, 'name="include_email"', html=False)
        self.assertContains(portal, 'name="include_phone"', html=False)
        self.assertContains(portal, 'name="include_fecha_ingreso"', html=False)
        self.assertNotContains(portal, 'name="include_plan"', html=False)
        self.assertNotContains(portal, "Quiero solicitar este ingreso")
        self.assertNotContains(portal, "Enviar la respuesta no modifica automáticamente Zoho ni la póliza.")
        self.assertContains(portal, 'type="submit" formaction="/solicitudes/colectivos/externa/portal/guardar/" formnovalidate data-drawer-done>Preparar ingreso', html=False)
        self.assertEqual(portal.content.decode().count("Prefiero trabajar con Excel"), 1)

    @patch("cotizacion_colectivos.external_views.subrisk_relationship_choice_pairs", return_value=(("Hijo", "Hijo"), ("Afiliado", "Afiliado")))
    def test_vg_ingress_renders_dynamic_parentesco_select(self, _relationships):
        generated = self.access()
        self.request.status = self.request.Status.SENT
        self.request.save(update_fields=("status",))
        self.enter_with_otp(generated)
        portal = self.client.get(reverse("colectivos_external:portal"))
        self.assertContains(portal, 'name="include_parentesco"', html=False)
        self.assertContains(portal, '<option value="Hijo">Hijo</option>', html=False)
        self.assertContains(portal, '<option value="Afiliado">Afiliado</option>', html=False)
        self.assertNotContains(portal, '<input name="include_parentesco"', html=False)

    @patch("cotizacion_colectivos.services.external.identification_type_values", return_value=frozenset({"CC"}))
    @patch("cotizacion_colectivos.external_views.subrisk_relationship_choice_pairs", return_value=(("Hijo", "Hijo"),))
    def test_invalid_parentesco_stays_in_portal_without_creating_draft(self, _relationships, _types):
        generated = self.access()
        self.request.status = self.request.Status.SENT
        self.request.save(update_fields=("status",))
        self.enter_with_otp(generated)
        response = self.client.post(reverse("colectivos_external:save_draft"), {
            "include_action": "INCLUIR", "include_tipo_id": "CC", "include_documento": "1019",
            "include_nombres": "Ana", "include_apellidos": "Uno", "include_rol": "Asegurado",
            "include_parentesco": "No valido", "include_fecha_ingreso": "2026-10-01",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("colectivos_external:portal"))
        self.assertFalse(self.request.responses.filter(status=RespuestaSolicitudColectivo.Status.DRAFT).exists())
        portal = self.client.get(reverse("colectivos_external:portal"))
        self.assertContains(portal, "El parentesco seleccionado no es válido.")

    @patch("cotizacion_colectivos.services.external.identification_type_values", return_value=frozenset({"CC"}))
    @patch(
        "cotizacion_colectivos.external_views.subrisk_relationship_choice_pairs",
        return_value=(("Hijo", "Hijo/a"), ("Afiliado", "Afiliado")),
    )
    def test_parentesco_catalog_value_is_accepted_and_persisted(self, _relationships, _types):
        """The select label may differ, but the submitted API value is canonical."""
        generated = self.access()
        self.request.status = self.request.Status.SENT
        self.request.save(update_fields=("status",))
        self.enter_with_otp(generated)
        response = self.client.post(reverse("colectivos_external:save_draft"), {
            "include_action": "INCLUIR", "include_tipo_id": "CC", "include_documento": "101901",
            "include_nombres": "Ana", "include_apellidos": "Prueba", "include_rol": "Asegurado",
            "include_parentesco": "Hijo", "include_fecha_ingreso": "2026-10-01",
            "include_action__2": "INCLUIR", "include_tipo_id__2": "CC", "include_documento__2": "101902",
            "include_nombres__2": "Bea", "include_apellidos__2": "Prueba", "include_rol__2": "Asegurado",
            "include_parentesco__2": "Afiliado", "include_fecha_ingreso__2": "2026-10-01",
        })
        self.assertEqual(response.status_code, 302)
        draft = self.request.responses.get(status=RespuestaSolicitudColectivo.Status.DRAFT)
        values = [
            decrypt(change.encrypted_new_value)
            for change in draft.changes.filter(functional_field="parentesco").order_by("position")
        ]
        self.assertEqual(values, ["Hijo", "Afiliado"])

    @patch("cotizacion_colectivos.external_views.identification_choice_pairs", return_value=(("CC", "CC - Cédula de ciudadanía"),))
    @patch("cotizacion_colectivos.external_views.subrisk_relationship_choice_pairs", return_value=(("Afiliado", "Afiliado"),))
    def test_portal_hides_empty_policy_records_warning(self, _relationships, _identification):
        self.request.status = self.request.Status.SENT
        self.request.warnings = ["La póliza no tiene registros relacionados precargados."]
        self.request.save(update_fields=("status", "warnings"))
        generated = self.access()
        self.enter_with_otp(generated)
        portal = self.client.get(reverse("colectivos_external:portal"))
        self.assertEqual(portal.status_code, 200)
        self.assertNotContains(portal, "La póliza no tiene registros relacionados precargados.")
        self.assertContains(portal, "Registrar ingreso")

    @patch("cotizacion_colectivos.external_views.identification_choice_pairs", return_value=(("CC", "CC - Cédula de ciudadanía"),))
    def test_web_ingress_prepare_button_bypasses_hidden_drawer_native_validation(self, _choices):
        policy = SolicitudColectivoPoliza.objects.create(
            request=self.request,
            policy_reference_hash="p" * 64,
            encrypted_policy_token=encrypt(json.dumps({"id": "policy-2"})),
            masked_policy_reference="Póliza terminada en 2222",
            branch_code="91",
            branch_name="VG deudores",
            encrypted_snapshot=encrypt("{}"),
            snapshot_checksum="q" * 64,
            position=2,
            active=True,
            enabled_adjustments=["INCLUSION"],
        )
        SolicitudColectivoPoliza.objects.create(
            request=self.request,
            policy_reference_hash="r" * 64,
            encrypted_policy_token=encrypt(json.dumps({"id": "policy-3"})),
            masked_policy_reference="Póliza terminada en 3333",
            branch_code="91",
            branch_name="VG deudores",
            encrypted_snapshot=encrypt("{}"),
            snapshot_checksum="s" * 64,
            position=3,
            active=True,
            enabled_adjustments=["INCLUSION"],
        )
        self.request.status = self.request.Status.SENT
        self.request.save(update_fields=("status",))
        generated = self.access()
        self.enter_with_otp(generated)
        portal = self.client.get(reverse("colectivos_external:portal"))
        self.assertEqual(portal.status_code, 200)
        self.assertGreaterEqual(portal.content.decode().count('data-drawer-done'), 2)
        self.assertGreaterEqual(portal.content.decode().count('formnovalidate data-drawer-done'), 2)

    @patch("cotizacion_colectivos.services.external.identification_type_values", return_value=frozenset({"CC"}))
    def test_two_web_ingresses_share_one_draft_and_render_as_separate_cards(self, _types):
        generated = self.access()
        self.request.status = self.request.Status.SENT
        self.request.save(update_fields=("status",))
        self.enter_with_otp(generated)
        payload = {
            "include_action": "INCLUIR", "include_tipo_id": "CC", "include_documento": "1019",
            "include_nombres": "Ana", "include_apellidos": "Uno", "include_rol": "Asegurado",
            "include_fecha_nacimiento": "1990-01-01", "include_fecha_ingreso": "2026-10-01",
            "include_action__2": "INCLUIR", "include_tipo_id__2": "CC", "include_documento__2": "2020",
            "include_nombres__2": "Bea", "include_apellidos__2": "Dos", "include_rol__2": "Asegurado",
            "include_fecha_nacimiento__2": "1991-01-01", "include_fecha_ingreso__2": "2026-10-02",
        }
        saved = self.client.post(reverse("colectivos_external:save_draft"), payload)
        self.assertEqual(saved.status_code, 302)
        draft = self.request.responses.get(status=RespuestaSolicitudColectivo.Status.DRAFT)
        self.assertEqual(draft.changes.filter(functional_field="accion", action="INCLUIR").count(), 2)
        portal = self.client.get(reverse("colectivos_external:portal"))
        self.assertContains(portal, "Ana Uno")
        self.assertContains(portal, "Bea Dos")
        self.assertEqual(portal.content.decode().count("Novedades preparadas"), 1)
        self.assertNotContains(portal, "Previsualización de cambios guardados")
        self.assertContains(portal, "Cédula de ciudadanía")

    @patch("cotizacion_colectivos.services.external.identification_type_values", return_value=frozenset({"CC"}))
    def test_incremental_web_ingress_save_keeps_the_previous_draft_row(self, _types):
        generated = self.access()
        self.request.status = self.request.Status.SENT
        self.request.save(update_fields=("status",))
        self.enter_with_otp(generated)
        first = {
            "include_action": "INCLUIR", "include_tipo_id": "CC", "include_documento": "1019",
            "include_nombres": "Ana", "include_apellidos": "Uno", "include_rol": "Asegurado",
            "include_fecha_nacimiento": "1990-01-01", "include_fecha_ingreso": "2026-10-01",
        }
        second = {
            "include_action__2": "INCLUIR", "include_tipo_id__2": "CC", "include_documento__2": "2020",
            "include_nombres__2": "Bea", "include_apellidos__2": "Dos", "include_rol__2": "Asegurado",
            "include_fecha_nacimiento__2": "1991-01-01", "include_fecha_ingreso__2": "2026-10-02",
        }
        self.assertEqual(self.client.post(reverse("colectivos_external:save_draft"), first).status_code, 302)
        self.assertEqual(self.client.post(reverse("colectivos_external:save_draft"), second).status_code, 302)
        draft = self.request.responses.get(status=RespuestaSolicitudColectivo.Status.DRAFT)
        self.assertEqual(draft.changes.filter(functional_field="accion", action="INCLUIR").count(), 2)
        portal = self.client.get(reverse("colectivos_external:portal"))
        self.assertContains(portal, "Ana Uno")
        self.assertContains(portal, "Bea Dos")

    def test_portal_does_not_render_unchanged_policy_records_as_prepared_novelties(self):
        generated = self.access()
        self.request.status = self.request.Status.SENT
        self.request.save(update_fields=("status",))
        self.enter_with_otp(generated)
        save_response(
            access=generated.access,
            rows=[{"record": str(self.record.public_key), "action": "SIN_CAMBIOS"}],
            observations="",
        )

        portal = self.client.get(reverse("colectivos_external:portal"))

        self.assertEqual(portal.status_code, 200)
        self.assertContains(portal, "Novedades preparadas")
        self.assertContains(portal, "Aún no ha preparado novedades.")
        self.assertNotContains(portal, "Sin cambios")
        self.assertNotContains(portal, "Preparado")

    @patch("cotizacion_colectivos.services.external.identification_type_values", return_value=frozenset({"CC"}))
    def test_portal_counts_only_actionable_draft_markers(self, _types):
        generated = self.access()
        self.request.status = self.request.Status.SENT
        self.request.save(update_fields=("status",))
        self.enter_with_otp(generated)
        save_response(
            access=generated.access,
            rows=[
                {"record": str(self.record.public_key), "action": "SIN_CAMBIOS"},
                {
                    "record": "", "action": "INCLUIR", "tipo_id": "CC",
                    "documento": "1019059650", "nombres": "Ana",
                    "apellidos": "Prueba", "rol": "Asegurado",
                    "fecha_ingreso": "2026-10-01", "functional_key": "ingreso-real",
                },
            ],
            observations="",
        )

        portal = self.client.get(reverse("colectivos_external:portal"))

        self.assertEqual(portal.status_code, 200)
        self.assertContains(portal, "1 novedad(es) lista(s) para revisar.")
        self.assertContains(portal, "Ana Prueba")
        self.assertEqual(portal.content.decode().count("prepared-novelty\""), 1)
        self.assertNotContains(portal, "Sin cambios")

    @patch("cotizacion_colectivos.external_views.identification_choice_pairs", return_value=(("CC", "CC - Cédula de ciudadanía"),))
    def test_template_download_is_a_real_xlsx_response(self, _choices):
        generated = self.access()
        self.enter_with_otp(generated)
        response = self.client.post(reverse("colectivos_external:download_template"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"PK"))

    @patch("cotizacion_colectivos.services.external.identification_type_values", return_value=frozenset({"CC"}))
    def test_web_ingress_is_persisted_before_final_submit_and_support_is_scoped(self, _types):
        generated = self.access()
        self.request.status = self.request.Status.SENT
        self.request.save(update_fields=("status",))
        self.enter_with_otp(generated)
        support = SimpleUploadedFile("cedula.pdf", b"%PDF-1.7\n%%EOF", content_type="application/pdf")
        saved = self.client.post(reverse("colectivos_external:save_draft"), {
            "include_action": "INCLUIR", "include_tipo_id": "CC", "include_documento": "1019059650",
            "include_nombres": "Camilo", "include_apellidos": "Vargas", "include_rol": "Asegurado",
            "include_fecha_nacimiento": "1990-01-01", "include_fecha_ingreso": "2026-10-01",
            "include_support": support,
        })
        self.assertEqual(saved.status_code, 302)
        draft = self.request.responses.get(status=RespuestaSolicitudColectivo.Status.DRAFT)
        marker = draft.changes.get(functional_field="accion", action="INCLUIR")
        self.assertTrue(draft.changes.filter(functional_field="nombres", encrypted_new_value__isnull=False).exists())
        self.assertTrue(marker.attachments.filter(category="SOPORTE").exists())
        portal = self.client.get(reverse("colectivos_external:portal"))
        self.assertContains(portal, "Camilo Vargas")
        self.assertContains(portal, "Novedades preparadas")
        self.assertTrue(self.request.responses.filter(status=RespuestaSolicitudColectivo.Status.DRAFT).first().changes.filter(action__in={"INCLUIR", "RETIRAR", "MODIFICAR"}).exists())
        submitted = self.client.post(reverse("colectivos_external:submit"), {"declaration": "on"})
        self.assertEqual(submitted.status_code, 200, submitted.content[:500])
        draft.refresh_from_db()
        self.assertEqual(draft.status, RespuestaSolicitudColectivo.Status.SUBMITTED)

    @patch("cotizacion_colectivos.services.external.identification_type_values", return_value=frozenset({"CC"}))
    def test_prepared_ingress_edit_is_prg_noop_and_preserves_contact_data_and_attachment(self, _types):
        generated = self.access()
        self.request.status = self.request.Status.SENT
        self.request.save(update_fields=("status",))
        self.enter_with_otp(generated)
        support = SimpleUploadedFile("cedula.pdf", b"%PDF-1.7\n%%EOF", content_type="application/pdf")
        self.assertEqual(self.client.post(reverse("colectivos_external:save_draft"), {
            "include_action": "INCLUIR", "include_tipo_id": "CC", "include_documento": "1019059650",
            "include_nombres": "Camilo", "include_apellidos": "Vargas", "include_rol": "Asegurado",
            "include_fecha_nacimiento": "1990-01-01", "include_email": "camilo@example.test",
            "include_phone": "3000000000", "include_fecha_ingreso": "2026-10-01",
            "include_support": support,
        }).status_code, 302)
        draft = self.request.responses.get(status=RespuestaSolicitudColectivo.Status.DRAFT)
        marker = draft.changes.get(functional_field="accion", action=CambioSolicitudColectivo.Action.INCLUDE)
        attachment = marker.attachments.get(category="SOPORTE")
        functional_key = json.loads(decrypt(marker.encrypted_branch_payload))["functional_key"]
        edit_url = reverse("colectivos_external:edit_draft_change")
        no_op = self.client.post(edit_url, {
            "change_id": marker.pk, "tipo_id": "CC", "documento": "1019059650",
            "nombres": "Camilo", "apellidos": "Vargas", "fecha_ingreso": "2026-10-01",
            "correo": "camilo@example.test", "phone": "3000000000",
        })
        self.assertEqual(no_op.status_code, 302)
        marker.refresh_from_db()
        attachment.refresh_from_db()
        self.assertEqual(attachment.change_id, marker.pk)
        self.assertEqual(json.loads(decrypt(marker.encrypted_branch_payload))["functional_key"], functional_key)
        self.assertEqual(decrypt(draft.changes.get(functional_field="correo").encrypted_new_value), "camilo@example.test")
        changed = self.client.post(edit_url, {
            "change_id": marker.pk, "tipo_id": "CC", "documento": "1019059650",
            "nombres": "Camilo Andrés", "apellidos": "Vargas", "fecha_ingreso": "2026-10-01",
            "correo": "camilo@example.test", "phone": "3000000000",
        })
        self.assertEqual(changed.status_code, 302)
        self.assertEqual(decrypt(draft.changes.get(functional_field="nombres").encrypted_new_value), "Camilo Andrés")
        self.assertEqual(decrypt(draft.changes.get(functional_field="phone").encrypted_new_value), "3000000000")

    @patch("cotizacion_colectivos.services.external.identification_type_values", return_value=frozenset({"CC"}))
    def test_new_request_does_not_inherit_previous_draft_or_documents(self, _types):
        first_access = self.access()
        self.enter_with_otp(first_access)
        first_saved = self.client.post(reverse("colectivos_external:save_draft"), {
            "include_action": "INCLUIR", "include_tipo_id": "CC",
            "include_documento": "1019", "include_nombres": "Ana",
            "include_apellidos": "Anterior", "include_rol": "Asegurado",
            "include_fecha_ingreso": "2026-10-01",
        })
        self.assertEqual(first_saved.status_code, 302)
        first_response = self.request.responses.get(status=RespuestaSolicitudColectivo.Status.DRAFT)
        marker = first_response.changes.filter(
            functional_field="accion", action=CambioSolicitudColectivo.Action.INCLUDE,
        ).get()
        support = SimpleUploadedFile("anterior.pdf", b"%PDF-1.7\n%%EOF", content_type="application/pdf")
        support_saved = self.client.post(reverse("colectivos_external:save_draft"), {
            "include_action": "INCLUIR", "include_tipo_id": "CC",
            "include_documento": "1019", "include_nombres": "Ana",
            "include_apellidos": "Anterior", "include_rol": "Asegurado",
            "include_fecha_ingreso": "2026-10-01", "include_support": support,
        })
        self.assertEqual(support_saved.status_code, 302)
        first_response.refresh_from_db()
        first_portal = self.client.get(reverse("colectivos_external:portal"))
        self.assertContains(first_portal, "Ana Anterior")
        self.assertContains(first_portal, "anterior.pdf")
        second = SolicitudColectivo.objects.create(
            source_kind=self.request.source_kind,
            source_reference_hash=self.request.source_reference_hash,
            policy_reference_hash=self.request.policy_reference_hash,
            encrypted_policy_token=self.request.encrypted_policy_token,
            masked_policy_reference=self.request.masked_policy_reference,
            client_label=self.request.client_label,
            branch_code=self.request.branch_code,
            branch_name=self.request.branch_name,
            request_type=self.request.request_type,
            status=SolicitudColectivo.Status.READY,
            assigned_to=self.admin,
            deadline=timezone.localdate() + timedelta(days=10),
            zoho_profile="sandbox",
            encrypted_snapshot=self.request.encrypted_snapshot,
            created_by=self.admin,
        )
        second_access = generate_access(request=second, actor=self.admin, recipient="cliente2@example.test")
        self.enter_with_otp(second_access)
        second_portal = self.client.get(reverse("colectivos_external:portal"))
        self.assertContains(second_portal, "Aún no ha preparado novedades.")
        self.assertNotContains(second_portal, "Ana Anterior")
        self.assertNotContains(second_portal, "anterior.pdf")
        second_saved = self.client.post(reverse("colectivos_external:save_draft"), {
            "include_action": "INCLUIR", "include_tipo_id": "CC",
            "include_documento": "2020", "include_nombres": "Bea",
            "include_apellidos": "Nueva", "include_rol": "Asegurado",
            "include_fecha_ingreso": "2026-10-02",
        })
        self.assertEqual(second_saved.status_code, 302)
        second_portal = self.client.get(reverse("colectivos_external:portal"))
        self.assertContains(second_portal, "Bea Nueva")
        self.assertNotContains(second_portal, "Ana Anterior")
        self.assertNotContains(second_portal, "anterior.pdf")
        self.assertEqual(
            second.responses.get(status=RespuestaSolicitudColectivo.Status.DRAFT)
            .changes.filter(functional_field="accion").count(),
            1,
        )
        self.assertEqual(
            self.request.responses.get(status=RespuestaSolicitudColectivo.Status.DRAFT)
            .attachments.count(),
            1,
        )
        self.assertEqual(first_response.request_id, self.request.pk)

        # The same browser can return to S1.  Its link/OTP establishes the
        # original request session again, preserving its own draft and file.
        self.enter_with_otp(first_access)
        first_portal = self.client.get(reverse("colectivos_external:portal"))
        self.assertContains(first_portal, "Ana Anterior")
        self.assertContains(first_portal, "anterior.pdf")

    def test_excel_import_attachment_is_scoped_to_request(self):
        first_access = self.verified_access()
        excel_response = save_response(
            access=first_access, rows=[], observations="",
            origin=RespuestaSolicitudColectivo.Origin.EXCEL,
        )
        workbook_stream = io.BytesIO()
        Workbook().save(workbook_stream)
        workbook = SimpleUploadedFile(
            "S1-import.xlsx", workbook_stream.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        imported = store_attachment(response=excel_response, uploaded=workbook, allow_excel=True)
        self.assertEqual(imported.category, "EXCEL_IMPORT")

        second = SolicitudColectivo.objects.create(
            source_kind=self.request.source_kind,
            source_reference_hash=self.request.source_reference_hash,
            policy_reference_hash=self.request.policy_reference_hash,
            encrypted_policy_token=self.request.encrypted_policy_token,
            masked_policy_reference=self.request.masked_policy_reference,
            client_label=self.request.client_label,
            branch_code=self.request.branch_code,
            branch_name=self.request.branch_name,
            request_type=self.request.request_type,
            status=SolicitudColectivo.Status.READY,
            assigned_to=self.admin,
            deadline=timezone.localdate() + timedelta(days=10),
            zoho_profile="sandbox",
            encrypted_snapshot=self.request.encrypted_snapshot,
            created_by=self.admin,
        )
        second_access = generate_access(request=second, actor=self.admin, recipient="cliente2@example.test")
        self.enter_with_otp(second_access)
        portal = self.client.get(reverse("colectivos_external:portal"))
        self.assertNotContains(portal, "S1-import.xlsx")
        self.assertEqual(excel_response.request_id, self.request.pk)
        self.assertEqual(second.attachments.count(), 0)

    def test_external_entities_render_structured_rows_and_drawers(self):
        cases = {
            "91": ("Nombre", "Rol", "Solicitar retiro"),
            "86": ("Nombre", "Parentesco", "Solicitar retiro"),
            "28": ("Inmueble 1", "Calle 10 # 20-30", "Solicitar retiro"),
            "83": ("Asegurado", "Valor asegurado", "Solicitar retiro"),
            "40": ("Vehículo 1", "ABC123", "Solicitar retiro"),
        }
        member = {
            "display_name": "Persona de prueba",
            "insured_name": "Persona de prueba",
            "insured_key": "a" * 64,
            "state": "Activo",
            "plan": "Plan vigente",
            "relationship": "Hija",
            "risk_key": "b" * 64,
            "risk_summary": "Bien registrado",
            "risk_attributes": {
                "direccion": "Calle 10 # 20-30", "ciudad": "Medellín",
                "tipo_uso": "Residencial", "anio_construccion": "2018",
                "vehiculo": "Mazda CX-5", "placa": "ABC123",
                "marca": "Mazda CX-5", "modelo": "2024",
            },
        }
        self.record.economic_values = {"Valor asegurado": "$450.000.000"}
        self.record.save(update_fields=("economic_values",))
        for index, (branch_code, expected) in enumerate(cases.items()):
            with self.subTest(branch=branch_code):
                self.request.branch_code = branch_code
                self.request.status = self.request.Status.READY
                self.request.encrypted_snapshot = encrypt(json.dumps({
                    "version": 1, "policy": {}, "group": [member], "warnings": [],
                }, ensure_ascii=False))
                self.request.save(update_fields=("branch_code", "status", "encrypted_snapshot"))
                generated = generate_access(
                    request=self.request, actor=self.admin,
                    recipient="cliente@example.test", regenerate=index > 0,
                )
                self.enter_with_otp(generated)
                portal = self.client.get(reverse("colectivos_external:portal"))
                self.assertEqual(portal.status_code, 200)
                for text in expected:
                    self.assertContains(portal, text)
                self.assertContains(portal, "data-functional-table", html=False)
                self.assertContains(portal, "data-record-summary", html=False)
                self.assertContains(portal, "data-edit-panel", html=False)
                self.assertContains(portal, "data-edit-panel hidden", html=False)
                self.assertContains(portal, 'role="dialog"', html=False)
                self.assertNotContains(portal, ">Modificar<")
                self.assertNotContains(portal, "data-edit-disclosure", html=False)
                self.assertNotContains(portal, "Titular o principal")
                self.assertNotContains(portal, "Ver familia, beneficiarios y coberturas")

    def test_progressive_editor_keeps_the_existing_backend_field_contract(self):
        member = {
            "display_name": "Persona de prueba", "insured_name": "Persona de prueba",
            "insured_key": "a" * 64, "state": "Activo", "plan": "Plan vigente",
        }
        self.request.encrypted_snapshot = encrypt(json.dumps({
            "version": 1, "policy": {}, "group": [member], "warnings": [],
        }))
        self.request.save(update_fields=("encrypted_snapshot",))
        generated = self.access()
        self.enter_with_otp(generated)
        portal = self.client.get(reverse("colectivos_external:portal"))
        functional_key = "a" * 64
        self.assertContains(portal, f'name="action_entity_{functional_key}"', html=False)
        self.assertContains(portal, f'name="fecha_retiro_entity_{functional_key}"', html=False)
        self.assertNotContains(portal, f'name="plan_entity_{functional_key}"', html=False)
        self.assertContains(portal, f'name="source_records_{functional_key}"', html=False)

    def test_drawer_posts_the_unchanged_payload_contract(self):
        functional_key = "a" * 64
        member = {
            "display_name": "Persona de prueba", "insured_name": "Persona de prueba",
            "insured_key": functional_key, "state": "Activo", "plan": "Plan vigente",
        }
        self.request.encrypted_snapshot = encrypt(json.dumps({
            "version": 1, "policy": {}, "group": [member], "warnings": [],
        }))
        self.request.save(update_fields=("encrypted_snapshot",))
        generated = self.access()
        self.enter_with_otp(generated)
        saved = self.client.post(reverse("colectivos_external:save_draft"), {
            f"source_records_{functional_key}": str(self.record.public_key),
            f"action_entity_{functional_key}": "RETIRAR",
            f"fecha_retiro_entity_{functional_key}": "2026-09-01",
        })
        self.assertEqual(saved.status_code, 302)
        response = self.request.responses.get(status=RespuestaSolicitudColectivo.Status.DRAFT)
        change = response.changes.get(functional_field="fecha_retiro")
        self.assertEqual(decrypt(change.encrypted_new_value), "2026-09-01")

    def test_final_submit_persists_prepared_retirement_from_the_same_form(self):
        functional_key = "people-kathe"
        self.request.encrypted_snapshot = encrypt(json.dumps({
            "version": 1, "policy": {}, "group": [{
                "display_name": "Persona de prueba", "insured_key": functional_key,
                "state": "Activo", "plan": "Plan vigente",
            }], "warnings": [],
        }))
        self.request.status = self.request.Status.SENT
        self.request.save(update_fields=("encrypted_snapshot", "status"))
        generated = self.access()
        self.enter_with_otp(generated)

        submitted = self.client.post(reverse("colectivos_external:submit"), {
            f"source_records_{functional_key}": str(self.record.public_key),
            f"action_entity_{functional_key}": "RETIRAR",
            f"fecha_retiro_entity_{functional_key}": "2026-09-01",
            f"observaciones_entity_{functional_key}": "Retiro solicitado por el cliente",
            "declaration": "on",
        })
        self.assertEqual(submitted.status_code, 200)
        response = self.request.responses.get(status=RespuestaSolicitudColectivo.Status.SUBMITTED)
        self.assertEqual(response.safe_metadata.get("response_type"), "CHANGES")
        retirement = response.changes.filter(
            action=CambioSolicitudColectivo.Action.RETIRE,
            functional_field="fecha_retiro",
        ).get()
        self.assertEqual(decrypt(retirement.encrypted_new_value), "2026-09-01")
        observation = response.changes.filter(
            action=CambioSolicitudColectivo.Action.RETIRE,
            functional_field="observaciones",
        ).get()
        self.assertEqual(decrypt(observation.encrypted_new_value), "Retiro solicitado por el cliente")
        self.assertEqual(decrypt(observation.encrypted_observation), "Retiro solicitado por el cliente")

    def test_external_table_has_final_submit_and_local_pagination_controls(self):
        generated = self.access()
        self.enter_with_otp(generated)
        portal = self.client.get(reverse("colectivos_external:portal"))
        self.assertContains(portal, "Confirmar y enviar")
        self.assertNotContains(portal, "Guardar mis cambios")
        self.assertNotContains(portal, "¿Hay algo más que debamos saber?")
        self.assertContains(portal, 'data-external-response', html=False)
        self.assertContains(portal, 'name="action_entity_', html=False)
        self.assertContains(portal, 'name="fecha_retiro_entity_', html=False)
        self.assertContains(portal, 'formnovalidate', html=False)
        self.assertContains(portal, "data-page-size", html=False)
        self.assertContains(portal, '<option value="25">25</option>', html=False)
        self.assertContains(portal, '<option value="50">50</option>', html=False)
        self.assertContains(portal, '<option value="100">100</option>', html=False)
        self.assertContains(portal, "data-page-previous", html=False)
        self.assertContains(portal, "data-page-next", html=False)

    def test_external_drawer_accessibility_and_javascript_contract(self):
        generated = self.access()
        self.enter_with_otp(generated)
        portal = self.client.get(reverse("colectivos_external:portal"))
        self.assertContains(portal, 'aria-modal="true"', html=False)
        self.assertContains(portal, "data-drawer-title", html=False)
        self.assertContains(portal, "data-drawer-close", html=False)
        self.assertContains(portal, "data-drawer-backdrop", html=False)
        script = (settings.BASE_DIR / "static" / "js" / "colectivos-external.js").read_text(encoding="utf-8")
        self.assertIn('event.key === "Escape"', script)
        self.assertIn('event.key !== "Tab"', script)
        self.assertIn("activeTrigger", script)
        self.assertIn("focusableElements", script)

    def test_people_with_same_confirmed_key_render_once_with_consolidated_roles(self):
        shared_key = "a" * 64
        second = SolicitudColectivoRegistro.objects.create(
            request=self.request,
            element_type=SolicitudColectivoRegistro.ElementType.BENEFICIARY,
            role="Beneficiario",
            external_reference_hash="e" * 64,
            initial_status="Activo",
            plan="Plan vigente",
            original_position=2,
            checksum="f" * 64,
        )
        members = [
            {"insured_name": "Persona de prueba", "insured_key": shared_key},
            {"beneficiary_name": "Persona de prueba", "beneficiary_key": shared_key},
        ]
        self.request.encrypted_snapshot = encrypt(json.dumps({
            "version": 1, "policy": {}, "group": members, "warnings": [],
        }))
        self.request.save(update_fields=("encrypted_snapshot",))
        generated = self.access()
        self.enter_with_otp(generated)
        portal = self.client.get(reverse("colectivos_external:portal"))
        self.assertEqual(portal.content.count(b"data-functional-entity"), 1)
        self.assertContains(portal, "Asegurado · Beneficiario")
        source_records = ",".join(sorted((str(self.record.public_key), str(second.public_key))))
        self.assertContains(
            portal,
            f'value="{source_records}"',
            html=False,
        )

    def test_policy_snapshot_recovers_legacy_parent_scoped_records_for_all_roles(self):
        """A valid snapshot must not disappear when legacy records lack policy FK."""
        policy = SolicitudColectivoPoliza.objects.create(
            request=self.request,
            policy_reference_hash="p" * 64,
            encrypted_policy_token=encrypt(json.dumps({"id": "4991513000270954040"})),
            masked_policy_reference="Póliza terminada en 4040",
            branch_code="91",
            branch_name="Salud colectivo",
            encrypted_snapshot=encrypt("{}"),
            snapshot_checksum="q" * 64,
            position=1,
        )
        roles = (
            ("Afiliado", "associate"),
            ("Asegurado", "insured"),
            ("Beneficiario", "beneficiary"),
        )
        members = []
        for index, (role, prefix) in enumerate(roles, 1):
            if index == 1:
                self.record.role = role
                self.record.save(update_fields=("role",))
            else:
                SolicitudColectivoRegistro.objects.create(
                    request=self.request,
                    element_type=SolicitudColectivoRegistro.ElementType.PERSON,
                    role=role,
                    external_reference_hash=(chr(99 + index) * 64),
                    initial_status="Activo",
                    original_position=index,
                    checksum=(chr(100 + index) * 64),
                )
            members.append({
                "role": role,
                "display_name": f"Persona {role}",
                "id_type": "CC",
                f"{prefix}_name": f"Persona {role}",
                f"{prefix}_key": (str(index) * 64)[:64],
            })
        snapshot = {"version": 1, "policy": {}, "group": members, "warnings": []}
        sections = _policy_sections(self.request, snapshot)
        self.assertEqual(len(sections[0]["rows"]), 3)
        self.assertEqual(
            {row["role"] for row in sections[0]["rows"]},
            {"Afiliado", "Asegurado", "Beneficiario"},
        )
        self.assertEqual(len(sections[0]["functional_groups"]), 3)

    @patch("cotizacion_colectivos.external_views.subrisk_relationship_choice_pairs", return_value=(("Afiliado", "Afiliado"),))
    @patch("cotizacion_colectivos.zoho.get_zoho")
    def test_external_table_and_drawer_use_only_persisted_snapshot(self, get_zoho, _relationships):
        generated = self.access()
        self.enter_with_otp(generated)
        portal = self.client.get(reverse("colectivos_external:portal"))
        self.assertEqual(portal.status_code, 200)
        get_zoho.assert_not_called()

    def test_external_table_and_drawer_have_mobile_layout_without_page_overflow(self):
        css = (settings.BASE_DIR / "static" / "css" / "colectivos_external.css").read_text(encoding="utf-8")
        self.assertIn("@media(max-width:1024px)", css)
        self.assertIn("@media(max-width:768px)", css)
        self.assertIn("@media(max-width:480px)", css)
        self.assertIn(".functional-drawer{width:100%}", css)
        self.assertIn("left:50%", css)
        self.assertIn("width:min(720px,calc(100vw - 2rem))", css)
        self.assertIn("transform:translate(-50%,-50%) scale(1)", css)
        self.assertNotIn("transform:translateX(100%)", css)
        self.assertIn(".functional-drawer .external-add-card label:first-child{display:flex", css)
        self.assertIn("gap:1rem", css)
        self.assertIn(".functional-drawer .external-add-card{display:grid;min-width:0", css)
        self.assertIn("padding:1.2rem", css)
        self.assertIn("body main{width:min(100% - 1rem,1480px)}", css)

    def test_external_token_is_only_persisted_as_hash_and_is_tamper_evident(self):
        generated = self.access()
        self.assertNotIn(generated.token, generated.access.token_hash)
        self.assertNotIn("cliente@example.test", generated.access.encrypted_recipient)
        self.assertEqual(resolve_token(generated.token).pk, generated.access.pk)
        with self.assertRaises(ExternalAccessError):
            resolve_token(generated.token + "alterado")

    def test_regeneration_revokes_previous_access(self):
        first = self.access()
        second = generate_access(
            request=self.request,
            actor=self.admin,
            recipient="cliente@example.test",
            regenerate=True,
        )
        first.access.refresh_from_db()
        self.assertEqual(first.access.status, first.access.Status.REVOKED)
        self.assertEqual(resolve_token(second.token).pk, second.access.pk)
        with self.assertRaises(ExternalAccessError):
            resolve_token(first.token)

    def test_expired_token_fails_closed(self):
        generated = self.access()
        generated.access.expires_at = timezone.now() - timedelta(seconds=1)
        generated.access.save(update_fields=("expires_at",))
        with self.assertRaises(ExternalAccessError):
            resolve_token(generated.token)

    def test_access_expiry_uses_exact_seconds_not_end_of_day(self):
        before = timezone.now()
        generated = self.access()
        after = timezone.now()
        self.assertGreaterEqual(
            generated.access.expires_at,
            before + timedelta(seconds=settings.COLECTIVOS_EXTERNAL_LINK_TTL_SECONDS),
        )
        self.assertLessEqual(
            generated.access.expires_at,
            after + timedelta(seconds=settings.COLECTIVOS_EXTERNAL_LINK_TTL_SECONDS),
        )

    @override_settings(COLECTIVOS_EXTERNAL_LINK_TTL_SECONDS=172800, COLECTIVOS_EXTERNAL_LINK_MAX_TTL_SECONDS=604800)
    def test_link_ttl_is_elapsed_48_hours_even_when_deadline_is_today(self):
        self.request.deadline = timezone.localdate() + timedelta(days=1)
        self.request.save(update_fields=("deadline",))
        before = timezone.now()
        generated = self.access()
        self.assertGreaterEqual(generated.access.expires_at, before + timedelta(hours=47, minutes=59))
        self.assertLessEqual(generated.access.expires_at, before + timedelta(hours=48, seconds=2))
        self.assertEqual(resolve_token(generated.token).pk, generated.access.pk)

    def test_otp_expires_with_short_ttl_and_link_remains_independent(self):
        generated = self.access()
        before = timezone.now()
        with patch(
            "cotizacion_colectivos.services.external.send_notification"
        ) as sender, patch(
            "cotizacion_colectivos.services.external.secrets.randbelow",
            return_value=123456,
        ):
            self.assertTrue(issue_otp(generated.access))
        generated.access.refresh_from_db()
        self.assertGreaterEqual(generated.access.otp_expires_at, before + timedelta(seconds=599))
        self.assertLessEqual(generated.access.otp_expires_at, before + timedelta(seconds=601))
        self.assertGreater(generated.access.expires_at, generated.access.otp_expires_at)
        message = sender.call_args.kwargs
        self.assertIn("solicite un código nuevo", message["text_body"])
        self.assertNotIn("minutos", message["text_body"])

    def test_entry_does_not_resend_an_unexpired_otp_after_link_bound_issue(self):
        generated = self.access()
        with patch("cotizacion_colectivos.services.external.send_notification") as sender, patch(
            "cotizacion_colectivos.services.external.secrets.randbelow", return_value=123456,
        ):
            first = self.client.get(reverse("colectivos_external:entry", args=[generated.token]))
            second = self.client.get(reverse("colectivos_external:entry", args=[generated.token]))
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(sender.call_count, 1)

    def test_email_backend_receives_real_otp_while_database_and_logs_do_not(self):
        generated = self.access()
        backend = Mock(name="otp_backend")
        backend.name = "smtp"
        backend.send.return_value = "accepted"
        with patch("cotizacion_colectivos.services.external.secrets.randbelow", return_value=123456), patch(
            "vault.notifications.get_backend", return_value=backend,
        ), patch("vault.notifications.logger") as notification_logger:
            self.assertTrue(issue_otp(generated.access))

        subject, text_body, html_body, recipient = backend.send.call_args.args
        self.assertEqual(recipient, "cliente@example.test")
        self.assertIn("123456", text_body)
        self.assertIn("123456", html_body)
        self.assertNotIn("[CÓDIGO OMITIDO]", text_body + html_body)
        self.assertIn("Código de verificación", subject + html_body)
        generated.access.refresh_from_db()
        self.assertNotEqual(generated.access.otp_hash, "123456")
        self.assertNotIn("123456", repr(generated.access.__dict__))
        self.assertNotIn("123456", repr(notification_logger.mock_calls))

    def test_otp_is_one_use_and_external_session_is_request_scoped(self):
        generated = self.access()
        generated.access.otp_hash = ""
        from django.contrib.auth.hashers import make_password

        generated.access.otp_hash = make_password("123456")
        generated.access.otp_expires_at = timezone.now() + timedelta(minutes=5)
        generated.access.save(update_fields=("otp_hash", "otp_expires_at"))
        cookie = verify_otp(generated.access, "123456")
        session_access = resolve_external_session(cookie)
        self.assertEqual(session_access.request_id, self.request.pk)
        session_access.refresh_from_db()
        self.assertEqual(session_access.otp_hash, "")
        with self.assertRaises(ExternalAccessError):
            verify_otp(session_access, "123456")

    def test_otp_blocks_after_bounded_failures(self):
        generated = self.access()
        from django.contrib.auth.hashers import make_password

        generated.access.otp_hash = make_password("123456")
        generated.access.otp_expires_at = timezone.now() + timedelta(minutes=5)
        generated.access.save(update_fields=("otp_hash", "otp_expires_at"))
        for _ in range(3):
            with self.assertRaises(ExternalAccessError):
                verify_otp(generated.access, "000000")
        generated.access.refresh_from_db()
        self.assertEqual(generated.access.status, generated.access.Status.BLOCKED)

    def verified_access(self):
        generated = self.access()
        generated.access.status = generated.access.Status.VERIFIED
        generated.access.save(update_fields=("status",))
        self.request.status = self.request.Status.OPENED
        self.request.save(update_fields=("status",))
        return generated.access

    def test_web_draft_versions_changes_and_submission_is_idempotent(self):
        access = self.verified_access()
        response = save_response(
            access=access,
            rows=[{
                "record": str(self.record.public_key),
                "action": "RETIRAR",
                "fecha_retiro": "2026-09-01",
            }],
            observations="Observación del cliente",
        )
        self.assertEqual(response.origin, RespuestaSolicitudColectivo.Origin.WEB)
        self.assertNotIn("Observación del cliente", response.encrypted_client_observations)
        self.assertTrue(response.changes.filter(functional_field="fecha_retiro").exists())
        submitted = submit_response(access=access, response=response, declaration=True)
        again = submit_response(access=access, response=submitted, declaration=True)
        self.assertEqual(again.pk, submitted.pk)
        self.request.refresh_from_db()
        self.assertEqual(self.request.status, self.request.Status.ANSWERED)
        access.refresh_from_db()
        self.assertEqual(access.status, access.Status.USED)

    def test_personal_identifier_validation_for_inclusion(self):
        access = self.verified_access()
        with self.assertRaises(ExternalAccessError):
            save_response(
                access=access,
                rows=[{
                    "record": "",
                    "action": "INCLUIR",
                    "tipo_id": "CC",
                    "documento": "12-34",
                    "nombre": "Persona",
                    "fecha_efectiva": "2026-09-01",
                }],
                observations="",
            )

    @patch("cotizacion_colectivos.services.external.identification_type_values", return_value=frozenset({"CC"}))
    def test_novelties_use_requested_dates_without_effective_date(self, _identification_types):
        access = self.verified_access()
        ingress = save_response(
            access=access,
            rows=[{
                "record": "",
                "action": "INCLUIR",
                "tipo_id": "CC",
                "documento": "12345678",
                "nombres": "Persona",
                "apellidos": "de prueba",
                "rol": "Asegurado",
                "fecha_nacimiento": "1990-01-01",
                "fecha_ingreso": "2026-09-01",
            }],
            observations="",
        )
        self.assertTrue(ingress.changes.filter(functional_field="fecha_ingreso").exists())
        self.assertTrue(ingress.changes.filter(functional_field="fecha_nacimiento").exists())
        self.assertFalse(ingress.changes.filter(functional_field="fecha_efectiva").exists())

        retirement = save_response(
            access=access,
            rows=[{
                "record": str(self.record.public_key),
                "action": "RETIRAR",
                "fecha_retiro": "2026-10-01",
            }],
            observations="",
        )
        self.assertTrue(retirement.changes.filter(functional_field="fecha_retiro").exists())
        self.assertFalse(retirement.changes.filter(functional_field="fecha_efectiva").exists())

    def test_template_round_trip_and_formula_rejection(self):
        content = mark_novelties_actions(build_novelties_template(self.request))
        upload = SimpleUploadedFile(
            "novedades.xlsx",
            content,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        preview = parse_novelties(upload, self.request)
        self.assertEqual(preview.counts["INCLUIR"], 1)
        self.assertEqual(preview.rows[0]["rol"], "Asegurado")

        workbook = load_workbook(io.BytesIO(content))
        workbook["Novedades"]["A2"] = "=1+1"
        modified = io.BytesIO()
        workbook.save(modified)
        bad = SimpleUploadedFile("novedades.xlsx", modified.getvalue())
        with self.assertRaises(ValidationError):
            parse_novelties(bad, self.request)

    def test_functional_excel_consolidates_roles_and_keeps_signed_source_mapping(self):
        functional_key = "a" * 64
        payload = {
            "display_name": "Información protegida",
            "insured_name": "Información protegida",
            "insured_key": functional_key,
        }
        self.record.encrypted_branch_payload = encrypt(json.dumps(payload))
        self.record.save(update_fields=("encrypted_branch_payload",))
        second = SolicitudColectivoRegistro.objects.create(
            request=self.request,
            element_type=SolicitudColectivoRegistro.ElementType.PERSON,
            role="Asegurado",
            external_reference_hash="e" * 64,
            initial_status="Activo",
            plan="Plan vigente",
            encrypted_branch_payload=encrypt(json.dumps(payload)),
            original_position=2,
            checksum="f" * 64,
        )
        content = mark_novelties_actions(build_novelties_template(self.request))
        workbook = load_workbook(io.BytesIO(content), read_only=True)
        self.assertEqual(workbook["Novedades"].max_row, 2)
        upload = SimpleUploadedFile("novedades.xlsx", content)
        preview = parse_novelties(upload, self.request)
        self.assertEqual(preview.rows[0]["action"], "INCLUIR")

    def test_one_functional_novelty_references_multiple_technical_records(self):
        second = SolicitudColectivoRegistro.objects.create(
            request=self.request,
            element_type=SolicitudColectivoRegistro.ElementType.PERSON,
            role="Beneficiario",
            external_reference_hash="e" * 64,
            initial_status="Activo",
            plan="Plan vigente",
            original_position=2,
            checksum="f" * 64,
        )
        access = self.verified_access()
        response = save_response(
            access=access,
            rows=[{
                "record": str(self.record.public_key),
                "records": (str(self.record.public_key), str(second.public_key)),
                "functional_key": "a" * 64,
                "action": "MODIFICAR",
                "plan": "Plan solicitado",
                "fecha_efectiva": "2026-09-01",
            }],
            observations="",
        )
        self.assertEqual(response.changes.filter(functional_field="accion").count(), 1)
        self.assertEqual(response.changes.filter(functional_field="plan").count(), 1)
        mapping = json.loads(decrypt(response.changes.get(functional_field="accion").encrypted_branch_payload))
        self.assertEqual(
            set(mapping["source_record_keys"]),
            {str(self.record.public_key), str(second.public_key)},
        )

    def test_macro_or_external_link_payload_is_rejected(self):
        content = mark_novelties_actions(build_novelties_template(self.request))
        source = zipfile.ZipFile(io.BytesIO(content))
        altered = io.BytesIO()
        with zipfile.ZipFile(altered, "w") as output:
            for info in source.infolist():
                output.writestr(info, source.read(info.filename))
            output.writestr("xl/vbaProject.bin", b"not-a-macro")
        uploaded = SimpleUploadedFile("novedades.xlsx", altered.getvalue())
        with self.assertRaises(ValidationError):
            parse_novelties(uploaded, self.request)

    def test_cell_hyperlink_is_consumed_as_displayed_text(self):
        workbook = load_workbook(io.BytesIO(build_novelties_template(self.request)))
        sheet = workbook["Novedades"]
        sheet["A2"] = "Ingreso"
        sheet["B2"] = "CC"
        sheet["C2"] = "123456"
        sheet["D2"] = "Cliente"
        sheet["E2"] = "Prueba"
        sheet["H2"] = "3000000000"
        sheet["I2"] = "2026-09-01"
        sheet["G2"] = "cliente@empresa.com"
        sheet["G2"].hyperlink = "https://example.invalid/cliente"
        output = io.BytesIO()
        workbook.save(output)
        uploaded = SimpleUploadedFile("novedades.xlsx", output.getvalue())
        preview = parse_novelties(uploaded, self.request)
        self.assertTrue(preview.rows)
        self.assertIn("cliente@empresa.com", str(preview.rows[0]))

    @override_settings(COLECTIVOS_PRIVATE_ROOT=None)
    def test_attachment_setting_is_required(self):
        access = self.verified_access()
        response = save_response(
            access=access,
            rows=[{"record": str(self.record.public_key), "action": "SIN_CAMBIOS"}],
            observations="",
        )
        uploaded = SimpleUploadedFile("soporte.pdf", b"%PDF-1.7\n%%EOF", content_type="application/pdf")
        with self.assertRaises((ValidationError, TypeError)):
            store_attachment(response=response, uploaded=uploaded)

    @override_settings(COLECTIVOS_PRIVATE_ROOT="private_assets/colectivos-test")
    def test_attachment_rejects_extension_content_mismatch(self):
        access = self.verified_access()
        response = save_response(
            access=access,
            rows=[{"record": str(self.record.public_key), "action": "SIN_CAMBIOS"}],
            observations="",
        )
        uploaded = SimpleUploadedFile("soporte.pdf", b"not a pdf", content_type="application/pdf")
        with self.assertRaises(ValidationError):
            store_attachment(response=response, uploaded=uploaded)
        self.assertFalse(AdjuntoSolicitudColectivo.objects.exists())

    def test_no_write_api_is_exposed_by_external_services(self):
        import cotizacion_colectivos.services.external as external

        forbidden = {"create", "update", "delete", "upsert", "upload", "attach"}
        self.assertFalse(forbidden.intersection(set(dir(external))))

    def submitted_response(self):
        access = self.verified_access()
        response = save_response(
            access=access,
            rows=[{
                "record": str(self.record.public_key),
                "action": "MODIFICAR",
                "plan": "Plan aprobado",
                "fecha_efectiva": "2026-09-01",
            }],
            observations="",
        )
        return submit_response(access=access, response=response, declaration=True)

    def test_response_export_contains_no_review_and_approved_export_requires_decisions(self):
        response = self.submitted_response()
        response_content = build_response_workbook(response)
        response_book = load_workbook(io.BytesIO(response_content), data_only=False)
        self.assertEqual(response_book.sheetnames, ["Resumen", "Respuesta", "Metadatos"])
        with self.assertRaises(ValidationError):
            build_approved_consolidated(response)

        decisions = {
            change.pk: {"decision": RevisionSolicitudColectivo.Decision.APPROVE}
            for change in response.changes.all()
        }
        record_reviews(response=response, reviewer=self.admin, decisions=decisions)
        finalize_review(response=response, reviewer=self.admin, action="approve")
        response.refresh_from_db()
        content = build_approved_consolidated(response)
        approved_book = load_workbook(io.BytesIO(content), data_only=False)
        self.assertIn("Consolidado aprobado", approved_book.sheetnames)

    @patch("cotizacion_colectivos.services.external.send_invitation")
    def test_correction_reuses_same_request_and_rotates_access(self, send_invitation_mock):
        response = self.submitted_response()
        decisions = {
            change.pk: {
                "decision": RevisionSolicitudColectivo.Decision.CORRECTION,
                "client_observation": "Ajuste requerido",
            }
            for change in response.changes.all()
        }
        record_reviews(response=response, reviewer=self.admin, decisions=decisions)
        with self.captureOnCommitCallbacks(execute=True):
            result = finalize_review(response=response, reviewer=self.admin, action="correction")
        self.assertEqual(result.pk, self.request.pk)
        result.refresh_from_db()
        self.assertEqual(result.status, result.Status.CORRECTION)
        self.assertEqual(result.external_accesses.filter(status="ACTIVO").count(), 1)
        send_invitation_mock.assert_called_once()
