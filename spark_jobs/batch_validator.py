"""Read a validation request, run rules on Spark, write Delta tables to MinIO.

The batch validator — a one-shot Spark job that validates a single
file. Called by:
    - ``make submit-batch RUN_ID=... SOURCE=...`` (manual/backfill)
    - ``airflow/dags/batch_backfill_dag.py`` (scheduled retries)
    - a future Django management command for forced re-validation

Sibling file: ``streaming_validator.py`` — the long-running consumer
that does the same work in response to Kafka events. Both share
``rules.py`` (the rule engine) and ``session.py`` (Spark config).

What this job does NOT do (handled elsewhere):
    - Write to ClickHouse — the batch job skips the summary insert
    - Update the Postgres ValidationRun row — the caller owns that
    - Publish to Kafka — the job is a consumer, not a producer

Why a batch job and not just the streaming one:
    Streaming handles the live path. But if a run failed, or a rule
    changed, or a bank re-uploaded, we need to re-process a specific
    file. The batch job takes an explicit run_id and source, giving
    operators a deterministic replay tool.
"""
from __future__ import annotations

import argparse
import os
import sys

from pyspark.sql import functions as F

# Spark's Python worker doesn't have /opt/spark-jobs on its path by
# default — only the driver does. Adding it here ensures imports
# work in both driver and executor processes.
#
# The `# noqa: E402` comments below tell the linter to ignore
# "module level import not at top of file" — required because the
# sys.path modification must come before the imports.
sys.path.insert(0, "/opt/spark-jobs")
from session import build_session  # noqa: E402
from rules import apply_all, split_valid_invalid  # noqa: E402


# The bank allow-list. Read from an env var so it can vary per
# environment. In production, this would be populated from the same
# source as Django's ``BankReference`` table — either by writing the
# codes to an env var at deploy time, or (better) by having Spark
# query Postgres directly.
#
# Why not query Postgres here:
#     Adds a JDBC driver and connection config to the Spark job. For
#     a portfolio project with a static list of test banks, an env
#     var is simpler. The trade-off is documented — a production
#     version would query the real source.
ALLOWED_BANK_CODES = os.environ.get(
    "ALLOWED_BANK_CODES", "010,020,030"
).split(",")


def read_source(spark, source_uri: str):
    """Read a CSV or JSON file from S3 into a Spark DataFrame.

    Args:
        spark: the SparkSession (built by ``build_session``).
        source_uri: an ``s3://`` URI from Django, e.g.
                    ``s3://raw/uploads/<uuid>.csv``.

    Returns:
        A Spark DataFrame. All columns are strings (no schema
        inference) — the rule engine does its own parsing.

    Format dispatch is by extension, matching the parser on the
    Django side. CSV and JSON are the two supported formats.
    """
    # Convert to s3a:// scheme. Hadoop's S3A client uses this scheme;
    # plain s3:// is the deprecated client. Django's URIs use s3://
    # because that's the storage convention — the conversion is
    # one string replace.
    path = source_uri.replace("s3://", "s3a://")

    # JSON first — the parser needs different options than CSV.
    if path.endswith(".json"):
        # multiLine=true allows a JSON array that spans multiple
        # lines. Without it, Spark expects one JSON object per line
        # (the "JSON Lines" format), which is different.
        return spark.read.option("multiLine", "true").json(path)

    # CSV path. Two options matter here:
    #
    #   header=true       — the first row is the header. Spark uses
    #                       those names as column names.
    #   inferSchema=false — don't guess types. Everything stays a
    #                       string. This is deliberate: the rule
    #                       engine handles type parsing and needs to
    #                       see the original values, not Spark's
    #                       guesses.
    #
    # Why inferSchema=false is critical:
    #     If Spark inferred types, a malformed date or number would
    #     become null during read — hiding the actual bad value from
    #     the rule engine. With strings, the engine sees "abc" in a
    #     numeric field and flags E002.
    return (
        spark.read
        .option("header", "true")
        .option("inferSchema", "false")
        .csv(path)
    )


