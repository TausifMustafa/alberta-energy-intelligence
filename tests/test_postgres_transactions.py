"""Opt-in live PostgreSQL checks for ST60 revision and rollback behaviour.

Run only when explicitly enabled, using aei_loader credentials. Synthetic
2022-12 source versions stay inside one outer transaction that is rolled back.
PostgreSQL sequences can still advance because sequence values are not rolled back.
"""

import csv
import getpass
import os
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from alberta_energy.database import SOURCE_FIELDS, ST60LoadError, load_st60_month
from alberta_energy.ingest import read_st60_csv
from alberta_energy.quality import assess_st60_quality

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "data" / "raw" / "ST60_2026-08.csv"
TEST_MONTH = date(2022, 12, 1)


class DiscardSyntheticTest(Exception):
    """Abort the outer transaction after successful assertions."""


def _create_synthetic_revision(tmp_path: Path, revision: int):
    """Create small fake ST60 records with real source headings and units."""
    with REFERENCE.open("r", encoding="utf-8-sig", newline="") as reference:
        reader = csv.reader(reference)
        next(reader)  # Source metadata is replaced with the synthetic period.
        headings = next(reader)
        units = next(reader)

    combined = [
        f"{heading.strip()} {unit.strip()}".strip()
        for heading, unit in zip(headings, units, strict=True)
    ]
    assert combined == [field for field, _ in SOURCE_FIELDS]

    folder = tmp_path / f"revision_{revision}"
    folder.mkdir()
    path = folder / "ST60_2022-12.csv"
    records = []
    if revision == 1:
        volumes = ["10.25", "20.50"]
    elif revision == 2:
        volumes = ["11.25", "22.50", "33.75"]
    else:
        volumes = ["12.25", "23.50", "34.75"]
    for index, oil in enumerate(volumes):
        values = dict.fromkeys(combined, "")
        values.update({
            "FIELD CENTRE": "A",
            "BATTERY CODE": f"9900{index + 1:04d}",
            "BTY TYPE": "B",
            "OPER CODE": "0099",
            "OPERATOR NAME": "Synthetic Test Operator",
            "OIL PROD (M3)": oil,
            "GAS PROD (1000 M3)": "12.5",
            "GAS FLARED (1000 M3)": "0",
            "GAS VENTED (1000 M3)": "0",
            "WTR PROD (M3)": "5.5",
            "TOTAL # WELLS": "1",
        })
        records.append([values[name] for name in combined])

    with path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(["Run Date: 20 Jan 2023", "", "Dec 2022 Data"])
        writer.writerow(headings)
        writer.writerow(units)
        writer.writerows(records)

    batch = read_st60_csv(path, record_mode="raw")
    quality = assess_st60_quality(batch)
    assert quality.summary["accepted_rows"] == len(records)
    assert quality.summary["quarantined_rows"] == 0
    assert quality.summary["warning_rows"] == 0
    return batch, quality


def _source_versions(connection):
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT source_file_id, is_active, accepted_rows
               FROM aei.st60_source_file
               WHERE report_month = %s
               ORDER BY source_file_id""",
            (TEST_MONTH,),
        )
        return cursor.fetchall()


def _active_month_totals(connection):
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT COUNT(*), COALESCE(SUM(oil_prod_m3), 0)
               FROM aei.v_st60_current_battery_month
               WHERE report_month = %s""",
            (TEST_MONTH,),
        )
        return cursor.fetchone()


def _overall_active_count(connection):
    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM aei.v_st60_current_battery_month")
        return cursor.fetchone()[0]


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("AEI_TEST_POSTGRES") != "1",
    reason="Opt-in live database test: set AEI_TEST_POSTGRES=1",
)
def test_live_revisions_and_failed_load_roll_back(tmp_path: Path):
    """Check real DB revisions and injected reconciliation failure, then undo."""
    import psycopg

    if not REFERENCE.is_file():
        pytest.skip("Original August 2026 ST60 CSV is missing from data/raw")

    versions = [
        _create_synthetic_revision(tmp_path, revision)
        for revision in (1, 2, 3)
    ]
    password = os.environ.get("AEI_DB_PASSWORD") or getpass.getpass(
        "Password for PostgreSQL user aei_loader: "
    )

    with psycopg.connect(
        host=os.environ.get("AEI_DB_HOST", "localhost"),
        port=int(os.environ.get("AEI_DB_PORT", "5432")),
        dbname=os.environ.get("AEI_DB_NAME", "alberta_energy_intelligence"),
        user=os.environ.get("AEI_DB_USER", "aei_loader"),
        password=password,
        autocommit=True,
        connect_timeout=10,
    ) as connection:
        if _source_versions(connection):
            pytest.fail("Synthetic test month 2022-12 already exists; no writes attempted")
        baseline = _overall_active_count(connection)

        # The outer transaction is NEVER committed. The production loader's
        # nested transactions become PostgreSQL savepoints within this block.
        with pytest.raises(DiscardSyntheticTest), connection.transaction():
            first = load_st60_month(connection, *versions[0])
            assert first.status == "loaded"
            assert _active_month_totals(connection) == (2, Decimal("30.75"))

            second = load_st60_month(connection, *versions[1])
            assert second.status == "loaded"
            assert _active_month_totals(connection) == (3, Decimal("67.50"))
            assert _source_versions(connection) == [
                (first.source_file_id, False, 2),
                (second.source_file_id, True, 3),
            ]

            repeated = load_st60_month(connection, *versions[1])
            assert repeated.status == "already_loaded"
            assert repeated.source_file_id == second.source_file_id

            # Fail after the loader has inserted the candidate source rows,
            # before it can activate the revision. Its nested savepoint
            # must roll back and retain revision 2 as the active snapshot.
            with patch(
                "alberta_energy.database._reconcile",
                side_effect=ST60LoadError("injected reconciliation failure"),
            ), pytest.raises(ST60LoadError, match="injected"):
                load_st60_month(connection, *versions[2])

            assert _source_versions(connection) == [
                (first.source_file_id, False, 2),
                (second.source_file_id, True, 3),
            ]
            assert _active_month_totals(connection) == (3, Decimal("67.50"))
            assert _overall_active_count(connection) == baseline + 3

            # Always roll back even when the revision checks pass.
            raise DiscardSyntheticTest

        # Check from the same connection after the outer transaction rolled
        # back: no synthetic rows remain and existing reporting totals match.
        assert _source_versions(connection) == []
        assert _active_month_totals(connection) == (0, Decimal(0))
        assert _overall_active_count(connection) == baseline

    print("Live PostgreSQL revision, duplicate, and failure rollback checks passed")
    print("Synthetic reporting rows fully rolled back; existing data unchanged")
