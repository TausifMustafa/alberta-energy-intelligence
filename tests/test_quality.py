
"""Tests for Day 4 raw ingestion and data-quality processing."""

from pathlib import Path

from test_ingest import row, write_csv

from alberta_energy.ingest import read_st60_csv


def test_raw_mode_preserves_problematic_source_values(tmp_path: Path) -> None:
    """Raw mode retains problematic rows for later quality assessment."""
    first = row("000123", "0012")
    first[1] = " 0012 "
    first[4] = "not-a-number"

    second = row("000123", "0013")

    source = write_csv(tmp_path, records=[first, second])

    batch = read_st60_csv(source, record_mode="raw")
    records = batch.records

    assert len(records) == 2
    assert records["BATTERY CODE"].tolist() == ["000123", "000123"]
    assert records.loc[0, "OPER CODE"] == " 0012 "
    assert records.loc[0, "OIL PROD (M3)"] == "not-a-number"
    assert records["source_row_number"].tolist() == [4, 5]
    assert records["source_sha256"].eq(batch.sha256).all()


def test_quality_classifies_accepted_and_quarantined_records(
    tmp_path: Path,
) -> None:
    from alberta_energy.quality import assess_st60_quality

    valid = row("000101")
    valid[1] = " 0012 "
    valid[4] = ""  # Missing oil production, not zero.

    invalid = row("000102")
    invalid[4] = "not-a-number"

    duplicate_a = row("000103")
    duplicate_b = row("000103", "0013")

    missing_operator = row("000104", "")

    source = write_csv(
        tmp_path,
        records=[
            valid,
            invalid,
            duplicate_a,
            duplicate_b,
            missing_operator,
        ],
    )

    batch = read_st60_csv(source, record_mode="raw")
    result = assess_st60_quality(batch)

    assert result.summary["input_rows"] == 5
    assert result.summary["accepted_rows"] == 1
    assert result.summary["quarantined_rows"] == 4
    assert result.summary["warning_rows"] == 1

    assert result.accepted["BATTERY CODE"].tolist() == ["000101"]
    assert result.accepted["OPER CODE"].iloc[0] == "0012"

    import pandas as pd

    assert pd.isna(result.accepted["OIL PROD (M3)"].iloc[0])
    assert result.accepted["GAS FLARED (1000 M3)"].iloc[0] == 0

    assert result.quarantined["source_row_number"].tolist() == [
        5, 6, 7, 8
    ]

    reason_codes = result.issues["reason_code"].tolist()
    assert "invalid_numeric" in reason_codes
    assert reason_codes.count("duplicate_battery_month") == 2
    assert "missing_identifier" in reason_codes
    assert "missing_measure" in reason_codes

    # Raw source values remain available for auditing.
    assert result.raw_records.equals(batch.records)
    assert result.raw_records.loc[0, "OPER CODE"] == " 0012 "
    assert result.raw_records.loc[1, "OIL PROD (M3)"] == "not-a-number"

    assert result.accepted["source_sha256"].iloc[0] == batch.sha256
    assert (
        result.summary["input_rows"]
        == result.summary["accepted_rows"]
        + result.summary["quarantined_rows"]
    )


def test_negative_gas_quantity_is_warning_not_automatic_rejection(
    tmp_path: Path,
) -> None:
    from alberta_energy.quality import assess_st60_quality

    record = row("000201")
    record[6] = "-1.5"

    source = write_csv(tmp_path, records=[record])
    batch = read_st60_csv(source, record_mode="raw")

    result = assess_st60_quality(batch)

    assert result.summary["accepted_rows"] == 1
    assert result.summary["quarantined_rows"] == 0
    assert result.summary["warning_rows"] == 1

    assert result.accepted["GAS FLARED (1000 M3)"].iloc[0] == -1.5
    assert "negative_quantity" in result.issues["reason_code"].tolist()


def test_duplicates_detected_after_identifier_cleaning(
    tmp_path: Path,
) -> None:
    from alberta_energy.quality import assess_st60_quality

    first = row(" 000301 ", "0012")
    second = row("000301", "0013")

    source = write_csv(tmp_path, records=[first, second])
    batch = read_st60_csv(source, record_mode="raw")

    result = assess_st60_quality(batch)

    assert result.summary["input_rows"] == 2
    assert result.summary["accepted_rows"] == 0
    assert result.summary["quarantined_rows"] == 2

    assert (
        result.issues["reason_code"]
        .eq("duplicate_battery_month")
        .sum()
        == 2
    )

    # The original identifier remains available for auditing.
    assert result.raw_records.loc[0, "BATTERY CODE"] == " 000301 "
    assert result.quarantined["source_row_number"].tolist() == [4, 5]


