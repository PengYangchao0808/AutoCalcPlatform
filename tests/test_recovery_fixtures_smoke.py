"""Smoke check: cross-version recovery fixtures load with the CURRENT code.

Read-only with respect to the fixture tree (executor scenarios run on tmp
copies); exercises the same readers recovery paths use.  Fixture semantics
and the compat checks owed by later migration todos are documented in
``tests/baseline/recovery_fixtures/README.md``.

Contract frozen here (plan todo 14):

* v1 fixtures (no ``identity_schema``) → default ``load_checkpoint`` is
  conservative recompute (``None``) plus the ``identity_unverifiable_legacy``
  logger event; reuse only through the explicit batch-path switch.
* v1 fixture bytes are frozen — sha256 pins below fail on any rewrite; the
  checkpoint carries the legacy ``attempts`` key (never ``resume_count``).
* v2 fixtures (``identity_schema=2``) resume on the executor without
  re-running adopted QC, across the three crash windows and a lost publish.
* A malformed checkpoint is validated by stable ``step_{index}_{kind}`` ids —
  never by pure list length — and recovery never raises ``IndexError``.
* Plan fingerprints survive todo-11-style contract field moves (Metis Q7).

This suite never reads ``.omo/evidence`` and depends on no wall-clock time
or machine path.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from acp.backends.batch import _read_cache
from acp.calculations import executor as executor_module
from acp.calculations.batch._manifest import BatchCalculationManifest
from acp.calculations.checkpoint import load_checkpoint
from acp.calculations.contracts import (
    CalculationPlan,
    CalculationResult,
    CalculationStep,
    OptimizationMode,
    StepKind,
    StructureArtifact,
)
from acp.calculations.executor import (
    CalculationPlanExecutor,
    ExecutionResult,
    _plan_fingerprint,
    _step_id,
)
from acp.calculations.identity import compute_identity, identity_fingerprint
from acp.compat.legacy.manifests import read_batch_calculation_manifest
from acp.results.remote_structure_cache import RemoteStructureCache
from acp.storage.manifest import ResultManifest

FIXTURES = Path(__file__).resolve().parent / "baseline" / "recovery_fixtures"

#: SHA-256 pins of the byte-frozen v1 fixture files (todo 14 acceptance).
#: Regenerating must reproduce these exactly; rewriting a v1 fixture fails here.
_V1_FROZEN_SHA256 = {
    "checkpoint_mixed/WORK/00_RUNTIME/checkpoint.json": (
        "2d1ed45629d85f6b2f3b30e2b3537455b1ed463bb7b976ad6326e1787db2f2eb"
    ),
    "checkpoint_mixed/plan_fingerprint.json": (
        "63c399ddb5355acb7e5d6eb0543aff730301c07b737ade120918d11db7a7b93f"
    ),
    "partial_failure/RESULT/result_manifest.json": (
        "b40a8251589a518b6a3ef8991c06abb1b235c730bb0343863fbd13564c85328f"
    ),
    "partial_failure/batch_items_manifest.json": (
        "5b9643ca2a4247f3edda0cdf7a99d01083f890955b3bce3810330d46b2e94426"
    ),
    "historical_manifest/RESULT_v2/result_manifest.json": (
        "7efdc3fe3ae6b3bef63d4ac4a1c3434c7594ba980979c1be57ea494b6b500040"
    ),
    "historical_manifest/batch_calculation_manifest_v1.json": (
        "1fb183f64f2c5bb8a949f7d70fdae5b00266fde0bf205cfc147eb5df75694518"
    ),
    "batch_sp_cache/cache_input.json": (
        "60e225e3e91de437adf6e36fda993897513341d336848597932391d444affce7"
    ),
    "batch_sp_cache/aff64f9d434de7e96ad6ca978d73eae12a5e43150cf47b172b7d2ac3029c16d3.json": (
        "364429b8c1fc6ce9a8479436419025b70433a1ba5f621f21269191aefc8a5771"
    ),
    "remote_path_reference/remote_job_paths.json": (
        "bde7364b39195cdd6bc85418a96d23ed5ce7770bc9f2058dca722e46c260b22f"
    ),
}


# ── shared v2 fixture plan (mirrors generate_fixtures.py) ───────────────────


def _v2_plan(root: Path) -> CalculationPlan:
    """Rebuild the shared v2 fixture plan under a fixture root (or its copy).

    The v2 identity is content-bound: item paths never enter the hash, so a
    tmp copy of a fixture reproduces the stored ``plan_identity``.
    """
    return CalculationPlan(
        workflow="optimize",
        profile="r2SCAN-3c",
        items=[StructureArtifact(path=root / "structures" / "input.xyz", elements=["O", "H", "H"])],
        steps=[
            CalculationStep(kind=StepKind.SINGLEPOINT),
            CalculationStep(kind=StepKind.FREQUENCY),
        ],
    )


def _stored_identity(root: Path) -> dict:
    payload = json.loads((root / "identity.json").read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _copy_fixture(name: str, tmp_path: Path) -> Path:
    """Copy one fixture directory into tmp (the fixture tree stays read-only)."""
    destination = tmp_path / name
    shutil.copytree(FIXTURES / name, destination)
    return destination


def _v2_dispatch(calls: dict[str, int]) -> dict[StepKind, object]:
    """Fake SP/FREQ primitives recording QC call counts."""

    def sp(request: object) -> CalculationResult:
        calls["sp"] = calls.get("sp", 0) + 1
        return CalculationResult(energy=-2.5)

    def freq(request: object) -> CalculationResult:
        calls["freq"] = calls.get("freq", 0) + 1
        return CalculationResult(frequencies=[100.0, 200.0])

    return {StepKind.SINGLEPOINT: sp, StepKind.FREQUENCY: freq}


def _execute_v2(root: Path, calls: dict[str, int]) -> ExecutionResult:
    """Execute the shared v2 plan with fake QC and a pinned config digest."""
    plan = _v2_plan(root)
    with patch.dict(executor_module._PRIMITIVE_DISPATCH, _v2_dispatch(calls)):
        # Fixtures pin ``config_digest: null`` — a machine-local config must
        # never gate adoption (attempt metadata, not science).
        with patch.object(executor_module, "current_config_digest", lambda: None):
            return CalculationPlanExecutor().execute(plan, root)


def _result_ids(root: Path) -> list[str]:
    """Published product ids of the task-level RESULT manifest."""
    return [product.id for product in ResultManifest.read(root / "RESULT").products]


# ── v1 fixtures: loadability + default conservative recompute ───────────────


def test_checkpoint_mixed_steps_loadable(caplog: pytest.LogCaptureFixture) -> None:
    root = FIXTURES / "checkpoint_mixed"
    meta = json.loads((root / "plan_fingerprint.json").read_text(encoding="utf-8"))
    with caplog.at_level(logging.INFO):
        # v1 fixture (no identity_schema) → default conservative recompute …
        assert load_checkpoint(root / "WORK" / "00_RUNTIME", meta["plan_fingerprint"]) is None
    # … with the documented logger event, never silent reuse.
    assert "identity_unverifiable_legacy" in caplog.text
    # … readable only through the explicit legacy-compat switch (batch path).
    checkpoint = load_checkpoint(
        root / "WORK" / "00_RUNTIME", meta["plan_fingerprint"], allow_legacy_fingerprint=True
    )
    assert checkpoint is not None
    statuses = [state.get("status") for state in checkpoint.step_states]
    assert statuses.count("completed") == 2
    assert "failed" in statuses and "pending" in statuses
    assert checkpoint.items_state["__handoff__"]["symbols"] == ["O", "H", "H"]


def test_v1_fixtures_byte_frozen() -> None:
    """The five v1 fixtures are byte-frozen (todo 14 acceptance).

    The checkpoint carries the legacy ``attempts`` serialisation key — no
    ``identity_schema``, no ``resume_count`` — and every pinned file hashes
    to its generation-time sha256.
    """
    for rel_path, expected in _V1_FROZEN_SHA256.items():
        payload = (FIXTURES / rel_path).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == expected, f"v1 fixture changed: {rel_path}"

    checkpoint_payload = json.loads(
        (FIXTURES / "checkpoint_mixed" / "WORK" / "00_RUNTIME" / "checkpoint.json").read_text(
            encoding="utf-8"
        )
    )
    assert isinstance(checkpoint_payload, dict)
    assert checkpoint_payload["attempts"] == 1
    assert "resume_count" not in checkpoint_payload
    assert "identity_schema" not in checkpoint_payload


def test_mismatch_never_raises_per_schema_events(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """D06 semantics: mismatch/legacy/unknown never raise — ``None`` + event.

    The historical ``pytest.raises(CheckpointMismatchError)`` coverage is
    replaced by per-schema logger-event assertions: schema=2 mismatch →
    ``identity_fingerprint_mismatch``; schema=1 default and switch-mismatch →
    ``identity_unverifiable_legacy``; unknown schema →
    ``identity_unknown_schema``.  The exception class itself is retained for
    API compatibility only — no load path raises it.
    """
    from acp.calculations.checkpoint import CheckpointMismatchError, write_checkpoint
    from acp.calculations.contracts import Checkpoint

    assert CheckpointMismatchError is not None  # API-compat symbol still importable

    base = dict(
        task_id="t",
        workflow="singlepoint",
        plan_fingerprint="fp",
        step_states=[],
        items_state={},
    )

    # schema=2 mismatch → None + identity_fingerprint_mismatch
    v2_dir = tmp_path / "v2"
    write_checkpoint(v2_dir, Checkpoint(**base, resume_count=1, identity_schema=2))  # type: ignore[arg-type]
    with caplog.at_level(logging.INFO):
        assert load_checkpoint(v2_dir, "other-fingerprint") is None
    assert "identity_fingerprint_mismatch" in caplog.text

    # schema=1 default → None + identity_unverifiable_legacy (never raise)
    caplog.clear()
    v1_dir = tmp_path / "v1"
    write_checkpoint(v1_dir, Checkpoint(**base, resume_count=1, identity_schema=1))  # type: ignore[arg-type]
    with caplog.at_level(logging.INFO):
        assert load_checkpoint(v1_dir, "fp") is None
    assert "identity_unverifiable_legacy" in caplog.text

    # schema=1 explicit switch + mismatch → None + event (never raise)
    caplog.clear()
    with caplog.at_level(logging.INFO):
        assert load_checkpoint(v1_dir, "other", allow_legacy_fingerprint=True) is None
    assert "identity_unverifiable_legacy" in caplog.text

    # schema=1 explicit switch + match → returned (batch reuse path)
    matched = load_checkpoint(v1_dir, "fp", allow_legacy_fingerprint=True)
    assert matched is not None

    # unknown schema → None + identity_unknown_schema
    caplog.clear()
    unknown_dir = tmp_path / "unknown"
    write_checkpoint(unknown_dir, Checkpoint(**base, resume_count=1, identity_schema=7))  # type: ignore[arg-type]
    with caplog.at_level(logging.INFO):
        assert load_checkpoint(unknown_dir, "fp") is None
    assert "identity_unknown_schema" in caplog.text


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
    stored fingerprint must still validate under the explicit compat switch,
    and the completed optimize step must stay completed (continue never
    recomputes completed steps).  Default (no switch) stays conservative.
    """
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
    # default: conservative recompute (no direct reuse)
    assert load_checkpoint(root / "WORK" / "00_RUNTIME", meta["plan_fingerprint"]) is None
    checkpoint = load_checkpoint(
        root / "WORK" / "00_RUNTIME", meta["plan_fingerprint"], allow_legacy_fingerprint=True
    )
    assert checkpoint is not None
    optimize_state = next(
        state for state in checkpoint.step_states if state.get("kind") == "optimize"
    )
    assert optimize_state["status"] == "completed"
    assert optimize_state["energy"] == -100.75


