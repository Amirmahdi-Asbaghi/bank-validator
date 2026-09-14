"""Rule engine — composes all rules and applies them to a batch of records.

This is the entry point for validation. It does three things:

    1. Calls every row-level rule (E001–E005, E007, E008, E011) against
       each record and collects the failures.

    2. Handles duplicate detection (E006) — the only rule that requires
       cross-record state. It tracks keys seen so far and flags any
       key that appears more than once.

    3. Aggregates results into a summary plus per-record outcomes.

The engine is deliberately the only place that knows about the order
of rules and the shape of the final result. Every rule module stays
focused on a single check, and swapping or adding rules is done here.

Note on parallelism:
    This engine is single-threaded for clarity. The Spark path
    (spark_jobs/rules.py) implements the same semantics distributively.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

# Import each rule module with a short alias. The `r_` prefix keeps the
# namespace flat and makes it obvious these are rule modules, not
# utility modules.
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

# The ordered list of row-level rules. Order matters for two reasons:
#
#   1. Rule semantics. E001 checks that a field is present; running it
#      before E003 means we don't try to inspect fields that aren't there.
#
#   2. Failure collection. Failures are reported in the order they're
#      discovered, so putting structural checks first (E001, E002, E003)
#      gives a more logical error list to the bank.
#
# E006 is intentionally absent — it's handled separately below because
# it needs cross-record state.
ROW_LEVEL_RULES = (
    r_required.check,     # E001 — field presence
    r_types.check,        # E002 — numeric parseability
    r_nulls.check,        # E003 — non-empty strings
    r_bank_code.check,    # E004 — allow-list membership
    r_period.check,       # E005 — format regex
    r_financial.check,    # E007, E008 — accounting consistency
    r_currency.check,     # E011 — optional ISO code
)


def validate_batch(
    records: Iterable[dict[str, Any]],
    allowed_bank_codes: set[str] | None = None,
) -> dict[str, Any]:
    """Run every rule against every record.

    Args:
        records: an iterable of parsed input rows. Must yield dicts
                 where each dict represents one record. The iterable
                 is consumed exactly once and not stored — this keeps
                 memory usage flat even for very large files.
        allowed_bank_codes: set of bank codes to accept (E004). If None,
                 the allow-list is empty and every bank code fails E004.

    Returns:
        A dict with two keys:
          - summary: aggregate counts for the whole batch
          - outcomes: one entry per record, in source order

    The two-key return allows callers to persist the records and the
    summary separately — which is what the ingestion service does
    (writes both to Postgres for the sync path).
    """
    # Context is passed to every rule. It carries shared per-batch data
    # that would be expensive to reload for each row — like the bank
    # allow-list, which is a single DB query per batch rather than
    # one per record.
    context: dict[str, Any] = {
        "allowed_bank_codes": allowed_bank_codes or set(),
    }

    # Per-record outcomes are collected here. For a 1M-row file this
    # list holds 1M small dicts — the caller is expected to consume it
    # and write to a store, not keep it in memory forever.
    outcomes: list[dict[str, Any]] = []

    # Duplicate detection state. Maps key → first row number where the
    # key was seen. Using a dict (not a set) so we could later surface
    # "row 42 is a duplicate of row 7" without extra work.
    seen_keys: dict[tuple[str, ...], int] = {}

    # Aggregate error counts by code. Used to build the summary's
    # errors_by_code map. A Counter is a dict subclass that makes
    # incrementing easy.
    error_counts: Counter[str] = Counter()

    # Number of rows flagged E006. Tracked separately because a single
    # row can be a duplicate only once, but multiple rows can fail
    # E004 — we want the duplicate count exact.
    duplicate_count = 0

    # idx starts at 1 because the row_number in the outcome should be
    # 1-indexed. The bank's CSV has line 1 as the header, so data row N
    # is file line N+1. 1-indexed aligns with how humans count rows.
    for idx, record in enumerate(records, start=1):
        failures: list[RuleResult] = []

        # -------- Row-level rules --------
        # Each rule sees only this record (and the shared context).
        # Failures accumulate; no rule short-circuits another.
        for rule in ROW_LEVEL_RULES:
            failures.extend(rule(record, context))

        # -------- E006 — duplicate detection --------
        # Batch-level rule. Requires the set of keys already seen,
        # which is why it can't live in its own check function.
        key = r_duplicates.duplicate_key(record)

        # key is None when the record can't be identified — missing
        # record_id AND missing one of the fallback fields. Those
        # records already failed E001 or E003; we skip duplicate
        # detection for them rather than flagging a second error.
        if key is not None:
            if key in seen_keys:
                # Duplicate found. Increment the counter and add E006
                # to this record's failures.
                duplicate_count += 1
                failures.append(
                    RuleResult(
                        code=ErrorCode.DUPLICATE_RECORD,
                        message=ERROR_MESSAGES[ErrorCode.DUPLICATE_RECORD],
                        # No specific field — the duplicate is a
                        # property of the whole record, not one column.
                        field=None,
                    )
                )
            else:
                # First time we've seen this key. Record it so later
                # rows with the same key are flagged.
                # Store the row number (idx) rather than just True —
                # useful for future features like "row X dups row Y".
                seen_keys[key] = idx

        # -------- Build the outcome for this record --------
        # Convert RuleResult objects to plain strings for storage.
        # The engine's output is meant to be serializable — Django's
        # JSONField, ClickHouse, and API responses all want strings.
        error_codes = [f.code.value for f in failures]
        error_messages = [f.message for f in failures]

        # Update the aggregate count.
        for code in error_codes:
            error_counts[code] += 1

        outcomes.append(
            {
                "row_number": idx,
                "is_valid": not failures,  # empty failures list = valid
                "error_codes": error_codes,
                "error_messages": error_messages,
                # The original record is preserved so the invalid
                # record in quarantine can carry `raw_row`. This is
                # what makes reprocessing possible.
                "raw_row": record,
            }
        )

    # -------- Final summary --------
    total = len(outcomes)
    invalid = sum(1 for o in outcomes if not o["is_valid"])
    valid = total - invalid

    return {
        "summary": {
            "total": total,
            "valid": valid,
            "invalid": invalid,
            "duplicates": duplicate_count,
            # dict() converts the Counter to a plain dict, which is
            # what JSONField and ClickHouse Map expect.
            "errors_by_code": dict(error_counts),
        },
        "outcomes": outcomes,
    }