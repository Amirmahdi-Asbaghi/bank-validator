"""Thin wrapper around clickhouse-connect for the gold layer.

ClickHouse holds the analytical result of each validation run — one
row per run with aggregate counts and an error breakdown. It's the
"gold" layer in the medallion model: not raw files, not per-record
data, but pre-aggregated summaries designed for fast analytical queries.

Why a separate store from Postgres:
    Postgres is the operational database — ACID, point lookups, the
    run's metadata. ClickHouse is the analytical database — columnar,
    compressed, sub-second GROUP BY across millions of rows. A dashboard
    querying "how many errors for bank 010 last month?" hits ClickHouse,
    not Postgres.

    This is a CQRS-style split. Writes go to Postgres (the run row).
    Analytical reads go to ClickHouse. The two stores hold different
    shapes of the same data.

Why only two functions:
    The app needs exactly two operations — write a summary and read one
    back. Adding more would grow the interface without a caller. When
    the app grows (e.g. a cross-run dashboard), we'd add the queries it
    needs, not a general-purpose ClickHouse API.

Who writes to ClickHouse:
    The Spark streaming job, not Django. When Spark finishes a run, it
    inserts the summary directly. Django reads it via fetch_summary.
    write_run_summary exists for the sync path or a future post-processing
    hook — it's the Django-side writer for completeness.
"""
from __future__ import annotations

import logging

import clickhouse_connect
from django.conf import settings

logger = logging.getLogger(__name__)


def _client():
    """Create a clickhouse-connect client.

    Creates a new client per call. clickhouse-connect clients are
    lightweight — they wrap an HTTP connection. For our volume (a few
    queries per minute), per-call is fine.

    The password falls back to empty string because ClickHouse accepts
    an empty password for the default user in local dev. In production,
    the password would come from a secret manager.
    """
    return clickhouse_connect.get_client(
        host=settings.CLICKHOUSE_HOST,
        port=settings.CLICKHOUSE_PORT,
        username=settings.CLICKHOUSE_USER,
        # Empty string, not None — clickhouse-connect rejects None but
        # accepts an empty password (the default in dev).
        password=settings.CLICKHOUSE_PASSWORD or "",
        database=settings.CLICKHOUSE_DB,
    )


def write_run_summary(run) -> None:
    """Insert one row into validation_summary for a ValidationRun.

    Called by the async path after Spark finishes, or by a
    post-processing hook in the sync path. Idempotency depends on the
    caller: a second call inserts a second row, so callers should
    ensure it runs once per run.

    Args:
        run: a ValidationRun instance with its count fields populated.

    The column order matters — clickhouse-connect inserts by position,
    not by dict key. The `column_names` argument names the columns for
    clarity, but the data rows are positional lists.

    Why positional and not dict:
        clickhouse-connect's insert API takes either a list of tuples
        (positional) or a list of dicts. Positional is faster — no
        per-row dict lookup. For a one-row insert the difference is
        negligible, but the pattern is consistent with bulk inserts.
    """
    client = _client()
    client.insert(
        "validation_summary",
        [
            [
                # ClickHouse UUIDs are strict — pass a string, not a
                # UUID object. The library handles the conversion but
                # the string form is unambiguous.
                str(run.id),
                # Empty strings, not None. The ClickHouse column is
                # `String`, not `Nullable(String)`. None would fail
                # the insert. `bank_code` and `period` are currently
                # always empty for async runs.
                run.bank_code or "",
                run.period or "",
                # Integers, not the raw field values. ClickHouse's
                # UInt32 rejects None. `or 0` converts None to 0.
                int(run.total_records or 0),
                int(run.valid_count or 0),
                int(run.invalid_count or 0),
                int(run.duplicate_count or 0),
                # errors_by_code is a Map(String, UInt32) in
                # ClickHouse. Django's JSONField gives us a dict;
                # clickhouse-connect serializes it to the native type.
                run.errors_by_code or {},
                # `created_at` is a DateTime column — non-nullable.
                # Fall back to created_at if finished_at is None
                # (shouldn't happen for a completed run, but defensive).
                run.finished_at or run.created_at,
                # `finished_at` is Nullable(DateTime) — None is allowed.
                run.finished_at,
            ]
        ],
        column_names=[
            "run_id",
            "bank_code",
            "period",
            "total_records",
            "valid_count",
            "invalid_count",
            "duplicate_count",
            "errors_by_code",
            "created_at",
            "finished_at",
        ],
    )
    logger.info("Wrote ClickHouse summary for run %s", run.id)


def fetch_summary(run_id: str) -> dict | None:
    """Return the summary row for a run, or None if not present.

    Used by the API's fallback logic in reports/views.py: when Postgres
    says a run is still "queued", the view asks ClickHouse whether Spark
    has actually finished. A non-None result means yes.

    Args:
        run_id: the UUID of the run, as a string.

    Returns:
        A dict with the summary fields, or None if no row exists.

    Why return a dict instead of a dataclass:
        The result flows into an API response — a dict is the natural
        shape. The keys match the JSON field names (short names like
        "total", not "total_records") because the caller merges them
        into the run's serialized dict.
    """
    client = _client()

    # Parameterized query — the {rid:UUID} placeholder is replaced by
    # clickhouse-connect with the parameter's value, correctly typed
    # as a UUID. This prevents SQL injection and handles the type
    # conversion (ClickHouse expects a specific UUID format).
    #
    # LIMIT 1 because run_id is the table's primary key (via ORDER BY
    # in the MergeTree). At most one row can match.
    result = client.query(
        "SELECT run_id, bank_code, period, total_records, valid_count, "
        "invalid_count, duplicate_count, errors_by_code, created_at, finished_at "
        "FROM validation_summary WHERE run_id = {rid:UUID} LIMIT 1",
        parameters={"rid": str(run_id)},
    )

    # `result_rows` is a list of tuples. If no rows match, it's empty.
    rows = result.result_rows
    if not rows:
        return None

    # Unpack the single row into a dict. Positional indexing matches
    # the SELECT column order.
    #
    # Why not use `result.named_results()`?
    #     clickhouse-connect supports named results, but the column
    #     names in the table (e.g. `total_records`) don't match the
    #     dict keys the caller expects (e.g. `total`). The positional
    #     unpacking lets us rename in one place.
    r = rows[0]
    return {
        "run_id": str(r[0]),
        "bank_code": r[1],
        "period": r[2],
        # Renamed for the API's compact summary shape. The view merges
        # these into a dict that also has short names.
        "total": r[3],
        "valid": r[4],
        "invalid": r[5],
        "duplicates": r[6],
        # ClickHouse returns Map as a plain dict. `or {}` handles the
        # (impossible) None case defensively.
        "errors_by_code": dict(r[7] or {}),
        "created_at": r[8],
        "finished_at": r[9],
    }