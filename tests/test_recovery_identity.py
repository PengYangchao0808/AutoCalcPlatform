"""D06 scientific request identity v2 acceptance suite (plan todo 10).

Covers: content-bound fingerprints, effective-parameter binding, two-layer
(plan/step) identity, path remapping, legacy checkpoint conservative
compat, config-digest recompute, artifact-key content digests, and the
r12 batch charge/multiplicity fingerprint contract.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pytest

from acp.calculations.batch._items import BatchStructureItem, item_cache_key
from acp.calculations.batch.engine import _batch_plan_fingerprint
from acp.calculations.checkpoint import load_checkpoint, write_checkpoint
from acp.calculations.contracts import (
    CalculationPlan,
    CalculationResult,
    CalculationStep,
    Checkpoint,
    OptimizationMode,
    StepKind,
    StructureArtifact,
    StructureRole,
)
from acp.calculations.executor import (
    CalculationPlanExecutor,
    _plan_fingerprint,
    legacy_plan_fingerprint,
)
from acp.calculations.identity import (
    IDENTITY_SCHEMA,
    IdentityInputMissing,
    compute_identity,
    identity_fingerprint,
    inputs_not_newer_than,
    resolve_stored_location,
)
from tests.conftest import FakeBackend

XYZ = "1\ninput\nC 0.0 0.0 0.0\n"


def _write_xyz(path: Path, text: str = XYZ) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _plan(
    task_root: Path,
    *,
    steps: list[CalculationStep] | None = None,
    role: StructureRole = StructureRole.MINIMUM,
    profile: str = "r2SCAN-3c",
    item_path: Path | None = None,
) -> CalculationPlan:
    input_path = item_path if item_path is not None else _write_xyz(task_root / "input.xyz")
    return CalculationPlan(
        workflow="test",
        profile=profile,
        items=[
            StructureArtifact(path=input_path, elements=["C"], role=role, source="test"),
        ],
        steps=steps or [CalculationStep(kind=StepKind.SINGLEPOINT)],
    )


# ── content binding ──────────────────────────────────────────────────────


def test_content_change_same_path_changes_fingerprint(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    before = compute_identity(plan).plan_identity
    _write_xyz(tmp_path / "input.xyz", "1\ninput\nHe 1.0 2.0 3.0\n")
    after = compute_identity(plan).plan_identity
    assert before != after
    assert before.startswith("v2:") and after.startswith("v2:")
    assert len(before) == len("v2:") + 32


def test_content_change_rejects_reuse_and_reruns(
    fake_backend: FakeBackend, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Same path, new content → v2 mismatch → no reuse, primitive re-runs."""
    plan = _plan(tmp_path)
    executor = CalculationPlanExecutor()
    first = executor.execute(plan, task_root=tmp_path)
    assert first.is_completed
    calls_after_first = len(fake_backend.calls)
    assert calls_after_first > 0

    _write_xyz(tmp_path / "input.xyz", "1\ninput\nHe 1.0 2.0 3.0\n")
    with caplog.at_level(logging.INFO, logger="acp.calculations.checkpoint"):
        second = executor.execute(plan, task_root=tmp_path)
    assert second.is_completed
    assert len(fake_backend.calls) > calls_after_first
    assert "identity_fingerprint_mismatch" in caplog.text

    payload = json.loads(
        (tmp_path / "WORK" / "00_RUNTIME" / "checkpoint.json").read_text(encoding="utf-8")
    )
    assert payload["identity_schema"] == IDENTITY_SCHEMA


def test_role_change_changes_fingerprint(tmp_path: Path) -> None:
    minimum = compute_identity(_plan(tmp_path, role=StructureRole.MINIMUM))
    transition = compute_identity(_plan(tmp_path, role=StructureRole.TRANSITION_STATE))
    assert minimum.plan_identity != transition.plan_identity


def test_missing_item_file_raises_identity_input_missing(tmp_path: Path) -> None:
    plan = _plan(tmp_path, item_path=tmp_path / "absent.xyz")
    with pytest.raises(IdentityInputMissing):
        compute_identity(plan)


