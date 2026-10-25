"""B06-4 (todo 33) — submission-fidelity checklist, pinned by tests.

The B06 execution switch (todo 32) moves local/remote execution onto the
envelope/worker entry.  This module pins the *behaviours that must survive the
switch* so a later refactor cannot silently drop them:

1. **Submit intent is persisted BEFORE ``bsub``** — ``submit_state="intent"``
   with ``submission_id`` / owner token / wall-clock lease is durable, and the
   intent event precedes the LSF submission (manager ``_persist_submit_intent``).
2. **Scheduler markers upload before ``bsub``** — ``job.json``/``task.json``
   exist in the remote job dir when ``bsub`` runs (remote ``runner.py``).
3. **Directory identity is stable across attempts** — the single persisted
   ``remote_dir`` path key + ``remote.relative`` metadata stay in sync
   (``paths.py``).
4. **Release binding lands BEFORE ``bsub``** — ``code_release`` is persisted
   with the submit-intent write (remote ``runner.py``).
5. **Unknown-submission reconciliation** — a valid submit lease never yields
   ``not_accepted``; ``not_accepted`` needs the full positive-absence evidence
   set; multi-match/unreadable stay ``unknown`` (``submission.py``).
6. **Old in-flight release adoption** — a prior/continue release is *verified*
   and never re-created; a missing prior release is an explicit rejection, never
   a silent upgrade (``release.py::verify_existing_release``).
7. **Stale attempt/submission/release receipts are rejected via the envelope**
   — ``verify_receipt`` rejects forgery/staleness and the CAS port is untouched.
8. **Physical write protection** — a new attempt never lets an unconfirmed old
   process's receipts finalise it: stale receipts are re-checked by attempt and
   old-attempt receipts are archived out of the execution namespace BEFORE the
   new ``bsub`` (remote + local).
9. **No duplicate ``bsub``** — reconcile/adoption never re-runs LSF submission.

Run with: PYTHONPATH=src python3.11 -m pytest tests/test_services_submission_fidelity.py -q
"""

from __future__ import annotations

import dataclasses
import json
import posixpath
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.manager import JobManager
from acp.scheduler.remote import ssh as ssh_mod
from acp.scheduler.remote.config import RemoteExecutionConfig
from acp.scheduler.remote.paths import (
    compose_remote_dir,
    resolve_remote_dir,
    storage_relative_path,
)
from acp.scheduler.remote.release import build_release_manifest
from acp.scheduler.remote.runner import (
    RemoteJobRunner,
    RemoteSubmissionIndeterminate,
    RemoteSubmissionRejected,
)
from acp.scheduler.remote.sftp import FileStager
from acp.scheduler.remote.ssh import SSHConnectionPool
from acp.scheduler.remote.submission import (
    submission_id_for,
)
from acp_services.contracts.envelope import ExecutionEnvelope, expected_submission_id
from acp_services.runtime.receipts import (
    ReceiptRejectedError,
    advance_job_state,
    verify_receipt,
)
from acp_services.runtime.worker import TaskExecutionOutcome, build_receipt
from tests.test_remote_code_release_binding import (
    _Harness,
    _make_project_tree,
    _patch_project_root,
    _seed_release,
)
from tests.test_remote_phase2 import FakeSFTP, FakeSSHClient, make_node
from tests.test_remote_submission_protocol import (
    _event_types,
    _future_iso,
    _make_manager,
    _past_iso,
    _remote_meta,
    _RunnerHarness,
)

try:
    import paramiko  # noqa: F401

    REMOTE_AVAILABLE = True
except ImportError:  # pragma: no cover
    REMOTE_AVAILABLE = False

requires_remote = pytest.mark.skipif(not REMOTE_AVAILABLE, reason="paramiko not installed")


@contextmanager
def _connected(harness: _RunnerHarness):
    """Route every SSH command of *harness* through its fake client."""
    with patch.object(ssh_mod, "_create_client", side_effect=lambda n, timeout=30: harness.client):
        yield


