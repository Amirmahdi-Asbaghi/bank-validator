"""E002 — debit/credit/balance must parse to Decimal.

Guards the pipeline against non-numeric values in money fields. The
downstream rules (E007 balance check, E008 negative check) operate on
Decimal values and would raise exceptions on garbage input, so this
rule runs first to flag bad types cleanly.

Uses Decimal, never float. Floats lose precision on values like 0.1,
which is unacceptable for financial data. Python's Decimal handles
arbitrary precision and exact arithmetic.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from .base import ErrorCode, ERROR_MESSAGES, RuleResult

# The fields that must be numeric. Kept separate from REQUIRED_FIELDS
# in required_fields.py because not every required field is numeric
# (bank_code and period are strings).
NUMERIC_FIELDS = ("debit", "credit", "balance")


def _to_decimal(value: Any) -> Decimal | None:
    """Best-effort conversion to Decimal. Returns None on failure.

    None is used as a sentinel meaning "cannot parse" — callers must
    distinguish this from a valid Decimal(0), which is a real value.

    Converts via str() first because Decimal() accepts only strings,
    ints, and other Decimals. Passing a float directly would go through
    the binary representation and could introduce the exact precision
    errors we're trying to avoid.
    """
    if value is None or value == "":
        # Empty/null is not a type error — that's E003's territory.
        # Returning None here means "no opinion", and the caller
        # will skip this field.
        return None

    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        # InvalidOperation: str doesn't parse as a number
        # ValueError:     e.g. Decimal("") on some versions
        # TypeError:      Decimal(None), Decimal([]), etc.
        return None


def check(record: dict[str, Any], context: dict[str, Any]) -> list[RuleResult]:
    """Flag any numeric field whose value cannot parse to Decimal.

    Skips fields that:
      - are missing entirely (E001 handles that)
      - are None or "" (E003 handles that)

    This rule only answers one question: given a value that should be
    a number, is it one? Sign, range, and consistency with other fields
    are separate rules with separate error codes.
    """
    failures: list[RuleResult] = []

    for field in NUMERIC_FIELDS:
        if field not in record:
            # Missing field is a structural problem, not a type problem.
            # E001 already reported it; don't double-count here.
            continue

        raw = record[field]

        if raw is None or raw == "":
            # Empty value on a required field is E003's job.
            # Reporting E002 here would mislead the bank into thinking
            # the data is malformed when it's just absent.
            continue

        if _to_decimal(raw) is None:
            failures.append(
                RuleResult(
                    code=ErrorCode.INVALID_DATA_TYPE,
                    # Include the raw value so the message is self-contained
                    # in the API response. `!r` shows quotes for strings and
                    # repr for other types, which helps debugging.
                    message=(
                        f"{ERROR_MESSAGES[ErrorCode.INVALID_DATA_TYPE]}: "
                        f"{field}={raw!r}"
                    ),
                    field=field,
                )
            )

    return failures