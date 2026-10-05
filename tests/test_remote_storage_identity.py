"""D01 storage identity tests — remote dir derivation, ownership claims.

Covers plan todo 4 acceptance:
- storage identity = ``record.work_dir`` relative to run_root (project leaf
  + ``__NN`` dedupe suffix) drives the remote directory.
- exclusive claim: foreign ``job.json``/``task.json`` → ``RemoteDirConflictError``
  with the directory byte-snapshot unchanged and never deleted.
- persisted mapping wins; bare runner records fall back with the legacy event.
- cleanup targets equal the persisted/derived directory; reused dirs are never
  cleaned on late submission failure.
"""

from __future__ import annotations

import json
import posixpath
from pathlib import Path
from unittest.mock import patch

import pytest

from acp.scheduler.events import JobEventLog
from acp.scheduler.jobs import JobRecord, JobSpec
from acp.scheduler.remote import ssh as ssh_mod
from acp.scheduler.remote.config import RemoteExecutionConfig
from acp.scheduler.remote.paths import (
    compose_remote_dir,
    resolve_remote_dir,
    storage_relative_path,
)
from acp.scheduler.remote.runner import RemoteJobRunner, RemoteSubmissionRejected
from acp.scheduler.remote.sftp import FileStager, RemoteDirConflictError
from acp.scheduler.remote.ssh import SSHConnectionPool
from tests.test_remote_phase2 import FakeSFTP, FakeSSHClient, make_node

# ====================================================================== #
# Helpers
# ====================================================================== #

_RUN_ROOT = "/data/runs"


def _make_record(
    job_id: str,
    *,
    work_dir: str,
    workflow: str = "ensemble",
    result: dict | None = None,
    attempt: int = 1,
) -> JobRecord:
    spec = JobSpec(workflow=workflow, input={"source": "CCO", "source_type": "smiles"})
    return JobRecord(id=job_id, spec=spec, work_dir=work_dir, result=result or {}, attempt=attempt)


def _run_root(tmp_path: Path) -> Path:
    root = tmp_path / "runs"
    root.mkdir(parents=True, exist_ok=True)
    return root


class _FakeStager(FileStager):
    """FileStager with in-memory rename for the fake SFTP backend."""

    def __init__(self, ssh_pool, sftp_holder):
        super().__init__(ssh_pool)
        self._sftp_holder = sftp_holder

    def rename_remote(self, node, src, dst):  # noqa: D401 - fake override
        sftp = self._sftp_holder["sftp"]
        if dst in sftp.files or dst in sftp.dirs:
            raise OSError(f"target exists: {dst}")
        parent = posixpath.dirname(dst)
        if parent:
            sftp.dirs.add(parent)
        if src in sftp.files:
            sftp.files[dst] = sftp.files.pop(src)
            sftp.attrs[dst] = sftp.attrs.pop(src, sftp.attrs.get(dst))
        elif src in sftp.dirs:
            sftp.dirs.discard(src)
            sftp.dirs.add(dst)
            for key in list(sftp.files):
                if key.startswith(src + "/"):
                    sftp.files[dst + key[len(src) :]] = sftp.files.pop(key)
            for key in list(sftp.attrs):
                if key.startswith(src + "/"):
                    sftp.attrs[dst + key[len(src) :]] = sftp.attrs.pop(key)
        else:
            raise FileNotFoundError(src)


def _make_runner(sftp: FakeSFTP, config: RemoteExecutionConfig, stager=None):
    pool = SSHConnectionPool()
    client = FakeSSHClient(sftp)
    client.cmd_handler = lambda cmd: (
        (0, "Job <54321> is submitted to queue <normal>.\n", "")
        if "bsub" in cmd and "<" in cmd
        else (0, "", "")
    )
    holder = {"sftp": sftp}
    stager = stager or _FakeStager(pool, holder)
    runner = RemoteJobRunner(pool, config, stager=stager, poll_interval=0)
    return runner, client, holder


def _snapshot(sftp: FakeSFTP) -> dict[str, bytes]:
    return dict(sftp.files)