def _seed_manager_job(mgr: JobManager, tmp_path: Path, job_id: str) -> JobRecord:
    """Seed a QUEUED Confsearch job WITH an input so materialisation succeeds."""
    work_dir = tmp_path / "runs" / job_id
    work_dir.mkdir(parents=True, exist_ok=True)
    record = JobRecord(
        id=job_id,
        spec=JobSpec(
            workflow="Confsearch",
            input={"source": "CCO", "source_type": "smiles"},
            method={"protocol": "xtb-crest"},
        ),
        status=JobStatus.QUEUED,
        work_dir=str(work_dir),
    )
    mgr.store.create(record)
    return record


# --------------------------------------------------------------------------- #
# 1. Submit intent is persisted BEFORE bsub
# --------------------------------------------------------------------------- #


@requires_remote
def test_submit_intent_persisted_before_bsub(tmp_path, monkeypatch):
    """The DB must already carry the intent when ``bsub`` runs (id + lease)."""
    mgr = _make_manager(tmp_path, monkeypatch)
    try:
        node = mgr._remote_config.get_node("compute-01")
        assert node is not None
        config = RemoteExecutionConfig(execution_mode="remote", auto_sync=False, nodes=[node])
        pool = SSHConnectionPool()
        sftp = FakeSFTP()
        client = FakeSSHClient(sftp)
        seen: dict = {}

        def cmd_handler(cmd):
            if "bsub" in cmd and "<" in cmd:
                stored = mgr.store.get("intentjob")
                meta = dict((stored.result or {}).get("remote") or {}) if stored else {}
                seen["intent_at_bsub"] = meta
                return (0, "Job <5150> is submitted to queue <normal>.\n", "")
            return (0, "", "")

        client.cmd_handler = cmd_handler
        # auto_sync=False + explicit dev escape hatch keeps the release step green.
        monkeypatch.setenv("ACP_REMOTE_ALLOW_UNVERSIONED", "1")
        runner = RemoteJobRunner(pool, config, stager=FileStager(pool), poll_interval=0)
        mgr.remote_runner = runner  # type: ignore[assignment]
        _seed_manager_job(mgr, tmp_path, "intentjob")

        with patch.object(ssh_mod, "_create_client", side_effect=lambda n, timeout=30: client):
            assert mgr._submit_job("intentjob") is True

        intent = seen["intent_at_bsub"]
        assert intent.get("submit_state") == "intent", "intent must be durable before bsub"
        assert intent.get("submission_id") == submission_id_for("intentjob", 1)
        assert intent.get("submit_owner"), "owner token must be part of the intent"
        assert intent.get("lease_expires_at"), "wall-clock lease must be part of the intent"
        assert intent.get("intent_at")

        # The intent event is written before submission; the accepted id converges.
        types = _event_types(mgr, "intentjob")
        assert "remote.submit_intent" in types
        stored = mgr.store.get("intentjob")
        assert stored is not None
        assert stored.status == JobStatus.PENDING
        assert _remote_meta(mgr, "intentjob").get("submit_state") == "submitted"
        assert stored.remote_job_id == "5150"
        pool.close()
    finally:
        mgr.shutdown()


# --------------------------------------------------------------------------- #
# 2. Scheduler markers upload before bsub
# --------------------------------------------------------------------------- #


@requires_remote
def test_scheduler_markers_uploaded_before_bsub(tmp_path, monkeypatch):
    """``job.json``/``task.json`` must be on the node when ``bsub`` runs."""
    tree = tmp_path / "tree"
    _make_project_tree(tree)
    _patch_project_root(monkeypatch, tree)

    holder: dict = {}

    def listener(cmd: str) -> None:
        remote_dir = holder["remote_dir"]
        holder["markers_at_bsub"] = all(
            posixpath.join(remote_dir, name) in harness.sftp.files
            for name in ("job.json", "task.json")
        )

    harness = _Harness(tmp_path, monkeypatch, auto_sync=True, bsub_listener=listener)
    try:
        record, event_log, remote_dir = harness.make_record("markers")
        holder["remote_dir"] = remote_dir
        harness.submit(record, event_log)

        assert holder.get("markers_at_bsub") is True, "markers must precede bsub"
        assert "remote.markers_uploaded" in harness.event_types(event_log)
        for name in ("job.json", "task.json"):
            assert posixpath.join(remote_dir, name) in harness.sftp.files
    finally:
        harness.close()


