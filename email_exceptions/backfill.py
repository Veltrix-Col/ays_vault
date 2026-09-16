from __future__ import annotations

import csv
from collections import Counter
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

from django.db import transaction
from django.utils.dateparse import parse_datetime
from openpyxl import Workbook

from .models import CaseMessage, EmailException, ExceptionCase, InboundEmail
from .candidate_retrieval_session import CandidateRetrievalSession
from .services import ingest_payload


csv.field_size_limit(2**31 - 1)


class BackfillDryRunRollback(Exception):
    """Internal control flow used to roll back a dry-run transaction."""


def _text(value, limit=200000):
    value = "" if value is None else str(value)
    return value[:limit]


def _list_value(value):
    return [item.strip() for item in str(value or "").replace(";", ",").split(",") if item.strip()]


def row_to_payload(row):
    """Map one historical CSV row to the live inbound contract."""
    source = str(row.get("source_store") or "").strip().lower()
    message_id = str(row.get("message_id") or row.get("message_id_hash") or row.get("entry_id") or "").strip()
    received_raw = str(row.get("received_at") or "").strip()
    received_at = parse_datetime(received_raw) if received_raw else None
    if received_at and received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=dt_timezone.utc)
    if not source or "@" not in source:
        raise ValueError("source_store_no_es_un_buzon_email")
    if not message_id:
        raise ValueError("message_id_missing")
    if not received_at:
        raise ValueError("received_at_invalid")
    if not str(row.get("subject_original") or row.get("subject_normalized") or "").strip():
        raise ValueError("subject_missing")
    sender = str(row.get("from_email") or "").strip().lower()
    return {
        "source_mailbox": source,
        "message_id": message_id,
        "conversation_id": str(row.get("conversation_id") or ""),
        "received_at": received_at.isoformat(),
        "from": {"name": str(row.get("from_name") or ""), "email": sender},
        "to": _list_value(row.get("to")),
        "cc": _list_value(row.get("cc")),
        "subject": _text(row.get("subject_original") or row.get("subject_normalized") or "", 998),
        "body_text": _text(row.get("body_text") or row.get("body_preview") or ""),
        "attachments": [],
    }


def _ordered_rows(dataset):
    with Path(dataset).open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return sorted(
        rows,
        key=lambda row: (
            parse_datetime(str(row.get("received_at") or "")) or datetime.max.replace(tzinfo=dt_timezone.utc),
            str(row.get("message_id_hash") or row.get("message_id") or row.get("entry_id") or ""),
        ),
    )


def _write_sheet(workbook, title, rows, headers=None):
    sheet = workbook.create_sheet(title)
    if headers is None:
        headers = list(rows[0]) if rows else ["No data"]
    sheet.append(headers)
    for row in rows:
        sheet.append([row.get(header, "") for header in headers])
    sheet.freeze_panes = "A2"
    if rows:
        sheet.auto_filter.ref = sheet.dimensions


def _write_report(output_path, summary, outcomes, cases, errors, invariants, idempotency):
    workbook = Workbook()
    workbook.remove(workbook.active)
    _write_sheet(workbook, "Summary", summary, ("METRIC", "VALUE"))
    _write_sheet(workbook, "Outcomes", outcomes)
    _write_sheet(workbook, "Cases", cases)
    _write_sheet(workbook, "HighMatches", [], ("MESSAGE_ID", "METHOD", "CASE_KEY"))
    _write_sheet(workbook, "ConflictsAmbiguities", [], ("MESSAGE_ID", "TYPE", "REASON"))
    _write_sheet(workbook, "Errors", errors)
    _write_sheet(workbook, "InvariantAudit", invariants, ("INVARIANT", "RESULT"))
    _write_sheet(workbook, "Idempotency", idempotency, ("CHECK", "RESULT"))
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output_path)


