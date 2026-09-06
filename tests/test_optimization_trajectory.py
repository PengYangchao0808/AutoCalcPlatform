"""Tests for live optimization trajectory capture and projection."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from acp.backends.base import QCResult
from acp.calculations.contracts import CalculationRequest, StructureArtifact
from acp.calculations.primitives.optimization_trajectory import (
    OptimizationTrajectoryRecorder,
    finalize_optimization_trajectory,
    merge_trajectories,
    parse_output_text,
)
from acp.calculations.primitives.optimize import run_optimize
from acp.calculations.progress import LiveMetric, ProgressReporter
from acp.results.energy_graph import build_energy_graph_from_job

REAL_OPT_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "orca_opt_real_sections.txt"


def _strip_fixture_comments(text: str) -> str:
    """Real ORCA stdout never contains ``#`` lines; the fixture header is docs."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


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


def test_orca_optimization_reports_trajectory_metrics(monkeypatch, tmp_path: Path) -> None:
    input_path = tmp_path / "input.xyz"
    input_path.write_text("1\ninput\nC 0.0 0.0 0.0\n", encoding="utf-8")
    updates: list[list[LiveMetric]] = []

    class RecordingReporter(ProgressReporter):
        def set_live_metrics(self, metrics: list[LiveMetric]) -> None:
            updates.append(list(metrics))
            super().set_live_metrics(metrics)

    class SyntheticOrca:
        def optimize(self, coordinates, symbols, **kwargs) -> QCResult:
            output_callback = kwargs["output_callback"]
            for line in (
                "CYCLE 32",
                "RMS gradient 0.200000",
                "SCF CONVERGED AFTER 8 ITERATIONS",
                "FINAL SINGLE POINT ENERGY -10.000000",
                "GEOMETRY OPTIMIZATION CONVERGED",
            ):
                output_callback(line)
            return QCResult(
                success=True,
                energy=-10.0,
                coordinates=np.asarray(coordinates, dtype=float),
                symbols=list(symbols),
                converged=True,
            )

    monkeypatch.setattr("acp.backends.get_backend", lambda _name: SyntheticOrca())
    reporter = RecordingReporter(tmp_path, min_interval=60.0)
    request = CalculationRequest(
        input_artifact=StructureArtifact(path=input_path, elements=["C"]),
        method="r2SCAN-3c",
        resources={"backend": "orca", "output_dir": str(tmp_path / "opt")},
    )

    result = run_optimize(request, progress_reporter=reporter)

    assert result.status == "completed"
    assert [metrics[0].value for metrics in updates] == ["Step 32"] * len(updates)
    assert [metrics[1].value for metrics in updates] == ["running", "converged"]


def test_orca_failed_optimization_overrides_misleading_convergence(
    monkeypatch, tmp_path: Path
) -> None:
    input_path = tmp_path / "input.xyz"
    input_path.write_text("1\ninput\nC 0.0 0.0 0.0\n", encoding="utf-8")
    updates: list[list[LiveMetric]] = []

    class RecordingReporter(ProgressReporter):
        def set_live_metrics(self, metrics: list[LiveMetric]) -> None:
            updates.append(list(metrics))
            super().set_live_metrics(metrics)

    class SyntheticOrca:
        def optimize(self, _coordinates, _symbols, **kwargs) -> QCResult:
            output_callback = kwargs["output_callback"]
            output_callback("CYCLE 9")
            output_callback("FINAL SINGLE POINT ENERGY -2.000000")
            output_callback("GEOMETRY OPTIMIZATION CONVERGED")
            return QCResult(success=False, error_message="SCF failure")

    monkeypatch.setattr("acp.backends.get_backend", lambda _name: SyntheticOrca())
    reporter = RecordingReporter(tmp_path / "progress")
    request = CalculationRequest(
        input_artifact=StructureArtifact(path=input_path, elements=["C"]),
        method="r2SCAN-3c",
        resources={
            "backend": "orca",
            "output_dir": str(tmp_path / "opt"),
            "failure_type": "scf_failure",
        },
    )

    result = run_optimize(request, progress_reporter=reporter)

    assert result.status == "failed"
    assert updates[-1][1].key == "opt_convergence"
    assert updates[-1][1].value == "failed"


def test_non_orca_optimization_does_not_publish_trajectory_metrics(
    monkeypatch, tmp_path: Path
) -> None:
    input_path = tmp_path / "input.xyz"
    input_path.write_text("1\ninput\nC 0.0 0.0 0.0\n", encoding="utf-8")

    class SyntheticXtb:
        def optimize(self, coordinates, symbols, **_kwargs) -> QCResult:
            return QCResult(
                success=True,
                coordinates=np.asarray(coordinates, dtype=float),
                symbols=list(symbols),
            )

    monkeypatch.setattr("acp.backends.get_backend", lambda _name: SyntheticXtb())
    reporter = ProgressReporter(tmp_path / "progress")
    request = CalculationRequest(
        input_artifact=StructureArtifact(path=input_path, elements=["C"]),
        method="GFN2-xTB",
        resources={"backend": "xtb", "output_dir": str(tmp_path / "opt")},
    )

    result = run_optimize(request, progress_reporter=reporter)

    assert result.status == "completed"
    assert not (tmp_path / "progress" / "state.json").exists()


