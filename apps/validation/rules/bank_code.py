"""E004 — bank_code must exist in the allow-list.

The first rule that requires external data. The allow-list is loaded
once per batch (from the BankReference table) and passed in via
`context`, so we don't query the database for every row — that would
be millions of queries for a large file.

The engine keeps this rule decoupled from Django's ORM on purpose:
the rule takes a set, not a queryset. That makes it testable in
isolation and reusable from the Spark path.
"""
from __future__ import annotations

from typing import Any

from .base import ErrorCode, ERROR_MESSAGES, RuleResult


def check(record: dict[str, Any], context: dict[str, Any]) -> list[RuleResult]:
    """Flag records whose bank_code is not in the allow-list.

    Empty or missing bank_code is not this rule's concern:
      - missing key    → E001
      - None or ""     → E003
    We return no failures for those so the bank sees the real problem
    (missing/empty) rather than a misleading "invalid bank code".
    """
    # The allow-list arrives via context as a plain set of strings.
    # This decouples the rule from Django: tests can pass {"010", "020"}
    # directly without setting up a database.
    allowed: set[str] = context.get("allowed_bank_codes", set())

    bank_code = record.get("bank_code")

    # Skip records where bank_code is absent or empty.
    # E001 and E003 already flagged these; reporting E004 as well would
    # double-count in the summary and confuse the bank about what to fix.
    if bank_code is None or bank_code == "":
        return []

    # str() normalizes the value. In practice bank_code is always a
    # string from the parser, but this guards against a value like 10
    # being compared to "10" in the allow-list — we want them to match.
    if str(bank_code) not in allowed:
        return [
            RuleResult(
                code=ErrorCode.INVALID_BANK_CODE,
                # Include the offending value so the API response is
                # self-contained without needing to look at raw_row.
                message=f"{ERROR_MESSAGES[ErrorCode.INVALID_BANK_CODE]}: {bank_code}",
                field="bank_code",
            )
        ]

    # One rule, one result. A bank_code can only be valid or invalid —
    # no need for a list with more than one entry.
    return []