# Bank Validation Platform

A distributed validation pipeline for bank reporting files.

Upload a CSV or JSON file of accounting records. Every record is checked
against a rule set (structure, types, bank allow-list, period format,
duplicates, `balance = debit − credit`). Valid records land in a curated
Delta table. Invalid records go to quarantine with their error codes. A
per-run summary lands in ClickHouse for fast analytics.

Runs end-to-end on Docker Compose. Every container maps 1:1 to Kubernetes.

**Tested at scale:** 51 MB / 920,088 records processed end-to-end in ~76
seconds on a single Spark worker — ~12,100 records/sec.

**Tested by code:** 68 automated tests pass in ~8 seconds.

---

## Highlights

- **Two engines, one semantics** — small files run in pandas; large files
  run on Spark. Same error codes (E001–E011), same results.
- **Never lose data** — invalid records persist in Delta with their
  reasons and the original row.
- **Money is `Decimal`** — exact equality for `balance = debit − credit`,
  never float.
- **Event-driven ingestion** — Django returns in under a second; Spark
  processes asynchronously via Kafka.
- **ACID + idempotent** — Delta Lake sinks, per-`run_id` writes,
  replayable.
- **Observable** — business-level counters in Prometheus, dashboards in
  Grafana, DAGs in Airflow.
- **Tested** — 68 tests covering rules, parsers, serializers, services,
  API endpoints, and Prometheus metrics.

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
                 │           ▼                 ▼
                 │      MinIO (S3)        MinIO (S3)
                 │
                 └────────────────┬────────────┐
                                  ▼            ▼
                            Postgres      ClickHouse
                            (ledger)      (summary)
```

Full detail: [`docs/architecture.md`](docs/architecture.md)

---

## Stack

| Layer | Technology | Role |
|---|---|---|
| API | Django 5 + DRF | Ingestion + serving |
| Async (small) | Celery + Redis | Small-file async fallback |
| Sync compute | pandas + `decimal.Decimal` | In-process validation |
| Distributed compute | Apache Spark 3.5 (PySpark) | Large-file validation |
| Event bus | Apache Kafka 4 (KRaft) | Decoupled ingestion |
| Object storage | MinIO (S3 API) | Raw files + Delta tables |
| Table format | Delta Lake 3.2 | ACID records store |
| Operational DB | PostgreSQL 16 | Run ledger |
| Analytical DB | ClickHouse 24.8 | Per-run summaries |
| Orchestration | Apache Airflow 2.9 | Scheduled health + backfill |
| Observability | Prometheus + Grafana | Business metrics |
| Testing | pytest + pytest-django | Unit + API tests |
| Packaging | Docker Compose | Local dev |

---

## Data flow — where results live

For **every** upload, three outputs are produced. Where they land depends on
the file size.

### Small file (< 50 MB, default)

1. Django receives the file
2. Runs the pandas rule engine in-process
3. Writes results directly to Postgres:
   - `validation_validationrun` — one row per run
   - `validation_validrecord` — valid records
   - `validation_invalidrecord` — invalid records with error codes
4. Returns **HTTP 201** with the full summary

All four read endpoints (`.`, `/summary`, `/valid`, `/invalid`) serve from
Postgres.

### Large file (≥ 50 MB)

1. Django saves the raw file to MinIO: `s3://raw/uploads/<uuid>.csv`
2. Django writes a `queued` run to Postgres
3. Django publishes a small event to Kafka topic `bank-uploads`
4. Returns **HTTP 202** with the run_id — takes under a second
5. Spark Structured Streaming consumes the event:
   - Reads the raw file from MinIO
   - Applies the same rules as column expressions
   - Writes **valid records** → `s3://curated/runs/<run_id>/` (Delta)
   - Writes **invalid records** → `s3://quarantine/runs/<run_id>/` (Delta)
   - Inserts a **summary row** → ClickHouse `bankval.validation_summary`

For large files:
- `/summary` and `/validation/{id}` return ClickHouse data (fallback from Postgres)
- `/valid` and `/invalid` are empty via the API — the records live in Delta
  on MinIO, queryable with Spark

### Where to look for what

| Question | Store | Location |
|---|---|---|
| Did the upload succeed? | Postgres | `validation_validationrun.status` |
| What were the counts? | ClickHouse | `bankval.validation_summary` |
| Raw file bytes? | MinIO | `s3://raw/uploads/<uuid>.csv` |
| Valid records? | MinIO (Delta) | `s3://curated/runs/<run_id>/` |
| Invalid records? | MinIO (Delta) | `s3://quarantine/runs/<run_id>/` |

---

## Quickstart

Requirements: **Docker Desktop**, **Make**, **Git**.

```bash
git clone https://github.com/Amirmahdi-Asbaghi/bank-validator.git
cd bank-validator

cp .env.example .env
cp .env docker/.env

make demo
```

`make demo` brings the stack up, applies migrations, seeds the bank
reference data, and uploads two sample files.

### After `make demo`

