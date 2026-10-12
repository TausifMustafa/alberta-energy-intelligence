
"""Export reproducible ST60 data-quality and quarantine artifacts."""

import json
from pathlib import Path

from alberta_energy.ingest import ST60Batch
from alberta_energy.quality import ST60QualityResult


def write_quality_artifacts(
    batch: ST60Batch,
    result: ST60QualityResult,
    output_root: str | Path,
) -> Path:
    """Write a quality summary, issue log, and raw quarantined records.

    Outputs are grouped by reporting month and source-file checksum.
    Repeating the same assessment replaces that snapshot's output
    files rather than appending duplicate records.

    The original ST60 source is never modified.
    """
    summary = result.summary

    # Confirm all input records are accounted for before exporting.
    if (
        summary["input_rows"]
        != summary["accepted_rows"] + summary["quarantined_rows"]
    ):
        raise ValueError("Quality result has inconsistent row counts")

    if len(result.raw_records) != summary["input_rows"]:
        raise ValueError("Quality result does not match input row count")

    if len(result.quarantined) != summary["quarantined_rows"]:
        raise ValueError("Quarantine count does not match its records")

    if not result.raw_records["source_sha256"].eq(batch.sha256).all():
        raise ValueError("Quality records do not match source checksum")

    # A source revision gets a different directory.
    period = batch.report_month.strftime("%Y-%m")
    folder_name = f"{period}_{batch.sha256[:12]}"

    output_dir = Path(output_root) / folder_name
    output_dir.mkdir(parents=True, exist_ok=True)

    # Preserve ZIP provenance when the batch came from an archive.
    archive_name = (
        batch.source_path.name
        if batch.source_member is not None
        else None
    )

    source_file = (
        Path(batch.source_member).name
        if batch.source_member is not None
        else batch.source_path.name
    )

    # Use plain Python types so the summary is JSON serializable.
    report = {
        "report_month": period,
        "source_file": source_file,
        "source_sha256": batch.sha256,
        "source_archive": archive_name,
        "source_archive_member": batch.source_member,
        "source_archive_sha256": batch.source_archive_sha256,
        **{
            key: int(value)
            for key, value in summary.items()
        },
    }

    # Save the batch-level summary.
    summary_path = output_dir / "summary.json"
    summary_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # Save every issue, retaining its source row and reason code.
    issues_path = output_dir / "issues.csv"
    result.issues.to_csv(
        issues_path,
        index=False,
        encoding="utf-8",
    )

    # Export rejected records from the RAW table, not the cleaned one.
    quarantine_path = output_dir / "quarantined.csv"
    result.quarantined.to_csv(
        quarantine_path,
        index=False,
        encoding="utf-8",
    )

    return output_dir
