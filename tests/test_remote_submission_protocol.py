"""D02 submit-intent / immediate-persist / result-unknown protocol tests.

Scenarios (i)-(xiii) of plan todo 5 (acp-execution-integrity-remediation):

* intent persisted before bsub, id persisted immediately via callback
* RemoteSubmissionRejected vs RemoteSubmissionIndeterminate classification
* never delete the remote directory on rejection / indeterminate
* reconcile_submission ownership + full 5-evidence not_accepted gate
* submit lease (intent) blocks terminal judgements until the worker dies
* cancel/bsub coordination: exactly one outcome, no duplicate bsub
* orphan cancel persistence with bounded backoff across restarts

Run with: PYTHONPATH=src python3.11 -m pytest tests/test_remote_submission_protocol.py -q
"""

from __future__ import annotations

import json
import posixpath
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from acp.scheduler.events import JobEventLog
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.manager import JobManager
from acp.scheduler.remote import ssh as ssh_mod
from acp.scheduler.remote.config import RemoteExecutionConfig, RemoteNode
from acp.scheduler.remote.runner import (
    RemoteJobRunner,
    RemotePollObservation,
    RemoteSubmissionIndeterminate,
    RemoteSubmissionRejected,
)
from acp.scheduler.remote.sftp import FileStager
from acp.scheduler.remote.ssh import SSHConnectionPool
from acp.scheduler.remote.submission import (
    SUBMIT_WORKERS,
    heartbeat_submit_worker,
    register_submit_worker,
    release_submit_worker,
    submit_lease_valid,
    submission_id_for,
    submission_lsf_name,
)
from acp.scheduler.store import JobStateConflictError

from tests.test_remote_phase2 import FakeSSHClient, FakeSFTP, make_node

try:
    import paramiko  # noqa: F401

    REMOTE_AVAILABLE = True
except ImportError:  # pragma: no cover
    REMOTE_AVAILABLE = False

requires_remote = pytest.mark.skipif(not REMOTE_AVAILABLE, reason="paramiko not installed")


# --------------------------------------------------------------------- #
# Manager-level fake remote runner (scripted verdicts, bsub spy)
# --------------------------------------------------------------------- #


class _ScriptedRunner:
    """Protocol double: counts bsubs, replays reconcile verdicts."""

    def __init__(self, lsf_id: str = "7777", submit_error: Exception | None = None,
                 verdicts: list[str] | None = None) -> None:
        self.lsf_id = lsf_id
        self.submit_error = submit_error
        self.verdicts = list(verdicts or [])
        self.submit_calls = 0
        self.reconcile_calls = 0
        self.recover_calls = 0
        self.cancel_calls: list[str] = []

    def submit_remote(
        self,
        record,
        event_log,
        target_node=None,
        *,
        remote_job_dir=None,
        on_submitted=None,
        submission_id=None,
        on_code_release_bound=None,  # protocol double: no real release to bind
    ) -> str:
        self.submit_calls += 1
        if self.submit_error is not None:
            raise self.submit_error
        if on_submitted is not None:
            on_submitted(self.lsf_id)
        return self.lsf_id

    def reconcile_submission(self, record) -> str:
        self.reconcile_calls += 1
        verdict = self.verdicts.pop(0) if self.verdicts else "unknown"
        if verdict == "found":
            record.remote_job_id = self.lsf_id
        return verdict

    def recover_job_state(self, record) -> bool:
        self.recover_calls += 1
        return bool(record.remote_job_id)

    def poll_remote(self, record, event_log, cancel_event):
        return RemotePollObservation(terminal=False)

    def apply_terminal_side_effects(self, record, event_log, stage_events=()) -> None:
        return None

    def cancel_remote(self, job_id, record=None) -> bool:
        self.cancel_calls.append(job_id)
        return True


class _MonitorSpy:
    """RemoteJobMonitor double: scripted status replies + kill recorder."""

    def __init__(self, statuses: list[str], kill_ok: bool = True) -> None:
        self.statuses = list(statuses)
        self.kill_ok = kill_ok
        self.gets = 0
        self.kills: list[str] = []
        self.raise_on_get = False

    def get_lsf_status(self, node, lsf_job_id: str) -> str:
        self.gets += 1
        if self.raise_on_get:
            raise ConnectionError("node unreachable")
        if not self.statuses:
            return "unknown"
        if len(self.statuses) == 1:
            return self.statuses[0]
        return self.statuses.pop(0)

    def cancel_job(self, node, lsf_job_id: str) -> bool:
        self.kills.append(lsf_job_id)
        return self.kill_ok


def _confsearch_spec() -> JobSpec:
    return JobSpec(workflow="Confsearch", method={"protocol": "xtb-crest"})


def _remote_node(name: str = "compute-01") -> RemoteNode:
    from acp.scheduler.remote.config import NodeCapabilities

    node = RemoteNode(
        name=name,
        host=f"{name}.example.com",
        username="qc",
        remote_work_dir="/scratch/qc/acp",
        remote_code_dir="/home/qc/acp_code",
        max_concurrent_jobs=4,
        enabled=True,
    )
    node.capabilities = NodeCapabilities(software=("xtb", "crest"))
    return node


def _make_manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> JobManager:
    monkeypatch.setattr("acp.scheduler.manager.local_satisfies", lambda required: False)
    cfg = RemoteExecutionConfig(execution_mode="local", nodes=[_remote_node()])
    mgr = JobManager(run_root=tmp_path, remote_config=cfg)
    mgr.registry.status_provider = lambda name: {
        "name": name,
        "running": 0,
        "status": "online",
    }
    return mgr


def _seed_queued(mgr: JobManager, tmp_path: Path, job_id: str) -> JobRecord:
    work_dir = tmp_path / "runs" / job_id
    work_dir.mkdir(parents=True, exist_ok=True)
    record = JobRecord(
        id=job_id,
        spec=_confsearch_spec(),
        status=JobStatus.QUEUED,
        work_dir=str(work_dir),
    )
    mgr.store.create(record)
    return record


def _events(mgr: JobManager, job_id: str) -> list[dict]:
    record = mgr.store.get(job_id)
    assert record is not None
    return JobEventLog(Path(record.work_dir) / "events.jsonl").read_all()


def _event_types(mgr: JobManager, job_id: str) -> list[str]:
    return [e.get("type") for e in _events(mgr, job_id)]


def _remote_meta(mgr: JobManager, job_id: str) -> dict:
    record = mgr.store.get(job_id)
    assert record is not None
    result = record.result or {}
    meta = result.get("remote")
    return dict(meta) if isinstance(meta, dict) else {}


