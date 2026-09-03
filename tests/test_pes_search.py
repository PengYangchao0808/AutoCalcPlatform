# pyright: reportAny=false, reportExplicitAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from acp.backends.base import QCResult
from acp.calculations.batch.singlepoint import BatchSinglePointExecutor, BatchSinglePointFrameResult
from acp.calculations.pes.contracts import (
    CandidateRecommendation,
    EnergyProfile,
    PesScanRequest,
    ScanCoordinate,
    ScanFrame,
    SinglePointSpec,
    build_default_protocol,
    coordinate_step,
    validate_scan_coordinate,
    validate_scan_coordinates,
)
from acp.calculations.pes.scan import (
    _extract_frames,
    _run_single_points,
    build_coordinate_plan,
    run_pes_scan,
)
from acp.calculations.progress import ProgressReporter
from acp.core.models import Structure
from cccp.qc.interfaces.constraints import CoordinateSpec
from cccp.qc.interfaces.xtb_scan import RelaxedScanPoint, RelaxedScanResult
from tests.conftest import FakeBackend


def _frames() -> list[Structure]:
    return [
        Structure(
            id=f"frame_{index:03d}",
            symbols=["H"],
            coordinates=[[float(index), 0.0, 0.0]],
        )
        for index in range(1, 6)
    ]


def _file_backed_fake(
    fake_backend: FakeBackend,
    monkeypatch: pytest.MonkeyPatch,
    *,
    failing_frames: set[int] | None = None,
) -> None:
    original = fake_backend.single_point
    failures = failing_frames or set()

    def single_point(coordinates: Any, symbols: Any, **kwargs: Any) -> QCResult:
        frame_index = int(round(float(np.asarray(coordinates)[0, 0])))
        if frame_index in failures:
            raise RuntimeError(f"frame {frame_index} failed")
        result = original(coordinates, symbols, **kwargs)
        output_dir = Path(kwargs["output_dir"])
        output_name = str(kwargs["output_name"])
        output_file = output_dir / f"{output_name}.out"
        _ = output_file.write_text(f"frame {frame_index}\n", encoding="utf-8")
        result.output_file = output_file
        return result

    monkeypatch.setattr(fake_backend, "single_point", single_point)


def _executor(
    frames: list[Structure],
    output_dir: Path,
    fake_backend: FakeBackend,
) -> BatchSinglePointExecutor:
    return BatchSinglePointExecutor(
        frames=frames,
        method="B97-3c",
        basis="def2-SVP",
        output_dir=output_dir,
        max_workers=5,
        backend_factory=lambda name: fake_backend,
    )


