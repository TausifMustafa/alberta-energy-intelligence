"""Read one AER ST60 monthly CSV into a traceable, typed DataFrame.

This module deliberately does not clean business values or load PostgreSQL.
It validates the observed three-row CSV layout and keeps source identifiers
as text, including leading zeros. Source files are never modified.
"""

import csv
import hashlib
import io
import re
from calendar import month_abbr
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

import pandas as pd

EXPECTED_COLUMN_COUNT = 29
REQUIRED_COLUMNS = frozenset(
    {
        "BATTERY CODE",
        "OPER CODE",
        "FIELD CENTRE",
        "BTY TYPE",
        "OIL PROD (M3)",
        "GAS PROD (1000 M3)",
        "GAS FLARED (1000 M3)",
        "GAS VENTED (1000 M3)",
        "WTR PROD (M3)",
    }
)
SOURCE_NAME_PATTERN = re.compile(r"^ST60_(\d{4})-(\d{2})\.csv$", re.IGNORECASE)
NUMERIC_SUFFIXES = ("(M3)", "(1000 M3)", "# WELLS")
RecordMode = Literal["strict", "raw"]

class ST60FormatError(ValueError):
    """An ST60 source file violates the observed monthly CSV contract."""


@dataclass(frozen=True)
class ST60Batch:
    """A single reporting month and its file-level provenance."""

    source_path: Path
    report_month: date
    metadata: tuple[str, ...]
    sha256: str
    encoding: str
    records: pd.DataFrame
    source_member: str | None = None
    source_archive_sha256: str | None = None


def _period_from_filename(path: Path) -> date:
    match = SOURCE_NAME_PATTERN.fullmatch(path.name)
    if match is None:
        raise ST60FormatError(
            f"Expected ST60_YYYY-MM.csv filename, got {path.name!r}"
        )
    year, month = map(int, match.groups())
    try:
        return date(year, month, 1)
    except ValueError as exc:
        raise ST60FormatError(f"Invalid reporting month in {path.name!r}") from exc


def _decode(raw: bytes) -> tuple[str, str]:
    """Match encodings encountered by the existing historical inspection scripts."""
    try:
        return raw.decode("utf-8-sig"), "utf-8-sig"
    except UnicodeDecodeError:
        return raw.decode("cp1252"), "cp1252"