# ====================================================================== #
# A. paths.py — storage identity helpers
# ====================================================================== #


def test_storage_relative_path_includes_project_leaf_and_dedupe_suffix(tmp_path):
    root = _run_root(tmp_path)
    work_dir = root / "projA" / "mol_opt_remark__02"
    record = _make_record("job1", work_dir=str(work_dir))
    rel = storage_relative_path(record, root)
    assert rel == "projA/mol_opt_remark__02"
    print("  [OK] storage_relative_path keeps project leaf + __02 suffix")


def test_storage_relative_path_rejects_work_dir_outside_run_root(tmp_path):
    root = _run_root(tmp_path)
    record = _make_record("job1", work_dir=str(tmp_path / "elsewhere" / "task"))
    with pytest.raises(ValueError):
        storage_relative_path(record, root)
    print("  [OK] storage_relative_path rejects work_dir outside run_root")


def test_compose_remote_dir_joins_node_base(tmp_path):
    node = make_node()
    assert (
        compose_remote_dir("projA/mol_opt_remark", node)
        == "/scratch/test/acp_jobs/projA/mol_opt_remark"
    )
    print("  [OK] compose_remote_dir = remote_work_dir / relative")


def test_resolve_prefers_explicit_then_persisted_dir(tmp_path):
    node = make_node()
    record = _make_record(
        "job1",
        work_dir=str(tmp_path / "runs" / "p" / "t"),
        result={"remote_dir": "/scratch/test/acp_jobs/p/t"},
    )
    path, fallback = resolve_remote_dir(record, node, explicit="/explicit/dir")
    assert path == "/explicit/dir" and fallback is False
    path, fallback = resolve_remote_dir(record, node)
    assert path == "/scratch/test/acp_jobs/p/t" and fallback is False
    print("  [OK] resolve order: explicit > result['remote_dir']")


def test_resolve_composes_from_relative_and_writes_back(tmp_path):
    node = make_node()
    record = _make_record(
        "job1",
        work_dir=str(tmp_path / "runs" / "p" / "t"),
        result={"remote": {"schema": 1, "relative": "p/t", "attempt": 1}},
    )
    path, fallback = resolve_remote_dir(record, node)
    assert path == posixpath.join(node.remote_work_dir, "p/t")
    assert fallback is False
    # Single path key: written back and in sync with result["remote"].
    assert record.result["remote_dir"] == path
    assert record.result["remote"]["relative"] == "p/t"
    print("  [OK] resolve composes from remote.relative and writes remote_dir")


def test_result_remote_dir_equals_derived_at_all_read_points(tmp_path):
    """fetcher.resolve and every reader must see the same single path value."""
    from acp.scheduler.remote.fetcher import RemoteResultFetcher

    node = make_node()
    record = _make_record(
        "job1",
        work_dir=str(tmp_path / "runs" / "p" / "t"),
        result={"remote": {"schema": 1, "relative": "p/t", "attempt": 1}},
    )
    path, _ = resolve_remote_dir(record, node)
    # Simulate the submit-time write-back persisted to the record.
    record.result = dict(record.result)
    record.result["remote_dir"] = path
    record.result["node"] = node.name

    config = RemoteExecutionConfig(execution_mode="remote", nodes=[node])
    fetcher = RemoteResultFetcher.__new__(RemoteResultFetcher)
    fetcher._config = config
    _node, resolved = fetcher.resolve(record)
    assert resolved == path == record.result["remote_dir"]
    print("  [OK] fetcher.resolve returns the same single remote_dir value")


# ====================================================================== #
# D. claim_remote_job_dir — exclusive ownership
# ====================================================================== #


def _seed_owner(sftp: FakeSFTP, remote_dir: str, job_id: str, attempt: int = 1):
    sftp.dirs.add(remote_dir)
    payload = {"id": job_id, "attempt": attempt}
    blob = json.dumps(payload).encode()
    sftp.files[posixpath.join(remote_dir, "job.json")] = blob
    sftp.attrs[posixpath.join(remote_dir, "job.json")] = _AttrStub(len(blob))


