"""Guard: run residue must never be tracked under ``reports/`` again."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PREFIXES = ("reports/acp2_dev_batch_20261029/",)
FORBIDDEN_SUFFIXES = (".db-wal", ".db-shm", ".tmp", ".pyc")


def tracked_violations(paths: list[str]) -> list[str]:
    issues: list[str] = []
    for path in paths:
        if any(path.startswith(prefix) for prefix in FORBIDDEN_PREFIXES):
            issues.append(path)
        elif path.startswith("reports/") and path.endswith(FORBIDDEN_SUFFIXES):
            issues.append(path)
    return sorted(issues)


def test_tracked_tree_has_no_reports_residue() -> None:
    files = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    assert tracked_violations(files) == []


def test_guard_catches_injected_paths() -> None:
    injected = [
        "reports/acp2_dev_batch_20261029/README.md",
        "reports/whatever/acp_jobs.db-wal",
        "reports/whatever/sp.C0.tmp",
        "reports/acp2_mcp_ux_20261029/P4_ACCEPTANCE.md",
    ]
    assert tracked_violations(injected) == injected[:3]