def test_batch_sp_success(
    tmp_path: Path,
    fake_backend: FakeBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _file_backed_fake(fake_backend, monkeypatch)

    result = _executor(_frames(), tmp_path, fake_backend).run()

    assert list(result) == [f"frame_{index:03d}" for index in range(1, 6)]
    assert all(record.status == "completed" for record in result.values())
    assert all(record.energy_hartree == -1.0 for record in result.values())
    assert all(not record.cache_hit for record in result.values())
    assert len(fake_backend.calls) == 5


def test_batch_sp_cache_hit(
    tmp_path: Path,
    fake_backend: FakeBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _file_backed_fake(fake_backend, monkeypatch)
    executor = _executor(_frames(), tmp_path, fake_backend)

    first = executor.run()
    first_call_count = len(fake_backend.calls)
    second = executor.run()

    assert first_call_count == 5
    assert len(fake_backend.calls) == first_call_count
    assert all(not record.cache_hit for record in first.values())
    assert all(record.cache_hit for record in second.values())
    assert all(record.status == "completed" for record in second.values())


def test_batch_sp_frame_isolation(
    tmp_path: Path,
    fake_backend: FakeBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _file_backed_fake(fake_backend, monkeypatch, failing_frames={3, 5})

    result = _executor(_frames(), tmp_path, fake_backend).run()

    assert result["frame_001"].energy_hartree == -1.0
    assert result["frame_002"].energy_hartree == -1.0
    assert result["frame_004"].energy_hartree == -1.0
    assert result["frame_003"].status == "failed"
    assert result["frame_005"].status == "failed"
    assert result["frame_003"].energy_hartree is None
    assert result["frame_005"].energy_hartree is None
    assert "frame 3 failed" in (result["frame_003"].error_message or "")
    assert "frame 5 failed" in (result["frame_005"].error_message or "")


# ── PES scan contracts ─────────────────────────────────────────────────


_ETHYLENE_XYZ = """\
6
C2H4 charge=0 mult=1
C   0.000000   0.000000   0.000000
C   1.339000   0.000000   0.000000
H  -0.506000   0.934000   0.000000
H  -0.506000  -0.934000   0.000000
H   1.845000   0.934000   0.000000
H   1.845000  -0.934000   0.000000
"""


def _pes_request(n_points: int = 5) -> dict[str, Any]:
    return {
        "mode": "bond_length_scan",
        "source": {
            "source_type": "xyz_text",
            "xyz_text": _ETHYLENE_XYZ,
            "charge": 0,
            "multiplicity": 1,
        },
        "coordinate": {
            "kind": "distance",
            "atoms": [0, 1],
            "start": 1.2,
            "end": 2.5,
            "n_points": n_points,
        },
    }


def _pes_scan_result(
    n_points: int,
    coords: np.ndarray[Any, Any],
    symbols: list[str],
    *,
    energies: list[float] | None = None,
    success: bool = True,
    output_dir: Path | None = None,
) -> RelaxedScanResult:
    if energies is None:
        energies = [-1.0 + 0.5 * (i / max(n_points - 1, 1) - 0.5) ** 2 for i in range(n_points)]
    points: list[RelaxedScanPoint] = []
    for i in range(n_points):
        frame_coordinates = coords.copy()
        frame_coordinates[1, 0] += 0.01 * i
        points.append(
            RelaxedScanPoint(
                frame_index=i,
                progress=i / max(n_points - 1, 1),
                coordinates=frame_coordinates,
                symbols=symbols.copy(),
                energy_hartree=energies[i],
                success=True,
                coordinate_values={"distance": 1.2 + i * (2.5 - 1.2) / max(n_points - 1, 1)},
            )
        )
    scan_dir = output_dir or Path("/tmp/pes_test")
    return RelaxedScanResult(
        points=points,
        input_xyz=scan_dir / "input.xyz",
        scan_dir=scan_dir,
        success=success,
    )


class TestPesContracts:
    def test_scan_coordinate_roundtrip(self) -> None:
        coord = ScanCoordinate(kind="distance", atoms=(0, 1), start=1.2, end=2.5, n_points=10)
        restored = ScanCoordinate.from_dict(coord.to_dict())
        assert restored == coord

    def test_pes_scan_request_roundtrip(self) -> None:
        req = PesScanRequest.from_dict(_pes_request(5))
        restored = PesScanRequest.from_dict(req.to_dict())
        assert restored.coordinate.n_points == 5
        assert restored.coordinate.atoms == (0, 1)

    def test_coordinate_step_distance(self) -> None:
        coord = ScanCoordinate(kind="distance", atoms=(0, 1), start=1.0, end=2.0, n_points=11)
        assert coordinate_step(coord) == pytest.approx(0.1)

    def test_validate_scan_coordinate_valid(self) -> None:
        coord = ScanCoordinate(kind="distance", atoms=(0, 1), start=1.0, end=2.0, n_points=10)
        validate_scan_coordinate(coord)

    def test_validate_scan_coordinate_same_atoms(self) -> None:
        coord = ScanCoordinate(kind="distance", atoms=(0, 0), start=1.0, end=2.0, n_points=10)
        with pytest.raises(ValueError, match="different atoms"):
            validate_scan_coordinate(coord)

    def test_validate_scan_coordinate_equal_start_end(self) -> None:
        coord = ScanCoordinate(kind="distance", atoms=(0, 1), start=1.5, end=1.5, n_points=10)
        with pytest.raises(ValueError, match="must differ"):
            validate_scan_coordinate(coord)

    def test_build_coordinate_plan(self) -> None:
        coord = ScanCoordinate(kind="distance", atoms=(2, 5), start=1.0, end=3.0, n_points=8)
        plan = build_coordinate_plan(coord)
        assert isinstance(plan, CoordinateSpec)
        assert plan.atoms == (2, 5)
        assert plan.start == 1.0
        assert plan.end == 3.0
        assert plan.role == "drive"

    def test_build_default_protocol(self) -> None:
        coord = ScanCoordinate(kind="distance", atoms=(0, 1), start=1.0, end=2.0, n_points=10)
        proto = build_default_protocol(coord)
        assert proto.scan_driver.software == "orca"
        assert proto.scan_optimizer.method == "GFN2-xTB"
        assert proto.single_point.method == "B97-3c"

    def test_energy_profile_to_dict(self) -> None:
        profile = EnergyProfile(
            energy_source="single_point",
            unit="kcal/mol",
            reference_index=0,
            relative_energies_kcal_mol=(0.0, 1.5, 3.0),
            raw_hartree=(-1.0, -0.997, -0.995),
        )
        d = profile.to_dict()
        assert d["energy_source"] == "single_point"
        assert len(d["raw_hartree"]) == 3

    def test_scan_frame_to_dict(self) -> None:
        frame = ScanFrame(index=2, target_coordinate=1.5, actual_coordinate=1.48)
        d = frame.to_dict()
        assert d["index"] == 2
        assert d["single_point_status"] == "skipped"

    def test_candidate_recommendation_to_dict(self) -> None:
        rec = CandidateRecommendation(
            candidate_id="ts_guess_003",
            kind="ts",
            frame_index=2,
            geometry_path="scan_frames/frame_002.xyz",
            score=0.85,
            confidence="high",
        )
        d = rec.to_dict()
        assert d["candidate_id"] == "ts_guess_003"
        assert d["kind"] == "ts"


# ── PES scan pipeline ──────────────────────────────────────────────────


def test_scan_five_frames_profile(
    fake_backend: FakeBackend,
    tmp_path: Path,
) -> None:
    """5 frames → pes_profile.json + candidate xyz materialized."""
    coords = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.34, 0.0, 0.0],
            [-0.51, 0.93, 0.0],
            [-0.51, -0.93, 0.0],
            [1.85, 0.93, 0.0],
            [1.85, -0.93, 0.0],
        ]
    )
    symbols = ["C", "C", "H", "H", "H", "H"]
    n_points = 5

    energies = [-1.0, -0.8, -0.5, -0.8, -1.0]
    scan_result = _pes_scan_result(
        n_points, coords, symbols, energies=energies, output_dir=tmp_path
    )
    fake_backend.set_result("relaxed_scan", scan_result)

    sp_energies = [-1.01, -0.81, -0.51, -0.81, -1.01]
    fake_backend.set_results(
        "single_point",
        [QCResult(success=True, energy=e) for e in sp_energies],
    )

    result = run_pes_scan(
        request=_pes_request(n_points),
        output_dir=tmp_path,
        config={"resources": {"nproc": 1}},
    )

    assert sum(call.method == "single_point" for call in fake_backend.calls) == 5
    frames = result["frames"]
    assert len(frames) == 5
    assert all(f["optimization_converged"] for f in frames)

    profile = result["profile"]
    assert profile["energy_source"] == "single_point"
    assert len(profile["relative_energies_kcal_mol"]) == 5
    assert profile["reference_index"] == 0
    assert profile["relative_energies_kcal_mol"][2] > profile["relative_energies_kcal_mol"][0]

    quality = result["quality"]
    assert quality["scan_complete"] is True

    ts_recs = result["ts_recommendations"]
    assert len(ts_recs) >= 1
    assert ts_recs[0]["kind"] == "ts"

    scan_dir = tmp_path / "WORK" / "07_PATH" / "pes_scan_001"
    assert (scan_dir / "scan_frames" / "frame_000.xyz").exists()
    assert (scan_dir / "scan_frames" / "frame_004.xyz").exists()
    assert not (tmp_path / "WORK" / "02_SEARCH" / "pes_scan_001").exists()


