"""Spark Structured Streaming consumer for the bank-uploads Kafka topic.

The live path. Runs continuously, consuming events from Kafka. For each
event (a ``run_id`` + ``source_file``), it:

    1. Reads the file from MinIO
    2. Applies the same rule engine used by the batch job
    3. Writes valid records to Delta (curated) and invalid to Delta
       (quarantine)
    4. Writes a summary row to ClickHouse

Checkpointing gives at-least-once delivery from Kafka. Delta's
transactional writes give exactly-once semantics for the sink — a
restart resumes from where it left off, no duplicates.

Why foreachBatch and not foreach:
    Each event does more than one thing — read a file, write two Delta
    tables, insert into ClickHouse. That's DataFrame-level work, not
    row-level. ``foreach`` is per-row; ``foreachBatch`` gives us a
    DataFrame per micro-batch, which is what we need.

Sibling file: ``batch_validator.py`` — the one-shot version for
backfills and manual re-runs. Both share ``rules.py`` and ``session.py``.

Known gap:
    The Postgres ``ValidationRun`` row stays at ``status=queued``
    forever for async runs. Spark writes to Delta and ClickHouse, not
    to Postgres. The API's ``/summary`` endpoint falls back to
    ClickHouse to compensate. A production improvement would be to
    have Spark update Postgres on completion.
"""
from __future__ import annotations

import os
import sys

import clickhouse_connect

from pyspark.sql import SparkSession, functions as F
from pyspark.sql.types import StringType, StructField, StructType

# Make /opt/spark-jobs importable. The bind mount from docker-compose
# puts session.py and rules.py there. Both the driver and executors
# need this path.
sys.path.insert(0, "/opt/spark-jobs")
from session import build_session  # noqa: E402
from rules import apply_all, split_valid_invalid  # noqa: E402


# -------- Configuration --------
#
# All from env vars with sensible defaults. Same pattern as the batch
# validator — the values come from .env in dev, from the deployment
# environment in production.

KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
KAFKA_TOPIC = os.environ.get("KAFKA_TOPIC_UPLOADS", "bank-uploads")

# The checkpoint location. Spark stores Kafka offsets, processed
# batch IDs, and any state here. On restart, it resumes from the last
# checkpoint — that's what gives exactly-once semantics.
#
# Stored on S3 (via s3a://) rather than local disk, because the
# container's local disk is ephemeral. A new container would lose the
# checkpoint and re-process everything.
CHECKPOINT = os.environ.get("SPARK_CHECKPOINT", "s3a://curated/_checkpoints/streaming")

ALLOWED_BANK_CODES = os.environ.get("ALLOWED_BANK_CODES", "010,020,030").split(",")


# -------- The Kafka message schema --------
#
# The producer (Django) writes JSON like:
#   {"run_id": "...", "source_file": "s3://...", "bank_code": "", "period": ""}
#
# This schema declares those fields. Spark's from_json uses it to
# parse the string into typed columns.
#
# Why all StringType:
#     The producer writes everything as strings. run_id is a string,
#     source_file is a string, and bank_code/period are empty strings
#     for now. Any future field would also be a string until we
#     introduce non-string types.
#
# Why nullable=True for every field:
#     from_json with a strict schema drops rows that don't match.
#     Nullable fields mean a missing key becomes null — the row is
#     preserved and the downstream code can decide what to do.
EVENT_SCHEMA = StructType(
    [
        StructField("run_id", StringType(), True),
        StructField("source_file", StringType(), True),
        StructField("bank_code", StringType(), True),
        StructField("period", StringType(), True),
    ]
)


def read_event_value(df):
    """Parse Kafka's ``value`` bytes into typed columns.

    Kafka's envelope has many columns (key, value, topic, partition,
    offset, timestamp, ...). We only care about ``value`` — the JSON
    payload Django wrote.

    The two-step transformation:

        1. CAST(value AS STRING) — Kafka stores bytes; we decode to text
        2. from_json(..., EVENT_SCHEMA) — parse the JSON into columns

    The intermediate ``.alias("event")`` step names the parsed struct,
    then ``.select("event.*")`` explodes it into top-level columns.

    Why this shape:
        Spark's from_json returns a struct column. To get individual
        columns (run_id, source_file, ...), we need to select the
        struct's fields. ``event.*`` is the idiomatic way.
    """
    return (
        df.selectExpr("CAST(value AS STRING) as json_str")
        .select(F.from_json("json_str", EVENT_SCHEMA).alias("event"))
        .select("event.*")
    )


