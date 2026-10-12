"""Synthetic tests for the two AER archive names and the Day 6 preflight."""

import csv
import json
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from test_ingest import row, write_csv

from alberta_energy.archive import read_st60_archive
from alberta_energy.ingest import read_st60_csv
from alberta_energy.quality import assess_st60_quality
from scripts import backfill_st60 as day6


def write_source_month(root: Path, year: int, month: int, *, wide_metadata=False):
    source = write_csv(
        root,
        month=f"{year}-{month:02d}",
        metadata=f"{date(year, month, 1):%b %Y} Data",
        records=[row(battery=f"00{year}{month:02d}")],
    )
    if wide_metadata:
        with source.open(encoding="utf-8-sig", newline="") as file:
            content = list(csv.reader(file))
        content[0] = ["Run Date:23 May 2024", "", "Apr  2024  Data", *([""] * 26)]
        with source.open("w", encoding="utf-8-sig", newline="") as file:
            csv.writer(file).writerows(content)
    return source


def make_history_archive(root: Path, year: int):
    name = "ST60-2023.zip" if year == 2023 else f"ST60_{year}.zip"
    output = root / name
    with zipfile.ZipFile(output, "w") as zipped:
        for month in range(1, 13):
            source = write_source_month(
                root, year, month, wide_metadata=(year, month) == (2024, 4)
            )
            member = f"{year}/{source.name}" if year == 2023 else source.name
            zipped.write(source, arcname=member)
            source.unlink()
    return output


def test_hyphenated_archive_retains_member_paths(tmp_path: Path):
    source = make_history_archive(tmp_path, 2023)
    archive = read_st60_archive(source, record_mode="raw")
    assert len(archive.batches) == 12
    assert archive.missing_months == ()
    assert archive.batches[0].source_member == "2023/ST60_2023-01.csv"
    assert archive.batches[0].source_path.name == "ST60-2023.zip"
    assert archive.records["source_archive"].eq("ST60-2023.zip").all()
    assert len(archive.records) == 12


def test_april_2024_extra_empty_metadata_is_kept(tmp_path: Path):
    source = write_source_month(tmp_path, 2024, 4, wide_metadata=True)
    batch = read_st60_csv(source, record_mode="raw")
    assert len(batch.metadata) == 29
    assert batch.metadata[2].strip() == "Apr  2024  Data"
    quality = assess_st60_quality(batch)
    assert quality.summary["accepted_rows"] == 1
    assert quality.summary["quarantined_rows"] == 0


def test_preflight_covers_all_31_missing_months(tmp_path: Path, monkeypatch):
    make_history_archive(tmp_path, 2023)
    make_history_archive(tmp_path, 2024)
    for month in range(1, 9):
        write_source_month(tmp_path, 2026, month)

    report_path = tmp_path / "preflight.json"
    monkeypatch.setattr(day6, "REPORT_PATH", report_path)
    result = day6.check_sources(tmp_path)
    assert len(result) == 31
    assert sum(r["accepted_rows"] for r in result) == 31
    assert sum(r["quarantined_rows"] for r in result) == 0
    assert len(day6.full_target_months()) == 44
    assert len(day6.missing_months()) == 31
    assert result[0]["archive_member"] == "2023/ST60_2023-01.csv"
    assert json.loads(report_path.read_text(encoding="utf-8")) == result


def test_preflight_rejects_missing_file(tmp_path: Path):
    with pytest.raises(ValueError, match="Missing source files"):
        day6.check_sources(tmp_path)


def test_exact_sum_distinguishes_missing_from_zero():
    assert day6.exact_sum(["0.10", "", "0", " 0.20 "]) == Decimal("0.30")
    with pytest.raises(ValueError, match="Non-finite"):
        day6.exact_sum(["NaN"])