def _future_iso(seconds: int = 600) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _past_iso(seconds: int = 600) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


# --------------------------------------------------------------------- #
# Runner-level harness (phase-2 fakes + real RemoteJobRunner)
# --------------------------------------------------------------------- #


class _RunnerHarness:
    """Real RemoteJobRunner over FakeSSH/FakeSFTP with a bsub counter."""

    def __init__(self, tmp: str, *, bsub_result=None, bsub_raises: Exception | None = None):
        self.node = make_node()
        self.config = RemoteExecutionConfig(
            execution_mode="remote", auto_sync=False, nodes=[self.node]
        )
        self.pool = SSHConnectionPool()
        self.sftp = FakeSFTP()
        self.client = FakeSSHClient(self.sftp)
        self.bsub_calls = 0
        self.removes: list[str] = []
        self.bsub_result = bsub_result or (0, "Job <54321> is submitted to queue <normal>.\n", "")
        self.bsub_raises = bsub_raises
        self.query_handler = None  # callable(cmd) -> (code, out, err) | None

        def cmd_handler(cmd):
            if "bsub" in cmd and "<" in cmd:
                self.bsub_calls += 1
                if self.bsub_raises is not None:
                    raise self.bsub_raises
                return self.bsub_result
            if self.query_handler is not None:
                result = self.query_handler(cmd)
                if result is not None:
                    return result
            return (0, "", "")

        self.client.cmd_handler = cmd_handler
        self.runner = RemoteJobRunner(
            self.pool,
            self.config,
            stager=FileStager(self.pool),
            poll_interval=0,
        )
        original_remove = self.runner._stager.remove_remote_dir

        def spy_remove(n, path):
            self.removes.append(str(path))
            return original_remove(n, path)

        self.runner._stager.remove_remote_dir = spy_remove  # type: ignore[assignment]
        self._tmp = tmp

    def make_record(
        self,
        job_id: str,
        *,
        with_intent: bool = True,
        lease_expires_at: str | None = None,
    ) -> tuple[JobRecord, JobEventLog, str]:
        work_dir = Path(self._tmp) / "runs" / "projA" / job_id
        work_dir.mkdir(parents=True, exist_ok=True)
        rel = f"projA/{job_id}"
        remote_dir = posixpath.join(self.node.remote_work_dir, rel)
        sid = submission_id_for(job_id, 1)
        result: dict = {"remote_dir": remote_dir}
        if with_intent:
            result["remote"] = {
                "schema": 1,
                "relative": rel,
                "attempt": 1,
                "node": self.node.name,
                "submission_id": sid,
                "submit_state": "intent",
                "lease_expires_at": lease_expires_at or _past_iso(),
            }
        spec = JobSpec(
            workflow="Confsearch",
            input={"source": "CCO", "source_type": "smiles"},
            method={"protocol": "xtb-crest"},
        )
        record = JobRecord(id=job_id, spec=spec, work_dir=str(work_dir), result=result)
        return record, JobEventLog(work_dir / "events.jsonl"), remote_dir

    def reconcile(self, record: JobRecord) -> str:
        with patch.object(
            ssh_mod, "_create_client", side_effect=lambda n, timeout=30: self.client
        ):
            return self.runner.reconcile_submission(record)

    def close(self) -> None:
        self.pool.close()


# --------------------------------------------------------------------- #
# (i) bsub accepted, reply lost -> unconfirmed, adopted, no 2nd bsub
# --------------------------------------------------------------------- #


@requires_remote
def test_reply_lost_keeps_dir_unconfirmed_and_adopts_without_second_bsub(tmp_path):
    harness = _RunnerHarness(str(tmp_path), bsub_result=(0, "garbage without job id\n", ""))
    record, event_log, remote_dir = harness.make_record("lostreply")
    sid = submission_id_for("lostreply", 1)

    with patch.object(ssh_mod, "_create_client", side_effect=lambda n, timeout=30: harness.client):
        with pytest.raises(RemoteSubmissionIndeterminate):
            harness.runner.submit_remote(record, event_log)

    # Directory kept, no cleanup, unconfirmed projected + event written.
    assert harness.removes == []
    assert posixpath.join(remote_dir, "submit.lsf") in harness.sftp.files
    assert posixpath.join(remote_dir, "job.json") in harness.sftp.files
    meta = (record.result or {}).get("remote") or {}
    assert meta.get("submit_state") == "unconfirmed"
    assert "remote.submit_unconfirmed" in [e["type"] for e in event_log.read_all()]

    # Reconcile by submission name hits the single matching job.
    name = submission_lsf_name(sid)

    def queries(cmd):
        if "bjobs" in cmd and name in cmd:
            return (
                0,
                "JOBID   USER    STAT  QUEUE      JOBNAME          SUBMIT\n"
                "54321   qc      RUN   normal    " + name + "   Thu Oct  1\n",
                "",
            )
        return None

    harness.query_handler = queries
    verdict = harness.reconcile(record)
    assert verdict == "found"
    assert record.remote_job_id == "54321"
    assert harness.bsub_calls == 1, "reconcile must never re-run bsub"
    assert harness.removes == []
    harness.close()


# --------------------------------------------------------------------- #
# (ii) on_submitted persistence throws -> re-read convergence, no 2nd bsub
# --------------------------------------------------------------------- #


