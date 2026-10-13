# Power BI import semantic model — Day 8 implementation guide

## Status and scope

These files are **ready to apply**, not proof that a Power BI model was created or tested. Your already-verified PostgreSQL Day 7 views are the source. Use Power BI Desktop **Import** mode, with PostgreSQL connection as the restricted `aei_loader` account. Do **not** publish or expose the database. The source PostgreSQL `NUMERIC` measures may be represented as floating-point decimal numbers in Power BI, so validate at the published reporting precision.

## Files

- `powerbi/powerquery/FactBatteryMonth.pq`: M query for `aei.v_fact_battery_month`.
- `powerbi/powerquery/DimDate.pq`: M query for `aei.v_dim_date`.
- `powerbi/powerquery/DimOperator.pq`: M query for `aei.v_dim_operator`.
- `powerbi/powerquery/DimBattery.pq`: M query for `aei.v_dim_battery`.
- `powerbi/dax/core_measures.md`: **individual** DAX formulas for New measure.
- `sql/validate_powerbi.sql`: independent SQL checkpoints.

## Batch A — connect and import four queries

1. Launch Power BI Desktop. Start a **blank report** (no existing report to overwrite). Go to **Home > Get data > PostgreSQL database**. Set **Server** to `localhost:5432`, **Database** to `alberta_energy_intelligence`, and **Data Connectivity mode** to **Import**. Leave the optional native SQL statement blank. Use **Database** authentication with username `aei_loader`; enter the password locally. For a normal local connection, follow any security/encryption prompts deliberately; don't disable TLS in a remote production connection just to bypass an error.
2. In **Navigator**, expand schema `aei` and select **only** `v_fact_battery_month`, `v_dim_date`, `v_dim_operator`, `v_dim_battery`. Click **Transform data**, not Load.
3. Rename the queries exactly to `FactBatteryMonth`, `DimDate`, `DimOperator`, `DimBattery`. For each query, open **Advanced Editor** and replace its contents with the matching `.pq` file. Each file is a complete M query; copy its full content. The server/database names in the files are fixed to your known local setup; edit them only if yours differs.
4. Verify the **Applied Steps** and column types: month/calendar dates are **Date** (not text or Date/Time with a hidden time component), `battery_code`/`oper_code` and optional licence are **Text** (preserve leading zeroes), quantities are **Decimal Number**, and provenance columns have their intended types. **Never replace NULL with zero**. Leave all 44 source months included; do not filter 2023-2025 or August 2026 out at ingestion.
5. Choose **Close & Apply**. Check the model's imported tables and query errors. At the verified baseline you should have 371,244 FactBatteryMonth rows and 1,339 DimDate rows (2023-01-01 through 2026-08-31). The operator and battery dimension counts should match `sql/validate_powerbi.sql`, not a guessed fixed number.

Microsoft's PostgreSQL connector has included its Npgsql provider in Power BI Desktop since 2019; a separate driver install is normally not required in supported current Desktop builds. Use the native connector, not an external ODBC shortcut.

## Batch B — validate relationships

In **Model view**, inspect auto-detected relationships first; remove or change wrong paths. Create or verify EXACTLY these relationships:

| From (one side) | To (many side) | Cardinality | Filter direction | Active |
| --- | --- | --- | --- | --- |
| `DimDate[calendar_date]` | `FactBatteryMonth[report_month]` | One to many (1:*) | Single (dimension to fact) | Yes |
| `DimOperator[oper_code]` | `FactBatteryMonth[oper_code]` | One to many (1:*) | Single (dimension to fact) | Yes |
| `DimBattery[battery_code]` | `FactBatteryMonth[battery_code]` | One to many (1:*) | Single (dimension to fact) | Yes |

Do **not** relate `DimDate[month_start]` to the fact. `DimDate[month_start]` repeats for every day of a month, so it cannot be the unique dimension key. Do **not** link DimBattery to DimOperator: operators change across reporting months. Do not load or relate `v_monthly_kpis` to the detailed fact.

Then:

- **Mark DimDate as date table**, selecting `DimDate[calendar_date]` (Table tools or right-click the table). It should pass uniqueness/nonblank/contiguous validations.
- Set `DimDate[year_month]` **Sort by column** = `DimDate[year_month_sort]`. Use `year_month` (e.g., `2026-08`) for axes, not daily dates.
- Prefer to turn off **Auto date/time** for the current file to avoid redundant internal date hierarchies (File > Options and settings > Options > Current File > Data Load > Auto date/time; UI labels vary). Use DimDate's explicit columns in visuals.
- Hide technical fact keys (`source_file_id`, `source_row_number`, `source_sha256`) from **report view** after validating lineage; the columns still remain available in model. For operator slicers, prefer `DimOperator[oper_code]` or a uniquely identified code + label; the `latest_observed_name` field is **not** an as-of name for historical months.

## Batch C — create DAX measures