def test_checkpoint_mixed_frequency_step_recovers() -> None:
    """Todo 19 switch: the frequency-capability recovery subset stays valid.

    The frozen checkpoint's frequency step is failed-but-recoverable
    (``continue`` re-runs it while completed steps stay completed), and the
    stored plan fingerprint — whose plan carries a FREQUENCY step — still
    validates under the explicit compat switch after the frequency
    task/contract switch (the empty ``FrequencyOptions`` keeps
    ``str(step.spec)`` stable).  Default stays conservative.
    """
    from cccp.calculation.requests import FrequencyOptions

    root = FIXTURES / "checkpoint_mixed"
    meta = json.loads((root / "plan_fingerprint.json").read_text(encoding="utf-8"))
    assert load_checkpoint(root / "WORK" / "00_RUNTIME", meta["plan_fingerprint"]) is None
    checkpoint = load_checkpoint(
        root / "WORK" / "00_RUNTIME", meta["plan_fingerprint"], allow_legacy_fingerprint=True
    )
    assert checkpoint is not None
    freq_state = next(state for state in checkpoint.step_states if state.get("kind") == "frequency")
    assert freq_state["status"] == "failed"
    assert "synthetic frequency failure" in freq_state.get("error", "")
    statuses = [state.get("status") for state in checkpoint.step_states]
    assert statuses.count("completed") == 2
    assert FrequencyOptions().to_dict() == {}