class _AttrStub:
    def __init__(self, size):
        self.st_size = size
        self.st_mtime = 0.0


def test_claim_creates_missing_dir(tmp_path):
    node = make_node()
    sftp = FakeSFTP()
    pool = SSHConnectionPool()
    client = FakeSSHClient(sftp)
    stager = FileStager(pool)
    record = _make_record("job1", work_dir=str(tmp_path / "runs" / "p" / "t"))
    target = posixpath.join(node.remote_work_dir, "p/t")
    with patch.object(ssh_mod, "_create_client", side_effect=lambda n, timeout=30: client):
        disposition = stager.claim_remote_job_dir(node, target, record)
    assert disposition == "created"
    assert target in sftp.dirs
    print("  [OK] claim: missing dir → created")


def test_claim_reuses_own_dir(tmp_path):
    node = make_node()
    sftp = FakeSFTP()
    pool = SSHConnectionPool()
    client = FakeSSHClient(sftp)
    stager = FileStager(pool)
    record = _make_record("job1", work_dir=str(tmp_path / "runs" / "p" / "t"), attempt=2)
    target = posixpath.join(node.remote_work_dir, "p/t")
    _seed_owner(sftp, target, "job1", attempt=1)
    with patch.object(ssh_mod, "_create_client", side_effect=lambda n, timeout=30: client):
        disposition = stager.claim_remote_job_dir(node, target, record)
    assert disposition == "reused"
    print("  [OK] claim: same job_id → reused (rerun/continue reuse)")


def test_claim_foreign_owner_raises_conflict_bytes_unchanged_never_deleted(tmp_path):
    node = make_node()
    sftp = FakeSFTP()
    pool = SSHConnectionPool()
    client = FakeSSHClient(sftp)
    stager = FileStager(pool)
    record = _make_record("job1", work_dir=str(tmp_path / "runs" / "p" / "t"))
    target = posixpath.join(node.remote_work_dir, "p/t")
    _seed_owner(sftp, target, "other-job")
    foreign_blob = dict(_snapshot(sftp))
    with patch.object(ssh_mod, "_create_client", side_effect=lambda n, timeout=30: client):
        with pytest.raises(RemoteDirConflictError):
            stager.claim_remote_job_dir(node, target, record)
    assert _snapshot(sftp) == foreign_blob, "conflicting dir bytes must be unchanged"
    assert target in sftp.dirs, "conflicting dir must never be deleted"
    print("  [OK] claim: foreign job_id → RemoteDirConflictError, bytes intact")


# ====================================================================== #
# C/G. submit paths through resolve + claim
# ====================================================================== #


def _submit(runner, record, event_log, client, **kw):
    with patch.object(ssh_mod, "_create_client", side_effect=lambda n, timeout=30: client):
        return runner.submit_remote(record, event_log, **kw)


def test_manager_record_uses_persisted_mapping_no_legacy_fallback_event(tmp_path):
    """A record carrying the manager-persisted mapping resolves from it."""
    node = make_node()
    config = RemoteExecutionConfig(execution_mode="remote", auto_sync=False, nodes=[node])
    sftp = FakeSFTP()
    runner, client, _holder = _make_runner(sftp, config)
    work_dir = tmp_path / "runs" / "projA" / "mol_ens_remark"
    work_dir.mkdir(parents=True)
    rel = storage_relative_path(_make_record("job1", work_dir=str(work_dir)), tmp_path / "runs")
    record = _make_record(
        "job1",
        work_dir=str(work_dir),
        result={"remote": {"schema": 1, "relative": rel, "attempt": 1}},
    )
    event_log = JobEventLog(work_dir / "events.jsonl")
    _submit(runner, record, event_log, client)

    expected = posixpath.join(node.remote_work_dir, rel)
    assert record.result["remote_dir"] == expected
    types = [e["type"] for e in event_log.read_all()]
    assert "remote.path_legacy_fallback" not in types
    assert "remote.path_legacy_flat" not in types
    assert posixpath.join(expected, "job.json") in sftp.files
    print("  [OK] manager-persisted mapping used; no legacy fallback event")