# --------------------------------------------------------------------------- #
# 3. Directory identity is stable across attempts
# --------------------------------------------------------------------------- #


def _local_record(tmp_path: Path, job_id: str, task_leaf: str) -> tuple[JobRecord, Path]:
    run_root = tmp_path / "runs"
    work_dir = run_root / "projA" / task_leaf
    work_dir.mkdir(parents=True, exist_ok=True)
    record = JobRecord(
        id=job_id,
        spec=JobSpec(workflow="Confsearch", molecule_name="mol"),
        work_dir=str(work_dir),
    )
    return record, run_root


def test_directory_identity_is_stable_and_keeps_keys_in_sync(tmp_path):
    """One persisted path key, stable across repeated resolution (rerun/continue)."""
    node = make_node()
    record, run_root = _local_record(tmp_path, "dirjob", "mol_opt_remark__02")

    rel = storage_relative_path(record, run_root)
    assert rel == "projA/mol_opt_remark__02", "identity keeps the project + __NN leaves"
    remote_dir = compose_remote_dir(rel, node)

    # Persisted mapping (steps 1-3): explicit then stored resolve to the same dir.
    first, fallback_first = resolve_remote_dir(record, node, explicit=remote_dir, run_root=run_root)
    assert (first, fallback_first) == (remote_dir, False)

    record.result = {
        "remote_dir": remote_dir,
        "remote": {"schema": 1, "relative": rel},
    }
    second, fallback_second = resolve_remote_dir(record, node, run_root=run_root)
    assert (second, fallback_second) == (remote_dir, False)
    assert record.result["remote_dir"] == remote_dir
    assert record.result["remote"]["relative"] == rel


def test_directory_identity_derives_and_syncs_relative_key(tmp_path):
    """``remote.relative`` alone composes the path and writes ``remote_dir`` back."""
    node = make_node()
    record, run_root = _local_record(tmp_path, "reljob", "mol_SP")
    record.result = {"remote": {"schema": 1, "relative": "projA/mol_SP"}}

    path, fallback = resolve_remote_dir(record, node, run_root=run_root)
    assert path == posixpath.join(node.remote_work_dir, "projA/mol_SP")
    assert fallback is False
    assert record.result["remote_dir"] == path
    assert record.result["remote"]["path_source"] == "stored"


def test_directory_identity_ownership_probe_adopts_derived_dir(tmp_path):
    """An owned new-style candidate is adopted (never a delete/rename)."""
    node = make_node()
    record, run_root = _local_record(tmp_path, "probejob", "mol_opt")
    record.result = {}
    derived = compose_remote_dir("projA/mol_opt", node)

    path, fallback = resolve_remote_dir(
        record,
        node,
        probe=lambda candidate: candidate == derived,
        run_root=run_root,
    )
    assert (path, fallback) == (derived, False)
    assert record.result["remote"]["path_source"] == "derived"
    assert record.result["remote_dir"] == derived


# --------------------------------------------------------------------------- #
# 4. Release binding lands before bsub
# --------------------------------------------------------------------------- #


