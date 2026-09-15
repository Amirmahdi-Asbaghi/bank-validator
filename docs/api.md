# API Reference

Base URL (local): `http://localhost:8000`

All responses are JSON. Errors follow the standard DRF shape:

```json
{ "detail": "Human-readable message" }
```

Validation errors on the request body return **400** with a field map:

```json
{ "file": ["Only .csv and .json files are accepted."] }
```

> **Note on commands.** On Windows, `curl` in PowerShell is an alias for
> `Invoke-WebRequest` and does not accept `-X` or `-F`. Use `curl.exe`
> instead. On Linux and macOS, plain `curl` is the real binary. Both forms
> are shown below.

---

## 1. `POST /api/v1/validation`

Upload a CSV or JSON file for validation.

### Request

- **Content-Type:** `multipart/form-data`
- **Max file size:** 200 MB (enforced at the serializer)
- **Accepted extensions:** `.csv`, `.json`

| Field | Type | Required | Notes |
|---|---|---|---|
| `file` | file | yes | The reporting file |

### Behavior

The endpoint has **two response modes**, chosen automatically by file size:

| File size | Path | HTTP status | Body |
|---|---|---|---|
| `< SMALL_FILE_THRESHOLD` (default 50 MB) | pandas validation, in-request | **201 Created** | Full summary, `status="completed"` |
| `≥ SMALL_FILE_THRESHOLD` | MinIO persist → Kafka publish | **202 Accepted** | Run record, `status="queued"` |

The event is then consumed by Spark Structured Streaming, which writes Delta
tables and a ClickHouse summary.

### Response — sync path (`HTTP 201`)

```json
{
  "id": "e92956a6-d294-4d1d-91a2-737812bcfa82",
  "bank_code": "",
  "period": "",
  "source_file": "s3://raw/uploads/a1231d8d599340ce9e32cee5dad78327.csv",
  "file_hash": "a769e87fc2c4aa4b4bd1cb6c82c29525282e3815b6eaf7e7d874b74f5c222a1c",
  "file_size_bytes": 194,
  "status": "completed",
  "total_records": 3,
  "valid_count": 3,
  "invalid_count": 0,
  "duplicate_count": 0,
  "errors_by_code": {},
  "error_message": "",
  "created_at": "2026-09-13T12:09:19.768323Z",
  "started_at": "2026-09-13T12:09:19.754465Z",
  "finished_at": "2026-09-13T12:09:19.842880Z"
}
```

### Response — async path (`HTTP 202`)

```json
{
  "id": "22f422a9-2b3e-4c60-90ce-79bd8bd5f894",
  "status": "queued",
  "source_file": "s3://raw/uploads/7545fa747fe441e68c659a5444b0bb2f.csv",
  "file_hash": "a769e8...",
  "file_size_bytes": 194,
  "total_records": 0,
  "valid_count": 0,
  "invalid_count": 0,
  "duplicate_count": 0,
  "errors_by_code": {},
  "error_message": "",
  "created_at": "2026-09-13T16:30:34.425511Z",
  "started_at": null,
  "finished_at": null
}
```

Client should poll `GET /api/v1/validation/{id}` until `status` is `completed`
or `failed`.

### Error responses

| Status | When |
|---|---|
| `400` | Missing `file`, unsupported extension, file too large |
| `415` | Unsupported `Content-Type` |
| `500` | Internal error (Kafka unreachable, parse failure on sync path) |

### Example

**Windows (PowerShell):**

```powershell
curl.exe -X POST -F "file=@data/samples/valid_small.csv" "http://localhost:8000/api/v1/validation"
```

**Linux / macOS:**

```bash
curl -X POST -F "file=@data/samples/valid_small.csv" \
  http://localhost:8000/api/v1/validation
```

---

## 2. `GET /api/v1/validation/{run_id}`

Return the current state of a run.

### Path parameters

| Parameter | Type | Notes |
|---|---|---|
| `run_id` | UUID | The `id` returned by `POST /validation` |

### Response — `HTTP 200`

Same shape as the `POST` response. `status` transitions over time:

```
queued → running → completed
                ↘ failed
```

### Error responses

| Status | When |
|---|---|
| `404` | `run_id` not found |
| `400` | `run_id` is not a valid UUID |

### Example

**Windows (PowerShell):**

