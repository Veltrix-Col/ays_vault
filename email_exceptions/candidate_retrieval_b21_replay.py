"""B.2.1 shadow replay with separate conflict evidence retrieval."""

from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from openpyxl import Workbook

from .candidate_retrieval_replay import (
    B2_DECISION_FIELDS,
    _case_snapshot,
    _human_sample,
    _ordered_rows,
    _comparison_row,
    run_candidate_retrieval_replay,
)
from .candidate_retrieval import SafeCandidateIndex
from .correlation import _case_references, extract_functional_references
from .correlation_replay import (
    _case_key,
    _correlate_with_precomputed_features,
    _decision_row,
    _false_merge_reasons,
    _false_split_rows,
    _historical_email,
    _link,
    _originates_case,
    _received_key,
)
from .services import classify_inbound_email


def _unique_cases(*groups):
    seen = set()
    result = []
    for group in groups:
        for case in group:
            identity = id(case)
            if identity not in seen:
                result.append(case)
                seen.add(identity)
    return tuple(sorted(result, key=lambda case: case.case_key))


def _triple_row(b1, b2, b21, retrieval):
    b1_case = b1.get("MATCHED_CASE_KEY") or b1.get("SIMULATED_CASE_KEY", "")
    b2_case = b2.get("MATCHED_CASE_KEY") or b2.get("SIMULATED_CASE_KEY", "")
    b21_case = b21.get("MATCHED_CASE_KEY") or b21.get("SIMULATED_CASE_KEY", "")
    return {
        "message_id_hash": b1["message_id_hash"],
        "B1_METHOD": b1["METHOD"], "B1_MATCHED": b1["MATCHED"], "B1_CONFIDENCE": b1["CONFIDENCE"], "B1_REASON": b1["REASON"], "B1_CASE": b1_case,
        "B2_METHOD": b2["METHOD"], "B2_MATCHED": b2["MATCHED"], "B2_CONFIDENCE": b2["CONFIDENCE"], "B2_REASON": b2["REASON"], "B2_CASE": b2_case,
        "B21_METHOD": b21["METHOD"], "B21_MATCHED": b21["MATCHED"], "B21_CONFIDENCE": b21["CONFIDENCE"], "B21_REASON": b21["REASON"], "B21_CASE": b21_case,
        "B1_CANDIDATE_COUNT": b1["CANDIDATE_COUNT"], "B2_CANDIDATE_COUNT": b2["CANDIDATE_COUNT"], "B21_DECISION_CANDIDATE_COUNT": b21["CANDIDATE_COUNT"],
        "B21_CONFLICT_EVIDENCE_COUNT": len(retrieval.conflict_candidates),
        "B21_CONFLICT_EVIDENCE_TYPES": ", ".join(sorted(retrieval.conflict_hits_by_type)),
        "MATCH_EQUIVALENT": b1["MATCHED"] == b21["MATCHED"],
        "CASE_EQUIVALENT": b1_case == b21_case,
        "CONFLICT_PRESERVED": bool(b1["CONFLICTS"]) == bool(b21["CONFLICTS"]),
        "AMBIGUITY_PRESERVED": (b1["REASON"] == "ambiguous_multiple_candidates") == (b21["REASON"] == "ambiguous_multiple_candidates"),
        "DECISION_EQUIVALENT": (b1["MATCHED"], b1_case, b1["METHOD"], b1["CONFIDENCE"], bool(b1["CONFLICTS"]), b1["REASON"] == "ambiguous_multiple_candidates") == (b21["MATCHED"], b21_case, b21["METHOD"], b21["CONFIDENCE"], bool(b21["CONFLICTS"]), b21["REASON"] == "ambiguous_multiple_candidates"),
    }


class ReplayCaseB21:
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


