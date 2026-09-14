"""Upload endpoint for validation files.

This is the entry point for the entire pipeline. Everything downstream —
MinIO, Kafka, Spark, Delta, ClickHouse — is triggered from here.

The view has two modes, chosen by file size:

    Small file  (< SMALL_FILE_THRESHOLD, default 50 MB)
        Validate synchronously with pandas, write results to Postgres,
        return 201 with the full summary.

    Large file  (>= threshold)
        Persist to MinIO, publish an event to Kafka, return 202 with a
        run_id. Spark Structured Streaming consumes the topic and does
        the actual work asynchronously.

Why two modes?
    Spark's startup overhead (~10s for a SparkContext) is unacceptable
    for a 200 KB file. But an in-process pandas validator cannot scale
    to a 500 MB file with millions of rows. The size router picks the
    right engine per request.
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
            "Small files are validated synchronously and return **201** with the "
            "full summary. Large files are persisted to object storage and a "
            "Kafka event is published; the response is **202** with "
            "`status='queued'` and a `run_id` you can poll."
        ),
        # The request is multipart because of the file field. Swagger UI
        # renders this as a file picker.
        request={"multipart/form-data": UploadSerializer},
        # Two success shapes depending on which path is taken. We declare
        # both so the spec is honest about the 201 vs 202 contract.
        responses={
            201: ValidationRunSerializer,
            202: ValidationRunSerializer,
            400: OpenApiResponse(
                description="Missing file, unsupported extension, or validation failure"
            ),
            415: OpenApiResponse(description="Unsupported media type"),
        },
        tags=["validation"],
    )
)
class ValidationUploadView(APIView):
    # File uploads arrive as multipart form data, not JSON.
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, *args, **kwargs):
        # Validate the request shape before doing any work. If the file is
        # missing, too large, or the extension is wrong, the serializer
        # raises a 400 with a useful message.
        serializer = UploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        upload = serializer.validated_data["file"]

        # Read the whole file into memory once. We need it both to compute
        # the hash and to save it to MinIO, so buffering it here avoids a
        # second read. Files are capped at 200 MB by the serializer, so
        # this is safe.
        content = upload.read()

        # Threshold is configurable via .env (SMALL_FILE_THRESHOLD, in bytes)
        # so we can tune it per environment without a code change.
        threshold = getattr(settings, "SMALL_FILE_THRESHOLD", 50 * 1024 * 1024)

        if len(content) < threshold:
            # -------- Sync path (pandas) --------
            # The request blocks until validation finishes. For small files
            # this is milliseconds, so the trade-off is fine.
            run = run_validation_sync(upload.name, content)

            # A run that ends in "failed" (e.g. unparseable CSV) is a client
            # error, not a server error, so we map it to 400.
            http_status = (
                status.HTTP_201_CREATED
                if run.status == "completed"
                else status.HTTP_400_BAD_REQUEST
            )
        else:
            # -------- Async path (Kafka + Spark) --------
            # The file is persisted to MinIO and a Kafka event is published.
            # We do NOT wait for Spark to process it. The client polls the
            # run_id to see progress.
            run = run_validation_async(upload.name, content)
            http_status = status.HTTP_202_ACCEPTED

        return Response(ValidationRunSerializer(run).data, status=http_status)