@requires_remote
def test_release_bound_before_bsub(tmp_path, monkeypatch):
    """``code_release`` is visible on the record when ``bsub`` runs."""
    tree = tmp_path / "tree"
    _make_project_tree(tree)
    release = build_release_manifest(tree).release_id
    _patch_project_root(monkeypatch, tree)

    holder: dict = {}

    def listener(cmd: str) -> None:
        record = holder["record"]
        holder["release_at_bsub"] = ((record.result or {}).get("remote") or {}).get("code_release")

    harness = _Harness(tmp_path, monkeypatch, auto_sync=True, bsub_listener=listener)
    try:
        record, event_log, remote_dir = harness.make_record("bindpre")
        holder["record"] = record
        harness.submit(record, event_log)

        assert holder.get("release_at_bsub") == release, "bind must precede bsub"
        assert record.result["remote"]["code_release"] == release
        assert f"releases/{release}/src" in harness.script_of(remote_dir)
    finally:
        harness.close()


# --------------------------------------------------------------------------- #
# 5. Unknown-submission reconciliation matrix
# --------------------------------------------------------------------------- #


def _bjobs_name_table(name: str, rows: list[str]) -> str:
    header = "JOBID   USER    STAT  QUEUE      JOBNAME          SUBMIT_TIME\n"
    body = "".join(f"{jid}   qc      RUN   normal     {name}   Thu Oct  1\n" for jid in rows)
    return header + body


def test_unknown_submission_valid_lease_never_not_accepted(tmp_path):
    harness = _RunnerHarness(str(tmp_path))
    try:
        record, _ev, _rd = harness.make_record("unknown-lease", lease_expires_at=_future_iso())
        name = f"acp_{submission_id_for('unknown-lease', 1)}"
        harness.query_handler = lambda cmd: (
            (0, f"No matching job found for {name}\n", "") if " -J " in cmd else None
        )
        verdict = harness.reconcile(record)
        assert verdict == "unknown", "a valid lease must never be judged not_accepted"
        assert harness.bsub_calls == 0
        assert harness.removes == []
    finally:
        harness.close()


def test_unknown_submission_expired_lease_full_evidence_not_accepted(tmp_path):
    harness = _RunnerHarness(str(tmp_path))
    try:
        record, _ev, remote_dir = harness.make_record("unknown-full", lease_expires_at=_past_iso())
        harness.sftp.dirs.add(remote_dir)
        name = f"acp_{submission_id_for('unknown-full', 1)}"
        harness.query_handler = lambda cmd: (
            (0, f"No matching job found for {name}\n", "") if " -J " in cmd else None
        )
        verdict = harness.reconcile(record)
        assert verdict == "not_accepted"
        assert harness.removes == [], "not_accepted must never delete the directory"
        assert remote_dir in harness.sftp.dirs
    finally:
        harness.close()


def test_unknown_submission_run_evidence_stays_unknown(tmp_path):
    harness = _RunnerHarness(str(tmp_path))
    try:
        record, _ev, remote_dir = harness.make_record("unknown-run", lease_expires_at=_past_iso())
        harness.sftp.files[posixpath.join(remote_dir, ".exit_code")] = b"0"
        name = f"acp_{submission_id_for('unknown-run', 1)}"
        harness.query_handler = lambda cmd: (
            (0, f"No matching job found for {name}\n", "") if " -J " in cmd else None
        )
        assert harness.reconcile(record) == "unknown"
    finally:
        harness.close()


def test_unknown_submission_multiple_matches_unknown(tmp_path):
    harness = _RunnerHarness(str(tmp_path))
    try:
        record, _ev, _rd = harness.make_record("unknown-multi")
        name = f"acp_{submission_id_for('unknown-multi', 1)}"
        harness.query_handler = lambda cmd: (
            (0, _bjobs_name_table(name, ["101", "202"]), "") if " -J " in cmd else None
        )
        assert harness.reconcile(record) == "unknown"
        assert record.remote_job_id is None
    finally:
        harness.close()


def test_unknown_submission_single_owned_match_adopts_without_bsub(tmp_path):
    harness = _RunnerHarness(str(tmp_path))
    try:
        record, _ev, remote_dir = harness.make_record("unknown-one")
        harness.sftp.files[posixpath.join(remote_dir, "job.json")] = json.dumps(
            {"id": record.id, "attempt": 1}
        ).encode("utf-8")
        name = f"acp_{submission_id_for('unknown-one', 1)}"
        harness.query_handler = lambda cmd: (
            (0, _bjobs_name_table(name, ["777"]), "") if " -J " in cmd else None
        )
        assert harness.reconcile(record) == "found"
        assert record.remote_job_id == "777"
        assert harness.bsub_calls == 0, "reconcile/adoption must never re-run bsub"
    finally:
        harness.close()


