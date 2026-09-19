from django.test import TestCase

from bank_validator.validators import (
    check_required_fields,
    check_string_types,
    check_numeric_types,
    check_nulls_or_empty,
    check_bank_code,
    check_period,
    check_balance_consistency,
    validate_record,
    ALLOWED_BANK_CODES,
    validate_dataframe,
    check_duplicates,
    build_summary,
    ERROR_CODES
)

from bank_validator.readers import read_json, read_csv

from persiantools.jdatetime import JalaliDate
import pandas as pd
import io
import json

def _valid_record():
    return {
        "bank_code": "101",
        "period": "1405/06",
        "account_code": "12345",
        "debit": 1000,
        "credit": 400,
        "balance": 600,
    }


class CheckRequiredFieldsTests(TestCase):
    def test_all_fields_present_returns_none(self):
        record = _valid_record()
        self.assertIsNone(check_required_fields(record))


    def test_missing_one_field_returns_e001(self):
        record = _valid_record()
        del record["debit"]
        self.assertEqual(check_required_fields(record), "E001")


    def test_missing_multiple_fields_returns_e001(self):
        record = _valid_record()
        del record["debit"]
        del record["credit"]
        self.assertEqual(check_required_fields(record), "E001")


    def test_empty_record_returns_e001(self):
        self.assertEqual(check_required_fields({}), "E001")


class CheckStringTypesTests(TestCase):
    def test_all_fields_ok(self):
        record = _valid_record()
        self.assertIsNone(check_string_types(record))

    def test_bank_code_not_string(self):
        record = _valid_record()
        record["bank_code"] = 101
        self.assertEqual(check_string_types(record), "E002")

    def test_period_not_string(self):
        record = _valid_record()
        record["period"] = 140506
        self.assertEqual(check_string_types(record), "E002")

    def test_account_code_not_string(self):
        record = _valid_record()
        record["account_code"] = 12345
        self.assertEqual(check_string_types(record), "E002")

    def test_multiple_wrong_types_returns_one_code(self):
        record = _valid_record()
        record["bank_code"] = 101
        record["period"] = 140506
        self.assertEqual(check_string_types(record), "E002")

    def test_missing_field_is_skipped(self):
        record = _valid_record()
        del record["bank_code"]
        self.assertIsNone(check_string_types(record))

    def test_none_value_is_skipped(self):
        record = _valid_record()
        record["bank_code"] = None
        self.assertIsNone(check_string_types(record))


class CheckNumericTypesTests(TestCase):

    def test_all_fields_ok(self):
        record = _valid_record()
        self.assertIsNone(check_numeric_types(record))

    def test_debit_not_number(self):
        record = _valid_record()
        record["debit"] = "not number"
        self.assertEqual(check_numeric_types(record), "E002")

    def test_credit_not_number(self):
        record = _valid_record()
        record["credit"] = "400"
        self.assertEqual(check_numeric_types(record), "E002")

    def test_balance_not_number(self):
        record = _valid_record()
        record["balance"] = "600"
        self.assertEqual(check_numeric_types(record), "E002")

    def test_bool_is_rejected(self):
        record = _valid_record()
        record["debit"] = True
        self.assertEqual(check_numeric_types(record), "E002")

    def test_whole_float_is_accepted(self):
        record = _valid_record()
        record["debit"] = 1000.0
        record["credit"] = 400.0
        record["balance"] = 600.0
        self.assertIsNone(check_numeric_types(record))

    def test_fractional_float_is_rejected(self):
        record = _valid_record()
        record["debit"] = 1000.5
        self.assertEqual(check_numeric_types(record), "E002")

    def test_multiple_wrong_types_returns_one_code(self):
        record = _valid_record()
        record["debit"] = "1000"
        record["credit"] = "two"
        self.assertEqual(check_numeric_types(record), "E002")

    def test_missing_field_is_skipped(self):
        record = _valid_record()
        del record["debit"]
        self.assertIsNone(check_numeric_types(record))

    def test_none_value_is_skipped(self):
        record = _valid_record()
        record["debit"] = None
        self.assertIsNone(check_numeric_types(record))


