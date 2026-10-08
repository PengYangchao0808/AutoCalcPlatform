# pyright: reportMissingImports=false, reportUnknownVariableType=false
"""Cross-defect end-to-end inverted probes (acp-execution-integrity todo 17).

Inverts **every** scenario of the defect baseline probe
``.omo/evidence/acp-cccp-design-review/probe.py`` (assertions that passed at
commit ``2d1725b``) into post-fix assertions, and combines the defects across
subsystems.  Everything runs offline: fake SSH/SFTP, protocol doubles,
temporary directories — no network, no QC binaries, no ``sleep``-based races
(deterministic single-thread interleavings + ``threading.Barrier`` only).

Probe scenario → post-fix E2E mapping (authoritative copy also lives in
``.omo/evidence/acp-execution-integrity/task-17-e2e.json``)::

    probe scenario                  defect   post-fix E2E test
    ------------------------------  ------   ---------------------------------------------
    remote_collision                D01      test_e2e_d01_d02_d05_full_sequence_on_one_fake_node
    ambiguous_submit                D02      (same test: unconfirmed, no cleanup, reconcile)
    cancellation_restart            D05      (same test: CANCELLING confirmed via bjobs)
    deployment           D03      test_e2e_d03_release_content_deletion_partial_upload_and_gate
    lifecycle                       D04      test_e2e_d04_stale_poll_terminal_and_progress
    (poll/pause races)   D04      test_e2e_d04_barrier_interleavings_and_attempt_isolation
    fingerprint           D06      test_e2e_d06_identity_change_with_resumes_publish_retry_handoff
    resume_existing_plan_coverage   V01/V02  (same test: multi-resume + publish retry)
    (thermo handoff legs)           V03      (same test: handoff survives resume)
    plan_cardinality                D08      test_e2e_d08_rejection_then_d07_blocking
    dependencies                    D07      test_e2e_d08_rejection_then_d07_blocking

The original probe and ``probe-results.json`` are NOT modified by this suite:
they are defect-baseline evidence (expected to PASS on ``2d1725b`` and to FAIL
on the fixed code) — never target assertions.

Run with: PYTHONPATH=src python3.11 -m pytest tests/test_execution_integrity_e2e.py -q
"""

from __future__ import annotations

import json
import os
import posixpath
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

import acp.calculations.executor as executor_module
from acp.calculations import identity as identity_mod
from acp.calculations import result_publication
from acp.calculations.contracts import (
    CalculationPlan,
    CalculationResult,
    CalculationStep,
    StepKind,
    StructureArtifact,
    StructureRole,
    validate_plan,
)
from acp.calculations.executor import CalculationPlanExecutor
from acp.calculations.identity import IDENTITY_SCHEMA
from acp.scheduler.events import JobEventLog
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.remote import release as release_mod
from acp.scheduler.remote import ssh as ssh_mod
from acp.scheduler.remote.config import RemoteExecutionConfig
from acp.scheduler.remote.paths import compose_remote_dir, storage_relative_path
from acp.scheduler.remote.release import build_release_manifest, ensure_node_release, releases_root
from acp.scheduler.remote.runner import (
    RemoteJobRunner,
    RemoteSubmissionIndeterminate,
    RemoteSubmissionRejected,
)
from acp.scheduler.remote.sftp import FileStager
from acp.scheduler.remote.ssh import SSHConnectionPool
from acp.scheduler.remote.submission import submission_id_for, submission_lsf_name
from acp.scheduler.store import JobStateConflictError
from acp.storage.manifest import ResultManifest
from tests.test_checkpoint_resume_semantics import (
    _counting_dispatcher as _resume_counting_dispatcher,
)
from tests.test_checkpoint_resume_semantics import (
    _dispatcher as _resume_dispatcher,
)
from tests.test_checkpoint_resume_semantics import (
    _handoff as _read_handoff,
)
from tests.test_checkpoint_resume_semantics import (
    _thermo_plan as _resume_thermo_plan,
)
from tests.test_checkpoint_resume_semantics import (
    _thermo_resume_dispatcher,
)
from tests.test_job_state_transitions import (
    _event_types as _state_event_types,
)
from tests.test_job_state_transitions import (
    _make_manager as _state_manager,
)
from tests.test_job_state_transitions import (
    _raw_row,
    _seed_running_job,
)
from tests.test_job_state_transitions import (
    _record as _state_record,
)
from tests.test_job_state_transitions import (
    _registry_paths as _state_registry_paths,
)
from tests.test_plan_contract_guards import (
    CARDINALITY_ERROR,
    THERMOCHEMISTRY_ERROR,
    _assert_rejected_without_primitives,
)
from tests.test_plan_prerequisites import _failed_opt
from tests.test_plan_prerequisites import _plan as _prereq_plan
from tests.test_remote_cancel_confirmation import (
    _event_types as _cancel_event_types,
)
from tests.test_remote_cancel_confirmation import (
    _make_manager as _cancel_manager,
)
from tests.test_remote_cancel_confirmation import (
    _MonitorSpy as _CancelMonitorSpy,
)
from tests.test_remote_cancel_confirmation import (
    _no_local_kill,
    _RunnerSpy,
)
from tests.test_remote_cancel_confirmation import (
    _remote_meta as _cancel_remote_meta,
)
from tests.test_remote_cancel_confirmation import (
    _seed_job as _seed_cancel_job,
)
from tests.test_remote_code_release import make_env, make_tree, patch_client
from tests.test_remote_phase2 import FakeSFTP, FakeSSHClient, make_node

try:
    import paramiko  # noqa: F401

    REMOTE_AVAILABLE = True
except ImportError:  # pragma: no cover
    REMOTE_AVAILABLE = False

requires_remote = pytest.mark.skipif(not REMOTE_AVAILABLE, reason="paramiko not installed")


# ====================================================================== #
# Shared local helpers
# ====================================================================== #


def _past_iso(seconds: int = 600) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def _spec(workflow: str = "singlepoint", name: str = "same") -> JobSpec:
    return JobSpec(workflow=workflow, name=name, input={"source": "CCO"})