def write_delta(df, bucket: str, subpath: str):
    """Write a DataFrame as a Delta table to MinIO.

    Args:
        df: the DataFrame to write.
        bucket: the S3 bucket name (``curated`` or ``quarantine``).
        subpath: the path within the bucket (``runs/<run_id>``).

    The full target is ``s3a://<bucket>/<subpath>``.

    Write mode: overwrite
        A re-run of the same run_id overwrites the previous result.
        This makes the job idempotent — safe to retry. The trade-off:
        we lose history (Delta's time travel could recover it if we
        kept versions, but we overwrite).

    Why not append:
        Appending would accumulate rows on every re-run. Since a
        run's result is deterministic, overwrite is correct.
    """
    target = f"s3a://{bucket}/{subpath}"
    (
        df.write
        .format("delta")
        # Overwrite the whole table at this path. Delta handles the
        # transaction log — the write is atomic from the reader's
        # perspective.
        .mode("overwrite")
        # mergeSchema=true allows the schema to evolve. If the source
        # file has extra columns in a later version, the Delta table
        # picks them up. Without this, a schema mismatch would fail
        # the write.
        .option("mergeSchema", "true")
        .save(target)
    )


def main():
    """Entry point. Parses args, runs the job, exits.

    Called by ``spark-submit`` with CLI arguments. Any unhandled
    exception exits with a non-zero status — the caller (Airflow,
    Make, or a human) sees the failure.
    """
    # Standard argparse. All three required args come from the
    # caller:
    #   --run-id     the ValidationRun UUID
    #   --source     the s3:// URI of the raw file
    #   --bank-code  optional metadata (currently unused, kept for
    #                future use when we populate the run's fields)
    #   --period     same
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--source", required=True, help="s3://raw/uploads/xxx.csv")
    parser.add_argument("--bank-code", default="")
    parser.add_argument("--period", default="")
    args = parser.parse_args()

    # Build the SparkSession. Configures Delta, S3A, and the UI.
    # The app name shows up in the Spark master's UI.
    spark = build_session("bankval-batch-validator")

    # Read the raw file. All columns are strings — the rule engine
    # will handle type conversion.
    df = read_source(spark, args.source)

    # Add a row_number column for traceability. `monotonically_increasing_id`
    # is Spark's way of assigning a unique, increasing ID per partition.
    #
    # Why +1:
    #     monotonically_increasing_id() starts at 0. We add 1 so
    #     row numbers are 1-indexed, matching the line numbers a
    #     human sees in a text editor (assuming no header mismatch).
    #
    # Caveat: this isn't the file's line number. Spark processes
    # partitions in parallel, and the IDs are assigned per partition,
    # not per source line. In practice the numbers are unique and
    # roughly ordered, but not exactly the source line.
    df = df.withColumn("row_number", F.monotonically_increasing_id() + 1)

    # Apply all row-level rules. After this, each row has:
    #   error_codes    array<string>
    #   error_messages array<string>
    df = apply_all(df, ALLOWED_BANK_CODES)

    # Split into valid (empty error_codes) and invalid (non-empty).
    # Two DataFrames, complementary partition of the input.
    valid_df, invalid_df = split_valid_invalid(df)

    # -------- Write the results --------
    #
    # Two Delta tables, one per outcome. Both are written under the
    # run's UUID, so a run's data is isolated in its own folder.
    #
    # Why per-run folders:
    #     Runs are independent. A re-run replaces its own folder
    #     without touching others. Deleting a run is one directory
    #     delete. Cross-run queries would join folders — rare in our
    #     workload.
    write_delta(valid_df, "curated", f"runs/{args.run_id}")
    write_delta(invalid_df, "quarantine", f"runs/{args.run_id}")

    # -------- Report --------
    #
    # Trigger the counts. Each `.count()` runs a Spark job — this is
    # the first action after the lazy transformations, so it's where
    # the actual work happens.
    #
    # The counts are also written to the log/stdout so the caller
    # can capture them. Airflow parses this line; a human running
    # the job sees it directly.
    v = valid_df.count()
    i = invalid_df.count()
    print(f"RUN {args.run_id}: valid={v} invalid={i}")

    # Clean shutdown. Releases the SparkContext, closes connections.
    # Without this, the JVM might linger until the process is killed.
    spark.stop()


if __name__ == "__main__":
    main()