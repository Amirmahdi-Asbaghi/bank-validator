"""Upload service: parse file, run rules, persist valid/invalid records."""
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
    return hashlib.sha256(content).hexdigest()


def _allowed_bank_codes() -> set[str]:
    return set(
        BankReference.objects.filter(is_active=True).values_list("bank_code", flat=True)
    )


def _dec(value: Any):
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
    """
    Run rules on the given bytes and populate the run with results.
    Does NOT set status to COMPLETED — the caller controls status.
    """
    try:
        records = parse_file(run.source_file, content)
        result = validate_batch(records, allowed_bank_codes=_allowed_bank_codes())

        with transaction.atomic():
            # Clean any prior rows for this run (idempotent re-runs)
            ValidRecord.objects.filter(run=run).delete()
            InvalidRecord.objects.filter(run=run).delete()

            for outcome in result["outcomes"]:
                raw = outcome["raw_row"]
                if outcome["is_valid"]:
                    ValidRecord.objects.create(
                        run=run,
                        row_number=outcome["row_number"],
                        bank_code=str(raw.get("bank_code", "")),
                        period=str(raw.get("period", "")),
                        account_code=str(raw.get("account_code", "")),
                        debit=_dec(raw.get("debit")) or 0,
                        credit=_dec(raw.get("credit")) or 0,
                        balance=_dec(raw.get("balance")) or 0,
                        record_id=str(raw.get("record_id", "") or ""),
                        currency=str(raw.get("currency", "") or ""),
                        branch_code=str(raw.get("branch_code", "") or ""),
                        description=str(raw.get("description", "") or ""),
                    )
                else:
                    InvalidRecord.objects.create(
                        run=run,
                        row_number=outcome["row_number"],
                        raw_row=raw,
                        error_codes=outcome["error_codes"],
                        error_messages=outcome["error_messages"],
                    )

        summary = result["summary"]
        run.total_records = summary["total"]
        run.valid_count = summary["valid"]
        run.invalid_count = summary["invalid"]
        run.duplicate_count = summary["duplicates"]
        run.errors_by_code = summary["errors_by_code"]
        run.status = ValidationRun.Status.COMPLETED
        run.finished_at = timezone.now()
        # Prometheus counters
        RECORDS_UPLOADED_TOTAL.inc(summary["total"])
        RECORDS_VALID_TOTAL.inc(summary["valid"])
        RECORDS_INVALID_TOTAL.inc(summary["invalid"])
        run.save()

    except ParseError as e:
        run.status = ValidationRun.Status.FAILED
        run.error_message = str(e)
        run.finished_at = timezone.now()
        run.save()
    except Exception as e:  # noqa: BLE001
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
    """Synchronous path — small files. Blocks the request until done."""
    path = save_uploaded_file(filename, content)

    run = ValidationRun.objects.create(
        file_hash=_file_hash(content),
        source_file=path,
        file_size_bytes=len(content),
        status=ValidationRun.Status.RUNNING,
        started_at=timezone.now(),
    )

    RUNS_TOTAL.labels(path="sync").inc() #promethus

    return run_validation_from_bytes(run, content)


def run_validation_async(filename: str, content: bytes) -> ValidationRun:
    """
    Asynchronous path — large files.
    Save the file to MinIO, publish a Kafka event, return immediately.
    The Spark consumer (later) will pick this up.
    """
    from .kafka_producer import publish_validation_event

    path = save_uploaded_file(filename, content)

    run = ValidationRun.objects.create(
        file_hash=_file_hash(content),
        source_file=path,
        file_size_bytes=len(content),
        status=ValidationRun.Status.QUEUED,
    )

    try:
        publish_validation_event(
            run_id=str(run.id),
            source_file=path,
            bank_code="",
            period="",
        )
    except Exception as e:  # noqa: BLE001
        run.status = ValidationRun.Status.FAILED
        run.error_message = f"Kafka publish failed: {e}"
        run.save(update_fields=["status", "error_message"])
        raise

    RUNS_TOTAL.labels(path="async_kafka").inc()

    return run