# ── effective-parameter binding ──────────────────────────────────────────


def test_effective_params_change_changes_fingerprint(tmp_path: Path) -> None:
    base = compute_identity(
        _plan(
            tmp_path,
            steps=[CalculationStep(kind=StepKind.SINGLEPOINT, spec={"method": "r2SCAN-3c"})],
        )
    )
    other_method = compute_identity(
        _plan(
            tmp_path,
            steps=[CalculationStep(kind=StepKind.SINGLEPOINT, spec={"method": "B3LYP"})],
        )
    )
    assert base.plan_identity != other_method.plan_identity

    # profile resolves into the default method when the step carries none
    profile_a = compute_identity(_plan(tmp_path, profile="r2SCAN-3c"))
    profile_b = compute_identity(_plan(tmp_path, profile="HF"))
    assert profile_a.plan_identity != profile_b.plan_identity

    # resolved defaults ride the effective layer (basis clamp from method meta)
    with_basis = compute_identity(
        _plan(
            tmp_path,
            steps=[
                CalculationStep(
                    kind=StepKind.SINGLEPOINT,
                    spec={"method": "r2SCAN-3c", "basis": "def2-TZVP"},
                )
            ],
        )
    )
    assert with_basis.plan_identity != base.plan_identity
    assert base.plan_identity.startswith("v2:")


def test_dict_key_order_does_not_change_fingerprint(tmp_path: Path) -> None:
    spec_a = {"method": "B3LYP", "basis": "def2-SVP", "charge": 0}
    spec_b = {"charge": 0, "basis": "def2-SVP", "method": "B3LYP"}
    a = compute_identity(
        _plan(tmp_path, steps=[CalculationStep(kind=StepKind.SINGLEPOINT, spec=spec_a)])
    )
    b = compute_identity(
        _plan(tmp_path, steps=[CalculationStep(kind=StepKind.SINGLEPOINT, spec=spec_b)])
    )
    assert a.plan_identity == b.plan_identity
    # canonicalisation itself is order-insensitive too
    assert identity_fingerprint({"a": 1, "b": {"x": 1, "y": 2}}) == identity_fingerprint(
        {"b": {"y": 2, "x": 1}, "a": 1}
    )


def test_task_option_changes_change_identity_all_four_kinds(tmp_path: Path) -> None:
    def fingerprint_for(kind: StepKind, spec: dict) -> str:
        return compute_identity(
            _plan(tmp_path, steps=[CalculationStep(kind=kind, spec=spec)])
        ).plan_identity

    scan_a = {"coordinates": [[1, 2, 1.0, 3.0]], "points": 11, "mode": "relaxed"}
    scan_b = {"coordinates": [[1, 2, 1.5, 3.0]], "points": 11, "mode": "relaxed"}
    scan_c = {"coordinates": [[1, 2, 1.0, 3.0]], "points": 21, "mode": "relaxed"}
    assert fingerprint_for(StepKind.SCAN, scan_a) != fingerprint_for(StepKind.SCAN, scan_b)
    assert fingerprint_for(StepKind.SCAN, scan_a) != fingerprint_for(StepKind.SCAN, scan_c)

    opt_a = {"constraints": [{"atoms": [1, 2], "length": 1.5}], "ts": False}
    opt_b = {"constraints": [{"atoms": [1, 2], "length": 1.8}], "ts": False}
    assert fingerprint_for(StepKind.OPTIMIZE, opt_a) != fingerprint_for(StepKind.OPTIMIZE, opt_b)

    irc_a = {"directions": ["forward", "reverse"]}
    irc_b = {"directions": ["forward"]}
    assert fingerprint_for(StepKind.SINGLEPOINT, irc_a) != fingerprint_for(
        StepKind.SINGLEPOINT, irc_b
    )

    casscf_a = {"casscf": {"active_electrons": 2, "active_orbitals": 2}}
    casscf_b = {"casscf": {"active_electrons": 4, "active_orbitals": 4}}
    assert fingerprint_for(StepKind.CASSCF, casscf_a) != fingerprint_for(StepKind.CASSCF, casscf_b)


