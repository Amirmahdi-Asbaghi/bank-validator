# Project Map

A file-by-file guide to the project. Every folder and file is listed
with a short explanation of its role.

**For the 30-second version, read the [README](README.md).**
**For the 30-minute version, read this file.**

---

## Top-level layout

```
bank-validator/
├── README.md               Project overview, quickstart, demo
├── PROJECT_MAP.md          ← this file
├── Makefile                Developer entry points (make up, test, demo)
├── pytest.ini              Pytest configuration
├── requirements.txt        Python dependencies
├── manage.py               Django's CLI entry point
├── .env.example            Environment variable template
├── .gitignore              What Git should ignore
│
├── config/                 Django project configuration
├── apps/                   Django applications (the domain)
├── spark_jobs/             Spark batch + streaming jobs
├── airflow/                Airflow DAGs
├── docker/                 Container definitions and compose
├── data/                   Reference data and sample files
├── docs/                   Architecture, rules, API, decisions
└── tests/                  Automated test suite
```

---

## `config/` — Django project configuration

The settings, URLs, and WSGI entry point. Everything here is about
wiring Django up, not about business logic.

```
config/
├── __init__.py             Marks the package; imports the Celery app
├── settings.py             All Django settings (env-driven)
├── urls.py                 Root URL routing table
├── celery.py               Celery application bootstrapping
├── wsgi.py                 WSGI entry point for gunicorn
└── asgi.py                 ASGI entry point (unused, kept for defaults)
```

| File | What it does |
|---|---|
| **`settings.py`** | Reads `.env` via `django-environ`, configures Postgres, MinIO, Kafka, ClickHouse, DRF, Celery, and the `SMALL_FILE_THRESHOLD` that drives the sync/async branch. |
| **`urls.py`** | The root URL table. Routes `/admin/`, includes `apps.ingestion.urls` and `apps.reports.urls` under `/api/v1/`, mounts the OpenAPI schema and Swagger UI, and exposes `/metrics`. |
| **`celery.py`** | Creates the Celery app, tells it to read config from Django settings under the `CELERY_` prefix, and enables autodiscovery of tasks. Configured but not currently used as a task path. |
| **`wsgi.py`** | Standard Django WSGI entry point. Gunicorn calls `config.wsgi:application` in production. |
| **`asgi.py`** | ASGI entry point for async servers. Not used — we're on WSGI. |

---

## `apps/` — Django applications

The domain logic. Five apps, each with a single responsibility.

```
apps/
├── common/                 Shared utilities (Prometheus metrics)
├── ingestion/              Upload path: parser, service, view, Kafka
├── validation/             Models, admin, rule engine, seed command
├── reports/                Read-only endpoints (summary, records)
└── storage/                MinIO and ClickHouse clients
```

### `apps/common/` — shared utilities

```
apps/common/
├── __init__.py
├── apps.py                 AppConfig
└── metrics.py              Prometheus counters + /metrics view
```

| File | What it does |
|---|---|
| **`metrics.py`** | Defines five Prometheus counters (records uploaded, valid, invalid, runs by path, Kafka publish errors) and a view that exposes them at `/metrics` in the Prometheus exposition format. |

### `apps/ingestion/` — the write path

Everything related to receiving an upload and starting its processing.

```
apps/ingestion/
├── __init__.py
├── apps.py
├── migrations/
├── models.py               (empty — no models here)
├── parsers.py              CSV/JSON parsing into dicts
├── serializers.py          DRF UploadSerializer (validates the request)
├── services.py             Orchestration: parse → validate → persist
├── views.py                POST /api/v1/validation (the upload endpoint)
├── urls.py                 Routes for the ingestion app
├── kafka_producer.py       Publishes events to Kafka
├── admin.py                (empty)
└── tasks.py                (removed — was Celery, now Kafka)
```

