#!/usr/bin/env python3.11
"""Generate cross-version recovery fixtures from the CURRENT code's real formats.

Run:  python3.11 tests/baseline/recovery_fixtures/generate_fixtures.py

Fixtures are byte-stable: no timestamps, no machine-specific paths (the batch
manifest leaves created_at/updated_at empty; v2 identity is content-bound and
never hashes item paths).  Regenerating must produce zero ``git diff``.

Two write paths are deliberately separated (plan todo 14):

* **legacy v1 path (byte-frozen)** — the five original fixtures reproduce the
  generation-time bytes exactly.  ``checkpoint_mixed`` is written as the raw
  legacy payload: it predates ``identity_schema`` (todo 10), the
  ``attempts``→``resume_count`` rename (todo 11) and ``StepState.result_ref``
  (todo 12), so it must NEVER be routed through ``write_checkpoint`` /
  ``StepState.to_dict()``, whose current output carries those newer keys.
  ``_plan_fingerprint`` (the back-compat alias of ``legacy_plan_fingerprint``)
  stays importable here for the same reason.
* **v2 path (controlled regeneration)** — ``identity_schema=2`` checkpoints,
  durable ``step_result.json`` and publication-sequence states, written with
  the CURRENT production writers.  Regenerating these is allowed; they must
  stay reproducible (deterministic content only).

See README.md for what each fixture is and what future tests must verify.
"""

from __future__ import annotations

import json
import os
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
    ArtifactRef,
    CalculationPlan,
    CalculationResult,
    CalculationStep,
    Checkpoint,
    OptimizationMode,
    StepKind,
    StructureArtifact,
)
from acp.calculations.executor import (  # noqa: E402
    StepState,
    _plan_fingerprint,
    _step_id,
    _step_publication_manifest,
    _step_result_id,
    _step_scientific_record,
)
from acp.calculations.identity import compute_identity  # noqa: E402
from acp.calculations.result_publication import (  # noqa: E402
    publish_result,
    register_result_manifest,
    save_scientific_result,
)
from acp.calculations.step_result import STEP_RESULT_SCHEMA_VERSION, write_step_result  # noqa: E402
from acp.results.remote_structure_cache import _CATALOG_FETCH_PATHS  # noqa: E402
from acp.storage.manifest import ProductKind, ResultManifest  # noqa: E402
from cccp.version import __version__ as _platform_version  # noqa: E402

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

# ── shared deterministic content for the v2 fixtures ────────────────────────

V2_INPUT_XYZ = (
    "3\n"
    "v2 recovery fixture water\n"
    "O 0.000000 0.000000 0.000000\n"
    "H 0.957200 0.000000 0.000000\n"
    "H -0.239987 0.926627 0.000000\n"
)
V2_SP_OUTPUT_TEXT = "v2 fixture single-point output\n"
V2_TASK_ID = "fixture_recovery_v2"
V2_SP_ENERGY = -100.5


def _dump(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


# ══════════════════════════════════════════════════════════════════════════
# legacy (v1) write path — BYTE-FROZEN, do not route through current writers
# ══════════════════════════════════════════════════════════════════════════


def _write_frozen_v1_checkpoint(runtime: Path, payload: dict) -> None:
    """Write the legacy ``checkpoint.json`` byte shape directly.

    ``write_checkpoint`` always emits ``identity_schema`` and (via
    ``StepState.to_dict``) the V01/V02 fields — none of which exist in the
    frozen v1 bytes, so regeneration must bypass both.
    """
    path = runtime / "checkpoint.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(path.name + ".tmp")
    temporary_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary_path, path)


def build_checkpoint_mixed() -> None:
    """(a) legacy checkpoint.json: completed + incomplete steps coexisting."""
    fingerprint = _plan_fingerprint(FIXED_FINGERPRINT_PLAN)
    step_states = [
        {"index": 0, "kind": "singlepoint", "status": "completed", "error": "", "energy": -100.5},
        {"index": 1, "kind": "optimize", "status": "completed", "error": "", "energy": -100.75},
        {
            "index": 2,
            "kind": "frequency",
            "status": "failed",
            "error": "fixture: synthetic frequency failure",
            "energy": None,
        },
        {"index": 3, "kind": "thermochemistry", "status": "pending", "error": "", "energy": None},
    ]
    _write_frozen_v1_checkpoint(
        FIXTURES_DIR / "checkpoint_mixed" / "WORK" / "00_RUNTIME",
        {
            "attempts": 1,
            "items_state": {
                "__handoff__": {
                    "coords": [[0.0, 0.0, 0.0], [0.96, 0.0, 0.0], [-0.24, 0.93, 0.0]],
                    "symbols": ["O", "H", "H"],
                }
            },
            "plan_fingerprint": fingerprint,
            "step_states": step_states,
            "task_id": "fixture_recovery",
            "workflow": "BatchOptimize",
        },
    )
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


