"""E005 — period must match ^\\d{4}/(0[1-9]|1[0-2])$ (Jalali YYYY/MM).

Reports the reporting period in Jalali calendar format: a four-digit
year, a slash, and a two-digit month with a leading zero. The format
is exactly `YYYY/MM` — no dashes, no single-digit months, no spaces.

The Jalali year is validated for shape, not for validity. `9999/12`
matches the regex but is a nonsense year; we don't reject it here
because "is this a real year" is a policy question the business should
answer, not a format question. If they later want a range check, that
becomes a new error code, not a change to E005.
"""
from __future__ import annotations

import re
from typing import Any

from .base import ErrorCode, ERROR_MESSAGES, RuleResult

# Compiled once at module load. Regex compilation is fast but not free,
# and this rule runs once per row — millions of times for a large file.
# Compiling at import time means the cost is paid once per process.
#
# Breaking down the pattern:
#   ^              start of string
#   \d{4}          exactly four digits (year)
#   /              literal slash
#   (0[1-9]|1[0-2]) month: 01-09 OR 10-12
#   $              end of string
#
# The `match()` method anchors at start by default, but we include ^
# explicitly for clarity. $ is required to prevent trailing garbage.
PERIOD_RE = re.compile(r"^\d{4}/(0[1-9]|1[0-2])$")


def check(record: dict[str, Any], context: dict[str, Any]) -> list[RuleResult]:
    """Flag records whose period doesn't match the expected format.

    Empty or missing period is skipped — E001 (missing) and E003 (empty)
    already reported those. Reporting E005 too would double-count and
    mislead the bank about what to fix.
    """
    period = record.get("period")

    # Absent or empty is not a format problem — it's a presence problem.
    # Let E001 and E003 handle those cases.
    if period is None or period == "":
        return []

    # str() guards against a non-string value (e.g. an int in JSON).
    # The regex needs a string to match against.
    if not PERIOD_RE.match(str(period)):
        return [
            RuleResult(
                code=ErrorCode.INVALID_PERIOD_FORMAT,
                # Include the offending value so the bank sees exactly
                # what was sent. "1405-03" is more useful than "invalid".
                message=f"{ERROR_MESSAGES[ErrorCode.INVALID_PERIOD_FORMAT]}: {period}",
                field="period",
            )
        ]

    return []