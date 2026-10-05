"""D03 job↔release binding tests (plan todo 8).

Every remote job must be bound to a verified, immutable content-hash code
release BEFORE ``bsub`` (contract D).  Scenarios mirror the plan acceptance
list:

* queue upgrade isolation — job A binds release1, job B triggers release2;
  A's script keeps ``releases/<release1>/src`` and release1 is untouched
* unverified release → ``RemoteSubmissionRejected`` and bsub never called
* ``code_release`` persisted with the submit intent BEFORE bsub
  (asserted from inside the fake bsub handler)
* the temporary ``acquire_release_ref`` spans the ensure→bind window
* GC barrier — an aged release hit while binding survives ``prune_releases``
* ``auto_sync=False`` + node ``pinned_release`` → PYTHONPATH at that release
* ``auto_sync=False`` without any verified release → rejected
* dev escape hatch ``ACP_REMOTE_ALLOW_UNVERSIONED=1`` → provenance
  ``unversioned-shared`` + alert (immutability does NOT cover that mode)
* continue reuses the job's original release; a deleted release is an
  explicit error, never a silent upgrade

Run with: PYTHONPATH=src python3.11 -m pytest tests/test_remote_code_release_binding.py -q
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import shlex
from pathlib import Path
from unittest.mock import patch

import pytest

from acp.scheduler.events import JobEventLog
from acp.scheduler.jobs import JobRecord, JobSpec
from acp.scheduler.remote import ssh as ssh_mod
from acp.scheduler.remote.config import RemoteExecutionConfig, RemoteNode
from acp.scheduler.remote.release import (
    ReleaseError,
    build_release_manifest,
    prune_releases,
    releases_root,
)
from acp.scheduler.remote.runner import RemoteJobRunner, RemoteSubmissionRejected
from acp.scheduler.remote.script_gen import build_lsf_script_spec, generate_lsf_script
from acp.scheduler.remote.sftp import FileStager
from acp.scheduler.remote.ssh import SSHConnectionPool
from acp.scheduler.store import JobStore
from tests.test_remote_phase2 import FakeSFTP, FakeSSHClient, make_node

try:
    import paramiko  # noqa: F401

    REMOTE_AVAILABLE = True
except ImportError:  # pragma: no cover
    REMOTE_AVAILABLE = False

requires_remote = pytest.mark.skipif(not REMOTE_AVAILABLE, reason="paramiko not installed")

# Stable fake release id used by the pinned-release scenarios (16 chars,
# matching the content-hash id shape).
PINNED_RELEASE_ID = "0d0e0fa11ce00001"
MISSING_RELEASE_ID = "0bad000000000000"

# Files of the synthetic pinned release seeded into the fake node.
_PINNED_FILES = {"src/acp/__init__.py": b'"""pinned"""\n'}


# --------------------------------------------------------------------- #
# Fake-node helpers
# --------------------------------------------------------------------- #


def _seed_release(
    sftp: FakeSFTP,
    node: RemoteNode,
    release_id: str,
    files: dict[str, bytes],
    *,
    mtime: float | None = None,
) -> str:
    """Seed an already-published (``.complete``-marked) release into the fake.

    The marker carries a manifest matching *files* exactly, so
    ``verify_existing_release`` content verification passes.  When *mtime*
    is given the release directory reports that mtime in listings (used by
    the GC scenarios).
    """
    root = posixpath.join(node.remote_code_dir, "releases", release_id)
    manifest = {
        rel: {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
        for rel, data in files.items()
    }
    marker = json.dumps(
        {
            "schema_version": 1,
            "release_id": release_id,
            "files": manifest,
            "requirements_sha256": "",
            "defaults_sha256": "",
            "git_commit": None,
            "dirty": True,
        },
        sort_keys=True,
        indent=2,
    )
    for rel, data in files.items():
        remote = posixpath.join(root, rel)
        sftp.files[remote] = data
        sftp._set_attr(remote, size=len(data))
    sftp.files[posixpath.join(root, ".complete")] = marker.encode("utf-8")
    sftp.dirs.add(root)
    if mtime is not None and isinstance(sftp, _AgedSFTP):
        sftp.mtimes[root] = mtime
    return root


class _AgedSFTP(FakeSFTP):
    """FakeSFTP whose directory listings can report explicit mtimes."""

    def __init__(self) -> None:
        super().__init__()
        self.mtimes: dict[str, float] = {}

    def listdir_attr(self, path):  # type: ignore[no-untyped-def]
        attrs = super().listdir_attr(path)
        for attr in attrs:
            full = posixpath.join(path, attr.filename)
            if full in self.mtimes:
                attr.st_mtime = self.mtimes[full]
        return attrs


def _sha256sum_reply(sftp: FakeSFTP, cmd: str) -> tuple[int, str, str] | None:
    """Answer a remote ``sha256sum <path>`` against the fake filesystem."""
    parts = shlex.split(cmd)
    if not parts or parts[0] != "sha256sum":
        return None
    path = parts[1]
    data = sftp.files.get(path)
    if data is None:
        return 1, "", f"sha256sum: {path}: No such file or directory"
    return 0, f"{hashlib.sha256(data).hexdigest()}  {path}\n", ""


def _remove_tree(sftp: FakeSFTP, target: str) -> None:
    target = posixpath.normpath(target)
    for mapping in (sftp.files, sftp.attrs):
        for path in list(mapping.keys()):
            norm = posixpath.normpath(path)
            if norm == target or norm.startswith(target.rstrip("/") + "/"):
                mapping.pop(path, None)
    for path in list(sftp.dirs):
        norm = posixpath.normpath(path)
        if norm == target or norm.startswith(target.rstrip("/") + "/"):
            sftp.dirs.discard(path)


def _release_cmd_handler(harness: _Harness):  # type: ignore[no-untyped-def]
    """Command handler covering bsub + the remote release protocol."""

    def handler(cmd: str) -> tuple[int, str, str]:
        if "bsub" in cmd and "<" in cmd:
            harness.bsub_calls += 1
            if harness.bsub_listener is not None:
                harness.bsub_listener(cmd)
            return 0, "Job <54321> is submitted to queue <normal>.\n", ""
        digest = _sha256sum_reply(harness.sftp, cmd)
        if digest is not None:
            return digest
        s = cmd.strip()
        if s.startswith("rm -rf"):
            _remove_tree(harness.sftp, shlex.split(s[len("rm -rf") :].strip())[0])
            return 0, "", ""
        if s.startswith("rmdir "):
            harness.sftp.dirs.discard(shlex.split(s)[1])
            return 0, "", ""
        # mkdir-based locks: single-writer tests accept immediately.
        return 0, "", ""

    return handler


class _Harness:
    """Real RemoteJobRunner over the phase-2 fakes with release support."""

    def __init__(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        *,
        auto_sync: bool,
        node: RemoteNode | None = None,
        aged: bool = False,
        bsub_listener=None,  # type: ignore[no-untyped-def]
    ) -> None:
        self.node = node or make_node()
        self.config = RemoteExecutionConfig(
            execution_mode="remote", auto_sync=auto_sync, nodes=[self.node]
        )
        self.pool = SSHConnectionPool()
        self.sftp: FakeSFTP = _AgedSFTP() if aged else FakeSFTP()
        self.client = FakeSSHClient(self.sftp)
        self.bsub_calls = 0
        self.bsub_listener = bsub_listener
        self.client.cmd_handler = _release_cmd_handler(self)
        self.runner = RemoteJobRunner(
            self.pool, self.config, stager=FileStager(self.pool), poll_interval=0
        )
        monkeypatch.setenv("ACP_REMOTE_RELEASE_STATE_DIR", str(tmp_path / "release-state"))
        self._tmp = tmp_path

    def make_record(
        self, job_id: str, *, remote_meta: dict | None = None
    ) -> tuple[JobRecord, JobEventLog, str]:
        work_dir = self._tmp / "runs" / "projA" / job_id
        work_dir.mkdir(parents=True, exist_ok=True)
        spec = JobSpec(
            workflow="ensemble",
            input={"source": "CCO", "source_type": "smiles"},
            resources={"nproc": 2},
            molecule_name=job_id,
        )
        result: dict = {}
        if remote_meta is not None:
            result["remote"] = dict(remote_meta)
        record = JobRecord(id=job_id, spec=spec, work_dir=str(work_dir), result=result)
        remote_dir = posixpath.join(self.node.remote_work_dir, spec.task_dir_name())
        return record, JobEventLog(work_dir / "events.jsonl"), remote_dir

    def submit(self, record: JobRecord, event_log: JobEventLog, **kwargs) -> str:  # type: ignore[no-untyped-def]
        with self.connected():
            return self.runner.submit_remote(record, event_log, **kwargs)

    def connected(self):  # type: ignore[no-untyped-def]
        """Patch ``_create_client`` so every SSH command lands in the fake."""
        return patch.object(
            ssh_mod, "_create_client", side_effect=lambda n, timeout=30: self.client
        )

    def script_of(self, remote_dir: str) -> str:
        return self.sftp.files[posixpath.join(remote_dir, "submit.lsf")].decode("utf-8")

    def release_files(self, node: RemoteNode, release_id: str) -> dict[str, bytes]:
        prefix = posixpath.join(releases_root(node), release_id) + "/"
        return {
            path[len(prefix) :]: blob
            for path, blob in self.sftp.files.items()
            if path.startswith(prefix)
        }

    def release_dirs(self, node: RemoteNode) -> set[str]:
        root = releases_root(node) + "/"
        out: set[str] = set()
        for path in self.sftp.files:
            if path.startswith(root):
                out.add(path[len(root) :].split("/", 1)[0])
        return out

    def ref_files(self, node: RemoteNode, release_id: str) -> list[str]:
        refs_dir = posixpath.join(releases_root(node), ".refs") + "/"
        return [
            path
            for path in self.sftp.files
            if path.startswith(refs_dir) and path.split("/")[-1].startswith(release_id + ".")
        ]

    def event_types(self, event_log: JobEventLog) -> list[str]:
        return [e.get("type") for e in event_log.read_all()]

    def close(self) -> None:
        self.pool.close()


def _make_project_tree(root: Path, core_value: str = "v1") -> None:
    """Minimal tree accepted by ``build_sync_file_list``."""
    (root / "src" / "acp").mkdir(parents=True, exist_ok=True)
    (root / "src" / "cccp").mkdir(parents=True, exist_ok=True)
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "src" / "acp" / "core.py").write_text(f"# {core_value}\n", encoding="utf-8")
    (root / "src" / "cccp" / "util.py").write_text("def f(): ...\n", encoding="utf-8")
    (root / "requirements-node.txt").write_text("numpy\n", encoding="utf-8")
    (root / "config" / "defaults.yaml").write_text("resources: {}\n", encoding="utf-8")


def _patch_project_root(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setattr("acp.scheduler.remote.runner._project_root", lambda: root)


# --------------------------------------------------------------------- #
# 1. Queue upgrade isolation
# --------------------------------------------------------------------- #


@requires_remote
def test_queue_upgrade_isolates_job_a_release_from_job_b(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Job A binds release1; B's content change publishes release2 — A's
    script keeps ``releases/<release1>/src`` and release1 bytes never move."""
    tree = tmp_path / "tree"
    _make_project_tree(tree, "v1")
    rel1 = build_release_manifest(tree).release_id
    _patch_project_root(monkeypatch, tree)

    harness = _Harness(tmp_path, monkeypatch, auto_sync=True)
    try:
        record_a, log_a, dir_a = harness.make_record("joba")
        harness.submit(record_a, log_a)
        script_a = harness.script_of(dir_a)
        assert f"releases/{rel1}/src" in script_a
        assert record_a.result["remote"]["code_release"] == rel1

        snapshot = harness.release_files(harness.node, rel1)

        # Content changes → a NEW release id for the next submission.
        _make_project_tree(tree, "v2-changed")
        rel2 = build_release_manifest(tree).release_id
        assert rel2 != rel1

        record_b, log_b, dir_b = harness.make_record("jobb")
        harness.submit(record_b, log_b)
        script_b = harness.script_of(dir_b)
        assert f"releases/{rel2}/src" in script_b
        assert f"releases/{rel1}/src" not in script_b

        # Job A's release is untouched by B's ensure (immutability).
        assert harness.release_files(harness.node, rel1) == snapshot
        assert harness.event_types(log_a).count("remote.code_release_ready") == 1
        ready_a = [e for e in log_a.read_all() if e.get("type") == "remote.code_release_ready"][0]
        assert ready_a["release_id"] == rel1
        assert harness.bsub_calls == 2
        print(f"  [OK] queue upgrade: A={rel1} B={rel2}; release1 immutable")
    finally:
        harness.close()


