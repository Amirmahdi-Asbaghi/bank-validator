"""E002 — debit/credit/balance must parse to Decimal."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from .base import ErrorCode, ERROR_MESSAGES, RuleResult

NUMERIC_FIELDS = ("debit", "credit", "balance")


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def check(record: dict[str, Any], context: dict[str, Any]) -> list[RuleResult]:
    failures: list[RuleResult] = []
    for field in NUMERIC_FIELDS:
        if field not in record:
            continue  # E001 already flagged missing fields
        raw = record[field]
        if raw is None or raw == "":
            continue  # E003 handles empties on required fields
        if _to_decimal(raw) is None:
            failures.append(
                RuleResult(
                    code=ErrorCode.INVALID_DATA_TYPE,
                    message=f"{ERROR_MESSAGES[ErrorCode.INVALID_DATA_TYPE]}: {field}={raw!r}",
                    field=field,
                )
            )
    return failures