def _write_clickhouse_summary(run_id, bank_code, period, valid, invalid, errors_by_code):
    """Insert one summary row into the ClickHouse gold table.

    Called once per processed event, after the Delta writes succeed.
    Failures here are caught by the caller and logged — a ClickHouse
    outage shouldn't block Delta writes (which are the source of truth).

    Args:
        run_id: the run's UUID as a string.
        bank_code, period: metadata (currently empty strings).
        valid, invalid: record counts.
        errors_by_code: dict like {"E004": 9034, "E007": 9800}.

    Returns:
        None. The row is inserted; failures raise.
    """
    from datetime import datetime

    # Create the client per call. Same reasoning as the Django-side
    # client: the volume is low, and a fresh client avoids global state.
    client = clickhouse_connect.get_client(
        host=os.environ.get("CLICKHOUSE_HOST", "clickhouse"),
        port=int(os.environ.get("CLICKHOUSE_PORT", "8123")),
        username=os.environ.get("CLICKHOUSE_USER", "default"),
        password=os.environ.get("CLICKHOUSE_PASSWORD", "") or "",
        database=os.environ.get("CLICKHOUSE_DB", "bankval"),
    )

    # `datetime.utcnow()` — ClickHouse expects naive UTC datetimes.
    # The column type is `DateTime`, not `DateTime64` or timezone-aware.
    # Python's utcnow returns naive UTC, which is what ClickHouse wants.
    now = datetime.utcnow()

    client.insert(
        "validation_summary",
        [[
            run_id,                 # UUID as a string
            bank_code or "",        # empty strings, not None
            period or "",
            int(valid + invalid),   # total
            int(valid),
            int(invalid),
            0,                      # duplicate_count — always 0 here (see note below)
            errors_by_code or {},   # Map(String, UInt32)
            now,                    # created_at — non-nullable
            now,                    # finished_at — nullable but we always provide
        ]],
        column_names=[
            "run_id", "bank_code", "period",
            "total_records", "valid_count", "invalid_count", "duplicate_count",
            "errors_by_code", "created_at", "finished_at",
        ],
    )


def process_batch(batch_df, batch_id):
    """Process one micro-batch of events.

    Spark calls this for every micro-batch. ``batch_df`` contains the
    events in that batch; ``batch_id`` is a monotonically increasing
    integer we don't currently use (would be useful for logging).

    Why collect() the batch first:
        Micro-batches are small (a handful of uploads at most). We
        iterate over them to process each file separately — each file
        has its own run_id and needs its own Delta tables.

        For a high-volume stream, we'd process the batch as a whole
        (e.g. write all valid records to a shared Delta table partitioned
        by run_id). At our scale, per-event processing is clearer.

    Error handling:
        Each event is wrapped in its own try/except. A failure on one
        event doesn't stop the batch — later events still process.
        The event's error is printed and the loop continues.
    """
    # ``collect()`` materializes the batch. For a handful of events,
    # this is fine. If the batch were large (thousands of events),
    # we'd process them as a DataFrame instead of iterating.
    rows = batch_df.collect()

    if not rows:
        # Empty batch — happens on every trigger even when no new
        # events arrive. Nothing to do.
        return

    for row in rows:
        run_id = row["run_id"]
        source = row["source_file"]

        # A malformed event (missing run_id or source) is skipped.
        # The producer always sends both, but defensive.
        if not run_id or not source:
            continue

        print(f"[streaming] processing run_id={run_id} source={source}")

        try:
            # -------- Read the raw file from MinIO --------
            #
            # Same options as the batch validator: header, no schema
            # inference. The s3:// → s3a:// conversion matches the
            # batch job.
            #
            # Why read from batch_df.sparkSession:
            #     We need the session to build a reader. Any DataFrame
            #     carries a reference to its session — that's the
            #     cleanest way to get it here.
            df = (
                batch_df.sparkSession.read
                .option("header", "true")
                .option("inferSchema", "false")
                .csv(source.replace("s3://", "s3a://"))
            )

            # Row numbers for traceability. Same as the batch job.
            df = df.withColumn("row_number", F.monotonically_increasing_id() + 1)

            # Apply all row-level rules.
            df = apply_all(df, ALLOWED_BANK_CODES)

            # Split into two DataFrames.
            valid_df, invalid_df = split_valid_invalid(df)

            # -------- Write to Delta --------
            #
            # Same overwrite semantics as the batch job. A re-process
            # of the same run_id replaces the previous result — idempotent.
            (
                valid_df.write
                .format("delta")
                .mode("overwrite")
                .option("mergeSchema", "true")
                .save(f"s3a://curated/runs/{run_id}")
            )
            (
                invalid_df.write
                .format("delta")
                .mode("overwrite")
                .option("mergeSchema", "true")
                .save(f"s3a://quarantine/runs/{run_id}")
            )

            # Count for the summary. Two actions — each triggers a
            # Spark job. Could be combined but they read different
            # DataFrames.
            v = valid_df.count()
            i = invalid_df.count()
            print(f"[streaming] run {run_id}: valid={v} invalid={i}")

            # -------- Aggregate error codes --------
            #
            # The ClickHouse summary needs counts per error code, not
            # just the total. We explode the error_codes array (one
            # row per (record, code) pair), group by code, and count.
            #
            # Why explode + groupBy:
            #     A single record can have multiple error codes. To
            #     count each code's occurrences, we need to iterate
            #     the arrays. Spark's explode does that in one
            #     DataFrame operation.
            #
            # Example: 100 rows, one with codes ["E004","E007"],
            # the rest with ["E004"]. After explode: 101 rows
            # (100 E004 + 1 E007). Grouped: {"E004": 100, "E007": 1}.
            error_rows = (
                invalid_df
                .select(F.explode("error_codes").alias("code"))
                .groupBy("code").count()
                .collect()
            )
            # Convert to a plain dict. `.count()` returns a Spark Row
            # with a "count" field; `int(...)` coerces to a Python int
            # (Spark returns the count as a long).
            errors_by_code = {r["code"]: int(r["count"]) for r in error_rows}

            # -------- Write to ClickHouse --------
            #
            # Wrapped in its own try/except. If ClickHouse is down,
            # the Delta writes have already succeeded — that's the
            # source of truth. The summary is a convenience for
            # analytics; losing one doesn't corrupt the pipeline.
            #
            # The event is still considered "processed" even if the
            # ClickHouse write fails. The next batch won't retry it.
            # A production system would add a retry queue or a
            # compensating job — documented as a known gap.
            try:
                _write_clickhouse_summary(
                    run_id=run_id,
                    bank_code=row["bank_code"] or "",
                    period=row["period"] or "",
                    valid=v,
                    invalid=i,
                    errors_by_code=errors_by_code,
                )
                print(f"[streaming] ClickHouse summary written for {run_id}")
            except Exception as ch_exc:  # noqa: BLE001
                print(f"[streaming] ClickHouse write failed for {run_id}: {ch_exc}")
        except Exception as exc:  # noqa: BLE001
            # Catch-all for anything that fails during processing:
            # read errors, write errors, rule engine bugs. Print and
            # continue — the next event in the batch still processes.
            #
            # Why not re-raise: re-raising would fail the entire
            # micro-batch, and Spark would retry it. But Spark can't
            # retry a single event within a batch — it would re-process
            # all of them. Some would succeed again (idempotent), some
            # would fail again. Better to log and continue.
            print(f"[streaming] run {run_id} FAILED: {exc}")


