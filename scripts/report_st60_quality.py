
"""Report data quality and reconciliation for the August 2026 ST60 CSV."""

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "raw" / "ST60_2026-08.csv"

MEASURES = (
    "OIL PROD (M3)",
    "GAS PROD (1000 M3)",
    "GAS FLARED (1000 M3)",
    "GAS VENTED (1000 M3)",
    "WTR PROD (M3)",
)


def parse_source_numbers(values: pd.Series) -> pd.Series:
    """Parse source values for independent numerical reconciliation."""
    parsed = pd.to_numeric(values.str.strip(), errors="coerce")
    return parsed.replace(
        [float("inf"), float("-inf")],
        float("nan"),
    )


def main() -> None:
    """Assess one real source file and print an auditable summary."""
    sys.path.insert(0, str(ROOT / "src"))

    from alberta_energy.ingest import read_st60_csv
    from alberta_energy.quality import assess_st60_quality

    if not SOURCE.is_file():
        raise SystemExit(f"Source file not found: {SOURCE}")

    batch = read_st60_csv(SOURCE, record_mode="raw")
    result = assess_st60_quality(batch)
    summary = result.summary

    print("=" * 65)
    print("AER ST60 — DATA QUALITY REPORT")
    print("=" * 65)
    print(f"Source: {SOURCE.name}")
    print(f"Reporting month: {batch.report_month:%Y-%m}")
    print(f"Source SHA-256: {batch.sha256}")

    print("\nROW CLASSIFICATION")
    print(f"Input records:       {summary['input_rows']:,}")
    print(f"Accepted records:    {summary['accepted_rows']:,}")
    print(f"Quarantined records: {summary['quarantined_rows']:,}")
    print(f"Warning-only rows:   {summary['warning_rows']:,}")
    print(f"Error issues:        {summary['error_issues']:,}")
    print(f"Warning issues:      {summary['warning_issues']:,}")

    accounted = (
        summary["accepted_rows"]
        + summary["quarantined_rows"]
    )

    if accounted != summary["input_rows"]:
        raise RuntimeError("Record-count reconciliation failed")

    print("Row-count reconciliation: PASS")

    print("\nISSUES BY REASON")
    if result.issues.empty:
        print("No validation issues detected")
    else:
        counts = result.issues["reason_code"].value_counts()
        for reason, count in counts.items():
            print(f"  {reason}: {count:,}")

        print("\nSAMPLE ISSUES (UP TO 10)")
        for _, issue in result.issues.head(10).iterrows():
            print(
                f"  Row {issue['source_row_number']}: "
                f"{issue['reason_code']} "
                f"[{issue['column']}] "
                f"value={issue['raw_value']!r}"
            )

    print("\nVOLUME RECONCILIATION")
    print("All totals retain the ST60 units shown in column names.")
    print("Source totals include only parseable finite values.")
    print(
        f"{'Measure':<27} {'Source':>15} "
        f"{'Accepted':>15} {'Quarantined':>15}"
    )

    for column in MEASURES:
        source_values = parse_source_numbers(
            result.raw_records[column]
        )
        quarantined_values = parse_source_numbers(
            result.quarantined[column]
        )

        source_total = float(source_values.sum())
        accepted_total = float(result.accepted[column].sum())
        quarantined_total = float(quarantined_values.sum())

        difference = source_total - (
            accepted_total + quarantined_total
        )

        if abs(difference) > 0.05:
            raise RuntimeError(
                f"Volume reconciliation failed for {column}: "
                f"difference={difference}"
            )

        print(
            f"{column:<27} "
            f"{source_total:>15,.1f} "
            f"{accepted_total:>15,.1f} "
            f"{quarantined_total:>15,.1f}"
        )

    print("\nVolume reconciliation: PASS")
    print("\nData-quality assessment complete.")
    print("Warnings are not evidence of reporting violations.")


if __name__ == "__main__":
    main()
