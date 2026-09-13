"""Read a validation request, run rules on Spark, write Delta tables to MinIO."""
from __future__ import annotations

import argparse
import os
import sys

from pyspark.sql import functions as F

sys.path.insert(0, "/opt/spark-jobs")
from session import build_session  # noqa: E402
from rules import apply_all, split_valid_invalid  # noqa: E402


ALLOWED_BANK_CODES = os.environ.get(
    "ALLOWED_BANK_CODES", "010,020,030"
).split(",")


def read_source(spark, source_uri: str):
    """source_uri like s3://raw/uploads/xxx.csv (or .json)."""
    path = source_uri.replace("s3://", "s3a://")
    if path.endswith(".json"):
        return spark.read.option("multiLine", "true").json(path)
    return (
        spark.read
        .option("header", "true")
        .option("inferSchema", "false")
        .csv(path)
    )


def write_delta(df, bucket: str, subpath: str):
    target = f"s3a://{bucket}/{subpath}"
    (
        df.write
        .format("delta")
        .mode("overwrite")
        .option("mergeSchema", "true")
        .save(target)
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--source", required=True, help="s3://raw/uploads/xxx.csv")
    parser.add_argument("--bank-code", default="")
    parser.add_argument("--period", default="")
    args = parser.parse_args()

    spark = build_session("bankval-batch-validator")

    df = read_source(spark, args.source)

    # Add row_number for traceability
    df = df.withColumn("row_number", F.monotonically_increasing_id() + 1)

    df = apply_all(df, ALLOWED_BANK_CODES)

    valid_df, invalid_df = split_valid_invalid(df)

    write_delta(valid_df, "curated", f"runs/{args.run_id}")
    write_delta(invalid_df, "quarantine", f"runs/{args.run_id}")

    v = valid_df.count()
    i = invalid_df.count()
    print(f"RUN {args.run_id}: valid={v} invalid={i}")

    spark.stop()


if __name__ == "__main__":
    main()