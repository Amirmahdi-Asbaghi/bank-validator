# Bank Validation Platform

A distributed validation pipeline for bank reporting files.

Upload a CSV or JSON file of accounting records. Every record is checked against
a rule set (structure, types, bank allow-list, period format, duplicates,
`balance = debit − credit`). Valid records land in a curated Delta table.
Invalid records go to quarantine with their error codes. A summary is written
to ClickHouse for fast analytics.

Runs end-to-end on Docker Compose. Every container maps 1:1 to Kubernetes.

---

## Highlights

- **Two engines, one semantics** — small files run in pandas; large files run
  on Spark. Same error codes, same results.
- **Never lose data** — invalid records persist with their reasons and the
  original row.
- **Money is `Decimal`** — exact equality for `balance = debit − credit`,
  never float.
- **Event-driven ingestion** — Django returns in milliseconds; Spark processes
  asynchronously via Kafka.
- **ACID + idempotent** — Delta Lake sinks, per-`run_id` writes, replayable.
- **Observable** — business-level counters in Prometheus, dashboards in Grafana,
  DAGs in Airflow.

---

## Architecture at a glance

```
Upload  →  Django + DRF  →  MinIO (raw)
                            │
                 ┌──────────┴─────────┐
            small file            large file
                 │                    │
                 ▼                    ▼
            pandas rules         Kafka topic
                 │                    │
                 │                    ▼
                 │           Spark Structured Streaming
                 │           (same rules, distributed)
                 │                    │
                 │           ┌────────┴────────┐
                 │           ▼                 ▼
                 │      curated Delta    quarantine Delta
                 │      (valid)          (+ error_codes, raw_row)
                 │           │                 │
                 └───────────┴────────┬────────┘
                                      ▼
                              ClickHouse gold
                              (per-run summary)

Airflow       —  scheduled health checks + manual backfill
Prometheus    —  business metrics
Grafana       —  dashboards
```

Full detail: [`docs/architecture.md`](docs/architecture.md)

---

## Stack

| Layer | Technology |
|---|---|
| API | Django 5 + DRF + drf-spectacular |
| Async (small) | Celery + Redis |
| Sync compute | pandas + `decimal.Decimal` |
| Distributed compute | Apache Spark 3.5 (PySpark) |
| Event bus | Apache Kafka 4 (KRaft mode) |
| Object storage | MinIO (S3 API) |
| Table format | Delta Lake 3.2 |
| Operational DB | PostgreSQL 16 |
| Analytical DB | ClickHouse 24.8 |
| Orchestration | Apache Airflow 2.9 |
| Observability | Prometheus + Grafana |
| Packaging | Docker Compose |

---

## Quickstart

Requirements: **Docker Desktop** (or Docker Engine + Compose), **Make**, **Git**.

```bash
git clone <your-repo-url> bank-validator
cd bank-validator

# First-time setup: create .env, then bring the stack up
cp .env.example .env
cp .env docker/.env

# Bring up + migrate + seed + run a sample upload
make demo
```

After `make demo` completes, open:

| Service | URL | Credentials |
|---|---|---|
| Django API | http://localhost:8000 | — |
| Swagger | http://localhost:8000/api/schema/swagger/ | — |
| Django Admin | http://localhost:8000/admin | superuser (see below) |
| MinIO console | http://localhost:9001 | `minioadmin` / `minioadmin` |
| Spark master UI | http://localhost:8080 | — |
| Airflow | http://localhost:8081 | `airflow` / `airflow` |
| Prometheus | http://localhost:9090 | — |
| Grafana | http://localhost:3000 | `admin` / `admin` |

Create a Django superuser (for the admin panel):

```bash
make shell
>>> from django.contrib.auth import get_user_model
>>> get_user_model().objects.create_superuser("admin", "a@b.c", "admin")
```

---

## Try it

Upload the sample files and inspect the responses.

**Valid — 3 rows, no errors:**

```bash
curl -X POST -F "file=@data/samples/valid_small.csv" \
  http://localhost:8000/api/v1/validation
```

```json
{
  "status": "completed",
  "total_records": 3,
  "valid_count": 3,
  "invalid_count": 0,
  "errors_by_code": {}
}
```

**Invalid — 4 rows, one per error code:**

```bash
curl -X POST -F "file=@data/samples/invalid_small.csv" \
  http://localhost:8000/api/v1/validation
```

```json
{
  "status": "completed",
  "total_records": 4,
  "valid_count": 1,
  "invalid_count": 3,
  "duplicate_count": 1,
  "errors_by_code": { "E004": 1, "E005": 1, "E006": 1, "E007": 1 }
}
```

More sample files: [`data/samples/README.md`](data/samples/README.md)

Full API reference: [`docs/api.md`](docs/api.md)

---

## The distributed path

Small files (`< 50 MB` by default) validate synchronously in the API process.

Larger files take the async path:

1. Django saves the file to MinIO under `s3://raw/uploads/<uuid>.csv`
2. Publishes an event to the Kafka topic `bank-uploads`
3. Returns **202 Accepted** with the run ID and `status: queued`
4. Spark Structured Streaming consumes the event, applies the same rules
   (implemented as column expressions with `DecimalType(18,2)`)
