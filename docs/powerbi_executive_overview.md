# Executive Overview — Power BI report

## Implementation status and scope

The Executive Overview was built and visually reviewed in Power BI Desktop during Day 9.
The owner confirmed that the saved `AlbertaEnergyIntelligence_Day9.pbix` was
closed and reopened successfully. That file is local-only at
`data/processed/powerbi/` and is intentionally excluded from Git.

**Source:** Alberta Energy Regulator (AER), [ST60 monthly battery reporting](https://www.aer.ca/data-and-performance-reports/statistical-reports/st60).
This report concerns ST60 reporting batteries, not all Alberta production or
all upstream facilities. It does not establish regulatory compliance, operational
causation, emissions mass, or whether a reporting battery was physically active.

**Validated data snapshot:** January 2023–August 2026 (44 months,
371,244 accepted battery-month observations). Newer source revisions or
future refreshes can change results.

## Architecture and fields

Power BI Desktop uses Import mode from PostgreSQL view
`aei.v_fact_battery_month`, plus `aei.v_dim_date`,
`aei.v_dim_operator`, and `aei.v_dim_battery`. The validated one-to-many,
single-direction relationships are documented in [powerbi_model.md](powerbi_model.md).
`aei.v_monthly_kpis` is not imported as another Power BI fact.

The page contains:

| Component | Field/measure | Business use |
| --- | --- | --- |
| Year dropdown | `DimDate[report_year]` | Calendar-year selection |
| Reporting Month / Period | `DimDate[month_start]` in *Between* mode | Select a continuous range of reporting months |
| Operator dropdown | `DimOperator[oper_code]` | Historically meaningful operator-code filtering |
| Field Centre dropdown | `FactBatteryMonth[field_centre]` | Filter reporting observations by recorded field centre |
| Oil card | `[Oil Produced (m³)]` | Reported oil quantity |
| Gas card | `[Gas Produced (1,000 m³)]` | Reported gas quantity |
| Flaring card | `[Gas Flared (1,000 m³)]` | Reported flared gas quantity |
| Venting card | `[Gas Vented (1,000 m³)]` | Reported vented gas quantity |
| Battery card | `[Reporting Battery Codes]` | Distinct battery *codes* in the selected context |
| Oil trend | `DimDate[year_month]`, `[Oil Produced (m³)]` | Monthly reported oil trend |
| Gas trend | `DimDate[year_month]`, `[Gas Produced (1,000 m³)]` | Monthly reported gas trend |
| Flaring/venting trend | `DimDate[year_month]`, flared and vented measures | Compare same-unit gas quantities |
| Operator ranking | `DimOperator[oper_code]`, `[Oil Produced (m³)]` | Top 10 by selected-period reported oil volume |
| Period indicator | `[Reporting Period Context]` | Earliest/latest observed month and number of observed months |

The reporting-period measure is copied in
[`powerbi/dax/reporting_period_context.md`](../powerbi/dax/reporting_period_context.md).
The six additive/count measures were established during Day 8 and are recorded in
[`powerbi/dax/core_measures.md`](../powerbi/dax/core_measures.md).

The 1920 × 1080 (16:9) page has a light background and consistent visual cards.
The color convention is teal for oil, blue for gas, amber for flared gas, and
purple for vented gas. The reusable [theme JSON](../powerbi/theme/Alberta_Energy_Intelligence_Theme.json)
records the intended palette; the local PBIX is authoritative for final visual
formatting.

### Filtering and interpretive details

- The `Between` control remains a date-based slider/calendar, even though
  `month_start` denotes the first day of a reporting month. Use first-of-month
  endpoints when selecting month ranges.
- The Year and month-range slicers combine. Set **Year = All** for ranges
  crossing multiple years, such as August 2025–June 2026.
- The period indicator uses actual fact observations under the current filters.
  A range's first and last reporting months do not guarantee every intervening
  month has records for every operator or field centre.
- `Reporting Battery Codes` is `DISTINCTCOUNT(battery_code)`, not operating
  facility count. `latest_observed_name` is a tooltip/display attribute only:
  grouping uses historical `oper_code`, and original `operator_name` remains on
  each fact observation.
- Production trend Y-axes intentionally use nonzero lower bounds to make
  fluctuations visible. Judge changes using axis values and tooltips, not
  visual steepness alone. Never interpret the chart as daily production.
- Do not substitute missing values with zero or average percentages across
  battery observations without an explicit denominator methodology.
- 2026 contains **January–August only** in this snapshot. Do not compare it
  with all 12 months of 2025 when reporting year-over-year conclusions.
- Water and intensity-style KPIs are intentionally not shown; water-unit and
  gas-denominator interpretations require further source-manual review.

## Validation evidence — Day 9

The owner executed read-only PostgreSQL queries through SQLTools and shared
screenshots. Power BI cards/rankings were compared to those outputs. Values
match **at the displayed rounding precision**; screenshots are not proof of
bitwise equivalence with PostgreSQL's exact `NUMERIC` sums.

### January–August 2026, all operators and centres

| KPI | PostgreSQL exact displayed source precision | Power BI card |
| --- | ---: | ---: |
| Oil produced (m³) | 91,107,077.1 | 91.11M |
| Gas produced (1,000 m³) | 21,241,534.6 | 21.24M |
| Gas flared (1,000 m³) | 561,264.5 | 561.26K |
| Gas vented (1,000 m³) | 55,689.4 | 55.69K |
| Distinct reporting battery codes | 9,117 | 9,117 |

### Top 10 operator codes by reported oil production, Jan–Aug 2026

The ten operator codes, ordering, and rounded bar-label values were checked
against the owner's SQLTools result:

| Rank | Operator code | PostgreSQL oil (m³) |
| ---: | --- | ---: |
| 1 | A5D4 | 26,283,104.9 |
| 2 | 0HE9 | 15,893,557.2 |
| 3 | 0054 | 9,994,871.2 |
| 4 | 0007 | 6,020,983.9 |
| 5 | A6LC | 2,885,335.5 |
| 6 | A8FG | 2,558,128.6 |
| 7 | A7GR | 2,424,586.3 |
| 8 | A04N | 2,150,976.2 |
| 9 | 0RL9 | 1,629,897.5 |
| 10 | A5D1 | 1,573,843.5 |

This is a **volume ranking**, not an operator performance, safety, or
compliance ranking.

### August 2026 filter

| KPI | Independent source/SQL baseline | Power BI displayed |
| --- | ---: | ---: |
| Oil produced (m³) | 11,935,345.6 | 11.94M |
| Gas produced (1,000 m³) | 2,702,926.9 | 2.70M |
| Gas flared (1,000 m³) | 75,771.9 | 75.77K |
| Gas vented (1,000 m³) | 7,100.2 | 7.10K |
| Distinct reporting battery codes | 8,156 | 8,156 |

Filtering to operator `0007` with August 2026 selected visibly changed
all five cards, reduced the operator chart to that code, and retained a
single observed reporting month. The owner's field-centre filtering check
was reported to work. After clearing those filters, the full Jan–Aug
2026 values returned.

These interaction checks do **not** establish independent SQL validation
for every operator, field centre, or possible custom date range.

## Reproduce the checks

Run read-only [`sql/validate_executive_overview.sql`](../sql/validate_executive_overview.sql)
against the same active PostgreSQL reporting snapshot as the PBIX import.
Compare each query at the correct selection/precision. Refresh the local
Import-mode model before comparing if source files were revised.

The PBIX and any source data remain local; no dashboard or screenshot has
been published or committed. Screenshots, safe PBIP definitions, and possible
interactive sharing require a separate privacy, AER terms, and data-exposure
review **and explicit owner approval**.

## Follow-up

- Day 10: build Production Performance with matched-period analysis, after
  independent SQL validation of any newly added time-intelligence measures.
- Before a portfolio release: verify source terms and report provenance,
  review whether screenshots or PBIP contain disclosable details, test a
  reproducible opening path, and obtain owner approval.
