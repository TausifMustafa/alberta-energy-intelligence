"""Discover, validate, load, and reconcile AER ST60 sources.

This module coordinates the existing archive, quality and database modules.
It does not alter source files or the ST60 quality rules.
"""

import getpass
import json
import os
import re
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from alberta_energy.archive import read_st60_archive
from alberta_energy.database import SOURCE_FIELDS, load_st60_month
from alberta_energy.ingest import read_st60_csv
from alberta_energy.quality import CORE_MEASURES, assess_st60_quality

ARCHIVE_NAME = re.compile(r"ST60[-_]\d{4}\.zip", re.IGNORECASE)
MONTH_NAME = re.compile(r"ST60_\d{4}-\d{2}\.csv", re.IGNORECASE)
SOURCE_COLUMNS = tuple(name for name, _ in SOURCE_FIELDS)


class PipelineError(ValueError):
    """A source or reconciliation problem needs review before continuing."""


def discover_sources(source: Path) -> tuple[Path, ...]:
    """Accept one official file or scan a directory without touching other files."""
    if source.is_file():
        paths = (source,)
    elif source.is_dir():
        paths = tuple(sorted(
            (path for path in source.iterdir() if path.is_file() and
             (ARCHIVE_NAME.fullmatch(path.name) or MONTH_NAME.fullmatch(path.name))),
            key=lambda path: path.name.lower(),
        ))
    else:
        raise PipelineError(f"Source path does not exist: {source}")

    if not paths:
        raise PipelineError(f"No official ST60 CSV/ZIP files found in {source}")
    for path in paths:
        if not (ARCHIVE_NAME.fullmatch(path.name) or MONTH_NAME.fullmatch(path.name)):
            raise PipelineError(f"Unexpected ST60 source filename: {path.name}")
    return paths


def iter_batches(paths: tuple[Path, ...], *, allow_partial_year: bool = False):
    """Read monthly records in raw mode, retaining ZIP member provenance."""
    for path in paths:
        if path.suffix.lower() == ".zip":
            yield from read_st60_archive(
                path,
                record_mode="raw",
                require_complete_year=not allow_partial_year,
            ).batches
        else:
            yield read_st60_csv(path, record_mode="raw")


def _months_inclusive(first: date, last: date) -> set[date]:
    months: set[date] = set()
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        months.add(date(year, month, 1))
        month += 1
        if month == 13:
            year += 1
            month = 1
    return months


def exact_sum(values) -> Decimal:
    """Sum raw accepted measurements as decimals, not converted floats."""
    result = Decimal(0)
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if not text or text == "<NA>":
            continue
        try:
            number = Decimal(text)
        except InvalidOperation as exc:
            raise PipelineError(f"Invalid numeric source value: {text!r}") from exc
        if not number.is_finite():
            raise PipelineError(f"Non-finite numeric source value: {text!r}")
        result += number
    return result


