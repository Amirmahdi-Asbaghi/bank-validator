# Documentation

This folder contains the written documentation for the bank-validation
platform. Each file answers a specific question a reviewer or operator
might have.

---

## Index

| File | Question it answers |
|---|---|
| [`architecture.md`](architecture.md) | How does the pipeline fit together? What are the layers? |
| [`rules.md`](rules.md) | What are the validation rules? What does each error code mean? |
| [`api.md`](api.md) | What endpoints exist? What do they return? |
| [`scaling.md`](scaling.md) | How does this scale? What would change in production? |
| [`decisions.md`](decisions.md) | Why is the design this way? What were the trade-offs? |

---

## Reading order

If you're reviewing this project for the first time:

1. **Root [`README.md`](../README.md)** — project overview, quickstart, demo
2. **[`architecture.md`](architecture.md)** — the big picture
3. **[`decisions.md`](decisions.md)** — the *why* behind the design
4. **[`rules.md`](rules.md)** — the validation rules
5. **[`api.md`](api.md)** — how to interact with the API
6. **[`scaling.md`](scaling.md)** — what changes at scale

---

## What each doc covers

### architecture.md

The complete technical picture:

- End-to-end flow diagram (upload → Django → MinIO → Kafka → Spark → Delta → ClickHouse)
- Layer-by-layer breakdown (ingestion, storage, compute, serving)
- The CQRS-style split (Postgres for state, ClickHouse for aggregates, MinIO for bulk)
- Two rule engines (pandas + Spark) and how they stay in sync
- Data zones: raw, curated, quarantine
- Scale path: Compose → Kubernetes mapping

**Read this if** you want to understand how the system works as a whole.

### rules.md

The validation rule catalogue:

- Required and optional fields
- Duplicate key strategy
- Balance rule (`balance = debit − credit`, exact Decimal)
- Error codes E001–E011 with triggers and examples
- Design notes (no derived fields trusted, no PII, Decimal not float)

**Read this if** you need to know exactly what each error code means.

### api.md

The REST API reference:

- `POST /api/v1/validation` — upload a file
- `GET /api/v1/validation/{id}` — run detail
- `GET /api/v1/validation/{id}/summary` — compact counts
- `GET /api/v1/validation/{id}/valid` — paginated valid records
- `GET /api/v1/validation/{id}/invalid` — paginated invalid records
- `GET /metrics` — Prometheus endpoint

Each endpoint has a request/response example and status code documentation.

**Read this if** you're integrating with the API.

### scaling.md

The production-readiness story:

- Current topology (Compose, single-host)
- How each component scales (Spark workers, Kafka partitions, ClickHouse shards)
- Idempotency and backpressure strategies
- Skew handling (salting)
- Late data (watermarking)
- Compose → Kubernetes mapping table

**Read this if** you're wondering what changes when this runs in production.

### decisions.md

The Architecture Decision Records (ADRs):

- Two rule engines, one semantics
- Kafka for large files, Celery for small
- Delta Lake for curated/quarantine
- Decimal everywhere money is involved
- Boolean rule columns, not chained `array_union`
- ClickHouse as gold, not Postgres
- `foreachBatch` in streaming, not `foreach`

Each ADR states the decision, the reasoning, and the trade-offs.

**Read this if** you want to understand *why* the system is built this way, not just *how*.

---

## Related documentation

Outside `docs/`, the project has:

- **[Root README](../README.md)** — project overview, quickstart
- **[Tests README](../tests/README.md)** — test suite guide
- **[Sample data README](../data/samples/README.md)** — fixture files explained
- **Swagger UI** at `/api/schema/swagger/` — interactive API reference (auto-generated)

---
