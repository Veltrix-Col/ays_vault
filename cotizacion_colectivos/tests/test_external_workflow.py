from __future__ import annotations

import io
import zipfile
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import resolve, reverse
from django.utils import timezone
from openpyxl import load_workbook

from vault.crypto import decrypt, encrypt

from cotizacion_colectivos.models import (
    AccesoExternoSolicitudColectivo,
    AdjuntoSolicitudColectivo,
    CambioSolicitudColectivo,
    RespuestaSolicitudColectivo,
    RevisionSolicitudColectivo,
    SolicitudColectivo,
    SolicitudColectivoRegistro,
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
    resolve_external_session,
    resolve_token,
    save_response,
    submit_response,
    verify_otp,
)
from cotizacion_colectivos.services.review import finalize_review, record_reviews


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
            branch_name="Salud colectivo",
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

    def test_static_portal_route_is_not_interpreted_as_a_token(self):
        match = resolve(reverse("colectivos_external:portal"))
        self.assertEqual(match.url_name, "portal")

    @override_settings(DEBUG=True, COLECTIVOS_EXTERNAL_ACCESS_VERIFICATION="token_only")
    def test_external_portal_uses_isolated_cookie_without_django_login(self):
        generated = self.access()
        self.request.status = self.request.Status.SENT
        self.request.save(update_fields=("status",))
        entry_response = self.client.get(
            reverse("colectivos_external:entry", args=[generated.token])
        )
        self.assertEqual(entry_response.status_code, 302)
        self.assertIn("colectivos_external_session", entry_response.cookies)
        portal_response = self.client.get(reverse("colectivos_external:portal"))
        self.assertEqual(portal_response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(portal_response["Cache-Control"], "max-age=0, no-cache, no-store, must-revalidate, private")

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
                "action": "MODIFICAR",
                "plan": "Plan solicitado",
                "fecha_efectiva": "2026-09-01",
            }],
            observations="Observación del cliente",
        )
        self.assertEqual(response.origin, RespuestaSolicitudColectivo.Origin.WEB)
        self.assertNotIn("Observación del cliente", response.encrypted_client_observations)
        self.assertTrue(response.changes.filter(functional_field="plan").exists())
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

    def test_template_round_trip_and_formula_rejection(self):
        content = build_novelties_template(self.request)
        upload = SimpleUploadedFile(
            "novedades.xlsx",
            content,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        preview = parse_novelties(upload, self.request)
        self.assertEqual(preview.counts["SIN_CAMBIOS"], 1)

        workbook = load_workbook(io.BytesIO(content))
        workbook["Novedades"]["A2"] = "=1+1"
        modified = io.BytesIO()
        workbook.save(modified)
        bad = SimpleUploadedFile("novedades.xlsx", modified.getvalue())
        with self.assertRaises(ValidationError):
            parse_novelties(bad, self.request)

    def test_macro_or_external_link_payload_is_rejected(self):
        content = build_novelties_template(self.request)
        source = zipfile.ZipFile(io.BytesIO(content))
        altered = io.BytesIO()
        with zipfile.ZipFile(altered, "w") as output:
            for info in source.infolist():
                output.writestr(info, source.read(info.filename))
            output.writestr("xl/vbaProject.bin", b"not-a-macro")
        uploaded = SimpleUploadedFile("novedades.xlsx", altered.getvalue())
        with self.assertRaises(ValidationError):
            parse_novelties(uploaded, self.request)

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
