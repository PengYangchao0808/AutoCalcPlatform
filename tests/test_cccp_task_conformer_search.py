"""Pure-cccp conformer_search task tests (plan todo 42) — never import acp.

Independent call surface: ``run_conformer_search(request, context=…)``
drives a fake conformer-search backend through the same
validate → select → translate → execute → typed-result pipeline as the real
CREST entry; the three contract states (success / partial / empty) are
asserted against ``P2_TASK_CONTRACTS`` semantics.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from cccp.calculation.context import TaskContext
from cccp.calculation.errors import TaskInputError
from cccp.calculation.requests import (
    ConformerSearchOptions,
    MdSamplingOptions,
    StructureInput,
    TaskKind,
    TaskRequest,
)
from cccp.calculation.results import ConformerSearchPayload, ErrorKind
from cccp.calculation.tasks.conformer_search import run_conformer_search
from cccp.qc.interfaces.base import QCResult

_H2 = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.74))
_SYMBOLS = ("H", "H")


def _request(tmp_path: Path, **overrides: Any) -> TaskRequest:
    payload: dict[str, Any] = {
        "task": TaskKind.CONFORMER_SEARCH,
        "structure": StructureInput(coordinates=_H2, symbols=_SYMBOLS),
        "options": ConformerSearchOptions(),
        "output_dir": tmp_path / "run",
    }
    payload.update(overrides)
    return TaskRequest(**payload)


def _write_ensemble(path: Path, energies: list[float | None]) -> Path:
    lines: list[str] = []
    for index, energy in enumerate(energies):
        title = f"Frame {index} | Energy: {energy:.10f}" if energy is not None else f"Frame {index}"
        lines.extend(
            [
                "2",
                title,
                f"H 0.0 0.0 {index * 0.1:.3f}",
                f"H 0.0 0.0 {0.74 + index * 0.1:.3f}",
            ]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class _FakeCrestBackend:
    """CREST-shaped fake: ``run_conformer_search`` geometry entry (QCResult)."""

    name = "crest"

    def __init__(self, factory: Any) -> None:
        self._factory = factory
        self.calls: list[dict[str, Any]] = []

    def run_conformer_search(
        self, coordinates: Any, symbols: Any, output_dir: Any = None, **kwargs: Any
    ) -> Any:
        target = Path(output_dir) if output_dir is not None else Path.cwd()
        self.calls.append(
            {
                "coordinates": coordinates,
                "symbols": list(symbols),
                "output_dir": target,
                **kwargs,
            }
        )
        return self._factory(target)


class _FakeSearchBackend:
    """ConformerSearcher-shaped fake: ``search`` path entry (returns a Path)."""

    name = "molclus"

    def __init__(self, factory: Any) -> None:
        self._factory = factory
        self.calls: list[dict[str, Any]] = []

    def search(self, initial_xyz: Any, output_dir: Any = None, **kwargs: Any) -> Any:
        target = Path(output_dir) if output_dir is not None else Path.cwd()
        self.calls.append({"initial_xyz": Path(initial_xyz), "output_dir": target, **kwargs})
        return self._factory(target)


# ── independent cccp call: success state ───────────────────────────────


def test_success_returns_ensemble_count_and_energy_table(tmp_path: Path) -> None:
    def factory(target: Path) -> QCResult:
        ensemble = _write_ensemble(target / "crest_conformers.xyz", [-100.2, -100.1, -100.0])
        return QCResult(success=True, output_file=ensemble, metadata={"n_conformers": 3})

    backend = _FakeCrestBackend(factory)
    result = run_conformer_search(
        _request(tmp_path, options=ConformerSearchOptions(energy_window=6.0, gfn_level=2)),
        context=TaskContext(backend=backend),
    )

    assert result.status == "completed"
    assert result.complete is True
    assert result.errors == ()
    payload = result.payload
    assert isinstance(payload, ConformerSearchPayload)
    assert payload.conformer_count == 3
    assert payload.ensemble_ref is not None
    assert payload.ensemble_ref.type == "ensemble"
    assert payload.ensemble_ref.path.is_file()
    assert payload.ensemble_ref.checksum.startswith("sha256:")
    assert [row.frame_index for row in payload.energy_table] == [0, 1, 2]
    assert [row.conf_id for row in payload.energy_table] == ["conf_0", "conf_1", "conf_2"]
    assert payload.energy_table[1].energy_hartree == pytest.approx(-100.1)
    assert result.metadata["conformer_count"] == 3

    call = backend.calls[0]
    assert call["energy_window"] == pytest.approx(6.0)
    assert call["gfn_level"] == 2
    assert call["charge"] == 0
    assert call["multiplicity"] == 1


def test_path_shaped_search_entry_also_produces_payload(tmp_path: Path) -> None:
    def factory(target: Path) -> Path:
        return _write_ensemble(target / "ensemble.xyz", [-10.0, -9.5])

    backend = _FakeSearchBackend(factory)
    result = run_conformer_search(_request(tmp_path), context=TaskContext(backend=backend))

    assert result.status == "completed"
    assert result.complete is True
    payload = result.payload
    assert isinstance(payload, ConformerSearchPayload)
    assert payload.conformer_count == 2
    assert payload.ensemble_ref is not None
    assert payload.ensemble_ref.path.name == "ensemble.xyz"
    assert backend.calls[0]["initial_xyz"].is_file()


# ── independent cccp call: partial-failure state ───────────────────────


def test_partial_keeps_valid_conformers_at_original_indices(tmp_path: Path) -> None:
    def factory(target: Path) -> QCResult:
        ensemble = target / "crest_conformers.xyz"
        ensemble.parent.mkdir(parents=True, exist_ok=True)
        ensemble.write_text(
            "\n".join(
                [
                    "2",
                    "Frame 0 | Energy: -100.2000000000",
                    "H 0.0 0.0 0.0",
                    "H 0.0 0.0 0.74",
                    "2",
                    "Frame 1 | Energy: -100.1000000000",
                    "CORRUPT COORDINATE LINE",
                    "H 0.0 0.0 0.84",
                    "2",
                    "Frame 2 | Energy: -100.0000000000",
                    "H 0.0 0.0 0.2",
                    "H 0.0 0.0 0.94",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        return QCResult(success=False, error_message="CREST timed out", output_file=ensemble)

    backend = _FakeCrestBackend(factory)
    result = run_conformer_search(_request(tmp_path), context=TaskContext(backend=backend))

    assert result.status == "failed"
    assert result.complete is False
    assert any("CREST timed out" in error for error in result.errors)
    payload = result.payload
    assert isinstance(payload, ConformerSearchPayload)
    assert payload.conformer_count == 2
    assert [row.frame_index for row in payload.energy_table] == [0, 2]
    assert payload.energy_table[0].energy_hartree == pytest.approx(-100.2)


# ── independent cccp call: empty state ─────────────────────────────────


@pytest.mark.parametrize("with_empty_file", [False, True])
def test_zero_conformers_is_failed_backend_failure(tmp_path: Path, with_empty_file: bool) -> None:
    def factory(target: Path) -> QCResult:
        if not with_empty_file:
            return QCResult(success=False, error_message="CREST output not found in run dir")
        ensemble = _write_ensemble(target / "crest_conformers.xyz", [])
        return QCResult(success=True, output_file=ensemble, metadata={"n_conformers": 0})

    backend = _FakeCrestBackend(factory)
    result = run_conformer_search(_request(tmp_path), context=TaskContext(backend=backend))

    assert result.status == "failed"
    assert result.complete is False
    assert result.error_kind is ErrorKind.BACKEND_FAILURE
    assert result.errors
    assert not isinstance(result.payload, ConformerSearchPayload) or (
        result.payload.conformer_count == 0
    )


# ── envelope validation ────────────────────────────────────────────────


def test_wrong_task_kind_rejected(tmp_path: Path) -> None:
    backend = _FakeCrestBackend(lambda target: QCResult(success=False))
    request = _request(tmp_path, task=TaskKind.MD_SAMPLING, options=MdSamplingOptions())
    with pytest.raises(TaskInputError):
        run_conformer_search(request, context=TaskContext(backend=backend))


def test_wrong_options_type_rejected(tmp_path: Path) -> None:
    backend = _FakeCrestBackend(lambda target: QCResult(success=False))
    request = _request(tmp_path, options=MdSamplingOptions())
    with pytest.raises(TaskInputError):
        run_conformer_search(request, context=TaskContext(backend=backend))


# ── isolation guard (acceptance) ───────────────────────────────────────


def test_task_modules_do_not_reference_acp() -> None:
    tasks_dir = Path(__file__).resolve().parents[1] / "src" / "cccp" / "calculation" / "tasks"
    for module in ("conformer_search.py", "md_sampling.py", "clustering.py", "xtb_path_search.py"):
        tree = ast.parse((tasks_dir / module).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(not alias.name.startswith("acp") for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert node.module is None or not node.module.startswith("acp")