# --------------------------------------------------------------------- #
# 2. Unverified release → rejected before bsub
# --------------------------------------------------------------------- #


@requires_remote
def test_unverified_pinned_release_rejected_and_bsub_never_called(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """auto_sync=False with a pinned release that does NOT exist/verify on
    the node → RemoteSubmissionRejected, zero bsub calls, failure event."""
    monkeypatch.delenv("ACP_REMOTE_ALLOW_UNVERSIONED", raising=False)
    node = make_node(pinned_release=MISSING_RELEASE_ID)
    harness = _Harness(tmp_path, monkeypatch, auto_sync=False, node=node)
    try:
        record, log, _remote_dir = harness.make_record("unverified")
        with pytest.raises(RemoteSubmissionRejected) as excinfo:
            harness.submit(record, log)
        assert MISSING_RELEASE_ID in str(excinfo.value)
        assert harness.bsub_calls == 0, "bsub must never run for an unverified release"
        failed = [e for e in log.read_all() if e.get("type") == "remote.code_release_failed"]
        assert failed, "remote.code_release_failed must carry the reason"
        assert MISSING_RELEASE_ID in str(failed[0].get("reason", ""))
        print("  [OK] unverified pinned release rejected before bsub")
    finally:
        harness.close()


@requires_remote
def test_release_ensure_failure_rejected_before_bsub(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A publishing/verification failure inside ensure_node_release fails
    CLOSED: RemoteSubmissionRejected + event, no bsub, no submission dir."""
    tree = tmp_path / "tree"
    _make_project_tree(tree)
    _patch_project_root(monkeypatch, tree)

    harness = _Harness(tmp_path, monkeypatch, auto_sync=True)
    try:

        def boom(*_args, **_kwargs):  # type: ignore[no-untyped-def]
            raise ReleaseError("staging verification failed for release deadbeef")

        monkeypatch.setattr("acp.scheduler.remote.runner.ensure_node_release", boom)
        record, log, remote_dir = harness.make_record("ensurefail")
        with pytest.raises(RemoteSubmissionRejected) as excinfo:
            harness.submit(record, log)
        assert "staging verification failed" in str(excinfo.value)
        assert harness.bsub_calls == 0
        assert posixpath.join(remote_dir, "submit.lsf") not in harness.sftp.files
        failed = [e for e in log.read_all() if e.get("type") == "remote.code_release_failed"]
        assert failed and "staging verification failed" in str(failed[0].get("reason", ""))
        print("  [OK] ensure failure → rejected before bsub, no script uploaded")
    finally:
        harness.close()


# --------------------------------------------------------------------- #
# 3. code_release persisted with the intent BEFORE bsub (manager level)
# --------------------------------------------------------------------- #


@requires_remote
def test_code_release_persisted_with_intent_before_bsub(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The fake bsub handler reads the DB: ``result.remote.code_release``
    must already be there — the bind is part of the submit-intent write and
    lands strictly before ``bsub`` (contract D)."""
    from acp.scheduler.jobs import JobStatus
    from tests.test_remote_submission_protocol import _make_manager

    mgr = _make_manager(tmp_path, monkeypatch)
    try:
        tree = tmp_path / "tree"
        _make_project_tree(tree)
        _patch_project_root(monkeypatch, tree)
        monkeypatch.setenv("ACP_REMOTE_RELEASE_STATE_DIR", str(tmp_path / "release-state"))

        node = mgr._remote_config.get_node("compute-01")
        assert node is not None
        config = RemoteExecutionConfig(execution_mode="remote", auto_sync=True, nodes=[node])
        pool = SSHConnectionPool()
        sftp = FakeSFTP()
        client = FakeSSHClient(sftp)
        seen: dict = {"code_release_at_bsub": None}

        def cmd_handler(cmd: str):  # type: ignore[no-untyped-def]
            if "bsub" in cmd and "<" in cmd:
                stored = mgr.store.get("bindjob")
                meta = (stored.result or {}).get("remote") or {} if stored else {}
                seen["code_release_at_bsub"] = meta.get("code_release")
                return (0, "Job <8181> is submitted to queue <normal>.\n", "")
            digest = _sha256sum_reply(sftp, cmd)
            if digest is not None:
                return digest
            s = cmd.strip()
            if s.startswith("rm -rf"):
                _remove_tree(sftp, shlex.split(s[len("rm -rf") :].strip())[0])
                return (0, "", "")
            if s.startswith("rmdir "):
                sftp.dirs.discard(shlex.split(s)[1])
                return (0, "", "")
            return (0, "", "")

        client.cmd_handler = cmd_handler
        runner = RemoteJobRunner(pool, config, stager=FileStager(pool), poll_interval=0)
        mgr.remote_runner = runner  # type: ignore[assignment]

        expected_release = build_release_manifest(tree).release_id
        work_dir = tmp_path / "runs" / "bindjob"
        work_dir.mkdir(parents=True, exist_ok=True)
        seed = JobRecord(
            id="bindjob",
            spec=JobSpec(
                workflow="Confsearch",
                input={"source": "CCO", "source_type": "smiles"},
                method={"protocol": "xtb-crest"},
            ),
            status=JobStatus.QUEUED,
            work_dir=str(work_dir),
        )
        mgr.store.create(seed)

        with patch.object(ssh_mod, "_create_client", side_effect=lambda n, timeout=30: client):
            assert mgr._submit_job("bindjob") is True

        # Visible from inside the fake bsub handler — pre-bsub persistence.
        assert seen["code_release_at_bsub"] == expected_release, (
            "code_release must be persisted with the intent BEFORE bsub"
        )
        stored = mgr.store.get("bindjob")
        assert stored is not None
        meta = (stored.result or {}).get("remote") or {}
        assert meta.get("code_release") == expected_release
        assert stored.remote_job_id == "8181"
        assert stored.status == JobStatus.PENDING
        pool.close()
        print("  [OK] code_release visible in DB inside the fake bsub handler")
    finally:
        mgr.shutdown()


# --------------------------------------------------------------------- #
# 4. Temp ref spans the ensure→bind window
# --------------------------------------------------------------------- #


@requires_remote
def test_temp_ref_spans_ensure_to_bind_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """``ensure_node_release`` holds ``acquire_release_ref`` from before the
    bind callback until after it: the ref file exists INSIDE the window and
    is gone after submission completes."""
    tree = tmp_path / "tree"
    _make_project_tree(tree)
    rel1 = build_release_manifest(tree).release_id
    _patch_project_root(monkeypatch, tree)

    harness = _Harness(tmp_path, monkeypatch, auto_sync=True)
    observations: dict = {}

    def on_bound(release_id: str) -> None:
        # Inside the ensure→bind window: the temp ref must still be held.
        observations["refs_in_window"] = harness.ref_files(harness.node, release_id)
        observations["release_present"] = release_id in harness.release_dirs(harness.node)

    try:
        record, log, _remote_dir = harness.make_record("refwin")
        harness.submit(record, log, on_code_release_bound=on_bound)
        assert observations.get("refs_in_window"), "temp ref must protect the bind window"
        assert observations.get("release_present") is True
        # Bind succeeded → ref dropped on every exit path.
        assert harness.ref_files(harness.node, rel1) == []
        print("  [OK] temp ref held across ensure→bind, released afterwards")
    finally:
        harness.close()


# --------------------------------------------------------------------- #
# 5. GC barrier: aged release + prune inside the bind window
# --------------------------------------------------------------------- #


@requires_remote
def test_gc_and_ref_acquisition_are_mutually_exclusive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """An aged, unreferenced release is prune-eligible — until a submission
    binds it: pruning INSIDE the ensure→bind window must keep it (active
    ref), pruning after the ref is dropped but before any DB binding would
    reclaim it.  Eligibility and ref acquisition are mutually exclusive."""
    rel1 = "aged000000000001"
    harness = _Harness(tmp_path, monkeypatch, auto_sync=False, aged=True)
    node = harness.node
    import time as _time

    aged_mtime = _time.time() - 10 * 86400
    _seed_release(
        harness.sftp,
        node,
        rel1,
        {"src/acp/__init__.py": b"aged\n"},
        mtime=aged_mtime,
    )
    store = JobStore(tmp_path / "gc.sqlite")
    stager = FileStager(harness.pool)

    try:
        # Eligibility sanity: no DB refs, no active refs, past both age floors.
        with harness.connected():
            dry = prune_releases(
                node,
                stager=stager,
                ssh=harness.pool,
                job_store=store,
                retention_days=0,
                min_age_hours=0,
                dry_run=True,
            )
        assert rel1 in dry.pruned, "aged unreferenced release must be prune-eligible"

        window: dict = {}

        def on_bound(release_id: str) -> None:
            # Inside the ensure→bind window (ref held): GC must not reclaim.
            report = prune_releases(
                node,
                stager=stager,
                ssh=harness.pool,
                job_store=store,
                retention_days=0,
                min_age_hours=0,
            )
            window["report"] = report
            window["survived"] = release_id in harness.release_dirs(node)

        record, log, remote_dir = harness.make_record("gcjob", remote_meta={"code_release": rel1})
        harness.submit(record, log, on_code_release_bound=on_bound)

        report = window["report"]
        assert rel1 not in report.pruned, "GC must not reclaim a release inside the bind window"
        assert rel1 in report.kept_active_ref, "active ref must protect the release"
        assert window["survived"] is True
        assert harness.ref_files(node, rel1) == [], "ref dropped after the bind"

        # Ref gone + no DB binding (bare runner record) → now it reclaims:
        # proves the earlier survival came from the ref, not from age gates.
        with harness.connected():
            after = prune_releases(
                node,
                stager=stager,
                ssh=harness.pool,
                job_store=store,
                retention_days=0,
                min_age_hours=0,
            )
        assert rel1 in after.pruned
        assert rel1 not in harness.release_dirs(node)
        print("  [OK] GC barrier: ref ∩ prune eligibility mutually exclusive")
    finally:
        harness.close()


# --------------------------------------------------------------------- #
# 6. auto_sync=False + node pinned_release
# --------------------------------------------------------------------- #


@requires_remote
def test_auto_sync_false_with_pinned_release_points_pythonpath_at_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """No automatic upload: the verified pinned release is selected, the
    script's PYTHONPATH points at ``releases/<pinned>/src``, and nothing is
    uploaded into the shared code dir."""
    monkeypatch.delenv("ACP_REMOTE_ALLOW_UNVERSIONED", raising=False)
    node = make_node(pinned_release=PINNED_RELEASE_ID)
    harness = _Harness(tmp_path, monkeypatch, auto_sync=False, node=node)
    try:
        _seed_release(harness.sftp, node, PINNED_RELEASE_ID, dict(_PINNED_FILES))
        record, log, remote_dir = harness.make_record("pinnedjob")
        harness.submit(record, log)
        script = harness.script_of(remote_dir)
        assert f"releases/{PINNED_RELEASE_ID}/src" in script
        assert f"{node.remote_code_dir}/src:" not in script.replace(
            f"releases/{PINNED_RELEASE_ID}/src", ""
        )
        assert record.result["remote"]["code_release"] == PINNED_RELEASE_ID
        assert "remote.code_release_ready" in harness.event_types(log)
        # Verify-only: the shared code dir received no files.
        shared_prefix = posixpath.join(node.remote_code_dir, "src/")
        assert not [p for p in harness.sftp.files if p.startswith(shared_prefix)]
        assert harness.bsub_calls == 1
        print("  [OK] auto_sync=False + pinned_release → PYTHONPATH at releases/<id>/src")
    finally:
        harness.close()


@requires_remote
def test_auto_sync_false_without_verified_release_rejects_submission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """No pinned release, no ACP_REMOTE_CODE_RELEASE, no dev hatch → the
    submission is rejected (never a silent fall back to the shared dir)."""
    monkeypatch.delenv("ACP_REMOTE_ALLOW_UNVERSIONED", raising=False)
    monkeypatch.delenv("ACP_REMOTE_CODE_RELEASE", raising=False)
    node = make_node()
    harness = _Harness(tmp_path, monkeypatch, auto_sync=False, node=node)
    try:
        record, log, remote_dir = harness.make_record("norel")
        with pytest.raises(RemoteSubmissionRejected) as excinfo:
            harness.submit(record, log)
        message = str(excinfo.value).lower()
        assert "release" in message
        assert harness.bsub_calls == 0
        assert posixpath.join(remote_dir, "submit.lsf") not in harness.sftp.files
        failed = [e for e in log.read_all() if e.get("type") == "remote.code_release_failed"]
        assert failed
        print("  [OK] auto_sync=False without verified release → rejected")
    finally:
        harness.close()


# --------------------------------------------------------------------- #
# 7. Explicit ACP_REMOTE_CODE_RELEASE selection
# --------------------------------------------------------------------- #


@requires_remote
def test_explicit_env_release_selects_verified_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """ACP_REMOTE_CODE_RELEASE selects an existing verified release even
    without a node-level pin."""
    monkeypatch.delenv("ACP_REMOTE_ALLOW_UNVERSIONED", raising=False)
    monkeypatch.setenv("ACP_REMOTE_CODE_RELEASE", PINNED_RELEASE_ID)
    node = make_node()
    harness = _Harness(tmp_path, monkeypatch, auto_sync=False, node=node)
    try:
        _seed_release(harness.sftp, node, PINNED_RELEASE_ID, dict(_PINNED_FILES))
        record, log, remote_dir = harness.make_record("envrel")
        harness.submit(record, log)
        assert f"releases/{PINNED_RELEASE_ID}/src" in harness.script_of(remote_dir)
        assert record.result["remote"]["code_release"] == PINNED_RELEASE_ID
        print("  [OK] ACP_REMOTE_CODE_RELEASE selects the verified release")
    finally:
        harness.close()


# --------------------------------------------------------------------- #
# 8. Dev escape hatch
# --------------------------------------------------------------------- #


@requires_remote
def test_dev_escape_hatch_records_unversioned_provenance_with_alert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """ACP_REMOTE_ALLOW_UNVERSIONED=1 is the ONLY way to submit against the
    shared dir: provenance ``code_release="unversioned-shared"`` plus a
    loud event stating the immutability guarantee does not cover it."""
    monkeypatch.setenv("ACP_REMOTE_ALLOW_UNVERSIONED", "1")
    node = make_node()
    harness = _Harness(tmp_path, monkeypatch, auto_sync=False, node=node)
    try:
        record, log, remote_dir = harness.make_record("devjob")
        harness.submit(record, log)
        assert record.result["remote"]["code_release"] == "unversioned-shared"
        script = harness.script_of(remote_dir)
        assert f'PYTHONPATH="{node.remote_code_dir}/src:$PYTHONPATH"' in script
        ready = [e for e in log.read_all() if e.get("type") == "remote.code_release_ready"]
        assert ready, "unversioned mode must still emit the ready event"
        assert ready[0]["release_id"] == "unversioned-shared"
        assert ready[0].get("warning"), "the alert must state immutability is not covered"
        assert harness.bsub_calls == 1
        print("  [OK] dev hatch → provenance unversioned-shared + alert event")
    finally:
        harness.close()


# --------------------------------------------------------------------- #
# 9/10. Continue version rule
# --------------------------------------------------------------------- #


@requires_remote
def test_continue_reuses_original_release_without_upgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A continue re-dispatch reuses the job's ORIGINAL code_release (even
    though the current tree would hash to a new one) — no silent upgrade."""
    tree = tmp_path / "tree"
    _make_project_tree(tree, "original")
    rel1 = build_release_manifest(tree).release_id

    # The tree moved on since the original submission.
    _make_project_tree(tree, "upgraded")
    assert build_release_manifest(tree).release_id != rel1
    _patch_project_root(monkeypatch, tree)

    harness = _Harness(tmp_path, monkeypatch, auto_sync=True)
    try:
        # The original release was already published on the node.
        rel1_files = {
            "src/acp/core.py": b"# original\n",
            "src/cccp/util.py": b"def f(): ...\n",
        }
        _seed_release(harness.sftp, harness.node, rel1, rel1_files)

        record, log, remote_dir = harness.make_record(
            "continue1", remote_meta={"code_release": rel1, "attempt": 2}
        )
        harness.submit(record, log)
        script = harness.script_of(remote_dir)
        assert f"releases/{rel1}/src" in script
        assert record.result["remote"]["code_release"] == rel1
        # No NEW release was published for the upgraded tree.
        assert harness.release_dirs(harness.node) == {rel1}
        ready = [e for e in log.read_all() if e.get("type") == "remote.code_release_ready"]
        assert ready and ready[0]["release_id"] == rel1
        assert harness.bsub_calls == 1
        print("  [OK] continue reuses the original release; no silent upgrade")
    finally:
        harness.close()


@requires_remote
def test_continue_with_deleted_release_errors_explicitly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The job's original release is gone → explicit rejection naming it;
    the current tree must NOT be published as a replacement."""
    tree = tmp_path / "tree"
    _make_project_tree(tree, "current")
    _patch_project_root(monkeypatch, tree)

    harness = _Harness(tmp_path, monkeypatch, auto_sync=True)
    try:
        record, log, remote_dir = harness.make_record(
            "continue2", remote_meta={"code_release": MISSING_RELEASE_ID, "attempt": 2}
        )
        with pytest.raises(RemoteSubmissionRejected) as excinfo:
            harness.submit(record, log)
        assert MISSING_RELEASE_ID in str(excinfo.value)
        assert harness.bsub_calls == 0
        # Never a silent upgrade: no release directory was published.
        assert harness.release_dirs(harness.node) == set()
        assert posixpath.join(remote_dir, "submit.lsf") not in harness.sftp.files
        failed = [e for e in log.read_all() if e.get("type") == "remote.code_release_failed"]
        assert failed and MISSING_RELEASE_ID in str(failed[0].get("reason", ""))
        print("  [OK] deleted original release → explicit error, no upgrade")
    finally:
        harness.close()


# --------------------------------------------------------------------- #
# Script-spec parameter unit check (no other bytes change)
# --------------------------------------------------------------------- #


def test_build_lsf_script_spec_code_release_only_changes_pythonpath():
    """``code_release`` reuses ``LSFScriptSpec.remote_code_dir``: only the
    PYTHONPATH target moves to the release snapshot; all other bytes —
    BSUB directives, traps, cd, CLI line — stay identical."""
    node = make_node()
    spec = JobSpec(
        workflow="ensemble",
        input={"source": "CCO", "source_type": "smiles"},
        resources={"nproc": 4},
    )
    shared, cli = build_lsf_script_spec(spec, "job1", node, queue="normal")
    released, cli2 = build_lsf_script_spec(
        spec, "job1", node, queue="normal", code_release="cafef00d12345678"
    )
    assert cli == cli2
    shared_text = generate_lsf_script(shared)
    released_text = generate_lsf_script(released)
    assert shared.remote_code_dir == node.remote_code_dir
    assert released.remote_code_dir == posixpath.join(
        node.remote_code_dir, "releases", "cafef00d12345678"
    )
    assert f'PYTHONPATH="{node.remote_code_dir}/src:$PYTHONPATH"' in shared_text
    assert (
        f'PYTHONPATH="{node.remote_code_dir}/releases/cafef00d12345678/src:$PYTHONPATH"'
        in released_text
    )
    # Diff the two scripts: exactly ONE line may differ (the PYTHONPATH line).
    shared_lines = shared_text.splitlines()
    released_lines = released_text.splitlines()
    assert len(shared_lines) == len(released_lines)
    diff = [(a, b) for a, b in zip(shared_lines, released_lines) if a != b]
    assert len(diff) == 1
    assert diff[0][0].startswith("export PYTHONPATH=")
    print("  [OK] code_release parameter changes only the PYTHONPATH line")
