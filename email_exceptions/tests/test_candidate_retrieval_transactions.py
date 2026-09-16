from unittest.mock import patch

from django.db import transaction
from django.test import TransactionTestCase

from email_exceptions.candidate_retrieval_session import CandidateRetrievalSession
from email_exceptions.models import CaseMessage, EmailException, ExceptionCase, InboundEmail
from email_exceptions.services import _schedule_candidate_index_update, ingest_payload


class CandidateRetrievalTransactionTests(TransactionTestCase):
    reset_sequences = True
    mailbox = "comunicaciones@segurosays.com"

    def payload(self, message_id, *, subject="Pago no aplicado Reclamación ABC-123", conversation_id="conv-abc"):
        return {
            "source_mailbox": self.mailbox,
            "message_id": message_id,
            "subject": subject,
            "body_text": "Requiere conciliación del pago.",
            "conversation_id": conversation_id,
            "from": {"email": "synthetic@example.test", "name": "Synthetic"},
        }

    def test_commit_publishes_case_and_message_evidence_after_commit(self):
        session = CandidateRetrievalSession(using="default")

        email, exception, created = ingest_payload(self.payload("commit-a"), retrieval_context=session)

        self.assertTrue(created)
        self.assertIsNotNone(exception)
        self.assertEqual(session.index_incremental_case_updates, 1)
        self.assertEqual(session.index_incremental_message_updates, 1)
        result = session.retrieve(email)
        self.assertEqual(len(result.strong_candidates), 1)
        self.assertEqual(result.strong_candidates[0].pk, exception.case_id)

    def test_outer_rollback_discards_index_update_and_database_rows(self):
        session = CandidateRetrievalSession(using="default")
        with self.assertRaisesRegex(RuntimeError, "rollback-after-ingest"):
            with transaction.atomic(using="default"):
                ingest_payload(self.payload("rollback-a"), retrieval_context=session)
                self.assertEqual(session.index_incremental_updates, 0)
                raise RuntimeError("rollback-after-ingest")

        self.assertEqual(InboundEmail.objects.count(), 0)
        self.assertEqual(ExceptionCase.objects.count(), 0)
        self.assertEqual(CaseMessage.objects.count(), 0)
        self.assertEqual(EmailException.objects.count(), 0)
        self.assertEqual(session.index_incremental_updates, 0)
        self.assertEqual(session.index._cases_by_token, {})
        self.assertEqual(session.index.functional, {})
        self.assertEqual(session.index.conversations, {})

    def test_message_after_rollback_cannot_match_ghost_case(self):
        session = CandidateRetrievalSession(using="default")
        with self.assertRaises(RuntimeError):
            with transaction.atomic(using="default"):
                ingest_payload(self.payload("rolled-back-a"), retrieval_context=session)
                raise RuntimeError("rollback")

        email_b, exception_b, _ = ingest_payload(self.payload("committed-b"), retrieval_context=session)
        self.assertIsNotNone(exception_b)
        self.assertEqual(ExceptionCase.objects.count(), 1)
        self.assertEqual(CaseMessage.objects.count(), 1)
        link = CaseMessage.objects.get(email=email_b)
        self.assertEqual(link.correlation_method, CaseMessage.CorrelationMethod.NEW_CASE)
        self.assertEqual(session.index_incremental_case_updates, 1)
        self.assertEqual(session.index_incremental_message_updates, 1)

    def test_nested_savepoint_rollback_discards_only_its_index_callback(self):
        session = CandidateRetrievalSession(using="default")
        with transaction.atomic(using="default"):
            try:
                with transaction.atomic(using="default"):
                    ingest_payload(self.payload("savepoint-a"), retrieval_context=session)
                    raise RuntimeError("rollback-savepoint")
            except RuntimeError:
                pass
            self.assertEqual(session.index_incremental_updates, 0)
            self.assertFalse(InboundEmail.objects.exists())
            email, exception, _ = ingest_payload(self.payload("savepoint-b"), retrieval_context=session)
            self.assertIsNotNone(exception)
            self.assertEqual(CaseMessage.objects.get(email=email).correlation_method, CaseMessage.CorrelationMethod.NEW_CASE)
        self.assertEqual(ExceptionCase.objects.count(), 1)
        self.assertEqual(session.index_incremental_case_updates, 1)
        self.assertEqual(session.index_incremental_message_updates, 1)

    def test_on_commit_is_bound_to_requested_database_alias(self):
        session = CandidateRetrievalSession(using="default")
        case = ExceptionCase(case_key="case:alias", pk=123)
        email = InboundEmail(pk=456, source_mailbox=self.mailbox, external_message_id="alias-message")
        with patch("email_exceptions.services.transaction.on_commit") as on_commit:
            _schedule_candidate_index_update(session, case, email, case_created=True, using="isolated_test_alias")
        on_commit.assert_called_once()
        self.assertEqual(on_commit.call_args.kwargs["using"], "isolated_test_alias")

    def test_idempotent_reprocessing_does_not_register_duplicate_evidence(self):
        session = CandidateRetrievalSession(using="default")
        payload = self.payload("idempotent-a")
        email, _, _ = ingest_payload(payload, retrieval_context=session)
        after_first = (session.index_incremental_case_updates, session.index_incremental_message_updates)
        second_email, _, created = ingest_payload(payload, retrieval_context=session)

        self.assertFalse(created)
        self.assertEqual(email.pk, second_email.pk)
        self.assertEqual((session.index_incremental_case_updates, session.index_incremental_message_updates), after_first)
        self.assertEqual(InboundEmail.objects.count(), 1)
        self.assertEqual(ExceptionCase.objects.count(), 1)
        self.assertEqual(CaseMessage.objects.count(), 1)
        self.assertEqual(EmailException.objects.count(), 1)