def test_single_points_report_live_metrics_during_and_after_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    frames = [
        ScanFrame(
            index=index,
            target_coordinate=1.2,
            actual_coordinate=1.2,
            geometry_path=f"scan_frames/frame_{index:03d}.xyz",
        )
        for index in range(25)
    ]
    reporter = ProgressReporter(tmp_path, stages=["run_single_points"])
    reporter.initialize()
    reporter.start_stage("run_single_points")
    snapshots: list[dict[str, Any]] = []

    class StubExecutor:
        def __init__(self, **kwargs: Any) -> None:
            self._frame_ids = kwargs["frame_ids"]
            self._on_frame_start = kwargs.get("on_frame_start")
            self._progress_callback = kwargs.get("progress_callback")

        def run(self) -> dict[str, BatchSinglePointFrameResult]:
            snapshots.append(json.loads((tmp_path / "state.json").read_text(encoding="utf-8")))
            if self._on_frame_start is not None:
                self._on_frame_start("malformed", 0, 25)
                self._on_frame_start("frame_015", 0, 25)
            if self._progress_callback is not None:
                self._progress_callback(14, 25)
            snapshots.append(json.loads((tmp_path / "state.json").read_text(encoding="utf-8")))
            return {
                frame_id: BatchSinglePointFrameResult(
                    frame_id=frame_id,
                    energy_hartree=-1.0,
                    status="completed",
                    cache_key=frame_id,
                )
                for frame_id in self._frame_ids
            }

    monkeypatch.setattr("acp.calculations.pes.scan.BatchSinglePointExecutor", StubExecutor)

    _run_single_points(
        frames,
        charge=0,
        multiplicity=1,
        sp_spec=build_default_protocol(
            ScanCoordinate(kind="distance", atoms=(0, 1), start=1.2, end=2.5, n_points=25)
        ).single_point,
        scan_dir=tmp_path,
        cfg={},
        reporter=reporter,
    )

    initial_metrics = {metric["key"]: metric for metric in snapshots[0]["live_metrics"]}
    assert initial_metrics["completed_total"]["value"] == "0 / 25"
    mid_run_metrics = {metric["key"]: metric for metric in snapshots[1]["live_metrics"]}
    assert mid_run_metrics["completed_total"]["value"] == "14 / 25"
    assert mid_run_metrics["current_frame"]["value"] == "Frame 15"
    final_metrics = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))[
        "live_metrics"
    ]
    assert [(metric["key"], metric["value"]) for metric in final_metrics] == [
        ("completed_total", "25 / 25")
    ]


def test_single_points_disabled_does_not_write_live_metrics(tmp_path: Path) -> None:
    reporter = ProgressReporter(tmp_path, stages=["run_single_points"])
    reporter.initialize()
    reporter.start_stage("run_single_points")

    _run_single_points(
        [],
        charge=0,
        multiplicity=1,
        sp_spec=SinglePointSpec(enabled=False),
        scan_dir=tmp_path,
        cfg={},
        reporter=reporter,
    )

    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert "live_metrics" not in state


def test_extract_frames_reports_live_metric(tmp_path: Path) -> None:
    coords = np.array([[0.0, 0.0, 0.0], [1.2, 0.0, 0.0]])
    coordinate = ScanCoordinate(kind="distance", atoms=(0, 1), start=1.2, end=2.5, n_points=3)
    reporter = ProgressReporter(tmp_path, stages=["extract_frames"], min_interval=999.0)
    reporter.initialize()
    reporter.start_stage("extract_frames")

    _extract_frames(
        _pes_scan_result(3, coords, ["C", "C"], output_dir=tmp_path),
        coordinate,
        tmp_path,
        reporter,
    )

    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["live_metrics"] == [
        {
            "key": "frames_extracted",
            "label_key": "live.frames_extracted",
            "label": None,
            "value": "3 / 3",
            "kind": "count",
            "priority": 100,
            "detail": None,
        }
    ]


def test_frame_extraction_rescue_or_structured_fail(
    fake_backend: FakeBackend,
    tmp_path: Path,
) -> None:
    """Frame extraction failure → rescue or structured failure (no crash)."""
    failed_result = RelaxedScanResult(
        points=[],
        input_xyz=tmp_path / "input.xyz",
        scan_dir=tmp_path,
        success=False,
        message="ORCA scan crashed at point 3",
    )
    fake_backend.set_result("relaxed_scan", failed_result)

    with pytest.raises(RuntimeError, match="Relaxed scan failed"):
        _ = run_pes_scan(
            request=_pes_request(5),
            output_dir=tmp_path,
            config={"resources": {"nproc": 1}},
        )


_DOUBLE_SCAN_XYZ = """\
5
C5 chain
C   0.000000   0.000000   0.000000
C   1.400000   0.000000   0.000000
C   2.800000   0.000000   0.000000
C   4.200000   0.000000   0.000000
C   5.600000   0.000000   0.000000
"""


def _double_scan_request() -> dict[str, Any]:
    coordinate = {
        "kind": "distance",
        "atoms": [0, 1],
        "start": 1.2,
        "end": 2.2,
        "n_points": 4,
    }
    return {
        "mode": "bond_length_scan",
        "source": {
            "source_type": "xyz_text",
            "xyz_text": _DOUBLE_SCAN_XYZ,
            "charge": 0,
            "multiplicity": 1,
        },
        "coordinate": coordinate,
        "coordinates": [
            coordinate,
            {"kind": "distance", "atoms": [3, 4], "start": 1.2, "end": 2.2, "n_points": 4},
        ],
        "selection": {"kind": "double_bond_scan", "atom_indices": [0, 1, 3, 4]},
    }


