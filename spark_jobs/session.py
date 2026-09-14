"""Build a SparkSession configured for MinIO (S3) and Delta Lake.

This is the single place where Spark is configured. Every Spark job
in the project calls ``build_session()`` instead of constructing a
SparkSession directly — so the Delta, S3A, and UI settings are
consistent everywhere.

Configuration grouped by concern:

    Delta Lake      the table format we write to MinIO
    S3A             Hadoop's S3 connector, pointed at MinIO
    Parquet         compression codec for the files Delta writes
    Spark UI        reverse proxy so the UI is reachable from a browser
    Log level       WARN to reduce noise

Environment-driven values:

    MINIO_ENDPOINT          http://minio:9000 in dev
    MINIO_ROOT_USER         access key
    MINIO_ROOT_PASSWORD     secret key
    SPARK_DRIVER_HOST       how the driver advertises itself

All other settings are baked in. They don't vary by environment
because they're part of how the job runs, not where it runs.
"""
from __future__ import annotations

import os

from pyspark.sql import SparkSession


def build_session(app_name: str = "bankval") -> SparkSession:
    """Create a SparkSession with Delta and S3A configured.

    Args:
        app_name: shown in the Spark UI and logs. Defaults to
                  "bankval"; callers pass a more specific name
                  (e.g. "bankval-streaming-validator").

    Returns:
        A configured SparkSession. The session is cached internally
        by Spark — calling this function twice in the same process
        returns the same session.

    When to call this:
        Once per Spark job, at the top of ``main()``. The session
        persists for the process lifetime and is not meant to be
        re-created per operation.
    """
    # Read MinIO connection info from the environment. These are set
    # by Docker Compose (or the shell) and mirror the values Django
    # uses. The same env vars feed both services so they can't drift.
    #
    # Defaults are provided for local script execution outside the
    # container — they match the standard Compose setup.
    endpoint = os.environ.get("MINIO_ENDPOINT", "http://minio:9000")
    access = os.environ.get("MINIO_ROOT_USER", "minioadmin")
    secret = os.environ.get("MINIO_ROOT_PASSWORD", "minioadmin")

    spark = (
        SparkSession.builder
        .appName(app_name)

        # -------- Delta Lake --------
        #
        # Two settings that install Delta's extension points into Spark:
        #
        #   spark.sql.extensions
        #       The Delta SparkSession extension. Adds Delta-specific
        #       SQL commands (e.g. MERGE INTO, DESCRIBE HISTORY) and
        #       hooks into Spark's planner.
        #
        #   spark.sql.catalog.spark_catalog
        #       Replaces Spark's default catalog with Delta's. This is
        #       what lets `spark.read.format("delta")` and
        #       `df.write.format("delta")` work — the catalog knows
        #       how to handle the _delta_log directory alongside the
        #       parquet files.
        #
        # Both are required. Omitting either breaks Delta reads/writes.
        .config(
            "spark.sql.extensions",
            "io.delta.sql.DeltaSparkSessionExtension",
        )
        .config(
            "spark.sql.catalog.spark_catalog",
            "org.apache.spark.sql.delta.catalog.DeltaCatalog",
        )

        # -------- S3A (Hadoop's S3 connector) --------
        #
        # Spark doesn't talk S3 natively. It uses Hadoop's S3A
        # filesystem, which reads from any S3-compatible endpoint.
        # MinIO speaks the S3 protocol, so S3A works against it.
        #
        # ``s3a://`` vs ``s3://``:
        #   s3://  is the old Hadoop S3 client (uses JNI, less
        #          compatible, often broken)
        #   s3a:// is the modern, pure-Java client used by Hadoop 3
        #
        # Our code converts s3:// URIs to s3a:// at the boundary
        # (in batch_validator and streaming_validator).

        # Where the S3 endpoint lives. For MinIO, this includes the
        # scheme and port (http://minio:9000). For AWS S3, it would
        # be https://s3.amazonaws.com or similar.
        .config("spark.hadoop.fs.s3a.endpoint", endpoint)

        # Credentials. In production, these would come from a secret
        # manager or an IAM role. For MinIO, they're the root user's
        # access keys.
        .config("spark.hadoop.fs.s3a.access.key", access)
        .config("spark.hadoop.fs.s3a.secret.key", secret)

        # Path-style vs virtual-host style S3 URLs.
        #
        # Virtual-host: http://bucket.s3.amazonaws.com/key
        # Path-style:   http://s3.amazonaws.com/bucket/key
        #
        # MinIO requires path-style. AWS S3 supports both (path-style
        # is deprecated but still works). Setting this to true is
        # required for MinIO and harmless for S3.
        .config("spark.hadoop.fs.s3a.path.style.access", "true")

        # Which filesystem implementation to use for s3a:// URIs.
        # S3AFileSystem is the modern Hadoop implementation. This
        # config is implicit in most setups but explicit here for
        # clarity — the job won't start without the class on the
        # classpath (it's loaded from hadoop-aws jar).
        .config(
            "spark.hadoop.fs.s3a.impl",
            "org.apache.hadoop.fs.s3a.S3AFileSystem",
        )

        # Whether to use HTTPS for S3 traffic. MinIO in dev runs over
        # HTTP, so this is false. In production with real S3, it
        # would be true. Setting it explicitly avoids Hadoop's
        # default (which is true) failing against an HTTP endpoint.
        .config("spark.hadoop.fs.s3a.connection.ssl.enabled", "false")

        # -------- Parquet compression --------
        #
        # Delta stores data as parquet. This sets the compression
        # codec for those parquet files.
        #
        # snappy is a good default: fast compression/decompression,
        # reasonable ratio. Alternatives:
        #   gzip   — smaller files, slower
        #   zstd   — best ratio, slower than snappy
        #   lz4    — fastest, larger files
        #
        # For a validation pipeline that reads more than it writes,
        # snappy balances speed and size.
        .config("spark.sql.parquet.compression.codec", "snappy")

        # -------- Spark UI reverse proxy --------
        #
        # The Spark UI is served on port 4040 inside the driver
        # container. From the host browser, that port isn't reachable
        # directly — Docker's NAT doesn't expose it.
        #
        # The reverse proxy makes the master's UI (port 8080, which
        # IS exposed) proxy the driver's UI under a URL like
        # /proxy/<app-id>/. Setting these two configs enables that.
        #
        # Note: this is a best-effort fix for Docker Desktop on
        # Windows. Some links in the UI still resolve to internal
        # container hostnames. The fix is partial; the driver-level
        # UI is reachable through the master's proxied URL.
        .config("spark.ui.reverseProxy", "true")
        .config("spark.ui.reverseProxyUrl", "http://localhost:8080")

        # -------- Driver hostname --------
        #
        # The hostname the driver advertises to executors and the UI.
        #
        # In Docker Compose, the container's hostname is a random ID
        # (e.g. "42010dd11376"). That name isn't resolvable outside
        # the Docker network. Setting it to "spark-master" (the
        # container's fixed hostname from compose) makes the UI links
        # consistent.
        #
        # SPARK_DRIVER_HOST lets a future environment override this —
        # e.g. a Kubernetes deployment would use the pod name.
        .config(
            "spark.driver.host",
            os.environ.get("SPARK_DRIVER_HOST", "spark-master"),
        )

        # Bind the driver to 0.0.0.0 so it accepts connections from
        # any interface inside the container. Without this, the
        # driver might bind only to localhost, and executors on other
        # containers couldn't reach it.
        .config("spark.driver.bindAddress", "0.0.0.0")

        # -------- Build --------
        #
        # getOrCreate returns the existing session if one is already
        # active in this process, or creates a new one. This makes
        # build_session safe to call multiple times.
        .getOrCreate()
    )

    # Quiet the flood of INFO logs. Spark logs every stage, task, and
    # shuffle decision at INFO. For a batch job that runs millions of
    # tasks, that's gigabytes of logs.
    #
    # WARN shows only warnings and errors — the interesting ones.
    # The Spark UI still has the full detail if we need to debug.
    spark.sparkContext.setLogLevel("WARN")

    return spark