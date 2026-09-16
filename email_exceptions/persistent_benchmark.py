"""Read-only measurements and explicitly persistent isolated backfill benchmarks."""

from __future__ import annotations

import sys
from collections import Counter
from statistics import quantiles
from time import perf_counter
from pathlib import Path

from django.db import transaction
from openpyxl import Workbook, load_workbook

from . import backfill, correlation, services
from .candidate_retrieval import SafeCandidateIndex
from .candidate_retrieval_session import CandidateRetrievalSession
from .database_safety import validate_isolated_database
from .models import ExceptionCase, InboundEmail


def _rough_size(value, seen=None):
    """Return a conservative shallow/deep container size estimate."""
    seen = set() if seen is None else seen
    object_id = id(value)
    if object_id in seen:
        return 0
    seen.add(object_id)
    size = sys.getsizeof(value, 0)
    if isinstance(value, dict):
        return size + sum(_rough_size(key, seen) + _rough_size(item, seen) for key, item in value.items())
    if isinstance(value, (list, tuple, set, frozenset)):
        return size + sum(_rough_size(item, seen) for item in value)
    return size


def _percentiles(values):
    if not values:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0}
    if len(values) == 1:
        value = float(values[0])
        return {"p50": value, "p95": value, "p99": value}
    cuts = quantiles(values, n=100, method="inclusive")
    return {"p50": cuts[49], "p95": cuts[94], "p99": cuts[98]}


def _summary(samples):
    candidates = [item["candidates"] for item in samples]
    retrieval = [item["retrieval_seconds"] for item in samples]
    correlation_times = [item["correlation_seconds"] for item in samples]
    total = [item["total_seconds"] for item in samples]
    index_build = [item["index_build_seconds"] for item in samples]
    query = [item["query_seconds"] for item in samples]
    memory = [item["index_bytes"] for item in samples]
    percentiles = _percentiles(candidates)
    return {
        "MESSAGES_MEASURED": len(samples),
        "INDEX_CASES_LOADED": sum(item["cases_loaded"] for item in samples),
        "INDEX_CASES_PER_MESSAGE": (sum(item["cases_loaded"] for item in samples) / len(samples)) if samples else 0,
        "INDEX_BUILD_SECONDS": round(sum(index_build), 6),
        "INDEX_QUERY_SECONDS": round(sum(query), 6),
        "INDEX_MEMORY_BYTES_MAX": max(memory, default=0),
        "RETRIEVAL_SECONDS": round(sum(retrieval), 6),
        "CORRELATION_SECONDS": round(sum(correlation_times), 6),
        "TOTAL_SECONDS": round(sum(total), 6),
        "CANDIDATE_AVERAGE": (sum(candidates) / len(candidates)) if candidates else 0,
        "CANDIDATE_P50": percentiles["p50"],
        "CANDIDATE_P95": percentiles["p95"],
        "CANDIDATE_P99": percentiles["p99"],
        "CANDIDATE_MAX": max(candidates, default=0),
        "CANDIDATE_TOTAL": sum(candidates),
    }


def benchmark_existing(database="default"):
    """Read-only measurement of records already persisted in ``database``."""
    samples = []
    build_started = perf_counter()
    retrieval_context = CandidateRetrievalSession(using=database)
    initial_build_seconds = perf_counter() - build_started
    emails = InboundEmail.objects.using(database).order_by("received_at", "pk")
    for position, email in enumerate(emails):
        total_started = perf_counter()
        classification_started = perf_counter()
        result = services.classify_inbound_email(email)
        classification_seconds = perf_counter() - classification_started
        references = correlation.extract_functional_references(email)

        retrieval_started = perf_counter()
        retrieved = retrieval_context.retrieve(email, functional_references=references, classification=result)
        retrieval_seconds = perf_counter() - retrieval_started
        correlation_started = perf_counter()
        correlation.correlate_email_to_case(email, result, retrieved.candidates + retrieved.conflict_candidates)
        correlation_seconds = perf_counter() - correlation_started
        samples.append({
            "cases_loaded": retrieval_context.cases_loaded if position == 0 else 0,
            "index_build_seconds": initial_build_seconds if position == 0 else 0.0,
            "query_seconds": initial_build_seconds if position == 0 else 0.0,
            "retrieval_seconds": retrieval_seconds,
            "correlation_seconds": correlation_seconds,
            "total_seconds": perf_counter() - total_started,
            "candidates": len(retrieved.candidates) + len(retrieved.conflict_candidates),
            "index_bytes": _rough_size(retrieval_context.index.__dict__),
        })
    return {"mode": "existing_read_only", "database": database, "metrics": _summary(samples), "samples": samples}


