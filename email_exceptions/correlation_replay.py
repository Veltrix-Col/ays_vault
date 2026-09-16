from __future__ import annotations

import csv
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from time import perf_counter

from openpyxl import Workbook

from . import correlation as correlation_module
from .correlation import (
    CorrelationDecision,
    FUNCTIONAL_FIELDS,
    STRONG_FIELDS,
    _case_references,
    correlate_email_to_case,
    extract_functional_references,
)
from .historical_replay import _historical_email
from .services import classify_inbound_email


MAX_CELL = 32000
CASE_ORIGIN_OUTCOMES = {"FAILURE", "PENDING_ACTION"}
HUMAN_COLUMNS = ("VALIDACION_HUMANA", "COMENTARIO_HUMANO")


def _text(value, limit=MAX_CELL):
    value = "" if value is None else str(value)
    return value if len(value) <= limit else value[:limit] + "…"


def _bool(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "si", "sí"}


def _received_key(value, fallback):
    raw = str(value or "")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        parsed = datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc), str(fallback or "")


def _case_key(email, references, message_hash):
    parts = [f"{field}:{getattr(references, field)}" for field in STRONG_FIELDS if getattr(references, field)]
    if not parts:
        return f"message:{message_hash or email.external_message_id}"
    return "|".join(parts)


@dataclass(eq=False)
class ReplayCase:
    case_key: str
    organization: str = ""
    family: str = ""
    event_type: str = ""
    status: str = "OPEN"
    action_type: str = "NONE"
    scope: str = "CASE"
    opened_at: object = None
    last_activity_at: object = None
    resolved_at: object = None
    messages: list = field(default_factory=list)
    functional_references: object = None
    conversation_ids: set = field(default_factory=set)


class CandidateIndex:
    def __init__(self):
        self.functional = defaultdict(set)
        self.conversations = defaultdict(set)
        self.context = defaultdict(set)

    def add(self, case):
        refs = case.functional_references
        for field in FUNCTIONAL_FIELDS:
            value = getattr(refs, field, "")
            if value:
                self.functional[(field, value)].add(case)
        for conversation_id in case.conversation_ids:
            self.conversations[conversation_id].add(case)
        for field in ("organization", "family", "event_type"):
            value = getattr(case, field, "")
            if value:
                self.context[(field, value)].add(case)

    def candidates(self, email, classification, *, functional_references=None):
        refs = functional_references or extract_functional_references(email)
        selected = set()
        for field in FUNCTIONAL_FIELDS:
            value = getattr(refs, field, "")
            if value:
                selected.update(self.functional.get((field, value), ()))
        if email.conversation_id:
            selected.update(self.conversations.get(email.conversation_id, ()))
        for field in ("organization", "family", "event_type"):
            value = getattr(classification, field, "")
            if value:
                selected.update(self.context.get((field, value), ()))
        return tuple(sorted(selected, key=lambda case: case.case_key))


def _correlate_with_precomputed_features(email, classification, candidates, functional_references, case_references):
    """Run the unchanged public engine while supplying replay-local features.

    ``correlate_email_to_case`` and ``evaluate_candidate`` remain the public
    contract.  The temporary substitutions are confined to this single-threaded
    diagnostic replay and are restored immediately afterwards.
    """
    original_extract = correlation_module.extract_functional_references
    original_case_references = correlation_module._case_references

    def cached_extract(candidate_email):
        if candidate_email is email:
            return functional_references
        return original_extract(candidate_email)

    def cached_case_references(case):
        return case_references[id(case)]

    correlation_module.extract_functional_references = cached_extract
    correlation_module._case_references = cached_case_references
    try:
        return correlate_email_to_case(email, classification, candidates)
    finally:
        correlation_module.extract_functional_references = original_extract
        correlation_module._case_references = original_case_references


def _link(case, email):
    case.messages.append(SimpleNamespace(email=email))
    if email.conversation_id:
        case.conversation_ids.add(email.conversation_id)


