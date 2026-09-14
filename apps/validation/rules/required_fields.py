"""E001 — required fields must be present in the row.

The simplest rule in the engine. It checks that each required field
exists as a key in the record dict. It does NOT check that the value
is non-empty (that's E003) or that it parses correctly (that's E002).

This separation of concerns matters: a missing `debit` column is a
structural problem (the file doesn't match the contract), while an
empty `debit` value is a data problem. They get different error codes
so the bank can tell which is which.
"""
from __future__ import annotations

from typing import Any

from .base import ErrorCode, ERROR_MESSAGES, RuleResult

# The six fields the spec requires on every record.
# Order doesn't matter for correctness, but keeping it alphabetical
# makes the failures deterministic when a row is missing several fields.
REQUIRED_FIELDS = (
    "bank_code",
    "period",
    "account_code",
    "debit",
    "credit",
    "balance",
)


def check(record: dict[str, Any], context: dict[str, Any]) -> list[RuleResult]:
    """Return one RuleResult per missing required field.

    `context` is unused here but required by the RuleFn contract — every
    rule has the same signature so the engine can call them uniformly.

    A row that is missing three fields produces three separate results.
    This lets the bank see the full list of problems in one pass, instead
    of fixing one field, re-uploading, and finding another.
    """
    failures: list[RuleResult] = []

    for field in REQUIRED_FIELDS:
        # `not in` checks key presence only — a key with value None or ""
        # is still "present" and passes this rule. Those cases are caught
        # by E003 (nulls.py) later in the pipeline.
        if field not in record:
            failures.append(
                RuleResult(
                    code=ErrorCode.MISSING_REQUIRED_FIELD,
                    # Include the field name in the message so the API
                    # response is actionable without decoding the row.
                    message=f"{ERROR_MESSAGES[ErrorCode.MISSING_REQUIRED_FIELD]}: {field}",
                    field=field,
                )
            )

    return failures