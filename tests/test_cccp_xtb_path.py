"""Tests for the xTB path-search interface."""

# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnusedCallResult=false

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from cccp.qc.interfaces.xtb_path import XTBPathInterface

PATH_TRAJECTORY = """3
Frame 0 | energy: -100.10000000
H 0.000000 0.000000 0.000000
H 1.000000 0.000000 0.000000
H 0.000000 1.000000 0.000000
3
Frame 1 | energy=-100.05000000
H 0.000000 0.000000 0.000000
H 1.500000 0.000000 0.000000
H 0.000000 1.000000 0.000000
"""


def _write_xyz(path: Path, distance: float) -> Path:
    path.write_text(
        f"3\nframe\nH 0.0 0.0 0.0\nH {distance:.6f} 0.0 0.0\nH 0.0 1.0 0.0\n",
        encoding="utf-8",
    )
    return path


def test_xtb_path_search_writes_input_and_parses_multiframe_output(
    sample_config: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interface = XTBPathInterface(sample_config, solvent_model="alpb")
    interface.executable = Path("/usr/bin/xtb")
    start_xyz = _write_xyz(tmp_path / "start.xyz", 1.0)
    end_xyz = _write_xyz(tmp_path / "end.xyz", 1.5)
    run_dir = tmp_path / "path_run"
    captured: dict[str, object] = {}

    def _fake_run(
        cmd: list[str],
        *,
        cwd: Path,
        capture_output: bool,
        text: bool,
        timeout: int | None,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        _ = capture_output
        _ = text
        _ = timeout
        captured["cmd"] = cmd
        captured["env"] = env
        (Path(cwd) / "xtbpath.txt").write_text(PATH_TRAJECTORY, encoding="utf-8")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="path ok\n", stderr="")

    monkeypatch.setattr("cccp.qc.interfaces.xtb_path.subprocess.run", _fake_run)

    result = interface.path_search(
        start_xyz,
        end_xyz,
        run_dir,
        nrun=2,
        npoint=9,
        anopt=12,
        kpush=0.004,
        kpull=-0.020,
        ppull=0.060,
        alp=1.4,
        charge=-1,
        multiplicity=2,
        gfn_level=1,
        solvent="water",
    )

    assert result.success is True
    assert len(result.frame_paths) == 2
    assert result.energies_hartree == pytest.approx([-100.1, -100.05])
    assert result.stdout_file == run_dir / "xtb_path.stdout.log"
    assert result.stderr_file == run_dir / "xtb_path.stderr.log"
    assert result.trajectory_file == run_dir / "xtbpath.txt"
    assert result.frame_paths[0].read_text(encoding="utf-8").startswith("3\nFrame 0")

    input_text = (run_dir / "path.inp").read_text(encoding="utf-8")
    assert "nrun=2" in input_text
    assert "npoint=9" in input_text
    assert "anopt=12" in input_text
    assert "kpush=0.004" in input_text
    assert "kpull=-0.02" in input_text
    assert "ppull=0.06" in input_text
    assert "alp=1.4" in input_text

    cmd = captured["cmd"]
    env = captured["env"]
    assert isinstance(cmd, list)
    assert cmd[:5] == [
        "/usr/bin/xtb",
        "start.xyz",
        "--path",
        "end.xyz",
        "--input",
    ]
    assert "-P" in cmd
    assert "--chrg" in cmd and "-1" in cmd
    assert "--uhf" in cmd and "1" in cmd
    assert "--gfn" in cmd and "1" in cmd
    assert "--alpb" in cmd
    assert isinstance(env, dict)
    assert env["OMP_NUM_THREADS"] == "1"
    assert env["MKL_NUM_THREADS"] == "1"
    assert env["OPENBLAS_NUM_THREADS"] == "1"


def test_xtb_path_search_reports_missing_binary(
    sample_config: dict[str, object],
    tmp_path: Path,
) -> None:
    interface = XTBPathInterface(sample_config)
    interface.executable = None
    start_xyz = _write_xyz(tmp_path / "start.xyz", 1.0)
    end_xyz = _write_xyz(tmp_path / "end.xyz", 1.5)

    result = interface.path_search(start_xyz, end_xyz, tmp_path / "missing")

    assert result.success is False
    assert result.error_message is not None
    assert "xTB executable not found" in result.error_message
