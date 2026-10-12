-- Day 6: read-only coverage and active-source reconciliation.
-- Run on the alberta_energy_intelligence database after backfill.

-- Active records by reporting year. 2026 is a partial year through August.
SELECT EXTRACT(YEAR FROM report_month)::INTEGER AS report_year,
       COUNT(DISTINCT report_month) AS months,
       COUNT(*) AS battery_rows,
       SUM(oil_prod_m3) AS oil_prod_m3,
       SUM(gas_flared_1000_m3) AS gas_flared_1000_m3,
       SUM(gas_vented_1000_m3) AS gas_vented_1000_m3
FROM aei.v_st60_current_battery_month
GROUP BY EXTRACT(YEAR FROM report_month)
ORDER BY report_year;

-- This should produce zero rows when Jan 2023--Aug 2026 is complete.
SELECT month_start::DATE AS missing_month
FROM generate_series(
    DATE '2023-01-01', DATE '2026-08-01', INTERVAL '1 month'
) AS expected(month_start)
WHERE NOT EXISTS (
    SELECT 1 FROM aei.st60_source_file AS s
    WHERE s.report_month = expected.month_start::DATE
      AND s.is_active
)
ORDER BY missing_month;

-- Expect 44 active versions, positive active row count, and zero duplicates.
SELECT
    (SELECT COUNT(*) FROM aei.st60_source_file WHERE is_active)
        AS active_source_versions,
    (SELECT COUNT(*) FROM aei.v_st60_current_battery_month)
        AS active_battery_records,
    (SELECT COUNT(*) FROM (
        SELECT report_month
        FROM aei.st60_source_file
        WHERE is_active
        GROUP BY report_month
        HAVING COUNT(*) <> 1
    ) AS mismatches) AS invalid_active_version_months,
    (SELECT COUNT(*) FROM (
        SELECT report_month, battery_code
        FROM aei.v_st60_current_battery_month
        GROUP BY report_month, battery_code
        HAVING COUNT(*) > 1
    ) AS duplicates) AS duplicate_battery_months;

-- Source summary counts versus stored row counts, only discrepancies.
SELECT s.report_month, s.source_file_name,
       s.input_rows, s.accepted_rows, s.quarantined_rows,
       (SELECT COUNT(*) FROM aei.st60_source_row r
        WHERE r.source_file_id = s.source_file_id) AS stored_raw,
       (SELECT COUNT(*) FROM aei.st60_battery_month f
        WHERE f.source_file_id = s.source_file_id) AS stored_accepted
FROM aei.st60_source_file AS s
WHERE s.is_active
  AND (
      s.input_rows <> (SELECT COUNT(*) FROM aei.st60_source_row r
                       WHERE r.source_file_id = s.source_file_id)
      OR s.accepted_rows <> (SELECT COUNT(*) FROM aei.st60_battery_month f
                            WHERE f.source_file_id = s.source_file_id)
  )
ORDER BY s.report_month;
