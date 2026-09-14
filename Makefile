# Bank Validation Platform — developer entry points
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

.PHONY: help up up-dev down reset migrate seed test lint shell logs \
        build demo ps clean

help:
	@echo "Targets:"
	@echo "  up            Start the full stack"
	@echo "  up-dev        Start only web + postgres + redis (fast iteration)"
	@echo "  down          Stop all containers (keep volumes)"
	@echo "  reset         Stop and remove volumes (fresh state)"
	@echo "  build         Rebuild custom images"
	@echo "  migrate       Run Django migrations"
	@echo "  seed          Load bank_codes.csv into BankReference"
	@echo "  test          Run pytest"
	@echo "  lint          Run ruff + black --check"
	@echo "  shell         Open a Django shell in the web container"
	@echo "  logs SERVICE=web     Tail logs of one service"
	@echo "  ps            Show running services"
	@echo "  demo          Bring up + seed + upload a sample + print summary"
	@echo "  submit-batch RUN_ID=<uuid> SOURCE=s3://raw/uploads/<file>"

up:
	$(COMPOSE) up -d
	@echo "Services:"
	@$(COMPOSE) ps

up-dev:
	$(COMPOSE) up -d postgres redis minio minio-init
	$(COMPOSE) up -d --force-recreate web
	@echo "Dev stack up (web + postgres + redis + minio)."
	@echo "Django: http://localhost:8000"
	@echo "MinIO console: http://localhost:9001 (minioadmin / minioadmin)"

build:
	$(COMPOSE) build web spark-master

down:
	$(COMPOSE) down

reset:
	$(COMPOSE) down -v
	@echo "All volumes removed."

migrate:
	$(COMPOSE) exec web python manage.py migrate

seed:
	$(COMPOSE) exec web python manage.py seed_bank_codes

test:
	$(COMPOSE) exec web pytest

lint:
	$(COMPOSE) exec web ruff check apps spark_jobs
	$(COMPOSE) exec web black --check apps spark_jobs

shell:
	$(COMPOSE) exec web python manage.py shell

logs:
	$(COMPOSE) logs -f $(SERVICE)

ps:
	$(COMPOSE) ps

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name "*.egg-info" -exec rm -rf {} +

# ---------- Batch job ----------
submit-batch:
	@if [ -z "$(RUN_ID)" ] || [ -z "$(SOURCE)" ]; then \
	  echo "Usage: make submit-batch RUN_ID=<uuid> SOURCE=s3://raw/uploads/<file>"; \
	  exit 1; \
	fi
	$(COMPOSE) exec spark-master /opt/spark/bin/spark-submit \
	  --master spark://spark-master:7077 \
	  --driver-memory 2g --executor-memory 1g \
	  --jars /opt/spark/jars/delta-spark_2.12-3.2.0.jar,/opt/spark/jars/delta-storage-3.2.0.jar,/opt/spark/jars/hadoop-aws-3.3.4.jar,/opt/spark/jars/aws-java-sdk-bundle-1.12.262.jar \
	  /opt/spark-jobs/batch_validator.py \
	  --run-id $(RUN_ID) --source $(SOURCE)


# Generate the 51 MB test file for the async path
big-file:
	$(COMPOSE) exec web python /app/scripts/generate_big_file.py --target-mb 51 --out /app/data/samples/big_51mb.csv

# Read a Delta table from MinIO with Spark
# Usage: make show-delta RUN_ID=<uuid> [BUCKET=curated|quarantine] [ERROR_CODE=E007]
show-delta:
	@if [ -z "$(RUN_ID)" ]; then \
	  echo "Usage: make show-delta RUN_ID=<uuid> [BUCKET=curated|quarantine] [ERROR_CODE=E007]"; \
	  exit 1; \
	fi
	$(COMPOSE) exec spark-master /opt/spark/bin/spark-submit \
	  --master spark://spark-master:7077 \
	  --driver-memory 2g \
	  --jars /opt/spark/jars/delta-spark_2.12-3.2.0.jar,/opt/spark/jars/delta-storage-3.2.0.jar,/opt/spark/jars/hadoop-aws-3.3.4.jar,/opt/spark/jars/aws-java-sdk-bundle-1.12.262.jar \
	  /opt/spark-jobs/show_delta.py \
	  $(or $(BUCKET),curated) $(RUN_ID) $(ERROR_CODE)
# ---------- Demo ----------
demo: up
	@echo ""
	@echo ">> Waiting for web to be ready..."
	@timeout=30; \
	  until curl.exe -sf http://localhost:8000/api/schema/ >/dev/null 2>&1; do \
	    sleep 1; timeout=$$((timeout-1)); \
	    if [ $$timeout -le 0 ]; then echo "web not ready after 30s"; exit 1; fi; \
	  done
	@echo ">> Applying migrations..."
	@$(COMPOSE) exec web python manage.py migrate --noinput
	@echo ""
	@echo ">> Seeding bank reference data..."
	@$(COMPOSE) exec web python manage.py seed_bank_codes
	@echo ""
	@echo ">> Uploading valid_small.csv..."
	@curl.exe -s -X POST -F "file=@data/samples/valid_small.csv" \
	  http://localhost:8000/api/v1/validation | python -m json.tool
	@echo ""
	@echo ">> Uploading invalid_small.csv..."
	@curl.exe -s -X POST -F "file=@data/samples/invalid_small.csv" \
	  http://localhost:8000/api/v1/validation | python -m json.tool
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