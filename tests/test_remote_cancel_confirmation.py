"""D05 cancel confirmation — CANCELLING survives restarts, CANCELLED only
after LSF confirmation.

Scenarios ①–⑦ of plan todo 6 (acp-execution-integrity-remediation):

① restart with bkill not yet sent → bkill re-sent, CANCELLING kept until confirmed
② bkill failure → stays CANCELLING with ``cancel_state="unconfirmed"``
③ bkill reply lost but LSF killed the job → reconcile finds it gone → CANCELLED
④ remote finished naturally → CANCELLED only via bjobs reconciliation (≥1 call)
⑤ real-time poll path: ``cancel_job`` False → non-terminal observation, never
   CANCELLED from ``cancel_event`` + non-zero exit alone
⑥ cancel before the bsub reply → adopt id → bkill → CANCELLED, no duplicate bsub
⑦ no-id indeterminate remote submission → ``_is_remote_job`` stays True and the
   startup reconcile keeps reconciling (never a short-circuit CANCELLED)
Plus the QA failure leg: SSH fully unavailable → CANCELLING kept, no local kill,
no directory cleanup.

Run with: PYTHONPATH=src python3.11 -m pytest tests/test_remote_cancel_confirmation.py -q
"""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from acp.scheduler.events import JobEventLog
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.manager import JobManager
from acp.scheduler.remote.config import RemoteExecutionConfig, RemoteNode
from acp.scheduler.remote.runner import RemoteJobRunner

try:
    import paramiko  # noqa: F401

    REMOTE_AVAILABLE = True
except ImportError:  # pragma: no cover
    REMOTE_AVAILABLE = False

requires_remote = pytest.mark.skipif(not REMOTE_AVAILABLE, reason="paramiko not installed")


# --------------------------------------------------------------------- #
# Doubles: manager-level runner protocol + monitor classification spy
# --------------------------------------------------------------------- #


class _RunnerSpy:
    """Protocol double for ``RemoteJobRunner`` (cancel/submit/reconcile)."""

    def __init__(
        self,
        lsf_id: str | None = None,
        cancel_ok: bool = True,
        verdicts: list[str] | None = None,
    ) -> None:
        self.lsf_id = lsf_id
        self.cancel_ok = cancel_ok
        self.verdicts = list(verdicts or [])
        self.submit_calls = 0
        self.reconcile_calls = 0
        self.recover_calls = 0
        self.cancel_remote_calls: list[str] = []

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
        if on_submitted is not None and self.lsf_id is not None:
            on_submitted(self.lsf_id)
        assert self.lsf_id is not None
        return self.lsf_id

    def reconcile_submission(self, record) -> str:
        self.reconcile_calls += 1
        verdict = self.verdicts.pop(0) if self.verdicts else "unknown"
        if verdict == "found":
            assert self.lsf_id is not None
            record.remote_job_id = self.lsf_id
        return verdict

    def recover_job_state(self, record) -> bool:
        self.recover_calls += 1
        return bool(record.remote_job_id)

    def poll_remote(self, record, event_log, cancel_event):
        from acp.scheduler.remote.runner import RemotePollObservation

        return RemotePollObservation(terminal=False)

    def apply_terminal_side_effects(self, record, event_log, stage_events=()) -> None:
        return None

    def cancel_remote(self, job_id, record=None) -> bool:
        self.cancel_remote_calls.append(job_id)
        return self.cancel_ok


class _MonitorSpy:
    """RemoteJobMonitor double: scripted bjobs classifications + kill recorder."""

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
    return RemoteNode(
        name=name,
        host=f"{name}.example.com",
        username="qc",
        remote_work_dir="/scratch/qc/acp",
        remote_code_dir="/home/qc/acp_code",
        max_concurrent_jobs=4,
        enabled=True,
    )


def _make_manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> JobManager:
    monkeypatch.setattr("acp.scheduler.manager.local_satisfies", lambda required: False)
    cfg = RemoteExecutionConfig(execution_mode="local", nodes=[_remote_node()])
    mgr = JobManager(run_root=tmp_path, remote_config=cfg, poll_interval=30)
    mgr.registry.status_provider = lambda name: {
        "name": name,
        "running": 0,
        "status": "online",
    }
    return mgr


