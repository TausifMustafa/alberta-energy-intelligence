-- Read-only analytical views audit. Run as aei_loader after analytics.sql.
-- Expected: zero rows for checks 2 and 4; check 3 returns three
-- zero-valued missing-key counts. Other queries display descriptive values.

-- 1. Coverage and totals by year (2026 Jan-Aug is a partial year).
SELECT EXTRACT(YEAR FROM report_month)::int AS report_year,
       COUNT(*) AS reporting_months,
       SUM(battery_month_rows) AS reporting_rows,
       SUM(oil_prod_m3) AS oil_prod_m3,
       SUM(gas_prod_1000_m3) AS gas_prod_1000_m3,
       SUM(wtr_prod_m3) AS wtr_prod_m3,
       SUM(gas_flared_1000_m3) AS gas_flared_1000_m3,
       SUM(gas_vented_1000_m3) AS gas_vented_1000_m3
FROM aei.v_monthly_kpis
GROUP BY EXTRACT(YEAR FROM report_month)
ORDER BY report_year;

-- 2. Duplicate analytical fact keys: expected zero rows.
SELECT report_month, battery_code, COUNT(*) AS copies
FROM aei.v_fact_battery_month
GROUP BY report_month, battery_code
HAVING COUNT(*) <> 1;

-- 3. Missing dimension matches: expected 0, 0, 0 and exactly as many
-- calendar days as are in the complete inclusive reported-date window.
SELECT
    COUNT(*) FILTER (WHERE d.calendar_date IS NULL) AS missing_date_keys,
    COUNT(*) FILTER (WHERE o.oper_code IS NULL) AS missing_operator_keys,
    COUNT(*) FILTER (WHERE b.battery_code IS NULL) AS missing_battery_keys
FROM aei.v_fact_battery_month AS f
LEFT JOIN aei.v_dim_date AS d ON d.calendar_date = f.report_month
LEFT JOIN aei.v_dim_operator AS o ON o.oper_code = f.oper_code
LEFT JOIN aei.v_dim_battery AS b ON b.battery_code = f.battery_code;

-- 4. Compare monthly KPI view against INDEPENDENT aggregation directly
-- from the existing current-source view. Expected zero rows. NULLs
-- are compared safely using IS DISTINCT FROM.
WITH source_monthly AS (
    SELECT report_month,
           COUNT(*) AS row_count,
           COUNT(DISTINCT battery_code) AS battery_count,
           SUM(oil_prod_m3) AS oil,
           SUM(gas_prod_1000_m3) AS gas,
           SUM(wtr_prod_m3) AS water,
           SUM(gas_flared_1000_m3) AS flared,
           SUM(gas_vented_1000_m3) AS vented,
           COUNT(oil_prod_m3) AS oil_coverage,
           COUNT(gas_prod_1000_m3) AS gas_coverage,
           COUNT(wtr_prod_m3) AS water_coverage,
           COUNT(gas_flared_1000_m3) AS flared_coverage,
           COUNT(gas_vented_1000_m3) AS vented_coverage
    FROM aei.v_st60_current_battery_month
    GROUP BY report_month
)
SELECT COALESCE(s.report_month, k.report_month) AS failed_month
FROM source_monthly AS s
FULL JOIN aei.v_monthly_kpis AS k ON k.report_month = s.report_month
WHERE s.report_month IS NULL OR k.report_month IS NULL
   OR s.row_count IS DISTINCT FROM k.battery_month_rows
   OR s.battery_count IS DISTINCT FROM k.reporting_battery_codes
   OR s.oil IS DISTINCT FROM k.oil_prod_m3
   OR s.gas IS DISTINCT FROM k.gas_prod_1000_m3
   OR s.water IS DISTINCT FROM k.wtr_prod_m3
   OR s.flared IS DISTINCT FROM k.gas_flared_1000_m3
   OR s.vented IS DISTINCT FROM k.gas_vented_1000_m3
   OR s.oil_coverage IS DISTINCT FROM k.oil_reported_rows
   OR s.gas_coverage IS DISTINCT FROM k.gas_reported_rows
   OR s.water_coverage IS DISTINCT FROM k.water_reported_rows
   OR s.flared_coverage IS DISTINCT FROM k.flared_reported_rows
   OR s.vented_coverage IS DISTINCT FROM k.vented_reported_rows
ORDER BY failed_month;

-- 5. Missing vs reported zero: both are retained as different states.
SELECT report_month,
       COUNT(*) FILTER (WHERE oil_prod_m3 IS NULL) AS missing_oil,
       COUNT(*) FILTER (WHERE oil_prod_m3 = 0) AS reported_zero_oil,
       COUNT(*) FILTER (WHERE gas_prod_1000_m3 IS NULL) AS missing_gas,
       COUNT(*) FILTER (WHERE gas_prod_1000_m3 = 0) AS reported_zero_gas
FROM aei.v_fact_battery_month
GROUP BY report_month
ORDER BY report_month DESC
LIMIT 6;

-- 6. Comparable YTD totals: latest year and previous year through the
-- same month number. Months covered are explicit, no full-year mixing.
WITH anchor AS (
    SELECT MAX(report_month) AS latest_month FROM aei.v_monthly_kpis
), matching AS (
    SELECT EXTRACT(YEAR FROM k.report_month)::integer AS report_year,
           COUNT(*) AS months_in_period,
           SUM(k.oil_prod_m3) AS oil_prod_m3,
           SUM(k.gas_prod_1000_m3) AS gas_prod_1000_m3
    FROM aei.v_monthly_kpis AS k
    CROSS JOIN anchor AS a
    WHERE EXTRACT(YEAR FROM k.report_month) IN
        (EXTRACT(YEAR FROM a.latest_month), EXTRACT(YEAR FROM a.latest_month) - 1)
      AND EXTRACT(MONTH FROM k.report_month) <= EXTRACT(MONTH FROM a.latest_month)
    GROUP BY EXTRACT(YEAR FROM k.report_month)
)
SELECT report_year, months_in_period, oil_prod_m3, gas_prod_1000_m3
FROM matching ORDER BY report_year;

-- 7. Historical operator changes by reported battery code. Descriptive
-- only: operator-code changes must NOT be treated as an error or failure.
SELECT COUNT(*) AS battery_codes_with_multiple_reported_operators
FROM (
    SELECT battery_code
    FROM aei.v_fact_battery_month
    GROUP BY battery_code
    HAVING COUNT(DISTINCT oper_code) > 1
) AS changed;