# ══════════════════════════════════════════════════════════════════════════
# v2 fixtures — controlled regeneration with the CURRENT production writers
# ══════════════════════════════════════════════════════════════════════════


def _v2_plan(root: Path) -> CalculationPlan:
    """The shared v2 fixture plan (SP → FREQ) bound to *root*'s input."""
    return CalculationPlan(
        workflow="optimize",
        profile="r2SCAN-3c",
        items=[StructureArtifact(path=root / "structures" / "input.xyz", elements=["O", "H", "H"])],
        steps=[
            CalculationStep(kind=StepKind.SINGLEPOINT),
            CalculationStep(kind=StepKind.FREQUENCY),
        ],
    )


def _write_v2_scaffold(root: Path) -> tuple[CalculationPlan, object]:
    """Input structure + ``identity.json`` (content-bound, path-free)."""
    root.mkdir(parents=True, exist_ok=True)
    xyz_path = root / "structures" / "input.xyz"
    xyz_path.parent.mkdir(parents=True, exist_ok=True)
    xyz_path.write_text(V2_INPUT_XYZ, encoding="utf-8")
    plan = _v2_plan(root)
    identity = compute_identity(plan)
    _dump(
        root / "identity.json",
        {
            "plan_identity": identity.plan_identity,
            "step_identities": list(identity.step_identities),
            "plan_repr": {
                "workflow": plan.workflow,
                "profile": plan.profile,
                "items": [{"path": "structures/input.xyz", "elements": ["O", "H", "H"]}],
                "steps": [
                    {"kind": s.kind.value, "mode": s.mode.value, "spec": s.spec} for s in plan.steps
                ],
            },
        },
    )
    return plan, identity


def _write_sp_step_result(root: Path, identity: object) -> dict[str, str]:
    """Contract C step ① for the completed SP step; returns ``result_ref``."""
    output_path = root / "WORK" / "05_SP" / "sp_output.log"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(V2_SP_OUTPUT_TEXT, encoding="utf-8")
    result = CalculationResult(
        energy=V2_SP_ENERGY,
        artifacts=[ArtifactRef(path=output_path, type="output", source="fixture")],
    )
    payload = result.to_step_result_dict(root=root)
    payload.update(
        {
            "schema_version": STEP_RESULT_SCHEMA_VERSION,
            "step_identity": identity.step_identities[0],
            "step_id": _step_id(0, StepKind.SINGLEPOINT),
            "index": 0,
            "kind": "singlepoint",
            "symbols": ["O", "H", "H"],
            "job_id": None,
            "attempt": None,
            "code_release": str(_platform_version),
            "config_digest": None,
            "dependency_artifacts": [],
        }
    )
    digest = write_step_result(root / "WORK" / "05_SP" / "step_result.json", payload)
    return {"path": "WORK/05_SP/step_result.json", "sha256": digest}


def _sp_publication(root: Path, identity: object, *, mark_complete: bool) -> None:
    """Contract C steps ②③ for the SP step (③ skipped when interrupted)."""
    sp_dir = root / "WORK" / "05_SP"
    result = CalculationResult(
        energy=V2_SP_ENERGY,
        artifacts=[ArtifactRef(path=sp_dir / "sp_output.log", type="output", source="fixture")],
    )
    result_id = _step_result_id(identity.plan_identity, 0, StepKind.SINGLEPOINT)
    record = _step_scientific_record(
        result, result_id=result_id, kind=StepKind.SINGLEPOINT, result_dir=sp_dir
    )
    manifest = _step_publication_manifest(record)
    if mark_complete:
        publish_result(sp_dir, record=record, manifest=manifest)
    else:
        # publish interruption: ① record + ② step manifest written, ③ absent
        save_scientific_result(sp_dir, record)
        register_result_manifest(sp_dir, manifest)


def _write_v2_checkpoint(
    root: Path,
    identity: object,
    sp_result_ref: dict[str, str],
    *,
    freq_status: str,
    freq_error: str,
) -> None:
    """schema=2 checkpoint for the shared SP→FREQ plan."""
    sp_state = StepState(
        index=0,
        kind=StepKind.SINGLEPOINT,
        status="completed",
        result=CalculationResult(energy=V2_SP_ENERGY),
        executed_this_run=False,
        last_executed_attempt=1,
        result_ref=dict(sp_result_ref),
    )
    freq_state = StepState(
        index=1,
        kind=StepKind.FREQUENCY,
        status=freq_status,
        error=freq_error,
    )
    write_checkpoint(
        root / "WORK" / "00_RUNTIME",
        Checkpoint(
            task_id=V2_TASK_ID,
            workflow="optimize",
            plan_fingerprint=identity.plan_identity,
            step_states=[s.to_dict() for s in (sp_state, freq_state)],
            items_state={"__handoff__": {"single_point_energy": V2_SP_ENERGY}},
            resume_count=0,
            identity_schema=2,
        ),
    )