def _seed_job(
    mgr: JobManager,
    tmp_path: Path,
    job_id: str,
    *,
    status: JobStatus,
    remote_job_id: str | None = None,
    submit_state: str = "submitted",
    cancel_state: str | None = "requested",
) -> JobRecord:
    work_dir = tmp_path / "runs" / job_id
    work_dir.mkdir(parents=True, exist_ok=True)
    remote_meta: dict = {
        "schema": 1,
        "relative": f"projA/{job_id}",
        "attempt": 1,
        "node": "compute-01",
        "submission_id": f"sub_{job_id}",
        "submit_state": submit_state,
    }
    if cancel_state is not None:
        remote_meta["cancel_state"] = cancel_state
    record = JobRecord(
        id=job_id,
        spec=_confsearch_spec(),
        status=status,
        work_dir=str(work_dir),
        remote_job_id=remote_job_id,
        result={
            "remote": remote_meta,
            "node": "compute-01",
            "remote_dir": f"/scratch/qc/acp/projA/{job_id}",
            "execution_kind": "remote",
        },
    )
    mgr.store.create(record)
    return record


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
    meta = (record.result or {}).get("remote")
    assert isinstance(meta, dict)
    return meta


def _no_local_kill(mgr: JobManager) -> None:
    """Any local kill/cleanup on a remote job is a contract violation."""

    def _boom(*args, **kwargs):
        raise AssertionError("remote jobs must never be locally killed/cleaned")

    mgr.runner.cancel_local = _boom  # type: ignore[method-assign]
    mgr._terminate_stale_task_processes = _boom  # type: ignore[method-assign]


# --------------------------------------------------------------------- #
# ① restart with bkill not yet sent
# --------------------------------------------------------------------- #


def test_restart_before_bkill_resends_and_keeps_cancelling_until_confirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    mgr = _make_manager(tmp_path, monkeypatch)
    try:
        job_id = "d05-restart"
        # Crash window: cancel intent persisted, bkill never sent.
        _seed_job(
            mgr,
            tmp_path,
            job_id,
            status=JobStatus.CANCELLING,
            remote_job_id="7001",
            cancel_state="requested",
        )
        _no_local_kill(mgr)
        runner = _RunnerSpy(lsf_id="7001")
        monitor = _MonitorSpy(["running"], kill_ok=True)
        mgr.remote_runner = runner  # type: ignore[assignment]
        mgr._remote_monitor = monitor  # type: ignore[assignment]

        # Restart recovery: reconcile — alive → bkill re-sent, NOT CANCELLED.
        mgr._requeue_active_on_startup()
        stored = mgr.store.get(job_id)
        assert stored is not None
        assert stored.status == JobStatus.CANCELLING, "never CANCELLED before confirmation"
        assert monitor.kills == ["7001"], "bkill must be re-sent after the restart"
        assert monitor.gets >= 1, "classification must go through bjobs"
        assert _remote_meta(mgr, job_id).get("cancel_state") == "sent"

        # LSF now reports the job gone → bjobs confirmation publishes CANCELLED.
        monitor.statuses = ["not_found"]
        mgr._poll_job(job_id)
        stored = mgr.store.get(job_id)
        assert stored is not None
        assert stored.status == JobStatus.CANCELLED
        assert _remote_meta(mgr, job_id).get("cancel_state") == "confirmed"
        assert "remote.cancel_confirmed" in _event_types(mgr, job_id)
    finally:
        mgr.shutdown()


# --------------------------------------------------------------------- #
# ② bkill failure then restart
# --------------------------------------------------------------------- #


def test_bkill_failure_keeps_cancelling_unconfirmed_across_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    mgr = _make_manager(tmp_path, monkeypatch)
    try:
        job_id = "d05-bkill-fail"
        _seed_job(mgr, tmp_path, job_id, status=JobStatus.RUNNING, remote_job_id="7002")
        _no_local_kill(mgr)
        runner = _RunnerSpy(lsf_id="7002", cancel_ok=False)
        mgr.remote_runner = runner  # type: ignore[assignment]

        cancelled = mgr.cancel(job_id)
        assert cancelled is not None and cancelled.status == JobStatus.CANCELLING
        assert runner.cancel_remote_calls == [job_id]
        assert _remote_meta(mgr, job_id).get("cancel_state") == "unconfirmed"
        assert "remote.cancel_failed" in _event_types(mgr, job_id)

        # Restart: still alive, bkill still failing → unconfirmed, kept.
        monitor = _MonitorSpy(["running"], kill_ok=False)
        mgr._remote_monitor = monitor  # type: ignore[assignment]
        mgr._requeue_active_on_startup()
        stored = mgr.store.get(job_id)
        assert stored is not None
        assert stored.status == JobStatus.CANCELLING, "failed bkill can never publish CANCELLED"
        assert _remote_meta(mgr, job_id).get("cancel_state") == "unconfirmed"
        assert monitor.kills == ["7002"], "the retry must re-attempt bkill"
    finally:
        mgr.shutdown()


