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
    ALLOWED_BANK_CODES
)


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

    def test_bad_float_input_pass(self):
        record = _valid_record()
        record["debit"] = 100.23
        record["credit"] = 80
        record["balance"] = 20.23
        self.assertIsNone(check_balance_consistency(record))


