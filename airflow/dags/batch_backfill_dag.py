"""Manual DAG to backfill a validation run via Spark.

Trigger with:
    airflow dags trigger batch_backfill \
      --conf '{"run_id": "<uuid>", "source": "s3://raw/uploads/<file>.csv"}'
"""
from __future__ import annotations

from datetime import datetime

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator


def log_conf(**context):
    conf = context["dag_run"].conf or {}
    run_id = conf.get("run_id")
    source = conf.get("source")
    print(f"Backfill requested: run_id={run_id} source={source}")
    if not run_id or not source:
        raise ValueError("Both run_id and source are required in --conf")


with DAG(
    dag_id="batch_backfill",
    description="Backfill a specific validation run via spark-submit",
    start_date=datetime(2026, 1, 1),
    schedule=None,
    catchup=False,
    tags=["backfill", "spark", "bankval"],
) as dag:
    t_log = PythonOperator(task_id="log_request", python_callable=log_conf)

    t_spark = BashOperator(
        task_id="run_spark_batch",
        bash_command=(
            "echo 'Would run: spark-submit batch_validator.py "
            '--run-id {{ dag_run.conf["run_id"] }} '
            '--source {{ dag_run.conf["source"] }}\''
        ),
    )

    t_log >> t_spark