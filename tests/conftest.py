"""Shared pytest fixtures for the test suite.

Fixtures are functions that pytest runs before a test to prepare data
or dependencies. Any test can request a fixture by naming it as a
parameter — pytest finds it here, calls it, and passes the result.

Why conftest.py:
    pytest auto-discovers conftest.py files and their fixtures. A
    fixture defined here is available to every test in tests/ and its
    subdirectories — no imports needed in the test files.

What lives here:
    Fixtures used by multiple test files. Fixtures specific to one
    file stay in that file (e.g. API-specific setup).

Naming convention:
    Fixture names are nouns describing what they provide (``allowed_bank_codes``,
    ``valid_upload``), not verbs. This reads naturally at call sites.
"""
from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile


# ---------------------------------------------------------------------------
# Engine configuration
# ---------------------------------------------------------------------------

@pytest.fixture
def allowed_bank_codes():
    """The set of bank codes accepted by E004.

    Kept as a small set matching the test bank_codes.csv
    (010, 020, 030). Tests that need different allow-lists should
    build them locally rather than mutating this fixture — pytest
    fixtures are function-scoped by default, so each test gets a
    fresh set, but overriding is clearer than mutating.
    """
    return {"010", "020", "030"}


# ---------------------------------------------------------------------------
# Record fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def valid_record():
    """A record that passes every rule.

    Used as a starting point in tests that mutate one field to
    trigger a specific error. Copying a known-valid dict and changing
    one value is clearer than building the dict from scratch — the
    diff shows what the test is actually testing.

    All values are strings, matching what the parser returns for CSV.
    The rule engine handles the Decimal conversion.
    """
    return {
        "bank_code": "010",
        "period": "1405/03",
        "account_code": "A1",
        "debit": "100.00",
        "credit": "40.00",
        "balance": "60.00",
        "currency": "IRR",
        "record_id": "R1",
    }


# ---------------------------------------------------------------------------
# File bytes fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def valid_csv_bytes():
    """Raw bytes of a valid CSV file.

    Three rows, all valid. Every required field is populated with
    sensible values. Used by API tests that upload a file and expect
    a fully successful result.
    """
    return (
        b"bank_code,period,account_code,debit,credit,balance,currency,record_id\n"
        b"010,1405/03,A1,100.00,40.00,60.00,IRR,R1\n"
        b"010,1405/03,A2,200.00,50.00,150.00,IRR,R2\n"
        b"020,1405/03,A3,10.00,0.00,10.00,IRR,R3\n"
    )


@pytest.fixture
def invalid_csv_bytes():
    """Raw bytes of a CSV file with one row per error type.

    The four rows exercise four error codes:

        row 1: bad bank code (999) → E004
        row 2: bad period (1405-03) + bad balance (999 ≠ 150) → E005, E007
        row 3: valid (control row)
        row 4: duplicate of row 3 → E006

    A single upload that triggers every non-numeric error code. Used
    by API tests that verify the error aggregation path.
    """
    return (
        b"bank_code,period,account_code,debit,credit,balance,currency,record_id\n"
        b"999,1405/03,A1,100.00,40.00,60.00,IRR,R1\n"
        b"010,1405-03,A2,200.00,50.00,999.00,IRR,R2\n"
        b"010,1405/03,A3,100.00,40.00,60.00,IRR,R3\n"
        b"010,1405/03,A3,100.00,40.00,60.00,IRR,R3\n"
    )


# ---------------------------------------------------------------------------
# Upload fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def valid_upload(valid_csv_bytes):
    """A SimpleUploadedFile wrapping the valid CSV bytes.

    DRF's test client expects an UploadedFile object, not raw bytes.
    This fixture bridges the byte fixtures above into the shape the
    test client needs.

    Depends on ``valid_csv_bytes`` — pytest resolves the dependency
    graph automatically.
    """
    return SimpleUploadedFile(
        "valid_small.csv",
        valid_csv_bytes,
        content_type="text/csv",
    )


@pytest.fixture
def invalid_upload(invalid_csv_bytes):
    """A SimpleUploadedFile wrapping the invalid CSV bytes.

    Same shape as ``valid_upload``, different content.
    """
    return SimpleUploadedFile(
        "invalid_small.csv",
        invalid_csv_bytes,
        content_type="text/csv",
    )