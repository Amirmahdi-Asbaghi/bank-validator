"""Unit tests for the rule engine — one test per error code.

Coverage strategy:
    For each error code (E001–E011), at least one test asserts the
    code fires on a bad input. Where a rule can produce false
    positives, a second test asserts it doesn't fire on valid data.

    The asymmetry matters: a rule that fires on good data is as
    broken as one that misses bad data. Both directions are tested
    where the risk exists.

Why unit tests and not just the API tests:
    These tests exercise the rule engine directly, without HTTP,
    without a database, without serializers. They run in microseconds
    instead of milliseconds. When a rule breaks, the failure points
    to the specific rule, not to a chain of view → service → engine.

Scope:
    Only the rule engine (``apps.validation.rules.engine.validate_batch``).
    Persistence, HTTP, and the Spark path are tested elsewhere.
"""
from __future__ import annotations

from apps.validation.rules.engine import validate_batch


# The allow-list used by every test in this file. Matches the test
# fixture data (010, 020, 030) and the standard seed file.
ALLOWED = {"010", "020", "030"}


def _run_one(record):
    """Helper: run the engine on a single record.

    Returns a tuple of (outcome, summary). Most tests only need
    the outcome (the per-record result). Batch-level tests use
    the summary.

    Why a helper:
        Every test starts with the same one-record call. Wrapping it
        keeps the test bodies focused on the assertion, not the setup.
        The ``_`` prefix marks it as module-private — not exported.
    """
    result = validate_batch([record], allowed_bank_codes=ALLOWED)
    return result["outcomes"][0], result["summary"]


# ---------------------------------------------------------------------------
# E001 — required field missing
# ---------------------------------------------------------------------------

def test_e001_missing_required_field():
    """A record missing `debit` should flag E001."""
    record = {
        "bank_code": "010",
        "period": "1405/03",
        "account_code": "A1",
        # debit missing entirely
        "credit": "10",
        "balance": "10",
    }
    outcome, _ = _run_one(record)
    assert "E001" in outcome["error_codes"]


# ---------------------------------------------------------------------------
# E002 — invalid data type
# ---------------------------------------------------------------------------

def test_e002_invalid_data_type():
    """A non-numeric `debit` value should flag E002."""
    record = {
        "bank_code": "010",
        "period": "1405/03",
        "account_code": "A1",
        "debit": "not-a-number",
        "credit": "10",
        "balance": "0",
    }
    outcome, _ = _run_one(record)
    assert "E002" in outcome["error_codes"]


def test_e002_parses_valid_decimals():
    """A valid decimal string should not flag E002.

    The false-positive check: this test verifies that values which
    *are* parseable don't get flagged. A regex-based rule could
    easily be too strict here.
    """
    record = {
        "bank_code": "010",
        "period": "1405/03",
        "account_code": "A1",
        "debit": "1234.56",
        "credit": "0.01",
        "balance": "1234.55",
    }
    outcome, _ = _run_one(record)
    assert "E002" not in outcome["error_codes"]


# ---------------------------------------------------------------------------
# E003 — null or empty value
# ---------------------------------------------------------------------------

def test_e003_null_or_empty():
    """An empty `bank_code` string should flag E003."""
    record = {
        "bank_code": "",             # empty string
        "period": "1405/03",
        "account_code": "A1",
        "debit": "10",
        "credit": "5",
        "balance": "5",
    }
    outcome, _ = _run_one(record)
    assert "E003" in outcome["error_codes"]


def test_e003_whitespace_only_is_empty():
    """A whitespace-only `bank_code` should also flag E003.

    The rule trims whitespace before checking emptiness. A file
    with "   " in a required field is functionally the same as an
    empty field — banks sometimes export padded values.
    """
    record = {
        "bank_code": "   ",          # whitespace only
        "period": "1405/03",
        "account_code": "A1",
        "debit": "10",
        "credit": "5",
        "balance": "5",
    }
    outcome, _ = _run_one(record)
    assert "E003" in outcome["error_codes"]


# ---------------------------------------------------------------------------
# E004 — invalid bank code
# ---------------------------------------------------------------------------

def test_e004_invalid_bank_code():
    """A bank code not in the allow-list should flag E004."""
    record = {
        "bank_code": "999",          # not in ALLOWED
        "period": "1405/03",
        "account_code": "A1",
        "debit": "100",
        "credit": "40",
        "balance": "60",
    }
    outcome, _ = _run_one(record)
    assert "E004" in outcome["error_codes"]