def test_on_submitted_persist_failure_converges_without_second_bsub(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    mgr = _make_manager(tmp_path, monkeypatch)
    try:
        runner = _ScriptedRunner(lsf_id="4242")
        mgr.remote_runner = runner  # type: ignore[assignment]
        _seed_queued(mgr, tmp_path, "ii-callback")
        job_id = "ii-callback"

        original_transition = mgr.store.transition
        state = {"failed_once": False}

        def flaky_transition(jid, **kwargs):
            if "remote_job_id" in kwargs and not state["failed_once"]:
                state["failed_once"] = True
                raise JobStateConflictError(jid, {"revision": -1}, None)
            return original_transition(jid, **kwargs)

        monkeypatch.setattr(mgr.store, "transition", flaky_transition)
        assert mgr._submit_job(job_id) is True
        monkeypatch.setattr(mgr.store, "transition", original_transition)

        stored = mgr.store.get(job_id)
        assert stored is not None
        assert stored.status == JobStatus.PENDING
        assert stored.remote_job_id == "4242"
        assert _remote_meta(mgr, job_id).get("submit_state") == "submitted"
        assert runner.submit_calls == 1, "never bsub twice for one attempt"
        assert state["failed_once"] is True, "the CAS conflict must have fired"
    finally:
        mgr.shutdown()


@requires_remote
def test_on_submitted_exception_does_not_roll_back_submission(tmp_path):
    """Runner side: a throwing callback never fails an accepted submission."""
    harness = _RunnerHarness(str(tmp_path))
    record, event_log, _remote_dir = harness.make_record("cbthrow")
    seen: list[str] = []

    def exploding(lsf_job_id: str) -> None:
        seen.append(lsf_job_id)
        raise RuntimeError("db write failed")

    with patch.object(ssh_mod, "_create_client", side_effect=lambda n, timeout=30: harness.client):
        lsf_id = harness.runner.submit_remote(
            record, event_log, on_submitted=exploding
        )

    assert lsf_id == "54321"
    assert seen == ["54321"]
    assert harness.bsub_calls == 1
    assert harness.removes == []
    assert "remote.submit_id_persist_failed" in [e["type"] for e in event_log.read_all()]
    harness.close()


# --------------------------------------------------------------------- #
# (iii) remote.submitted event write failure -> no rollback
# --------------------------------------------------------------------- #


@requires_remote
def test_submitted_event_write_failure_does_not_roll_back(tmp_path):
    harness = _RunnerHarness(str(tmp_path))
    record, event_log, _remote_dir = harness.make_record("evfail")
    seen: list[str] = []
    original_append = event_log.append

    def failing_append(event_type, **kwargs):
        if event_type == "remote.submitted":
            raise OSError("disk full writing events.jsonl")
        return original_append(event_type, **kwargs)

    event_log.append = failing_append  # type: ignore[assignment]
    with patch.object(ssh_mod, "_create_client", side_effect=lambda n, timeout=30: harness.client):
        lsf_id = harness.runner.submit_remote(
            record, event_log, on_submitted=seen.append
        )

    assert lsf_id == "54321"
    assert seen == ["54321"], "id persistence must run even when the event fails"
    assert harness.removes == []
    assert harness.bsub_calls == 1
    harness.close()


# --------------------------------------------------------------------- #
# (iv) multiple matches -> unknown (stays pending)
# --------------------------------------------------------------------- #


@requires_remote
def test_multiple_name_matches_return_unknown(tmp_path):
    harness = _RunnerHarness(str(tmp_path))
    record, event_log, _remote_dir = harness.make_record("multimatch")
    sid = submission_id_for("multimatch", 1)
    name = submission_lsf_name(sid)

    def queries(cmd):
        if "bjobs" in cmd and name in cmd:
            return (
                0,
                "JOBID   USER    STAT  QUEUE      JOBNAME          SUBMIT\n"
                "54321   qc      RUN   normal    " + name + "   Thu\n"
                "54322   qc      PEND  normal    " + name + "   Thu\n",
                "",
            )
        return None

    harness.query_handler = queries
    verdict = harness.reconcile(record)
    assert verdict == "unknown"
    assert record.remote_job_id is None
    harness.close()


# --------------------------------------------------------------------- #
# (v) not_accepted only with the full 5-evidence set
# --------------------------------------------------------------------- #


def _reconcile_with_query(harness: _RunnerHarness, record: JobRecord, query_output) -> str:
    def queries(cmd):
        if "bjobs" in cmd:
            return query_output
        return None

    harness.query_handler = queries
    return harness.reconcile(record)


@requires_remote
def test_not_accepted_requires_full_evidence_set(tmp_path):
    explicit_not_found = (0, "Job <acp_x> is not found.\n", "")
    cases = []

    # A: full evidence (expired lease + explicit not-found + empty dir) -> accepted
    harness = _RunnerHarness(str(tmp_path))
    record, _ev, _rd = harness.make_record("na-full", lease_expires_at=_past_iso())
    verdict = _reconcile_with_query(harness, record, explicit_not_found)
    cases.append(("full", verdict))
    assert verdict == "not_accepted"
    assert harness.removes == []
    harness.close()

    # B: STATUS_UNKNOWN only -> unknown
    harness = _RunnerHarness(str(tmp_path))
    record, _ev, _rd = harness.make_record("na-unknown")
    verdict = _reconcile_with_query(harness, record, (0, "Unable to query job status\n", ""))
    cases.append(("unknown", verdict))
    assert verdict == "unknown"
    harness.close()

    # C: empty output (monitor maps it to STATUS_NOT_FOUND) -> unknown
    harness = _RunnerHarness(str(tmp_path))
    record, _ev, _rd = harness.make_record("na-empty", lease_expires_at=_past_iso())
    verdict = _reconcile_with_query(harness, record, (0, "", ""))
    cases.append(("empty", verdict))
    assert verdict == "unknown"
    harness.close()

    # D: explicit not-found but one exclusion evidence present (state.json) -> unknown
    harness = _RunnerHarness(str(tmp_path))
    record, _ev, remote_dir = harness.make_record("na-state", lease_expires_at=_past_iso())
    harness.sftp.dirs.add(remote_dir)
    harness.sftp.files[posixpath.join(remote_dir, "state.json")] = b'{"attempt": 1}'
    verdict = _reconcile_with_query(harness, record, explicit_not_found)
    cases.append(("state-present", verdict))
    assert verdict == "unknown"
    harness.close()

    # E: explicit not-found but a VALID submit lease -> unknown (live owner)
    harness = _RunnerHarness(str(tmp_path))
    record, _ev, _rd = harness.make_record("na-lease", lease_expires_at=_future_iso())
    verdict = _reconcile_with_query(harness, record, explicit_not_found)
    cases.append(("lease-valid", verdict))
    assert verdict == "unknown"
    harness.close()

    # F: SSH read failure during evidence probe -> unknown
    harness = _RunnerHarness(str(tmp_path))
    record, _ev, remote_dir = harness.make_record("na-ssh", lease_expires_at=_past_iso())
    harness.sftp.dirs.add(remote_dir)

    def boom(node, path, *args, **kwargs):
        raise ConnectionError("sftp down")

    harness.runner._stager.list_remote_dir = boom  # type: ignore[assignment]
    verdict = _reconcile_with_query(harness, record, explicit_not_found)
    cases.append(("sftp-fail", verdict))
    assert verdict == "unknown"
    harness.close()

    assert cases[0][1] == "not_accepted" and all(v == "unknown" for _, v in cases[1:])


# --------------------------------------------------------------------- #
# (vi) indeterminate converges inside continuous reconcile, no restart
# --------------------------------------------------------------------- #


def test_indeterminate_converges_in_continuous_polls_without_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    mgr = _make_manager(tmp_path, monkeypatch)
    try:
        runner = _ScriptedRunner(lsf_id="9090", verdicts=["unknown", "found"])
        mgr.remote_runner = runner  # type: ignore[assignment]
        _seed_queued(mgr, tmp_path, "vi-pending")
        job_id = "vi-pending"

        # Force an indeterminate submission (start with an intent row).
        original_submit = runner.submit_remote

        def indeterminate_submit(record, event_log, target_node=None, **kwargs):
            runner.submit_calls += 1
            raise RemoteSubmissionIndeterminate("reply lost")

        runner.submit_remote = indeterminate_submit  # type: ignore[assignment]
        assert mgr._submit_job(job_id) is True

        stored = mgr.store.get(job_id)
        assert stored.status == JobStatus.STARTING
        assert _remote_meta(mgr, job_id).get("submit_state") == "unconfirmed"
        assert "remote.submit_unconfirmed" in [e.get("type") for e in _events(mgr, job_id)]

        # Poll cycle 1: still unknown -> stays STARTING (never FAILED).
        mgr._reconcile_once()
        stored = mgr.store.get(job_id)
        assert stored.status == JobStatus.STARTING
        assert stored.remote_job_id is None

        # Poll cycle 2 on the SAME manager (no restart): adopted.
        mgr._reconcile_once()
        stored = mgr.store.get(job_id)
        assert stored.status == JobStatus.PENDING
        assert stored.remote_job_id == "9090"
        assert _remote_meta(mgr, job_id).get("submit_state") == "submitted"
        assert "remote.submit_reconciled" in [e.get("type") for e in _events(mgr, job_id)]
        assert runner.submit_calls == 1
        assert runner.reconcile_calls == 2
        runner.submit_remote = original_submit  # type: ignore[assignment]
    finally:
        mgr.shutdown()


# --------------------------------------------------------------------- #
# (vii) cancel before the bsub reply -> id backfill -> bkill -> CANCELLED
# --------------------------------------------------------------------- #


def test_cancel_before_bsub_reply_adopts_then_kills_then_cancels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    mgr = _make_manager(tmp_path, monkeypatch)
    try:
        runner = _ScriptedRunner(lsf_id="5151", verdicts=["found"])
        mgr.remote_runner = runner  # type: ignore[assignment]
        _seed_queued(mgr, tmp_path, "vii-cancel")
        job_id = "vii-cancel"

        # Submission becomes indeterminate, user cancels before the reply.
        def indeterminate(record, event_log, target_node=None, **kwargs):
            runner.submit_calls += 1
            raise RemoteSubmissionIndeterminate("reply lost")

        runner.submit_remote = indeterminate  # type: ignore[assignment]
        assert mgr._submit_job(job_id) is True
        cancelled = mgr.cancel(job_id)
        assert cancelled is not None and cancelled.status == JobStatus.CANCELLING
        assert _remote_meta(mgr, job_id).get("cancel_state") == "requested"
        assert _remote_meta(mgr, job_id).get("submit_state") == "unconfirmed"

        monitor = _MonitorSpy(["running", "not_found"], kill_ok=True)
        mgr._remote_monitor = monitor  # type: ignore[assignment]

        # Contract A sequence: find -> backfill id -> bkill -> confirm -> CANCELLED.
        mgr._poll_job(job_id)
        stored = mgr.store.get(job_id)
        assert stored.remote_job_id == "5151"
        assert stored.status == JobStatus.CANCELLED
        assert _remote_meta(mgr, job_id).get("cancel_state") == "confirmed"
        assert monitor.kills == ["5151"], "bkill must run after the id is persisted"
        assert monitor.gets >= 2, "confirmation requires a bjobs check"
        assert "remote.cancel_confirmed" in [e.get("type") for e in _events(mgr, job_id)]
        assert runner.submit_calls == 1
    finally:
        mgr.shutdown()


def test_cancel_confirmation_needs_positive_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """STATUS_UNKNOWN keeps CANCELLING — CANCELLED needs confirmation."""
    mgr = _make_manager(tmp_path, monkeypatch)
    try:
        runner = _ScriptedRunner(lsf_id="5252")
        mgr.remote_runner = runner  # type: ignore[assignment]
        _seed_queued(mgr, tmp_path, "vii-unknown")
        job_id = "vii-unknown"
        assert mgr._submit_job(job_id) is True
        mgr.cancel(job_id)

        monitor = _MonitorSpy(["unknown"], kill_ok=False)
        mgr._remote_monitor = monitor  # type: ignore[assignment]
        mgr._reconcile_once()

        stored = mgr.store.get(job_id)
        assert stored.status == JobStatus.CANCELLING
        assert _remote_meta(mgr, job_id).get("cancel_state") in ("unconfirmed", "sent")
    finally:
        mgr.shutdown()


# --------------------------------------------------------------------- #
# (viii) explicit rejection -> FAILED only, directory (reused) intact
# --------------------------------------------------------------------- #


def test_explicit_rejection_fails_job_without_touching_reused_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    mgr = _make_manager(tmp_path, monkeypatch)
    try:
        runner = _ScriptedRunner(
            submit_error=RemoteSubmissionRejected("bsub: Queue <gpu> does not exist")
        )
        mgr.remote_runner = runner  # type: ignore[assignment]
        _seed_queued(mgr, tmp_path, "viii-reject")
        job_id = "viii-reject"

        assert mgr._submit_job(job_id) is True
        stored = mgr.store.get(job_id)
        assert stored.status == JobStatus.FAILED
        assert "remote_submit_not_accepted" in (stored.error or "")
        assert _remote_meta(mgr, job_id).get("submit_state") == "not_accepted"
        assert runner.submit_calls == 1
    finally:
        mgr.shutdown()


@requires_remote
def test_rejected_submission_keeps_reused_dir_science_intact(tmp_path):
    """Runner side: rejection never deletes a reused dir's checkpoint/RESULT."""
    harness = _RunnerHarness(str(tmp_path), bsub_result=(1, "", "Queue does not exist\n"))
    record, event_log, remote_dir = harness.make_record("viii-reused")
    harness.sftp.dirs.add(remote_dir)
    harness.sftp.files[posixpath.join(remote_dir, "job.json")] = json.dumps(
        {"id": "viii-reused", "attempt": 1}
    ).encode()
    harness.sftp.files[posixpath.join(remote_dir, "checkpoint.json")] = b'{"step": 7}'
    harness.sftp.files[posixpath.join(remote_dir, "RESULT", "result_manifest.json")] = b"{}"
    harness.sftp.dirs.add(posixpath.join(remote_dir, "RESULT"))
    before = dict(harness.sftp.files)

    with patch.object(ssh_mod, "_create_client", side_effect=lambda n, timeout=30: harness.client):
        with pytest.raises(RemoteSubmissionRejected):
            harness.runner.submit_remote(record, event_log)

    assert harness.removes == []
    # Science artifacts stay byte-identical (markers/submit.lsf are the
    # submission-time snapshot, rewritten on every submit by design).
    science = [
        posixpath.join(remote_dir, "checkpoint.json"),
        posixpath.join(remote_dir, "RESULT", "result_manifest.json"),
    ]
    for key in science:
        assert harness.sftp.files.get(key) == before[key], f"{key} must be byte-identical"
    assert posixpath.join(remote_dir, "checkpoint.json") in harness.sftp.files
    assert posixpath.join(remote_dir, "RESULT", "result_manifest.json") in harness.sftp.files
    harness.close()


@requires_remote
def test_ssh_failure_keeps_submission_unconfirmed_without_cleanup(tmp_path):
    harness = _RunnerHarness(str(tmp_path), bsub_raises=TimeoutError("ssh read timeout"))
    record, event_log, remote_dir = harness.make_record("viii-ssh")

    with patch.object(ssh_mod, "_create_client", side_effect=lambda n, timeout=30: harness.client):
        with pytest.raises(RemoteSubmissionIndeterminate):
            harness.runner.submit_remote(record, event_log)

    assert harness.removes == []
    assert posixpath.join(remote_dir, "submit.lsf") in harness.sftp.files
    meta = (record.result or {}).get("remote") or {}
    assert meta.get("submit_state") == "unconfirmed"
    assert "remote.submit_unconfirmed" in [e["type"] for e in event_log.read_all()]
    harness.close()


def test_pending_submission_survives_polls_and_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    mgr = _make_manager(tmp_path, monkeypatch)
    job_id = "viii-restart"
    try:
        runner = _ScriptedRunner(verdicts=["unknown"])
        mgr.remote_runner = runner  # type: ignore[assignment]
        _seed_queued(mgr, tmp_path, job_id)

        def indeterminate(record, event_log, target_node=None, **kwargs):
            runner.submit_calls += 1
            raise RemoteSubmissionIndeterminate("reply lost")

        runner.submit_remote = indeterminate  # type: ignore[assignment]
        assert mgr._submit_job(job_id) is True

        # Poll cycles: STARTING + unconfirmed never becomes FAILED.
        mgr._reconcile_once()
        mgr._reconcile_once()
        stored = mgr.store.get(job_id)
        assert stored.status == JobStatus.STARTING
        assert _remote_meta(mgr, job_id).get("submit_state") == "unconfirmed"
        assert runner.submit_calls == 1
        mgr.shutdown()

        # Restart: startup reconcile must not finalise the row either.
        mgr2 = _make_manager(tmp_path, monkeypatch)
        try:
            runner2 = _ScriptedRunner(verdicts=["unknown"])
            mgr2.remote_runner = runner2  # type: ignore[assignment]
            mgr2._requeue_active_on_startup()
            stored = mgr2.store.get(job_id)
            assert stored.status == JobStatus.STARTING
            assert _remote_meta(mgr2, job_id).get("submit_state") == "unconfirmed"
            assert runner2.submit_calls == 0, "restart must never resubmit"
        finally:
            mgr2.shutdown()
    except Exception:
        try:
            mgr.shutdown()
        except Exception:
            pass
        raise


def test_startup_reconcile_adopts_before_recover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Startup runs reconcile FIRST; an adopted id reaches recover (no restart-fail)."""
    mgr = _make_manager(tmp_path, monkeypatch)
    job_id = "startup-adopt"
    try:
        runner = _ScriptedRunner(lsf_id="6060")
        mgr.remote_runner = runner  # type: ignore[assignment]
        _seed_queued(mgr, tmp_path, job_id)

        def indeterminate(record, event_log, target_node=None, **kwargs):
            runner.submit_calls += 1
            raise RemoteSubmissionIndeterminate("reply lost")

        runner.submit_remote = indeterminate  # type: ignore[assignment]
        assert mgr._submit_job(job_id) is True

        runner.verdicts = ["found"]
        mgr._requeue_active_on_startup()

        stored = mgr.store.get(job_id)
        assert stored.status == JobStatus.PENDING
        assert stored.remote_job_id == "6060"
        assert runner.recover_calls >= 1, "adoption must feed recover_job_state"
        assert runner.submit_calls == 1, "startup never resubmits"
    finally:
        mgr.shutdown()


def test_detail_recovery_projects_submit_state(tmp_path: Path):
    from acp.api.v1_routes import _compute_recovery
    from acp.api.v1_schemas import JobDiskState

    record = JobRecord(
        id="projection",
        spec=_confsearch_spec(),
        status=JobStatus.STARTING,
        work_dir=str(tmp_path),
        result={"remote": {"submit_state": "unconfirmed", "cancel_state": "requested"}},
    )
    recovery = _compute_recovery(record, JobDiskState())
    assert recovery.submit_state == "unconfirmed"
    assert recovery.cancel_state == "requested"
    assert recovery.reconcile_action == "reconcile_submission_pending"
    # No new JobStatus: the enum is untouched.
    assert {s.value for s in JobStatus} >= {"starting", "pending", "cancelling"}


# --------------------------------------------------------------------- #
# (ix) submit lease: live owner blocks not_accepted; worker death converges
# --------------------------------------------------------------------- #


@requires_remote
def test_valid_lease_blocks_not_accepted_across_reconciles(tmp_path):
    harness = _RunnerHarness(str(tmp_path))
    record, _ev, _rd = harness.make_record("ix-lease", lease_expires_at=_future_iso())

    def not_found(cmd):
        if "bjobs" in cmd:
            return (0, "Job <acp_x> is not found.\n", "")
        return None

    harness.query_handler = not_found
    for _ in range(3):
        assert harness.reconcile(record) == "unknown"
    assert record.remote_job_id is None
    harness.close()


@requires_remote
def test_submit_worker_death_converges_without_service_restart(tmp_path):
    harness = _RunnerHarness(str(tmp_path))
    record, _ev, remote_dir = harness.make_record("ix-death", lease_expires_at=_future_iso())
    sid = submission_id_for("ix-death", 1)
    harness.sftp.dirs.add(remote_dir)  # dir exists from the earlier submit
    try:
        def not_found(cmd):
            if "bjobs" in cmd:
                return (0, "No unfinished jobs found.\n", "")
            return None

        harness.query_handler = not_found

        # Worker registered + heartbeat fresh -> lease held -> unknown.
        register_submit_worker(sid, "pid:1:2:1", 120)
        assert harness.reconcile(record) == "unknown"

        # Worker dies (explicit release, service keeps running) -> evidence rules.
        release_submit_worker(sid)
        assert harness.reconcile(record) == "not_accepted"
        assert harness.removes == [], "not_accepted never deletes the directory"
        assert remote_dir in harness.sftp.dirs, "not_accepted must never delete the directory"
    finally:
        SUBMIT_WORKERS.pop(sid, None)
        harness.close()


# --------------------------------------------------------------------- #
# (ix-b) F2: a live submit worker is never judged not_accepted
# --------------------------------------------------------------------- #


@requires_remote
def test_live_worker_outranks_stale_heartbeat_and_expired_lease(tmp_path):
    """F2 B2: live submit thread keeps the lease valid past both windows."""
    harness = _RunnerHarness(str(tmp_path))
    record, _ev, remote_dir = harness.make_record("ix-live", lease_expires_at=_past_iso())
    sid = submission_id_for("ix-live", 1)
    harness.sftp.dirs.add(remote_dir)  # dir exists from the earlier submit
    try:

        def not_found(cmd):
            if "bjobs" in cmd:
                return (0, "Job <acp_x> is not found.\n", "")
            return None

        harness.query_handler = not_found

        worker = register_submit_worker(sid, "pid:1:2:1", 120)
        # Heartbeat far older than ttl*1.5: a long pre-bsub phase (house-
        # keeping, binary probe, whole-tree release upload) outlived the
        # heartbeat window while the submitting thread is still alive here.
        worker.heartbeat_at = _past_iso(600)

        assert submit_lease_valid(record.result["remote"], ttl_seconds=120) is True
        assert harness.reconcile(record) == "unknown"
        assert harness.removes == []
        assert remote_dir in harness.sftp.dirs
    finally:
        SUBMIT_WORKERS.pop(sid, None)
        harness.close()


def test_heartbeat_refresh_restores_lease_for_worker_without_thread_handle():
    """Heartbeat renewal re-arms liveness; release stays dead regardless."""
    sid = "sub_hb_refresh_unit"
    try:
        worker = register_submit_worker(sid, "pid:1:2:1", 120)
        worker.thread_ident = None  # heartbeat is the only liveness evidence
        worker.heartbeat_at = _past_iso(600)
        meta = {"submission_id": sid, "lease_expires_at": _past_iso(600)}

        assert submit_lease_valid(meta, ttl_seconds=120) is False

        heartbeat_submit_worker(sid)
        assert submit_lease_valid(meta, ttl_seconds=120) is True

        # An explicit release always wins, even over a fresh heartbeat.
        release_submit_worker(sid)
        heartbeat_submit_worker(sid)
        assert submit_lease_valid(meta, ttl_seconds=120) is False
    finally:
        SUBMIT_WORKERS.pop(sid, None)


def test_submission_jobs_guard_keeps_starting_row_owned_by_submit_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """F2 B2: reconcile skips a STARTING row its submit thread still owns."""
    mgr = _make_manager(tmp_path, monkeypatch)
    job_id = "guard-active-submit"
    try:
        runner = _ScriptedRunner(verdicts=["not_accepted"])
        mgr.remote_runner = runner  # type: ignore[assignment]
        # Ownership registered BEFORE the row exists so the background
        # reconcile loop can never observe it unguarded.
        mgr._submission_jobs.add(job_id)
        work_dir = tmp_path / "runs" / job_id
        work_dir.mkdir(parents=True, exist_ok=True)
        record = JobRecord(
            id=job_id,
            spec=_confsearch_spec(),
            status=JobStatus.STARTING,
            work_dir=str(work_dir),
            result={
                "remote": {
                    "submit_state": "intent",
                    "submission_id": submission_id_for(job_id, 1),
                }
            },
        )
        mgr.store.create(record)

        mgr._reconcile_once()

        stored = mgr.store.get(job_id)
        assert stored is not None
        assert stored.status == JobStatus.STARTING
        assert stored.status != JobStatus.FAILED
        assert runner.reconcile_calls == 0, "guard must skip the row entirely"
        assert _remote_meta(mgr, job_id).get("submit_state") == "intent"
    finally:
        mgr._submission_jobs.discard(job_id)
        mgr.shutdown()


# --------------------------------------------------------------------- #
# (x) cancel/bsub coordination — exactly one outcome, no duplicate bsub
# --------------------------------------------------------------------- #


def test_cancel_before_checkpoint_aborts_without_bsub(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    mgr = _make_manager(tmp_path, monkeypatch)
    try:
        runner = _ScriptedRunner()
        mgr.remote_runner = runner  # type: ignore[assignment]
        _seed_queued(mgr, tmp_path, "x-abort")
        job_id = "x-abort"

        original_intent = mgr._persist_submit_intent

        def intent_then_cancel(record, node_name):
            persisted = original_intent(record, node_name)
            if persisted is not None:
                mgr.cancel(job_id)  # cancel lands between intent and bsub
            return persisted

        monkeypatch.setattr(mgr, "_persist_submit_intent", intent_then_cancel)
        assert mgr._submit_job(job_id) is True

        assert runner.submit_calls == 0, "bsub must never run after a cancel request"
        stored = mgr.store.get(job_id)
        assert stored.status == JobStatus.CANCELLING
        meta = _remote_meta(mgr, job_id)
        assert meta.get("submit_state") == "aborted_before_bsub"
        assert meta.get("aborted_at")

        # Cancel chain confirms CANCELLED from the persisted marker alone
        # (no LSF disappearance evidence required).
        monitor = _MonitorSpy(["running"])
        mgr._remote_monitor = monitor  # type: ignore[assignment]
        mgr._reconcile_once()
        stored = mgr.store.get(job_id)
        assert stored.status == JobStatus.CANCELLED
        assert monitor.gets == 0, "aborted_before_bsub needs no LSF query"
        confirm_events = [
            e for e in _events(mgr, job_id) if e.get("type") == "remote.cancel_confirmed"
        ]
        assert confirm_events and confirm_events[0].get("evidence") == "aborted_before_bsub"
    finally:
        mgr.shutdown()


def test_barrier_cancel_race_has_exactly_one_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Cancel transition between intent and bsub: aborted XOR submitted+id."""
    mgr = _make_manager(tmp_path, monkeypatch)
    try:
        runner = _ScriptedRunner(lsf_id="6161")
        mgr.remote_runner = runner  # type: ignore[assignment]
        _seed_queued(mgr, tmp_path, "x-race")
        job_id = "x-race"
        barrier = threading.Barrier(2, timeout=10)
        original_ckpt = mgr._submit_checkpoint

        def racing_checkpoint(jid: str) -> bool:
            ok = original_ckpt(jid)
            if ok:
                # Cancel transition lands AFTER the checkpoint (bsub window).
                barrier.wait()
            return ok

        monkeypatch.setattr(mgr, "_submit_checkpoint", racing_checkpoint)

        def canceller() -> None:
            barrier.wait()
            mgr.cancel(job_id)

        thread = threading.Thread(target=canceller, daemon=True)
        thread.start()
        assert mgr._submit_job(job_id) is True
        thread.join(timeout=10)

        stored = mgr.store.get(job_id)
        meta = _remote_meta(mgr, job_id)
        aborted = meta.get("submit_state") == "aborted_before_bsub"
        submitted = stored.remote_job_id == "6161" and meta.get("submit_state") == "submitted"
        assert aborted != submitted, "exactly one outcome must hold"
        assert runner.submit_calls <= 1, "never bsub twice for one attempt"
        if submitted:
            assert stored.status == JobStatus.CANCELLING, "id-only, status untouched"
    finally:
        mgr.shutdown()


def test_cancel_after_bsub_persists_id_before_kill_then_cancelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    mgr = _make_manager(tmp_path, monkeypatch)
    try:
        runner = _ScriptedRunner(lsf_id="7171")
        mgr.remote_runner = runner  # type: ignore[assignment]
        _seed_queued(mgr, tmp_path, "x-post")
        job_id = "x-post"
        assert mgr._submit_job(job_id) is True
        stored = mgr.store.get(job_id)
        assert stored.status == JobStatus.PENDING
        assert stored.remote_job_id == "7171"

        mgr.cancel(job_id)
        stored = mgr.store.get(job_id)
        assert stored.status == JobStatus.CANCELLING
        assert stored.remote_job_id == "7171", "id must survive the cancel"

        monitor = _MonitorSpy(["running", "not_found"], kill_ok=True)
        mgr._remote_monitor = monitor  # type: ignore[assignment]
        mgr._reconcile_once()
        stored = mgr.store.get(job_id)
        assert stored.status == JobStatus.CANCELLED
        assert monitor.kills == ["7171"]
        assert _remote_meta(mgr, job_id).get("cancel_state") == "confirmed"
        assert runner.submit_calls == 1
    finally:
        mgr.shutdown()


# --------------------------------------------------------------------- #
# (xi)-(xiii) orphan cancel persistence
# --------------------------------------------------------------------- #


def _seed_terminal_with_remote_meta(
    mgr: JobManager, tmp_path: Path, job_id: str, **remote_extra
) -> JobRecord:
    work_dir = tmp_path / "runs" / job_id
    work_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "schema": 1,
        "relative": f"projA/{job_id}",
        "attempt": 1,
        "node": "compute-01",
        "submission_id": submission_id_for(job_id, 1),
        "lsf_job_id": "777",
        **remote_extra,
    }
    record = JobRecord(
        id=job_id,
        spec=_confsearch_spec(),
        status=JobStatus.FAILED,
        work_dir=str(work_dir),
        result={"remote": meta, "terminal_side_effects_done": True},
        error="remote_submit_not_accepted: rejected",
    )
    mgr.store.create(record)
    return record


