#!/usr/bin/env python3
"""Read an ACP2 evidence report and print execution facts plus claim status.

The report path is supplied with ``--report``; nothing is imported from the
repository and nothing is written.  Execution facts are printed separately
from the per-claim status table, because a completed process is not evidence
that a scientific claim is supported.

Usage:
    python read_report.py --report evidence-report.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from cccp.analysis.research.evidence import evidence_report_from_dict


def _status_table(report: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in report.status_rows():
        rows.append(
            {
                "claim_id": row["claim_id"],
                "status": row["status"],
                "validator_id": row["validator_id"],
                "validator_version": row["validator_version"],
                "validator_kind": row["validator_kind"],
                "source_ref_count": row["source_ref_count"],
                "missing_checks": row["missing_checks"],
                "limitations": row["limitations"],
            }
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    """Parse the report, print the separated facts and return 0 (2 on input error)."""
    parser = argparse.ArgumentParser(
        description="Read an ACP2 evidence report (software-free, read-only)."
    )
    parser.add_argument("--report", required=True, help="path to an EvidenceReport JSON file")
    args = parser.parse_args(argv)

    path = Path(args.report)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        report = evidence_report_from_dict(payload)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR: cannot read evidence report {path}: {exc}", file=sys.stderr)
        return 2

    document = {
        "example": "read_report",
        "report_path": str(path),
        "report_id": report.report_id,
        "schema_version": report.schema_version,
        "subject": report.subject.to_dict(),
        "execution": report.execution.to_dict(),
        "execution_completed": report.execution.completed,
        "overall_claim_status": report.overall_claim_status,
        "claim_statuses": report.claim_statuses,
        "status_table": _status_table(report),
        "limitations": [limitation.to_dict() for limitation in report.limitations],
        "provenance_digest": (
            None if report.provenance_digest is None else report.provenance_digest.to_dict()
        ),
    }
    print(json.dumps(document, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
