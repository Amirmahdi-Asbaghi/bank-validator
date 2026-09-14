# Sample Files

Small fixtures for demoing the validation pipeline. Each file is
under 500 bytes and exercises a specific behavior.

| File | Purpose | Expected result |
|---|---|---|
| `valid_small.csv` | All rows valid | valid=3, invalid=0 |
| `invalid_small.csv` | One row per error code | valid=1, invalid=3, codes: E004, E005, E006, E007 |
| `duplicate_small.csv` | Three identical rows | valid=1, invalid=2, code: E006 |
| `balance_mismatch.csv` | Bad `balance = debit − credit` | valid=1, invalid=2, code: E007 |
| `valid_small.json` | JSON variant of the valid file | valid=3, invalid=0 |
| `invalid_small.json` | JSON variant of the invalid file | valid=1, invalid=3 |

---

## The files

### valid_small.csv

Three rows, every field correct. This is the "happy path" sample —
useful for confirming the pipeline works end-to-end without errors.

```csv
bank_code,period,account_code,debit,credit,balance,currency,record_id
010,1405/03,A1,100.00,40.00,60.00,IRR,R1
010,1405/03,A2,200.00,50.00,150.00,IRR,R2
020,1405/03,A3,10.00,0.00,10.00,IRR,R3
```

### invalid_small.csv

Four rows, each triggering a specific error:

| Row | Content | Fails |
|---|---|---|
| 1 | `bank_code = 999` | E004 — not in allow-list |
| 2 | `period = 1405-03`, `balance = 999` | E005 (bad format) + E007 (mismatch) |
| 3 | All correct | — (control row) |
| 4 | Same as row 3 | E006 — duplicate |

**Expected summary:** `valid=1, invalid=3, duplicates=1`,
`errors_by_code = {E004: 1, E005: 1, E006: 1, E007: 1}`.

### duplicate_small.csv

Three identical rows with the same `record_id`. The first is valid;
the other two are flagged E006.

### balance_mismatch.csv

Three rows, two with incorrect balances. Includes a
precision-sensitive row (`0.10 − 0.05 = 0.05`) that would fail with
float arithmetic but passes with Decimal.

### valid_small.json and invalid_small.json

JSON variants of the CSV files. Same semantics, different format.
The pipeline accepts both — the extension determines the parser.

**JSON structure accepted:**

```json
{
  "records": [
    {"bank_code": "010", "period": "1405/03"}
  ]
}
```

A bare JSON array (`[{...}, {...}]`) is also accepted.

---

## Try it

With the stack running:

```bash
curl -X POST -F "file=@data/samples/valid_small.csv" \
  http://localhost:8000/api/v1/validation
```

The summary fields `valid_count`, `invalid_count`, and `errors_by_code`
should match the "Expected result" column above.

For the invalid file:

```bash
curl -X POST -F "file=@data/samples/invalid_small.csv" \
  http://localhost:8000/api/v1/validation
```

**Or via Swagger UI:** open `http://localhost:8000/api/schema/swagger/`,
expand `POST /api/v1/validation`, click "Try it out", choose a file,
and click Execute.

---

## Which files are committed

The `.gitignore` **whitelists** these specific files so a reviewer can
run the demo without cloning data:

```
!data/samples/valid_small.csv
!data/samples/invalid_small.csv
!data/samples/duplicate_small.csv
!data/samples/balance_mismatch.csv
!data/samples/valid_small.json
!data/samples/invalid_small.json
!data/samples/README.md
```

Any other `.csv` or `.json` in this folder — including generated files
like `big_51mb.csv` — is ignored by default.

---

## Generating a large file

For testing the async path (Kafka → Spark), generate a file just over
the sync threshold:

```bash
make big-file
```

This creates `data/samples/big_51mb.csv` (~51 MB, ~920k rows, ~5%
deliberate errors). It's **not** committed — it's a local test
artifact. See the root README for expected performance numbers.

---

## Which tests use these fixtures

The sample files here are for **manual demos**. The test suite uses
its own fixtures defined in `tests/conftest.py` (byte literals instead
of files on disk). If you change a sample file, the tests won't break —
they don't read from `data/samples/`.

To keep them in sync, mirror any change in the corresponding
`conftest.py` fixture:

| Sample file | Test fixture |
|---|---|
| `valid_small.csv` | `valid_csv_bytes` |
| `invalid_small.csv` | `invalid_csv_bytes` |

---

## Error codes reference

See `docs/rules.md` for the full catalogue (E001–E011) with examples.