class CheckNullsOrEmptyTests(TestCase):

    def test_all_fields_ok(self):
        record = _valid_record()
        self.assertIsNone(check_nulls_or_empty(record))

    def test_none_value_returns_e003(self):
        record = _valid_record()
        record["bank_code"] = None
        self.assertEqual(check_nulls_or_empty(record), "E003")

    def test_empty_string_returns_e003(self):
        record = _valid_record()
        record["bank_code"] = ""
        self.assertEqual(check_nulls_or_empty(record), "E003")

    def test_zero_is_not_empty(self):
        record = _valid_record()
        record["debit"] = 0
        self.assertIsNone(check_nulls_or_empty(record))

    def test_zero_float_is_not_empty(self):
        record = _valid_record()
        record["debit"] = 0.0
        self.assertIsNone(check_nulls_or_empty(record))

    def test_multiple_empty_fields_returns_one_code(self):
        record = _valid_record()
        record["bank_code"] = ""
        record["account_code"] = None
        self.assertEqual(check_nulls_or_empty(record), "E003")

    def test_missing_field_is_skipped(self):
        record = _valid_record()
        del record["bank_code"]
        self.assertIsNone(check_nulls_or_empty(record))


class CheckBankCodeTests(TestCase):

    def test_valid_bank_code(self):
        record = _valid_record()
        self.assertIsNone(check_bank_code(record))

    def test_unknown_bank_code(self):
        record = _valid_record()
        record["bank_code"] = "999"
        self.assertEqual(check_bank_code(record), "E004")

    def test_each_allowed_bank_code_passes(self):
        for code in ALLOWED_BANK_CODES:
            record = _valid_record()
            record["bank_code"] = code
            self.assertIsNone(check_bank_code(record))

    def test_missing_field_is_skipped(self):
        record = _valid_record()
        del record["bank_code"]
        self.assertIsNone(check_bank_code(record))

    def test_none_value_is_skipped(self):
        record = _valid_record()
        record["bank_code"] = None
        self.assertIsNone(check_bank_code(record))

    def test_non_string_bank_code(self):
        record = _valid_record()
        record["bank_code"] = 101
        self.assertEqual(check_bank_code(record), "E004")


class CheckPeriodTests(TestCase):

    jalali_now = JalaliDate.today()

    def test_valid_period_current_month(self):
        record = _valid_record()
        record["period"] = "1405/06"
        self.assertIsNone(check_period(record, self.jalali_now))

    def test_valid_period_past_same_year(self):
        record = _valid_record()
        record["period"] = "1405/03"
        self.assertIsNone(check_period(record, self.jalali_now))

    def test_valid_period_past_year(self):
        record = _valid_record()
        record["period"] = "1404/12"
        self.assertIsNone(check_period(record, self.jalali_now))

    def test_bad_format_short_month(self):
        record = _valid_record()
        record["period"] = "1405/6"
        self.assertEqual(check_period(record, self.jalali_now), "E005")

    def test_bad_format_dash(self):
        record = _valid_record()
        record["period"] = "1405-06"
        self.assertEqual(check_period(record, self.jalali_now), "E005")

    def test_bad_format_letters(self):
        record = _valid_record()
        record["period"] = "abcd/ef"
        self.assertEqual(check_period(record, self.jalali_now), "E005")

    def test_bad_format_extra_text(self):
        record = _valid_record()
        record["period"] = "1405/06abc"
        self.assertEqual(check_period(record, self.jalali_now), "E005")

    def test_future_year(self):
        record = _valid_record()
        record["period"] = "1500/01"
        self.assertEqual(check_period(record, self.jalali_now), "E005")

    def test_future_month_same_year(self):
        record = _valid_record()
        record["period"] = "1405/07"
        self.assertEqual(check_period(record, self.jalali_now), "E005")

    def test_too_old(self):
        record = _valid_record()
        record["period"] = "1309/12"
        self.assertEqual(check_period(record, self.jalali_now), "E005")

    def test_missing_key_is_skipped(self):
        record = _valid_record()
        del record["period"]
        self.assertIsNone(check_period(record, self.jalali_now))

    def test_none_value_is_skipped(self):
        record = _valid_record()
        record["period"] = None
        self.assertIsNone(check_period(record, self.jalali_now))

    def test_empty_string_is_skipped(self):
        record = _valid_record()
        record["period"] = ""
        self.assertIsNone(check_period(record, self.jalali_now))


