"""Regression tests: BatchOptimizeEngine request builders must carry ``config``.

``acp.calculations.primitives._common._backend_config`` reads backend
configuration only from ``request.resources["config"]``.  When the batch
builders dropped that key, ``ORCAInterface(config={})`` degraded executable
resolution to a PATH lookup — under the systemd service PATH that resolves
to the unrelated GNOME screen reader ``/usr/bin/orca``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from acp.calculations.batch import BatchMethodOptions, BatchStructureItem
from acp.calculations.batch.engine import BatchOptimizeEngine
from acp.calculations.contracts import CalculationRequest, CalculationResult
from acp.calculations.primitives._common import _backend_config

ENGINE_CONFIG: dict[str, Any] = {
    "executables": {"orca": {"path": "/opt/example/orca"}},
    "resources": {"nproc": 4},
}


def _engine(tmp_path: Path) -> BatchOptimizeEngine:
    return BatchOptimizeEngine(config=dict(ENGINE_CONFIG), work_root=tmp_path)


def _item() -> BatchStructureItem:
    return BatchStructureItem(
        item_id="item1",
        name="Item 1",
        xyz="2\n\nO 0.0 0.0 0.0\nH 0.0 0.0 0.96\n",
    )


def _opt_result() -> CalculationResult:
    return CalculationResult(energy=-76.0, coords=[[0.0, 0.0, 0.0], [0.0, 0.0, 0.96]])


def _build_requests(engine: BatchOptimizeEngine, tmp_path: Path) -> dict[str, CalculationRequest]:
    """Build one request per step kind (opt / freq / sp) from *engine*."""
    item = _item()
    opt = engine._build_opt_request(
        input_path=tmp_path / "input.xyz",
        is_ts=False,
        charge=0,
        multiplicity=1,
        output_dir=tmp_path / "opt",
        opt_kwargs={},
        methods=BatchMethodOptions(),
    )
    freq = engine._build_freq_request(
        opt_result=_opt_result(),
        item=item,
        charge=0,
        multiplicity=1,
        output_dir=tmp_path / "freq",
        symbols=["O", "H"],
        methods=BatchMethodOptions(),
    )
    sp = engine._build_sp_request(
        result=_opt_result(),
        item=item,
        charge=0,
        multiplicity=1,
        output_dir=tmp_path / "sp",
        symbols=["O", "H"],
        methods=BatchMethodOptions(),
    )
    return {"opt": opt, "freq": freq, "sp": sp}


@pytest.mark.parametrize("step", ["opt", "freq", "sp"])
def test_builder_carries_engine_config(tmp_path: Path, step: str) -> None:
    engine = _engine(tmp_path)

    request = _build_requests(engine, tmp_path)[step]

    assert request.resources["config"] == ENGINE_CONFIG


def test_backend_config_seam_receives_opt_config(tmp_path: Path) -> None:
    engine = _engine(tmp_path)

    request = _build_requests(engine, tmp_path)["opt"]

    assert _backend_config(request) == ENGINE_CONFIG


@pytest.mark.parametrize("step", ["opt", "freq", "sp"])
def test_builder_without_config_omits_config_resource(tmp_path: Path, step: str) -> None:
    engine = BatchOptimizeEngine(work_root=tmp_path)

    request = _build_requests(engine, tmp_path)[step]

    assert "config" not in request.resources


def test_resource_config_helper_returns_copy_or_empty(tmp_path: Path) -> None:
    configured = _engine(tmp_path)
    unconfigured = BatchOptimizeEngine(work_root=tmp_path)

    carried = configured._resource_config()
    configured._config["resources"]["nproc"] = 999

    assert carried == {"config": dict(ENGINE_CONFIG)}
    assert carried["config"] is not configured._config
    assert unconfigured._resource_config() == {}
