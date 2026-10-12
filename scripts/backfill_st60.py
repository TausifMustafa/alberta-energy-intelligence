"""Validate and optionally load the missing 2023, 2024, and 2026 ST60 months.

Usage from the repository root:
    python scripts/backfill_st60.py
    python scripts/backfill_st60.py --load

The first command does not write to PostgreSQL. Source files in data/raw
remain untouched. Every load is handled by the existing monthly loader.
"""

import argparse
import getpass
import json
import os
import sys
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from alberta_energy.archive import read_st60_archive
from alberta_energy.database import load_st60_month
from alberta_energy.ingest import EXPECTED_COLUMN_COUNT, read_st60_csv
from alberta_energy.quality import CORE_MEASURES, assess_st60_quality

DATA_DIR = ROOT / "data" / "raw"
REPORT_PATH = ROOT / "data" / "processed" / "day6_preflight.json"


def missing_sources(data_dir: Path) -> tuple[Path, ...]:
    """Files not already loaded at the end of Day 5."""
    return (
        data_dir / "ST60-2023.zip",
        data_dir / "ST60_2024.zip",
        *(data_dir / f"ST60_2026-{month:02d}.csv" for month in range(1, 8)),
    )


def missing_months() -> set[date]:
    """All 31 months not loaded at the Day 5 checkpoint."""
    return {
        *(date(year, month, 1) for year in (2023, 2024) for month in range(1, 13)),
        *(date(2026, month, 1) for month in range(1, 8)),
    }


def full_target_months() -> set[date]:
    return {
        *(date(year, month, 1) for year in (2023, 2024, 2025) for month in range(1, 13)),
        *(date(2026, month, 1) for month in range(1, 9)),
    }


def batches_from_file(path: Path):
    """Use the existing parser and enforce all 12 months of each ZIP."""
    if path.suffix.lower() == ".zip":
        archive = read_st60_archive(path, record_mode="raw")
        yield from archive.batches
    else:
        yield read_st60_csv(path, record_mode="raw")


def exact_sum(series) -> Decimal:
    """Independently sum accepted raw decimal text, not pandas floats."""
    result = Decimal(0)
    for value in series:
        if value is None:
            continue
        text = str(value).strip()
        if not text or text == "<NA>":
            continue
        try:
            number = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError(f"Invalid numeric source value: {text!r}") from exc
        if not number.is_finite():
            raise ValueError(f"Non-finite numeric source value: {text!r}")
        result += number
    return result


def check_sources(data_dir: Path) -> list[dict]:
    """Reject incomplete or unexpected coverage before writing any database rows."""
    paths = missing_sources(data_dir)
    absent = [str(path) for path in paths if not path.is_file()]
    if absent:
        raise ValueError("Missing source files:\n" + "\n".join(absent))

    reference = read_st60_csv(data_dir / "ST60_2026-08.csv", record_mode="raw")
    expected_columns = tuple(reference.records.columns[:EXPECTED_COLUMN_COUNT])
    seen: set[date] = set()
    reports: list[dict] = []

    for path in paths:
        for batch in batches_from_file(path):
            month = batch.report_month
            if month in seen:
                raise ValueError(f"Duplicate reporting month in source files: {month}")
            seen.add(month)
            columns = tuple(batch.records.columns[:EXPECTED_COLUMN_COUNT])
            if columns != expected_columns:
                raise ValueError(f"Different source columns or units: {month:%Y-%m}")

            quality = assess_st60_quality(batch)
            summary = quality.summary
            if summary["input_rows"] != summary["accepted_rows"] + summary["quarantined_rows"]:
                raise ValueError(f"Row counts do not reconcile: {month:%Y-%m}")
            if summary["accepted_rows"] == 0:
                raise ValueError(f"No accepted rows for {month:%Y-%m}")

            original_accepted = quality.raw_records.loc[quality.accepted.index]
            totals = {
                field: str(exact_sum(original_accepted[field]))
                for field in CORE_MEASURES
            }
            reasons = (
                Counter(quality.issues["reason_code"].tolist())
                if not quality.issues.empty else Counter()
            )
            report = {
                "month": month.isoformat(),
                "source_file": batch.source_path.name,
                "archive_member": batch.source_member,
                "source_sha256": batch.sha256,
                **{key: int(value) for key, value in summary.items()},
                "issue_reasons": dict(reasons),
                "source_totals": totals,
            }
            reports.append(report)
            print(
                f"{month:%Y-%m}: read={summary['input_rows']:,}, "
                f"accepted={summary['accepted_rows']:,}, "
                f"rejected={summary['quarantined_rows']:,}, "
                f"warnings={summary['warning_issues']:,}, "
                f"sha={batch.sha256[:12]}"
            )

    expected = missing_months()
    if seen != expected:
        missing = sorted(expected - seen)
        extra = sorted(seen - expected)
        raise ValueError(f"Incorrect coverage. Missing={missing}; extra={extra}")

    reports.sort(key=lambda entry: entry["month"])
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(reports, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        f"\nPreflight passed: {len(reports)} months, "
        f"{sum(r['accepted_rows'] for r in reports):,} accepted rows, "
        f"{sum(r['quarantined_rows'] for r in reports):,} quarantined rows, "
        f"{sum(r['warning_issues'] for r in reports):,} warning issues"
    )
    print(f"Manifest: {REPORT_PATH}")
    return reports