def preflight(
    source: Path,
    *,
    allow_partial_year: bool = False,
    allow_gaps: bool = False,
    manifest_path: Path | None = None,
) -> tuple[tuple[Path, ...], list[dict[str, Any]]]:
    """Validate every available source before any database work starts.

    A directory is expected to contain a contiguous range of months by
    default; --allow-gaps permits intentional subsets. A single CSV/ZIP
    can be processed without unrelated months being present.
    """
    paths = discover_sources(source)
    seen: set[date] = set()
    reports: list[dict[str, Any]] = []

    for batch in iter_batches(paths, allow_partial_year=allow_partial_year):
        period = batch.report_month
        if period in seen:
            raise PipelineError(f"Duplicate reporting month across sources: {period:%Y-%m}")
        seen.add(period)
        if tuple(batch.records.columns[:len(SOURCE_COLUMNS)]) != SOURCE_COLUMNS:
            raise PipelineError(f"Unexpected ST60 columns or units: {period:%Y-%m}")
        quality = assess_st60_quality(batch)
        summary = quality.summary
        if summary["input_rows"] != summary["accepted_rows"] + summary["quarantined_rows"]:
            raise PipelineError(f"Source row counts do not reconcile: {period:%Y-%m}")
        if summary["accepted_rows"] == 0:
            raise PipelineError(f"No accepted records for {period:%Y-%m}")

        original = quality.raw_records.loc[quality.accepted.index]
        totals = {column: str(exact_sum(original[column])) for column in CORE_MEASURES}
        reasons = Counter(quality.issues["reason_code"]) if not quality.issues.empty else Counter()
        report = {
            "month": period.isoformat(),
            "source_file": batch.source_path.name,
            "archive_member": batch.source_member,
            "source_sha256": batch.sha256,
            **{key: int(value) for key, value in summary.items()},
            "issue_reasons": dict(reasons),
            "source_totals": totals,
        }
        reports.append(report)
        print(
            f"{period:%Y-%m}: input={summary['input_rows']:,}, "
            f"accepted={summary['accepted_rows']:,}, "
            f"quarantined={summary['quarantined_rows']:,}, "
            f"warnings={summary['warning_issues']:,}, sha={batch.sha256[:12]}"
        )

    if source.is_dir() and not allow_gaps:
        expected = _months_inclusive(min(seen), max(seen))
        if missing := sorted(expected - seen):
            raise PipelineError(
                "Reporting months missing from source directory: "
                + ", ".join(f"{month:%Y-%m}" for month in missing)
            )

    reports.sort(key=lambda entry: entry["month"])
    if manifest_path is not None:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(reports, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Manifest: {manifest_path}")

    print(
        f"Preflight passed: {len(reports)} month(s), "
        f"{sum(r['accepted_rows'] for r in reports):,} accepted, "
        f"{sum(r['quarantined_rows'] for r in reports):,} quarantined, "
        f"{sum(r['warning_issues'] for r in reports):,} warning issues"
    )
    return paths, reports


def connect_database():
    """Connect as the least-privilege loader without storing credentials."""
    import psycopg

    password = os.environ.get("AEI_DB_PASSWORD") or getpass.getpass(
        "Password for PostgreSQL user aei_loader: "
    )
    return psycopg.connect(
        host=os.environ.get("AEI_DB_HOST", "localhost"),
        port=int(os.environ.get("AEI_DB_PORT", "5432")),
        dbname=os.environ.get("AEI_DB_NAME", "alberta_energy_intelligence"),
        user=os.environ.get("AEI_DB_USER", "aei_loader"),
        password=password,
        autocommit=True,
        connect_timeout=10,
    )


def load_sources(
    connection: Any,
    paths: tuple[Path, ...],
    reports: list[dict[str, Any]],
    *,
    allow_partial_year: bool = False,
    allow_quality_issues: bool = False,
) -> None:
    """Load only after an approved preflight, rechecking checksums."""
    if not allow_quality_issues and any(
        r["quarantined_rows"] or r["warning_issues"] for r in reports
    ):
        raise PipelineError(
            "Quality findings need review. See the manifest; "
            "use --allow-quality-issues only after reviewing them."
        )

    expected = {r["month"]: r for r in reports}
    for batch in iter_batches(paths, allow_partial_year=allow_partial_year):
        report = expected[batch.report_month.isoformat()]
        if report["source_sha256"] != batch.sha256:
            raise PipelineError(f"Source changed since preflight: {batch.report_month}")
        quality = assess_st60_quality(batch)
        if any(quality.summary[k] != report[k] for k in (
            "input_rows", "accepted_rows", "quarantined_rows", "warning_rows",
            "warning_issues", "error_issues",
        )):
            raise PipelineError(f"Quality results changed since preflight: {batch.report_month}")
        outcome = load_st60_month(connection, batch, quality)
        print(f"DB {outcome.report_month}: {outcome.status}, accepted={outcome.accepted_rows:,}")


def verify_database(connection: Any, reports: list[dict[str, Any]]) -> None:
    """Read-only verification for the selected months, including source totals.

    Extra active months outside the selected source set are allowed, making
    this usable for a single future month as well as a whole directory.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT s.report_month, s.source_sha256,
                      s.input_rows, s.accepted_rows, s.quarantined_rows,
                      s.warning_rows,
                      (SELECT COUNT(*) FROM aei.st60_source_row AS raw
                       WHERE raw.source_file_id = s.source_file_id),
                      (SELECT COUNT(*) FROM aei.st60_quality_issue AS issue
                       WHERE issue.source_file_id = s.source_file_id),
                      COUNT(f.source_file_id),
                      COALESCE(SUM(f.oil_prod_m3), 0),
                      COALESCE(SUM(f.gas_prod_1000_m3), 0),
                      COALESCE(SUM(f.gas_flared_1000_m3), 0),
                      COALESCE(SUM(f.gas_vented_1000_m3), 0),
                      COALESCE(SUM(f.wtr_prod_m3), 0)
               FROM aei.st60_source_file AS s
               LEFT JOIN aei.st60_battery_month AS f
                 ON f.source_file_id = s.source_file_id
               WHERE s.is_active
               GROUP BY s.source_file_id
               ORDER BY s.report_month"""
        )
        rows = cursor.fetchall()

    actual = {row[0]: row for row in rows}
    if len(actual) != len(rows):
        raise PipelineError("Multiple active source versions found for a month")

    for report in reports:
        month = date.fromisoformat(report["month"])
        if month not in actual:
            raise PipelineError(f"Active reporting month missing: {month}")
        row = actual[month]
        if row[1] != report["source_sha256"]:
            raise PipelineError(f"Active source checksum differs: {month}")
        if (row[2], row[3], row[4], row[5]) != (
            report["input_rows"], report["accepted_rows"],
            report["quarantined_rows"], report["warning_rows"],
        ):
            raise PipelineError(f"Stored source counts differ: {month}")
        if row[6] != report["input_rows"] or row[8] != report["accepted_rows"]:
            raise PipelineError(f"Stored raw or accepted rows differ: {month}")
        expected_issues = report["error_issues"] + report["warning_issues"]
        if row[7] != expected_issues:
            raise PipelineError(f"Stored quality issue count differs: {month}")
        for field, stored in zip(CORE_MEASURES, row[9:], strict=True):
            if Decimal(stored) != Decimal(report["source_totals"][field]):
                raise PipelineError(f"Database sum differs for {field}: {month}")

    print(
        f"PostgreSQL verification PASSED: {len(reports)} selected month(s), "
        f"{sum(r['accepted_rows'] for r in reports):,} accepted records; "
        "five source volume totals reconcile per month."
    )