| File | What it does |
|---|---|
| **`parsers.py`** | Takes raw file bytes and produces a list of dicts. Handles CSV (with BOM) and JSON (array or `{"records": [...]}`). Converts empty CSV cells to `None` for consistency. |
| **`serializers.py`** | The `UploadSerializer` — validates the request: file present, extension is `.csv` or `.json`, size ≤ 200 MB. Rejects bad requests before any pipeline code runs. |
| **`services.py`** | The orchestration layer. Three entry points: `run_validation_sync` (pandas path), `run_validation_async` (Kafka path), and `run_validation_from_bytes` (shared core). Ties together parser, rule engine, MinIO, and Postgres. |
| **`views.py`** | The upload endpoint. Validates the request, reads the file, checks the size against `SMALL_FILE_THRESHOLD`, and routes to the sync or async service. Returns 201 or 202. |
| **`kafka_producer.py`** | Publishes the `{run_id, source_file}` event to `bank-uploads`. Uses `confluent-kafka` (not `kafka-python`, which broke on Python 3.12). |
| **`urls.py`** | One route: `path("validation", ValidationUploadView.as_view())`. |

### `apps/validation/` — the domain

The models, the rule engine, and the seed command.

```
apps/validation/
├── __init__.py
├── apps.py
├── admin.py                Django admin registrations
├── models.py               ValidationRun, ValidRecord, InvalidRecord, BankReference
├── migrations/
├── management/
│   └── commands/
│       └── seed_bank_codes.py   Loads bank_codes.csv into BankReference
└── rules/
    ├── __init__.py
    ├── base.py             ErrorCode enum, RuleResult dataclass
    ├── required_fields.py  E001
    ├── types.py            E002
    ├── nulls.py            E003
    ├── bank_code.py        E004
    ├── period.py           E005
    ├── duplicates.py       E006 (key function only)
    ├── financial.py        E007 + E008
    ├── currency.py         E011
    └── engine.py           Composes all rules, handles batches
```

| File | What it does |
|---|---|
| **`models.py`** | Four models. `ValidationRun` (one row per upload, the audit anchor), `ValidRecord` (passing rows), `InvalidRecord` (failing rows + reasons + raw data), `BankReference` (allow-list). |
| **`admin.py`** | Registers the four models with Django admin. `list_display`, `list_filter`, `search_fields` tuned for ops workflows. |
| **`seed_bank_codes.py`** | A management command: reads `data/reference/bank_codes.csv` and upserts rows into `BankReference`. Idempotent. |
| **`rules/base.py`** | The rule engine's vocabulary: `ErrorCode` enum (E001–E011), `ERROR_MESSAGES` dict, `RuleResult` dataclass, `RuleFn` type alias. |
| **`rules/required_fields.py`** | E001 — every required field is present as a key. |
| **`rules/types.py`** | E002 — `debit`, `credit`, `balance` parse to Decimal. |
| **`rules/nulls.py`** | E003 — `bank_code`, `period`, `account_code` are non-empty. |
| **`rules/bank_code.py`** | E004 — `bank_code` is in the allow-list (from `context`). |
| **`rules/period.py`** | E005 — `period` matches `^\d{4}/(0[1-9]|1[0-2])$`. |
| **`rules/duplicates.py`** | E006 — computes the duplicate key (record_id, or fallback triple). The engine does the actual detection. |
| **`rules/financial.py`** | E007 — `balance == debit - credit` (exact Decimal). E008 — non-negative debit/credit. |
| **`rules/currency.py`** | E011 — currency is a valid ISO 4217 code (if present). |
| **`rules/engine.py`** | `validate_batch` — the composition root. Iterates records, calls each rule, handles duplicate detection, aggregates results into a summary. |

### `apps/reports/` — the read path

Read-only endpoints that serve from Postgres or ClickHouse.

```
apps/reports/
├── __init__.py
├── apps.py
├── migrations/
├── models.py               (empty)
├── serializers.py          Response shapes (validation run, records)
├── views.py                Run detail, summary, valid/invalid listings
└── urls.py                 Routes for the reports app
```

| File | What it does |
|---|---|
| **`views.py`** | Four endpoints: `GET /validation/{id}` (detail), `/summary` (compact), `/valid` (paginated), `/invalid` (paginated). The first two merge Postgres + ClickHouse for async runs. |
| **`serializers.py`** | Response shapes for the four endpoints. Uses `ModelSerializer`. |
| **`urls.py`** | Four routes under `/api/v1/`. |

### `apps/storage/` — external storage clients

```
apps/storage/
├── __init__.py
├── apps.py
├── migrations/
├── models.py               (empty)
├── locator.py              MinIO (S3) client: save/read raw files
└── clickhouse_client.py    ClickHouse client: write/fetch run summaries
```

