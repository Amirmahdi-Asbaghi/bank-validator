"""Rule engine — composes all rules and applies them to a batch of records."""
from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

from . import (
    bank_code as r_bank_code,
    currency as r_currency,
    duplicates as r_duplicates,
    financial as r_financial,
    nulls as r_nulls,
    period as r_period,
    required_fields as r_required,
    types as r_types,
)
from .base import ErrorCode, ERROR_MESSAGES, RuleResult

ROW_LEVEL_RULES = (
    r_required.check,
    r_types.check,
    r_nulls.check,
    r_bank_code.check,
    r_period.check,
    r_financial.check,
    r_currency.check,
)


def validate_batch(
    records: Iterable[dict[str, Any]],
    allowed_bank_codes: set[str] | None = None,
) -> dict[str, Any]:
    """
    Run every rule against every record.
    Returns a summary dict plus per-record outcomes.
    """
    context: dict[str, Any] = {
        "allowed_bank_codes": allowed_bank_codes or set(),
    }

    outcomes: list[dict[str, Any]] = []
    seen_keys: dict[tuple[str, ...], int] = {}
    error_counts: Counter[str] = Counter()
    duplicate_count = 0

    for idx, record in enumerate(records, start=1):
        failures: list[RuleResult] = []
        for rule in ROW_LEVEL_RULES:
            failures.extend(rule(record, context))

        # Duplicate detection across the batch
        key = r_duplicates.duplicate_key(record)
        if key is not None:
            if key in seen_keys:
                duplicate_count += 1
                failures.append(
                    RuleResult(
                        code=ErrorCode.DUPLICATE_RECORD,
                        message=ERROR_MESSAGES[ErrorCode.DUPLICATE_RECORD],
                        field=None,
                    )
                )
            else:
                seen_keys[key] = idx

        error_codes = [f.code.value for f in failures]
        error_messages = [f.message for f in failures]
        for code in error_codes:
            error_counts[code] += 1

        outcomes.append(
            {
                "row_number": idx,
                "is_valid": not failures,
                "error_codes": error_codes,
                "error_messages": error_messages,
                "raw_row": record,
            }
        )

    total = len(outcomes)
    invalid = sum(1 for o in outcomes if not o["is_valid"])
    valid = total - invalid

    return {
        "summary": {
            "total": total,
            "valid": valid,
            "invalid": invalid,
            "duplicates": duplicate_count,
            "errors_by_code": dict(error_counts),
        },
        "outcomes": outcomes,
    }