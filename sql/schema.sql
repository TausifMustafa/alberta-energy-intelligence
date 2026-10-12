-- Alberta Energy Intelligence: ST60 database tables.
-- Run this script as the postgres administrator while connected to
-- the alberta_energy_intelligence database.
-- The Python loader will use the restricted aei_loader account.

BEGIN;

CREATE SCHEMA IF NOT EXISTS aei;

-- One source version for one reporting month. A revision has a new checksum.
CREATE TABLE IF NOT EXISTS aei.st60_source_file (
    source_file_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    report_month DATE NOT NULL,
    source_file_name TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    source_archive_name TEXT,
    source_archive_sha256 TEXT,
    source_archive_member TEXT,
    source_encoding TEXT NOT NULL,
    source_metadata JSONB NOT NULL DEFAULT '[]'::jsonb,
    input_rows INTEGER NOT NULL,
    accepted_rows INTEGER NOT NULL,
    quarantined_rows INTEGER NOT NULL,
    warning_rows INTEGER NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_st60_source_month_checksum
        UNIQUE (report_month, source_sha256),
    CONSTRAINT uq_st60_source_id_month
        UNIQUE (source_file_id, report_month),
    CONSTRAINT chk_st60_source_month_start
        CHECK (EXTRACT(DAY FROM report_month) = 1),
    CONSTRAINT chk_st60_source_file_name
        CHECK (BTRIM(source_file_name) <> ''),
    CONSTRAINT chk_st60_source_sha256
        CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT chk_st60_archive_sha256
        CHECK (
            source_archive_sha256 IS NULL
            OR source_archive_sha256 ~ '^[0-9a-f]{64}$'
        ),
    CONSTRAINT chk_st60_source_metadata_array
        CHECK (jsonb_typeof(source_metadata) = 'array'),
    CONSTRAINT chk_st60_source_row_counts
        CHECK (
            input_rows > 0
            AND accepted_rows >= 0
            AND quarantined_rows >= 0
            AND warning_rows >= 0
            AND warning_rows <= accepted_rows
            AND input_rows = accepted_rows + quarantined_rows
        )
);

-- At most one source version can be current for each reporting month.
CREATE UNIQUE INDEX IF NOT EXISTS ux_st60_one_active_source_per_month
    ON aei.st60_source_file (report_month)
    WHERE is_active;

-- Every parsed record, including records that fail quality checks.
-- raw_values contains the original 29 named fields as text values.
CREATE TABLE IF NOT EXISTS aei.st60_source_row (
    source_file_id BIGINT NOT NULL
        REFERENCES aei.st60_source_file (source_file_id),
    source_row_number INTEGER NOT NULL,
    battery_code TEXT,
    row_status TEXT NOT NULL,
    raw_values JSONB NOT NULL,
    PRIMARY KEY (source_file_id, source_row_number),
    CONSTRAINT chk_st60_source_row_number
        CHECK (source_row_number >= 4),
    CONSTRAINT chk_st60_source_row_status
        CHECK (row_status IN ('accepted', 'quarantined')),
    CONSTRAINT chk_st60_source_raw_object
        CHECK (jsonb_typeof(raw_values) = 'object')
);

-- One record for each quality finding. A source row can have many issues.
CREATE TABLE IF NOT EXISTS aei.st60_quality_issue (
    quality_issue_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_file_id BIGINT NOT NULL,
    source_row_number INTEGER NOT NULL,
    column_name TEXT NOT NULL,
    raw_value TEXT,
    reason_code TEXT NOT NULL,
    severity TEXT NOT NULL,
    FOREIGN KEY (source_file_id, source_row_number)
        REFERENCES aei.st60_source_row (source_file_id, source_row_number),
    CONSTRAINT chk_st60_issue_severity
        CHECK (severity IN ('warning', 'error'))
);

CREATE INDEX IF NOT EXISTS ix_st60_issue_reason
    ON aei.st60_quality_issue (reason_code, severity);
CREATE INDEX IF NOT EXISTS ix_st60_issue_file_row
    ON aei.st60_quality_issue (source_file_id, source_row_number);

-- One accepted battery record per source version. Reported quantities
-- use their original source units. No sign constraints: quality warnings
-- already identify negative quantities for review.
CREATE TABLE IF NOT EXISTS aei.st60_battery_month (
    source_file_id BIGINT NOT NULL,
    source_row_number INTEGER NOT NULL,
    report_month DATE NOT NULL,
    field_centre TEXT,
    bty_location TEXT,
    bty_location_edit TEXT,
    fld_area_code TEXT,
    battery_code TEXT NOT NULL,
    bty_type TEXT,
    oper_code TEXT NOT NULL,
    operator_name TEXT,
    oil_prod_m3 NUMERIC,
    oil_receipt_m3 NUMERIC,
    oil_op_inv_m3 NUMERIC,
    oil_cl_inv_m3 NUMERIC,
    oil_delivs_m3 NUMERIC,
    gas_prod_1000_m3 NUMERIC,
    gas_receipt_1000_m3 NUMERIC,
    lease_fuel_1000_m3 NUMERIC,
    gas_flared_1000_m3 NUMERIC,
    gas_vented_1000_m3 NUMERIC,
    gas_m_diff_1000_m3 NUMERIC,
    gas_delivs_1000_m3 NUMERIC,
    wtr_prod_m3 NUMERIC,
    wtr_receipt_m3 NUMERIC,
    wtr_op_inv_m3 NUMERIC,
    wtr_cl_inv_m3 NUMERIC,
    wtr_m_diff_m3 NUMERIC,
    wtr_deliv_m3 NUMERIC,
    total_wells NUMERIC,
    licence_num TEXT,
    bty_description TEXT,
    PRIMARY KEY (source_file_id, battery_code),
    UNIQUE (source_file_id, source_row_number),
    FOREIGN KEY (source_file_id, report_month)
        REFERENCES aei.st60_source_file (source_file_id, report_month),
    FOREIGN KEY (source_file_id, source_row_number)
        REFERENCES aei.st60_source_row (source_file_id, source_row_number),
    CONSTRAINT chk_st60_fact_month_start
        CHECK (EXTRACT(DAY FROM report_month) = 1),
    CONSTRAINT chk_st60_fact_battery_code
        CHECK (BTRIM(battery_code) <> ''),
    CONSTRAINT chk_st60_fact_operator_code
        CHECK (BTRIM(oper_code) <> '')
);

-- Access paths for Power BI monthly trends and investigations.
CREATE INDEX IF NOT EXISTS ix_st60_fact_report_month
    ON aei.st60_battery_month (report_month);
CREATE INDEX IF NOT EXISTS ix_st60_fact_battery_history
    ON aei.st60_battery_month (battery_code, report_month DESC);
CREATE INDEX IF NOT EXISTS ix_st60_fact_operator_month
    ON aei.st60_battery_month (oper_code, report_month);
CREATE INDEX IF NOT EXISTS ix_st60_fact_field_centre_month
    ON aei.st60_battery_month (field_centre, report_month);

-- Analytics should use only the active, validated source version
-- for each reporting month. Old revisions remain available in tables.
CREATE OR REPLACE VIEW aei.v_st60_current_battery_month AS
SELECT
    fact.*,
    src.source_file_name,
    src.source_sha256,
    src.source_archive_name,
    src.source_archive_sha256,
    src.source_archive_member,
    src.loaded_at
FROM aei.st60_battery_month AS fact
JOIN aei.st60_source_file AS src
    ON src.source_file_id = fact.source_file_id
WHERE src.is_active;

COMMIT;
