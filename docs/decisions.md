# Architecture Decision Records (ADRs)

Every significant design choice in this project is documented here with
its reasoning and trade-offs. These aren't post-hoc justifications —
they're the decisions that shaped the architecture, written in the form
"Context → Decision → Consequences."

---

## ADR-001 — Two rule engines, one semantics

**Status:** Accepted

**Context**

The spec requires validating bank reporting files that vary in size from
a few hundred bytes to multi-gigabyte. A single engine has to handle both
extremes, or we need to accept two.

**Decision**

Two engines:
- **pandas** (`apps/validation/rules/`) — Python functions, called
  per record, in-process.
- **Spark** (`spark_jobs/rules.py`) — column expressions, evaluated per
  partition, distributed.

Both produce the same error codes (E001–E011) with identical semantics.

**Consequences**

*Positive:*
- Small files validate in milliseconds (no Spark startup cost).
- Large files distribute across workers (no memory limits).
- The API can return a 201 for small files and a 202 for large ones.

*Negative:*
- Duplication. A rule change touches two files.
- No automated test asserts parity between the engines (a candidate
  for a future standalone Spark test).

*Mitigation:*
- Keep the rule set small and stable.
- Document both implementations in `docs/rules.md`.
- Reserve the option to add a parity check as a `spark-submit` job.

**Alternatives considered**
- **Single pandas engine:** would OOM on multi-GB files.
- **Single Spark engine:** 10-second startup is unacceptable for a 200 KB file.
- **Spark with Python UDFs:** 5–10× slower than native column expressions.
- **Shared "rule DSL" compiled to both:** significantly more code, no clear benefit.

---

## ADR-002 — Kafka for large files, Celery for small

**Status:** Accepted

**Context**

Both async paths (Kafka, Celery) could work for any file. The question
is which to use, or whether to use one for everything.

**Decision**

- **Small files** (< 50 MB) → sync path, pandas, in-process.
- **Large files** (≥ 50 MB) → Kafka → Spark Streaming.
- **Celery** is configured but not currently used as a task path.

**Consequences**

*Positive:*
- Spark's ~10s startup is only paid for files large enough to benefit.
- Kafka decouples ingestion from processing; replayable; durable.
- Different tools for different workloads.

*Negative:*
- Two async mechanisms means more infrastructure (Kafka + Celery + Redis).
- Celery is currently unused — a source of confusion.

*Mitigation:*
- Document the Celery setup as "available but not active" in the
  architecture diagram.
- Remove Celery if it stays unused past a certain point.

**Alternatives considered**
- **Kafka for everything:** overhead for small files is unjustified.
- **Celery for everything:** can't distribute a single large file.
- **No async at all:** blocks the API on large uploads.

---

## ADR-003 — Delta Lake for curated and quarantine

**Status:** Accepted

**Context**

The curated (valid) and quarantine (invalid) records need durable
storage on object storage. Candidates: plain parquet, Delta Lake,
Apache Iceberg, Apache Hudi.

**Decision**

Delta Lake on MinIO.

**Consequences**

*Positive:*
- **ACID transactions.** A run's writes are atomic — no partial state.
- **Time travel.** `DESCRIBE HISTORY` shows every version of a table.
- **Schema evolution.** `mergeSchema=true` handles new columns.
- **MERGE support.** Future upserts without full rewrites.

*Negative:*
- Additional JARs in the Spark image (`delta-spark`, `delta-storage`).
- Slower writes than raw parquet (the transaction log is a small overhead).

*Mitigation:*
- The jars are baked into the Spark image; no runtime download.
- Writes are batched per run — the overhead is amortized.

**Alternatives considered**
- **Plain parquet:** no transactions, no time travel, no schema evolution.
- **Apache Iceberg:** comparable features, more complex metadata layer.
- **Apache Hudi:** more streaming-oriented; less natural for our batch+stream mix.
- **Database (Postgres) for records:** doesn't scale to millions of rows per run.

---

## ADR-004 — Decimal everywhere money is involved

**Status:** Accepted

**Context**

