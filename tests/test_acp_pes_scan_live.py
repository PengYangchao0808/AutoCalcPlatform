"""Tests for the live PES scan chain: snapshot writer, providers, projections,
API 404-tolerance, and per-point callbacks.

Covers the acceptance matrix from the live-view design:
waiting state → per-point growth → failed points → SP backfill →
native-ORCA ledger back-fill for already-running jobs → final-profile switch.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient

from acp.backends.base import QCResult
from acp.calculations.pes.scan_snapshot import PesScanSnapshotWriter
from acp.results import pes_scan_live
from acp.results.energy_graph import build_energy_graph_from_job
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from cccp.qc.interfaces.constraints import CoordinateSpec, ReactionCoordinatePlan
from cccp.qc.interfaces.orca import ORCAInterface
from cccp.qc.interfaces.xtb import XTBInterface
from cccp.qc.interfaces.xtb_scan import RelaxedScanPoint
from tests.conftest import FakeBackend
from tests.test_acp_api_v1 import make_client

_WATER_SYMBOLS = ["O", "H", "H"]
_WATER_COORDS = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.96], [0.0, 0.0, -0.96]])


def _water_xyz(atom_count: int = 3, energy: str = "") -> str:
    lines = [str(atom_count), energy or "water frame"]
    for index in range(min(atom_count, 3)):
        symbol = _WATER_SYMBOLS[index] if index < 3 else "H"
        x, y, z = _WATER_COORDS[index] if index < 3 else (0.0, 0.1 * index, 0.0)
        lines.append(f"{symbol} {x:.4f} {y:.4f} {z:.4f}")
    for index in range(3, atom_count):
        lines.append(f"H 0.0 0.1 {0.1 * (index - 2):.4f}")
    return "\n".join(lines) + "\n"


def _make_writer(tmp_path: Path, *, points_total: int = 3) -> PesScanSnapshotWriter:
    task_root = tmp_path / "pes_task"
    scan_dir = task_root / "WORK" / "07_PATH" / "pes_scan_001"
    scan_dir.mkdir(parents=True)
    coordinate = {
        "kind": "distance",
        "atoms": [1, 2],
        "unit": "angstrom",
        "start": 1.0,
        "end": 2.0,
        "n_points": points_total,
    }
    return PesScanSnapshotWriter(
        task_root / "RESULT",
        scan_dir=scan_dir,
        coordinate=coordinate,
        coordinates=[coordinate],
        points_total=points_total,
        driver="xtb",
    )


def _point(
    index: int,
    *,
    energy: float | None,
    success: bool = True,
    coordinates: np.ndarray | None = _WATER_COORDS,
) -> RelaxedScanPoint:
    return RelaxedScanPoint(
        frame_index=index,
        progress=index / 2.0,
        coordinates=coordinates,
        symbols=list(_WATER_SYMBOLS) if coordinates is not None else None,
        energy_hartree=energy,
        success=success,
        coordinate_values={"distance": 1.0 + 0.5 * index},
    )


def _no_nan_json(text: str) -> dict[str, Any]:
    def _reject(token: str) -> Any:
        raise ValueError(f"non-strict JSON token: {token}")

    return json.loads(text, parse_constant=_reject)


# ── snapshot writer ────────────────────────────────────────────────────


def test_writer_publishes_points_atomically(tmp_path: Path) -> None:
    writer = _make_writer(tmp_path)
    writer.publish_point(_point(0, energy=-10.0))
    writer.publish_point(_point(1, energy=-11.0))
    writer.publish_point(_point(2, energy=None, success=False))

    payload = _no_nan_json(writer.snapshot_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "pes_scan_trajectory_v1"
    assert payload["scan_stage"] == "running"
    assert [frame["index"] for frame in payload["frames"]] == [0, 1, 2]
    assert payload["frames"][1]["energy_hartree"] == -11.0
    assert payload["frames"][2]["status"] == "failed"

    geometry = tmp_path / "pes_task" / "WORK" / "07_PATH" / "pes_scan_001" / "scan_frames"
    assert (geometry / "frame_000.xyz").is_file()
    assert (geometry / "frame_002.xyz").is_file()
    assert (
        payload["frames"][0]["geometry_ref"]
        == "WORK/07_PATH/pes_scan_001/scan_frames/frame_000.xyz"
    )
    # A failed frame with geometry keeps a viewable ref; empty only without geometry.
    assert payload["frames"][2]["geometry_ref"] == (
        "WORK/07_PATH/pes_scan_001/scan_frames/frame_002.xyz"
    )


def test_writer_sp_update_and_finalize_first_terminal_wins(tmp_path: Path) -> None:
    writer = _make_writer(tmp_path)
    writer.publish_point(_point(0, energy=-10.0))
    writer.publish_sp(0, -10.2, "completed")
    writer.finalize("completed")
    writer.finalize("failed")

    payload = _no_nan_json(writer.snapshot_path.read_text(encoding="utf-8"))
    assert payload["frames"][0]["sp_energy_hartree"] == -10.2
    assert payload["frames"][0]["sp_status"] == "completed"
    assert payload["scan_stage"] == "completed"


def test_writer_sanitizes_non_finite_values(tmp_path: Path) -> None:
    writer = _make_writer(tmp_path)
    # NaN on the measured atoms (1, 2) so the distance itself is non-finite.
    nan_coords = np.array([[0.0, 0.0, 0.0], [np.nan, 0.0, 0.96], [0.0, 0.0, -0.96]])
    writer.publish_point(_point(0, energy=-10.0, coordinates=nan_coords))
    writer.publish_point(_point(1, energy=float("inf")))

    payload = _no_nan_json(writer.snapshot_path.read_text(encoding="utf-8"))
    assert payload["frames"][0]["actual_coordinate"] is None
    assert payload["frames"][1]["energy_hartree"] is None


def test_writer_callbacks_never_raise(tmp_path: Path) -> None:
    writer = _make_writer(tmp_path)
    writer.publish_point(_point(0, energy=-10.0))
    writer.publish_sp(99, None, "failed")
    writer.finalize("not-a-stage")
    assert writer.snapshot_path.is_file()


# ── providers ──────────────────────────────────────────────────────────


def test_provider_snapshot_wins(tmp_path: Path) -> None:
    writer = _make_writer(tmp_path)
    writer.publish_point(_point(0, energy=-10.0))
    pes_scan_live._cache_clear()
    live = pes_scan_live.collect_pes_scan_live_frames(tmp_path / "pes_task")
    assert live is not None
    assert live.driver == "xtb"
    assert live.source.endswith("pes_scan_trajectory.json")
    assert [frame["index"] for frame in live.frames] == [0]


def test_provider_native_orca_ledger(tmp_path: Path) -> None:
    scan_dir = tmp_path / "WORK" / "07_PATH" / "pes_scan_001"
    scan_dir.mkdir(parents=True)
    (scan_dir / "input.xyz").write_text(_water_xyz(), encoding="utf-8")
    (scan_dir / "orca_relaxed_scan.inp").write_text(
        "%geom\n  Scan\n  B 2 3 = 1.50000000, 4.00000000, 21\n  end\nend\n",
        encoding="utf-8",
    )
    (scan_dir / "orca_relaxed_scan.001.xyz").write_text(_water_xyz(), encoding="utf-8")
    (scan_dir / "orca_relaxed_scan.002.xyz").write_text(_water_xyz(atom_count=4), encoding="utf-8")
    (scan_dir / "orca_relaxed_scan.003.xyz").write_text(_water_xyz(), encoding="utf-8")
    (scan_dir / "orca_relaxed_scan.relaxscanact.dat").write_text(
        "1.50000000 -43.57791466 \n"
        "1.87500000 -43.57629114 \n"
        "2.25000000 -43.56496440 \n"
        "2.6\n",  # torn in-flight tail row must be skipped
        encoding="utf-8",
    )

    pes_scan_live._cache_clear()
    live = pes_scan_live.collect_pes_scan_live_frames(tmp_path)
    assert live is not None
    assert live.driver == "orca"
    assert [frame["index"] for frame in live.frames] == [0, 1, 2]
    assert [frame["target_coordinate"] for frame in live.frames] == [
        pytest.approx(1.5),
        pytest.approx(1.625),
        pytest.approx(1.75),
    ]
    assert live.frames[0]["energy_hartree"] == pytest.approx(-43.57791466)
    assert live.frames[0]["geometry_ref"].endswith("orca_relaxed_scan.001.xyz")
    # atom-count mismatch → geometry ref dropped, energy point kept
    assert live.frames[1]["geometry_ref"] == ""
    assert live.points_total == 21
    assert live.x_source == "target"


def test_provider_frame_dirs_xtb_layout(tmp_path: Path) -> None:
    scan_dir = tmp_path / "WORK" / "07_PATH" / "pes_scan_001"
    complete = scan_dir / "frame_000"
    complete.mkdir(parents=True)
    (scan_dir / "input.xyz").write_text(_water_xyz(), encoding="utf-8")
    (complete / "xtbopt.xyz").write_text(_water_xyz(), encoding="utf-8")
    (complete / ".xcontrol").write_text(
        "$constrain\n  distance: 2, 3, 1.500000\n$end\n", encoding="utf-8"
    )
    (complete / "xtb.log").write_text(
        "....\n         TOTAL ENERGY      -43.57791466 Eh\n....\n", encoding="utf-8"
    )
    incomplete = scan_dir / "frame_001"
    incomplete.mkdir()
    (incomplete / "xtb_input.xyz").write_text(_water_xyz(), encoding="utf-8")

    pes_scan_live._cache_clear()
    live = pes_scan_live.collect_pes_scan_live_frames(tmp_path)
    assert live is not None
    assert [frame["index"] for frame in live.frames] == [0]
    assert live.frames[0]["target_coordinate"] == pytest.approx(1.5)
    assert live.frames[0]["energy_hartree"] == pytest.approx(-43.57791466)
    assert live.frames[0]["geometry_ref"] == ("WORK/07_PATH/pes_scan_001/frame_000/xtbopt.xyz")


def test_provider_none_when_directory_empty(tmp_path: Path) -> None:
    pes_scan_live._cache_clear()
    assert pes_scan_live.collect_pes_scan_live_frames(tmp_path) is None


# ── projections ────────────────────────────────────────────────────────


def test_live_graph_projection_order_failed_points_and_minimum(tmp_path: Path) -> None:
    writer = _make_writer(tmp_path)
    writer.publish_point(_point(0, energy=-10.0))
    writer.publish_point(_point(1, energy=-11.0))
    writer.publish_point(_point(2, energy=-9.5, success=False))
    pes_scan_live._cache_clear()

    graph = pes_scan_live.build_pes_scan_live_graph("job", tmp_path / "pes_task")
    assert graph is not None
    assert graph["view_type"] == "scan"
    assert graph["complete"] is False
    assert [node["frame_index"] for node in graph["nodes"]] == [0, 1, 2]
    assert graph["nodes"][2]["status"] == "failed"
    series_by_id = {item["id"]: item for item in graph["series"]}
    assert "single_point_energy" not in series_by_id
    relative = series_by_id["relative_energy"]["values"]
    assert relative[0] == pytest.approx(0.0)
    assert relative[1] == pytest.approx(-627.5094740631, rel=1e-6)
    annotation_types = {item["type"] for item in graph["annotations"]}
    assert "failed" in annotation_types
    assert "minimum" in annotation_types
    assert graph["metadata"]["live"] is True


def test_live_graph_sp_series_keeps_nulls(tmp_path: Path) -> None:
    writer = _make_writer(tmp_path)
    writer.publish_point(_point(0, energy=-10.0))
    writer.publish_point(_point(1, energy=-11.0))
    writer.publish_point(_point(2, energy=-9.5))
    writer.publish_sp(1, -11.2, "completed")
    pes_scan_live._cache_clear()

    graph = pes_scan_live.build_pes_scan_live_graph("job", tmp_path / "pes_task")
    assert graph is not None
    series_by_id = {item["id"]: item for item in graph["series"]}
    sp_values = series_by_id["single_point_energy"]["values"]
    assert sp_values == [None, pytest.approx(-11.2), None]


def test_pending_projection_running_vs_terminal_job() -> None:
    pending = pes_scan_live.build_pes_scan_pending_energy_graph("job", job_status="running")
    assert pending["view_type"] == "scan"
    assert pending["nodes"] == [] and pending["series"] == []
    assert pending["metadata"]["reason"] == "pes_scan_pending"

    ended = pes_scan_live.build_pes_scan_pending_energy_graph("job", job_status="completed")
    assert ended["view_type"] == "unsupported"
    assert ended["metadata"]["reason"] == "pes_scan_no_data"


# ── dispatch ───────────────────────────────────────────────────────────


def test_dispatch_running_pes_job_uses_live_then_pending(tmp_path: Path) -> None:
    pes_scan_live._cache_clear()
    graph = build_energy_graph_from_job(
        "job",
        workflow="PESsearch",
        method={"mode": "bond_length_scan"},
        work_dir=tmp_path,
        job_status="running",
    )
    assert graph["view_type"] == "scan"
    assert graph["metadata"]["reason"] == "pes_scan_pending"
    assert graph["metadata"]["job_status"] == "running"

    writer = _make_writer(tmp_path)
    writer.publish_point(_point(0, energy=-10.0))
    pes_scan_live._cache_clear()
    graph = build_energy_graph_from_job(
        "job",
        workflow="PESsearch",
        method={"mode": "bond_length_scan"},
        work_dir=tmp_path / "pes_task",
        job_status="running",
    )
    assert graph["metadata"]["live"] is True
    assert len(graph["nodes"]) == 1


def test_dispatch_final_profile_still_wins(tmp_path: Path) -> None:
    payload = {
        "schema_version": "pes_profile_v2",
        "status": "completed",
        "scan": {
            "frames": [
                {
                    "index": 0,
                    "target_coordinate": 1.0,
                    "actual_coordinate": 1.0,
                    "scan_energy_hartree": -10.0,
                    "optimization_converged": True,
                    "single_point_status": "skipped",
                }
            ],
            "quality": {"scan_complete": True},
        },
        "energy_profile": {
            "energy_source": "scan",
            "relative_energies_kcal_mol": [0.0],
            "raw_hartree": [-10.0],
        },
    }
    graph = build_energy_graph_from_job(
        "job",
        workflow="PESsearch",
        method={"mode": "bond_length_scan"},
        work_dir=tmp_path,
        s2_payload=payload,
    )
    assert graph["view_type"] == "scan"
    assert graph["complete"] is True
    assert graph["source"].endswith("pes_profile.json") or "s2" in graph["source"]


# ── API route ──────────────────────────────────────────────────────────


def _register_pes_job(
    client: TestClient,
    work_dir: Path,
    *,
    status: JobStatus = JobStatus.RUNNING,
) -> str:
    manager = client.app.state.job_manager
    spec = JobSpec(
        workflow="PESsearch",
        name="pes_live",
        input={"scan_request": {}},
        method={"mode": "bond_length_scan"},
    )
    record = JobRecord(
        id="20260914_001_PESsearch", spec=spec, status=status, work_dir=str(work_dir)
    )
    manager.store.create(record)
    return record.id


def test_energy_graph_running_pes_job_returns_pending_200(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work_dir = tmp_path / "pes_task"
    work_dir.mkdir()
    pes_scan_live._cache_clear()
    with make_client(tmp_path, monkeypatch) as client:
        job_id = _register_pes_job(client, work_dir)
        response = client.get(f"/api/v1/jobs/{job_id}/energy-graph")
        assert response.status_code == 200
        body = response.json()
        assert body["view_type"] == "scan"
        assert body["nodes"] == []
        assert body["metadata"]["reason"] == "pes_scan_pending"
        assert body["metadata"]["job_status"] == "running"


def test_energy_graph_running_native_orca_job_returns_live_200(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work_dir = tmp_path / "pes_task"
    scan_dir = work_dir / "WORK" / "07_PATH" / "pes_scan_001"
    scan_dir.mkdir(parents=True)
    (work_dir / "RESULT").mkdir()
    (scan_dir / "input.xyz").write_text(_water_xyz(), encoding="utf-8")
    (scan_dir / "orca_relaxed_scan.inp").write_text(
        "%geom\n  Scan\n  B 1 2 = 1.5 4.0 5\n  end\nend\n", encoding="utf-8"
    )
    (scan_dir / "orca_relaxed_scan.001.xyz").write_text(_water_xyz(), encoding="utf-8")
    (scan_dir / "orca_relaxed_scan.relaxscanact.dat").write_text(
        "1.50000000 -43.57791466 \n", encoding="utf-8"
    )
    pes_scan_live._cache_clear()
    with make_client(tmp_path, monkeypatch) as client:
        job_id = _register_pes_job(client, work_dir)
        response = client.get(f"/api/v1/jobs/{job_id}/energy-graph")
        assert response.status_code == 200
        body = response.json()
        assert body["view_type"] == "scan"
        assert len(body["nodes"]) == 1
        assert body["nodes"][0]["geometry_ref"].endswith("orca_relaxed_scan.001.xyz")


def test_energy_graph_unknown_job_still_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with make_client(tmp_path, monkeypatch) as client:
        response = client.get("/api/v1/jobs/missing_job/energy-graph")
        assert response.status_code == 404


# ── interface callbacks ────────────────────────────────────────────────


def test_xtb_relaxed_scan_invokes_point_callback(tmp_path: Path) -> None:
    interface = XTBInterface({})
    results = [
        QCResult(
            success=True,
            coordinates=_WATER_COORDS,
            symbols=list(_WATER_SYMBOLS),
            energy=-10.0,
            output_file=tmp_path / "a.xyz",
        ),
        QCResult(
            success=True,
            coordinates=_WATER_COORDS,
            symbols=list(_WATER_SYMBOLS),
            energy=-10.5,
            output_file=tmp_path / "b.xyz",
        ),
        QCResult(success=False, error_message="frame 2 diverged"),
    ]
    calls: list[RelaxedScanPoint] = []

    def _fake_constrained_optimize(*args: Any, **kwargs: Any) -> QCResult:
        return results.pop(0)

    interface.constrained_optimize = _fake_constrained_optimize  # type: ignore[method-assign]
    plan = ReactionCoordinatePlan(
        coordinates=(
            CoordinateSpec(
                id="distance", kind="distance", atoms=(1, 2), role="drive", start=1.0, end=2.0
            ),
        ),
        points=3,
    )
    result = interface.relaxed_scan(
        _WATER_COORDS,
        list(_WATER_SYMBOLS),
        output_dir=tmp_path,
        plan=plan,
        fail_fast=True,
        point_callback=calls.append,
    )

    assert [point.frame_index for point in calls] == [0, 1, 2]
    assert calls[2].success is False
    assert result.success is False
    assert "frame 2 failed" in result.message


def test_xtb_callback_exception_does_not_abort_scan(tmp_path: Path) -> None:
    interface = XTBInterface({})
    ok = QCResult(
        success=True,
        coordinates=_WATER_COORDS,
        symbols=list(_WATER_SYMBOLS),
        energy=-10.0,
        output_file=tmp_path / "a.xyz",
    )
    interface.constrained_optimize = lambda *a, **k: ok  # type: ignore[method-assign]

    def _boom(point: RelaxedScanPoint) -> None:
        raise RuntimeError("publisher exploded")

    plan = ReactionCoordinatePlan(
        coordinates=(
            CoordinateSpec(
                id="distance", kind="distance", atoms=(1, 2), role="drive", start=1.0, end=2.0
            ),
        ),
        points=2,
    )
    result = interface.relaxed_scan(
        _WATER_COORDS,
        list(_WATER_SYMBOLS),
        output_dir=tmp_path,
        plan=plan,
        point_callback=_boom,
    )
    assert result.success is True
    assert len(result.points) == 2


def test_orca_synchronous_scan_invokes_point_callback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cccp.config import load_config

    interface = ORCAInterface(load_config())
    outcomes = [True, True, False]
    calls: list[RelaxedScanPoint] = []

    def _fake_constrained_optimize(*args: Any, **kwargs: Any) -> QCResult:
        success = outcomes.pop(0)
        if not success:
            return QCResult(success=False, error_message="diverged")
        return QCResult(
            success=True,
            coordinates=_WATER_COORDS,
            symbols=list(_WATER_SYMBOLS),
            energy=-10.0,
            output_file=tmp_path / "opt.xyz",
        )

    monkeypatch.setattr(interface, "constrained_optimize", _fake_constrained_optimize)
    plan = ReactionCoordinatePlan(
        coordinates=(
            CoordinateSpec(
                id="rc1", kind="distance", atoms=(0, 1), role="drive", start=1.5, end=2.1
            ),
            CoordinateSpec(
                id="rc2", kind="distance", atoms=(2, 3), role="drive", start=1.5, end=2.1
            ),
        ),
        points=3,
    )
    result = interface.relaxed_scan(
        _WATER_COORDS,
        list(_WATER_SYMBOLS),
        plan=plan,
        output_dir=tmp_path,
        point_callback=calls.append,
    )
    assert [point.frame_index for point in calls] == [0, 1, 2]
    assert calls[2].success is False
    assert result.success is False


def test_native_orca_scan_drops_point_callback_kwarg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cccp.config import load_config

    interface = ORCAInterface(load_config())
    monkeypatch.setattr(
        interface,
        "_run_orca",
        lambda inp, out: out.write_text("", encoding="utf-8") or True,
    )
    monkeypatch.setattr(
        interface,
        "_collect_relaxed_scan_points",
        lambda *a, **k: [],
    )
    plan = ReactionCoordinatePlan(
        coordinates=(
            CoordinateSpec(
                id="distance", kind="distance", atoms=(1, 2), role="drive", start=1.0, end=2.0
            ),
        ),
        points=3,
    )
    result = interface.relaxed_scan(
        _WATER_COORDS,
        list(_WATER_SYMBOLS),
        scan_coordinate=plan.coordinates[0],
        points=3,
        output_dir=tmp_path,
        point_callback=lambda point: None,
    )
    assert result.success is False  # zero frames collected, but no TypeError raised


# ── SP executor on_frame_done ──────────────────────────────────────────


def test_executor_emits_on_frame_done(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    frame_dir = tmp_path / "frames"
    frame_dir.mkdir()
    frame_paths = []
    for index in range(2):
        path = frame_dir / f"frame_{index:03d}.xyz"
        # Distinct geometries: the shared batch helper dedupes identical inputs,
        # so twin frames would share one backend call (and one callback).
        path.write_text(_water_xyz().replace("0.9600", f"0.9{index}00"), encoding="utf-8")
        frame_paths.append(path)

    fake_backend = FakeBackend()
    original = fake_backend.single_point

    def _file_backed(coordinates: Any, symbols: Any, **kwargs: Any) -> QCResult:
        result = original(coordinates, symbols, **kwargs)
        output_dir = Path(str(kwargs["output_dir"]))
        output_name = str(kwargs["output_name"])
        output_file = output_dir / f"{output_name}.out"
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text("sp\n", encoding="utf-8")
        result.output_file = output_file
        return result

    fake_backend.single_point = _file_backed  # type: ignore[method-assign]
    fake_backend.set_result("single_point", QCResult(success=True, energy=-1.0))

    events: list[tuple[str, float | None, str]] = []
    from acp.calculations.batch.singlepoint import BatchSinglePointExecutor

    BatchSinglePointExecutor(
        frames=frame_paths,
        method="B97-3c",
        output_dir=tmp_path / "sp",
        backend_factory=lambda name: fake_backend,
        cache=False,
        frame_ids=["frame_000", "frame_001"],
        on_frame_done=lambda fid, energy, status: events.append((fid, energy, status)),
    ).run()

    assert sorted(events) == [("frame_000", -1.0, "completed"), ("frame_001", -1.0, "completed")]


# ── frontend contract ──────────────────────────────────────────────────


def test_frontend_pes_pending_contract() -> None:
    html = (Path(__file__).parent.parent / "frontend" / "ACP_Workbench_v2.html").read_text(
        encoding="utf-8"
    )
    assert '"energy.pes_pending"' in html
    assert "pes_scan_pending" in html
    # zh + en i18n definitions plus the runtime t() lookup
    assert html.count('"energy.pes_pending"') == 3
    assert 't("energy.pes_pending")' in html
    # scan view gets the same follow / pick-survive semantics in its own block
    assert 'if (String(data.view_type || "") === "scan") {' in html
    assert "var selectedScan = scanNodes.find(" in html