def _write_v2_result_manifest(root: Path) -> None:
    """Partial-failure task manifest: failed status + surviving SP products.

    Product ids mirror ``CalculationPlanExecutor._write_result_manifest`` so
    the smoke tests can assert id stability across resumes.
    """
    label = "singlepoint (step 0)"
    manifest = ResultManifest(
        task_id=V2_TASK_ID,
        workflow="optimize",
        status="failed",
        version=2,
    )
    manifest.add_product(
        "step_0_singlepoint_output",
        f"{label} — output",
        "WORK/05_SP/sp_output.log",
        ProductKind.ENERGY_REPORT,
        metadata={
            "stage_status": "completed",
            "stage_id": "step_0_singlepoint",
            "optimization_status": "unknown",
            "policy_version": 1,
        },
    )
    manifest.add_product(
        "step_0_singlepoint_energy",
        f"{label} — energy",
        "",
        ProductKind.ENERGY_REPORT,
    )
    manifest.write(root / "RESULT")


def build_v2_checkpoint_mixed() -> None:
    """(f) v2: completed + incomplete mix, partial-failure manifest, publish done."""
    root = FIXTURES_DIR / "v2_checkpoint_mixed"
    _, identity = _write_v2_scaffold(root)
    sp_result_ref = _write_sp_step_result(root, identity)
    _sp_publication(root, identity, mark_complete=True)
    _write_v2_checkpoint(
        root,
        identity,
        sp_result_ref,
        freq_status="failed",
        freq_error="fixture: synthetic frequency failure",
    )
    _write_v2_result_manifest(root)


def build_v2_publish_interrupted() -> None:
    """(g) v2: publish interruption — record + step manifest, marker absent."""
    root = FIXTURES_DIR / "v2_publish_interrupted"
    _, identity = _write_v2_scaffold(root)
    sp_result_ref = _write_sp_step_result(root, identity)
    _sp_publication(root, identity, mark_complete=False)
    _write_v2_checkpoint(root, identity, sp_result_ref, freq_status="pending", freq_error="")
    # RESULT/result_manifest.json intentionally absent (crash before task publish)


def build_v2_checkpoint_malformed() -> None:
    """(h) v2: malformed step_states for stable-``step_id`` validation.

    Four states for a two-step plan: a valid SP state, a position-conflicting
    duplicate (claims ``step_0`` while sitting at position 1), a non-mapping
    corruption and an extra §9.5 stability node (``step_2_singlepoint``).
    Recovery must validate by stable id — never by pure length — and must
    reject the corrupt entries for rebuild without raising ``IndexError``.
    """
    root = FIXTURES_DIR / "v2_checkpoint_malformed"
    _, identity = _write_v2_scaffold(root)
    sp_result_ref = _write_sp_step_result(root, identity)
    _sp_publication(root, identity, mark_complete=True)
    step_states = [
        {
            "index": 0,
            "kind": "singlepoint",
            "status": "completed",
            "error": "",
            "energy": V2_SP_ENERGY,
            "executed_this_run": False,
            "last_executed_attempt": 1,
            "result_ref": dict(sp_result_ref),
            "reused_from_attempt": None,
        },
        # position-conflicting duplicate: claims step_0 at position 1
        {
            "index": 0,
            "kind": "singlepoint",
            "status": "completed",
            "error": "",
            "energy": V2_SP_ENERGY,
        },
        # non-mapping corruption
        "not-a-step-state",
        # extra §9.5 stability node beyond the plan — allowed by stable id
        {"index": 2, "kind": "singlepoint", "status": "completed", "error": "", "energy": -1.25},
    ]
    write_checkpoint(
        root / "WORK" / "00_RUNTIME",
        Checkpoint(
            task_id=V2_TASK_ID,
            workflow="optimize",
            plan_fingerprint=identity.plan_identity,
            step_states=step_states,
            items_state={},
            resume_count=0,
            identity_schema=2,
        ),
    )


