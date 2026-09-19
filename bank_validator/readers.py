import pandas as pd
import json


def read_csv(source):
    try:
        df = pd.read_csv(source)
    except Exception:
        raise ValueError("could not parse CSV")

    if df.empty:
        raise ValueError("file is empty")

    return df


def read_json(source):
    try:
        if isinstance(source, str):
            # for raw json
            data = json.loads(source)
        else:
            # JSON as file
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

    return df