def test_on_submitted_terminal_conflict_records_orphan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    mgr = _make_manager(tmp_path, monkeypatch)
    try:
        job_id = "xi-orphan"
        _seed_terminal_with_remote_meta(mgr, tmp_path, job_id)
        monitor = _MonitorSpy(["running"], kill_ok=True)
        mgr._remote_monitor = monitor  # type: ignore[assignment]

        # Concurrent reconcile already failed the row; the bsub reply arrives.
        mgr._on_submitted(job_id, "9999", expected_attempt=1)

        stored = mgr.store.get(job_id)
        assert stored.status == JobStatus.FAILED, "terminal row must stay terminal"
        orphans = (stored.result or {}).get("remote", {}).get("orphans") or []
        assert len(orphans) == 1
        orphan = orphans[0]
        assert orphan["lsf_job_id"] == "9999"
        assert orphan["node"] == "compute-01"
        assert orphan["attempt"] == 1
        assert orphan["cancel_state"] == "unconfirmed"
        assert orphan.get("requested_at")
        assert "remote.submitted_orphan" in [e.get("type") for e in _events(mgr, job_id)]
        assert monitor.kills == ["9999"], "first cancel attempt must fire"
    finally:
        mgr.shutdown()


def test_orphan_cancel_retries_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    mgr = _make_manager(tmp_path, monkeypatch)
    job_id = "xii-restart"
    monitor = _MonitorSpy(["running"], kill_ok=False)
    try:
        _seed_terminal_with_remote_meta(mgr, tmp_path, job_id)
        mgr._remote_monitor = monitor  # type: ignore[assignment]

        mgr._on_submitted(job_id, "8888", expected_attempt=1)
        stored = mgr.store.get(job_id)
        orphans = (stored.result or {}).get("remote", {}).get("orphans") or []
        assert len(orphans) == 1 and orphans[0]["cancel_state"] == "unconfirmed"
        assert monitor.kills == ["8888"]
        assert orphans[0].get("failures", 0) >= 1, "failed bkill must be recorded"
        mgr.shutdown()
    except Exception:
        try:
            mgr.shutdown()
        except Exception:
            pass
        raise

    # Restart: same DB, fresh manager — backoff window already elapsed.
    mgr2 = _make_manager(tmp_path, monkeypatch)
    try:
        monitor2 = _MonitorSpy(["running", "not_found"], kill_ok=True)
        mgr2._remote_monitor = monitor2  # type: ignore[assignment]

        record = mgr2.store.get(job_id)
        orphans = (record.result or {}).get("remote", {}).get("orphans") or []
        _age_orphans(mgr2, job_id, orphans[0]["lsf_job_id"])

        # Round 1: still running -> bkill resent, stays unconfirmed.
        mgr2._reconcile_once()
        stored = mgr2.store.get(job_id)
        orphans = (stored.result or {}).get("remote", {}).get("orphans") or []
        assert len(orphans) == 1
        assert monitor2.kills == ["8888"]
        assert stored.status == JobStatus.FAILED

        # Round 2: job gone -> confirmed and cleared.
        _age_orphans(mgr2, job_id, "8888")
        mgr2._reconcile_once()
        stored = mgr2.store.get(job_id)
        assert stored.status == JobStatus.FAILED, "original job stays terminal"
        assert not (stored.result or {}).get("remote", {}).get("orphans")
        confirmed = [
            e for e in _events(mgr2, job_id) if e.get("type") == "remote.orphan_cancel_confirmed"
        ]
        assert confirmed, "confirmation must be observable via event"
        assert confirmed[0]["lsf_job_id"] == "8888"
        assert confirmed[0]["cancel_state"] == "confirmed"
    finally:
        mgr2.shutdown()


