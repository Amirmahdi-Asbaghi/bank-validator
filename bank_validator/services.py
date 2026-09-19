import uuid
from django.db import transaction

from bank_validator.models import ValidationRun, BankRecord
from bank_validator.readers import read_csv, read_json
from bank_validator.validators import build_summary, validate_dataframe




def process_csv(source, file_name=""):
    df = read_csv(source)
    return _process(df, file_name)


def process_json(source, file_name=""):
    df = read_json(source)
    return _process(df, file_name)

def _process(df, file_name):
    df = validate_dataframe(df)
    summary = build_summary(df)
    run_id = uuid.uuid4()

    with transaction.atomic():
        _save_run(summary, run_id, file_name)
        _save_records(df, run_id)

    return {"run_id": str(run_id), "summary": summary}

def _save_run(summary, run_id, file_name):
    ValidationRun.objects.create(
        run_id=run_id,
        file_name=file_name,
        total=summary["total"],
        valid_count=summary["valid"],
        invalid_count=summary["invalid"],
        errors_by_code=summary["errors_by_code"],
    )


def _save_records(df, run_id):
    instances = []
    for _, row in df.iterrows():
        instances.append(
            BankRecord(
                run_id=run_id,
                bank_code=row["bank_code"],
                period=row["period"],
                account_code=row["account_code"],
                debit=row["debit"],
                credit=row["credit"],
                balance=row["balance"],
                valid=bool(row["valid"]),
                errors=row["errors"],
            )
        )

    # insert all rows at once with this line of code
    BankRecord.objects.bulk_create(instances)