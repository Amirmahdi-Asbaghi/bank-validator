"""Build a SparkSession configured for MinIO (S3) and Delta Lake."""
from __future__ import annotations

import os

from pyspark.sql import SparkSession


def build_session(app_name: str = "bankval") -> SparkSession:
    endpoint = os.environ.get("MINIO_ENDPOINT", "http://minio:9000")
    access = os.environ.get("MINIO_ROOT_USER", "minioadmin")
    secret = os.environ.get("MINIO_ROOT_PASSWORD", "minioadmin")

    spark = (
        SparkSession.builder
        .appName(app_name)
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config(
            "spark.sql.catalog.spark_catalog",
            "org.apache.spark.sql.delta.catalog.DeltaCatalog",
        )
        .config("spark.hadoop.fs.s3a.endpoint", endpoint)
        .config("spark.hadoop.fs.s3a.access.key", access)
        .config("spark.hadoop.fs.s3a.secret.key", secret)
        .config("spark.hadoop.fs.s3a.path.style.access", "true")
        .config("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem")
        .config("spark.hadoop.fs.s3a.connection.ssl.enabled", "false")
        .config("spark.sql.parquet.compression.codec", "snappy")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("WARN")
    return spark