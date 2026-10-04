"""Smoke check: cross-version recovery fixtures load with the CURRENT code.

Read-only; exercises the same readers recovery paths use.  Fixture semantics
and the compat checks owed by later migration todos are documented in
``tests/baseline/recovery_fixtures/README.md``.
"""

from __future__ import annotations

import json
from pathlib import Path

from acp.backends.batch import _read_cache
from acp.calculations.batch._manifest import BatchCalculationManifest
from acp.calculations.checkpoint import load_checkpoint
from acp.compat.legacy.manifests import read_batch_calculation_manifest
from acp.results.remote_structure_cache import RemoteStructureCache
from acp.storage.manifest import ResultManifest

FIXTURES = Path(__file__).resolve().parent / "baseline" / "recovery_fixtures"


def test_checkpoint_mixed_steps_loadable() -> None:
    root = FIXTURES / "checkpoint_mixed"
    meta = json.loads((root / "plan_fingerprint.json").read_text(encoding="utf-8"))
    checkpoint = load_checkpoint(root / "WORK" / "00_RUNTIME", meta["plan_fingerprint"])
    assert checkpoint is not None
    statuses = [state.get("status") for state in checkpoint.step_states]
    assert statuses.count("completed") == 2
    assert "failed" in statuses and "pending" in statuses
    assert checkpoint.items_state["__handoff__"]["symbols"] == ["O", "H", "H"]


def test_partial_failure_results_loadable() -> None:
    manifest = ResultManifest.read(FIXTURES / "partial_failure" / "RESULT")
    assert manifest.status == "failed"
    assert {product.kind.value for product in manifest.products} == {"structure", "energy_report"}

    batch = BatchCalculationManifest.read(
        FIXTURES / "partial_failure" / "batch_items_manifest.json"
    )
    assert batch is not None
    statuses = [item.status for item in batch.items]
    assert statuses.count("completed") == 2
    assert statuses.count("failed") == 1
    failed = next(item for item in batch.items if item.status == "failed")
    assert failed.error


def test_historical_manifests_loadable() -> None:
    v2 = ResultManifest.read(FIXTURES / "historical_manifest" / "RESULT_v2")
    assert v2.version == 2
    assert v2.products and v2.products[0].path == "reports/fixture_sp_energy.json"

    legacy = read_batch_calculation_manifest(
        FIXTURES / "historical_manifest" / "batch_calculation_manifest_v1.json"
    )
    assert legacy is not None
    assert legacy["schema_version"] == "batch_calculation_v1"


def test_batch_sp_cache_entry_loadable() -> None:
    root = FIXTURES / "batch_sp_cache"
    meta = json.loads((root / "cache_input.json").read_text(encoding="utf-8"))
    cache_path = root / f"{meta['cache_key']}.json"
    assert cache_path.is_file()
    record = _read_cache(cache_path, 0, meta["coordinates"], root)
    assert record is not None
    assert record.cache_hit is True
    assert record.energy_hartree == -76.4
    assert record.output_path == root / "frames" / "frame_000" / "sp_output.log"


def test_remote_path_reference_loadable(tmp_path: Path) -> None:
    payload = json.loads(
        (FIXTURES / "remote_path_reference" / "remote_job_paths.json").read_text(encoding="utf-8")
    )
    assert payload["storage_mode"] == "sftp"
    cache = RemoteStructureCache(run_root=tmp_path)
    for rel_paths in payload["cache_rel_paths"].values():
        assert rel_paths
        for rel_path in rel_paths:
            resolved = cache.cache_path(payload["job_id"], rel_path)
            assert str(resolved).startswith(str(cache.job_root(payload["job_id"])))


def test_checkpoint_mixed_optimize_step_fingerprint_compatible() -> None:
    """Todo 18 switch: the optimize-capability recovery subset stays valid.

    The plan fingerprint consumes ``str(step.spec)`` over the legacy plan
    contract; the typed OptimizeOptions rewrite must not change it, the
    stored fingerprint must still validate, and the completed optimize step
    must stay completed (continue never recomputes completed steps).
    """
    from acp.calculations.contracts import (
        CalculationPlan,
        CalculationStep,
        OptimizationMode,
        StepKind,
    )
    from acp.calculations.executor import _plan_fingerprint

    root = FIXTURES / "checkpoint_mixed"
    meta = json.loads((root / "plan_fingerprint.json").read_text(encoding="utf-8"))
    plan = CalculationPlan(
        workflow="BatchOptimize",
        profile="opt_freq",
        items=[{"path": "structures/input.xyz"}],
        steps=[
            CalculationStep(
                kind=StepKind.OPTIMIZE,
                mode=OptimizationMode.UNCONSTRAINED,
                spec={"max_cycles": 5},
            ),
            CalculationStep(
                kind=StepKind.FREQUENCY,
                mode=OptimizationMode.UNCONSTRAINED,
                spec=None,
            ),
        ],
    )
    assert _plan_fingerprint(plan) == meta["plan_fingerprint"]
    checkpoint = load_checkpoint(root / "WORK" / "00_RUNTIME", meta["plan_fingerprint"])
    assert checkpoint is not None
    optimize_state = next(
        state for state in checkpoint.step_states if state.get("kind") == "optimize"
    )
    assert optimize_state["status"] == "completed"
    assert optimize_state["energy"] == -100.75
