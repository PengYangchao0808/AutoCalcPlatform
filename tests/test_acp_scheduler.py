"""Tests for the scheduler core (store, manager, runner) without external binaries."""

# pyright: reportMissingImports=false, reportPrivateUsage=false, reportPossiblyUnboundVariable=false, reportOptionalMemberAccess=false, reportUnusedCallResult=false, reportAny=false

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from acp.scheduler.events import JobEventLog
from acp.scheduler.jobs import (
    EXIT_WAITING_REVIEW,
    JobRecord,
    JobSpec,
    JobStatus,
)
from acp.scheduler.manager import JobManager
from acp.scheduler.migrations import migrate
from acp.scheduler.runner import JobRunner, materialize_job_input
from acp.scheduler.store import JobStore

# Local constant for tests that reference the retired mechanism config filename.
MECHANISM_CONFIG_FILENAME = "mechanism_config.json"


def _write_mechanism_study_json(
    work_dir: Path,
    *,
    study_id: str,
    quality: str | None,
    fidelity_profile_name: str,
    provider_backend: str = "native",
    include_high_fidelity: bool = False,
) -> Path:
    study_dir = work_dir / "mechanism_study" / study_id
    study_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "study_id": study_id,
        "status": "waiting" if quality == "medium" else "completed",
        "quality": quality,
        "routes": [],
        "metadata": {
            "study_runner": {
                "provider_backend": provider_backend,
                "fidelity_profile_name": fidelity_profile_name,
                "high_fidelity_profile_name": "s4",
                "config": {"mechanism": {"provider_backend": provider_backend}},
            },
            "high_fidelity": {"profile": "s4"} if include_high_fidelity else None,
        },
    }
    path = study_dir / "study.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_materialize_com_and_inp_preserve_suffix(tmp_path: Path) -> None:
    """M8: .com/.inp inputs must not fall into the SMILES conversion path;
    their format-significant suffix is preserved for the CLI parser."""
    run_root = tmp_path / "root"
    run_root.mkdir()
    inputs_dir = tmp_path / "inputs"
    com = run_root / "mol.com"
    com.write_text("! SP wB97X-D4 def2-TZVPP\n* xyz 0 1\nC 0 0 0\n*\n", encoding="utf-8")
    inp_file = run_root / "mol.inp"
    inp_file.write_text("! SP\n* xyz 0 1\nC 0 0 0\n*\n", encoding="utf-8")

    for src, expected in ((com, "input.com"), (inp_file, "input.inp")):
        dest = materialize_job_input(
            {"source_type": "file", "source": str(src)},
            inputs_dir,
            run_root,
        )
        assert dest is not None
        assert dest.name == expected
        assert dest.read_text(encoding="utf-8") == src.read_text(encoding="utf-8")


def test_materialize_structure_asset_preserves_com_suffix(tmp_path: Path) -> None:
    run_root = tmp_path / "root"
    run_root.mkdir()
    asset = run_root / "mol.com"
    asset.write_text("! SP\n* xyz 0 1\nC 0 0 0\n*\n", encoding="utf-8")

    dest = materialize_job_input(
        {"source_type": "structure_asset", "source": "mol.com"}, tmp_path / "inputs", run_root
    )
    assert dest is not None
    assert dest.name == "input.com"


@pytest.mark.skip(reason="mechanism retired — write_mechanism_job_config removed")
def test_mechanism_frontend_payload_materializes_and_builds_cmd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_smiles_to_xyz(smiles: str, *, seed: int = 42, comment: str | None = None) -> str:
        del seed, comment
        return (
            "3\n"
            f"source={smiles}\n"
            "C 0.000000 0.000000 0.000000\n"
            "H 0.000000 0.000000 1.089000\n"
            "H 1.026719 0.000000 -0.363000\n"
        )

    monkeypatch.setattr("acp.scheduler.runner.smiles_to_xyz", fake_smiles_to_xyz)

    payload = {
        "source_type": "mechanism",
        "reactant": {
            "source_type": "smiles",
            "source": "CCO",
            "asset_id": None,
            "charge": 0,
            "multiplicity": 1,
        },
        "product": {
            "source_type": "smiles",
            "source": "CC=O",
            "asset_id": None,
            "charge": 0,
            "multiplicity": 1,
        },
        "ts_guess": None,
        "routes": [{"reactant_id": "reactant", "product_id": "product", "label": "frontend-shape"}],
    }
    inputs_dir = tmp_path / "job" / "inputs"
    _work_dir = tmp_path / "job"
    role_paths: dict[str, Path] = {}

    reactant_path = materialize_job_input(payload, inputs_dir, tmp_path, role_paths)

    assert reactant_path == inputs_dir / "reactant.xyz"
    assert reactant_path is not None and reactant_path.is_file()
    assert role_paths["product"] == inputs_dir / "product.xyz"
    assert role_paths["product"].is_file()
    assert "C 0.000000 0.000000 0.000000" in reactant_path.read_text(encoding="utf-8")
    reactant_payload = payload["reactant"]
    assert isinstance(reactant_payload, dict)
    assert reactant_payload["source"] == "CCO"

    # write_mechanism_job_config removed in Wave 6 — test body gutted
    pytest.skip("mechanism retired — write_mechanism_job_config removed")


