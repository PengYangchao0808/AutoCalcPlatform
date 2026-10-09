"""Real-ORCA manual-command acceptance for ``acp run scan`` (BUG-3(b)).

Gated exactly like ``tests/test_pes_orca_simulscan_integration.py`` (the
only real-ORCA scan precedent): ``requires_orca`` (conftest production
resolver) + ``--run-slow``.

Two cases:

* **CCO manual command** — the README command
  ``acp run scan --input "CCO" --coordinate 3,4,1.0,3.0`` with pure CLI
  defaults (21 points, r2SCAN-3c, T4 ``geom_maxiter=200``).  Asserts the
  run succeeds, ALL 21 scan points converge (per-point ORCA log markers +
  profile flags + exact frame counts) and the generated ORCA input
  renders ``MaxIter 200`` inside the ``%geom`` block — the T4 pipeline
  proven end-to-end in a real run.
* **Water control regression** — the audit's water O-H relaxed scan
  (real-qc-gap task-14 case 1): 3 points 1.05 -> 1.25 A, r2SCAN-3c;
  frames, target distances and reference SCF energies (within 1e-3 Eh)
  must still match the recorded PASS.

Per-point convergence / cycle counts / wall times are written to the
task-5 evidence JSON for future budget calibration.  The CCO subprocess
carries an explicit 240-minute timeout (hung-command budget probe).
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from tests.conftest import requires_orca

_REPO_ROOT = Path(__file__).resolve().parents[1]
_EVIDENCE_PATH = (
    _REPO_ROOT
    / ".omo"
    / "evidence"
    / "acp-legacy-bug-remediation"
    / "task-5-acp-legacy-bug-remediation.json"
)
_EVIDENCE_SCHEMA = "acp_scan_manual_acceptance_evidence_v1"

_CCO_TIMEOUT_S = 240 * 60  # mandated runtime budget for the 21-point CCO scan
_WATER_TIMEOUT_S = 30 * 60

_CCO_SCAN_POINTS = 21  # cli.py --scan-points default
_CCO_GEOM_MAXITER = 200  # T4 _handle_scan resources.setdefault default
_WATER_TARGETS = (1.05, 1.15, 1.25)
# Audit reference (real-qc-gap task-14 case 1): water O-H scan, r2SCAN-3c,
# ORCA 6.1.1 — recorded SCF energies of the three constrained points (Eh).
_WATER_REFERENCE_ENERGIES = (-76.41475646, -76.39847304, -76.3775457)

_WATER_XYZ = (
    "3\n"
    "water\n"
    "O 0.0000000000 0.0000000000 0.1173000000\n"
    "H 0.0000000000 0.7572000000 -0.4692000000\n"
    "H 0.0000000000 -0.7572000000 -0.4692000000\n"
)

_STEP_RE = re.compile(r"RELAXED SURFACE SCAN STEP\s+(\d+)")
_CYCLE_RE = re.compile(r"GEOMETRY OPTIMIZATION CYCLE\s+\d+")
_CONVERGED_RE = re.compile(r"THE OPTIMIZATION HAS CONVERGED")
_NOT_CONVERGED_RE = re.compile(r"OPTIMIZATION DID NOT CONVERGE|SCF NOT CONVERGED")
_NPROCS_RE = re.compile(r"%pal nprocs (\d+)")


def _run_cli(args: list[str], *, timeout_s: float, tmp_path: Path) -> dict[str, Any]:
    """Run ``python -m acp.cli`` with a hard timeout; capture output tails.

    On timeout the whole process group (CLI + ORCA children) is signalled
    so a hung run cannot outlive the budget probe.  ``~/.cccp.yaml``
    production resolution (ORCA path) is deliberately preserved — HOME is
    untouched — while ``ACP_RUN_ROOT`` is pinned to the test's tmp dir.
    ``tests.conftest`` sets ``ACP_DISABLE_MPI_SNIFF=1`` for mock-subprocess
    isolation; that escape hatch is dropped here because a real ORCA child
    must resolve the OpenMPI runtime through the production login-shell
    sniff (otherwise conda's ABI-incompatible ``mpiexec.hydra`` wins and
    every scan point fails).  Mirrors ``tests/test_pes_e2e_propylene.py``.
    """
    cmd = [sys.executable, "-m", "acp.cli", *args]
    env = dict(os.environ)
    env.pop("ACP_DISABLE_MPI_SNIFF", None)
    env["ACP_RUN_ROOT"] = str(tmp_path / "run_root")
    start = time.monotonic()
    timed_out = False
    exit_code: int | None
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=str(tmp_path),
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
        exit_code = proc.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            stdout, stderr = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            stdout, stderr = proc.communicate()
        exit_code = None
    return {
        "command": cmd,
        "timeout_s": timeout_s,
        "timed_out": timed_out,
        "exit_code": exit_code,
        "wall_time_s": round(time.monotonic() - start, 2),
        "stdout_tail": stdout[-4000:],
        "stderr_tail": stderr[-4000:],
    }


def _locate(out_root: Path) -> dict[str, Any]:
    """Find the single task artifacts under a fresh ``--output`` root."""
    trajectories = sorted(out_root.glob("**/RESULT/trajectories/scan_trajectory.json"))
    orca_dirs = sorted(out_root.glob("**/WORK/07_PATH/ORCA"))
    return {
        "trajectory": trajectories[0] if len(trajectories) == 1 else None,
        "trajectory_count": len(trajectories),
        "orca_dir": orca_dirs[0] if len(orca_dirs) == 1 else None,
        "orca_dir_count": len(orca_dirs),
    }


def _geom_block(inp_text: str) -> str:
    """Slice the ``%geom`` block the way T4's render test does.

    The nested ``Scan ... end`` sub-block is indented, so the first
    column-0 ``end`` is the outer ``%geom`` terminator.
    """
    start = inp_text.index("%geom")
    return inp_text[start:].split("\nend\n", 1)[0]


def _parse_scan_out(out_text: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Per-step cycle/convergence table + run-level flags from the ORCA log."""
    steps = list(_STEP_RE.finditer(out_text))
    per_step: list[dict[str, Any]] = []
    for index, match in enumerate(steps):
        seg_end = steps[index + 1].start() if index + 1 < len(steps) else len(out_text)
        segment = out_text[match.end() : seg_end]
        per_step.append(
            {
                "step": int(match.group(1)),
                "geom_cycles": len(_CYCLE_RE.findall(segment)),
                "converged_markers": len(_CONVERGED_RE.findall(segment)),
            }
        )
    summary = {
        "scan_steps": len(steps),
        "converged_markers_total": len(_CONVERGED_RE.findall(out_text)),
        "not_converged_markers": len(_NOT_CONVERGED_RE.findall(out_text)),
        "scan_done": "RELAXED SURFACE SCAN DONE" in out_text,
    }
    return per_step, summary


