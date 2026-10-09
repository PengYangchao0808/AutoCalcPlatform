"""Tests for prepared frame single-point execution signals."""

from __future__ import annotations

from pathlib import Path
from typing import final

import numpy as np
import pytest
from numpy.typing import NDArray

from acp.backends.base import QCResult
from acp.calculations.batch._singlepoint_execution import (
    BatchSinglePointExecutionOptions,
    run_prepared_frames,
)
from acp.calculations.batch._singlepoint_models import PreparedFrame


@final
class DeterministicBackend:
    """Fake single-point backend with deterministic frame outcomes."""

    def __init__(self, fail_indices: set[int]) -> None:
        self._fail_indices = frozenset(fail_indices)
        self.calls: list[int] = []

    def single_point(
        self,
        coordinates: NDArray[np.float64],
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: str | int | float | bool | Path | None,
    ) -> QCResult:
        del coordinates, symbols, charge, multiplicity
        output_name = kwargs.get("output_name")
        if not isinstance(output_name, str):
            raise TypeError("output_name must be a string")
        frame_index = int(output_name.rsplit("_", 1)[1])
        self.calls.append(frame_index)
        if frame_index in self._fail_indices:
            raise RuntimeError(f"frame {frame_index} failed")

        output_root = output_dir or Path.cwd()
        output_root.mkdir(parents=True, exist_ok=True)
        output_file = output_root / f"{output_name}.out"
        _ = output_file.write_text(f"frame {frame_index}\n", encoding="utf-8")
        return QCResult(success=True, energy=-100.0 - frame_index, output_file=output_file)


def _prepared_frames() -> list[PreparedFrame]:
    return [
        PreparedFrame(
            frame_id=f"frame_{index:03d}",
            coordinates=np.array([[float(index), 0.0, 0.0]], dtype=np.float64),
            symbols=("H",),
            charge=0,
            multiplicity=1,
            cache_key=f"cache-key-{index}",
        )
        for index in range(3)
    ]


def _settings(output_dir: Path) -> BatchSinglePointExecutionOptions:
    return BatchSinglePointExecutionOptions(
        output_dir=output_dir,
        method="B97-3c",
        basis="def2-SVP",
        max_workers=1,
        solvent=None,
        cache=True,
        config=None,
        options={},
    )


def test_prepared_frames_emit_ordered_signals_for_cache_and_failure(tmp_path: Path) -> None:
    """Given mixed outcomes, signal each frame in start-then-done order."""
    frames = _prepared_frames()
    settings = _settings(tmp_path)

    seed_backend = DeterministicBackend({0, 2})
    seeded = run_prepared_frames(seed_backend, frames, settings)

    assert [seeded[frame.frame_id].status for frame in frames] == ["failed", "completed", "failed"]

    events: list[tuple[str, str, int, int]] = []

    def on_frame_start(frame_id: str, done: int, total: int) -> None:
        events.append(("start", frame_id, done, total))

    def on_progress(done: int, total: int) -> None:
        events.append(("done", str(done), done, total))

    backend = DeterministicBackend({2})
    result = run_prepared_frames(
        backend,
        frames,
        settings,
        progress_callback=on_progress,
        on_frame_start=on_frame_start,
    )

    starts = [(e[1], e[2]) for e in events if e[0] == "start"]
    dones = [(e[1], e[2]) for e in events if e[0] == "done"]
    assert starts == [("frame_000", 0), ("frame_001", 1), ("frame_002", 2)]
    assert dones == [("1", 1), ("2", 2), ("3", 3)]
    assert len(events) == 6
    assert [result[frame.frame_id].status for frame in frames] == [
        "completed",
        "completed",
        "failed",
    ]
    assert result["frame_001"].cache_hit is True
    assert backend.calls == [0, 2]


# ── plan todo 17: production batch path goes through the generic executor ─


