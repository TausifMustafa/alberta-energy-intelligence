"""Synthetic CSV tests plus an optional, pinned real-source reconciliation."""

import csv
import hashlib
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from alberta_energy.ingest import ST60FormatError, read_st60_csv

# Headings and units are deliberately separate, like the observed ST60 layout.
FIELDS = [
    ("BATTERY", "CODE"),
    ("OPER", "CODE"),
    ("FIELD", "CENTRE"),
    ("BTY", "TYPE"),
    ("OIL PROD", "(M3)"),
    ("GAS PROD", "(1000 M3)"),
    ("GAS FLARED", "(1000 M3)"),
    ("GAS VENTED", "(1000 M3)"),
    ("WTR PROD", "(M3)"),
    ("LICENCE", "NUM"),
    ("#", "WELLS"),
    *[(f"EXTRA{i:02d}", "") for i in range(18)],
]
assert len(FIELDS) == 29


def row(battery: str = "000123", operator: str = "0012") -> list[str]:
    values = [""] * len(FIELDS)
    values[0] = battery
    values[1] = operator
    values[2] = "A"
    values[3] = "B"
    values[4:9] = ["12.5", "20", "0", "1.5", "80"]
    values[9] = ""  # Missing licence is permitted.
    values[10] = "2"
    return values


def write_csv(
    directory: Path,
    *,
    month: str = "2026-08",
    metadata: str = "Aug 2026 Data",
    records: list[list[str]] | None = None,
    headings: list[str] | None = None,
    units: list[str] | None = None,
) -> Path:
    path = directory / f"ST60_{month}.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow([metadata])
        writer.writerow(headings if headings is not None else [h for h, _ in FIELDS])
        writer.writerow(units if units is not None else [u for _, u in FIELDS])
        writer.writerows([row()] if records is None else records)
    return path


def test_valid_source_keeps_codes_units_and_provenance(tmp_path: Path) -> None:
    source = write_csv(tmp_path, records=[row(), row("000124", "0013")])
    batch = read_st60_csv(source)

    assert batch.report_month == date(2026, 8, 1)
    assert batch.metadata == ("Aug 2026 Data",)
    assert batch.encoding == "utf-8-sig"
    assert batch.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert len(batch.records) == 2
    assert batch.records["BATTERY CODE"].tolist() == ["000123", "000124"]
    assert batch.records["OPER CODE"].tolist() == ["0012", "0013"]
    assert batch.records["source_row_number"].tolist() == [4, 5]
    assert batch.records["source_file"].eq(source.name).all()
    assert batch.records["source_sha256"].eq(batch.sha256).all()
    assert batch.records["report_month"].eq(pd.Timestamp("2026-08-01")).all()
    assert batch.records["OIL PROD (M3)"].sum() == pytest.approx(25.0)
    assert batch.records["GAS FLARED (1000 M3)"].eq(0).all()
    assert batch.records["LICENCE NUM"].eq("").all()


def test_blank_numeric_is_missing_not_zero(tmp_path: Path) -> None:
    record = row()
    record[4] = ""
    source = write_csv(tmp_path, records=[record])
    actual = read_st60_csv(source).records
    assert pd.isna(actual.loc[0, "OIL PROD (M3)"])
    assert actual.loc[0, "GAS FLARED (1000 M3)"] == 0.0


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_st60_csv(tmp_path / "ST60_2026-08.csv")


def test_empty_file(tmp_path: Path) -> None:
    source = tmp_path / "ST60_2026-08.csv"
    source.write_bytes(b"")
    with pytest.raises(ST60FormatError, match="Empty ST60 file"):
        read_st60_csv(source)


def test_missing_header_row(tmp_path: Path) -> None:
    source = tmp_path / "ST60_2026-08.csv"
    source.write_text("Aug 2026 Data\n", encoding="utf-8")
    with pytest.raises(ST60FormatError, match="three ST60 header rows"):
        read_st60_csv(source)


def test_missing_required_column(tmp_path: Path) -> None:
    headings = [h for h, _ in FIELDS]
    headings[0] = "UNKNOWN"
    source = write_csv(tmp_path, headings=headings)
    with pytest.raises(ST60FormatError, match="Missing required ST60 columns"):
        read_st60_csv(source)


