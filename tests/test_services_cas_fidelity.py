"""CAS / control-intent / PAUSED fidelity across the Runtime extraction (B05-2).

Todo 25 extracted the job submit/control entries into
``acp_services.runtime.commands.RuntimeCommands``; the API and CLI now drive the
live ``JobManager`` **through** that surface.  Todo 26 pins down that the
extraction introduced **zero** behavioural drift: every execution-integrity
invariant that the manager/store enforce must hold identically whether a caller
reaches the manager directly (the pre-extraction path) or through the extracted
Runtime use-case chain (``RuntimeCommands -> injected port -> composition-root
adapter -> manager``).

Equivalence harness
-------------------
``_DirectPath`` and ``_RuntimePath`` expose the same control verbs.  A scenario
is executed once per path over an independent ``JobManager`` / run_root, then the
two *observable* snapshots (the ``jobs`` row with volatile timestamps removed +
the event-type sequence) are compared for exact equality.  This is the
"post-extraction call chain" equivalence the todo requires.

Covered invariants (each one direct-vs-runtime unless noted fidelity-only):

* (0) boundary fidelity — each Runtime use case forwards **exactly once** to the
  live manager with identical arguments and returns that call's result untouched.
* (1) CAS — revision/status/attempt guard: a stale write after a control command
  still raises ``JobStateConflictError`` and never mutates the row.
* (2) control intent is never overwritten by a stale poll observation
  (non-terminal *and* terminal).
* (3) a remote cancel is ``CANCELLED`` only after LSF positively confirms the job
  is gone (``_CANCEL_CONFIRMED_LSF``), never from ``cancel_event`` alone.
* (4) PAUSED resource semantics — local killpg SIGSTOP/SIGCONT, remote
  ``bstop``/``bresume``, poller skip, and purge-skip (PAUSED cannot be deleted).
* (5) terminal side effects run **after** the terminal CAS commits, and the
  retry is idempotent (event ``idempotency_key`` dedup).
* (6) in-place rerun bumps ``attempt`` by exactly one, keeps the storage
  identity, and archives the closed attempt's receipts under
  ``WORK/00_RUNTIME/attempts/<n>/``.

Run with: ``PYTHONPATH=src python3.11 -m pytest tests/test_services_cas_fidelity.py -q``
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.store import JobStateConflictError
from tests.test_job_state_transitions import (
    _event_types,
    _raw_row,
    _seed_job_for_transitions,
)
from tests.test_job_state_transitions import (
    _make_manager as _local_manager,
)
from tests.test_remote_cancel_confirmation import (
    _make_manager as _remote_manager,
)
from tests.test_remote_cancel_confirmation import (
    _MonitorSpy,
    _no_local_kill,
    _RunnerSpy,
)
from tests.test_remote_cancel_confirmation import (
    _seed_job as _seed_remote_job,
)

# ====================================================================== #
# Post-extraction call-chain harness (direct vs Runtime)
# ====================================================================== #


class _DirectPath:
    """Pre-extraction path: call the live ``JobManager`` control verbs."""

    def __init__(self, manager: Any) -> None:
        self._m = manager

    def pause(self, job_id: str) -> Any:
        return self._m.pause_job(job_id)

    def unpause(self, job_id: str) -> Any:
        return self._m.unpause_job(job_id)

    def cancel(self, job_id: str) -> Any:
        return self._m.cancel(job_id)

    def rerun(self, job_id: str, **kwargs: Any) -> Any:
        return self._m.rerun_job(job_id, **kwargs)

    def purge(self, **kwargs: Any) -> Any:
        return self._m.purge_jobs(**kwargs)


class _RuntimePath:
    """Extracted path: RuntimeCommands -> injected ports -> adapters -> manager."""

    def __init__(self, manager: Any) -> None:
        from acp.services_bootstrap import build_runtime_commands

        self._c = build_runtime_commands(manager)

    def pause(self, job_id: str) -> Any:
        return self._c.pause(job_id)

    def unpause(self, job_id: str) -> Any:
        return self._c.unpause(job_id)

    def cancel(self, job_id: str) -> Any:
        return self._c.cancel(job_id)

    def rerun(self, job_id: str, **kwargs: Any) -> Any:
        return self._c.rerun(job_id, **kwargs)

    def purge(self, **kwargs: Any) -> Any:
        return self._c.purge(**kwargs)


# Volatile values: wall-clock timestamps (root + nested result payloads).
_VOLATILE_KEYS = frozenset(
    {
        "created_at",
        "updated_at",
        "started_at",
        "completed_at",
        "confirmed_at",
        "requested_at",
        "submitted_at",
    }
)


def _strip_volatile(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_volatile(v) for k, v in value.items() if k not in _VOLATILE_KEYS}
    if isinstance(value, list):
        return [_strip_volatile(item) for item in value]
    return value


def _observable(db: Path, job_id: str) -> dict[str, Any]:
    """Compare-ready row snapshot: volatile timestamps + work_dir removed."""
    row = _raw_row(db, job_id)
    out = {k: v for k, v in row.items() if k not in _VOLATILE_KEYS and k != "work_dir"}
    for json_col in ("result_json", "spec_json"):
        raw = out.get(json_col)
        if isinstance(raw, str) and raw:
            out[json_col] = json.dumps(_strip_volatile(json.loads(raw)), sort_keys=True)
    return out


def _seed_local(mgr: Any, tmp_path: Path, job_id: str, **overrides: Any) -> JobRecord:
    work_dir = tmp_path / "runs" / job_id
    work_dir.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, Any] = {
        "spec": JobSpec(workflow="fake", name=job_id, input={"source": "CCO"}),
        "status": JobStatus.RUNNING,
        "work_dir": str(work_dir),
    }
    kwargs.update(overrides)
    record = JobRecord(id=job_id, **kwargs)
    mgr.store.create(record)
    return record


def _split_dirs(tmp_path: Path, tag: str) -> tuple[Path, Path]:
    direct = tmp_path / f"{tag}-direct"
    runtime = tmp_path / f"{tag}-runtime"
    direct.mkdir(parents=True, exist_ok=True)
    runtime.mkdir(parents=True, exist_ok=True)
    return direct, runtime


class _PauseMonitorSpy:
    """Remote monitor double with bstop/bresume recorders (pause semantics)."""

    def __init__(self) -> None:
        self.bstop_calls: list[str] = []
        self.bresume_calls: list[str] = []

    def get_lsf_status(self, node: Any, lsf_job_id: str) -> str:
        return "running"

    def bstop_job(self, node: Any, lsf_job_id: str) -> bool:
        self.bstop_calls.append(lsf_job_id)
        return True

    def bresume_job(self, node: Any, lsf_job_id: str) -> bool:
        self.bresume_calls.append(lsf_job_id)
        return True


# ====================================================================== #
# (0) boundary fidelity — one forwarding hop with identical args
# ====================================================================== #


def test_runtime_ports_forward_exactly_once_to_live_manager(tmp_path: Path) -> None:
    """Each extracted use case reaches the live manager once with identical args."""
    from acp.services_bootstrap import build_runtime_commands

    mgr = _local_manager(tmp_path)
    try:
        commands = build_runtime_commands(mgr)
        sentinel = object()

        def _install(name: str) -> Mock:
            mock = Mock(return_value=sentinel)
            setattr(mgr, name, mock)
            return mock

        submit = _install("submit")
        pause = _install("pause_job")
        unpause = _install("unpause_job")
        cancel = _install("cancel")
        cont = _install("continue_job")
        rerun = _install("rerun_job")
        purge = _install("purge_jobs")
        edit = _install("edit_recalculate")

        spec = JobSpec(workflow="fake", name="fwd")
        assert commands.submit(spec, group_id="grp") is sentinel
        submit.assert_called_once_with(spec, "grp")

        assert commands.pause("j") is sentinel
        pause.assert_called_once_with("j")

        assert commands.unpause("j") is sentinel
        unpause.assert_called_once_with("j")

        assert commands.cancel("j") is sentinel
        cancel.assert_called_once_with("j")

        assert commands.continue_job("j", target_node="node-9") is sentinel
        cont.assert_called_once_with("j", target_node="node-9")

        assert commands.rerun("j", project_id="proj") is sentinel
        rerun.assert_called_once_with("j", "proj")

        assert commands.purge(job_ids=["a"], force_cancel=True) is sentinel
        purge.assert_called_once_with(["a"], None, None, None, False, True)

        assert commands.edit_recalculate("j", mode="in_place") is sentinel
        edit.assert_called_once_with("j", mode="in_place")
    finally:
        mgr.shutdown()


def test_runtime_registration_port_forwards_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """The CLI ``--register`` submit entry forwards once to the registration impl."""
    from acp.services_bootstrap import build_runtime_commands

    sentinel = object()
    recorded: list[dict[str, Any]] = []

    def _fake_register(**kwargs: Any) -> object:
        recorded.append(kwargs)
        return sentinel

    monkeypatch.setattr("acp.scheduler.registration.register_completed_cli_job", _fake_register)
    commands = build_runtime_commands(None)  # CLI register needs no scheduler
    assert commands.register_completed_job(workflow="XtbPathSearch") is sentinel
    assert recorded == [{"workflow": "XtbPathSearch"}]


# ====================================================================== #
# (1) CAS revision/status/attempt
# ====================================================================== #


def _cas_scenario(mgr: Any, path: Any, root: Path) -> tuple[dict[str, Any], Any]:
    record = _seed_local(mgr, root, "cas")
    mgr.runner.pause_local = lambda job_id: True  # type: ignore[method-assign]
    paused = path.pause("cas")
    assert paused.status == JobStatus.PAUSED
    assert paused.revision == record.revision + 1
    assert paused.attempt == record.attempt == 1

    conflict: dict[str, Any] | None = None
    try:
        mgr.store.transition(
            "cas",
            expected_status=JobStatus.RUNNING,
            expected_revision=record.revision,
            expected_attempt=record.attempt,
            status=JobStatus.CANCELLED,
        )
    except JobStateConflictError as exc:
        actual = exc.actual or {}
        conflict = {
            "expected_revision": exc.expected.get("revision"),
            "actual_status": actual.get("status"),
            "actual_revision": actual.get("revision"),
            "actual_attempt": actual.get("attempt"),
        }
    return _observable(mgr.store.db_path, "cas"), conflict


def test_cas_revision_status_attempt_equivalence(tmp_path: Path) -> None:
    direct_root, runtime_root = _split_dirs(tmp_path, "cas")
    mgr_d = _local_manager(direct_root)
    mgr_r = _local_manager(runtime_root)
    try:
        obs_d, conflict_d = _cas_scenario(mgr_d, _DirectPath(mgr_d), direct_root)
        obs_r, conflict_r = _cas_scenario(mgr_r, _RuntimePath(mgr_r), runtime_root)
    finally:
        mgr_d.shutdown()
        mgr_r.shutdown()

    assert obs_d == obs_r, "post-extraction chain must yield the identical CAS outcome"
    assert conflict_d == conflict_r
    assert conflict_d == {
        "expected_revision": 0,
        "actual_status": "paused",
        "actual_revision": 1,
        "actual_attempt": 1,
    }, "a stale write must be rejected by the revision/status/attempt CAS"


# ====================================================================== #
# (2) control intent not overwritten by a stale poll observation
# ====================================================================== #


def _stale_poll_scenario(
    mgr: Any, path: Any, root: Path, *, terminal: bool
) -> tuple[dict[str, Any], list[str], str, Any]:
    job_id = "stale-term" if terminal else "stale-prog"
    record = _seed_local(mgr, root, job_id)
    mgr.runner.pause_local = lambda job_id: True  # type: ignore[method-assign]

    def poll(stale: JobRecord) -> tuple[bool, int | None]:
        path.pause(record.id)
        stale.progress = 0.77
        stale.current_stage = "late"
        stale.exit_code = 0
        return (terminal, 0 if terminal else None)

    mgr.runner.poll = poll  # type: ignore[method-assign]
    mgr._poll_job(record.id)
    stored = mgr.store.get(job_id)
    assert stored is not None
    return (
        _observable(mgr.store.db_path, job_id),
        _event_types(mgr, job_id),
        stored.status.value,
        _raw_row(mgr.store.db_path, job_id)["completed_at"],
    )


def test_control_intent_not_overwritten_by_stale_poll_equivalence(tmp_path: Path) -> None:
    direct_root, runtime_root = _split_dirs(tmp_path, "poll")
    mgr_d = _local_manager(direct_root)
    mgr_r = _local_manager(runtime_root)
    try:
        for terminal in (False, True):
            obs_d, events_d, status_d, done_d = _stale_poll_scenario(
                mgr_d, _DirectPath(mgr_d), direct_root, terminal=terminal
            )
            obs_r, events_r, status_r, done_r = _stale_poll_scenario(
                mgr_r, _RuntimePath(mgr_r), runtime_root, terminal=terminal
            )
            assert obs_d == obs_r, "stale-poll handling must not depend on the entry path"
            assert events_d == events_r
            assert status_d == status_r == "paused"
            assert done_d == done_r is None
            assert "job.paused" in events_d
            assert "job.poll_dropped_stale" in events_d
            # The stale progress write and the stale terminal write both lost.
            assert obs_d["progress"] != 0.77
            assert "terminal_side_effects_done" not in (
                json.loads(obs_d["result_json"]) if obs_d["result_json"] else {}
            )
    finally:
        mgr_d.shutdown()
        mgr_r.shutdown()


# ====================================================================== #
# (3) CANCELLING -> CANCELLED only after confirmed stop (remote)
# ====================================================================== #


def _cancel_scenario(
    mgr: Any, path: Any, root: Path
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[str]]:
    job_id = "cancel-src"
    _seed_remote_job(
        mgr,
        root,
        job_id,
        status=JobStatus.RUNNING,
        remote_job_id="7001",
    )
    _no_local_kill(mgr)
    runner = _RunnerSpy(lsf_id="7001")
    monitor = _MonitorSpy(["running"], kill_ok=True)
    mgr.remote_runner = runner  # type: ignore[assignment]
    mgr._remote_monitor = monitor  # type: ignore[assignment]

    record = path.cancel(job_id)
    assert record is not None and record.status == JobStatus.CANCELLING
    after_cancel = _observable(mgr.store.db_path, job_id)

    mgr._poll_job(job_id)  # alive -> bkill (re)sent, still unconfirmed
    alive = _observable(mgr.store.db_path, job_id)
    assert alive["status"] == "cancelling"
    assert monitor.kills == ["7001"]

    monitor.statuses = ["not_found"]  # LSF positively confirms the job is gone
    mgr._poll_job(job_id)
    confirmed = _observable(mgr.store.db_path, job_id)
    assert confirmed["status"] == "cancelled"
    return after_cancel, alive, confirmed, _event_types(mgr, job_id)


def test_cancelling_only_confirmed_after_stop_equivalence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    direct_root, runtime_root = _split_dirs(tmp_path, "cancel")
    mgr_d = _remote_manager(direct_root, monkeypatch)
    mgr_r = _remote_manager(runtime_root, monkeypatch)
    try:
        d_cancel, d_alive, d_confirmed, d_events = _cancel_scenario(
            mgr_d, _DirectPath(mgr_d), direct_root
        )
        r_cancel, r_alive, r_confirmed, r_events = _cancel_scenario(
            mgr_r, _RuntimePath(mgr_r), runtime_root
        )
    finally:
        mgr_d.shutdown()
        mgr_r.shutdown()

    assert d_cancel == r_cancel
    assert d_alive == r_alive
    assert d_confirmed == r_confirmed
    assert d_events == r_events
    assert "remote.cancel_confirmed" in d_events
    # Contract A: no CANCELLED before the bjobs confirmation step.
    assert json.loads(d_alive["result_json"])["remote"]["cancel_state"] != "confirmed"
    assert json.loads(d_confirmed["result_json"])["remote"]["cancel_state"] == "confirmed"


# ====================================================================== #
# (4) PAUSED resource semantics
# ====================================================================== #


def _paused_local_scenario(mgr: Any, path: Any, root: Path) -> tuple[str, str, list[str]]:
    record = _seed_local(mgr, root, "paused-local")
    pause_calls: list[str] = []
    resume_calls: list[str] = []
    mgr.runner.pause_local = (  # type: ignore[method-assign]
        lambda job_id: pause_calls.append(job_id) or True
    )
    mgr.runner.resume_local = (  # type: ignore[method-assign]
        lambda job_id: resume_calls.append(job_id) or True
    )

    path.pause(record.id)  # SIGSTOP
    paused = mgr.store.get(record.id)
    assert paused is not None and paused.status == JobStatus.PAUSED

    # Poller skip: PAUSED is outside _POLL_SCAN_STATUSES, poll must not be reached.
    def _boom(_record: JobRecord) -> tuple[bool, int | None]:
        raise AssertionError("PAUSED jobs must be skipped by the poller")

    mgr.runner.poll = _boom  # type: ignore[method-assign]
    mgr._poll_job(record.id)
    assert mgr.store.get(record.id).status == JobStatus.PAUSED

    path.unpause(record.id)  # SIGCONT
    resumed = mgr.store.get(record.id)
    assert resumed is not None and resumed.status == JobStatus.RUNNING
    assert pause_calls == [record.id] and resume_calls == [record.id]
    return paused.status.value, resumed.status.value, pause_calls + resume_calls


def test_paused_local_resource_semantics_equivalence(tmp_path: Path) -> None:
    direct_root, runtime_root = _split_dirs(tmp_path, "pauselocal")
    mgr_d = _local_manager(direct_root)
    mgr_r = _local_manager(runtime_root)
    try:
        d = _paused_local_scenario(mgr_d, _DirectPath(mgr_d), direct_root)
        r = _paused_local_scenario(mgr_r, _RuntimePath(mgr_r), runtime_root)
    finally:
        mgr_d.shutdown()
        mgr_r.shutdown()
    assert d == r == ("paused", "running", ["paused-local", "paused-local"])


def _paused_remote_scenario(mgr: Any, path: Any, root: Path) -> tuple[list[str], list[str]]:
    job_id = "paused-remote"
    _seed_remote_job(mgr, root, job_id, status=JobStatus.RUNNING, remote_job_id="8001")
    _no_local_kill(mgr)
    monitor = _PauseMonitorSpy()
    mgr._remote_monitor = monitor  # type: ignore[assignment]
    mgr.remote_runner = _RunnerSpy(lsf_id="8001")  # type: ignore[assignment]

    path.pause(job_id)
    assert mgr.store.get(job_id).status == JobStatus.PAUSED  # type: ignore[union-attr]
    path.unpause(job_id)
    assert mgr.store.get(job_id).status == JobStatus.RUNNING  # type: ignore[union-attr]
    return monitor.bstop_calls, monitor.bresume_calls


def test_paused_remote_bstop_bresume_equivalence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    direct_root, runtime_root = _split_dirs(tmp_path, "pauseremote")
    mgr_d = _remote_manager(direct_root, monkeypatch)
    mgr_r = _remote_manager(runtime_root, monkeypatch)
    try:
        d = _paused_remote_scenario(mgr_d, _DirectPath(mgr_d), direct_root)
        r = _paused_remote_scenario(mgr_r, _RuntimePath(mgr_r), runtime_root)
    finally:
        mgr_d.shutdown()
        mgr_r.shutdown()
    assert d == r == (["8001"], ["8001"]), "pause must use bstop, unpause bresume"


def _purge_skip_scenario(mgr: Any, path: Any, root: Path) -> tuple[Any, bool]:
    _seed_local(mgr, root, "paused-purge", status=JobStatus.PAUSED)
    report = path.purge(job_ids=["paused-purge"])
    return report, mgr.store.get("paused-purge") is not None


def test_paused_is_not_deletable_equivalence(tmp_path: Path) -> None:
    """PAUSED is an active non-terminal state: a plain purge must skip it."""
    direct_root, runtime_root = _split_dirs(tmp_path, "purge")
    mgr_d = _local_manager(direct_root)
    mgr_r = _local_manager(runtime_root)
    try:
        report_d, alive_d = _purge_skip_scenario(mgr_d, _DirectPath(mgr_d), direct_root)
        report_r, alive_r = _purge_skip_scenario(mgr_r, _RuntimePath(mgr_r), runtime_root)
    finally:
        mgr_d.shutdown()
        mgr_r.shutdown()

    assert report_d == report_r
    assert report_d[0]["action"] == "skipped_active"
    assert alive_d and alive_r, "a PAUSED row must survive a plain purge"


# ====================================================================== #
# (5) terminal side effects after persist + idempotent retry
# ====================================================================== #


def test_terminal_side_effects_run_after_persist_and_retry_is_idempotent(
    tmp_path: Path,
) -> None:
    mgr = _local_manager(tmp_path)
    try:
        record = _seed_local(mgr, tmp_path, "term")
        order: list[str] = []

        real_transition = mgr.store.transition

        def transition_spy(*args: Any, **kwargs: Any) -> JobRecord:
            out = real_transition(*args, **kwargs)
            order.append("persist")
            return out

        mgr.store.transition = transition_spy  # type: ignore[method-assign]
        mgr._collect_result = lambda rec: {"collected": True}  # type: ignore[method-assign]
        mgr._capture_local_completion = (  # type: ignore[method-assign]
            lambda rec: order.append("side_effect")
        )

        mgr._persist_terminal(
            record,
            record.id,
            record.revision,
            record.attempt,
            0,
            threading.Event(),
            mgr._event_log(record),
            False,
            None,
        )
        assert order[:2] == ["persist", "side_effect"], (
            "terminal side effects must run after the terminal CAS commits"
        )
        stored = mgr.store.get(record.id)
        assert stored is not None and stored.status == JobStatus.COMPLETED
        assert (stored.result or {}).get("terminal_side_effects_done") is True
        assert _event_types(mgr, record.id).count("job.completed") == 1

        # Reset the marker to model a crash-before-marker; the retry is idempotent.
        mgr.store.update_progress(
            record.id,
            expected_revision=stored.revision,
            result={**(stored.result or {}), "terminal_side_effects_done": False},
        )
        fresh = mgr.store.get(record.id)
        assert fresh is not None
        order.clear()
        mgr._retry_terminal_side_effects(fresh)

        assert "side_effect" in order, "the retry must re-run the idempotent side effect"
        retried = mgr.store.get(record.id)
        assert retried is not None
        assert (retried.result or {}).get("terminal_side_effects_done") is True
        assert _event_types(mgr, record.id).count("job.completed") == 1, (
            "the terminal event carries an idempotency key; the retry must not duplicate it"
        )
    finally:
        mgr.shutdown()


# ====================================================================== #
# (6) in-place rerun attempt+1 + receipt archive
# ====================================================================== #


def _seed_rerun_source(mgr: Any, root: Path) -> Path:
    work_dir = root / "runs" / "rerun-src"
    work_dir.mkdir(parents=True)
    _seed_job_for_transitions(
        mgr,
        work_dir,
        "rerun-src",
        status=JobStatus.FAILED,
        result={
            "attempts": 1,
            "remote": {"node": "n1", "submit_state": "done", "lsf_job_id": "7"},
            "remote_dir": "/scratch/acp/rerun-src",
        },
    )
    runtime = work_dir / "WORK" / "00_RUNTIME"
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "checkpoint.json").write_text(
        json.dumps({"task_id": "rerun-src", "workflow": "fake", "plan_fingerprint": "abc"}),
        encoding="utf-8",
    )
    search = work_dir / "WORK" / "02_SEARCH"
    search.mkdir(parents=True)
    (search / "step.out").write_text("old-step", encoding="utf-8")
    (work_dir / "RESULT").mkdir(parents=True, exist_ok=True)
    (work_dir / "RESULT" / "result.json").write_text("{}", encoding="utf-8")
    (runtime / "run.lock").write_text("lock", encoding="utf-8")
    (work_dir / ".exit_code").write_text("1", encoding="utf-8")
    (work_dir / "state.json").write_text("{}", encoding="utf-8")
    (work_dir / "input.xyz").write_text("3\n\nC 0 0 0\n", encoding="utf-8")
    (work_dir / "job.json").write_text("{}", encoding="utf-8")
    return work_dir


def _rerun_scenario(
    mgr: Any, path: Any, root: Path
) -> tuple[dict[str, Any], dict[str, bool], list[str]]:
    work_dir = _seed_rerun_source(mgr, root)
    mgr._start_submission_thread = lambda job_id, name: True  # type: ignore[method-assign]

    rerun = path.rerun("rerun-src")
    assert rerun is not None
    assert rerun.attempt == 2
    assert rerun.status == JobStatus.QUEUED
    assert Path(rerun.work_dir) == work_dir

    archive = work_dir / "WORK" / "00_RUNTIME" / "attempts" / "1"
    archive_state = {
        "exit_code": (archive / ".exit_code").is_file(),
        "state_json": (archive / "state.json").is_file(),
        "run_lock": (archive / "WORK" / "00_RUNTIME" / "run.lock").is_file(),
        "checkpoint": (archive / "WORK" / "00_RUNTIME" / "checkpoint.json").is_file(),
        "root_exit_code_gone": not (work_dir / ".exit_code").exists(),
        "stable_identity_kept": (work_dir / "input.xyz").is_file()
        and (work_dir / "job.json").is_file(),
    }
    return (
        _observable(mgr.store.db_path, "rerun-src"),
        archive_state,
        _event_types(mgr, "rerun-src"),
    )


def test_inplace_rerun_attempt_and_receipt_archive_equivalence(tmp_path: Path) -> None:
    direct_root, runtime_root = _split_dirs(tmp_path, "rerun")
    mgr_d = _local_manager(direct_root)
    mgr_r = _local_manager(runtime_root)
    try:
        obs_d, archive_d, events_d = _rerun_scenario(mgr_d, _DirectPath(mgr_d), direct_root)
        obs_r, archive_r, events_r = _rerun_scenario(mgr_r, _RuntimePath(mgr_r), runtime_root)
    finally:
        mgr_d.shutdown()
        mgr_r.shutdown()

    assert obs_d == obs_r
    assert archive_d == archive_r
    assert archive_d == {
        "exit_code": True,
        "state_json": True,
        "run_lock": True,
        "checkpoint": True,
        "root_exit_code_gone": True,
        "stable_identity_kept": True,
    }
    assert obs_d["attempt"] == 2 and obs_d["status"] == "queued"
    assert "job.rerun" in events_d
    assert events_d == events_r
