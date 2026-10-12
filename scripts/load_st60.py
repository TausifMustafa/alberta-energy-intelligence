"""Validate, load or verify ST60 source files using one reusable command.

Examples (run from the repository root):
    python scripts/load_st60.py --validate data/raw
    python scripts/load_st60.py --verify-db data/raw
    python scripts/load_st60.py --load data/raw/ST60_2026-08.csv
    python scripts/load_st60.py --load data/raw
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from alberta_energy.pipeline import (
    PipelineError,
    connect_database,
    load_sources,
    preflight,
    verify_database,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--validate", action="store_true", help="Preflight only (default; no DB connection)")
    mode.add_argument("--load", action="store_true", help="Preflight, then load and reconcile PostgreSQL")
    mode.add_argument("--verify-db", action="store_true", help="Preflight and read-only PostgreSQL reconciliation")
    parser.add_argument("source", type=Path, help="One ST60 CSV/ZIP or a directory of source files")
    parser.add_argument("--allow-partial-year", action="store_true", help="Permit fewer than 12 months in a yearly ZIP")
    parser.add_argument("--allow-gaps", action="store_true", help="Permit nonconsecutive months in a directory")
    parser.add_argument(
        "--allow-quality-issues", action="store_true",
        help="Permit a reviewed load with quarantined rows or warnings",
    )
    parser.add_argument(
        "--manifest", type=Path,
        default=ROOT / "data" / "processed" / "st60_manifest.json",
        help="Local JSON validation report (ignored under data/processed/)",
    )
    args = parser.parse_args()
    if args.allow_quality_issues and not args.load:
        parser.error("--allow-quality-issues requires --load")

    try:
        paths, reports = preflight(
            args.source,
            allow_partial_year=args.allow_partial_year,
            allow_gaps=args.allow_gaps,
            manifest_path=args.manifest,
        )
        if not (args.load or args.verify_db):
            print("Validation only: PostgreSQL was not modified.")
            return 0
        with connect_database() as connection:
            if args.load:
                load_sources(
                    connection, paths, reports,
                    allow_partial_year=args.allow_partial_year,
                    allow_quality_issues=args.allow_quality_issues,
                )
            verify_database(connection, reports)
        return 0
    except (PipelineError, ValueError, OSError) as exc:
        print(f"ST60 pipeline stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