# ── position separation / path remapping ─────────────────────────────────


def test_same_content_different_path_unchanged_and_remapped(tmp_path: Path) -> None:
    old_path = _write_xyz(tmp_path / "old" / "input.xyz")
    plan_old = _plan(tmp_path, item_path=old_path)
    before = compute_identity(plan_old)

    new_path = _write_xyz(tmp_path / "new_location" / "input.xyz")
    plan_new = _plan(tmp_path, item_path=new_path)
    after = compute_identity(plan_new, previous_paths={"0": str(old_path)})

    assert after.plan_identity == before.plan_identity
    assert after.path_remaps
    assert after.path_remaps[0]["event"] == "identity.path_remapped"
    assert after.path_remaps[0]["from"] == str(old_path)
    assert after.path_remaps[0]["to"] == str(new_path)


def test_run_root_migration_relocation_is_legal_remap(tmp_path: Path) -> None:
    old_path = _write_xyz(tmp_path / "old_root" / "task" / "input.xyz")
    before = compute_identity(_plan(tmp_path, item_path=old_path))

    new_root = tmp_path / "new_root" / "task"
    new_path = _write_xyz(new_root / "input.xyz")
    old_path.unlink()  # migrate_run_root-style move: the old tree is gone
    after = compute_identity(
        _plan(tmp_path, item_path=new_path), previous_paths={"0": str(old_path)}
    )

    assert after.plan_identity == before.plan_identity
    assert after.path_remaps and after.path_remaps[0]["event"] == "identity.path_remapped"


def test_resolve_stored_location_prefers_checkpoint_then_work_dir() -> None:
    assert resolve_stored_location("stored/input.xyz", "record/input.xyz") == "stored/input.xyz"
    assert resolve_stored_location(None, "record/input.xyz") == "record/input.xyz"
    assert resolve_stored_location("", None) is None


# ── serialization stability (no repr / physical-position dependence) ─────


def test_v2_stable_across_sibling_field_moves(tmp_path: Path) -> None:
    """Typed dataclass steps vs raw mapping steps with the same content
    must hash identically — the identity never sees repr or field order."""
    input_path = _write_xyz(tmp_path / "input.xyz")
    spec = {"method": "B3LYP", "basis": "def2-SVP"}
    typed = CalculationPlan(
        workflow="test",
        profile="default",
        items=[StructureArtifact(path=input_path, elements=["C"])],
        steps=[
            CalculationStep(
                kind=StepKind.SINGLEPOINT,
                mode=OptimizationMode.UNCONSTRAINED,
                spec=dict(spec),
            )
        ],
    )
    mapping = CalculationPlan(
        workflow="test",
        profile="default",
        items=[{"path": str(input_path), "elements": ["C"]}],
        steps=[{"kind": "singlepoint", "mode": "unconstrained", "spec": dict(spec)}],
    )
    assert compute_identity(typed).plan_identity == compute_identity(mapping).plan_identity
    assert _plan_fingerprint is legacy_plan_fingerprint


