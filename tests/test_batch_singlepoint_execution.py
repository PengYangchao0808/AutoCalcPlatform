"""Tests for prepared frame single-point execution signals."""

from __future__ import annotations

from pathlib import Path
from typing import final

import numpy as np
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

    assert events == [
        ("start", "frame_000", 0, 3),
        ("done", "1", 1, 3),
        ("start", "frame_001", 1, 3),
        ("done", "2", 2, 3),
        ("start", "frame_002", 2, 3),
        ("done", "3", 3, 3),
    ]
    assert [result[frame.frame_id].status for frame in frames] == [
        "completed",
        "completed",
        "failed",
    ]
    assert result["frame_001"].cache_hit is True
    assert backend.calls == [0, 2]
