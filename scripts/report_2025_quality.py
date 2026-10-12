
"""Assess and reconcile all 12 months of the 2025 AER ST60 archive."""

import sys
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "raw" / "ST60_2025.zip"
OUTPUT_ROOT = ROOT / "data" / "processed" / "quality" / "2025"

MEASURES = (
    "OIL PROD (M3)",
    "GAS PROD (1000 M3)",
    "GAS FLARED (1000 M3)",
    "GAS VENTED (1000 M3)",
    "WTR PROD (M3)",
)


def finite_source_total(values: pd.Series) -> float:
    """Sum parseable finite source values for independent reconciliation."""
    parsed = pd.to_numeric(values.str.strip(), errors="coerce")
    parsed = parsed.replace(
        [float("inf"), float("-inf")],
        float("nan"),
    )
    return float(parsed.sum())


def main() -> None:
    """Produce per-month and full-year ST60 data-quality summaries."""
    sys.path.insert(0, str(ROOT / "src"))

    from alberta_energy.archive import read_st60_archive
    from alberta_energy.quality import assess_st60_quality
    from alberta_energy.quality_report import write_quality_artifacts

    if not SOURCE.is_file():
        raise SystemExit(f"Source archive not found: {SOURCE}")

    archive = read_st60_archive(SOURCE, record_mode="raw")

    totals = Counter()
    reasons = Counter()
    assessments = []

    volume_totals = {
        column: {
            "source": 0.0,
            "accepted": 0.0,
            "quarantined": 0.0,
        }
        for column in MEASURES
    }

    print("=" * 78)
    print("AER ST60 — 2025 HISTORICAL DATA QUALITY REPORT")
    print("=" * 78)
    print(f"Archive: {SOURCE.name}")
    print(f"SHA-256: {archive.sha256}")
    print(f"Reporting months: {len(archive.batches)}")
    print(f"Missing months: {len(archive.missing_months)}")

    print("\nMONTHLY RECORD CLASSIFICATION")
    print(
        f"{'Month':<10} {'Input':>9} {'Accepted':>10} "
        f"{'Rejected':>10} {'Warnings':>10}"
    )

    for batch in archive.batches:
        result = assess_st60_quality(batch)
        summary = result.summary
        assessments.append((batch, result))

        if (
            summary["input_rows"]
            != summary["accepted_rows"] + summary["quarantined_rows"]
        ):
            raise RuntimeError(
                f"Row reconciliation failed for {batch.report_month:%Y-%m}"
            )

        totals.update(summary)

        if not result.issues.empty:
            reasons.update(result.issues["reason_code"].tolist())

        print(
            f"{batch.report_month:%Y-%m} "
            f"{summary['input_rows']:>9,} "
            f"{summary['accepted_rows']:>10,} "
            f"{summary['quarantined_rows']:>10,} "
            f"{summary['warning_rows']:>10,}"
        )

        # Independently reconcile every important measure, month by month.
        for column in MEASURES:
            source_total = finite_source_total(
                result.raw_records[column]
            )
            accepted_total = float(result.accepted[column].sum())
            rejected_total = finite_source_total(
                result.quarantined[column]
            )

            difference = source_total - (
                accepted_total + rejected_total
            )

            if abs(difference) > 0.05:
                raise RuntimeError(
                    f"{batch.report_month:%Y-%m}: "
                    f"volume reconciliation failed for {column}; "
                    f"difference={difference}"
                )

            volume_totals[column]["source"] += source_total
            volume_totals[column]["accepted"] += accepted_total
            volume_totals[column]["quarantined"] += rejected_total

    if totals["input_rows"] != len(archive.records):
        raise RuntimeError(
            "Archive record count differs from monthly input totals"
        )

    print("\nFULL-YEAR QUALITY SUMMARY")
    for key in (
        "input_rows",
        "accepted_rows",
        "quarantined_rows",
        "warning_rows",
        "error_issues",
        "warning_issues",
    ):
        print(f"{key:<25}: {totals[key]:,}")

    print("\nQUALITY ISSUES BY REASON")
    if not reasons:
        print("No validation issues detected")
    else:
        for reason, count in reasons.most_common():
            print(f"  {reason}: {count:,}")

    print("\nFULL-YEAR VOLUME RECONCILIATION")
    print("Source totals include only parseable finite values.")
    print("Units are preserved in each measure name.")

    for column, values in volume_totals.items():
        difference = values["source"] - (
            values["accepted"] + values["quarantined"]
        )

        if abs(difference) > 0.05:
            raise RuntimeError(
                f"Annual reconciliation failed for {column}"
            )

        print(f"\n{column}")
        print(f"  Source:      {values['source']:,.1f}")
        print(f"  Accepted:    {values['accepted']:,.1f}")
        print(f"  Quarantined: {values['quarantined']:,.1f}")
    print("\nWRITING QUALITY ARTIFACTS")

    for batch, result in assessments:
        output_dir = write_quality_artifacts(
            batch,
            result,
            OUTPUT_ROOT,
        )
        print(
            f"  {batch.report_month:%Y-%m}: "
            f"{output_dir.name}"
        )

    print(f"Monthly quality folders written: {len(assessments)}")
    print(f"Output location: {OUTPUT_ROOT}")

    print("\nRow-count reconciliation: PASS")
    print("Monthly and annual volume reconciliation: PASS")
    print("\nAssessment complete.")
    print("Quality warnings do not establish regulatory violations.")


if __name__ == "__main__":
    main()