def _age_orphans(mgr: JobManager, job_id: str, lsf_job_id: str) -> None:
    """Rewrite the orphan's last_attempt_at into the past (backoff elapsed)."""
    record = mgr.store.get(job_id)
    assert record is not None
    result = dict(record.result or {})
    meta = dict(result.get("remote") or {})
    orphans = [dict(o) for o in meta.get("orphans", []) if isinstance(o, dict)]
    for orphan in orphans:
        if orphan.get("lsf_job_id") == lsf_job_id:
            orphan["last_attempt_at"] = _past_iso(7200)
    meta["orphans"] = orphans
    result["remote"] = meta
    mgr.store.update_progress(job_id, expected_revision=record.revision, result=result)


def test_orphan_cancel_retry_is_bounded_and_backs_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    mgr = _make_manager(tmp_path, monkeypatch)
    try:
        job_id = "xiii-backoff"
        _seed_terminal_with_remote_meta(mgr, tmp_path, job_id)
        monitor = _MonitorSpy(["running"], kill_ok=True)
        monitor.raise_on_get = True  # node permanently unreachable
        mgr._remote_monitor = monitor  # type: ignore[assignment]

        mgr._on_submitted(job_id, "7777", expected_attempt=1)
        after_first = monitor.gets
        assert after_first >= 1

        # Rapid reconcile rounds must NOT hammer the unreachable node
        # (bounded by the backoff window).
        for _ in range(5):
            mgr._reconcile_once()
        assert monitor.gets == after_first, "no unbounded fast retries"

        # Backoff grows with consecutive failures, capped at 1h.
        assert mgr._orphan_backoff_seconds({"failures": 0}) == 30
        assert mgr._orphan_backoff_seconds({"failures": 1}) == 60
        assert mgr._orphan_backoff_seconds({"failures": 2}) == 120
        assert mgr._orphan_backoff_seconds({"failures": 20}) == 3600

        # Keep failing (aging the window each round) until the stall alert.
        for _ in range(8):
            record = mgr.store.get(job_id)
            orphans = (record.result or {}).get("remote", {}).get("orphans") or []
            if not orphans:
                break
            _age_orphans(mgr, job_id, orphans[0]["lsf_job_id"])
            mgr._reconcile_once()

        record = mgr.store.get(job_id)
        assert record.status == JobStatus.FAILED, "terminal row never moves"
        orphans = (record.result or {}).get("remote", {}).get("orphans") or []
        assert orphans, "unconfirmed orphan is retained until confirmed"
        assert orphans[0]["failures"] >= 5
        stall_events = [
            e for e in _events(mgr, job_id) if e.get("type") == "remote.orphan_cancel_stalled"
        ]
        assert stall_events, "threshold must downgrade with an alert event"
        assert stall_events[0]["lsf_job_id"] == "7777"
        assert len(stall_events) == 1, "alert fires once"
    finally:
        mgr.shutdown()


