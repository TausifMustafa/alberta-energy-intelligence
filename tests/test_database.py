"""Unit tests for Day 5 PostgreSQL row preparation and loading decisions.

These tests use synthetic source records; they do not prove a live DB load.
The CLI and reconciliation queries provide the separate real-DB checks.
"""

from contextlib import nullcontext
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from alberta_energy.database import (
    FACT_COLUMNS,
    NUMERIC_DB_NAMES,
    SOURCE_FIELDS,
    ST60LoadError,
    _chunks,
    _clean_text,
    _source_decimal,
    fact_rows,
    issue_rows,
    load_st60_month,
    raw_rows,
    validate_quality,
)


def sample():
    """Make accepted, warned, and quarantined rows using the full ST60 schema."""
    records = []
    for i in range(3):
        fields = {name: "" for name, _ in SOURCE_FIELDS}
        fields["BATTERY CODE"] = f"000{i + 11}"
        fields["OPER CODE"] = " 0012 "
        fields["OPERATOR NAME"] = " Example Operator "
        fields["OIL PROD (M3)"] = "0.10" if i == 0 else ""
        fields["GAS PROD (1000 M3)"] = "1.20" if i != 2 else "broken"
        fields["GAS FLARED (1000 M3)"] = "0"
        fields["TOTAL # WELLS"] = "2"
        fields["report_month"] = pd.Timestamp("2026-08-01")
        fields["source_sha256"] = "a" * 64
        fields["source_row_number"] = i + 4
        records.append(fields)

    raw = pd.DataFrame(records, dtype="string")
    raw["report_month"] = pd.to_datetime(raw["report_month"])
    raw["source_row_number"] = pd.Series([4, 5, 6], dtype="int64")
    accepted = raw.iloc[:2].copy()
    accepted["OPER CODE"] = accepted["OPER CODE"].str.strip()
    accepted["OIL PROD (M3)"] = pd.Series([0.1, pd.NA], index=accepted.index, dtype="Float64")
    rejected = raw.iloc[2:].copy()
    issues = pd.DataFrame([
        {
            "source_row_number": 5,
            "column": "OIL PROD (M3)",
            "raw_value": "",
            "reason_code": "missing_measure",
            "severity": "warning",
        },
        {
            "source_row_number": 6,
            "column": "GAS PROD (1000 M3)",
            "raw_value": "broken",
            "reason_code": "invalid_numeric",
            "severity": "error",
        },
    ])
    batch = SimpleNamespace(
        report_month=date(2026, 8, 1),
        sha256="a" * 64,
        metadata=("Aug 2026 Data",),
        source_path=Path("ST60_2026-08.csv"),
        source_member=None,
        source_archive_sha256=None,
        encoding="utf-8-sig",
    )
    quality = SimpleNamespace(
        raw_records=raw,
        accepted=accepted,
        quarantined=rejected,
        issues=issues,
        summary={
            "input_rows": 3,
            "accepted_rows": 2,
            "quarantined_rows": 1,
            "warning_rows": 1,
        },
    )
    return batch, quality


def test_all_29_source_columns_are_mapped():
    assert len(SOURCE_FIELDS) == 29
    assert len({source for source, _ in SOURCE_FIELDS}) == 29
    assert len({target for _, target in SOURCE_FIELDS}) == 29
    assert len(NUMERIC_DB_NAMES) == 19
    assert len(FACT_COLUMNS) == 32


def test_decimal_and_missing_numeric_values():
    assert _source_decimal(" 0.10 ", "OIL PROD (M3)") == Decimal("0.10")
    assert _source_decimal("", "OIL PROD (M3)") is None
    assert _source_decimal("0", "OIL PROD (M3)") == Decimal(0)
    assert _clean_text("   ") is None
    with pytest.raises(ST60LoadError, match="Non-finite"):
        _source_decimal("NaN", "OIL PROD (M3)")
    with pytest.raises(ST60LoadError, match="Invalid decimal"):
        _source_decimal("broken", "OIL PROD (M3)")


def test_fact_rows_preserve_clean_codes_and_original_decimal_text():
    batch, quality = sample()
    validate_quality(batch, quality)
    rows = list(fact_rows(7, batch, quality))
    assert len(rows) == 2
    assert rows[0][FACT_COLUMNS.index("battery_code")] == "00011"
    assert rows[0][FACT_COLUMNS.index("oper_code")] == "0012"
    assert rows[0][FACT_COLUMNS.index("licence_num")] is None
    assert rows[0][FACT_COLUMNS.index("oil_prod_m3")] == Decimal("0.10")
    assert rows[1][FACT_COLUMNS.index("oil_prod_m3")] is None
    assert rows[0][FACT_COLUMNS.index("gas_flared_1000_m3")] == Decimal(0)
    assert rows[0][FACT_COLUMNS.index("total_wells")] == Decimal(2)
    assert rows[0][2] == date(2026, 8, 1)