# --------------------------------------------------------------------------- #
# 6. Old in-flight release adoption
# --------------------------------------------------------------------------- #

_RELEASE_FILES = {"src/acp/__init__.py": b'"""pinned"""\n'}


@requires_remote
def test_prior_release_reused_verified_not_recreated(tmp_path, monkeypatch):
    """A continue/rerun release is verified and reused — never re-published."""
    release = "0d0e0fa11ce0000a"
    harness = _Harness(tmp_path, monkeypatch, auto_sync=True)
    try:
        _seed_release(harness.sftp, harness.node, release, _RELEASE_FILES)
        before = set(harness.release_dirs(harness.node))

        record, event_log, _remote_dir = harness.make_record(
            "reuse", remote_meta={"code_release": release, "attempt": 1}
        )
        harness.submit(record, event_log)

        assert record.result["remote"]["code_release"] == release
        ready = [e for e in event_log.read_all() if e.get("type") == "remote.code_release_ready"]
        assert ready and ready[0]["source"] == "reused"
        assert harness.release_dirs(harness.node) == before, "no new release published"
        assert harness.bsub_calls == 1
    finally:
        harness.close()


@requires_remote
def test_missing_prior_release_rejected_never_silently_upgraded(tmp_path, monkeypatch):
    harness = _Harness(tmp_path, monkeypatch, auto_sync=True)
    try:
        record, event_log, _remote_dir = harness.make_record(
            "gone", remote_meta={"code_release": "deadbeefdeadbeef", "attempt": 1}
        )
        with pytest.raises(RemoteSubmissionRejected) as excinfo:
            harness.submit(record, event_log)
        assert "refusing to upgrade" in str(excinfo.value)
        assert harness.bsub_calls == 0, "a missing release must fail closed"
    finally:
        harness.close()


# --------------------------------------------------------------------------- #
# 7. Stale attempt/submission/release receipts rejected via the envelope
# --------------------------------------------------------------------------- #

_JOB = "fid-job"
_RELEASE = "rel-fid"
_OWNER = "owner-fid"
_FENCE = "fence-fid"


def _envelope(attempt: int = 1, **overrides: object) -> ExecutionEnvelope:
    kwargs: dict = {
        "envelope_version": 1,
        "job_id": _JOB,
        "attempt": attempt,
        "submission_id": expected_submission_id(_JOB, attempt),
        "release_id": _RELEASE,
        "release_digest": "reldig-fid",
        "owner": _OWNER,
        "fencing_token": _FENCE,
        "plan_digest": "v2:plan-digest",
        "plan_identity": "v2:plan-identity",
        "step_identities": ("v2:step-0",),
        "entrypoint": "worker.bootstrap",
        "protocol": "local",
    }
    kwargs.update(overrides)
    return ExecutionEnvelope(**kwargs)


class _AdvanceSpy:
    def __init__(self) -> None:
        self.calls = 0

    def advance(self, *args: object, **kwargs: object) -> str:
        self.calls += 1
        return "advanced"


def _verdict(receipt, envelope, **current):
    defaults = dict(
        current_attempt=envelope.attempt,
        current_submission_id=envelope.submission_id,
        current_release_id=envelope.release_id,
        owner_token=_OWNER,
        fencing_token=_FENCE,
    )
    defaults.update(current)
    return verify_receipt(receipt, envelope, **defaults)