Money values appear in three places: pandas rules, Spark rules, Django
models. Each could use float, Decimal, or string.

**Decision**

`decimal.Decimal` in Python, `DecimalType(18, 2)` in Spark,
`DecimalField(max_digits=18, decimal_places=2)` in Django.

**Consequences**

*Positive:*
- Exact arithmetic. `0.1 + 0.2 == 0.3` exactly.
- `balance = debit − credit` is a true equality — no epsilon.
- Round-trip between systems preserves the exact value.

*Negative:*
- Slower than float (irrelevant at our scale).
- 18,2 has a precision ceiling (10^16 with two decimals — enough for any bank amount).
- JSON serialization requires strings (numbers would lose precision).

*Mitigation:*
- DRF serializes DecimalField as strings. Clients parse with a
  Decimal-aware library.
- The 18,2 cap is documented; if we ever needed more, it's a
  model + rule update.

**Alternatives considered**
- **float:** precision loss — unacceptable for financial data. The
  E007 test (`0.10 - 0.05 == 0.05`) would fail.
- **Integer cents:** works for USD but breaks for currencies with
  different minor units (JPY, KWD). Also requires scaling logic everywhere.
- **Fraction:** exact but no decimal representation — unsuitable for
  user-facing values.

---

## ADR-005 — Boolean rule columns, not chained `array_union`

**Status:** Accepted

**Context**

The Spark rule engine needs to accumulate error codes per record. The
initial implementation chained `array_union` per rule to build up an
array. It looked idiomatic but caused a driver OOM.

**Decision**

Compute each rule as an independent boolean column
(`r_e001`, `r_e002`, ...), then assemble the error arrays in **one
pass** using `F.array(...)` + `F.flatten(...)`.

**Consequences**

*Positive:*
- Generated Java code is small (kilobytes instead of megabytes).
- No driver OOM.
- Each rule's logic is independent — easier to test and debug.
- Adding a rule is additive; doesn't deepen the expression tree.

*Negative:*
- Requires a final "drop intermediate columns" step.
- Slightly less obvious than a single accumulating expression.

*Mitigation:*
- The intermediate column names (`r_e001`..`r_e011`) are documented.
- The pattern is documented in the module docstring as a cautionary
  example.

**Alternatives considered**
- **Chained `array_union`:** caused the OOM; documented as the reason
  for the change.
- **UDF per record:** 5–10× slower due to Python↔JVM serialization.
- **`concat` on arrays (Spark 3.0+):** equivalent to the current
  approach; a candidate for a simplification.

**Real incident:** this decision was made *because* the initial version
hit the OOM. The stack trace and fix are documented in the file's
module docstring.

---

## ADR-006 — ClickHouse for the gold layer, not Postgres

**Status:** Accepted

**Context**

Every run needs a per-run summary (counts, error breakdown). The summary
is used by the API for status polling and by dashboards for aggregate
queries. Could live in Postgres (already running) or a dedicated
analytical store.

**Decision**

ClickHouse, in a table `validation_summary`.

**Consequences**

*Positive:*
- Columnar storage — fast `GROUP BY bank_code, period` across millions of runs.
- Native `Map(String, UInt32)` type for `errors_by_code` (no joins).
- Sub-second query latency for the API.
- Isolates analytical workloads from transactional ones.

*Negative:*
- Another service to run, back up, and monitor.
- No native `UPDATE` — the summary is append-only (fine for our use).
- Async runs require a Postgres → ClickHouse fallback in the API.

*Mitigation:*
- The `docs/api.md` documents the fallback.
- A future enhancement would have Spark update Postgres so the fallback
  isn't needed.

**Alternatives considered**
- **Postgres:** fine for small volumes, but `GROUP BY` across millions
  of rows is slow (full scans, row-oriented storage).
- **DuckDB:** excellent for local analytics, but not a server.
- **Single Postgres materialized view:** refreshes are expensive and
  block the main tables.

---

## ADR-007 — `foreachBatch`, not `foreach`

**Status:** Accepted

**Context**

