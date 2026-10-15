-- Executive Overview read-only reconciliation.
-- Matches the reporting snapshot validated during Day 9.
-- The file is an audit query collection, NOT a migration.
-- Run on local alberta_energy_intelligence as a read-only account.
-- PostgreSQL NUMERIC is exact; compare with Power BI at display precision.

BEGIN READ ONLY;

-- 1. Five cards for Jan-Aug 2026 (Year=2026, other slicers All).
SELECT
    COUNT(DISTINCT battery_code) AS reporting_battery_codes,
    ROUND(SUM(oil_prod_m3), 1) AS oil_produced_m3,
    ROUND(SUM(gas_prod_1000_m3), 1) AS gas_produced_1000_m3,
    ROUND(SUM(gas_flared_1000_m3), 1) AS gas_flared_1000_m3,
    ROUND(SUM(gas_vented_1000_m3), 1) AS gas_vented_1000_m3,
    COUNT(DISTINCT report_month) AS reporting_months
FROM aei.v_fact_battery_month
WHERE report_month >= DATE '2026-01-01'
  AND report_month < DATE '2026-09-01';

-- 2. Ten operator codes by selected-period oil volume.
SELECT
    oper_code,
    ROUND(SUM(oil_prod_m3), 1) AS oil_produced_m3
FROM aei.v_fact_battery_month
WHERE report_month >= DATE '2026-01-01'
  AND report_month < DATE '2026-09-01'
GROUP BY oper_code
ORDER BY oil_produced_m3 DESC NULLS LAST, oper_code
LIMIT 10;

-- 3. Month-by-month line-chart reconciliation.
SELECT
    report_month,
    ROUND(SUM(oil_prod_m3), 1) AS oil_produced_m3,
    ROUND(SUM(gas_prod_1000_m3), 1) AS gas_produced_1000_m3,
    ROUND(SUM(gas_flared_1000_m3), 1) AS gas_flared_1000_m3,
    ROUND(SUM(gas_vented_1000_m3), 1) AS gas_vented_1000_m3
FROM aei.v_fact_battery_month
WHERE report_month >= DATE '2026-01-01'
  AND report_month < DATE '2026-09-01'
GROUP BY report_month
ORDER BY report_month;

-- 4. August 2026 card reconciliation.
SELECT
    COUNT(DISTINCT battery_code) AS reporting_battery_codes,
    ROUND(SUM(oil_prod_m3), 1) AS oil_produced_m3,
    ROUND(SUM(gas_prod_1000_m3), 1) AS gas_produced_1000_m3,
    ROUND(SUM(gas_flared_1000_m3), 1) AS gas_flared_1000_m3,
    ROUND(SUM(gas_vented_1000_m3), 1) AS gas_vented_1000_m3
FROM aei.v_fact_battery_month
WHERE report_month = DATE '2026-08-01';

-- 5. Specific interaction sample: Operator 0007 in August 2026.
SELECT
    oper_code,
    COUNT(DISTINCT battery_code) AS reporting_battery_codes,
    ROUND(SUM(oil_prod_m3), 1) AS oil_produced_m3,
    ROUND(SUM(gas_prod_1000_m3), 1) AS gas_produced_1000_m3,
    ROUND(SUM(gas_flared_1000_m3), 1) AS gas_flared_1000_m3,
    ROUND(SUM(gas_vented_1000_m3), 1) AS gas_vented_1000_m3
FROM aei.v_fact_battery_month
WHERE report_month = DATE '2026-08-01'
  AND oper_code = '0007'
GROUP BY oper_code;

-- 6. Optional custom-range check: Aug 2025 through Jun 2026,
-- corresponding to a Between slicer with first-of-month endpoints
-- and Year cleared to All. Not previously asserted as SQL-validated.
SELECT
    MIN(report_month) AS first_report_month,
    MAX(report_month) AS last_report_month,
    COUNT(DISTINCT report_month) AS reporting_months,
    COUNT(DISTINCT battery_code) AS reporting_battery_codes,
    ROUND(SUM(oil_prod_m3), 1) AS oil_produced_m3
FROM aei.v_fact_battery_month
WHERE report_month >= DATE '2025-08-01'
  AND report_month <= DATE '2026-06-01';

COMMIT;
