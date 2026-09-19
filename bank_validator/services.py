import uuid
import pandas as pd
from django.db import transaction

from bank_validator.models import ValidationRun, BankRecord
from bank_validator.readers import read_csv, read_json
from bank_validator.validators import build_summary, validate_dataframe




def process_csv(source, file_name=""):
    df = read_csv(source)
    return _process(df, file_name)


def process_json(source, file_name):
    df = read_json(source)
    return _process(df, file_name)

def _process(df, file_name):
    run_id = uuid.uuid4()

    with transaction.atomic():
        _save_run(df, run_id, file_name)
        _save_records(df, run_id)