def _originates_case(classification):
    return classification.scope == "CASE" and classification.action_required and classification.message_outcome in CASE_ORIGIN_OUTCOMES


def _decision_row(row, email, classification, decision, case_key=""):
    return {
        "message_id_hash": row.get("message_id_hash", ""),
        "received_at": row.get("received_at", ""),
        "from_email": row.get("from_email", ""),
        "subject": row.get("subject_original") or row.get("subject_normalized", ""),
        "body_preview": _text(row.get("body_preview") or row.get("body_text", ""), 1000),
        "MESSAGE_OUTCOME": classification.message_outcome,
        "ACTION_REQUIRED": classification.action_required,
        "ACTION_TYPE": classification.action_type,
        "SCOPE": classification.scope,
        "ORGANIZATION": classification.organization,
        "FAMILY": classification.family,
        "EVENT_TYPE": classification.event_type,
        "RULE_ID": classification.rule_id,
        "CORRELATION_KEY": classification.correlation_key,
        "CASE_ORIGINATING": _originates_case(classification),
        "MATCHED": decision.matched,
        "MATCHED_CASE_KEY": getattr(decision.case, "case_key", "") if decision.case else "",
        "METHOD": decision.method,
        "CONFIDENCE": decision.confidence or "",
        "REASON": decision.reason,
        "EVIDENCE": "; ".join(decision.evidence),
        "CONFLICTS": "; ".join(decision.conflicts),
        "CANDIDATE_COUNT": row.get("candidate_count", 0),
        "SIMULATED_CASE_KEY": case_key,
        "POTENTIAL_RESOLUTION": classification.message_outcome == "SUCCESS" and decision.matched,
    }


def _case_row(case, potential_false_merge=False, merge_reasons=()):
    refs = case.functional_references.as_dict() if case.functional_references else {}
    return {
        "case_key": case.case_key,
        "status": case.status,
        "organization": case.organization,
        "family": case.family,
        "event_type": case.event_type,
        "action_type": case.action_type,
        "scope": case.scope,
        "message_count": len(case.messages),
        "opened_at": case.opened_at,
        "last_activity_at": case.last_activity_at,
        "resolved_at": case.resolved_at,
        "conversation_ids": ", ".join(sorted(case.conversation_ids)),
        "functional_references": "; ".join(f"{key}={value}" for key, value in refs.items()),
        "POTENTIAL_FALSE_MERGE": potential_false_merge,
        "MERGE_REASONS": "; ".join(merge_reasons),
    }


def _false_merge_reasons(case):
    refs = defaultdict(set)
    organizations, families = set(), set()
    times = []
    for link in case.messages:
        email = link.email
        extracted = extract_functional_references(email)
        for field in STRONG_FIELDS:
            if getattr(extracted, field):
                refs[field].add(getattr(extracted, field))
        classification = getattr(email, "_replay_classification", None)
        if classification:
            if classification.organization:
                organizations.add(classification.organization)
            if classification.family:
                families.add(classification.family)
        if email.received_at:
            times.append(email.received_at)
    reasons = [f"multiple_{field}" for field, values in refs.items() if len(values) > 1]
    if len(organizations) > 1:
        reasons.append("multiple_organizations")
    if len(families) > 2 and not any(refs.values()):
        reasons.append("incompatible_families_without_functional_id")
    if len(times) > 1 and (max(times) - min(times)).days > 180:
        reasons.append("extraordinary_duration")
    if len(case.messages) > 50:
        reasons.append("abnormally_large_case")
    return tuple(reasons)