| File | What it does |
|---|---|
| **`locator.py`** | `save_uploaded_file` writes bytes to MinIO and returns an `s3://` URI. `read_uploaded_file` fetches bytes. The only module that imports boto3. |
| **`clickhouse_client.py`** | `write_run_summary` and `fetch_summary`. The API uses `fetch_summary` for the ClickHouse fallback on async runs. |

---

## `spark_jobs/` — Spark jobs

Batch and streaming jobs that validate files distributed. They share
`session.py` (Spark config) and `rules.py` (the Spark rule engine).

```
spark_jobs/
├── session.py              SparkSession factory (Delta + S3A + UI)
├── rules.py                The rule engine as column expressions
├── batch_validator.py      One-shot: validate a single file
├── streaming_validator.py  Long-running: consume Kafka continuously
└── show_delta.py           Dev utility: read a Delta table
```

| File | What it does |
|---|---|
| **`session.py`** | `build_session(name)` returns a SparkSession configured for Delta, S3A (pointed at MinIO), and the Spark UI reverse proxy. Called by every Spark job. |
| **`rules.py`** | The Spark rule engine. Same error codes as the pandas engine, expressed as column expressions. The design note explains why we compute boolean columns instead of chaining `array_union` (that caused a driver OOM). |
| **`batch_validator.py`** | Takes `--run-id` and `--source`, reads the file from MinIO, applies rules, writes two Delta tables. Called manually or from Airflow. |
| **`streaming_validator.py`** | Long-running. Reads the `bank-uploads` Kafka topic, processes each event with the same rules, writes Delta + ClickHouse. The live path. |
| **`show_delta.py`** | A dev utility. Reads a Delta table from MinIO and prints rows. Used to inspect async results. |

---

## `airflow/dags/` — scheduled jobs

Airflow DAGs. Currently two: a health check and a backfill trigger.

```
airflow/dags/
├── health_check_dag.py     Runs every 15 minutes, checks all services
└── batch_backfill_dag.py   Manual trigger for the Spark batch validator
```

| File | What it does |
|---|---|
| **`health_check_dag.py`** | Every 15 minutes, checks that Postgres, Redis, MinIO, Kafka, and ClickHouse are reachable. Logs results to the Airflow UI. |
| **`batch_backfill_dag.py`** | Manual-only DAG. Triggered with `--conf '{"run_id": "...", "source": "..."}'`. Currently echoes the command; production would exec `spark-submit`. |

---

## `docker/` — container definitions

Dockerfiles, Compose overlays, and init scripts. All infrastructure
config lives here.

```
docker/
├── Dockerfile.web          Django + DRF + Celery image
├── Dockerfile.spark        Spark + Delta + S3A image
├── compose.yml             All services (Postgres, MinIO, Kafka, ...)
├── .dockerignore
├── pip.conf                Pip timeout config for slow networks
│
├── init/
│   └── clickhouse-init.sql Creates the validation_summary table
│
├── prometheus/
│   └── prometheus.yml      Scrape configs
│
└── grafana/
    └── provisioning/
        ├── datasources/prometheus.yml
        └── dashboards/dashboards.yml
```

| File | What it does |
|---|---|
| **`Dockerfile.web`** | Python 3.12-slim with Django, DRF, boto3, clickhouse-connect, confluent-kafka. Used by both `web` and `celery` services. |
| **`Dockerfile.spark`** | Apache Spark 3.5 + Delta JARs + Hadoop-AWS + boto3. Used by `spark-master` and `spark-worker`. |
| **`compose.yml`** | The full stack. Every service with its ports, volumes, env vars, and health checks. |
| **`clickhouse-init.sql`** | Creates the `validation_summary` table on first startup. |
| **`prometheus.yml`** | Tells Prometheus where to scrape (Django `/metrics`, Spark master UI). |

---

## `data/` — reference data and samples

```
data/
├── reference/
│   └── bank_codes.csv      The allow-list seed file
└── samples/
    ├── valid_small.csv
    ├── invalid_small.csv
    ├── duplicate_small.csv
    ├── balance_mismatch.csv
    ├── valid_small.json
    ├── invalid_small.json
    └── README.md           Explains each fixture
```