5. Writes valid rows to `s3://curated/runs/<run_id>/` (Delta)
6. Writes invalid rows to `s3://quarantine/runs/<run_id>/` (Delta)
7. Inserts a summary into `bankval.validation_summary` (ClickHouse)

The client polls `GET /api/v1/validation/{run_id}` until `status` is
`completed` or `failed`.

You can toggle the threshold via `.env`:

```env
SMALL_FILE_THRESHOLD=52428800   # 50 MB
```

Set it to `10` to force the async path for any file (useful for local testing).

---

## Rule set

| Code | Name | Trigger |
|---|---|---|
| E001 | MISSING_REQUIRED_FIELD | Required column absent |
| E002 | INVALID_DATA_TYPE | Numeric field not parseable to Decimal |
| E003 | NULL_OR_EMPTY_VALUE | Empty `bank_code` / `period` / `account_code` |
| E004 | INVALID_BANK_CODE | Not in the allow-list |
| E005 | INVALID_PERIOD_FORMAT | Fails `^\d{4}/(0[1-9]|1[0-2])$` |
| E006 | DUPLICATE_RECORD | Duplicate key (`record_id` or fallback tuple) |
| E007 | BALANCE_MISMATCH | `balance ≠ debit − credit` |
| E008 | NEGATIVE_AMOUNT | `debit < 0` or `credit < 0` |
| E011 | INVALID_CURRENCY | Not a valid ISO 4217 code |

Full catalogue: [`docs/rules.md`](docs/rules.md)

---

## Tests

```bash
make test
```

Covers:

- One unit test per error code (15 tests)
- Batch summary aggregation
- API: valid upload, invalid upload, missing file

```
18 passed in ~4s
```

---

## Operations

Common Makefile targets:

| Command | Purpose |
|---|---|
| `make up` | Start the full stack |
| `make up-dev` | Fast iteration — only web + postgres + redis + minio |
| `make down` | Stop containers (keep volumes) |
| `make reset` | Stop and delete volumes (fresh state) |
| `make migrate` | Apply Django migrations |
| `make seed` | Load `bank_codes.csv` into the allow-list |
| `make test` | Run pytest |
| `make shell` | Django shell in the web container |
| `make logs SERVICE=web` | Tail a service's logs |
| `make submit-batch RUN_ID=<uuid> SOURCE=s3://raw/uploads/<file>` | Run the Spark batch validator on a specific file |
| `make demo` | Full end-to-end demo |

---

## Project layout

```
apps/
├── common/          Prometheus metrics
├── ingestion/       Upload view, parsers, services, Kafka producer
├── validation/      Models, admin, rule engine, seed command
├── reports/         Read-only endpoints (summary, invalid, valid)
└── storage/         MinIO and ClickHouse clients

spark_jobs/
├── session.py             SparkSession with Delta + S3A
├── rules.py               Column-expression rule engine
├── batch_validator.py     Manual spark-submit entry
└── streaming_validator.py Kafka → Delta streaming consumer

airflow/dags/
├── health_check_dag.py    Runs every 15 minutes
└── batch_backfill_dag.py  Manual trigger with --conf

docker/
├── Dockerfile.web / Dockerfile.spark
├── compose.yml
├── prometheus/prometheus.yml
├── grafana/provisioning/
└── init/                  MinIO buckets, Kafka topic, ClickHouse table

docs/
├── architecture.md        Full architecture + diagrams
├── rules.md               Error code catalogue
├── api.md                 REST reference
├── scaling.md             Compose → K8s mapping
└── decisions.md           Architecture Decision Records

data/
├── reference/bank_codes.csv
└── samples/               Small fixtures + expected outputs

tests/
├── unit/test_rules.py
└── api/test_validation_api.py
```

---

## Design decisions worth reading

Short versions of the ADRs in [`docs/decisions.md`](docs/decisions.md):

- **Two rule engines** — pandas for small, Spark for large. Same codes, same semantics.
- **Kafka, not just Celery** — decouples ingestion from distributed compute; replayable.
- **Delta over parquet** — ACID, exactly-once sinks, MERGE for upserts.
- **ClickHouse as gold, not Postgres** — columnar for `GROUP BY`, operational for point lookups.
- **`Decimal` everywhere money is involved** — floats lose precision.
- **Boolean rule columns, not chained `array_union`** — avoids Spark codegen explosion (driver OOM).
- **`foreachBatch` in streaming** — three sinks per micro-batch; per-row doesn't fit.

---

## What would change in production

- **Compute:** Compose → Kubernetes, Spark Operator for `SparkApplication` CRDs
- **Storage:** single-node MinIO → S3 / Ceph; ClickHouse → clustered with shards and replicas
- **Kafka:** 1 broker → 3 brokers, replication factor 3
- **Airflow:** LocalExecutor → KubernetesExecutor
- **Security:** secrets via Vault/KMS, TLS on all endpoints
- **Multi-tenancy:** Row-level security keyed on `bank_code`
- **Schema evolution:** register input schemas; reject unknown columns at ingestion

See [`docs/scaling.md`](docs/scaling.md) for details.

---

## License

MIT — see `LICENSE`.