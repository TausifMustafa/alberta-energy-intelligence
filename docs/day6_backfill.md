# Day 6: Historical ST60 backfill

The target is January 2023 through August 2026 (44 reporting months).
Day 5 already loaded 2025 and August 2026 (13 months). Day 6 adds the two
2023/2024 annual ZIPs and the January--July 2026 monthly CSVs (31 months).

## Source compatibility

The AER archive for 2023 is named `ST60-2023.zip`, rather than the underscore
convention used in 2024/2025. The archive reader accepts both forms while
preserving original archive and member names in source provenance. The 2023
members are stored in a `2023/` folder inside the ZIP. April 2024 also has
extra empty cells in its metadata row. Neither changes its 29 data columns.

## Safe workflow

Put original files in ignored `data/raw/`, without renaming them:

- `ST60-2023.zip`
- `ST60_2024.zip`
- `ST60_2026-01.csv` through `ST60_2026-07.csv`
- `ST60_2026-08.csv` (reference file, already loaded)

First run the read-only preflight:

```powershell
python scripts/backfill_st60.py
python -m pytest -q
ruff check .
```

The preflight checks the existence of all sources, 29-column schema and units
against the August 2026 source, required reporting months, and all existing
record-level quality rules. It prints per-month counts, warnings and rejected
rows, and writes `data/processed/day6_preflight.json` with full source checksums
and five independently summed accepted-source metrics. That report is ignored
by Git. It does **not** connect to or change PostgreSQL.

Review the quality counts before loading. If there are no rejected rows or
warnings, use:

```powershell
python scripts/backfill_st60.py --load
```

This reruns preflight before writing anything. It prompts once for the loader
password, then calls the Day 5 transactional month loader. Each month is one
transaction; earlier months remain committed if a later month fails. Repeating
the same source checksum is skipped. The final check requires 44 active months,
consistent counts and equal source/SQL totals for all five core measures in
the 31 added months.

If there are warnings or quarantined rows, **stop and review** the preflight
summary before deciding whether to run `--load --allow-quality-issues`.
Warnings are not proof of incorrect reporting; quarantined records remain
traceable in the database when an authorized load proceeds.

Use `sql/day6_coverage.sql` for a separate read-only SQL check.

## Verified results - October 12, 2026

- 2023: 12 months, 102,936 accepted records.
- 2024: 12 months, 101,910 accepted records.
- 2025: 12 months, 100,637 accepted records.
- 2026 January-August: 8 months, 65,761 accepted records.
- Total: 44 months, 371,244 accepted battery-month records.
- New Day 6 sources: 262,451 accepted, 0 quarantined, 0 warnings.
- Exactly one active source version per month.
- No missing months or duplicate active battery-month keys.
- Independent SQL check found no source/storage count discrepancies.
- Five source totals reconciled with PostgreSQL for all 31 added months.
- Regular pytest: 53 passed, 1 opt-in test skipped.
- Ruff and dependency checks passed.
- Opt-in live PostgreSQL integration test: 1 passed.
- Synthetic test rows were rolled back; the existing dataset was retained.

These results apply to the tested source snapshots and quality rules.
They do not establish regulatory compliance or complete source accuracy.

## Limits and planned follow-ups

- This run verifies the reporting snapshots on disk, not whether an AER file
  was subsequently revised on the regulator's website.
- Source availability and consistent headers do not guarantee complete
  accuracy of optional fields or stable battery/operator attributes.
- SQL `NUMERIC` preserves source decimals. AER quantities retain their
  original m3 / 1,000 m3 units. Missing is not zero.
- The opt-in PostgreSQL integration test verified revision activation,
  identical-source skipping, and an injected failure rollback using
  synthetic data. Additional concurrency testing remains future work.
- Do not treat a partial 2026 year as comparable to a full 2025 year.
- Raw source files and generated quality reports stay outside GitHub.
