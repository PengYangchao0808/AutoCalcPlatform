"""Pure-cccp md_sampling task tests (plan todo 42) — never import acp.

Independent call surface: ``run_md_sampling(request, context=…))`` drives a
fake Molclus/xTB-MD backend through the validate → select → translate →
execute → typed-result pipeline; success / partial / empty semantics follow
``P2_TASK_CONTRACTS`` (trajectory artifact + frame count).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

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
from cccp.calculation.results import ErrorKind, MdSamplingPayload
from cccp.calculation.tasks.md_sampling import run_md_sampling
from cccp.qc.interfaces.base import QCResult

_H2 = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.74))
_SYMBOLS = ("H", "H")


def _request(tmp_path: Path, **overrides: Any) -> TaskRequest:
    payload: dict[str, Any] = {
        "task": TaskKind.MD_SAMPLING,
        "structure": StructureInput(coordinates=_H2, symbols=_SYMBOLS),
        "options": MdSamplingOptions(),
        "output_dir": tmp_path / "run",
    }
    payload.update(overrides)
    return TaskRequest(**payload)


def _write_trajectory(path: Path, n_frames: int) -> Path:
    lines: list[str] = []
    for index in range(n_frames):
        lines.extend(
            [
                "2",
                f"frame {index}",
                f"H 0.0 0.0 {index * 0.05:.3f}",
                f"H 0.0 0.0 {0.74 + index * 0.05:.3f}",
            ]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class _FakeMolclusBackend:
    """Molclus-shaped fake: ``run_md`` path entry (QCResult)."""

    name = "molclus"

    def __init__(self, factory: Any) -> None:
        self._factory = factory
        self.calls: list[dict[str, Any]] = []

    def run_md(self, initial_xyz: Any, **kwargs: Any) -> Any:
        target = Path(kwargs["output_dir"]) if kwargs.get("output_dir") else Path.cwd()
        self.calls.append({"initial_xyz": Path(initial_xyz), **kwargs})
        return self._factory(target)


# ── independent cccp call: success state ───────────────────────────────


def test_success_maps_trajectory_and_frame_count(tmp_path: Path) -> None:
    def factory(target: Path) -> QCResult:
        traj = _write_trajectory(target / "traj.xyz", 4)
        return QCResult(
            success=True,
            converged=True,
            output_file=traj,
            metadata={"trajectory_file": str(traj), "n_frames": 4},
        )

    backend = _FakeMolclusBackend(factory)
    result = run_md_sampling(
        _request(
            tmp_path,
            options=MdSamplingOptions(
                md_method="gfn2",
                gfn_level=2,
                temperature_k=400.0,
                time_ps=50.0,
                dump_fs=100.0,
                step_fs=1.0,
                hmass=1.0,
                shake=True,
                nvt=True,
                seed=7,
            ),
        ),
        context=TaskContext(backend=backend),
    )

    assert result.status == "completed"
    assert result.complete is True
    payload = result.payload
    assert isinstance(payload, MdSamplingPayload)
    assert payload.n_frames == 4
    assert payload.trajectory_ref is not None
    assert payload.trajectory_ref.type == "trajectory"
    assert payload.trajectory_ref.path.is_file()
    assert result.metadata["n_frames"] == 4

    call = backend.calls[0]
    assert call["initial_xyz"].is_file()
    assert call["md_method"] == "gfn2"
    assert call["gfn_level"] == 2
    assert call["temperature"] == pytest.approx(400.0)
    assert call["time_ps"] == pytest.approx(50.0)
    assert call["seed"] == 7
    assert call["nvt"] is True


# ── independent cccp call: partial-failure state ───────────────────────


def test_partial_keeps_valid_frame_prefix_at_original_indices(tmp_path: Path) -> None:
    def factory(target: Path) -> QCResult:
        traj = _write_trajectory(target / "traj.xyz", 3)
        return QCResult(
            success=False,
            error_message="xTB-MD trajectory invalid: only 3 frames (minimum 10 frames)",
            output_file=traj,
        )

    backend = _FakeMolclusBackend(factory)
    result = run_md_sampling(_request(tmp_path), context=TaskContext(backend=backend))

    assert result.status == "failed"
    assert result.complete is False
    assert any("only 3 frames" in error for error in result.errors)
    payload = result.payload
    assert isinstance(payload, MdSamplingPayload)
    assert payload.n_frames == 3
    assert payload.trajectory_ref is not None
    assert payload.trajectory_ref.path.is_file()


# ── independent cccp call: empty state ─────────────────────────────────


@pytest.mark.parametrize("with_empty_file", [False, True])
def test_zero_frames_is_failed_backend_failure(tmp_path: Path, with_empty_file: bool) -> None:
    def factory(target: Path) -> QCResult:
        if not with_empty_file:
            return QCResult(success=False, error_message="xTB-MD completed without xtb.trj")
        traj = _write_trajectory(target / "traj.xyz", 0)
        return QCResult(success=True, output_file=traj, metadata={"n_frames": 0})

    backend = _FakeMolclusBackend(factory)
    result = run_md_sampling(_request(tmp_path), context=TaskContext(backend=backend))

    assert result.status == "failed"
    assert result.complete is False
    assert result.error_kind is ErrorKind.BACKEND_FAILURE
    assert result.errors


# ── envelope validation ────────────────────────────────────────────────


def test_wrong_options_type_rejected(tmp_path: Path) -> None:
    backend = _FakeMolclusBackend(lambda target: QCResult(success=False))
    request = _request(tmp_path, options=ClusteringOptions())
    with pytest.raises(TaskInputError):
        run_md_sampling(request, context=TaskContext(backend=backend))


def test_wrong_task_kind_rejected(tmp_path: Path) -> None:
    backend = _FakeMolclusBackend(lambda target: QCResult(success=False))
    request = _request(tmp_path, task=TaskKind.CLUSTERING, options=ClusteringOptions())
    with pytest.raises(TaskInputError):
        run_md_sampling(request, context=TaskContext(backend=backend))
