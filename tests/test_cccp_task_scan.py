"""cccp scan task core — plan todo 20 (relaxed scan in cccp, products in ACP).

Pure-cccp tests (never import ``acp``): an independent call drives a
multi-coordinate relaxed scan and returns ``ScanPayload`` frames with their
ORIGINAL indices (failed frames never renumbered), keeps valid sub-results
on partial failure under the overall status rule, references per-frame
geometries / the energy profile as scientific artifacts, and writes NO
platform result manifest.  ``rigid`` / ``optimizer_level`` /
``single_point_level`` are absent from the v1 contract.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from cccp.calculation.context import TaskContext
from cccp.calculation.errors import TaskInputError
from cccp.calculation.requests import (
    MethodSpec,
    ScanCoordinateSpec,
    ScanMode,
    ScanOptions,
    StructureInput,
    TaskKind,
    TaskRequest,
)
from cccp.calculation.results import ErrorKind, ScanPayload
from cccp.calculation.tasks.scan import build_scan_plan, run_scan

_COORDS = ((0.0, 0.0, 0.0), (1.2, 0.0, 0.0), (0.0, 1.1, 0.0), (1.2, 1.1, 0.0))
_SYMBOLS = ("C", "C", "O", "H")


class _RecordingBackend:
    """Minimal ``relaxed_scan`` capability fake recording call kwargs."""

    name = "orca"

    def __init__(self, responses: list[object] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._responses = list(responses or [])

    def relaxed_scan(
        self,
        coordinates: Any,
        symbols: list[str],
        output_dir: Path | None = None,
        plan: Any = None,
        charge: int = 0,
        multiplicity: int = 1,
        **kwargs: Any,
    ) -> Any:
        self.calls.append(
            {
                "coordinates": np.asarray(coordinates, dtype=float),
                "symbols": list(symbols),
                "output_dir": output_dir,
                "plan": plan,
                "charge": charge,
                "multiplicity": multiplicity,
                "kwargs": dict(kwargs),
            }
        )
        if self._responses:
            response = self._responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            return response
        return _scan_result(4, failed=())


def _scan_result(points: int, *, failed: tuple[int, ...], message: str = "") -> Any:
    from cccp.qc.interfaces.xtb_scan import RelaxedScanPoint, RelaxedScanResult

    entries = []
    for index in range(points):
        ok = index not in failed
        entries.append(
            RelaxedScanPoint(
                frame_index=index,
                progress=index / max(points - 1, 1),
                coordinates=(np.asarray(_COORDS, dtype=float) + index * 0.05 if ok else None),
                symbols=list(_SYMBOLS) if ok else None,
                energy_hartree=(-100.0 - index * 0.1) if ok else None,
                success=ok,
                coordinate_values={"rc1": 1.2 + index * 0.4, "rc2": 0.5 + index * 0.333},
            )
        )
    return RelaxedScanResult(
        points=entries,
        input_xyz=Path("input.xyz"),
        scan_dir=Path("."),
        success=not failed,
        message=message,
    )


def _request(
    *,
    options: ScanOptions | None = None,
    output_dir: Path | None = None,
    backend: str | None = "orca",
    task: TaskKind = TaskKind.SCAN,
) -> TaskRequest:
    return TaskRequest(
        task=task,
        structure=StructureInput(coordinates=_COORDS, symbols=_SYMBOLS),
        charge=0,
        multiplicity=1,
        level=MethodSpec(method="r2SCAN-3c"),
        backend=backend,
        options=options,
        output_dir=output_dir,
    )


def _multi_options() -> ScanOptions:
    return ScanOptions(
        coordinates=(
            ScanCoordinateSpec(
                atoms=(0, 1), start=1.2, end=2.4, kind="distance", atom_index_base=0
            ),
            ScanCoordinateSpec(
                atoms=(3, 4), start=0.5, end=1.5, kind="distance", atom_index_base=1
            ),
        ),
        points=4,
    )


# ── happy path: multi-coordinate relaxed scan through one task call ────


def test_multi_coordinate_relaxed_scan_returns_frames_and_profile(tmp_path: Path) -> None:
    """Two drive coordinates, one call, typed frames + energy profile."""
    backend = _RecordingBackend()
    result = run_scan(
        _request(options=_multi_options(), output_dir=tmp_path),
        context=TaskContext(backend=backend),
    )

    assert result.status == "completed"
    assert result.complete is True
    assert result.errors == ()
    assert len(backend.calls) == 1
    call = backend.calls[0]
    assert call["charge"] == 0 and call["multiplicity"] == 1
    assert call["kwargs"].get("method") == "r2SCAN-3c"

    # the compiled plan carries 0-based atoms (atom_index_base honoured)
    plan = call["plan"]
    assert plan.points == 4
    assert [coordinate.id for coordinate in plan.coordinates] == ["rc1", "rc2"]
    assert plan.coordinates[0].atoms == (0, 1)
    assert plan.coordinates[1].atoms == (2, 3)

    # typed payload: per-frame energy curve with original indices
    assert isinstance(result.payload, ScanPayload)
    frames = result.payload.frames
    assert [frame.index for frame in frames] == [0, 1, 2, 3]
    assert [frame.energy_hartree for frame in frames] == [-100.0, -100.1, -100.2, -100.3]
    assert frames[0].values == (1.2, 0.5)
    assert frames[0].converged is True and frames[0].success is True

    # scientific artifacts: per-frame geometry + energy profile (no manifest)
    assert frames[0].geometry_ref is not None
    assert frames[0].geometry_ref.type == "frame_geometry"
    assert frames[0].geometry_ref.path == Path("scan_frame_000.xyz")
    assert (tmp_path / "scan_frame_000.xyz").is_file()
    assert (tmp_path / "scan_frame_003.xyz").is_file()
    assert result.payload.profile_ref is not None
    assert result.payload.profile_ref.type == "scan_profile"
    profile = json.loads((tmp_path / "scan_profile.json").read_text(encoding="utf-8"))
    assert profile["schema"] == "scan_profile_v1"
    assert profile["frame_count"] == 4
    assert profile["frames"][2]["energy_hartree"] == -100.2
    assert profile["frames"][1]["coordinate_values"] == {
        "rc1": 1.2 + 1 * 0.4,
        "rc2": 0.5 + 1 * 0.333,
    }

    # the scan step never writes a platform result manifest
    assert not any(path.name == "result_manifest.json" for path in tmp_path.rglob("*"))


def test_explicit_values_grid_drives_single_coordinate(tmp_path: Path) -> None:
    """``ScanOptions.values`` is the explicit grid of one coordinate."""
    options = ScanOptions(
        coordinates=(ScanCoordinateSpec(atoms=(0, 1), start=1.0, end=2.0, atom_index_base=0),),
        values=(1.0, 1.25, 1.5, 2.0),
    )
    backend = _RecordingBackend()
    result = run_scan(
        _request(options=options, output_dir=tmp_path),
        context=TaskContext(backend=backend),
    )

    assert result.status == "completed"
    plan = backend.calls[0]["plan"]
    assert plan.points == 4
    assert plan.coordinates[0].values == (1.0, 1.25, 1.5, 2.0)


# ── record identity + partial failure semantics (doc §"Record identity") ──


def test_partial_failure_keeps_valid_frames_and_original_indices(tmp_path: Path) -> None:
    """Failed frames keep their index; the status rule stays all-or-failed."""
    backend = _RecordingBackend(
        [_scan_result(4, failed=(1, 3), message="relaxed scan aborted: 2 of 4 frames failed")]
    )
    result = run_scan(
        _request(options=_multi_options(), output_dir=tmp_path),
        context=TaskContext(backend=backend),
    )

    # overall status rule: not every frame usable → failed, complete=False
    assert result.status == "failed"
    assert result.complete is False
    assert result.errors == ("relaxed scan aborted: 2 of 4 frames failed",)

    # valid sub-results are kept; failed frames never renumbered
    frames = result.payload.frames  # type: ignore[union-attr]
    assert [frame.index for frame in frames] == [0, 1, 2, 3]
    assert [frame.success for frame in frames] == [True, False, True, False]
    assert frames[1].geometry_ref is None
    assert frames[1].energy_hartree is None
    assert frames[2].energy_hartree == -100.2
    assert frames[2].geometry_ref is not None
    # best converged point still surfaces as the result energy
    assert result.energy_hartree == -100.2
    assert result.metadata["frame_count"] == 4
    assert result.metadata["successful_frame_count"] == 2
    # geometries exist only for usable frames
    assert (tmp_path / "scan_frame_002.xyz").is_file()
    assert not (tmp_path / "scan_frame_001.xyz").exists()


def test_backend_exception_returns_structured_failure() -> None:
    backend = _RecordingBackend([RuntimeError("scan exploded")])
    result = run_scan(_request(options=_multi_options()), context=TaskContext(backend=backend))

    assert result.status == "failed"
    assert result.complete is False
    assert result.error_kind is ErrorKind.BACKEND_FAILURE
    assert result.errors == ("scan exploded",)


def test_unsupported_result_type_returns_structured_failure(tmp_path: Path) -> None:
    from cccp.qc.interfaces.base import QCResult

    backend = _RecordingBackend(
        [QCResult(success=True, energy=-1.0, symbols=["C"], converged=True)]
    )
    result = run_scan(
        _request(options=_multi_options(), output_dir=tmp_path),
        context=TaskContext(backend=backend),
    )

    assert result.status == "failed"
    assert result.errors == ("relaxed_scan returned an unsupported result type",)
    assert not (tmp_path / "scan_profile.json").exists()


# ── contract validation ────────────────────────────────────────────────


def test_request_validation_rejects_wrong_task() -> None:
    request = _request(task=TaskKind.SINGLEPOINT, options=None)
    with pytest.raises(TaskInputError, match="run_scan requires task 'scan'"):
        run_scan(request, context=TaskContext(backend=_RecordingBackend()))


def test_request_validation_rejects_wrong_options() -> None:
    from cccp.calculation.requests import FrequencyOptions

    request = _request(options=FrequencyOptions())
    with pytest.raises(TaskInputError, match="expected ScanOptions"):
        run_scan(request, context=TaskContext(backend=_RecordingBackend()))


def test_atom_index_out_of_range_rejected_before_backend(tmp_path: Path) -> None:
    options = ScanOptions(
        coordinates=(ScanCoordinateSpec(atoms=(0, 9), start=1.0, end=2.0, atom_index_base=0),),
        points=3,
    )
    backend = _RecordingBackend()
    with pytest.raises(TaskInputError, match=r"atom index 9"):
        run_scan(
            _request(options=options, output_dir=tmp_path),
            context=TaskContext(backend=backend),
        )
    assert backend.calls == []


def test_scan_v1_contract_has_no_rigid_or_split_levels() -> None:
    """``rigid`` / ``optimizer_level`` / ``single_point_level`` stay out of v1."""
    assert set(mode.value for mode in ScanMode) == {"relaxed"}
    with pytest.raises(TaskInputError):
        ScanOptions.from_dict({"mode": "rigid"})
    options = ScanOptions.from_dict({"optimizer_level": "tight", "single_point_level": "loose"})
    assert options == ScanOptions()
    assert not hasattr(options, "optimizer_level")
    assert not hasattr(options, "single_point_level")


def test_raw_plan_mapping_keeps_full_fidelity(tmp_path: Path) -> None:
    """A legacy ``scan_plan`` mapping wins over the typed options (todo 25 seam)."""
    raw_plan = {
        "coordinates": [
            {"id": "d1", "kind": "distance", "atoms": [0, 1], "start": 1.2, "end": 2.4}
        ],
        "points": 3,
    }
    backend = _RecordingBackend([_scan_result(3, failed=())])
    result = run_scan(
        _request(options=ScanOptions(), output_dir=tmp_path),
        context=TaskContext(backend=backend, capability_extras={"scan_plan": raw_plan}),
    )

    assert result.status == "completed"
    plan = backend.calls[0]["plan"]
    assert plan.points == 3
    assert [coordinate.id for coordinate in plan.coordinates] == ["d1"]
    assert "scan_plan" not in backend.calls[0]["kwargs"]


# ── plan compilation is the single shared implementation ───────────────


def test_build_scan_plan_rejects_points_below_two() -> None:
    options = ScanOptions(
        coordinates=(ScanCoordinateSpec(atoms=(0, 1), start=1.0, end=2.0, atom_index_base=0),),
        points=1,
    )
    with pytest.raises(TaskInputError, match="scan_points must be an integer >= 2"):
        build_scan_plan(options)


def test_build_scan_plan_requires_a_coordinate() -> None:
    with pytest.raises(TaskInputError, match="scan requires at least one coordinate"):
        build_scan_plan(ScanOptions())
