"""S3-compatible object storage via boto3 (MinIO in dev).

Handles uploads and downloads of raw validation files.
Kept behind a small interface so the persistence mechanism can change
without touching the callers.
"""
from __future__ import annotations

import io
import uuid
from pathlib import Path

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
from django.conf import settings


def _client():
    return boto3.client(
        "s3",
        endpoint_url=settings.MINIO_ENDPOINT,
        aws_access_key_id=settings.MINIO_ROOT_USER,
        aws_secret_access_key=settings.MINIO_ROOT_PASSWORD,
        region_name="us-east-1",
        config=Config(signature_version="s3v4"),
    )


def _raw_bucket() -> str:
    return settings.MINIO_BUCKET_RAW


def ensure_bucket(bucket: str) -> None:
    client = _client()
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError:
        client.create_bucket(Bucket=bucket)


def save_uploaded_file(filename: str, content: bytes) -> str:
    """
    Store the uploaded bytes in the raw bucket and return the S3 object URI
    (used as `source_file` on ValidationRun).
    """
    ensure_bucket(_raw_bucket())

    ext = Path(filename).suffix.lower() or ".bin"
    key = f"uploads/{uuid.uuid4().hex}{ext}"

    _client().put_object(
        Bucket=_raw_bucket(),
        Key=key,
        Body=io.BytesIO(content),
        ContentType="application/octet-stream",
    )
    return f"s3://{_raw_bucket()}/{key}"


def read_uploaded_file(location: str) -> bytes:
    """
    Accepts either an `s3://bucket/key` URI or a plain key.
    Returns the object body bytes.
    """
    if location.startswith("s3://"):
        _, rest = location.split("s3://", 1)
        bucket, key = rest.split("/", 1)
    else:
        bucket, key = _raw_bucket(), location

    obj = _client().get_object(Bucket=bucket, Key=key)
    return obj["Body"].read()