def test_malformed_orca_stdout_does_not_publish_metrics(monkeypatch, tmp_path: Path) -> None:
    input_path = tmp_path / "input.xyz"
    input_path.write_text("1\ninput\nC 0.0 0.0 0.0\n", encoding="utf-8")

    class SyntheticOrca:
        def optimize(self, coordinates, symbols, **kwargs) -> QCResult:
            kwargs["output_callback"]("garbage stdout")
            return QCResult(
                success=True,
                coordinates=np.asarray(coordinates, dtype=float),
                symbols=list(symbols),
            )

    monkeypatch.setattr("acp.backends.get_backend", lambda _name: SyntheticOrca())
    reporter = ProgressReporter(tmp_path / "progress")
    request = CalculationRequest(
        input_artifact=StructureArtifact(path=input_path, elements=["C"]),
        method="r2SCAN-3c",
        resources={"backend": "orca", "output_dir": str(tmp_path / "opt")},
    )

    result = run_optimize(request, progress_reporter=reporter)

    assert result.status == "completed"
    assert not (tmp_path / "progress" / "state.json").exists()


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


def test_recorder_parses_orca6_starred_banners_and_step_rows(tmp_path: Path) -> None:
    """ORCA 6 format: starred banners, RMS/MAX step rows, HAS CONVERGED marker."""
    recorder = OptimizationTrajectoryRecorder(tmp_path, item_id="X1")
    lines = [
        "Convergence Tolerances:",
        "Max. Gradient            TolMAXG  ....  3.0000e-04 Eh/bohr",
        "RMS Gradient             TolRMSG  ....  1.0000e-04 Eh/bohr",
        "Max. Displacement        TolMAXD  ....  4.0000e-03 bohr",
        "RMS Displacement         TolRMSD  ....  2.0000e-03 bohr",
        "         *************************************************************",
        "         *                GEOMETRY OPTIMIZATION CYCLE   1            *",
        "         *************************************************************",
        "FINAL SINGLE POINT ENERGY      -10.000000",
        "          RMS gradient        0.0032199552            0.0001000000      NO",
        "          MAX gradient        0.0152413631            0.0003000000      NO",
        "          RMS step            0.0083536803            0.0020000000      NO",
        "          MAX step            0.0279840557            0.0040000000      NO",
        "Making the step                 :      0.001 s ( 8.411 %)",
        "         *                GEOMETRY OPTIMIZATION CYCLE   2            *",
        "FINAL SINGLE POINT ENERGY      -10.100000",
        "          RMS gradient        0.0000500000            0.0001000000      YES",
        "          MAX gradient        0.0001200000            0.0003000000      YES",
        "          RMS step            0.0005000000            0.0020000000      YES",
        "          MAX step            0.0010000000            0.0040000000      YES",
        "                    ***        THE OPTIMIZATION HAS CONVERGED     ***",
    ]
    for line in lines:
        recorder.feed_line(line)
    recorder.finish(converged=True)

    payload = json.loads((tmp_path / "optimization_trajectory.json").read_text(encoding="utf-8"))
    # No phantom cycle from the tolerance header or the "Making the step" timing line.
    assert [cycle["cycle"] for cycle in payload["cycles"]] == [1, 2]
    first, second = payload["cycles"]
    assert first["energy_hartree"] == pytest.approx(-10.0)
    assert first["rms_gradient"] == pytest.approx(0.0032199552)
    assert first["rms_displacement"] == pytest.approx(0.0083536803)
    assert first["max_displacement"] == pytest.approx(0.0279840557)
    assert second["rms_gradient"] == pytest.approx(0.00005)
    assert payload["converged"] is True
    assert payload["thresholds"] == {
        "max_gradient": pytest.approx(3.0e-4),
        "rms_gradient": pytest.approx(1.0e-4),
        "max_displacement": pytest.approx(4.0e-3),
        "rms_displacement": pytest.approx(2.0e-3),
    }


def test_parse_output_text_matches_real_orca_opt_output() -> None:
    """The real-output fixture must yield one clean entry per ORCA cycle."""
    text = _strip_fixture_comments(REAL_OPT_FIXTURE.read_text(encoding="utf-8"))
    payload = parse_output_text(text, item_id="conf_000")

    assert payload["converged"] is True
    assert payload["status"] == "completed"
    assert len(payload["cycles"]) == 6
    assert [cycle["cycle"] for cycle in payload["cycles"]] == [1, 2, 3, 4, 5, 6]
    assert payload["cycles"][0]["energy_hartree"] == pytest.approx(-671.089015445161)
    assert payload["cycles"][-1]["energy_hartree"] == pytest.approx(-671.091508901641)
    for cycle in payload["cycles"]:
        assert cycle["rms_gradient"] is not None
        assert cycle["max_gradient"] is not None
        assert cycle["rms_displacement"] is not None
        assert cycle["max_displacement"] is not None
    assert payload["thresholds"] == {
        "max_gradient": pytest.approx(3.0e-4),
        "rms_gradient": pytest.approx(1.0e-4),
        "max_displacement": pytest.approx(4.0e-3),
        "rms_displacement": pytest.approx(2.0e-3),
    }


