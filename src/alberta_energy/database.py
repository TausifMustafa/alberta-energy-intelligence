"""Load validated monthly ST60 snapshots into PostgreSQL.

One reporting month is loaded in one transaction. Old file revisions remain
stored, but only one validated source version is active for analysis.
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

import pandas as pd

if TYPE_CHECKING:
    from alberta_energy.ingest import ST60Batch
    from alberta_energy.quality import ST60QualityResult

# Keep this order matched to aei.st60_battery_month in sql/schema.sql.
SOURCE_FIELDS = (
    ("FIELD CENTRE", "field_centre"),
    ("BTY LOCATION", "bty_location"),
    ("BTY LOCATION EDIT", "bty_location_edit"),
    ("FLD/AREA CODE", "fld_area_code"),
    ("BATTERY CODE", "battery_code"),
    ("BTY TYPE", "bty_type"),
    ("OPER CODE", "oper_code"),
    ("OPERATOR NAME", "operator_name"),
    ("OIL PROD (M3)", "oil_prod_m3"),
    ("OIL RECEIPT (M3)", "oil_receipt_m3"),
    ("OIL OP INV (M3)", "oil_op_inv_m3"),
    ("OIL CL INV (M3)", "oil_cl_inv_m3"),
    ("OIL DELIVS (M3)", "oil_delivs_m3"),
    ("GAS PROD (1000 M3)", "gas_prod_1000_m3"),
    ("GAS RECEIPT (1000 M3)", "gas_receipt_1000_m3"),
    ("LEASE FUEL (1000 M3)", "lease_fuel_1000_m3"),
    ("GAS FLARED (1000 M3)", "gas_flared_1000_m3"),
    ("GAS VENTED (1000 M3)", "gas_vented_1000_m3"),
    ("GAS M DIFF (1000 M3)", "gas_m_diff_1000_m3"),
    ("GAS DELIVS (1000 M3)", "gas_delivs_1000_m3"),
    ("WTR PROD (M3)", "wtr_prod_m3"),
    ("WTR RECEIPT (M3)", "wtr_receipt_m3"),
    ("WTR OP INV (M3)", "wtr_op_inv_m3"),
    ("WTR CL INV (M3)", "wtr_cl_inv_m3"),
    ("WTR M DIFF (M3)", "wtr_m_diff_m3"),
    ("WTR DELIV (M3)", "wtr_deliv_m3"),
    ("TOTAL # WELLS", "total_wells"),
    ("LICENCE NUM", "licence_num"),
    ("BTY DESCRIPTION", "bty_description"),
)

NUMERIC_NAMES = frozenset(
    name for name, _ in SOURCE_FIELDS
    if name.endswith(("(M3)", "(1000 M3)", "# WELLS"))
)
NUMERIC_DB_NAMES = tuple(
    db_name for name, db_name in SOURCE_FIELDS if name in NUMERIC_NAMES
)
FACT_COLUMNS = (
    "source_file_id", "source_row_number", "report_month",
    *(db_name for _, db_name in SOURCE_FIELDS),
)

# SQL identifiers here are fixed constants written by this project, not input.
_INSERT_FACT = (
    f"INSERT INTO aei.st60_battery_month ({', '.join(FACT_COLUMNS)}) "
    f"VALUES ({', '.join(['%s'] * len(FACT_COLUMNS))})"
)
_INSERT_RAW = """
    INSERT INTO aei.st60_source_row
        (source_file_id, source_row_number, battery_code, row_status, raw_values)
    VALUES (%s, %s, %s, %s, %s)
"""
_INSERT_ISSUE = """
    INSERT INTO aei.st60_quality_issue
        (source_file_id, source_row_number, column_name, raw_value,
         reason_code, severity)
    VALUES (%s, %s, %s, %s, %s, %s)