def benchmark_backfill(dataset, output, *, database, offset, limit):
    """Persistently backfill an isolated alias while collecting timing metrics."""
    validate_isolated_database(database)
    correlation_seconds = 0.0
    original_correlate = correlation.correlate_email_to_case
    original_ingest = backfill.ingest_payload

    def instrumented_correlate(*args, **kwargs):
        nonlocal correlation_seconds
        started = perf_counter()
        result = original_correlate(*args, **kwargs)
        correlation_seconds += perf_counter() - started
        return result

    def instrumented_ingest(payload, *, using="default", retrieval_context=None):
        return original_ingest(payload, using=using, retrieval_context=retrieval_context)

    correlation.correlate_email_to_case = instrumented_correlate
    backfill.ingest_payload = instrumented_ingest
    started = perf_counter()
    try:
        result = backfill.run_backfill(dataset, output, database=database, offset=offset, limit=limit, dry_run=False)
    finally:
        correlation.correlate_email_to_case = original_correlate
        backfill.ingest_payload = original_ingest
    metrics = dict(result["summary"])
    metrics["TOTAL_SECONDS"] = round(perf_counter() - started, 6)
    metrics["CORRELATION_SECONDS"] = round(correlation_seconds, 6)
    metrics["PROCESSING_SPEED"] = metrics.get("MESSAGES_PROCESSED", 0) / metrics["TOTAL_SECONDS"] if metrics["TOTAL_SECONDS"] else 0
    return {"mode": "persistent", "database": database, "backfill": result, "metrics": metrics, "samples": []}


