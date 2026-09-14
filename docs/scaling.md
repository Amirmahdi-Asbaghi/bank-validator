# Scaling Notes

This document explains how each component of the platform scales, what
the current local setup looks like, and what would change in a
production deployment.

---

## Current topology (local)

Everything runs on Docker Compose on a single host.

| Component | Instances | Resources |
|---|---|---|
| Django (web) | 1 | 2 cores, 1 GB RAM |
| Celery worker | 1 | (configured, no tasks) |
| PostgreSQL | 1 | default |
| Redis | 1 | default |
| MinIO | 1 node | default |
| Kafka | 1 broker (KRaft) | 3 partitions on `bank-uploads` |
| Spark master | 1 | — |
| Spark worker | 1 | 2 cores, 1 GB RAM |
| ClickHouse | 1 | default |
| Airflow | webserver + scheduler | default |
| Prometheus + Grafana | 1 each | default |

**Reference performance:** a 51 MB file with ~920k rows processed in
~76 seconds on this setup. Upload HTTP response time under 1 second.

---

## Where the bottlenecks are

Ranked by how soon they'd bite in production:

1. **Spark worker cores** — the single 2-core worker caps throughput at
   ~12,000 rows/sec. Adding workers scales this near-linearly.
2. **Kafka partitions** — 3 partitions cap parallel consumers at 3. Each
   Spark executor consumes one partition. More partitions → more parallelism.
3. **Django single process** — the dev server is single-threaded per
   request. Under load, gunicorn with multiple workers is required.
4. **Postgres** — the operational DB. Handles point lookups fine, but
   analytical queries (`GROUP BY bank_code`) would degrade at scale.
   That's why summaries go to ClickHouse instead.
5. **MinIO single node** — no replication. A disk failure loses data.
   Production needs multi-node or S3.
6. **ClickHouse single node** — no sharding. Fine for millions of rows,
   tight at billions.

---

## How each component scales

### Spark workers (horizontal)

**Current:** 1 worker, 2 cores, 1 GB.

**Scale strategy:** add workers. Each worker contributes its cores to the
executor pool.

```yaml
# docker-compose.yml — add a second worker
  spark-worker-2:
    image: bankval-spark:dev
    command: /opt/spark/bin/spark-class org.apache.spark.deploy.worker.Worker spark://spark-master:7077
    environment:
      SPARK_WORKER_MEMORY: 2G
      SPARK_WORKER_CORES: 4
    volumes:
      - ../spark_jobs:/opt/spark-jobs
```

**Expected impact:** 2 workers → ~2× throughput. 4 workers → ~4×.
The bottleneck shifts to the source file read (S3A) and the Delta
write (MinIO), which are network-bound.

**In production (Kubernetes):** use the Spark Operator. It spins up
executor pods on demand and tears them down when the job finishes.

### Kafka partitions (horizontal)

**Current:** 3 partitions on `bank-uploads`.

**Scale strategy:** increase partitions.

```bash
kafka-topics.sh --bootstrap-server kafka:9092 \
  --alter --topic bank-uploads --partitions 12
```

**Why it matters:** each partition can be consumed by one executor at a
time. With 3 partitions and 3 executors, you saturate. With 12
partitions, you can scale to 12 consumers.

**Trade-off:** more partitions means more metadata overhead and slower
leader elections. Rule of thumb: partitions = max expected consumers × 2.

**In production:** 12–24 partitions for a topic with high volume. Kafka
replication factor ≥ 3 for durability.

### Django (vertical + horizontal)

**Current:** `python manage.py runserver` — the dev server. Single-threaded.

**Scale strategy:**

```bash
gunicorn config.wsgi:application \
  --bind 0.0.0.0:8000 \
  --workers 4 \
  --threads 2 \
  --timeout 60
```

**Why:** gunicorn forks worker processes that handle requests in parallel.
The `runserver` command is single-threaded and unsuitable for load.

**Then:** put multiple gunicorn containers behind a load balancer (nginx,
Traefik, or a cloud LB). The API is stateless (no session affinity
required), so scaling is trivial.

**What breaks first:** the shared Postgres connection pool. Add PgBouncer
in front of Postgres if concurrent connections become an issue.

### Postgres (vertical, then replicas)

