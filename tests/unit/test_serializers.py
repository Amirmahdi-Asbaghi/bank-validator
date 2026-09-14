"""Unit tests for the upload serializer.

The serializer is the API's first boundary — it rejects bad files
before any storage or processing happens. Every check it performs
(extension, size) is a promise to the pipeline that the input is
safe to handle.

These tests cover:
    - Accepting valid CSV and JSON files
    - Rejecting unsupported extensions
    - Rejecting oversized files
    - Rejecting when no file is provided

Why test the serializer in isolation:
    The API tests exercise the serializer indirectly. If one of its
    rules breaks, an API test fails but the error surfaces as a
    generic 400 — the same code path as many other errors. A unit
    test pins each failure mode to its own assertion, so the
    diagnosis is immediate.
"""
from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.ingestion.serializers import UploadSerializer


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_accepts_valid_csv():
    """A .csv file passes validation."""
    upload = SimpleUploadedFile(
        "data.csv",
        b"bank_code,period\n010,1405/03\n",
        content_type="text/csv",
    )
    serializer = UploadSerializer(data={"file": upload})

    assert serializer.is_valid()
    assert serializer.validated_data["file"].name == "data.csv"


def test_accepts_valid_json():
    """A .json file passes validation."""
    upload = SimpleUploadedFile(
        "data.json",
        b'[{"bank_code": "010"}]',
        content_type="application/json",
    )
    serializer = UploadSerializer(data={"file": upload})

    assert serializer.is_valid()


def test_accepts_uppercase_extension():
    """A .CSV file (uppercase) passes validation.

    The serializer lowercases the name before the extension check.
    Extensions are a client-side convention; case shouldn't matter.
    """
    upload = SimpleUploadedFile(
        "DATA.CSV",
        b"bank_code\n010\n",
        content_type="text/csv",
    )
    serializer = UploadSerializer(data={"file": upload})

    assert serializer.is_valid()


# ---------------------------------------------------------------------------
# Extension rejection
# ---------------------------------------------------------------------------

def test_rejects_unsupported_extension():
    """A .txt file fails validation with a clear message."""
    upload = SimpleUploadedFile(
        "report.txt",
        b"some content",
        content_type="text/plain",
    )
    serializer = UploadSerializer(data={"file": upload})

    assert not serializer.is_valid()
    assert "file" in serializer.errors
    # The message mentions the accepted formats
    message = str(serializer.errors["file"][0]).lower()
    assert "csv" in message
    assert "json" in message


def test_rejects_file_with_no_extension():
    """A file with no extension fails validation."""
    upload = SimpleUploadedFile(
        "data",
        b"bank_code\n010\n",
        content_type="application/octet-stream",
    )
    serializer = UploadSerializer(data={"file": upload})

    assert not serializer.is_valid()


def test_rejects_extension_that_merely_contains_csv():
    """A file named "report.csv.txt" is rejected.

    The extension check uses endswith(".csv"), not "contains csv".
    This test guards against a naive substring check — a common
    bug that would accept files with "csv" anywhere in the name.
    """
    upload = SimpleUploadedFile(
        "report.csv.txt",
        b"some content",
        content_type="text/plain",
    )
    serializer = UploadSerializer(data={"file": upload})

    assert not serializer.is_valid()


# ---------------------------------------------------------------------------
# Size rejection
# ---------------------------------------------------------------------------

def test_rejects_file_over_200mb():
    """A file larger than the cap fails validation.

    Building a real 200+ MB file in a test would be slow and
    memory-hungry. Instead, we build the smallest object that
    reports a large size via the ``size`` property.
    """
    # Create a small file, then override its size attribute to
    # simulate an oversized upload. The serializer only reads
    # ``f.size`` — it doesn't stat the disk.
    upload = SimpleUploadedFile(
        "big.csv",
        b"bank_code\n010\n",
        content_type="text/csv",
    )
    # 201 MB in bytes — just over the 200 MB cap
    upload.size = 201 * 1024 * 1024

    serializer = UploadSerializer(data={"file": upload})

    assert not serializer.is_valid()
    assert "file" in serializer.errors
    message = str(serializer.errors["file"][0]).lower()
    assert "200" in message


def test_accepts_file_at_exactly_200mb():
    """A file at exactly the cap passes validation.

    The check is ``> cap``, not ``>= cap``. A file at exactly 200 MB
    is accepted. This test verifies the boundary — an off-by-one
    mistake here would be silent for most tests but visible at the
    boundary.
    """
    upload = SimpleUploadedFile(
        "big.csv",
        b"bank_code\n010\n",
        content_type="text/csv",
    )
    # Exactly 200 MB
    upload.size = 200 * 1024 * 1024

    serializer = UploadSerializer(data={"file": upload})

    assert serializer.is_valid()


# ---------------------------------------------------------------------------
# Missing file
# ---------------------------------------------------------------------------

def test_rejects_missing_file():
    """A request with no file fails validation."""
    serializer = UploadSerializer(data={})

    assert not serializer.is_valid()
    assert "file" in serializer.errors