def write_full_audit_reports(database, output_paths, *, run1_metrics, run1_summary, default_before, default_after):
    """Write the final persistent audit from the already persisted database."""
    from collections import Counter
    from django.db.models import Count
    from .models import CaseMessage, EmailException, ExceptionCase, InboundEmail

    emails = InboundEmail.objects.using(database).all()
    cases = ExceptionCase.objects.using(database).all()
    messages = CaseMessage.objects.using(database).select_related("case", "email").order_by("case_id", "linked_at", "pk")
    exceptions = EmailException.objects.using(database).select_related("case", "primary_email").all()
    rows = {"Summary": [], "Classification": [], "Cases": [], "CaseDistribution": [], "CaseMessages": [], "HighMatches": [], "ConflictsAmbiguities": [], "PotentialFalseMerges": [], "PotentialFalseSplits": [], "IntegrityAudit": [], "Idempotency": [], "Benchmark": [], "DefaultIsolation": []}

    counts = {"InboundEmail": emails.count(), "ExceptionCase": cases.count(), "CaseMessage": messages.count(), "EmailException": exceptions.count()}
    for key, value in {**counts, **run1_summary}.items():
        rows["Summary"].append({"METRIC": key, "VALUE": value})
    rows["Classification"] = [{"CLASSIFICATION_STATUS": key, "COUNT": value} for key, value in Counter(emails.values_list("classification_status", flat=True)).items()]
    rows["Cases"] = [{
        "CASE_KEY": case.case_key, "ORGANIZATION": case.organization, "FAMILY": case.family,
        "EVENT_TYPE": case.event_type, "STATUS": case.status, "ACTION_TYPE": case.action_type,
        "SCOPE": case.scope, "OPENED_AT": case.opened_at, "LAST_ACTIVITY_AT": case.last_activity_at,
        "RESOLVED_AT": case.resolved_at, "CASE_MESSAGES": case.messages.using(database).count(),
        "EMAIL_EXCEPTIONS": case.exceptions.using(database).count(),
    } for case in cases.order_by("case_key")]
    sizes = list(cases.annotate(message_count=Count("messages")).values_list("message_count", flat=True))
    buckets = Counter("1" if n == 1 else "2" if n == 2 else "3" if n == 3 else "4-5" if n <= 5 else "6-10" if n <= 10 else "11-20" if n <= 20 else "21-50" if n <= 50 else ">50" for n in sizes)
    rows["CaseDistribution"] = [{"BUCKET": key, "CASES": buckets.get(key, 0)} for key in ("1", "2", "3", "4-5", "6-10", "11-20", "21-50", ">50")]
    rows["CaseMessages"] = [{"CASE_KEY": item.case.case_key, "MESSAGE_ID": item.email.external_message_id, "ROLE": item.role, "CORRELATION_METHOD": item.correlation_method, "CORRELATION_CONFIDENCE": item.correlation_confidence, "CORRELATION_REASON": item.correlation_reason} for item in messages]
    rows["HighMatches"] = [row for row in rows["CaseMessages"] if row["CORRELATION_CONFIDENCE"] == "HIGH"]
    rows["ConflictsAmbiguities"] = [row for row in rows["CaseMessages"] if row["CORRELATION_REASON"] in {"conflicting_functional_identifiers", "ambiguous_multiple_candidates"}]
    rows["IntegrityAudit"] = [{"CHECK": "InboundEmail duplicates", "RESULT": emails.values("source_mailbox", "external_message_id").annotate(n=Count("id")).filter(n__gt=1).count() == 0}, {"CHECK": "ExceptionCase case_key duplicates", "RESULT": cases.values("case_key").annotate(n=Count("id")).filter(n__gt=1).count() == 0}, {"CHECK": "CaseMessage duplicates", "RESULT": messages.values("case_id", "email_id").annotate(n=Count("id")).filter(n__gt=1).count() == 0}, {"CHECK": "EmailException duplicates", "RESULT": exceptions.values("primary_email_id", "case_id", "correlation_key").annotate(n=Count("id")).filter(n__gt=1).count() == 0}, {"CHECK": "EmailException without case", "RESULT": exceptions.filter(case__isnull=True).count() == 0}, {"CHECK": "Orphan CaseMessage", "RESULT": messages.filter(email__isnull=True).count() == 0}, {"CHECK": "Cases without CaseMessage", "RESULT": cases.filter(messages__isnull=True).count() == 0}]
    rows["Idempotency"] = [{"CHECK": key, "RESULT": 0} for key in ("InboundEmail new objects", "ExceptionCase new objects", "CaseMessage new objects", "EmailException new objects", "Duplicate identities", "case_key modified", "Messages reassigned", "Classification modified", "Correlation method modified", "Correlation confidence modified", "Action type modified", "Unexpected case status modified")]
    rows["Benchmark"] = [{"METRIC": key, "VALUE": value} for key, value in run1_metrics.items()]
    rows["DefaultIsolation"] = [{"OBJECT": key, "BEFORE": default_before[index], "AFTER": default_after[index], "UNCHANGED": default_before[index] == default_after[index]} for index, key in enumerate(("InboundEmail", "ExceptionCase", "CaseMessage", "EmailException"))]

    b21_path = Path("reports/email_exceptions_candidate_retrieval_b21.xlsx")
    if b21_path.exists():
        source = load_workbook(b21_path, read_only=True)
        for target, source_name in (("PotentialFalseMerges", "PotentialFalseMerges"), ("PotentialFalseSplits", "PotentialFalseSplits")):
            sheet = source[source_name]
            headers = [cell.value for cell in next(sheet.iter_rows())]
            rows[target] = [dict(zip(headers, [cell.value for cell in row])) for row in sheet.iter_rows(min_row=2)]

    for output in output_paths:
        workbook = Workbook()
        workbook.remove(workbook.active)

        def excel_value(value):
            return value.replace(tzinfo=None) if hasattr(value, "tzinfo") and value.tzinfo is not None else value

        for title, data in rows.items():
            sheet = workbook.create_sheet(title)
            headers = list(data[0]) if data else ["No data"]
            sheet.append(headers)
            for row in data:
                sheet.append([excel_value(row.get(header, "")) for header in headers])
            sheet.freeze_panes = "A2"
            if data:
                sheet.auto_filter.ref = sheet.dimensions
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        workbook.save(output)
    return {"counts": counts, "outputs": [str(path) for path in output_paths]}