```powershell
curl.exe http://localhost:8000/api/v1/validation/22f422a9-2b3e-4c60-90ce-79bd8bd5f894
```

**Linux / macOS:**

```bash
curl http://localhost:8000/api/v1/validation/22f422a9-2b3e-4c60-90ce-79bd8bd5f894
```

---

## 3. `GET /api/v1/validation/{run_id}/summary`

Return a compact aggregate for a run. Reads from Postgres (`ValidationRun`),
with a fallback to ClickHouse for async runs.

### Response — `HTTP 200`

```json
{
  "run_id": "22f422a9-2b3e-4c60-90ce-79bd8bd5f894",
  "bank_code": "",
  "period": "",
  "status": "completed",
  "total": 3,
  "valid": 3,
  "invalid": 0,
  "duplicates": 0,
  "errors_by_code": {},
  "source": "postgres",
  "created_at": "2026-09-13T16:30:34.425511Z",
  "finished_at": "2026-09-13T16:30:35.100000Z"
}
```

### Notes

- `errors_by_code` is a map of code → count. `{}` means no errors.
- `source` tells you which store answered: `"postgres"` or `"clickhouse"`.
- The equivalent summary is also written to ClickHouse by the streaming job
  (`bankval.validation_summary`).

### Example

**Windows (PowerShell):**

```powershell
curl.exe http://localhost:8000/api/v1/validation/<run_id>/summary
```

**Linux / macOS:**

```bash
curl http://localhost:8000/api/v1/validation/<run_id>/summary
```

---

## 4. `GET /api/v1/validation/{run_id}/invalid`

Paginated list of invalid records for a run. Reads from Postgres
(`InvalidRecord`).

### Query parameters

| Parameter | Type | Default | Max | Notes |
|---|---|---|---|---|
| `page` | int | 1 | — | 1-indexed |
| `page_size` | int | 100 | 1000 | Records per page |

### Response — `HTTP 200`

```json
{
  "count": 3,
  "next": "http://localhost:8000/api/v1/validation/<id>/invalid?page=2",
  "previous": null,
  "results": [
    {
      "id": "…",
      "row_number": 1,
      "error_codes": ["E004"],
      "error_messages": ["Bank code is not in the allow-list: 999"],
      "raw_row": {
        "bank_code": "999",
        "period": "1405/03",
        "account_code": "A1",
        "debit": "100.00",
        "credit": "40.00",
        "balance": "60.00",
        "currency": "IRR",
        "record_id": "R1"
      }
    }
  ]
}
```

### Notes

- `raw_row` is the original record as parsed from the input file.
- `error_codes` may contain multiple codes for the same row (e.g. `["E005","E007"]`).

### Example

**Windows (PowerShell):**

```powershell
curl.exe "http://localhost:8000/api/v1/validation/<run_id>/invalid?page=1&page_size=50"
```

**Linux / macOS:**

```bash
curl "http://localhost:8000/api/v1/validation/<run_id>/invalid?page=1&page_size=50"
```

---

## 5. `GET /api/v1/validation/{run_id}/valid`

Paginated list of valid records. Same shape as `/invalid`, but without
`error_codes` / `error_messages` / `raw_row`. Instead it exposes the typed
data fields:

```json
{
  "count": 3,
  "results": [
    {
      "id": "…",
      "row_number": 1,
      "bank_code": "010",
      "period": "1405/03",
      "account_code": "A1",
      "debit": "100.00",
      "credit": "40.00",
      "balance": "60.00",
      "record_id": "R1",
      "currency": "IRR",
      "branch_code": "",
      "description": ""
    }
  ]
}
```

### Example

**Windows (PowerShell):**

```powershell
curl.exe "http://localhost:8000/api/v1/validation/<run_id>/valid?page=1&page_size=50"
```

**Linux / macOS:**

```bash
curl "http://localhost:8000/api/v1/validation/<run_id>/valid?page=1&page_size=50"
```

---

## 6. `GET /api/schema/swagger/`

Swagger UI generated by `drf-spectacular` from the DRF views.

- OpenAPI JSON: `GET /api/schema/`
- Interactive UI: `GET /api/schema/swagger/`

Use this to explore the API without reading this doc. Open it in a browser:

```
http://localhost:8000/api/schema/swagger/
```