def _write_input(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "input.xyz"
    path.write_text("1\ninput\nH 0 0 0\n", encoding="utf-8")
    return path


def _plan(root: Path, kinds: list[StepKind], *, workflow: str = "test") -> CalculationPlan:
    """Probe ``plan_for`` inverted: same shape, consumed by post-fix suites."""
    return CalculationPlan(
        workflow=workflow,
        profile="HF",
        items=[StructureArtifact(path=_write_input(root), elements=["H"], source="e2e")],
        steps=[CalculationStep(kind=kind) for kind in kinds],
    )


def _artifact(path: Path) -> StructureArtifact:
    return StructureArtifact(path=path, elements=["H"], source="e2e")


def _sp_products(root: Path) -> list[str]:
    manifest = ResultManifest.read(root / "RESULT")
    return [p.id for p in manifest.products if p.id.startswith("step_0_singlepoint")]


def _clear_release_registries() -> None:
    """Mirror test_remote_code_release's registry isolation (module autouse)."""
    for registry in (
        release_mod._REF_REGISTRY,
        release_mod._ACTIVE_STAGING,
        release_mod._NODE_LOCKS,
    ):
        registry.clear()


# ====================================================================== #
# D01 × D02 × D05 — one full sequence on one fake node
# ====================================================================== #


class _NodeHarness:
    """Real RemoteJobRunner over FakeSSH/FakeSFTP: bsub counter + remove spy."""

    def __init__(self, sftp: FakeSFTP, config: RemoteExecutionConfig) -> None:
        self.pool = SSHConnectionPool()
        self.sftp = sftp
        self.client = FakeSSHClient(sftp)
        self.bsub_calls = 0
        self.removes: list[str] = []
        self.bsub_result: tuple[int, str, str] = (
            0,
            "Job <54321> is submitted to queue <normal>.\n",
            "",
        )
        self.query_handler = None  # callable(cmd) -> (code, out, err) | None
        harness = self

        def cmd_handler(cmd: str):
            if "bsub" in cmd and "<" in cmd:
                harness.bsub_calls += 1
                return harness.bsub_result
            if harness.query_handler is not None:
                result = harness.query_handler(cmd)
                if result is not None:
                    return result
            return (0, "", "")

        self.client.cmd_handler = cmd_handler
        self.runner = RemoteJobRunner(
            self.pool, config, stager=FileStager(self.pool), poll_interval=0
        )
        original_remove = self.runner._stager.remove_remote_dir

        def spy_remove(node, path):
            harness.removes.append(str(path))
            return original_remove(node, path)

        self.runner._stager.remove_remote_dir = spy_remove  # type: ignore[method-assign]

    def submit(self, record: JobRecord, event_log: JobEventLog, **kw: object) -> str:
        with patch.object(ssh_mod, "_create_client", side_effect=lambda n, timeout=30: self.client):
            return self.runner.submit_remote(record, event_log, **kw)  # type: ignore[arg-type]

    def reconcile(self, record: JobRecord) -> str:
        with patch.object(ssh_mod, "_create_client", side_effect=lambda n, timeout=30: self.client):
            return self.runner.reconcile_submission(record)

    def close(self) -> None:
        self.pool.close()


def _identity_record(root: Path, project: str) -> tuple[JobRecord, JobEventLog]:
    """Same task name ``same_singlepoint`` under *project* (probe remote_collision)."""
    work_dir = root / project / "same_singlepoint"
    work_dir.mkdir(parents=True, exist_ok=True)
    record = JobRecord(
        id=f"job-{project}",
        spec=_spec(workflow="singlepoint", name="same"),
        work_dir=str(work_dir),
        result={},
    )
    rel = storage_relative_path(record, root)
    record.result = {"remote": {"schema": 1, "relative": rel, "attempt": 1}}
    return record, JobEventLog(work_dir / "events.jsonl")


def _intent_record(
    tmp_path: Path, node_name: str, remote_work_dir: str, job_id: str
) -> tuple[JobRecord, JobEventLog, str]:
    """Contract-A submit intent (bsub before persist) for the D02 leg."""
    work_dir = tmp_path / "runs" / "projC" / job_id
    work_dir.mkdir(parents=True, exist_ok=True)
    rel = f"projC/{job_id}"
    remote_dir = posixpath.join(remote_work_dir, rel)
    sid = submission_id_for(job_id, 1)
    record = JobRecord(
        id=job_id,
        spec=_spec(workflow="Confsearch"),
        work_dir=str(work_dir),
        result={
            "remote_dir": remote_dir,
            "remote": {
                "schema": 1,
                "relative": rel,
                "attempt": 1,
                "node": node_name,
                "submission_id": sid,
                "submit_state": "intent",
                "lease_expires_at": _past_iso(),
            },
        },
    )
    return record, JobEventLog(work_dir / "events.jsonl"), remote_dir


@requires_remote
def test_e2e_d01_d02_d05_full_sequence_on_one_fake_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D01 directory identity × D02 indeterminate submission × D05 cancel
    confirmation as ONE sequence against a single fake node."""
    node = make_node()  # the one fake node: "compute-01"
    config = RemoteExecutionConfig(execution_mode="remote", auto_sync=False, nodes=[node])
    root = tmp_path / "runs"
    root.mkdir(parents=True, exist_ok=True)
    sftp = FakeSFTP()
    harness = _NodeHarness(sftp, config)

    # ── D01 (probe remote_collision): same task name, different projects →
    # distinct storage-identity-derived remote dirs, markers in each. ──
    dirs: list[str] = []
    for project in ("projA", "projB"):
        record, event_log = _identity_record(root, project)
        harness.submit(record, event_log)
        remote_dir = record.result["remote_dir"]
        expected = compose_remote_dir(storage_relative_path(record, root), node)
        assert remote_dir == expected, "remote dir must derive from the local storage identity"
        assert posixpath.join(remote_dir, "submit.lsf") in sftp.files
        assert posixpath.join(remote_dir, "job.json") in sftp.files
        dirs.append(remote_dir)
    assert dirs[0] != dirs[1], "probe remote_collision: same-name dirs must stay distinct"
    assert dirs[0].endswith("/projA/same_singlepoint")
    assert dirs[1].endswith("/projB/same_singlepoint")

    # ── D02 (probe ambiguous_submit): reply lost after bsub → unconfirmed,
    # directory kept, then reconcile adopts the id WITHOUT a second bsub. ──
    harness.bsub_result = (0, "garbage without job id\n", "")
    lost, lost_log, lost_dir = _intent_record(
        tmp_path, node.name, node.remote_work_dir, "lostreply"
    )
    bsubs_before = harness.bsub_calls
    with pytest.raises(RemoteSubmissionIndeterminate):
        harness.submit(lost, lost_log)
    assert harness.removes == [], "probe ambiguous_submit: no cleanup on indeterminate"
    assert posixpath.join(lost_dir, "submit.lsf") in sftp.files
    assert posixpath.join(lost_dir, "job.json") in sftp.files
    meta = (lost.result or {}).get("remote") or {}
    assert meta.get("submit_state") == "unconfirmed"
    assert "remote.submit_unconfirmed" in [e["type"] for e in lost_log.read_all()]
    assert harness.bsub_calls == bsubs_before + 1

    sid = submission_id_for("lostreply", 1)
    name = submission_lsf_name(sid)

    def queries(cmd: str):
        if "bjobs" in cmd and name in cmd:
            return (
                0,
                "JOBID   USER    STAT  QUEUE      JOBNAME          SUBMIT\n"
                f"54321   qc      RUN   normal    {name}   Thu Oct  1\n",
                "",
            )
        return None

    harness.query_handler = queries
    verdict = harness.reconcile(lost)
    assert verdict == "found", "an adoptable submission must be found, never re-submitted"
    assert lost.remote_job_id == "54321"
    assert harness.bsub_calls == bsubs_before + 1, "reconcile must never re-run bsub"
    assert harness.removes == []
    harness.close()

    # ── D05 (probe cancellation_restart): CANCELLING survives restart +
    # unreachable node, bkill only once reachable, CANCELLED only after
    # bjobs confirmation. ──
    mgr = _cancel_manager(tmp_path, monkeypatch)
    try:
        _no_local_kill(mgr)
        job_id = "e2e-cancel"
        _seed_cancel_job(
            mgr,
            tmp_path,
            job_id,
            status=JobStatus.CANCELLING,
            remote_job_id="7001",
            cancel_state="requested",
        )
        runner_spy = _RunnerSpy(lsf_id="7001")
        monitor = _CancelMonitorSpy(["running"])
        monitor.raise_on_get = True  # node unreachable at restart
        mgr.remote_runner = runner_spy  # type: ignore[assignment]
        mgr._remote_monitor = monitor  # type: ignore[assignment]

        mgr._requeue_active_on_startup()
        stored = mgr.store.get(job_id)
        assert stored is not None and stored.status == JobStatus.CANCELLING, (
            "probe cancellation_restart asserted an unconfirmed CANCELLED; "
            "restart must keep CANCELLING until LSF confirms"
        )
        assert monitor.kills == [], "no blind bkill while the node is unreachable"
        assert _cancel_remote_meta(mgr, job_id).get("cancel_state") == "unconfirmed"

        mgr._poll_job(job_id)
        assert mgr.store.get(job_id).status == JobStatus.CANCELLING, (
            "communication failure must keep CANCELLING"
        )
        assert monitor.kills == []

        monitor.raise_on_get = False  # node reachable again
        mgr._poll_job(job_id)
        stored = mgr.store.get(job_id)
        assert stored is not None and stored.status == JobStatus.CANCELLING, (
            "alive classification keeps CANCELLING — confirmation is still pending"
        )
        assert monitor.kills == ["7001"], "bkill re-sent exactly once when reachable"
        assert _cancel_remote_meta(mgr, job_id).get("cancel_state") == "sent"

        monitor.statuses = ["not_found"]  # bjobs confirms the job is gone
        mgr._poll_job(job_id)
        stored = mgr.store.get(job_id)
        assert stored is not None and stored.status == JobStatus.CANCELLED
        assert _cancel_remote_meta(mgr, job_id).get("cancel_state") == "confirmed"
        assert "remote.cancel_confirmed" in _cancel_event_types(mgr, job_id)
        assert monitor.kills == ["7001"], "confirmation must not trigger another bkill"
        assert runner_spy.submit_calls == 0, "cancel path must never submit"
    finally:
        mgr.shutdown()


# ====================================================================== #
# D03 — content-hash releases + fail-closed submission gate
# ====================================================================== #


@requires_remote
def test_e2e_d03_release_content_deletion_partial_upload_and_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Probe deployment inverted: content/deletion drive the release id,
    partial uploads raise without a ``.complete`` marker, and an unverified
    submission is rejected BEFORE bsub / any remote directory write."""
    # (a) content change with preserved mtime → new release id.
    tree = tmp_path / "tree"
    make_tree(tree)
    m1 = build_release_manifest(tree)
    target = tree / "src" / "cccp" / "core.py"
    st = target.stat()
    target.write_bytes(b"def f():  # changed\n")
    os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns))
    m2 = build_release_manifest(tree)
    assert m2.release_id != m1.release_id, "content (not mtime) must drive the release id"

    # (b) local deletion → new release id + file excluded
    # (probe deployment: needed_after_delete was False).
    target.unlink()
    m3 = build_release_manifest(tree)
    assert m3.release_id != m2.release_id, "probe deployment: deletion must be detected"
    assert "src/cccp/core.py" not in m3.files

    # (c) partial upload → raise, no .complete, no published dir
    # (probe deployment: partial sync errors did not raise).
    _clear_release_registries()
    tree2 = tmp_path / "tree2"
    make_tree(tree2)
    manifest = build_release_manifest(tree2)
    env = make_env()
    count = {"n": 0}

    def fail_second(path: str) -> None:
        count["n"] += 1
        if count["n"] == 2:
            raise OSError("simulated network failure on file 2")

    env.sftp.put_hook = fail_second  # type: ignore[attr-defined]
    try:
        with patch_client(env):
            with pytest.raises(OSError, match="simulated network failure"):
                ensure_node_release(
                    env.node,
                    manifest,
                    stager=env.stager,
                    ssh=env.pool,
                    state_dir=tmp_path / "state",
                    project_root=tree2,
                )
        assert not [p for p in env.sftp.files if p.endswith("/.complete")], (
            "a partial upload must never produce the publish marker"
        )
        final = posixpath.join(releases_root(env.node), manifest.release_id)
        assert final not in env.sftp.dirs, "an incomplete release must never be published"
    finally:
        env.pool.close()
        _clear_release_registries()

    # (d) submission gate: auto_sync off, no verified release, no escape
    # hatch → rejected BEFORE bsub, remote untouched, failure recorded.
    monkeypatch.delenv("ACP_REMOTE_ALLOW_UNVERSIONED", raising=False)
    monkeypatch.delenv("ACP_REMOTE_CODE_RELEASE", raising=False)
    gate_node = make_node(name="gate-node")
    gate_config = RemoteExecutionConfig(execution_mode="remote", auto_sync=False, nodes=[gate_node])
    gate_sftp = FakeSFTP()
    gate_client = FakeSSHClient(gate_sftp)
    gate_counts = {"bsub": 0}

    def gate_handler(cmd: str):
        if "bsub" in cmd and "<" in cmd:
            gate_counts["bsub"] += 1
            return (0, "Job <54321> is submitted to queue <normal>.\n", "")
        return (0, "", "")

    gate_client.cmd_handler = gate_handler
    pool = SSHConnectionPool()
    runner = RemoteJobRunner(pool, gate_config, stager=FileStager(pool), poll_interval=0)
    work_dir = tmp_path / "runs" / "projG" / "gate"
    work_dir.mkdir(parents=True, exist_ok=True)
    record = JobRecord(
        id="gate",
        spec=_spec(workflow="singlepoint", name="gate"),
        work_dir=str(work_dir),
        result={"remote": {"schema": 1, "relative": "projG/gate", "attempt": 1}},
    )
    event_log = JobEventLog(work_dir / "events.jsonl")
    try:
        with patch.object(ssh_mod, "_create_client", side_effect=lambda n, timeout=30: gate_client):
            with pytest.raises(RemoteSubmissionRejected, match="auto_sync is disabled"):
                runner.submit_remote(record, event_log)
    finally:
        pool.close()
    assert gate_counts["bsub"] == 0, "unverified submissions must be rejected BEFORE bsub"
    assert gate_sftp.files == {} and not gate_sftp.dirs, (
        "a rejected submission must not touch the remote directory"
    )
    types = [e["type"] for e in event_log.read_all()]
    assert "remote.code_release_failed" in types, "the rejection must be recorded"


