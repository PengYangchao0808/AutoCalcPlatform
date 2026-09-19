"""Edit-and-recalculate tests (docs/ACP_Edit_And_Recalculate_Plan.md §14).

Coverage layers:
* pure adapter/serialisation round-trips for all 11 active workflows (E01/E02)
* manager-level in-place/new-job/idempotency/conflict behaviour (E07–E13)
* API-level draft/preview/submit endpoints incl. retired-view rules (E10)
"""

# pyright: reportMissingTypeArgument=false, reportAny=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnusedCallResult=false, reportFunctionMemberAccess=false, reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportReturnType=false, reportInvalidTypeForm=false
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from acp.scheduler.job_edit import (
    EDIT_ACTIVE_WORKFLOWS,
    EditConflictError,
    JobEditOperationStore,
    attempt_number,
    audit_workflow_edit_coverage,
    build_edit_draft,
    compute_payload_hash,
    compute_preview_fingerprint,
    compute_source_revision,
    diff_editable_specs,
    editable_spec_from_record,
    normalize_for_compare,
    resolve_last_structure,
    spec_semantics_hash,
)
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.manager import JobManager

XYZ_COOH = "3\n\nO 0.0 0.0 0.0\nC 1.2 0.0 0.0\nH 2.0 0.0 0.0\n"


# ---------------------------------------------------------------------------
# Representative specs per active workflow (shapes follow catalog METHOD_SCHEMAS
# and the Workbench v2 submit payloads).
# ---------------------------------------------------------------------------


def _spec(workflow: str) -> JobSpec:
    base: dict[str, Any] = {"workflow": workflow}
    if workflow == "singlepoint":
        base.update(
            input={"source_type": "xyz_text", "source": XYZ_COOH, "charge": 0, "multiplicity": 1},
            method={
                "schema_id": "dft_singlepoint",
                "profile_id": "default",
                "levels": {
                    "sp": {
                        "functional": "r2SCAN",
                        "basis": "def2-TZVP",
                        "dispersion": "none",
                        "solvent_model": "none",
                        "solvent": "",
                        "grid": "def2",
                        "scf_convergence": "normal",
                    }
                },
            },
            resources={"nproc": 8, "mem": 16},
        )
    elif workflow == "optimize":
        base.update(
            input={"source_type": "xyz_text", "source": XYZ_COOH, "charge": 0, "multiplicity": 1},
            method={
                "schema_id": "dft_optimize",
                "levels": {
                    "opt": {"functional": "B97-3c", "max_steps": 300, "opt_convergence": "tight"}
                },
            },
            resources={"nproc": 8},
        )
    elif workflow == "frequency":
        base.update(
            input={"source_type": "xyz_text", "source": XYZ_COOH},
            method={"schema_id": "dft_frequency", "levels": {"freq": {"functional": "B97-3c"}}},
        )
    elif workflow == "scan":
        base.update(
            input={"source_type": "xyz_text", "source": XYZ_COOH},
            method={
                "schema_id": "dft_scan",
                "scan_coordinates": ["1,2,1.0,2.0", "2,3,1.0,1.8"],
                "scan_points": 21,
                "levels": {"scan": {"functional": "B97-3c"}},
            },
        )
    elif workflow == "irc":
        base.update(
            input={
                "source_type": "xyz_text",
                "source": XYZ_COOH,
                "input_role": "transition_state",
                "directions": ["both"],
            },
            method={
                "schema_id": "irc",
                "method": "r2SCAN-3c",
                "basis": "def2-TZVP",
                "maxpoints": 100,
                "step": 0.1,
            },
        )
    elif workflow == "casscf":
        base.update(
            input={"source_type": "xyz_text", "source": XYZ_COOH, "charge": 0, "multiplicity": 1},
            method={
                "schema_id": "casscf",
                "basis": "def2-SVP",
                "casscf": {
                    "active_electrons": 6,
                    "active_orbitals": 6,
                    "nroots": 2,
                    "dynamic_correlation": "none",
                    "frozen_core": True,
                    "state_weights": [0.6, 0.4],
                },
            },
        )
    elif workflow == "xtb_optimize":
        base.update(
            input={"source_type": "xyz_text", "source": XYZ_COOH, "charge": 0, "multiplicity": 1},
            method={
                "schema_id": "xtb_optimize",
                "levels": {
                    "xtb": {"gfn": 2, "opt_level": "tight", "solvent_model": "none", "solvent": ""}
                },
            },
        )
    elif workflow == "Confsearch":
        base.update(
            input={"source_type": "xyz_text", "source": "CCO", "charge": 0, "multiplicity": 1},
            method={
                "schema_id": "confsearch_unified",
                "protocol": "censo-crest",
                "profile": "default",
                "refinement_policy": "screen",
                "ewin": 6.0,
                "md_temperature": 500,
                "md_seeds": 3,
                "levels": {
                    "dft_opt": {"functional": "wB97X-D4"},
                    "refinement_sp": {"functional": "wB97X-D4"},
                },
            },
        )
    elif workflow == "PESsearch":
        base.update(
            input={
                "source_type": "xyz_text",
                "source": XYZ_COOH,
                "scan_request": {
                    "mode": "bond_length_scan",
                    "source": {
                        "source_type": "xyz_text",
                        "xyz_text": XYZ_COOH,
                        "charge": 0,
                        "multiplicity": 1,
                    },
                    "coordinate": {
                        "kind": "distance",
                        "atoms": [1, 2],
                        "start": 1.0,
                        "end": 2.0,
                        "n_points": 15,
                    },
                },
            },
            method={"schema_id": "pes_scan", "mode": "bond_length_scan", "profile_id": "default"},
        )
    elif workflow == "BatchOptimize":
        base.update(
            input={
                "source_type": "batch_structures",
                "charge": 0,
                "multiplicity": 1,
                "items": [
                    {"name": "TS1", "xyz": XYZ_COOH, "tag": "TS", "candidate_id": "pes_ts_001"},
                    {"name": "INT1", "xyz": XYZ_COOH, "tag": "INT"},
                ],
            },
            method={
                "schema_id": "batch_optimize",
                "profile": "opt_freq_sp_thermo",
                "optimization_method": "wB97X-D4",
                "optimization_basis": "def2-TZVP",
                "single_point_method": "DLPNO-CCSD(T)",
                "temperature": 298.15,
                "batch_roles": {
                    "int": {"method": "wB97X-D4", "opt_convergence": "tight"},
                    "ts": {"method": "wB97X-D4", "opt_trust_radius": 0.1},
                },
            },
        )
    elif workflow == "nmr":
        base.update(
            input={
                "source_type": "candidates",
                "candidates": [
                    {"source_type": "xyz_text", "source": XYZ_COOH, "charge": 0, "multiplicity": 1}
                ],
                "experiment": {"mode": "assigned", "path": "/nonexistent/exp.csv"},
            },
            method={
                "schema_id": "nmr",
                "nuclei": ["1H", "13C"],
                "boltzmann_temp": 298.15,
                "error_model": "goodman",
                "enumerate": False,
            },
        )
    else:  # pragma: no cover - guard for typos in the parametrisation
        raise AssertionError(f"unknown workflow {workflow}")
    return JobSpec(**base)


