"""Read AER ST60 annual ZIP archives using the monthly CSV parser.

The original archive and member names are kept for provenance. Archives are
read without extracting source files, and missing months are not hidden.
"""

import hashlib
import re
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath

import pandas as pd

from alberta_energy.ingest import (
    EXPECTED_COLUMN_COUNT,
    RecordMode,
    ST60Batch,
    ST60FormatError,
    _read_st60_bytes,
)

# The AER uses both ST60_2024.zip and ST60-2023.zip.
ARCHIVE_PATTERN = re.compile(r"^ST60[-_](\d{4})\.zip$", re.IGNORECASE)
MEMBER_PATTERN = re.compile(r"^ST60_(\d{4})-(\d{2})\.csv$", re.IGNORECASE)


@dataclass(frozen=True)
class ST60Archive:
    """Validated monthly batches and original annual ZIP provenance."""

    source_path: Path
    sha256: str
    batches: tuple[ST60Batch, ...]
    missing_months: tuple[date, ...]
    records: pd.DataFrame


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_st60_archive(
    path: str | Path,
    *,
    require_complete_year: bool = True,
    record_mode: RecordMode = "strict",
) -> ST60Archive:
    """Read each month from a ZIP, preserving each member's source metadata."""
    source = Path(path)
    match = ARCHIVE_PATTERN.fullmatch(source.name)
    if match is None:
        raise ST60FormatError(
            f"Expected ST60_YYYY.zip or ST60-YYYY.zip filename, got {source.name!r}"
        )
    archive_year = int(match.group(1))
    archive_sha256 = _file_sha256(source)

    monthly_members: dict[int, zipfile.ZipInfo] = {}
    batches: list[ST60Batch] = []
    try:
        with zipfile.ZipFile(source) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                name = PurePosixPath(info.filename).name
                member_match = MEMBER_PATTERN.fullmatch(name)
                if member_match is None:
                    if name.upper().startswith("ST60_") and name.lower().endswith(".csv"):
                        raise ST60FormatError(
                            f"Unexpected ST60 member name: {info.filename!r}"
                        )
                    continue
                year, month = map(int, member_match.groups())
                if year != archive_year or not 1 <= month <= 12:
                    raise ST60FormatError(
                        f"Archive {source.name} contains out-of-year/invalid member "
                        f"{info.filename!r}"
                    )
                if month in monthly_members:
                    raise ST60FormatError(
                        f"Duplicate ST60 month {year}-{month:02d} in {source.name}"
                    )
                if info.flag_bits & 1:
                    raise ST60FormatError(
                        f"Encrypted ST60 member not supported: {info.filename!r}"
                    )
                monthly_members[month] = info

            if not monthly_members:
                raise ST60FormatError(f"No ST60 monthly CSVs found in {source.name}")

            missing = tuple(
                date(archive_year, month, 1)
                for month in range(1, 13)
                if month not in monthly_members
            )
            if require_complete_year and missing:
                missing_str = ", ".join(f"{period:%Y-%m}" for period in missing)
                raise ST60FormatError(f"Missing ST60 archive months: {missing_str}")

            expected_columns: tuple[str, ...] | None = None
            for month, info in sorted(monthly_members.items()):
                try:
                    raw = archive.read(info)
                except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                    raise ST60FormatError(
                        f"Cannot read ST60 member {info.filename!r}: {exc}"
                    ) from exc
                try:
                    batch = _read_st60_bytes(
                        raw,
                        source_name=PurePosixPath(info.filename).name,
                        source_path=source,
                        source_member=info.filename,
                        source_archive_sha256=archive_sha256,
                        record_mode=record_mode,
                    )
                except ST60FormatError as exc:
                    raise ST60FormatError(f"{info.filename}: {exc}") from exc
                if batch.report_month != date(archive_year, month, 1):
                    raise ST60FormatError(
                        f"Reporting period mismatch in {info.filename!r}"
                    )
                columns = tuple(batch.records.columns[:EXPECTED_COLUMN_COUNT])
                if expected_columns is None:
                    expected_columns = columns
                elif columns != expected_columns:
                    raise ST60FormatError(
                        f"ST60 column schema differs in {info.filename!r} "
                        "from first month"
                    )
                batches.append(batch)
    except zipfile.BadZipFile as exc:
        raise ST60FormatError(f"Invalid ST60 ZIP archive {source}: {exc}") from exc

    records = pd.concat((batch.records for batch in batches), ignore_index=True)
    return ST60Archive(
        source_path=source,
        sha256=archive_sha256,
        batches=tuple(batches),
        missing_months=missing,
        records=records,
    )
