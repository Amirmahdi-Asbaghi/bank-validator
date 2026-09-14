"""Parity test: pandas and Spark rule engines produce identical results.

This is the highest-value test in the suite. The project has two
rule engines — one in Python (``apps.validation.rules``) used for
small files, one in Spark (``spark_jobs.rules``) used for large
files. They're supposed to have identical semantics.

The risk:
    A change to one engine (adding a rule, tweaking a regex,
    changing a message) without updating the other creates a silent
    divergence. Files processed via the sync path would classify
    differently than the same files via the async path.

How the test works:
    1. Build a small DataFrame with rows that trigger every rule.
    2. Run the pandas engine on the same data.
    3. Compare the ``errors_by_code`` maps.

    If they match, the two engines agree. If they differ, the test
    fails with a diff showing which codes diverged.

Why a Spark test in the integration folder:
    Spark tests need a real SparkSession. That's a heavier
    dependency than a pure Python test — hence "integration". The
    ``pytest-spark`` fixture provides the session.

Running this test:
    By default, ``pytest`` skips it (see ``pytest.ini`` markers).
    To run it explicitly:
        pytest -m spark
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.spark


# ---------------------------------------------------------------------------
# Test data — one row per rule, plus a valid control row.
# ---------------------------------------------------------------------------
#
# Each row is designed to trigger exactly one rule (or a specific
# combination), so the resulting errors_by_code maps have a known
# shape. If the two engines disagree on which codes fire, the diff
# is immediate.
#
# All values are strings — both engines work from string inputs and
# do their own Decimal parsing.

TEST_RECORDS = [
    # Row 1: valid control
    {
        "bank_code": "010",
        "period": "1405/03",
        "account_code": "A1",
        "debit": "100.00",
        "credit": "40.00",
        "balance": "60.00",
        "currency": "IRR",
        "record_id": "R1",
    },
    # Row 2: bad bank code (E004)
    {
        "bank_code": "999",
        "period": "1405/03",
        "account_code": "A2",
        "debit": "100.00",
        "credit": "40.00",
        "balance": "60.00",
        "record_id": "R2",
    },
    # Row 3: bad period (E005) + bad balance (E007)
    {
        "bank_code": "010",
        "period": "1405-03",
        "account_code": "A3",
        "debit": "200.00",
        "credit": "50.00",
        "balance": "999.00",
        "record_id": "R3",
    },
    # Row 4: negative debit (E008)
    {
        "bank_code": "010",
        "period": "1405/03",
        "account_code": "A4",
        "debit": "-10.00",
        "credit": "5.00",
        "balance": "-15.00",
        "record_id": "R4",
    },
    # Row 5: bad currency (E011)
    {
        "bank_code": "010",
        "period": "1405/03",
        "account_code": "A5",
        "debit": "10.00",
        "credit": "5.00",
        "balance": "5.00",
        "currency": "XYZ",
        "record_id": "R5",
    },
    # Row 6: non-numeric debit (E002)
    {
        "bank_code": "010",
        "period": "1405/03",
        "account_code": "A6",
        "debit": "abc",
        "credit": "5.00",
        "balance": "0.00",
        "record_id": "R6",
    },
    # Row 7: empty account_code (E003)
    {
        "bank_code": "010",
        "period": "1405/03",
        "account_code": "",
        "debit": "10.00",
        "credit": "5.00",
        "balance": "5.00",
        "record_id": "R7",
    },
]


ALLOWED_BANK_CODES = ["010", "020", "030"]


# ---------------------------------------------------------------------------
# The parity test
# ---------------------------------------------------------------------------

def test_pandas_and_spark_engines_agree(spark_session):
    """The two rule engines classify the same input identically.

    Compares the ``errors_by_code`` maps produced by each engine.
    A difference means one engine has a rule the other doesn't, or
    a rule's condition differs.

    The test is intentionally coarse — it doesn't compare per-row
    error lists, just the aggregate counts. This catches the common
    case (a rule added to one engine but not the other) without
    being brittle to ordering differences.
    """
    # -------- Run the pandas engine --------
    from apps.validation.rules.engine import validate_batch

    pandas_result = validate_batch(
        TEST_RECORDS,
        allowed_bank_codes=set(ALLOWED_BANK_CODES),
    )
    pandas_errors = pandas_result["summary"]["errors_by_code"]

    # -------- Run the Spark engine --------
    from pyspark.sql import SparkSession, functions as F

    # The Spark rules module lives outside the Django app package —
    # it's in spark_jobs/. Importing it requires the path to be on
    # sys.path, which the app's pytest.ini doesn't do by default.
    #
    # Add it here explicitly for this test.
    import sys
    sys.path.insert(0, "/app/spark_jobs")
    from rules import apply_all, split_valid_invalid

    # Build a Spark DataFrame from the same records. Every column is
    # a string, matching what the CSV reader would produce.
    df = spark_session.createDataFrame(TEST_RECORDS)

    # Apply the Spark rules engine.
    result_df = apply_all(df, ALLOWED_BANK_CODES)

    # Split into valid/invalid, then count errors by code. This
    # mirrors what the streaming validator does when building its
    # ClickHouse summary.
    _, invalid_df = split_valid_invalid(result_df)

    error_rows = (
        invalid_df
        .select(F.explode("error_codes").alias("code"))
        .groupBy("code").count()
        .collect()
    )
    spark_errors = {r["code"]: int(r["count"]) for r in error_rows}

    # -------- Compare --------
    # Both maps should have the same keys (same set of error codes
    # triggered by the test data).
    assert set(pandas_errors.keys()) == set(spark_errors.keys()), (
        f"Error codes differ:\n"
        f"  pandas: {sorted(pandas_errors.keys())}\n"
        f"  spark:  {sorted(spark_errors.keys())}"
    )

    # And the same values (each code triggered by the same number
    # of records).
    for code in pandas_errors:
        assert pandas_errors[code] == spark_errors[code], (
            f"Count mismatch for {code}: "
            f"pandas={pandas_errors[code]}, spark={spark_errors[code]}"
        )


def test_spark_engine_handles_missing_column(spark_session):
    """The Spark engine adds missing required columns as nulls.

    A file that's missing a column entirely should have every row
    flagged with E001 for that field. This tests the "ensure
    columns exist" step at the top of ``apply_all``.
    """
    from pyspark.sql import functions as F
    import sys
    sys.path.insert(0, "/app/spark_jobs")
    from rules import apply_all

    # A record missing the ``balance`` column
    records = [
        {
            "bank_code": "010",
            "period": "1405/03",
            "account_code": "A1",
            "debit": "100.00",
            "credit": "40.00",
            # no balance column
        }
    ]
    df = spark_session.createDataFrame(records)
    result_df = apply_all(df, ALLOWED_BANK_CODES)

    # The result should have the balance column (as null) and E001
    # should fire on it.
    assert "balance" in result_df.columns
    row = result_df.select("error_codes").first()
    assert "E001" in row["error_codes"]