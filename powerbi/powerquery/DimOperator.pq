// Query name in Power BI: DimOperator
// The name is a latest-observed label; it does NOT overwrite fact history.
let
    Source = PostgreSQL.Database("localhost:5432", "alberta_energy_intelligence"),
    OperatorView = Source{[Schema="aei", Item="v_dim_operator"]}[Data],
    Typed = Table.TransformColumnTypes(
        OperatorView,
        {
            {"oper_code", type text},
            {"latest_observed_name", type text},
            {"first_report_month", type date},
            {"last_report_month", type date},
            {"observed_name_variants", Int64.Type}
        },
        "en-US"
    )
in
    Typed