def _read_st60_bytes(
    raw_bytes: bytes,
    *,
    source_name: str,
    source_path: Path,
    source_member: str | None = None,
    source_archive_sha256: str | None = None,
    record_mode: RecordMode = "strict",
) -> ST60Batch:
    """Parse monthly CSV bytes, shared by standalone and ZIP ingestion.

    For ZIP members source_path is the actual ZIP file, while source_name
    names the member CSV. Member bytes receive their own source_sha256.
    """
    source = Path(source_name)
    report_month = _period_from_filename(source)
    if record_mode not in ("strict", "raw"):
        raise ValueError(f"Unsupported ST60 record mode: {record_mode!r}")
    if not raw_bytes:
        raise ST60FormatError(f"Empty ST60 file: {source}")

    decoded, encoding = _decode(raw_bytes)
    reader = csv.reader(io.StringIO(decoded, newline=""), strict=True)
    try:
        metadata = next(reader)
        headings = next(reader)
        units = next(reader)
    except StopIteration as exc:
        raise ST60FormatError("Missing one or more of the three ST60 header rows") from exc
    except csv.Error as exc:
        raise ST60FormatError(f"Malformed CSV header: {exc}") from exc

    if len(headings) != EXPECTED_COLUMN_COUNT or len(units) != EXPECTED_COLUMN_COUNT:
        raise ST60FormatError(
            f"Expected {EXPECTED_COLUMN_COUNT} heading/unit columns; "
            f"found {len(headings)} headings and {len(units)} units"
        )

    columns = [
        f"{heading.strip()} {unit.strip()}".strip()
        for heading, unit in zip(headings, units, strict=True)
    ]
    if any(not name for name in columns) or len(columns) != len(set(columns)):
        raise ST60FormatError("Blank or repeated combined ST60 column names")
    if missing := REQUIRED_COLUMNS.difference(columns):
        raise ST60FormatError(f"Missing required ST60 columns: {sorted(missing)}")

    metadata_text = " ".join(field.strip() for field in metadata)
    expected_period = re.compile(
        rf"\b{month_abbr[report_month.month]}\s+{report_month.year}\s+Data\b",
        re.IGNORECASE,
    )
    if not expected_period.search(metadata_text):
        raise ST60FormatError(
            f"Source metadata does not confirm {report_month:%Y-%m}: {metadata!r}"
        )

    checksum = hashlib.sha256(raw_bytes).hexdigest()

    rows: list[list[str]] = []
    csv_row_numbers: list[int] = []
    try:
        for row in reader:
            line_number = reader.line_num
            if len(row) != EXPECTED_COLUMN_COUNT:
                raise ST60FormatError(
                    f"CSV line {line_number}: expected {EXPECTED_COLUMN_COUNT} "
                    f"fields, got {len(row)}"
                )

            if record_mode == "strict":
                rows.append([value.strip() for value in row])
            else:
                rows.append(row)

            csv_row_numbers.append(line_number)
    except csv.Error as exc:
        raise ST60FormatError(f"Malformed CSV record: {exc}") from exc

    if not rows:
        raise ST60FormatError("ST60 file contains no reporting records")


    frame = pd.DataFrame(rows, columns=columns, dtype="string")

    if record_mode == "strict":
        for column in ("BATTERY CODE", "OPER CODE"):
            empty = frame[column].eq("")
            if empty.any():
                number = csv_row_numbers[int(empty[empty].index[0])]
                raise ST60FormatError(
                    f"CSV line {number}: blank required {column}"
                )

        duplicates = frame["BATTERY CODE"].duplicated(keep=False)
        if duplicates.any():
            example = frame.loc[duplicates, "BATTERY CODE"].iloc[0]
            raise ST60FormatError(
                f"Repeated BATTERY CODE within "
                f"{report_month:%Y-%m}: {example!r}"
            )

        numeric_columns = [
            name for name in columns if name.endswith(NUMERIC_SUFFIXES)
        ]

        for column in numeric_columns:
            original = frame[column]
            parsed = pd.to_numeric(
                original.mask(original.eq("")),
                errors="coerce",
            )
            invalid = original.ne("") & (
                parsed.isna()
                | parsed.isin([float("inf"), float("-inf")])
            )

            if invalid.any():
                bad_index = int(invalid[invalid].index[0])
                number = csv_row_numbers[bad_index]
                raise ST60FormatError(
                    f"CSV line {number}: invalid numeric value "
                    f"{frame.loc[bad_index, column]!r} "
                    f"in {column}"
                )

            frame[column] = parsed.astype("Float64")

    frame["report_month"] = pd.Timestamp(report_month)
    frame["source_file"] = source.name
    frame["source_sha256"] = checksum
    frame["source_row_number"] = csv_row_numbers
    if source_member is not None:
        frame["source_archive"] = source_path.name
        frame["source_archive_sha256"] = source_archive_sha256
        frame["source_archive_member"] = source_member

    return ST60Batch(
        source_path=source_path,
        report_month=report_month,
        metadata=tuple(metadata),
        sha256=checksum,
        encoding=encoding,
        records=frame,
        source_member=source_member,
        source_archive_sha256=source_archive_sha256,
    )



def read_st60_csv(
    path: str | Path,
    *,
    record_mode: RecordMode = "strict",
) -> ST60Batch:
    """Read an ST60 CSV using strict or raw record handling.

    Strict mode validates records and converts numeric columns.
    Raw mode preserves original field text for quality assessment.
    Both modes enforce the source file's structural contract.
    """
    source = Path(path)

    return _read_st60_bytes(
        source.read_bytes(),
        source_name=source.name,
        source_path=source,
        record_mode=record_mode,
    )