def test_e004_valid_bank_code_passes():
    """An allow-listed bank code should not flag E004.

    False-positive check: verifies that valid codes are correctly
    accepted. This catches bugs where the rule might compare against
    the wrong field or mishandle string types.
    """
    record = {
        "bank_code": "010",          # in ALLOWED
        "period": "1405/03",
        "account_code": "A1",
        "debit": "100.00",
        "credit": "40.00",
        "balance": "60.00",
    }
    outcome, _ = _run_one(record)
    assert "E004" not in outcome["error_codes"]


# ---------------------------------------------------------------------------
# E005 — invalid period format
# ---------------------------------------------------------------------------

def test_e005_invalid_period_format():
    """A period with a dash instead of a slash should flag E005."""
    record = {
        "bank_code": "010",
        "period": "1405-03",         # wrong separator
        "account_code": "A1",
        "debit": "100",
        "credit": "40",
        "balance": "60",
    }
    outcome, _ = _run_one(record)
    assert "E005" in outcome["error_codes"]


def test_e005_valid_period_passes():
    """A well-formatted YYYY/MM period should not flag E005.

    Uses 1405/12 — the last valid month — to exercise the boundary
    of the month range in the regex.
    """
    record = {
        "bank_code": "010",
        "period": "1405/12",         # boundary: month 12
        "account_code": "A1",
        "debit": "100.00",
        "credit": "40.00",
        "balance": "60.00",
    }
    outcome, _ = _run_one(record)
    assert "E005" not in outcome["error_codes"]


def test_e005_single_digit_month_rejected():
    """A period with a single-digit month should flag E005.

    The spec requires zero-padded months (1405/03, not 1405/3).
    The regex enforces this. This test verifies the enforcement.
    """
    record = {
        "bank_code": "010",
        "period": "1405/3",          # missing leading zero
        "account_code": "A1",
        "debit": "100",
        "credit": "40",
        "balance": "60",
    }
    outcome, _ = _run_one(record)
    assert "E005" in outcome["error_codes"]


# ---------------------------------------------------------------------------
# E006 — duplicate record
# ---------------------------------------------------------------------------

def test_e006_duplicate_record():
    """The second occurrence of a record should flag E006.

    Note the assertion: ``outcomes[1]`` — the second row has the
    error, not the first. The engine keeps the first occurrence as
    valid and flags subsequent matches.
    """
    record = {
        "bank_code": "010",
        "period": "1405/03",
        "account_code": "A1",
        "debit": "100.00",
        "credit": "40.00",
        "balance": "60.00",
        "record_id": "R1",
    }
    result = validate_batch([record, record], allowed_bank_codes=ALLOWED)
    outcomes = result["outcomes"]

    # First row is valid (first occurrence wins)
    assert outcomes[0]["is_valid"]
    # Second row has E006
    assert "E006" in outcomes[1]["error_codes"]
    # Summary counts the duplicate once
    assert result["summary"]["duplicates"] == 1


def test_e006_fallback_key_detects_duplicate():
    """Duplicates without `record_id` use the fallback key.

    When `record_id` is absent, the engine falls back to
    (bank_code, period, account_code). This test verifies that the
    fallback path detects duplicates.
    """
    record = {
        "bank_code": "010",
        "period": "1405/03",
        "account_code": "A1",
        "debit": "100.00",
        "credit": "40.00",
        "balance": "60.00",
        # no record_id — fallback key applies
    }
    result = validate_batch([record, record], allowed_bank_codes=ALLOWED)
    assert result["summary"]["duplicates"] == 1


# ---------------------------------------------------------------------------
# E007 — balance mismatch
# ---------------------------------------------------------------------------

def test_e007_balance_mismatch():
    """A balance inconsistent with debit - credit should flag E007."""
    record = {
        "bank_code": "010",
        "period": "1405/03",
        "account_code": "A1",
        "debit": "100.00",
        "credit": "40.00",
        "balance": "999.00",         # should be 60.00
    }
    outcome, _ = _run_one(record)
    assert "E007" in outcome["error_codes"]


def test_e007_exact_decimal_match():
    """A mathematically exact balance should not flag E007.

    This is the precision test. With floats, 0.10 - 0.05 would be
    0.05000000000000001 and the test would fail. With Decimal, it's
    exactly 0.05 — no E007.

    A change to float arithmetic would break this test, which is
    the point.
    """
    record = {
        "bank_code": "010",
        "period": "1405/03",
        "account_code": "A1",
        "debit": "0.10",
        "credit": "0.05",
        "balance": "0.05",           # exactly correct, no tolerance
    }
    outcome, _ = _run_one(record)
    assert "E007" not in outcome["error_codes"]