def _per_point_wall_times(orca_dir: Path, points: int) -> list[float | None]:
    """Approximate per-point wall time from per-point ``.NNN.xyz`` mtimes.

    ORCA writes each point's relaxed geometry as the scan progresses, so
    consecutive mtime deltas bound the per-point cost (first point measured
    from the rendered input).  Calibration evidence only — never asserted.
    """
    inp = orca_dir / "orca_relaxed_scan.inp"
    if not inp.is_file():
        return [None] * points
    previous = inp.stat().st_mtime
    times: list[float | None] = []
    for number in range(1, points + 1):
        point_file = orca_dir / f"orca_relaxed_scan.{number:03d}.xyz"
        if not point_file.is_file():
            times.append(None)
            continue
        mtime = point_file.stat().st_mtime
        times.append(round(max(mtime - previous, 0.0), 2))
        previous = mtime
    return times


def _write_evidence(section: str, payload: dict[str, Any]) -> None:
    """Merge one run's record into the task-5 evidence JSON (atomic)."""
    _EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    document: dict[str, Any] = {}
    if _EVIDENCE_PATH.is_file():
        try:
            loaded = json.loads(_EVIDENCE_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                document = loaded
        except json.JSONDecodeError:
            document = {"note": "corrupt prior evidence replaced"}
    document.setdefault("schema", _EVIDENCE_SCHEMA)
    document.setdefault("task", "acp-legacy-bug-remediation / task-5")
    document.setdefault(
        "plan",
        ".omo/plans/acp-legacy-bug-remediation.md (todo 5 — BUG-3(b))",
    )
    document.setdefault("date", time.strftime("%Y-%m-%d"))
    runs = document.setdefault("runs", {})
    if not isinstance(runs, dict):
        runs = {}
        document["runs"] = runs
    runs[section] = payload
    tmp_path = _EVIDENCE_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp_path, _EVIDENCE_PATH)