@pytest.mark.skip(reason="mechanism retired — mechanism_method_flags removed")
def test_mechanism_method_flags_emit_study_controls() -> None:
    pytest.skip("mechanism retired — mechanism_method_flags removed")


@pytest.mark.skip(reason="mechanism retired — _write_mechanism_config_if_needed removed")
def test_mechanism_runner_cmd_uses_config_channel(tmp_path: Path) -> None:
    runner = JobRunner(python_executable="python")
    spec = JobSpec(
        workflow="mechanism",
        name="rxn",
        input={"source": "C=C", "product": "CC"},
        method={
            "study_id": "study_001",
            "conformer_mode": "xtb-fast",
            "max_elementary_steps": 4,
            "promotion_policy": "rate_relevant",
            "int_extension": True,
            "auto_converge": True,
        },
        resources={"nproc": 8, "mem": "16GB"},
    )

    mechanism_config_path = tmp_path / MECHANISM_CONFIG_FILENAME
    cmd = runner._build_cmd(spec, tmp_path, mechanism_config_path=str(mechanism_config_path))

    assert "--mechanism-config" in cmd and str(mechanism_config_path) in cmd
    assert "--study-id" not in cmd
    assert "--conformer-mode" not in cmd
    assert "--max-elementary-steps" not in cmd
    assert "--promotion-policy" not in cmd
    assert "--int-extension" not in cmd
    assert "--auto-converge" not in cmd


@pytest.mark.skip(reason="mechanism retired — write_mechanism_job_config removed")
def test_mechanism_reaction_json_materializes_and_sets_schema_version(tmp_path: Path) -> None:
    mgr = JobManager(run_root=tmp_path, max_running=1)
    try:
        work_dir = tmp_path / mgr.default_project_id / "job-mech"
        work_dir.mkdir(parents=True, exist_ok=True)
        record = JobRecord(
            id="job-mech",
            spec=JobSpec(
                workflow="mechanism",
                name="rxn",
                input={
                    "source_type": "mechanism",
                    "reactant": {"source_type": "smiles", "source": "C1CC1"},
                    "product": {"source_type": "smiles", "source": "C1CC1"},
                    "routes": [],
                },
                method={"study_id": "study-lock"},
                resources={"nproc": 2, "mem": "2GB"},
                project_id=mgr.default_project_id,
            ),
            work_dir=str(work_dir),
            project_id=mgr.default_project_id,
        )
        reaction_payload = {
            "schema_version": 2,
            "study_id": "study-lock",
            "reactant": {
                "path": None,
                "smiles": "C1CC1",
                "asset_id": None,
                "charge": 0,
                "multiplicity": 1,
            },
            "product": {
                "path": None,
                "smiles": "C1CC1",
                "asset_id": None,
                "charge": 0,
                "multiplicity": 1,
            },
            "ts_guess": None,
            "atom_mapping": [{"reactant_index": 0, "product_index": 0}],
            "bond_changes": [],
            "index_base": 0,
            "content_hash": "sha256:test",
            "locked_at": "2026-08-16T00:00:00Z",
            "confirmed_by": "user",
        }
        with sqlite3.connect(str(mgr.store.db_path)) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO mechanism_studies "
                "(id, job_id, study_json, status, created_at,"
                " updated_at, reaction_json, config_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "study-lock",
                    None,
                    json.dumps({"study_id": "study-lock"}),
                    "reaction_confirmed",
                    "2026-08-16T00:00:00Z",
                    "2026-08-16T00:00:00Z",
                    json.dumps(reaction_payload),
                    "sha256:test",
                ),
            )
            conn.commit()

        mgr._materialize_mechanism_reaction_if_present(record)
        reaction_path = work_dir / "WORK" / "08_ANALYSIS" / "reaction.json"
        assert reaction_path.is_file()
        assert json.loads(reaction_path.read_text(encoding="utf-8"))["schema_version"] == 2
    finally:
        mgr.shutdown()