# ====================================================================== #
# D04 — poll/pause interleavings, terminal protection, narrow progress
# ====================================================================== #


def test_e2e_d04_stale_poll_terminal_and_progress(tmp_path: Path) -> None:
    """Probe lifecycle inverted: pause during poll survives the stale write
    (probe: final RUNNING → now PAUSED), terminal observations are never
    resurrected, and progress writes never clobber status/spec/result."""
    mgr = _state_manager(tmp_path)
    try:
        # leg A — probe lifecycle: pause mid-poll, stale RUNNING dropped.
        record = _seed_running_job(mgr, tmp_path, "e2e-race")
        mgr.runner.pause_local = lambda job_id: True  # type: ignore[method-assign]
        seen: list[str] = []

        def poll(stale_record: JobRecord) -> tuple[bool, int | None]:
            mgr.pause_job(record.id)
            seen.append(mgr.store.get(record.id).status.value)  # type: ignore[union-attr]
            stale_record.progress = 0.55
            stale_record.current_stage = "sampling"
            return (False, None)

        mgr.runner.poll = poll  # type: ignore[method-assign]
        mgr._poll_job(record.id)
        final = mgr.store.get(record.id)
        assert final is not None
        assert seen == [JobStatus.PAUSED.value], "pause must win during the poll"
        assert final.status == JobStatus.PAUSED, (
            "probe lifecycle: stale poll write resurrected RUNNING — must stay PAUSED"
        )
        assert final.progress != 0.55, "stale progress observation must be dropped"
        events = _state_event_types(mgr, record.id)
        assert "job.poll_dropped_stale" in events
        assert "job.paused" in events

        # leg B — a terminal observation racing a pause must lose.
        record_b = _seed_running_job(mgr, tmp_path, "e2e-term-race")

        def terminal_poll(stale_record: JobRecord) -> tuple[bool, int | None]:
            mgr.pause_job(record_b.id)
            stale_record.exit_code = 0
            return (True, 0)

        mgr.runner.poll = terminal_poll  # type: ignore[method-assign]
        mgr._poll_job(record_b.id)
        final_b = mgr.store.get(record_b.id)
        assert final_b is not None
        assert final_b.status == JobStatus.PAUSED, "terminal observation must lose to pause"
        assert final_b.completed_at is None
        assert not (final_b.result or {}).get("terminal_side_effects_done")
        assert "job.poll_dropped_stale" in _state_event_types(mgr, record_b.id)

        # leg C — terminal committed inside the poll survives the old poll.
        record_c = _seed_running_job(mgr, tmp_path, "e2e-terminal")
        terminal_committed = threading.Event()

        def committing_poll(stale_record: JobRecord) -> tuple[bool, int | None]:
            committed = mgr.store.transition(
                record_c.id,
                expected_status=JobStatus.RUNNING,
                expected_revision=stale_record.revision,
                expected_attempt=stale_record.attempt,
                status=JobStatus.COMPLETED,
                exit_code=0,
                progress=1.0,
                completed_at="2026-01-01T00:00:00+00:00",
            )
            terminal_committed.set()
            assert committed.status == JobStatus.COMPLETED
            stale_record.exit_code = 0
            return (True, 0)

        mgr.runner.poll = committing_poll  # type: ignore[method-assign]
        mgr._poll_job(record_c.id)
        assert terminal_committed.is_set()
        final_c = mgr.store.get(record_c.id)
        assert final_c is not None
        assert final_c.status == JobStatus.COMPLETED, "terminal state must survive the old poll"
        assert final_c.completed_at == "2026-01-01T00:00:00+00:00"
        assert not (final_c.result or {}).get("terminal_side_effects_done")
        assert "job.poll_dropped_stale" in _state_event_types(mgr, record_c.id)

        # leg D — narrow progress write keeps status/spec/result byte-identical.
        record_d = _seed_running_job(
            mgr, tmp_path, "e2e-prog", progress=0.1, result={"state": {"stage": 1}}
        )
        db = tmp_path / "acp_jobs.db"
        before = _raw_row(db, record_d.id)

        def progress_poll(stale_record: JobRecord) -> tuple[bool, int | None]:
            stale_record.progress = 0.42
            stale_record.current_stage = "opt"
            return (False, None)

        mgr.runner.poll = progress_poll  # type: ignore[method-assign]
        mgr._poll_job(record_d.id)
        after = _raw_row(db, record_d.id)
        assert after["status"] == before["status"] == JobStatus.RUNNING.value
        assert after["spec_json"] == before["spec_json"], "progress writes never touch spec"
        assert after["result_json"] == before["result_json"], "result must stay byte-identical"
        assert after["progress"] == 0.42
        assert after["revision"] != before["revision"], "narrow write must bump revision"
    finally:
        mgr.shutdown()


