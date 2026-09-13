"""E007 — balance == debit - credit (exact Decimal match).
E008 — debit and credit must not be negative.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from .base import ErrorCode, ERROR_MESSAGES, RuleResult


def _d(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def check(record: dict[str, Any], context: dict[str, Any]) -> list[RuleResult]:
    failures: list[RuleResult] = []

    debit = _d(record.get("debit"))
    credit = _d(record.get("credit"))
    balance = _d(record.get("balance"))

    # E008 — negative amounts
    for field, val in (("debit", debit), ("credit", credit)):
        if val is not None and val < 0:
            failures.append(
                RuleResult(
                    code=ErrorCode.NEGATIVE_AMOUNT,
                    message=f"{ERROR_MESSAGES[ErrorCode.NEGATIVE_AMOUNT]}: {field}={val}",
                    field=field,
                )
            )

    # E007 — balance consistency (only if all three parsed)
    if debit is not None and credit is not None and balance is not None:
        if balance != (debit - credit):
            failures.append(
                RuleResult(
                    code=ErrorCode.BALANCE_MISMATCH,
                    message=(
                        f"{ERROR_MESSAGES[ErrorCode.BALANCE_MISMATCH]}: "
                        f"balance={balance}, debit-credit={debit - credit}"
                    ),
                    field="balance",
                )
            )

    return failures