from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from .models import CaseMessage, ExceptionCase, InboundEmail
from .services import _current_and_historical_text


CorrelationMethod = CaseMessage.CorrelationMethod
CorrelationConfidence = CaseMessage.CorrelationConfidence


FUNCTIONAL_FIELDS = (
    "policy_number", "claim_number", "case_number", "request_number",
    "operation_number", "transaction_number", "contract_number",
    "invoice_number", "payment_reference", "period",
)
STRONG_FIELDS = tuple(field for field in FUNCTIONAL_FIELDS if field not in {"policy_number", "period"})
CASE_KEY_LABELS = {
    "poliza": "policy_number", "policy": "policy_number", "policy_number": "policy_number",
    "reclamacion": "claim_number", "claim": "claim_number", "claim_number": "claim_number",
    "caso": "case_number", "case": "case_number", "case_number": "case_number",
    "solicitud": "request_number", "request": "request_number", "request_number": "request_number",
    "operacion": "operation_number", "operation": "operation_number", "operation_number": "operation_number",
    "transaccion": "transaction_number", "transaction": "transaction_number", "transaction_number": "transaction_number",
    "contrato": "contract_number", "contract": "contract_number", "contract_number": "contract_number",
    "factura": "invoice_number", "invoice": "invoice_number", "invoice_number": "invoice_number",
    "pago": "payment_reference", "payment": "payment_reference", "payment_reference": "payment_reference",
    "periodo": "period", "period": "period",
}


@dataclass(frozen=True)
class FunctionalReferences:
    policy_number: str = ""
    claim_number: str = ""
    case_number: str = ""
    request_number: str = ""
    operation_number: str = ""
    transaction_number: str = ""
    contract_number: str = ""
    invoice_number: str = ""
    payment_reference: str = ""
    period: str = ""

    def as_dict(self):
        return {field: getattr(self, field) for field in FUNCTIONAL_FIELDS if getattr(self, field)}


@dataclass(frozen=True)
class CorrelationDecision:
    matched: bool
    case: ExceptionCase | None
    method: str
    confidence: str | None
    reason: str
    evidence: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()


def normalize_reference(value: str) -> str:
    """Normalize safely without turning distinct identifiers into one value."""
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.strip().upper()
    value = re.sub(r"\s+", "", value)
    return re.sub(r"[‐‑‒–—−]", "-", value)


def _candidate_value(value: str) -> str:
    value = normalize_reference(value)
    if len(value) < 4 or not any(char.isdigit() for char in value):
        return ""
    return value


