// Query name in Power BI: FactBatteryMonth
// ST60 reporting battery / month grain; preserve codes as TEXT.
let
    Source = PostgreSQL.Database("localhost:5432", "alberta_energy_intelligence"),
    ActiveFact = Source{[Schema="aei", Item="v_fact_battery_month"]}[Data],
    Typed = Table.TransformColumnTypes(
        ActiveFact,
        {
            {"report_month", type date},
            {"battery_code", type text},
            {"oper_code", type text},
            {"operator_name", type text},
            {"field_centre", type text},
            {"bty_type", type text},
            {"bty_location", type text},
            {"licence_num", type text},
            {"bty_description", type text},
            {"total_wells", type number},
            {"oil_prod_m3", type number},
            {"gas_prod_1000_m3", type number},
            {"wtr_prod_m3", type number},
            {"gas_flared_1000_m3", type number},
            {"gas_vented_1000_m3", type number},
            {"source_file_id", Int64.Type},
            {"source_row_number", Int64.Type},
            {"source_file_name", type text},
            {"source_sha256", type text}
        },
        "en-US"
    )
in
    Typed