class CheckBalanceConsistencyTests(TestCase):

    def test_balanced_record(self):
        record = _valid_record()
        self.assertIsNone(check_balance_consistency(record))

    def test_balance_mismatch(self):
        record = _valid_record()
        record["balance"] = 999
        self.assertEqual(check_balance_consistency(record), "E006")

    def test_missing_debit_is_skipped(self):
        record = _valid_record()
        del record["debit"]
        self.assertIsNone(check_balance_consistency(record))

    def test_missing_credit_is_skipped(self):
        record = _valid_record()
        del record["credit"]
        self.assertIsNone(check_balance_consistency(record))

    def test_missing_balance_is_skipped(self):
        record = _valid_record()
        del record["balance"]
        self.assertIsNone(check_balance_consistency(record))

    def test_none_debit_is_skipped(self):
        record = _valid_record()
        record["debit"] = None
        self.assertIsNone(check_balance_consistency(record))

    def test_wrong_type_debit_is_skipped(self):
        record = _valid_record()
        record["debit"] = "1000"
        self.assertIsNone(check_balance_consistency(record))

    def test_bool_debit_is_skipped(self):
        record = _valid_record()
        record["debit"] = True
        self.assertIsNone(check_balance_consistency(record))

    def test_whole_floats_still_balance(self):
        record = _valid_record()
        record["debit"] = 1000.0
        record["credit"] = 400.0
        record["balance"] = 600.0
        self.assertIsNone(check_balance_consistency(record))

    def test_negative_balance_is_ok_if_math_matches(self):
        record = _valid_record()
        record["debit"] = 100
        record["credit"] = 500
        record["balance"] = -400
        self.assertIsNone(check_balance_consistency(record))


class CheckValidateRecordTests(TestCase):

    jalali_now = JalaliDate.today()

    def test_valid_record_returns_empty_list(self):
        record = _valid_record()
        self.assertEqual(validate_record(record, self.jalali_now), [])

    def test_missing_field_returns_e001(self):
        record = _valid_record()
        del record["debit"]
        result = validate_record(record, self.jalali_now)
        self.assertIn("E001", result)

    def test_wrong_string_type_returns_e002(self):
        record = _valid_record()
        record["bank_code"] = 101
        result = validate_record(record, self.jalali_now)
        self.assertIn("E002", result)

    def test_wrong_numeric_type_returns_e002(self):
        record = _valid_record()
        record["debit"] = "1000"
        result = validate_record(record, self.jalali_now)
        self.assertIn("E002", result)

    def test_both_type_failures_return_single_e002(self):
        record = _valid_record()
        record["bank_code"] = 101
        record["debit"] = "1000"
        result = validate_record(record, self.jalali_now)
        self.assertEqual(result.count("E002"), 1)

    def test_none_value_returns_e003(self):
        record = _valid_record()
        record["bank_code"] = None
        result = validate_record(record, self.jalali_now)
        self.assertIn("E003", result)

    def test_unknown_bank_code_returns_e004(self):
        record = _valid_record()
        record["bank_code"] = "999"
        result = validate_record(record, self.jalali_now)
        self.assertIn("E004", result)

    def test_bad_period_returns_e005(self):
        record = _valid_record()
        record["period"] = "1405/07"
        result = validate_record(record, self.jalali_now)
        self.assertIn("E005", result)

    def test_balance_mismatch_returns_e006(self):
        record = _valid_record()
        record["balance"] = 999
        result = validate_record(record, self.jalali_now)
        self.assertIn("E006", result)

    def test_multiple_errors_all_returned(self):
        record = _valid_record()
        record["bank_code"] = "999"
        record["balance"] = 999
        result = validate_record(record, self.jalali_now)
        self.assertIn("E004", result)
        self.assertIn("E006", result)

    def test_missing_debit_does_not_trigger_e006(self):
        record = _valid_record()
        del record["debit"]
        result = validate_record(record, self.jalali_now)
        self.assertIn("E001", result)
        self.assertNotIn("E006", result)

    def test_missing_field_does_not_trigger_e005(self):
        record = _valid_record()
        del record["period"]
        result = validate_record(record, self.jalali_now)
        self.assertIn("E001", result)
        self.assertNotIn("E005", result)


