"""Prometheus counters for the validation pipeline."""
from __future__ import annotations

from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST
from django.http import HttpResponse


# Business-level counters
RECORDS_UPLOADED_TOTAL = Counter(
    "bankval_records_uploaded_total",
    "Total number of records uploaded for validation",
)

RECORDS_VALID_TOTAL = Counter(
    "bankval_records_valid_total",
    "Total number of valid records",
)

RECORDS_INVALID_TOTAL = Counter(
    "bankval_records_invalid_total",
    "Total number of invalid records",
)

RUNS_TOTAL = Counter(
    "bankval_runs_total",
    "Total number of validation runs, labelled by path",
    ["path"],  # path = "sync" | "async_celery" | "async_kafka"
)

KAFKA_PUBLISH_ERRORS_TOTAL = Counter(
    "bankval_kafka_publish_errors_total",
    "Total number of failed Kafka publishes",
)


def metrics_view(request):
    """Django view exposing the Prometheus metrics."""
    return HttpResponse(generate_latest(), content_type=CONTENT_TYPE_LATEST)