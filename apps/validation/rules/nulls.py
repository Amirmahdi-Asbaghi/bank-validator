"""E003 — bank_code / period / account_code must not be null or empty.

Complements E001 (field is present) by checking that the value is
usable. A column can exist in the file but have no value in a specific
row — that's a different problem from a missing column, and it gets a
different error code so the bank can tell which issue they have.

Only checks string fields. The numeric fields (debit, credit, balance)
have their own empty-value handling inside E002, because "empty number"
and "empty string" are conceptually the same problem but the fix
differs — you either provide a number or provide text.
"""
from __future__ import annotations

from typing import Any

from .base import ErrorCode, ERROR_MESSAGES, RuleResult

# The string fields that must be non-empty. Excludes the numeric fields
# because their emptiness is handled by E002 (types.py).
REQUIRED_NON_EMPTY = ("bank_code", "period", "account_code")


def check(record: dict[str, Any], context: dict[str, Any]) -> list[RuleResult]:
    """Return one RuleResult per required string field that is empty.

    Empty means:
      - None          (JSON null, or Python None from the parser)
      - ""            (empty string)
      - "   "         (whitespace only — trimmed before checking)

    A record with three empty fields produces three results, same
    pattern as E001.
    """
    failures: list[RuleResult] = []

    for field in REQUIRED_NON_EMPTY:
        if field not in record:
            # Missing key is a structural problem (E001), not emptiness.
            # Skip to avoid double-reporting the same issue.
            continue

        value = record[field]

        # Two cases collapse into "empty":
        #   1. None — null in JSON or missing value in CSV
        #   2. A string of only whitespace — treat as empty, because
        #      banks sometimes export "   " for missing values and we
        #      don't want those to slip through.
        #
        # The isinstance check matters: value could be a number,
        # a list, or anything else. If it's not a string, we let the
        # field's own rule (E002 for numerics) decide what to do.
        is_empty = value is None or (
            isinstance(value, str) and value.strip() == ""
        )

        if is_empty:
            failures.append(
                RuleResult(
                    code=ErrorCode.NULL_OR_EMPTY_VALUE,
                    message=f"{ERROR_MESSAGES[ErrorCode.NULL_OR_EMPTY_VALUE]}: {field}",
                    field=field,
                )
            )

    return failures