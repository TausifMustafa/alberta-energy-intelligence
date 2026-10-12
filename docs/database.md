# PostgreSQL ST60 storage and reconciliation

## Database and role

Use PostgreSQL database `alberta_energy_intelligence`, schema `aei`. Apply
`sql/schema.sql` as a database administrator when first setting up the project;
do **not** recreate existing tables for this refactor. The Python pipeline
connects as `aei_loader`, a dedicated login with scoped DML permissions.
Credentials are prompted or supplied through environment variables. The
`.env.example` lists variable names only; real secrets remain local.

## Tables and analytical grain

| Object | Purpose |
| --- | --- |
| `aei.st60_source_file` | Monthly source provenance, checksum, summary and active revision flag |
| `aei.st60_source_row` | Original text for all 29 fields, including quarantined rows |
| `aei.st60_quality_issue` | Reasons/severity linked to source row numbers |
| `aei.st60_battery_month` | One accepted reporting battery per active or historic source version, with typed quantities |
| `aei.v_st60_current_battery_month` | Only the active validated version of each reporting month; start point for Power BI |

The fact grain is one **battery code + reporting month** for the active
snapshot. Keep leading-zero codes as text; do not assume an operator code,
licence or location is fixed for the battery over time. Source `NUMERIC`
columns store exact decimal text, with SQL `NULL` for missing values.

## Idempotency, revisions and recovery

Each monthly load is an atomic PostgreSQL transaction. File SHA-256 plus month
identifies a snapshot; running identical bytes a second time reports
`already_loaded` without duplicating records. Changed source bytes create a
new stored version, which becomes active only after row counts and **all 19**
numeric totals match. The prior version remains for auditing. The loader
serializes concurrent loads for the same month with a PostgreSQL advisory lock.

When a month fails, its candidate source rows and activity change are rolled
back. Earlier successful months in the batch remain committed and may be safely
skipped on retry. Rollback does not necessarily rewind identity sequences.

## Validation

Use the CLI for independent source-to-database reconciliation:

```powershell
python scripts/load_st60.py --verify-db data/raw
```

Or run the read-only SQL file:

```powershell
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -X -v ON_ERROR_STOP=1 -P pager=off -h localhost -p 5432 -U aei_loader -d alberta_energy_intelligence -f ".\sql\validate_st60.sql"
```

The opt-in PostgreSQL integration test (`tests/test_postgres_transactions.py`)
uses synthetic reporting month 2022-12 in an outer transaction that is rolled
back. It checks source replacement, repeated-load skipping and failure
rollback. Run it only on the intended local test-capable database; PostgreSQL
sequences may advance even though synthetic rows roll back.

## Verified October 12, 2026 baseline

- 2023: 12 months, **102,936** accepted battery-month records.
- 2024: 12 months, **101,910** records.
- 2025: 12 months, **100,637** records.
- 2026 (January–August): 8 months, **65,761** records.
- Total: **44 months, 371,244** active accepted records.
- Initial backfill introduced **262,451** new records, zero quarantined or
  warned under implemented rules. The additional 31 months reconciled on
  five source totals and all stored counts.
- Independent SQL reported no missing months, invalid active-version months,
  duplicate battery-month keys or stored count discrepancies.
- At this baseline the opt-in database test passed for revision activation,
  skipping identical sources and rolling back an injected failure. Future
  concurrency and official-source revision scenarios remain follow-ups.

The above is a dated **verified historical snapshot**, not a claim that new
refactored code is already validated or that the source never changes. AER
ST60 is not all Alberta production; source review and attribution remain
required before downstream Power BI interpretation.
