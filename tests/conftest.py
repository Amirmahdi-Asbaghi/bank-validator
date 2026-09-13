"""Shared pytest fixtures."""
from __future__ import annotations

import os
from io import BytesIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile


@pytest.fixture
def allowed_bank_codes():
    return {"010", "020", "030"}


@pytest.fixture
def valid_record():
    return {
        "bank_code": "010",
        "period": "1405/03",
        "account_code": "A1",
        "debit": "100.00",
        "credit": "40.00",
        "balance": "60.00",
        "currency": "IRR",
        "record_id": "R1",
    }


@pytest.fixture
def valid_csv_bytes():
    return (
        b"bank_code,period,account_code,debit,credit,balance,currency,record_id\n"
        b"010,1405/03,A1,100.00,40.00,60.00,IRR,R1\n"
        b"010,1405/03,A2,200.00,50.00,150.00,IRR,R2\n"
        b"020,1405/03,A3,10.00,0.00,10.00,IRR,R3\n"
    )


@pytest.fixture
def invalid_csv_bytes():
    return (
        b"bank_code,period,account_code,debit,credit,balance,currency,record_id\n"
        b"999,1405/03,A1,100.00,40.00,60.00,IRR,R1\n"
        b"010,1405-03,A2,200.00,50.00,999.00,IRR,R2\n"
        b"010,1405/03,A3,100.00,40.00,60.00,IRR,R3\n"
        b"010,1405/03,A3,100.00,40.00,60.00,IRR,R3\n"
    )


@pytest.fixture
def valid_upload(valid_csv_bytes):
    return SimpleUploadedFile(
        "valid_small.csv",
        valid_csv_bytes,
        content_type="text/csv",
    )


@pytest.fixture
def invalid_upload(invalid_csv_bytes):
    return SimpleUploadedFile(
        "invalid_small.csv",
        invalid_csv_bytes,
        content_type="text/csv",
    )