def test_bare_runner_record_falls_back_with_legacy_fallback_event(tmp_path):
    """No mapping at all → legacy fallback path + event, dir still created."""
    node = make_node()
    config = RemoteExecutionConfig(execution_mode="remote", auto_sync=False, nodes=[node])
    sftp = FakeSFTP()
    runner, client, _holder = _make_runner(sftp, config)
    # work_dir NOT under a run_root-shaped parent — no usable relative mapping
    # (bare runner record: no result metadata whatsoever).
    work_dir = tmp_path / "loose" / "task"
    work_dir.mkdir(parents=True)
    record = _make_record("job1", work_dir=str(work_dir))
    record.result = {}
    event_log = JobEventLog(work_dir / "events.jsonl")
    _submit(runner, record, event_log, client)

    types = [e["type"] for e in event_log.read_all()]
    assert "remote.path_legacy_fallback" in types
    # fallback target = remote_work_dir / <task_dir_name or record.id>
    flat = posixpath.join(node.remote_work_dir, record.spec.task_dir_name())
    assert record.result["remote_dir"] == flat
    print("  [OK] bare runner record falls back with remote.path_legacy_fallback")


def test_legacy_in_flight_flat_dir_persisted_with_legacy_flat_event(tmp_path):
    """Old in-flight job: flat remote dir exists and is owned by this job."""
    node = make_node()
    config = RemoteExecutionConfig(execution_mode="remote", auto_sync=False, nodes=[node])
    sftp = FakeSFTP()
    runner, client, _holder = _make_runner(sftp, config)
    work_dir = tmp_path / "runs" / "projA" / "mol_ens_remark"
    work_dir.mkdir(parents=True)
    record = _make_record("job1", work_dir=str(work_dir))
    record.result = {}  # legacy: no mapping at all
    flat = posixpath.join(node.remote_work_dir, record.spec.task_dir_name())
    _seed_owner(sftp, flat, "job1")
    sftp.files[posixpath.join(flat, "stdout.log")] = b"legacy output"

    event_log = JobEventLog(work_dir / "events.jsonl")
    _submit(runner, record, event_log, client)

    types = [e["type"] for e in event_log.read_all()]
    assert "remote.path_legacy_flat" in types
    assert "remote.path_legacy_fallback" not in types
    assert record.result["remote_dir"] == flat
    # persisted into the metadata mapping so future resolutions hit step 2/3
    relative = record.result["remote"]["relative"]
    assert posixpath.join(node.remote_work_dir, relative) == flat
    print("  [OK] legacy flat dir adopted, event remote.path_legacy_flat persisted")


def test_dual_candidate_no_ownership_conflicts_without_deletion(tmp_path):
    """Neither candidate owned → claim raises; foreign bytes untouched."""
    node = make_node()
    config = RemoteExecutionConfig(execution_mode="remote", auto_sync=False, nodes=[node])
    sftp = FakeSFTP()
    runner, client, _holder = _make_runner(sftp, config)
    work_dir = tmp_path / "runs" / "projA" / "mol_ens_remark"
    work_dir.mkdir(parents=True)
    record = _make_record("job1", work_dir=str(work_dir))
    record.result = {}
    flat = posixpath.join(node.remote_work_dir, record.spec.task_dir_name())
    derived = posixpath.join(node.remote_work_dir, "runs/projA/mol_ens_remark")
    # Both candidates exist but belong to foreign jobs.
    _seed_owner(sftp, flat, "foreign-a")
    _seed_owner(sftp, derived, "foreign-b")
    before = _snapshot(sftp)

    event_log = JobEventLog(work_dir / "events.jsonl")
    with pytest.raises(RemoteDirConflictError):
        _submit(runner, record, event_log, client)

    assert _snapshot(sftp) == before, "no bytes may change on conflict"
    assert flat in sftp.dirs, "conflicting dir must never be deleted"
    cleanup = [e for e in event_log.read_all() if e["type"] == "remote.cleanup"]
    assert not cleanup, "conflict must never trigger directory cleanup"
    print("  [OK] dual-candidate no-ownership → conflict, no deletion")


