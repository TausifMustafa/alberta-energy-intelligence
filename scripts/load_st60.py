"""Load one ST60 monthly CSV or historical ZIP into local PostgreSQL.

Examples:
    python scripts/load_st60.py data/raw/ST60_2026-08.csv
    python scripts/load_st60.py data/raw/ST60_2025.zip
"""

import argparse
import getpass
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from alberta_energy.archive import read_st60_archive
from alberta_energy.database import load_st60_month
from alberta_energy.ingest import read_st60_csv
from alberta_energy.quality import assess_st60_quality


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Official ST60_YYYY-MM.csv or ST60_YYYY.zip")
    parser.add_argument("--allow-partial-year", action="store_true", help="Allow missing months in a ZIP")
    args = parser.parse_args()
    if not args.source.is_file():
        parser.error(f"Source not found: {args.source}")

    if args.source.suffix.lower() == ".zip":
        archive = read_st60_archive(
            args.source,
            require_complete_year=not args.allow_partial_year,
            record_mode="raw",
        )
        batches = archive.batches
    elif args.source.suffix.lower() == ".csv":
        batches = (read_st60_csv(args.source, record_mode="raw"),)
    else:
        parser.error("Source must be a monthly .csv or annual .zip file")

    try:
        import psycopg
    except ImportError as exc:
        raise SystemExit(
            "Psycopg is not installed. Run: python -m pip install -r requirements.txt"
        ) from exc

    password = os.environ.get("AEI_DB_PASSWORD") or getpass.getpass(
        "Password for PostgreSQL user aei_loader: "
    )
    config = {
        "host": os.environ.get("AEI_DB_HOST", "localhost"),
        "port": int(os.environ.get("AEI_DB_PORT", "5432")),
        "dbname": os.environ.get("AEI_DB_NAME", "alberta_energy_intelligence"),
        "user": os.environ.get("AEI_DB_USER", "aei_loader"),
        "password": password,
        "connect_timeout": 10,
        "autocommit": True,
    }

    # One connection; a separate transaction for each month in the archive.
    with psycopg.connect(**config) as connection:
        for batch in batches:
            quality = assess_st60_quality(batch)
            outcome = load_st60_month(connection, batch, quality)
            print(
                f"{outcome.report_month}: {outcome.status}; "
                f"source={outcome.source_sha256[:12]}; "
                f"input={outcome.input_rows:,}, "
                f"accepted={outcome.accepted_rows:,}, "
                f"quarantined={outcome.quarantined_rows:,}, "
                f"issues={outcome.issues:,}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