ACTIVE_WORKFLOWS = sorted(EDIT_ACTIVE_WORKFLOWS)


def _record(workflow: str, *, status: JobStatus = JobStatus.COMPLETED) -> JobRecord:
    return JobRecord(
        id=f"20260919_001_{workflow}",
        spec=_spec(workflow),
        status=status,
        work_dir=f"/tmp/acp/{workflow}",
    )


# ---------------------------------------------------------------------------
# P1: coverage registry + pure projections
# ---------------------------------------------------------------------------


def test_coverage_audit_complete() -> None:
    """Every catalog-active workflow must be edit-registered (plan §1)."""
    audit = audit_workflow_edit_coverage()
    assert audit == {"missing": [], "stale": []}


@pytest.mark.parametrize("workflow", ACTIVE_WORKFLOWS)
def test_editable_spec_round_trip_is_lossless(workflow: str) -> None:
    """E02: normalize(original) == normalize(draft → resubmit) per workflow."""
    record = _record(workflow)
    draft = editable_spec_from_record(record)
    rebuilt = JobSpec(
        workflow=draft["workflow"],
        name=record.spec.name,
        input=draft["input"],
        method=draft["method"],
        resources=draft["resources"],
        tags=draft["tags"],
        node_tags=draft["node_tags"],
        molecule_name=draft["molecule_name"],
        task_name=draft["task_name"],
        remark=draft["remark"],
    )
    assert spec_semantics_hash(rebuilt) == spec_semantics_hash(record.spec)
    assert normalize_for_compare(draft["input"]) == normalize_for_compare(record.spec.input)
    assert normalize_for_compare(draft["method"]) == normalize_for_compare(record.spec.method)