# --------------------------------------------------------------------- #
# ③ bkill reply lost → reconcile finds the job gone
# --------------------------------------------------------------------- #


def test_lost_bkill_reply_confirmed_by_reconcile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    mgr = _make_manager(tmp_path, monkeypatch)
    try:
        job_id = "d05-lost-reply"
        _seed_job(mgr, tmp_path, job_id, status=JobStatus.RUNNING, remote_job_id="7003")
        _no_local_kill(mgr)
        runner = _RunnerSpy(lsf_id="7003", cancel_ok=False)  # reply lost
        monitor = _MonitorSpy(["not_found"], kill_ok=True)
        mgr.remote_runner = runner  # type: ignore[assignment]
        mgr._remote_monitor = monitor  # type: ignore[assignment]

        mgr.cancel(job_id)
        assert mgr.store.get(job_id).status == JobStatus.CANCELLING  # type: ignore[union-attr]
        assert _remote_meta(mgr, job_id).get("cancel_state") == "unconfirmed"

        mgr._poll_job(job_id)
        stored = mgr.store.get(job_id)
        assert stored is not None
        assert stored.status == JobStatus.CANCELLED, "bjobs gone → confirmed"
        assert monitor.gets >= 1, "confirmation must record a bjobs call"
        assert _remote_meta(mgr, job_id).get("cancel_state") == "confirmed"
        confirm = [e for e in _events(mgr, job_id) if e.get("type") == "remote.cancel_confirmed"]
        assert confirm and str(confirm[0].get("evidence", "")).startswith("bjobs:")
    finally:
        mgr.shutdown()


# --------------------------------------------------------------------- #
# ④ remote already finished naturally
# --------------------------------------------------------------------- #


def test_naturally_finished_remote_cancel_confirmed_on_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    mgr = _make_manager(tmp_path, monkeypatch)
    try:
        job_id = "d05-natural-end"
        _seed_job(
            mgr,
            tmp_path,
            job_id,
            status=JobStatus.CANCELLING,
            remote_job_id="7004",
            cancel_state="requested",
        )
        _no_local_kill(mgr)
        runner = _RunnerSpy(lsf_id="7004")
        monitor = _MonitorSpy(["done"], kill_ok=True)
        mgr.remote_runner = runner  # type: ignore[assignment]
        mgr._remote_monitor = monitor  # type: ignore[assignment]

        mgr._requeue_active_on_startup()
        stored = mgr.store.get(job_id)
        assert stored is not None
        assert stored.status == JobStatus.CANCELLED
        assert monitor.gets >= 1, "reconciliation call records must be >= 1"
        assert monitor.kills == [], "a finished job needs no bkill"
        assert _remote_meta(mgr, job_id).get("cancel_state") == "confirmed"
        confirm = [e for e in _events(mgr, job_id) if e.get("type") == "remote.cancel_confirmed"]
        assert confirm and confirm[0].get("evidence") == "bjobs:done"
        # No local cleanup for a remote lifecycle.
        assert Path(stored.work_dir).exists()
    finally:
        mgr.shutdown()


# --------------------------------------------------------------------- #
# ⑤ real-time poll path
# --------------------------------------------------------------------- #