def test_stale_receipt_matrix_rejected_via_envelope():
    """Each staleness/forgery dimension maps to a stable verdict code."""
    envelope = _envelope()
    receipt = build_receipt(
        envelope, TaskExecutionOutcome(state="completed", result_digest="sha256:r")
    )

    # Baseline: the matching receipt is accepted.
    assert _verdict(receipt, envelope).accepted is True

    # job mismatch — a receipt from a different job.
    other_job = _envelope(job_id="other-job")
    assert _verdict(receipt, other_job).code == "job_mismatch"

    # stale attempt — receipt attempt is not current/envelope.
    assert _verdict(receipt, envelope, current_attempt=2).code == "stale_attempt"

    # forged submission — not the deterministic derivation.
    forged = dataclasses.replace(receipt, submission_id="sub_deadbeefdeadbeef")
    assert _verdict(forged, envelope).code == "forged_submission"

    # stale submission — derived but no longer the current one.
    assert (
        _verdict(receipt, envelope, current_submission_id="sub_somebodyelse").code
        == "stale_submission"
    )

    # stale release — the binding moved on.
    stale_release = dataclasses.replace(receipt, release_id="rel-other")
    assert _verdict(stale_release, envelope).code == "stale_release"

    # wrong owner / fencing.
    assert _verdict(dataclasses.replace(receipt, owner="bad"), envelope).code == "wrong_owner"
    assert (
        _verdict(dataclasses.replace(receipt, fencing_token="bad"), envelope).code
        == "wrong_fencing"
    )

    # digest mismatch — plan/step/release digest disagrees with the envelope.
    assert (
        _verdict(dataclasses.replace(receipt, plan_identity="v2:other"), envelope).code
        == "digest_mismatch"
    )


def test_rejected_receipt_never_advances_state():
    """A stale receipt raises and the CAS port is never touched."""
    envelope = _envelope()
    stale = build_receipt(
        _envelope(attempt=1), TaskExecutionOutcome(state="completed", result_digest="sha256:r")
    )
    port = _AdvanceSpy()
    with pytest.raises(ReceiptRejectedError) as excinfo:
        advance_job_state(
            port,
            envelope,
            stale,
            current_attempt=2,
            current_submission_id=expected_submission_id(_JOB, 2),
            current_release_id=_RELEASE,
            owner_token=_OWNER,
            fencing_token=_FENCE,
            expected_revision=0,
        )
    assert excinfo.value.verdict.code == "stale_attempt"
    assert port.calls == 0, "state must be unchanged on rejection"


def test_matching_receipt_advances_exactly_once():
    envelope = _envelope()
    receipt = build_receipt(
        envelope, TaskExecutionOutcome(state="completed", result_digest="sha256:r")
    )
    port = _AdvanceSpy()
    assert (
        advance_job_state(
            port,
            envelope,
            receipt,
            current_attempt=1,
            current_submission_id=expected_submission_id(_JOB, 1),
            current_release_id=_RELEASE,
            owner_token=_OWNER,
            fencing_token=_FENCE,
            expected_revision=0,
        )
        == "advanced"
    )
    assert port.calls == 1


# --------------------------------------------------------------------------- #
# 8. Physical write protection (stale output isolation / generation re-check)
# --------------------------------------------------------------------------- #


@requires_remote
def test_receipt_is_current_rejects_older_declared_attempt(tmp_path):
    """A receipt declaring another attempt must never drive finalisation."""
    harness = _RunnerHarness(str(tmp_path))
    try:
        record, _ev, remote_dir = harness.make_record("gen-current")
        node = harness.node

        with _connected(harness):
            # Undeclared legacy receipt stays current.
            harness.sftp.files[posixpath.join(remote_dir, "state.json")] = b"{}"
            assert harness.runner._receipt_is_current(node, remote_dir, 2) is True

            # Declared older attempt → stale.
            harness.sftp.files[posixpath.join(remote_dir, "state.json")] = json.dumps(
                {"attempt": 1}
            ).encode("utf-8")
            assert harness.runner._receipt_is_current(node, remote_dir, 2) is False

            # Declared current attempt → current.
            harness.sftp.files[posixpath.join(remote_dir, "state.json")] = json.dumps(
                {"attempt": 2}
            ).encode("utf-8")
            assert harness.runner._receipt_is_current(node, remote_dir, 2) is True
    finally:
        harness.close()


