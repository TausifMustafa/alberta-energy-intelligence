# Analytical SQL model and KPI contracts

## Purpose and source

This layer sits on top of `aei.v_st60_current_battery_month`, which
contains only the validated active source revision for each reporting month.
The versioned source and typed storage tables remain untouched. Run
`sql/analytics.sql` **as the local PostgreSQL administrator** to create views;
`aei_loader` receives SELECT on them. Run `sql/validate_analytics.sql`
read-only as `aei_loader` after applying the views.

AER ST60 is *reported battery-level* crude oil and bitumen production and
flaring/venting, not all production in Alberta. The recorded dimensions and
measures are observations, not confirmed regulatory or operational findings.

## Model objects and grains

| SQL view | Grain | Power BI plan |
| --- | --- | --- |
| `aei.v_fact_battery_month` | One accepted battery code per reporting month | Import as **FactBatteryMonth** |
| `aei.v_dim_date` | One day in continuous observed reporting calendar | Import as **DimDate** |
| `aei.v_dim_operator` | One operator code | Import as **DimOperator** |
| `aei.v_dim_battery` | One reporting battery code | Import as **DimBattery** |
| `aei.v_monthly_kpis` | One report month | SQL validation only for MVP; do **not** also join to the detailed fact |

Power BI relationships should all be **one-to-many, single-direction**, from
unique dimension keys to fact keys:

- `DimDate[calendar_date]` -> `FactBatteryMonth[report_month]` (active).
- `DimOperator[oper_code]` -> `FactBatteryMonth[oper_code]` (active).
- `DimBattery[battery_code]` -> `FactBatteryMonth[battery_code]` (active).

Only the first of each month has a reported fact. The daily date dimension is
required for a unique, gapless date calendar suitable for classic DAX time
intelligence. Mark `DimDate[calendar_date]` as the Date table in Power BI if
using classic time intelligence. Keep report visuals and user slicers at
**month or higher grain**: do not present daily ST60 production. Hide
`is_month_start` and daily-level date fields from report users where useful.
`DimDate[year_month]` sorts by `DimDate[year_month_sort]`.

No synthetic integer dimension keys are required yet because reporting codes
are already single-column keys. Do not relate operator and battery dimensions
to each other: use the fact's monthly observations for their association.

## Changing attributes and historical attribution

The operator code and `operator_name` are retained **on each fact observation**.
`DimOperator[latest_observed_name]` is only a convenient **current-ish display
label**, not the operator name at any past date. When investigating history,
show the fact's recorded `operator_name` alongside `report_month`, `oper_code`
and battery code. Group historical production by `oper_code`, never by a
possibly changing name alone. A battery code is treated as a reporting key;
this alone does not prove the underlying physical facility was unchanged.
Battery location, type, name, licence and operator are therefore intentionally
*not* fixed attributes of `DimBattery`.

## Initial KPI definitions

| KPI | Definition | Unit / caveat |
| --- | --- | --- |
| Oil produced | `SUM(oil_prod_m3)` | m³ of ST60 reported battery oil production |
| Gas produced | `SUM(gas_prod_1000_m3)` | **1,000 m³** (not m³) |
| Water produced | `SUM(wtr_prod_m3)` | m³ per the currently ingested ST60 CSV headings; recheck source manual before publishing |
| Gas flared | `SUM(gas_flared_1000_m3)` | 1,000 m³ of reported gas |
| Gas vented | `SUM(gas_vented_1000_m3)` | 1,000 m³ of reported gas |
| Reporting batteries, selected period | `DISTINCTCOUNT(FactBatteryMonth[battery_code])` | Distinct codes with a record, **not** proof of being operational |
| Battery-month observations | `COUNTROWS(FactBatteryMonth)` | Sums each reporting month's records; may recount a code across months |

Source `NULL` means missing; `0` means explicitly reported zero. PostgreSQL
`SUM` returns `NULL` for all-null groups, and `COUNT(column)` identifies non-null
coverage. **Do not use COALESCE to replace missing source values with zero.**
For time-series and operator visualizations, calculate totals from the fact;
`v_monthly_kpis` exists for independent reconciliation only. Use DAX `DIVIDE`
for ratios, and only after deciding a defensible numerator and denominator.
An overall ratio of totals is **not** the mean of row-level ratios.

For an initial MVP, defer water-to-oil ratio and flaring/venting intensity
until numerator/denominator coverage, domain definitions and rules for tiny or
zero denominators have been reviewed. Exclude unsupported negative conclusions.

## Comparable-period comparisons

2026 has data only for January through August in the baseline. Compare
**January-August 2026** with **January-August 2025**, not calendar 2026 with
full-year 2025. A SQL example is included in `sql/validate_analytics.sql`.
Before calculating YTD or YoY percentage change, ensure the compared reporting
months exist and are equivalent; if there is no usable baseline, show NULL or
BLANK, not infinite growth. Do not infer incidents or noncompliance from changes.

## Test and maintenance expectations

Apply views in one transaction using `psql -v ON_ERROR_STOP=1`. The schema
remains the same, and base facts are never modified by this script. Run
`sql/validate_analytics.sql`, the regular pytest/Ruff checks, and the opt-in
`tests/test_postgres_analytics.py` against your local database. Its connection
sets `default_transaction_read_only=on`. Two important independent checks are:
aggregation from the **original active-data view** and Python `Decimal`
summation for selected months. The test suite is not a substitute for a live
Power BI filter/DAX validation after model import.

As of the bundle's preparation, the tests and SQL are **drafted, not executed**
against the owner's PostgreSQL database. No Git commit or push has occurred.
