"""Synthetic yearly ZIP tests and locally available real-source reconciliation."""

import hashlib
import zipfile
from datetime import date
from pathlib import Path

import pytest
from test_ingest import row, write_csv

from alberta_energy.archive import read_st60_archive
from alberta_energy.ingest import ST60FormatError


def make_archive(
    directory: Path,
    *,
    months: tuple[int, ...] = tuple(range(1, 13)),
    overrides: dict[int, dict] | None = None,
    nested: bool = False,
) -> Path:
    """Build a tiny source-like archive without bundling actual AER records."""
    archive_path = directory / "ST60_2025.zip"
    overrides = overrides or {}
    with zipfile.ZipFile(archive_path, mode="w") as archive:
        for month in months:
            custom = overrides.get(month, {})
            # A different operator across months is legitimate and should remain visible.
            source = write_csv(
                directory,
                month=f"2025-{month:02d}",
                metadata=f"{date(2025, month, 1):%b %Y} Data",
                records=custom.get("records", [row(operator=f"{month:04d}")]),
                headings=custom.get("headings"),
                units=custom.get("units"),
            )
            member_name = f"folder/{source.name}" if nested else source.name
            archive.write(source, arcname=member_name)
            source.unlink()
    return archive_path


def test_full_year_archive_preserves_periods_ids_and_provenance(tmp_path: Path) -> None:
    source = make_archive(tmp_path)
    result = read_st60_archive(source)
    assert len(result.batches) == 12
    assert result.missing_months == ()
    assert len(result.records) == 12
    assert result.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert result.records["BATTERY CODE"].tolist() == ["000123"] * 12
    assert result.records["OPER CODE"].tolist() == [f"{m:04d}" for m in range(1, 13)]
    assert result.records["report_month"].dt.date.tolist() == [
        date(2025, m, 1) for m in range(1, 13)
    ]
    assert result.records["source_row_number"].eq(4).all()
    assert result.records["source_archive"].eq(source.name).all()
    assert result.records["source_archive_sha256"].eq(result.sha256).all()
    assert result.records["source_archive_member"].tolist() == [
        f"ST60_2025-{m:02d}.csv" for m in range(1, 13)
    ]
    assert all(batch.source_path == source for batch in result.batches)
    assert all(batch.source_member for batch in result.batches)
    assert all(batch.sha256 != result.sha256 for batch in result.batches)
    assert result.records["OIL PROD (M3)"].sum() == pytest.approx(150.0)
    assert not any(tmp_path.glob("ST60_2025-*.csv")), "ZIP must not be extracted"


def test_months_are_sorted_and_nested_paths_are_not_extracted(tmp_path: Path) -> None:
    source = make_archive(tmp_path, months=(12, 1), nested=True)
    result = read_st60_archive(source, require_complete_year=False)
    assert [batch.report_month.month for batch in result.batches] == [1, 12]
    assert len(result.missing_months) == 10
    assert result.records["source_archive_member"].tolist() == [
        "folder/ST60_2025-01.csv", "folder/ST60_2025-12.csv"
    ]
    assert not (tmp_path / "folder").exists()


def test_missing_month_rejected_by_default(tmp_path: Path) -> None:
    source = make_archive(tmp_path, months=(1, 2))
    with pytest.raises(ST60FormatError, match="Missing ST60 archive months"):
        read_st60_archive(source)


def test_archive_without_monthly_data_rejected(tmp_path: Path) -> None:
    source = tmp_path / "ST60_2025.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("README.txt", "no data")
    with pytest.raises(ST60FormatError, match="No ST60 monthly CSVs"):
        read_st60_archive(source)


def test_duplicate_month_rejected(tmp_path: Path) -> None:
    source = make_archive(tmp_path)
    with zipfile.ZipFile(source, "a") as archive:
        archive.writestr("other/ST60_2025-01.csv", "junk")
    with pytest.raises(ST60FormatError, match="Duplicate ST60 month"):
        read_st60_archive(source)


def test_out_of_year_member_rejected(tmp_path: Path) -> None:
    source = make_archive(tmp_path)
    with zipfile.ZipFile(source, "a") as archive:
        archive.writestr("ST60_2026-01.csv", "junk")
    with pytest.raises(ST60FormatError, match="out-of-year"):
        read_st60_archive(source)


def test_member_with_invalid_month_rejected(tmp_path: Path) -> None:
    source = make_archive(tmp_path)
    with zipfile.ZipFile(source, "a") as archive:
        archive.writestr("ST60_2025-13.csv", "junk")
    with pytest.raises(ST60FormatError, match="out-of-year/invalid"):
        read_st60_archive(source)


def test_schema_drift_across_months_rejected(tmp_path: Path) -> None:
    headings = ["BATTERY", "OPER", "FIELD", "BTY", "OIL PROD", "GAS PROD",
                "GAS FLARED", "GAS VENTED", "WTR PROD", "LICENCE", "#"]
    headings.extend(f"EXTRA{i:02d}" for i in range(18))
    headings[-1] = "MODIFIED"
    source = make_archive(tmp_path, overrides={2: {"headings": headings}})
    with pytest.raises(ST60FormatError, match="column schema differs"):
        read_st60_archive(source)


def test_invalid_member_record_rejected_without_partial_output(tmp_path: Path) -> None:
    record = row()
    record[4] = "broken"
    source = make_archive(tmp_path, overrides={3: {"records": [record]}})
    with pytest.raises(ST60FormatError, match="invalid numeric value"):
        read_st60_archive(source)


def test_archive_rejects_invalid_zip(tmp_path: Path) -> None:
    source = tmp_path / "ST60_2025.zip"
    source.write_bytes(b"not a zip")
    with pytest.raises(ST60FormatError, match="Invalid ST60 ZIP archive"):
        read_st60_archive(source)


def test_archive_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_st60_archive(tmp_path / "ST60_2025.zip")


def test_wrong_archive_filename(tmp_path: Path) -> None:
    with pytest.raises(ST60FormatError, match=r"ST60_YYYY.zip"):
        read_st60_archive(tmp_path / "archive.zip")


@pytest.mark.integration
def test_real_2025_archive_reconciles_day1_profile() -> None:
    root = Path(__file__).resolve().parents[1]
    source = root / "data" / "raw" / "ST60_2025.zip"
    if not source.is_file():
        pytest.skip("Original 2025 ZIP is not available in data/raw/")
    result = read_st60_archive(source)
    assert len(result.batches) == 12
    assert len(result.records) == 100637
    assert not result.records.duplicated(["report_month", "BATTERY CODE"]).any()
    assert all(len(batch.records.columns) == 36 for batch in result.batches)
    assert result.records["report_month"].nunique() == 12
    assert result.records["source_sha256"].nunique() == 12