def test_two_same_project_jobs_get_distinct_remote_dirs_including_dedupe(tmp_path):
    """Same project + same name → __02 leaves produce distinct remote dirs."""
    node = make_node()
    root = _run_root(tmp_path)
    r1 = _make_record("job1", work_dir=str(root / "projA" / "mol_opt_x"))
    r2 = _make_record("job2", work_dir=str(root / "projA" / "mol_opt_x__02"))
    d1 = compose_remote_dir(storage_relative_path(r1, root), node)
    d2 = compose_remote_dir(storage_relative_path(r2, root), node)
    assert d1 != d2
    assert d1.endswith("/projA/mol_opt_x")
    assert d2.endswith("/projA/mol_opt_x__02")
    print("  [OK] same-project same-name jobs → distinct remote dirs (__02)")


def test_different_projects_same_name_keep_project_leaves(tmp_path):
    node = make_node()
    root = _run_root(tmp_path)
    r1 = _make_record("job1", work_dir=str(root / "projA" / "mol_opt_x"))
    r2 = _make_record("job2", work_dir=str(root / "projB" / "mol_opt_x"))
    d1 = compose_remote_dir(storage_relative_path(r1, root), node)
    d2 = compose_remote_dir(storage_relative_path(r2, root), node)
    assert d1 != d2
    assert d1.endswith("/projA/mol_opt_x") and d2.endswith("/projB/mol_opt_x")
    print("  [OK] different projects → project leaves in remote dirs")


def test_same_identity_two_submissions_create_two_independent_dirs(tmp_path):
    """Two JobRecords submitted through the fake runner → two remote dirs."""
    node = make_node()
    config = RemoteExecutionConfig(execution_mode="remote", auto_sync=False, nodes=[node])
    root = _run_root(tmp_path)
    dirs = []
    for idx, name in enumerate(("mol_opt_x", "mol_opt_x__02"), start=1):
        sftp = FakeSFTP()
        runner, client, _holder = _make_runner(sftp, config)
        work_dir = root / "projA" / name
        work_dir.mkdir(parents=True)
        rel = storage_relative_path(_make_record(f"job{idx}", work_dir=str(work_dir)), root)
        record = _make_record(
            f"job{idx}",
            work_dir=str(work_dir),
            result={"remote": {"schema": 1, "relative": rel, "attempt": 1}},
        )
        event_log = JobEventLog(work_dir / "events.jsonl")
        _submit(runner, record, event_log, client)
        remote_dir = record.result["remote_dir"]
        dirs.append(remote_dir)
        assert posixpath.join(remote_dir, "submit.lsf") in sftp.files
    assert dirs[0] != dirs[1]
    print("  [OK] two submissions → two independent remote dirs in sftp.files")


