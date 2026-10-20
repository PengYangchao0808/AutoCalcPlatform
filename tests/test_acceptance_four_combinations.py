"""Unit tests for the four-combination acceptance harness (T44)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "acceptance" / "four_combinations.py"
sys.path.insert(0, str(SCRIPT.parent))

import four_combinations as harness  # noqa: E402


def _fixture(f0: str = harness.F0_REVISION, f1: str = "candidate-sha") -> dict:
    return {"cells": harness._default_fixture(f0, f1)}


def test_validate_cells_accepts_default_fixture() -> None:
    cells = harness.validate_cells(_fixture()["cells"])
    assert {cell["cell"] for cell in cells} == set(harness.CELL_NAMES)


def test_validate_cells_rejects_s1_as_s0() -> None:
    cells = _fixture()["cells"]
    cells[0]["service_revision"] = "candidate-sha"
    try:
        harness.validate_cells(cells)
    except harness.HarnessError as exc:
        assert "S1-as-S0" in str(exc)
    else:  # pragma: no cover - the guard must fire
        raise AssertionError("S1-as-S0 fixture was accepted")


def test_validate_cells_rejects_testclient_as_f0() -> None:
    cells = _fixture()["cells"]
    cells[0]["frontend_kind"] = "testclient"
    try:
        harness.validate_cells(cells)
    except harness.HarnessError as exc:
        assert "frontend_kind" in str(exc) or "TestClient" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("TestClient-as-F0 fixture was accepted")


def test_validate_cells_rejects_missing_cell() -> None:
    cells = _fixture()["cells"][:3]
    try:
        harness.validate_cells(cells)
    except harness.HarnessError as exc:
        assert "exactly" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("incomplete fixture was accepted")


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_help_exits_zero() -> None:
    result = _run("--help")
    assert result.returncode == 0
    assert "F0/S0" not in result.stdout or "four" in result.stdout.lower()


def test_check_only_valid_fixture(tmp_path: Path) -> None:
    path = tmp_path / "cells.json"
    path.write_text(json.dumps(_fixture()), encoding="utf-8")
    result = _run("--check-only", str(path))
    assert result.returncode == 0, result.stderr
    assert "VALID" in result.stdout


def test_check_only_rejects_s1_as_s0(tmp_path: Path) -> None:
    fixture = _fixture()
    fixture["cells"][0]["service_revision"] = "candidate-sha"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(fixture), encoding="utf-8")
    result = _run("--check-only", str(path))
    assert result.returncode == 1
    assert "INVALID" in result.stderr


def test_dry_run_lists_all_cells() -> None:
    result = _run("--dry-run")
    assert result.returncode == 0, result.stderr
    for cell in harness.CELL_NAMES:
        assert cell in result.stdout
    assert "uvicorn" in result.stdout