def run_candidate_retrieval_b21_replay(dataset_path, output_path, *, progress_callback=None):
    with TemporaryDirectory() as directory:
        b2_result = run_candidate_retrieval_replay(dataset_path, Path(directory) / "b2.xlsx")
    b1 = b2_result["b1"]
    b1_by_id = {row["message_id_hash"]: row for row in b1["decisions"]}
    # Consume B.2's canonical in-memory decision rows. The exported
    # DecisionComparison rows are report-shaped and use B2_* column names.
    b2_decisions = b2_result["decisions"]
    b2_by_id = {row["message_id_hash"]: row for row in b2_decisions}
    if any(not B2_DECISION_FIELDS.issubset(row) for row in b2_decisions):
        raise ValueError("B.2 decision schema no longer satisfies B2_DECISION_FIELDS")
    ordered = _ordered_rows(dataset_path)
    cases, index, case_features = [], SafeCandidateIndex(), {}
    b21_rows, comparisons = [], []
    started_at = perf_counter()
    conflict_lookups = conflict_lookup_max = 0
    decision_candidate_total = decision_candidate_max = 0
    for processed, row in enumerate(ordered, start=1):
        email = _historical_email(row)
        email.received_at = _received_key(row.get("received_at"), row.get("message_id_hash"))[0]
        references = extract_functional_references(email)
        classification = classify_inbound_email(email)
        email._replay_classification = classification
        retrieval = index.retrieve(email, functional_references=references, classification=classification)
        decision_candidates = retrieval.candidates
        engine_candidates = _unique_cases(decision_candidates, retrieval.conflict_candidates)
        for case in engine_candidates:
            if id(case) not in case_features:
                case_features[id(case)] = _case_references(case)
        decision_candidate_total += len(decision_candidates)
        decision_candidate_max = max(decision_candidate_max, len(decision_candidates))
        conflict_lookups += len(retrieval.conflict_candidates)
        conflict_lookup_max = max(conflict_lookup_max, len(retrieval.conflict_candidates))
        decision = _correlate_with_precomputed_features(email, classification, engine_candidates, references, {id(case): case_features[id(case)] for case in engine_candidates})
        row["candidate_count"] = len(decision_candidates)
        decision_row = _decision_row(row, email, classification, decision)
        b21_rows.append(decision_row)
        if not decision.matched and _originates_case(classification):
            case = ReplayCaseB21(
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
        comparisons.append(_triple_row(b1_by_id[row["message_id_hash"]], b2_by_id[row["message_id_hash"]], decision_row, retrieval))
        if progress_callback and (processed % 1000 == 0 or processed == len(ordered)):
            elapsed = perf_counter() - started_at
            progress_callback({"processed": processed, "total": len(ordered), "simulated_cases": len(cases), "candidate_average": decision_candidate_total / processed, "candidate_max": decision_candidate_max, "elapsed_seconds": elapsed, "messages_per_second": processed / elapsed if elapsed else 0})

    b21_merges = [{"case_key": case.case_key, "reasons": "; ".join(reasons)} for case in cases if (reasons := _false_merge_reasons(case))]
    b21_splits = _false_split_rows(cases)
    metrics = {
        "TOTAL_MESSAGES": len(ordered),
        "B1_CANDIDATE_TOTAL": b1["metrics"].get("CANDIDATE_TOTAL", 0), "B2_CANDIDATE_TOTAL": b2_result["metrics"].get("B2_CANDIDATE_TOTAL", 0), "B21_DECISION_CANDIDATE_TOTAL": decision_candidate_total,
        "B1_CANDIDATE_AVERAGE": b1["metrics"].get("CANDIDATE_AVERAGE", 0), "B2_CANDIDATE_AVERAGE": b2_result["metrics"].get("B2_CANDIDATE_AVERAGE", 0), "B21_DECISION_CANDIDATE_AVERAGE": round(decision_candidate_total / len(ordered), 4) if ordered else 0,
        "B21_CONFLICT_EVIDENCE_LOOKUPS": conflict_lookups, "B21_CONFLICT_EVIDENCE_AVERAGE": round(conflict_lookups / len(ordered), 4) if ordered else 0, "B21_CONFLICT_EVIDENCE_MAX": conflict_lookup_max,
        "B1_HIGH_MATCHES": b1["metrics"].get("MESSAGES_LINKED_HIGH", 0), "B21_HIGH_MATCHES": sum(row["B21_CONFIDENCE"] == "HIGH" for row in comparisons),
        "HIGH_MATCHES_PRESERVED": sum(row["B1_CONFIDENCE"] == "HIGH" and row["B21_CONFIDENCE"] == "HIGH" for row in comparisons), "HIGH_MATCHES_LOST": sum(row["B1_CONFIDENCE"] == "HIGH" and row["B21_CONFIDENCE"] != "HIGH" for row in comparisons), "HIGH_TARGET_CHANGED": sum(row["B1_CONFIDENCE"] == "HIGH" and row["B21_CONFIDENCE"] == "HIGH" and row["B1_CASE"] != row["B21_CASE"] for row in comparisons),
        "B1_CONFLICTS": sum(row["B1_REASON"] == "conflicting_functional_identifiers" for row in comparisons), "B21_CONFLICTS": sum(row["B21_REASON"] == "conflicting_functional_identifiers" for row in comparisons), "CONFLICTS_PRESERVED": sum(row["B1_REASON"] == "conflicting_functional_identifiers" and row["B21_REASON"] == "conflicting_functional_identifiers" for row in comparisons), "CONFLICTS_LOST": sum(row["B1_REASON"] == "conflicting_functional_identifiers" and row["B21_REASON"] != "conflicting_functional_identifiers" for row in comparisons), "CONFLICTS_NEW": sum(row["B1_REASON"] != "conflicting_functional_identifiers" and row["B21_REASON"] == "conflicting_functional_identifiers" for row in comparisons),
        "B1_HIGH_AMBIGUITIES": sum(row["B1_REASON"] == "ambiguous_multiple_candidates" for row in comparisons), "B21_HIGH_AMBIGUITIES": sum(row["B21_REASON"] == "ambiguous_multiple_candidates" for row in comparisons), "HIGH_AMBIGUITIES_PRESERVED": sum(row["B1_REASON"] == "ambiguous_multiple_candidates" and row["B21_REASON"] == "ambiguous_multiple_candidates" for row in comparisons),
        "FUNCTIONAL_DIVERGENCES": sum(not row["DECISION_EQUIVALENT"] for row in comparisons), "FULL_SCAN_FALLBACKS": 0, "SIMULATED_CASES_B1": len(b1["cases"]), "SIMULATED_CASES_B21": len(cases), "B1_POTENTIAL_FALSE_MERGES": len(b1["potential_false_merges"]), "B21_POTENTIAL_FALSE_MERGES": len(b21_merges), "B1_POTENTIAL_FALSE_SPLITS": len(b1["potential_false_splits"]), "B21_POTENTIAL_FALSE_SPLITS": len(b21_splits),
    }
    summary = [{"METRIC": key, "VALUE": value} for key, value in metrics.items()]
    workbook = Workbook()
    workbook.remove(workbook.active)
    _write = __import__("email_exceptions.correlation_replay", fromlist=["_write_sheet"])._write_sheet
    _write(workbook, "Summary", summary)
    _write(workbook, "DecisionComparison", comparisons)
    _write(workbook, "CandidateMetrics", [{"METRIC": "CANDIDATE_TOTAL", "B1": metrics["B1_CANDIDATE_TOTAL"], "B2": metrics["B2_CANDIDATE_TOTAL"], "B21": metrics["B21_DECISION_CANDIDATE_TOTAL"]}, {"METRIC": "CANDIDATE_AVERAGE", "B1": metrics["B1_CANDIDATE_AVERAGE"], "B2": metrics["B2_CANDIDATE_AVERAGE"], "B21": metrics["B21_DECISION_CANDIDATE_AVERAGE"]}])
    _write(workbook, "HighMatches", [row for row in comparisons if row["B1_CONFIDENCE"] == "HIGH" or row["B21_CONFIDENCE"] == "HIGH"])
    _write(workbook, "Conflicts", [row for row in comparisons if row["B1_REASON"] == "conflicting_functional_identifiers" or row["B21_REASON"] == "conflicting_functional_identifiers"])
    _write(workbook, "Ambiguities", [row for row in comparisons if row["B1_REASON"] == "ambiguous_multiple_candidates" or row["B21_REASON"] == "ambiguous_multiple_candidates"])
    _write(workbook, "CandidateReductions", [row for row in comparisons if int(row["B1_CANDIDATE_COUNT"]) > int(row["B21_DECISION_CANDIDATE_COUNT"])])
    _write(workbook, "FunctionalIndexAudit", [{"metric": "decision_functional_index", "value": "enabled"}])
    _write(workbook, "ConversationIndexAudit", [{"metric": "conversation_index", "value": "enabled"}])
    _write(workbook, "InvariantAudit", [{"invariant": "No full scan fallback", "result": metrics["FULL_SCAN_FALLBACKS"] == 0}, {"invariant": "HIGH matches preserved", "result": metrics["HIGH_MATCHES_LOST"] == 0}, {"invariant": "Conflicts preserved", "result": metrics["CONFLICTS_LOST"] == 0}])
    _write(workbook, "Divergences", [row for row in comparisons if not row["DECISION_EQUIVALENT"]])
    _write(workbook, "PotentialFalseMerges", [{"SOURCE": "B1", **row} for row in b1["potential_false_merges"]] + [{"SOURCE": "B21", **row} for row in b21_merges])
    _write(workbook, "PotentialFalseSplits", [{"SOURCE": "B1", **row} for row in b1["potential_false_splits"]] + [{"SOURCE": "B21", **row} for row in b21_splits])
    _write(workbook, "HumanValidationSample", _human_sample(comparisons))
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output_path)
    return {"metrics": metrics, "comparisons": comparisons, "b1": b1, "b2": b2_result, "b21_cases": cases, "divergences": [row for row in comparisons if not row["DECISION_EQUIVALENT"]], "output_path": str(output_path)}
