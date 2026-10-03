#!/usr/bin/env python3.11
"""Generate cross-version recovery fixtures from the CURRENT code's real formats.

Run:  python3.11 tests/baseline/recovery_fixtures/generate_fixtures.py

Fixtures are byte-stable: no timestamps, no machine-specific paths (the batch
manifest leaves created_at/updated_at empty).  Regenerating must produce zero
``git diff``.  See README.md for what each fixture is and what future tests
must verify.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

FIXTURES_DIR = Path(__file__).resolve().parent
REPO_ROOT = FIXTURES_DIR.parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from acp.backends.batch import _geometry_cache_key, _write_cache  # noqa: E402
from acp.calculations.batch._items import BatchCalculationItem  # noqa: E402
from acp.calculations.batch._manifest import BatchCalculationManifest  # noqa: E402
from acp.calculations.checkpoint import write_checkpoint  # noqa: E402
from acp.calculations.contracts import (  # noqa: E402
    CalculationPlan,
    CalculationResult,
    CalculationStep,
    Checkpoint,
    OptimizationMode,
    StepKind,
)
from acp.calculations.executor import StepState, _plan_fingerprint  # noqa: E402
from acp.results.remote_structure_cache import _CATALOG_FETCH_PATHS  # noqa: E402
from acp.storage.manifest import ProductKind, ResultManifest  # noqa: E402

FIXED_FINGERPRINT_PLAN = CalculationPlan(
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


def _dump(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def build_checkpoint_mixed() -> None:
    """(a) checkpoint.json: completed + incomplete steps coexisting."""
    fingerprint = _plan_fingerprint(FIXED_FINGERPRINT_PLAN)
    steps = [
        StepState(
            index=0,
            kind=StepKind.SINGLEPOINT,
            status="completed",
            result=CalculationResult(energy=-100.5, status="completed"),
        ),
        StepState(
            index=1,
            kind=StepKind.OPTIMIZE,
            status="completed",
            result=CalculationResult(energy=-100.75, status="completed"),
        ),
        StepState(
            index=2,
            kind=StepKind.FREQUENCY,
            status="failed",
            error="fixture: synthetic frequency failure",
        ),
        StepState(index=3, kind=StepKind.THERMOCHEMISTRY, status="pending"),
    ]
    checkpoint = Checkpoint(
        task_id="fixture_recovery",
        workflow="BatchOptimize",
        plan_fingerprint=fingerprint,
        step_states=[s.to_dict() for s in steps],
        items_state={
            "__handoff__": {
                "coords": [[0.0, 0.0, 0.0], [0.96, 0.0, 0.0], [-0.24, 0.93, 0.0]],
                "symbols": ["O", "H", "H"],
            }
        },
        attempts=1,
    )
    runtime = FIXTURES_DIR / "checkpoint_mixed" / "WORK" / "00_RUNTIME"
    write_checkpoint(runtime, checkpoint)
    _dump(
        FIXTURES_DIR / "checkpoint_mixed" / "plan_fingerprint.json",
        {
            "plan_fingerprint": fingerprint,
            "plan_repr": {
                "workflow": FIXED_FINGERPRINT_PLAN.workflow,
                "profile": FIXED_FINGERPRINT_PLAN.profile,
                "steps": [
                    {"kind": s.kind.value, "mode": s.mode.value, "spec": s.spec}
                    for s in FIXED_FINGERPRINT_PLAN.steps
                ],
                "items": ["structures/input.xyz"],
            },
        },
    )


def build_partial_failure() -> None:
    """(b) partially-failed results: surviving products + mixed batch items."""
    manifest = ResultManifest(
        task_id="fixture_partial_failure",
        workflow="BatchOptimize",
        status="failed",
        version=2,
    )
    manifest.add_product(
        "opt_geometry",
        "Optimized geometry (step completed before failure)",
        "structures/fixture_opt.xyz",
        ProductKind.STRUCTURE,
    )
    manifest.add_product(
        "energy_report",
        "Energy report (step completed before failure)",
        "reports/fixture_energy.json",
        ProductKind.ENERGY_REPORT,
    )
    manifest.write(FIXTURES_DIR / "partial_failure" / "RESULT")

    items = [
        BatchCalculationItem(
            item_id="item_ok_1",
            candidate_id="pes_ts_frame_001",
            name="fixture_ok_1",
            tag="TS",
            status="completed",
            cache_key="fixture-cache-key-ok-1",
        ),
        BatchCalculationItem(
            item_id="item_ok_2",
            candidate_id="pes_ts_frame_002",
            name="fixture_ok_2",
            tag="TS",
            status="completed",
            cache_key="fixture-cache-key-ok-2",
        ),
        BatchCalculationItem(
            item_id="item_fail_1",
            candidate_id="pes_ts_frame_003",
            name="fixture_fail_1",
            tag="TS",
            status="failed",
            error="fixture: synthetic optimization failure",
            cache_key="fixture-cache-key-fail-1",
        ),
    ]
    batch_manifest = BatchCalculationManifest(
        profile="opt_freq",
        items=items,
        workflow="BatchOptimize",
    )
    batch_manifest.write(FIXTURES_DIR / "partial_failure" / "batch_items_manifest.json")


def build_historical_manifest() -> None:
    """(c) historical manifests: pre-migration v2 + legacy compat-reader shape."""
    manifest = ResultManifest(
        task_id="fixture_historical",
        workflow="singlepoint",
        status="completed",
        version=2,
    )
    manifest.add_product(
        "sp_energy",
        "Single-point energy",
        "reports/fixture_sp_energy.json",
        ProductKind.ENERGY_REPORT,
    )
    manifest.write(FIXTURES_DIR / "historical_manifest" / "RESULT_v2")

    _dump(
        FIXTURES_DIR / "historical_manifest" / "batch_calculation_manifest_v1.json",
        {
            "kind": "batch_items_manifest",
            "schema_version": "batch_calculation_v1",
            "workflow": "BatchOptimize",
            "profile": "opt_freq",
            "created_at": "",
            "updated_at": "",
            "items": [
                {
                    "item_id": "hist_item_1",
                    "candidate_id": "hist_cand_1",
                    "name": "hist_1",
                    "tag": "TS",
                    "status": "completed",
                    "cache_key": "hist-cache-key-1",
                }
            ],
        },
    )


def build_batch_sp_cache() -> None:
    """(d) batch SP cache entry: <cache_key>.json as written by acp.backends.batch."""
    symbols = ["O", "H", "H"]
    coords = np.array(
        [[0.0, 0.0, 0.0], [0.96, 0.0, 0.0], [-0.24, 0.93, 0.0]],
        dtype=np.float64,
    )
    key = _geometry_cache_key(
        symbols,
        coords,
        charge=0,
        multiplicity=1,
        method="r2SCAN-3c",
        basis=None,
        solvent=None,
    )
    cache_dir = FIXTURES_DIR / "batch_sp_cache"
    output_rel = Path("frames") / "frame_000" / "sp_output.log"
    output_file = cache_dir / output_rel
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("fixture single-point output stub\n", encoding="utf-8")
    _write_cache(
        cache_dir / f"{key}.json",
        {"energy_hartree": -76.4, "output_ref": output_rel.as_posix()},
    )
    _dump(
        cache_dir / "cache_input.json",
        {
            "cache_key": key,
            "cache_key_algorithm": "acp.backends.batch._geometry_cache_key",
            "symbols": symbols,
            "coordinates": coords.tolist(),
            "charge": 0,
            "multiplicity": 1,
            "method": "r2SCAN-3c",
            "basis": None,
            "solvent": None,
        },
    )


def build_remote_path_reference() -> None:
    """(e) remote path reference: sftp job record + catalog rel_paths."""
    _dump(
        FIXTURES_DIR / "remote_path_reference" / "remote_job_paths.json",
        {
            "job_id": "fixture_remote_job",
            "remote_job_id": "424242",
            "node_id": "fixture-node",
            "storage_mode": "sftp",
            "remote_work_dir": "/remote/acp/runs/fixture_mol_fixture_task_remark",
            "cache_rel_paths": {
                workflow: list(paths) for workflow, paths in sorted(_CATALOG_FETCH_PATHS.items())
            },
        },
    )


def main() -> int:
    build_checkpoint_mixed()
    build_partial_failure()
    build_historical_manifest()
    build_batch_sp_cache()
    build_remote_path_reference()
    print(f"fixtures regenerated under {FIXTURES_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