@pytest.mark.skip(reason="mechanism retired — materialize_job_input mechanism branch removed")
def test_mechanism_materialize_requires_valid_reactant(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="reactant"):
        materialize_job_input(
            {
                "source_type": "mechanism",
                "reactant": None,
                "product": None,
                "ts_guess": None,
                "routes": [],
            },
            tmp_path / "inputs",
            tmp_path,
        )


def test_store_roundtrip(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    from acp.scheduler.jobs import JobRecord

    record = JobRecord(
        id="job-1",
        spec=JobSpec(workflow="fake", name="t", input={"source": "CCO"}),
        status=JobStatus.QUEUED,
        work_dir=str(tmp_path / "job-1"),
    )
    store.create(record)
    fetched = store.get("job-1")
    assert fetched is not None
    assert fetched.spec.workflow == "fake"
    assert fetched.status == JobStatus.QUEUED

    fetched.status = JobStatus.RUNNING
    fetched.pid = 4242
    store.update(fetched)
    assert store.get("job-1").pid == 4242

    store.create(
        JobRecord(
            id="job-2",
            spec=JobSpec(workflow="fake"),
            status=JobStatus.COMPLETED,
            work_dir=str(tmp_path / "job-2"),
        )
    )
    counts = store.counts()
    assert counts["running"] == 1
    assert counts["completed"] == 1
    assert len(store.list()) == 2


def test_store_reload_preserves_history(tmp_path: Path) -> None:
    db = tmp_path / "jobs.db"
    JobStore(db).create(
        JobRecord(
            id="persisted",
            spec=JobSpec(workflow="fake", name="p"),
            status=JobStatus.COMPLETED,
            work_dir=str(tmp_path / "p"),
        )
    )
    reopened = JobStore(db)
    assert reopened.get("persisted") is not None
    assert reopened.counts()["completed"] == 1


def test_manager_runs_fake_job_to_completion(tmp_path: Path) -> None:
    mgr = JobManager(run_root=tmp_path, max_running=2)
    record = mgr.submit(JobSpec(workflow="fake", name="demo", input={"source": "CCO"}))
    assert record.status == JobStatus.QUEUED

    for _ in range(40):
        cur = mgr.get(record.id)
        assert cur is not None
        if cur.status.is_terminal:
            break
        time.sleep(0.5)

    assert cur.status == JobStatus.COMPLETED
    assert cur.exit_code == 0
    events = mgr.event_log(record.id).read_all()
    types = [e["type"] for e in events]
    assert "job.created" in types
    assert "stage.started" in types
    assert "job.completed" in types
    mgr.shutdown()


def test_batch_parallelism_one_persists_all_jobs_and_dispatches_fifo(tmp_path: Path) -> None:
    """A two-molecule batch creates two durable jobs before execution is gated."""
    runner = MagicMock()
    runner.poll.return_value = (False, None)
    mgr = JobManager(run_root=tmp_path, runner=runner, poll_interval=30, local_max_jobs=4)
    try:
        resources = {"batch_id": "batch-1", "batch_index": 0, "batch_total": 2, "parallelism": 1}
        first = mgr.submit(
            JobSpec(workflow="Confsearch", name="mol-1", resources={**resources, "batch_index": 0})
        )
        second = mgr.submit(
            JobSpec(workflow="Confsearch", name="mol-2", resources={**resources, "batch_index": 1})
        )

        deadline = time.time() + 5
        while time.time() < deadline:
            first_now = mgr.get(first.id)
            second_now = mgr.get(second.id)
            if (
                first_now is not None
                and second_now is not None
                and first_now.status == JobStatus.RUNNING
                and second_now.status == JobStatus.QUEUED
            ):
                break
            time.sleep(0.05)

        assert mgr.store.counts()["running"] == 1
        assert mgr.store.counts()["queued"] == 1
        assert runner.submit.call_count == 1

        first_now = mgr.get(first.id)
        assert first_now is not None
        first_now.status = JobStatus.COMPLETED
        first_now.completed_at = "2026-08-23T00:00:00+00:00"
        mgr.store.update(first_now)
        mgr._dispatch_queued_jobs()

        deadline = time.time() + 5
        while time.time() < deadline:
            second_now = mgr.get(second.id)
            if second_now is not None and second_now.status == JobStatus.RUNNING:
                break
            time.sleep(0.05)

        assert mgr.get(second.id).status == JobStatus.RUNNING  # type: ignore[union-attr]
        assert runner.submit.call_count == 2
    finally:
        mgr.shutdown()


def test_manager_rejects_unknown_workflow(tmp_path: Path) -> None:
    mgr = JobManager(run_root=tmp_path, max_running=1)
    try:
        import pytest

        with pytest.raises(ValueError):
            mgr.submit(JobSpec(workflow="nonexistent"))
    finally:
        mgr.shutdown()


def test_manager_marks_interrupted_jobs_on_startup(tmp_path: Path) -> None:
    db = tmp_path / "jobs.db"
    store = JobStore(db)
    from acp.scheduler.jobs import JobRecord

    store.create(
        JobRecord(
            id="was-running",
            spec=JobSpec(workflow="fake"),
            status=JobStatus.RUNNING,
            work_dir=str(tmp_path / "r"),
        )
    )
    mgr = JobManager(run_root=tmp_path, store=store, max_running=1)
    interrupted = mgr.get("was-running")
    assert interrupted.status == JobStatus.FAILED
    assert "interrupted" in (interrupted.error or "")
    mgr.shutdown()


def test_event_log_append_read(tmp_path: Path) -> None:
    log = JobEventLog(tmp_path / "events.jsonl")
    log.append("job.started", job_id="x")
    log.append("log", line="hello")
    records = log.read_all()
    assert len(records) == 2
    assert records[0]["type"] == "job.started"
    assert records[1]["line"] == "hello"
    assert "timestamp" in records[0]


def test_find_state_file_prefers_shallowest(tmp_path: Path) -> None:
    """P1#1: nested workflows (NMR) write multiple state.json; shallowest wins."""
    import json

    from acp.scheduler.runner import find_workflow_state

    root = tmp_path / "work"
    (root / "jobA").mkdir(parents=True)
    (root / "jobA" / "state.json").write_text(json.dumps({"current_stage": "nmr"}))
    (root / "conformer" / "mol").mkdir(parents=True)
    (root / "conformer" / "mol" / "state.json").write_text(json.dumps({"current_stage": "crest"}))

    found = find_workflow_state(root)
    assert found is not None
    assert found.relative_to(root) == Path("jobA/state.json")

    single = tmp_path / "single"
    (single / "mol_CCO_1234").mkdir(parents=True)
    (single / "mol_CCO_1234" / "state.json").write_text("{}")
    found2 = find_workflow_state(single)
    assert found2 is not None
    assert found2.relative_to(single) == Path("mol_CCO_1234/state.json")

    assert find_workflow_state(tmp_path / "empty") is None


def test_queued_job_cancel_is_immediate(tmp_path: Path) -> None:
    """P1#3: cancelling a running job transitions to CANCELLING then terminal.

    With the poller-driven architecture, jobs no longer queue — all are
    submitted concurrently on daemon threads.  This test verifies the
    cancel flow for a running fake job.
    """
    mgr = JobManager(run_root=tmp_path, poll_interval=30)
    try:
        second = mgr.submit(JobSpec(workflow="fake", name="second", input={"source": "Y"}))
        time.sleep(0.3)

        cancelled = mgr.cancel(second.id)
        assert cancelled is not None
        cur = mgr.get(second.id)
        assert cur.status in (JobStatus.CANCELLING, JobStatus.CANCELLED, JobStatus.RUNNING)

        for _ in range(20):
            cur = mgr.get(second.id)
            if cur.status.is_terminal:
                break
            time.sleep(0.3)

        assert mgr.get(second.id).status.is_terminal
    finally:
        mgr.shutdown()


def test_resolve_safe_rejects_traversal(tmp_path: Path) -> None:
    """Sprint 0: resolve_safe() must reject path-traversal attempts."""
    from acp.scheduler.files import resolve_safe

    work = tmp_path / "work"
    work.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("password")

    (work / "ok.txt").write_text("data")
    ok = resolve_safe(work, "ok.txt")
    assert ok is not None and ok.name == "ok.txt"

    assert resolve_safe(work, "../secret.txt") is None
    assert resolve_safe(work, "../../etc/passwd") is None
    assert resolve_safe(work, "../../../secret.txt") is None

    assert resolve_safe(work, "missing.txt") is None


def test_output_dir_outside_run_root_is_clamped(tmp_path: Path) -> None:
    """P2#2: an output_dir escaping run_root must be ignored (clamped under run_root)."""
    mgr = JobManager(run_root=tmp_path, max_running=1)
    try:
        escape = tmp_path.parent / "evil_elsewhere"
        record = mgr.submit(
            JobSpec(workflow="fake", name="clamp", input={"source": "X"}, output_dir=str(escape))
        )
        work_dir = Path(record.work_dir).resolve()
        run_root = tmp_path.resolve()
        assert run_root in work_dir.parents or work_dir == run_root, (
            f"work_dir {work_dir} escaped run_root {run_root}"
        )
        assert "evil_elsewhere" not in record.work_dir
    finally:
        mgr.shutdown()


def test_output_dir_is_parent_override_with_canonical_task_leaf(tmp_path: Path) -> None:
    """An in-root output override cannot replace the canonical task leaf."""
    mgr = _quiet_manager(tmp_path)
    try:
        parent = tmp_path / "custom-parent"
        record = mgr.submit(
            JobSpec(
                workflow="Confsearch",
                name="legacy-random-label",
                input={"source": "CCO"},
                molecule_name="ethanol",
                task_name="opt",
                remark="final",
                output_dir=str(parent),
            )
        )
        work_dir = Path(record.work_dir)
        assert work_dir.parent == parent.resolve()
        assert work_dir.name == "ethanol_opt_final"
        assert record.spec.name == work_dir.name
    finally:
        mgr.shutdown()


def test_waiting_review_status_flags() -> None:
    assert JobStatus.WAITING_REVIEW.is_active is True
    assert JobStatus.WAITING_REVIEW.is_terminal is False


def test_pause_for_review_and_resume_roundtrip(tmp_path: Path) -> None:
    mgr = JobManager(run_root=tmp_path, poll_interval=30)
    try:
        work_dir = tmp_path / "job-running"
        work_dir.mkdir()
        record = JobRecord(
            id="job-running",
            spec=JobSpec(workflow="fake", name="review"),
            status=JobStatus.RUNNING,
            work_dir=str(work_dir),
        )
        mgr.store.create(record)

        paused = mgr.pause_for_review(record.id, {"decision_id": "dec-1"})
        assert paused.status == JobStatus.WAITING_REVIEW
        assert paused.result is not None
        assert paused.result["review_payload"]["decision_id"] == "dec-1"
        assert mgr.event_log(record.id).read_all()[-1]["type"] == "job.waiting_review"

        resumed = mgr.resume(record.id, {"approved": True})
        assert resumed.status == JobStatus.RUNNING
        assert resumed.result is not None
        assert resumed.result["review_resolution"]["approved"] is True
        assert mgr.event_log(record.id).read_all()[-1]["type"] == "job.review_resumed"
    finally:
        mgr.shutdown()


def test_poll_loop_skips_waiting_review_jobs(tmp_path: Path) -> None:
    mgr = JobManager(run_root=tmp_path, poll_interval=30)
    try:
        work_dir = tmp_path / "job-waiting"
        work_dir.mkdir()
        record = JobRecord(
            id="job-waiting",
            spec=JobSpec(workflow="fake", name="waiting"),
            status=JobStatus.WAITING_REVIEW,
            work_dir=str(work_dir),
        )
        mgr.store.create(record)

        seen: list[str] = []

        def fake_poll(job_id: str) -> None:
            seen.append(job_id)

        mgr._poll_job = fake_poll  # type: ignore[method-assign]
        mgr._poll_stop.set()
        mgr._poll_loop()

        assert record.id not in seen
    finally:
        mgr.shutdown()


def test_move_delete_block_waiting_review_jobs(tmp_path: Path) -> None:
    mgr = JobManager(run_root=tmp_path, poll_interval=30)
    try:
        project = mgr.projects.create_project("Target")
        work_dir = tmp_path / "job-review-blocked"
        work_dir.mkdir()
        record = JobRecord(
            id="job-review-blocked",
            spec=JobSpec(workflow="fake", name="blocked", project_id=mgr.default_project_id),
            status=JobStatus.WAITING_REVIEW,
            work_dir=str(work_dir),
            project_id=mgr.default_project_id,
        )
        mgr.store.create(record)

        with pytest.raises(ValueError, match="active job"):
            mgr.move_job(record.id, str(project["project_id"]))
        with pytest.raises(ValueError, match="active"):
            mgr.delete_job(record.id)
    finally:
        mgr.shutdown()


def test_requeue_active_on_startup_preserves_waiting_review(tmp_path: Path) -> None:
    db = tmp_path / "jobs.db"
    store = JobStore(db)
    waiting_dir = tmp_path / "waiting"
    waiting_dir.mkdir()
    store.create(
        JobRecord(
            id="waiting-review",
            spec=JobSpec(workflow="fake", name="review"),
            status=JobStatus.WAITING_REVIEW,
            work_dir=str(waiting_dir),
        )
    )

    mgr = JobManager(run_root=tmp_path, store=store, poll_interval=30)
    try:
        waiting = mgr.get("waiting-review")
        assert waiting is not None
        assert waiting.status == JobStatus.WAITING_REVIEW
    finally:
        mgr.shutdown()


def test_poll_job_translates_waiting_review_exit_code(tmp_path: Path) -> None:
    mgr = JobManager(run_root=tmp_path, poll_interval=30)
    try:
        work_dir = tmp_path / "job-review-gate"
        work_dir.mkdir()
        (work_dir / "review_payload.json").write_text(
            json.dumps(
                {
                    "study_id": "study-1",
                    "status": "waiting",
                    "pending_decisions": ["decision-1"],
                }
            ),
            encoding="utf-8",
        )
        record = JobRecord(
            id="job-review-gate",
            spec=JobSpec(workflow="mechanism", name="review-gate"),
            status=JobStatus.RUNNING,
            work_dir=str(work_dir),
        )
        mgr.store.create(record)
        mgr.runner.poll = lambda record: (True, EXIT_WAITING_REVIEW)  # type: ignore[method-assign]

        mgr._poll_job(record.id)

        updated = mgr.get(record.id)
        assert updated is not None
        assert updated.status == JobStatus.WAITING_REVIEW
        assert updated.exit_code == EXIT_WAITING_REVIEW
        assert updated.completed_at is None
        assert updated.result is not None
        assert updated.result["review_payload"]["study_id"] == "study-1"
        assert updated.result["review_payload"]["pending_decisions"] == ["decision-1"]
        assert mgr.event_log(record.id).read_all()[-1]["type"] == "job.waiting_review"
    finally:
        mgr.shutdown()


@pytest.mark.skip(reason="mechanism retired — populate_mechanism_study_result_metadata removed")
def test_collect_result_extracts_mechanism_study_metadata_for_completed_job(tmp_path: Path) -> None:
    mgr = JobManager(run_root=tmp_path, poll_interval=30)
    try:
        work_dir = tmp_path / "job-mech-complete"
        work_dir.mkdir()
        _write_mechanism_study_json(
            work_dir,
            study_id="study-1",
            quality="high",
            fidelity_profile_name="s3",
            include_high_fidelity=True,
        )
        record = JobRecord(
            id="job-mech-complete",
            spec=JobSpec(
                workflow="mechanism",
                name="completed-study",
                method={"study_id": "study-1"},
            ),
            status=JobStatus.RUNNING,
            work_dir=str(work_dir),
        )

        result = mgr._collect_result(record)

        assert result["provider"] == "native"
        assert result["fidelity"] == "s4"
        assert result["quality"] == "high"
    finally:
        mgr.shutdown()


@pytest.mark.skip(reason="mechanism retired — populate_mechanism_study_result_metadata removed")
def test_poll_job_waiting_review_extracts_mechanism_study_metadata(tmp_path: Path) -> None:
    mgr = JobManager(run_root=tmp_path, poll_interval=30)
    try:
        work_dir = tmp_path / "job-mech-review"
        work_dir.mkdir()
        _write_mechanism_study_json(
            work_dir,
            study_id="study-1",
            quality="medium",
            fidelity_profile_name="s3",
        )
        (work_dir / "review_payload.json").write_text(
            json.dumps(
                {
                    "study_id": "study-1",
                    "status": "waiting",
                    "pending_decisions": ["decision-1"],
                    "effective_fidelity": "s3",
                }
            ),
            encoding="utf-8",
        )
        record = JobRecord(
            id="job-mech-review",
            spec=JobSpec(
                workflow="mechanism",
                name="review-study",
                method={"study_id": "study-1"},
            ),
            status=JobStatus.RUNNING,
            work_dir=str(work_dir),
        )
        mgr.store.create(record)
        mgr.runner.poll = lambda record: (True, EXIT_WAITING_REVIEW)  # type: ignore[method-assign]

        mgr._poll_job(record.id)

        updated = mgr.get(record.id)
        assert updated is not None
        assert updated.result is not None
        assert updated.result["provider"] == "native"
        assert updated.result["fidelity"] == "s3"
        assert updated.result["quality"] == "medium"
        assert updated.result["review_payload"]["study_id"] == "study-1"
    finally:
        mgr.shutdown()


@pytest.mark.skip(reason="mechanism retired — populate_mechanism_study_result_metadata removed")
def test_collect_result_skips_mechanism_metadata_when_study_dir_missing(tmp_path: Path) -> None:
    mgr = JobManager(run_root=tmp_path, poll_interval=30)
    try:
        work_dir = tmp_path / "job-mech-missing"
        work_dir.mkdir()
        record = JobRecord(
            id="job-mech-missing",
            spec=JobSpec(
                workflow="mechanism",
                name="missing-study",
                method={"study_id": "missing-study"},
            ),
            status=JobStatus.RUNNING,
            work_dir=str(work_dir),
        )

        result = mgr._collect_result(record)

        assert "provider" not in result
        assert "fidelity" not in result
        assert "quality" not in result
    finally:
        mgr.shutdown()


@pytest.mark.skip(reason="mechanism retired — mechanism store helpers removed")
def test_migration_006_and_mechanism_store_helpers(tmp_path: Path) -> None:
    db_path = tmp_path / "db" / "scheduler.db"
    applied_first = migrate(db_path)
    applied_second = migrate(db_path)

    # 001-012 apply directly except the jobs-gated ones (002/005/008 check
    # the jobs table, which only JobStore._init_schema creates, so they land
    # on the JobStore init below).
    assert applied_first == 9
    assert applied_second == 0

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        study_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(mechanism_studies)").fetchall()
        }
        task_columns = {row[1] for row in conn.execute("PRAGMA table_info(stage_tasks)").fetchall()}
    assert "mechanism_studies" in tables
    assert "decision_points" in tables
    assert {
        "reaction_json",
        "mechanism_plan_json",
        "config_hash",
        "cycle_index",
        "consumed_cycle",
    } <= study_columns
    assert "status_detail" in task_columns

    store = JobStore(db_path)
    with sqlite3.connect(db_path) as conn:
        job_columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    assert "group_id" in job_columns
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO mechanism_studies "
            "(id, job_id, study_json, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "study-1",
                "job-1",
                '{"study_id": "study-1"}',
                "waiting",
                "2026-08-12T00:00:00+00:00",
                "2026-08-12T01:00:00+00:00",
            ),
        )
        conn.execute(
            "INSERT OR REPLACE INTO decision_points "
            "(id, study_id, status, payload, resolution, created_at, resolved_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "decision-1",
                "study-1",
                "waiting",
                '{"decision": 1}',
                None,
                "2026-08-12T00:10:00+00:00",
                None,
            ),
        )
        conn.commit()

    study = store.get_mechanism_study("study-1")
    assert study is not None
    assert study["status"] == "waiting"
    assert store.list_mechanism_studies(limit=10)[0]["id"] == "study-1"

    decision = store.get_decision_point("decision-1")
    assert decision is not None
    assert decision["status"] == "waiting"
    assert store.list_decision_points("study-1", limit=10)[0]["id"] == "decision-1"


