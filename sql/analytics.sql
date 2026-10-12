-- Read-only analytical layer for AER ST60 active reporting snapshots.
-- Apply as the database administrator to alberta_energy_intelligence.
-- Does not alter existing tables or source-version activation rules.
-- The aei_loader role has read-only access to these views.

BEGIN;

-- One accepted reporting battery per reporting month from the current
-- source version only. Historical operator and location values stay on
-- the observation instead of being treated as permanent battery attributes.
CREATE OR REPLACE VIEW aei.v_fact_battery_month AS
SELECT
    report_month,
    battery_code,
    oper_code,
    operator_name,
    field_centre,
    bty_type,
    bty_location,
    licence_num,
    bty_description,
    total_wells,
    oil_prod_m3,
    gas_prod_1000_m3,
    wtr_prod_m3,
    gas_flared_1000_m3,
    gas_vented_1000_m3,
    source_file_id,
    source_row_number,
    source_file_name,
    source_sha256
FROM aei.v_st60_current_battery_month;

-- One row per calendar day. The source fact remains monthly and has
-- report_month set to the first day of each reporting month.
-- A daily contiguous calendar supports Power BI's classic time intelligence.
CREATE OR REPLACE VIEW aei.v_dim_date AS
WITH bounds AS (
    SELECT
        MIN(report_month)::date AS first_day,
        (MAX(report_month) + INTERVAL '1 month - 1 day')::date AS last_day
    FROM aei.st60_source_file
    WHERE is_active
)
SELECT
    days.calendar_date::date AS calendar_date,
    DATE_TRUNC('month', days.calendar_date)::date AS month_start,
    EXTRACT(YEAR FROM days.calendar_date)::integer AS report_year,
    EXTRACT(QUARTER FROM days.calendar_date)::integer AS report_quarter,
    EXTRACT(MONTH FROM days.calendar_date)::integer AS month_number,
    TO_CHAR(days.calendar_date, 'YYYY-MM') AS year_month,
    (
        EXTRACT(YEAR FROM days.calendar_date)::integer * 100
        + EXTRACT(MONTH FROM days.calendar_date)::integer
    ) AS year_month_sort,
    EXTRACT(DAY FROM days.calendar_date)::integer = 1
        AS is_month_start
FROM bounds
CROSS JOIN LATERAL GENERATE_SERIES(
    bounds.first_day, bounds.last_day, INTERVAL '1 day'
) AS days(calendar_date);

-- One row per operator code. The name is a convenient latest-observed
-- display label ONLY; historical attribution comes from the fact's
-- report_month + oper_code + operator_name on each original observation.
CREATE OR REPLACE VIEW aei.v_dim_operator AS
WITH observations AS (
    SELECT
        oper_code,
        MIN(report_month) AS first_report_month,
        MAX(report_month) AS last_report_month,
        COUNT(DISTINCT operator_name) AS observed_name_variants
    FROM aei.v_fact_battery_month
    GROUP BY oper_code
),
latest_names AS (
    SELECT DISTINCT ON (oper_code)
        oper_code,
        operator_name AS latest_observed_name
    FROM aei.v_fact_battery_month
    WHERE operator_name IS NOT NULL
    ORDER BY
        oper_code,
        report_month DESC,
        source_file_id DESC,
        source_row_number DESC
)
SELECT
    obs.oper_code,
    names.latest_observed_name,
    obs.first_report_month,
    obs.last_report_month,
    obs.observed_name_variants
FROM observations AS obs
LEFT JOIN latest_names AS names
    ON names.oper_code = obs.oper_code;

-- One row per *reported battery code*. The code may not necessarily
-- identify an unchanged physical facility through every historical period.
CREATE OR REPLACE VIEW aei.v_dim_battery AS
SELECT
    battery_code,
    MIN(report_month) AS first_report_month,
    MAX(report_month) AS last_report_month,
    COUNT(*) AS reported_months
FROM aei.v_fact_battery_month
GROUP BY battery_code;

-- Monthly quality/reconciliation summary. Use for SQL spot-checks;
-- do not separately relate this aggregate to the detailed fact in
-- Power BI's initial star schema (that can create ambiguous totals).
-- SQL SUM preserves NULL if *every* reported value in a group is missing.
CREATE OR REPLACE VIEW aei.v_monthly_kpis AS
SELECT
    report_month,
    COUNT(*) AS battery_month_rows,
    COUNT(DISTINCT battery_code) AS reporting_battery_codes,
    SUM(oil_prod_m3) AS oil_prod_m3,
    SUM(gas_prod_1000_m3) AS gas_prod_1000_m3,
    SUM(wtr_prod_m3) AS wtr_prod_m3,
    SUM(gas_flared_1000_m3) AS gas_flared_1000_m3,
    SUM(gas_vented_1000_m3) AS gas_vented_1000_m3,
    COUNT(oil_prod_m3) AS oil_reported_rows,
    COUNT(gas_prod_1000_m3) AS gas_reported_rows,
    COUNT(wtr_prod_m3) AS water_reported_rows,
    COUNT(gas_flared_1000_m3) AS flared_reported_rows,
    COUNT(gas_vented_1000_m3) AS vented_reported_rows
FROM aei.v_fact_battery_month
GROUP BY report_month;

COMMENT ON VIEW aei.v_fact_battery_month IS
    'Current ST60 snapshot: one accepted battery code per reporting month; original units retained.';
COMMENT ON VIEW aei.v_dim_date IS
    'Continuous daily calendar; fact observations occur on month-start dates only.';
COMMENT ON VIEW aei.v_dim_operator IS
    'One code per operator. Latest name is a label, not historical attribution.';
COMMENT ON VIEW aei.v_dim_battery IS
    'One reporting battery code; historical attributes live on the fact.';
COMMENT ON VIEW aei.v_monthly_kpis IS
    'Monthly totals and non-null coverage for checks; not an additional Power BI fact.';

GRANT USAGE ON SCHEMA aei TO aei_loader;
GRANT SELECT ON
    aei.v_fact_battery_month,
    aei.v_dim_date,
    aei.v_dim_operator,
    aei.v_dim_battery,
    aei.v_monthly_kpis
TO aei_loader;

COMMIT;
