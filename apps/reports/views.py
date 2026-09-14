"""Read-only endpoints for inspecting a validation run.

These views are the "serving" layer of the pipeline. They read from
Postgres (operational state) and, indirectly, reflect what Spark has
already written to Delta and ClickHouse.

    GET /api/v1/validation/{run_id}            — full run record
    GET /api/v1/validation/{run_id}/summary    — compact aggregate
    GET /api/v1/validation/{run_id}/invalid    — paginated invalid records
    GET /api/v1/validation/{run_id}/valid      — paginated valid records

Why these are read-only:
    Runs are only created by the upload endpoint. All mutations happen
    through the pipeline (Spark writing to Delta, ClickHouse summaries).
    Exposing read endpoints lets clients poll without any write path.
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

from apps.validation.models import InvalidRecord, ValidRecord, ValidationRun

from .serializers import (
    InvalidRecordSerializer,
    ValidRecordSerializer,
    ValidationRunSerializer,
)


class InvalidPagination(PageNumberPagination):
    """Pagination for the record listing endpoints.

    Default page size is 100 (reasonable for API browsing). Clients can
    pass ?page_size=N up to 1000 to bulk-export without hundreds of
    round-trips.
    """
    page_size = 100
    page_size_query_param = "page_size"
    max_page_size = 1000


# Shared path parameter declaration for OpenAPI. Declared once to keep the
# decorators short and the spec consistent across all four endpoints.
_RUN_ID_PARAM = OpenApiParameter(
    name="run_id",
    type=str,
    location=OpenApiParameter.PATH,
    description="UUID of the validation run",
)


class RunDetailView(APIView):
    """Return the current state of a run.

    Clients use this to poll after an async upload: the `status` field
    transitions over time from `queued` to `running` to `completed`
    (or `failed`).
    """

    @extend_schema(
        summary="Get a validation run by ID",
        description=(
            "Returns the current state of a run. `status` transitions over time: "
            "`queued` → `running` → `completed` (or `failed`)."
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
        return Response(ValidationRunSerializer(run).data)


class RunSummaryView(APIView):
    """Return a compact aggregate for a run.

    This is a lighter projection of the run: only the fields a dashboard
    or a status page would need. The full record (with timestamps,
    file_hash, etc.) is available via RunDetailView.
    """

    @extend_schema(
        summary="Get a compact summary for a run",
        description=(
            "Returns counts per run: total, valid, invalid, duplicates, and a "
            "map of `errors_by_code` (e.g. `{\"E004\": 1, \"E007\": 2}`)."
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
        return Response(
            {
                "run_id": str(run.id),
                "bank_code": run.bank_code,
                "period": run.period,
                "status": run.status,
                "total": run.total_records,
                "valid": run.valid_count,
                "invalid": run.invalid_count,
                "duplicates": run.duplicate_count,
                # errors_by_code is a JSONField on the model, so it's already
                # a dict — no decoding needed.
                "errors_by_code": run.errors_by_code,
                "created_at": run.created_at,
                "finished_at": run.finished_at,
            }
        )


class RunInvalidView(APIView):
    """Paginated list of invalid records for a run.

    Each record carries three fields worth noting:

        error_codes      — the rule codes that failed (e.g. ["E005","E007"])
        error_messages   — human-readable explanations
        raw_row          — the original input as parsed

    `raw_row` is what makes reprocessing possible: the original data is
    never lost, so a bank can fix the source and re-upload, or we can
    replay the record through a corrected rule set.
    """

    @extend_schema(
        summary="List invalid records for a run",
        description=(
            "Paginated list of records that failed at least one rule. Each "
            "record carries `error_codes`, `error_messages`, and `raw_row`."
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

        # order_by row_number gives a stable, source-order listing — useful
        # when cross-referencing against the original file.
        qs = InvalidRecord.objects.filter(run=run).order_by("row_number")

        paginator = InvalidPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = InvalidRecordSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class RunValidView(APIView):
    """Paginated list of valid records for a run.

    These are the rows that made it through every rule. In production,
    downstream systems would read from the curated Delta table in MinIO
    instead — this endpoint is a convenience for browsing and testing.
    """

    @extend_schema(
        summary="List valid records for a run",
        description="Paginated list of records that passed every rule.",
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