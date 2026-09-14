"""Upload service: parse file, run rules, persist valid/invalid records.

The orchestration layer between the API and the rule engine. Three
public entry points:

    run_validation_sync   — small files, pandas path, blocks the request
    run_validation_async  — large files, Kafka path, returns immediately
    run_validation_from_bytes — internal, runs the rules on a run

What lives here vs. elsewhere:

    parsers.py       knows about CSV and JSON
    rules/engine.py  knows about validation
    models.py        knows about persistence
    locator.py       knows about MinIO
    kafka_producer   knows about Kafka
    THIS FILE        knows the order and how to combine them

Design notes:

    - The sync path is transactional: either all records are persisted
      or none are. Partial writes would confuse the bank.
    - The async path is NOT transactional. It creates the run row and
      publishes the event; if the publish fails, the run is marked
      failed. Spark handles the rest asynchronously.
    - Prometheus counters are incremented here, at the only place that
      knows the final counts.
"""
from __future__ import annotations

import hashlib
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.storage.locator import save_uploaded_file
from apps.validation.models import (
    BankReference,
    InvalidRecord,
    ValidRecord,
    ValidationRun,
)

from apps.common.metrics import (
    RECORDS_UPLOADED_TOTAL,
    RECORDS_VALID_TOTAL,
    RECORDS_INVALID_TOTAL,
    RUNS_TOTAL,
)


from apps.validation.rules.engine import validate_batch

from .parsers import ParseError, parse_file


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _file_hash(content: bytes) -> str:
    """SHA-256 of the file content, as a hex string.

    Used for idempotency: the same bytes uploaded twice produce the
    same hash. Currently stored on ValidationRun but not yet used to
    short-circuit duplicate uploads — that's a documented follow-up.
    """
    return hashlib.sha256(content).hexdigest()


def _allowed_bank_codes() -> set[str]:
    """Load the bank allow-list once per batch.

    One query per validation run, not one per row. The engine receives
    the result via ``context["allowed_bank_codes"]`` and does O(1)
    lookups for each record.

    Only active banks are returned. Deactivated banks still appear in
    historical runs, but they can't be used in new uploads.
    """
    return set(
        BankReference.objects.filter(is_active=True).values_list("bank_code", flat=True)
    )


def _dec(value: Any):
    """Parse a value into Decimal, or return None on failure.

    Local helper. Same rationale as the one in rules/financial.py —
    each module owns its parsing so they can evolve independently.
    """
    from decimal import Decimal, InvalidOperation
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Core — runs the rules and writes valid/invalid rows
# ---------------------------------------------------------------------------

