"""E006 — duplicate detection key computation."""
from __future__ import annotations

from typing import Any


def duplicate_key(record: dict[str, Any]) -> tuple[str, ...] | None:
    """
    Primary key:    record_id (if present and non-empty)
    Fallback key:   (bank_code, period, account_code)
    Returns None if the fallback fields are missing — such rows
    already fail E001/E003 and shouldn't also be flagged as duplicates.
    """
    record_id = record.get("record_id")
    if record_id:
        return ("id", str(record_id))

    parts = (
        record.get("bank_code"),
        record.get("period"),
        record.get("account_code"),
    )
    if any(p is None or p == "" for p in parts):
        return None
    return ("fallback", str(parts[0]), str(parts[1]), str(parts[2]))