| Service | URL | Credentials |
|---|---|---|
| Django API | http://localhost:8000 | — |
| Swagger UI | http://localhost:8000/api/schema/swagger/ | — |
| Django Admin | http://localhost:8000/admin | create superuser below |
| MinIO console | http://localhost:9001 | `minioadmin` / `minioadmin` |
| Spark master UI | http://localhost:8080 | — |
| Airflow | http://localhost:8081 | `airflow` / `airflow` |
| Prometheus | http://localhost:9090 | — |
| Grafana | http://localhost:3000 | `admin` / `admin` |

Create a Django superuser:

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

## Tests

```bash
make test
```

**68 tests, all passing in ~8 seconds.** They cover the full sync path —
rule engine, parser, serializer, service layer, HTTP endpoints, and
Prometheus metrics.

### What's tested

| Area | Tests | File |
|---|---|---|
| Rule engine (E001–E011) | 19 | `tests/unit/test_rules.py` |
| File parser (CSV / JSON) | 14 | `tests/unit/test_parsers.py` |
| Upload serializer | 8 | `tests/unit/test_serializers.py` |
| Service layer (sync path) | 9 | `tests/unit/test_services.py` |
| API endpoints (upload + reads) | 7 | `tests/api/test_validation_api.py` |
| Prometheus metrics | 7 | `tests/api/test_metrics_api.py` |

### What's not tested (and why)

| Gap | Reason |
|---|---|
| Spark rule engine | Needs a JVM; runs in a different container |
| Kafka producer | External dependency |
| MinIO, ClickHouse clients | External services |
| End-to-end async path | Kafka + Spark + Delta + ClickHouse all required |

Full test guide: [`tests/README.md`](tests/README.md)

---

## Performance test

Generate a synthetic file just over the async threshold:

```bash
make big-file
```

This creates `data/samples/big_51mb.csv` (~51 MB, ~920k rows, ~5% errors).
Then upload it via Swagger UI or:

```bash
curl -X POST -F "file=@data/samples/big_51mb.csv" \
  http://localhost:8000/api/v1/validation
```

While it processes, you can watch:

- The **Spark streaming window** for `[streaming] run <id>: valid=X invalid=Y`
- The **Spark Master UI** (http://localhost:8080) for running jobs

### Measured results

| Metric | Value |
|---|---|
| File size | ~52 MB |
| Total records | **920,088** |
| Valid records | **876,467** (95.3%) |
| Invalid records | **43,621** (4.7%) |
| Upload HTTP response time | **< 1 second** (202 Accepted) |
| Spark end-to-end processing | **~76 seconds** |
| Throughput (1 worker, 2 cores) | **~12,100 rows/sec** |

To read the records back from Delta:

```bash
make show-delta RUN_ID=<run-id> BUCKET=curated
make show-delta RUN_ID=<run-id> BUCKET=quarantine
make show-delta RUN_ID=<run-id> BUCKET=quarantine ERROR_CODE=E007
```

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
`completed` or `failed`. The response merges Postgres (metadata) with
ClickHouse (summary counts).

Toggle the threshold via `.env`:

```env
SMALL_FILE_THRESHOLD=52428800   # 50 MB
```

Set it to `10` to force the async path for any file (useful for local testing).

---

## Why three stores?

Each store handles what it's best at. This is a CQRS-style split.

| Concern | Store | Reason |
|---|---|---|
| Transactional run state, point lookups | **Postgres** | ACID, fast by UUID, Django ORM |
| Bulk files + parquet records | **MinIO (S3)** | Cheap, scales to TB, Spark-native |
| Analytical aggregates | **ClickHouse** | Columnar, sub-second `GROUP BY` |
| Stream buffer | **Kafka** | Durable, replayable, decoupled |
| Distributed compute | **Spark** | Partitions a file across workers |

Putting everything in one store would compromise all three access patterns.

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
| `make demo` | Full end-to-end demo |
| `make big-file` | Generate the 51 MB test file |
| `make show-delta RUN_ID=... BUCKET=curated` | Read Delta records with Spark |
| `make submit-batch RUN_ID=... SOURCE=...` | Manually re-run the Spark batch validator |

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
├── streaming_validator.py Kafka → Delta streaming consumer
└── show_delta.py          Utility to read Delta tables

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
├── conftest.py            Shared fixtures
├── unit/
│   ├── test_rules.py      19 tests, one per error code
│   ├── test_parsers.py    14 tests, CSV/JSON parsing
│   ├── test_serializers.py 8 tests, upload validation
│   └── test_services.py   9 tests, sync path end-to-end
└── api/
    ├── test_validation_api.py 7 tests, HTTP endpoints
    └── test_metrics_api.py    7 tests, Prometheus counters
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

## Known limitations

- **Async record listing** — `/valid` and `/invalid` serve from Postgres.
  For large files, records live in Delta on MinIO; use Spark or a
  ClickHouse addition to query them.
- **Spark UI links** — the Application UI links resolve to the container's
  internal IP. The Master UI works. In production on Kubernetes this
  doesn't apply.
- **`bank_code` / `period` on runs** — designed as denormalized metadata
  but not populated. Doesn't affect correctness.
- **No authentication** — the API is open. Fine for a portfolio project;
  production would add DRF token or JWT auth.
- **No Spark rule tests** — they require a JVM, which the `web` container
  doesn't have. The pandas engine has full test coverage.

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