import pandas as pd
import json
from bank_validator.validators import STRING_FIELDS


def read_csv(source):
    dtype_map = {}
    for col in STRING_FIELDS:
        dtype_map[col] = str

    try:
        df = pd.read_csv(source, dtype=dtype_map, na_filter=False)
    except Exception:
        raise ValueError("could not parse CSV")

    if df.empty:
        raise ValueError("file is empty")

    return df


def read_json(source):
    try:
        if isinstance(source, (str, bytes)):
            data = json.loads(source)
        else:
            data = json.load(source)
    except Exception:
        raise ValueError("could not parse JSON")

    if isinstance(data, dict):
        data = [data]

    if not isinstance(data, list):
        raise ValueError("expected a list of objects")

    if not data:
        raise ValueError("file is empty")

    df = pd.DataFrame(data)

    for col in STRING_FIELDS:
        if col in df.columns:
            df[col] = df[col].astype(str)

    return df