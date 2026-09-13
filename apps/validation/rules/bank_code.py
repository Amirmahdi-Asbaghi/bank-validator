"""E004 — bank_code must exist in the allow-list."""
from __future__ import annotations

from typing import Any

from .base import ErrorCode, ERROR_MESSAGES, RuleResult


def check(record: dict[str, Any], context: dict[str, Any]) -> list[RuleResult]:
    allowed: set[str] = context.get("allowed_bank_codes", set())
    bank_code = record.get("bank_code")

    if bank_code is None or bank_code == "":
        return []  # E003 handles empties

    if str(bank_code) not in allowed:
        return [
            RuleResult(
                code=ErrorCode.INVALID_BANK_CODE,
                message=f"{ERROR_MESSAGES[ErrorCode.INVALID_BANK_CODE]}: {bank_code}",
                field="bank_code",
            )
        ]
    return []