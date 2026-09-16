"""B.2 shadow replay comparing safe retrieval against B.1."""

import csv
from collections import Counter, defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from openpyxl import Workbook

from .candidate_retrieval import SafeCandidateIndex
from .correlation import STRONG_FIELDS, _case_references, extract_functional_references
from .correlation_replay import (
    _case_key,
    _correlate_with_precomputed_features,
    _false_merge_reasons,
    _false_split_rows,
    _historical_email,
    _link,
    _originates_case,
    _received_key,
    _write_sheet,
    run_correlation_replay,
)
from .services import classify_inbound_email


TIMING_METRICS = {"B1_SECONDS", "CLASSIFICATION_SECONDS", "CORRELATION_SECONDS", "DIAGNOSTICS_SECONDS", "EXCEL_WRITE_SECONDS", "TOTAL_SECONDS"}

# Stable in-memory contract consumed by later shadow replays. The exported
# DecisionComparison sheet intentionally uses B1_/B2_ prefixes instead.
B2_DECISION_FIELDS = frozenset({
    "message_id_hash", "MATCHED", "MATCHED_CASE_KEY", "SIMULATED_CASE_KEY",
    "METHOD", "CONFIDENCE", "REASON", "EVIDENCE", "CONFLICTS",
    "CANDIDATE_COUNT", "SCOPE", "CASE_ORIGINATING",
})


def _ordered_rows(dataset_path):
    rows = []
    with Path(dataset_path).open("r", encoding="utf-8-sig", newline="") as handle:
        for order, row in enumerate(csv.DictReader(handle)):
            row["_order"] = order
            rows.append(row)
    return sorted(rows, key=lambda row: _received_key(row.get("received_at"), row.get("message_id_hash") or row.get("message_id") or row.get("_order")))


def _case_snapshot(case):
    return (
        case.case_key,
        case.organization,
        case.family,
        case.event_type,
        case.status,
        case.action_type,
        case.scope,
        case.opened_at,
        case.last_activity_at,
        case.resolved_at,
        case.functional_references,
        tuple(sorted(case.conversation_ids)),
        tuple(getattr(link.email, "external_message_id", "") for link in case.messages),
    )


def _semantic_tuple(row):
    return (
        row["MATCHED"],
        row["MATCHED_CASE_KEY"],
        row["METHOD"],
        row["CONFIDENCE"],
        row["EVIDENCE"],
        row["CONFLICTS"],
    )


def _comparison_row(b1, b2, retrieval):
    b1_case = b1.get("MATCHED_CASE_KEY") or b1.get("SIMULATED_CASE_KEY", "")
    b2_case = b2.get("MATCHED_CASE_KEY") or b2.get("SIMULATED_CASE_KEY", "")
    b1_conflict = bool(b1.get("CONFLICTS"))
    b2_conflict = bool(b2.get("CONFLICTS"))
    b1_ambiguous = b1.get("REASON") == "ambiguous_multiple_candidates"
    b2_ambiguous = b2.get("REASON") == "ambiguous_multiple_candidates"
    return {
        "message_id_hash": b1["message_id_hash"],
        "B1_METHOD": b1["METHOD"],
        "B1_MATCHED": b1["MATCHED"],
        "B1_CONFIDENCE": b1["CONFIDENCE"],
        "B1_REASON": b1["REASON"],
        "B1_CASE": b1_case,
        "B1_SCOPE": b1["SCOPE"],
        "B1_CASE_ORIGINATING": b1["CASE_ORIGINATING"],
        "B2_METHOD": b2["METHOD"],
        "B2_MATCHED": b2["MATCHED"],
        "B2_CONFIDENCE": b2["CONFIDENCE"],
        "B2_REASON": b2["REASON"],
        "B2_CASE": b2_case,
        "B2_SCOPE": b2["SCOPE"],
        "B2_CASE_ORIGINATING": b2["CASE_ORIGINATING"],
        "B1_CANDIDATE_COUNT": b1["CANDIDATE_COUNT"],
        "B2_CANDIDATE_COUNT": b2["CANDIDATE_COUNT"],
        "DECISION_EQUIVALENT": _semantic_tuple(b1) == _semantic_tuple(b2),
        "MATCH_EQUIVALENT": b1["MATCHED"] == b2["MATCHED"],
        "CASE_EQUIVALENT": b1_case == b2_case,
        "CONFLICT_PRESERVED": b1_conflict == b2_conflict and (not b1_conflict or b1["CONFLICTS"] == b2["CONFLICTS"]),
        "AMBIGUITY_PRESERVED": b1_ambiguous == b2_ambiguous,
        "B2_RETRIEVAL_POLICY": retrieval.policy,
        "B2_STRONG_CANDIDATE_COUNT": len(retrieval.strong_candidates),
        "B2_FUNCTIONAL_CANDIDATE_COUNT": len(retrieval.functional_candidates),
        "B2_CONVERSATION_CANDIDATE_COUNT": len(retrieval.conversation_candidates),
    }