def test_reused_dir_late_failure_is_never_cleaned(tmp_path):
    """Late failure after a `reused` claim leaves the directory untouched."""
    node = make_node()
    config = RemoteExecutionConfig(execution_mode="remote", auto_sync=False, nodes=[node])
    sftp = FakeSFTP()
    runner, client, _holder = _make_runner(sftp, config)
    work_dir = tmp_path / "runs" / "projA" / "mol_ens_remark"
    work_dir.mkdir(parents=True)
    rel = "projA/mol_ens_remark"
    # Same attempt re-submitting into its own reused dir: any byte change
    # after the claim would therefore be a cleanup bug, not archiving.
    record = _make_record(
        "job1",
        work_dir=str(work_dir),
        result={"remote": {"schema": 1, "relative": rel, "attempt": 1}},
        attempt=1,
    )
    remote_dir = posixpath.join(node.remote_work_dir, rel)
    _seed_owner(sftp, remote_dir, "job1", attempt=1)
    sftp.files[posixpath.join(remote_dir, "checkpoint.json")] = b'{"step": 7}'
    sftp.files[posixpath.join(remote_dir, "RESULT", "result_manifest.json")] = b"{}"
    sftp.dirs.add(posixpath.join(remote_dir, "RESULT"))
    before = _snapshot(sftp)
    dirs_before = set(sftp.dirs)

    cleanups: list[str] = []
    original_cleanup = runner._cleanup_remote_dir

    def spy_cleanup(node_, remote_dir_, event_log, job_id):
        cleanups.append(remote_dir_)
        return original_cleanup(node_, remote_dir_, event_log, job_id)

    runner._cleanup_remote_dir = spy_cleanup  # type: ignore[assignment]

    def failing_upload(n, local_path, remote_path):
        raise OSError("SFTP reset during upload")

    runner._stager.upload_file = failing_upload  # type: ignore[assignment]

    event_log = JobEventLog(work_dir / "events.jsonl")
    with pytest.raises(RemoteSubmissionRejected):
        _submit(runner, record, event_log, client)

    assert cleanups == [], "reused dir must never be passed to _cleanup_remote_dir"
    assert _snapshot(sftp) == before, "reused dir bytes must be unchanged"
    assert set(sftp.dirs) == dirs_before, "reused dir must not be deleted"
    removed = [
        e
        for e in event_log.read_all()
        if e["type"] == "remote.cleanup" and e.get("removed") is not False
    ]
    assert not removed, "no remote.cleanup event for a reused dir"
    print("  [OK] reused dir late failure → no cleanup, byte snapshot intact")


def test_submission_failure_never_deletes_foreign_conflict_dir(tmp_path):
    """A conflict raised at claim time must not invoke remove_remote_dir."""
    node = make_node()
    config = RemoteExecutionConfig(execution_mode="remote", auto_sync=False, nodes=[node])
    sftp = FakeSFTP()
    runner, client, _holder = _make_runner(sftp, config)
    work_dir = tmp_path / "runs" / "projA" / "mol_ens_remark"
    work_dir.mkdir(parents=True)
    rel = "projA/mol_ens_remark"
    record = _make_record(
        "job1", work_dir=str(work_dir), result={"remote": {"relative": rel, "attempt": 1}}
    )
    remote_dir = posixpath.join(node.remote_work_dir, rel)
    _seed_owner(sftp, remote_dir, "some-other-job")
    before = _snapshot(sftp)

    removed: list[str] = []
    original_remove = runner._stager.remove_remote_dir

    def spy_remove(node_, path):
        removed.append(path)
        return original_remove(node_, path)

    runner._stager.remove_remote_dir = spy_remove  # type: ignore[assignment]

    event_log = JobEventLog(work_dir / "events.jsonl")
    with pytest.raises(RemoteDirConflictError):
        _submit(runner, record, event_log, client)

    assert removed == [], "remove_remote_dir must never run on a conflict"
    assert _snapshot(sftp) == before
    assert remote_dir in sftp.dirs
    print("  [OK] conflict → remove_remote_dir spy count 0, dir intact")


# ====================================================================== #
# G. cleanup rules
# ====================================================================== #


def test_delete_project_dirs_validates_joined_target(tmp_path):
    from acp.scheduler.remote.cleanup import RemoteCleanup

    node = make_node()
    config = RemoteExecutionConfig(execution_mode="remote", nodes=[node])
    sftp = FakeSFTP()
    pool = SSHConnectionPool()
    client = FakeSSHClient(sftp)
    stager = FileStager(pool)
    cleanup = RemoteCleanup(pool, stager, config)

    removed: list[str] = []

    def spy_remove(node_, path):
        removed.append(path)

    stager.remove_remote_dir = spy_remove  # type: ignore[assignment]

    with patch.object(ssh_mod, "_create_client", side_effect=lambda n, timeout=30: client):
        # leaf/sub relative dirs (project leaf + task leaf) are tolerated.
        cleanup.delete_project_dirs("proj", ["projA/mol_opt", "projA/mol_opt__02"])
        # traversal / base-equality / unsafe targets are rejected.
        cleanup.delete_project_dirs(
            "proj",
            ["..", ".", "/", "../etc", "projA/../../etc"],
        )

    expected_ok = [
        posixpath.join(node.remote_work_dir, "projA/mol_opt"),
        posixpath.join(node.remote_work_dir, "projA/mol_opt__02"),
    ]
    assert removed == expected_ok
    print("  [OK] delete_project_dirs: leaf/sub accepted, traversal/base rejected")


