"""Upload endpoint for validation files.

This is the entry point for the entire pipeline. Everything downstream
— MinIO, Kafka, Spark, Delta, ClickHouse — is triggered from here.

Two modes, chosen by file size:

    Small file (< SMALL_FILE_THRESHOLD, default 50 MB)
        Validate synchronously with pandas, write results to Postgres,
        return 201 with the full summary.

    Large file (>= threshold)
        Persist to MinIO, publish an event to Kafka, return 202 with a
        run_id. Spark Structured Streaming consumes the topic and does
        the actual work asynchronously.

Why two modes?
    Spark's startup overhead (~10s for a SparkContext) is unacceptable
    for a 200 KB file. But an in-process pandas validator cannot scale
    to a 500 MB file with millions of rows. The size router picks the
    right engine per request.

Why the view stays thin:
    HTTP concerns (parsing, status codes) live here. Domain logic
    (save, validate, persist) lives in services.py. The view has no
    business logic — it decides which service function to call and
    maps the result to a status code.
"""
from django.conf import settings
from drf_spectacular.utils import (
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.reports.serializers import ValidationRunSerializer

from .serializers import UploadSerializer
from .services import run_validation_async, run_validation_sync


@extend_schema_view(
    post=extend_schema(
        summary="Upload a file for validation",
        description=(
            "Accepts a CSV or JSON file (max 200 MB). "
            "Small files are validated synchronously and return **201** with "
            "the full summary. Large files are persisted to object storage "
            "and a Kafka event is published; the response is **202** with "
            "`status='queued'` and a `run_id` you can poll."
        ),
        # Declare the request body explicitly so Swagger UI renders a
        # file picker. Without `format: binary`, the field would render
        # as a text input and the multipart encoding would be lost.
        request={
            "multipart/form-data": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "format": "binary",
                    },
                },
                "required": ["file"],
            },
        },
        # Both success shapes are declared. The spec is honest that
        # the endpoint returns 201 OR 202 depending on file size.
        responses={
            201: ValidationRunSerializer,
            202: ValidationRunSerializer,
            400: OpenApiResponse(
                description=(
                    "Missing file, unsupported extension, or validation failure"
                )
            ),
            415: OpenApiResponse(description="Unsupported media type"),
        },
        tags=["validation"],
    )
)
class ValidationUploadView(APIView):
    """POST /api/v1/validation — accept a file for validation."""

    # File uploads arrive as multipart form data, not JSON.
    # DRF needs explicit parsers here because the default is JSON.
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, *args, **kwargs):
        """Handle the upload.

        Order of operations matters:

            1. Validate the request shape (serializer).
            2. Read the file content (once, into memory).
            3. Decide sync vs async by size.
            4. Call the appropriate service.
            5. Return with the right status code.

        Errors during step 1 return 400 with a useful message.
        Errors during step 4 are handled by the service — the run is
        marked failed, and the response reflects that.
        """
        # -------- 1. Validate the request --------
        # UploadSerializer enforces:
        #   - file is present
        #   - extension is .csv or .json
        #   - size ≤ 200 MB
        # Any failure raises a ValidationError, which DRF turns into a
        # 400 with a structured error body. We don't catch it — the
        # default behavior is what we want.
        serializer = UploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # At this point the file is guaranteed valid. DRF has already
        # buffered it (in memory for small files, to disk for large).
        upload = serializer.validated_data["file"]

        # -------- 2. Read the file into memory --------
        # We read the whole file to a bytes object so we can:
        #   - Compute the SHA-256 hash (both paths need it)
        #   - Save to MinIO (both paths need it)
        #   - Parse (the sync path needs it immediately)
        #
        # Reading once and passing bytes everywhere avoids a second
        # read from the uploaded file. The serializer caps size at
        # 200 MB, so this is bounded.
        content = upload.read()

        # -------- 3. Decide sync vs async --------
        # Threshold is configurable via .env so environments can tune
        # it without a code change. The default (50 MB) matches the
        # spot where Spark's startup overhead starts to pay off.
        threshold = getattr(settings, "SMALL_FILE_THRESHOLD", 50 * 1024 * 1024)

        if len(content) < threshold:
            # -------- Sync path (pandas) --------
            # The request blocks until validation finishes. For small
            # files this is milliseconds, so the trade-off is fine.
            run = run_validation_sync(upload.name, content)

            # A failed sync run (e.g. unparseable CSV) is the client's
            # fault — bad input, not a server error. Map to 400.
            # A completed run is a normal 201 Created.
            http_status = (
                status.HTTP_201_CREATED
                if run.status == "completed"
                else status.HTTP_400_BAD_REQUEST
            )
        else:
            # -------- Async path (Kafka + Spark) --------
            # The file is persisted to MinIO and a Kafka event is
            # published. We do NOT wait for Spark to process it.
            # The client polls the run_id to see progress.
            #
            # If the Kafka publish fails, run_validation_async raises
            # — DRF turns that into a 500 automatically.
            run = run_validation_async(upload.name, content)
            http_status = status.HTTP_202_ACCEPTED

        # -------- 5. Return the run state --------
        # Serialize the run and return with the appropriate status.
        # The response shape is identical for 201 and 202; the
        # `status` field distinguishes them (completed vs. queued).
        return Response(
            ValidationRunSerializer(run).data,
            status=http_status,
        )