def test_e2e_d04_barrier_interleavings_and_attempt_isolation(tmp_path: Path) -> None:
    """Barrier races (no sleeps) + old-attempt replay isolation."""
    mgr = _state_manager(tmp_path)
    try:
        # poll vs pause — pause wins regardless of interleaving.
        record = _seed_running_job(mgr, tmp_path, "e2e-barrier-poll-pause")
        mgr.runner.pause_local = lambda job_id: True  # type: ignore[method-assign]
        barrier = threading.Barrier(2, timeout=10)
        seen: dict[str, object] = {}

        def poll(stale_record: JobRecord) -> tuple[bool, int | None]:
            barrier.wait()
            stale_record.progress = 0.55
            stale_record.current_stage = "late"
            return (False, None)

        mgr.runner.poll = poll  # type: ignore[method-assign]

        def do_poll() -> None:
            mgr._poll_job(record.id)

        def do_pause() -> None:
            barrier.wait()
            seen["pause"] = mgr.pause_job(record.id).status

        t_poll = threading.Thread(target=do_poll)
        t_pause = threading.Thread(target=do_pause)
        t_poll.start()
        t_pause.start()
        t_poll.join(timeout=30)
        t_pause.join(timeout=30)
        assert not t_poll.is_alive() and not t_pause.is_alive()
        final = mgr.store.get(record.id)
        assert final is not None
        assert seen["pause"] == JobStatus.PAUSED
        assert final.status == JobStatus.PAUSED, "pause must win regardless of interleaving"
        assert "job.paused" in _state_event_types(mgr, record.id)

        # cancel vs unpause — cancel always ends CANCELLING.
        record2 = _seed_running_job(
            mgr, tmp_path, "e2e-barrier-cancel-unpause", status=JobStatus.PAUSED
        )
        barrier2 = threading.Barrier(2, timeout=10)

        def _resume(job_id: str) -> bool:
            barrier2.wait()
            return True

        mgr.runner.resume_local = _resume  # type: ignore[method-assign]
        mgr.runner.cancel_local = lambda job_id: True  # type: ignore[method-assign]
        seen2: dict[str, object] = {}

        def do_unpause() -> None:
            try:
                seen2["unpause"] = mgr.unpause_job(record2.id).status
            except ValueError as exc:
                seen2["unpause"] = f"rejected: {exc}"

        def do_cancel() -> None:
            barrier2.wait()
            result = mgr.cancel(record2.id)
            seen2["cancel"] = None if result is None else result.status

        t_unpause = threading.Thread(target=do_unpause)
        t_cancel = threading.Thread(target=do_cancel)
        t_unpause.start()
        t_cancel.start()
        t_unpause.join(timeout=30)
        t_cancel.join(timeout=30)
        assert not t_unpause.is_alive() and not t_cancel.is_alive()
        final2 = mgr.store.get(record2.id)
        assert final2 is not None
        assert final2.status == JobStatus.CANCELLING, "cancel must beat the racing unpause"
        assert seen2["cancel"] == JobStatus.CANCELLING
    finally:
        mgr.shutdown()

    # attempt isolation — an old-attempt replay can never modify the new row.
    db = tmp_path / "acp_jobs.db"
    mgr2 = _state_manager(tmp_path)
    try:
        mgr2.store.create(_state_record("e2e-iso", status=JobStatus.FAILED, error="boom"))
        old = mgr2.store.get("e2e-iso")
        assert old is not None and old.attempt == 1
        updated = mgr2.store.requeue_with_spec(
            "e2e-iso",
            new_spec=old.spec,
            expected_revision=old.revision,
            expected_attempt=1,
            expected_status=JobStatus.FAILED,
        )
        mgr2.store.transition(
            "e2e-iso",
            expected_status=JobStatus.QUEUED,
            expected_revision=updated.revision,
            expected_attempt=2,
            status=JobStatus.CANCELLED,
            completed_at="2026-01-02T00:00:00+00:00",
        )
        before = _raw_row(db, "e2e-iso")
        assert before["attempt"] == 2
        with pytest.raises(JobStateConflictError):
            mgr2._requeue_record_cas(
                "e2e-iso",
                new_spec=old.spec,
                expected=old,
                expected_status=(JobStatus.FAILED, JobStatus.CANCELLED),
                result={"continued_from": "failed"},
            )
        with pytest.raises(JobStateConflictError):
            mgr2._cas_write(
                old,
                expected_status=JobStatus.FAILED,
                status=JobStatus.RUNNING,
            )
    finally:
        mgr2.shutdown()
    after = _raw_row(db, "e2e-iso")
    assert after == before, "an old-attempt replay must never modify the new attempt's row"


