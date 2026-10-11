"""Check whether ST60 battery attributes remain consistent across 2025 months.

This is source profiling, not proof that battery/operator identities are stable.
Reads CSV members in the official 2025 ZIP without extracting the archive.
"""

import csv
import io
import re
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "data" / "raw" / "ST60_2025.zip"

ATTRIBUTES = (
    "OPER CODE",
    "OPERATOR NAME",
    "LICENCE NUM",
    "BTY LOCATION",
    "BTY LOCATION EDIT",
    "BTY TYPE",
    "FIELD CENTRE",
    "FLD/AREA CODE",
    "BTY DESCRIPTION",
)
EXAMPLE_FIELDS = ("OPER CODE", "LICENCE NUM", "BTY LOCATION", "BTY TYPE")


def read_month(raw: bytes, month: str) -> tuple[list[str], dict[str, dict[str, str]]]:
    """Read one monthly CSV; retain IDs as text and reject ambiguous rows."""
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        content = raw.decode("cp1252")

    reader = csv.reader(io.StringIO(content, newline=""))
    try:
        metadata = next(reader)
        headings = next(reader)
        units = next(reader)
    except StopIteration as exc:
        raise ValueError(f"{month}: missing ST60 header rows") from exc

    if len(headings) != len(units):
        raise ValueError(f"{month}: inconsistent header lengths")
    columns = [
        f"{heading.strip()} {unit.strip()}".strip()
        for heading, unit in zip(headings, units)
    ]
    required = {"BATTERY CODE", *ATTRIBUTES}
    if len(columns) != len(set(columns)) or not required.issubset(columns):
        raise ValueError(f"{month}: duplicate or missing required columns")

    month_name = datetime.strptime(month, "%Y-%m").strftime("%b")
    metadata_text = " ".join(metadata)
    if not re.search(rf"\b{month_name}\s+{month[:4]}\s+Data\b", metadata_text, re.IGNORECASE):
        print(f"WARNING: {month} source period metadata needs review: {metadata!r}")

    records = {}
    for line_number, row in enumerate(reader, start=4):
        if len(row) != len(columns):
            raise ValueError(f"{month}: wrong column count at CSV row {line_number}")
        record = dict(zip(columns, (value.strip() for value in row)))
        battery_code = record["BATTERY CODE"]
        if not battery_code or battery_code in records:
            raise ValueError(
                f"{month}: missing/repeated battery code at CSV row {line_number}"
            )
        records[battery_code] = record
    return columns, records


def compare_months(
    before: dict[str, dict[str, str]],
    after: dict[str, dict[str, str]],
    previous_month: str,
    current_month: str,
) -> tuple[int, Counter, Counter, dict[str, list[str]]]:
    """Compare only battery codes present in both adjacent source months."""
    shared = before.keys() & after.keys()
    value_changes = Counter()
    missing_transitions = Counter()
    examples = {field: [] for field in EXAMPLE_FIELDS}

    for battery_code in sorted(shared):
        earlier = before[battery_code]
        later = after[battery_code]
        for field in ATTRIBUTES:
            old, new = earlier[field], later[field]
            if old and new and old != new:
                value_changes[field] += 1
                if field in examples and len(examples[field]) < 3:
                    examples[field].append(
                        f"  {battery_code}: {previous_month} {old!r} -> "
                        f"{current_month} {new!r}"
                    )
            elif bool(old) != bool(new):
                missing_transitions[field] += 1
    return len(shared), value_changes, missing_transitions, examples


def main() -> None:
    if not ARCHIVE.is_file():
        raise SystemExit(f"Source ZIP not found: {ARCHIVE}")

    expected = {f"ST60_2025-{month:02d}.csv" for month in range(1, 13)}
    with zipfile.ZipFile(ARCHIVE) as archive:
        members = {}
        for member in archive.infolist():
            basename = Path(member.filename).name
            if basename in expected:
                if basename in members:
                    raise ValueError(f"Repeated archive member: {basename}")
                members[basename] = member
        if set(members) != expected:
            raise ValueError(f"Missing 2025 monthly CSVs: {sorted(expected - members.keys())}")

        previous_records = None
        previous_month = None
        reference_columns = None
        changes_total = Counter()
        missing_total = Counter()
        examples_total = {field: [] for field in EXAMPLE_FIELDS}

        print("2025 ST60: adjacent-month battery attribute comparison")
        print("MONTH     ROWS   SHARED   OPER CHANGES   LICENCE CHANGES   LOCATION CHANGES")
        for filename in sorted(members):
            month = filename.removeprefix("ST60_").removesuffix(".csv")
            columns, records = read_month(archive.read(members[filename]), month)
            if reference_columns is None:
                reference_columns = columns
            elif columns != reference_columns:
                raise ValueError(f"{month}: source headers/units differ from 2025-01")

            if previous_records is None:
                print(f"{month} {len(records):8,}      n/a            n/a               n/a                n/a")
            else:
                shared, changes, missing, examples = compare_months(
                    previous_records, records, previous_month, month
                )
                changes_total.update(changes)
                missing_total.update(missing)
                for field in EXAMPLE_FIELDS:
                    remaining = 3 - len(examples_total[field])
                    examples_total[field].extend(examples[field][:remaining])
                print(
                    f"{month} {len(records):8,} {shared:8,} "
                    f"{changes['OPER CODE']:14,} "
                    f"{changes['LICENCE NUM']:17,} "
                    f"{changes['BTY LOCATION']:18,}"
                )
            previous_records = records
            previous_month = month

    print("\nAdjacent-month comparisons (not unique battery counts):")
    print("FIELD                    NONBLANK VALUE CHANGES   MISSING/KNOWN TRANSITIONS")
    for field in ATTRIBUTES:
        print(f"{field:24} {changes_total[field]:22,} {missing_total[field]:27,}")

    print("\nSample changes for manual review (at most 3 per field):")
    for field in EXAMPLE_FIELDS:
        print(f"{field}:")
        for example in examples_total[field]:
            print(example)
        if not examples_total[field]:
            print("  No nonblank changes observed in adjacent months")

    print("\nCaution: code/attribute changes may reflect source corrections or reporting")
    print("changes, not necessarily real facility moves or ownership transfers.")
    print("Only consecutive calendar months with the battery in both files are compared.")


if __name__ == "__main__":
    main()
