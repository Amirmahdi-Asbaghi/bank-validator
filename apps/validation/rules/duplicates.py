"""E006 — duplicate detection key computation.

Unlike the other rules, this module doesn't check a single record.
Duplicate detection requires comparing records against each other, so
the actual detection happens in `engine.py` — this file just defines
what "duplicate" means by computing a stable key for each record.

Why a separate key function?
    - The rule engine can change (e.g. sort order, streaming windows)
      without changing the definition of a duplicate.
    - The Spark path uses the same key definition in a window function.
    - Tests can verify the key logic in isolation from the engine.
"""
from __future__ import annotations

from typing import Any


def duplicate_key(record: dict[str, Any]) -> tuple[str, ...] | None:
    """Return the identity key for a record, or None if it can't be keyed.

    Strategy (from the spec):
      1. If `record_id` is present and non-empty, it is the key.
         This is the preferred path — the bank provides a unique ID.
      2. Otherwise, fall back to (bank_code, period, account_code).
         This is the minimum natural key that identifies an accounting
         entry in most reporting schemas.

    Returns None when the record can't be keyed — that happens when:
      - record_id is missing, AND
      - any of the fallback fields is missing or empty

    Returning None means "this record can't be checked for duplicates".
    Those records are already flagged by E001/E003, so we don't add
    a duplicate error on top — that would double-count and mislead.
    """
    record_id = record.get("record_id")

    # Preferred key: the bank-provided record_id.
    # Truthiness check covers None, "", and 0 (though 0 wouldn't be a
    # valid record_id anyway). Whitespace-only strings are technically
    # truthy; if the bank sends "   ", we treat it as a real ID — the
    # spec doesn't define whitespace handling for this field.
    if record_id:
        # Prefix with "id" so the tuple shape differs from the fallback.
        # Without the prefix, a record_id of "A1" and a fallback key
        # of ("A1", ...) could theoretically collide. The prefix makes
        # the two namespaces disjoint.
        return ("id", str(record_id))

    # Fallback key: the natural composite identifier.
    parts = (
        record.get("bank_code"),
        record.get("period"),
        record.get("account_code"),
    )

    # If any part is missing, we can't form a key.
    # Returning None signals "skip this row" — the engine doesn't
    # flag it as a duplicate, and doesn't crash either.
    if any(p is None or p == "" for p in parts):
        return None

    # str() normalizes non-string values. Same reasoning as E004.
    # Prefix "fallback" keeps the two key namespaces separate.
    return ("fallback", str(parts[0]), str(parts[1]), str(parts[2]))