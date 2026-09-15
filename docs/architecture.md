# Architecture

A distributed validation platform for bank reporting files. It runs end-to-end
locally on Docker Compose and maps 1:1 to Kubernetes primitives.

---

## 1. The problem

A supervised bank uploads a reporting-period file (CSV or JSON). Each record
must pass structural and financial validation before entering downstream
reconciliation. The system must:

- Accept files of any practical size (KB to GB)
- Validate every record against the spec
- Keep invalid records — never drop them
- Produce a summary per run
- Be replayable, auditable, and observable

---

## 2. Design principles

1. **Two engines, one semantics.** Small files run in-process with pandas.
   Large files run on Spark. Same error codes, same rules, same results.
2. **Never lose data.** Invalid records go to quarantine with their error
   reasons and the original row. Nothing is deleted.
3. **Money is Decimal.** Not float. Exact equality for `balance = debit − credit`.
4. **Idempotent by construction.** Files are content-hashed; Delta sinks are
   per-`run_id` so reruns overwrite cleanly.
5. **Decoupled ingestion.** The API returns in milliseconds; processing happens
   on Kafka + Spark.
6. **Observable at the business level.** Metrics track *records_valid_total*,
   not just HTTP latency.

---

## 3. End-to-end flow

```
                          ┌────────────────────────────┐
                          │  Bank upload (CSV / JSON)  │
                          └─────────────┬──────────────┘
                                        │
                                        ▼
                   ┌─────────────────────────────────────┐
                   │  Django + DRF  —  Ingestion Gateway │
                   │  POST /api/v1/validation            │
                   │  • validates request                │
                   │  • hashes file (SHA-256)            │
                   │  • persists to MinIO raw/           │
                   │  • decides sync vs async            │
                   └────────────┬───────────────┬────────┘
                                │               │
                    size < threshold      size ≥ threshold
                                │               │
                                ▼               ▼
                  ┌───────────────────┐   ┌───────────────────┐
                  │  Sync path        │   │  Async path       │
                  │  pandas rule      │   │  Kafka topic      │
                  │  engine           │   │  bank-uploads     │
                  │  (in-process)     │   └─────────┬─────────┘
                  └─────────┬─────────┘             │
                            │                       ▼
                            │        ┌──────────────────────────────┐
                            │        │  Spark Structured Streaming  │
                            │        │  • consumes topic            │
                            │        │  • reads source from MinIO   │
                            │        │  • same rules (column exprs) │
                            │        │  • DecimalType(18,2)         │
                            │        │  • exactly-once via Delta    │
                            │        └───────┬────────────┬─────────┘
                            │                │            │
                            │           is_valid      !is_valid
                            │                │            │
                            │                ▼            ▼
                            │      ┌─────────────┐  ┌──────────────────┐
                            │      │ curated     │  │ quarantine       │
                            │      │ Delta       │  │ Delta            │
                            │      │ (valid)     │  │ + error_codes[]  │
                            │      └──────┬──────┘  │ + raw_row        │
                            │             │         └────────┬─────────┘
                            │             │                  │
                            └─────────────┴──────┬───────────┘
                                                 │
                                                 ▼
                                   ┌──────────────────────────┐
                                   │  Gold layer              │
                                   │  ClickHouse              │
                                   │  validation_summary      │
                                   │  (per-run aggregate)     │
                                   └────────────┬─────────────┘
                                                │
                                                ▼
                                   ┌──────────────────────────┐
                                   │  Serving                 │
                                   │  • GET /validation/{id}  │
                                   │  • GET .../summary       │
                                   │  • GET .../invalid       │
                                   │  • Grafana dashboards    │
                                   └──────────────────────────┘
```

---

## 4. Layered view

| Layer | Component | Responsibility |
|---|---|---|
| **Ingestion** | Django + DRF | Receive file, hash, persist to raw zone, route by size |
| **Sync compute** | pandas rule engine | Validate small files inline |
| **Async compute** | Kafka + Spark Structured Streaming | Validate large files distributed |
| **Object storage** | MinIO (S3 API) | Raw uploads + Delta tables (curated / quarantine) |
| **Event bus** | Kafka (KRaft) | Decouple ingestion from distributed compute |
| **Table format** | Delta Lake | ACID sinks, time travel, MERGE |
| **Operational DB** | PostgreSQL | Runs, valid records, invalid records, bank allow-list |
| **Analytical DB** | ClickHouse | Per-run summaries for fast `GROUP BY` |
| **Orchestration** | Airflow | Scheduled health checks + manual backfill |
| **Observability** | Prometheus + Grafana | Business metrics + pipeline health |

---

## 5. Data zones (medallion)

| Zone | Path | Contents | Format |
|---|---|---|---|
| **Bronze** (raw) | `s3://raw/uploads/<uuid>.<ext>` | Files exactly as uploaded | CSV / JSON |
| **Silver** (curated) | `s3://curated/runs/<run_id>/` | Records that passed all rules | Delta |
| **Quarantine** | `s3://quarantine/runs/<run_id>/` | Records that failed, with `error_codes`, `error_messages`, `raw_row` | Delta |
| **Gold** | `bankval.validation_summary` (ClickHouse) | One row per run: counts, error breakdown | MergeTree |