# ---------------------------------------------------------------------------
# v2 naming/flattening (Unit A): task dirs are "<molecule>_<task>_<remark>",
# job_id stays the timestamped DB identity and moves into the log content.
# ---------------------------------------------------------------------------


def _quiet_manager(tmp_path: Path) -> JobManager:
    runner = MagicMock()
    runner.poll.return_value = (False, None)
    return JobManager(run_root=tmp_path, runner=runner, poll_interval=30)


def test_submit_v2_named_task_dir_and_timestamped_job_id(tmp_path: Path) -> None:
    mgr = _quiet_manager(tmp_path)
    try:
        record = mgr.submit(
            JobSpec(
                workflow="Confsearch",
                name="demo",
                input={"source": "CCO"},
                molecule_name="ethanol",
                task_name="opt",
                remark="final",
            )
        )
        work_dir = Path(record.work_dir)
        assert work_dir.name == "ethanol_opt_final"
        assert record.spec.name == work_dir.name
        assert work_dir.parent.name == mgr.default_project_id
        assert work_dir.parent.parent == tmp_path.resolve()
        # job_id keeps the {ts}_{seq:03d}_{safe_name} format as DB identity.
        assert re.fullmatch(r"\d{8}_\d{6}_\d{3}_demo", record.id)
    finally:
        mgr.shutdown()


