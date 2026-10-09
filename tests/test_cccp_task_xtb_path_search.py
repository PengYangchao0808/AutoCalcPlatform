"""Pure-cccp xtb_path_search task tests (plan todo 42) — never import acp.

Independent call surface: ``run_xtb_path_search(request, context=…)``
consumes the CCCP typed envelope (structure pair + typed options + scoped
``path_inp_text``/``extra_args`` fragments) against a fake xTB PATH backend;
success / partial / empty semantics follow ``P2_TASK_CONTRACTS`` (trajectory
/ frames / endpoint indices).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cccp.calculation.context import TaskContext
from cccp.calculation.errors import TaskInputError
from cccp.calculation.requests import (
    BackendInputFragment,
    BackendInputKind,
    FragmentConflictRule,
    StructureInput,
    TaskKind,
    TaskRequest,
    XtbPathSearchOptions,
)
from cccp.calculation.results import ErrorKind, XtbPathSearchPayload
from cccp.calculation.tasks.xtb_path_search import run_xtb_path_search
from cccp.qc.interfaces.xtb_path import PathSearchResult

_START = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.74))
_END = ((0.0, 0.0, 0.0), (0.0, 0.0, 3.00))
_SYMBOLS = ("H", "H")


def _path_fragment(content: str) -> BackendInputFragment:
    return BackendInputFragment(
        kind=BackendInputKind.PATH_INP_TEXT,
        source="recipe.path_inp_text",
        content=content,
        conflict_rule=FragmentConflictRule.STRUCTURED_FIELDS_WIN,
    )


def _args_fragment(args: tuple[str, ...]) -> BackendInputFragment:
    return BackendInputFragment(
        kind=BackendInputKind.EXTRA_ARGS,
        source="recipe.extra_args",
        content=args,
        conflict_rule=FragmentConflictRule.STRUCTURED_FIELDS_WIN,
    )


def _request(tmp_path: Path, **overrides: Any) -> TaskRequest:
    options: XtbPathSearchOptions = overrides.pop(
        "options",
        XtbPathSearchOptions(end_structure=StructureInput(coordinates=_END, symbols=_SYMBOLS)),
    )
    payload: dict[str, Any] = {
        "task": TaskKind.XTB_PATH_SEARCH,
        "structure": StructureInput(coordinates=_START, symbols=_SYMBOLS),
        "options": options,
        "output_dir": tmp_path / "run",
    }
    payload.update(overrides)
    return TaskRequest(**payload)


def _frame_file(path: Path, z: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"2\n\nH 0.0 0.0 0.0\nH 0.0 0.0 {z:.3f}\n", encoding="utf-8")
    return path


class _FakeXtbBackend:
    """xTB-shaped fake: ``path_search`` pair entry (PathSearchResult)."""

    name = "xtb"

    def __init__(self, factory: Any) -> None:
        self._factory = factory
        self.calls: list[dict[str, Any]] = []

    def path_search(self, start_xyz: Any, end_xyz: Any, output_dir: Any, **kwargs: Any) -> Any:
        self.calls.append(
            {
                "start_xyz": Path(start_xyz),
                "end_xyz": Path(end_xyz),
                "output_dir": Path(output_dir),
                **kwargs,
            }
        )
        return self._factory(Path(output_dir))


def _success_factory(target: Path) -> PathSearchResult:
    frames = [
        _frame_file(target / "path_frames" / f"path_frame_{i:03d}.xyz", 0.74 + i * 1.1)
        for i in range(3)
    ]
    trajectory = target / "xtbpath.xyz"
    trajectory.write_text("path trajectory", encoding="utf-8")
    return PathSearchResult(
        frame_paths=frames,
        energies_hartree=[-10.2, -9.8, -10.1],
        success=True,
        trajectory_file=trajectory,
        stdout_file=target / "xtb_path.stdout.log",
        stderr_file=target / "xtb_path.stderr.log",
    )


# ── independent cccp call: success state ───────────────────────────────


def test_success_maps_frames_endpoints_and_fragments(tmp_path: Path) -> None:
    backend = _FakeXtbBackend(_success_factory)
    result = run_xtb_path_search(
        _request(
            tmp_path,
            options=XtbPathSearchOptions(
                end_structure=StructureInput(coordinates=_END, symbols=_SYMBOLS),
                gfn_level=2,
                uhf=1,
                seed=7,
                backend_inputs=(
                    _path_fragment("$path\n   nrun=1\n$end"),
                    _args_fragment(("--etemp", "300.0", "--flag")),
                ),
            ),
        ),
        context=TaskContext(backend=backend),
    )

    assert result.status == "completed"
    assert result.complete is True
    payload = result.payload
    assert isinstance(payload, XtbPathSearchPayload)
    assert [frame.index for frame in payload.frames] == [0, 1, 2]
    assert payload.frames[1].energy_hartree == pytest.approx(-9.8)
    assert payload.start_frame_index == 0
    assert payload.end_frame_index == 2
    assert payload.trajectory_ref is not None
    assert payload.trajectory_ref.type == "trajectory"
    assert payload.trajectory_ref.path.is_file()

    call = backend.calls[0]
    assert call["start_xyz"].is_file()
    assert call["end_xyz"].is_file()
    assert call["gfn_level"] == 2
    assert call["uhf"] == 1
    assert call["seed"] == 7
    assert call["charge"] == 0
    assert call["multiplicity"] == 1
    assert call["path_inp_text"] == "$path\n   nrun=1\n$end"
    assert call["extra_args"] == ("--etemp", "300.0", "--flag")


def test_structured_fields_win_over_conflicting_extra_args(tmp_path: Path) -> None:
    backend = _FakeXtbBackend(_success_factory)
    result = run_xtb_path_search(
        _request(
            tmp_path,
            options=XtbPathSearchOptions(
                end_structure=StructureInput(coordinates=_END, symbols=_SYMBOLS),
                gfn_level=2,
                backend_inputs=(_args_fragment(("--gfn", "1", "--keep")),),
            ),
        ),
        context=TaskContext(backend=backend),
    )

    assert result.status == "completed"
    assert backend.calls[0]["gfn_level"] == 2
    assert backend.calls[0]["extra_args"] == ("--keep",)


# ── independent cccp call: partial-failure state ───────────────────────


def test_partial_keeps_valid_frames_at_original_indices(tmp_path: Path) -> None:
    def factory(target: Path) -> PathSearchResult:
        frames = [
            _frame_file(target / "path_frames" / f"path_frame_{i:03d}.xyz", 0.74 + i)
            for i in range(2)
        ]
        return PathSearchResult(
            frame_paths=frames,
            energies_hartree=[-10.2, None],
            success=False,
            error_message="xTB path search failed with return code 1",
        )

    backend = _FakeXtbBackend(factory)
    result = run_xtb_path_search(_request(tmp_path), context=TaskContext(backend=backend))

    assert result.status == "failed"
    assert result.complete is False
    assert any("return code 1" in error for error in result.errors)
    payload = result.payload
    assert isinstance(payload, XtbPathSearchPayload)
    assert [frame.index for frame in payload.frames] == [0, 1]
    assert payload.frames[0].energy_hartree == pytest.approx(-10.2)
    assert payload.start_frame_index == 0
    assert payload.end_frame_index == 1


# ── independent cccp call: empty state ─────────────────────────────────


def test_zero_frames_is_failed_backend_failure(tmp_path: Path) -> None:
    backend = _FakeXtbBackend(
        lambda target: PathSearchResult(
            frame_paths=[],
            energies_hartree=[],
            success=False,
            error_message="xTB path search failed with return code 1",
        )
    )
    result = run_xtb_path_search(_request(tmp_path), context=TaskContext(backend=backend))

    assert result.status == "failed"
    assert result.complete is False
    assert result.error_kind is ErrorKind.BACKEND_FAILURE
    assert result.errors


# ── envelope validation ────────────────────────────────────────────────


def test_missing_end_structure_rejected_by_envelope(tmp_path: Path) -> None:
    backend = _FakeXtbBackend(_success_factory)
    request = _request(tmp_path, options=XtbPathSearchOptions())
    with pytest.raises(TaskInputError):
        run_xtb_path_search(request, context=TaskContext(backend=backend))


def test_wrong_task_kind_rejected(tmp_path: Path) -> None:
    from cccp.calculation.requests import ClusteringOptions

    backend = _FakeXtbBackend(_success_factory)
    request = _request(tmp_path, task=TaskKind.CLUSTERING, options=ClusteringOptions())
    with pytest.raises(TaskInputError):
        run_xtb_path_search(request, context=TaskContext(backend=backend))