# ====================================================================== #
# D06 × V01 × V02 × V03 — identity change, multi-resume, publish retry,
# downstream handoff
# ====================================================================== #


def _identity_plan(root: Path, *, role: StructureRole = StructureRole.MINIMUM) -> CalculationPlan:
    path = _write_input(root)
    return CalculationPlan(
        workflow="test",
        profile="HF",
        items=[StructureArtifact(path=path, elements=["H"], role=role, source="e2e")],
        steps=[CalculationStep(kind=StepKind.SINGLEPOINT)],
    )


def test_e2e_d06_identity_change_with_resumes_publish_retry_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cross-defect chain: D06 identity binding → V01 completed facts across
    multiple resumes → V02 publish-only retry → V03 handoff restore."""

    # ── Phase D06 (probe fingerprint): content/role bind the v2 identity and
    # an identity change re-runs QC instead of silently reusing results. ──
    root_a = tmp_path / "d06"
    plan_a = _identity_plan(root_a)
    before = identity_mod.compute_identity(plan_a).plan_identity
    assert before.startswith("v2:") and len(before) == len("v2:") + 32
    minimum = identity_mod.compute_identity(
        _identity_plan(root_a, role=StructureRole.MINIMUM)
    ).plan_identity
    transition = identity_mod.compute_identity(
        _identity_plan(root_a, role=StructureRole.TRANSITION_STATE)
    ).plan_identity
    assert minimum != transition, "probe fingerprint: role changes must bind the identity"

    sp_calls: list[int] = []

    def sp(request: object) -> CalculationResult:
        sp_calls.append(1)
        return CalculationResult(energy=-2.0)

    with patch.dict(executor_module._PRIMITIVE_DISPATCH, {StepKind.SINGLEPOINT: sp}):
        first = CalculationPlanExecutor().execute(plan_a, root_a)
        assert first.status == "completed" and sp_calls == [1]

        (root_a / "input.xyz").write_text("1\ninput\nHe 1 2 3\n", encoding="utf-8")
        after = identity_mod.compute_identity(plan_a).plan_identity
        assert after != before and after.startswith("v2:"), (
            "probe fingerprint: same path + new content must change the identity"
        )
        second = CalculationPlanExecutor().execute(plan_a, root_a)
        assert second.status == "completed"
        assert len(sp_calls) == 2, "an identity change must re-run QC, never adopt silently"

    payload_a = json.loads(
        (root_a / "WORK" / "00_RUNTIME" / "checkpoint.json").read_text(encoding="utf-8")
    )
    assert payload_a["identity_schema"] == IDENTITY_SCHEMA

    # ── Phase V01 (probe qc_call_counts 1→1→2): completed facts survive any
    # number of resumes; products persist; v2 resume_count key. ──
    root_b = tmp_path / "v01"
    plan_b = _plan(root_b, [StepKind.SINGLEPOINT, StepKind.FREQUENCY])
    sp_b: list[int] = []
    observations: list[dict[str, object]] = []
    with patch.dict(executor_module._PRIMITIVE_DISPATCH, _resume_dispatcher(sp_b)):
        for run in range(4):  # first run + three resumes
            (root_b / "job.json").write_text(json.dumps({"attempt": run + 1}), encoding="utf-8")
            result = CalculationPlanExecutor().execute(plan_b, root_b)
            payload = json.loads(
                (root_b / "WORK" / "00_RUNTIME" / "checkpoint.json").read_text(encoding="utf-8")
            )
            assert result.status == "failed"  # FREQ keeps failing
            assert payload["step_states"][0]["status"] == "completed", (
                "V01: the completed fact must never be rewritten on resume"
            )
            assert payload["resume_count"] == run and "attempts" not in payload
            observations.append(
                {
                    "sp_calls": len(sp_b),
                    "sp_executed": result.step_states[0].executed_this_run,
                    "sp_products": len(_sp_products(root_b)),
                }
            )
    assert [o["sp_calls"] for o in observations] == [1, 1, 1, 1], (
        f"probe qc_call_counts 1→1→2 inverted: {observations}"
    )
    assert [o["sp_executed"] for o in observations] == [True, False, False, False]
    assert [o["sp_products"] for o in observations] == [1, 1, 1, 1], (
        "probe products 1→0→1 inverted: products must survive every resume"
    )

    # ── Phase V02: a manifest publication failure retries publication only —
    # QC call counts stay frozen. ──
    root_c = tmp_path / "v02"
    plan_c = _plan(root_c, [StepKind.SINGLEPOINT, StepKind.FREQUENCY])
    sp_c: list[int] = []
    freq_c: list[int] = []
    real_register = result_publication.register_result_manifest
    injection = {"armed": True}

    def flaky_register(result_dir, manifest):  # type: ignore[no-untyped-def]
        if injection["armed"] and manifest.workflow == "frequency":
            injection["armed"] = False
            raise OSError("injected manifest write failure")
        return real_register(result_dir, manifest)

    monkeypatch.setattr(result_publication, "register_result_manifest", flaky_register)
    with patch.dict(
        executor_module._PRIMITIVE_DISPATCH,
        _resume_counting_dispatcher(root_c, sp_c, freq_c),
    ):
        run1 = CalculationPlanExecutor().execute(plan_c, root_c)
        assert run1.status == "failed", "the publication failure must be observable"
        assert len(sp_c) == 1 and len(freq_c) == 1
        run2 = CalculationPlanExecutor().execute(plan_c, root_c)
        assert run2.status == "completed", run2.errors
    assert len(sp_c) == 1, "SP QC must not re-run on publish retry"
    assert len(freq_c) == 1, "FREQ QC must not re-run on publish retry"
    state = result_publication.load_publication_state(root_c / "WORK" / "04_FREQ")
    assert state is not None and state.complete is True
    manifest_c = ResultManifest.read(root_c / "RESULT")
    assert any(p.id.startswith("step_0_singlepoint") for p in manifest_c.products)
    assert any(p.id.startswith("step_1_frequency") for p in manifest_c.products)

    # ── Phase V03 (probe thermo_requests[1] all-None): the downstream handoff
    # persists and the resumed THERMO step receives identical inputs. ──
    root_d = tmp_path / "v03"
    plan_d = _resume_thermo_plan(root_d)
    counts = {"opt": 0, "freq": 0, "sp": 0, "thermo": 0}
    thermo_requests: list[dict[str, object]] = []
    with patch.dict(
        executor_module._PRIMITIVE_DISPATCH,
        _thermo_resume_dispatcher(root_d, counts, thermo_requests),
    ):
        first_d = CalculationPlanExecutor().execute(plan_d, root_d)
        handoff_first = _read_handoff(root_d)
        second_d = CalculationPlanExecutor().execute(plan_d, root_d)
        handoff_second = _read_handoff(root_d)

    assert first_d.status == "failed" and second_d.status == "completed", second_d.errors
    assert counts == {"opt": 1, "freq": 1, "sp": 1, "thermo": 2}, (
        "V03: completed upstream steps must not re-run across the resume"
    )
    assert handoff_first["frequency_log_path"] == "WORK/04_FREQ/freq.out"
    assert handoff_first["single_point_energy"] == -3.0
    assert handoff_first["energy_unit"] == "hartree"
    assert handoff_second == handoff_first, "the persisted handoff must be stable"
    assert len(thermo_requests) == 2
    assert thermo_requests[0] == thermo_requests[1], (
        "probe therm_requests[1] == all-None inverted: identical handoff on resume"
    )
    assert thermo_requests[1]["freq_log_path"] is not None
    assert thermo_requests[1]["sp_energy_hartree"] == -3.0


# ====================================================================== #
# D08 rejection → D07 blocked propagation
# ====================================================================== #


def test_e2e_d08_rejection_then_d07_blocking(tmp_path: Path) -> None:
    """Probe plan_cardinality + dependencies inverted: invalid plans are
    rejected before any primitive; a failed upstream blocks dependents."""

    # ── D08 leg 1 (probe multi-item silently executed): two items rejected. ──
    task = tmp_path / "d08"
    input_path = _write_input(task)
    two_items = CalculationPlan(
        workflow="test",
        profile="HF",
        items=[_artifact(input_path), _artifact(input_path)],
        steps=[CalculationStep(kind=StepKind.SINGLEPOINT)],
    )
    errors, calls = _assert_rejected_without_primitives(two_items, CARDINALITY_ERROR, task)
    assert len(errors) == 1 and calls == 0

    # ── D08 leg 2 (probe duplicate kinds shared one output dir): rejected. ──
    dup = CalculationPlan(
        workflow="test",
        profile="HF",
        items=[_artifact(input_path)],
        steps=[
            CalculationStep(kind=StepKind.SINGLEPOINT),
            CalculationStep(kind=StepKind.SINGLEPOINT),
        ],
    )
    dup_errors = validate_plan(dup)
    assert any("duplicate step kind" in err and "singlepoint" in err for err in dup_errors), (
        dup_errors
    )
    spy = Mock(return_value=CalculationResult(energy=-1.0))
    with patch.dict(executor_module._PRIMITIVE_DISPATCH, {kind: spy for kind in StepKind}):
        with pytest.raises(ValueError, match="plan validation failed"):
            CalculationPlanExecutor().execute(dup, task)
    assert spy.call_count == 0, "no primitive may run for a rejected plan"

    # ── D08 leg 3: illegal THERMOCHEMISTRY combination rejected. ──
    thermo_only_root = tmp_path / "d08-thermo"
    thermo_only = CalculationPlan(
        workflow="test",
        profile="HF",
        items=[_artifact(_write_input(thermo_only_root))],
        steps=[CalculationStep(kind=StepKind.THERMOCHEMISTRY)],
    )
    _, thermo_calls = _assert_rejected_without_primitives(
        thermo_only, THERMOCHEMISTRY_ERROR, thermo_only_root
    )
    assert thermo_calls == 0

    # control: a legal single-item single-step plan still validates.
    legal = CalculationPlan(
        workflow="test",
        profile="HF",
        items=[_artifact(_write_input(tmp_path / "legal"))],
        steps=[CalculationStep(kind=StepKind.SINGLEPOINT)],
    )
    assert validate_plan(legal) == []

    # ── D07 (probe dependencies: FREQ consumed the failed OPT's coords) ──
    dep_root = tmp_path / "d07"
    dep_plan = _prereq_plan(dep_root, [StepKind.OPTIMIZE, StepKind.FREQUENCY, StepKind.SINGLEPOINT])
    freq_requests: list[object] = []
    freq_spy = Mock(
        side_effect=lambda req: (
            freq_requests.append(req.resources.get("coordinates"))
            or CalculationResult(frequencies=[100.0])
        )
    )
    sp_spy = Mock(return_value=CalculationResult(energy=-1.0))
    with patch.dict(
        executor_module._PRIMITIVE_DISPATCH,
        {
            StepKind.OPTIMIZE: _failed_opt,
            StepKind.FREQUENCY: freq_spy,
            StepKind.SINGLEPOINT: sp_spy,
        },
    ):
        result = CalculationPlanExecutor().execute(dep_plan, dep_root)

    assert [state.status for state in result.step_states] == ["failed", "blocked", "blocked"]
    assert freq_spy.call_count == 0, "FREQ must not be invoked after a failed OPT"
    assert sp_spy.call_count == 0, "SP must not be invoked after a failed OPT"
    assert freq_requests == [], (
        "probe dependencies: FREQ consumed failed-OPT coordinates — must be empty"
    )
    assert result.blocked_reasons == [
        {"index": 1, "reason": "upstream_failed"},
        {"index": 2, "reason": "upstream_failed"},
    ]
    assert result.status == "failed" and result.is_failed and not result.is_completed
    assert len(result.errors) == 1 and "opt did not converge" in result.errors[0]
    checkpoint = json.loads(
        (dep_root / "WORK" / "00_RUNTIME" / "checkpoint.json").read_text(encoding="utf-8")
    )
    assert [entry["status"] for entry in checkpoint["step_states"]] == [
        "failed",
        "blocked",
        "blocked",
    ]
    assert checkpoint["step_states"][1]["blocked_reason"] == "upstream_failed"
    manifest = ResultManifest.read(dep_root / "RESULT")
    assert manifest.status == "failed"
    blocked_products = {p.id for p in manifest.products if p.id.endswith("_blocked")}
    assert blocked_products == {"step_1_frequency_blocked", "step_2_singlepoint_blocked"}


# ====================================================================== #
# Todo 10 — R2 × R3 acceptance: side-effect isolation, attempt isolation,
# tasks/jobs consistency under delay, bounded convergence (no sleeps).
# ====================================================================== #


def test_e2e_r2_r3_side_effect_isolation_projection_delay_and_convergence(
    tmp_path: Path,
) -> None:
    """Cross-module acceptance (plan todo 10): terminal side effects (R2,
    GAP-1) and the jobs/tasks projection (R3, GAP-4) hold together under
    four deterministic interleavings on one manager — a stale terminal
    observation racing cancel (zero side effects on CAS rejection), a
    terminal CAS then immediate rerun (attempt isolation across jobs/
    job.json/tasks/provenance/cancel events), a delayed old projection
    after new PAUSED (jobs/tasks consistency under delay), and a
    sync-failure recovery that converges within the ceil(N/B) scan bound."""
    mgr = _state_manager(tmp_path)
    mgr.runner.pause_local = lambda job_id: True  # type: ignore[method-assign]
    mgr._start_submission_thread = lambda job_id, name: True  # type: ignore[method-assign]
    try:
        # ── (a) stale terminal observation racing cancel → rejected CAS. ──
        race_id = "e2e-t10-cancel-race"
        race = _seed_running_job(mgr, tmp_path, race_id)
        race_results = Path(race.work_dir) / "RESULT"
        race_results.mkdir(parents=True, exist_ok=True)
        (race_results / "late.xyz").write_text("late", encoding="utf-8")
        mgr.tasks.sync_from_job(race)  # type: ignore[union-attr]
        race_cancel_event = threading.Event()
        mgr._cancel_events[race.id] = race_cancel_event
        mgr.runner.cancel_local = lambda job_id: True  # type: ignore[method-assign]

        def cancelling_poll(stale_record: JobRecord) -> tuple[bool, int | None]:
            mgr.cancel(race.id)
            stale_record.exit_code = 0
            return (True, 0)

        mgr.runner.poll = cancelling_poll  # type: ignore[method-assign]
        mgr._poll_job(race.id)

        final_a = mgr.store.get(race.id)
        assert final_a is not None
        assert final_a.status == JobStatus.CANCELLING, "the cancel must win the stale terminal CAS"
        assert _state_registry_paths(mgr, race.id) == [], (
            "rejection: zero artifact rows for the dropped terminal observation"
        )
        result_a = final_a.result or {}
        assert "provenance" not in result_a and not result_a.get("terminal_side_effects_done")
        events_a = _state_event_types(mgr, race.id)
        assert "job.completed" not in events_a
        assert "job.poll_dropped_stale" in events_a and "job.cancelling" in events_a
        task_a = mgr.tasks.get(race.id)
        assert task_a is not None and task_a["status"] == final_a.status.value, (
            "jobs/tasks consistency after a rejected CAS"
        )
        stored_event = mgr._cancel_events.get(race.id)
        assert stored_event is race_cancel_event and stored_event.is_set()

        # ── (b) terminal CAS then immediate rerun → attempt isolation. ──
        rerun_id = "e2e-t10-midflight-rerun"
        rerun_src = _seed_running_job(mgr, tmp_path, rerun_id)
        rerun_results = Path(rerun_src.work_dir) / "RESULT"
        rerun_results.mkdir(parents=True, exist_ok=True)
        (rerun_results / "a.xyz").write_text("a", encoding="utf-8")
        mgr.tasks.sync_from_job(rerun_src)  # type: ignore[union-attr]

        real_transition = mgr.store.transition
        fired = {"n": 0}

        def transitioning(*args: object, **kwargs: object) -> JobRecord:
            out = real_transition(*args, **kwargs)  # type: ignore[arg-type]
            if fired["n"] == 0:
                fired["n"] += 1
                rerun = mgr.rerun_job(rerun_src.id)
                assert rerun is not None and rerun.attempt == 2
            return out

        mgr.store.transition = transitioning  # type: ignore[method-assign]

        def terminal_poll(stale_record: JobRecord) -> tuple[bool, int | None]:
            stale_record.exit_code = 0
            return (True, 0)

        mgr.runner.poll = terminal_poll  # type: ignore[method-assign]
        mgr._poll_job(rerun_src.id)
        mgr.store.transition = real_transition  # type: ignore[method-assign]
        assert fired["n"] == 1

        final_b = mgr.store.get(rerun_src.id)
        assert final_b is not None
        assert final_b.attempt == 2 and final_b.status == JobStatus.QUEUED
        assert _state_registry_paths(mgr, rerun_src.id) == [], (
            "attempt isolation: the superseded attempt registers zero artifact rows"
        )
        result_b = final_b.result or {}
        assert not result_b.get("terminal_side_effects_done")
        assert "provenance" not in result_b, "attempt isolation: no provenance from the old attempt"
        events_b = _state_event_types(mgr, rerun_src.id)
        assert "job.completed" not in events_b, "attempt isolation: no terminal event"
        assert "job.rerun" in events_b
        job_json_b = json.loads((Path(final_b.work_dir) / "job.json").read_text(encoding="utf-8"))
        assert job_json_b["attempt"] == 2 and job_json_b["status"] == JobStatus.QUEUED.value, (
            "attempt isolation: job.json belongs to the new attempt"
        )
        task_b = mgr.tasks.get(rerun_src.id)
        assert task_b is not None and task_b["status"] == JobStatus.QUEUED.value, (
            "attempt isolation: tasks must not carry the old attempt's status"
        )
        assert rerun_src.id in mgr._cancel_events, (
            "attempt isolation: the new attempt's cancel event must survive"
        )

        # ── (c) delayed old RUNNING projection after new PAUSED. ──
        delay_id = "e2e-t10-delay"
        delay = _seed_running_job(mgr, tmp_path, delay_id)
        mgr.tasks.sync_from_job(delay)  # type: ignore[union-attr]
        stale_running = mgr.store.get(delay.id)
        assert stale_running is not None and stale_running.status == JobStatus.RUNNING

        mgr.pause_job(delay.id)
        assert mgr.store.get(delay.id).status == JobStatus.PAUSED  # type: ignore[union-attr]
        task_c = mgr.tasks.get(delay.id)
        assert task_c is not None and task_c["status"] == "paused"
        db = tmp_path / "acp_jobs.db"
        before_c = _raw_row(db, delay.id)

        mgr._sync_task_status(stale_running)

        task_c = mgr.tasks.get(delay.id)
        assert task_c is not None and task_c["status"] == "paused", (
            "delay: a stale RUNNING projection must not overwrite PAUSED"
        )
        assert _raw_row(db, delay.id) == before_c, "delay: projection never writes the jobs row"
        assert mgr.tasks.find_projection_drift() == []  # type: ignore[union-attr],call-args

        # ── (d) sync-failure recovery → bounded convergence. ──
        sync_id = "e2e-t10-sync-fail"
        sync_job = _seed_running_job(mgr, tmp_path, sync_id)
        mgr.tasks.sync_from_job(sync_job)  # type: ignore[union-attr]
        real_sync = mgr.tasks.sync_job_transition  # type: ignore[union-attr]
        sync_fired = {"n": 0}

        def flaky_sync(rec: JobRecord) -> None:
            if sync_fired["n"] == 0:
                sync_fired["n"] += 1
                raise RuntimeError("projection backend down")
            return real_sync(rec)

        mgr.tasks.sync_job_transition = flaky_sync  # type: ignore[method-assign,union-attr]

        mgr.pause_job(sync_job.id)

        assert sync_fired["n"] == 1
        assert mgr.store.get(sync_job.id).status == JobStatus.PAUSED, "jobs is authoritative"
        task_d = mgr.tasks.get(sync_job.id)
        assert task_d is not None and task_d["status"] == "running", "the drift must be visible"

        # Five more drifted rows (jobs exist, tasks missing) for the batch
        # bound: N = 6 drifted rows, B = 2 → ceil(6/2) = 3 scans.
        for i in range(5):
            mgr.store.create(_state_record(f"e2e-t10-drift-{i}"))
        mgr._task_reconcile_batch = 2
        initial_drift = mgr.tasks.find_projection_drift()  # type: ignore[union-attr]
        assert len(initial_drift) == 6, f"expected 6 drifted rows, got {initial_drift}"

        scans = 0
        while mgr.tasks.find_projection_drift():  # type: ignore[union-attr]
            mgr._reconcile_once()
            scans += 1
            assert scans <= 3, "drift did not converge within ceil(N/B) scans"
        assert scans == 3, (
            f"6 drifted rows with batch B=2 must converge in exactly 3 scans: {scans}"
        )

        task_d = mgr.tasks.get(sync_job.id)
        assert task_d is not None and task_d["status"] == "paused", (
            "recovery: the reconcile pass must repair the sync-failure drift"
        )
        for i in range(5):
            row = mgr.tasks.get(f"e2e-t10-drift-{i}")
            assert row is not None and row["status"] == JobStatus.QUEUED.value
        mgr._reconcile_once()
        assert mgr.tasks.find_projection_drift() == []  # type: ignore[union-attr],call-args
        "convergence must be stable across passes"
    finally:
        mgr.shutdown()
