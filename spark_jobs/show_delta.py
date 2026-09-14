"""Read a Delta table from MinIO and print rows.

Usage:
    spark-submit show_delta.py <bucket> <run_id> [error_code]

Examples:
    spark-submit show_delta.py curated ee93d797-5658-4b34-b18d-254a189402d4
    spark-submit show_delta.py quarantine ee93d797-5658-4b34-b18d-254a189402d4 E007

Why this utility exists:
    The streaming and batch validators write Delta tables to MinIO.
    Inspecting them normally means writing a small Spark script every
    time — configure the session, know the path format, remember the
    Delta catalog setup. This file packages that into one command.

    It's a dev tool, not part of the production pipeline. The pipeline
    itself never calls it.

Design:
    - Not a module. No importable functions. It's a script meant to
      be run with spark-submit.
    - Argument parsing is positional and simple. No argparse — for
      three arguments, the minimal form is clearer.
    - Output is human-oriented: a header, a count, columns, a table.
      Not JSON, not machine-parseable. The API is for machines; this
      is for humans.

Relationship to the pipeline:
    - The Django API (reports/views.py) serves records from Postgres.
    - This utility serves records from Delta.
    - Both read the same underlying data, just at different layers.

    The API is for small runs; this is for the large async ones.
"""
import sys
from pyspark.sql import SparkSession, functions as F

# -------- Parse arguments --------
#
# Positional:
#     $1  bucket      (curated | quarantine)   — required
#     $2  run_id      (UUID)                   — required
#     $3  error_code  (E001..E011)             — optional

bucket = sys.argv[1] if len(sys.argv) > 1 else "curated"
run_id = sys.argv[2] if len(sys.argv) > 2 else None
error_code = sys.argv[3] if len(sys.argv) > 3 else None

# If no run_id, print usage and exit non-zero. The caller sees a
# clear error rather than a stack trace from a missing argument.
if not run_id:
    print("Usage: show_delta.py <curated|quarantine> <run_id> [error_code]")
    sys.exit(1)

# -------- Build the Spark session --------
#
# Unlike the batch and streaming validators, this script doesn't call
# ``build_session()`` from session.py. Why not:
#
#   1. It's a standalone tool. Depending on another module makes it
#      harder to copy or run in isolation.
#   2. The session config is slightly different — no UI reverse proxy,
#      no driver host config, no SPARK_DRIVER_HOST env var.
#   3. It hardcodes the MinIO credentials because it's a dev tool —
#      production utilities would read env vars.
#
# The trade-off: config duplication. If the S3A settings changed, this
# file would need the same update. Acceptable for a dev tool.
spark = (
    SparkSession.builder
    .appName("show-delta")

    # Delta Lake — same two configs as session.py.
    # Without these, `spark.read.format("delta")` returns garbage.
    .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
    .config(
        "spark.sql.catalog.spark_catalog",
        "org.apache.spark.sql.delta.catalog.DeltaCatalog",
    )

    # S3A — hardcoded MinIO credentials. This is a dev tool, so the
    # convenience of hardcoding outweighs the flexibility of env vars.
    # A production version would read from env or a secret manager.
    .config("spark.hadoop.fs.s3a.endpoint", "http://minio:9000")
    .config("spark.hadoop.fs.s3a.access.key", "minioadmin")
    .config("spark.hadoop.fs.s3a.secret.key", "minioadmin")
    .config("spark.hadoop.fs.s3a.path.style.access", "true")
    .config("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem")

    .getOrCreate()
)

# Reduce Spark's log noise. Same reasoning as the other jobs — we want
# to see our print statements, not Spark's INFO chatter.
spark.sparkContext.setLogLevel("WARN")

# -------- Build the path and read --------
#
# The path convention is shared with the batch and streaming validators:
#     s3a://<bucket>/runs/<run_id>/
#
# The s3a:// scheme is Hadoop's modern S3 client. Delta reads through
# Hadoop's filesystem API.
path = f"s3a://{bucket}/runs/{run_id}/"
print(f"\n=== Reading {path} ===\n")

# Wrap the read in try/except. Common failures:
#   - The run_id doesn't exist (PathNotFound)
#   - The bucket is empty (no _delta_log)
#   - MinIO is unreachable (ConnectionError)
#
# All produce a clear message instead of a stack trace, so a human
# running the command sees what went wrong.
try:
    df = spark.read.format("delta").load(path)
except Exception as e:
    print(f"Failed to read: {e}")
    sys.exit(1)

# -------- Print metadata --------
#
# Three pieces of context before the data:
#   - total rows (from a count)
#   - column names
#
# Why before showing rows:
#     The count triggers a full scan. On a 900k-row table, that takes
#     a few seconds. It's useful to see the total up front rather than
#     after the sample.
print(f"Total rows: {df.count()}\n")
print(f"Columns: {df.columns}\n")

# -------- Optional error code filter --------
#
# If the caller passed an error code (e.g. E007), filter the DataFrame
# to rows whose error_codes array contains it.
#
# `array_contains` is Spark's built-in for checking membership in an
# array column. It's the correct way to filter on our error_codes
# array<string> field.
if error_code:
    df = df.filter(F.array_contains(F.col("error_codes"), error_code))
    print(f"Rows with {error_code}: {df.count()}\n")

# -------- Show the data --------
#
# `truncate=False` prevents Spark from truncating long string values
# with "...". We want to see the raw_row JSON in full.
#
# The default is 20 rows. That's enough to eyeball the data without
# flooding the terminal.
df.show(20, truncate=False)

# Clean shutdown. Same reasoning as the batch validator — release the
# SparkContext so the JVM exits cleanly.
spark.stop()