@pytest.mark.parametrize("workflow", ACTIVE_WORKFLOWS)
def test_draft_capabilities_and_input_refs(workflow: str) -> None:
    """E01: the draft opens with complete parameters, not fresh defaults."""
    record = _record(workflow, status=JobStatus.FAILED)
    draft = build_edit_draft(record)
    assert draft["workflow"] == workflow
    assert draft["workflow_status"] == "active"
    assert draft["capabilities"]["can_edit"] is True
    assert draft["capabilities"]["can_in_place"] is True
    assert draft["capabilities"]["can_new_job"] is True
    assert draft["capabilities"]["disabled_reasons"] == []
    assert draft["source_revision"].startswith("sr_")
    assert draft["attempt"] == 1
    assert draft["editable_spec"]["method"], "method payload must survive the projection"
    if workflow == "BatchOptimize":
        assert draft["input_refs"]["original"]["items_count"] == 2
        assert draft["preserved_fields"], "batch profile/roles must be preserved"
    if workflow == "scan":
        assert "scan_coordinates" in draft["preserved_fields"]
    if workflow == "casscf":
        assert "casscf" in draft["preserved_fields"]


def test_draft_missing_fields_detected() -> None:
    record = _record("casscf")
    record.spec = replace(
        record.spec, method={"schema_id": "casscf", "casscf": {"active_electrons": 0}}
    )
    draft = build_edit_draft(record)
    assert "method.casscf.active_electrons" in draft["missing_fields"]
    assert "method.casscf.active_orbitals" in draft["missing_fields"]


def test_draft_retired_workflow_is_view_only() -> None:
    """E10: retired workflows view their config but cannot recalc in place."""
    record = _record("Confsearch")
    record.spec = replace(record.spec, workflow="ensemble")
    draft = build_edit_draft(record)
    assert draft["capabilities"]["can_edit"] is False
    assert draft["capabilities"]["can_in_place"] is False
    assert draft["capabilities"]["can_new_job"] is False
    assert draft["migration_hint"]
    assert draft["workflow_status"] == "retired"
    assert any("ensemble" in reason for reason in draft["capabilities"]["disabled_reasons"])


def test_draft_active_status_blocks_in_place_only() -> None:
    record = _record("singlepoint", status=JobStatus.RUNNING)
    draft = build_edit_draft(record)
    assert draft["capabilities"]["can_edit"] is True
    assert draft["capabilities"]["can_in_place"] is False
    assert draft["capabilities"]["can_new_job"] is True
    assert any("非终态" in reason for reason in draft["capabilities"]["disabled_reasons"])


def test_source_revision_ignores_progress_noise() -> None:
    """Plan §9: ordinary progress updates must not fabricate conflicts."""
    record = _record("singlepoint")
    before = compute_source_revision(record)
    record.status = JobStatus.RUNNING
    record.progress = 0.5
    record.pid = 4321
    record.result = {"state": {"stage": 2}}
    assert compute_source_revision(record) == before
    record.spec = replace(record.spec, method={"levels": {"sp": {"functional": "HF"}}})
    assert compute_source_revision(record) != before
    record2 = _record("singlepoint")
    record2.result = {"attempts": 2}
    assert compute_source_revision(record2) != before


def test_diff_editable_specs_semantics() -> None:
    old = {
        "method": {"levels": {"sp": {"functional": "r2SCAN", "basis": "def2-TZVP"}}},
        "resources": {"nproc": 8},
        "input": {"source_type": "xyz_text", "source": "a"},
        "remark": "r1",
    }
    new = {
        "method": {"levels": {"sp": {"functional": "wB97X-D4", "basis": "def2-TZVP"}}},
        "resources": {"nproc": 16},
        "input": {"source_type": "xyz_text", "source": "a"},
        "remark": "r1",
    }
    diff = diff_editable_specs(old, new)
    paths = {entry["path"] for entry in diff}
    assert paths == {"method.levels.sp.functional", "resources.nproc"}
    kinds = {entry["path"]: entry["kind"] for entry in diff}
    assert kinds["method.levels.sp.functional"] == "method"
    assert kinds["resources.nproc"] == "resource"
    by_path = {entry["path"]: entry for entry in diff}
    assert by_path["method.levels.sp.functional"]["old"] == "r2SCAN"
    assert by_path["method.levels.sp.functional"]["new"] == "wB97X-D4"
    assert diff_editable_specs(old, dict(old)) == []


