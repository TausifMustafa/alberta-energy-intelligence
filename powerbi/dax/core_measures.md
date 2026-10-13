# Day 8 Power BI DAX — copy one measure at a time

Create each measure with **Modeling > New measure** while selecting `FactBatteryMonth` in the Data pane. This Markdown file is a reference, **not a single DAX script** to paste into the editor. Leave the numeric field columns as `Do not summarize` in report visuals: use explicit measures for totals.

## Additive ST60 measures

```dax
Oil Produced (m³) =
SUM ( FactBatteryMonth[oil_prod_m3] )
```

```dax
Gas Produced (1,000 m³) =
SUM ( FactBatteryMonth[gas_prod_1000_m3] )
```

```dax
Gas Flared (1,000 m³) =
SUM ( FactBatteryMonth[gas_flared_1000_m3] )
```

```dax
Gas Vented (1,000 m³) =
SUM ( FactBatteryMonth[gas_vented_1000_m3] )
```

```dax
Water Produced (m³, source heading) =
SUM ( FactBatteryMonth[wtr_prod_m3] )
```

**Note:** The water unit label reproduces the ingested CSV heading and remains provisional pending an AER source-document/manual cross-check. Do not present water units as independently source-certified until reconciled.

## Counts: distinguish records and entities

```dax
Battery-Month Records =
COUNTROWS ( FactBatteryMonth )
```

```dax
Reporting Battery Codes =
DISTINCTCOUNT ( FactBatteryMonth[battery_code] )
```

```dax
Reporting Months =
DISTINCTCOUNT ( FactBatteryMonth[report_month] )
```

`Reporting Battery Codes` counts distinct reporting codes in the **current filter context** (so selecting several months doesn't sum repeated batteries). It is **not** the number of active/operating physical facilities.

## Optional single-month time comparison (after core totals validate)

Only use this formula on a visual with **exactly one `DimDate[year_month]`** per row, such as a matrix showing one reporting month per row. At the all-time/annual card level it deliberately returns blank. Select individual contiguous months when comparing; a non-contiguous day selection can cause classic `DATEADD` to error.

```dax
Oil YoY % (monthly only) =
VAR CurrentOil = [Oil Produced (m³)]
VAR PriorOil =
    CALCULATE (
        [Oil Produced (m³)],
        DATEADD ( DimDate[calendar_date], -1, YEAR )
    )
RETURN
    IF (
        HASONEVALUE ( DimDate[year_month] )
            && NOT ISBLANK ( CurrentOil )
            && NOT ISBLANK ( PriorOil ),
        DIVIDE ( CurrentOil - PriorOil, PriorOil ),
        BLANK ()
    )
```

Format this measure as a percentage with 1–2 decimal places. `DIVIDE` intentionally returns blank for zero or missing previous-year oil production. A monthly YoY change is descriptive, not evidence of a specific operational cause. Do **not** use this measure as a substitute for validated matched-month YTD reporting; that is a subsequent milestone.

## Display formatting and safeguards

For the initial comparison visuals, choose decimal formatting with one decimal place and display units **None**. Do not silently convert gas from `1,000 m³` to `m³`, and do not coalesce blanks to zero. Power Query's `type number` is a floating-point representation, while PostgreSQL `NUMERIC` is exact; compare values at the published/source reporting precision rather than claiming bitwise-exact double/decimal parity. Avoid adding a calculated column for simple additive quantities or hardcoding a 2026 reporting endpoint into production measures.
