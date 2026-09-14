"""Tests for the Prometheus metrics endpoint.

The /metrics endpoint exposes counters that Prometheus scrapes. Two
things to verify:

    1. The endpoint returns the expected content type and format
    2. Uploads actually increment the counters

Why test counters:
    A counter that doesn't increment is worse than no counter — it
    silently reports zero, and dashboards/alerts based on it are
    wrong. These tests catch the "forgot to call .inc()" class of
    bug.

Testing the values (not just the format):
    The prometheus_client library maintains process-global counters.
    Testing absolute values is unreliable because other tests might
    have incremented them. Instead, we read the value before and
    after an action, and assert the delta.
"""
from __future__ import annotations

import pytest
from prometheus_client import REGISTRY
from rest_framework.test import APIClient


@pytest.fixture
def client():
    """An unauthenticated DRF test client."""
    return APIClient()


@pytest.fixture
def api_client():
    """Alias for tests that prefer a clearer name."""
    return APIClient()


def _get_counter_value(name, **labels):
    """Read the current value of a Prometheus counter.

    Args:
        name: the metric name (e.g. ``bankval_records_uploaded_total``).
        **labels: label key-value pairs (e.g. ``path="sync"``).

    Returns 0 if the counter has never been incremented.

    Why labels as kwargs:
        ``REGISTRY.get_sample_value`` takes a dict of labels, not a
        PromQL-style string. Passing ``path="sync"`` is cleaner than
        building the dict at each call site.
    """
    return REGISTRY.get_sample_value(name, labels) or 0


def _seed_bank_reference():
    """Populate the allow-list before uploads."""
    from apps.validation.models import BankReference
    for code in ("010", "020", "030"):
        BankReference.objects.update_or_create(
            bank_code=code,
            defaults={"name": f"Bank {code}", "is_active": True},
        )


# ---------------------------------------------------------------------------
# Endpoint format
# ---------------------------------------------------------------------------

def test_metrics_endpoint_responds(client):
    """The /metrics endpoint returns 200."""
    response = client.get("/metrics")
    assert response.status_code == 200


def test_metrics_endpoint_content_type(client):
    """The endpoint returns the Prometheus text format content type.

    Prometheus expects ``text/plain; version=0.0.4`` (or a newer
    version). The library constant ``CONTENT_TYPE_LATEST`` provides
    the right value.
    """
    response = client.get("/metrics")
    content_type = response["Content-Type"]
    assert "text/plain" in content_type


def test_metrics_endpoint_lists_counters(client):
    """The body includes our counter names in the exposition format.

    Even if the counters are zero, they should be exposed — a
    dashboard can show "0" as a value. A missing counter would
    break the query.
    """
    body = client.get("/metrics").content.decode("utf-8")

    # Every counter we define should appear in the exposition
    assert "bankval_records_uploaded_total" in body
    assert "bankval_records_valid_total" in body
    assert "bankval_records_invalid_total" in body
    assert "bankval_runs_total" in body
    assert "bankval_kafka_publish_errors_total" in body


def test_metrics_has_help_and_type_lines(client):
    """Each metric has HELP and TYPE annotations.

    Prometheus uses these for display and querying. A metric
    missing them still works, but the exposition is less useful.
    """
    body = client.get("/metrics").content.decode("utf-8")

    assert "# HELP bankval_records_uploaded_total" in body
    assert "# TYPE bankval_records_uploaded_total counter"


# ---------------------------------------------------------------------------
# Counter increments
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_upload_increments_record_counters(api_client, valid_upload):
    """A successful upload increments the record counters.

    Reads the counters before and after the upload, then asserts
    the delta matches the number of records in the file.
    """
    _seed_bank_reference()

    before_uploaded = _get_counter_value("bankval_records_uploaded_total")
    before_valid = _get_counter_value("bankval_records_valid_total")
    before_invalid = _get_counter_value("bankval_records_invalid_total")

    response = api_client.post(
        "/api/v1/validation",
        {"file": valid_upload},
        format="multipart",
    )
    assert response.status_code == 201

    # Three records uploaded, all valid
    after_uploaded = _get_counter_value("bankval_records_uploaded_total")
    after_valid = _get_counter_value("bankval_records_valid_total")
    after_invalid = _get_counter_value("bankval_records_invalid_total")

    assert after_uploaded - before_uploaded == 3
    assert after_valid - before_valid == 3
    # No invalid records in this file
    assert after_invalid - before_invalid == 0


@pytest.mark.django_db
def test_upload_invalid_increments_invalid_counter(api_client, invalid_upload):
    """An upload with invalid records increments the invalid counter.

    The invalid CSV has 4 rows: 1 valid, 3 invalid.
    """
    _seed_bank_reference()

    before_valid = _get_counter_value("bankval_records_valid_total")
    before_invalid = _get_counter_value("bankval_records_invalid_total")

    response = api_client.post(
        "/api/v1/validation",
        {"file": invalid_upload},
        format="multipart",
    )
    assert response.status_code == 201

    after_valid = _get_counter_value("bankval_records_valid_total")
    after_invalid = _get_counter_value("bankval_records_invalid_total")

    # 1 valid row, 3 invalid
    assert after_valid - before_valid == 1
    assert after_invalid - before_invalid == 3


@pytest.mark.django_db
def test_sync_upload_increments_runs_counter(api_client, valid_upload):
    """A sync upload increments the runs counter with path="sync".

    The counter is labeled by execution path. A sync upload should
    add 1 to the sync series and 0 to the async series.
    """
    _seed_bank_reference()

    before_sync = _get_counter_value("bankval_runs_total", path="sync")
    before_async = _get_counter_value("bankval_runs_total", path="async_kafka")

    response = api_client.post(
        "/api/v1/validation",
        {"file": valid_upload},
        format="multipart",
    )
    assert response.status_code == 201

    after_sync = _get_counter_value("bankval_runs_total", path="sync")
    after_async = _get_counter_value("bankval_runs_total", path="async_kafka")

    assert after_sync - before_sync == 1
    assert after_async - before_async == 0
