"""Base types for the rule engine.

This module is the contract that every rule and both validation engines
(pandas and Spark) depend on. It contains no logic — only the vocabulary
shared across the codebase:

    ErrorCode       the catalogue of validation failures
    ERROR_MESSAGES  human-readable text for each code
    RuleResult      what a rule returns when it flags a record
    RuleFn          the shape every rule function must have

Keeping these in one place means the pandas path and the Spark path
cannot drift on error codes. If E007 means "balance mismatch" here,
it means the same everywhere.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable


class ErrorCode(str, Enum):
    """Every validation failure the platform can report.

    Values are stable identifiers used in:
      - Postgres JSONField (InvalidRecord.error_codes)
      - ClickHouse Map column (validation_summary.errors_by_code)
      - API responses (errors_by_code)
      - Prometheus counters (per-code counters)

    E009 and E010 are intentionally absent — they're reserved for
    future rules (e.g. cross-field consistency) without renumbering.
    """
    MISSING_REQUIRED_FIELD = "E001"
    INVALID_DATA_TYPE = "E002"
    NULL_OR_EMPTY_VALUE = "E003"
    INVALID_BANK_CODE = "E004"
    INVALID_PERIOD_FORMAT = "E005"
    DUPLICATE_RECORD = "E006"
    BALANCE_MISMATCH = "E007"
    NEGATIVE_AMOUNT = "E008"
    INVALID_CURRENCY = "E011"


# Human-readable messages shown alongside error codes in API responses
# and ClickHouse summaries. Kept separate from the enum so the codes
# stay stable (numeric) while the wording can be tweaked or translated.
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
    """One failure produced by one rule for one record.

    A single record can produce multiple RuleResults (e.g. bad bank_code
    AND bad period). The engine collects them all into the record's
    error_codes and error_messages arrays.

    frozen=True makes instances immutable and hashable — useful when
    batching results or using them as dict keys in aggregation.
    """
    code: ErrorCode
    message: str
    field: str | None = None


# Type alias for a rule function.
#
# Every rule takes:
#   record  — one parsed input row as a dict
#   context — shared data for the batch (e.g. allowed_bank_codes)
# and returns:
#   a list of RuleResult objects (empty list = record passed this rule)
#
# This uniform signature is what lets the engine iterate over rules
# without special-casing any of them.
RuleFn = Callable[[dict[str, Any], dict[str, Any]], list[RuleResult]]