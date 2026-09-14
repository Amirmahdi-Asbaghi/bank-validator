"""Unit tests for the validation service layer.

The service layer is where the pipeline components are wired
together: parser → rule engine → Postgres persistence. These tests
exercise that wiring end-to-end, without going through HTTP.

These tests cover:
    - The sync path from bytes to persisted ValidationRun + records
    - The file hash calculation
    - Failure modes (bad CSV → failed run)
    - Idempotency (re-running the same content)

Why these matter:
    The API tests exercise the same path, but they add HTTP,
    serializers, and DRF's exception handling on top. When a bug
    is in the service layer, these tests point to it directly —
    the failure line is in services.py, not views.py.

Scope:
    These tests use MinIO via ``save_uploaded_file``. That's a real
    S3 call. The tests assume MinIO is running (via docker compose).
"""
from __future__ import annotations

import pytest

from apps.ingestion.services import (
    _file_hash,
    run_validation_sync,
)
from apps.validation.models import (
    BankReference,
    InvalidRecord,
    ValidRecord,
    ValidationRun,
)


def _seed_bank_reference():
    """Ensure the allow-list is populated before the test runs.

    Same reasoning as the API tests — without the reference codes,
    every row fails E004. A fixture could do this, but a helper
    function keeps the setup visible at each call site.
    """
    for code in ("010", "020", "030"):
        BankReference.objects.update_or_create(
            bank_code=code,
            defaults={"name": f"Bank {code}", "is_active": True},
        )


# ---------------------------------------------------------------------------
# File hash
# ---------------------------------------------------------------------------

def test_file_hash_is_deterministic():
    """The same bytes always produce the same hash."""
    content = b"bank_code,period\n010,1405/03\n"
    assert _file_hash(content) == _file_hash(content)


def test_file_hash_differs_for_different_content():
    """Different bytes produce different hashes."""
    assert _file_hash(b"a") != _file_hash(b"b")


def test_file_hash_is_sha256_hex():
    """The hash is a 64-character hex string (SHA-256)."""
    h = _file_hash(b"test")
    assert len(h) == 64
    # Hex characters only
    assert all(c in "0123456789abcdef" for c in h)


# ---------------------------------------------------------------------------
# Sync path — happy path
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_sync_valid_file_creates_completed_run():
    """A valid upload creates a ValidationRun with status=completed.

    The run should have the correct counts and no errors. This is
    the whole sync path in one test: parse → validate → persist.
    """
    _seed_bank_reference()

    content = (
        b"bank_code,period,account_code,debit,credit,balance\n"
        b"010,1405/03,A1,100.00,40.00,60.00\n"
        b"020,1405/03,A2,200.00,50.00,150.00\n"
    )
    run = run_validation_sync("valid.csv", content)

    # Run is complete with correct counts
    assert run.status == ValidationRun.Status.COMPLETED
    assert run.total_records == 2
    assert run.valid_count == 2
    assert run.invalid_count == 0
    assert run.duplicate_count == 0
    assert run.errors_by_code == {}

    # The valid records were persisted
    assert ValidRecord.objects.filter(run=run).count() == 2
    assert InvalidRecord.objects.filter(run=run).count() == 0

    # The source file was saved to MinIO
    assert run.source_file.startswith("s3://raw/uploads/")

    # Timestamps were set
    assert run.started_at is not None
    assert run.finished_at is not None
    # finished_at is after started_at
    assert run.finished_at >= run.started_at


@pytest.mark.django_db
def test_sync_invalid_file_separates_records():
    """Invalid rows go to InvalidRecord, valid ones to ValidRecord."""
    _seed_bank_reference()

    content = (
        b"bank_code,period,account_code,debit,credit,balance\n"
        b"999,1405/03,A1,100.00,40.00,60.00\n"   # E004 (bad bank)
        b"010,1405/03,A2,100.00,40.00,60.00\n"   # valid
    )
    run = run_validation_sync("mixed.csv", content)

    assert run.status == ValidationRun.Status.COMPLETED
    assert run.total_records == 2
    assert run.valid_count == 1
    assert run.invalid_count == 1

    assert ValidRecord.objects.filter(run=run).count() == 1
    assert InvalidRecord.objects.filter(run=run).count() == 1

    # The invalid record carries its errors
    invalid = InvalidRecord.objects.get(run=run)
    assert "E004" in invalid.error_codes
    assert invalid.raw_row["bank_code"] == "999"


@pytest.mark.django_db
def test_sync_persists_run_hash():
    """The run stores the SHA-256 hash of its content."""
    _seed_bank_reference()

    content = b"bank_code,period,account_code,debit,credit,balance\n010,1405/03,A1,100.00,40.00,60.00\n"
    run = run_validation_sync("hashed.csv", content)

    assert run.file_hash == _file_hash(content)
    assert len(run.file_hash) == 64


# ---------------------------------------------------------------------------
# Sync path — failure modes
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_sync_unparseable_csv_marks_run_failed():
    """A file that can't be parsed marks the run as failed.

    The run still exists (for audit) but its status is FAILED, and
    the error message explains why. No records are created.
    """
    content = b"not,a,valid\n\xff\xfe\xff\xfe"
    run = run_validation_sync("bad.csv", content)

    assert run.status == ValidationRun.Status.FAILED
    assert run.error_message  # non-empty
    assert run.finished_at is not None

    # No records were persisted
    assert ValidRecord.objects.filter(run=run).count() == 0
    assert InvalidRecord.objects.filter(run=run).count() == 0


@pytest.mark.django_db
def test_sync_empty_file_produces_empty_run():
    """A file with only a header produces zero records.

    Not a failure — an empty input is valid input. The run is
    completed with total_records=0.
    """
    _seed_bank_reference()

    content = b"bank_code,period,account_code,debit,credit,balance\n"
    run = run_validation_sync("empty.csv", content)

    # Completed with zero records — no error
    assert run.status == ValidationRun.Status.COMPLETED
    assert run.total_records == 0
    assert run.valid_count == 0
    assert run.invalid_count == 0


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_sync_same_content_produces_same_hash():
    """Two runs with the same content have the same file_hash.

    This is the basis for future idempotency: a re-upload of the
    same file can be detected by comparing hashes. The current
    implementation doesn't short-circuit, but the data supports it.
    """
    _seed_bank_reference()

    content = b"bank_code,period,account_code,debit,credit,balance\n010,1405/03,A1,100.00,40.00,60.00\n"

    run1 = run_validation_sync("first.csv", content)
    run2 = run_validation_sync("second.csv", content)

    # Two distinct runs
    assert run1.id != run2.id
    # Same content → same hash
    assert run1.file_hash == run2.file_hash
    # Both completed
    assert run1.status == ValidationRun.Status.COMPLETED
    assert run2.status == ValidationRun.Status.COMPLETED


# ---------------------------------------------------------------------------
# Duplicate detection across the batch
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_sync_duplicate_records_flagged():
    """Duplicate rows in the same file are flagged with E006."""
    _seed_bank_reference()

    content = (
        b"bank_code,period,account_code,debit,credit,balance,record_id\n"
        b"010,1405/03,A1,100.00,40.00,60.00,R1\n"
        b"010,1405/03,A1,100.00,40.00,60.00,R1\n"   # same record_id → duplicate
    )
    run = run_validation_sync("dupes.csv", content)

    assert run.total_records == 2
    assert run.valid_count == 1        # first wins
    assert run.invalid_count == 1
    assert run.duplicate_count == 1
    assert run.errors_by_code.get("E006") == 1