def test_double_scan_persists_per_frame_target_and_actual(
    fake_backend: FakeBackend,
    tmp_path: Path,
) -> None:
    """Both bonds carry target+actual values on every frame (synchronous λ)."""
    symbols = ["C"] * 5
    points: list[RelaxedScanPoint] = []
    for i in range(4):
        target = 1.2 + i * (2.2 - 1.2) / 3
        coords = np.array(
            [
                [0.0, 0.0, 0.0],
                [target, 0.0, 0.0],
                [target + 1.4, 0.0, 0.0],
                [target + 2.8, 0.0, 0.0],
                [target + 2.8 + target, 0.0, 0.0],
            ]
        )
        points.append(
            RelaxedScanPoint(
                frame_index=i,
                progress=i / 3,
                coordinates=coords,
                symbols=list(symbols),
                energy_hartree=-1.0 - i * 0.01,
                success=True,
                coordinate_values={"coordinate_1": target, "coordinate_2": target},
            )
        )
    fake_backend.set_result(
        "relaxed_scan",
        RelaxedScanResult(
            points=points,
            input_xyz=tmp_path / "input.xyz",
            scan_dir=tmp_path,
            success=True,
        ),
    )
    fake_backend.set_results(
        "single_point",
        [QCResult(success=True, energy=-1.0 - i * 0.01) for i in range(4)],
    )

    result = run_pes_scan(
        request=_double_scan_request(),
        output_dir=tmp_path,
        config={"resources": {"nproc": 1}},
    )

    assert len(result["coordinates"]) == 2
    assert result["selection"]["kind"] == "double_bond_scan"
    assert result["coordinate"]["atoms"] == [0, 1]
    frames = result["frames"]
    assert len(frames) == 4
    for position, frame in enumerate(frames):
        target = 1.2 + position * (2.2 - 1.2) / 3
        assert set(frame["target_coordinates"]) == {"coordinate_1", "coordinate_2"}
        assert set(frame["actual_coordinates"]) == {"coordinate_1", "coordinate_2"}
        assert frame["target_coordinates"]["coordinate_1"] == pytest.approx(target)
        assert frame["target_coordinates"]["coordinate_2"] == pytest.approx(target)
        assert frame["actual_coordinates"]["coordinate_1"] == pytest.approx(target, abs=1e-3)
        assert frame["actual_coordinates"]["coordinate_2"] == pytest.approx(target, abs=1e-3)


def test_coordinates_only_request_resolves_primary(
    fake_backend: FakeBackend,
    tmp_path: Path,
) -> None:
    request = _pes_request(5)
    del request["coordinate"]
    request["coordinates"] = [
        {"kind": "distance", "atoms": [0, 1], "start": 1.2, "end": 2.5, "n_points": 5}
    ]
    ethylene = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.339, 0.0, 0.0],
            [-0.506, 0.934, 0.0],
            [-0.506, -0.934, 0.0],
            [1.845, 0.934, 0.0],
            [1.845, -0.934, 0.0],
        ]
    )
    fake_backend.set_result(
        "relaxed_scan",
        _pes_scan_result(5, ethylene, ["C", "C", "H", "H", "H", "H"], output_dir=tmp_path),
    )

    result = run_pes_scan(request=request, output_dir=tmp_path)

    assert result["coordinate"]["atoms"] == [0, 1]
    assert len(result["coordinates"]) == 1
    assert len(result["frames"]) == 5


def test_partial_synchronous_failure_fails_fast(
    fake_backend: FakeBackend,
    tmp_path: Path,
) -> None:
    """success=False with partial points aborts before frames/SP (no scan_dir/'')."""
    good_point = RelaxedScanPoint(
        frame_index=0,
        progress=0.0,
        coordinates=np.array([[0.0, 0.0, 0.0], [1.2, 0.0, 0.0]]),
        symbols=["C", "C"],
        energy_hartree=-1.0,
        success=True,
        coordinate_values={"distance": 1.2},
    )
    failed_point = RelaxedScanPoint(
        frame_index=1,
        progress=1.0,
        coordinates=None,
        symbols=None,
        energy_hartree=None,
        success=False,
        coordinate_values={"distance": 2.5},
    )
    fake_backend.set_result(
        "relaxed_scan",
        RelaxedScanResult(
            points=[good_point, failed_point],
            input_xyz=tmp_path / "input.xyz",
            scan_dir=tmp_path,
            success=False,
            message="constrained optimization failed at frame 1",
        ),
    )

    with pytest.raises(RuntimeError, match="Relaxed scan failed"):
        _ = run_pes_scan(
            request=_pes_request(3),
            output_dir=tmp_path,
            config={"resources": {"nproc": 1}},
        )

    assert not (tmp_path / "WORK" / "07_PATH" / "pes_scan_001" / "scan_frames").exists()
    assert not any(call.method == "single_point" for call in fake_backend.calls)


def test_validate_scan_coordinates_rejects_too_many() -> None:
    coordinates = [
        ScanCoordinate(kind="distance", atoms=(0, 1), start=1.0, end=2.0, n_points=5)
        for _ in range(5)
    ]
    with pytest.raises(ValueError, match="at most 4"):
        _ = validate_scan_coordinates(tuple(coordinates))


def test_scan_coordinate_out_of_range(
    tmp_path: Path,
) -> None:
    request = {
        "mode": "bond_length_scan",
        "source": {
            "source_type": "xyz_text",
            "xyz_text": _ETHYLENE_XYZ,
            "charge": 0,
            "multiplicity": 1,
        },
        "coordinate": {
            "kind": "distance",
            "atoms": [0, 99],
            "start": 1.0,
            "end": 2.0,
            "n_points": 5,
        },
    }
    with pytest.raises(ValueError, match="out of range"):
        _ = run_pes_scan(request=request, output_dir=tmp_path)


