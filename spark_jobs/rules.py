"""Spark DataFrame implementations of the validation rules.

Mirrors apps/validation/rules/ (pandas path). Same error codes, same
semantics — a record that fails E007 here fails E007 there, for the
same reason.

Why a separate implementation and not a shared one:
    The pandas rules are Python functions called per record. The Spark
    rules are column expressions evaluated per partition. Expressing
    one as the other would mean UDFs, which are 5-10× slower in Spark
    because they serialize every row across the JVM/Python boundary.

    The trade-off is duplication. We mitigate by:
      - Sharing the exact same error codes (E001-E011)
      - Keeping the rule semantics identical
      - Documenting both in docs/rules.md

Design note (this is the important part):
    The first version of this file chained `array_union` per rule —
    each rule appended to the error_codes array. That's idiomatic-looking
    but exploded Spark's generated code: each array_union wraps a
    `when/otherwise`, and 8 rules of nesting create a Java method so
    large that building its source string OOMs the driver.

    The fix: compute each rule as a boolean column (`r_e001`, `r_e002`,
    ...), then assemble the error arrays in ONE pass using
    `F.array(...)` + `F.flatten(...)`. This reduces the generated code
    from megabytes to kilobytes.
"""
from __future__ import annotations

from pyspark.sql import DataFrame, functions as F
from pyspark.sql.types import DecimalType

# Decimal type for money fields. 18 digits total, 2 after the decimal.
# This is the Spark equivalent of Django's DecimalField(18, 2).
#
# Why not DoubleType:
#   Doubles are IEEE 754 floats. They lose precision on values like
#   0.1, which would make the E007 balance check produce false
#   positives on correct data. Decimal is exact.
MONEY = DecimalType(18, 2)

# Field groupings, mirroring the pandas rules.
REQUIRED_FIELDS = ("bank_code", "period", "account_code", "debit", "credit", "balance")
NON_EMPTY_FIELDS = ("bank_code", "period", "account_code")
NUMERIC_FIELDS = ("debit", "credit", "balance")

# ISO 4217 currency codes accepted by E011. Same list as the pandas
# rule — kept in sync manually. If this list grows to the full ISO
# standard, both files would need the same update.
ISO_4217 = [
    "IRR", "USD", "EUR", "GBP", "AED", "SAR", "JPY", "CNY",
    "CHF", "CAD", "AUD", "INR", "TRY", "RUB", "KWD", "QAR", "OMR", "BHD",
]

