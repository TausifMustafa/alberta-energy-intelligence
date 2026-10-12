# ST60 ingestion and validation pipeline

Alberta Energy Intelligence uses a single reusable command-line workflow for
standalone monthly ST60 CSVs, historical ZIPs, or a directory containing both.
Original files are placed in ignored `data/raw/`; they are never edited or
included in Git.

## Architecture

`src/alberta_energy/ingest.py` reads monthly CSVs, verifies the reported period,
units and observed 29-column layout, and preserves leading-zero identifiers.
`archive.py` reads annual archives in memory, supporting both `ST60-2023.zip`
and `ST60_2024.zip` naming conventions, nested members and original archive
checksums. `quality.py` classifies records and preserves raw values;
`database.py` performs version-aware PostgreSQL transactions. `pipeline.py`
coordinates source discovery, validation, loading and reconciliation. The only
command-line entry point for that workflow is `scripts/load_st60.py`.

## Commands (project-root PowerShell, active `.venv`)

```powershell
# Preflight: no PostgreSQL connection or database writes.
python scripts/load_st60.py --validate data/raw

# Read-only verification of existing active PostgreSQL rows against source files.
python scripts/load_st60.py --verify-db data/raw

# Write approved sources transactionally; an identical revision is skipped.
python scripts/load_st60.py --load data/raw

# The same command works for an individual future monthly CSV or yearly ZIP.
python scripts/load_st60.py --validate data/raw/ST60_2026-08.csv
python scripts/load_st60.py --load data/raw/ST60_2026-08.csv
```

`--validate` is the default when no mode is specified. Loading **always**
requires `--load`; this prevents accidental database writes. The pipeline
uses environment variables from `.env.example`, or prompts for the
`aei_loader` password (never display or commit it). The optional
`--manifest <path>` controls where the local JSON validation manifest is
written; the default is `data/processed/st60_manifest.json`, excluded from Git.

The directory mode scans official ST60 CSV/ZIP filenames, compares every month
to the canonical 29-column/units mapping, rejects duplicate months and
requires a continuous period from earliest to latest discovered source. Use
`--allow-gaps` only when reviewing an intentional partial collection. Annual
ZIPs require 12 months by default; `--allow-partial-year` is an explicit
exception. A single monthly file does not require unrelated months on disk.

The manifest records source SHA-256, archive member, accepted/quarantined
counts, reasons for quality issues and five core exact-decimal volume totals.
**Review any quarantine or warning findings before loading**. A load with
findings is blocked by default; `--load --allow-quality-issues` is an explicit
opt-in after review. Warnings are not automatically invalid measurements.

When loading, the pipeline re-reads the source and checks the SHA-256 and
quality counts against preflight before each monthly transaction. The loader
persists source snapshots, source rows, issues and typed accepted facts; then
independently reconciles all 19 numeric source fields in PostgreSQL before
activating a revision. After loading, the pipeline checks selected months,
source checksums, raw/accepted/issue counts and five core aggregates. For a
read-only check against already stored sources, use `--verify-db`.

For a separate SQL audit, run `sql/validate_st60.sql` on the project database
using SQLTools or `psql`. Its coverage query checks gaps **between** the first
and last active months; validating a particular target date range still
requires an explicitly chosen source set.

## Data safety and limitations

- A failed monthly transaction rolls back that month's candidate revision;
  earlier successfully committed archive months remain. Old source revisions
  stay stored, with only one active for analysis.
- Missing numeric values are preserved as missing, not zero; the source's gas
  quantities are in **1,000 m³**, while oil/water quantities are in **m³**.
- Operator and other battery attributes can change over time; they are not
  treated as permanent properties of a battery.
- Preflight requires our observed source format. New official columns,
  metadata layouts, licensing or definitions must be inspected before use.
- Passing validation does not prove accuracy, operational failure or
  regulatory compliance. ST60 is battery-level reporting, not all production.