"""


class ST60LoadError(ValueError):
    """The ST60 records or database results did not reconcile."""


@dataclass(frozen=True)
class LoadResult:
    report_month: str
    source_sha256: str
    source_file_id: int
    status: str
    input_rows: int
    accepted_rows: int
    quarantined_rows: int
    issues: int


def _plain_text(value: Any) -> str | None:
    """Keep raw CSV text, including spaces and empty strings."""
    if value is None or pd.isna(value):
        return None
    return str(value)


def _clean_text(value: Any) -> str | None:
    """Represent optional blank text as SQL NULL in the clean table."""
    original = _plain_text(value)
    return original.strip() or None if original is not None else None


def _source_decimal(value: Any, column: str) -> Decimal | None:
    """Use original numeric text to avoid Float64 rounding during loading."""
    cleaned = _clean_text(value)
    if cleaned is None:
        return None
    try:
        number = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ST60LoadError(f"Invalid decimal in accepted {column}: {cleaned!r}") from exc
    if not number.is_finite():
        raise ST60LoadError(f"Non-finite value in accepted {column}: {cleaned!r}")
    return number


def validate_quality(batch: "ST60Batch", quality: "ST60QualityResult") -> None:
    """Check that the previously assessed source was not mixed with another."""
    summary = quality.summary
    if summary["input_rows"] != len(quality.raw_records):
        raise ST60LoadError("Input row count differs from raw records")
    if summary["accepted_rows"] != len(quality.accepted):
        raise ST60LoadError("Accepted row count differs from accepted records")
    if summary["quarantined_rows"] != len(quality.quarantined):
        raise ST60LoadError("Quarantine count differs from quarantined records")
    if summary["input_rows"] != summary["accepted_rows"] + summary["quarantined_rows"]:
        raise ST60LoadError("Source row counts do not reconcile")
    if not quality.raw_records.index.is_unique:
        raise ST60LoadError("Raw source index is not unique")
    accepted = set(quality.accepted.index)
    quarantined = set(quality.quarantined.index)
    if accepted & quarantined or accepted | quarantined != set(quality.raw_records.index):
        raise ST60LoadError("Accepted and quarantined records do not partition source")
    for frame in (quality.raw_records, quality.accepted, quality.quarantined):
        if not frame.empty and not frame["source_sha256"].eq(batch.sha256).all():
            raise ST60LoadError("Source checksum differs from quality records")
        if not frame.empty and not frame["report_month"].eq(pd.Timestamp(batch.report_month)).all():
            raise ST60LoadError("Report month differs from quality records")
    source_columns = {name for name, _ in SOURCE_FIELDS}
    if not source_columns.issubset(quality.raw_records.columns):
        raise ST60LoadError("Source is missing a mapped ST60 column")
    row_numbers = quality.raw_records["source_row_number"]
    if row_numbers.duplicated().any() or row_numbers.isna().any():
        raise ST60LoadError("Source CSV row numbers are not unique")
    if not quality.issues.empty:
        valid_numbers = set(row_numbers)
        if not set(quality.issues["source_row_number"]).issubset(valid_numbers):
            raise ST60LoadError("A quality issue references an unknown source row")


def raw_rows(
    source_file_id: int, quality: "ST60QualityResult"
) -> Iterator[tuple[Any, ...]]:
    """Return the original 29 fields for every accepted or rejected row."""
    from psycopg.types.json import Jsonb

    rejected_numbers = set(quality.quarantined["source_row_number"])
    columns = tuple(quality.raw_records.columns)
    for values_tuple in quality.raw_records.itertuples(index=False, name=None):
        record = dict(zip(columns, values_tuple, strict=True))
        values = {name: _plain_text(record[name]) for name, _ in SOURCE_FIELDS}
        number = int(record["source_row_number"])
        battery_code = _clean_text(record["BATTERY CODE"])
        yield (
            source_file_id,
            number,
            battery_code,
            "quarantined" if number in rejected_numbers else "accepted",
            Jsonb(values),
        )


def fact_rows(
    source_file_id: int,
    batch: "ST60Batch",
    quality: "ST60QualityResult",
) -> Iterator[tuple[Any, ...]]:
    """Use cleaned identities and exact raw decimal text for accepted rows."""
    for index, cleaned in quality.accepted.iterrows():
        original = quality.raw_records.loc[index]
        fields: list[Any] = []
        for source_name, _ in SOURCE_FIELDS:
            if source_name in NUMERIC_NAMES:
                fields.append(_source_decimal(original[source_name], source_name))
            else:
                fields.append(_clean_text(cleaned[source_name]))
        yield (
            source_file_id,
            int(cleaned["source_row_number"]),
            batch.report_month,
            *fields,
        )


def issue_rows(
    source_file_id: int, quality: "ST60QualityResult"
) -> Iterator[tuple[Any, ...]]:
    """Link each quality warning/error to its original source row."""
    for issue in quality.issues.itertuples(index=False):
        yield (
            source_file_id,
            int(issue.source_row_number),
            str(issue.column),
            _plain_text(issue.raw_value),
            str(issue.reason_code),
            str(issue.severity),
        )


def _chunks(rows: Iterable[tuple[Any, ...]], size: int) -> Iterator[list[tuple[Any, ...]]]:
    chunk: list[tuple[Any, ...]] = []
    for row in rows:
        chunk.append(row)
        if len(chunk) == size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def _insert_chunks(cursor: Any, statement: str, rows: Iterable[tuple[Any, ...]], size: int) -> None:
    for chunk in _chunks(rows, size):
        cursor.executemany(statement, chunk)


def _reconcile(cursor: Any, source_file_id: int, quality: "ST60QualityResult", expected: dict[str, Decimal]) -> None:
    """Compare every stored numeric sum and row count with source decimals."""
    cursor.execute(
        "SELECT COUNT(*) FROM aei.st60_source_row WHERE source_file_id = %s",
        (source_file_id,),
    )
    raw_count = cursor.fetchone()[0]
    cursor.execute(
        "SELECT COUNT(*) FROM aei.st60_quality_issue WHERE source_file_id = %s",
        (source_file_id,),
    )
    issue_count = cursor.fetchone()[0]
    sums = ", ".join(f"COALESCE(SUM({name}), 0)" for name in NUMERIC_DB_NAMES)
    cursor.execute(
        f"SELECT COUNT(*), {sums} FROM aei.st60_battery_month "
        "WHERE source_file_id = %s",
        (source_file_id,),
    )
    fact_count, *actual_totals = cursor.fetchone()
    if raw_count != quality.summary["input_rows"]:
        raise ST60LoadError(f"Raw count mismatch: {raw_count}")
    if issue_count != len(quality.issues):
        raise ST60LoadError(f"Quality issue count mismatch: {issue_count}")
    if fact_count != quality.summary["accepted_rows"]:
        raise ST60LoadError(f"Accepted fact count mismatch: {fact_count}")
    for column, actual in zip(NUMERIC_DB_NAMES, actual_totals, strict=True):
        if Decimal(actual) != expected[column]:
            raise ST60LoadError(
                f"Database sum differs for {column}: "
                f"SQL={actual}, source={expected[column]}"
            )


def load_st60_month(
    connection: Any,
    batch: "ST60Batch",
    quality: "ST60QualityResult",
    *,
    chunk_size: int = 1000,
) -> LoadResult:
    """Store a full source snapshot and activate it only after reconciliation.

    Expects a Psycopg 3 connection with autocommit=True. Identical sources
    are skipped. Separate months have separate transactions so a failed
    archive month can be retried without losing earlier committed months.
    """
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    validate_quality(batch, quality)
    summary = quality.summary
    if summary["accepted_rows"] == 0:
        raise ST60LoadError("No accepted records; refusing to activate an empty month")
    report_month = batch.report_month.strftime("%Y-%m")

    with connection.transaction(), connection.cursor() as cursor:
        # Serialize loads for the same month before looking for revisions.
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s, %s)",
            (60126, batch.report_month.toordinal()),
        )
        cursor.execute(
            """SELECT source_file_id, input_rows, accepted_rows,
                      quarantined_rows, warning_rows
               FROM aei.st60_source_file
               WHERE report_month = %s AND source_sha256 = %s""",
            (batch.report_month, batch.sha256),
        )
        previous = cursor.fetchone()
        if previous is not None:
            old_counts = tuple(int(value) for value in previous[1:])
            new_counts = tuple(summary[key] for key in (
                "input_rows", "accepted_rows", "quarantined_rows",
                "warning_rows",
            ))
            if old_counts != new_counts:
                raise ST60LoadError(
                    "This file checksum was loaded with different quality "
                    "counts. Review rule changes before reloading."
                )
            return LoadResult(
                report_month, batch.sha256, int(previous[0]), "already_loaded",
                summary["input_rows"], summary["accepted_rows"],
                summary["quarantined_rows"], len(quality.issues),
            )

        source_file_name = (
            PurePosixPath(batch.source_member).name
            if batch.source_member is not None
            else batch.source_path.name
        )
        archive_name = batch.source_path.name if batch.source_member else None
        from psycopg.types.json import Jsonb

        cursor.execute(
            """INSERT INTO aei.st60_source_file
                (report_month, source_file_name, source_sha256,
                 source_archive_name, source_archive_sha256,
                 source_archive_member, source_encoding, source_metadata,
                 input_rows, accepted_rows, quarantined_rows, warning_rows)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING source_file_id""",
            (
                batch.report_month, source_file_name, batch.sha256,
                archive_name, batch.source_archive_sha256,
                batch.source_member, batch.encoding, Jsonb(list(batch.metadata)),
                summary["input_rows"], summary["accepted_rows"],
                summary["quarantined_rows"], summary["warning_rows"],
            ),
        )
        source_file_id = int(cursor.fetchone()[0])
        _insert_chunks(cursor, _INSERT_RAW, raw_rows(source_file_id, quality), chunk_size)
        _insert_chunks(cursor, _INSERT_ISSUE, issue_rows(source_file_id, quality), chunk_size)

        expected = {name: Decimal(0) for name in NUMERIC_DB_NAMES}
        numeric_positions = {
            column: FACT_COLUMNS.index(column) for column in NUMERIC_DB_NAMES
        }

        def facts_and_totals() -> Iterator[tuple[Any, ...]]:
            for row in fact_rows(source_file_id, batch, quality):
                for column, position in numeric_positions.items():
                    if row[position] is not None:
                        expected[column] += row[position]
                yield row

        _insert_chunks(cursor, _INSERT_FACT, facts_and_totals(), chunk_size)
        _reconcile(cursor, source_file_id, quality, expected)

        # Both statements are in the same transaction: never show two
        # active revisions or a partially validated new revision.
        cursor.execute(
            """UPDATE aei.st60_source_file SET is_active = FALSE
               WHERE report_month = %s AND is_active""",
            (batch.report_month,),
        )
        cursor.execute(
            """UPDATE aei.st60_source_file SET is_active = TRUE
               WHERE source_file_id = %s""",
            (source_file_id,),
        )
        cursor.execute(
            """SELECT COUNT(*) FROM aei.v_st60_current_battery_month
               WHERE source_file_id = %s""",
            (source_file_id,),
        )
        if cursor.fetchone()[0] != summary["accepted_rows"]:
            raise ST60LoadError("Active-source view count did not reconcile")

    return LoadResult(
        report_month, batch.sha256, source_file_id, "loaded",
        summary["input_rows"], summary["accepted_rows"],
        summary["quarantined_rows"], len(quality.issues),
    )
