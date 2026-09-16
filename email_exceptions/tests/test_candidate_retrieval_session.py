from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase

from email_exceptions.candidate_retrieval import SafeCandidateIndex
from email_exceptions.candidate_retrieval_session import CandidateRetrievalSession
from email_exceptions.correlation import FunctionalReferences, correlate_email_to_case
from email_exceptions.models import InboundEmail


class CandidateRetrievalSessionTests(TestCase):
    def email(self, subject, *, conversation_id=""):
        return InboundEmail(
            subject=subject,
            body_text="",
            conversation_id=conversation_id,
            source_mailbox="a@example.test",
            from_email="sender@example.test",
        )

    def case(self, key, *, refs=None, conversations=(), organization=""):
        messages = [
            SimpleNamespace(email=SimpleNamespace(conversation_id=conversation_id))
            for conversation_id in conversations
        ]
        return SimpleNamespace(
            case_key=key,
            functional_references=refs,
            conversation_ids=set(conversations),
            messages=messages,
            organization=organization,
            family="",
            event_type="",
            scope="CASE",
            status="OPEN",
        )

    def test_existing_case_is_available_after_one_initial_build(self):
        index = SafeCandidateIndex()
        case = self.case("claim:ABC-123", refs=FunctionalReferences(claim_number="ABC-123"))
        index.add(case)
        result = index.retrieve(self.email("Reclamación ABC-123"))
        self.assertEqual(result.strong_candidates, (case,))

    def test_case_created_after_build_is_added_incrementally(self):
        index = SafeCandidateIndex()
        case = self.case("claim:ABC-123", refs=FunctionalReferences(claim_number="ABC-123"))
        index.add(case)
        new_case = self.case("claim:XYZ-999", refs=FunctionalReferences(claim_number="XYZ-999"))
        index.add(new_case)
        self.assertEqual(index.retrieve(self.email("Reclamación XYZ-999")).strong_candidates, (new_case,))

    def test_new_message_reference_and_conversation_are_indexed_incrementally(self):
        index = SafeCandidateIndex()
        case = self.case("case:initial")
        index.add(case)
        message = self.email("Reclamación ABC-123", conversation_id="conv-new")
        index.add_message_evidence(case, message)
        by_reference = index.retrieve(self.email("Reclamación ABC-123"))
        by_conversation = index.retrieve(self.email("Seguimiento", conversation_id="conv-new"))
        self.assertEqual(by_reference.strong_candidates, (case,))
        self.assertEqual(by_conversation.conversation_candidates, (case,))

    def test_incremental_evidence_preserves_conflict_without_promoting_candidate(self):
        index = SafeCandidateIndex()
        case = self.case("claim:ABC-123", refs=FunctionalReferences(claim_number="ABC-123"), organization="BEMSA")
        index.add(case)
        index.add_message_evidence(case, self.email("Reclamación ABC-123"))
        result = index.retrieve(
            self.email("Reclamación XYZ-999"),
            functional_references=FunctionalReferences(claim_number="XYZ-999"),
            classification=SimpleNamespace(organization="BEMSA", family="", event_type=""),
        )
        self.assertEqual(result.candidates, ())
        self.assertEqual(result.conflict_candidates, (case,))

    def test_incremental_evidence_preserves_high_ambiguity(self):
        index = SafeCandidateIndex()
        first = self.case("case:one", conversations=("conv-ambiguous",))
        second = self.case("case:two", conversations=("conv-ambiguous",))
        index.add(first)
        index.add(second)
        email = self.email("Seguimiento", conversation_id="conv-ambiguous")
        result = index.retrieve(email)
        decision = correlate_email_to_case(email, SimpleNamespace(message_outcome="FAILURE", scope="CASE"), result.candidates)
        self.assertEqual(len(result.conversation_candidates), 2)
        self.assertEqual(decision.reason, "ambiguous_multiple_candidates")

    def test_session_uses_only_the_requested_database_alias(self):
        with patch("email_exceptions.candidate_retrieval_session.ExceptionCase.objects") as manager:
            CandidateRetrievalSession(using="isolated_test_alias")
        manager.using.assert_called_once_with("isolated_test_alias")

    def test_session_metrics_separate_initial_load_and_incremental_updates(self):
        session = CandidateRetrievalSession()
        case = self.case("claim:ABC-123", refs=FunctionalReferences(claim_number="ABC-123"))
        case.pk = 1
        session.register_case(case)
        session.register_message(case, self.email("Reclamación ABC-123", conversation_id="conv-1"))

        self.assertEqual(session.index_initial_cases, 0)
        self.assertEqual(session.index_cases_loaded_from_db, 0)
        self.assertEqual(session.index_incremental_case_updates, 1)
        self.assertEqual(session.index_incremental_message_updates, 1)
        self.assertEqual(session.index_incremental_updates, 2)
        self.assertGreaterEqual(session.index_initial_build_seconds, 0)
        self.assertGreaterEqual(session.index_incremental_seconds, 0)
