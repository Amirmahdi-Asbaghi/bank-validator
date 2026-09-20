# Bank Validator

A Django REST API that ingests bank reporting data as CSV, JSON files, or raw
JSON bodies, validates every record against a set of rules, stores both the
individual records and a per-run summary, and returns the results as JSON.

---

## Features

- Accepts three input modes: CSV file, JSON file, raw JSON body
- Validates each record against seven rules
- Stores every row — valid or not — with its error codes
- Detects duplicates within a submission
- Returns a summary per run: totals, valid/invalid counts, errors by code
- Exposes a second endpoint to fetch all records for a given run
- Provides a Django admin for browsing runs and records

---

## Tech Stack

- Django
- Django REST Framework
- SQLite

---

## Setup

### 1. Get the project

    git clone https://github.com/Amirmahdi-Asbaghi/bank-validator.git
    cd bank-validator

### 2. Create a virtual environment

    python -m venv .venv

### 3. Activate it

Windows (PowerShell):

    .venv\Scripts\Activate.ps1

macOS / Linux:

    source .venv/bin/activate

### 4. Install dependencies

    pip install -r requirements.txt

### 5. Create the .env file

Copy `.env.example` to `.env`, then generate a secret key:

    python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"

Paste the output as the value of `SECRET_KEY` in `.env`.

### 6. Run migrations

    python manage.py migrate

### 7. Create a superuser

    python manage.py createsuperuser

### 8. Start the server

    python manage.py runserver

- Admin: http://localhost:8000/admin/
- API base: http://localhost:8000/api/v1/

---

## Admin

The Django admin is available at:

    http://localhost:8000/admin/

Log in with the superuser account created during setup. From there you can:

- **Validation runs** — browse every submission: its `run_id`, timestamp,
  file name, and the counts (total, valid, invalid, errors by code).
- **Bank records** — browse every stored row: its data, verdict (`valid`),
  and error codes.

This is the easiest way to inspect what the API stored.

---

## API Endpoints

### POST /api/v1/validation/

Submit data for validation. Accepts one of:

- A CSV file (field name `file`)
- A JSON file (field name `file`)
- A raw JSON body (`Content-Type: application/json`)

Response:

    {
      "run_id": "d5262ef1-5e56-45de-b554-f2cbc25550d2",
      "summary": {
        "total": 3,
        "valid": 2,
        "invalid": 1,
        "errors_by_code": { "E001": 0, "E002": 0, "E003": 0, "E004": 1, "E005": 0, "E006": 0, "E007": 0 }
      }
    }

### GET /api/v1/runs/<run_id>/

Fetch all records for a given run. Returns a JSON list of records.

---

## Usage Examples

Sample files are provided in `data/` — see the table at the bottom for the full
list. The commands below use them directly.

### Upload a CSV file

    curl.exe -X POST http://localhost:8000/api/v1/validation/ -F "file=@data/valid.csv"

Expected result: `total: 10`, `valid: 10`, `invalid: 0`.

To see errors fire, upload `data/mixed.csv` instead:

    curl.exe -X POST http://localhost:8000/api/v1/validation/ -F "file=@data/mixed.csv"

### Upload a JSON file

    curl.exe -X POST http://localhost:8000/api/v1/validation/ -F "file=@data/valid.json"

### POST raw JSON

    curl.exe -X POST http://localhost:8000/api/v1/validation/ -H "Content-Type: application/json" -d "[{\"bank_code\":\"101\",\"period\":\"1405/03\",\"account_code\":\"A001\",\"debit\":1000,\"credit\":400,\"balance\":600}]"

### Fetch records for a run

Use the `run_id` returned by a previous upload:

    curl.exe http://localhost:8000/api/v1/runs/d5262ef1-5e56-45de-b554-f2cbc25550d2/

Or open it in a browser.

---

## Validation Rules

| Code | Meaning                                        |
|------|------------------------------------------------|
| E001 | A required field is missing                    |
| E002 | A field has the wrong type                     |
| E003 | A required field is empty or null              |
| E004 | Bank code is not in the allowed list           |
| E005 | Period is malformed, in the future, or too old |
| E006 | Balance does not equal debit minus credit      |
| E007 | Record is a duplicate within the submission    |

---

## Project Flow

    User request
        |
        v
    urls.py           routes to the view
        |
        v
    views.py          classifies input (CSV / JSON / raw body)
        |
        v
    services.py       orchestrates the pipeline
        |
        v
    readers.py        parses input into a DataFrame
        |
        v
    validators.py     validates rows, detects duplicates, builds summary
        |
        v
    models.py         persists records and run summary
        |
        v
    views.py          builds the HTTP response
        |
        v
    User receives JSON

---

## Database Schema

    +---------------------------------------------------+
    |                    BankRecord                     |
    +---------------------------------------------------+
    | PK  id              BigAutoField                  |
    |     run_id          UUID              [indexed]   |
    |     timestamp       DateTime                      |
    |     bank_code       CharField(50)                 |
    |     period          CharField(20)                 |
    |     account_code    CharField(50)                 |
    |     debit           Decimal(20,2)     [nullable]  |
    |     credit          Decimal(20,2)     [nullable]  |
    |     balance         Decimal(20,2)     [nullable]  |
    |     valid           Boolean           [indexed]   |
    |     errors          JSONField                     |
    +---------------------------------------------------+
                            |
                            | run_id (shared value, not a FK)
                            v
    +---------------------------------------------------+
    |                  ValidationRun                    |
    +---------------------------------------------------+
    | PK  run_id          UUID                          |
    |     timestamp       DateTime                      |
    |     file_name       CharField(255)                |
    |     total           Integer                       |
    |     valid_count     Integer                       |
    |     invalid_count   Integer                       |
    |     errors_by_code  JSONField                     |
    +---------------------------------------------------+

Each `ValidationRun` represents one submission. All `BankRecord` rows that
share its `run_id` belong to that run.

---

## Running Tests

    python manage.py test bank_validator

All 118 tests should pass.

---

## Sample Files

Located in `data/`:

| File                            | What it demonstrates                       |
|---------------------------------|--------------------------------------------|
| valid.csv                       | 10 rows, all valid                         |
| mixed.csv                       | 5 valid, 5 invalid (no duplicates)         |
| invalid_and_duplicates.csv      | all invalid, all duplicated                |
| valid.json                      | JSON version of valid.csv                  |
| mixed.json                      | JSON version of mixed.csv                  |
| invalid_and_duplicates.json     | JSON version of invalid_and_duplicates.csv |