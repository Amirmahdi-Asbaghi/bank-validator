"""API tests for the validation upload endpoint.

These tests exercise the endpoint through DRF's test client — the
full HTTP stack, URL routing, serializer validation, service layer,
rule engine, and Postgres persistence.

What's covered:
    - Rejection when no file is provided
    - A successful sync upload (valid CSV)
    - A partial failure (invalid CSV → error codes)

What's NOT covered:
    - The async path (Kafka → Spark). Would need a running broker
      and consumer.
    - Pagination of the /invalid and /valid endpoints. Worth adding
      when those endpoints gain filters or page-size changes.

Why a real database:
    The API writes to Postgres. Using a mocked database would test
    the view logic but skip the model interactions — the FK
    relationships, the JSONField serialization, the cascade deletes.
    ``@pytest.mark.django_db`` gives each test a real database with
    automatic rollback after.
"""
from __future__ import annotations

import pytest
from rest_framework.test import APIClient


@pytest.fixture
def client():
    """An unauthenticated DRF test client.

    Not the same as pytest-django's ``client`` fixture — that's
    Django's, which doesn't speak DRF serializers. This one routes
    through DRF's dispatcher and returns DRF responses.
    """
    return APIClient()


def _seed_bank_reference():
    """Seed the BankReference table with the codes the tests expect.

    Every API test that uploads a file needs the allow-list populated.
    Without it, every row fails E004 — a false negative that would
    mask the real assertion.

    Why seed inside each test and not in a fixture:
        A fixture at ``conftest.py`` scope would apply to unit tests
        too, which don't touch the database. Keeping the seeding
        local to the API tests keeps the fixture setup scoped.

    Why ``update_or_create`` and not ``create``:
        If two tests somehow share state (they don't, due to
        transaction rollback, but defensively), ``update_or_create``
        won't raise on duplicate keys.
    """
    from apps.validation.models import BankReference
    for code in ("010", "020", "030"):
        BankReference.objects.update_or_create(
            bank_code=code,
            defaults={"name": f"Bank {code}", "is_active": True},
        )


@pytest.mark.django_db
def test_upload_requires_file(client):
    """A POST with no file returns 400 with a validation error.

    This is the first boundary: the serializer catches the missing
    file before any pipeline code runs. The response body should
    have a ``file`` key with the error message.
    """
    response = client.post("/api/v1/validation", {}, format="multipart")
    assert response.status_code == 400
    # DRF returns errors keyed by field name
    assert "file" in response.json()


@pytest.mark.django_db
def test_upload_valid_csv_returns_completed(client, valid_upload):
    """A valid file uploads successfully with 201.

    The endpoint runs the sync path (file is small) and returns the
    full summary. All three rows should be valid, and the response
    should have the expected shape.
    """
    _seed_bank_reference()

    response = client.post(
        "/api/v1/validation",
        {"file": valid_upload},
        format="multipart",
    )
    assert response.status_code == 201, response.content

    body = response.json()
    assert body["status"] == "completed"
    assert body["total_records"] == 3
    assert body["valid_count"] == 3
    assert body["invalid_count"] == 0
    assert body["duplicate_count"] == 0
    # No errors to report
    assert body["errors_by_code"] == {}
    # The source file was saved to MinIO
    assert body["source_file"].startswith("s3://raw/uploads/")


@pytest.mark.django_db
def test_upload_invalid_csv_flags_errors(client, invalid_upload):
    """A file with failures returns 201 with error codes populated.

    Note: this is still a 201, not a 400. The upload itself
    succeeded — the endpoint processed the file and produced a
    result. The result contains errors, but the request was valid.

    The response should have invalid_count > 0 and the errors_by_code
    map should list the specific codes triggered.
    """
    _seed_bank_reference()

    response = client.post(
        "/api/v1/validation",
        {"file": invalid_upload},
        format="multipart",
    )
    assert response.status_code == 201, response.content

    body = response.json()
    assert body["status"] == "completed"
    assert body["total_records"] == 4
    # 1 valid row (row 3), 3 invalid
    assert body["valid_count"] == 1
    assert body["invalid_count"] == 3
    # 1 duplicate (row 4 is a repeat of row 3)
    assert body["duplicate_count"] == 1

    # Each expected error code appears at least once
    codes = body["errors_by_code"]
    assert codes.get("E004", 0) >= 1   # bad bank code
    assert codes.get("E005", 0) >= 1   # bad period
    assert codes.get("E006", 0) >= 1   # duplicate
    assert codes.get("E007", 0) >= 1   # balance mismatch


@pytest.mark.django_db
def test_upload_rejects_unsupported_extension(client):
    """A file with an unsupported extension returns 400.

    The UploadSerializer checks the extension. A ``.txt`` file
    should be rejected before any pipeline work.
    """
    from django.core.files.uploadedfile import SimpleUploadedFile

    txt_upload = SimpleUploadedFile(
        "report.txt",
        b"some content",
        content_type="text/plain",
    )
    response = client.post(
        "/api/v1/validation",
        {"file": txt_upload},
        format="multipart",
    )
    assert response.status_code == 400
    # The error should mention the accepted extensions
    assert "csv" in str(response.json()).lower() or "json" in str(response.json()).lower()


@pytest.mark.django_db
def test_get_run_detail_after_upload(client, valid_upload):
    """After a successful upload, GET /validation/{id} returns the run.

    Exercises the read path: the client gets the run's id from the
    upload response, then fetches the full detail. The detail
    response should match the upload response.
    """
    _seed_bank_reference()

    # Upload
    upload_response = client.post(
        "/api/v1/validation",
        {"file": valid_upload},
        format="multipart",
    )
    assert upload_response.status_code == 201
    run_id = upload_response.json()["id"]

    # Fetch detail
    detail_response = client.get(f"/api/v1/validation/{run_id}")
    assert detail_response.status_code == 200
    detail = detail_response.json()

    assert detail["id"] == run_id
    assert detail["status"] == "completed"
    assert detail["valid_count"] == 3


@pytest.mark.django_db
def test_get_run_summary(client, valid_upload):
    """The /summary endpoint returns the compact shape.

    Same data as the detail endpoint, but with the shorter field
    names (total, valid, invalid) that dashboards use.
    """
    _seed_bank_reference()

    upload_response = client.post(
        "/api/v1/validation",
        {"file": valid_upload},
        format="multipart",
    )
    run_id = upload_response.json()["id"]

    summary_response = client.get(f"/api/v1/validation/{run_id}/summary")
    assert summary_response.status_code == 200

    summary = summary_response.json()
    assert summary["total"] == 3
    assert summary["valid"] == 3
    assert summary["invalid"] == 0
    # The source of the data (Postgres for sync runs)
    assert summary["source"] == "postgres"


@pytest.mark.django_db
def test_get_run_detail_not_found(client):
    """A non-existent run_id returns 404.

    Uses a well-formed UUID that doesn't exist. The endpoint
    should return 404, not 500 or 400.
    """
    response = client.get("/api/v1/validation/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404