def test_submit_without_task_name_uses_workflow_component(tmp_path: Path) -> None:
    mgr = _quiet_manager(tmp_path)
    try:
        record = mgr.submit(JobSpec(workflow="fake", name="legacyjob", input={"source": "Y"}))
        assert Path(record.work_dir).name == "legacyjob_fake"
    finally:
        mgr.shutdown()


def test_submit_dedupes_colliding_task_dirs(tmp_path: Path) -> None:
    mgr = _quiet_manager(tmp_path)
    try:
        spec = JobSpec(workflow="fake", name="dup", input={"source": "Y"})
        first = mgr.submit(spec)
        second = mgr.submit(spec)
        assert Path(first.work_dir).name == "dup_fake"
        assert Path(second.work_dir).name == "dup_fake__02"
        assert first.spec.name == Path(first.work_dir).name
        assert second.spec.name == Path(second.work_dir).name
    finally:
        mgr.shutdown()


def _launch_with_mocked_popen(tmp_path: Path) -> JobRecord:
    """Drive JobRunner.submit with Popen mocked so only pre-launch io runs."""
    work_dir = tmp_path / "proj" / "ethanol_energy"
    work_dir.mkdir(parents=True)
    spec = JobSpec(
        workflow="energy",
        name="demo",
        input={"source": "CCO", "source_type": "smiles"},
        molecule_name="ethanol",
    )
    record = JobRecord(id="20260823_120000_001_demo", spec=spec, work_dir=str(work_dir))
    event_log = JobEventLog(work_dir / "events.jsonl")
    runner = JobRunner()
    with patch("acp.scheduler.runner.subprocess.Popen") as popen:
        popen.return_value = MagicMock(pid=12345)
        runner.submit(record, event_log, threading.Event())
    return record


def test_stdout_log_header_carries_job_id(tmp_path: Path) -> None:
    record = _launch_with_mocked_popen(tmp_path)
    work_dir = Path(record.work_dir)
    stdout = (work_dir / "WORK" / "00_RUNTIME" / "stdout.log").read_text(encoding="utf-8")
    stderr = (work_dir / "WORK" / "00_RUNTIME" / "stderr.log").read_text(encoding="utf-8")
    for text in (stdout, stderr):
        assert f"# job_id: {record.id}" in text
        assert "# workflow: energy" in text
        assert f"# task_dir_name: {work_dir.name}" in text
        assert "# command: " in text


def test_task_json_carries_job_id_and_task_dir_name(tmp_path: Path) -> None:
    record = _launch_with_mocked_popen(tmp_path)
    payload = json.loads((Path(record.work_dir) / "task.json").read_text(encoding="utf-8"))
    assert payload["task_id"] == record.id
    assert payload["task_dir_name"] == "ethanol_energy"
    assert payload["workflow"] == "energy"
