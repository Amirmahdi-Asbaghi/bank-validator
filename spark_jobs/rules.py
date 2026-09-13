"""Spark DataFrame implementations of the validation rules.

Mirrors apps/validation/rules/ (pandas path). Same error codes, same semantics.

Design note: instead of chaining array_union per rule (which creates huge
generated code and blows up the JVM driver heap), we compute each rule as a
boolean column, then assemble the error arrays in ONE pass.
"""
from __future__ import annotations

from pyspark.sql import DataFrame, functions as F
from pyspark.sql.types import DecimalType

MONEY = DecimalType(18, 2)

REQUIRED_FIELDS = ("bank_code", "period", "account_code", "debit", "credit", "balance")
NON_EMPTY_FIELDS = ("bank_code", "period", "account_code")
NUMERIC_FIELDS = ("debit", "credit", "balance")

ISO_4217 = [
    "IRR", "USD", "EUR", "GBP", "AED", "SAR", "JPY", "CNY",
    "CHF", "CAD", "AUD", "INR", "TRY", "RUB", "KWD", "QAR", "OMR", "BHD",
]

RULES = [
    # (error_code, message, condition_column_name)
    ("E001", "Required field is missing", "r_e001"),
    ("E002", "Invalid numeric value", "r_e002"),
    ("E003", "Required field is empty", "r_e003"),
    ("E004", "Bank code is not in the allow-list", "r_e004"),
    ("E005", "Period format invalid (expected YYYY/MM)", "r_e005"),
    ("E007", "Balance does not equal debit - credit", "r_e007"),
    ("E008", "Debit or credit is negative", "r_e008"),
    ("E011", "Currency is not a valid ISO 4217 code", "r_e011"),
]


def apply_all(df: DataFrame, allowed_bank_codes: list[str]) -> DataFrame:
    """Apply every rule as a boolean column; then collect errors in one pass."""

    # Ensure required columns exist
    for f in REQUIRED_FIELDS:
        if f not in df.columns:
            df = df.withColumn(f, F.lit(None))

    # E001 — required field missing
    df = df.withColumn(
        "r_e001",
        F.lit(False)
        | F.col("bank_code").isNull()
        | F.col("period").isNull()
        | F.col("account_code").isNull()
        | F.col("debit").isNull()
        | F.col("credit").isNull()
        | F.col("balance").isNull(),
    )

    # E003 — null/empty required string fields
    df = df.withColumn(
        "r_e003",
        (F.trim(F.col("bank_code").cast("string")) == "")
        | (F.trim(F.col("period").cast("string")) == "")
        | (F.trim(F.col("account_code").cast("string")) == ""),
    )

    # E002 — non-numeric values in numeric fields
    numeric_regex = r"^-?\d+(\.\d+)?$"
    df = df.withColumn(
        "r_e002",
        (F.col("debit").isNotNull() & ~F.col("debit").cast("string").rlike(numeric_regex))
        | (F.col("credit").isNotNull() & ~F.col("credit").cast("string").rlike(numeric_regex))
        | (F.col("balance").isNotNull() & ~F.col("balance").cast("string").rlike(numeric_regex)),
    )

    # Cast numerics to Decimal
    for f in NUMERIC_FIELDS:
        df = df.withColumn(f, F.col(f).cast(MONEY))

    # E004 — bank code not in allow-list
    df = df.withColumn(
        "r_e004",
        F.col("bank_code").isNotNull() & ~F.col("bank_code").isin(allowed_bank_codes),
    )

    # E005 — period format
    df = df.withColumn(
        "r_e005",
        F.col("period").isNotNull() & ~F.col("period").rlike(r"^\d{4}/(0[1-9]|1[0-2])$"),
    )

    # E008 — negative amounts
    df = df.withColumn(
        "r_e008",
        (F.col("debit") < 0) | (F.col("credit") < 0),
    )

    # E007 — balance mismatch
    df = df.withColumn(
        "r_e007",
        F.col("balance").isNotNull()
        & F.col("debit").isNotNull()
        & F.col("credit").isNotNull()
        & (F.col("balance") != (F.col("debit") - F.col("credit"))),
    )

    # E011 — invalid currency
    df = df.withColumn(
        "r_e011",
        F.col("currency").isNotNull()
        & (F.col("currency") != "")
        & ~F.upper(F.col("currency")).isin(ISO_4217),
    )

    # Collect error codes + messages in ONE expression
    code_arrays = [
        F.when(F.col(cond_col), F.array(F.lit(code))).otherwise(F.array().cast("array<string>"))
        for code, _msg, cond_col in RULES
    ]
    msg_arrays = [
        F.when(F.col(cond_col), F.array(F.lit(msg))).otherwise(F.array().cast("array<string>"))
        for _code, msg, cond_col in RULES
    ]

    df = (
        df.withColumn("error_codes", F.flatten(F.array(*code_arrays)))
          .withColumn("error_messages", F.flatten(F.array(*msg_arrays)))
    )

    # Drop intermediate boolean columns
    for _code, _msg, cond_col in RULES:
        df = df.drop(cond_col)

    return df


def split_valid_invalid(df: DataFrame) -> tuple[DataFrame, DataFrame]:
    """Split into (valid, invalid) based on error_codes emptiness."""
    is_valid = F.size(F.col("error_codes")) == 0
    return df.filter(is_valid), df.filter(~is_valid)