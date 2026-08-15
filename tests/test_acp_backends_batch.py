"""Tests for the batch single-point backend helper."""

# pyright: reportMissingImports=false, reportAttributeAccessIssue=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import final

import numpy as np
from numpy.typing import NDArray

from acp.backends.base import QCResult
from acp.backends.batch import batch_single_point


def _make_config(
    *,
    nproc: int = 1,
    method: str = "B97-3c",
    basis: str = "def2-mTZVP",
) -> dict[str, object]:
    return {
        "resources": {"nproc": nproc},
        "theory": {
            "single_point": {
                "method": method,
                "basis": basis,
            }
        },
    }


@final
class RecordingBackend:
    """Mock backend recording batch single-point calls."""

    def __init__(
        self,
        *,
        sleep_s: float = 0.0,
        fail_indices: set[int] | None = None,
        raise_indices: set[int] | None = None,
    ) -> None:
        self.sleep_s = sleep_s
        self.fail_indices = fail_indices or set()
        self.raise_indices = raise_indices or set()
        self.calls: list[dict[str, object]] = []
        self.call_count = 0
        self.max_active = 0
        self._active = 0
        self._lock = threading.Lock()

    def single_point(
        self,
        coordinates: NDArray[np.float64],
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: object,
    ) -> QCResult:
        output_name_obj = kwargs["output_name"]
        if not isinstance(output_name_obj, str):
            raise TypeError("output_name must be a string")
        output_name = output_name_obj
        frame_index = int(output_name.rsplit("_", 1)[1])

        with self._lock:
            self.call_count += 1
            self._active += 1
            self.max_active = max(self.max_active, self._active)

        try:
            if self.sleep_s > 0:
                time.sleep(self.sleep_s)

            output_root = output_dir or Path.cwd()
            output_root.mkdir(parents=True, exist_ok=True)
            output_file = output_root / f"{output_name}.out"
            _ = output_file.write_text(f"frame {frame_index}\n", encoding="utf-8")

            self.calls.append(
                {
                    "coordinates": np.array(coordinates, copy=True),
                    "symbols": list(symbols),
                    "charge": charge,
                    "multiplicity": multiplicity,
                    "output_dir": output_root,
                    "output_name": output_name,
                    "method": kwargs.get("method"),
                    "basis": kwargs.get("basis"),
                    "solvent": kwargs.get("solvent"),
                }
            )

            if frame_index in self.raise_indices:
                raise RuntimeError(f"boom {frame_index}")

            if frame_index in self.fail_indices:
                return QCResult(
                    success=False,
                    error_message=f"failed {frame_index}",
                    output_file=output_file,
                )

            return QCResult(
                success=True,
                energy=-100.0 - float(frame_index),
                output_file=output_file,
            )
        finally:
            with self._lock:
                self._active -= 1


def test_batch_single_point_runs_in_parallel_and_preserves_order(tmp_path: Path) -> None:
    backend = RecordingBackend(sleep_s=0.05)
    geometries = [
        np.array([[float(index), 0.0, 0.0], [0.0, 0.0, 0.7]], dtype=np.float64)
        for index in range(4)
    ]

    result = batch_single_point(
        backend,
        geometries,
        ["H", "H"],
        output_dir=tmp_path,
        config=_make_config(nproc=4),
    )

    assert [record.index for record in result.records] == [0, 1, 2, 3]
    assert all(
        record.structure_ref is geometry for record, geometry in zip(result.records, geometries)
    )
    assert [record.energy_hartree for record in result.records] == [-100.0, -101.0, -102.0, -103.0]
    assert backend.max_active >= 2
    assert result.n_total == 4
    assert result.n_success == 4
    assert result.n_failed == 0
    assert result.n_cache_hits == 0
    for index in range(4):
        assert (tmp_path / f"sp_{index:04d}").is_dir()


def test_batch_single_point_isolates_frame_failures(tmp_path: Path) -> None:
    backend = RecordingBackend(raise_indices={1})
    geometries = [np.zeros((1, 3), dtype=np.float64) + index for index in range(3)]

    result = batch_single_point(
        backend,
        geometries,
        ["H"],
        output_dir=tmp_path,
        max_workers=3,
    )

    assert result.n_total == 3
    assert result.n_success == 2
    assert result.n_failed == 1
    assert result.records[0].success is True
    assert result.records[1].success is False
    assert result.records[2].success is True
    assert result.records[1].energy_hartree is None
    assert result.records[1].cache_hit is False
    assert result.records[1].error_message == "boom 1"


def test_batch_single_point_uses_cache_on_repeat_call(tmp_path: Path) -> None:
    backend = RecordingBackend()
    geometries = [np.array([[0.0, 0.0, 0.0]], dtype=np.float64)]

    first = batch_single_point(
        backend,
        geometries,
        ["H"],
        output_dir=tmp_path,
        method="B97-3c",
        basis="def2-mTZVP",
    )
    second = batch_single_point(
        backend,
        geometries,
        ["H"],
        output_dir=tmp_path,
        method="B97-3c",
        basis="def2-mTZVP",
    )

    assert backend.call_count == 1
    assert first.records[0].cache_hit is False
    assert second.records[0].cache_hit is True
    assert second.n_cache_hits == 1
    assert second.records[0].output_path == first.records[0].output_path
    assert any((tmp_path / ".cache").iterdir())


def test_batch_single_point_passes_method_and_basis_from_config(tmp_path: Path) -> None:
    backend = RecordingBackend()

    result = batch_single_point(
        backend,
        [np.array([[0.0, 0.0, 0.0]], dtype=np.float64)],
        ["H"],
        output_dir=tmp_path,
        config=_make_config(method="r2SCAN-3c", basis="def2-SVP"),
        max_workers=1,
    )

    assert result.n_success == 1
    assert backend.calls[0]["method"] == "r2SCAN-3c"
    assert backend.calls[0]["basis"] == "def2-SVP"


def test_batch_single_point_handles_empty_geometry_list(tmp_path: Path) -> None:
    backend = RecordingBackend()

    result = batch_single_point(backend, [], ["H"], output_dir=tmp_path)

    assert result.records == []
    assert result.n_total == 0
    assert result.n_success == 0
    assert result.n_failed == 0
    assert result.n_cache_hits == 0
    assert result.wall_time_s >= 0.0
    assert backend.call_count == 0
