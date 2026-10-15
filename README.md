# Alberta Energy Intelligence

**Oil & Gas Production, Flaring, Venting & Operational Analytics**

Alberta Energy Intelligence is a data analytics and business intelligence project focused on exploring Alberta's oil and gas production, flaring, and venting patterns using publicly available Alberta Energy Regulator (AER) data.

The project aims to develop an end-to-end analytical pipeline using **Python, pandas, PostgreSQL, SQL, and Microsoft Power BI** to transform raw regulatory reporting data into interactive dashboards, production trends, operational KPIs, and explainable anomaly flags.

### Project Status

**In Development — Working Power BI Executive Overview**

Implemented reusable Python ingestion for monthly AER ST60 CSV files and historical ZIP archives, including source validation, SHA-256 provenance tracking, and automated pytest testing.

Added data cleaning, record-level validation, quarantine handling, quality reports, and source-volume reconciliation. Validated the pipeline against 12 months of 2025 ST60 data (100,637 records) and August 2026 data (8,156 records).

PostgreSQL tables and a version-aware Python loader are implemented. The database now contains **371,244 accepted battery-month records across 44 reporting months (January 2023-August 2026)**. Source-to-database counts and five core measurements reconciled for the Day 6 backfill. Live PostgreSQL integration testing verified source revisions, identical-source skipping, and failed-load rollback. The analytical SQL layer now provides a validated battery-month fact view, date/operator/battery dimensions, and monthly KPIs. The Power BI semantic model and Executive Overview dashboard are implemented and validated locally. The Production Performance, Flaring & Venting, and Facility Investigation pages, along with explainable anomaly detection, remain upcoming milestones.

### Data Source

[Alberta Energy Regulator — ST60 Monthly Production, Flaring and Venting Data](https://www.aer.ca/data-and-performance-reports/statistical-reports/st60)

ST60 contains reported battery-level data and does not represent all Alberta oil and gas production. This independent educational project is not affiliated with or endorsed by the AER.

## Reusable ST60 ETL Workflow

Use the unified command to validate, load, and reconcile ST60 data:

```powershell
python scripts/load_st60.py --validate data/raw
python scripts/load_st60.py --verify-db data/raw
python scripts/load_st60.py --load data/raw
```

Validation is read-only. Loading requires the explicit `--load` option.
Individual monthly CSVs and annual ZIPs are also supported.

See [Data Pipeline](docs/data_pipeline.md) for commands and safeguards,
and [Database](docs/database.md) for schema, provenance, revisions,
and reconciliation. Run `sql/validate_st60.sql` for a separate SQL audit.

## ST60 Data Ingestion and Quality Validation

The Python pipeline supports standalone monthly ST60 CSV files and
historical yearly ZIP archives.

### Ingestion modes

- **Strict (default):** Validates source structure, required identifiers, duplicate battery codes, and numeric fields. A record-level validation error stops ingestion of the source file or archive.
- **Raw:** Preserves original field text, including whitespace, while enforcing structural checks. A separate quality processor then classifies accepted and quarantined records.

Both modes preserve reporting periods and source provenance,
including SHA-256 checksums and original CSV row numbers.

### Data-quality rules

The quality processor classifies records as accepted or quarantined.

Records are quarantined for:

- Missing required battery or operator identifiers.
- Duplicate battery identifiers within a reporting month.
- Invalid nonblank numeric values.

Warnings are recorded for:

- Missing core production, flaring, venting, or water measurements.
- Negative numeric quantities requiring further review.

Missing values remain distinct from reported zeros.
Original source values are preserved for auditing.

### Running the quality reports

Place original AER files in `data/raw/`:

- `ST60_2026-08.csv`
- `ST60_2025.zip`

From the project root, with the Python virtual environment activated:

```powershell
python scripts/report_st60_quality.py
python scripts/report_2025_quality.py
python -m pytest -q
ruff check .
```

The historical quality report generates monthly artifacts under:

`data/processed/quality/2025/`

Each monthly folder contains:

- `summary.json` — reporting period, checksums, and quality counts.
- `issues.csv` — validation findings with reason codes.
- `quarantined.csv` — rejected records with original source values.

Generated files and original raw data remain excluded from Git.

### Verified source reconciliation

**2025 historical archive**
- 12 reporting months.
- 100,637 source records.
- 100,637 accepted records.
- 0 quarantined records under the implemented rules.
- Monthly and annual reconciliation checks passed.

**August 2026 CSV**
- 8,156 source records.
- 8,156 accepted records.
- 0 quarantined records under the implemented rules.
- Five major volume measures reconciled with source totals.

These results apply only to the source snapshots and validation
rules tested. Zero recorded issues does not establish complete
data accuracy, completeness of optional attributes, or regulatory
compliance.

ST60 reports battery-level quantities and does not represent
all Alberta oil and gas production. Original reporting units are
preserved, including oil in m³ and gas volumes in thousands of m³.

Data source: Alberta Energy Regulator (AER), ST60.

See [Analytical SQL and KPI definitions](docs/analytics.md) for the reporting model, relationships, and validation rules.

See [Power BI Executive Overview](docs/powerbi_executive_overview.md)
for dashboard design, validated results, interactive filters, and limitations.