def test_invalid_coordinate_kind() -> None:
    coord = ScanCoordinate(kind="stretch", atoms=(0, 1), start=1.0, end=2.0, n_points=5)
    with pytest.raises(ValueError, match="must be 'distance'"):
        validate_scan_coordinate(coord)


def test_from_confsearch_manifest(
    fake_backend: FakeBackend,
    tmp_path: Path,
) -> None:
    """Fixture manifest → candidates TS/INT + profile."""
    from acp.calculations.pes.engine import PesSearchEngine

    conformer_dir = tmp_path / "conformers"
    conformer_dir.mkdir(parents=True, exist_ok=True)
    xyz_path = conformer_dir / "conf_0001.xyz"
    xyz_path.write_text(_ETHYLENE_XYZ, encoding="utf-8")

    manifest = {
        "schema_version": "confsearch_v1",
        "workflow": "Confsearch",
        "protocol": "censo-crest",
        "profile": "default",
        "refinement_policy": "screen",
        "backend": "native",
        "conformers": [
            {
                "conf_id": "conf_0001",
                "geometry": "conformers/conf_0001.xyz",
                "energy_hartree": -78.5,
                "free_energy_hartree": -78.4,
                "relative_energy_kcal": 0.0,
                "boltzmann_weight": 1.0,
                "rank": 1,
            }
        ],
        "selected_conformers": ["conf_0001"],
    }
    manifest_path = tmp_path / "confsearch_manifest.json"
    manifest_path.write_text(__import__("json").dumps(manifest), encoding="utf-8")

    coords = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.34, 0.0, 0.0],
            [-0.51, 0.93, 0.0],
            [-0.51, -0.93, 0.0],
            [1.85, 0.93, 0.0],
            [1.85, -0.93, 0.0],
        ]
    )
    symbols = ["C", "C", "H", "H", "H", "H"]
    n_points = 5
    energies = [-1.0, -0.8, -0.5, -0.8, -1.0]
    scan_result = _pes_scan_result(
        n_points, coords, symbols, energies=energies, output_dir=tmp_path
    )
    fake_backend.set_result("relaxed_scan", scan_result)
    sp_energies = [-1.01, -0.81, -0.51, -0.81, -1.01]
    fake_backend.set_results(
        "single_point",
        [QCResult(success=True, energy=e) for e in sp_energies],
    )

    engine = PesSearchEngine(config={"resources": {"nproc": 1}}, output_dir=tmp_path)
    result = engine.run(
        confsearch_manifest=manifest_path,
        coordinate=ScanCoordinate(
            kind="distance",
            atoms=(0, 1),
            start=1.2,
            end=2.5,
            n_points=n_points,
        ),
        charge=0,
        multiplicity=1,
    )

    assert result.status == "complete"
    assert result.profile.get("energy_source") == "single_point"
    assert len(result.ts_candidates) >= 1
    assert result.pes_profile_path is not None
    assert result.pes_profile_path.exists()
    assert (tmp_path / "RESULT" / "result_manifest.json").exists()
    result_manifest = __import__("json").loads(
        (tmp_path / "RESULT" / "result_manifest.json").read_text(encoding="utf-8")
    )
    assert {product["kind"] for product in result_manifest["products"]} >= {
        "pes_profile",
        "structure",
    }

    for cand in result.ts_candidates:
        if cand.candidate_id in result.candidate_structures:
            assert result.candidate_structures[cand.candidate_id].exists()


def test_bad_manifest_structured_error(
    tmp_path: Path,
) -> None:
    """Missing conformers → PES_E_MANIFEST structured error."""
    from acp.calculations.pes.engine import (
        PES_E_MANIFEST,
        PesSearchError,
        load_confsearch_manifest,
    )

    missing_path = tmp_path / "nonexistent.json"
    with pytest.raises(PesSearchError, match=PES_E_MANIFEST):
        load_confsearch_manifest(missing_path)

    empty_manifest = {
        "schema_version": "confsearch_v1",
        "workflow": "Confsearch",
        "conformers": [],
    }
    empty_path = tmp_path / "empty_manifest.json"
    empty_path.write_text(__import__("json").dumps(empty_manifest), encoding="utf-8")
    with pytest.raises(PesSearchError, match=PES_E_MANIFEST):
        load_confsearch_manifest(empty_path)

    bad_json_path = tmp_path / "bad.json"
    bad_json_path.write_text("not json {{{", encoding="utf-8")
    with pytest.raises(PesSearchError, match=PES_E_MANIFEST):
        load_confsearch_manifest(bad_json_path)


def test_path_selection_smoke() -> None:
    from acp.calculations.pes.path_selection import (
        SelectionPolicy,
        policy_from_config,
    )

    policy = SelectionPolicy()
    assert policy.ts_min_prominence_kcal_mol > 0

    config_policy = policy_from_config({"ts_min_prominence_kcal_mol": 1.5})
    assert config_policy.ts_min_prominence_kcal_mol == 1.5


def test_validation_smoke() -> None:
    from acp.calculations.pes.validation import compare_graph_topology

    coords = np.array([[0.0, 0.0, 0.0], [1.5, 0.0, 0.0]])
    result = compare_graph_topology(
        product_coords=coords,
        candidate_coords=coords,
        symbols=["C", "C"],
        forming_bonds=[(0, 1)],
    )
    assert result.is_valid


def test_candidates_smoke() -> None:
    from acp.calculations.pes.candidates import PathPoint, select_candidates

    points = [
        PathPoint(
            point_id=f"p{i:03d}",
            progress=i / 4.0,
            energies_hartree={"scan": e},
        )
        for i, e in enumerate([-1.0, -0.8, -0.5, -0.8, -1.0])
    ]
    candidates = select_candidates(points, energy_key="scan")
    assert len(candidates) >= 1
    assert any(c.kind == "ts_seed" for c in candidates)


def test_bond_changes_smoke() -> None:
    from acp.calculations.pes.bond_changes import BondChange

    assert BondChange is not None


def test_atom_mapping_smoke() -> None:
    from acp.calculations.pes.atom_mapping import map_reactant_to_product

    assert map_reactant_to_product is not None


