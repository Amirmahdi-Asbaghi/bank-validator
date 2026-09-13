"""Parse uploaded CSV or JSON files into a list of record dicts."""
from __future__ import annotations

import csv
import io
import json
from typing import Any


class ParseError(Exception):
    pass


def parse_file(filename: str, content: bytes) -> list[dict[str, Any]]:
    name = filename.lower()
    if name.endswith(".csv"):
        return _parse_csv(content)
    if name.endswith(".json"):
        return _parse_json(content)
    raise ParseError(f"Unsupported file extension: {filename}")


def _parse_csv(content: bytes) -> list[dict[str, Any]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise ParseError(f"CSV must be UTF-8: {e}") from e

    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for row in reader:
        rows.append({k: (v if v != "" else None) for k, v in row.items()})
    return rows


def _parse_json(content: bytes) -> list[dict[str, Any]]:
    try:
        data = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ParseError(f"Invalid JSON: {e}") from e

    if isinstance(data, dict) and "records" in data:
        data = data["records"]
    if not isinstance(data, list):
        raise ParseError("JSON must be a list of records or an object with 'records'")
    return data