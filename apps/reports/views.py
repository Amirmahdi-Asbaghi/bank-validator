"""Read-only endpoints for inspecting a validation run.

These endpoints serve two different storage backends depending on how the
run was processed:

    Sync path   (small files)
        Django validates in-process and writes to Postgres.
        All four endpoints are served directly from Postgres.

    Async path  (large files → Kafka → Spark)
        Django only enqueues. Spark writes the actual results to
        ClickHouse (summary) and Delta on MinIO (records).
        For these runs, Postgres stays at status=queued forever, so the
        endpoints fall back to ClickHouse to reflect the real state.

This is a CQRS-style split: Postgres holds operational state, ClickHouse
holds the analytical result. The `source` field in each response tells
the client which store answered.
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
    """
    page_size = 100
    page_size_query_param = "page_size"
    max_page_size = 1000


# Declared once so all four endpoint schemas stay consistent.
_RUN_ID_PARAM = OpenApiParameter(
    name="run_id",
    type=str,
    location=OpenApiParameter.PATH,
    description="UUID of the validation run",
)


def _resolve_run_state(run: ValidationRun) -> dict:
    """Return the *real* state of a run, merging two sources.

    Priority:
      1. If Postgres already has finished numbers (sync path) → use them.
      2. Otherwise, try ClickHouse (async path result written by Spark).
      3. Otherwise, return Postgres as-is (still queued / processing).

    A `source` key is added so clients can tell where the numbers came from.
    This is what makes `/validation/{id}` and `/summary` accurate for both
    paths with a single code path.
    """
    data = ValidationRunSerializer(run).data

    # Sync path — Postgres has everything we need.
    if run.status == "completed" and run.total_records > 0:
        data["source"] = "postgres"
        return data

    # Async path — Postgres is stale, ask ClickHouse.
    try:
        ch = fetch_summary(str(run.id))
    except Exception:
        # ClickHouse unreachable shouldn't 500 the API. Fall through
        # to returning the Postgres row (which will say "queued").
        ch = None

    if ch:
        data["status"] = "completed"          # Spark only writes finished runs
        data["total_records"] = ch.get("total", 0)
        data["valid_count"] = ch.get("valid", 0)
        data["invalid_count"] = ch.get("invalid", 0)
        data["duplicate_count"] = ch.get("duplicates", 0)
        data["errors_by_code"] = ch.get("errors_by_code", {})
        data["source"] = "clickhouse"
    else:
        data["source"] = "postgres"

    return data


class RunDetailView(APIView):
    """Full run record, merged from Postgres + ClickHouse.

    Clients use this to poll after an async upload. `status` will flip
    from `queued` to `completed` as soon as Spark writes the ClickHouse
    summary — the API reflects that automatically.
    """

    @extend_schema(
        summary="Get a validation run by ID",
        description=(
            "Returns the current state of a run. For async runs, the summary "
            "fields come from ClickHouse once Spark has finished. The "
            "`source` field tells you which store answered."
        ),
        parameters=[_RUN_ID_PARAM],
        responses={
            200: ValidationRunSerializer,
            404: OpenApiResponse(description="Run not found"),
        },
        tags=["validation"],
    )
    def get(self, request, run_id):
        run = get_object_or_404(ValidationRun, id=run_id)
        return Response(_resolve_run_state(run))


class RunSummaryView(APIView):
    """Compact summary — just the counts, nothing else.

    Same fallback logic as RunDetailView, but the response shape is
    trimmed to what a dashboard or status page would use.
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
                "created_at": run.created_at,
                "finished_at": merged.get("finished_at"),
            }
        )


class RunInvalidView(APIView):
    """Paginated list of invalid records for a run.

    Reads from Postgres only. For async runs, records live in the Delta
    quarantine table on MinIO — that data isn't mirrored to Postgres by
    the streaming job yet. The OpenAPI description states this clearly
    so clients know what to expect.

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

        # Stable ordering by row_number — matches the original file order
        # so failures are easy to cross-reference with the source.
        qs = InvalidRecord.objects.filter(run=run).order_by("row_number")

        paginator = InvalidPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = InvalidRecordSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class RunValidView(APIView):
    """Paginated list of valid records for a run.

    Same caveat as RunInvalidView — Postgres only. For sync runs this is
    a complete listing; for async runs, the equivalent data is in the
    curated Delta table on MinIO.
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