// Query name in Power BI: DimDate
// One row per day, 2023-01-01 to 2026-08-31 at the verified baseline.
let
    Source = PostgreSQL.Database("localhost:5432", "alberta_energy_intelligence"),
    DateView = Source{[Schema="aei", Item="v_dim_date"]}[Data],
    Typed = Table.TransformColumnTypes(
        DateView,
        {
            {"calendar_date", type date},
            {"month_start", type date},
            {"report_year", Int64.Type},
            {"report_quarter", Int64.Type},
            {"month_number", Int64.Type},
            {"year_month", type text},
            {"year_month_sort", Int64.Type},
            {"is_month_start", type logical}
        },
        "en-US"
    )
in
    Typed