def main():
    """Build the session, wire the stream, run forever."""
    spark = build_session("bankval-streaming-validator")

    # -------- Read from Kafka --------
    #
    # Spark's Kafka source connector. Requires the
    # spark-sql-kafka-0-10 package on the classpath — provided via
    # --packages in the spark-submit command.
    #
    # Options:
    #   kafka.bootstrap.servers  — where Kafka is
    #   subscribe                — the topic to consume
    #   startingOffsets          — "earliest" reads from the beginning
    #                              of the topic on first run; "latest"
    #                              starts from now. "earliest" is
    #                              useful for dev: any events published
    #                              before Spark started are still
    #                              processed.
    #   failOnDataLoss           — if Kafka has deleted old offsets
    #                              (retention policy), don't crash.
    #                              Process what's available.
    raw = (
        spark.readStream
        .format("kafka")
        .option("kafka.bootstrap.servers", KAFKA_BOOTSTRAP)
        .option("subscribe", KAFKA_TOPIC)
        .option("startingOffsets", "earliest")
        .option("failOnDataLoss", "false")
        .load()
    )

    # Parse the Kafka envelope's value column into run_id, source_file, etc.
    events = read_event_value(raw)

    # -------- Write stream --------
    #
    # The output sink. ``foreachBatch`` calls ``process_batch`` for
    # every micro-batch.
    #
    # outputMode="append"
    #     Only new rows are passed to foreachBatch. Since we don't
    #     aggregate across batches, append is correct. Update mode
    #     would re-send rows whose values changed (never here).
    #
    # checkpointLocation
    #     Where Spark stores offsets and state. On restart, it
    #     resumes from the last checkpoint — no reprocessing, no
    #     missed events.
    #
    # trigger(processingTime="10 seconds")
    #     How often to check for new events. A 10-second micro-batch
    #     interval balances latency (10s worst-case) against overhead
    #     (starting a Spark job per interval). For our workload,
    #     10s is fine.
    query = (
        events.writeStream
        .foreachBatch(process_batch)
        .outputMode("append")
        .option("checkpointLocation", CHECKPOINT)
        .trigger(processingTime="10 seconds")
        .start()
    )

    # Block forever. The streaming query runs in background threads;
    # this keeps the driver process alive. Ctrl+C (SIGINT) stops it.
    query.awaitTermination()


if __name__ == "__main__":
    main()