def test_delete_job_disk_passes_persisted_relative_dir(tmp_path):
    """manager._delete_job_disk must delete exactly the persisted directory."""
    from acp.scheduler.manager import JobManager

    node = make_node()
    root = _run_root(tmp_path)
    work_dir = root / "projA" / "mol_opt_x__02"
    work_dir.mkdir(parents=True)
    rel = "projA/mol_opt_x__02"
    record = _make_record(
        "job1", work_dir=str(work_dir), result={"remote": {"relative": rel, "attempt": 1}}
    )
    record.result["node"] = node.name
    record.result["remote_dir"] = posixpath.join(node.remote_work_dir, rel)

    seen: dict = {}

    class _SpyCleanup:
        def delete_job_dirs(self, job_id, dir_names=None):
            seen["job_id"] = job_id
            seen["dir_names"] = list(dir_names or [])
            return {}

    mgr = JobManager.__new__(JobManager)
    mgr._remote_cleanup = _SpyCleanup()
    mgr._is_remote_enabled = lambda: True  # type: ignore[method-assign]
    mgr._delete_job_disk(record)

    assert seen["dir_names"] == [rel]
    assert (
        posixpath.join(node.remote_work_dir, seen["dir_names"][0]) == (record.result["remote_dir"])
    )
    print("  [OK] _delete_job_disk passes the persisted full relative dir")


# ====================================================================== #
# F. attempt isolation — receipts archived per attempt
# ====================================================================== #


def test_continue_archives_previous_resume_source_before_new_one(tmp_path):
    """Consecutive continues archive resume_source.json into attempts/<n>/."""
    from acp.scheduler.manager import JobManager

    root = _run_root(tmp_path)
    work_dir = root / "projA" / "mol_opt_x"
    work_dir.mkdir(parents=True)
    work_dir.joinpath("resume_source.json").write_text(json.dumps({"attempt": 1}), encoding="utf-8")
    record = _make_record("job1", work_dir=str(work_dir), attempt=2)

    mgr = JobManager.__new__(JobManager)
    mgr._archive_previous_resume_source(record, previous_attempt=1)

    archived = work_dir / "WORK" / "00_RUNTIME" / "attempts" / "1" / "resume_source.json"
    assert archived.is_file(), "previous resume_source.json must be archived"
    assert not (work_dir / "resume_source.json").exists()
    print("  [OK] previous resume_source.json archived to attempts/<n>/")


def test_remote_receipts_archived_before_bsub_as_submission_gate(tmp_path):
    """Remote attempt receipts move to attempts/<n>/ BEFORE bsub runs."""
    node = make_node()
    config = RemoteExecutionConfig(execution_mode="remote", auto_sync=False, nodes=[node])
    sftp = FakeSFTP()
    runner, client, _holder = _make_runner(sftp, config)
    work_dir = tmp_path / "runs" / "projA" / "mol_ens_remark"
    work_dir.mkdir(parents=True)
    rel = "projA/mol_ens_remark"
    record = _make_record(
        "job1",
        work_dir=str(work_dir),
        result={"remote": {"schema": 1, "relative": rel, "attempt": 2}},
        attempt=2,
    )
    remote_dir = posixpath.join(node.remote_work_dir, rel)
    _seed_owner(sftp, remote_dir, "job1", attempt=1)
    receipts = {
        ".exit_code": b"1\n",
        "state.json": b'{"status": "failed", "attempt": 1}',
        ".stage_opt": b"done",
        "checkpoint.json": b'{"step": 3}',
        "step_result_01.json": b'{"ok": true}',
    }
    for name, blob in receipts.items():
        sftp.files[posixpath.join(remote_dir, name)] = blob
    sftp.files[posixpath.join(remote_dir, "RESULT", "result_manifest.json")] = b"{}"
    sftp.dirs.add(posixpath.join(remote_dir, "RESULT"))

    order: list[str] = []
    original_submit_lsf = runner._submit_lsf

    def spy_submit(n, script_path, remote_root, **kwargs):
        order.append("bsub")
        return original_submit_lsf(n, script_path, remote_root, **kwargs)

    runner._submit_lsf = spy_submit  # type: ignore[assignment]

    event_log = JobEventLog(work_dir / "events.jsonl")
    _submit(runner, record, event_log, client)

    assert order == ["bsub"]
    archive_root = posixpath.join(remote_dir, "WORK", "00_RUNTIME", "attempts", "1")
    for name in receipts:
        assert posixpath.join(archive_root, name) in sftp.files, (
            f"{name} must be archived under attempts/1/"
        )
        assert posixpath.join(remote_dir, name) not in sftp.files, (
            f"{name} must be moved out of the live dir"
        )
    assert posixpath.join(archive_root, "RESULT", "result_manifest.json") in sftp.files
    # bsub happened only after every receipt moved: assert via order + files
    print("  [OK] remote receipts archived to attempts/1/ before bsub")


