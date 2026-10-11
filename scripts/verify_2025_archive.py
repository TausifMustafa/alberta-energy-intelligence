"""Print an auditable month-by-month summary of the local 2025 ST60 archive."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "raw" / "ST60_2025.zip"


def main() -> None:
    # Source tree is not yet installed as a distributable Python package.
    sys.path.insert(0, str(ROOT / "src"))
    from alberta_energy.archive import read_st60_archive

    result = read_st60_archive(SOURCE)
    print(f"Archive: {result.source_path.name}")
    print(f"Archive SHA-256: {result.sha256}")
    print("MONTH    RECORDS   OIL PROD (M3)   GAS FLARED (1000 M3)")
    for batch in result.batches:
        records = batch.records
        print(
            f"{batch.report_month:%Y-%m}  {len(records):>7,}   "
            f"{records['OIL PROD (M3)'].sum():>13,.1f}   "
            f"{records['GAS FLARED (1000 M3)'].sum():>18,.1f}"
        )
    print(f"TOTAL    {len(result.records):>7,}")
    print(f"Missing months: {len(result.missing_months)}")


if __name__ == "__main__":
    main()