Open `powerbi/dax/core_measures.md`. Add the eight basic measures **one at a time** using **Modeling > New measure** with `FactBatteryMonth` selected. Format totals with **one decimal place** and **Display units = None** for the validation table. Ensure charts and cards use explicit measures rather than implicit source-column sums.

Optionally add the *single-month-only* YoY example, once the base totals/relationships pass. For 2026-versus-2025 year-to-date comparison, use the comparable-period SQL check first; do not compare a partial 2026 year with the entire 2025 year. A fully tested DAX matched-YTD measure is a subsequent feature, not a Day 8 completion requirement.

## Batch D — independent checks before report styling

From the project root in PowerShell, with `.venv` active:

```powershell
$psql = 'C:\Program Files\PostgreSQL\18\bin\psql.exe'
& $psql -X -v ON_ERROR_STOP=1 -P pager=off -h localhost -p 5432 -U aei_loader -d alberta_energy_intelligence -f '.\sql\validate_powerbi.sql'
if ($LASTEXITCODE -ne 0) { throw 'Power BI SQL checkpoint failed' }
python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'pytest failed' }
ruff check .
if ($LASTEXITCODE -ne 0) { throw 'Ruff failed' }
git status --short
```

Build a **temporary validation table/matrix** in Power BI (not a polished final report page yet):

1. With no filters, use eight measures and compare with query 2 below. Add a month slicer `DimDate[year_month]` and select `2026-08`; compare with query 3.
2. Display `DimDate[year_month]` in table rows and `Oil Produced (m³)` in values. Filter Year to `2026`; the table should have eight months (Jan–Aug). If you see all monthly values repeating or grand totals on each row, the DimDate relationship is wrong or you're using the fact's date instead of DimDate.
3. Select `2026-08` and the first `DimOperator[oper_code]` in SQL query 5. Compare oil and reporting battery counts. If changing the operator slicer leaves measures unchanged, check the relationship, key type, and slicer table.
4. Deselect filters, then filter `2025-01` to `2025-08` and `2026-01` to `2026-08` separately. Compare oil/gas YTD totals with SQL query 4. Do not use a full-year chart to assert partial-year growth.

### Known dated baseline (from locally verified Day 7 results)

| Filter | Battery-month records | Oil produced (m³) | Gas produced (1,000 m³) |
| --- | ---: | ---: | ---: |
| All 44 months | 371,244 | 435,590,258.8 | 108,405,689.1 |
| 2026-08 only | 8,156 | 11,935,345.6 | 2,702,926.9 |
| 2025 Jan–Aug | SQL query 4 | 79,904,678.2 | 20,271,300.1 |
| 2026 Jan–Aug | 65,761 | 91,107,077.1 | 21,241,534.6 |

The all-date numbers are exact decimal additions of independently reconciled 2023–2026 SQL output. Import-mode floating-point results might differ at machine precision, so compare with at least one decimal place (not raw floating point equality). An error of many units is a blocker, not rounding.

## Save and review

Save your local Power BI working file as `data/processed/powerbi/AlbertaEnergyIntelligence.pbix` **only after the model has loaded correctly**; create the directory if necessary. `data/processed/` is already excluded from Git, protecting an imported data snapshot from accidental commits. For Git-friendly collaboration later, consider saving a Power BI Project (`.pbip`) using Desktop after verifying your Desktop build supports it and reviewing its local caches/connection settings. **Do not publish to the Power BI Service or push any report file yet.** Do not stage `.pbix`/`.pbip` files before reviewing their contents and data-licensing implications.

Expected evidence to share: Model view showing relationships; Power Query types for battery and operator code, date and volume; validation visual with overall and Aug 2026 measures; and combined PowerShell SQL/pytest/Ruff/Git output. Do not share credentials.

## Troubleshooting

- **Npgsql/driver error:** inspect Desktop version first; most modern builds include provider. Verify connection uses native PostgreSQL connector.
- **Can't authenticate:** confirm server/database and user, or adjust only local connection details; do not paste passwords or alter role permissions as a first response.
- **Date relationship error:** both date fields must be the same Date type; use `calendar_date`, never repeated `month_start`.
- **Unexpected many-to-many:** don't accept auto-detection; verify unique dim keys and types using query 6.
- **Numeric mismatch:** verify source query and filter context, then decimal display scale before changing ETL logic.
- **Name filter oddities:** latest operator labels aren't historical names; group/filter by `oper_code`, show monthly `operator_name` in investigation details.
- **Water-unit discrepancy:** stop before external claims; reconfirm official AER source units and decide if semantic relabeling or conversion is required.

## Interview check

Explain why `DimDate[month_start]` is not the one-side key, why `DimOperator[latest_observed_name]` cannot replace the fact's original historical `operator_name`, and why `DISTINCTCOUNT(battery_code)` differs from summing monthly battery counts over a year.
