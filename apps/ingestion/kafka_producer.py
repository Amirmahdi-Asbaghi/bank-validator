"""Kafka producer for large-file validation events.

Publishes a small JSON message to the ``bank-uploads`` topic when a
large file is uploaded. The message contains the ``run_id`` and the
MinIO URI of the raw file. Spark's streaming consumer reads the topic
and does the actual validation.

Why ``confluent-kafka`` and not ``kafka-python``:
    ``confluent-kafka`` is a thin wrapper around ``librdkafka`` (C).
    It's 5–10× faster than the pure-Python ``kafka-python`` and has
    stable Python 3.12 wheels. ``kafka-python`` had a broken import
    path on 3.12 due to a vendored ``six`` module.

Producer lifecycle:
    A single ``Producer`` is created lazily and reused for the life of
    the process. Kafka producers are thread-safe and maintain a
    connection pool internally — creating one per request would be
    wasteful and defeat the batching.

Delivery semantics:
    ``produce()`` is asynchronous — it enqueues the message and returns
    immediately. ``flush()`` blocks until the message is delivered or
    the timeout expires. The delivery report callback is invoked on
    success or failure.
"""
from __future__ import annotations

import json
import logging

from confluent_kafka import Producer
from django.conf import settings

from apps.common.metrics import KAFKA_PUBLISH_ERRORS_TOTAL

logger = logging.getLogger(__name__)

# Module-level singleton. Kafka producers are expensive to create
# (they connect, spawn background threads, allocate buffers) and
# thread-safe. One per process is the standard pattern.
#
# ``None`` means "not yet created". ``_get_producer`` initializes it
# on first use, so importing this module doesn't trigger a connection.
_producer: Producer | None = None


def _get_producer() -> Producer:
    """Return the shared Producer, creating it on first call.

    The double-checked pattern (check None, then create) is not strictly
    thread-safe here — two threads could both see None and create two
    producers. In practice, the first call happens during a single
    request before any concurrency. If we needed strict safety, we'd
    use a lock.
    """
    global _producer

    if _producer is None:
        _producer = Producer(
            {
                # Where the broker lives. For local dev, "kafka:9092"
                # (the compose service name). For production, a
                # bootstrap list of 3 brokers for redundancy.
                "bootstrap.servers": settings.KAFKA_BOOTSTRAP_SERVERS,

                # acks=all means the broker waits for all in-sync
                # replicas to acknowledge before returning success.
                # This is the strongest durability guarantee — we don't
                # lose messages even if the leader fails immediately
                # after receiving. Slower than acks=1, but for a run
                # event that must not be lost, worth the latency.
                "acks": "all",

                # Retry up to 3 times on transient failures. Combined
                # with acks=all, this gives us a solid guarantee
                # without idempotence (which would need a producer ID).
                "retries": 3,

                # Batch messages for up to 10 ms before sending.
                # Larger batching = higher throughput; longer latency.
                # 10 ms is negligible for our workload and helps when
                # many uploads arrive in a burst.
                "linger.ms": 10,
            }
        )
    return _producer


def _delivery_report(err, msg):
    """Callback invoked by the producer for each message.

    Runs on the producer's background thread — not the request thread.
    Keep it fast; anything slow here blocks delivery reports.

    On success, log at INFO. On failure, log at ERROR so alerts can
    fire. We do NOT raise here — by the time the callback runs, the
    caller may have already moved on (produce() is async). Failures
    are visible in logs, but the caller has to check flush() for
    exceptions to handle them synchronously.
    """
    if err is not None:
        logger.error("Delivery failed for record %s: %s", msg.key(), err)
    else:
        logger.info(
            "Record produced to %s [%s] at offset %s",
            msg.topic(),
            msg.partition(),
            msg.offset(),
        )


def publish_validation_event(
    run_id: str,
    source_file: str,
    bank_code: str = "",
    period: str = "",
) -> None:
    """Publish a validation request to the Kafka topic.

    Args:
        run_id:       UUID of the ValidationRun row.
        source_file:  MinIO URI of the raw file (s3://raw/uploads/...).
        bank_code:    optional, currently empty. A future version could
                      parse the first row to infer it.
        period:       optional, same as bank_code.

    Raises:
        Exception: any failure from the producer. The caller (services.py)
                   marks the run as failed and re-raises so the API
                   returns a 500.
    """
    # Build the message payload. Kept small (a few hundred bytes) —
    # Kafka is a buffer, not a file store. The file itself is on MinIO.
    payload = {
        "run_id": str(run_id),         # force string; UUID → str
        "source_file": source_file,
        "bank_code": bank_code,
        "period": period,
    }

    try:
        producer = _get_producer()

        # produce() is asynchronous — it enqueues the message and
        # returns immediately. The callback will fire later on the
        # producer's thread.
        #
        # value is bytes — Kafka is binary. We serialize JSON to UTF-8
        # bytes. ``key`` is omitted, so Kafka assigns round-robin
        # partition (fine for our workload; we don't need ordering).
        producer.produce(
            topic=settings.KAFKA_TOPIC_UPLOADS,
            value=json.dumps(payload).encode("utf-8"),
            callback=_delivery_report,
        )

        # flush() blocks until all enqueued messages are delivered or
        # the timeout expires. Without this, the request could return
        # before the message reaches Kafka — and if the process
        # crashed, we'd lose the event. 10 seconds is generous for a
        # local broker; in production, a shorter timeout (1-2s) with
        # a clear error would be typical.
        producer.flush(timeout=10)

        # If flush() returned without raising, the message is confirmed
        # delivered (thanks to acks=all and the delivery callback).
        logger.info("Published validation event for run %s", run_id)

    except Exception as e:  # noqa: BLE001
        # Any failure — connection error, timeout, serialization issue.
        # Increment the Prometheus counter so alerts can fire, log with
        # traceback for debugging, then re-raise. The caller marks the
        # run failed and returns 500 to the client.
        KAFKA_PUBLISH_ERRORS_TOTAL.inc()
        logger.exception(
            "Failed to publish validation event for run %s: %s", run_id, e
        )
        raise