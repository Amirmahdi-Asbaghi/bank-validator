"""Base types for the rule engine."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable


class ErrorCode(str, Enum):
    MISSING_REQUIRED_FIELD = "E001"
    INVALID_DATA_TYPE = "E002"
    NULL_OR_EMPTY_VALUE = "E003"
    INVALID_BANK_CODE = "E004"
    INVALID_PERIOD_FORMAT = "E005"
    DUPLICATE_RECORD = "E006"
    BALANCE_MISMATCH = "E007"
    NEGATIVE_AMOUNT = "E008"
    INVALID_CURRENCY = "E011"


ERROR_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.MISSING_REQUIRED_FIELD: "Required field is missing from the record",
    ErrorCode.INVALID_DATA_TYPE: "Field value cannot be parsed to the required type",
    ErrorCode.NULL_OR_EMPTY_VALUE: "Required field is empty or null",
    ErrorCode.INVALID_BANK_CODE: "Bank code is not in the allow-list",
    ErrorCode.INVALID_PERIOD_FORMAT: "Period format is invalid (expected YYYY/MM)",
    ErrorCode.DUPLICATE_RECORD: "Duplicate record detected",
    ErrorCode.BALANCE_MISMATCH: "Balance does not equal debit - credit",
    ErrorCode.NEGATIVE_AMOUNT: "Debit or credit is negative",
    ErrorCode.INVALID_CURRENCY: "Currency is not a valid ISO 4217 code",
}


@dataclass(frozen=True)
class RuleResult:
    """Outcome of applying a rule to a single record."""
    code: ErrorCode
    message: str
    field: str | None = None


# A rule receives the record dict (and optional context) and returns a list of failures.
RuleFn = Callable[[dict[str, Any], dict[str, Any]], list[RuleResult]]