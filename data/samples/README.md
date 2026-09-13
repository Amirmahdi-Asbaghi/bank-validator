# Sample Files

Small fixtures to demo the validation pipeline.

| File | Purpose | Expected result |
|---|---|---|
| `valid_small.csv` | All rows valid | valid=3, invalid=0 |
| `invalid_small.csv` | One row per error code | valid=1, invalid=3, codes: E004, E005, E006, E007 |
| `duplicate_small.csv` | Three identical rows | valid=1, invalid=2, code: E006 |
| `balance_mismatch.csv` | Bad `balance = debit - credit` | valid=1, invalid=2, code: E007 |
| `valid_small.json` | JSON variant of valid file | valid=3, invalid=0 |
| `invalid_small.json` | JSON variant of invalid file | valid=1, invalid=3 |

## Try it

```bash
curl -X POST -F "file=@data/samples/valid_small.csv" \
  http://localhost:8000/api/v1/validation