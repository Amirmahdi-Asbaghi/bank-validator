"""E011 — currency, if present, must be a valid ISO 4217 code.

Unlike every other field in the spec, `currency` is optional. If it's
missing or empty, the record is valid — the platform assumes a default
(the spec suggests IRR for Iranian banks).

This is the only rule with a "skip if absent" behavior. All the other
required-field rules (E001, E003) treat absence as a failure; this one
does not.
"""
from __future__ import annotations

from typing import Any

from .base import ErrorCode, ERROR_MESSAGES, RuleResult

# A pragmatic subset of ISO 4217 — the currencies most relevant to a
# Middle East / Iran context, plus major reserve currencies. The full
# standard has ~180 active codes.
#
# Why a subset?
#   - It's the code we can defend: "these are the currencies our banks
#     transact in"
#   - It rejects junk like "XYZ" without needing the full list
#   - Swapping in the full ISO 4217 list is a one-file change — the
#     rule's logic doesn't depend on the list's size
#
# If the business requires the full standard, replace this set with
# `pycountry.currencies` or a hardcoded list. The rule stays the same.
ISO_4217 = {
    "IRR", "USD", "EUR", "GBP", "AED", "SAR", "JPY", "CNY",
    "CHF", "CAD", "AUD", "INR", "TRY", "RUB", "KWD", "QAR", "OMR", "BHD",
}


def check(record: dict[str, Any], context: dict[str, Any]) -> list[RuleResult]:
    """Flag records with an invalid currency code.

    Missing and empty values pass — currency is optional. This is the
    only rule in the engine that treats absence as valid.

    The comparison is case-insensitive: `usd`, `Usd`, and `USD` all
    pass. Currency codes are conventionally uppercase, but banks export
    inconsistently, and being lenient here costs nothing.
    """
    currency = record.get("currency")

    # Optional field: skip silently when absent or empty.
    # Unlike E001/E003, this rule does not flag missing values.
    if currency is None or currency == "":
        return []

    # Normalize to uppercase for comparison. The raw value is preserved
    # in raw_row; only the comparison uses the normalized form.
    #
    # str() guards against a non-string value (e.g. a number from JSON)
    # so `.upper()` doesn't crash.
    if str(currency).upper() not in ISO_4217:
        return [
            RuleResult(
                code=ErrorCode.INVALID_CURRENCY,
                # Show the original value (not the uppercased one) so
                # the bank sees exactly what they sent.
                message=f"{ERROR_MESSAGES[ErrorCode.INVALID_CURRENCY]}: {currency}",
                field="currency",
            )
        ]

    return []