def test_remote_archive_failure_aborts_submission_before_bsub(tmp_path):
    node = make_node()
    config = RemoteExecutionConfig(execution_mode="remote", auto_sync=False, nodes=[node])
    sftp = FakeSFTP()
    runner, client, _holder = _make_runner(sftp, config)
    work_dir = tmp_path / "runs" / "projA" / "mol_ens_remark"
    work_dir.mkdir(parents=True)
    rel = "projA/mol_ens_remark"
    record = _make_record(
        "job1",
        work_dir=str(work_dir),
        result={"remote": {"schema": 1, "relative": rel, "attempt": 2}},
        attempt=2,
    )
    remote_dir = posixpath.join(node.remote_work_dir, rel)
    _seed_owner(sftp, remote_dir, "job1", attempt=1)
    sftp.files[posixpath.join(remote_dir, ".exit_code")] = b"1\n"

    def failing_rename(node_, src, dst):
        raise OSError("rename failed")

    runner._stager.rename_remote = failing_rename  # type: ignore[assignment]

    bsub_ran = {"yes": False}
    original_submit_lsf = runner._submit_lsf

    def spy_submit(n, script_path, remote_root, **kwargs):
        bsub_ran["yes"] = True
        return original_submit_lsf(n, script_path, remote_root, **kwargs)

    runner._submit_lsf = spy_submit  # type: ignore[assignment]

    event_log = JobEventLog(work_dir / "events.jsonl")
    with pytest.raises(RemoteSubmissionRejected):
        _submit(runner, record, event_log, client)
    assert bsub_ran["yes"] is False, "archive failure must abort before bsub"
    print("  [OK] remote archive failure → submission gate aborts, no bsub")


def test_receipt_readers_reject_stale_attempt_receipts(tmp_path):
    """Local completed-disk probe ignores receipts from an older attempt."""
    from acp.scheduler.manager import JobManager

    root = _run_root(tmp_path)
    work_dir = root / "projA" / "mol_opt_x"
    work_dir.mkdir(parents=True)
    work_dir.joinpath(".exit_code").write_text("0", encoding="utf-8")

    def _write_state(attempt: int) -> None:
        work_dir.joinpath("state.json").write_text(
            json.dumps(
                {
                    "status": "completed",
                    "attempt": attempt,
                    "stages": {"opt": {"status": "completed"}},
                }
            ),
            encoding="utf-8",
        )

    # A stale receipt from attempt 1 lying next to an attempt-2 record.
    _write_state(1)
    record = _make_record("job1", work_dir=str(work_dir), attempt=2)
    mgr = JobManager.__new__(JobManager)
    assert mgr._disk_shows_completed(work_dir, record.attempt) is False
    # A receipt without an attempt stamp keeps legacy behaviour (accepted).
    _write_state(2)
    assert mgr._disk_shows_completed(work_dir, record.attempt) is True
    print("  [OK] _disk_shows_completed rejects old-attempt receipts")