---

## 7. `GET /metrics`

Prometheus exposition format. Business-level counters:

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `bankval_records_uploaded_total` | counter | — | Total records received across all runs |
| `bankval_records_valid_total` | counter | — | Records that passed every rule |
| `bankval_records_invalid_total` | counter | — | Records that failed ≥ 1 rule |
| `bankval_runs_total` | counter | `path` = `sync` \| `async_kafka` | Validation runs by execution path |
| `bankval_kafka_publish_errors_total` | counter | — | Failed publishes to the `bank-uploads` topic |

Scrape config lives in `docker/prometheus/prometheus.yml`.

### Example

**Windows (PowerShell):**

```powershell
curl.exe http://localhost:8000/metrics | Select-String "bankval_"
```

**Linux / macOS:**

```bash
curl http://localhost:8000/metrics | grep bankval_
```

---

## 8. Status codes summary

| Code | Meaning |
|---|---|
| `200` | Successful read |
| `201` | Sync upload completed |
| `202` | Async upload accepted (queued) |
| `400` | Bad request (validation / parse / missing fields) |
| `404` | Run not found |
| `415` | Unsupported media type |
| `500` | Internal error — check logs |

---

## 9. Conventions

- **Money fields** are serialized as strings (e.g. `"100.00"`) to preserve
  Decimal precision. Clients should parse with a decimal-aware library.
- **Timestamps** are ISO 8601, UTC, with microseconds.
- **UUIDs** are lowercase hyphenated form.
- **Pagination** uses DRF's `PageNumberPagination` — `count`, `next`, `previous`, `results`.

---

## 10. Try it with the sample files

**Windows (PowerShell):**

```powershell
# Valid — expect 201, valid_count=3
curl.exe -X POST -F "file=@data/samples/valid_small.csv" "http://localhost:8000/api/v1/validation"

# Invalid — expect 201, invalid_count=3, errors_by_code has E004..E007
curl.exe -X POST -F "file=@data/samples/invalid_small.csv" "http://localhost:8000/api/v1/validation"

# Duplicates — expect 201, valid=1 invalid=2, errors_by_code={E006:2}
curl.exe -X POST -F "file=@data/samples/duplicate_small.csv" "http://localhost:8000/api/v1/validation"

# Balance mismatch — expect 201, valid=1 invalid=2, errors_by_code={E007:2}
curl.exe -X POST -F "file=@data/samples/balance_mismatch.csv" "http://localhost:8000/api/v1/validation"

# JSON variant
curl.exe -X POST -F "file=@data/samples/valid_small.json" "http://localhost:8000/api/v1/validation"
```

**Linux / macOS:**

```bash
# Valid — expect 201, valid_count=3
curl -X POST -F "file=@data/samples/valid_small.csv" \
  http://localhost:8000/api/v1/validation

# Invalid — expect 201, invalid_count=3, errors_by_code has E004..E007
curl -X POST -F "file=@data/samples/invalid_small.csv" \
  http://localhost:8000/api/v1/validation

# Duplicates — expect 201, valid=1 invalid=2, errors_by_code={E006:2}
curl -X POST -F "file=@data/samples/duplicate_small.csv" \
  http://localhost:8000/api/v1/validation

# Balance mismatch — expect 201, valid=1 invalid=2, errors_by_code={E007:2}
curl -X POST -F "file=@data/samples/balance_mismatch.csv" \
  http://localhost:8000/api/v1/validation

# JSON variant
curl -X POST -F "file=@data/samples/valid_small.json" \
  http://localhost:8000/api/v1/validation
```

---

## 11. Querying results beyond the API

For large (async) runs, the API's `/valid` and `/invalid` endpoints serve
from Postgres and are empty. Use the Delta tables on MinIO instead.

### Read records from Delta

Same command on both platforms:

```bash
make show-delta RUN_ID=<run-id> BUCKET=curated
make show-delta RUN_ID=<run-id> BUCKET=quarantine
```

### Count records per error code

The streaming job writes the per-code breakdown to ClickHouse. Same command
on both platforms:

```bash
docker exec bankval-clickhouse clickhouse-client --query "SELECT run_id, total_records, valid_count, invalid_count, errors_by_code FROM bankval.validation_summary WHERE run_id = '<run-id>' FORMAT Vertical"
```