Spark Structured Streaming has two output sinks: `foreach` (per row)
and `foreachBatch` (per micro-batch). The streaming job needs to process
each event: read a file, write two Delta tables, insert into ClickHouse.

**Decision**

`foreachBatch`.

**Consequences**

*Positive:*
- Full DataFrame API available inside the batch function.
- Multiple sinks in one call (Delta + ClickHouse).
- Error handling per event (a bad file doesn't kill the batch).

*Negative:*
- Higher latency per batch than per-row processing (irrelevant here).
- The batch function must iterate manually for per-event work.

*Mitigation:*
- Micro-batches are small (a handful of events). `collect()` is cheap.
- For a high-volume stream, we'd batch the writes (single Delta write
  partitioned by run_id).

**Alternatives considered**
- **`foreach`:** per-row, no DataFrame API. Would require re-creating
  DataFrames per row — slow and awkward.
- **`writeStream` to a Delta sink:** single-sink only. We need two
  Delta tables plus ClickHouse.

---

## ADR-008 — Postgres for operational state, Delta for records

**Status:** Accepted

**Context**

Async runs produce hundreds of thousands to millions of records.
Sync runs produce a few hundred. Could store all records in Postgres,
or split by path.

**Decision**

- **Sync path** — write records to Postgres. Small volume, transactional.
- **Async path** — write records to Delta on MinIO. Large volume, columnar.

**Consequences**

*Positive:*
- Postgres stays fast (no large-record tables in the async path).
- Delta handles the volume.
- The API's `/valid` and `/invalid` endpoints work for sync runs.

*Negative:*
- The same endpoints don't work for async runs.
- Two paths to reason about.
- A known gap documented in the README.

*Mitigation:*
- The API's `/summary` and `/validation/{id}` endpoints merge Postgres
  and ClickHouse, so they work for both paths.
- For async records, users are directed to Delta via Spark
  (`make show-delta`).

**Alternatives considered**
- **All records in Postgres:** async runs would insert 900k rows per
  run. Slow and unsustainable.
- **All records in Delta:** sync runs would need Spark (~10s startup)
  for a 200 KB file. Unacceptable.
- **Both paths write to Delta:** forces Spark into the sync path.

---

## ADR-009 — MinIO as the S3-compatible object store

**Status:** Accepted

**Context**

Both raw files and Delta tables need object storage. Could use local
disk, S3 (requires AWS), MinIO (self-hosted S3-compatible), or Ceph.

**Decision**

MinIO in Docker Compose; the same code works against real S3 in
production.

**Consequences**

*Positive:*
- **S3 API** — same code path for local dev and prod.
- **Scales horizontally** — MinIO's distributed mode.
- **Cheap** — no egress fees, no vendor lock-in.
- **Familiar tooling** — `mc` CLI, `boto3`, Spark's S3A connector.

*Negative:*
- Single-node in dev — no durability.
- The `bitnami/minio` image moved to `quay.io/minio` mid-project —
  a reminder to pin image sources.

*Mitigation:*
- Use `quay.io/minio/minio` for the image.
- In production, switch to real S3 (only the endpoint URL changes).

**Alternatives considered**
- **Local disk:** can't share across containers, no durability.
- **AWS S3 directly in dev:** requires AWS account, costs, network dependency.
- **Ceph:** heavier to run locally.

---

## ADR-010 — Kafka image: `apache/kafka`, not `bitnami/kafka`

**Status:** Accepted

**Context**

Bitnami's Kafka images moved to a paid registry mid-project. The
`bitnami/kafka` tag stopped pulling. Needed an alternative.

**Decision**

Switch to `apache/kafka:4.0.2` — the official Apache distribution.

**Consequences**

*Positive:*
- Official image; maintained by the Apache Foundation.
- No subscription required.

*Negative:*
- Different env var names (`KAFKA_NODE_ID` vs `KAFKA_CFG_NODE_ID`).
- Different file paths (`/opt/kafka/bin/` vs `/opt/bitnami/kafka/bin/`).
- The `entrypoint` must be overridden explicitly in Compose.

