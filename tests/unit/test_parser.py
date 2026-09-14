"""Unit tests for the file parser.

The parser is the boundary between the raw uploaded bytes and the
list of dicts the rule engine consumes. Bugs here propagate to every
downstream component — a parser that drops a column silently makes
E001 fire on every row.

These tests cover:
    - CSV parsing (valid, empty cells, BOM, malformed)
    - JSON parsing (array, envelope, malformed)
    - Extension dispatch
    - Error handling (ParseError on bad input)

Why these matter more than most unit tests:
    Every rule in the engine assumes the parser produced the right
    dicts. A subtly wrong parser — an empty string instead of None,
    a dropped column, a mis-decoded byte — would either produce
    false positives or false negatives across the whole pipeline.
"""
from __future__ import annotations

import pytest

from apps.ingestion.parsers import ParseError, parse_file


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------

def test_csv_parses_simple_file():
    """A minimal CSV parses into a list of dicts.

    The header becomes the dict keys; each data row becomes one dict.
    Values stay as strings — the parser does not type-convert.
    """
    content = b"bank_code,period,account_code\n010,1405/03,A1\n020,1405/04,A2\n"
    records = parse_file("data.csv", content)

    assert len(records) == 2
    assert records[0]["bank_code"] == "010"
    assert records[0]["period"] == "1405/03"
    assert records[0]["account_code"] == "A1"
    assert records[1]["bank_code"] == "020"


def test_csv_converts_empty_cells_to_none():
    """Empty CSV cells become None, not empty string.

    This is a deliberate normalization. In CSV, an empty cell and a
    missing value are indistinguishable — DictReader gives "" for
    both. Converting to None gives the rule engine a consistent
    shape: E003 handles None as "empty", and E001 checks key presence.

    Without this, an empty cell would fire BOTH E002 (can't parse "")
    and E003 (empty value) — double-counting one problem.
    """
    content = b"bank_code,period,account_code\n010,,A1\n"
    records = parse_file("data.csv", content)

    assert len(records) == 1
    # The empty cell is None, not ""
    assert records[0]["period"] is None
    # Non-empty cells stay as strings
    assert records[0]["bank_code"] == "010"


def test_csv_handles_utf8_bom():
    """A UTF-8 BOM at the start of the file is stripped.

    Excel and some Windows tools prepend a 3-byte BOM to CSV files.
    Without handling it, the first header becomes "\\ufeffbank_code"
    instead of "bank_code", and every lookup fails silently.
    """
    # \xef\xbb\xbf is the UTF-8 BOM
    content = b"\xef\xbb\xbfbank_code,period\n010,1405/03\n"
    records = parse_file("data.csv", content)

    assert len(records) == 1
    # The BOM should not be in the key
    assert records[0]["bank_code"] == "010"
    assert "\ufeffbank_code" not in records[0]


def test_csv_rejects_invalid_utf8():
    """Invalid UTF-8 bytes raise ParseError with a clear message.

    We don't try to guess the encoding. A non-UTF-8 file is rejected
    rather than decoded with a wrong codec (which would produce
    mojibake).
    """
    # 0xff is not valid UTF-8
    content = b"bank_code,period\n\xff\xfe\xfd,1405/03\n"
    with pytest.raises(ParseError) as exc:
        parse_file("data.csv", content)
    assert "UTF-8" in str(exc.value)


def test_csv_only_header_returns_empty_list():
    """A CSV with only a header produces an empty list.

    Edge case: no data rows. The parser should return [] rather than
    raising or producing a phantom record.
    """
    content = b"bank_code,period,account_code\n"
    records = parse_file("data.csv", content)
    assert records == []


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------

def test_json_parses_bare_array():
    """A JSON array of objects parses correctly."""
    content = b'[{"bank_code": "010", "period": "1405/03"}]'
    records = parse_file("data.json", content)

    assert len(records) == 1
    assert records[0]["bank_code"] == "010"
    assert records[0]["period"] == "1405/03"


def test_json_parses_wrapped_records():
    """A JSON object with a "records" key is unwrapped.

    This shape is common in API responses and lets us add metadata
    later without breaking the parser.
    """
    content = b'{"records": [{"bank_code": "010"}, {"bank_code": "020"}]}'
    records = parse_file("data.json", content)

    assert len(records) == 2
    assert records[0]["bank_code"] == "010"
    assert records[1]["bank_code"] == "020"


def test_json_rejects_malformed():
    """Malformed JSON raises ParseError.

    The error message should reference the underlying JSON error.
    """
    content = b'[{"bank_code": "010"'
    with pytest.raises(ParseError) as exc:
        parse_file("data.json", content)
    assert "JSON" in str(exc.value)


def test_json_rejects_object_without_records():
    """A JSON object without a "records" key is rejected.

    The parser accepts two shapes: an array, or an object with a
    "records" key. Anything else is a structural error.
    """
    content = b'{"data": [{"bank_code": "010"}]}'
    with pytest.raises(ParseError) as exc:
        parse_file("data.json", content)
    assert "records" in str(exc.value)


def test_json_rejects_scalar():
    """A bare JSON value (string, number) is rejected.

    The parser's contract is "list of records". A scalar doesn't
    match.
    """
    content = b'"just a string"'
    with pytest.raises(ParseError):
        parse_file("data.json", content)


def test_json_preserves_null_vs_empty_string():
    """JSON null and empty string stay distinct.

    Unlike CSV, JSON distinguishes null from "". The parser keeps
    both as-is so the rule engine can see the difference.
    """
    content = b'[{"bank_code": null, "account_code": ""}]'
    records = parse_file("data.json", content)

    assert records[0]["bank_code"] is None
    assert records[0]["account_code"] == ""


# ---------------------------------------------------------------------------
# Extension dispatch
# ---------------------------------------------------------------------------

def test_extension_case_insensitive():
    """The extension check is case-insensitive.

    .CSV and .Json should dispatch to the same parsers as .csv
    and .json. Extensions are a client-side convention; case
    shouldn't matter.
    """
    csv_content = b"bank_code\n010\n"
    assert parse_file("data.CSV", csv_content)[0]["bank_code"] == "010"

    json_content = b'[{"bank_code": "010"}]'
    assert parse_file("data.Json", json_content)[0]["bank_code"] == "010"


def test_unknown_extension_raises():
    """An unsupported extension raises ParseError.

    The API serializer already rejects these at the HTTP layer;
    this is defence in depth for direct calls to parse_file.
    """
    with pytest.raises(ParseError) as exc:
        parse_file("data.txt", b"whatever")
    assert "Unsupported" in str(exc.value)


def test_no_extension_raises():
    """A filename with no extension is rejected."""
    with pytest.raises(ParseError):
        parse_file("data", b"whatever")