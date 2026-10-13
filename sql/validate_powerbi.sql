-- Read-only Power BI validation anchors from validated active ST60 snapshots.
-- Run as aei_loader; compare with an imported, freshly refreshed Power BI model.
-- Source units: oil/water as ingested CSV headings in m3, gas in 1,000 m3.
BEGIN READ ONLY;

-- 1. Table sizes and date boundaries. These are dynamic, not test fixtures.
SELECT 'FactBatteryMonth' AS model_table, COUNT(*) AS rows FROM aei.v_fact_battery_month
UNION ALL SELECT 'DimDate', COUNT(*) FROM aei.v_dim_date
UNION ALL SELECT 'DimOperator', COUNT(*) FROM aei.v_dim_operator
UNION ALL SELECT 'DimBattery', COUNT(*) FROM aei.v_dim_battery
ORDER BY model_table;

-- 2. All-date baseline: exactly the columns used in initial DAX cards.
SELECT COUNT(*) AS battery_month_records,
       COUNT(DISTINCT battery_code) AS reporting_battery_codes,
       COUNT(DISTINCT report_month) AS reporting_months,
       SUM(oil_prod_m3) AS oil_prod_m3,
       SUM(gas_prod_1000_m3) AS gas_prod_1000_m3,
       SUM(wtr_prod_m3) AS wtr_prod_m3,
       SUM(gas_flared_1000_m3) AS gas_flared_1000_m3,
       SUM(gas_vented_1000_m3) AS gas_vented_1000_m3
FROM aei.v_fact_battery_month;

-- 3. Fixed month and full-year comparisons for the baseline dataset.
-- Aug 2026 must yield 8,156 rows and oil 11,935,345.6 m3.
SELECT report_month,
       COUNT(*) AS battery_month_records,
       COUNT(DISTINCT battery_code) AS reporting_battery_codes,
       SUM(oil_prod_m3) AS oil_prod_m3,
       SUM(gas_prod_1000_m3) AS gas_prod_1000_m3,
       SUM(wtr_prod_m3) AS wtr_prod_m3,
       SUM(gas_flared_1000_m3) AS gas_flared_1000_m3,
       SUM(gas_vented_1000_m3) AS gas_vented_1000_m3
FROM aei.v_fact_battery_month
WHERE report_month IN (DATE '2025-08-01', DATE '2026-08-01')
GROUP BY report_month
ORDER BY report_month;

-- 4. Comparable Jan-Aug totals; do not compare 2026 partial vs full 2025.
SELECT EXTRACT(YEAR FROM report_month)::int AS report_year,
       COUNT(DISTINCT report_month) AS months,
       COUNT(*) AS battery_month_records,
       SUM(oil_prod_m3) AS oil_prod_m3,
       SUM(gas_prod_1000_m3) AS gas_prod_1000_m3
FROM aei.v_fact_battery_month
WHERE report_month >= DATE '2025-01-01'
  AND report_month < DATE '2026-09-01'
  AND EXTRACT(MONTH FROM report_month) <= 8
GROUP BY EXTRACT(YEAR FROM report_month)
ORDER BY report_year;

-- 5. Test DimOperator -> FactBatteryMonth filter with an actual code.
-- Use the result's FIRST oper_code in a Power BI operator slicer, with
-- DimDate year_month = '2026-08'. Compare oil and battery counts.
SELECT oper_code,
       COUNT(*) AS battery_month_records,
       COUNT(DISTINCT battery_code) AS reporting_battery_codes,
       SUM(oil_prod_m3) AS oil_prod_m3
FROM aei.v_fact_battery_month
WHERE report_month = DATE '2026-08-01'
GROUP BY oper_code
ORDER BY oil_prod_m3 DESC NULLS LAST, oper_code
LIMIT 5;

-- 6. Verify all cardinality assumptions directly in SQL.
SELECT
    (SELECT COUNT(*) FROM (
         SELECT calendar_date FROM aei.v_dim_date
         GROUP BY calendar_date HAVING COUNT(*) > 1
     ) AS duplicate_date) AS duplicate_date_keys,
    (SELECT COUNT(*) FROM (
         SELECT oper_code FROM aei.v_dim_operator
         GROUP BY oper_code HAVING COUNT(*) > 1
     ) AS duplicate_operator) AS duplicate_operator_keys,
    (SELECT COUNT(*) FROM (
         SELECT battery_code FROM aei.v_dim_battery
         GROUP BY battery_code HAVING COUNT(*) > 1
     ) AS duplicate_battery) AS duplicate_battery_keys;

COMMIT;
