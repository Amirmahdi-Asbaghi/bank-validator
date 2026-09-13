"""E001 — required fields must be present in the row."""
from __future__ import annotations

from typing import Any

from .base import ErrorCode, ERROR_MESSAGES, RuleResult

REQUIRED_FIELDS = ("bank_code", "period", "account_code", "debit", "credit", "balance")


def check(record: dict[str, Any], context: dict[str, Any]) -> list[RuleResult]:
    failures: list[RuleResult] = []
    for field in REQUIRED_FIELDS:
        if field not in record:
            failures.append(
                RuleResult(
                    code=ErrorCode.MISSING_REQUIRED_FIELD,
                    message=f"{ERROR_MESSAGES[ErrorCode.MISSING_REQUIRED_FIELD]}: {field}",
                    field=field,
                )
            )
    return failures