def test_merge_trajectories_renumbers_and_prefixes_rescue_cycles() -> None:
    base = {
        "status": "failed",
        "converged": False,
        "item_id": "TS1",
        "cycles": [
            {"cycle": 1, "energy_hartree": -10.0, "geometry_ref": "cycles/cycle_0001.xyz"},
            {"cycle": 2, "energy_hartree": -10.1, "geometry_ref": "cycles/cycle_0002.xyz"},
        ],
        "thresholds": {"rms_gradient": 1e-4},
    }
    rescue = {
        "status": "completed",
        "converged": True,
        "item_id": "TS1",
        "cycles": [
            {"cycle": 1, "energy_hartree": -10.2, "geometry_ref": "cycles/cycle_0001.xyz"},
        ],
    }

    merged = merge_trajectories([(base, ""), (rescue, "rescue_01_calcall_opt/")], item_id="TS1")

    assert [cycle["cycle"] for cycle in merged["cycles"]] == [1, 2, 3]
    assert merged["cycles"][0]["geometry_ref"] == "cycles/cycle_0001.xyz"
    assert merged["cycles"][2]["geometry_ref"] == "rescue_01_calcall_opt/cycles/cycle_0001.xyz"
    assert merged["cycles"][2]["attempt_cycle"] == 1
    assert merged["converged"] is True
    assert merged["status"] == "completed"
    assert merged["attempts"] == 2
    assert merged["thresholds"]["rms_gradient"] == pytest.approx(1e-4)


def test_finalize_reparses_final_output_over_truncated_snapshot(tmp_path: Path) -> None:
    """A one-cycle incremental snapshot is rebuilt from the full final output."""
    (tmp_path / "optimization_trajectory.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "item_id": "X1",
                "status": "running",
                "converged": False,
                "cycles": [{"cycle": 1, "energy_hartree": -10.0}],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "orca_opt.out").write_text(
        "\n".join(
            [
                "         *                GEOMETRY OPTIMIZATION CYCLE   1            *",
                "FINAL SINGLE POINT ENERGY      -10.000000",
                "          RMS step            0.0080000000            0.0020000000      NO",
                "         *                GEOMETRY OPTIMIZATION CYCLE   2            *",
                "FINAL SINGLE POINT ENERGY      -10.050000",
                "          RMS step            0.0010000000            0.0020000000      YES",
                "                    ***        THE OPTIMIZATION HAS CONVERGED     ***",
            ]
        ),
        encoding="utf-8",
    )

    merged = finalize_optimization_trajectory(tmp_path, item_id="X1")

    assert merged is not None
    assert [cycle["cycle"] for cycle in merged["cycles"]] == [1, 2]
    assert merged["converged"] is True
    assert merged["cycles"][1]["rms_displacement"] == pytest.approx(0.001)
    on_disk = json.loads((tmp_path / "optimization_trajectory.json").read_text(encoding="utf-8"))
    assert len(on_disk["cycles"]) == 2


def test_finalize_merges_rescue_attempt_outputs(tmp_path: Path) -> None:
    (tmp_path / "orca_opt.out").write_text(
        "\n".join(
            [
                "         *                GEOMETRY OPTIMIZATION CYCLE   1            *",
                "FINAL SINGLE POINT ENERGY      -10.000000",
                "         *                GEOMETRY OPTIMIZATION CYCLE   2            *",
                "FINAL SINGLE POINT ENERGY      -10.050000",
            ]
        ),
        encoding="utf-8",
    )
    rescue_dir = tmp_path / "rescue_01_calcall_opt"
    rescue_dir.mkdir()
    (rescue_dir / "orca_opt.out").write_text(
        "\n".join(
            [
                "         *                GEOMETRY OPTIMIZATION CYCLE   1            *",
                "FINAL SINGLE POINT ENERGY      -10.080000",
                "                    ***        THE OPTIMIZATION HAS CONVERGED     ***",
            ]
        ),
        encoding="utf-8",
    )

    merged = finalize_optimization_trajectory(tmp_path, item_id="X1")

    assert merged is not None
    assert [cycle["cycle"] for cycle in merged["cycles"]] == [1, 2, 3]
    assert merged["cycles"][2]["energy_hartree"] == pytest.approx(-10.08)
    assert merged["converged"] is True
    assert merged["attempts"] == 2