Partition key: `run_id` (which maps to `bank_code / period / ingest_date` in
production).

---

## 6. Two engines, one semantics

| Concern | pandas path | Spark path |
|---|---|---|
| File location | in-memory bytes | `s3a://raw/uploads/...` |
| Rule unit | Python function per code | Column expression per code |
| Money type | `decimal.Decimal` | `DecimalType(18, 2)` |
| Duplicate detection | dict of seen keys | window / `row_number` |
| Output | `ValidRecord` / `InvalidRecord` rows in Postgres | Delta tables in MinIO |
| Error codes | E001–E011 | E001–E011 (identical) |

The two implementations are **not** shared code — they share **semantics**.
A parity test (future work) would assert they produce identical `errors_by_code`
for the same input.

---

## 7. Why these choices

| Decision | Reason |
|---|---|
| Kafka in front of Spark | Absorbs bursts; replayable; decouples ingestion latency from compute |
| Delta instead of parquet | ACID, exactly-once writes, time travel, MERGE for upserts |
| ClickHouse alongside Postgres | Columnar storage for `GROUP BY`; Postgres stays operational |
| Decimal everywhere | Floats lose precision — unacceptable in financial data |
| `foreachBatch` (not `foreach`) | Each micro-batch writes to 3 sinks; per-row doesn't fit |
| Boolean columns (not chained `array_union`) | Avoids Spark codegen explosion → driver OOM |
| Airflow for backfill only | Streaming handles live; Airflow handles scheduled / manual |

See `docs/decisions.md` for the full ADR log.

---

## 8. Scale path

### Today (Docker Compose)

Single-host deployment. 1 Spark worker, 1 Kafka broker, 1 of each datastore.

### Production (Kubernetes)

| Compose concept | Kubernetes equivalent |
|---|---|
| Service | Deployment + Service |
| Named volume | PersistentVolumeClaim |
| `.env` | ConfigMap + Secret |
| `depends_on: service_healthy` | Init containers + readiness probes |
| `spark-master` + `spark-worker` | Spark Operator (`SparkApplication` CRD) |
| `make up` | `helm install` + `kubectl apply` |
| 1 broker / 1 worker | 3 brokers (RF=3), N workers with HPA on Kafka lag |

The application containers are identical. Only the orchestration layer changes.

---

## 9. Failure and replay

| Failure | Handling |
|---|---|
| Kafka publish fails | Run marked `failed`, error persisted; counter incremented; caller gets a 5xx |
| Spark streaming job dies | Restarts; resumes from Delta checkpoint (Kafka offsets stored there) |
| Bad file that fails parsing | Run marked `failed`; quarantined row still has `raw_row` |
| Duplicate upload of same file | Same `file_hash` — run-level dedup possible; not yet enforced |
| Skewed bank dominating topic | Salt key: `bank_code || '_' || hash(account_code) % N` |
| Backpressure | `maxOffsetsPerTrigger` on the Kafka source; Airflow pool limits |

---

## 10. Observability

| Signal | Source | Where |
|---|---|---|
| `bankval_records_uploaded_total` | Django counter | `/metrics` → Prometheus → Grafana |
| `bankval_records_valid_total` | Django counter | same |
| `bankval_records_invalid_total` | Django counter | same |
| `bankval_runs_total{path}` | Django counter | same |
| `bankval_kafka_publish_errors_total` | Django counter | same |
| Spark job stages / tasks | Spark UI | `:8080` |
| Airflow DAG runs | Airflow UI | `:8081` |
| Per-run summaries | ClickHouse | `clickhouse-client` or Grafana |

---

## 11. Repository map

```
apps/
├── common/         Prometheus metrics endpoint
├── ingestion/      Upload view, parsers, services, Kafka producer
├── validation/     Models, admin, rule engine (pandas)
├── reports/        Read-only DRF endpoints (summary, invalid)
└── storage/        MinIO + ClickHouse clients

spark_jobs/
├── session.py             SparkSession with Delta + S3A
├── rules.py               Column-expression rule engine
├── batch_validator.py     Manual spark-submit entry
└── streaming_validator.py Structured Streaming consumer

airflow/dags/
├── health_check_dag.py    Every 15 minutes
└── batch_backfill_dag.py  Manual trigger

docker/
├── Dockerfile.web / .spark / etc.
├── compose.yml
├── prometheus/
├── grafana/
└── init/                  Bucket + topic + table creation

docs/
├── architecture.md        (this file)
├── rules.md               Error code catalogue
├── api.md                 REST reference
├── scaling.md             Production mapping
└── decisions.md           ADRs
```

---

## 12. Reading order for a reviewer

1. `README.md` — get it running
2. **this file** — understand the shape
3. `docs/decisions.md` — understand *why*
4. `apps/validation/rules/` — the rule engine
5. `spark_jobs/rules.py` — the distributed twin
6. `docs/api.md` — interact with it