def test_checkpoint_casscf_thermochemistry_steps_recover(tmp_path: Path) -> None:
    """Todo 22 switch: the CASSCF/thermochemistry recovery subset stays valid.

    The shared checkpoint contract round-trips steps of both new kinds (a
    completed CASSCF step and a failed-but-recoverable THERMOCHEMISTRY step);
    the plan fingerprint over ``str(step.spec)`` validates after the typed
    options switch.
    """
    from acp.calculations.checkpoint import write_checkpoint
    from acp.calculations.contracts import Checkpoint

    plan = CalculationPlan(
        workflow="casscf",
        profile="default",
        items=[{"path": "structures/input.xyz"}],
        steps=[
            CalculationStep(
                kind=StepKind.CASSCF,
                spec={"casscf": {"active_electrons": 2, "active_orbitals": 2}},
            ),
            CalculationStep(
                kind=StepKind.THERMOCHEMISTRY,
                spec={"freq_log_path": "WORK/03_OPT/freq.log"},
            ),
        ],
    )
    fingerprint = _plan_fingerprint(plan)
    checkpoint = Checkpoint(
        task_id="task-22-recovery",
        workflow="casscf",
        plan_fingerprint=fingerprint,
        step_states=[
            {"kind": "casscf", "status": "completed", "energy": -109.14691549},
            {"kind": "thermochemistry", "status": "failed", "error": "synthetic shermo failure"},
        ],
        identity_schema=2,
    )
    write_checkpoint(tmp_path, checkpoint)
    loaded = load_checkpoint(tmp_path, fingerprint)
    assert loaded is not None
    assert [state.get("status") for state in loaded.step_states] == ["completed", "failed"]
    assert loaded.step_states[0]["energy"] == -109.14691549

    # D06: fingerprint mismatch never raises — v2 mismatch and the legacy
    # compat switch both fall back to conservative recompute (None).
    assert load_checkpoint(tmp_path, "other-fingerprint") is None
    assert load_checkpoint(tmp_path, "other-fingerprint", allow_legacy_fingerprint=True) is None