*Mitigation:*
- The Compose file uses explicit commands, not Bitnami's convenience env vars.
- The image tag is pinned (`4.0.2`, not `latest`).
- Documented in the README.

**Alternatives considered**
- **`bitnamilegacy/kafka`:** temporary; unsupported; will disappear.
- **Confluent's `cp-kafka`:** heavier, license constraints.
- **Redpanda:** Kafka-compatible but a different product.

---

## ADR-011 — Checkpoint to S3, not local disk

**Status:** Accepted

**Context**

Spark Structured Streaming needs a checkpoint location for Kafka offsets
and streaming state. Could be local disk or S3.

**Decision**

S3 (via `s3a://curated/_checkpoints/streaming`).

**Consequences**

*Positive:*
- Survives container restarts.
- Replayable — the streaming job resumes from the last committed offset.
- Consistent with where the data lives (MinIO).

*Negative:*
- Slightly slower than local disk (network writes for each commit).
- S3 dependencies for the streaming job to start.

*Mitigation:*
- Checkpoint writes are batched; the latency is amortized over the
  micro-batch interval (10 seconds).
- The trade-off is correctness over speed.

**Alternatives considered**
- **Local disk:** lost on container restart. Would reprocess from
  `startingOffsets=earliest` — potentially reprocessing thousands of
  events on every restart.
- **NFS:** shared disk; fragile in containerized environments.

---

## ADR-012 — `monotonically_increasing_id`, not `row_number`

**Status:** Accepted

**Context**

Each record needs a row identifier for traceability. `row_number` gives
a sequential 1..N; `monotonically_increasing_id` gives unique but
non-contiguous IDs across partitions.

**Decision**

`monotonically_increasing_id() + 1`.

**Consequences**

*Positive:*
- **Partition-local** — no global sort, no single-partition bottleneck.
- **Fast** — negligible overhead.
- **Unique** — the requirement that matters.

*Negative:*
- Not sequential. IDs look like 1, 2, 3, 8589934592, ... across partitions.
- Doesn't map directly to source file line numbers.

*Mitigation:*
- Row numbers are for identification, not ordering. The exact value doesn't matter.
- Future enhancement: compute the true line number during parsing
  (before Spark), pass it through as a column.

**Alternatives considered**
- **`row_number()` with `Window.orderBy(F.lit(1))`:** forces all data
  through a single partition. Kills parallelism on large files.
- **`zipWithIndex` on an RDD:** same bottleneck.
- **Client-generated IDs:** the parser could assign them. Adds overhead
  per record; no clear benefit.

---

## Summary table

| ADR | Decision | Primary reason |
|---|---|---|
| 001 | Two rule engines | Optimize for file size |
| 002 | Kafka for large, Celery idle | Decouple ingestion from compute |
| 003 | Delta Lake for records | ACID + time travel + schema evolution |
| 004 | Decimal for money | Exact arithmetic |
| 005 | Boolean columns, not `array_union` | Avoid driver OOM |
| 006 | ClickHouse for summaries | Columnar aggregation |
| 007 | `foreachBatch` in streaming | Multiple sinks per micro-batch |
| 008 | Postgres for state, Delta for records | Split by access pattern |
| 009 | MinIO (S3-compatible) | Same code as prod |
| 010 | `apache/kafka` image | Avoid paid registry |
| 011 | Checkpoint to S3 | Survive container restarts |
| 012 | `monotonically_increasing_id` | Parallel-safe IDs |

---

## For the interview

If asked "why did you choose X?":

> "I documented every significant decision in `docs/decisions.md`. It's
> an ADR log — each entry states the context, the decision, the
> consequences, and the alternatives considered. The two most interesting
> ones are ADR-005, where I hit a real Spark driver OOM and fixed it by
> changing the expression shape, and ADR-004, on why money is Decimal
> everywhere — `0.1 + 0.2` in float isn't `0.3`, and in financial
> validation that's a critical bug."

---

## Related docs

- **[architecture.md](architecture.md)** — the system overview
- **[scaling.md](scaling.md)** — the production path
- **[rules.md](rules.md)** — the validation rules