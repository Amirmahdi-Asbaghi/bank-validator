"""Read-only endpoints for inspecting a validation run.

These endpoints serve two different storage backends depending on how
the run was processed:

    Sync path   (small files)
        Django validates in-process and writes to Postgres.
        All four endpoints are served directly from Postgres.

    Async path  (large files → Kafka → Spark)
        Django only enqueues. Spark writes the actual results to
        ClickHouse (summary) and Delta on MinIO (records).
        For these runs, Postgres stays at status=queued forever, so
        the endpoints fall back to ClickHouse to reflect the real state.

This is a CQRS-style split: Postgres holds operational state, ClickHouse
holds the analytical result. The ``source`` field in each response tells
the client which store answered.

Known limitation:
    ``/invalid`` and ``/valid`` serve from Postgres only. For async
    runs, records live in Delta on MinIO — queryable with Spark, not
    through these endpoints. The OpenAPI description states this.
"""
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.storage.clickhouse_client import fetch_summary
from apps.validation.models import InvalidRecord, ValidRecord, ValidationRun

from .serializers import (
    InvalidRecordSerializer,
    ValidRecordSerializer,
    ValidationRunSerializer,
)


class InvalidPagination(PageNumberPagination):
    """Pagination for record listings.

    Default 100 rows keeps a single page small enough for a browser.
    Clients can request up to 1000 per page for bulk export.

    The class name ``InvalidPagination`` is a bit misleading — it's
    used by both the valid and invalid endpoints. A rename to
    ``RecordPagination`` would be cleaner; left as-is to avoid churn.
    """
    page_size = 100
    page_size_query_param = "page_size"
    max_page_size = 1000


# Declared once so all four endpoint schemas stay consistent.
# Rather than repeat the OpenApiParameter definition on each view,
# we reference the same instance. One place to update.
_RUN_ID_PARAM = OpenApiParameter(
    name="run_id",
    type=str,
    location=OpenApiParameter.PATH,
    description="UUID of the validation run",
)


def _resolve_run_state(run: ValidationRun) -> dict:
    """Return the *real* state of a run, merging two sources.

    Priority:

        1. If Postgres already has finished numbers (sync path),
           use them.
        2. Otherwise, try ClickHouse (async path result written by
           Spark's streaming job).
        3. Otherwise, return Postgres as-is (still queued / processing).

    A ``source`` key is added so clients can tell which store answered.
    This is what makes ``/validation/{id}`` and ``/summary`` accurate
    for both paths with a single code path.

    Why a merge instead of two endpoints?
        Clients shouldn't have to know whether their run was sync or
        async. The endpoint returns the truth regardless. The ``source``
        field is diagnostic, not required for correct usage.

    Args:
        run: the ValidationRun row (already fetched from Postgres).

    Returns:
        A dict with all the fields the serializers expect, plus
        ``source``.
    """
    # Start with Postgres — always available, always has the metadata
    # (id, source_file, timestamps).
    data = ValidationRunSerializer(run).data

    # -------- Sync path --------
    # If Postgres says the run is completed AND has non-zero counts,
    # the pandas path filled these fields. Trust them — they're the
    # authoritative source for sync runs.
    #
    # The `total_records > 0` check matters: a run can legitimately
    # have 0 records (empty file), but we treat that as "no counts
    # yet" here because ClickHouse might have a row for it. A future
    # refinement would distinguish "completed with 0 records" from
    # "queued with unknown counts" via the started_at timestamp.
    if run.status == "completed" and run.total_records > 0:
        data["source"] = "postgres"
        return data

    # -------- Async path --------
    # Postgres is stale (still queued). Ask ClickHouse where Spark
    # writes the summary for async runs.
    try:
        ch = fetch_summary(str(run.id))
    except Exception:
        # ClickHouse unreachable shouldn't 500 the API. Fall through
        # to returning the Postgres row, which will say "queued".
        # The client can retry.
        ch = None

    if ch:
        # ClickHouse has data — Spark finished processing.
        # Overwrite the stale Postgres fields.
        data["status"] = "completed"          # Spark only writes finished runs
        data["total_records"] = ch.get("total", 0)
        data["valid_count"] = ch.get("valid", 0)
        data["invalid_count"] = ch.get("invalid", 0)
        data["duplicate_count"] = ch.get("duplicates", 0)
        data["errors_by_code"] = ch.get("errors_by_code", {})
        data["source"] = "clickhouse"
    else:
        # No data anywhere else — the run is genuinely still queued
        # (or Spark hasn't finished yet). Return Postgres as-is.
        data["source"] = "postgres"

    return data