def test_diff_preserves_false_zero_null_distinctions() -> None:
    old = {"method": {"frozen_core": False, "nroots": 0, "solvent": None, "tags": [1, 2]}}
    new = {"method": {"frozen_core": None, "nroots": 0, "solvent": "", "tags": [2, 1]}}
    diff = diff_editable_specs(old, new)
    paths = {entry["path"] for entry in diff}
    assert "method.frozen_core" in paths  # False vs None differ
    assert "method.nroots" not in paths
    assert "method.solvent" in paths  # None vs ""
    assert "method.tags" in paths  # order is semantic for item lists


def test_preview_fingerprint_binds_configuration() -> None:
    a = compute_preview_fingerprint(
        "j1", "sr_x", "singlepoint", {"source": "CCO"}, {"m": 1}, {"nproc": 8}
    )
    same = compute_preview_fingerprint(
        "j1", "sr_x", "singlepoint", {"source": "CCO"}, {"m": 1}, {"nproc": 8}
    )
    other = compute_preview_fingerprint(
        "j1", "sr_x", "singlepoint", {"source": "CCC"}, {"m": 1}, {"nproc": 8}
    )
    stale = compute_preview_fingerprint(
        "j1", "sr_y", "singlepoint", {"source": "CCO"}, {"m": 1}, {"nproc": 8}
    )
    assert a == same
    assert a != other
    assert a != stale


def test_resolve_last_structure_from_result_manifest(tmp_path: Path) -> None:
    result_dir = tmp_path / "RESULT"
    result_dir.mkdir()
    (result_dir / "best.xyz").write_text(XYZ_COOH, encoding="utf-8")
    manifest = {
        "schema_version": "result_manifest_v2",
        "task_id": "t1",
        "workflow": "Confsearch",
        "created_at": "2026-09-19T00:00:00Z",
        "products": [
            {"id": "rank1", "label": "Rank 1 conformer", "path": "best.xyz", "kind": "structure"},
            {"id": "log", "label": "log", "path": "out.log", "kind": "file"},
        ],
    }
    (result_dir / "result_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    hit = resolve_last_structure(tmp_path)
    assert hit is not None
    assert hit["entry_id"] == "rank1"
    assert hit["available"] is True
    assert "O" in hit["xyz_text"]

    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    assert resolve_last_structure(empty_dir) is None


# ---------------------------------------------------------------------------
# Manager-level behaviour
# ---------------------------------------------------------------------------


def _make_manager(tmp_path: Path) -> JobManager:
    manager = JobManager(run_root=tmp_path / "runs", poll_interval=30)
    manager._execute_submission = lambda job_id: None  # type: ignore[method-assign]
    return manager


def _seed(
    manager: JobManager,
    job_id: str,
    *,
    workflow: str = "singlepoint",
    status: JobStatus = JobStatus.FAILED,
    spec: JobSpec | None = None,
) -> JobRecord:
    work_dir = manager.run_root / "default" / job_id
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "input.xyz").write_text(XYZ_COOH, encoding="utf-8")
    (work_dir / "WORK").mkdir(exist_ok=True)
    (work_dir / "WORK" / "old.out").write_text("old", encoding="utf-8")
    (work_dir / "RESULT").mkdir(exist_ok=True)
    (work_dir / "RESULT" / "old.json").write_text("{}", encoding="utf-8")
    record = JobRecord(
        id=job_id,
        spec=spec or _spec(workflow),
        status=status,
        work_dir=str(work_dir),
        project_id=manager.default_project_id,
        group_id=job_id,
    )
    manager.store.create(record)
    return manager.store.get(job_id)  # type: ignore[return-value]


def _edit_spec(workflow: str, **overrides: Any) -> JobSpec:
    spec = _spec(workflow)
    return replace(spec, **overrides)


def test_edit_in_place_updates_spec_attempts_and_clears(tmp_path: Path) -> None:
    """E08: identity kept, spec updated, only the new attempt is queued."""
    manager = _make_manager(tmp_path)
    try:
        record = _seed(manager, "edit1")
        revision = compute_source_revision(record)
        new_spec = _edit_spec("singlepoint", method={"levels": {"sp": {"functional": "wB97X-D4"}}})
        result = manager.edit_recalculate(
            "edit1",
            mode="in_place",
            new_spec=new_spec,
            expected_source_revision=revision,
            request_id="req-1",
            payload_hash=compute_payload_hash({"job_id": "edit1"}),
            payload_json="{}",
            diff_summary=[{"path": "method.levels.sp.functional", "kind": "method"}],
        )
        assert result["operation"] == "in_place"
        assert result["attempt"] == 2
        updated = manager.get("edit1")
        assert updated is not None
        assert updated.status == JobStatus.QUEUED
        assert updated.work_dir == record.work_dir
        assert updated.spec.method == {"levels": {"sp": {"functional": "wB97X-D4"}}}
        assert updated.result is not None
        assert updated.result["attempts"] == 2
        assert updated.result["attempt_history"][0]["mode"] == "edit_recalculate"
        work_dir = Path(updated.work_dir)
        assert not (work_dir / "WORK" / "old.out").exists()
        assert not (work_dir / "RESULT" / "old.json").exists()
        assert (work_dir / "WORK").is_dir()  # scaffold recreated
        assert (work_dir / "job.json").is_file()
    finally:
        manager.shutdown()