def test_run_group_uses_cccp_batch_executor_with_run_singlepoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given the switched production path, the generic executor receives the
    single-task core ``run_singlepoint`` as its per-item execution function."""
    import acp.calculations.batch._singlepoint_execution as execution
    from cccp.calculation.batch import run_batch as real_run_batch

    frames = _prepared_frames()
    settings = _settings(tmp_path)

    run_batch_calls: list[object] = []
    execute_refs: list[object] = []
    task_calls: list[str] = []

    def spy_run_batch(entries, execute, **kwargs):
        run_batch_calls.append(entries)
        execute_refs.append(execute)
        return real_run_batch(entries, execute, **kwargs)

    real_task = execution.run_singlepoint

    def spy_task(request, *, context=None):
        task_calls.append(str(request.output_dir))
        return real_task(request, context=context)

    monkeypatch.setattr(execution, "run_batch", spy_run_batch)
    monkeypatch.setattr(execution, "run_singlepoint", spy_task)

    backend = DeterministicBackend(set())
    result = run_prepared_frames(backend, frames, settings)

    assert run_batch_calls, "_run_group must call the generic batch executor"
    assert execute_refs, "the generic executor must receive an execution function"
    assert len(task_calls) == 3, "every cache-miss frame goes through run_singlepoint"
    assert [result[frame.frame_id].status for frame in frames] == ["completed"] * 3
    assert [result[frame.frame_id].energy_hartree for frame in frames] == [
        -100.0,
        -101.0,
        -102.0,
    ]


def test_run_group_source_is_quarantined_from_legacy_batch() -> None:
    import ast

    source = (
        Path(__file__).resolve().parent.parent
        / "src"
        / "acp"
        / "calculations"
        / "batch"
        / "_singlepoint_execution.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(not alias.name.startswith("acp.backends.batch") for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("acp.backends.batch")
            if (node.module or "") == "acp.backends":
                assert all(alias.name != "batch" for alias in node.names)
        elif isinstance(node, ast.ClassDef):
            assert node.name != "_SignallingBackend"
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            assert name != "batch_single_point"


# ── plan todo 17: A8 cache-version rule for the Wave-0 recovery fixture ──


def test_batch_sp_cache_recovery_fixture_misses_under_versioned_cache(tmp_path: Path) -> None:
    """The pre-migration geometry-keyed cache record cannot prove
    compatibility with the versioned cache and is an explicit miss (A8)."""
    from cccp.calculation.batch import FileSystemCacheStore

    fixture_dir = (
        Path(__file__).resolve().parent
        / "baseline"
        / "recovery_fixtures"
        / "batch_sp_cache"
    )
    records = [p for p in fixture_dir.glob("*.json") if p.name != "cache_input.json"]
    assert records, "recovery fixture must carry a legacy cache record"
    store = FileSystemCacheStore(fixture_dir)
    for record in records:
        assert store.read(record.stem) is None, (
            "legacy geometry-keyed records are an explicit miss under the "
            "cache-v1 identity rule (documented in _singlepoint_execution)"
        )


# ── plan todo 17: fault injection on the publication contract ───────────


def test_fault_stored_scientific_result_recovery_retries_publish_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """科学结果已存 + 发布失败 → 恢复只重试发布、QC 调用不增."""
    import acp.calculations.result_publication as publication
    from acp.calculations.contracts import (
        CalculationRequest,
        JsonValue,
        StructureArtifact,
    )
    from acp.calculations.primitives.singlepoint import recover_singlepoint

    fake = DeterministicBackend(set())
    monkeypatch.setattr("cccp.backends.registry.get_backend", lambda name: fake)
    monkeypatch.setattr(
        "cccp.calculation.selection.resolve_executable",
        lambda name, configured_path=None: "/synthetic/orca",
    )

    result_dir = tmp_path / "RESULT"
    result_dir.mkdir()
    resources: dict[str, JsonValue] = {
        "backend": "orca",
        "coordinates": [[0.0, 0.0, 0.0]],
        "symbols": ["H"],
        "output_dir": str(result_dir),
        "output_name": "sp_0000",
    }
    request = CalculationRequest(
        input_artifact=StructureArtifact(path=tmp_path / "in.xyz", source="test"),
        method="r2SCAN-3c",
        resources=resources,
        workflow="test",
    )

    real_register = publication.register_result_manifest
    state = {"failures": 1}

    def flaky_register(result_dir_arg, manifest):
        if state["failures"]:
            state["failures"] -= 1
            raise OSError("injected manifest write failure")
        return real_register(result_dir_arg, manifest)

    monkeypatch.setattr(publication, "register_result_manifest", flaky_register)

    with pytest.raises(OSError):
        recover_singlepoint(request, result_dir=result_dir, result_id="sp-1")
    assert len(fake.calls) == 1, "the calculation runs once before the publish fault"
    assert publication.load_scientific_result(result_dir) is not None

    outcome = recover_singlepoint(request, result_dir=result_dir, result_id="sp-1")
    assert outcome.recovered is True
    assert len(fake.calls) == 1, "recovery must not re-execute QC"
    assert publication.load_publication_state(result_dir).complete is True
