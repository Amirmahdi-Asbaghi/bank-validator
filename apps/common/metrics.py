"""Prometheus counters for the validation pipeline.

Prometheus is a pull-based metrics system: it periodically scrapes a
`/metrics` endpoint that exposes counters, gauges, and histograms in a
text format. This module defines the counters and the view that exposes
them.

Why business-level metrics:
    Infrastructure metrics (CPU, memory, disk) come free with any
    runtime. Business metrics — records validated, invalid rate,
    Kafka publish failures — are what operations actually watches.
    A spike in `bankval_records_invalid_total` means a bank is
    sending bad data; a spike in `bankval_kafka_publish_errors_total`
    means the broker is unhealthy. Neither shows up in CPU graphs.

Naming convention:
    `<namespace>_<subject>_<unit>`
        bankval   — our service namespace
        records   — the subject
        uploaded  — the action
        total     — the unit (counters end in _total by convention)

    Prometheus requires the `_total` suffix on counters; the client
    library adds it automatically if missing. We include it explicitly
    for readability.

Which metrics we chose and why:
    - records_uploaded  — throughput, the pipeline's core "how much"
    - records_valid     — the good outcome
    - records_invalid   — the bad outcome (alert-worthy if spiking)
    - runs_total        — activity per path (sync vs. async)
    - kafka_publish_errors — the one place where the pipeline can
      silently fail; a non-zero rate is an incident

What we deliberately didn't add:
    - HTTP request counts (Django middleware exists for this)
    - Per-endpoint latency histograms (not required by the spec)
    - Spark executor metrics (Spark has its own Prometheus exporter)
    - User-identifying labels (PII concerns; per-bank breakdown is
      better done via ClickHouse)
"""
from __future__ import annotations

from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST
from django.http import HttpResponse


# ---------------------------------------------------------------------------
# Business-level counters
# ---------------------------------------------------------------------------
#
# Each Counter is a module-level singleton. Prometheus client requires
# this — defining a Counter with the same name twice raises a
# ValueError at import time. So the declaration is the contract; call
# sites only ever call `.inc(...)`.

RECORDS_UPLOADED_TOTAL = Counter(
    "bankval_records_uploaded_total",
    "Total number of records uploaded for validation",
)
# Incremented once per run, by the total count. Tells us the pipeline's
# throughput. A sudden drop means banks stopped uploading or the
# ingestion path broke.

RECORDS_VALID_TOTAL = Counter(
    "bankval_records_valid_total",
    "Total number of valid records",
)
# Records that passed every rule. The ratio valid/(valid+invalid) is
# the "success rate" — the primary business KPI for the platform.

RECORDS_INVALID_TOTAL = Counter(
    "bankval_records_invalid_total",
    "Total number of invalid records",
)
# Records that failed at least one rule. The rate of increase is a
# leading indicator of data quality issues. A spike often means a
# bank's exporter changed.

RUNS_TOTAL = Counter(
    "bankval_runs_total",
    "Total number of validation runs, labelled by path",
    # Labels multiply the metric into a series per label value. Here,
    # one series per `path` value. Prometheus requires the label set
    # to be declared up front — you can't add a label later.
    #
    # Why `path` and not `bank_code`:
    #   `path` has a bounded set of values (currently two). `bank_code`
    #   could grow to hundreds, and each new bank would create a new
    #   time series — Prometheus's cardinality is a real constraint.
    #   Per-bank metrics belong in ClickHouse, not Prometheus.
    ["path"],  # path = "sync" | "async_celery" | "async_kafka"
)
# Counts runs by execution path. A spike in "async_kafka" with a drop
# in "sync" means the threshold is being hit — worth knowing when
# tuning SMALL_FILE_THRESHOLD.

KAFKA_PUBLISH_ERRORS_TOTAL = Counter(
    "bankval_kafka_publish_errors_total",
    "Total number of failed Kafka publishes",
)
# Incremented when publishing to Kafka fails. Non-zero should page
# on-call — every failure means a run will never be processed. The
# metric is unlabelled because any failure is an incident; we don't
# need per-topic or per-broker breakdowns.


# ---------------------------------------------------------------------------
# /metrics endpoint
# ---------------------------------------------------------------------------

def metrics_view(request):
    """Django view exposing the Prometheus metrics.

    Returns the standard Prometheus text exposition format. Prometheus
    scrapes this endpoint on its configured interval (15 seconds by
    default, see `docker/prometheus/prometheus.yml`).

    Security:
        The endpoint has no authentication. In production, we'd either:
          - Bind it to an internal-only port / network
          - Put it behind a reverse proxy with basic auth
          - Use Prometheus's `basic_auth` scrape config
        In this project, the endpoint is only reachable from inside the
        Docker network (Prometheus scrapes it there; the host doesn't
        expose it publicly).
    """
    # `generate_latest()` returns the current metrics as bytes in the
    # Prometheus text format. `CONTENT_TYPE_LATEST` sets the correct
    # MIME type (`text/plain; version=0.0.4; charset=utf-8`).
    #
    # No per-request work happens here — the counters are module-level
    # singletons that live for the process lifetime. The view is O(n)
    # in the number of registered metrics, which is a handful.
    return HttpResponse(
        generate_latest(),
        content_type=CONTENT_TYPE_LATEST,
    )