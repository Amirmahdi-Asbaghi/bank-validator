# Bank Validation Platform - developer entry points
#
# Usage:
#   make up          Start the full stack
#   make demo        Run the end-to-end demo (up + seed + sample upload)
#   make down        Stop everything
#   make reset       Stop and delete all volumes (fresh start)
#   make test        Run pytest in the web container
#   make migrate     Apply Django migrations
#   make seed        Load reference bank codes
#   make logs        Tail logs of a service (SERVICE=web)
#   make shell       Open a Django shell
#   make submit-batch RUN_ID=... SOURCE=s3://...

COMPOSE := docker compose -f docker/compose.yml

.PHONY: help up up-dev down reset build migrate makemigrations seed \
        test test-unit test-api lint format check shell psql clickhouse \
        kafka-topics logs ps clean restart-web rebuild-web rebuild-spark \
        demo big-file show-delta submit-batch streaming


help:
	@echo "Targets:"
	@echo ""
	@echo "  Stack:"
	@echo "    up               Start the full stack"
	@echo "    up-dev           Only web + postgres + redis + minio (fast)"
	@echo "    down             Stop all containers (keep volumes)"
	@echo "    reset            Stop and remove volumes (fresh state)"
	@echo "    build            Rebuild all custom images"
	@echo "    restart-web      Restart just the web container"
	@echo "    rebuild-web      Rebuild the web image"
	@echo "    rebuild-spark    Rebuild the Spark image"
	@echo "    ps               Show running services"
	@echo ""
	@echo "  Django:"
	@echo "    migrate          Apply Django migrations"
	@echo "    makemigrations   Create new migrations"
	@echo "    seed             Load bank_codes.csv into BankReference"
	@echo "    check            Run Django's system check"
	@echo "    shell            Open a Django shell"
	@echo ""
	@echo "  Quality:"
	@echo "    test             Run all tests"
	@echo "    test-unit        Run only unit tests (fast)"
	@echo "    test-api         Run only API tests"
	@echo "    lint             Check formatting and lint"
	@echo "    format           Auto-format code"
	@echo "    clean            Remove Python caches"
	@echo ""
	@echo "  Debug:"
	@echo "    logs SERVICE=web       Tail logs of one service"
	@echo "    psql                   Open a Postgres shell"
	@echo "    clickhouse             Open a ClickHouse shell"
	@echo "    kafka-topics           List Kafka topics"
	@echo ""
	@echo "  Jobs:"
	@echo "    demo                             Full end-to-end demo"
	@echo "    big-file                         Generate the 51 MB test file"
	@echo "    show-delta RUN_ID=...            Read a Delta table with Spark"
	@echo "    submit-batch RUN_ID=... SOURCE=...  Re-run the batch validator"
	@echo "    streaming                        Run the Spark streaming consumer (async path)"

# ---------- Stack management ----------

up: 
	$(COMPOSE) up -d
	$(MAKE) clickhouse-init
	@echo "Services:"
	@$(COMPOSE) ps

up-dev:
	$(COMPOSE) up -d postgres redis minio minio-init
	$(COMPOSE) up -d --force-recreate web
	@echo "Dev stack up (web + postgres + redis + minio)."
	@echo "Django: http://localhost:8000"
	@echo "MinIO console: http://localhost:9001 (minioadmin / minioadmin)"

down:
	$(COMPOSE) down

reset:
	$(COMPOSE) down -v
	@echo "All volumes removed."

build:
	$(COMPOSE) build web spark-master

restart-web:
	$(COMPOSE) up -d --force-recreate web

rebuild-web:
	$(COMPOSE) build web
	$(COMPOSE) up -d --force-recreate web

rebuild-spark:
	$(COMPOSE) build spark-master
	$(COMPOSE) up -d --force-recreate spark-master spark-worker

ps:
	$(COMPOSE) ps

# ---------- Django ----------

migrate:
	$(COMPOSE) exec web python manage.py migrate

makemigrations:
	$(COMPOSE) exec web python manage.py makemigrations

seed:
	$(COMPOSE) exec web python manage.py seed_bank_codes

check:
	$(COMPOSE) exec web python manage.py check

shell:
	$(COMPOSE) exec web python manage.py shell

# ---------- Quality ----------

test:
	$(COMPOSE) exec web pytest

test-unit:
	$(COMPOSE) exec web pytest tests/unit -v

test-api:
	$(COMPOSE) exec web pytest tests/api -v

lint:
	$(COMPOSE) exec web ruff check apps spark_jobs
	$(COMPOSE) exec web black --check apps spark_jobs

format:
	$(COMPOSE) exec web ruff check --fix apps spark_jobs
	$(COMPOSE) exec web black apps spark_jobs

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name "*.egg-info" -exec rm -rf {} +

# ---------- Debug shells ----------

logs:
	$(COMPOSE) logs -f $(SERVICE)

