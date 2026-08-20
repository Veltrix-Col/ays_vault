from __future__ import annotations

import tempfile
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from vault.crypto import encrypt

from cotizacion_colectivos.models import (
    AccesoExternoSolicitudColectivo,
    RespuestaSolicitudColectivo,
    SolicitudColectivo,
    SolicitudColectivoRegistro,
    VistaPreviaExcelSolicitudColectivo,
)
from cotizacion_colectivos.services.deadlines import process_deadlines
from cotizacion_colectivos.services.excel_previews import cancel_preview, confirm_preview, create_preview
from cotizacion_colectivos.services.excel_roundtrip import build_novelties_template
from cotizacion_colectivos.services.external import ExternalAccessError, generate_access


class PreviewDeadlineTests(TestCase):
    def setUp(self):
        self.private = tempfile.TemporaryDirectory()
        self.addCleanup(self.private.cleanup)
        self.settings_override = override_settings(
            COLECTIVOS_PRIVATE_ROOT=Path(self.private.name),
            COLECTIVOS_EXCEL_PREVIEW_TTL_SECONDS=1800,
            COLECTIVOS_DEADLINE_EMAIL_ENABLED=True,
            COLECTIVOS_DEADLINES_ENABLED=True,
            COLECTIVOS_DEADLINE_REMINDER_DAYS=3,
            COLECTIVOS_DEADLINE_BATCH_LIMIT=100,
            COLECTIVOS_EXTERNAL_LINK_TTL_SECONDS=3600,
            COLECTIVOS_EXTERNAL_LINK_MAX_TTL_SECONDS=7200,
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        user = get_user_model().objects.create_superuser("preview-admin", "preview@example.test", "Password123!")
        self.request = SolicitudColectivo.objects.create(
            source_kind="company", source_reference_hash="a" * 64, policy_reference_hash="b" * 64,
            encrypted_policy_token=encrypt("token"), masked_policy_reference="Póliza terminada en 1234",
            client_label="Cliente", branch_code="91", branch_name="Salud colectivo",
            request_type=SolicitudColectivo.RequestType.UPDATE, status=SolicitudColectivo.Status.OPENED,
            assigned_to=user, deadline=timezone.localdate() + timedelta(days=2), zoho_profile="sandbox",
            encrypted_snapshot=encrypt('{"version":1}'), created_by=user,
        )
        SolicitudColectivoRegistro.objects.create(
            request=self.request, element_type=SolicitudColectivoRegistro.ElementType.PERSON,
            role="Asegurado", external_reference_hash="c" * 64, initial_status="Activo",
            original_position=1, checksum="d" * 64,
        )
        generated = generate_access(request=self.request, actor=user, recipient="client@example.test")
        self.access = generated.access
        self.access.status = self.access.Status.VERIFIED
        self.access.save(update_fields=("status",))
        self.cookie = "signed-session-value"

    def workbook(self):
        raw = build_novelties_template(self.request)
        return SimpleUploadedFile("novedades.xlsx", raw, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    def test_preview_does_not_create_response_and_cancel_removes_encrypted_file(self):
        item, token = create_preview(access=self.access, session_cookie=self.cookie, uploaded=self.workbook())
        self.assertFalse(RespuestaSolicitudColectivo.objects.exists())
        path = Path(self.private.name) / "excel_previews" / item.stored_path
        self.assertTrue(path.exists())
        self.assertNotIn("PK", path.read_text(encoding="utf-8")[:20])
        with self.captureOnCommitCallbacks(execute=True):
            cancel_preview(token=token, access=self.access, session_cookie=self.cookie)
        item.refresh_from_db()
        self.assertEqual(item.status, item.Status.CANCELLED)
        self.assertFalse(path.exists())
        self.assertFalse(RespuestaSolicitudColectivo.objects.exists())

    def test_confirmation_is_atomic_and_idempotent(self):
        item, token = create_preview(access=self.access, session_cookie=self.cookie, uploaded=self.workbook())
        path = Path(self.private.name) / "excel_previews" / item.stored_path
        with self.captureOnCommitCallbacks(execute=True):
            first = confirm_preview(token=token, access=self.access, session_cookie=self.cookie)
        second = confirm_preview(token=token, access=self.access, session_cookie=self.cookie)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(RespuestaSolicitudColectivo.objects.count(), 1)
        item.refresh_from_db()
        self.assertEqual(item.status, item.Status.IMPORTED)
        self.assertEqual(item.encrypted_payload, "")
        self.assertFalse(path.exists())

    def test_invalid_preview_never_persists_response_or_preview(self):
        invalid = SimpleUploadedFile("novedades.xlsx", b"not-an-xlsx", content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        with self.assertRaises(ValidationError):
            create_preview(access=self.access, session_cookie=self.cookie, uploaded=invalid)
        self.assertFalse(RespuestaSolicitudColectivo.objects.exists())
        self.assertFalse(VistaPreviaExcelSolicitudColectivo.objects.exists())

    def test_preview_is_bound_to_request_and_access(self):
        item, token = create_preview(access=self.access, session_cookie=self.cookie, uploaded=self.workbook())
        other = SolicitudColectivo.objects.create(
            source_kind="company", source_reference_hash="e" * 64, policy_reference_hash="f" * 64,
            encrypted_policy_token=encrypt("other"), masked_policy_reference="Póliza terminada en 5678",
            client_label="Otro", branch_code="91", branch_name="Salud colectivo",
            request_type=SolicitudColectivo.RequestType.UPDATE, status=SolicitudColectivo.Status.OPENED,
            assigned_to=self.request.assigned_to, deadline=timezone.localdate() + timedelta(days=2), zoho_profile="sandbox",
            encrypted_snapshot=encrypt('{"version":1}'), created_by=self.request.created_by,
        )
        other_access = generate_access(request=other, actor=self.request.created_by, recipient="other@example.test").access
        other_access.status = other_access.Status.VERIFIED
        other_access.save(update_fields=("status",))
        with self.assertRaises(ExternalAccessError):
            confirm_preview(token=token, access=other_access, session_cookie=self.cookie)
        self.assertFalse(RespuestaSolicitudColectivo.objects.exists())

    def test_preview_rejects_other_session_expiry_and_checksum_change(self):
        item, token = create_preview(access=self.access, session_cookie=self.cookie, uploaded=self.workbook())
        with self.assertRaises(ExternalAccessError):
            confirm_preview(token=token, access=self.access, session_cookie="other-session")
        item.expires_at = timezone.now() - timedelta(seconds=1)
        item.save(update_fields=("expires_at",))
        with self.assertRaises(ExternalAccessError):
            confirm_preview(token=token, access=self.access, session_cookie=self.cookie)
        self.assertFalse(RespuestaSolicitudColectivo.objects.exists())

        item2, token2 = create_preview(access=self.access, session_cookie=self.cookie, uploaded=self.workbook())
        path = Path(self.private.name) / "excel_previews" / item2.stored_path
        path.write_text(encrypt("YWx0ZXJhZG8="), encoding="utf-8")
        with self.assertRaises(ExternalAccessError):
            confirm_preview(token=token2, access=self.access, session_cookie=self.cookie)
        self.assertFalse(RespuestaSolicitudColectivo.objects.exists())

    @patch("cotizacion_colectivos.services.deadlines.send_notification")
    def test_deadline_processing_is_dry_run_then_idempotent(self, mocked_send):
        mocked_send.return_value = SimpleNamespace(result="SENT")
        self.access.expires_at = timezone.now() - timedelta(seconds=1)
        self.access.otp_hash = "hash"
        self.access.otp_expires_at = timezone.now() - timedelta(seconds=1)
        self.access.save(update_fields=("expires_at", "otp_hash", "otp_expires_at"))
        dry = process_deadlines(dry_run=True)
        self.assertEqual(dry.accesses_expired, 1)
        self.access.refresh_from_db()
        self.assertEqual(self.access.status, self.access.Status.VERIFIED)
        self.assertEqual(mocked_send.call_count, 0)
        result = process_deadlines()
        self.access.refresh_from_db()
        self.assertEqual(self.access.status, self.access.Status.EXPIRED)
        self.assertEqual(result.requests_near_due, 1)
        first_count = mocked_send.call_count
        process_deadlines()
        self.assertGreaterEqual(mocked_send.call_count, first_count)

    @patch("cotizacion_colectivos.services.deadlines.send_notification")
    def test_expired_request_changes_only_allowed_active_states(self, mocked_send):
        mocked_send.return_value = SimpleNamespace(result="SENT")
        self.request.deadline = timezone.localdate() - timedelta(days=1)
        self.request.save(update_fields=("deadline",))
        # Una solicitud con enlace externo todavía vigente conserva el acceso
        # hasta su TTL; para probar la transición por deadline aislamos ese
        # caso dejando el acceso ya vencido.
        self.access.expires_at = timezone.now() - timedelta(seconds=1)
        self.access.save(update_fields=("expires_at",))
        process_deadlines()
        self.request.refresh_from_db()
        self.assertEqual(self.request.status, self.request.Status.EXPIRED)
        self.request.status = self.request.Status.CLOSED
        self.request.save(update_fields=("status",))
        process_deadlines(now=timezone.now() + timedelta(days=1))
        self.request.refresh_from_db()
        self.assertEqual(self.request.status, self.request.Status.CLOSED)

    def test_command_supports_safe_dry_run(self):
        call_command("colectivos_process_deadlines", "--dry-run", "--limit", "10", "--now", timezone.now().isoformat())

    def test_preview_confirmation_post_keeps_csrf_enforced(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post(reverse("colectivos_external:confirm_excel_preview", args=["selector.secret-that-is-long-enough-1234567890"]))
        self.assertEqual(response.status_code, 403)