def _analyse_run(out_root: Path, expected_points: int, geom_maxiter: int) -> dict[str, Any]:
    """Collect trajectory/profile/input/log facts for evidence + assertions."""
    located = _locate(out_root)
    analysis: dict[str, Any] = {
        "trajectory_path": str(located["trajectory"]) if located["trajectory"] else None,
        "trajectory_count": located["trajectory_count"],
        "orca_dir_count": located["orca_dir_count"],
        "trajectory": None,
        "profile": None,
        "input": None,
        "orca_log": None,
    }
    trajectory_path = located["trajectory"]
    if isinstance(trajectory_path, Path):
        payload = json.loads(trajectory_path.read_text(encoding="utf-8"))
        frames = payload.get("frames", [])
        energies = [frame.get("energy_hartree") for frame in frames if isinstance(frame, dict)]
        numeric = [float(e) for e in energies if e is not None]
        analysis["trajectory"] = {
            "frame_count": payload.get("frame_count"),
            "successful_frame_count": payload.get("successful_frame_count"),
            "points": payload.get("points"),
            "frames_len": len(frames),
            "energy_min_hartree": min(numeric) if numeric else None,
            "energy_max_hartree": max(numeric) if numeric else None,
            "missing_energy_frames": sum(1 for e in energies if e is None),
        }
    orca_dir = located["orca_dir"]
    if isinstance(orca_dir, Path):
        profile_path = orca_dir / "scan_profile.json"
        profile_frames: list[dict[str, Any]] = []
        if profile_path.is_file():
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            profile_frames = profile.get("frames", [])
            analysis["profile"] = {
                "frame_count": profile.get("frame_count"),
                "successful_frame_count": profile.get("successful_frame_count"),
                "points": profile.get("points"),
                "all_converged": (
                    all(bool(f.get("converged")) for f in profile_frames)
                    if profile_frames
                    else False
                ),
                "all_success": (
                    all(bool(f.get("success")) for f in profile_frames) if profile_frames else False
                ),
            }
        inp_path = orca_dir / "orca_relaxed_scan.inp"
        out_path = orca_dir / "orca_relaxed_scan.out"
        if inp_path.is_file():
            inp_text = inp_path.read_text(encoding="utf-8", errors="replace")
            geom_block = _geom_block(inp_text)
            nprocs_match = _NPROCS_RE.search(inp_text)
            first_route = next((line for line in inp_text.splitlines() if line.startswith("!")), "")
            analysis["input"] = {
                "path": str(inp_path),
                "maxiter_rendered": f"MaxIter {geom_maxiter}" in geom_block,
                "maxiter_expected": f"MaxIter {geom_maxiter}",
                "geom_block": geom_block,
                "route_line": first_route,
                "orca_nprocs": int(nprocs_match.group(1)) if nprocs_match else None,
                "has_scants": "ScanTS" in inp_text,
            }
        if out_path.is_file():
            out_text = out_path.read_text(encoding="utf-8", errors="replace")
            per_step, summary = _parse_scan_out(out_text)
            wall_times = _per_point_wall_times(orca_dir, expected_points)
            per_point = []
            for index in range(expected_points):
                step_info = per_step[index] if index < len(per_step) else {}
                profile_frame = profile_frames[index] if index < len(profile_frames) else {}
                coordinate_values = profile_frame.get("coordinate_values") or {}
                per_point.append(
                    {
                        "point": index,
                        "target": coordinate_values.get("rc1"),
                        "energy_hartree": profile_frame.get("energy_hartree"),
                        "converged": profile_frame.get("converged"),
                        "success": profile_frame.get("success"),
                        "geom_cycles": step_info.get("geom_cycles"),
                        "converged_markers": step_info.get("converged_markers"),
                        "wall_time_s": wall_times[index] if index < len(wall_times) else None,
                    }
                )
            analysis["orca_log"] = {**summary, "per_point": per_point}
    return analysis


