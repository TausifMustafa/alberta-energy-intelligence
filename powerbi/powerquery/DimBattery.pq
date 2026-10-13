// Query name in Power BI: DimBattery
// A battery code is a reporting key, not an immutable facility description.
let
    Source = PostgreSQL.Database("localhost:5432", "alberta_energy_intelligence"),
    BatteryView = Source{[Schema="aei", Item="v_dim_battery"]}[Data],
    Typed = Table.TransformColumnTypes(
        BatteryView,
        {
            {"battery_code", type text},
            {"first_report_month", type date},
            {"last_report_month", type date},
            {"reported_months", Int64.Type}
        },
        "en-US"
    )
in
    Typed
