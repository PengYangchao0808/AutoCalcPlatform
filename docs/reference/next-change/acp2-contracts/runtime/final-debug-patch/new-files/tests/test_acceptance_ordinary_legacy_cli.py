"""Each contracted legacy entry receives real inputs and preserves failure exits."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.acceptance.ordinary_legacy_cli import LEGACY_WORKFLOWS, run_legacy_cli_checks


@pytest.mark.parametrize("workflow", LEGACY_WORKFLOWS)
def test_ordinary_legacy_cli_success_and_failure(workflow, tmp_path):
    root = Path(__file__).resolve().parents[1]
    rows = run_legacy_cli_checks(root, tmp_path, [workflow])
    assert len(rows) == 1
    row = rows[0]
    if row["result"] == "NOT_VERIFIED":
        pytest.skip(f"NOT_VERIFIED: {row['reason']}")
    assert row["result"] == "PASS", json.dumps(row, indent=2)
    assert row["ok"] is True
    assert len(row["cases"]) == 2
    assert [case["exit_code"] for case in row["cases"]] == [0, 1]
    assert all(len(case["received"]) == 1 for case in row["cases"])
    assert row["real_qc"] == "NOT_VERIFIED"
    assert row["scheduler_background_publication"] == "NOT_VERIFIED"