@requires_remote
def test_stale_exit_code_never_finalises_new_attempt(tmp_path):
    """``_wait_exit_code`` with an attempt guard ignores an old attempt's exit."""
    harness = _RunnerHarness(str(tmp_path))
    try:
        record, _ev, remote_dir = harness.make_record("gen-wait")
        node = harness.node
        harness.sftp.files[posixpath.join(remote_dir, "state.json")] = json.dumps(
            {"attempt": 1}
        ).encode("utf-8")

        with (
            _connected(harness),
            patch.object(harness.runner._monitor, "get_exit_code", return_value=0),
            patch("acp.scheduler.remote.runner.time.sleep", lambda _s: None),
        ):
            assert (
                harness.runner._wait_exit_code(node, remote_dir, timeout=0.02, attempt=2) is None
            ), "a stale .exit_code must not finalise the new attempt"
            harness.sftp.files[posixpath.join(remote_dir, "state.json")] = json.dumps(
                {"attempt": 2}
            ).encode("utf-8")
            assert harness.runner._wait_exit_code(node, remote_dir, timeout=0.02, attempt=2) == 0
    finally:
        harness.close()


@requires_remote
def test_old_attempt_receipts_archived_before_new_submission(tmp_path):
    """Old-attempt receipts leave the execution namespace before root files."""
    harness = _RunnerHarness(str(tmp_path))
    try:
        record, event_log, remote_dir = harness.make_record("arch-remote")
        record.attempt = 2
        node = harness.node
        for name in (".exit_code", "state.json"):
            harness.sftp.files[posixpath.join(remote_dir, name)] = b"x"
        harness.sftp.dirs.add(remote_dir)

        with _connected(harness):
            harness.runner._archive_remote_attempt(
                node, remote_dir, record, event_log, disposition="reused"
            )

        archive = posixpath.join(remote_dir, "WORK", "00_RUNTIME", "attempts", "1")
        for name in (".exit_code", "state.json"):
            assert posixpath.join(archive, name) in harness.sftp.files
            assert posixpath.join(remote_dir, name) not in harness.sftp.files
        assert "remote.attempts_archived" in [e.get("type") for e in event_log.read_all()]
    finally:
        harness.close()


def test_local_attempt_receipts_archived_and_never_clobbered(tmp_path, monkeypatch):
    """Local rerun archives the closed attempt's receipts; an existing target blocks."""
    mgr = _make_manager(tmp_path, monkeypatch)
    try:
        work_dir = tmp_path / "runs" / "localarch"
        work_dir.mkdir(parents=True)
        (work_dir / ".exit_code").write_text("0", encoding="utf-8")
        (work_dir / "state.json").write_text("{}", encoding="utf-8")
        (work_dir / "checkpoint.json").write_text("{}", encoding="utf-8")
        record = JobRecord(
            id="localarch",
            spec=JobSpec(workflow="Confsearch", molecule_name="mol"),
            work_dir=str(work_dir),
            attempt=2,
        )

        mgr._archive_attempt_receipts(record, 1, include_science=True)

        archive = work_dir / "WORK" / "00_RUNTIME" / "attempts" / "1"
        assert (archive / ".exit_code").is_file()
        assert (archive / "state.json").is_file()
        assert (archive / "checkpoint.json").is_file()
        assert not (work_dir / ".exit_code").exists()

        # Never clobber: a fresh receipt with the archive target present blocks.
        (work_dir / ".exit_code").write_text("1", encoding="utf-8")
        with pytest.raises(RuntimeError):
            mgr._archive_attempt_receipts(record, 1, include_science=True)
    finally:
        mgr.shutdown()


# --------------------------------------------------------------------------- #
# 9. No duplicate bsub
# --------------------------------------------------------------------------- #


