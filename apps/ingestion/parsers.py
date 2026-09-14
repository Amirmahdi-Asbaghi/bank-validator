"""Parse uploaded CSV or JSON files into a list of record dicts.

This is the boundary between the raw file the bank sent and the
structured rows the validation engine consumes. Every record that
reaches the rules has been through exactly one parser (CSV or JSON)
and comes out as a plain Python dict.

Design notes:

    - The parser does NOT validate. It only guarantees structure:
      a list of dicts. Validation is the rule engine's job.
    - Empty CSV cells become None, not "". This distinguishes "the
      bank sent an empty field" from "the field is absent", which
      the rules (E001, E003) need to tell apart.
    - Errors here are fatal — if the file can't be parsed at all,
      we raise ParseError and the run is marked failed. Per-row
      errors are not parse errors; those go to the rule engine.
"""
from __future__ import annotations

import csv
import io
import json
from typing import Any


class ParseError(Exception):
    """Raised when a file cannot be parsed.

    Distinct from validation failures (which produce RuleResults).
    ParseError means "we couldn't even read this as a table of rows" —
    a structural problem, not a data quality problem.

    The caller (services.py) catches this and marks the run as failed
    with the error message, so the bank sees a clear explanation
    rather than a 500.
    """
    pass


def parse_file(filename: str, content: bytes) -> list[dict[str, Any]]:
    """Dispatch to the right parser based on file extension.

    The extension is the only signal we have — no content sniffing.
    This matches the spec, which defines CSV and JSON as the input
    formats, and accepts either by extension. If a bank sends a
    mislabeled file (JSON content in a .csv file), the parser will
    raise ParseError and the run fails with a clear message.

    Args:
        filename: the original filename, used only for the extension.
        content:  raw bytes of the uploaded file.

    Returns:
        A list of dicts, one per record.

    Raises:
        ParseError: if the extension is unsupported or the content
                    can't be parsed.
    """
    name = filename.lower()

    # Dispatch by extension. `.lower()` handles ".CSV" and ".Json".
    if name.endswith(".csv"):
        return _parse_csv(content)
    if name.endswith(".json"):
        return _parse_json(content)

    # Unknown extension — the API's UploadSerializer already rejects
    # these at the HTTP layer, so this is a defence-in-depth check.
    # It catches the case where parse_file is called directly
    # (e.g. from a script or a test) without going through the API.
    raise ParseError(f"Unsupported file extension: {filename}")


def _parse_csv(content: bytes) -> list[dict[str, Any]]:
    """Parse CSV bytes into a list of dicts.

    The first row is treated as the header. Each subsequent row
    becomes a dict keyed by the header names.
    """
    # Decode as UTF-8, tolerating a BOM. The BOM (byte-order mark) is
    # a 3-byte prefix (EF BB BF) that Excel and some Windows tools
    # prepend to UTF-8 files. Without "utf-8-sig", the first header
    # would be "\ufeffbank_code" instead of "bank_code", silently
    # breaking every lookup.
    #
    # If the file isn't UTF-8, raise a clear error rather than
    # producing garbage. Some banks export in cp1252 or other
    # encodings; we don't guess, we reject.
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        # The `from e` preserves the original exception as __cause__,
        # useful for debuggers and log aggregators.
        raise ParseError(f"CSV must be UTF-8: {e}") from e

    # io.StringIO wraps the decoded string as a file-like object,
    # which csv.DictReader expects. It never touches disk.
    reader = csv.DictReader(io.StringIO(text))

    rows = []
    for row in reader:
        # Convert empty cells ("") to None. This is a deliberate
        # normalization:
        #
        #   - E001 checks field presence (key in dict)
        #   - E003 checks field emptiness (None or "")
        #
        # If we left "" in place, the two rules would overlap. By
        # converting "" to None, the rules see a consistent shape:
        # "present with a value", "present but None", or "absent".
        #
        # A cell containing "0" or "false" stays as-is — those are
        # real values, not empties.
        rows.append({
            k: (v if v != "" else None) for k, v in row.items()
        })
    return rows


def _parse_json(content: bytes) -> list[dict[str, Any]]:
    """Parse JSON bytes into a list of dicts.

    Accepts two shapes:
        1. A bare array:  ``[{...}, {...}]``
        2. A wrapped array: ``{"records": [{...}, {...}]}``

    Anything else is rejected with a clear error.
    """
    try:
        data = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        # Two failure modes collapsed into one message:
        #   - bytes aren't valid UTF-8
        #   - bytes are UTF-8 but not valid JSON
        # Both are "the file is broken", and the message names the
        # actual underlying reason.
        raise ParseError(f"Invalid JSON: {e}") from e

    # Unwrap the {"records": [...]} envelope. This shape is common
    # in API responses and gives us room to add metadata later
    # (e.g. {"records": [...], "bank_code": "010", "period": "1405/03"}).
    if isinstance(data, dict) and "records" in data:
        data = data["records"]

    # After unwrapping, we require a list. A dict without "records",
    # a string, a number — all rejected. This is the parser's
    # contract: the result is always a list of dicts.
    if not isinstance(data, list):
        raise ParseError(
            "JSON must be a list of records or an object with 'records'"
        )

    # We do NOT check that each item is a dict. That would be a
    # per-row validation concern. If a row is a string, the rule
    # engine will flag it as missing all required fields (E001),
    # which is the correct outcome.
    return data