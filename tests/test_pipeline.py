"""Source-level tests for the reusable ST60 validation/loading workflow."""

import csv
import json
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from alberta_energy.database import SOURCE_FIELDS
from alberta_energy.pipeline import (
    PipelineError,
    discover_sources,
    exact_sum,
    load_sources,
    preflight,
    verify_database,
)


def source_headers():
    headings, units = [], []
    for field, _ in SOURCE_FIELDS:
        if field.endswith("(1000 M3)"):
            headings.append(field.removesuffix("(1000 M3)").strip())
            units.append("(1000 M3)")
        elif field.endswith("(M3)"):
            headings.append(field.removesuffix("(M3)").strip())
            units.append("(M3)")
        else:
            header, _, unit = field.rpartition(" ")
            headings.append(header)
            units.append(unit)
    return headings, units


def write_month(directory: Path, year: int, month: int, *, oil="12.50", wide_metadata=False):
    path = directory / f"ST60_{year}-{month:02d}.csv"
    columns = [field for field, _ in SOURCE_FIELDS]
    row = dict.fromkeys(columns, "")
    row.update({
        "FIELD CENTRE": "A",
        "BATTERY CODE": "00000019",
        "BTY TYPE": "B",
        "OPER CODE": "0012",
        "OIL PROD (M3)": oil,
        "GAS PROD (1000 M3)": "20.25",
        "GAS FLARED (1000 M3)": "0",
        "GAS VENTED (1000 M3)": "0",
        "WTR PROD (M3)": "40.5",
        "TOTAL # WELLS": "1",
    })
    headings, units = source_headers()
    metadata = [f"{date(year, month, 1):%b %Y} Data"]
    if wide_metadata:
        metadata = ["Run Date:23 May 2024", "", "Apr  2024  Data", *([""] * 26)]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(metadata)
        writer.writerow(headings)
        writer.writerow(units)
        writer.writerow([row[column] for column in columns])
    return path


def write_archive(directory: Path, year: int):
    filename = "ST60-2023.zip" if year == 2023 else f"ST60_{year}.zip"
    archive_path = directory / filename
    with zipfile.ZipFile(archive_path, "w") as archive:
        for month in range(1, 13):
            member = write_month(directory, year, month, wide_metadata=(year, month) == (2024, 4))
            name = f"{year}/{member.name}" if year == 2023 else member.name
            archive.write(member, arcname=name)
            member.unlink()
    return archive_path


def test_directory_preflight_handles_archives_and_wide_metadata(tmp_path: Path):
    write_archive(tmp_path, 2023)
    write_archive(tmp_path, 2024)
    write_month(tmp_path, 2025, 1)
    write_month(tmp_path, 2025, 2)
    (tmp_path / "notes.txt").write_text("not an ST60 file", encoding="utf-8")
    manifest = tmp_path / "manifest.json"

    paths, reports = preflight(tmp_path, manifest_path=manifest)
    assert len(paths) == 4
    assert len(reports) == 26
    assert sum(r["accepted_rows"] for r in reports) == 26
    assert reports[0]["archive_member"] == "2023/ST60_2023-01.csv"
    assert all(r["quarantined_rows"] == 0 for r in reports)
    assert all(r["warning_issues"] == 0 for r in reports)
    assert json.loads(manifest.read_text(encoding="utf-8")) == reports


def test_directory_rejects_missing_months_unless_opted_out(tmp_path: Path):
    write_month(tmp_path, 2026, 1)
    write_month(tmp_path, 2026, 3)
    with pytest.raises(PipelineError, match="Reporting months missing"):
        preflight(tmp_path)
    _, reports = preflight(tmp_path, allow_gaps=True)
    assert [r["month"] for r in reports] == ["2026-01-01", "2026-03-01"]


def test_duplicate_archive_month_rejected(tmp_path: Path):
    write_archive(tmp_path, 2023)
    write_month(tmp_path, 2023, 1)
    with pytest.raises(PipelineError, match="Duplicate reporting month"):
        preflight(tmp_path)


def test_single_source_and_wrong_filename(tmp_path: Path):
    source = write_month(tmp_path, 2026, 8)
    _, reports = preflight(source)
    assert len(reports) == 1
    assert reports[0]["source_totals"]["OIL PROD (M3)"] == "12.50"
    invalid = tmp_path / "notes.csv"
    invalid.write_text("not a source", encoding="utf-8")
    with pytest.raises(PipelineError, match="Unexpected ST60 source filename"):
        discover_sources(invalid)


def test_quality_warnings_require_explicit_opt_in(tmp_path: Path, monkeypatch):
    source = write_month(tmp_path, 2026, 8, oil="")
    paths, reports = preflight(source)
    assert reports[0]["warning_issues"] > 0
    with pytest.raises(PipelineError, match="Quality findings need review"):
        load_sources(None, paths, reports)

    calls = []
    def fake_load(_connection, batch, quality):
        calls.append(batch.report_month)
        return SimpleNamespace(report_month="2026-08", status="loaded", accepted_rows=1)

    monkeypatch.setattr("alberta_energy.pipeline.load_st60_month", fake_load)
    load_sources(None, paths, reports, allow_quality_issues=True)
    assert calls == [date(2026, 8, 1)]


def test_read_only_db_reconciliation_checks_source_totals(tmp_path: Path):
    source = write_month(tmp_path, 2026, 8)
    _, reports = preflight(source)
    report = reports[0]
    correct = (
        date(2026, 8, 1), report["source_sha256"], 1, 1, 0, 0,
        1, 0, 1, Decimal("12.50"), Decimal("20.25"), Decimal(0),
        Decimal(0), Decimal("40.5"),
    )

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, sql):
            assert "s.is_active" in sql

        def fetchall(self):
            return [correct]

    class FakeConnection:
        def cursor(self):
            return FakeCursor()

    verify_database(FakeConnection(), reports)
    assert exact_sum(["0.10", "", "0", " 0.20 "]) == Decimal("0.30")
    with pytest.raises(PipelineError, match="Non-finite"):
        exact_sum(["NaN"])


def test_hyphenated_archive_provenance_is_retained(tmp_path: Path):
    from alberta_energy.archive import read_st60_archive

    source = write_archive(tmp_path, 2023)
    archive = read_st60_archive(source, record_mode="raw")
    assert len(archive.batches) == 12
    assert archive.missing_months == ()
    assert archive.batches[0].source_path.name == "ST60-2023.zip"
    assert archive.batches[0].source_member == "2023/ST60_2023-01.csv"
    assert archive.records["source_archive"].eq("ST60-2023.zip").all()


def test_extended_april_metadata_kept_during_quality_validation(tmp_path: Path):
    from alberta_energy.ingest import read_st60_csv
    from alberta_energy.quality import assess_st60_quality

    source = write_month(tmp_path, 2024, 4, wide_metadata=True)
    batch = read_st60_csv(source, record_mode="raw")
    assert len(batch.metadata) == 29
    assert batch.metadata[2].strip() == "Apr  2024  Data"
    quality = assess_st60_quality(batch)
    assert quality.summary["accepted_rows"] == 1
    assert quality.summary["quarantined_rows"] == 0
