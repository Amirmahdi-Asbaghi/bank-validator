"""API tests for the validation endpoints."""
from __future__ import annotations

import pytest
from django.test import override_settings
from rest_framework.test import APIClient


@pytest.fixture
def client():
    return APIClient()


@pytest.mark.django_db
def test_upload_requires_file(client):
    response = client.post("/api/v1/validation", {}, format="multipart")
    assert response.status_code == 400


@pytest.mark.django_db
def test_upload_valid_csv_returns_completed(client, valid_upload):
    # Seed the reference data this test needs
    from apps.validation.models import BankReference
    for code in ("010", "020", "030"):
        BankReference.objects.update_or_create(
            bank_code=code, defaults={"name": f"Bank {code}", "is_active": True}
        )

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


@pytest.mark.django_db
def test_upload_invalid_csv_flags_errors(client, invalid_upload):
    from apps.validation.models import BankReference
    for code in ("010", "020", "030"):
        BankReference.objects.update_or_create(
            bank_code=code, defaults={"name": f"Bank {code}", "is_active": True}
        )

    response = client.post(
        "/api/v1/validation",
        {"file": invalid_upload},
        format="multipart",
    )
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["status"] == "completed"
    assert body["total_records"] == 4
    # E004 (bad bank), E005 (bad period), E006 (dup), E007 (bad balance)
    codes = body["errors_by_code"]
    assert codes.get("E004", 0) >= 1
    assert codes.get("E005", 0) >= 1
    assert codes.get("E006", 0) >= 1
    assert codes.get("E007", 0) >= 1