# Day 5: PostgreSQL loading

## What the loader does

The project database is `alberta_energy_intelligence`, with tables in the `aei` schema.
Run `sql/schema.sql` as `postgres` once before loading data. The Python loader
connects as `aei_loader`, which has data permissions but does not own tables.

`python scripts/load_st60.py <source>` supports a monthly `ST60_YYYY-MM.csv`
or a historical `ST60_YYYY.zip`. The loader runs the existing raw ingestion and
quality assessment first. It stores all source rows and quality issues, then
stores accepted records with source-correct units. Original source text stays
in `st60_source_row.raw_values`, while `st60_battery_month` stores numeric fields
as exact decimals and optional blank attributes as SQL NULL.

Each month is one transaction. The loader inserts all rows, reconciles the
counts and all 19 numeric source fields against SQL sums, then activates that
source version. A failure rolls back the whole month, including revision flags.
A repeated month with the same SHA-256 is skipped. A changed monthly file is
stored as a new version and becomes active after successful reconciliation.
Older versions remain in the source tables, not in the active Power BI view.

If a file checksum exists but its quality counts differ, loading stops for
review. This protects against silently treating a changed quality rule as the
same validated snapshot. Quality rule version tracking is a later improvement.
A changed yearly ZIP containing identical monthly bytes does not create new
monthly versions; the original archive provenance is retained.

## Set up and run (VS Code PowerShell)

With `.venv` active and from the repository root:

```powershell
python -m pip install -r requirements.txt
python -m pytest -q
ruff check .
python scripts/load_st60.py data/raw/ST60_2026-08.csv
python scripts/load_st60.py data/raw/ST60_2025.zip
python scripts/load_st60.py data/raw/ST60_2026-08.csv
```

Enter the `aei_loader` password when prompted; input is not displayed. Do not
save the real password in Git or send it to ChatGPT.

Use `sql/check_st60_load.sql` in the Alberta Energy Intelligence SQLTools
connection, or run it using `psql -f` with that database selected.

Expected after loading the previously validated files: 12 months of 2025
containing 100,637 accepted observations, plus 8,156 for August 2026.
The active view should have 108,793 observations across 13 months. The final
repeat of August 2026 should say `already_loaded` and not change row counts.
These are expected results, not proof of database execution.

## Known limitations

- The loader assumes the currently verified 29-column ST60 format. Inspect
  unfamiliar archive years before trying to load them.
- The current quality rules classify negative values as warnings, not errors.
  SQL intentionally does not reject those values.
- `st60_battery_month` records source reporting quantities, not production
  from every well or all Alberta facilities.
- Each month in a yearly archive commits separately. If a later month fails,
  earlier successful months remain loaded and can be safely skipped on retry.
- Initial PostgreSQL loading was verified locally on October 12, 2026, using
  the 2025 archive and August 2026 CSV. Live source-revision replacement and
  failed-transaction rollback still need dedicated tests.
- Test data is synthetic; live data and totals must be independently verified.