| File | What it does |
|---|---|
| **`bank_codes.csv`** | The three bank codes (`010`, `020`, `030`) seeded into `BankReference`. E004 checks against this list. |
| **`valid_small.csv`** | Three valid rows. The happy-path fixture. |
| **`invalid_small.csv`** | Four rows, one per error code (E004, E005, E006, E007). |
| **`duplicate_small.csv`** | Three identical rows — tests E006. |
| **`balance_mismatch.csv`** | Includes a precision-sensitive row that passes only with Decimal. |

See [`data/samples/README.md`](data/samples/README.md) for the full breakdown.

---

## `docs/` — written documentation

```
docs/
├── README.md               Index of the docs folder
├── architecture.md         How the pipeline fits together
├── rules.md                Error code catalogue (E001–E011)
├── api.md                  REST endpoint reference
├── scaling.md              Production scaling + Compose → K8s mapping
└── decisions.md            Architecture Decision Records (ADRs)
```

| File | What it does |
|---|---|
| **`architecture.md`** | The end-to-end flow, the layered view, the two rule engines, the medallion zones, the scale path. |
| **`rules.md`** | Every error code with its trigger and example. Duplicate key strategy. Decimal note. |
| **`api.md`** | Each endpoint's request/response, status codes, and examples. Includes `/metrics`. |
| **`scaling.md`** | How each component scales, backpressure, skew, late data, and the Compose → Kubernetes mapping. |
| **`decisions.md`** | 12 ADRs. Every "why" question has an answer here. |

---

## `tests/` — automated test suite

```
tests/
├── README.md               Test suite guide
├── conftest.py             Shared fixtures
├── unit/
│   ├── test_rules.py       19 tests: one per error code
│   ├── test_parsers.py     14 tests: CSV/JSON edge cases
│   ├── test_serializers.py 8 tests: upload validation
│   └── test_services.py    9 tests: sync path end-to-end
└── api/
    ├── test_validation_api.py 7 tests: HTTP endpoints
    └── test_metrics_api.py    7 tests: Prometheus counters
```

**68 tests total, all passing in ~8 seconds.**

| File | What it does |
|---|---|
| **`conftest.py`** | Shared fixtures: `allowed_bank_codes`, `valid_record`, `valid_upload`, `invalid_upload`. |
| **`test_rules.py`** | One test per error code, plus a false-positive check for valid data. |
| **`test_parsers.py`** | CSV parsing (BOM, encoding, empty cells), JSON parsing, error handling. |
| **`test_serializers.py`** | Upload validation: extension, size, presence. |
| **`test_services.py`** | End-to-end sync path: bytes → persisted records. |
| **`test_validation_api.py`** | Full HTTP stack: upload, read, error cases. |
| **`test_metrics_api.py`** | Prometheus counters increment correctly. |

See [`tests/README.md`](tests/README.md) for the full guide.

---

## Root files

| File | What it does |
|---|---|
| **`README.md`** | Project overview: highlights, architecture, quickstart, demo, performance numbers. |
| **`PROJECT_MAP.md`** | This file. A file-by-file guide. |
| **`Makefile`** | Developer entry points: `make up`, `make test`, `make demo`, `make show-delta`, etc. |
| **`pytest.ini`** | Pytest configuration. Points at `config.settings`, scans `tests/`. |
| **`requirements.txt`** | Python dependencies for the web image. |
| **`manage.py`** | Django's CLI. `python manage.py <command>`. |
| **`.env.example`** | Template for the `.env` file. Copy to `.env` and adjust. |
| **`.gitignore`** | What Git should not commit. Whitelists the sample fixtures. |

---

## Quick reference — where to look

| Question | File |
|---|---|
| "How do I run this?" | `README.md` |
| "How does the pipeline work?" | `docs/architecture.md` |
| "What does each error code mean?" | `docs/rules.md` |
| "How do I call the API?" | `docs/api.md` |
| "Why did you choose X?" | `docs/decisions.md` |
| "What changes in production?" | `docs/scaling.md` |
| "What does this file do?" | This file |
| "How do I run the tests?" | `tests/README.md` |

---

## Conventions

- **`apps/<name>/`** — a Django app. Models, views, services, and tests live together.
- **`spark_jobs/`** — Python scripts run by `spark-submit`, not Django code.
- **`docs/`** — Markdown, one topic per file.
- **`tests/`** — mirrors the structure of the code it tests.
- **`docker/`** — all container definitions and infra config.

Every module has a docstring explaining its role. Every non-obvious
decision has an inline comment or an ADR entry.