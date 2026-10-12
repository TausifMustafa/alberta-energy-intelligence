-- Read-only ST60 source and active-record validation.
-- Connect to alberta_energy_intelligence; the aei_loader role can run this.
-- Source reporting units: oil/water in m3; gas in 1,000 m3.

-- 1. Annual coverage and broad reported totals. A partial year is not
-- directly comparable with a full year without matching time windows.
SELECT EXTRACT(YEAR FROM report_month)::integer AS report_year,
       COUNT(DISTINCT report_month) AS reported_months,
       COUNT(*) AS battery_month_records,
       SUM(oil_prod_m3) AS oil_prod_m3,
       SUM(gas_prod_1000_m3) AS gas_prod_1000_m3,
       SUM(gas_flared_1000_m3) AS gas_flared_1000_m3,
       SUM(gas_vented_1000_m3) AS gas_vented_1000_m3,
       SUM(wtr_prod_m3) AS wtr_prod_m3
FROM aei.v_st60_current_battery_month
GROUP BY EXTRACT(YEAR FROM report_month)
ORDER BY report_year;

-- 2. Missing months between the earliest and latest ACTIVE source.
-- This cannot detect missing months outside that observed range.
WITH bounds AS (
    SELECT MIN(report_month) AS first_month,
           MAX(report_month) AS last_month
    FROM aei.st60_source_file
    WHERE is_active
)
SELECT month_start::date AS missing_month
FROM bounds
CROSS JOIN LATERAL generate_series(
    bounds.first_month, bounds.last_month, interval '1 month'
) AS expected(month_start)
WHERE NOT EXISTS (
    SELECT 1
    FROM aei.st60_source_file AS source
    WHERE source.report_month = expected.month_start::date
      AND source.is_active
)
ORDER BY missing_month;

-- 3. Summary checks. Every count in the last two columns should be zero.
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
    ) AS bad_version) AS invalid_active_version_months,
    (SELECT COUNT(*) FROM (
        SELECT report_month, battery_code
        FROM aei.v_st60_current_battery_month
        GROUP BY report_month, battery_code
        HAVING COUNT(*) > 1
    ) AS duplicate) AS duplicate_battery_months;

-- 4. Source summaries versus persisted raw, accepted and issue counts.
-- Includes retained historical revisions, not only active snapshots.
-- Expected result: zero rows; quality_issue table can contain several
-- findings on a single source row.
SELECT source.report_month,
       source.source_file_name,
       LEFT(source.source_sha256, 12) AS sha_prefix,
       source.is_active,
       source.input_rows,
       source.accepted_rows,
       source.quarantined_rows,
       (SELECT COUNT(*) FROM aei.st60_source_row AS raw
        WHERE raw.source_file_id = source.source_file_id) AS stored_raw,
       (SELECT COUNT(*) FROM aei.st60_battery_month AS fact
        WHERE fact.source_file_id = source.source_file_id) AS stored_accepted
FROM aei.st60_source_file AS source
WHERE source.input_rows <> (
        SELECT COUNT(*) FROM aei.st60_source_row AS raw
        WHERE raw.source_file_id = source.source_file_id
    )
   OR source.accepted_rows <> (
        SELECT COUNT(*) FROM aei.st60_battery_month AS fact
        WHERE fact.source_file_id = source.source_file_id
    )
ORDER BY source.report_month, source.loaded_at;

-- 5. Source version audit, including inactive historical revisions.
SELECT report_month,
       source_file_name,
       LEFT(source_sha256, 12) AS sha_prefix,
       is_active,
       input_rows,
       accepted_rows,
       quarantined_rows,
       warning_rows,
       loaded_at
FROM aei.st60_source_file
ORDER BY report_month, loaded_at, source_file_id;