def test_edit_in_place_removes_stale_input_snapshot(tmp_path: Path) -> None:
    """Plan §6.2: a changed input must not leave the old snapshot behind."""
    manager = _make_manager(tmp_path)
    try:
        record = _seed(manager, "edit2")
        revision = compute_source_revision(record)
        new_spec = _edit_spec(
            "singlepoint",
            input={"source_type": "xyz_text", "source": "4\n\nC 0 0 0\nH 1 0 0\nH 0 1 0\nH 0 0 1"},
        )
        manager.edit_recalculate(
            "edit2",
            mode="in_place",
            new_spec=new_spec,
            expected_source_revision=revision,
            request_id="req-2",
            payload_hash="ph_x",
            payload_json="{}",
        )
        work_dir = Path(manager.get("edit2").work_dir)  # type: ignore[union-attr]
        assert not (work_dir / "input.xyz").exists()
        assert not (work_dir / "input_source.json").exists()
    finally:
        manager.shutdown()


def test_edit_in_place_unchanged_input_keeps_snapshot(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    try:
        record = _seed(manager, "edit3")
        revision = compute_source_revision(record)
        new_spec = _edit_spec("singlepoint", resources={"nproc": 16})
        manager.edit_recalculate(
            "edit3",
            mode="in_place",
            new_spec=new_spec,
            expected_source_revision=revision,
            request_id="req-3",
            payload_hash="ph_x",
            payload_json="{}",
        )
        work_dir = Path(manager.get("edit3").work_dir)  # type: ignore[union-attr]
        assert (work_dir / "input.xyz").is_file()
    finally:
        manager.shutdown()


def test_edit_revision_conflict_rejected(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    try:
        _seed(manager, "edit4")
        with pytest.raises(EditConflictError):
            manager.edit_recalculate(
                "edit4",
                mode="in_place",
                new_spec=_edit_spec("singlepoint"),
                expected_source_revision="sr_stale",
                request_id="req-4",
                payload_hash="ph_x",
                payload_json="{}",
            )
        assert manager.get("edit4").status == JobStatus.FAILED  # type: ignore[union-attr]
    finally:
        manager.shutdown()


def test_edit_active_job_in_place_rejected(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    try:
        _seed(manager, "edit5", status=JobStatus.RUNNING)
        with pytest.raises(ValueError, match="terminal"):
            manager.edit_recalculate(
                "edit5",
                mode="in_place",
                new_spec=_edit_spec("singlepoint"),
                expected_source_revision=None,
                request_id="req-5",
                payload_hash="ph_x",
                payload_json="{}",
            )
    finally:
        manager.shutdown()


def test_edit_in_place_workflow_locked(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    try:
        _seed(manager, "edit6")
        with pytest.raises(ValueError, match="锁定工作流"):
            manager.edit_recalculate(
                "edit6",
                mode="in_place",
                new_spec=_edit_spec("optimize"),
                expected_source_revision=None,
                request_id="req-6",
                payload_hash="ph_x",
                payload_json="{}",
            )
    finally:
        manager.shutdown()


def test_edit_new_job_preserves_original(tmp_path: Path) -> None:
    """E09: new id/dir; original parameters, status and files untouched."""
    manager = _make_manager(tmp_path)
    try:
        record = _seed(manager, "edit7")
        original_dir = Path(record.work_dir)
        new_spec = _edit_spec("singlepoint", method={"levels": {"sp": {"functional": "HF"}}})
        result = manager.edit_recalculate(
            "edit7",
            mode="new_job",
            new_spec=new_spec,
            expected_source_revision=None,
            request_id="req-7",
            payload_hash="ph_x",
            payload_json="{}",
        )
        assert result["operation"] == "new_job"
        assert result["job_id"] != "edit7"
        created = manager.get(str(result["job_id"]))
        assert created is not None
        assert created.status == JobStatus.QUEUED
        assert created.spec.method == {"levels": {"sp": {"functional": "HF"}}}
        assert created.group_id == record.group_id
        original = manager.get("edit7")
        assert original is not None
        assert original.status == JobStatus.FAILED
        assert original.spec.method == _spec("singlepoint").method
        assert (original_dir / "WORK" / "old.out").is_file()
    finally:
        manager.shutdown()


def test_edit_idempotent_replay(tmp_path: Path) -> None:
    """E11: the same request_id+payload submits exactly once."""
    manager = _make_manager(tmp_path)
    try:
        record = _seed(manager, "edit8")
        revision = compute_source_revision(record)
        new_spec = _edit_spec("singlepoint", resources={"nproc": 4})
        kwargs: dict[str, Any] = {
            "mode": "in_place",
            "new_spec": new_spec,
            "expected_source_revision": revision,
            "request_id": "req-8",
            "payload_hash": "ph_same",
            "payload_json": "{}",
        }
        first = manager.edit_recalculate("edit8", **kwargs)
        assert first["replayed"] is False
        second = manager.edit_recalculate("edit8", **kwargs)
        assert second["replayed"] is True
        assert second["job_id"] == first["job_id"]
        updated = manager.get("edit8")
        assert updated is not None
        assert attempt_number(updated) == 2  # not 3 — no double requeue
    finally:
        manager.shutdown()


def test_edit_request_id_payload_conflict(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    try:
        record = _seed(manager, "edit9")
        revision = compute_source_revision(record)
        common: dict[str, Any] = {
            "mode": "in_place",
            "new_spec": _edit_spec("singlepoint"),
            "expected_source_revision": revision,
            "request_id": "req-9",
            "payload_json": "{}",
        }
        manager.edit_recalculate("edit9", payload_hash="ph_a", **common)
        with pytest.raises(EditConflictError):
            manager.edit_recalculate("edit9", payload_hash="ph_b", **common)
    finally:
        manager.shutdown()


def test_edit_cleanup_failure_blocks_queueing(tmp_path: Path) -> None:
    """E13/plan §10.5: a half-cleaned task root must never be queued."""
    manager = _make_manager(tmp_path)
    try:
        record = _seed(manager, "edit10")
        revision = compute_source_revision(record)

        import acp.scheduler.manager as manager_module

        original_rmtree = manager_module.shutil.rmtree

        def _fail_rmtree(path: Any, *args: Any, **kwargs: Any) -> None:
            if str(path).endswith("WORK"):
                raise OSError("permission denied (simulated)")
            original_rmtree(path, *args, **kwargs)

        manager_module.shutil.rmtree = _fail_rmtree  # type: ignore[attr-defined]
        try:
            with pytest.raises(RuntimeError, match="清理旧尝试产物失败"):
                manager.edit_recalculate(
                    "edit10",
                    mode="in_place",
                    new_spec=_edit_spec("singlepoint"),
                    expected_source_revision=revision,
                    request_id="req-10",
                    payload_hash="ph_x",
                    payload_json="{}",
                )
        finally:
            manager_module.shutil.rmtree = original_rmtree  # type: ignore[attr-defined]
        updated = manager.get("edit10")
        assert updated is not None
        assert updated.status == JobStatus.FAILED  # stayed terminal
    finally:
        manager.shutdown()


def test_operation_store_lifecycle(tmp_path: Path) -> None:
    store = JobEditOperationStore(tmp_path / "ops.db")
    from acp.scheduler.migrations import migrate

    migrate(tmp_path / "ops.db")
    assert store.get("r1") is None
    assert store.insert_prepared(
        request_id="r1", job_id="j1", mode="in_place", payload_hash="ph1", payload_json="{}"
    )
    assert not store.insert_prepared(
        request_id="r1", job_id="j1", mode="in_place", payload_hash="ph1", payload_json="{}"
    )
    row = store.get("r1")
    assert row is not None and row["status"] == "prepared"
    store.complete("r1", json.dumps({"job_id": "j1", "attempt": 2}))
    row = store.get("r1")
    assert row is not None and row["status"] == "completed"
    store.fail("r2", "boom")  # no row — must not raise


# ---------------------------------------------------------------------------
# API-level behaviour
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    import acp.scheduler.capabilities as capabilities_module
    import acp.scheduler.manager as manager_module

    monkeypatch.setattr(capabilities_module, "local_satisfies", lambda required: True)
    monkeypatch.setattr(manager_module, "local_satisfies", lambda required: True)
    from acp.api.server import create_app

    app = create_app(run_root=tmp_path, max_running=2)
    with TestClient(app) as test_client:
        manager: JobManager = test_client.app.state.job_manager
        manager._execute_submission = lambda job_id: None  # type: ignore[method-assign]
        yield test_client


def _seed_api_job(
    client: TestClient,
    job_id: str,
    *,
    workflow: str = "singlepoint",
    status: JobStatus = JobStatus.FAILED,
) -> JobRecord:
    manager: JobManager = client.app.state.job_manager
    work_dir = Path(manager.run_root) / "default" / job_id
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "input.xyz").write_text(XYZ_COOH, encoding="utf-8")
    record = JobRecord(
        id=job_id,
        spec=_spec(workflow),
        status=status,
        work_dir=str(work_dir),
        project_id=manager.default_project_id,
    )
    manager.store.create(record)
    return record


def test_api_edit_draft_round_trip(client: TestClient) -> None:
    _seed_api_job(client, "draft1", workflow="Confsearch")
    response = client.get("/api/v1/jobs/draft1/edit-draft")
    assert response.status_code == 200
    draft = response.json()
    assert draft["workflow"] == "Confsearch"
    assert draft["capabilities"]["can_edit"] is True
    assert draft["capabilities"]["can_in_place"] is True
    assert draft["editable_spec"]["method"]["protocol"] == "censo-crest"
    assert draft["source_revision"].startswith("sr_")


def test_api_edit_draft_unknown_job_404(client: TestClient) -> None:
    assert client.get("/api/v1/jobs/ghost/edit-draft").status_code == 404


def test_api_edit_draft_retired_view_only(client: TestClient) -> None:
    manager: JobManager = client.app.state.job_manager
    record = JobRecord(
        id="retired1",
        spec=replace(_spec("Confsearch"), workflow="ensemble"),
        status=JobStatus.COMPLETED,
        work_dir=str(Path(manager.run_root) / "default" / "retired1"),
        project_id=manager.default_project_id,
    )
    manager.store.create(record)
    response = client.get("/api/v1/jobs/retired1/edit-draft")
    assert response.status_code == 200
    draft = response.json()
    assert draft["capabilities"]["can_edit"] is False
    assert draft["migration_hint"]


def test_api_preview_returns_diff_and_fingerprint(client: TestClient) -> None:
    record = _seed_api_job(client, "prev1")
    revision = compute_source_revision(record)
    spec = _spec("singlepoint")
    body = {
        "mode": "in_place",
        "input": spec.input,
        "method": {"levels": {"sp": {"functional": "wB97X-D4", "basis": "def2-TZVP"}}},
        "resources": {"nproc": 16, "mem": 32},
        "expected_source_revision": revision,
    }
    response = client.post("/api/v1/jobs/prev1/edit-recalculate/preview", json=body)
    assert response.status_code == 200
    preview = response.json()
    assert preview["ok"] is True
    paths = {entry["path"] for entry in preview["diff"]}
    assert "method.levels.sp.functional" in paths
    assert "resources.nproc" in paths
    assert preview["source_revision"] == revision
    # Identical preview → identical fingerprint.
    again = client.post("/api/v1/jobs/prev1/edit-recalculate/preview", json=body)
    assert again.status_code == 200
    assert again.json()["preview_fingerprint"] == preview["preview_fingerprint"]


def test_api_preview_blocks_active_in_place(client: TestClient) -> None:
    _seed_api_job(client, "prev2", status=JobStatus.RUNNING)
    spec = _spec("singlepoint")
    response = client.post(
        "/api/v1/jobs/prev2/edit-recalculate/preview",
        json={"mode": "in_place", "input": spec.input, "method": spec.method},
    )
    assert response.status_code == 200
    preview = response.json()
    assert preview["ok"] is False
    assert any("非终态" in reason for reason in preview["blocking_reasons"])


def test_api_preview_revision_conflict_409(client: TestClient) -> None:
    _seed_api_job(client, "prev3")
    spec = _spec("singlepoint")
    response = client.post(
        "/api/v1/jobs/prev3/edit-recalculate/preview",
        json={
            "mode": "in_place",
            "input": spec.input,
            "method": spec.method,
            "expected_source_revision": "sr_stale",
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "source_revision_conflict"


def test_api_submit_in_place(client: TestClient) -> None:
    record = _seed_api_job(client, "sub1")
    revision = compute_source_revision(record)
    spec = _spec("singlepoint")
    body = {
        "mode": "in_place",
        "input": spec.input,
        "method": {"levels": {"sp": {"functional": "wB97X-D4", "basis": "def2-TZVP"}}},
        "resources": {"nproc": 16},
        "expected_source_revision": revision,
        "request_id": "req-sub1",
    }
    response = client.post("/api/v1/jobs/sub1/edit-recalculate", json=body)
    assert response.status_code == 200
    result = response.json()
    assert result["operation"] == "in_place"
    assert result["attempt"] == 2
    assert result["job_id"] == "sub1"
    fetched = client.get("/api/v1/jobs/sub1").json()
    assert fetched["status"] == "queued"
    assert fetched["spec"]["method"]["levels"]["sp"]["functional"] == "wB97X-D4"
    assert fetched["work_dir"] == record.work_dir
    # Idempotent replay.
    replay = client.post("/api/v1/jobs/sub1/edit-recalculate", json=body)
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True


def test_api_submit_fingerprint_conflict_409(client: TestClient) -> None:
    record = _seed_api_job(client, "sub2")
    spec = _spec("singlepoint")
    body = {
        "mode": "in_place",
        "input": spec.input,
        "method": spec.method,
        "resources": spec.resources,
        "expected_source_revision": compute_source_revision(record),
        "preview_fingerprint": "pf_outdated",
        "request_id": "req-sub2",
    }
    response = client.post("/api/v1/jobs/sub2/edit-recalculate", json=body)
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "preview_fingerprint_conflict"


def test_api_submit_retired_in_place_422(client: TestClient) -> None:
    manager: JobManager = client.app.state.job_manager
    record = JobRecord(
        id="sub3",
        spec=replace(_spec("Confsearch"), workflow="energy"),
        status=JobStatus.COMPLETED,
        work_dir=str(Path(manager.run_root) / "default" / "sub3"),
        project_id=manager.default_project_id,
    )
    manager.store.create(record)
    response = client.post(
        "/api/v1/jobs/sub3/edit-recalculate",
        json={"mode": "in_place", "request_id": "req-sub3", "input": {}, "method": {}},
    )
    assert response.status_code == 422


def test_api_submit_workflow_change_in_place_422(client: TestClient) -> None:
    record = _seed_api_job(client, "sub4")
    spec = _spec("singlepoint")
    response = client.post(
        "/api/v1/jobs/sub4/edit-recalculate",
        json={
            "mode": "in_place",
            "workflow": "optimize",
            "input": spec.input,
            "method": {},
            "expected_source_revision": compute_source_revision(record),
            "request_id": "req-sub4",
        },
    )
    assert response.status_code == 422


def test_api_submit_new_job_mode(client: TestClient) -> None:
    _seed_api_job(client, "sub5")
    spec = _spec("singlepoint")
    response = client.post(
        "/api/v1/jobs/sub5/edit-recalculate",
        json={
            "mode": "new_job",
            "workflow": "singlepoint",
            "input": spec.input,
            "method": {"levels": {"sp": {"functional": "HF"}}},
            "resources": spec.resources,
            "request_id": "req-sub5",
        },
    )
    assert response.status_code == 200
    result = response.json()
    assert result["operation"] == "new_job"
    new_id = result["job_id"]
    assert new_id != "sub5"
    original = client.get("/api/v1/jobs/sub5").json()
    assert original["status"] == "failed"
    created = client.get(f"/api/v1/jobs/{new_id}")
    assert created.status_code == 200
    assert created.json()["spec"]["method"]["levels"]["sp"]["functional"] == "HF"


def test_api_submit_active_job_in_place_409(client: TestClient) -> None:
    _seed_api_job(client, "sub6", status=JobStatus.RUNNING)
    spec = _spec("singlepoint")
    response = client.post(
        "/api/v1/jobs/sub6/edit-recalculate",
        json={"mode": "in_place", "input": spec.input, "method": spec.method, "request_id": "r6"},
    )
    assert response.status_code == 409


def test_api_submit_missing_request_id_422(client: TestClient) -> None:
    _seed_api_job(client, "sub7")
    spec = _spec("singlepoint")
    response = client.post(
        "/api/v1/jobs/sub7/edit-recalculate",
        json={"mode": "in_place", "input": spec.input, "method": spec.method, "request_id": "  "},
    )
    assert response.status_code == 422


def test_rerun_endpoint_still_works_after_refactor(client: TestClient) -> None:
    """E15: the legacy direct-rerun path keeps its semantics."""
    _seed_api_job(client, "rr1")
    response = client.post("/api/v1/jobs/rr1/rerun")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "rr1"
    assert body["status"] == "queued"
    fetched = client.get("/api/v1/jobs/rr1").json()
    assert fetched["result"]["attempts"] == 2
    assert fetched["result"]["attempt_history"][0]["mode"] == "rerun"