# --------------------------------------------------------------------- #
# Submission id determinism / LSF name shape
# --------------------------------------------------------------------- #


def test_submission_id_shape_and_stability():
    sid = submission_id_for("some-job", 3)
    assert sid.startswith("sub_") and len(sid) == 4 + 16
    assert submission_id_for("some-job", 3) == sid
    assert submission_id_for("some-job", 4) != sid
    assert submission_lsf_name(sid) == f"acp_{sid}"
    assert len(submission_lsf_name(sid)) < 200


ALL_TESTS = [
    test_reply_lost_keeps_dir_unconfirmed_and_adopts_without_second_bsub,
    test_on_submitted_persist_failure_converges_without_second_bsub,
    test_on_submitted_exception_does_not_roll_back_submission,
    test_submitted_event_write_failure_does_not_roll_back,
    test_multiple_name_matches_return_unknown,
    test_not_accepted_requires_full_evidence_set,
    test_indeterminate_converges_in_continuous_polls_without_restart,
    test_cancel_before_bsub_reply_adopts_then_kills_then_cancels,
    test_cancel_confirmation_needs_positive_evidence,
    test_explicit_rejection_fails_job_without_touching_reused_dir,
    test_rejected_submission_keeps_reused_dir_science_intact,
    test_ssh_failure_keeps_submission_unconfirmed_without_cleanup,
    test_pending_submission_survives_polls_and_restart,
    test_detail_recovery_projects_submit_state,
    test_valid_lease_blocks_not_accepted_across_reconciles,
    test_submit_worker_death_converges_without_service_restart,
    test_cancel_before_checkpoint_aborts_without_bsub,
    test_barrier_cancel_race_has_exactly_one_outcome,
    test_cancel_after_bsub_persists_id_before_kill_then_cancelled,
    test_on_submitted_terminal_conflict_records_orphan,
    test_orphan_cancel_retries_after_restart,
    test_startup_reconcile_adopts_before_recover,
    test_orphan_cancel_retry_is_bounded_and_backs_off,
    test_submission_id_shape_and_stability,
]


def main() -> int:
    import tempfile as _tempfile

    import pytest as _pytest

    failed = 0
    for test in ALL_TESTS:
        print(f"RUN  {test.__name__}")
        params = test.__code__.co_varnames[: test.__code__.co_argcount]
        holder = _tempfile.TemporaryDirectory()
        tmp_path = Path(holder.name)
        try:
            if "tmp_path" in params and "monkeypatch" in params:
                with _pytest.MonkeyPatch.context() as mp:
                    test(tmp_path, mp)
            elif "tmp_path" in params:
                test(tmp_path)
            else:
                test()
        except Exception:
            import traceback

            traceback.print_exc()
            failed += 1
            print(f"  FAIL {test.__name__}")
        finally:
            holder.cleanup()
    print(f"Results: {len(ALL_TESTS) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
