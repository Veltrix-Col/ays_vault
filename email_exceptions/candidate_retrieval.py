"""Safe, in-memory candidate retrieval for the B.2 shadow replay.

This module only selects candidates. Correlation decisions remain delegated to
the existing public correlation engine.
"""

from dataclasses import dataclass
from collections import defaultdict

from .correlation import (
    FUNCTIONAL_FIELDS,
    STRONG_FIELDS,
    _case_references,
    extract_functional_references,
)


@dataclass(frozen=True)
class CandidateRetrievalResult:
    candidates: tuple
    functional_candidates: tuple
    strong_candidates: tuple
    conversation_candidates: tuple
    conflict_candidates: tuple
    policy: str
    functional_hits_by_type: dict
    conflict_hits_by_type: dict

    @property
    def has_strong_candidates(self):
        return bool(self.strong_candidates)


class SafeCandidateIndex:
    """Indexes only evidence already recognized by the correlation engine."""

    def __init__(self):
        self.functional = defaultdict(set)
        self.conversations = defaultdict(set)
        self.conflict_evidence = defaultdict(lambda: defaultdict(set))
        self._case_tokens = {}
        self._cases_by_token = {}
        self._next_token = 0

    def _token_for(self, case):
        object_id = id(case)
        token = self._case_tokens.get(object_id)
        if token is None:
            token = (str(getattr(case, "case_key", "") or ""), self._next_token)
            self._next_token += 1
            self._case_tokens[object_id] = token
            self._cases_by_token[token] = case
        return token

    def _add_functional_references(self, token, references):
        for field in FUNCTIONAL_FIELDS:
            value = getattr(references, field, "")
            if value:
                self.functional[(field, value)].add(token)

    def _add_conflict_evidence(self, token, case, references):
        for context_field in ("organization", "family", "event_type"):
            context_value = str(getattr(case, context_field, "") or "")
            if not context_value:
                continue
            for reference_field in STRONG_FIELDS:
                reference_value = getattr(references, reference_field, "")
                if reference_value:
                    self.conflict_evidence[(context_field, context_value, reference_field)][reference_value].add(token)

    def add(self, case):
        token = self._token_for(case)
        references = getattr(case, "functional_references", None) or _case_references(case)
        self._add_functional_references(token, references)
        conversation_ids = getattr(case, "conversation_ids", None)
        if conversation_ids is None:
            messages = getattr(case, "messages", ())
            if hasattr(messages, "all"):
                messages = messages.all()
            conversation_ids = (
                getattr(getattr(message, "email", None), "conversation_id", "")
                for message in messages
            )
        for conversation_id in conversation_ids:
            if conversation_id:
                self.conversations[conversation_id].add(token)
        # Conflict evidence must mirror the canonical engine's case feature
        # extraction, rather than trusting a test/replay convenience field.
        self._add_conflict_evidence(token, case, _case_references(case))

    def add_message_evidence(self, case, email):
        """Incrementally add references and conversation evidence from email."""
        token = self._token_for(case)
        references = extract_functional_references(email)
        self._add_functional_references(token, references)
        if email.conversation_id:
            self.conversations[email.conversation_id].add(token)
        self._add_conflict_evidence(token, case, references)

    def retrieve(self, email, *, functional_references=None, classification=None):
        references = functional_references or extract_functional_references(email)
        functional_tokens = set()
        strong_tokens = set()
        functional_hits_by_type = {}
        for field in FUNCTIONAL_FIELDS:
            value = getattr(references, field, "")
            if not value:
                continue
            matches = self.functional.get((field, value), ())
            functional_hits_by_type[field] = len(matches)
            functional_tokens.update(matches)
            if field in STRONG_FIELDS:
                strong_tokens.update(matches)

        conversation_tokens = set()
        if email.conversation_id:
            conversation_tokens.update(self.conversations.get(email.conversation_id, ()))

        selected = functional_tokens | conversation_tokens
        ordered = lambda tokens: tuple(self._cases_by_token[token] for token in sorted(tokens, key=lambda token: token))
        functional_candidates = ordered(functional_tokens)
        strong_candidates = ordered(strong_tokens)
        conversation_candidates = ordered(conversation_tokens)
        conflict_tokens = set()
        conflict_hits_by_type = {}
        if classification is not None:
            for context_field in ("organization", "family", "event_type"):
                context_value = str(getattr(classification, context_field, "") or "")
                if not context_value:
                    continue
                for reference_field in STRONG_FIELDS:
                    current_value = getattr(references, reference_field, "")
                    if not current_value:
                        continue
                    values = self.conflict_evidence.get((context_field, context_value, reference_field), {})
                    incompatible = [(value, tokens) for value, tokens in values.items() if value != current_value]
                    if incompatible:
                        conflict_hits_by_type[reference_field] = conflict_hits_by_type.get(reference_field, 0) + 1
                        # One deterministic representative per reference type is
                        # sufficient to let the canonical engine observe the
                        # conflict without restoring the broad B.1 candidate set.
                        value, tokens = sorted(incompatible, key=lambda item: item[0])[0]
                        conflict_tokens.add(sorted(tokens, key=lambda token: token)[0])
        conflict_candidates = ordered(conflict_tokens)
        if strong_candidates:
            policy = "STRONG_OR_CONVERSATION"
        elif conversation_candidates:
            policy = "CONVERSATION_ONLY"
        elif functional_candidates:
            policy = "WEAK_FUNCTIONAL_ONLY"
        else:
            policy = "NO_INDEXABLE_EVIDENCE"
        return CandidateRetrievalResult(
            candidates=ordered(selected),
            functional_candidates=functional_candidates,
            strong_candidates=strong_candidates,
            conversation_candidates=conversation_candidates,
            conflict_candidates=conflict_candidates,
            policy=policy,
            functional_hits_by_type=functional_hits_by_type,
            conflict_hits_by_type=conflict_hits_by_type,
        )
