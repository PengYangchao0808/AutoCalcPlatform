from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol, TypedDict

import pytest

from scripts.repair_optimization_trajectories import main


class _Cycle(TypedDict, total=False):
    geometry_ref: str


class _Payload(TypedDict):
    cycles: list[_Cycle]


class _JsonLoader(Protocol):
    def loads(self, text: str, /) -> _Payload: ...


_JSON_LOADER: _JsonLoader = json


TASK_NAME = "20260905_211500_001_BatchOptimize"


def _orca_output() -> str:
    blocks: list[str] = []
    for cycle, energy, z in (
        (1, "-10.000000", "0.000000"),
        (2, "-10.100000", "1.100000"),
        (3, "-10.200000", "1.200000"),
    ):
        blocks.extend(
            [
                f"GEOMETRY OPTIMIZATION CYCLE {cycle}",
                "CARTESIAN COORDINATES (ANGSTROEM)",
                "---------------------------------",
                "    0         C    0.000000    0.000000    0.000000",
                f"    1         H    0.000000    0.000000    {z}",
                "---------------------------------",
                f"FINAL SINGLE POINT ENERGY     {energy}",
            ]
        )
    blocks.append("THE OPTIMIZATION HAS CONVERGED")
    return "\n".join(blocks) + "\n"


def _attempt_dir(run_root: Path) -> Path:
    return run_root / TASK_NAME / "WORK" / "03_OPT" / "batch" / "TS1" / "optimize"


def _write_trajectory(attempt_dir: Path, *, with_output: bool) -> Path:
    attempt_dir.mkdir(parents=True, exist_ok=True)
    cycles_dir = attempt_dir / "cycles"
    cycles_dir.mkdir()
    _ = (cycles_dir / "cycle_0001.xyz").write_text(
        "2\nexisting cycle\nC 0.0 0.0 0.0\nH 0.0 0.0 1.0\n",
        encoding="utf-8",
    )
    trajectory = {
        "schema_version": 1,
        "item_id": "TS1",
        "status": "completed",
        "converged": True,
        "cycles": [
            {"cycle": 1, "geometry_ref": "cycles/cycle_0001.xyz"},
            {"cycle": 2, "geometry_ref": "cycles/cycle_0002.xyz"},
            {"cycle": 3, "geometry_ref": "cycles/cycle_0003.xyz"},
        ],
    }
    trajectory_path = attempt_dir / "optimization_trajectory.json"
    _ = trajectory_path.write_text(json.dumps(trajectory, indent=2) + "\n", encoding="utf-8")
    if with_output:
        _ = (attempt_dir / "optimize.out").write_text(_orca_output(), encoding="utf-8")
    return trajectory_path


def test_repair_materializes_missing_cycle_xyz_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    attempt_dir = _attempt_dir(tmp_path)
    trajectory_path = _write_trajectory(attempt_dir, with_output=True)

    result = main([str(tmp_path)])

    assert result == 0
    payload = _JSON_LOADER.loads(trajectory_path.read_text(encoding="utf-8"))
    for cycle in payload["cycles"]:
        geometry_ref = cycle.get("geometry_ref")
        if geometry_ref:
            assert (trajectory_path.parent / geometry_ref).is_file()
    assert "REPAIRED (dangling 2->0)" in capsys.readouterr().out


def test_dry_run_does_not_change_trajectory(tmp_path: Path) -> None:
    attempt_dir = _attempt_dir(tmp_path)
    trajectory_path = _write_trajectory(attempt_dir, with_output=True)
    original = trajectory_path.read_text(encoding="utf-8")

    result = main([str(tmp_path), "--dry-run"])

    assert result == 0
    assert trajectory_path.read_text(encoding="utf-8") == original
    assert not (attempt_dir / "cycles" / "cycle_0002.xyz").exists()


def test_unrepairable_without_orca_output_is_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    attempt_dir = _attempt_dir(tmp_path)
    trajectory_path = _write_trajectory(attempt_dir, with_output=False)
    original = trajectory_path.read_text(encoding="utf-8")

    result = main([str(tmp_path)])

    assert result == 0
    assert trajectory_path.read_text(encoding="utf-8") == original
    assert "UNREPAIRABLE" in capsys.readouterr().out


def test_rescue_trajectory_is_not_processed_independently(tmp_path: Path) -> None:
    attempt_dir = _attempt_dir(tmp_path)
    _ = _write_trajectory(attempt_dir, with_output=True)
    rescue_dir = attempt_dir / "rescue_01_x"
    rescue_path = _write_trajectory(rescue_dir, with_output=True)
    rescue_original = rescue_path.read_text(encoding="utf-8")

    result = main([str(tmp_path)])

    assert result == 0
    assert rescue_path.read_text(encoding="utf-8") == rescue_original
    assert (attempt_dir / "rescue_01_x" / "cycles" / "cycle_0001.xyz").is_file()