def connect_database():
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
        connect_timeout=10,
        autocommit=True,
    )


def verify_database(connection, reports: list[dict]) -> None:
    """Cross-check 31 source manifests plus the full 44-month SQL coverage."""
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT s.report_month, s.source_sha256,
                      s.input_rows, s.accepted_rows, s.quarantined_rows,
                      COUNT(f.source_file_id) AS stored_accepted,
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
    if len(rows) != len(actual):
        raise ValueError("More than one active source version per month")
    if set(actual) != full_target_months():
        missing = sorted(full_target_months() - set(actual))
        extra = sorted(set(actual) - full_target_months())
        raise ValueError(f"Incorrect active month coverage: missing={missing}, extra={extra}")

    for row in rows:
        month, _, input_count, accepted_count, rejected_count, stored_count, *_ = row
        if input_count != accepted_count + rejected_count:
            raise ValueError(f"Source rows do not reconcile: {month}")
        if stored_count != accepted_count:
            raise ValueError(f"Stored accepted rows do not reconcile: {month}")

    for report in reports:
        month = date.fromisoformat(report["month"])
        row = actual[month]
        if row[1] != report["source_sha256"]:
            raise ValueError(f"Unexpected active source checksum: {month}")
        if (row[2], row[3], row[4]) != (
            report["input_rows"], report["accepted_rows"], report["quarantined_rows"]
        ):
            raise ValueError(f"Stored source counts differ: {month}")
        for field, stored in zip(CORE_MEASURES, row[6:], strict=True):
            if Decimal(stored) != Decimal(report["source_totals"][field]):
                raise ValueError(f"PostgreSQL total differs for {field}: {month}")

    print(
        f"\nPostgreSQL reconciliation PASSED: {len(rows)} active months, "
        f"{sum(row[5] for row in rows):,} accepted records."
    )
    print("All five main source totals match PostgreSQL for the 31 new months.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--load", action="store_true", help="Load after preflight")
    parser.add_argument(
        "--allow-quality-issues", action="store_true",
        help="Explicitly allow accepted rows with warnings or quarantined source rows",
    )
    args = parser.parse_args()

    try:
        reports = check_sources(DATA_DIR)
        rejected = sum(report["quarantined_rows"] for report in reports)
        warnings = sum(report["warning_issues"] for report in reports)
        if not args.load:
            print("Preflight only: PostgreSQL was not modified.")
            return 0
        if (rejected or warnings) and not args.allow_quality_issues:
            raise ValueError(
                "Quality issues require review before loading. "
                "Inspect data/processed/day6_preflight.json; if accepted, "
                "rerun --load --allow-quality-issues."
            )

        manifest = {report["month"]: report for report in reports}
        with connect_database() as connection:
            for path in missing_sources(DATA_DIR):
                for batch in batches_from_file(path):
                    report = manifest[batch.report_month.isoformat()]
                    if batch.sha256 != report["source_sha256"]:
                        raise ValueError(f"Source changed after preflight: {batch.report_month}")
                    quality = assess_st60_quality(batch)
                    outcome = load_st60_month(connection, batch, quality)
                    print(
                        f"DB {outcome.report_month}: {outcome.status}, "
                        f"accepted={outcome.accepted_rows:,}"
                    )
            verify_database(connection, reports)
        return 0
    except (ValueError, OSError) as exc:
        print(f"Day 6 backfill stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