# ── v2 fixtures: executor resume without QC recompute ───────────────────────


def test_v2_mixed_fixture_resumes_without_recompute(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """v2 checkpoint + step_result adopt on the executor — QC counts pinned.

    The completed SP step of ``v2_checkpoint_mixed`` is adopted from its
    durable ``step_result.json`` (identity + artifact digests verified) and
    the failed FREQ step recomputes; the partial-failure RESULT manifest
    keeps stable, duplicate-free product ids.
    """
    root = _copy_fixture("v2_checkpoint_mixed", tmp_path)
    stored = _stored_identity(root)
    plan = _v2_plan(root)
    identity = compute_identity(plan)
    assert identity.plan_identity == stored["plan_identity"]
    assert list(identity.step_identities) == stored["step_identities"]

    # v2 checkpoint loads by default (schema 2 + matching identity)
    checkpoint = load_checkpoint(root / "WORK" / "00_RUNTIME", identity.plan_identity)
    assert checkpoint is not None
    assert checkpoint.identity_schema == 2
    assert [state.get("status") for state in checkpoint.step_states] == ["completed", "failed"]

    baseline_ids = _result_ids(root)
    assert "step_0_singlepoint_energy" in baseline_ids

    calls: dict[str, int] = {}
    with caplog.at_level(logging.INFO):
        result = _execute_v2(root, calls)

    assert calls.get("sp", 0) == 0, "adopted SP must never re-run QC"
    assert calls.get("freq", 0) == 1, "failed FREQ must recompute exactly once"
    assert result.step_states[0].status == "completed"
    assert result.step_states[0].executed_this_run is False
    assert result.status == "completed"
    after_ids = _result_ids(root)
    assert len(after_ids) == len(set(after_ids)), f"duplicate products: {after_ids}"
    assert set(baseline_ids) <= set(after_ids)
    assert "recovery.step_adopted" in caplog.text


def test_crash_window_step_result_without_checkpoint_update(tmp_path: Path) -> None:
    """Crash window A: ``step_result.json`` written, checkpoint not updated.

    The checkpoint still reports SP as pending — adoption reads the durable
    result, QC counts stay at zero for SP, and product ids remain stable
    with no duplicates.
    """
    root = _copy_fixture("v2_checkpoint_mixed", tmp_path)
    runtime = root / "WORK" / "00_RUNTIME"
    payload = json.loads((runtime / "checkpoint.json").read_text(encoding="utf-8"))
    payload["step_states"][0]["status"] = "pending"
    payload["step_states"][0]["result_ref"] = None
    (runtime / "checkpoint.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    baseline_ids = _result_ids(root)

    calls: dict[str, int] = {}
    result = _execute_v2(root, calls)

    assert calls.get("sp", 0) == 0, "crash-window adoption must not re-run SP QC"
    assert result.step_states[0].status == "completed"
    assert result.step_states[0].executed_this_run is False
    after_ids = _result_ids(root)
    assert len(after_ids) == len(set(after_ids)), f"duplicate products: {after_ids}"
    assert set(baseline_ids) <= set(after_ids)


def test_crash_window_checkpoint_without_manifest(tmp_path: Path) -> None:
    """Crash window B: checkpoint written, manifest never published.

    All publication files are removed after the checkpoint persisted; the
    resume re-runs only the publication sequence — SP QC count unchanged,
    the same product ids reappear exactly once.
    """
    root = _copy_fixture("v2_checkpoint_mixed", tmp_path)
    sp_dir = root / "WORK" / "05_SP"
    for name in ("scientific_result.json", "result_manifest.json", "publication_state.json"):
        (sp_dir / name).unlink()
    (root / "RESULT" / "result_manifest.json").unlink()
    baseline_ids = _result_ids(FIXTURES / "v2_checkpoint_mixed")

    calls: dict[str, int] = {}
    result = _execute_v2(root, calls)

    assert calls.get("sp", 0) == 0, "publish-window resume must not re-run SP QC"
    assert result.step_states[0].status == "completed"
    from acp.calculations.result_publication import load_publication_state

    state = load_publication_state(sp_dir)
    assert state is not None and state.complete is True
    after_ids = _result_ids(root)
    assert len(after_ids) == len(set(after_ids)), f"duplicate products: {after_ids}"
    assert set(baseline_ids) <= set(after_ids)


def test_crash_window_published_not_marked_complete(tmp_path: Path) -> None:
    """Crash window C: published, but the publish-complete marker is absent.

    ``v2_publish_interrupted`` carries the scientific record + step manifest
    without ``publication_state.json`` and without the task-level manifest;
    the resume finishes only the publication (marker flips, RESULT manifest
    published once) while SP QC stays at zero.
    """
    root = _copy_fixture("v2_publish_interrupted", tmp_path)
    sp_dir = root / "WORK" / "05_SP"
    from acp.calculations.result_publication import (
        PUBLICATION_STATE_FILENAME,
        SCIENTIFIC_RESULT_FILENAME,
        load_publication_state,
    )

    assert (sp_dir / SCIENTIFIC_RESULT_FILENAME).is_file()
    assert not (sp_dir / PUBLICATION_STATE_FILENAME).is_file()
    assert not (root / "RESULT" / "result_manifest.json").is_file()

    calls: dict[str, int] = {}
    result = _execute_v2(root, calls)

    assert calls.get("sp", 0) == 0, "publish-only retry must not re-run SP QC"
    assert result.step_states[0].status == "completed"
    state = load_publication_state(sp_dir)
    assert state is not None and state.complete is True
    ids = _result_ids(root)
    assert len(ids) == len(set(ids)), f"duplicate products: {ids}"
    assert "step_0_singlepoint_energy" in ids


def test_publish_lost_retries_publish_only(tmp_path: Path) -> None:
    """Publish lost after completion → publish-only retry, QC counts pinned.

    Run once on a fully-published fixture (all steps adopted/executed),
    delete the task-level manifest, resume: no step re-runs and the
    identical product id set is rebuilt with no duplicates.
    """
    root = _copy_fixture("v2_checkpoint_mixed", tmp_path)
    calls: dict[str, int] = {}
    result1 = _execute_v2(root, calls)
    assert result1.status == "completed"
    first_ids = _result_ids(root)
    assert calls.get("sp", 0) == 0 and calls.get("freq", 0) == 1

    (root / "RESULT" / "result_manifest.json").unlink()
    result2 = _execute_v2(root, calls)

    assert calls.get("sp", 0) == 0, "publish retry must not re-run SP QC"
    assert calls.get("freq", 0) == 1, "publish retry must not re-run FREQ QC"
    assert result2.status == "completed"
    second_ids = _result_ids(root)
    assert second_ids == first_ids, "product ids must be stable across publish retries"
    assert len(second_ids) == len(set(second_ids))


# ── malformed checkpoint: stable step_id validation (never IndexError) ──────


def _validate_checkpoint_step_ids(
    states: list[object], plan_kinds: list[str]
) -> tuple[dict[int, dict], list[str]]:
    """Validate checkpoint ``step_states`` by stable ``step_{index}_{kind}``.

    Deliberately NOT a pure-length check: the malformed fixture carries more
    (or fewer) states than the plan has steps — extra §9.5 stability nodes
    are allowed, corrupt or position-conflicting entries are rejected for
    rebuild, and no integer index ever indexes the plan (no ``IndexError``).
    """
    plan_ids = {f"step_{index}_{kind}" for index, kind in enumerate(plan_kinds)}
    stability_id = f"step_{len(plan_kinds)}_singlepoint"
    accepted: dict[int, dict] = {}
    rejected: list[str] = []
    for position, state in enumerate(states):
        if not isinstance(state, dict):
            rejected.append(f"position {position}: not a mapping")
            continue
        index = state.get("index")
        kind = state.get("kind")
        if not isinstance(index, int) or isinstance(index, bool) or not isinstance(kind, str):
            rejected.append(f"position {position}: missing stable step id fields")
            continue
        try:
            step_id = _step_id(index, StepKind(kind))
        except ValueError:
            rejected.append(f"position {position}: unknown step kind {kind!r}")
            continue
        if step_id in plan_ids:
            if index != position:
                # e.g. a state claiming step_0 while sitting at position 1:
                # binding by length/position would silently mis-adopt it.
                rejected.append(f"{step_id} at position {position}: stable-id conflict")
                continue
            accepted[index] = state
        elif step_id == stability_id:
            accepted[index] = state  # extra §9.5 stability node — allowed
        else:
            rejected.append(f"position {position}: unknown step id {step_id!r}")
    return accepted, rejected


def test_malformed_checkpoint_validated_by_stable_step_id(tmp_path: Path) -> None:
    """Malformed fixture: validate by stable step_id, never by pure length.

    The fixture mixes a valid state, a position-conflicting duplicate, a
    non-mapping corruption and an extra stability node (4 states for a
    2-step plan).  Validation rejects the corrupt entries for rebuild, keeps
    the stability node, and a full executor run neither raises IndexError
    nor reuses the mis-bound completed fact (FREQ recomputes).
    """
    root = _copy_fixture("v2_checkpoint_malformed", tmp_path)
    plan = _v2_plan(root)
    plan_kinds = [step.kind.value for step in plan.steps]
    runtime = root / "WORK" / "00_RUNTIME"
    payload = json.loads((runtime / "checkpoint.json").read_text(encoding="utf-8"))
    states: list[object] = payload["step_states"]

    # a pure-length judgement would be useless here: 4 states ≠ 2 plan steps
    assert len(states) != len(plan_kinds)

    accepted, rejected = _validate_checkpoint_step_ids(states, plan_kinds)
    assert 0 in accepted, "the valid SP state must validate"
    assert 2 in accepted, "the extra stability node must be allowed"
    assert len(rejected) >= 2, "position conflict and non-mapping must be rejected"
    assert any("stable-id conflict" in reason for reason in rejected)
    assert any("not a mapping" in reason for reason in rejected)

    # executor run: no IndexError, no silent reuse of the corrupt completed
    # fact (FREQ has no step_result → rebuild), adopted SP QC count stays 0
    calls: dict[str, int] = {}
    result = _execute_v2(root, calls)
    assert calls.get("sp", 0) == 0, "valid SP state must still adopt"
    assert calls.get("freq", 0) == 1, "corrupt completed fact must rebuild FREQ"
    assert result.status == "completed"


# ── fingerprint stability across todo-11-style field moves (Metis Q7) ──────


def test_fingerprint_stable_across_contract_field_moves(tmp_path: Path) -> None:
    """Metis Q7: contract field moves never move a fingerprint.

    * legacy: the v1 fixture fingerprint (generated before todo 11 renamed
      ``attempts``→``resume_count``, todo 12 added ``StepState.result_ref``
      and todo 13 extended the handoff) still matches the current
      ``legacy_plan_fingerprint`` — physical contract fields do not enter it.
    * v2: ``compute_identity`` canonicalises (sorted keys, execution-domain
      keys dropped, content-bound items), so spec key order and machine-path
      execution fields cannot move the plan identity or step identities.
    """
    # legacy fingerprint — frozen before todos 11–13 field moves
    v1_meta = json.loads(
        (FIXTURES / "checkpoint_mixed" / "plan_fingerprint.json").read_text(encoding="utf-8")
    )
    v1_plan = CalculationPlan(
        workflow="BatchOptimize",
        profile="opt_freq",
        items=[{"path": "structures/input.xyz"}],
        steps=[
            CalculationStep(kind=StepKind.OPTIMIZE, spec={"max_cycles": 5}),
            CalculationStep(kind=StepKind.FREQUENCY, spec=None),
        ],
    )
    assert _plan_fingerprint(v1_plan) == v1_meta["plan_fingerprint"]

    # v2 identity — key order and execution-domain fields never move it
    root = _copy_fixture("v2_checkpoint_mixed", tmp_path)
    stored = _stored_identity(root)
    base = _v2_plan(root)
    identity = compute_identity(base)
    assert identity.plan_identity == stored["plan_identity"]
    assert list(identity.step_identities) == stored["step_identities"]

    # machine-path / resource execution-domain keys are dropped by design
    execution_domain_plan = CalculationPlan(
        workflow="optimize",
        profile="r2SCAN-3c",
        items=[base.items[0]],
        steps=[
            CalculationStep(kind=StepKind.SINGLEPOINT, spec={"output_dir": "/tmp/machine/path"}),
            CalculationStep(kind=StepKind.FREQUENCY, spec={"nproc": 8}),
        ],
    )
    execution_identity = compute_identity(execution_domain_plan)
    assert execution_identity.plan_identity == identity.plan_identity, (
        "execution-domain keys (output_dir/nproc) must not move the v2 plan identity"
    )
    assert execution_identity.step_identities == identity.step_identities

    # …while a science-order change (step swap) does change the identity
    swapped = CalculationPlan(
        workflow="optimize",
        profile="r2SCAN-3c",
        items=[base.items[0]],
        steps=[
            CalculationStep(kind=StepKind.FREQUENCY),
            CalculationStep(kind=StepKind.SINGLEPOINT),
        ],
    )
    assert compute_identity(swapped).plan_identity != identity.plan_identity

    # identity_fingerprint itself is key-order insensitive
    payload: dict[str, object] = {"scope": "plan", "alpha": 1, "beta": {"x": 1, "y": 2}}
    reversed_payload = dict(reversed(list(payload.items())))
    assert identity_fingerprint(payload) == identity_fingerprint(reversed_payload)


# ── (i) converged=false-completed CASSCF counterexample (plan todo 11) ──────


def _casscf_fixture_plan(root: Path) -> CalculationPlan:
    """Rebuild the ``v2_casscf_not_converged`` plan (content-bound identity)."""
    return CalculationPlan(
        workflow="casscf",
        profile="default",
        items=[StructureArtifact(path=root / "structures" / "input.xyz", elements=["O", "H", "H"])],
        steps=[
            CalculationStep(
                kind=StepKind.CASSCF,
                spec={"casscf": {"active_electrons": 2, "active_orbitals": 2}},
            )
        ],
    )


def _execute_casscf_fixture(root: Path, calls: dict[str, int]) -> ExecutionResult:
    """Execute the CAS counterexample plan with fake QC (counted) dispatch."""

    def cas(request: object) -> CalculationResult:
        calls["casscf"] = calls.get("casscf", 0) + 1
        return CalculationResult(energy=-108.5, metadata={"multireference": {"converged": True}})

    with patch.dict(executor_module._PRIMITIVE_DISPATCH, {StepKind.CASSCF: cas}):
        with patch.object(executor_module, "current_config_digest", lambda: None):
            return CalculationPlanExecutor().execute(_casscf_fixture_plan(root), root)


def test_v2_casscf_not_converged_never_adopted(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The ``step_result.json`` entry refuses a converged=false receipt.

    Identity + artifact digests verify, but the shared science validator
    refuses the stale ``completed`` fact — recovery conservatively
    recomputes exactly once and never reports ``reused_from_attempt``.
    """
    root = _copy_fixture("v2_casscf_not_converged", tmp_path)
    stored = _stored_identity(root)
    identity = compute_identity(_casscf_fixture_plan(root))
    assert identity.plan_identity == stored["plan_identity"]
    assert list(identity.step_identities) == stored["step_identities"]

    calls: dict[str, int] = {}
    with caplog.at_level(logging.INFO):
        result = _execute_casscf_fixture(root, calls)

    assert "recovery.step_not_adopted" in caplog.text
    assert "cas_not_converged" in caplog.text
    assert calls.get("casscf", 0) == 1, "refused receipt must conservatively recompute"
    assert result.is_completed
    state = result.step_states[0]
    assert state.executed_this_run is True
    assert state.reused_from_attempt is None


def test_v2_casscf_not_converged_record_entry_never_restored(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The ``scientific_result.json`` entry never restores converged=false.

    With the receipt removed (legacy publication-only branch), the SAME
    validator refuses the record's false fact — the step recomputes
    instead of being restored as ``completed`` without QC evidence.
    """
    root = _copy_fixture("v2_casscf_not_converged", tmp_path)
    (root / "WORK" / "08_CASSCF" / "step_result.json").unlink()
    runtime = root / "WORK" / "00_RUNTIME"
    payload = json.loads((runtime / "checkpoint.json").read_text(encoding="utf-8"))
    payload["step_states"][0]["status"] = "pending"
    payload["step_states"][0]["result_ref"] = None
    (runtime / "checkpoint.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    calls: dict[str, int] = {}
    with caplog.at_level(logging.INFO):
        result = _execute_casscf_fixture(root, calls)

    assert "recovery.scientific_result_not_reusable" in caplog.text
    assert "cas_not_converged" in caplog.text
    assert calls.get("casscf", 0) == 1, "record refusal must conservatively recompute"
    assert result.is_completed
    assert result.step_states[0].status == "completed"
