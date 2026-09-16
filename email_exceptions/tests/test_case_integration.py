from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from email_exceptions.models import CaseMessage, EmailException, ExceptionCase, InboundEmail
from email_exceptions.services import ingest_payload


class ExceptionCaseIngestionTests(TestCase):
    mailbox = "comunicaciones@segurosays.com"

    def payload(self, message_id, subject, body="", *, mailbox=None, sender="pagosarrendamientos@bemsa.com.co", conversation_id=""):
        return {
            "source_mailbox": mailbox or self.mailbox,
            "message_id": message_id,
            "subject": subject,
            "body_text": body,
            "conversation_id": conversation_id,
            "from": {"email": sender, "name": "Remitente sintético"},
        }

    def create_case(self, case_key, *, status=ExceptionCase.Status.OPEN, action_type="", conversation_id="", organization=""):
        now = timezone.now()
        case = ExceptionCase.objects.create(
            case_key=case_key,
            organization=organization,
            status=status,
            action_type=action_type,
            opened_at=now,
            last_activity_at=now,
        )
        if conversation_id:
            email = InboundEmail.objects.create(
                source_mailbox=self.mailbox,
                external_message_id=f"seed-{case_key}",
                conversation_id=conversation_id,
                received_at=now,
                subject="Mensaje inicial",
            )
            CaseMessage.objects.create(
                case=case,
                email=email,
                role=CaseMessage.Role.OPENING,
                correlation_method=CaseMessage.CorrelationMethod.NEW_CASE,
                correlation_confidence=CaseMessage.CorrelationConfidence.LOW,
                correlation_reason="Caso inicial sintético",
            )
        return case

    def test_failure_without_candidate_creates_open_case_and_exception(self):
        email, exception, created = ingest_payload(self.payload("new-failure", "Pago no aplicado"))

        self.assertTrue(created)
        self.assertEqual(ExceptionCase.objects.count(), 1)
        case = ExceptionCase.objects.get()
        self.assertEqual(case.status, ExceptionCase.Status.OPEN)
        self.assertEqual(case.action_type, "RECONCILE_PAYMENT")
        self.assertEqual(exception.case_id, case.pk)
        link = CaseMessage.objects.get()
        self.assertEqual(link.role, CaseMessage.Role.OPENING)
        self.assertEqual(link.correlation_method, CaseMessage.CorrelationMethod.NEW_CASE)
        self.assertEqual(link.correlation_confidence, CaseMessage.CorrelationConfidence.LOW)
        self.assertEqual(link.correlation_reason, "no_sufficient_correlation_evidence")
        self.assertEqual(email.classification_status, InboundEmail.CLASSIFICATION_EXCEPTION)

    def test_pending_action_without_candidate_creates_case(self):
        _, exception, _ = ingest_payload(self.payload("new-pending", "Pago pendiente de legalización"))

        self.assertIsNotNone(exception.case_id)
        self.assertEqual(ExceptionCase.objects.get().action_type, "LEGALIZE_PAYMENT")
        self.assertEqual(CaseMessage.objects.get().role, CaseMessage.Role.OPENING)

    def test_functional_high_reuses_case_and_creates_follow_up_exception(self):
        case = self.create_case("claim:ABC-123", action_type="REVIEW")

        _, exception, _ = ingest_payload(self.payload("functional-follow-up", "Pago no aplicado Reclamación ABC-123"))

        self.assertEqual(ExceptionCase.objects.count(), 1)
        case.refresh_from_db()
        self.assertEqual(exception.case_id, case.pk)
        link = CaseMessage.objects.get(case=case)
        self.assertEqual(link.role, CaseMessage.Role.FOLLOW_UP)
        self.assertEqual(link.correlation_method, CaseMessage.CorrelationMethod.FUNCTIONAL_ID)
        self.assertEqual(link.correlation_confidence, CaseMessage.CorrelationConfidence.HIGH)
        self.assertEqual(link.correlation_reason, "exact_functional_identifier")
        self.assertEqual(case.action_type, "REVIEW")

    def test_conversation_high_reuses_case(self):
        case = self.create_case("case:conversation-1", conversation_id="conv-1")

        _, exception, _ = ingest_payload(self.payload("conversation-follow-up", "Pago no aplicado", conversation_id="conv-1"))

        self.assertEqual(exception.case_id, case.pk)
        link = CaseMessage.objects.get(case=case, email=exception.primary_email)
        self.assertEqual(link.role, CaseMessage.Role.FOLLOW_UP)
        self.assertEqual(link.correlation_method, CaseMessage.CorrelationMethod.CONVERSATION)
        self.assertEqual(link.correlation_confidence, CaseMessage.CorrelationConfidence.HIGH)

    def test_success_high_is_resolution_without_exception_or_reopen(self):
        case = self.create_case("claim:ABC-123", status=ExceptionCase.Status.RESOLVED)

        email, exception, _ = ingest_payload(self.payload("success-resolution", "Reclamación ABC-123 - Pago aplicado"))

        self.assertIsNone(exception)
        self.assertEqual(EmailException.objects.count(), 0)
        case.refresh_from_db()
        self.assertEqual(case.status, ExceptionCase.Status.RESOLVED)
        link = CaseMessage.objects.get(case=case, email=email)
        self.assertEqual(link.role, CaseMessage.Role.RESOLUTION)
        self.assertEqual(link.correlation_method, CaseMessage.CorrelationMethod.FUNCTIONAL_ID)
        self.assertEqual(link.correlation_confidence, CaseMessage.CorrelationConfidence.HIGH)

    def test_informational_high_is_context_and_without_high_is_not_linked(self):
        case = self.create_case("claim:ABC-123")
        email, exception, _ = ingest_payload(self.payload("informational-context", "Documentos adjuntos para su información Reclamación ABC-123"))

        self.assertIsNone(exception)
        link = CaseMessage.objects.get(case=case, email=email)
        self.assertEqual(link.role, CaseMessage.Role.CONTEXT)
        self.assertEqual(link.correlation_confidence, CaseMessage.CorrelationConfidence.HIGH)

        no_match_email, _, _ = ingest_payload(self.payload("informational-no-match", "Documentos adjuntos para su información"))
        self.assertFalse(CaseMessage.objects.filter(email=no_match_email).exists())
        self.assertEqual(no_match_email.classification_status, InboundEmail.CLASSIFICATION_NO_MATCH)

    def test_unclear_noise_batch_and_platform_do_not_create_individual_cases(self):
        subjects = (
            ("unclear", "Consulta general"),
            ("noise", "Reunión de seguimiento"),
            ("batch", "Listado de 120 pólizas con inconsistencia PBS"),
            ("platform", "Contingencia plataforma: servicio de emisión no disponible"),
        )
        for message_id, subject in subjects:
            with self.subTest(message_id=message_id):
                email, exception, _ = ingest_payload(self.payload(message_id, subject))
                self.assertIsNone(exception)
                self.assertFalse(CaseMessage.objects.filter(email=email).exists())
        self.assertEqual(ExceptionCase.objects.count(), 0)
        self.assertEqual(EmailException.objects.count(), 0)

    def test_conflict_creates_separate_case_with_traceable_reason(self):
        existing = self.create_case("claim:OTHER-999", organization="BEMSA")

        _, exception, _ = ingest_payload(self.payload("functional-conflict", "Pago no aplicado Reclamación ABC-123"))

        self.assertEqual(ExceptionCase.objects.count(), 2)
        self.assertNotEqual(exception.case_id, existing.pk)
        link = CaseMessage.objects.get(email=exception.primary_email)
        self.assertEqual(link.correlation_method, CaseMessage.CorrelationMethod.NEW_CASE)
        self.assertEqual(link.correlation_reason, "conflicting_functional_identifiers")

    def test_ambiguity_creates_separate_case_without_colliding_with_existing_key(self):
        first = self.create_case("claim:ABC-123")
        second = self.create_case("claim:ABC-123|contract:CON-999")

        _, exception, _ = ingest_payload(self.payload("ambiguous-new-case", "Pago no aplicado Reclamación ABC-123"))

        self.assertEqual(ExceptionCase.objects.count(), 3)
        self.assertNotIn(exception.case_id, {first.pk, second.pk})
        new_case = exception.case
        self.assertIn("claim_number:ABC-123", new_case.case_key)
        self.assertIn("|message:", new_case.case_key)
        link = CaseMessage.objects.get(email=exception.primary_email)
        self.assertEqual(link.correlation_reason, "ambiguous_multiple_candidates")

    def test_same_inbound_is_idempotent_for_case_message_and_exception(self):
        payload = self.payload("idempotent-case", "Pago no aplicado")

        first = ingest_payload(payload)
        second = ingest_payload(payload)

        self.assertTrue(first[2])
        self.assertFalse(second[2])
        self.assertEqual(InboundEmail.objects.count(), 1)
        self.assertEqual(ExceptionCase.objects.count(), 1)
        self.assertEqual(CaseMessage.objects.count(), 1)
        self.assertEqual(EmailException.objects.count(), 1)
        self.assertEqual(first[1].pk, second[1].pk)

    def test_functional_id_works_across_mailbox_and_sender(self):
        case = self.create_case("claim:ABC-123")

        _, exception, _ = ingest_payload(self.payload(
            "different-origin",
            "Pago no aplicado Reclamación ABC-123",
            mailbox="aysltda@asesorsura.com",
            sender="otro@different.example",
        ))

        self.assertEqual(exception.case_id, case.pk)
        self.assertEqual(CaseMessage.objects.get(email=exception.primary_email).correlation_method, "FUNCTIONAL_ID")

    def test_shared_policy_reference_does_not_merge_cases(self):
        first = self.create_case("claim:ABC-123")
        second = self.create_case("claim:XYZ-999")

        _, exception, _ = ingest_payload(self.payload("policy-only", "Pago no aplicado Póliza M-600000598"))

        self.assertEqual(ExceptionCase.objects.count(), 3)
        self.assertNotIn(exception.case_id, {first.pk, second.pk})

    def test_persistence_failure_rolls_back_inbound_case_and_exception(self):
        with patch("email_exceptions.services.CaseMessage.objects.get_or_create", side_effect=RuntimeError("synthetic persistence failure")):
            with self.assertRaises(RuntimeError):
                ingest_payload(self.payload("atomic-failure", "Pago no aplicado"))

        self.assertEqual(InboundEmail.objects.count(), 0)
        self.assertEqual(ExceptionCase.objects.count(), 0)
        self.assertEqual(CaseMessage.objects.count(), 0)
        self.assertEqual(EmailException.objects.count(), 0)