def test_dispatch_rewiring_keeps_fingerprint_stable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sibling executor rewiring (dispatch to cccp.calculation.run_*)
    must not move the v2 fingerprint of the same plan."""
    import acp.calculations.executor as executor_module

    plan = _plan(tmp_path)
    before = compute_identity(plan).plan_identity

    rewired = dict(executor_module._PRIMITIVE_DISPATCH)
    rewired[StepKind.SINGLEPOINT] = lambda request: CalculationResult(status="completed")
    monkeypatch.setattr(executor_module, "_PRIMITIVE_DISPATCH", rewired)

    assert compute_identity(plan).plan_identity == before
    executor_module.CalculationPlanExecutor().execute(plan, task_root=tmp_path)
    payload = json.loads(
        (tmp_path / "WORK" / "00_RUNTIME" / "checkpoint.json").read_text(encoding="utf-8")
    )
    assert payload["plan_fingerprint"] == before


# ── two-layer identity: prefix step identities ───────────────────────────


def _three_step_plan(tmp_path: Path, specs: list[dict | None]) -> CalculationPlan:
    steps = [
        CalculationStep(kind=kind, spec=spec)
        for kind, spec in zip(
            (StepKind.OPTIMIZE, StepKind.FREQUENCY, StepKind.SINGLEPOINT),
            specs,
            strict=True,
        )
    ]
    return _plan(tmp_path, steps=steps)


def test_only_last_step_change_keeps_prior_step_identities(tmp_path: Path) -> None:
    base = compute_identity(_three_step_plan(tmp_path, [None, None, {"method": "r2SCAN-3c"}]))
    changed = compute_identity(_three_step_plan(tmp_path, [None, None, {"method": "B3LYP"}]))
    assert changed.plan_identity != base.plan_identity
    assert changed.step_identities[0] == base.step_identities[0]
    assert changed.step_identities[1] == base.step_identities[1]
    assert changed.step_identities[2] != base.step_identities[2]


def test_upstream_change_invalidates_dependent_step_identities(tmp_path: Path) -> None:
    base = compute_identity(_three_step_plan(tmp_path, [{"method": "r2SCAN-3c"}, None, None]))
    changed = compute_identity(_three_step_plan(tmp_path, [{"method": "B3LYP"}, None, None]))
    assert changed.step_identities[0] != base.step_identities[0]
    # dependents inherit the prefix → invalidated
    assert changed.step_identities[1] != base.step_identities[1]
    assert changed.step_identities[2] != base.step_identities[2]
    # the layers are distinct namespaces
    assert base.plan_identity not in base.step_identities


# ── legacy checkpoint conservative compatibility ─────────────────────────


def _legacy_checkpoint(fp: str = "legacy-fp") -> Checkpoint:
    return Checkpoint(
        task_id="t",
        workflow="test",
        plan_fingerprint=fp,
        step_states=[{"kind": "singlepoint", "status": "completed", "energy": -1.0}],
        items_state={},
        resume_count=1,
    )


def test_legacy_schema_default_conservative_recompute(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    checkpoint_dir = tmp_path / "WORK" / "00_RUNTIME"
    write_checkpoint(checkpoint_dir, _legacy_checkpoint())
    with caplog.at_level(logging.INFO, logger="acp.calculations.checkpoint"):
        assert load_checkpoint(checkpoint_dir, "legacy-fp") is None
    assert "identity_unverifiable_legacy" in caplog.text


def test_legacy_schema_reusable_with_explicit_compat_switch(tmp_path: Path) -> None:
    input_path = _write_xyz(tmp_path / "input.xyz")
    checkpoint_dir = tmp_path / "WORK" / "00_RUNTIME"
    write_checkpoint(checkpoint_dir, _legacy_checkpoint())
    os.utime(input_path, (1_700_000_000, 1_700_000_000))  # input older than checkpoint

    assert load_checkpoint(checkpoint_dir, "legacy-fp") is None  # default: conservative
    reused = load_checkpoint(checkpoint_dir, "legacy-fp", allow_legacy_fingerprint=True)
    assert reused is not None
    assert reused.identity_schema == 1
    # explicit-compat preconditions: input not newer + completed artifacts present
    assert inputs_not_newer_than(checkpoint_dir / "checkpoint.json", [input_path]) is True
    assert any(s.get("status") == "completed" for s in reused.step_states)

    # a newer input revokes the legacy reuse precondition
    future = checkpoint_dir.joinpath("checkpoint.json").stat().st_mtime + 60
    os.utime(input_path, (future, future))
    assert inputs_not_newer_than(checkpoint_dir / "checkpoint.json", [input_path]) is False


def test_legacy_fingerprint_mismatch_returns_none(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    checkpoint_dir = tmp_path / "WORK" / "00_RUNTIME"
    write_checkpoint(checkpoint_dir, _legacy_checkpoint())
    with caplog.at_level(logging.INFO, logger="acp.calculations.checkpoint"):
        assert load_checkpoint(checkpoint_dir, "other-fp", allow_legacy_fingerprint=True) is None
    assert "identity_unverifiable_legacy" in caplog.text


def test_unknown_schema_conservative_recompute(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    checkpoint_dir = tmp_path / "WORK" / "00_RUNTIME"
    write_checkpoint(checkpoint_dir, _legacy_checkpoint())
    path = checkpoint_dir / "checkpoint.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["identity_schema"] = 99
    path.write_text(json.dumps(payload), encoding="utf-8")
    with caplog.at_level(logging.INFO, logger="acp.calculations.checkpoint"):
        assert load_checkpoint(checkpoint_dir, "legacy-fp", allow_legacy_fingerprint=True) is None
    assert "identity_unknown_schema" in caplog.text


def test_v2_checkpoint_roundtrip_and_mismatch_does_not_raise(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    checkpoint_dir = tmp_path / "WORK" / "00_RUNTIME"
    v2 = Checkpoint(
        task_id="t",
        workflow="test",
        plan_fingerprint="v2:abc",
        step_states=[],
        items_state={},
        resume_count=0,
        identity_schema=IDENTITY_SCHEMA,
    )
    write_checkpoint(checkpoint_dir, v2)
    assert load_checkpoint(checkpoint_dir, "v2:abc") == v2
    with caplog.at_level(logging.INFO, logger="acp.calculations.checkpoint"):
        assert load_checkpoint(checkpoint_dir, "v2:other") is None
    assert "identity_fingerprint_mismatch" in caplog.text


# ── config digest → conservative recompute ───────────────────────────────


def test_config_digest_change_triggers_recompute(
    fake_backend: FakeBackend,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    plan = _plan(tmp_path)
    executor = CalculationPlanExecutor()
    first = executor.execute(plan, task_root=tmp_path)
    assert first.is_completed
    calls_after_first = len(fake_backend.calls)

    monkeypatch.setattr(
        "acp.calculations.executor.current_config_digest",
        lambda: "sha256:changed-config",
    )
    with caplog.at_level(logging.INFO, logger="acp.calculations.executor"):
        second = executor.execute(plan, task_root=tmp_path)
    assert second.is_completed
    assert len(fake_backend.calls) > calls_after_first  # conservative recompute
    assert "identity.config_digest_changed" in caplog.text

    record = json.loads(
        (tmp_path / "WORK" / "00_RUNTIME" / "checkpoint.json").read_text(encoding="utf-8")
    )["items_state"]["execution_record"]
    assert record["config_digest"] == "sha256:changed-config"
    assert record["code_release"]
    assert "attempt" in record
    assert "software_version" in record


def test_config_digest_stable_resume_keeps_completed_steps(
    fake_backend: FakeBackend, tmp_path: Path
) -> None:
    plan = _plan(tmp_path)
    executor = CalculationPlanExecutor()
    assert executor.execute(plan, task_root=tmp_path).is_completed
    calls = len(fake_backend.calls)
    again = executor.execute(plan, task_root=tmp_path)
    assert again.is_completed
    assert len(fake_backend.calls) == calls  # digest unchanged → adopted


# ── artifact keys bind by content digest ─────────────────────────────────


def test_dependency_artifact_relocation_keeps_identity(tmp_path: Path) -> None:
    log_a = _write_xyz(tmp_path / "logs_a" / "freq.log", "frequency log content\n")
    base = compute_identity(
        _plan(
            tmp_path,
            steps=[
                CalculationStep(
                    kind=StepKind.THERMOCHEMISTRY,
                    spec={"freq_log_path": str(log_a), "temperature": 298.15},
                )
            ],
        )
    )
    log_b = _write_xyz(tmp_path / "logs_b" / "freq.log", "frequency log content\n")
    moved = compute_identity(
        _plan(
            tmp_path,
            steps=[
                CalculationStep(
                    kind=StepKind.THERMOCHEMISTRY,
                    spec={"freq_log_path": str(log_b), "temperature": 298.15},
                )
            ],
        )
    )
    assert moved.plan_identity == base.plan_identity

    changed = compute_identity(
        _plan(
            tmp_path,
            steps=[
                CalculationStep(
                    kind=StepKind.THERMOCHEMISTRY,
                    spec={"freq_log_path": str(log_b), "temperature": 350.0},
                )
            ],
        )
    )
    assert changed.plan_identity != base.plan_identity

    # unreadable artifact location is an explicit error, never a raw path hash
    with pytest.raises(IdentityInputMissing):
        compute_identity(
            _plan(
                tmp_path,
                steps=[
                    CalculationStep(
                        kind=StepKind.THERMOCHEMISTRY,
                        spec={"freq_log_path": str(tmp_path / "gone.log")},
                    )
                ],
            )
        )


# ── r12 P1: batch fingerprints carry resolved charge/multiplicity ────────


def _batch_item() -> BatchStructureItem:
    return BatchStructureItem(
        item_id="i1",
        name="M",
        tag="INT",
        xyz="2\nTAG: INT\nH 0 0 0\nH 0 0 0.7\n",
        candidate_id="i1",
    )


def test_batch_fingerprint_includes_job_charge_multiplicity() -> None:
    item = _batch_item()
    base = _batch_plan_fingerprint(
        [item], "opt_only", None, "batch", job_charge=0, job_multiplicity=1
    )
    charged = _batch_plan_fingerprint(
        [item], "opt_only", None, "batch", job_charge=1, job_multiplicity=1
    )
    doubled = _batch_plan_fingerprint(
        [item], "opt_only", None, "batch", job_charge=0, job_multiplicity=2
    )
    assert len({base, charged, doubled}) == 3

    key_base = item_cache_key(item, "opt_only", "", "", default_charge=0, default_multiplicity=1)
    key_charged = item_cache_key(item, "opt_only", "", "", default_charge=1, default_multiplicity=1)
    key_doubled = item_cache_key(item, "opt_only", "", "", default_charge=0, default_multiplicity=2)
    assert len({key_base, key_charged, key_doubled}) == 3

    # item-pinned charge wins over the job default (resolved value enters)
    pinned = BatchStructureItem(
        item_id="i1",
        name="M",
        tag="INT",
        xyz="2\nTAG: INT\nH 0 0 0\nH 0 0 0.7\n",
        candidate_id="i1",
        charge=1,
        multiplicity=1,
    )
    assert item_cache_key(
        pinned, "opt_only", "", "", default_charge=0, default_multiplicity=1
    ) == item_cache_key(pinned, "opt_only", "", "", default_charge=5, default_multiplicity=3)


def test_prechange_batch_checkpoint_full_recompute(
    fake_backend: FakeBackend, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A pre-change-format batch checkpoint (fingerprint without the
    resolved charge/multiplicity bytes) must never authorize reuse."""
    from acp.calculations.batch.engine import BatchOptimizeEngine

    items = [_batch_item()]
    engine = BatchOptimizeEngine(
        work_root=tmp_path / "task" / "WORK",
        result_root=tmp_path / "task" / "RESULT",
    )
    first = engine.run(items, profile="opt_only", charge=0)
    assert all(record.status == "completed" for record in first.items)
    calls_after_first = len(fake_backend.calls)

    checkpoint_path = tmp_path / "task" / "WORK" / "00_RUNTIME" / "checkpoint.json"
    payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert payload["identity_schema"] == 1  # batch keeps schema=1
    payload["plan_fingerprint"] = "pre-change-format-fingerprint-without-charge-bytes"
    checkpoint_path.write_text(json.dumps(payload), encoding="utf-8")

    with caplog.at_level(logging.INFO, logger="acp.calculations.checkpoint"):
        second = engine.run(items, profile="opt_only", charge=0)
    assert all(record.status == "completed" for record in second.items)
    assert len(fake_backend.calls) > calls_after_first  # full recompute, no reuse
    assert "identity_unverifiable_legacy" in caplog.text