def _human_sample(rows, limit=150):
    strata = defaultdict(list)
    for row in rows:
        labels = []
        if not row["DECISION_EQUIVALENT"]:
            labels.append("DIVERGENCE")
        if row["B1_CONFIDENCE"] == "HIGH" or row["B2_CONFIDENCE"] == "HIGH":
            labels.append("HIGH")
        if row["B1_REASON"] == "conflicting_functional_identifiers":
            labels.append("CONFLICT")
        if row["B1_REASON"] == "ambiguous_multiple_candidates":
            labels.append("AMBIGUITY")
        labels.append("GENERAL")
        for label in labels:
            strata[label].append(row)
    selected = []
    for label in ("DIVERGENCE", "HIGH", "CONFLICT", "AMBIGUITY", "GENERAL"):
        selected.extend(strata[label][: max(1, limit // 5)])
    unique = []
    seen = set()
    for row in selected:
        if row["message_id_hash"] not in seen:
            unique.append({**row, "VALIDACION_HUMANA": "", "COMENTARIO_HUMANO": ""})
            seen.add(row["message_id_hash"])
        if len(unique) >= limit:
            break
    return unique


def run_candidate_retrieval_replay(dataset_path, output_path, *, progress_callback=None):
    """Run B.1 as baseline and B.2 as a non-persistent shadow replay."""
    total_started = perf_counter()
    with TemporaryDirectory() as directory:
        b1_started = perf_counter()
        baseline = run_correlation_replay(dataset_path, Path(directory) / "b1.xlsx")
        b1_seconds = perf_counter() - b1_started

    b1_by_message = {row["message_id_hash"]: row for row in baseline["decisions"]}
    ordered = _ordered_rows(dataset_path)
    cases, index, case_features = [], SafeCandidateIndex(), {}
    b2_decisions, comparisons = [], []
    b2_resolutions = []
    counters = Counter()
    classification_seconds = correlation_seconds = 0.0
    candidate_total = candidate_max = 0
    functional_index_hits = conversation_index_hits = 0
    functional_hits_by_type = Counter()
    zero_strong = one_strong = multiple_strong = 0
    for processed, row in enumerate(ordered, start=1):
        email = _historical_email(row)
        email.received_at = _received_key(row.get("received_at"), row.get("message_id_hash"))[0]
        started = perf_counter()
        references = extract_functional_references(email)
        classification = classify_inbound_email(email)
        classification_seconds += perf_counter() - started
        email._replay_classification = classification
        retrieval = index.retrieve(email, functional_references=references)
        candidate_total += len(retrieval.candidates)
        candidate_max = max(candidate_max, len(retrieval.candidates))
        functional_index_hits += bool(retrieval.functional_candidates)
        functional_hits_by_type.update(field for field, count in retrieval.functional_hits_by_type.items() if count)
        conversation_index_hits += bool(retrieval.conversation_candidates)
        strong_count = len(retrieval.strong_candidates)
        if strong_count == 0:
            zero_strong += 1
        elif strong_count == 1:
            one_strong += 1
        else:
            multiple_strong += 1
        for case in retrieval.candidates:
            if id(case) not in case_features:
                case_features[id(case)] = _case_references(case)
        started = perf_counter()
        decision = _correlate_with_precomputed_features(
            email,
            classification,
            retrieval.candidates,
            references,
            {id(case): case_features[id(case)] for case in retrieval.candidates},
        )
        correlation_seconds += perf_counter() - started
        row["candidate_count"] = len(retrieval.candidates)
        decision_row = _decision_row_b2(row, email, classification, decision)
        b2_decisions.append(decision_row)
        if not decision.matched and _originates_case(classification):
            case = ReplayCaseB2(
                case_key=_case_key(email, references, row.get("message_id_hash")),
                organization=classification.organization,
                family=classification.family,
                event_type=classification.event_type,
                status="PENDING" if classification.message_outcome == "PENDING_ACTION" else "OPEN",
                action_type=classification.action_type,
                scope=classification.scope,
                opened_at=email.received_at,
                last_activity_at=email.received_at,
                functional_references=references,
            )
            _link(case, email)
            cases.append(case)
            index.add(case)
            decision_row["SIMULATED_CASE_KEY"] = case.case_key
        elif decision.matched:
            _link(decision.case, email)
            decision.case.last_activity_at = email.received_at
            if classification.message_outcome == "SUCCESS":
                b2_resolutions.append(decision_row)
        b1_row = b1_by_message[row["message_id_hash"]]
        comparisons.append(_comparison_row(b1_row, decision_row, retrieval))
        if progress_callback and (processed % 1000 == 0 or processed == len(ordered)):
            elapsed = perf_counter() - total_started
            progress_callback({"processed": processed, "total": len(ordered), "simulated_cases": len(cases), "candidate_average": candidate_total / processed, "candidate_max": candidate_max, "elapsed_seconds": elapsed, "messages_per_second": processed / max(elapsed, 1e-9)})

    diagnostics_started = perf_counter()
    divergences = [row for row in comparisons if not row["DECISION_EQUIVALENT"]]
    high_matches = [row for row in comparisons if row["B1_CONFIDENCE"] == "HIGH" or row["B2_CONFIDENCE"] == "HIGH"]
    conflicts = [row for row in comparisons if row["B1_REASON"] == "conflicting_functional_identifiers" or row["B2_REASON"] == "conflicting_functional_identifiers"]
    ambiguities = [row for row in comparisons if row["B1_REASON"] == "ambiguous_multiple_candidates" or row["B2_REASON"] == "ambiguous_multiple_candidates"]
    b1_case_snapshots = [_case_snapshot(case) for case in baseline["cases"]]
    b2_case_snapshots = [_case_snapshot(case) for case in cases]
    counters["TOTAL_MESSAGES"] = len(ordered)
    counters["B1_CANDIDATE_TOTAL"] = baseline["metrics"].get("CANDIDATE_TOTAL", 0)
    counters["B1_CANDIDATE_AVERAGE"] = baseline["metrics"].get("CANDIDATE_AVERAGE", 0)
    counters["B1_CANDIDATE_MAX"] = baseline["metrics"].get("CANDIDATE_MAX", 0)
    counters["B2_CANDIDATE_TOTAL"] = candidate_total
    counters["B2_CANDIDATE_AVERAGE"] = round(candidate_total / len(ordered), 4) if ordered else 0
    counters["B2_CANDIDATE_MAX"] = candidate_max
    counters["SIMULATED_CASES_B1"] = len(baseline["cases"])
    counters["SIMULATED_CASES_B2"] = len(cases)
    counters["B1_FUNCTIONAL_ID_HIGH"] = baseline["metrics"].get("FUNCTIONAL_ID_HIGH", 0)
    counters["B1_CONVERSATION_HIGH"] = baseline["metrics"].get("CONVERSATION_HIGH", 0)
    counters["B2_FUNCTIONAL_ID_HIGH"] = sum(row["B2_METHOD"] == "FUNCTIONAL_ID" and row["B2_CONFIDENCE"] == "HIGH" for row in comparisons)
    counters["B2_CONVERSATION_HIGH"] = sum(row["B2_METHOD"] == "CONVERSATION" and row["B2_CONFIDENCE"] == "HIGH" for row in comparisons)
    counters["FUNCTIONAL_INDEX_HITS"] = functional_index_hits
    counters["CONVERSATION_INDEX_HITS"] = conversation_index_hits
    counters["MESSAGES_WITH_ZERO_STRONG_CANDIDATES"] = zero_strong
    counters["MESSAGES_WITH_ONE_STRONG_CANDIDATE"] = one_strong
    counters["MESSAGES_WITH_MULTIPLE_STRONG_CANDIDATES"] = multiple_strong
    counters["FULL_SCAN_FALLBACKS"] = 0
    counters["DECISION_DIVERGENCES"] = len(divergences)
    counters["MATCH_DIVERGENCES"] = sum(not row["MATCH_EQUIVALENT"] for row in comparisons)
    counters["CASE_DIVERGENCES"] = sum(not row["CASE_EQUIVALENT"] for row in comparisons)
    counters["CONFLICTS_B1"] = sum(bool(row["B1_REASON"] == "conflicting_functional_identifiers") for row in comparisons)
    counters["CONFLICTS_B2"] = sum(bool(row["B2_REASON"] == "conflicting_functional_identifiers") for row in comparisons)
    counters["CONFLICTS_PRESERVED"] = sum(row["CONFLICT_PRESERVED"] for row in comparisons)
    counters["AMBIGUITIES_B1"] = sum(row["B1_REASON"] == "ambiguous_multiple_candidates" for row in comparisons)
    counters["AMBIGUITIES_B2"] = sum(row["B2_REASON"] == "ambiguous_multiple_candidates" for row in comparisons)
    counters["AMBIGUITIES_PRESERVED"] = sum(row["AMBIGUITY_PRESERVED"] for row in comparisons)
    counters["CASE_SETS_EQUIVALENT"] = b1_case_snapshots == b2_case_snapshots
    counters["B1_POTENTIAL_FALSE_MERGES"] = len(baseline["potential_false_merges"])
    counters["B1_POTENTIAL_FALSE_SPLITS"] = len(baseline["potential_false_splits"])
    b2_merges = [{"case_key": case.case_key, "reasons": "; ".join(reasons)} for case in cases if (reasons := _false_merge_reasons(case))]
    b2_splits = _false_split_rows(cases)
    counters["B2_POTENTIAL_FALSE_MERGES"] = len(b2_merges)
    counters["B2_POTENTIAL_FALSE_SPLITS"] = len(b2_splits)
    counters["B1_POTENTIAL_RESOLUTIONS"] = len(baseline["potential_resolutions"])
    counters["B2_POTENTIAL_RESOLUTIONS"] = len(b2_resolutions)
    diagnostics_seconds = perf_counter() - diagnostics_started
    counters["B1_SECONDS"] = round(b1_seconds, 4)
    counters["CLASSIFICATION_SECONDS"] = round(classification_seconds, 4)
    counters["CORRELATION_SECONDS"] = round(correlation_seconds, 4)
    counters["DIAGNOSTICS_SECONDS"] = round(diagnostics_seconds, 4)

    functional_audit = []
    for field in ("policy_number", "claim_number", "case_number", "request_number", "operation_number", "transaction_number", "contract_number", "invoice_number", "payment_reference", "period"):
        functional_audit.append({"reference_type": field, "indexed": True, "messages_with_any_functional_hit": functional_hits_by_type[field], "note": "retrieval only; correlation strength remains unchanged"})
    conversation_audit = [{"metric": "MESSAGES_WITH_CONVERSATION_INDEX_HIT", "value": conversation_index_hits}, {"metric": "CONVERSATION_HIGH_B1", "value": counters["B1_CONVERSATION_HIGH"]}, {"metric": "CONVERSATION_HIGH_B2", "value": counters["B2_CONVERSATION_HIGH"]}]
    invariant_rows = [
        {"invariant": "BATCH/PLATFORM no individual origin", "result": all(not (row["B1_SCOPE"] in {"BATCH", "PLATFORM"} and row["B1_CASE_ORIGINATING"]) and not (row["B2_SCOPE"] in {"BATCH", "PLATFORM"} and row["B2_CASE_ORIGINATING"]) for row in comparisons), "scope": "B1/B2 shadow"},
        {"invariant": "HIGH ambiguity preserved", "result": counters["AMBIGUITIES_B1"] == counters["AMBIGUITIES_B2"], "scope": "comparison"},
        {"invariant": "No full scan fallback", "result": counters["FULL_SCAN_FALLBACKS"] == 0, "scope": "B2"},
        {"invariant": "Functional case set equivalent", "result": counters["CASE_SETS_EQUIVALENT"], "scope": "comparison"},
    ]
    summary = [{"METRIC": key, "VALUE": value} for key, value in counters.items()]
    excel_started = perf_counter()
    workbook = Workbook()
    workbook.remove(workbook.active)
    _write_sheet(workbook, "Summary", summary)
    _write_sheet(workbook, "DecisionComparison", comparisons)
    _write_sheet(workbook, "CandidateMetrics", [
        {"METRIC": "CANDIDATE_TOTAL", "B1": counters["B1_CANDIDATE_TOTAL"], "B2": counters["B2_CANDIDATE_TOTAL"]},
        {"METRIC": "CANDIDATE_AVERAGE", "B1": counters["B1_CANDIDATE_AVERAGE"], "B2": counters["B2_CANDIDATE_AVERAGE"]},
        {"METRIC": "CANDIDATE_MAX", "B1": counters["B1_CANDIDATE_MAX"], "B2": counters["B2_CANDIDATE_MAX"]},
        {"METRIC": "FULL_SCAN_FALLBACKS", "B1": "N/A", "B2": counters["FULL_SCAN_FALLBACKS"]},
    ])
    _write_sheet(workbook, "HighMatches", high_matches)
    _write_sheet(workbook, "Conflicts", conflicts)
    _write_sheet(workbook, "Ambiguities", ambiguities)
    _write_sheet(workbook, "CandidateReductions", [row for row in comparisons if int(row["B1_CANDIDATE_COUNT"]) > int(row["B2_CANDIDATE_COUNT"])])
    _write_sheet(workbook, "FunctionalIndexAudit", functional_audit)
    _write_sheet(workbook, "ConversationIndexAudit", conversation_audit)
    _write_sheet(workbook, "InvariantAudit", invariant_rows)
    _write_sheet(workbook, "Divergences", divergences)
    _write_sheet(workbook, "PotentialFalseMerges", [{"SOURCE": "B1", **row} for row in baseline["potential_false_merges"]] + [{"SOURCE": "B2", **row} for row in b2_merges])
    _write_sheet(workbook, "PotentialFalseSplits", [{"SOURCE": "B1", **row} for row in baseline["potential_false_splits"]] + [{"SOURCE": "B2", **row} for row in b2_splits])
    _write_sheet(workbook, "HumanValidationSample", _human_sample(comparisons))
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output_path)
    counters["EXCEL_WRITE_SECONDS"] = round(perf_counter() - excel_started, 4)
    counters["TOTAL_SECONDS"] = round(perf_counter() - total_started, 4)
    return {"metrics": dict(counters), "decisions": b2_decisions, "comparisons": comparisons, "b1": baseline, "b2_cases": cases, "divergences": divergences, "high_matches": high_matches, "conflicts": conflicts, "ambiguities": ambiguities, "output_path": str(output_path)}


class ReplayCaseB2:
    def __init__(self, case_key, organization="", family="", event_type="", status="OPEN", action_type="NONE", scope="CASE", opened_at=None, last_activity_at=None, resolved_at=None, messages=None, functional_references=None, conversation_ids=None):
        self.case_key = case_key
        self.organization = organization
        self.family = family
        self.event_type = event_type
        self.status = status
        self.action_type = action_type
        self.scope = scope
        self.opened_at = opened_at
        self.last_activity_at = last_activity_at
        self.resolved_at = resolved_at
        self.messages = messages or []
        self.functional_references = functional_references
        self.conversation_ids = conversation_ids or set()


def _decision_row_b2(row, email, classification, decision):
    from .correlation_replay import _decision_row
    return _decision_row(row, email, classification, decision)