@requires_orca
@pytest.mark.slow
def test_water_plain_scan_regression_audit_control(tmp_path: Path) -> None:
    """Audit water control still converges and reproduces reference energies.

    Real-qc-gap task-14 case 1: water O-H relaxed scan, 3 points
    1.05 -> 1.25 A, r2SCAN-3c via CLI defaults (T4 now renders
    ``MaxIter 200`` here too).
    """
    water_xyz = tmp_path / "water.xyz"
    water_xyz.write_text(_WATER_XYZ, encoding="utf-8")
    out_root = tmp_path / "out"
    out_root.mkdir()

    run = _run_cli(
        [
            "run",
            "scan",
            "--input",
            str(water_xyz),
            "--coordinate",
            "0,1,1.05,1.25",
            "--scan-points",
            "3",
            "--output",
            str(out_root),
        ],
        timeout_s=_WATER_TIMEOUT_S,
        tmp_path=tmp_path,
    )
    analysis = _analyse_run(out_root, expected_points=3, geom_maxiter=_CCO_GEOM_MAXITER)
    _write_evidence(
        "water_regression",
        {
            "control": "audit real-qc-gap task-14 case 1 (water O-H plain relaxed scan)",
            "command": run["command"],
            "exit_code": run["exit_code"],
            "timed_out": run["timed_out"],
            "timeout_s": run["timeout_s"],
            "wall_time_s": run["wall_time_s"],
            "analysis": analysis,
            "reference_energies_hartree": list(_WATER_REFERENCE_ENERGIES),
            "targets_angstrom": list(_WATER_TARGETS),
        },
    )

    assert not run["timed_out"], f"water scan exceeded {_WATER_TIMEOUT_S}s budget"
    assert run["exit_code"] == 0, (
        f"water scan failed (exit {run['exit_code']})\n"
        f"stdout tail:\n{run['stdout_tail']}\nstderr tail:\n{run['stderr_tail']}"
    )

    assert analysis["trajectory_count"] == 1, (
        f"expected exactly one scan_trajectory.json, found {analysis['trajectory_count']}"
    )
    trajectory = analysis["trajectory"]
    assert trajectory is not None
    assert trajectory["frame_count"] == 3, trajectory
    assert trajectory["successful_frame_count"] == 3, trajectory
    assert trajectory["points"] == 3, trajectory
    assert trajectory["frames_len"] == 3, trajectory
    assert trajectory["missing_energy_frames"] == 0, trajectory

    profile = analysis["profile"]
    assert profile is not None, "scan_profile.json missing"
    assert profile["all_converged"] and profile["all_success"], profile

    inp_info = analysis["input"]
    assert inp_info is not None, "ORCA input missing"
    assert inp_info["maxiter_rendered"], (
        f"'{inp_info['maxiter_expected']}' not rendered inside the %geom block "
        f"of {inp_info['path']}\ngeom block:\n{inp_info['geom_block']}"
    )
    assert not inp_info["has_scants"], "plain control must not enable ScanTS"
    assert "r2SCAN-3c" in inp_info["route_line"], inp_info["route_line"]

    # Frame geometries sit on the prescribed scan targets (audit observable).
    trajectory_path = analysis["trajectory_path"]
    assert trajectory_path is not None
    structures = Path(trajectory_path).parent.parent / "structures"
    frame_files = sorted(structures.glob("scan_frame_*.xyz"))
    assert len(frame_files) == 3, frame_files
    for index, frame_file in enumerate(frame_files):
        lines = frame_file.read_text(encoding="utf-8").splitlines()
        atom_count = int(lines[0].split()[0])
        coords = [
            np.array([float(value) for value in line.split()[1:4]])
            for line in lines[2 : 2 + atom_count]
        ]
        distance = float(np.linalg.norm(coords[0] - coords[1]))
        assert abs(distance - _WATER_TARGETS[index]) <= 0.01, (
            f"point {index}: O-H {distance:.4f} A != target {_WATER_TARGETS[index]} A"
        )

    # Energies reproduce the audit's recorded PASS (SCF energies, Eh).
    orca_dir = _locate(out_root)["orca_dir"]
    assert isinstance(orca_dir, Path)
    profile_doc = json.loads((orca_dir / "scan_profile.json").read_text(encoding="utf-8"))
    for index, frame in enumerate(profile_doc["frames"]):
        energy = frame["energy_hartree"]
        assert energy is not None, frame
        assert abs(energy - _WATER_REFERENCE_ENERGIES[index]) <= 1e-3, (
            f"point {index}: energy {energy} deviates from audit reference "
            f"{_WATER_REFERENCE_ENERGIES[index]} (tolerance 1e-3 Eh)"
        )

    log_summary = analysis["orca_log"]
    assert log_summary is not None, "ORCA .out missing"
    assert log_summary["scan_steps"] == 3, log_summary
    assert log_summary["not_converged_markers"] == 0, log_summary
    assert log_summary["scan_done"], log_summary
    unconverged = [step for step in log_summary["per_point"] if step["converged_markers"] < 1]
    assert not unconverged, f"points without an ORCA convergence marker: {unconverged}"


