from types import SimpleNamespace

from django.test import TestCase

from email_exceptions.correlation import (
    CorrelationDecision,
    CorrelationConfidence,
    CorrelationMethod,
    FunctionalReferences,
    correlate_email_to_case,
    extract_functional_references,
    normalize_reference,
)
from email_exceptions.models import CaseMessage, EmailException, ExceptionCase, InboundEmail
from email_exceptions.services import Classification


class CorrelationEngineTests(TestCase):
    def email(self, subject, body="", conversation_id="", sender="sender@example.test"):
        return InboundEmail(subject=subject, body_text=body, conversation_id=conversation_id, from_email=sender)

    def classification(self, **values):
        values.setdefault("scope", "CASE")
        return Classification(**values)

    def case(self, key, *, organization="", family="", event_type="", messages=(), status="OPEN"):
        return SimpleNamespace(case_key=key, organization=organization, family=family, event_type=event_type, scope="CASE", status=status, messages=messages)

    def link(self, email):
        return SimpleNamespace(email=email)

    def test_functional_references_are_optional_and_safely_normalized(self):
        refs = extract_functional_references(self.email("Reclamación ABC-123, póliza M-600000598", "Periodo: 2026-08"))
        self.assertEqual(refs.claim_number, "ABC-123")
        self.assertEqual(refs.policy_number, "M-600000598")
        self.assertEqual(refs.period, "2026-08")
        self.assertEqual(normalize_reference(" ABC‑123 "), "ABC-123")
        self.assertEqual(extract_functional_references(self.email("Año 2026 teléfono 123")), FunctionalReferences())

    def test_same_claim_is_high_functional_match(self):
        decision = correlate_email_to_case(self.email("Reclamación ABC-123"), self.classification(message_outcome="FAILURE"), [self.case("claim:ABC-123")])
        self.assertEqual((decision.matched, decision.method, decision.confidence), (True, "FUNCTIONAL_ID", "HIGH"))

    def test_different_claim_blocks_match(self):
        decision = correlate_email_to_case(self.email("Reclamación ABC-123"), self.classification(), [self.case("claim:XYZ-999")])
        self.assertFalse(decision.matched)
        self.assertEqual(decision.reason, "conflicting_functional_identifiers")

    def test_conversation_matches_without_functional_conflict(self):
        candidate = self.case("case:42", messages=(self.link(self.email("Seguimiento", conversation_id="conv-1")),))
        decision = correlate_email_to_case(self.email("Seguimiento", conversation_id="conv-1"), self.classification(), [candidate])
        self.assertEqual((decision.matched, decision.method, decision.confidence), (True, "CONVERSATION", "HIGH"))

    def test_conversation_with_different_claim_does_not_merge(self):
        candidate = self.case("claim:ABC-123", messages=(self.link(self.email("Caso", conversation_id="conv-1")),))
        decision = correlate_email_to_case(self.email("Reclamación XYZ-999", conversation_id="conv-1"), self.classification(), [candidate])
        self.assertFalse(decision.matched)
        self.assertEqual(decision.reason, "conflicting_functional_identifiers")

    def test_weak_single_signals_do_not_match(self):
        cases = (
            (self.email("Asunto idéntico"), self.case("case:42")),
            (self.email("Otro", sender="same@example.test"), self.case("case:42")),
            (self.email("Otro"), self.case("case:42", organization="BEMSA")),
            (self.email("Otro"), self.case("case:42", family="PAGO")),
        )
        for email, candidate in cases:
            with self.subTest(email=email.subject):
                decision = correlate_email_to_case(email, self.classification(organization="BEMSA", family="PAGO"), [candidate])
                self.assertFalse(decision.matched)

    def test_policy_only_is_not_high_but_structured_context_is_audit_suggestion(self):
        email = self.email("Póliza M-600000598 periodo 2026-08")
        weak = correlate_email_to_case(email, self.classification(), [self.case("policy:M-600000598")])
        self.assertFalse(weak.matched)
        self.assertEqual(weak.confidence, "LOW")
        candidate = self.case("policy:M-600000598|period:2026-08", organization="BEMSA", family="PAGO", event_type="PAGO_DOBLE")
        structured = correlate_email_to_case(email, self.classification(organization="BEMSA", family="PAGO", event_type="PAGO_DOBLE"), [candidate])
        self.assertFalse(structured.matched)
        self.assertEqual((structured.method, structured.confidence), ("STRUCTURED", "MEDIUM"))

    def test_policy_plus_different_claim_does_not_match(self):
        decision = correlate_email_to_case(self.email("Póliza M-600000598 Reclamación ABC-123"), self.classification(), [self.case("policy:M-600000598|claim:XYZ-999")])
        self.assertFalse(decision.matched)

    def test_success_can_match_open_or_resolved_case_without_mutating_it(self):
        for status in ("OPEN", "RESOLVED"):
            candidate = self.case("claim:ABC-123", status=status)
            decision = correlate_email_to_case(self.email("Reclamación ABC-123"), self.classification(message_outcome="SUCCESS"), [candidate])
            self.assertTrue(decision.matched)
            self.assertEqual(decision.method, "FUNCTIONAL_ID")
            self.assertEqual(candidate.status, status)

    def test_informational_unclear_batch_and_platform_are_conservative(self):
        for outcome in ("INFORMATIONAL", "UNCLEAR"):
            decision = correlate_email_to_case(self.email("Consulta general"), self.classification(message_outcome=outcome), [self.case("claim:ABC-123")])
            self.assertFalse(decision.matched)
        for scope in ("BATCH", "PLATFORM"):
            decision = correlate_email_to_case(self.email("Reclamación ABC-123"), self.classification(scope=scope), [self.case("claim:ABC-123")])
            self.assertFalse(decision.matched)
            self.assertIn(scope, decision.conflicts)

    def test_one_exact_candidate_is_selected_but_equivalent_high_matches_are_ambiguous(self):
        exact = self.case("claim:ABC-123")
        other = self.case("claim:XYZ-999")
        decision = correlate_email_to_case(self.email("Reclamación ABC-123"), self.classification(), [exact, other])
        self.assertTrue(decision.matched)
        self.assertIs(decision.case, exact)
        both = correlate_email_to_case(self.email("Conversación", conversation_id="conv-1"), self.classification(), [self.case("case:1", messages=(self.link(self.email("x", conversation_id="conv-1")),)), self.case("case:2", messages=(self.link(self.email("y", conversation_id="conv-1")),))])
        self.assertFalse(both.matched)
        self.assertEqual(both.reason, "ambiguous_multiple_candidates")

    def test_trivial_numbers_dates_money_subject_and_sender_do_not_correlate(self):
        candidate = self.case("case:2026")
        for subject in ("Reunión 2026", "Total $500.00", "2026-08-01"):
            decision = correlate_email_to_case(self.email(subject, sender="same@example.test"), self.classification(), [candidate])
            self.assertFalse(decision.matched)

    def test_current_text_has_priority_over_quoted_historical_text(self):
        email = self.email("Reclamación ABC-123", "La reclamación ABC-123 continúa en revisión.\n-----Mensaje anterior-----\nReclamación XYZ-999")
        refs = extract_functional_references(email)
        self.assertEqual(refs.claim_number, "ABC-123")

    def test_correlation_is_pure_and_does_not_create_or_modify_records(self):
        inbound = InboundEmail.objects.create(source_mailbox="comunicaciones@segurosays.com", external_message_id="correlation-pure-1", received_at="2026-09-14T10:00:00Z", subject="Reclamación ABC-123")
        case = ExceptionCase.objects.create(case_key="claim:ABC-123", opened_at="2026-09-14T10:00:00Z", last_activity_at="2026-09-14T10:00:00Z")
        before = (ExceptionCase.objects.count(), CaseMessage.objects.count(), EmailException.objects.count(), case.status, case.updated_at)
        decision = correlate_email_to_case(inbound, self.classification(), [case])
        after = (ExceptionCase.objects.count(), CaseMessage.objects.count(), EmailException.objects.count(), case.status, case.updated_at)
        self.assertTrue(decision.matched)
        self.assertEqual(before, after)


class CorrelationContractTests(TestCase):
    def test_contract_constants_are_small_and_explicit(self):
        self.assertEqual(tuple(item.value for item in CorrelationMethod), ("FUNCTIONAL_ID", "CONVERSATION", "STRUCTURED", "MANUAL", "BACKFILL", "NEW_CASE"))
        self.assertEqual(tuple(item.value for item in CorrelationConfidence), ("HIGH", "MEDIUM", "LOW"))
        self.assertEqual(CorrelationDecision.__dataclass_params__.frozen, True)
