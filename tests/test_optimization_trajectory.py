"""Tests for live optimization trajectory capture and projection."""

from __future__ import annotations

import json
from pathlib import Path

from acp.calculations.primitives.optimization_trajectory import OptimizationTrajectoryRecorder
from acp.results.energy_graph import build_energy_graph_from_job


def test_recorder_publishes_partial_cycles_and_xyz(tmp_path: Path) -> None:
    recorder = OptimizationTrajectoryRecorder(tmp_path, item_id="TS1")
    lines = [
        "GEOMETRY OPTIMIZATION CYCLE 1\n",
        "CARTESIAN COORDINATES (ANGSTROEM)\n",
        "---------------------------------\n",
        "    0         C    0.000000    0.000000    0.000000\n",
        "    1         H    0.000000    0.000000    1.100000\n",
        "---------------------------------\n",
        "FINAL SINGLE POINT ENERGY     -10.000000\n",
        "RMS gradient      0.200000\n",
        "MAX gradient      0.400000\n",
        "GEOMETRY OPTIMIZATION CYCLE 2\n",
        "FINAL SINGLE POINT ENERGY     -10.100000\n",
        "RMS gradient      0.010000\n",
    ]
    for line in lines:
        recorder.feed_line(line)

    partial = json.loads((tmp_path / "optimization_trajectory.json").read_text(encoding="utf-8"))
    assert partial["status"] == "running"
    assert [cycle["cycle"] for cycle in partial["cycles"]] == [1, 2]
    assert partial["cycles"][0]["geometry_ref"] == "cycles/cycle_0001.xyz"

    recorder.finish(converged=True)
    complete = json.loads((tmp_path / "optimization_trajectory.json").read_text(encoding="utf-8"))
    assert complete["status"] == "completed"
    assert complete["converged"] is True
    assert (tmp_path / "cycles" / "cycle_0001.xyz").is_file()


def test_batch_optimize_projection_reads_live_trajectory(tmp_path: Path) -> None:
    trajectory_dir = tmp_path / "WORK" / "03_OPT" / "batch" / "TS1" / "optimize"
    trajectory_dir.mkdir(parents=True)
    (trajectory_dir / "optimization_trajectory.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "item_id": "TS1",
                "status": "running",
                "converged": False,
                "current_cycle": 2,
                "cycles": [
                    {"cycle": 1, "energy_hartree": -10.0, "rms_gradient": 0.2},
                    {"cycle": 2, "energy_hartree": -10.1, "rms_gradient": 0.01},
                ],
            }
        ),
        encoding="utf-8",
    )

    graph = build_energy_graph_from_job(
        "batch-job", workflow="BatchOptimize", method=None, work_dir=tmp_path, item_id="TS1"
    )

    assert graph["view_type"] == "optimization"
    assert graph["status"] == "running"
    assert graph["complete"] is False
    assert [node["x"] for node in graph["nodes"]] == [1, 2]
    assert graph["nodes"][0]["energy"] == 0.0
    assert graph["nodes"][1]["energy"] < 0.0
    assert {item["id"] for item in graph["series"]} >= {
        "relative_energy",
        "delta_energy",
        "scf_energy",
        "rms_gradient",
    }


def test_batch_optimize_projection_reads_flat_live_trajectory(tmp_path: Path) -> None:
    trajectory_dir = tmp_path / "WORK" / "03_OPT"
    trajectory_dir.mkdir(parents=True)
    (trajectory_dir / "optimization_trajectory.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "item_id": "item_001",
                "status": "running",
                "converged": False,
                "current_cycle": 1,
                "cycles": [{"cycle": 1, "energy_hartree": -3.0}],
            }
        ),
        encoding="utf-8",
    )

    graph = build_energy_graph_from_job(
        "flat-batch-job",
        workflow="BatchOptimize",
        method=None,
        work_dir=tmp_path,
        item_id="item_001",
    )

    assert graph["source"] == "WORK/03_OPT/optimization_trajectory.json"
    assert graph["metadata"]["item_id"] == "item_001"
    assert graph["status"] == "running"
