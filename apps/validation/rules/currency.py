"""E011 — currency, if present, must be a valid ISO 4217 code."""
from __future__ import annotations

from typing import Any

from .base import ErrorCode, ERROR_MESSAGES, RuleResult

# Small starter set — replace with full ISO 4217 list later if needed.
ISO_4217 = {
    "IRR", "USD", "EUR", "GBP", "AED", "SAR", "JPY", "CNY",
    "CHF", "CAD", "AUD", "INR", "TRY", "RUB", "KWD", "QAR", "OMR", "BHD",
}


def check(record: dict[str, Any], context: dict[str, Any]) -> list[RuleResult]:
    currency = record.get("currency")
    if currency is None or currency == "":
        return []  # currency is optional

    if str(currency).upper() not in ISO_4217:
        return [
            RuleResult(
                code=ErrorCode.INVALID_CURRENCY,
                message=f"{ERROR_MESSAGES[ErrorCode.INVALID_CURRENCY]}: {currency}",
                field="currency",
            )
        ]
    return []