@requires_remote
def test_poll_remote_failed_bkill_returns_non_terminal_observation(tmp_path: Path):
    """cancel_job False → no ``cancel_sent``, no terminal observation."""
    from tests.test_remote_phase2 import make_node

    node = make_node()
    config = RemoteExecutionConfig(execution_mode="remote", nodes=[node])
    runner = RemoteJobRunner(MagicMock(), config, monitor=MagicMock(), code_syncer=MagicMock())
    runner._monitor.cancel_job.return_value = False
    runner._monitor.get_exit_code.return_value = None

    work_dir = tmp_path / "proj" / "cancel-fail"
    work_dir.mkdir(parents=True)
    spec = JobSpec(workflow="Confsearch", method={"protocol": "xtb-crest"})
    record = JobRecord(id="cancel-fail", spec=spec, work_dir=str(work_dir))
    event_log = JobEventLog(work_dir / "events.jsonl")
    cancel = threading.Event()
    cancel.set()
    runner._job_states["cancel-fail"] = {
        "node": node,
        "remote_job_dir": "/scratch/test/acp_jobs/cancel-fail",
        "lsf_job_id": "7005",
        "stdout_offset": 0,
        "stderr_offset": 0,
        "cli_cmd": ["python", "-m", "acp.cli", "run", "Confsearch"],
        "poll_cycle": 0,
        "seen_stages": set(),
    }

    observation = runner.poll_remote(record, event_log, cancel)
    assert observation.terminal is False, "failed bkill must not return a terminal observation"
    assert not runner._job_states["cancel-fail"].get("cancel_sent")
    assert runner._monitor.cancel_job.call_count == 1

    # Next poll retries the bkill instead of finalising.
    runner.poll_remote(record, event_log, cancel)
    assert runner._monitor.cancel_job.call_count == 2
    assert not runner._job_states["cancel-fail"].get("cancel_sent")
    events = [e["type"] for e in event_log.read_all()]
    assert "remote.cancel_sent" in events


