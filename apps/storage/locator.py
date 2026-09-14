"""S3-compatible object storage via boto3 (MinIO in dev).

Handles uploads and downloads of raw validation files. This is the
only module in the codebase that imports boto3 — every other module
talks to MinIO through these functions.

Why an interface and not direct boto3 calls:
    Three reasons.
      1. The credentials and endpoint are read from settings once,
         here, not scattered through the code.
      2. Swapping MinIO for real S3 or Ceph means changing one module.
      3. The URI convention (``s3://bucket/key``) is defined here so
         every caller produces and consumes the same format.

Compatibility:
    Works with any S3-compatible endpoint — MinIO locally, AWS S3 in
    production, Ceph, Backblaze B2. The only difference is the
    ``endpoint_url`` in settings.
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
    """Create a boto3 S3 client.

    Creates a new client on every call. boto3 clients are not free —
    they cache session state, credentials, and connection pools — but
    they're also not as expensive as a Kafka producer. For our volume
    (a handful of uploads per minute), creating a client per operation
    is fine.

    If this became a hot path, we'd cache the client at module level
    like the Kafka producer. For now, correctness over micro-optimization.

    ``signature_version="s3v4"`` is required by MinIO. Without it,
    boto3 uses the older V2 signing algorithm, which MinIO rejects.
    """
    return boto3.client(
        "s3",
        endpoint_url=settings.MINIO_ENDPOINT,
        aws_access_key_id=settings.MINIO_ROOT_USER,
        aws_secret_access_key=settings.MINIO_ROOT_PASSWORD,
        # MinIO doesn't enforce a real region, but boto3 requires one.
        # us-east-1 is the AWS default and works everywhere.
        region_name="us-east-1",
        config=Config(signature_version="s3v4"),
    )


def _raw_bucket() -> str:
    """Return the name of the raw bucket.

    Wrapped in a function so the setting is read at call time, not
    import time. This makes tests easier — they can override the
    setting and the change takes effect.
    """
    return settings.MINIO_BUCKET_RAW


def ensure_bucket(bucket: str) -> None:
    """Create the bucket if it doesn't exist.

    Idempotent: safe to call on every write. Uses ``head_bucket`` to
    check existence (returns 200 or 404), then ``create_bucket`` on
    the 404 path.

    Why not ``create_bucket`` unconditionally?
        S3's ``create_bucket`` fails if the bucket already exists.
        Checking first avoids that error and is the standard pattern.
        There's a race condition (two callers could both see 404 and
        both try to create), but the second create would just fail —
        and we ignore that case here since the bucket already exists
        by the time we return.

    Why call this on every write?
        Because it's cheap (one HEAD request) and it makes the write
        path robust against a fresh MinIO that hasn't been initialized.
        The production init container creates the buckets on startup,
        but this belt-and-suspenders check handles the case where
        someone wipes MinIO without restarting.
    """
    client = _client()
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError:
        # The bucket doesn't exist (or we don't have permission to
        # see it). Try to create it. If this also fails, the
        # exception propagates — the caller sees a clear error.
        client.create_bucket(Bucket=bucket)


def save_uploaded_file(filename: str, content: bytes) -> str:
    """Store uploaded bytes in the raw bucket and return the S3 URI.

    The URI (``s3://bucket/key``) becomes the ``source_file`` on the
    ValidationRun — the canonical reference to the raw input.

    Args:
        filename: the original filename, used only for its extension.
        content:  the raw bytes of the file.

    Returns:
        A URI like ``s3://raw/uploads/<uuid>.csv``.

    Why a UUID key and not the original filename?
        Two reasons.
          1. Collisions. Two banks could upload ``data.csv`` on the
             same day. UUIDs guarantee uniqueness.
          2. Security. The bank's filename might contain path
             separators, special characters, or PII. A UUID avoids
             all of that.
        The extension is preserved because the parser needs it to
        decide CSV vs JSON.
    """
    # Ensure the bucket exists. On a fresh MinIO (before init), this
    # creates it. On a healthy MinIO, it's a no-op HEAD request.
    ensure_bucket(_raw_bucket())

    # Extract the extension. ``.lower()`` normalizes .CSV and .Json.
    # Fall back to ``.bin`` for files with no extension — shouldn't
    # happen because the serializer already validated, but defensive.
    ext = Path(filename).suffix.lower() or ".bin"

    # Construct the object key. The ``uploads/`` prefix is a convention
    # that keeps uploaded files separate from other data in the same
    # bucket (future: exports, temp files, etc.).
    key = f"uploads/{uuid.uuid4().hex}{ext}"

    # Upload the bytes. boto3's put_object streams the body from the
    # file-like object; io.BytesIO wraps the in-memory bytes as a
    # stream. No disk I/O.
    #
    # ContentType is set to a generic binary type. We don't try to
    # guess (text/csv, application/json) because the S3 metadata
    # doesn't affect how our pipeline reads the file — the parser
    # uses the extension, not the content type.
    _client().put_object(
        Bucket=_raw_bucket(),
        Key=key,
        Body=io.BytesIO(content),
        ContentType="application/octet-stream",
    )

    # Return the canonical URI. The parser will call
    # ``Path(uri).suffix`` on this and see ``.csv`` or ``.json``.
    return f"s3://{_raw_bucket()}/{key}"


def read_uploaded_file(location: str) -> bytes:
    """Read the bytes at an S3 URI or plain key.

    Accepts two forms:
        - ``s3://bucket/key`` (the canonical URI from save_uploaded_file)
        - ``key`` (a bare key, assumed to be in the raw bucket)

    Args:
        location: the URI or key to read.

    Returns:
        The object's bytes.

    Raises:
        ClientError: if the object doesn't exist or the credentials
                     are wrong.
    """
    # Parse the URI. The two-branch logic handles both the URI form
    # and the bare-key form.
    if location.startswith("s3://"):
        # Strip the scheme, then split on the first slash to separate
        # bucket from key. The key may contain slashes (like
        # ``uploads/abc.csv``), so we split once, not on every slash.
        _, rest = location.split("s3://", 1)
        bucket, key = rest.split("/", 1)
    else:
        # No scheme — treat the location as a key in the raw bucket.
        # This is a convenience for callers who have a bare key from
        # a different source (e.g. a Spark job that emits keys without
        # the scheme).
        bucket, key = _raw_bucket(), location

    # Fetch the object. boto3 returns a response dict with a ``Body``
    # field — an IO stream. Reading it fully returns the bytes.
    #
    # Note: for very large objects, this loads the whole thing into
    # memory. Our uploads are capped at 200 MB, so the peak is
    # bounded. If we needed streaming, we'd use ``download_fileobj``
    # or read in chunks.
    obj = _client().get_object(Bucket=bucket, Key=key)
    return obj["Body"].read()