def test_missing_zero_and_multiple_warnings(
    tmp_path: Path,
) -> None:
    import pandas as pd

    from alberta_energy.quality import assess_st60_quality

    first = row("000401")
    first[4] = ""       # Missing oil production.
    first[6] = "-1.5"   # Negative gas flared.

    second = row("000402")
    second[4] = "0"     # Reported zero oil production.
    second[6] = "0"     # Reported zero gas flared.

    source = write_csv(tmp_path, records=[first, second])
    batch = read_st60_csv(source, record_mode="raw")

    result = assess_st60_quality(batch)

    assert result.summary["accepted_rows"] == 2
    assert result.summary["quarantined_rows"] == 0

    # Two issues, but only one affected record.
    assert result.summary["warning_rows"] == 1
    assert result.summary["warning_issues"] == 2

    assert pd.isna(result.accepted.loc[0, "OIL PROD (M3)"])
    assert result.accepted.loc[1, "OIL PROD (M3)"] == 0

    assert result.raw_records.loc[0, "OIL PROD (M3)"] == ""
    assert result.raw_records.loc[1, "OIL PROD (M3)"] == "0"


def test_quality_artifacts_preserve_rejected_source_records(
    tmp_path: Path,
) -> None:
    import json

    import pandas as pd

    from alberta_energy.quality import assess_st60_quality
    from alberta_energy.quality_report import write_quality_artifacts

    valid = row("000501", "0012")

    invalid = row("000502", "0013")
    invalid[1] = " 0013 "
    invalid[4] = "not-a-number"

    source = write_csv(
        tmp_path,
        records=[valid, invalid],
    )

    batch = read_st60_csv(source, record_mode="raw")
    result = assess_st60_quality(batch)

    output_dir = write_quality_artifacts(
        batch,
        result,
        tmp_path / "quality_reports",
    )

    assert output_dir.name == f"2026-08_{batch.sha256[:12]}"

    summary = json.loads(
        (output_dir / "summary.json").read_text(
            encoding="utf-8"
        )
    )

    assert summary["report_month"] == "2026-08"
    assert summary["source_sha256"] == batch.sha256
    assert summary["input_rows"] == 2
    assert summary["accepted_rows"] == 1
    assert summary["quarantined_rows"] == 1

    issues = pd.read_csv(
        output_dir / "issues.csv",
        dtype=str,
        keep_default_na=False,
    )

    assert len(issues) == 1
    assert issues.loc[0, "reason_code"] == "invalid_numeric"
    assert issues.loc[0, "severity"] == "error"
    assert issues.loc[0, "source_row_number"] == "5"

    quarantined = pd.read_csv(
        output_dir / "quarantined.csv",
        dtype=str,
        keep_default_na=False,
    )

    assert len(quarantined) == 1
    assert quarantined.loc[0, "BATTERY CODE"] == "000502"
    assert quarantined.loc[0, "OPER CODE"] == " 0013 "
    assert quarantined.loc[0, "OIL PROD (M3)"] == "not-a-number"
    assert quarantined.loc[0, "source_sha256"] == batch.sha256


def test_clean_source_exports_are_empty_and_repeatable(
    tmp_path: Path,
) -> None:
    import csv
    import json

    from alberta_energy.quality import assess_st60_quality
    from alberta_energy.quality_report import write_quality_artifacts

    source = write_csv(
        tmp_path,
        records=[
            row("000701"),
            row("000702"),
        ],
    )

    original_bytes = source.read_bytes()

    batch = read_st60_csv(source, record_mode="raw")
    result = assess_st60_quality(batch)

    output_root = tmp_path / "quality_reports"

    first_output = write_quality_artifacts(
        batch,
        result,
        output_root,
    )

    summary = json.loads(
        (first_output / "summary.json").read_text(
            encoding="utf-8"
        )
    )

    assert summary["input_rows"] == 2
    assert summary["accepted_rows"] == 2
    assert summary["quarantined_rows"] == 0
    assert summary["warning_rows"] == 0

    # Even empty issue/quarantine CSVs must contain headers.
    with (first_output / "issues.csv").open(
        encoding="utf-8",
        newline="",
    ) as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames is not None
        assert "reason_code" in reader.fieldnames
        assert list(reader) == []

    with (first_output / "quarantined.csv").open(
        encoding="utf-8",
        newline="",
    ) as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames is not None
        assert "BATTERY CODE" in reader.fieldnames
        assert list(reader) == []

    # Capture the output before repeating the same export.
    filenames = (
        "summary.json",
        "issues.csv",
        "quarantined.csv",
    )

    original_outputs = {
        name: (first_output / name).read_bytes()
        for name in filenames
    }

    second_output = write_quality_artifacts(
        batch,
        result,
        output_root,
    )

    assert second_output == first_output

    for name in filenames:
        assert (second_output / name).read_bytes() == original_outputs[name]

    # The original ST60 source must remain untouched.
    assert source.read_bytes() == original_bytes


def test_negative_infinity_is_invalid_without_extra_warning(
    tmp_path: Path,
) -> None:
    from alberta_energy.quality import assess_st60_quality

    record = row("000803")
    record[6] = "-inf"

    source = write_csv(tmp_path, records=[record])
    batch = read_st60_csv(source, record_mode="raw")

    result = assess_st60_quality(batch)

    assert result.summary["accepted_rows"] == 0
    assert result.summary["quarantined_rows"] == 1
    assert result.summary["error_issues"] == 1
    assert result.summary["warning_issues"] == 0
    assert result.issues["reason_code"].tolist() == [
        "invalid_numeric"
    ]