def build_v2_casscf_not_converged() -> None:
    """(i) v2 counterexample: converged=false-completed CASSCF receipts.

    Plan todo 11 (T10/D5): both recovery entries must refuse a stored
    ``completed`` receipt whose recorded CAS convergence fact is
    ``false`` — the ``step_result.json`` adoption path and the
    ``scientific_result.json`` publish-retry path — and conservatively
    recompute instead.  ``config_digest`` stays ``null`` (attempt
    metadata, never science), like every v2 fixture.
    """
    root = FIXTURES_DIR / "v2_casscf_not_converged"
    root.mkdir(parents=True, exist_ok=True)
    xyz_path = root / "structures" / "input.xyz"
    xyz_path.parent.mkdir(parents=True, exist_ok=True)
    xyz_path.write_text(V2_INPUT_XYZ, encoding="utf-8")
    plan = CalculationPlan(
        workflow="casscf",
        profile="default",
        items=[StructureArtifact(path=xyz_path, elements=["O", "H", "H"])],
        steps=[
            CalculationStep(
                kind=StepKind.CASSCF,
                spec={"casscf": {"active_electrons": 2, "active_orbitals": 2}},
            )
        ],
    )
    identity = compute_identity(plan)
    _dump(
        root / "identity.json",
        {
            "plan_identity": identity.plan_identity,
            "step_identities": list(identity.step_identities),
            "plan_repr": {
                "workflow": plan.workflow,
                "profile": plan.profile,
                "items": [{"path": "structures/input.xyz", "elements": ["O", "H", "H"]}],
                "steps": [
                    {"kind": s.kind.value, "mode": s.mode.value, "spec": s.spec} for s in plan.steps
                ],
            },
        },
    )

    step_dir = root / "WORK" / "08_CASSCF"
    step_dir.mkdir(parents=True, exist_ok=True)
    log_path = step_dir / "casscf.log"
    log_path.write_text("v2 fixture: CAS-SCF did not converge\n", encoding="utf-8")
    multireference = {
        "active_electrons": 2,
        "active_orbitals": 2,
        "multiplicity": 1,
        "nroots": 1,
        "casscf_energy_hartree": -108.5,
        "natural_occupations": [1.7, 0.3],
        "converged": False,
    }
    active_space_path = step_dir / "active_space.json"
    active_space_path.write_text(
        json.dumps(multireference, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    result = CalculationResult(
        energy=-108.5,
        artifacts=[
            ArtifactRef(path=log_path, type="log", source="fixture"),
            ArtifactRef(path=active_space_path, type="active_space", source="fixture"),
        ],
        metadata={
            "multireference": dict(multireference),
            "casscf": {"casscf_energy_hartree": -108.5, "converged": False},
        },
    )
    payload = result.to_step_result_dict(root=root)
    payload.update(
        {
            "schema_version": STEP_RESULT_SCHEMA_VERSION,
            "step_identity": identity.step_identities[0],
            "step_id": _step_id(0, StepKind.CASSCF),
            "index": 0,
            "kind": "casscf",
            "symbols": ["O", "H", "H"],
            "job_id": None,
            "attempt": None,
            "code_release": str(_platform_version),
            "config_digest": None,
            "dependency_artifacts": [],
        }
    )
    digest = write_step_result(step_dir / "step_result.json", payload)
    result_ref = {"path": "WORK/08_CASSCF/step_result.json", "sha256": digest}
    result_id = _step_result_id(identity.plan_identity, 0, StepKind.CASSCF)
    record = _step_scientific_record(
        result, result_id=result_id, kind=StepKind.CASSCF, result_dir=step_dir
    )
    publish_result(step_dir, record=record, manifest=_step_publication_manifest(record))

    cas_state = StepState(
        index=0,
        kind=StepKind.CASSCF,
        status="completed",
        result=CalculationResult(energy=-108.5),
        executed_this_run=False,
        last_executed_attempt=1,
        result_ref=dict(result_ref),
    )
    write_checkpoint(
        root / "WORK" / "00_RUNTIME",
        Checkpoint(
            task_id=V2_TASK_ID,
            workflow="casscf",
            plan_fingerprint=identity.plan_identity,
            step_states=[cas_state.to_dict()],
            items_state={},
            resume_count=0,
            identity_schema=2,
        ),
    )


def main() -> int:
    # legacy v1 — byte-frozen write path
    build_checkpoint_mixed()
    build_partial_failure()
    build_historical_manifest()
    build_batch_sp_cache()
    build_remote_path_reference()
    # v2 — controlled regeneration
    build_v2_checkpoint_mixed()
    build_v2_publish_interrupted()
    build_v2_checkpoint_malformed()
    build_v2_casscf_not_converged()
    print(f"fixtures regenerated under {FIXTURES_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