def test_raw_rows_preserve_original_text_and_quarantine():
    _, quality = sample()
    rows = list(raw_rows(7, quality))
    assert [row[3] for row in rows] == ["accepted", "accepted", "quarantined"]
    assert rows[0][4].obj["OPER CODE"] == " 0012 "
    assert rows[1][4].obj["OIL PROD (M3)"] == ""
    assert rows[2][4].obj["GAS PROD (1000 M3)"] == "broken"
    assert len(rows[0][4].obj) == 29
    assert len(list(issue_rows(7, quality))) == 2


def test_quality_reconciliation_detects_inconsistent_partitions():
    batch, quality = sample()
    quality.quarantined = quality.quarantined.iloc[:0]
    with pytest.raises(ST60LoadError, match="Quarantine count"):
        validate_quality(batch, quality)


def test_batches_include_final_partial_chunk():
    assert [len(chunk) for chunk in _chunks(((x,) for x in range(5)), 2)] == [2, 2, 1]


class FakeCursor:
    """Check command sequencing only; not a replacement for PostgreSQL tests."""

    def __init__(self, *, already_loaded=False):
        self.already_loaded = already_loaded
        self.next_row = None
        self.inserted = {"raw": [], "fact": [], "issue": []}
        self.commands = []
        self.active = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, parameters=None):
        self.commands.append((sql, parameters))
        if "FROM aei.st60_source_file" in sql and "WHERE report_month" in sql:
            self.next_row = (17, 3, 2, 1, 1) if self.already_loaded else None
        elif "RETURNING source_file_id" in sql:
            self.next_row = (17,)
        elif "COUNT(*) FROM aei.st60_source_row" in sql:
            self.next_row = (len(self.inserted["raw"]),)
        elif "COUNT(*) FROM aei.st60_quality_issue" in sql:
            self.next_row = (len(self.inserted["issue"]),)
        elif "COALESCE(SUM(" in sql:
            positions = {col: FACT_COLUMNS.index(col) for col in NUMERIC_DB_NAMES}
            sums = [
                sum((row[index] or Decimal(0) for row in self.inserted["fact"]), Decimal(0))
                for index in positions.values()
            ]
            self.next_row = (len(self.inserted["fact"]), *sums)
        elif "SET is_active = TRUE" in sql:
            self.active = True
        elif "COUNT(*) FROM aei.v_st60_current_battery_month" in sql:
            self.next_row = (len(self.inserted["fact"]) if self.active else 0,)
        else:
            self.next_row = None

    def executemany(self, sql, parameters):
        if "st60_source_row" in sql:
            self.inserted["raw"].extend(parameters)
        elif "st60_quality_issue" in sql:
            self.inserted["issue"].extend(parameters)
        elif "st60_battery_month" in sql:
            self.inserted["fact"].extend(parameters)
        else:
            raise AssertionError(f"Unexpected statement {sql}")

    def fetchone(self):
        return self.next_row


class FakeConnection:
    def __init__(self, *, already_loaded=False):
        self.query = FakeCursor(already_loaded=already_loaded)

    def transaction(self):
        return nullcontext()

    def cursor(self):
        return self.query


def test_loader_inserts_all_rows_and_reconciles_before_activation():
    batch, quality = sample()
    conn = FakeConnection()
    result = load_st60_month(conn, batch, quality, chunk_size=1)
    assert result.status == "loaded"
    assert result.source_file_id == 17
    assert len(conn.query.inserted["raw"]) == 3
    assert len(conn.query.inserted["fact"]) == 2
    assert len(conn.query.inserted["issue"]) == 2
    assert conn.query.active is True
    statements = [command for command, _ in conn.query.commands]
    reconcile_at = next(i for i, stmt in enumerate(statements) if "COALESCE(SUM(" in stmt)
    activate_at = next(i for i, stmt in enumerate(statements) if "SET is_active = TRUE" in stmt)
    assert reconcile_at < activate_at


def test_reloading_identical_checksum_skips_writes():
    batch, quality = sample()
    conn = FakeConnection(already_loaded=True)
    result = load_st60_month(conn, batch, quality)
    assert result.status == "already_loaded"
    assert conn.query.inserted == {"raw": [], "fact": [], "issue": []}