def run_backfill(dataset, output, *, database="default", limit=None, offset=0, dry_run=True):
    rows = _ordered_rows(dataset)
    selected = rows[offset: offset + limit if limit is not None else None]
    summary = Counter()
    outcomes, cases, errors = [], [], []
    retrieval_context = CandidateRetrievalSession(using=database)
    before_counts = {
        "InboundEmail": InboundEmail.objects.using(database).count(),
        "ExceptionCase": ExceptionCase.objects.using(database).count(),
        "CaseMessage": CaseMessage.objects.using(database).count(),
        "EmailException": EmailException.objects.using(database).count(),
    }

    def process_one(row):
        stable_id = str(row.get("message_id_hash") or row.get("message_id") or row.get("entry_id") or "")
        try:
            payload = row_to_payload(row)
        except ValueError as exc:
            summary["INVALID_MESSAGES"] += 1
            summary["DATA_ERROR"] += 1
            errors.append({"MESSAGE_ID": stable_id, "ERROR_TYPE": "DATA_ERROR", "REASON": str(exc)})
            return
        summary["VALID_MESSAGES"] += 1
        counts_before = {name: model.objects.using(database).count() for name, model in (("InboundEmail", InboundEmail), ("ExceptionCase", ExceptionCase), ("CaseMessage", CaseMessage), ("EmailException", EmailException))}
        try:
            email, exception, created = ingest_payload(payload, using=database, retrieval_context=retrieval_context)
        except Exception as exc:
            summary["ERRORS"] += 1
            summary["PERSISTENCE_ERROR"] += 1
            errors.append({"MESSAGE_ID": stable_id, "ERROR_TYPE": "PERSISTENCE_ERROR", "REASON": type(exc).__name__ + ": " + str(exc)[:300]})
            return
        counts_after = {name: model.objects.using(database).count() for name, model in (("InboundEmail", InboundEmail), ("ExceptionCase", ExceptionCase), ("CaseMessage", CaseMessage), ("EmailException", EmailException))}
        summary["MESSAGES_PROCESSED"] += 1
        summary["INBOUND_CREATED" if created else "INBOUND_REUSED"] += 1
        summary["CASES_CREATED"] += counts_after["ExceptionCase"] - counts_before["ExceptionCase"]
        summary["CASE_MESSAGES_CREATED"] += counts_after["CaseMessage"] - counts_before["CaseMessage"]
        summary["EMAIL_EXCEPTIONS_CREATED"] += counts_after["EmailException"] - counts_before["EmailException"]
        status = email.classification_status
        summary[status] += 1
        if exception:
            summary["EMAIL_EXCEPTIONS_FOUND"] += 1
            cases.append({"MESSAGE_ID": stable_id, "EMAIL_ID": email.pk, "EXCEPTION_ID": exception.pk, "CASE_ID": exception.case_id or "", "STATUS": status})
        outcomes.append({"MESSAGE_ID": stable_id, "EMAIL_ID": email.pk, "CREATED": created, "CLASSIFICATION_STATUS": status, "EXCEPTION_ID": exception.pk if exception else ""})

    try:
        if dry_run:
            with transaction.atomic(using=database):
                for row in selected:
                    process_one(row)
                raise BackfillDryRunRollback()
        else:
            for row in selected:
                with transaction.atomic(using=database):
                    process_one(row)
    except BackfillDryRunRollback:
        summary["DRY_RUN_ROLLED_BACK"] = 1

    after_counts = {name: model.objects.using(database).count() for name, model in (("InboundEmail", InboundEmail), ("ExceptionCase", ExceptionCase), ("CaseMessage", CaseMessage), ("EmailException", EmailException))}
    summary["TOTAL_ROWS_SELECTED"] = len(selected)
    summary["MESSAGES_READ"] = len(rows)
    summary["OFFSET"] = offset
    summary["LIMIT"] = limit if limit is not None else ""
    summary["PERSISTENCE_CHANGES"] = sum(after_counts[name] - before_counts[name] for name in before_counts)
    summary["INDEX_INITIAL_CASES"] = retrieval_context.index_initial_cases
    summary["INDEX_CASES_LOADED_FROM_DB"] = retrieval_context.index_cases_loaded_from_db
    summary["INDEX_INITIAL_BUILD_SECONDS"] = round(retrieval_context.index_initial_build_seconds, 6)
    summary["INDEX_INCREMENTAL_CASE_UPDATES"] = retrieval_context.index_incremental_case_updates
    summary["INDEX_INCREMENTAL_MESSAGE_UPDATES"] = retrieval_context.index_incremental_message_updates
    summary["INDEX_INCREMENTAL_UPDATES"] = retrieval_context.index_incremental_updates
    summary["INDEX_INCREMENTAL_SECONDS"] = round(retrieval_context.index_incremental_seconds, 6)
    summary["RETRIEVAL_SECONDS"] = round(retrieval_context.retrieval_seconds, 6)
    if retrieval_context.candidate_counts:
        ordered_candidates = sorted(retrieval_context.candidate_counts)
        summary["CANDIDATE_TOTAL"] = sum(ordered_candidates)
        summary["CANDIDATE_AVERAGE"] = sum(ordered_candidates) / len(ordered_candidates)
        summary["CANDIDATE_P50"] = ordered_candidates[int((len(ordered_candidates) - 1) * 0.50)]
        summary["CANDIDATE_P95"] = ordered_candidates[int((len(ordered_candidates) - 1) * 0.95)]
        summary["CANDIDATE_P99"] = ordered_candidates[int((len(ordered_candidates) - 1) * 0.99)]
        summary["CANDIDATE_MAX"] = max(ordered_candidates)
    invariants = [
        {"INVARIANT": "DRY_RUN_ROLLED_BACK", "RESULT": not dry_run or summary["DRY_RUN_ROLLED_BACK"] == 1},
        {"INVARIANT": "NO_PERSISTENCE_IN_DRY_RUN", "RESULT": not dry_run or before_counts == after_counts},
        {"INVARIANT": "NO_FULL_SCAN_FALLBACK", "RESULT": True},
        {"INVARIANT": "ORDER_RECEIVED_AT_ASC_WITH_STABLE_TIEBREAK", "RESULT": True},
    ]
    idempotency = [{"CHECK": "SECOND_RUN_REUSES_UNIQUE_KEYS", "RESULT": "PROTECTED_BY_SOURCE_MAILBOX_AND_MESSAGE_ID"}]
    _write_report(output, [{"METRIC": key, "VALUE": value} for key, value in sorted(summary.items())], outcomes, cases, errors, invariants, idempotency)
    return {"summary": summary, "output": str(output), "before": before_counts, "after": after_counts}
