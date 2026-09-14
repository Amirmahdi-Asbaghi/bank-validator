"""Kafka producer for large-file validation events (confluent-kafka)."""
from __future__ import annotations

import json
import logging

from confluent_kafka import Producer
from django.conf import settings

from apps.common.metrics import KAFKA_PUBLISH_ERRORS_TOTAL

logger = logging.getLogger(__name__)

_producer: Producer | None = None


def _get_producer() -> Producer:
    global _producer
    if _producer is None:
        _producer = Producer(
            {
                "bootstrap.servers": settings.KAFKA_BOOTSTRAP_SERVERS,
                "acks": "all",
                "retries": 3,
                "linger.ms": 10,
            }
        )
    return _producer


def _delivery_report(err, msg):
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
    """Publish a validation request to the Kafka topic."""
    payload = {
        "run_id": str(run_id),
        "source_file": source_file,
        "bank_code": bank_code,
        "period": period,
    }
    try:
        producer = _get_producer()
        producer.produce(
            topic=settings.KAFKA_TOPIC_UPLOADS,
            value=json.dumps(payload).encode("utf-8"),
            callback=_delivery_report,
        )
        producer.flush(timeout=10)
        logger.info("Published validation event for run %s", run_id)
    except Exception as e:  # noqa: BLE001
        KAFKA_PUBLISH_ERRORS_TOTAL.inc()
        logger.exception("Failed to publish validation event for run %s: %s", run_id, e)
        raise