class ValidateDataframeTests(TestCase):

    def _valid_records(self):
        return [
            {"bank_code": "101", "period": "1405/03", "account_code": "12", "debit": 1000, "credit": 400, "balance": 600},
            {"bank_code": "002", "period": "1405/03", "account_code": "13", "debit": 500, "credit": 200, "balance": 300},
        ]

    def test_all_valid_rows(self):
        df = pd.DataFrame(self._valid_records())
        result = validate_dataframe(df)

        self.assertEqual(result["valid"].tolist(), [True, True])
        self.assertEqual(result["errors"].tolist(), [[], []])

    def test_one_invalid_row(self):
        records = self._valid_records()
        records[1]["bank_code"] = "999"
        df = pd.DataFrame(records)
        result = validate_dataframe(df)

        self.assertEqual(result["valid"].tolist(), [True, False])
        self.assertIn("E004", result["errors"].iloc[1])

    def test_errors_aligned_with_rows(self):
        records = self._valid_records()
        records[0]["balance"] = 999
        df = pd.DataFrame(records)
        result = validate_dataframe(df)

        self.assertIn("E006", result["errors"].iloc[0])
        self.assertEqual(result["errors"].iloc[1], [])

    def test_nan_becomes_none(self):
        records = self._valid_records()
        records[0]["bank_code"] = None
        df = pd.DataFrame(records)
        result = validate_dataframe(df)

        self.assertFalse(result["valid"].iloc[0])
        self.assertIn("E003", result["errors"].iloc[0])

    def test_multiple_errors_on_same_row(self):
        records = self._valid_records()
        records[0]["bank_code"] = "999"
        records[0]["balance"] = 999
        df = pd.DataFrame(records)
        result = validate_dataframe(df)

        codes = result["errors"].iloc[0]
        self.assertIn("E004", codes)
        self.assertIn("E006", codes)

    def test_duplicate_rows_get_e007(self):
        records = self._valid_records()
        records.append(dict(records[0]))
        df = pd.DataFrame(records)
        result = validate_dataframe(df)

        self.assertIn("E007", result["errors"].iloc[0])
        self.assertIn("E007", result["errors"].iloc[2])
        self.assertNotIn("E007", result["errors"].iloc[1])

    def test_all_copies_of_duplicate_flagged(self):
        records = self._valid_records()
        records.append(dict(records[0]))
        records.append(dict(records[0]))
        df = pd.DataFrame(records)
        result = validate_dataframe(df)

        self.assertIn("E007", result["errors"].iloc[0])
        self.assertIn("E007", result["errors"].iloc[1 + 1])
        self.assertIn("E007", result["errors"].iloc[3])

    def test_duplicate_rows_are_invalid(self):
        records = self._valid_records()
        records.append(dict(records[0]))
        df = pd.DataFrame(records)
        result = validate_dataframe(df)

        self.assertFalse(result["valid"].iloc[0])
        self.assertFalse(result["valid"].iloc[2])
        self.assertTrue(result["valid"].iloc[1])

    def test_no_duplicates_no_e007(self):
        df = pd.DataFrame(self._valid_records())
        result = validate_dataframe(df)

        for codes in result["errors"]:
            self.assertNotIn("E007", codes)

    def test_duplicate_with_other_errors_has_both(self):
        records = self._valid_records()
        records[0]["bank_code"] = "999"
        records.append(dict(records[0]))
        df = pd.DataFrame(records)
        result = validate_dataframe(df)

        codes = result["errors"].iloc[0]
        self.assertIn("E004", codes)
        self.assertIn("E007", codes)

    def test_different_period_not_duplicate(self):
        records = self._valid_records()
        records[1]["bank_code"] = records[0]["bank_code"]
        records[1]["account_code"] = records[0]["account_code"]
        records[1]["period"] = "1405/04"
        df = pd.DataFrame(records)
        result = validate_dataframe(df)

        self.assertNotIn("E007", result["errors"].iloc[0])
        self.assertNotIn("E007", result["errors"].iloc[1])