def run_validation_from_bytes(run: ValidationRun, content: bytes) -> ValidationRun:
    """Run the rule engine on the file content and persist the results.

    Does NOT set status to COMPLETED on its own — the caller controls
    that. This lets the sync path mark the run completed immediately
    after, and keeps the function usable from contexts where the run's
    lifecycle is managed elsewhere.

    On ParseError, the run is marked FAILED with a clear message
    and no exception is raised (the caller sees the failed run).
    On any other exception, the run is marked FAILED and the
    exception re-raises (so the caller knows something unexpected
    happened).

    Args:
        run:     the ValidationRun row (created by the caller).
        content: the raw bytes of the file.

    Returns:
        The same ValidationRun instance, with counts and status set.
    """
    try:
        # Step 1: parse bytes into dicts.
        # The filename is passed to the parser only for its extension
        # (CSV vs JSON), not for its content. That's why the S3 URI
        # works — it ends with .csv or .json.
        records = parse_file(run.source_file, content)

        # Step 2: run the rules.
        # The allow-list is fetched once and passed by context.
        result = validate_batch(records, allowed_bank_codes=_allowed_bank_codes())

        # Step 3: persist results in a transaction.
        # All-or-nothing. If we're re-validating an existing run
        # (idempotent replay), the delete + insert must be atomic,
        # or a mid-failure would leave the run with mixed old and
        # new records.
        with transaction.atomic():
            # Clean any prior rows for this run. Makes the function
            # safe to call twice on the same run_id.
            ValidRecord.objects.filter(run=run).delete()
            InvalidRecord.objects.filter(run=run).delete()

            for outcome in result["outcomes"]:
                raw = outcome["raw_row"]

                if outcome["is_valid"]:
                    # Valid record: extract typed fields from the
                    # dict. `.get(..., "")` and `or ""` handle both
                    # missing keys and None values.
                    ValidRecord.objects.create(
                        run=run,
                        row_number=outcome["row_number"],
                        bank_code=str(raw.get("bank_code", "")),
                        period=str(raw.get("period", "")),
                        account_code=str(raw.get("account_code", "")),
                        # Decimal fields default to 0 if parsing
                        # fails — but valid records by definition
                        # passed E002, so this default shouldn't
                        # ever trigger. Defensive.
                        debit=_dec(raw.get("debit")) or 0,
                        credit=_dec(raw.get("credit")) or 0,
                        balance=_dec(raw.get("balance")) or 0,
                        record_id=str(raw.get("record_id", "") or ""),
                        currency=str(raw.get("currency", "") or ""),
                        branch_code=str(raw.get("branch_code", "") or ""),
                        description=str(raw.get("description", "") or ""),
                    )
                else:
                    # Invalid record: keep the entire original row
                    # plus the failure reasons. This is the audit
                    # trail — nothing is lost.
                    InvalidRecord.objects.create(
                        run=run,
                        row_number=outcome["row_number"],
                        raw_row=raw,
                        error_codes=outcome["error_codes"],
                        error_messages=outcome["error_messages"],
                    )

        # Step 4: update the run's summary fields.
        # These are set OUTSIDE the transaction because they're
        # the run's own metadata — if the record inserts succeeded,
        # the summary matches. The two writes could technically be
        # in the same transaction; they're separate because the run
        # row already exists and we're updating it.
        summary = result["summary"]
        run.total_records = summary["total"]
        run.valid_count = summary["valid"]
        run.invalid_count = summary["invalid"]
        run.duplicate_count = summary["duplicates"]
        run.errors_by_code = summary["errors_by_code"]
        run.status = ValidationRun.Status.COMPLETED
        run.finished_at = timezone.now()

        # Prometheus counters — incremented at the only place that
        # knows the final numbers. Metrics failures must never break
        # the run, so the library swallows internal errors.
        RECORDS_UPLOADED_TOTAL.inc(summary["total"])
        RECORDS_VALID_TOTAL.inc(summary["valid"])
        RECORDS_INVALID_TOTAL.inc(summary["invalid"])

        run.save()

    except ParseError as e:
        # The file couldn't be parsed at all. Mark the run as failed
        # with the parse error message. Do NOT re-raise — the caller
        # (views.py) treats a failed run as a 400, and returning the
        # run object is enough.
        run.status = ValidationRun.Status.FAILED
        run.error_message = str(e)
        run.finished_at = timezone.now()
        run.save()
    except Exception as e:  # noqa: BLE001
        # Something unexpected — a bug, a DB error, an S3 error.
        # Mark the run failed for the audit trail, then re-raise so
        # the caller sees the exception (usually a 500).
        run.status = ValidationRun.Status.FAILED
        run.error_message = f"{type(e).__name__}: {e}"
        run.finished_at = timezone.now()
        run.save()
        raise

    return run


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def run_validation_sync(filename: str, content: bytes) -> ValidationRun:
    """Synchronous path — small files.

    Blocks the request until validation finishes. For files under the
    threshold (default 50 MB), this is fast enough to be synchronous —
    typically milliseconds for the sample files, a few seconds for
    files near the threshold.

    Steps:
        1. Save the raw file to MinIO.
        2. Create a ValidationRun row with status=RUNNING.
        3. Run the rules and persist records (via run_validation_from_bytes).
        4. Increment Prometheus counters.

    Returns the same run with status=COMPLETED and counts filled.
    """
    # Save to MinIO first so the run's source_file URI is available
    # before any DB writes.
    path = save_uploaded_file(filename, content)

    # Create the run row with status=RUNNING.
    # started_at is set now, not after processing — it marks when
    # the run entered the processing state.
    run = ValidationRun.objects.create(
        file_hash=_file_hash(content),
        source_file=path,
        file_size_bytes=len(content),
        status=ValidationRun.Status.RUNNING,
        started_at=timezone.now(),
    )

    # Increment the "sync path" counter. Called before processing,
    # so the counter reflects runs started, not runs completed. A
    # crash mid-run still shows in the counter — that's the right
    # behavior for an operational metric.
    RUNS_TOTAL.labels(path="sync").inc()  # Prometheus

    return run_validation_from_bytes(run, content)


def run_validation_async(filename: str, content: bytes) -> ValidationRun:
    """Asynchronous path — large files.

    Saves the file, creates the run with status=QUEUED, publishes a
    Kafka event, and returns immediately. Spark Structured Streaming
    consumes the event and does the actual validation.

    The API returns 202 with this run — the client polls the run_id
    for the result (which lands in ClickHouse once Spark finishes).

    Returns the run with status=QUEUED (or FAILED if the publish
    itself failed).
    """
    # Local import: kafka_producer imports Django settings, and we
    # only want that dependency loaded when the async path is used.
    # Keeps the sync path free of Kafka-related import cost.
    from .kafka_producer import publish_validation_event

    # Save the raw file to MinIO. Same step as the sync path.
    path = save_uploaded_file(filename, content)

    # Create the run row with status=QUEUED.
    # started_at stays None — the run hasn't started processing yet.
    # Spark will update started_at when it picks up the event
    # (in a future version; currently it doesn't write to Postgres).
    run = ValidationRun.objects.create(
        file_hash=_file_hash(content),
        source_file=path,
        file_size_bytes=len(content),
        status=ValidationRun.Status.QUEUED,
    )

    try:
        # Publish the event to Kafka. This is the decoupling point:
        # from here on, the pipeline is asynchronous.
        publish_validation_event(
            run_id=str(run.id),
            source_file=path,
            # Empty for now — the bank_code and period are inside
            # the file, not known at this point. A future version
            # could parse the first row to infer them.
            bank_code="",
            period="",
        )
    except Exception as e:  # noqa: BLE001
        # Kafka publish failed. The run exists but will never be
        # processed. Mark it failed so the audit trail is honest.
        # `update_fields` avoids writing unchanged columns.
        run.status = ValidationRun.Status.FAILED
        run.error_message = f"Kafka publish failed: {e}"
        run.save(update_fields=["status", "error_message"])
        # Re-raise so the caller knows the request failed. A publish
        # failure is typically a broker outage or a config problem,
        # not a user error — the caller returns a 500.
        raise

    # Increment the "async path" counter. Called after a successful
    # publish — a failed publish doesn't count as a real async run.
    RUNS_TOTAL.labels(path="async_kafka").inc()

    return run