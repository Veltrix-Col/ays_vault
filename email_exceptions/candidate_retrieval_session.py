"""Execution-scoped persistent candidate index.

The session deliberately has no process-wide cache.  One instance belongs to
one ingestion/backfill execution and is bound to one Django database alias.
"""

from time import perf_counter

from .candidate_retrieval import SafeCandidateIndex
from .models import ExceptionCase


class CandidateRetrievalSession:
    """Reuse one candidate index while processing a sequence of messages."""

    def __init__(self, *, using="default"):
        self.using = using
        self.index = SafeCandidateIndex()
        self.cases = {}
        self.index_initial_cases = 0
        self.index_cases_loaded_from_db = 0
        self.index_initial_build_seconds = 0.0
        self.index_incremental_case_updates = 0
        self.index_incremental_message_updates = 0
        self.index_incremental_seconds = 0.0
        self.retrieval_seconds = 0.0
        self.candidate_counts = []
        started = perf_counter()
        queryset = (
            ExceptionCase.objects.using(using)
            .filter(scope=ExceptionCase.Scope.CASE)
            .prefetch_related("messages__email")
        )
        for case in queryset:
            self.cases[case.pk] = case
            self.index.add(case)
        self.index_initial_build_seconds = perf_counter() - started
        self.index_initial_cases = len(self.cases)
        self.index_cases_loaded_from_db = len(self.cases)

    @property
    def cases_loaded(self):
        return len(self.cases)

    def retrieve(self, email, *, functional_references=None, classification=None):
        started = perf_counter()
        result = self.index.retrieve(
            email,
            functional_references=functional_references,
            classification=classification,
        )
        self.retrieval_seconds += perf_counter() - started
        self.candidate_counts.append(len(result.candidates) + len(result.conflict_candidates))
        return result

    def register_case(self, case):
        """Add a newly persisted case to this execution's index."""
        started = perf_counter()
        self.cases[case.pk] = case
        self.index.add(case)
        self.index_incremental_case_updates += 1
        self.index_incremental_seconds += perf_counter() - started

    def register_message(self, case, email):
        """Index only evidence introduced by a newly linked message."""
        started = perf_counter()
        self.cases[case.pk] = case
        self.index.add_message_evidence(case, email)
        self.index_incremental_message_updates += 1
        self.index_incremental_seconds += perf_counter() - started

    @property
    def index_incremental_updates(self):
        return self.index_incremental_case_updates + self.index_incremental_message_updates
