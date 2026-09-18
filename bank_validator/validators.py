import re

from persiantools.jdatetime import JalaliDate

import pandas as pd
# jalali_now = JalaliDate.today()

REQUIRED_FIELDS = ["bank_code", "period", "account_code", "debit", "credit", "balance"]
STRING_FIELDS = ["bank_code", "period", "account_code"]
NUMERIC_FIELDS = ["debit", "credit", "balance"]
PERIOD_PATTERN = re.compile(r"\d{4}/\d{2}") # YYYY/MM
ALLOWED_BANK_CODES = ["101", "002", "305", "112", "310"]


def check_required_fields(record):
    return "E001" if any(field not in record for field in REQUIRED_FIELDS) else None


def check_string_types(record):
    for field in STRING_FIELDS:
        if field in record and record[field] is not None:
            if not isinstance(record[field], str):
                return "E002"
    return None


def check_numeric_types(record):
    for field in NUMERIC_FIELDS:
        if field in record and record[field] is not None:
            is_valid_num = _is_valid_number(record[field])
            if not is_valid_num:
                return "E002"
    return None


def check_nulls_or_empty(record):
    for field in REQUIRED_FIELDS:
        if field in record:
            if record[field] is None or record[field]=="":
                return "E003"
    return None


def check_bank_code(record):
    if "bank_code" in record and record["bank_code"] is not None:
        if record["bank_code"] not in ALLOWED_BANK_CODES:
            return "E004"
    return None


def check_period(record, jalali_now):
    if "period" in record and record["period"] is not None and record["period"] != "":
        # check format
        if not PERIOD_PATTERN.fullmatch(record["period"]):
            return "E005"

        # check the year and month not from future or far past
        year, month = map(int, record["period"].split("/"))
        if year > jalali_now.year or (year == jalali_now.year and month > jalali_now.month):
            return "E005"
        if year < 1310:
            return "E005"
    return None


def check_balance_consistency(record):
    for field in NUMERIC_FIELDS:
        if field not in record or record[field] is None:
            return None
    type_check = (
            _is_valid_number(record["balance"])
            and _is_valid_number(record["credit"])
            and _is_valid_number(record["debit"])
    )

    if type_check:
        if record["balance"] != record["debit"] - record["credit"]:
            return "E006"
    return None


def validate_record(record, jalali_now):
    errors = []

    for result in (
        check_required_fields(record),
        check_string_types(record),
        check_numeric_types(record),
        check_nulls_or_empty(record),
        check_bank_code(record),
        check_period(record, jalali_now),
        check_balance_consistency(record),
    ):
        if result is not None and result not in errors:
            errors.append(result)

    return errors


def validate_dataframe(df):
    jalali_now = JalaliDate.today()

    errors_column = []
    duplicate_indexes = check_duplicates(df)

    for _, row in df.iterrows():  # _ -> index that we don't want
        record = row.to_dict()
        # converting NaN from pandas to None (python)
        for key in record:
            if pd.isna(record[key]):
                record[key] = None

        errors_column.append(validate_record(record, jalali_now))
    df["errors"] = errors_column

    for index in duplicate_indexes:
        df.at[index, "errors"].append("E007")


    valid_column = []
    for codes in df["errors"]:
        valid_column.append(len(codes) == 0)
    df["valid"] = valid_column

    return df


# batch_validator
def check_duplicates(df):
    # logic looks like the group by
    # {key:("bank_code", "account_code", "period")- > tuple, value:[list of rows that are duplicate of this row]}
    groups = {}

    for index, row in df.iterrows():
        # building key's in dict
        key = (str(row.get("bank_code")), str(row.get("account_code")), str(row.get("period")))
        # check if
        if "None" in key or "" in key or "nan" in key:
            continue
        # creating the dict if its new it comes with the index
        # if it is duplicate the index list of the first accrue will be updated
        if key in groups:
            groups[key].append(index)
        else:
            groups[key] = [index]

    # adding the index of the duplicate rows in a set
    duplicate_indices = set()
    for indices in groups.values():
        if len(indices) > 1:
            duplicate_indices.update(indices)

    return duplicate_indices


# helper function
def _is_valid_number(value):
    is_int = isinstance(value, int) and not isinstance(value, bool)
    is_whole_float = isinstance(value, float) and value.is_integer()
    return is_int or is_whole_float