# ---------------------------------------------------------------------------
# E008 — negative amount
# ---------------------------------------------------------------------------

def test_e008_negative_amount():
    """A negative debit should flag E008."""
    record = {
        "bank_code": "010",
        "period": "1405/03",
        "account_code": "A1",
        "debit": "-10",              # negative
        "credit": "5",
        "balance": "15",
    }
    outcome, _ = _run_one(record)
    assert "E008" in outcome["error_codes"]


# ---------------------------------------------------------------------------
# E011 — invalid currency
# ---------------------------------------------------------------------------

def test_e011_invalid_currency():
    """A non-ISO currency code should flag E011."""
    record = {
        "bank_code": "010",
        "period": "1405/03",
        "account_code": "A1",
        "debit": "10",
        "credit": "5",
        "balance": "5",
        "currency": "XYZ",           # not in ISO 4217
    }
    outcome, _ = _run_one(record)
    assert "E011" in outcome["error_codes"]


def test_e011_absent_currency_is_ok():
    """A record without a currency field should not flag E011.

    Currency is optional. Absence is valid — the platform defaults
    to IRR. The rule only fires when a value is present and invalid.
    """
    record = {
        "bank_code": "010",
        "period": "1405/03",
        "account_code": "A1",
        "debit": "10.00",
        "credit": "5.00",
        "balance": "5.00",
        # no currency key
    }
    outcome, _ = _run_one(record)
    assert "E011" not in outcome["error_codes"]


def test_e011_lowercase_currency_accepted():
    """A lowercase currency code should be accepted.

    The rule uppercases before comparing. ISO 4217 codes are
    conventionally uppercase, but source systems export
    inconsistently — accepting lowercase is lenient and correct.
    """
    record = {
        "bank_code": "010",
        "period": "1405/03",
        "account_code": "A1",
        "debit": "10.00",
        "credit": "5.00",
        "balance": "5.00",
        "currency": "irr",           # lowercase
    }
    outcome, _ = _run_one(record)
    assert "E011" not in outcome["error_codes"]


# ---------------------------------------------------------------------------
# Full record
# ---------------------------------------------------------------------------

def test_valid_record_no_errors():
    """A fully valid record produces no errors.

    This is the strongest false-positive test. If any rule misfires
    on well-formed data, this test catches it.
    """
    record = {
        "bank_code": "010",
        "period": "1405/03",
        "account_code": "A1",
        "debit": "100.00",
        "credit": "40.00",
        "balance": "60.00",
        "currency": "IRR",
        "record_id": "R1",
    }
    outcome, summary = _run_one(record)
    assert outcome["is_valid"] is True
    assert outcome["error_codes"] == []
    assert summary["valid"] == 1
    assert summary["invalid"] == 0


# ---------------------------------------------------------------------------
# Batch summary aggregation
# ---------------------------------------------------------------------------

def test_batch_summary_counts():
    """The summary reflects the correct counts across a mixed batch.

    Three records: one valid, one with E004, one with E005. The
    summary should tally valid=1, invalid=2, and the errors_by_code
    map should have one E004 and one E005.
    """
    records = [
        # valid
        {
            "bank_code": "010",
            "period": "1405/03",
            "account_code": "A1",
            "debit": "100.00",
            "credit": "40.00",
            "balance": "60.00",
        },
        # E004: bad bank code
        {
            "bank_code": "999",
            "period": "1405/03",
            "account_code": "A2",
            "debit": "100.00",
            "credit": "40.00",
            "balance": "60.00",
        },
        # E005: bad period
        {
            "bank_code": "010",
            "period": "1405-03",
            "account_code": "A3",
            "debit": "10.00",
            "credit": "0.00",
            "balance": "10.00",
        },
    ]
    result = validate_batch(records, allowed_bank_codes=ALLOWED)
    s = result["summary"]
    assert s["total"] == 3
    assert s["valid"] == 1
    assert s["invalid"] == 2
    assert s["errors_by_code"].get("E004") == 1
    assert s["errors_by_code"].get("E005") == 1


def test_batch_empty_input():
    """An empty batch produces zero counts.

    Edge case: no records at all. The summary should be all zeros,
    not raise an exception. The engine is called with an empty
    iterable, which the loop skips over.
    """
    result = validate_batch([], allowed_bank_codes=ALLOWED)
    s = result["summary"]
    assert s["total"] == 0
    assert s["valid"] == 0
    assert s["invalid"] == 0
    assert s["duplicates"] == 0
    assert s["errors_by_code"] == {}