class CheckDuplicatesTests(TestCase):

    def _df(self, records):
        return pd.DataFrame(records)

    def test_no_duplicates(self):
        df = self._df([
            {"bank_code": "101", "account_code": "A1", "period": "1405/03"},
            {"bank_code": "202", "account_code": "A2", "period": "1405/03"},
        ])
        self.assertEqual(check_duplicates(df), set())

    def test_one_duplicate_pair(self):
        df = self._df([
            {"bank_code": "101", "account_code": "A1", "period": "1405/03"},
            {"bank_code": "202", "account_code": "A2", "period": "1405/03"},
            {"bank_code": "101", "account_code": "A1", "period": "1405/03"},
        ])
        self.assertEqual(check_duplicates(df), {0, 2})

    def test_three_copies(self):
        df = self._df([
            {"bank_code": "101", "account_code": "A1", "period": "1405/03"},
            {"bank_code": "101", "account_code": "A1", "period": "1405/03"},
            {"bank_code": "101", "account_code": "A1", "period": "1405/03"},
        ])
        self.assertEqual(check_duplicates(df), {0, 1, 2})

    def test_two_separate_groups(self):
        df = self._df([
            {"bank_code": "101", "account_code": "A1", "period": "1405/03"},
            {"bank_code": "202", "account_code": "A2", "period": "1405/03"},
            {"bank_code": "101", "account_code": "A1", "period": "1405/03"},
            {"bank_code": "202", "account_code": "A2", "period": "1405/03"},
        ])
        self.assertEqual(check_duplicates(df), {0, 2, 1, 3})

    def test_unique_row_not_flagged(self):
        df = self._df([
            {"bank_code": "101", "account_code": "A1", "period": "1405/03"},
            {"bank_code": "101", "account_code": "A1", "period": "1405/03"},
            {"bank_code": "303", "account_code": "A3", "period": "1405/03"},
        ])
        self.assertEqual(check_duplicates(df), {0, 1})

    def test_type_normalization(self):
        df = self._df([
            {"bank_code": 101, "account_code": "A1", "period": "1405/03"},
            {"bank_code": "101", "account_code": "A1", "period": "1405/03"},
        ])
        self.assertEqual(check_duplicates(df), {0, 1})

    def test_missing_key_part_is_skipped(self):
        df = self._df([
            {"bank_code": None, "account_code": "A1", "period": "1405/03"},
            {"bank_code": None, "account_code": "A1", "period": "1405/03"},
        ])
        self.assertEqual(check_duplicates(df), set())

    def test_empty_string_key_part_is_skipped(self):
        df = self._df([
            {"bank_code": "", "account_code": "A1", "period": "1405/03"},
            {"bank_code": "", "account_code": "A1", "period": "1405/03"},
        ])
        self.assertEqual(check_duplicates(df), set())

    def test_different_period_not_duplicate(self):
        df = self._df([
            {"bank_code": "101", "account_code": "A1", "period": "1405/03"},
            {"bank_code": "101", "account_code": "A1", "period": "1405/04"},
        ])
        self.assertEqual(check_duplicates(df), set())


