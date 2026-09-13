"""E005 — period must match ^\\d{4}/(0[1-9]|1[0-2])$ (Jalali YYYY/MM)."""
from __future__ import annotations

import re
from typing import Any

from .base import ErrorCode, ERROR_MESSAGES, RuleResult

PERIOD_RE = re.compile(r"^\d{4}/(0[1-9]|1[0-2])$")


def check(record: dict[str, Any], context: dict[str, Any]) -> list[RuleResult]:
    period = record.get("period")

    if period is None or period == "":
        return []  # E003 handles empties

    if not PERIOD_RE.match(str(period)):
        return [
            RuleResult(
                code=ErrorCode.INVALID_PERIOD_FORMAT,
                message=f"{ERROR_MESSAGES[ErrorCode.INVALID_PERIOD_FORMAT]}: {period}",
                field="period",
            )
        ]
    return []