@requires_orca
@pytest.mark.slow
def test_cco_manual_command_full_scan_acceptance(tmp_path: Path) -> None:
    """README manual command: CCO scan converges at ALL 21 points.

    Runs the exact documented command through the real CLI subprocess with
    pure defaults (``--scan-points 21``, ``r2SCAN-3c``, T4
    ``geom_maxiter=200``), under an explicit 240-minute timeout budget.
    A partial trajectory or a single non-converged point fails the exact
    frame/step/convergence assertions — nothing is skipped or lowered.
    """
    out_root = tmp_path / "out"
    out_root.mkdir()

    run = _run_cli(
        [
            "run",
            "scan",
            "--input",
            "CCO",
            "--coordinate",
            "3,4,1.0,3.0",
            "--output",
            str(out_root),
        ],
        timeout_s=_CCO_TIMEOUT_S,
        tmp_path=tmp_path,
    )
    analysis = _analyse_run(
        out_root, expected_points=_CCO_SCAN_POINTS, geom_maxiter=_CCO_GEOM_MAXITER
    )
    _write_evidence(
        "cco_manual_command",
        {
            "control": "README manual command: acp run scan --input CCO --coordinate 3,4,1.0,3.0",
            "command": run["command"],
            "exit_code": run["exit_code"],
            "timed_out": run["timed_out"],
            "timeout_s": run["timeout_s"],
            "wall_time_s": run["wall_time_s"],
            "budget_note": "explicit 240-minute subprocess timeout (hung_commands probe)",
            "analysis": analysis,
            "expected_points": _CCO_SCAN_POINTS,
            "expected_geom_maxiter": _CCO_GEOM_MAXITER,
        },
    )

    assert not run["timed_out"], (
        f"CCO scan exceeded the {_CCO_TIMEOUT_S}s budget — see evidence per-point wall times"
    )
    assert run["exit_code"] == 0, (
        f"manual command failed (exit {run['exit_code']})\n"
        f"stdout tail:\n{run['stdout_tail']}\nstderr tail:\n{run['stderr_tail']}"
    )

    assert analysis["trajectory_count"] == 1, (
        f"expected exactly one scan_trajectory.json, found {analysis['trajectory_count']}"
    )
    trajectory = analysis["trajectory"]
    assert trajectory is not None
    assert trajectory["points"] == _CCO_SCAN_POINTS, trajectory
    assert trajectory["frame_count"] == _CCO_SCAN_POINTS, trajectory
    assert trajectory["successful_frame_count"] == _CCO_SCAN_POINTS, trajectory
    assert trajectory["frames_len"] == _CCO_SCAN_POINTS, trajectory
    assert trajectory["missing_energy_frames"] == 0, trajectory
    assert trajectory["energy_min_hartree"] is not None, trajectory
    assert trajectory["energy_max_hartree"] is not None, trajectory
    assert trajectory["energy_max_hartree"] > trajectory["energy_min_hartree"], trajectory

    profile = analysis["profile"]
    assert profile is not None, "scan_profile.json missing"
    assert profile["frame_count"] == _CCO_SCAN_POINTS, profile
    assert profile["all_converged"], profile
    assert profile["all_success"], profile

    inp_info = analysis["input"]
    assert inp_info is not None, "ORCA input missing"
    assert inp_info["maxiter_rendered"], (
        f"'{inp_info['maxiter_expected']}' not rendered inside the %geom block "
        f"of {inp_info['path']}\ngeom block:\n{inp_info['geom_block']}"
    )
    assert not inp_info["has_scants"], "manual command must not enable ScanTS"
    assert "r2SCAN-3c" in inp_info["route_line"], inp_info["route_line"]

    # Per-point convergence table: every scan step carries an ORCA
    # convergence marker, no NOT-CONVERGED markers, scan completed.
    log_summary = analysis["orca_log"]
    assert log_summary is not None, "ORCA .out missing"
    assert log_summary["scan_steps"] == _CCO_SCAN_POINTS, log_summary
    assert log_summary["not_converged_markers"] == 0, log_summary
    assert log_summary["scan_done"], log_summary
    per_point = log_summary["per_point"]
    assert len(per_point) == _CCO_SCAN_POINTS, per_point
    unconverged = [step for step in per_point if step["converged_markers"] < 1]
    assert not unconverged, f"points without an ORCA convergence marker: {unconverged}"
    missing_cycles = [step for step in per_point if not step["geom_cycles"]]
    assert not missing_cycles, f"points without geometry optimization cycles: {missing_cycles}"
