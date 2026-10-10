
"""Inspect an original AER ST60 monthly CSV without changing it."""

import csv
import hashlib
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "raw" / "ST60_2026-08.csv"

MEASURES = [
    "OIL PROD (M3)",
    "GAS PROD (1000 M3)",
    "GAS FLARED (1000 M3)",
    "GAS VENTED (1000 M3)",
    "WTR PROD (M3)",
]


def main() -> None:
    if not SOURCE.is_file():
        raise SystemExit(f"Source file not found: {SOURCE}")

    # ST60 has a metadata row followed by two header rows.
    with SOURCE.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.reader(file)
        metadata = next(reader)
        headings = next(reader)
        units = next(reader)
        row_widths = Counter(len(row) for row in reader)

    if (
        len(headings) != 29
        or len(units) != 29
        or row_widths != {29: sum(row_widths.values())}
    ):
        raise SystemExit(f"Unexpected ST60 CSV structure: {row_widths}")

    # Combine source labels with units or identifier descriptions.
    names = [
        f"{heading.strip()} {unit.strip()}".strip()
        for heading, unit in zip(headings, units)
    ]

    if len(names) != len(set(names)):
        raise SystemExit("Duplicate column names in ST60 headers")

    # Keep raw values as strings initially, preserving leading zeros.
    frame = pd.read_csv(
        SOURCE,
        skiprows=3,
        header=None,
        names=names,
        dtype=str,
        keep_default_na=False,
        encoding="utf-8-sig",
    )

    required = {
        "BATTERY CODE",
        "OPER CODE",
        "FIELD CENTRE",
        "BTY TYPE",
        *MEASURES,
    }

    if missing := required.difference(frame.columns):
        raise SystemExit(f"Required columns missing: {sorted(missing)}")

    print(f"Source: {SOURCE.name}")
    print(f"SHA-256: {hashlib.sha256(SOURCE.read_bytes()).hexdigest()}")
    print(f"Report metadata: {metadata}")
    print(f"Data rows: {len(frame):,}; columns: {len(frame.columns)}")
    print(f"Unique batteries: {frame['BATTERY CODE'].nunique():,}")
    print(f"Repeated battery codes: {frame['BATTERY CODE'].duplicated().sum():,}")
    print(f"Exact duplicate records: {frame.duplicated().sum():,}")
    print(f"Operators: {frame['OPER CODE'].nunique():,}")
    print(f"Field centres: {frame['FIELD CENTRE'].nunique():,}")
    print(f"Battery types: {frame['BTY TYPE'].nunique():,}")

    print("\nEmpty/whitespace-only fields (nonzero counts):")
    for column in frame.columns:
        count = frame[column].str.strip().eq("").sum()
        if count:
            print(f"  {column}: {count:,}")

    numeric_fields = [
        name
        for name in names
        if name.endswith(("(M3)", "(1000 M3)", "# WELLS"))
    ]

    invalid_count = 0
    print(f"\nNumeric validation ({len(numeric_fields)} fields):")

    for column in numeric_fields:
        raw = frame[column].str.strip()
        values = pd.to_numeric(raw, errors="coerce")
        invalid = (raw.ne("") & values.isna()).sum()
        negatives = values.lt(0).sum()
        invalid_count += invalid

        if invalid or negatives:
            print(
                f"  {column}: invalid={invalid:,}, "
                f"negative={negatives:,}"
            )

    print(f"  Total invalid nonblank values: {invalid_count:,}")

    print("\nReported monthly totals (retain units in labels):")
    for column in MEASURES:
        values = pd.to_numeric(frame[column].str.strip(), errors="coerce")
        print(f"  {column}: {values.sum():,.1f}")

    oil = pd.to_numeric(frame["OIL PROD (M3)"], errors="coerce")
    flared = pd.to_numeric(frame["GAS FLARED (1000 M3)"], errors="coerce")
    print(f"Zero oil production rows: {oil.eq(0).sum():,}")
    print(f"Zero gas flared rows: {flared.eq(0).sum():,}")


if __name__ == "__main__":
    main()