def test_pes_engine_no_mechanism_imports() -> None:
    import acp.calculations.pes.engine as engine_module

    source = Path(engine_module.__file__).read_text(encoding="utf-8")
    import_lines = [
        line for line in source.splitlines() if line.strip().startswith(("import ", "from "))
    ]
    mechanism_imports = [line for line in import_lines if "acp.mechanism" in line]
    assert mechanism_imports == [], f"Found mechanism imports: {mechanism_imports}"


# ── PESsearch entry tests (todo 33) ────────────────────────────────────


def _make_confsearch_manifest(tmp_path: Path) -> Path:
    conformer_dir = tmp_path / "conformers"
    conformer_dir.mkdir(parents=True, exist_ok=True)
    xyz_path = conformer_dir / "conf_0001.xyz"
    xyz_path.write_text(_ETHYLENE_XYZ, encoding="utf-8")
    manifest = {
        "schema_version": "confsearch_v1",
        "workflow": "Confsearch",
        "protocol": "censo-crest",
        "profile": "default",
        "refinement_policy": "screen",
        "backend": "native",
        "conformers": [
            {
                "conf_id": "conf_0001",
                "geometry": "conformers/conf_0001.xyz",
                "energy_hartree": -78.5,
                "free_energy_hartree": -78.4,
                "relative_energy_kcal": 0.0,
                "boltzmann_weight": 1.0,
                "rank": 1,
            }
        ],
        "selected_conformers": ["conf_0001"],
    }
    manifest_path = tmp_path / "confsearch_manifest.json"
    manifest_path.write_text(__import__("json").dumps(manifest), encoding="utf-8")
    return manifest_path


def _setup_pes_fake_backend(
    fake_backend: FakeBackend,
    tmp_path: Path,
) -> None:
    coords = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.34, 0.0, 0.0],
            [-0.51, 0.93, 0.0],
            [-0.51, -0.93, 0.0],
            [1.85, 0.93, 0.0],
            [1.85, -0.93, 0.0],
        ]
    )
    symbols = ["C", "C", "H", "H", "H", "H"]
    n_points = 5
    energies = [-1.0, -0.8, -0.5, -0.8, -1.0]
    scan_result = _pes_scan_result(
        n_points, coords, symbols, energies=energies, output_dir=tmp_path
    )
    fake_backend.set_result("relaxed_scan", scan_result)
    sp_energies = [-1.01, -0.81, -0.51, -0.81, -1.01]
    fake_backend.set_results(
        "single_point",
        [QCResult(success=True, energy=e) for e in sp_energies],
    )


def test_entry_from_artifact(
    fake_backend: FakeBackend,
    tmp_path: Path,
) -> None:
    from acp.workflows.pes_search import run_pes_search

    manifest_path = _make_confsearch_manifest(tmp_path)
    _setup_pes_fake_backend(fake_backend, tmp_path)

    result = run_pes_search(
        confsearch_manifest=manifest_path,
        coordinate=ScanCoordinate(
            kind="distance",
            atoms=(0, 1),
            start=1.2,
            end=2.5,
            n_points=5,
        ),
        output_dir=tmp_path,
        config={"resources": {"nproc": 1}},
    )

    assert result.status == "completed"
    assert result.metadata["ts_candidates"] >= 1
    pes_profile = tmp_path / "RESULT" / "pes_search" / "pes_profile.json"
    assert pes_profile.exists()
    profile = __import__("json").loads(pes_profile.read_text(encoding="utf-8"))
    assert profile["schema_version"] == "pes_profile_v2"
    assert profile["scan_dir"] == "WORK/07_PATH/pes_scan_001"
    assert (tmp_path / "RESULT" / "result_manifest.json").exists()


def test_entry_from_direct_input(
    fake_backend: FakeBackend,
    tmp_path: Path,
) -> None:
    from acp.workflows.pes_search import run_pes_search

    xyz_path = tmp_path / "ethylene.xyz"
    xyz_path.write_text(_ETHYLENE_XYZ, encoding="utf-8")
    _setup_pes_fake_backend(fake_backend, tmp_path)

    result = run_pes_search(
        input_xyz=xyz_path,
        coordinate=ScanCoordinate(
            kind="distance",
            atoms=(0, 1),
            start=1.2,
            end=2.5,
            n_points=5,
        ),
        output_dir=tmp_path,
        config={"resources": {"nproc": 1}},
    )

    assert result.status == "completed"
    assert result.metadata["ts_candidates"] >= 1


def test_entry_from_job_missing(tmp_path: Path) -> None:
    from acp.workflows.pes_search import PesSearchInputError, run_pes_search

    with pytest.raises(PesSearchInputError, match="requires either"):
        run_pes_search(
            confsearch_manifest=None,
            input_xyz=None,
            output_dir=tmp_path,
        )


def test_entry_with_reaction(
    fake_backend: FakeBackend,
    tmp_path: Path,
) -> None:
    from acp.workflows.pes_search import run_pes_search

    manifest_path = _make_confsearch_manifest(tmp_path)
    _setup_pes_fake_backend(fake_backend, tmp_path)

    reaction = {
        "schema_version": 2,
        "index_base": 0,
        "reactant": {"smiles": "C=C"},
        "product": {"smiles": "C-C"},
        "atom_mapping": [[0, 0], [1, 1]],
        "content_hash": "abc123",
    }
    result = run_pes_search(
        confsearch_manifest=manifest_path,
        coordinate=ScanCoordinate(
            kind="distance",
            atoms=(0, 1),
            start=1.2,
            end=2.5,
            n_points=5,
        ),
        reaction=reaction,
        output_dir=tmp_path,
        config={"resources": {"nproc": 1}},
    )

    assert result.status == "completed"
    assert result.metadata["reaction"] is not None