@requires_remote
def test_lost_bsub_reply_reconciles_without_second_bsub(tmp_path):
    """An indeterminate reply is adopted by reconcile — exactly one bsub."""
    harness = _RunnerHarness(str(tmp_path), bsub_result=(0, "no job id here\n", ""))
    try:
        record, event_log, _remote_dir = harness.make_record("nodup")
        with patch.object(
            ssh_mod, "_create_client", side_effect=lambda n, timeout=30: harness.client
        ):
            with pytest.raises(RemoteSubmissionIndeterminate):
                harness.runner.submit_remote(record, event_log)

        name = f"acp_{submission_id_for('nodup', 1)}"
        harness.query_handler = lambda cmd: (
            (0, _bjobs_name_table(name, ["909"]), "") if " -J " in cmd else None
        )
        assert harness.reconcile(record) == "found"
        assert harness.bsub_calls == 1, "reconcile must never re-run bsub"
    finally:
        harness.close()


@requires_remote
def test_continue_remote_archives_frozen_group_protocol_claim_and_receipts(tmp_path):
    """The remote adopt_results=True branch archives every attempt-scoped file.

    Science (checkpoint/RESULT) stays in place for adoption, but the old
    protocol record, frozen group, claim, worker receipt and task_result must
    leave the execution namespace before the new attempt's bsub.
    """
    harness = _RunnerHarness(str(tmp_path))
    try:
        record, event_log, remote_dir = harness.make_record("arch-continue")
        record.attempt = 2
        meta = dict((record.result or {}).get("remote") or {})
        meta["resume"] = True
        record.result = {**(record.result or {}), "remote": meta}
        node = harness.node
        runtime = posixpath.join(remote_dir, "WORK", "00_RUNTIME")
        frozen = posixpath.join(runtime, "frozen")
        seeded = (
            "execution_receipt.json",
            ".exit_code",
            "state.json",
            posixpath.join("WORK", "00_RUNTIME", "execution_protocol.json"),
            posixpath.join("WORK", "00_RUNTIME", "attempt.lock"),
            posixpath.join("WORK", "00_RUNTIME", "task_result.json"),
            posixpath.join("WORK", "00_RUNTIME", "run.lock"),
            posixpath.join("WORK", "00_RUNTIME", "frozen", "envelope.json"),
            posixpath.join("WORK", "00_RUNTIME", "frozen", "plan.json"),
            posixpath.join("WORK", "00_RUNTIME", "frozen", "binding.json"),
            posixpath.join("WORK", "00_RUNTIME", "frozen", "frozen_group.json"),
        )
        for rel in seeded:
            harness.sftp.files[posixpath.join(remote_dir, rel)] = b"x"
        for directory in (
            remote_dir,
            posixpath.join(remote_dir, "WORK"),
            runtime,
            frozen,
            posixpath.join(remote_dir, "RESULT"),
        ):
            harness.sftp.dirs.add(directory)
        checkpoint = posixpath.join("checkpoint.json")
        manifest = posixpath.join("RESULT", "result_manifest.json")
        harness.sftp.files[posixpath.join(remote_dir, checkpoint)] = b"{}"
        harness.sftp.files[posixpath.join(remote_dir, manifest)] = b"{}"

        with _connected(harness):
            harness.runner._archive_remote_attempt(
                node, remote_dir, record, event_log, disposition="reused"
            )

        archive = posixpath.join(remote_dir, "WORK", "00_RUNTIME", "attempts", "1")
        for rel in seeded:
            assert posixpath.join(archive, rel) in harness.sftp.files, rel
            assert posixpath.join(remote_dir, rel) not in harness.sftp.files, rel
        assert posixpath.join(remote_dir, checkpoint) in harness.sftp.files
        assert posixpath.join(remote_dir, manifest) in harness.sftp.files
        event = [
            item for item in event_log.read_all() if item.get("type") == "remote.attempts_archived"
        ]
        assert event and event[-1].get("adopted_results") is True
    finally:
        harness.close()