psql:
	$(COMPOSE) exec postgres psql -U bankval -d bankval

clickhouse:
	$(COMPOSE) exec clickhouse clickhouse-client

clickhouse-init:
	$(COMPOSE) exec clickhouse clickhouse-client --query "CREATE DATABASE IF NOT EXISTS bankval"
	$(COMPOSE) exec clickhouse clickhouse-client --query "CREATE TABLE IF NOT EXISTS bankval.validation_summary (run_id UUID, bank_code String, period String, total_records UInt32, valid_count UInt32, invalid_count UInt32, duplicate_count UInt32, errors_by_code Map(String, UInt32), created_at DateTime DEFAULT now(), finished_at Nullable(DateTime)) ENGINE = MergeTree() ORDER BY (bank_code, period, created_at) SETTINGS index_granularity = 8192"

kafka-topics:
	$(COMPOSE) exec kafka /opt/kafka/bin/kafka-topics.sh \
	  --bootstrap-server localhost:9092 --list

# ---------- Spark jobs ----------

submit-batch:
	$(COMPOSE) exec spark-master /opt/spark/bin/spark-submit \
	  --master spark://spark-master:7077 \
	  --driver-memory 2g --executor-memory 1g \
	  --jars /opt/spark/jars/delta-spark_2.12-3.2.0.jar,/opt/spark/jars/delta-storage-3.2.0.jar,/opt/spark/jars/hadoop-aws-3.3.4.jar,/opt/spark/jars/aws-java-sdk-bundle-1.12.262.jar \
	  /opt/spark-jobs/batch_validator.py \
	  --run-id $(RUN_ID) --source $(SOURCE)

big-file:
	$(COMPOSE) exec web python /app/scripts/generate_big_file.py \
	  --target-mb 51 --out /app/data/samples/big_51mb.csv

show-delta:
	$(COMPOSE) exec spark-master /opt/spark/bin/spark-submit \
	  --master spark://spark-master:7077 \
	  --driver-memory 2g \
	  --jars /opt/spark/jars/delta-spark_2.12-3.2.0.jar,/opt/spark/jars/delta-storage-3.2.0.jar,/opt/spark/jars/hadoop-aws-3.3.4.jar,/opt/spark/jars/aws-java-sdk-bundle-1.12.262.jar \
	  /opt/spark-jobs/show_delta.py \
	  $(or $(BUCKET),curated) $(RUN_ID) $(ERROR_CODE)

# Run the Spark streaming consumer (long-running; keep this terminal open).
# The async path (Kafka → Spark → Delta → ClickHouse) only processes
# events while this job is running.
streaming:
	@echo "Starting the streaming consumer..."
	@echo "Leave this terminal open. Press Ctrl+C to stop."
	@echo ""
	$(COMPOSE) exec spark-master /opt/spark/bin/spark-submit \
	  --master spark://spark-master:7077 \
	  --driver-memory 2g --executor-memory 1g \
	  --jars /opt/spark/jars/delta-spark_2.12-3.2.0.jar,/opt/spark/jars/delta-storage-3.2.0.jar,/opt/spark/jars/hadoop-aws-3.3.4.jar,/opt/spark/jars/aws-java-sdk-bundle-1.12.262.jar \
	  --packages org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.1 \
	  /opt/spark-jobs/streaming_validator.py


# ---------- Demo ----------

demo: up
	@echo ""
	@echo ">> Applying migrations..."
	@$(COMPOSE) exec -T web python manage.py migrate --noinput
	@echo ""
	@echo ">> Seeding bank reference data..."
	@$(COMPOSE) exec -T web python manage.py seed_bank_codes
	@echo ""
	@echo ">> Uploading valid_small.csv..."
	@curl.exe -s -X POST -F "file=@data/samples/valid_small.csv" http://localhost:8000/api/v1/validation
	@echo ""
	@echo ""
	@echo ">> Uploading invalid_small.csv..."
	@curl.exe -s -X POST -F "file=@data/samples/invalid_small.csv" http://localhost:8000/api/v1/validation
	@echo ""
	@echo ""
	@echo "==============================================="
	@echo " Demo complete."
	@echo ""
	@echo " Django API:      http://localhost:8000"
	@echo " Swagger:         http://localhost:8000/api/schema/swagger/"
	@echo " Django Admin:    http://localhost:8000/admin"
	@echo " MinIO console:   http://localhost:9001   (minioadmin / minioadmin)"
	@echo " Spark master UI: http://localhost:8080"
	@echo " Kafka:           localhost:9092"
	@echo " ClickHouse HTTP: http://localhost:8123"
	@echo " Airflow UI:      http://localhost:8081   (airflow / airflow)"
	@echo " Prometheus:      http://localhost:9090"
	@echo " Grafana:         http://localhost:3000   (admin / admin)"
	@echo "==============================================="