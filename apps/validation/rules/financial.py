"""E007 — balance == debit - credit (exact Decimal match).
E008 — debit and credit must not be negative.

Two accounting rules that share the same numeric parsing and the same
domain. Grouping them keeps the decimal parsing in one place and makes
the invariants easy to reason about.

The key design point: comparison uses Decimal, never float. Floats
lose precision on values like 0.1, and a false balance mismatch on
correct data would be a critical bug in a financial system.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from .base import ErrorCode, ERROR_MESSAGES, RuleResult


def _d(value: Any) -> Decimal | None:
    """Parse a value into Decimal, or return None if it can't be parsed.

    The None sentinel has a specific meaning here: "no valid Decimal
    value to work with". Callers must check for None before doing
    arithmetic, because None < 0 would raise in Python 3.

    Same helper shape as `_to_decimal` in types.py. Kept local rather
    than imported to keep each rule module self-contained — if the
    parsing logic ever needs to change for financial rules, this stays
    independent.
    """
    if value is None or value == "":
        return None
    try:
        # str() first — see types.py for why passing a float to
        # Decimal() would silently introduce the precision errors
        # we're trying to avoid.
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def check(record: dict[str, Any], context: dict[str, Any]) -> list[RuleResult]:
    """Run both financial checks against a record.

    Order matters:
      1. Parse all three numeric fields once
      2. Run E008 (sign check) on debit and credit
      3. Run E007 (balance check) only if all three parsed successfully

    A record can fail E008 without failing E007 (e.g. negative debit
    with a consistent balance), and vice versa (correct signs but
    inconsistent balance). Both failures are collected.
    """
    failures: list[RuleResult] = []

    # Parse once, use everywhere. Each _d call is cheap, but doing the
    # parsing here means E008 and E007 see the same values.
    debit = _d(record.get("debit"))
    credit = _d(record.get("credit"))
    balance = _d(record.get("balance"))

    # -------- E008 — negative amounts --------
    #
    # Only check debit and credit, not balance. A negative balance can
    # be legitimate — it means the account is overdrawn or the entry
    # reverses a previous one. The spec only forbids negative debit
    # and credit.
    #
    # We loop over the two fields rather than writing two branches,
    # so the logic stays DRY and adding a third amount field later is
    # a one-line change.
    for field, val in (("debit", debit), ("credit", credit)):
        # Skip if the value didn't parse — types.py already flagged it
        # with E002. Adding an E008 here would be noise.
        if val is not None and val < 0:
            failures.append(
                RuleResult(
                    code=ErrorCode.NEGATIVE_AMOUNT,
                    # Include the value so the bank sees how negative.
                    # "-100.00" is more useful than just "negative".
                    message=f"{ERROR_MESSAGES[ErrorCode.NEGATIVE_AMOUNT]}: {field}={val}",
                    field=field,
                )
            )

    # -------- E007 — balance == debit - credit --------
    #
    # Only run if all three parsed. If any is None, we don't have
    # meaningful numbers to compare — that record is already flagged
    # by E001, E002, or E003.
    #
    # The comparison is exact. No epsilon, no rounding tolerance.
    # Decimal arithmetic is exact, so 0.10 - 0.05 == 0.05 exactly,
    # whereas with float it wouldn't.
    if debit is not None and credit is not None and balance is not None:
        expected_balance = debit - credit

        if balance != expected_balance:
            failures.append(
                RuleResult(
                    code=ErrorCode.BALANCE_MISMATCH,
                    # Show both the actual and the expected value so
                    # the bank can see the delta in the message itself.
                    message=(
                        f"{ERROR_MESSAGES[ErrorCode.BALANCE_MISMATCH]}: "
                        f"balance={balance}, debit-credit={expected_balance}"
                    ),
                    field="balance",
                )
            )

    return failures