def test_entry_with_reaction_invalid(tmp_path: Path) -> None:
    from acp.compat.legacy.manifests import read_reaction_definition

    bad_reaction_path = tmp_path / "bad_reaction.json"
    bad_reaction_path.write_text('{"schema_version": 1}', encoding="utf-8")

    with pytest.raises(ValueError, match="config_hash"):
        read_reaction_definition(bad_reaction_path)


def test_pes_e_manifest_structured_error(tmp_path: Path) -> None:
    from acp.calculations.pes.engine import PES_E_MANIFEST, PesSearchError, load_confsearch_manifest

    missing_path = tmp_path / "nonexistent.json"
    with pytest.raises(PesSearchError) as exc_info:
        load_confsearch_manifest(missing_path)
    assert exc_info.value.code == PES_E_MANIFEST


def test_pes_e_coord_out_of_range(tmp_path: Path) -> None:
    from acp.calculations.pes.contracts import ScanCoordinate
    from acp.workflows.pes_search import (
        PES_E_COORD,
        PesSearchInputError,
        _validate_coordinate_atoms,
    )

    coord = ScanCoordinate(kind="distance", atoms=(0, 99), start=1.0, end=2.0, n_points=5)
    with pytest.raises(PesSearchInputError, match=PES_E_COORD):
        _validate_coordinate_atoms(coord, n_atoms=6)


def test_pes_e_strategy_unknown(tmp_path: Path) -> None:
    from acp.workflows.pes_search import PES_E_STRATEGY, PesSearchInputError, _validate_strategy

    with pytest.raises(PesSearchInputError, match=PES_E_STRATEGY):
        _validate_strategy("unknown_strategy")


def test_pes_workflow_no_mechanism_imports() -> None:
    import acp.workflows.pes_search as pes_module

    source = Path(pes_module.__file__).read_text(encoding="utf-8")
    import_lines = [
        line for line in source.splitlines() if line.strip().startswith(("import ", "from "))
    ]
    mechanism_imports = [line for line in import_lines if "acp.mechanism" in line]
    assert mechanism_imports == [], f"Found mechanism imports: {mechanism_imports}"


def test_pes_search_stages_exported() -> None:
    from acp.calculations.pes.engine import PES_SEARCH_STAGES

    assert len(PES_SEARCH_STAGES) == 9
    assert PES_SEARCH_STAGES[0] == "prepare"
    assert PES_SEARCH_STAGES[-1] == "finalize"


def test_pes_search_stage_tasks_provider() -> None:
    from acp.scheduler.jobs import JobSpec
    from acp.scheduler.stage_tasks import get_stage_plan

    plan = get_stage_plan(JobSpec(workflow="PESsearch", method={"mode": "bond_length_scan"}))
    names = [s.stage_name for s in plan]
    assert names[0] == "prepare"
    assert names[-1] == "finalize"
    assert len(names) == 9

    plan_path = get_stage_plan(JobSpec(workflow="PESsearch", method={"mode": "path"}))
    names_path = [s.stage_name for s in plan_path]
    assert names_path == [
        "prepare",
        "validate_coordinate",
        "materialize_input",
        "run_relaxed_scan",
        "extract_frames",
        "run_single_points",
        "build_profile",
        "select_candidates",
        "finalize",
    ]


def test_cli_pessearch_help() -> None:
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "acp.cli", "run", "PESsearch", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "PESsearch" in result.stdout or "coordinate" in result.stdout


# ── distance-scan path-selection integration ───────────────────────────


_ETHYLENE_COORDS = np.array(
    [
        [0.0, 0.0, 0.0],
        [1.34, 0.0, 0.0],
        [-0.51, 0.93, 0.0],
        [-0.51, -0.93, 0.0],
        [1.85, 0.93, 0.0],
        [1.85, -0.93, 0.0],
    ]
)


def _stretch_scan_result(
    n_points: int,
    coords: np.ndarray[Any, Any],
    symbols: list[str],
    *,
    energies: list[float],
    output_dir: Path | None = None,
) -> RelaxedScanResult:
    """Scan result whose frames rigidly translate the second CH2 group."""
    delta = 1.6 / max(n_points - 1, 1)
    points: list[RelaxedScanPoint] = []
    for i in range(n_points):
        frame_coordinates = coords.copy()
        frame_coordinates[[1, 4, 5], 0] += delta * i
        points.append(
            RelaxedScanPoint(
                frame_index=i,
                progress=i / max(n_points - 1, 1),
                coordinates=frame_coordinates,
                symbols=symbols.copy(),
                energy_hartree=energies[i],
                success=True,
                coordinate_values={"distance": float(coords[1, 0] - coords[0, 0]) + delta * i},
            )
        )
    scan_dir = output_dir or Path("/tmp/pes_test")
    return RelaxedScanResult(
        points=points,
        input_xyz=scan_dir / "input.xyz",
        scan_dir=scan_dir,
        success=True,
    )