def test_incorrect_row_width(tmp_path: Path) -> None:
    source = write_csv(tmp_path, records=[row()[:-1]])
    with pytest.raises(ST60FormatError, match="expected 29 fields, got 28"):
        read_st60_csv(source)


def test_invalid_nonblank_numeric(tmp_path: Path) -> None:
    record = row()
    record[4] = "not-a-number"
    source = write_csv(tmp_path, records=[record])
    with pytest.raises(ST60FormatError, match="invalid numeric value.*OIL PROD"):
        read_st60_csv(source)


def test_infinite_numeric_rejected(tmp_path: Path) -> None:
    record = row()
    record[4] = "inf"
    source = write_csv(tmp_path, records=[record])
    with pytest.raises(ST60FormatError, match="invalid numeric value"):
        read_st60_csv(source)


def test_wrong_period_metadata(tmp_path: Path) -> None:
    source = write_csv(tmp_path, metadata="Jul 2026 Data")
    with pytest.raises(ST60FormatError, match="does not confirm 2026-08"):
        read_st60_csv(source)


def test_invalid_filename_month(tmp_path: Path) -> None:
    source = tmp_path / "ST60_2026-13.csv"
    source.write_text("bad month", encoding="utf-8")
    with pytest.raises(ST60FormatError, match="Invalid reporting month"):
        read_st60_csv(source)


def test_duplicate_battery_code(tmp_path: Path) -> None:
    source = write_csv(tmp_path, records=[row(), row("000123")])
    with pytest.raises(ST60FormatError, match="Repeated BATTERY CODE"):
        read_st60_csv(source)


def test_missing_battery_code(tmp_path: Path) -> None:
    source = write_csv(tmp_path, records=[row("")])
    with pytest.raises(ST60FormatError, match="blank required BATTERY CODE"):
        read_st60_csv(source)


def test_missing_operator_code(tmp_path: Path) -> None:
    source = write_csv(tmp_path, records=[row(operator="")])
    with pytest.raises(ST60FormatError, match="blank required OPER CODE"):
        read_st60_csv(source)


def test_no_reporting_records(tmp_path: Path) -> None:
    source = write_csv(tmp_path, records=[])
    with pytest.raises(ST60FormatError, match="no reporting records"):
        read_st60_csv(source)


@pytest.mark.integration
def test_august_2026_real_source_reconciliation() -> None:
    """Runs locally if the ignored Day 1 reference CSV is present."""
    root = Path(__file__).resolve().parents[1]
    source = root / "data" / "raw" / "ST60_2026-08.csv"
    if not source.is_file():
        pytest.skip("Real August ST60 CSV is not available in data/raw/")

    batch = read_st60_csv(source)
    assert batch.sha256 == (
        "3ce87fd870f44e69a980620ea570ce9cc840a45173b9868839796ff4668cd3a3"
    ), "Source snapshot differs from Day 1; check whether AER revised the file"
    frame = batch.records
    assert len(frame) == 8156
    assert frame["BATTERY CODE"].nunique() == 8156
    assert frame["OPER CODE"].nunique() == 350
    totals = {
        "OIL PROD (M3)": 11935345.6,
        "GAS PROD (1000 M3)": 2702926.9,
        "GAS FLARED (1000 M3)": 75771.9,
        "GAS VENTED (1000 M3)": 7100.2,
        "WTR PROD (M3)": 50238113.3,
    }
    for column, expected in totals.items():
        assert frame[column].sum() == pytest.approx(expected, abs=0.05)
    assert frame["OIL PROD (M3)"].eq(0).sum() == 1248
    assert frame["GAS FLARED (1000 M3)"].eq(0).sum() == 6580


def test_duplicate_header_names(tmp_path: Path) -> None:
    headings = [h for h, _ in FIELDS]
    units = [u for _, u in FIELDS]
    headings[-1], units[-1] = headings[-2], units[-2]
    source = write_csv(tmp_path, headings=headings, units=units)
    with pytest.raises(ST60FormatError, match="repeated combined"):
        read_st60_csv(source)


def test_source_without_utf8_uses_cp1252(tmp_path: Path) -> None:
    source = write_csv(tmp_path, metadata="Aug 2026 Data - Café")
    text = source.read_text(encoding="utf-8-sig")
    source.write_bytes(text.encode("cp1252"))
    batch = read_st60_csv(source)
    assert batch.encoding == "cp1252"
    assert batch.records["BATTERY CODE"].iloc[0] == "000123"
