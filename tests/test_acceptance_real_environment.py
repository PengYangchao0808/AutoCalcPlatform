"""Unit tests for the real-environment acceptance harness (T45)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "acceptance" / "real_environment.py"


def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    base = dict(os.environ)
    base.pop("ACP_M1_ORCA", None)
    base.pop("ACP_LSF_NODE", None)
    if env:
        base.update(env)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
        env=base,
    )


def test_help_exits_zero() -> None:
    result = _run("--help")
    assert result.returncode == 0
    assert "m1-singlepoint" in result.stdout


def test_dry_run_m1_prints_commands() -> None:
    result = _run("--scenario", "m1-singlepoint", "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "acp.cli run serve" in result.stdout
    assert "structure-viewer" in result.stdout


def test_dry_run_lsf_prints_commands() -> None:
    result = _run("--scenario", "lsf-singlepoint", "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "bsub" in result.stdout
    assert "bjobs" in result.stdout
    assert "bstop" in result.stdout


def test_missing_m1_env_is_not_verified(tmp_path: Path) -> None:
    result = _run("--scenario", "m1-singlepoint", "--out", str(tmp_path))
    assert result.returncode == 2, result.stderr
    document = json.loads((tmp_path / "m1-singlepoint.json").read_text())
    assert document["result"] == "NOT_VERIFIED"
    assert document["not_verified_reason"] == "ACP_M1_ORCA unset"


def test_missing_lsf_env_is_not_verified(tmp_path: Path) -> None:
    result = _run("--scenario", "lsf-singlepoint", "--out", str(tmp_path))
    assert result.returncode == 2, result.stderr
    document = json.loads((tmp_path / "lsf-singlepoint.json").read_text())
    assert document["result"] == "NOT_VERIFIED"
    assert document["not_verified_reason"] == "ACP_LSF_NODE unset"


def test_missing_out_is_usage_error() -> None:
    result = _run("--scenario", "m1-singlepoint")
    assert result.returncode == 3


def test_unknown_scenario_is_usage_error() -> None:
    result = _run("--scenario", "bogus", "--out", "/tmp/nowhere")
    assert result.returncode == 2  # argparse usage error
