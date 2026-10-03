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

RECIPE_PATH_INP = "$path\n   nrun=1\n   npoint=30\n   anopt=8\n   kpush=0.005\n$end\n"


def _write_xyz(path: Path, distance: float) -> Path:
    path.write_text(
        f"3\nframe\nH 0.0 0.0 0.0\nH {distance:.6f} 0.0 0.0\nH 0.0 1.0 0.0\n",
        encoding="utf-8",
    )
    return path


def _make_interface(sample_config: dict[str, object]) -> XTBPathInterface:
    interface = XTBPathInterface(sample_config)
    interface.executable = Path("/usr/bin/xtb")
    return interface


def _run_path_search(
    interface: XTBPathInterface,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    captured: dict[str, object],
    **kwargs: object,
):
    start_xyz = _write_xyz(tmp_path / "start.xyz", 1.0)
    end_xyz = _write_xyz(tmp_path / "end.xyz", 1.5)
    run_dir = tmp_path / "path_run"

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
    result = interface.path_search(start_xyz, end_xyz, run_dir, **kwargs)  # type: ignore[arg-type]
    return result, run_dir, captured


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


def test_path_inp_text_written_verbatim(
    sample_config: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interface = _make_interface(sample_config)
    captured: dict[str, object] = {}
    result, run_dir, _ = _run_path_search(
        interface,
        tmp_path,
        monkeypatch,
        captured,
        path_inp_text=RECIPE_PATH_INP,
    )

    assert result.success is True
    assert (run_dir / "path.inp").read_text(encoding="utf-8") == RECIPE_PATH_INP


def test_gfn_and_uhf_zero_always_in_argv(
    sample_config: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interface = _make_interface(sample_config)
    captured: dict[str, object] = {}
    result, _, _ = _run_path_search(interface, tmp_path, monkeypatch, captured)

    assert result.success is True
    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    assert "--gfn" in cmd
    assert cmd[cmd.index("--gfn") + 1] == "2"
    assert "--uhf" in cmd
    assert cmd[cmd.index("--uhf") + 1] == "0"


def test_extra_args_appended(
    sample_config: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interface = _make_interface(sample_config)
    captured: dict[str, object] = {}
    result, _, _ = _run_path_search(
        interface,
        tmp_path,
        monkeypatch,
        captured,
        extra_args=["--norestart", "--cma"],
    )

    assert result.success is True
    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    assert cmd[-2:] == ["--norestart", "--cma"]


def test_seed_passed_only_when_not_none(
    sample_config: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interface = _make_interface(sample_config)

    captured_no_seed: dict[str, object] = {}
    result, _, _ = _run_path_search(interface, tmp_path, monkeypatch, captured_no_seed)
    assert result.success is True
    cmd = captured_no_seed["cmd"]
    assert isinstance(cmd, list)
    assert "--seed" not in cmd

    captured_seed: dict[str, object] = {}
    result2, _, _ = _run_path_search(interface, tmp_path, monkeypatch, captured_seed, seed=42)
    assert result2.success is True
    cmd2 = captured_seed["cmd"]
    assert isinstance(cmd2, list)
    assert "--seed" in cmd2
    assert cmd2[cmd2.index("--seed") + 1] == "42"


def test_backward_compat_when_new_args_omitted(
    sample_config: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interface = _make_interface(sample_config)
    captured: dict[str, object] = {}
    result, run_dir, _ = _run_path_search(
        interface,
        tmp_path,
        monkeypatch,
        captured,
        nrun=2,
        npoint=9,
        multiplicity=2,
        gfn_level=1,
    )

    assert result.success is True
    input_text = (run_dir / "path.inp").read_text(encoding="utf-8")
    assert "nrun=2" in input_text
    assert "npoint=9" in input_text
    assert "kpush=0.003" in input_text

    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    assert cmd[cmd.index("--uhf") + 1] == "1"
    assert cmd[cmd.index("--gfn") + 1] == "1"
    assert "--seed" not in cmd


def test_uhf_explicit_overrides_multiplicity_mapping(
    sample_config: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interface = _make_interface(sample_config)
    captured: dict[str, object] = {}
    result, _, _ = _run_path_search(
        interface,
        tmp_path,
        monkeypatch,
        captured,
        uhf=3,
        multiplicity=1,
    )

    assert result.success is True
    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    assert cmd[cmd.index("--uhf") + 1] == "3"
