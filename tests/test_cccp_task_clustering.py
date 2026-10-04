"""Pure-cccp clustering task tests (plan todo 42) — never import acp.

Independent call surface: ``run_clustering(request, context=…)`` drives a
fake ISOSTAT-shaped clustering backend through the validate → select →
translate → execute → typed-result pipeline; assignments reference the
original ensemble frame indices (``P2_TASK_CONTRACTS`` identity rules).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from cccp.calculation.context import TaskContext
from cccp.calculation.errors import TaskInputError
from cccp.calculation.requests import (
    ClusteringOptions,
    MdSamplingOptions,
    StructureInput,
    TaskKind,
    TaskRequest,
)
from cccp.calculation.results import ClusteringPayload, ErrorKind
from cccp.calculation.tasks.clustering import run_clustering
from cccp.qc.interfaces.base import QCResult

_SYMBOLS = ("H", "H")
# Two distinct geometries, each repeated once (frames 0/1 = A, 2/3 = B).
_GEOM_A = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.74))
_GEOM_B = ((0.0, 0.0, 0.0), (0.0, 0.0, 3.00))
_STACKED = _GEOM_A + _GEOM_A + _GEOM_B + _GEOM_B


def _request(tmp_path: Path, **overrides: Any) -> TaskRequest:
    payload: dict[str, Any] = {
        "task": TaskKind.CLUSTERING,
        "structure": StructureInput(coordinates=_STACKED, symbols=_SYMBOLS),
        "options": ClusteringOptions(),
        "output_dir": tmp_path / "run",
    }
    payload.update(overrides)
    return TaskRequest(**payload)


def _write_frames(path: Path, geometries: list[tuple[tuple[float, float, float], ...]]) -> Path:
    lines: list[str] = []
    for index, geometry in enumerate(geometries):
        lines.append("2")
        lines.append(f"Frame {index} | Energy: {-10.0 - index:.10f}")
        for x, y, z in geometry:
            lines.append(f"H {x:.3f} {y:.3f} {z:.3f}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class _FakeIsostatBackend:
    """ISOSTAT-shaped fake: ``cluster`` path entry (QCResult)."""

    name = "isostat"

    def __init__(self, factory: Any) -> None:
        self._factory = factory
        self.calls: list[dict[str, Any]] = []

    def cluster(self, ensemble_xyz: Any, output_dir: Any = None, **kwargs: Any) -> Any:
        target = Path(output_dir) if output_dir is not None else Path(ensemble_xyz).parent
        self.calls.append({"ensemble_xyz": Path(ensemble_xyz), "output_dir": target, **kwargs})
        return self._factory(target)


def _reps_result(geometries: list[tuple[tuple[float, float, float], ...]]) -> Any:
    def factory(target: Path) -> QCResult:
        cluster_xyz = _write_frames(target / "cluster.xyz", geometries)
        stacked = np.asarray([row for geometry in geometries for row in geometry], dtype=np.float64)
        symbols = list(_SYMBOLS) * len(geometries)
        return QCResult(
            success=True,
            coordinates=stacked,
            symbols=symbols,
            output_file=cluster_xyz,
        )

    return factory


# ── independent cccp call: success state ───────────────────────────────


def test_success_assigns_every_frame_and_keeps_representatives(tmp_path: Path) -> None:
    backend = _FakeIsostatBackend(_reps_result([_GEOM_A, _GEOM_B]))
    result = run_clustering(
        _request(tmp_path, options=ClusteringOptions(edis=0.5, gdis=0.25, temperature_k=298.15)),
        context=TaskContext(backend=backend),
    )

    assert result.status == "completed"
    assert result.complete is True
    payload = result.payload
    assert isinstance(payload, ClusteringPayload)
    assert len(payload.assignments) == 2
    by_id = {a.cluster_id: a for a in payload.assignments}
    assert by_id[0].representative_index == 0
    assert by_id[0].member_indices == (0, 1)
    assert by_id[1].representative_index == 2
    assert by_id[1].member_indices == (2, 3)
    assigned = sorted(i for a in payload.assignments for i in a.member_indices)
    assert assigned == [0, 1, 2, 3]
    assert payload.clustered_ref is not None
    assert payload.clustered_ref.type == "clustered"
    assert payload.clustered_ref.path.is_file()

    call = backend.calls[0]
    assert call["edis"] == pytest.approx(0.5)
    assert call["gdis"] == pytest.approx(0.25)
    assert call["temperature"] == pytest.approx(298.15)
    assert call["ensemble_xyz"].is_file()


def test_path_ensemble_input_shape_supported(tmp_path: Path) -> None:
    ensemble = _write_frames(tmp_path / "ensemble.xyz", [_GEOM_A, _GEOM_A, _GEOM_B, _GEOM_B])
    backend = _FakeIsostatBackend(_reps_result([_GEOM_A, _GEOM_B]))
    request = _request(tmp_path, structure=StructureInput(path=ensemble))
    result = run_clustering(request, context=TaskContext(backend=backend))

    assert result.status == "completed"
    payload = result.payload
    assert isinstance(payload, ClusteringPayload)
    assert sorted(i for a in payload.assignments for i in a.member_indices) == [0, 1, 2, 3]


# ── independent cccp call: partial-failure state ───────────────────────


def test_partial_keeps_assigned_clusters_and_representatives(tmp_path: Path) -> None:
    def factory(target: Path) -> QCResult:
        outcome = _reps_result([_GEOM_A])(target)
        return QCResult(
            success=False,
            error_message="ISOSTAT clustering failed with exit code 1",
            output_file=outcome.output_file,
            coordinates=outcome.coordinates,
            symbols=outcome.symbols,
        )

    backend = _FakeIsostatBackend(factory)
    result = run_clustering(_request(tmp_path), context=TaskContext(backend=backend))

    assert result.status == "failed"
    assert result.complete is False
    assert any("exit code 1" in error for error in result.errors)
    payload = result.payload
    assert isinstance(payload, ClusteringPayload)
    assert len(payload.assignments) == 1
    assert payload.assignments[0].representative_index == 0
    assert payload.assignments[0].member_indices == (0, 1, 2, 3)


# ── independent cccp call: empty state ─────────────────────────────────


def test_zero_clusters_is_failed_backend_failure(tmp_path: Path) -> None:
    def factory(target: Path) -> QCResult:
        cluster_xyz = _write_frames(target / "cluster.xyz", [])
        return QCResult(success=True, output_file=cluster_xyz)

    backend = _FakeIsostatBackend(factory)
    result = run_clustering(_request(tmp_path), context=TaskContext(backend=backend))

    assert result.status == "failed"
    assert result.complete is False
    assert result.error_kind is ErrorKind.BACKEND_FAILURE
    assert result.errors


def test_missing_cluster_output_is_failed_backend_failure(tmp_path: Path) -> None:
    backend = _FakeIsostatBackend(
        lambda target: QCResult(
            success=False,
            error_message="ISOSTAT completed without producing cluster.xyz",
        )
    )
    result = run_clustering(_request(tmp_path), context=TaskContext(backend=backend))

    assert result.status == "failed"
    assert result.error_kind is ErrorKind.BACKEND_FAILURE


# ── envelope validation ────────────────────────────────────────────────


def test_empty_ensemble_input_rejected(tmp_path: Path) -> None:
    backend = _FakeIsostatBackend(_reps_result([_GEOM_A]))
    request = _request(
        tmp_path,
        structure=StructureInput(coordinates=(), symbols=()),
    )
    with pytest.raises(TaskInputError):
        run_clustering(request, context=TaskContext(backend=backend))


def test_wrong_options_type_rejected(tmp_path: Path) -> None:
    backend = _FakeIsostatBackend(_reps_result([_GEOM_A]))
    request = _request(tmp_path, options=MdSamplingOptions())
    with pytest.raises(TaskInputError):
        run_clustering(request, context=TaskContext(backend=backend))