def test_realtime_poll_keeps_cancelling_and_retries_until_confirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Manager leg: cancel_event + non-zero exit never finalises remotely."""
    mgr = _make_manager(tmp_path, monkeypatch)
    try:
        job_id = "d05-rt-poll"
        _seed_job(mgr, tmp_path, job_id, status=JobStatus.RUNNING, remote_job_id="7006")
        _no_local_kill(mgr)
        runner = _RunnerSpy(lsf_id="7006", cancel_ok=True)
        monitor = _MonitorSpy(["running"], kill_ok=False)
        mgr.remote_runner = runner  # type: ignore[assignment]
        mgr._remote_monitor = monitor  # type: ignore[assignment]

        mgr.cancel(job_id)
        assert mgr.store.get(job_id).status == JobStatus.CANCELLING  # type: ignore[union-attr]

        # Poll round 1: alive + bkill failure → CANCELLING, retry recorded.
        mgr._poll_job(job_id)
        assert mgr.store.get(job_id).status == JobStatus.CANCELLING  # type: ignore[union-attr]
        assert monitor.kills == ["7006"]
        # Poll round 2: retried, still unconfirmed → CANCELLING.
        mgr._poll_job(job_id)
        assert mgr.store.get(job_id).status == JobStatus.CANCELLING  # type: ignore[union-attr]
        assert monitor.kills == ["7006", "7006"], "next poll must retry bkill"
        assert _remote_meta(mgr, job_id).get("cancel_state") == "unconfirmed"

        # Even a terminal-looking observation must not publish CANCELLED
        # from cancel_event + non-zero exit alone.
        event = threading.Event()
        event.set()
        record = mgr.store.get(job_id)
        assert record is not None
        mgr._persist_terminal(
            record,
            record.id,
            record.revision,
            record.attempt,
            130,
            event,
            mgr._event_log(record),
            True,
            None,
        )
        assert mgr.store.get(job_id).status == JobStatus.CANCELLING  # type: ignore[union-attr]

        # bjobs finally confirms the job is gone → CANCELLED.
        monitor.statuses = ["not_found"]
        mgr._poll_job(job_id)
        assert mgr.store.get(job_id).status == JobStatus.CANCELLED  # type: ignore[union-attr]
        assert "remote.cancel_confirmed" in _event_types(mgr, job_id)
    finally:
        mgr.shutdown()


# --------------------------------------------------------------------- #
# ⑥ cancel before the bsub reply
# --------------------------------------------------------------------- #


def test_cancel_before_bsub_reply_adopts_kills_without_duplicate_submission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    mgr = _make_manager(tmp_path, monkeypatch)
    try:
        job_id = "d05-early-cancel"
        runner = _RunnerSpy(lsf_id="7106", verdicts=["found"])
        mgr.remote_runner = runner  # type: ignore[assignment]
        _seed_queued(mgr, tmp_path, job_id)

        def indeterminate(record, event_log, target_node=None, **kwargs):
            from acp.scheduler.remote.runner import RemoteSubmissionIndeterminate

            runner.submit_calls += 1
            raise RemoteSubmissionIndeterminate("reply lost")

        runner.submit_remote = indeterminate  # type: ignore[assignment]
        assert mgr._submit_job(job_id) is True
        assert _remote_meta(mgr, job_id).get("submit_state") == "unconfirmed"

        cancelled = mgr.cancel(job_id)
        assert cancelled is not None and cancelled.status == JobStatus.CANCELLING
        assert _remote_meta(mgr, job_id).get("cancel_state") == "requested"
        assert not mgr.store.get(job_id).remote_job_id  # type: ignore[union-attr]

        monitor = _MonitorSpy(["running", "not_found"], kill_ok=True)
        mgr._remote_monitor = monitor  # type: ignore[assignment]

        mgr._poll_job(job_id)
        stored = mgr.store.get(job_id)
        assert stored is not None
        assert stored.remote_job_id == "7106", "reconcile adopts the id first"
        assert stored.status == JobStatus.CANCELLED
        assert monitor.kills == ["7106"], "bkill only after the id is persisted"
        assert monitor.gets >= 2, "confirmation requires a bjobs check"
        assert _remote_meta(mgr, job_id).get("cancel_state") == "confirmed"
        assert "remote.cancel_confirmed" in _event_types(mgr, job_id)
        assert runner.submit_calls == 1, "no duplicate submission"
    finally:
        mgr.shutdown()


# --------------------------------------------------------------------- #
# ⑦ no-id indeterminate remote submission
# --------------------------------------------------------------------- #


def test_indeterminate_no_id_remote_stays_remote_and_reconciles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    mgr = _make_manager(tmp_path, monkeypatch)
    try:
        job_id = "d05-no-id"
        runner = _RunnerSpy(lsf_id="7207", verdicts=["unknown", "unknown"])
        mgr.remote_runner = runner  # type: ignore[assignment]
        mgr._remote_monitor = _MonitorSpy(["running"], kill_ok=True)  # type: ignore[assignment]
        _seed_queued(mgr, tmp_path, job_id)

        def indeterminate(record, event_log, target_node=None, **kwargs):
            from acp.scheduler.remote.runner import RemoteSubmissionIndeterminate

            runner.submit_calls += 1
            raise RemoteSubmissionIndeterminate("reply lost")

        runner.submit_remote = indeterminate  # type: ignore[assignment]
        assert mgr._submit_job(job_id) is True
        mgr.cancel(job_id)
        assert mgr.store.get(job_id).status == JobStatus.CANCELLING  # type: ignore[union-attr]

        # Persisted execution type / remote intent → must classify remote.
        record = mgr.store.get(job_id)
        assert record is not None
        assert record.remote_job_id is None
        assert JobManager._is_remote_job(record), "remote intent must never look local"

        _no_local_kill(mgr)
        # Restart: keeps reconciling, never short-circuits to CANCELLED.
        mgr._requeue_active_on_startup()
        stored = mgr.store.get(job_id)
        assert stored is not None
        assert stored.status == JobStatus.CANCELLING, "no id + unknown → keep CANCELLING"
        assert runner.reconcile_calls >= 1, "startup must reconcile the submission"
        assert stored.remote_job_id is None

        # Later cycles keep reconciling until the job is found (or evidence
        # proves there is none) — still never a direct CANCELLED.
        mgr._requeue_active_on_startup()
        assert mgr.store.get(job_id).status == JobStatus.CANCELLING  # type: ignore[union-attr]
        assert runner.reconcile_calls >= 2
    finally:
        mgr.shutdown()


def test_is_remote_job_classifies_remote_intent_sources():
    """Extension coverage: remote meta / execution_kind / target_node."""

    def _rec(result: dict | None = None, target_node: str | None = None) -> JobRecord:
        from acp.scheduler.jobs import JobSpec as _Spec

        return JobRecord(
            id="probe",
            spec=_Spec(workflow="Confsearch", target_node=target_node),
            status=JobStatus.CANCELLING,
            work_dir="/tmp/probe",
            result=result,
        )

    # Genuinely local: no remote intent whatsoever.
    assert not JobManager._is_remote_job(_rec(None))
    assert not JobManager._is_remote_job(_rec({"execution_kind": "local"}))
    assert not JobManager._is_remote_job(_rec(None, target_node="local"))

    for result in (
        {"remote": {"submit_state": "intent"}},
        {"remote": {"submit_state": "unconfirmed"}},
        {"remote": {"node": "compute-01"}},
        {"remote": {"relative": "projA/job"}},
        {"execution_kind": "remote"},
        {"target_node": "compute-01"},
        {"lsf_job_id": "1"},
    ):
        assert JobManager._is_remote_job(_rec(result)), result
    assert JobManager._is_remote_job(_rec(None, target_node="compute-01"))


# --------------------------------------------------------------------- #
# QA failure leg: SSH fully unavailable
# --------------------------------------------------------------------- #


def test_ssh_unavailable_keeps_cancelling_and_never_cleans_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    mgr = _make_manager(tmp_path, monkeypatch)
    try:
        job_id = "d05-ssh-down"
        _seed_job(
            mgr,
            tmp_path,
            job_id,
            status=JobStatus.CANCELLING,
            remote_job_id="7008",
            cancel_state="sent",
        )
        _no_local_kill(mgr)
        runner = _RunnerSpy(lsf_id="7008")
        monitor = _MonitorSpy(["running"], kill_ok=True)
        monitor.raise_on_get = True  # every bjobs attempt fails
        mgr.remote_runner = runner  # type: ignore[assignment]
        mgr._remote_monitor = monitor  # type: ignore[assignment]

        mgr._requeue_active_on_startup()
        mgr._poll_job(job_id)
        stored = mgr.store.get(job_id)
        assert stored is not None
        assert stored.status == JobStatus.CANCELLING, "communication failure keeps CANCELLING"
        assert _remote_meta(mgr, job_id).get("cancel_state") == "unconfirmed"
        assert monitor.kills == [], "no blind bkill while unreachable"
        assert Path(stored.work_dir).exists(), "directories must not be cleaned"
    finally:
        mgr.shutdown()


# --------------------------------------------------------------------- #
# Cross-attempt isolation: a superseded attempt's terminal retry must not
# run remote side effects (cleanup / cancel-state teardown) for the new row.
# --------------------------------------------------------------------- #


def test_stale_attempt_retry_skips_remote_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    mgr = _make_manager(tmp_path, monkeypatch)
    try:
        job_id = "stale-remote-retry"
        _seed_job(mgr, tmp_path, job_id, status=JobStatus.COMPLETED, remote_job_id="9001")
        stale = mgr.store.get(job_id)
        assert stale is not None and stale.attempt == 1
        mgr.store.update_progress(
            job_id,
            expected_revision=stale.revision,
            result={**(stale.result or {}), "terminal_side_effects_done": False},
        )

        runner = _RunnerSpy(lsf_id="9001")
        apply_calls: list[int] = []
        runner.apply_terminal_side_effects = (  # type: ignore[method-assign]
            lambda record, event_log, stage_events=(): apply_calls.append(record.attempt)
        )
        mgr.remote_runner = runner  # type: ignore[assignment]

        before_requeue = mgr.store.get(job_id)
        assert before_requeue is not None
        requeued = mgr.store.requeue_with_spec(
            job_id,
            new_spec=before_requeue.spec,
            expected_revision=before_requeue.revision,
            expected_attempt=1,
            expected_status=JobStatus.COMPLETED,
        )
        assert requeued.attempt == 2 and requeued.status == JobStatus.QUEUED

        cancel_event = threading.Event()
        mgr._cancel_events[job_id] = cancel_event

        mgr._retry_terminal_side_effects(stale)

        assert apply_calls == [], "stale retry must not run remote side effects"
        assert mgr._cancel_events.get(job_id) is cancel_event, (
            "stale retry must not tear down the new attempt's cancel event"
        )
        final = mgr.store.get(job_id)
        assert final is not None and final.attempt == 2
        assert (final.result or {}).get("terminal_side_effects_done") is not True
    finally:
        mgr.shutdown()

