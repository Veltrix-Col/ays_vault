import csv
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from django.test import TestCase

from email_exceptions.candidate_retrieval import SafeCandidateIndex
from email_exceptions.candidate_retrieval_replay import run_candidate_retrieval_replay
from email_exceptions.correlation import FunctionalReferences, correlate_email_to_case
from email_exceptions.correlation_replay import _correlate_with_precomputed_features
from email_exceptions.models import InboundEmail


class CandidateRetrievalTests(TestCase):
    def email(self, subject, *, conversation_id="", source_mailbox="a@example.test", sender="sender@example.test"):
        return InboundEmail(subject=subject, body_text="", conversation_id=conversation_id, source_mailbox=source_mailbox, from_email=sender)

    def classification(self, *, outcome="FAILURE", scope="CASE", organization="", family="", event_type=""):
        return SimpleNamespace(message_outcome=outcome, scope=scope, organization=organization, family=family, event_type=event_type)

    def case(self, key, *, refs=None, conversations=(), organization="", family="", event_type="", status="OPEN"):
        messages = [SimpleNamespace(email=SimpleNamespace(conversation_id=conversation_id, subject="", body_text="")) for conversation_id in conversations]
        return SimpleNamespace(case_key=key, functional_references=refs, conversation_ids=set(conversations), messages=messages, organization=organization, family=family, event_type=event_type, scope="CASE", status=status)

    def retrieve(self, email, cases):
        index = SafeCandidateIndex()
        for case in cases:
            index.add(case)
        return index.retrieve(email)

    def test_functional_id_finds_candidate(self):
        result = self.retrieve(self.email("Reclamación ABC-123"), [self.case("claim:ABC-123", refs=FunctionalReferences(claim_number="ABC-123"))])
        self.assertEqual(len(result.strong_candidates), 1)

    def test_conversation_id_finds_candidate(self):
        result = self.retrieve(self.email("Seguimiento", conversation_id="conv-1"), [self.case("case:42", conversations=("conv-1",))])
        self.assertEqual(len(result.conversation_candidates), 1)

    def test_unhashable_case_is_accepted_and_deduplicated_across_indexes(self):
        case = self.case("claim:ABC-123", refs=FunctionalReferences(claim_number="ABC-123"), conversations=("conv-1",))
        result = self.retrieve(self.email("Reclamación ABC-123", conversation_id="conv-1"), [case])
        self.assertIs(result.candidates[0], case)
        self.assertEqual(len(result.candidates), 1)

    def test_distinct_cases_with_similar_attributes_remain_distinct(self):
        first = self.case("claim:ABC-123", refs=FunctionalReferences(claim_number="ABC-123"))
        second = self.case("claim:ABC-123", refs=FunctionalReferences(claim_number="ABC-123"))
        result = self.retrieve(self.email("Reclamación ABC-123"), [first, second])
        self.assertEqual(len(result.candidates), 2)
        self.assertEqual({id(case) for case in result.candidates}, {id(first), id(second)})

    def test_functional_conflict_remains_visible_to_engine(self):
        email = self.email("Reclamación ABC-123 contrato CPYS-02/25")
        candidate = self.case("claim:ABC-123|contract:CPYS-01/25", refs=FunctionalReferences(claim_number="ABC-123"))
        result = self.retrieve(email, [candidate])
        decision = _correlate_with_precomputed_features(email, self.classification(), result.candidates, FunctionalReferences(claim_number="ABC-123", contract_number="CPYS-02/25"), {id(candidate): FunctionalReferences(claim_number="ABC-123", contract_number="CPYS-01/25")})
        self.assertFalse(decision.matched)
        self.assertEqual(decision.reason, "conflicting_functional_identifiers")

    def test_multiple_high_candidates_remain_ambiguous(self):
        email = self.email("Seguimiento", conversation_id="conv-1")
        cases = [self.case("case:1", conversations=("conv-1",)), self.case("case:2", conversations=("conv-1",))]
        result = self.retrieve(email, cases)
        decision = correlate_email_to_case(email, self.classification(), result.candidates)
        self.assertEqual((decision.matched, decision.reason), (False, "ambiguous_multiple_candidates"))

    def test_shared_policy_is_not_artificially_high(self):
        email = self.email("Póliza M-600000598")
        cases = [self.case("policy:M-600000598", refs=FunctionalReferences(policy_number="M-600000598")), self.case("policy:M-600000598|claim:ABC-123", refs=FunctionalReferences(policy_number="M-600000598"))]
        result = self.retrieve(email, cases)
        decision = correlate_email_to_case(email, self.classification(), result.candidates)
        self.assertFalse(decision.matched)

    def test_equal_organization_is_not_an_index_filter(self):
        result = self.retrieve(self.email("Consulta"), [self.case("case:1", organization="BEMSA")])
        self.assertFalse(result.candidates)

    def test_equal_family_is_not_an_index_filter(self):
        result = self.retrieve(self.email("Consulta"), [self.case("case:1", family="CARTERA")])
        self.assertFalse(result.candidates)

    def test_equal_event_is_not_an_index_filter(self):
        result = self.retrieve(self.email("Consulta"), [self.case("case:1", event_type="PAGO")])
        self.assertFalse(result.candidates)

    def test_different_mailboxes_can_share_functional_id(self):
        result = self.retrieve(self.email("Reclamación ABC-123", source_mailbox="otro@example.test"), [self.case("claim:ABC-123", refs=FunctionalReferences(claim_number="ABC-123"))])
        self.assertTrue(result.candidates)

    def test_different_senders_can_share_functional_id(self):
        result = self.retrieve(self.email("Reclamación ABC-123", sender="different@example.test"), [self.case("claim:ABC-123", refs=FunctionalReferences(claim_number="ABC-123"))])
        self.assertTrue(result.candidates)

    def test_batch_is_rejected_by_correlation_boundary(self):
        email = self.email("Reclamación ABC-123")
        case = self.case("claim:ABC-123", refs=FunctionalReferences(claim_number="ABC-123"))
        result = self.retrieve(email, [case])
        decision = correlate_email_to_case(email, self.classification(scope="BATCH"), result.candidates)
        self.assertFalse(decision.matched)

    def test_platform_is_rejected_by_correlation_boundary(self):
        email = self.email("Reclamación ABC-123")
        case = self.case("claim:ABC-123", refs=FunctionalReferences(claim_number="ABC-123"))
        result = self.retrieve(email, [case])
        decision = correlate_email_to_case(email, self.classification(scope="PLATFORM"), result.candidates)
        self.assertFalse(decision.matched)

    def test_success_matches_without_resolving_case(self):
        email = self.email("Reclamación ABC-123")
        case = self.case("claim:ABC-123", refs=FunctionalReferences(claim_number="ABC-123"))
        result = self.retrieve(email, [case])
        decision = correlate_email_to_case(email, self.classification(outcome="SUCCESS"), result.candidates)
        self.assertTrue(decision.matched)
        self.assertEqual(case.status, "OPEN")

    def test_no_indexable_evidence_has_no_full_scan_fallback(self):
        result = self.retrieve(self.email("Consulta general"), [self.case("claim:ABC-123", refs=FunctionalReferences(claim_number="ABC-123"))])
        self.assertEqual(result.policy, "NO_INDEXABLE_EVIDENCE")
        self.assertEqual(result.candidates, ())

    def test_candidate_order_is_deterministic(self):
        cases = [self.case("case:2", refs=FunctionalReferences(claim_number="ABC-123")), self.case("case:1", refs=FunctionalReferences(claim_number="ABC-123"))]
        result = self.retrieve(self.email("Reclamación ABC-123"), cases)
        self.assertEqual([case.case_key for case in result.candidates], ["case:1", "case:2"])

    def test_all_reference_types_are_indexed_without_changing_strength(self):
        subjects = {
            "policy_number": "Póliza M-600000598",
            "claim_number": "Reclamación ABC-123",
            "case_number": "Caso ABCD-1234",
            "request_number": "Solicitud ABCD-1234",
            "operation_number": "Operación ABCD-1234",
            "transaction_number": "Transacción ABCD-1234",
            "contract_number": "Contrato ABCD-1234",
            "invoice_number": "Factura ABCD-1234",
            "payment_reference": "Referencia de pago ABCD-1234",
            "period": "Periodo: 2026-08",
        }
        values = {field: "2026-08" if field == "period" else "M-600000598" if field == "policy_number" else "ABC-123" if field == "claim_number" else "ABCD-1234" for field in subjects}
        for field in ("policy_number", "claim_number", "case_number", "request_number", "operation_number", "transaction_number", "contract_number", "invoice_number", "payment_reference", "period"):
            with self.subTest(field=field):
                value = values[field]
                refs = FunctionalReferences(**{field: value})
                result = self.retrieve(self.email(subjects[field]), [self.case("case:1", refs=refs)])
                self.assertTrue(result.functional_candidates)

    def test_b2_replay_is_deterministic_on_non_temporal_results(self):
        fields = ("entry_id", "message_id", "message_id_hash", "conversation_id", "received_at", "from_name", "from_email", "from_domain", "subject_original", "subject_normalized", "body_text", "body_preview", "has_attachments", "attachment_count")
        with TemporaryDirectory() as directory:
            dataset = Path(directory) / "emails.csv"
            with dataset.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerow({"entry_id": "1", "message_id": "m1", "message_id_hash": "h1", "received_at": "2026-01-01T00:00:00+00:00", "subject_original": "Pago duplicado reclamación ABC-123", "body_text": "Requiere conciliación."})
            first = run_candidate_retrieval_replay(dataset, Path(directory) / "first.xlsx")
            second = run_candidate_retrieval_replay(dataset, Path(directory) / "second.xlsx")
        self.assertEqual(first["comparisons"], second["comparisons"])
        for metric in ("B1_CANDIDATE_TOTAL", "B2_CANDIDATE_TOTAL", "DECISION_DIVERGENCES"):
            self.assertEqual(first["metrics"][metric], second["metrics"][metric])