def _extract(text: str, patterns: tuple[str, ...]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = _candidate_value(match.group(1))
            if value:
                return value
    return ""


def extract_functional_references(email: InboundEmail) -> FunctionalReferences:
    """Extract labelled, non-trivial references from subject and current body."""
    current_text, _ = _current_and_historical_text(email.subject, email.body_text)
    text = current_text
    patterns = {
        "policy_number": (r"(?:poliza|p[oó]liza)\s*(?:numero|nro|no|#|:|-)?\s*([a-z0-9][a-z0-9/-]{3,39})",),
        "claim_number": (r"(?:reclamacion|reclamo|siniestro)\s*(?:numero|nro|no|#|:|-)?\s*([a-z0-9][a-z0-9/-]{3,39})",),
        "case_number": (r"(?:caso|case)\s*(?:numero|nro|no|#|:|-)?\s*([a-z0-9][a-z0-9/-]{3,39})",),
        "request_number": (r"(?:solicitud|request)\s*(?:numero|nro|no|#|:|-)?\s*([a-z0-9][a-z0-9/-]{3,39})",),
        "operation_number": (r"(?:operacion|operation)\s*(?:numero|nro|no|#|:|-)?\s*([a-z0-9][a-z0-9/-]{3,39})",),
        "transaction_number": (r"(?:transaccion|transaction)\s*(?:numero|nro|no|#|:|-)?\s*([a-z0-9][a-z0-9/-]{3,39})",),
        "contract_number": (r"(?:contrato|contract)\s*(?:numero|nro|no|#|:|-)?\s*([a-z0-9][a-z0-9/-]{3,39})",),
        "invoice_number": (r"(?:factura|invoice)\s*(?:numero|nro|no|#|:|-)?\s*([a-z0-9][a-z0-9/-]{3,39})",),
        "payment_reference": (r"(?:referencia\s+de\s+pago|referencia\s+pago|payment\s+reference)\s*(?:numero|nro|no|#|:|-)?\s*([a-z0-9][a-z0-9/-]{3,39})",),
        "period": (r"periodo\s*(?:de|:|-)?\s*([a-z0-9][a-z0-9/-]{3,19})",),
    }
    return FunctionalReferences(**{field: _extract(text, value) for field, value in patterns.items()})


def _case_references(case: ExceptionCase) -> FunctionalReferences:
    """Read only explicitly labelled references from the opaque case key."""
    values = {}
    for part in re.split(r"[|;,]", str(getattr(case, "case_key", ""))):
        match = re.match(r"\s*([a-z_]+)\s*[:=]\s*([^: =]+)\s*$", part, flags=re.IGNORECASE)
        if match:
            field = CASE_KEY_LABELS.get(normalize_reference(match.group(1)).lower())
            value = _candidate_value(match.group(2))
            if field and value:
                values[field] = value
    return FunctionalReferences(**values)


def _case_messages(case):
    relation = getattr(case, "messages", ())
    if hasattr(relation, "all"):
        relation = relation.all()
    return tuple(relation or ())


def _case_conversations(case):
    return {str(getattr(getattr(link, "email", None), "conversation_id", "") or "") for link in _case_messages(case) if getattr(getattr(link, "email", None), "conversation_id", "")}


def _case_context(case):
    return {
        "organization": str(getattr(case, "organization", "") or ""),
        "family": str(getattr(case, "family", "") or ""),
        "event_type": str(getattr(case, "event_type", "") or ""),
        "scope": str(getattr(case, "scope", "") or ""),
    }


def find_candidate_cases(cases, *, email=None, classification=None):
    """Candidate retrieval boundary; this phase accepts an explicit collection only."""
    return tuple(case for case in cases if getattr(case, "scope", ExceptionCase.Scope.CASE) not in {ExceptionCase.Scope.BATCH, ExceptionCase.Scope.PLATFORM})


def evaluate_candidate(email, classification, case) -> CorrelationDecision:
    if classification.scope in {"BATCH", "PLATFORM"}:
        return CorrelationDecision(False, None, "NEW_CASE", None, "scope_not_individual", conflicts=(classification.scope,))
    refs = extract_functional_references(email)
    case_refs = _case_references(case)
    evidence, conflicts = [], []
    for field in STRONG_FIELDS:
        current_value, candidate_value = getattr(refs, field), getattr(case_refs, field)
        if current_value and candidate_value and current_value != candidate_value:
            conflicts.append(f"{field}:{current_value}!={candidate_value}")
        elif current_value and candidate_value and current_value == candidate_value:
            evidence.append(f"{field}={current_value}")
    if conflicts:
        return CorrelationDecision(False, None, "NEW_CASE", None, "conflicting_functional_identifiers", tuple(evidence), tuple(conflicts))
    if evidence:
        return CorrelationDecision(True, case, "FUNCTIONAL_ID", "HIGH", "exact_functional_identifier", tuple(evidence), ())
    conversations = _case_conversations(case)
    if email.conversation_id and email.conversation_id in conversations:
        return CorrelationDecision(True, case, "CONVERSATION", "HIGH", "conversation_without_functional_conflict", (f"conversation_id={email.conversation_id}",), ())
    context = _case_context(case)
    context_matches = [field for field in ("organization", "family", "event_type") if getattr(classification, field, "") and context[field] and getattr(classification, field) == context[field]]
    policy_match = bool(refs.policy_number and case_refs.policy_number and refs.policy_number == case_refs.policy_number)
    period_match = bool(refs.period and case_refs.period and refs.period == case_refs.period)
    structured = [f"{field}={getattr(classification, field)}" for field in context_matches]
    if policy_match:
        structured.append("policy_number=" + refs.policy_number)
    if period_match:
        structured.append("period=" + refs.period)
    if len(structured) >= 3:
        return CorrelationDecision(False, None, "STRUCTURED", "MEDIUM", "structured_evidence_below_auto_link_threshold", tuple(structured), ())
    if policy_match or context_matches:
        return CorrelationDecision(False, None, "STRUCTURED", "LOW", "weak_context_is_not_sufficient_for_auto_link", tuple(structured), ())
    return CorrelationDecision(False, None, "NEW_CASE", None, "no_sufficient_correlation_evidence", (), ())


def correlate_email_to_case(email, classification, candidates) -> CorrelationDecision:
    if classification.scope in {"BATCH", "PLATFORM"}:
        return CorrelationDecision(False, None, "NEW_CASE", None, "scope_not_individual", conflicts=(classification.scope,))
    decisions = [evaluate_candidate(email, classification, case) for case in find_candidate_cases(candidates, email=email, classification=classification)]
    matches = [decision for decision in decisions if decision.matched and decision.confidence == "HIGH"]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        return CorrelationDecision(False, None, "NEW_CASE", None, "ambiguous_multiple_candidates", tuple(item for decision in matches for item in decision.evidence), ())
    conflicts = [decision for decision in decisions if decision.conflicts]
    if conflicts:
        return CorrelationDecision(False, None, "NEW_CASE", None, "conflicting_functional_identifiers", tuple(item for decision in conflicts for item in decision.evidence), tuple(item for decision in conflicts for item in decision.conflicts))
    suggestions = [decision for decision in decisions if decision.method == "STRUCTURED" and decision.evidence]
    if suggestions:
        if len(suggestions) == 1:
            return suggestions[0]
        return CorrelationDecision(False, None, "STRUCTURED", "MEDIUM", "ambiguous_or_insufficient_structured_evidence", tuple(item for decision in suggestions for item in decision.evidence), ())
    return CorrelationDecision(False, None, "NEW_CASE", None, "no_sufficient_correlation_evidence", (), ())