def _false_split_rows(cases):
    groups = defaultdict(list)
    for case in cases:
        for link in case.messages:
            refs = extract_functional_references(link.email)
            for field in STRONG_FIELDS:
                value = getattr(refs, field)
                if value:
                    groups[(field, value)].append(case)
        for conversation_id in case.conversation_ids:
            groups[("conversation_id", conversation_id)].append(case)
    rows = []
    seen = set()
    for key, members in groups.items():
        unique = {case.case_key: case for case in members}
        if len(unique) < 2:
            continue
        keys = sorted(unique)
        for left, right in zip(keys, keys[1:]):
            pair = (left, right, key)
            if pair in seen:
                continue
            seen.add(pair)
            rows.append({"EVIDENCE": f"{key[0]}={key[1]}", "CASE_A": left, "CASE_B": right, "LABEL": "POTENTIAL_FALSE_SPLIT"})
    return rows


def _conversation_audit(cases):
    rows = []
    for case in cases:
        if not case.conversation_ids:
            continue
        ids = defaultdict(set)
        organizations, families, events, senders, subjects = set(), set(), set(), set(), set()
        times = []
        for link in case.messages:
            email = link.email
            refs = extract_functional_references(email)
            for field in STRONG_FIELDS:
                if getattr(refs, field):
                    ids[field].add(getattr(refs, field))
            classification = getattr(email, "_replay_classification", None)
            if classification:
                organizations.add(classification.organization)
                families.add(classification.family)
                events.add(classification.event_type)
            senders.add(email.from_email)
            subjects.add(email.subject)
            if email.received_at:
                times.append(email.received_at)
        reasons = []
        if any(len(values) > 1 for values in ids.values()):
            reasons.append("MULTIPLE_FUNCTIONAL_IDENTITIES")
        if len(times) > 1 and (max(times) - min(times)).days > 180:
            reasons.append("LONG_RUNNING_CONVERSATION")
        category = "POTENTIAL_FALSE_MERGE" if reasons else "SAFE_CONVERSATION"
        rows.append({
            "case_key": case.case_key,
            "conversation_ids": ", ".join(sorted(case.conversation_ids)),
            "message_count": len(case.messages),
            "functional_ids": "; ".join(f"{field}={','.join(sorted(values))}" for field, values in ids.items()),
            "organizations": ", ".join(sorted(filter(None, organizations))),
            "families": ", ".join(sorted(filter(None, families))),
            "event_types": ", ".join(sorted(filter(None, events))),
            "senders": ", ".join(sorted(filter(None, senders))),
            "subjects": "; ".join(sorted(filter(None, subjects)))[:MAX_CELL],
            "period_start": min(times) if times else "",
            "period_end": max(times) if times else "",
            "category": category,
            "reasons": "; ".join(reasons) or "INSUFFICIENT_SUPPORT",
        })
    return rows


def _write_sheet(workbook, title, rows):
    sheet = workbook.create_sheet(title)
    if not rows:
        sheet.append(["No data"])
        return
    headers = list(rows[0])
    sheet.append(headers)
    for row in rows:
        sheet.append([_text(row.get(header)) for header in headers])
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions


def _human_sample(decisions, cases, ambiguities, conflicts, merges, splits, resolutions, limit=150):
    strata = defaultdict(list)
    for row in decisions:
        label = row["METHOD"] if row["MATCHED"] else row["REASON"]
        strata[label].append(row)
    for row in ambiguities:
        strata["AMBIGUITY"].append(row)
    for row in conflicts:
        strata["FUNCTIONAL_CONFLICT"].append(row)
    for row in resolutions:
        strata["POTENTIAL_RESOLUTION"].append(row)
    for row in merges:
        strata["POTENTIAL_FALSE_MERGE"].append(row)
    for row in splits:
        strata["POTENTIAL_FALSE_SPLIT"].append(row)
    selected = []
    for key in sorted(strata):
        selected.extend(strata[key][: max(1, limit // max(1, len(strata)))])
    selected = selected[:limit]
    result = []
    for row in selected:
        item = dict(row)
        for column in HUMAN_COLUMNS:
            item[column] = ""
        result.append(item)
    return result


def run_correlation_replay(dataset_path, output_path, *, progress_callback=None):
    ordered_rows = []
    with Path(dataset_path).open("r", encoding="utf-8-sig", newline="") as handle:
        for index, row in enumerate(csv.DictReader(handle)):
            row["_order"] = index
            ordered_rows.append(row)
    ordered_rows.sort(key=lambda row: _received_key(row.get("received_at"), row.get("message_id_hash") or row.get("message_id") or row.get("_order")))
    cases, index = [], CandidateIndex()
    case_features = {}
    decisions, ambiguities, conflicts, resolutions = [], [], [], []
    counters = Counter()
    loop_started = perf_counter()
    classification_seconds = 0.0
    correlation_seconds = 0.0
    feature_calls = 0
    case_feature_calls = 0
    candidate_total = 0
    candidate_max = 0
    for processed, row in enumerate(ordered_rows, start=1):
        email = _historical_email(row)
        email.received_at = _received_key(row.get("received_at"), row.get("message_id_hash"))[0]
        feature_started = perf_counter()
        email_references = extract_functional_references(email)
        feature_calls += 1
        classification = classify_inbound_email(email)
        classification_seconds += perf_counter() - feature_started
        email._replay_classification = classification
        candidates = index.candidates(email, classification, functional_references=email_references)
        candidate_total += len(candidates)
        candidate_max = max(candidate_max, len(candidates))
        case_references = {}
        for case in candidates:
            if id(case) not in case_features:
                case_features[id(case)] = _case_references(case)
                case_feature_calls += 1
            case_references[id(case)] = case_features[id(case)]
        correlation_started = perf_counter()
        decision = _correlate_with_precomputed_features(email, classification, candidates, email_references, case_references)
        correlation_seconds += perf_counter() - correlation_started
        row["candidate_count"] = len(candidates)
        decision_row = _decision_row(row, email, classification, decision)
        decisions.append(decision_row)
        counters["TOTAL_MESSAGES"] += 1
        counters[f"{classification.scope}_MESSAGES"] += 1
        if _originates_case(classification):
            counters["CASE_ORIGINATING_MESSAGES"] += 1
        if decision.matched:
            counters["MESSAGES_LINKED_HIGH"] += 1
            counters[f"{decision.method}_HIGH"] += 1
            if classification.message_outcome == "SUCCESS":
                counters["SUCCESS_LINKED_TO_CASE"] += 1
                counters["POTENTIAL_RESOLUTIONS"] += 1
                resolutions.append(decision_row)
            if classification.message_outcome == "INFORMATIONAL":
                counters["INFORMATIONAL_LINKED_TO_CASE"] += 1
            if classification.message_outcome == "UNCLEAR":
                counters["UNCLEAR_LINKED_TO_CASE"] += 1
        else:
            counters["MESSAGES_NOT_LINKED"] += 1
            if decision.method == "STRUCTURED" and decision.confidence == "MEDIUM":
                counters["STRUCTURED_MEDIUM"] += 1
            if decision.confidence == "LOW":
                counters["LOW_DECISIONS"] += 1
            if decision.method == "NEW_CASE":
                counters["NEW_CASE_DECISIONS"] += 1
            if decision.reason == "ambiguous_multiple_candidates":
                counters["AMBIGUOUS_DECISIONS"] += 1
                ambiguities.append({**decision_row, "CANDIDATES": ", ".join(sorted(case.case_key for case in candidates))})
            if decision.reason == "conflicting_functional_identifiers":
                counters["FUNCTIONAL_CONFLICTS"] += 1
                conflicts.append(decision_row)
        if not decision.matched and _originates_case(classification):
            case = ReplayCase(
                case_key=_case_key(email, email_references, row.get("message_id_hash")),
                organization=classification.organization,
                family=classification.family,
                event_type=classification.event_type,
                status="PENDING" if classification.message_outcome == "PENDING_ACTION" else "OPEN",
                action_type=classification.action_type,
                scope=classification.scope,
                opened_at=email.received_at,
                last_activity_at=email.received_at,
                functional_references=email_references,
            )
            _link(case, email)
            cases.append(case)
            index.add(case)
            decision_row["SIMULATED_CASE_KEY"] = case.case_key
            counters["SIMULATED_CASES"] = len(cases)
        elif decision.matched:
            case = decision.case
            _link(case, email)
            case.last_activity_at = email.received_at
            if classification.message_outcome == "SUCCESS":
                case.resolved_at = email.received_at
        else:
            counters["NEW_CASE_DECISIONS"] += 0
        if progress_callback and (processed % 1000 == 0 or processed == len(ordered_rows)):
            progress_callback({
                "processed": processed,
                "total": len(ordered_rows),
                "simulated_cases": len(cases),
                "candidate_average": candidate_total / processed,
                "candidate_max": candidate_max,
                "elapsed_seconds": perf_counter() - loop_started,
                "messages_per_second": processed / max(perf_counter() - loop_started, 1e-9),
            })
    counters["CANDIDATE_TOTAL"] = candidate_total
    counters["CANDIDATE_AVERAGE"] = round(candidate_total / len(ordered_rows), 4) if ordered_rows else 0
    counters["CANDIDATE_MAX"] = candidate_max
    counters["FEATURE_EXTRACTION_CALLS"] = feature_calls
    counters["FEATURE_EXTRACTION_CALLS_ESTIMATED_BEFORE"] = len(ordered_rows) + candidate_total
    counters["CASE_FEATURE_EXTRACTION_CALLS"] = case_feature_calls
    counters["CASE_FEATURE_EXTRACTION_CALLS_ESTIMATED_BEFORE"] = candidate_total
    counters["CLASSIFICATION_SECONDS"] = round(classification_seconds, 4)
    counters["CORRELATION_SECONDS"] = round(correlation_seconds, 4)
    diagnostics_started = perf_counter()
    merge_rows = []
    case_rows = []
    for case in cases:
        reasons = _false_merge_reasons(case)
        case_rows.append(_case_row(case, bool(reasons), reasons))
        if reasons:
            merge_rows.append({"case_key": case.case_key, "message_count": len(case.messages), "reasons": "; ".join(reasons), "LABEL": "POTENTIAL_FALSE_MERGE"})
    split_rows = _false_split_rows(cases)
    conversation_rows = _conversation_audit(cases)
    size_counts = Counter()
    for case in cases:
        count = len(case.messages)
        bucket = "1" if count == 1 else "2" if count == 2 else "3-5" if count <= 5 else "6-10" if count <= 10 else "11-20" if count <= 20 else "21-50" if count <= 50 else ">50"
        size_counts[bucket] += 1
    method_counts = Counter(row["METHOD"] for row in decisions)
    confidence_counts = Counter(row["CONFIDENCE"] or "NONE" for row in decisions)
    counters["POTENTIAL_FALSE_MERGES"] = len(merge_rows)
    counters["POTENTIAL_FALSE_SPLITS"] = len(split_rows)
    counters["HIGH_MATCH_RATE"] = round(100 * counters["MESSAGES_LINKED_HIGH"] / counters["TOTAL_MESSAGES"], 2) if counters["TOTAL_MESSAGES"] else 0
    counters["AMBIGUITY_RATE"] = round(100 * counters["AMBIGUOUS_DECISIONS"] / counters["TOTAL_MESSAGES"], 2) if counters["TOTAL_MESSAGES"] else 0
    counters["FUNCTIONAL_CONFLICT_RATE"] = round(100 * counters["FUNCTIONAL_CONFLICTS"] / counters["TOTAL_MESSAGES"], 2) if counters["TOTAL_MESSAGES"] else 0
    counters["POTENTIAL_FALSE_MERGE_RATE"] = round(100 * counters["POTENTIAL_FALSE_MERGES"] / max(1, len(cases)), 2)
    counters["POTENTIAL_FALSE_SPLIT_RATE"] = round(100 * counters["POTENTIAL_FALSE_SPLITS"] / max(1, len(cases)), 2)
    counters["CONVERSATION_RISK_RATE"] = round(100 * sum(row["category"] == "POTENTIAL_FALSE_MERGE" for row in conversation_rows) / max(1, len(conversation_rows)), 2)
    functional_audit = []
    all_values = defaultdict(list)
    for case in cases:
        seen_case_values = set()
        for link in case.messages:
            refs = extract_functional_references(link.email)
            for field, value in refs.as_dict().items():
                if value and (field, value) not in seen_case_values:
                    all_values[(field, value)].append(case)
                    seen_case_values.add((field, value))
    for (field, value), members in sorted(all_values.items()):
        functional_audit.append({"field": field, "value": value, "case_count": len({case.case_key for case in members}), "message_count": sum(len(case.messages) for case in members), "organizations": ", ".join(sorted({case.organization for case in members if case.organization})), "risk": "MULTIPLE_CASES" if len({case.case_key for case in members}) > 1 else ""})
    policy_audit = [row for row in functional_audit if row["field"] == "policy_number"]
    counters["DIAGNOSTICS_SECONDS"] = round(perf_counter() - diagnostics_started, 4)
    summary = [{"METRIC": key, "VALUE": value} for key, value in counters.items()]
    summary.extend({"METRIC": "CASE_SIZE_" + key, "VALUE": value} for key, value in sorted(size_counts.items()))
    summary.extend({"METRIC": "METHOD_" + key, "VALUE": value} for key, value in sorted(method_counts.items()))
    summary.extend({"METRIC": "CONFIDENCE_" + key, "VALUE": value} for key, value in sorted(confidence_counts.items()))
    excel_started = perf_counter()
    workbook = Workbook()
    workbook.remove(workbook.active)
    _write_sheet(workbook, "Resumen", summary)
    _write_sheet(workbook, "MessageDecisions", decisions)
    _write_sheet(workbook, "SimulatedCases", case_rows)
    _write_sheet(workbook, "CaseSizeDistribution", [{"SIZE_BUCKET": key, "CASE_COUNT": value} for key, value in sorted(size_counts.items())])
    _write_sheet(workbook, "MethodDistribution", [{"METHOD": key, "COUNT": value} for key, value in sorted(method_counts.items())])
    _write_sheet(workbook, "ConfidenceDistribution", [{"CONFIDENCE": key, "COUNT": value} for key, value in sorted(confidence_counts.items())])
    _write_sheet(workbook, "ConversationAudit", conversation_rows)
    _write_sheet(workbook, "FunctionalIdAudit", functional_audit)
    _write_sheet(workbook, "PolicyAudit", policy_audit)
    _write_sheet(workbook, "Ambiguities", ambiguities)
    _write_sheet(workbook, "FunctionalConflicts", conflicts)
    _write_sheet(workbook, "PotentialFalseMerges", merge_rows)
    _write_sheet(workbook, "PotentialFalseSplits", split_rows)
    _write_sheet(workbook, "PotentialResolutions", resolutions)
    _write_sheet(workbook, "HumanValidationSample", _human_sample(decisions, cases, ambiguities, conflicts, merge_rows, split_rows, resolutions))
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output_path)
    counters["EXCEL_WRITE_SECONDS"] = round(perf_counter() - excel_started, 4)
    counters["TOTAL_SECONDS"] = round(perf_counter() - loop_started, 4)
    counters["SIMULATED_CASES"] = len(cases)
    counters["CASE_SIZE_BUCKETS"] = dict(size_counts)
    return {"metrics": dict(counters), "decisions": decisions, "cases": cases, "case_rows": case_rows, "conversation_audit": conversation_rows, "functional_audit": functional_audit, "ambiguities": ambiguities, "conflicts": conflicts, "potential_false_merges": merge_rows, "potential_false_splits": split_rows, "potential_resolutions": resolutions, "output_path": str(output_path)}