class RunDetailView(APIView):
    """Full run record, merged from Postgres + ClickHouse.

    Clients use this to poll after an async upload. ``status`` will
    flip from ``queued`` to ``completed`` as soon as Spark writes the
    ClickHouse summary — the API reflects that automatically.
    """

    @extend_schema(
        summary="Get a validation run by ID",
        description=(
            "Returns the current state of a run. For async runs, the "
            "summary fields come from ClickHouse once Spark has finished. "
            "The `source` field tells you which store answered."
        ),
        parameters=[_RUN_ID_PARAM],
        responses={
            200: ValidationRunSerializer,
            404: OpenApiResponse(description="Run not found"),
        },
        tags=["validation"],
    )
    def get(self, request, run_id):
        # get_object_or_404 raises Http404 if not found, which DRF
        # converts to a 404 response. Cleaner than checking manually.
        run = get_object_or_404(ValidationRun, id=run_id)
        return Response(_resolve_run_state(run))


class RunSummaryView(APIView):
    """Compact summary — just the counts, nothing else.

    Same fallback logic as RunDetailView, but the response shape is
    trimmed to what a dashboard or status page would use. The full
    record (with file_hash, source_file, timestamps) is available via
    RunDetailView.
    """

    @extend_schema(
        summary="Get a compact summary for a run",
        description=(
            "Returns counts per run: total, valid, invalid, duplicates, "
            "and `errors_by_code`. Reads from Postgres for sync runs, "
            "from ClickHouse for async runs."
        ),
        parameters=[_RUN_ID_PARAM],
        responses={
            200: OpenApiResponse(description="Summary object"),
            404: OpenApiResponse(description="Run not found"),
        },
        tags=["validation"],
    )
    def get(self, request, run_id):
        run = get_object_or_404(ValidationRun, id=run_id)
        merged = _resolve_run_state(run)

        # Build the compact response from the merged dict. Field names
        # are shortened (total instead of total_records) — this is the
        # dashboard-friendly shape.
        return Response(
            {
                "run_id": str(run.id),
                "bank_code": merged.get("bank_code", ""),
                "period": merged.get("period", ""),
                "status": merged.get("status"),
                "total": merged.get("total_records", 0),
                "valid": merged.get("valid_count", 0),
                "invalid": merged.get("invalid_count", 0),
                "duplicates": merged.get("duplicate_count", 0),
                "errors_by_code": merged.get("errors_by_code", {}),
                "source": merged.get("source", "postgres"),
                # Timestamps come from Postgres — they're metadata,
                # not part of the analytical result.
                "created_at": run.created_at,
                "finished_at": merged.get("finished_at"),
            }
        )


class RunInvalidView(APIView):
    """Paginated list of invalid records for a run.

    Reads from Postgres only. For async runs, records live in the Delta
    quarantine table on MinIO — that data isn't mirrored to Postgres by
    the streaming job. The OpenAPI description states this clearly so
    clients know what to expect.

    Each returned record has:
        error_codes     — machine-readable (e.g. ["E004","E007"])
        error_messages  — human-readable
        raw_row         — the original input, never modified
    """

    @extend_schema(
        summary="List invalid records for a run",
        description=(
            "Paginated list of records that failed at least one rule. "
            "Served from Postgres. For async runs, records are stored in "
            "Delta on MinIO — this endpoint will be empty; use ClickHouse "
            "or MinIO to inspect the records."
        ),
        parameters=[_RUN_ID_PARAM],
        responses={
            200: InvalidRecordSerializer(many=True),
            404: OpenApiResponse(description="Run not found"),
        },
        tags=["validation"],
    )
    def get(self, request, run_id):
        run = get_object_or_404(ValidationRun, id=run_id)

        # Stable ordering by row_number — matches the original file
        # order so failures are easy to cross-reference with the
        # source file. The composite index (run, row_number) makes
        # this an index scan, not a sort.
        qs = InvalidRecord.objects.filter(run=run).order_by("row_number")

        # DRF's PageNumberPagination handles ?page=N&page_size=M.
        # It returns a paginated response with count/next/previous/results.
        paginator = InvalidPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = InvalidRecordSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class RunValidView(APIView):
    """Paginated list of valid records for a run.

    Same caveat as RunInvalidView — Postgres only. For sync runs this
    is a complete listing; for async runs, the equivalent data is in
    the curated Delta table on MinIO.
    """

    @extend_schema(
        summary="List valid records for a run",
        description=(
            "Paginated list of records that passed every rule. "
            "Served from Postgres. For async runs, records are stored in "
            "Delta on MinIO."
        ),
        parameters=[_RUN_ID_PARAM],
        responses={
            200: ValidRecordSerializer(many=True),
            404: OpenApiResponse(description="Run not found"),
        },
        tags=["validation"],
    )
    def get(self, request, run_id):
        run = get_object_or_404(ValidationRun, id=run_id)
        qs = ValidRecord.objects.filter(run=run).order_by("row_number")

        paginator = InvalidPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = ValidRecordSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)