"""Unit tests for the rule engine (one test per error code)."""
from __future__ import annotations

from apps.validation.rules.engine import validate_batch


ALLOWED = {"010", "020", "030"}


def _run_one(record):
    """Helper: run the engine on a single record, return (outcome, summary)."""
    result = validate_batch([record], allowed_bank_codes=ALLOWED)
    return result["outcomes"][0], result["summary"]


# ---------- E001 ----------

def test_e001_missing_required_field():
    record = {"bank_code": "010", "period": "1405/03", "account_code": "A1",
              # debit missing
              "credit": "10", "balance": "10"}
    outcome, _ = _run_one(record)
    assert "E001" in outcome["error_codes"]


# ---------- E002 ----------

def test_e002_invalid_data_type():
    record = {"bank_code": "010", "period": "1405/03", "account_code": "A1",
              "debit": "not-a-number", "credit": "10", "balance": "0"}
    outcome, _ = _run_one(record)
    assert "E002" in outcome["error_codes"]


# ---------- E003 ----------

def test_e003_null_or_empty():
    record = {"bank_code": "", "period": "1405/03", "account_code": "A1",
              "debit": "10", "credit": "5", "balance": "5"}
    outcome, _ = _run_one(record)
    assert "E003" in outcome["error_codes"]


# ---------- E004 ----------

def test_e004_invalid_bank_code():
    record = {"bank_code": "999", "period": "1405/03", "account_code": "A1",
              "debit": "100", "credit": "40", "balance": "60"}
    outcome, _ = _run_one(record)
    assert "E004" in outcome["error_codes"]


def test_e004_valid_bank_code_passes():
    record = {"bank_code": "010", "period": "1405/03", "account_code": "A1",
              "debit": "100.00", "credit": "40.00", "balance": "60.00"}
    outcome, _ = _run_one(record)
    assert "E004" not in outcome["error_codes"]


# ---------- E005 ----------

def test_e005_invalid_period_format():
    record = {"bank_code": "010", "period": "1405-03", "account_code": "A1",
              "debit": "100", "credit": "40", "balance": "60"}
    outcome, _ = _run_one(record)
    assert "E005" in outcome["error_codes"]


def test_e005_valid_period_passes():
    record = {"bank_code": "010", "period": "1405/12", "account_code": "A1",
              "debit": "100.00", "credit": "40.00", "balance": "60.00"}
    outcome, _ = _run_one(record)
    assert "E005" not in outcome["error_codes"]


# ---------- E006 ----------

def test_e006_duplicate_record():
    record = {"bank_code": "010", "period": "1405/03", "account_code": "A1",
              "debit": "100.00", "credit": "40.00", "balance": "60.00",
              "record_id": "R1"}
    result = validate_batch([record, record], allowed_bank_codes=ALLOWED)
    outcomes = result["outcomes"]
    assert outcomes[1]["error_codes"] == ["E006"] or "E006" in outcomes[1]["error_codes"]
    assert result["summary"]["duplicates"] == 1


# ---------- E007 ----------

def test_e007_balance_mismatch():
    record = {"bank_code": "010", "period": "1405/03", "account_code": "A1",
              "debit": "100.00", "credit": "40.00", "balance": "999.00"}
    outcome, _ = _run_one(record)
    assert "E007" in outcome["error_codes"]


def test_e007_exact_decimal_match():
    record = {"bank_code": "010", "period": "1405/03", "account_code": "A1",
              "debit": "0.10", "credit": "0.05", "balance": "0.05"}
    outcome, _ = _run_one(record)
    assert "E007" not in outcome["error_codes"]


# ---------- E008 ----------

def test_e008_negative_amount():
    record = {"bank_code": "010", "period": "1405/03", "account_code": "A1",
              "debit": "-10", "credit": "5", "balance": "15"}
    outcome, _ = _run_one(record)
    assert "E008" in outcome["error_codes"]


# ---------- E011 ----------

def test_e011_invalid_currency():
    record = {"bank_code": "010", "period": "1405/03", "account_code": "A1",
              "debit": "10", "credit": "5", "balance": "5",
              "currency": "XYZ"}
    outcome, _ = _run_one(record)
    assert "E011" in outcome["error_codes"]


def test_e011_absent_currency_is_ok():
    record = {"bank_code": "010", "period": "1405/03", "account_code": "A1",
              "debit": "10.00", "credit": "5.00", "balance": "5.00"}
    outcome, _ = _run_one(record)
    assert "E011" not in outcome["error_codes"]


# ---------- Full valid ----------

def test_valid_record_no_errors():
    record = {"bank_code": "010", "period": "1405/03", "account_code": "A1",
              "debit": "100.00", "credit": "40.00", "balance": "60.00",
              "currency": "IRR", "record_id": "R1"}
    outcome, summary = _run_one(record)
    assert outcome["is_valid"] is True
    assert outcome["error_codes"] == []
    assert summary["valid"] == 1
    assert summary["invalid"] == 0


# ---------- Batch summary ----------

def test_batch_summary_counts():
    records = [
        {"bank_code": "010", "period": "1405/03", "account_code": "A1",
         "debit": "100.00", "credit": "40.00", "balance": "60.00"},
        {"bank_code": "999", "period": "1405/03", "account_code": "A2",
         "debit": "100.00", "credit": "40.00", "balance": "60.00"},
        {"bank_code": "010", "period": "1405-03", "account_code": "A3",
         "debit": "10.00", "credit": "0.00", "balance": "10.00"},
    ]
    result = validate_batch(records, allowed_bank_codes=ALLOWED)
    s = result["summary"]
    assert s["total"] == 3
    assert s["valid"] == 1
    assert s["invalid"] == 2
    assert s["errors_by_code"].get("E004") == 1
    assert s["errors_by_code"].get("E005") == 1