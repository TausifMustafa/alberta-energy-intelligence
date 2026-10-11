"""Compare 2025 ST60 CSVs with the August 2026 reference schema."""

import csv
import hashlib
import io
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
REFERENCE = RAW / "ST60_2026-08.csv"
ARCHIVE = RAW / "ST60_2025.zip"
EXPECTED_MONTHS = {f"ST60_2025-{month:02d}.csv" for month in range(1, 13)}


def inspect_csv(raw: bytes) -> dict:
    try:
        content = raw.decode("utf-8-sig")
        encoding = "UTF-8"
    except UnicodeDecodeError:
        content = raw.decode("cp1252")
        encoding = "Windows-1252 fallback"

    reader = csv.reader(io.StringIO(content, newline=""))
    try:
        metadata = next(reader)
        headings = next(reader)
        units = next(reader)
    except StopIteration as exc:
        raise ValueError("File is missing one or more ST60 header rows") from exc

    if len(headings) != len(units):
        raise ValueError("Heading and unit rows have different widths")

    columns = [f"{heading.strip()} {unit.strip()}".strip()
               for heading, unit in zip(headings, units)]
    battery_index = columns.index("BATTERY CODE") if "BATTERY CODE" in columns else None
    battery_codes = set()
    duplicate_batteries = 0
    invalid_row_widths = 0
    records = 0
    for record in reader:
        records += 1
        if len(record) != len(columns):
            invalid_row_widths += 1
            continue
        if battery_index is not None:
            code = record[battery_index].strip()
            if code in battery_codes:
                duplicate_batteries += 1
            battery_codes.add(code)

    return {
        "metadata": metadata,
        "columns": columns,
        "records": records,
        "bad_widths": invalid_row_widths,
        "battery_codes": battery_codes,
        "duplicate_batteries": duplicate_batteries,
        "encoding": encoding,
    }


def main() -> None:
    if not REFERENCE.is_file() or not ARCHIVE.is_file():
        raise SystemExit("Missing source CSV or ZIP in data/raw")

    reference = inspect_csv(REFERENCE.read_bytes())
    print(f"Reference: {REFERENCE.name}, {len(reference['columns'])} columns")
    print(f"Archive SHA-256: {hashlib.sha256(ARCHIVE.read_bytes()).hexdigest()}")

    with zipfile.ZipFile(ARCHIVE) as archive:
        csv_members = {Path(member.filename).name: member
                       for member in archive.infolist()
                       if re.fullmatch(r"ST60_2025-\d{2}\.csv", Path(member.filename).name)}
        missing = sorted(EXPECTED_MONTHS - csv_members.keys())
        print(f"2025 CSV months found: {len(csv_members)}")
        print(f"Missing expected months: {missing or 'None'}")
        previous_codes = None
        print("\nMONTH    ROWS   COLS   SCHEMA   BAD WIDTHS   DUP BATTERIES   REPEATED VS PREV")
        for filename in sorted(csv_members):
            result = inspect_csv(archive.read(csv_members[filename]))
            same = result["columns"] == reference["columns"]
            overlap = (str(len(result["battery_codes"] & previous_codes))
                       if previous_codes is not None else "n/a")
            print(f"{filename[5:12]:7} {result['records']:7,} {len(result['columns']):6} "
                  f"{'MATCH' if same else 'DIFF':8} {result['bad_widths']:12,} "
                  f"{result['duplicate_batteries']:15,} {overlap:>18}")
            if not same:
                for index in range(max(len(reference["columns"]), len(result["columns"]))):
                    expected = (reference["columns"][index]
                                if index < len(reference["columns"]) else "<none>")
                    found = (result["columns"][index]
                             if index < len(result["columns"]) else "<none>")
                    if expected != found:
                        print(f"    Column {index + 1}: reference={expected!r}; historical={found!r}")
            if result["encoding"] != "UTF-8":
                print(f"    Encoding note: {result['encoding']}")
            previous_codes = result["battery_codes"]


if __name__ == "__main__":
    main()
