"""Generate a synthetic CSV just over a given size for pipeline testing.

Usage (inside the web container):
    python /app/scripts/generate_big_file.py --target-mb 51 --out /app/data/samples/big_51mb.csv

The script writes rows until the file exceeds the target size, then stops.
It mixes valid rows with rows that fail specific rules so we can verify the
validator catches exactly what we planted.
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
from pathlib import Path


HEADER = [
    "bank_code",
    "period",
    "account_code",
    "debit",
    "credit",
    "balance",
    "currency",
    "record_id",
]

VALID_BANK_CODES = ["010", "020", "030"]
VALID_CURRENCIES = ["IRR", "USD", "EUR", "GBP", "AED"]

# Rough mix (sums to 1.0)
MIX = {
    "valid": 0.95,
    "E004": 0.01,   # bad bank_code
    "E005": 0.01,   # bad period
    "E007": 0.01,   # balance mismatch
    "E008": 0.01,   # negative amount
    "E011": 0.01,   # bad currency
    # E006 (duplicates) is handled separately by repeating earlier record_ids
}


def pick_error() -> str:
    r = random.random()
    cum = 0.0
    for k, w in MIX.items():
        cum += w
        if r <= cum:
            return k
    return "valid"


def make_row(record_id: str) -> list[str]:
    kind = pick_error()

    bank_code = random.choice(VALID_BANK_CODES)
    period = f"1405/{random.randint(1, 12):02d}"
    account_code = f"A{random.randint(1, 999_999):06d}"
    debit = round(random.uniform(10, 10_000), 2)
    credit = round(random.uniform(0, debit), 2)
    balance = round(debit - credit, 2)
    currency = random.choice(VALID_CURRENCIES)

    if kind == "E004":
        bank_code = random.choice(["999", "998", "997", "XYZ"])
    elif kind == "E005":
        period = random.choice(["1405-03", "1405/13", "1405/00", "ABCD/EF", "1405/3"])
    elif kind == "E007":
        balance = round(balance + random.uniform(1, 100), 2)
    elif kind == "E008":
        debit = -abs(debit)
        balance = round(debit - credit, 2)
    elif kind == "E011":
        currency = random.choice(["XYZ", "ABC", "US", ""])

    return [
        bank_code,
        period,
        account_code,
        f"{debit:.2f}",
        f"{credit:.2f}",
        f"{balance:.2f}",
        currency,
        record_id,
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-mb", type=float, default=51.0,
                        help="Target file size in megabytes")
    parser.add_argument("--out", required=True, help="Output CSV path")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for reproducibility")
    parser.add_argument("--dup-rate", type=float, default=0.005,
                        help="Fraction of rows that reuse a previous record_id (E006)")
    args = parser.parse_args()

    random.seed(args.seed)
    target_bytes = int(args.target_mb * 1024 * 1024)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Track some record_ids to reuse for duplicate rows
    recent_record_ids: list[str] = []

    row_count = 0
    written_bytes = 0

    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(HEADER)
        written_bytes += (",".join(HEADER) + "\n").encode("utf-8").__len__()

        while written_bytes < target_bytes:
            row_count += 1
            record_id = f"R{row_count:09d}"

            # Occasionally duplicate an earlier record_id → triggers E006
            if recent_record_ids and random.random() < args.dup_rate:
                record_id = random.choice(recent_record_ids)

            row = make_row(record_id)
            writer.writerow(row)
            written_bytes += (",".join(row) + "\n").encode("utf-8").__len__()

            # Keep a rolling buffer of recent ids for duplicate injection
            if len(recent_record_ids) < 100_000:
                recent_record_ids.append(record_id)

            if row_count % 100_000 == 0:
                mb = written_bytes / (1024 * 1024)
                print(f"  ... {row_count:,} rows  ({mb:.1f} MB)", file=sys.stderr)

    mb = out_path.stat().st_size / (1024 * 1024)
    print(f"Done: {row_count:,} rows, {mb:.2f} MB → {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())