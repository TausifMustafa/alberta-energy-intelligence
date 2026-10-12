"""Opt-in read-only PostgreSQL checks for ST60 analytical views.

Run with AEI_TEST_ANALYTICS=1. No schemas, data or credentials are written.
"""

import calendar
import getpass
import os
from datetime import date
from decimal import Decimal

import pytest


@pytest.fixture(scope="module")
def connection():
    if os.environ.get("AEI_TEST_ANALYTICS") != "1":
        pytest.skip("Opt-in analytical database tests: set AEI_TEST_ANALYTICS=1")
    import psycopg

    password = os.environ.get("AEI_DB_PASSWORD") or getpass.getpass(
        "Password for PostgreSQL user aei_loader: "
    )
    with psycopg.connect(
        host=os.environ.get("AEI_DB_HOST", "localhost"),
        port=int(os.environ.get("AEI_DB_PORT", "5432")),
        dbname=os.environ.get("AEI_DB_NAME", "alberta_energy_intelligence"),
        user=os.environ.get("AEI_DB_USER", "aei_loader"),
        password=password,
        connect_timeout=10,
        options="-c default_transaction_read_only=on",
    ) as conn:
        yield conn


def test_analytics_fact_matches_active_source_exactly(connection):
    """EXCEPT ALL checks values, historical operators and duplicates."""
    columns = (
        "report_month, battery_code, oper_code, operator_name, field_centre, "
        "oil_prod_m3, gas_prod_1000_m3, wtr_prod_m3, "
        "gas_flared_1000_m3, gas_vented_1000_m3, "
        "source_file_id, source_row_number"
    )
    with connection.cursor() as cur:
        cur.execute(
            f"SELECT COUNT(*) FROM ("
            f"SELECT {columns} FROM aei.v_st60_current_battery_month "
            f"EXCEPT ALL SELECT {columns} FROM aei.v_fact_battery_month"
            f") AS missing"
        )
        assert cur.fetchone()[0] == 0
        cur.execute(
            f"SELECT COUNT(*) FROM ("
            f"SELECT {columns} FROM aei.v_fact_battery_month "
            f"EXCEPT ALL SELECT {columns} FROM aei.v_st60_current_battery_month"
            f") AS extra"
        )
        assert cur.fetchone()[0] == 0


def test_dimension_keys_unique_and_complete(connection):
    with connection.cursor() as cur:
        for view, key in (
            ("v_dim_date", "calendar_date"),
            ("v_dim_operator", "oper_code"),
            ("v_dim_battery", "battery_code"),
        ):
            # View and column names are fixed, developer-controlled literals.
            cur.execute(
                f"SELECT COUNT(*), COUNT(DISTINCT {key}) FROM aei.{view}"
            )
            rows, unique_keys = cur.fetchone()
            assert rows > 0
            assert rows == unique_keys

        cur.execute(
            """SELECT COUNT(*) FILTER (WHERE d.calendar_date IS NULL),
                      COUNT(*) FILTER (WHERE o.oper_code IS NULL),
                      COUNT(*) FILTER (WHERE b.battery_code IS NULL)
               FROM aei.v_fact_battery_month AS f
               LEFT JOIN aei.v_dim_date AS d ON d.calendar_date = f.report_month
               LEFT JOIN aei.v_dim_operator AS o ON o.oper_code = f.oper_code
               LEFT JOIN aei.v_dim_battery AS b ON b.battery_code = f.battery_code"""
        )
        assert cur.fetchone() == (0, 0, 0)

        cur.execute(
            "SELECT MIN(calendar_date), MAX(calendar_date), COUNT(*) "
            "FROM aei.v_dim_date"
        )
        first, last, days = cur.fetchone()
        assert first.day == 1
        assert last.day == calendar.monthrange(last.year, last.month)[1]
        assert days == (last - first).days + 1


def test_monthly_kpis_equal_direct_source_aggregates(connection):
    fields = (
        "COUNT(*), COUNT(DISTINCT battery_code), "
        "SUM(oil_prod_m3), SUM(gas_prod_1000_m3), SUM(wtr_prod_m3), "
        "SUM(gas_flared_1000_m3), SUM(gas_vented_1000_m3), "
        "COUNT(oil_prod_m3), COUNT(gas_prod_1000_m3), COUNT(wtr_prod_m3), "
        "COUNT(gas_flared_1000_m3), COUNT(gas_vented_1000_m3)"
    )
    with connection.cursor() as cur:
        cur.execute(
            f"SELECT report_month, {fields} "
            "FROM aei.v_st60_current_battery_month GROUP BY report_month"
        )
        direct = {row[0]: row[1:] for row in cur.fetchall()}
        cur.execute(
            """SELECT report_month, battery_month_rows, reporting_battery_codes,
                      oil_prod_m3, gas_prod_1000_m3, wtr_prod_m3,
                      gas_flared_1000_m3, gas_vented_1000_m3,
                      oil_reported_rows, gas_reported_rows, water_reported_rows,
                      flared_reported_rows, vented_reported_rows
               FROM aei.v_monthly_kpis"""
        )
        modeled = {row[0]: row[1:] for row in cur.fetchall()}
        assert direct
        assert direct == modeled


def test_python_decimal_spot_checks_source_rows(connection):
    """Calculate exact-decimal totals in Python for 3 selected months."""
    fields = (
        "oil_prod_m3, gas_prod_1000_m3, wtr_prod_m3, "
        "gas_flared_1000_m3, gas_vented_1000_m3"
    )
    with connection.cursor() as cur:
        cur.execute(
            "SELECT MIN(report_month), MAX(report_month) "
            "FROM aei.v_st60_current_battery_month"
        )
        oldest, newest = cur.fetchone()
        months = {oldest, newest}
        candidate = date(2025, 8, 1)
        if oldest <= candidate <= newest:
            months.add(candidate)

        for month in sorted(months):
            cur.execute(
                f"SELECT {fields} FROM aei.v_st60_current_battery_month "
                "WHERE report_month = %s",
                (month,),
            )
            sums = [None] * 5
            records = 0
            for row in cur:
                records += 1
                for idx, value in enumerate(row):
                    if value is not None:
                        sums[idx] = (
                            Decimal(0) if sums[idx] is None else sums[idx]
                        ) + value
            cur.execute(
                f"SELECT battery_month_rows, {fields} "
                "FROM aei.v_monthly_kpis WHERE report_month = %s",
                (month,),
            )
            actual = cur.fetchone()
            assert actual is not None
            assert actual == (records, *sums)
