from __future__ import annotations

import base64
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase, override_settings

from vault.crypto import encrypt

from cotizacion_colectivos.models import InvitacionAseguradoraAdjunto
from cotizacion_colectivos.services.common import sign_record_id, unsign_record_context
from cotizacion_colectivos.services.individual_attachment_publisher import (
    IndividualAttachmentBlocked,
    IndividualAttachmentUncertain,
    _validate_document_contract,
    publish_attachment,
)
from cotizacion_colectivos.services.invitation_attachment_publisher import (
    INVITATION_ATTACHMENT_FEATURE_FLAG,
    prepare_invitation_attachment,
)


POLICY_ID = "4234567890123456789"
TOKEN = sign_record_id(POLICY_ID, "policy", context={"source_id": "5234567890123456789", "source_kind": "company"})


class InvitationAttachmentContractTests(SimpleTestCase):
    def test_policy_token_resolves_only_the_signed_policy_id(self):
        self.assertEqual(unsign_record_context(TOKEN, "policy")["id"], POLICY_ID)
        with self.assertRaises(Exception):
            unsign_record_context(sign_record_id(POLICY_ID, "company"), "policy")

    def test_polizas_contract_is_closed_and_existing_contracts_remain(self):
        self.assertEqual(_validate_document_contract(module="Polizas", owner_type="policy", document_type="invitation_document"), "invitation_document")
        self.assertEqual(_validate_document_contract(module="Contacts", owner_type="contact", document_type="identity_document"), "identity_document")
        self.assertEqual(_validate_document_contract(module="Riesgos", owner_type="risk", document_type="vehicle_registration"), "vehicle_registration")
        for owner_type, document_type in (("policy", "identity_document"), ("contact", "invitation_document"), ("risk", "invitation_document")):
            with self.assertRaises(ValidationError):
                _validate_document_contract(module="Polizas", owner_type=owner_type, document_type=document_type)

    @override_settings(
        COLECTIVOS_PRIVATE_ROOT=".", ZOHO_ACTIVE_PROFILE="sandbox",
        COLECTIVOS_ATTACHMENT_PUBLISH_ENABLED=True,
        COLECTIVOS_INVITATION_ATTACHMENT_PUBLISH_ENABLED=True,
        COLECTIVOS_SANDBOX_ATTACHMENT_WRITE_CONFIRMATION="SANDBOX_ATTACHMENT_WRITE",
    )
    def test_upload_uses_polizas_record_and_canonical_filename_without_email(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "individual_quotations" / "invite.enc"
            target.parent.mkdir(parents=True)
            content = b"PK invitation workbook"
            target.write_bytes(encrypt(base64.b64encode(content).decode()).encode())
            attachment = SimpleNamespace(
                stored_path="invite.enc", safe_original_name="sura.xlsx",
                detected_mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                safe_metadata={
                    "owner_type": "policy", "document_type": "invitation_document",
                    "policy_number": "040006434488", "filename_detail": "SURA_40",
                }, save=Mock(),
            )
            upload = Mock(return_value={"attachment_id": "att-1"})
            zoho = SimpleNamespace(attachments=SimpleNamespace(upload=upload))
            with patch("cotizacion_colectivos.services.individual_attachment_publisher.settings.COLECTIVOS_PRIVATE_ROOT", folder):
                result = publish_attachment(
                    attachment=attachment, module="Polizas", record_id=POLICY_ID,
                    zoho=zoho, feature_flag=INVITATION_ATTACHMENT_FEATURE_FLAG,
                )
            kwargs = upload.call_args.kwargs
            self.assertEqual(kwargs["module"], "Polizas")
            self.assertEqual(kwargs["record_id"], POLICY_ID)
            self.assertTrue(kwargs["filename"].startswith("INVITACION_040006434488_SURA_40"))
            self.assertEqual(kwargs["file"].getvalue(), content)
            self.assertEqual(result["attachment_id"], "att-1")


class InvitationAttachmentPersistenceTests(TestCase):
    @override_settings(
        ZOHO_ACTIVE_PROFILE="sandbox",
        COLECTIVOS_INVITATION_ATTACHMENT_PUBLISH_ENABLED=True,
        COLECTIVOS_SANDBOX_ATTACHMENT_WRITE_CONFIRMATION="SANDBOX_ATTACHMENT_WRITE",
    )
    @patch("cotizacion_colectivos.services.invitation_attachment_publisher.preview_invitation_templates")
    @patch("cotizacion_colectivos.services.invitation_attachment_publisher.generate_invitation_templates")
    def test_same_checksum_is_idempotent_and_persists_attachment_id(self, generate, preview):
        with tempfile.TemporaryDirectory() as folder:
            detail = SimpleNamespace(full_reference="040006434488", masked_reference="Póliza", branch_code="40")
            preview.return_value = (detail, (), {})
            generate.return_value = (b"PK workbook", "sura_movilidad.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ())
            upload = Mock(return_value={"attachment_id": "att-1"})
            zoho = SimpleNamespace(attachments=SimpleNamespace(upload=upload))
            with override_settings(COLECTIVOS_PRIVATE_ROOT=folder):
                first = prepare_invitation_attachment(token=TOKEN, insurer_code="SURA", zoho=zoho)
                second = prepare_invitation_attachment(token=TOKEN, insurer_code="SURA", zoho=zoho)
            self.assertEqual(first["attachment_id"], "att-1")
            self.assertEqual(second["status"], "UPLOADED")
            self.assertEqual(upload.call_count, 1)
            row = InvitacionAseguradoraAdjunto.objects.get()
            self.assertEqual(row.policy_record_id, POLICY_ID)
            self.assertEqual(row.insurer_code, "SURA")
            self.assertEqual(row.safe_metadata["document_type"], "invitation_document")
            self.assertEqual(row.safe_metadata["zoho_attachment_id"], "att-1")

    @override_settings(
        ZOHO_ACTIVE_PROFILE="sandbox",
        COLECTIVOS_INVITATION_ATTACHMENT_PUBLISH_ENABLED=False,
        COLECTIVOS_SANDBOX_ATTACHMENT_WRITE_CONFIRMATION="SANDBOX_ATTACHMENT_WRITE",
    )
    @patch("cotizacion_colectivos.services.invitation_attachment_publisher.preview_invitation_templates")
    @patch("cotizacion_colectivos.services.invitation_attachment_publisher.generate_invitation_templates")
    def test_invitation_flag_off_blocks_before_upload(self, generate, preview):
        with tempfile.TemporaryDirectory() as folder:
            detail = SimpleNamespace(full_reference="040006434488", masked_reference="Póliza", branch_code="40")
            preview.return_value = (detail, (), {})
            generate.return_value = (b"PK workbook", "sura_movilidad.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ())
            upload = Mock()
            with override_settings(COLECTIVOS_PRIVATE_ROOT=folder):
                with self.assertRaises(IndividualAttachmentBlocked):
                    prepare_invitation_attachment(token=TOKEN, insurer_code="SURA", zoho=SimpleNamespace(attachments=SimpleNamespace(upload=upload)))
            upload.assert_not_called()

    @override_settings(
        COLECTIVOS_PRIVATE_ROOT=".", ZOHO_ACTIVE_PROFILE="sandbox",
        COLECTIVOS_ATTACHMENT_PUBLISH_ENABLED=True,
        COLECTIVOS_SANDBOX_ATTACHMENT_WRITE_CONFIRMATION="SANDBOX_ATTACHMENT_WRITE",
    )
    def test_confirmed_failure_is_retriable_and_uncertain_needs_reconciliation(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "individual_quotations" / "missing.enc"
            target.parent.mkdir(parents=True)
            target.write_bytes(encrypt(base64.b64encode(b"PK workbook").decode()).encode())
            attachment = SimpleNamespace(
                stored_path="missing.enc", safe_original_name="invite.xlsx",
                detected_mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                safe_metadata={"owner_type": "policy", "document_type": "invitation_document"}, save=Mock(),
            )
            class ConfirmedFailure(ConnectionError):
                request_sent = False
            with override_settings(COLECTIVOS_PRIVATE_ROOT=folder):
                with self.assertRaises(ConfirmedFailure):
                    publish_attachment(attachment=attachment, module="Polizas", record_id=POLICY_ID, zoho=SimpleNamespace(attachments=SimpleNamespace(upload=Mock(side_effect=ConfirmedFailure()))), feature_flag="COLECTIVOS_ATTACHMENT_PUBLISH_ENABLED")
            self.assertEqual(attachment.safe_metadata["zoho_status"], "failed")
            self.assertFalse(attachment.safe_metadata.get("zoho_attachment_id"))
        self.assertTrue(IndividualAttachmentUncertain.reconciliation_required)
