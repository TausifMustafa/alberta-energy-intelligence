# DAX — Reporting Period Context (Executive Overview)

This measure is used in the local Day 9 Power BI Executive Overview.
It provides a **filter-responsive display label**, not a date filter or
a replacement for the SQL reporting-period grain.

Create it under `FactBatteryMonth` with **Modeling > New measure**:

```dax
Reporting Period Context =
VAR FirstMonth =
    MIN ( FactBatteryMonth[report_month] )
VAR LastMonth =
    MAX ( FactBatteryMonth[report_month] )
VAR MonthCount =
    DISTINCTCOUNT ( FactBatteryMonth[report_month] )
RETURN
    IF (
        ISBLANK ( LastMonth ),
        "No ST60 reporting records for this selection",
        IF (
            FirstMonth = LastMonth,
            FORMAT ( FirstMonth, "MMM yyyy" ),
            FORMAT ( FirstMonth, "MMM yyyy" )
                & " - "
                & FORMAT ( LastMonth, "MMM yyyy" )
        )
            & " | "
            & FORMAT ( MonthCount, "0" )
            & IF (
                MonthCount = 1,
                " reporting month",
                " reporting months"
            )
    )
```

### Logic and precautions

- The active `DimDate[calendar_date]` -> `FactBatteryMonth[report_month]`
  relationship propagates the Year and Reporting Month/Period selections.
- `MIN`, `MAX`, and `DISTINCTCOUNT` then operate on the remaining fact
  records, including selected operator or field-centre filters.
- If there are no matching fact records, the measure returns an explicit
  no-record message, not `0` months or a misleading date.
- For multiple months, the endpoints report observed first/last months.
  The month count counts distinct months with records, not necessarily
  every month in the interval.
- The `Between` slicer uses `DimDate[month_start]` (month-level values
  represented as dates); a range crossing calendar years requires
  clearing a conflicting Year selection.

### Observed UI checks (owner-confirmed)

- Year = 2026, remaining filters All: `Jan 2026 - Aug 2026 | 8 reporting months`.
- The user reported the period indicator and interactive custom-range
  slider working and confirmed the PBIX saved, closed, and reopened.
- No automated DAX unit tests or independent SQL checks were run
  specifically for this text measure; its logic is auditable above.
