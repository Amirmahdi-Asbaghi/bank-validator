"""Thin wrapper around clickhouse-connect for the gold layer."""
from __future__ import annotations

import logging

import clickhouse_connect
from django.conf import settings

logger = logging.getLogger(__name__)


def _client():
    return clickhouse_connect.get_client(
        host=settings.CLICKHOUSE_HOST,
        port=settings.CLICKHOUSE_PORT,
        username=settings.CLICKHOUSE_USER,
        password=settings.CLICKHOUSE_PASSWORD or "",
        database=settings.CLICKHOUSE_DB,
    )


def write_run_summary(run) -> None:
    """
    Insert one row into validation_summary for the given ValidationRun.
    Called by the async path or by a post-processing hook.
    """
    client = _client()
    client.insert(
        "validation_summary",
        [
            [
                str(run.id),
                run.bank_code or "",
                run.period or "",
                int(run.total_records or 0),
                int(run.valid_count or 0),
                int(run.invalid_count or 0),
                int(run.duplicate_count or 0),
                run.errors_by_code or {},
                run.finished_at or run.created_at,
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
    """Return the summary row for a run, or None if not present."""
    client = _client()
    result = client.query(
        "SELECT run_id, bank_code, period, total_records, valid_count, "
        "invalid_count, duplicate_count, errors_by_code, created_at, finished_at "
        "FROM validation_summary WHERE run_id = {rid:UUID} LIMIT 1",
        parameters={"rid": str(run_id)},
    )
    rows = result.result_rows
    if not rows:
        return None
    r = rows[0]
    return {
        "run_id": str(r[0]),
        "bank_code": r[1],
        "period": r[2],
        "total": r[3],
        "valid": r[4],
        "invalid": r[5],
        "duplicates": r[6],
        "errors_by_code": dict(r[7] or {}),
        "created_at": r[8],
        "finished_at": r[9],
    }