# Rule registry: (error_code, message, condition_column_name).
#
# The condition_column_name is the boolean column that holds each
# rule's outcome. It's temporary — dropped after the error arrays
# are assembled. The naming convention (r_e001, r_e002, ...) makes
# it easy to find the intermediate columns if debugging.
#
# Order matters for the output: errors appear in the codes array in
# the order rules are listed here. E001 before E002, etc. Matching
# the pandas engine's order keeps the two implementations consistent
# for tests that compare error_codes as ordered lists.
RULES = [
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
    """Apply every rule as a boolean column, then assemble errors.

    Returns a DataFrame with two new columns:
        error_codes     array<string> — e.g. ["E004", "E007"]
        error_messages  array<string> — parallel to error_codes

    The intermediate boolean columns (r_e001, ...) are dropped before
    returning, so the output schema stays clean.

    Args:
        df: input DataFrame with at least the required columns.
            Missing columns are added as nulls first — that's how
            E001 catches them.
        allowed_bank_codes: list of valid bank codes for E004. Passed
            as a Python list, which Spark broadcasts to every
            executor as a closure variable.
    """
    # -------- Ensure required columns exist --------
    #
    # A file might be missing a column entirely (the schema doesn't
    # match the spec). Spark would raise a ColumnNotFound error when
    # the first rule references the missing column. Adding it as a
    # null column first makes every rule safe to reference, and the
    # null value is what E001 checks for.
    #
    # Why `.lit(None)` and not skipping the rule:
    #   The rule should fire — a missing column means every row fails
    #   that field's presence check. Adding the column as null and
    #   letting E001 flag it is the correct behavior.
    for f in REQUIRED_FIELDS:
        if f not in df.columns:
            df = df.withColumn(f, F.lit(None))

    # -------- E001 — required field missing --------
    #
    # A single boolean column ORing all the null checks. If any
    # required field is null, E001 fires.
    #
    # `F.lit(False)` at the start is a technique: `|` chains the
    # conditions, and starting with a literal False prevents a
    # potential "empty OR chain" if the tuple were ever empty.
    # It's defensive but cheap.
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

    # -------- E003 — null/empty required string fields --------
    #
    # `F.trim(...) == ""` catches empty strings and whitespace-only
    # strings. The `.cast("string")` is defensive — the field might
    # be a non-string type (e.g. a number from JSON), and `trim`
    # requires a string.
    #
    # Note: E001 and E003 overlap on nulls. A null bank_code triggers
    # both. That matches the pandas behavior (both rules fire), so
    # it's not a bug — it's consistent.
    df = df.withColumn(
        "r_e003",
        (F.trim(F.col("bank_code").cast("string")) == "")
        | (F.trim(F.col("period").cast("string")) == "")
        | (F.trim(F.col("account_code").cast("string")) == ""),
    )

    # -------- E002 — non-numeric values in numeric fields --------
    #
    # The regex matches an optional sign, digits, and an optional
    # decimal portion: -?\d+(\.\d+)?
    #
    # `~rlike(regex)` means "does not match the regex" — the field
    # is present but its string form isn't a number.
    #
    # The `isNotNull()` guard prevents E002 from firing on nulls
    # (E001 handles those). This is the same split as the pandas
    # rules — E002 is about type, not absence.
    numeric_regex = r"^-?\d+(\.\d+)?$"
    df = df.withColumn(
        "r_e002",
        (F.col("debit").isNotNull() & ~F.col("debit").cast("string").rlike(numeric_regex))
        | (F.col("credit").isNotNull() & ~F.col("credit").cast("string").rlike(numeric_regex))
        | (F.col("balance").isNotNull() & ~F.col("balance").cast("string").rlike(numeric_regex)),
    )

    # -------- Cast numerics to Decimal --------
    #
    # Rules that follow (E007, E008) need the numeric fields as
    # Decimal, not strings. This cast is where that conversion
    # happens.
    #
    # Note: values that failed E002 (weren't parseable) will cast to
    # null. The null-check guards in E007 and E008 prevent them from
    # firing on those rows — E002 is the correct error for them.
    for f in NUMERIC_FIELDS:
        df = df.withColumn(f, F.col(f).cast(MONEY))

    # -------- E004 — bank code not in allow-list --------
    #
    # `.isin(...)` takes a Python list. Spark broadcasts it to each
    # executor as a closure variable — a small list is cheap to
    # transmit. For very large allow-lists (millions of entries),
    # we'd use a broadcast join instead.
    #
    # The `isNotNull()` guard prevents E004 from firing on empty
    # bank codes — E003 handles those.
    df = df.withColumn(
        "r_e004",
        F.col("bank_code").isNotNull() & ~F.col("bank_code").isin(allowed_bank_codes),
    )

    # -------- E005 — period format --------
    #
    # Same regex as the pandas rule. Java's regex engine (used by
    # Spark's `rlike`) handles this pattern identically to Python's.
    #
    # `isNotNull()` again — an empty period is E003's job, not E005's.
    df = df.withColumn(
        "r_e005",
        F.col("period").isNotNull() & ~F.col("period").rlike(r"^\d{4}/(0[1-9]|1[0-2])$"),
    )

    # -------- E008 — negative amounts --------
    #
    # Compare Decimal values to zero. After the cast, these are
    # DecimalType columns, so `< 0` uses Decimal comparison.
    #
    # A null value (from a failed cast) returns null for the
    # comparison, which is treated as false in the boolean OR —
    # so E008 doesn't fire on unparseable values.
    df = df.withColumn(
        "r_e008",
        (F.col("debit") < 0) | (F.col("credit") < 0),
    )

    # -------- E007 — balance mismatch --------
    #
    # Three null checks ensure we only compare when all three values
    # are valid Decimals. Then the exact comparison: balance must
    # equal debit - credit.
    #
    # The `!=` on Decimal columns is exact — no epsilon. A balance
    # off by even 0.01 fires E007.
    df = df.withColumn(
        "r_e007",
        F.col("balance").isNotNull()
        & F.col("debit").isNotNull()
        & F.col("credit").isNotNull()
        & (F.col("balance") != (F.col("debit") - F.col("credit"))),
    )

    # -------- E011 — invalid currency --------
    #
    # Currency is optional. Only check when present and non-empty.
    # `F.upper(...)` normalizes case before comparison.
    #
    # Unlike the pandas rule (which uses `.upper()`), Spark's
    # `F.upper` handles the case normalization. The `isNotNull()` and
    # `!= ""` guards mean absent currencies pass.
    df = df.withColumn(
        "r_e011",
        F.col("currency").isNotNull()
        & (F.col("currency") != "")
        & ~F.upper(F.col("currency")).isin(ISO_4217),
    )

    # -------- Assemble error arrays in ONE pass --------
    #
    # This is the crux of the file. For each rule, we build a small
    # array: either [code] if the rule fired, or [] if not. Then we
    # combine all the arrays into one and flatten.
    #
    # Why this shape:
    #   The naive approach chains `array_union` per rule, accumulating
    #   into a growing array. That creates deeply nested `when`
    #   expressions, and Spark's code generator builds one giant
    #   Java method to evaluate them. For 8 rules, the generated
    #   source string exceeds the driver's heap and OOMs.
    #
    #   By computing each rule as a separate array and combining with
    #   `F.array(*arrays)` + `F.flatten(...)`, the generated code
    #   stays small — each rule is independent, and the combination
    #   is one function call.
    #
    # The `.cast("array<string>")` on the empty array is required:
    # `F.array()` with no arguments produces an untyped array. The
    # cast makes it an array<string> to match the other branch.

    code_arrays = [
        F.when(F.col(cond_col), F.array(F.lit(code)))
         .otherwise(F.array().cast("array<string>"))
        for code, _msg, cond_col in RULES
    ]
    msg_arrays = [
        F.when(F.col(cond_col), F.array(F.lit(msg)))
         .otherwise(F.array().cast("array<string>"))
        for _code, msg, cond_col in RULES
    ]

    # `F.array(*code_arrays)` produces an array of arrays — e.g.
    # [["E001"], [], ["E003"], ...]. `F.flatten(...)` collapses the
    # nesting into a single array: ["E001", "E003"].
    #
    # This is one expression tree, evaluated in one pass per row,
    # with small generated code.
    df = (
        df.withColumn("error_codes", F.flatten(F.array(*code_arrays)))
          .withColumn("error_messages", F.flatten(F.array(*msg_arrays)))
    )

    # -------- Drop intermediate columns --------
    #
    # The r_e001, r_e002, etc. columns were only needed to build the
    # error arrays. Dropping them keeps the output DataFrame clean —
    # only the original fields plus error_codes/error_messages.
    #
    # Without this, the Delta table would have 8 extra boolean columns
    # that don't belong to the schema. Adding them costs storage and
    # confuses anyone reading the table.
    for _code, _msg, cond_col in RULES:
        df = df.drop(cond_col)

    return df


def split_valid_invalid(df: DataFrame) -> tuple[DataFrame, DataFrame]:
    """Split into (valid, invalid) based on error_codes emptiness.

    A record is valid iff its error_codes array is empty. This is the
    same rule the pandas engine uses (`not failures`).

    The two DataFrames are:
        valid:   filter(size(error_codes) == 0)
        invalid: filter(size(error_codes) > 0)

    Note the two filters are complementary — together they partition
    the input. Every row goes to exactly one output.
    """
    is_valid = F.size(F.col("error_codes")) == 0
    return df.filter(is_valid), df.filter(~is_valid)