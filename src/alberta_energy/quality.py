
"""Assess ST60 raw records without losing source values or provenance."""

from dataclasses import dataclass

import pandas as pd

from alberta_energy.ingest import (
    EXPECTED_COLUMN_COUNT,
    NUMERIC_SUFFIXES,
    ST60Batch,
)

CORE_MEASURES = (
    "OIL PROD (M3)",
    "GAS PROD (1000 M3)",
    "GAS FLARED (1000 M3)",
    "GAS VENTED (1000 M3)",
    "WTR PROD (M3)",
)

REQUIRED_IDENTIFIERS = ("BATTERY CODE", "OPER CODE")

ISSUE_COLUMNS = (
    "report_month",
    "source_file",
    "source_sha256",
    "source_row_number",
    "source_archive",
    "source_archive_sha256",
    "source_archive_member",
    "battery_code",
    "column",
    "raw_value",
    "reason_code",
    "severity",
)


@dataclass(frozen=True)
class ST60QualityResult:
    """Clean accepted rows, raw quarantined rows, issues, and counts."""

    accepted: pd.DataFrame
    quarantined: pd.DataFrame
    issues: pd.DataFrame
    raw_records: pd.DataFrame
    summary: dict[str, int]


def assess_st60_quality(batch: ST60Batch) -> ST60QualityResult:
    """Classify raw-mode ST60 records without mutating the input.

    Invalid identifiers, duplicate battery/month keys, and malformed
    numeric values are quarantined.

    Missing core measures and negative quantities generate warnings.
    File-level structural validation remains the reader's responsibility.
    """
    raw = batch.records.copy(deep=True)

    source_columns = raw.columns[:EXPECTED_COLUMN_COUNT]
    numeric_columns = [
        name
        for name in source_columns
        if name.endswith(NUMERIC_SUFFIXES)
    ]

    # Require raw-mode input, not already-converted strict-mode records.
    for column in numeric_columns:
        if not isinstance(raw[column].dtype, pd.StringDtype):
            raise TypeError(
                "Pass a batch from "
                "read_st60_csv(..., record_mode='raw')"
            )

    # Work on a separate copy so source values remain available.
    cleaned = raw.copy(deep=True)

    for column in source_columns:
        cleaned[column] = cleaned[column].str.strip()

    rejected = pd.Series(False, index=cleaned.index, dtype=bool)
    warned = pd.Series(False, index=cleaned.index, dtype=bool)

    issues: list[dict[str, object]] = []

    def flag(
        index: int,
        column: str,
        reason: str,
        severity: str,
    ) -> None:
        """Record one traceable issue and update the row status."""
        record = raw.loc[index]

        issues.append(
            {
                "report_month": record["report_month"],
                "source_file": record["source_file"],
                "source_sha256": record["source_sha256"],
                "source_row_number": record["source_row_number"],
                "source_archive": record.get("source_archive"),
                "source_archive_sha256": record.get(
                    "source_archive_sha256"
                ),
                "source_archive_member": record.get(
                    "source_archive_member"
                ),
                "battery_code": record["BATTERY CODE"],
                "column": column,
                "raw_value": record[column],
                "reason_code": reason,
                "severity": severity,
            }
        )

        if severity == "error":
            rejected.loc[index] = True
        else:
            warned.loc[index] = True

    # 1. Required identifier validation.
    for column in REQUIRED_IDENTIFIERS:
        missing = cleaned[column].isna() | cleaned[column].eq("")

        for index in cleaned.index[missing]:
            flag(index, column, "missing_identifier", "error")

    # 2. Reject every conflicting battery/month record.
    duplicate = (
        cleaned["BATTERY CODE"].ne("")
        & cleaned.duplicated(
            ["report_month", "BATTERY CODE"],
            keep=False,
        )
    ).fillna(False)

    for index in cleaned.index[duplicate]:
        flag(
            index,
            "BATTERY CODE",
            "duplicate_battery_month",
            "error",
        )

    # 3. Convert numeric fields and record issues.
    for column in numeric_columns:
        text = cleaned[column]

        missing = text.isna() | text.eq("")

        parsed = pd.to_numeric(
            text.mask(missing),
            errors="coerce",
        )

        invalid = (
            ~missing
            & (
                parsed.isna()
                | parsed.isin([float("inf"), float("-inf")])
            )
        ).fillna(False)

        for index in cleaned.index[invalid]:
            flag(index, column, "invalid_numeric", "error")

        # Nullable Float64 preserves missing values as missing.
        cleaned[column] = parsed.astype("Float64")

        # Missing core measures are warnings, not automatic zeros.
        if column in CORE_MEASURES:
            for index in cleaned.index[missing]:
                flag(index, column, "missing_measure", "warning")

        # Negative quantities need review, not automatic deletion.
        negatives = (
            cleaned[column].lt(0) & ~invalid
        ).fillna(False)

        for index in cleaned.index[negatives]:
            flag(index, column, "negative_quantity", "warning")

    # 4. Split accepted and quarantined observations.
    accepted = cleaned.loc[~rejected].copy()
    quarantined = raw.loc[rejected].copy()

    # Keep one issue per observed problem, with consistent columns.
    issue_frame = pd.DataFrame(
        issues,
        columns=ISSUE_COLUMNS,
    )

    if not issue_frame.empty:
        issue_frame = issue_frame.sort_values(
            [
                "source_row_number",
                "severity",
                "column",
                "reason_code",
            ],
            kind="stable",
        ).reset_index(drop=True)

    # Count warning rows only among accepted observations.
    summary = {
        "input_rows": len(raw),
        "accepted_rows": len(accepted),
        "quarantined_rows": len(quarantined),
        "warning_rows": int((warned & ~rejected).sum()),
        "error_issues": int(
            (issue_frame["severity"] == "error").sum()
        ),
        "warning_issues": int(
            (issue_frame["severity"] == "warning").sum()
        ),
    }

    return ST60QualityResult(
        accepted=accepted,
        quarantined=quarantined,
        issues=issue_frame,
        raw_records=raw,
        summary=summary,
    )
