"""Pipeline health check DAG.

Runs every 15 minutes and asserts that the core services are reachable
from inside the Airflow scheduler container.
"""
from __future__ import annotations

import socket
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator


def check_port(host: str, port: int, name: str) -> None:
    try:
        with socket.create_connection((host, port), timeout=5):
            print(f"{name} reachable at {host}:{port}")
    except OSError as e:
        raise RuntimeError(f"{name} unreachable at {host}:{port}: {e}") from e


def check_postgres():
    check_port("postgres", 5432, "Postgres")


def check_redis():
    check_port("redis", 6379, "Redis")


def check_minio():
    check_port("minio", 9000, "MinIO")


def check_kafka():
    check_port("kafka", 9092, "Kafka")


def check_clickhouse():
    check_port("clickhouse", 8123, "ClickHouse")


default_args = {
    "owner": "bankval",
    "retries": 1,
    "retry_delay": timedelta(minutes=1),
}

with DAG(
    dag_id="pipeline_health_check",
    description="Assert all pipeline services are reachable",
    start_date=datetime(2026, 1, 1),
    schedule="*/15 * * * *",
    catchup=False,
    default_args=default_args,
    tags=["health", "bankval"],
) as dag:
    t_postgres = PythonOperator(task_id="check_postgres", python_callable=check_postgres)
    t_redis = PythonOperator(task_id="check_redis", python_callable=check_redis)
    t_minio = PythonOperator(task_id="check_minio", python_callable=check_minio)
    t_kafka = PythonOperator(task_id="check_kafka", python_callable=check_kafka)
    t_clickhouse = PythonOperator(task_id="check_clickhouse", python_callable=check_clickhouse)