class BuildSummaryTests(TestCase):

    def _df(self, records):
        return pd.DataFrame(records)

    def _valid_row(self, **overrides):
        row = {
            "bank_code": "101",
            "period": "1405/03",
            "account_code": "A1",
            "debit": 1000,
            "credit": 400,
            "balance": 600,
            "errors": [],
            "valid": True,
        }
        row.update(overrides)
        return row

    def test_all_valid(self):
        df = self._df([
            self._valid_row(account_code="A1"),
            self._valid_row(account_code="A2"),
        ])
        summary = build_summary(df)

        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["valid"], 2)
        self.assertEqual(summary["invalid"], 0)
        self.assertEqual(summary["errors_by_code"]["E007"], 0)

    def test_one_invalid_row(self):
        df = self._df([
            self._valid_row(account_code="A1"),
            self._valid_row(account_code="A2", errors=["E004"], valid=False),
        ])
        summary = build_summary(df)

        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["valid"], 1)
        self.assertEqual(summary["invalid"], 1)
        self.assertEqual(summary["errors_by_code"]["E004"], 1)

    def test_multiple_errors_on_same_row(self):
        df = self._df([
            self._valid_row(errors=["E004", "E006"], valid=False),
        ])
        summary = build_summary(df)

        self.assertEqual(summary["errors_by_code"]["E004"], 1)
        self.assertEqual(summary["errors_by_code"]["E006"], 1)

    def test_duplicates_counted(self):
        df = self._df([
            self._valid_row(errors=["E007"], valid=False),
            self._valid_row(errors=["E007"], valid=False),
            self._valid_row(account_code="A2"),
        ])
        summary = build_summary(df)

        self.assertEqual(summary["errors_by_code"]["E007"], 2)
        self.assertEqual(summary["valid"], 1)
        self.assertEqual(summary["invalid"], 2)

    def test_all_codes_present_even_if_zero(self):
        df = self._df([
            self._valid_row(),
        ])
        summary = build_summary(df)

        for code in ERROR_CODES:
            self.assertIn(code, summary["errors_by_code"])
            self.assertEqual(summary["errors_by_code"][code], 0)

    def test_empty_dataframe(self):
        df = self._df([])
        df["errors"] = []
        df["valid"] = []

        summary = build_summary(df)

        self.assertEqual(summary["total"], 0)
        self.assertEqual(summary["valid"], 0)
        self.assertEqual(summary["invalid"], 0)
        self.assertEqual(summary["errors_by_code"]["E007"], 0)

    def test_mixed_run(self):
        df = self._df([
            self._valid_row(account_code="A1"),
            self._valid_row(account_code="A2", errors=["E004"], valid=False),
            self._valid_row(account_code="A3", errors=["E002", "E005"], valid=False),
            self._valid_row(account_code="A4", errors=["E007"], valid=False),
        ])
        summary = build_summary(df)

        self.assertEqual(summary["total"], 4)
        self.assertEqual(summary["valid"], 1)
        self.assertEqual(summary["invalid"], 3)
        self.assertEqual(summary["errors_by_code"]["E002"], 1)
        self.assertEqual(summary["errors_by_code"]["E004"], 1)
        self.assertEqual(summary["errors_by_code"]["E005"], 1)
        self.assertEqual(summary["errors_by_code"]["E007"], 1)


class ReadCsvTests(TestCase):

    def _csv_text(self):
        return (
            "bank_code,period,account_code,debit,credit,balance\n"
            '101,1405/03,A1,1000,400,600\n'
            '202,1405/03,A2,500,200,300\n'
        )

    def _csv_file(self):
        return io.StringIO(self._csv_text())

    def test_valid_csv_file(self):
        df = read_csv(self._csv_file())

        self.assertEqual(len(df), 2)
        self.assertIn("bank_code", df.columns)
        self.assertIn("period", df.columns)
        self.assertIn("account_code", df.columns)
        self.assertIn("debit", df.columns)
        self.assertIn("credit", df.columns)
        self.assertIn("balance", df.columns)


    def test_empty_csv_raises(self):
        empty = io.StringIO("")
        with self.assertRaises(ValueError):
            read_csv(empty)


class ReadJsonTests(TestCase):

    def _json_list_text(self):
        return json.dumps([
            {"bank_code": "101", "period": "1405/03", "account_code": "A1", "debit": 1000, "credit": 400, "balance": 600},
            {"bank_code": "202", "period": "1405/03", "account_code": "A2", "debit": 500, "credit": 200, "balance": 300},
        ])

    def _json_object_text(self):
        return json.dumps({
            "bank_code": "101", "period": "1405/03", "account_code": "A1",
            "debit": 1000, "credit": 400, "balance": 600,
        })

    def test_valid_json_list_string(self):
        df = read_json(self._json_list_text())

        self.assertEqual(len(df), 2)
        self.assertIn("bank_code", df.columns)
        self.assertIn("period", df.columns)
        self.assertIn("account_code", df.columns)
        self.assertIn("debit", df.columns)
        self.assertIn("credit", df.columns)
        self.assertIn("balance", df.columns)

    def test_valid_json_single_object_string(self):
        df = read_json(self._json_object_text())

        self.assertEqual(len(df), 1)
        self.assertIn("bank_code", df.columns)

    def test_valid_json_file(self):
        file = io.StringIO(self._json_list_text())
        df = read_json(file)

        self.assertEqual(len(df), 2)

    def test_malformed_json_raises(self):
        bad = io.StringIO("{not valid json")
        with self.assertRaises(ValueError):
            read_json(bad)

    def test_json_not_list_or_dict_raises(self):
        with self.assertRaises(ValueError):
            read_json(json.dumps("just a string"))

    def test_empty_json_list_raises(self):
        with self.assertRaises(ValueError):
            read_json("[]")