"""Spark Structured Streaming consumer for the bank-uploads Kafka topic.

For each event (run_id, source_file), it:
  1. Reads the file from MinIO
  2. Applies the same rule engine used by the batch job
  3. Writes valid records to Delta (curated) and invalid to Delta (quarantine)

Checkpointing gives at-least-once delivery; Delta's transactional writes give
exactly-once semantics for the sink.
"""
from __future__ import annotations

import os
import sys

import clickhouse_connect

from pyspark.sql import SparkSession, functions as F
from pyspark.sql.types import StringType, StructField, StructType

sys.path.insert(0, "/opt/spark-jobs")
from session import build_session  # noqa: E402
from rules import apply_all, split_valid_invalid  # noqa: E402


KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
KAFKA_TOPIC = os.environ.get("KAFKA_TOPIC_UPLOADS", "bank-uploads")
CHECKPOINT = os.environ.get("SPARK_CHECKPOINT", "s3a://curated/_checkpoints/streaming")
ALLOWED_BANK_CODES = os.environ.get("ALLOWED_BANK_CODES", "010,020,030").split(",")


EVENT_SCHEMA = StructType(
    [
        StructField("run_id", StringType(), True),
        StructField("source_file", StringType(), True),
        StructField("bank_code", StringType(), True),
        StructField("period", StringType(), True),
    ]
)


def read_event_value(df):
    """Parse the Kafka `value` (bytes) into the EVENT_SCHEMA columns."""
    return (
        df.selectExpr("CAST(value AS STRING) as json_str")
        .select(F.from_json("json_str", EVENT_SCHEMA).alias("event"))
        .select("event.*")
    )


def _write_clickhouse_summary(run_id, bank_code, period, valid, invalid, errors_by_code):
    """Insert one summary row into the ClickHouse gold table."""
    from datetime import datetime

    client = clickhouse_connect.get_client(
        host=os.environ.get("CLICKHOUSE_HOST", "clickhouse"),
        port=int(os.environ.get("CLICKHOUSE_PORT", "8123")),
        username=os.environ.get("CLICKHOUSE_USER", "default"),
        password=os.environ.get("CLICKHOUSE_PASSWORD", "") or "",
        database=os.environ.get("CLICKHOUSE_DB", "bankval"),
    )
    now = datetime.utcnow()
    client.insert(
        "validation_summary",
        [[
            run_id,
            bank_code or "",
            period or "",
            int(valid + invalid),
            int(valid),
            int(invalid),
            0,
            errors_by_code or {},
            now,
            now,
        ]],
        column_names=[
            "run_id", "bank_code", "period",
            "total_records", "valid_count", "invalid_count", "duplicate_count",
            "errors_by_code", "created_at", "finished_at",
        ],
    )

def process_batch(batch_df, batch_id):
    """Called by foreachBatch for each micro-batch."""
    rows = batch_df.collect()
    if not rows:
        return

    for row in rows:
        run_id = row["run_id"]
        source = row["source_file"]
        if not run_id or not source:
            continue

        print(f"[streaming] processing run_id={run_id} source={source}")

        try:
            df = (
                batch_df.sparkSession.read
                .option("header", "true")
                .option("inferSchema", "false")
                .csv(source.replace("s3://", "s3a://"))
            )
            df = df.withColumn("row_number", F.monotonically_increasing_id() + 1)
            df = apply_all(df, ALLOWED_BANK_CODES)
            valid_df, invalid_df = split_valid_invalid(df)

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
            v = valid_df.count()
            i = invalid_df.count()
            print(f"[streaming] run {run_id}: valid={v} invalid={i}")

            # Aggregate error codes for the gold summary
            error_rows = (
                invalid_df
                .select(F.explode("error_codes").alias("code"))
                .groupBy("code").count()
                .collect()
            )
            errors_by_code = {r["code"]: int(r["count"]) for r in error_rows}

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
            print(f"[streaming] run {run_id} FAILED: {exc}")


def main():
    spark = build_session("bankval-streaming-validator")

    raw = (
        spark.readStream
        .format("kafka")
        .option("kafka.bootstrap.servers", KAFKA_BOOTSTRAP)
        .option("subscribe", KAFKA_TOPIC)
        .option("startingOffsets", "earliest")
        .option("failOnDataLoss", "false")
        .load()
    )

    events = read_event_value(raw)

    query = (
        events.writeStream
        .foreachBatch(process_batch)
        .outputMode("append")
        .option("checkpointLocation", CHECKPOINT)
        .trigger(processingTime="10 seconds")
        .start()
    )

    query.awaitTermination()


if __name__ == "__main__":
    main()