**Current:** single instance. Fine for one million runs.

**Scale strategy:**

- **Vertical:** more CPU and RAM. Postgres scales well up to 64 cores.
- **Read replicas:** for read-heavy workloads, add replicas and route
  read queries to them.
- **Partitioning:** partition `validation_validrecord` and
  `validation_invalidrecord` by `created_at` if the tables grow past
  ~100 million rows.
- **Archive:** move old runs to cold storage (Delta on S3 already holds
  the records; Postgres only needs recent metadata).

**What breaks first:** `InvalidRecord` and `ValidRecord` tables on
multi-million-row runs. The sync path only handles small files, so
these tables stay manageable. Async runs never touch them.

### MinIO (horizontal, distributed mode)

**Current:** single node.

**Scale strategy:** distributed MinIO mode.

```bash
minio server http://minio{1...4}/data
```

Four nodes with erasure coding survive two simultaneous node failures.
Throughput scales with nodes.

**In production:** use real S3 (or Ceph, or Wasabi). The app doesn't care —
same API. Only the `MINIO_ENDPOINT` and credentials change.

### ClickHouse (shards + replicas)

**Current:** single node.

**Scale strategy:** a ClickHouse cluster with shards for write throughput
and replicas for read throughput and HA.

```sql
CREATE TABLE validation_summary ON CLUSTER bankval_cluster (
  ...
) ENGINE = ReplicatedMergeTree(...)
```

**When you need it:** billions of rows, high write rate, or sub-second
query SLA.

**Why not Postgres for this:** a `GROUP BY bank_code, period` across 100
million rows in Postgres is a full table scan. In ClickHouse it's a
columnar read — 10–100× faster.

### Airflow (Celery or Kubernetes executor)

**Current:** LocalExecutor — tasks run in the scheduler's process.

**Scale strategy:**

- **CeleryExecutor** — dedicated worker pool for task execution. The
  scheduler doesn't run tasks itself.
- **KubernetesExecutor** — each task runs as a pod, spawning and cleaning
  up per job. Best for variable workloads.

**When you need it:** more than a handful of DAGs, or tasks that need
different resources (some CPU-heavy, some I/O-heavy).

---

## Idempotency at scale

Two things guarantee safe replays:

1. **File hash** — every run stores the SHA-256 of its content. Two
   uploads of the same file produce two runs with the same hash. A
   future enhancement would short-circuit the second (return the existing
   run). Currently, both process — wasteful but correct.

2. **Delta overwrite per run_id** — every run writes to
   `s3://curated/runs/<run_id>/` and `s3://quarantine/runs/<run_id>/`.
   A re-run of the same run_id overwrites the same folder. **No
   duplication, no cleanup needed.**

