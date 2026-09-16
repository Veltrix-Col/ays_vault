import csv
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from django.test import TestCase

from email_exceptions.candidate_retrieval import SafeCandidateIndex
from email_exceptions.candidate_retrieval_replay import B2_DECISION_FIELDS, run_candidate_retrieval_replay
from email_exceptions.correlation import FunctionalReferences, correlate_email_to_case


class CandidateRetrievalB21Tests(TestCase):
    def email(self, subject, *, conversation_id=""):
        return SimpleNamespace(subject=subject, body_text="", conversation_id=conversation_id)

    def classification(self, **values):
        defaults = {
            "message_outcome": "FAILURE",
            "action_required": True,
            "scope": "CASE",
            "organization": "",
            "family": "",
            "event_type": "",
        }
        defaults.update(values)
        return SimpleNamespace(**defaults)

    def case(self, key, *, refs=None, conversations=(), organization="", family="", event_type=""):
        messages = [
            SimpleNamespace(email=SimpleNamespace(conversation_id=value))
            for value in conversations
        ]
        return SimpleNamespace(
            case_key=key,
            functional_references=refs or FunctionalReferences(),
            conversation_ids=set(conversations),
            messages=messages,
            organization=organization,
            family=family,
            event_type=event_type,
            scope="CASE",
        )

    def retrieve(self, email, cases, classification):
        index = SafeCandidateIndex()
        for case in cases:
            index.add(case)
        return index.retrieve(email, classification=classification)

    def test_functional_and_conversation_candidates_remain_decision_candidates(self):
        functional = self.case("claim:ABC-123", refs=FunctionalReferences(claim_number="ABC-123"))
        conversation = self.case("case:conversation", conversations=("conv-1",))

        functional_result = self.retrieve(
            self.email("Reclamación ABC-123"),
            [functional],
            self.classification(),
        )
        conversation_result = self.retrieve(
            self.email("Seguimiento", conversation_id="conv-1"),
            [conversation],
            self.classification(),
        )

        self.assertEqual(functional_result.functional_candidates, (functional,))
        self.assertEqual(conversation_result.conversation_candidates, (conversation,))
        self.assertFalse(functional_result.conflict_candidates)
        self.assertFalse(conversation_result.conflict_candidates)

    def test_conflict_evidence_is_separate_and_preserves_canonical_conflict(self):
        email = self.email("Reclamación ABC-123")
        classification = self.classification(organization="BEMSA")
        conflicting = self.case("claim:OTHER-999", organization="BEMSA")
        result = self.retrieve(email, [conflicting], classification)

        self.assertEqual(result.candidates, ())
        self.assertEqual(result.conflict_candidates, (conflicting,))
        decision = correlate_email_to_case(
            email,
            classification,
            result.candidates + result.conflict_candidates,
        )
        self.assertFalse(decision.matched)
        self.assertEqual(decision.reason, "conflicting_functional_identifiers")

    def test_conflict_evidence_blocks_conversation_match_without_broad_context_scan(self):
        email = self.email("Reclamación ABC-123", conversation_id="conv-1")
        classification = self.classification(organization="BEMSA")
        conflicting = self.case(
            "claim:OTHER-999",
            conversations=("conv-1",),
            organization="BEMSA",
        )
        result = self.retrieve(email, [conflicting], classification)

        self.assertEqual(result.candidates, (conflicting,))
        self.assertEqual(result.conflict_candidates, (conflicting,))
        decision = correlate_email_to_case(
            email,
            classification,
            result.candidates + result.conflict_candidates,
        )
        self.assertFalse(decision.matched)
        self.assertEqual(decision.reason, "conflicting_functional_identifiers")

    def test_conflict_evidence_preserves_functional_conflict_without_restoring_context_candidates(self):
        email = self.email("Reclamación ABC-123")
        classification = self.classification(organization="BEMSA", family="CARTERA", event_type="PAGO")
        conflicting = self.case(
            "claim:OTHER-999",
            organization="BEMSA",
            family="CARTERA",
            event_type="PAGO",
        )
        unrelated = self.case(
            "claim:UNRELATED-888",
            organization="BEMSA",
            family="CARTERA",
            event_type="PAGO",
        )
        result = self.retrieve(email, [unrelated, conflicting], classification)

        self.assertEqual(result.candidates, ())
        # B.2.1 retrieves one deterministic representative for the claim
        # conflict. Returning every different claim would recreate B.1's
        # broad context candidate set rather than provide conflict evidence.
        self.assertEqual([case.case_key for case in result.conflict_candidates], ["claim:OTHER-999"])
        decision = correlate_email_to_case(
            email,
            classification,
            result.candidates + result.conflict_candidates,
        )
        self.assertFalse(decision.matched)
        self.assertEqual(decision.reason, "conflicting_functional_identifiers")

    def test_weak_reference_does_not_become_strong_or_conflict_evidence(self):
        email = self.email("Póliza M-600000598")
        classification = self.classification(organization="BEMSA")
        weak = self.case(
            "policy:M-600000598",
            refs=FunctionalReferences(policy_number="M-600000598"),
            organization="BEMSA",
        )
        result = self.retrieve(email, [weak], classification)

        self.assertEqual(result.policy, "WEAK_FUNCTIONAL_ONLY")
        self.assertFalse(result.strong_candidates)
        self.assertFalse(result.conflict_candidates)

    def test_no_indexable_evidence_has_no_full_scan_or_conflict_candidates(self):
        email = self.email("Consulta general")
        classification = self.classification(organization="BEMSA", family="CARTERA", event_type="PAGO")
        cases = [self.case("claim:ABC-123", organization="BEMSA")]
        result = self.retrieve(email, cases, classification)

        self.assertEqual(result.policy, "NO_INDEXABLE_EVIDENCE")
        self.assertEqual(result.candidates, ())
        self.assertEqual(result.conflict_candidates, ())

    def test_b2_in_memory_decision_contract_contains_all_b21_fields(self):
        fields = (
            "entry_id", "message_id", "message_id_hash", "conversation_id",
            "received_at", "from_name", "from_email", "from_domain",
            "subject_original", "subject_normalized", "body_text", "body_preview",
            "has_attachments", "attachment_count",
        )
        with TemporaryDirectory() as directory:
            dataset = Path(directory) / "emails.csv"
            with dataset.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerow({
                    "entry_id": "1", "message_id": "m1", "message_id_hash": "h1",
                    "received_at": "2026-01-01T00:00:00+00:00",
                    "subject_original": "Consulta general", "body_text": "Sin referencias.",
                })
            result = run_candidate_retrieval_replay(dataset, Path(directory) / "b2.xlsx")

        self.assertTrue(result["decisions"])
        self.assertTrue(B2_DECISION_FIELDS.issubset(result["decisions"][0]))
