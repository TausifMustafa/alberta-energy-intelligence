-- Read-only database checks after running scripts/load_st60.py.
-- Execute while connected to alberta_energy_intelligence.

SELECT
    report_month,
    source_file_name,
    LEFT(source_sha256, 12) AS sha_prefix,
    is_active,
    input_rows,
    accepted_rows,
    quarantined_rows,
    warning_rows
FROM aei.st60_source_file
ORDER BY report_month, loaded_at;

-- Each month should have exactly one current source version.
SELECT report_month, COUNT(*) AS active_versions
FROM aei.st60_source_file
WHERE is_active
GROUP BY report_month
ORDER BY report_month;

-- Data visible to Power BI, not old revisions or quarantined rows.
SELECT
    report_month,
    COUNT(*) AS battery_rows,
    SUM(oil_prod_m3) AS oil_prod_m3,
    SUM(gas_prod_1000_m3) AS gas_prod_1000_m3,
    SUM(gas_flared_1000_m3) AS gas_flared_1000_m3,
    SUM(gas_vented_1000_m3) AS gas_vented_1000_m3,
    SUM(wtr_prod_m3) AS wtr_prod_m3
FROM aei.v_st60_current_battery_month
GROUP BY report_month
ORDER BY report_month;

-- Verify the complete source/clean/issue counts across loaded versions.
SELECT
    s.report_month,
    LEFT(s.source_sha256, 12) AS sha_prefix,
    s.input_rows AS source_rows_expected,
    (SELECT COUNT(*) FROM aei.st60_source_row r
     WHERE r.source_file_id = s.source_file_id) AS stored_source_rows,
    s.accepted_rows AS accepted_rows_expected,
    (SELECT COUNT(*) FROM aei.st60_battery_month b
     WHERE b.source_file_id = s.source_file_id) AS stored_accepted_rows,
    (SELECT COUNT(*) FROM aei.st60_quality_issue i
     WHERE i.source_file_id = s.source_file_id) AS stored_issues
FROM aei.st60_source_file s
ORDER BY s.report_month, s.loaded_at;