def _run_barrier_scan(
    fake_backend: FakeBackend,
    tmp_path: Path,
    *,
    energies: list[float],
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    symbols = ["C", "C", "H", "H", "H", "H"]
    n_points = len(energies)
    fake_backend.set_result(
        "relaxed_scan",
        _stretch_scan_result(
            n_points,
            _ETHYLENE_COORDS,
            symbols,
            energies=energies,
            output_dir=tmp_path,
        ),
    )
    fake_backend.set_results(
        "single_point",
        [QCResult(success=True, energy=e) for e in energies],
    )
    return run_pes_scan(
        request=_pes_request(n_points),
        output_dir=tmp_path,
        config=config or {"resources": {"nproc": 1}},
    )


def test_distance_scan_knee_selection_resolves(
    fake_backend: FakeBackend,
    tmp_path: Path,
) -> None:
    """Barrier-shaped profile → knee-shifted selection, not global max/endpoint."""
    energies = [float(-1.0 + 0.03 * np.exp(-((i - 9) ** 2) / 8.0)) for i in range(15)]
    result = _run_barrier_scan(fake_backend, tmp_path, energies=energies)

    ts_recs = result["ts_recommendations"]
    assert len(ts_recs) == 1
    ts = ts_recs[0]
    assert ts["evidence"]["selection_algorithm"] == "endpoint_knee_shift_midpoint_v1"
    assert 5 <= ts["frame_index"] <= 13
    assert ts["confidence"] in ("medium", "high")
    assert ts["evidence"]["barrier_from_reactant_kcal_mol"] is not None

    int_recs = result["int_recommendations"]
    assert len(int_recs) == 1
    assert int_recs[0]["frame_index"] > ts["frame_index"]

    quality = result["quality"]
    assert "distance_selection_knee_shift_v1" in quality["notes"]
    assert quality["needs_review"] is False


def test_distance_scan_monotonic_rise_no_endpoint_ts(
    fake_backend: FakeBackend,
    tmp_path: Path,
) -> None:
    """Monotonic saturating rise → TS is knee-shifted, never the last frame."""
    energies = [float(-1.0 + 0.03 * (1.0 - np.exp(-i / 4.0))) for i in range(15)]
    result = _run_barrier_scan(fake_backend, tmp_path, energies=energies)

    ts_recs = result["ts_recommendations"]
    assert len(ts_recs) == 1
    ts = ts_recs[0]
    assert ts["frame_index"] <= 13
    assert ts["evidence"]["selection_algorithm"] == "endpoint_knee_shift_midpoint_v1"
    assert "distance_selection_knee_shift_v1" in result["quality"]["notes"]


def test_distance_scan_selection_config_gate_triggers_fallback(
    fake_backend: FakeBackend,
    tmp_path: Path,
) -> None:
    """Selection config flows into the policy: a barrier gate defers to fallback."""
    energies = [float(-1.0 + 0.03 * np.exp(-((i - 9) ** 2) / 8.0)) for i in range(15)]
    config = {
        "resources": {"nproc": 1},
        "pes": {
            "scan_selection": {
                "require_barrier_for_search_seed": True,
                "ts_min_reactant_barrier_kcal_mol": 100.0,
            }
        },
    }
    result = _run_barrier_scan(fake_backend, tmp_path, energies=energies, config=config)

    quality = result["quality"]
    assert "distance_selection_fallback:insufficient_barrier" in quality["notes"]
    assert quality["needs_review"] is True

    ts = result["ts_recommendations"][0]
    assert ts["evidence"]["peak_index"] == 9
    assert "selection_algorithm" not in ts["evidence"]


def test_engine_candidates_match_persisted_recommendations(
    fake_backend: FakeBackend,
    tmp_path: Path,
) -> None:
    """Engine candidates mirror the persisted recommendations (single source)."""
    from acp.calculations.pes.engine import PesSearchEngine

    manifest_path = _make_confsearch_manifest(tmp_path)
    _setup_pes_fake_backend(fake_backend, tmp_path)

    engine = PesSearchEngine(config={"resources": {"nproc": 1}}, output_dir=tmp_path)
    result = engine.run(
        confsearch_manifest=manifest_path,
        coordinate=ScanCoordinate(
            kind="distance",
            atoms=(0, 1),
            start=1.2,
            end=2.5,
            n_points=5,
        ),
        charge=0,
        multiplicity=1,
    )

    assert result.status == "complete"
    profile_path = result.pes_profile_path
    assert profile_path is not None
    profile_payload = json.loads(profile_path.read_text(encoding="utf-8"))
    assert {c.candidate_id for c in result.ts_candidates} == {
        c["candidate_id"] for c in profile_payload["ts_candidates"]
    }
    assert {c.candidate_id for c in result.int_candidates} == {
        c["candidate_id"] for c in profile_payload["int_candidates"]
    }
    search_result = result.metadata["search_result"]
    assert search_result["selected_ts_id"] == result.ts_candidates[0].candidate_id
    assert search_result["selected_ts_id"] in {
        c["candidate_id"] for c in profile_payload["ts_candidates"]
    }


def test_coordinate_scan_ids_rank_ordered() -> None:
    """Angle/dihedral ids follow prominence rank; config thresholds apply."""
    from acp.calculations.pes.scan import _recommend_coordinate_candidates

    energies = [-1.0, -0.9, -0.7, -0.95, -0.5, -0.96, -0.98, -0.97, -1.0]
    frames = [
        ScanFrame(
            index=i,
            target_coordinate=float(i),
            actual_coordinate=float(i),
            coordinate_unit="degree",
            geometry_path=f"scan_frames/frame_{i:03d}.xyz",
        )
        for i in range(len(energies))
    ]
    coordinate = ScanCoordinate(
        kind="dihedral",
        atoms=(0, 1, 2, 3),
        start=-180.0,
        end=180.0,
        n_points=len(energies),
    )
    profile = EnergyProfile(
        energy_source="scan",
        unit="kcal/mol",
        reference_index=0,
        relative_energies_kcal_mol=tuple(float((e - energies[0]) * 627.509) for e in energies),
        raw_hartree=tuple(energies),
    )

    ts_rows, int_rows, _quality = _recommend_coordinate_candidates(frames, coordinate, profile, {})
    assert [row.candidate_id for row in ts_rows] == [
        "ts_guess_001",
        "ts_guess_002",
        "ts_guess_003",
    ]
    assert ts_rows[0].frame_index == 4
    assert ts_rows[1].frame_index == 2
    assert ts_rows[2].frame_index == 7
    assert ts_rows[0].confidence == "medium"
    assert [row.candidate_id for row in int_rows] == ["int_guess_001", "int_guess_002"]
    assert int_rows[0].frame_index == 3
    assert int_rows[1].frame_index == 6

    strict_cfg = {"pes": {"scan_selection": {"ts_min_prominence_kcal_mol": 200.0}}}
    strict_rows, _int_rows, _quality = _recommend_coordinate_candidates(
        frames, coordinate, profile, strict_cfg
    )
    assert strict_rows[0].confidence == "medium"
    assert strict_rows[1].confidence == "low"
