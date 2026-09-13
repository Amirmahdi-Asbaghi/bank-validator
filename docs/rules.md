# Validation Rules

## Required fields

| Field | Type | Notes |
|---|---|---|
| `bank_code` | string | Must be in the allow-list (E004) |
| `period` | string | `YYYY/MM`, Jalali (E005) |
| `account_code` | string | Non-empty (E003) |
| `debit` | Decimal | ≥ 0, part of balance check |
| `credit` | Decimal | ≥ 0, part of balance check |
| `balance` | Decimal | Must equal `debit − credit` (E007) |

## Optional fields

| Field | Type | Notes |
|---|---|---|
| `currency` | string | ISO 4217 if present (E011); defaults to IRR |
| `record_id` | string | Preferred duplicate key (E006) |
| `branch_code` | string | Reporting only |
| `description` | string | Free text |

## Duplicate key strategy

- Primary: `record_id` when present
- Fallback: `(bank_code, period, account_code)`

## Balance rule

`balance == debit - credit` — **exact** Decimal comparison, no epsilon.

## Error codes

| Code | Name | Triggered by |
|---|---|---|
| E001 | MISSING_REQUIRED_FIELD | Required column absent from the record |
| E002 | INVALID_DATA_TYPE | Numeric field not parseable to Decimal |
| E003 | NULL_OR_EMPTY_VALUE | Empty `bank_code` / `period` / `account_code` |
| E004 | INVALID_BANK_CODE | Not in the allow-list |
| E005 | INVALID_PERIOD_FORMAT | Fails `^\d{4}/(0[1-9]|1[0-2])$` |
| E006 | DUPLICATE_RECORD | Duplicate key hit |
| E007 | BALANCE_MISMATCH | `balance ≠ debit − credit` |
| E008 | NEGATIVE_AMOUNT | `debit < 0` or `credit < 0` |
| E011 | INVALID_CURRENCY | Present but not a valid ISO 4217 code |

## Design notes

- Every required field participates in validation.
- No derived fields (`validity_flag`, computed totals) are trusted from input.
- No server-generated fields (`row_number`, `run_id`, timestamps) appear in input.
- Money is `Decimal`, never `float`.