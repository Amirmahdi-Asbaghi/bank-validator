"""E003 — bank_code / period / account_code must not be null or empty."""
from __future__ import annotations

from typing import Any

from .base import ErrorCode, ERROR_MESSAGES, RuleResult

REQUIRED_NON_EMPTY = ("bank_code", "period", "account_code")


def check(record: dict[str, Any], context: dict[str, Any]) -> list[RuleResult]:
    failures: list[RuleResult] = []
    for field in REQUIRED_NON_EMPTY:
        if field not in record:
            continue  # E001 already flagged missing fields
        value = record[field]
        if value is None or (isinstance(value, str) and value.strip() == ""):
            failures.append(
                RuleResult(
                    code=ErrorCode.NULL_OR_EMPTY_VALUE,
                    message=f"{ERROR_MESSAGES[ErrorCode.NULL_OR_EMPTY_VALUE]}: {field}",
                    field=field,
                )
            )
    return failures