The combination means a failed Spark job can be re-submitted safely.
No partial writes survive (Delta's transaction log rejects them).

---

## Backpressure

What happens when events arrive faster than Spark can process them?

**Currently:** the Kafka consumer lags. Offsets accumulate. Nothing breaks —
the topic buffers events. Lag is visible in the Kafka UI.

**Mitigations available:**

| Control | Where | Effect |
|---|---|---|
| `maxOffsetsPerTrigger` | Spark Kafka source | Caps rows read per micro-batch |
| `trigger(processingTime="30 seconds")` | Spark write stream | Longer interval between batches |
| More partitions | Kafka | More parallel consumers |
| More Spark executors | Spark cluster | More parallel processing |
| Airflow pool limits | Airflow config | Caps concurrent backfill tasks |

**What to watch:** consumer lag. If it grows steadily, either the input
rate exceeds capacity, or a single event is stuck. Alert on lag > N.

---

## Skew handling

If one bank dominates the topic (90% of events from one `bank_code`),
that partition becomes a hotspot. The other partitions sit idle.

**Fix:** salt the partition key when producing events. Instead of:

```python
key = bank_code
```

Use:

```python
key = f"{bank_code}_{hash(account_code) % 10}"
```

This spreads one bank's events across 10 partitions. Downside: events
from the same bank are no longer strictly ordered — usually fine, but
a correctness concern if ordering matters.

**Our current producer doesn't set a key.** Kafka distributes events
round-robin, which avoids skew but loses any ordering guarantee. For
one-event-per-run, ordering doesn't matter.

---

## Late data

Streaming data can arrive late — an event published before Spark
started, or one delayed by a network partition.

**Spark's answer:** watermarks.

```python
events.withWatermark("event_time", "2 hours")
```

Events older than the watermark are dropped (or routed to quarantine).
This bounds Spark's state, preventing memory growth.

**Our case:** we don't use event time. We process events in the order
they're consumed. Late-arriving events are still processed — no
dropping. The trade-off: Spark's checkpoint grows unbounded if events
accumulate. At our scale, negligible.

**When to add watermarks:** if we needed time-windowed aggregations
(e.g. "invalid rate over the last hour"), watermarks would be mandatory.

---

## Schema evolution

When a bank adds a column to their file:

**Current behavior:** the rule engine adds the column as null, and E001
fires if it's required. Extra columns pass through to `raw_row`.

**Why this is safe:** the parser doesn't fail on unknown columns.
Delta's `mergeSchema=true` writes the extra columns on the next run.

**Risk:** if two banks use different versions of the same column name,
the queries downstream would see inconsistent schemas.

**Production mitigation:** schema registry (Confluent Schema Registry for
Avro, or a homegrown JSON schema validator). Reject unknown columns at
ingestion, and provide a migration path.

---

## Compose → Kubernetes mapping

The application code is containerized and portable. Only the orchestration
layer changes.

| Compose concept | Kubernetes equivalent |
|---|---|
| Service | Deployment + Service |
| Named volume | PersistentVolumeClaim |
| `.env` | ConfigMap + Secret |
| `depends_on: service_healthy` | Init containers + readiness probes |
| `spark-master` + `spark-worker` | Spark Operator (`SparkApplication` CRD) |
| `make up` | `helm install` + `kubectl apply` |
| 1 broker / 1 worker | 3 brokers (RF=3), N workers with HPA on Kafka lag |
| Reverse proxy (hostname pinning) | Ingress with stable DNS |

**What doesn't change:**

- The Docker images
- The application code
- The rule engine (pandas or Spark)
- The storage layout (MinIO/S3 buckets, Delta table paths)
- The event schema (Kafka messages)

**What changes:**

- Service discovery (K8s DNS instead of Compose service names)
- Secrets (K8s Secrets or Vault instead of `.env`)
- Observability (Prometheus Operator instead of manual scrape config)
- Autoscaling (HPA on CPU or custom metrics like Kafka consumer lag)

---

## Cost profile

For a rough sense of where the money goes in production:

| Component | Relative cost | Notes |
|---|---|---|
| Kafka cluster | High | 3 brokers × managed service is expensive |
| Spark workers | High | Pay per executor-hour |
| Object storage (S3) | Low | Cheapest place to keep data |
| Postgres | Medium | Managed RDS is convenient but pricey |
| ClickHouse | Medium | Managed Cloud exists but costly |
| Airflow | Low | Small footprint |
| Observability | Low–medium | Depends on retention |

**Where to optimize:** Spark workers can be ephemeral (spin up, run a job,
shut down). That's how the Spark Operator on K8s works. Object storage
is the cheapest durable store — put everything there and compute from it.

---

## What scales vs. what doesn't

| Scales horizontally well | Scales vertically only | Doesn't scale at all (replace) |
|---|---|---|
| Django (stateless) | Postgres (up to a point) | Local file storage |
| Spark workers | ClickHouse (single-shard) | Single Kafka broker |
| Kafka partitions | MinIO (single-node) | Local Celery queue |
| Airflow workers | | |

The general principle: **stateless components scale out; stateful
components scale up.** Storage is the exception — some stores scale out
well (S3, Kafka), some don't (single Postgres).

---

## Summary

For the current workload (periodic bank uploads, ~1 million rows per
file), the local setup is adequate. It scales to production by:

1. **Vertical** where state matters (Postgres, ClickHouse single-node)
2. **Horizontal** where stateless (Django, Spark workers, Kafka partitions)
3. **Distributed** where the storage layer needs it (MinIO, ClickHouse)

The application code doesn't change. The deployment does.

---

## Related docs

- **[architecture.md](architecture.md)** — the system overview
- **[decisions.md](decisions.md)** — why each tool was chosen
- **[api.md](api.md)** — endpoint reference