"""D03 content-hash code releases — todo 7 acceptance suite.

Covers ``acp.scheduler.remote.release`` end to end against fake SSH/SFTP
infrastructure re-used from the Phase 2 fakes (extended with dir mtimes,
event/put logging, put hooks, and a command handler implementing
``sha256sum`` / lock ``mkdir`` / ``rmdir`` / ``rm -rf``):

* release_id is content-derived (mtime independence, content sensitivity,
  local-deletion sensitivity) and never includes git metadata;
* file-set parity with ``build_sync_file_list`` (api/scheduler excluded,
  requirements-node.txt + config/defaults.yaml included);
* ``ensure_node_release`` verify-then-publish: staging upload, remote
  sha256 verification, ``.publish.lock`` serialisation,
  ``remote_rename(must_not_exist=True)``, ``.complete`` written LAST,
  failure ⇒ no ``.complete`` / no binding / ref released / idempotent retry;
* ``prune_releases`` lock-scoped refresh (DB any-status refs + active ref
  files) → eligibility → isolate/delete in ONE critical section; referenced /
  active-ref / in-window releases are never deleted;
* (B) decoupling: ``pre_submit_housekeeping`` calls
  ``cleanup_old_jobs(..., with_release_gc=False)`` ⇒ prune spy=0; None
  job_store ⇒ prune never runs;
* (C) production reachability: JobManager startup trigger reaches
  ``prune_releases`` with the injected ``job_store`` (≥1 call);
* concurrency barrier: ref acquisition vs GC mutual exclusion, release
  survives while referenced.

Run with: python3.11 -m pytest tests/test_remote_code_release.py -q
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import posixpath
import shlex
import stat
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml

from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.remote import release as release_mod
from acp.scheduler.remote import ssh as ssh_mod
from acp.scheduler.remote.cleanup import RemoteCleanup
from acp.scheduler.remote.config import RemoteExecutionConfig, RemoteNode
from acp.scheduler.remote.monitor import RemoteJobMonitor
from acp.scheduler.remote.release import (
    ReleaseBinding,
    ReleaseError,
    ReleaseManifest,
    acquire_release_ref,
    build_release_manifest,
    ensure_node_release,
    prune_releases,
    release_release_ref,
    releases_root,
)
from acp.scheduler.remote.sftp import FileStager
from acp.scheduler.remote.ssh import SSHConnectionPool
from acp.scheduler.remote.sync import (
    _build_sync_file_list,
    _project_root,
    build_sync_file_list,
)
from acp.scheduler.store import JobStore

# ====================================================================== #
# Fake SSH/SFTP infrastructure (Phase 2 base, extended for releases)
# ====================================================================== #


class FakeSFTPFile(io.BytesIO):
    def __init__(self, data: bytes = b"", mode: str = "rb"):
        super().__init__(data if "b" in mode else b"")
        self.mode = mode

    def write(self, data):
        if "b" not in self.mode and isinstance(data, str):
            data = data.encode("utf-8")
        return super().write(data)

    def read(self, size=-1):
        raw = super().read(size)
        if "b" not in self.mode:
            return raw.decode("utf-8")
        return raw

    def close(self):
        pass


class FakeSFTP:
    """Phase-2 fake + dir mtimes, event log, put hook, rename log."""

    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.attrs: dict[str, MagicMock] = {}
        self.dirs: set[str] = set()
        self.dir_mtimes: dict[str, float] = {}
        # Ordered mutation log: ("put", path) / ("write", path) /
        # ("rename", src, dst) / ("sha256", path).
        self.events: list[tuple] = []
        self.put_log: list[str] = []
        self.write_log: list[str] = []
        self.rename_log: list[tuple[str, str]] = []
        # Hook invoked before every put; may raise OSError to simulate a
        # partial upload failure.
        self.put_hook = None

    def put(self, localpath, remotepath):
        if self.put_hook is not None:
            self.put_hook(remotepath)
        with open(localpath, "rb") as f:
            self.files[remotepath] = f.read()
        self._set_attr(remotepath, size=len(self.files[remotepath]))
        self.put_log.append(remotepath)
        self.events.append(("put", remotepath))

    def get(self, remotepath, localpath):
        if remotepath not in self.files:
            raise FileNotFoundError(remotepath)
        with open(localpath, "wb") as f:
            f.write(self.files[remotepath])

    def file(self, remote_path, mode="r"):
        data = self.files.get(remote_path, b"")
        if "w" in mode or "a" in mode:
            f = FakeSFTPFile(b"", mode)
            original_path = remote_path
            state = {"closed": False}

            def _on_close():
                # IOBase.__del__ also invokes close() — record once only.
                if state["closed"]:
                    return
                state["closed"] = True
                self.files[original_path] = f.getvalue()
                self._set_attr(original_path, size=len(f.getvalue()))
                self.write_log.append(original_path)
                self.events.append(("write", original_path))

            f.close = _on_close  # type: ignore[assignment]
            return f
        return FakeSFTPFile(data, mode)

    def stat(self, path):
        if path in self.dirs:
            a = MagicMock()
            a.st_size = 0
            a.st_mtime = self.dir_mtimes.get(path, 0.0)
            a.st_mode = stat.S_IFDIR
            return a
        if path not in self.attrs and path not in self.files:
            raise FileNotFoundError(path)
        if path in self.files:
            return self._set_attr(path, size=len(self.files[path]))
        return self.attrs[path]

    def _set_attr(self, path, size=0, mtime=0.0, is_dir=False):
        a = MagicMock()
        a.st_size = size
        a.st_mtime = mtime
        a.st_mode = stat.S_IFDIR if is_dir else stat.S_IFREG
        self.attrs[path] = a
        return a

    def listdir(self, path):
        prefix = path.rstrip("/") + "/" if path != "/" else "/"
        names = []
        for fpath in list(self.files.keys()) | self.dirs:
            if fpath.startswith(prefix):
                rest = fpath[len(prefix) :]
                if "/" not in rest and rest:
                    names.append(rest)
        return names

    def mkdir(self, path):
        self.dirs.add(path)

    def rename(self, src, dst):
        # POSIX-ish SFTP rename: refuses an existing target so nothing nests.
        if dst in self.files or dst in self.dirs:
            raise OSError(f"[Errno 17] File exists: {dst}")
        if src in self.files:
            self.files[dst] = self.files.pop(src)
            if src in self.attrs:
                self.attrs[dst] = self.attrs.pop(src)
        elif src in self.dirs:
            self.dirs.discard(src)
            self.dirs.add(dst)
            if src in self.dir_mtimes:
                self.dir_mtimes[dst] = self.dir_mtimes.pop(src)
            for key in list(self.files):
                if key.startswith(src + "/"):
                    self.files[dst + key[len(src) :]] = self.files.pop(key)
            for key in list(self.attrs):
                if key.startswith(src + "/"):
                    self.attrs[dst + key[len(src) :]] = self.attrs.pop(key)
            for key in list(self.dir_mtimes):
                if key.startswith(src + "/"):
                    self.dir_mtimes[dst + key[len(src) :]] = self.dir_mtimes.pop(key)
        else:
            raise FileNotFoundError(src)
        self.rename_log.append((src, dst))
        self.events.append(("rename", src, dst))

    def listdir_attr(self, path):
        prefix = path.rstrip("/") + "/" if path != "/" else "/"
        seen: dict[str, MagicMock] = {}
        for fpath in set(self.files) | self.dirs:
            if not fpath.startswith(prefix):
                continue
            rest = fpath[len(prefix) :]
            if not rest:
                continue
            name = rest.split("/", 1)[0]
            if name in seen:
                continue
            attr = MagicMock()
            attr.filename = name
            entry_path = posixpath.join(prefix, name)
            attr.st_size = len(self.files.get(entry_path, b""))
            is_dir = entry_path in self.dirs
            attr.st_mtime = self.dir_mtimes.get(entry_path, 0.0)
            attr.st_mode = stat.S_IFDIR if is_dir else stat.S_IFREG
            seen[name] = attr
        if not seen and path not in self.dirs:
            raise FileNotFoundError(path)
        return list(seen.values())

    def remove(self, path):
        self.files.pop(path, None)
        self.attrs.pop(path, None)

    def close(self):
        pass


class FakeSSHClient:
    """SSH fake: version probe, sha256sum, mkdir-lock, rmdir, rm -rf, du."""

    def __init__(self, fake_sftp: FakeSFTP):
        self.fake_sftp = fake_sftp
        self.closed = False
        self._transport = MagicMock()
        self._transport.is_active.return_value = True
        self.cmd_handler = None
        self.executed_commands: list[str] = []

    def set_missing_host_key_policy(self, policy):
        pass

    def connect(self, **kwargs):
        pass

    def get_transport(self):
        return self._transport

    def exec_command(self, command, timeout=None):
        self.executed_commands.append(command)
        if "sys.version_info" in command:
            result = (0, "3.12.4\n", "")
        elif self.cmd_handler is not None:
            result = self.cmd_handler(command)
        else:
            result = self._default_handler(command)
        stdin = MagicMock()
        stdout = MagicMock()
        stderr = MagicMock()
        stdout.read.return_value = result[1].encode("utf-8")
        stderr.read.return_value = result[2].encode("utf-8")
        stdout.channel = MagicMock()
        stdout.channel.recv_exit_status.return_value = result[0]
        return stdin, stdout, stderr

    def _default_handler(self, command: str) -> tuple[int, str, str]:
        sftp = self.fake_sftp
        s = command.strip()
        if s.startswith("sha256sum "):
            path = shlex.split(s)[1]
            data = sftp.files.get(path)
            if data is None:
                return 1, "", f"sha256sum: {path}: No such file or directory"
            digest = hashlib.sha256(data).hexdigest()
            sftp.events.append(("sha256", path))
            return 0, f"{digest}  {path}\n", ""
        if " && mkdir " in s:
            # `mkdir -p <parent> && mkdir <lock>` — exclusive lock acquire.
            lock = shlex.split(s.split(" && ")[-1])[1]
            if lock in sftp.dirs:
                return 1, "", f"mkdir: cannot create directory '{lock}': File exists"
            sftp.dirs.add(lock)
            return 0, "", ""
        if s.startswith("rmdir "):
            path = shlex.split(s)[1]
            sftp.dirs.discard(path)
            return 0, "", ""
        if s.startswith("rm -rf"):
            target = shlex.split(s[len("rm -rf") :].strip())[0]
            self._remove_tree(target)
            return 0, "", ""
        if s.startswith("du -sb"):
            target = shlex.split(s[len("du -sb") :].strip())[0]
            total = 0
            norm = posixpath.normpath(target)
            for fpath, data in sftp.files.items():
                if posixpath.normpath(fpath).startswith(norm):
                    total += len(data)
            return 0, f"{total}\t{target}\n", ""
        return 0, "", ""

    def _remove_tree(self, target: str) -> None:
        target = posixpath.normpath(target)
        sftp = self.fake_sftp
        for mapping in (sftp.files, sftp.attrs, sftp.dir_mtimes):
            for fpath in list(mapping.keys()):
                norm = posixpath.normpath(fpath)
                if norm == target or norm.startswith(target.rstrip("/") + "/"):
                    mapping.pop(fpath, None)
        for d in list(sftp.dirs):
            norm = posixpath.normpath(d)
            if norm == target or norm.startswith(target.rstrip("/") + "/"):
                sftp.dirs.discard(d)

    def open_sftp(self):
        return self.fake_sftp

    def close(self):
        self.closed = True


def make_node(name="compute-01", **kw) -> RemoteNode:
    defaults = dict(
        name=name,
        host="10.0.0.1",
        username="testuser",
        remote_work_dir="/scratch/test/acp_jobs",
        remote_code_dir="/home/test/acp_code",
        max_concurrent_jobs=5,
        host_key_policy="auto_add",
    )
    defaults.update(kw)
    return RemoteNode(**defaults)


def make_config(node: RemoteNode, **kw) -> RemoteExecutionConfig:
    defaults = dict(execution_mode="remote", nodes=[node], retention_days=180)
    defaults.update(kw)
    return RemoteExecutionConfig(**defaults)


def make_env() -> SimpleNamespace:
    """Pool + stager + fake client/sftp bound to one node."""
    pool = SSHConnectionPool()
    sftp = FakeSFTP()
    client = FakeSSHClient(sftp)
    stager = FileStager(pool)
    node = make_node()

    def factory(n, timeout=30):
        return client

    env = SimpleNamespace(
        pool=pool, sftp=sftp, client=client, stager=stager, node=node, factory=factory
    )
    return env


def patch_client(env):
    return patch.object(ssh_mod, "_create_client", side_effect=env.factory)


def _mkdirs(sftp: FakeSFTP, path: str) -> None:
    """Register every component of *path* as a directory."""
    parts = [p for p in path.split("/") if p]
    current = "/" if path.startswith("/") else ""
    for part in parts:
        current = posixpath.join(current, part) if current else part
        sftp.dirs.add(current)


def seed_release(
    sftp: FakeSFTP,
    node: RemoteNode,
    release_id: str,
    files: dict[str, bytes],
    *,
    mtime: float | None = None,
    complete: bool = True,
) -> str:
    """Materialise an already-published release directory in the fake fs."""
    root = releases_root(node)
    rel_dir = posixpath.join(root, release_id)
    _mkdirs(sftp, rel_dir)
    for rel, data in files.items():
        full = posixpath.join(rel_dir, rel)
        _mkdirs(sftp, posixpath.dirname(full))
        sftp.files[full] = data
    if complete:
        sftp.files[posixpath.join(rel_dir, ".complete")] = b"{}"
    if mtime is not None:
        sftp.dir_mtimes[rel_dir] = mtime
    return rel_dir


def make_tree(root: Path) -> dict[str, bytes]:
    """Synthetic project tree mirroring the sync/release file set."""
    contents = {
        "src/acp/__init__.py": b'"""acp"""\n',
        "src/acp/engine.py": b"VALUE = 1\n",
        "src/acp/api/routes.py": b"# excluded\n",
        "src/acp/scheduler/manager.py": b"# excluded\n",
        "src/acp/__pycache__/junk.pyc": b"\x00junk",
        "src/cccp/__init__.py": b'"""cccp"""\n',
        "src/cccp/core.py": b"def f(): ...\n",
        "requirements-node.txt": b"numpy>=2.1.0\nPyYAML>=6.0\n",
        "config/defaults.yaml": b"executables:\n  orca:\n    path: /opt/orca\n",
    }
    for rel, data in contents.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return contents


def make_cleanup(
    env,
    *,
    job_store=None,
    cleanup_threshold=90,
    skip_threshold=95,
    retention_days=180,
) -> RemoteCleanup:
    config = make_config(env.node, retention_days=retention_days)
    monitor = RemoteJobMonitor(env.pool, env.stager)
    return RemoteCleanup(
        ssh_pool=env.pool,
        stager=env.stager,
        remote_config=config,
        monitor=monitor,
        cleanup_threshold=cleanup_threshold,
        skip_threshold=skip_threshold,
        job_store=job_store,
    )


def set_disk_pct(client: FakeSSHClient, pct: int) -> None:
    def handler(command):
        s = command.strip()
        if s.startswith("df"):
            return 0, f"{pct}%\n", ""
        return client._default_handler(command)

    client.cmd_handler = handler


def bind_release(store: JobStore, release_id: str, *, status=JobStatus.QUEUED) -> JobRecord:
    """Persist a job (any status) whose result binds *release_id*."""
    rec = JobRecord(
        id=f"j_{uuid.uuid4().hex[:10]}",
        spec=JobSpec(workflow="singlepoint", input={"source": "CCO"}),
        status=status,
        work_dir="/tmp/bind",
        result={"remote": {"code_release": release_id}},
    )
    store.create(rec)
    return rec


@pytest.fixture(autouse=True)
def _clean_release_registries():
    """Isolate module-global ref/staging registries across tests."""
    for registry in (
        release_mod._REF_REGISTRY,
        release_mod._ACTIVE_STAGING,
        release_mod._NODE_LOCKS,
    ):
        registry.clear()
    yield
    for registry in (
        release_mod._REF_REGISTRY,
        release_mod._ACTIVE_STAGING,
        release_mod._NODE_LOCKS,
    ):
        registry.clear()


# ====================================================================== #
# Manifest: content-derived release_id
# ====================================================================== #


def test_release_id_stable_across_mtime_changes(tmp_path):
    """Same content, different mtime → same release_id (no mtime versioning)."""
    make_tree(tmp_path)
    m1 = build_release_manifest(tmp_path)
    for path in build_sync_file_list(tmp_path):
        st = path.stat()
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    m2 = build_release_manifest(tmp_path)
    assert m1.release_id == m2.release_id
    assert m1.files == m2.files


def test_release_id_changes_on_content_change_with_same_mtime(tmp_path):
    """Content change with preserved mtime → new id (mtime is never the basis)."""
    make_tree(tmp_path)
    m1 = build_release_manifest(tmp_path)
    target = tmp_path / "src" / "cccp" / "core.py"
    st = target.stat()
    target.write_bytes(b"def f():  # changed\n")
    os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns))
    m2 = build_release_manifest(tmp_path)
    assert m2.release_id != m1.release_id
    assert m2.files["src/cccp/core.py"]["sha256"] != m1.files["src/cccp/core.py"]["sha256"]


def test_release_id_ignores_git_metadata(tmp_path):
    """release_id = sha256(canonical JSON over the files map only)[:16]."""
    make_tree(tmp_path)
    m = build_release_manifest(tmp_path)
    canonical = json.dumps(m.files, sort_keys=True, separators=(",", ":"))
    assert m.release_id == hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    # git metadata is best-effort (tmp tree is not a repo → None/True) …
    assert m.git_commit is None
    assert m.dirty is True
    # … and must not participate in the id: rebuild with injected metadata.
    with patch.object(release_mod, "_git_metadata", return_value=("deadbeef", False)):
        m2 = build_release_manifest(tmp_path)
    assert m2.release_id == m.release_id
    assert m2.git_commit == "deadbeef"
    assert m2.dirty is False


def test_manifest_digest_fields(tmp_path):
    make_tree(tmp_path)
    m = build_release_manifest(tmp_path)
    assert m.schema_version == 1
    assert (
        m.requirements_sha256
        == hashlib.sha256((tmp_path / "requirements-node.txt").read_bytes()).hexdigest()
    )
    assert (
        m.defaults_sha256
        == hashlib.sha256((tmp_path / "config" / "defaults.yaml").read_bytes()).hexdigest()
    )
    assert all(set(entry) == {"sha256", "size"} for entry in m.files.values())
    assert isinstance(m, ReleaseManifest)
    assert ReleaseManifest.from_dict(m.to_dict()) == m


# ====================================================================== #
# File-set parity with build_sync_file_list
# ====================================================================== #


def test_file_set_parity_synthetic_tree(tmp_path):
    make_tree(tmp_path)
    m = build_release_manifest(tmp_path)
    expected = {p.relative_to(tmp_path).as_posix() for p in build_sync_file_list(tmp_path)}
    assert set(m.files) == expected
    assert not any(rel.startswith("src/acp/api/") for rel in m.files)
    assert not any(rel.startswith("src/acp/scheduler/") for rel in m.files)
    assert not any("__pycache__" in rel for rel in m.files)
    assert "requirements-node.txt" in m.files
    assert "config/defaults.yaml" in m.files


def test_file_set_parity_real_project_root():
    root = _project_root()
    m = build_release_manifest(root)
    expected = {p.relative_to(root).as_posix() for p in build_sync_file_list(root)}
    assert set(m.files) == expected
    rels = list(m.files)
    acp_rels = [r for r in rels if r.startswith("src/acp/")]
    assert not any(r.startswith("src/acp/api/") for r in acp_rels)
    assert not any(r.startswith("src/acp/scheduler/") for r in acp_rels)
    assert any(r.startswith("src/cccp/") for r in rels)
    assert "requirements-node.txt" in m.files
    assert "config/defaults.yaml" in m.files
    # Promoted name + compat alias kept (phase-1 import stays valid).
    assert build_sync_file_list.__module__ == "acp.scheduler.remote.sync"
    assert _build_sync_file_list is build_sync_file_list


# ====================================================================== #
# ensure_node_release — verify-then-publish
# ====================================================================== #


def test_ensure_publishes_with_complete_marker_last(tmp_path):
    """.complete appears only after upload + verification + rename."""
    make_tree(tmp_path)
    env = make_env()
    manifest = build_release_manifest(tmp_path)
    state_dir = tmp_path / "state"

    with patch_client(env):
        binding = ensure_node_release(
            env.node,
            manifest,
            stager=env.stager,
            ssh=env.pool,
            state_dir=state_dir,
            project_root=tmp_path,
        )

    assert isinstance(binding, ReleaseBinding)
    assert binding.release_id == manifest.release_id
    assert binding.release_dir == posixpath.join(releases_root(env.node), manifest.release_id)

    events = env.sftp.events
    put_idx = max(i for i, e in enumerate(events) if e[0] == "put")
    rename_idx = next(i for i, e in enumerate(events) if e[0] == "rename")
    complete_idx = next(
        i for i, e in enumerate(events) if e[0] == "write" and e[1].endswith("/.complete")
    )
    # sha256 verification of the staging copy precedes the rename/publish.
    verify_idx = next(i for i, e in enumerate(events) if e[0] == "sha256")
    assert verify_idx < rename_idx < complete_idx
    assert put_idx < rename_idx
    # .complete is the final write and lives in the published dir only.
    assert events[complete_idx][1] == posixpath.join(binding.release_dir, ".complete")
    final_prefix = binding.release_dir + "/"
    final_writes = [e for e in events if e[0] == "write" and e[1].startswith(final_prefix)]
    assert [e[1] for e in final_writes] == [posixpath.join(binding.release_dir, ".complete")]
    assert posixpath.join(binding.release_dir, ".complete") in env.sftp.files
    # Local verified cache written.
    cache = state_dir / "releases" / "compute-01.json"
    assert cache.exists() and manifest.release_id in json.loads(cache.read_text())
    env.pool.close()


def test_ensure_requires_release_ref_and_binding_carries_it(tmp_path):
    make_tree(tmp_path)
    env = make_env()
    manifest = build_release_manifest(tmp_path)
    with patch_client(env):
        binding = ensure_node_release(
            env.node,
            manifest,
            stager=env.stager,
            ssh=env.pool,
            state_dir=tmp_path / "state",
            project_root=tmp_path,
        )
        refs_dir = posixpath.join(releases_root(env.node), ".refs")
        ref_files = [p for p in env.sftp.files if p.startswith(refs_dir + "/")]
        assert len(ref_files) == 1
        assert binding.ref_id in ref_files[0]
        # Caller releases the ref (todo 8 keeps it across the bind window).
        release_release_ref(
            env.node,
            binding.release_id,
            binding.ref_id,
            stager=env.stager,
            ssh=env.pool,
        )
        assert not [p for p in env.sftp.files if p.startswith(refs_dir + "/")]
    env.pool.close()


def test_partial_upload_failure_no_complete_and_idempotent_retry(tmp_path):
    """OSError on 2nd upload → no .complete, no binding, ref released;
    re-ensure uploads ONLY the missing files (staging reuse, idempotent)."""
    make_tree(tmp_path)
    env = make_env()
    manifest = build_release_manifest(tmp_path)
    state_dir = tmp_path / "state"
    total = len(manifest.files)
    assert total >= 3

    count = {"n": 0}

    def fail_second(path):
        count["n"] += 1
        if count["n"] == 2:
            raise OSError("simulated network failure on file 2")

    env.sftp.put_hook = fail_second
    with patch_client(env):
        with pytest.raises(OSError):
            ensure_node_release(
                env.node,
                manifest,
                stager=env.stager,
                ssh=env.pool,
                state_dir=state_dir,
                project_root=tmp_path,
            )
        # No .complete anywhere (staging or final), final dir never created.
        assert not [p for p in env.sftp.files if p.endswith("/.complete")]
        final = posixpath.join(releases_root(env.node), manifest.release_id)
        assert final not in env.sftp.dirs
        assert final not in env.sftp.files
        # Temp ref released on the failure path (registry + remote ref file).
        assert not release_mod._REF_REGISTRY.get((env.node.name, manifest.release_id))
        refs_dir = posixpath.join(releases_root(env.node), ".refs")
        assert not [p for p in env.sftp.files if p.startswith(refs_dir + "/")]
        # Incomplete remote state is never trusted: with uploads still
        # failing, a second ensure must go through upload again (and fail)
        # rather than short-circuit on the partial staging dir.
        env.sftp.put_hook = lambda path: (_ for _ in ()).throw(OSError("still down"))
        with pytest.raises(OSError):
            ensure_node_release(
                env.node,
                manifest,
                stager=env.stager,
                ssh=env.pool,
                state_dir=state_dir,
                project_root=tmp_path,
            )
        assert not [p for p in env.sftp.files if p.endswith("/.complete")]

        # Heal the fault → re-ensure reuses the staging dir and uploads ONLY
        # the files that are still missing/wrong.
        env.sftp.put_hook = None
        env.sftp.put_log.clear()
        binding = ensure_node_release(
            env.node,
            manifest,
            stager=env.stager,
            ssh=env.pool,
            state_dir=state_dir,
            project_root=tmp_path,
        )
        # Exactly the still-missing files were uploaded — the verified file
        # from attempt 1 was NOT re-sent (idempotent staging reuse).
        first_rel = next(iter(manifest.files))
        assert len(env.sftp.put_log) == total - 1, (
            f"expected only the missing file(s), got {env.sftp.put_log}"
        )
        assert not any(p.endswith("/" + first_rel) for p in env.sftp.put_log), (
            "already-verified staging file must not be re-uploaded"
        )
        assert binding.release_id == manifest.release_id
        complete = posixpath.join(binding.release_dir, ".complete")
        assert complete in env.sftp.files
        # Every manifest file present under the published dir with right bytes.
        for rel, meta in manifest.files.items():
            blob = env.sftp.files[posixpath.join(binding.release_dir, rel)]
            assert hashlib.sha256(blob).hexdigest() == meta["sha256"]
    env.pool.close()


def test_local_file_deletion_new_manifest_excludes_and_old_release_kept(tmp_path):
    make_tree(tmp_path)
    env = make_env()
    state_dir = tmp_path / "state"
    m1 = build_release_manifest(tmp_path)
    with patch_client(env):
        b1 = ensure_node_release(
            env.node,
            m1,
            stager=env.stager,
            ssh=env.pool,
            state_dir=state_dir,
            project_root=tmp_path,
        )
        release_release_ref(env.node, b1.release_id, b1.ref_id, stager=env.stager, ssh=env.pool)

        # Delete a local file → new manifest excludes it, new id.
        (tmp_path / "src" / "cccp" / "core.py").unlink()
        m2 = build_release_manifest(tmp_path)
        assert m2.release_id != m1.release_id
        assert "src/cccp/core.py" in m1.files
        assert "src/cccp/core.py" not in m2.files
        b2 = ensure_node_release(
            env.node,
            m2,
            stager=env.stager,
            ssh=env.pool,
            state_dir=state_dir,
            project_root=tmp_path,
        )
        release_release_ref(env.node, b2.release_id, b2.ref_id, stager=env.stager, ssh=env.pool)

        # The old remote release still carries the deleted file.
        old_file = posixpath.join(b1.release_dir, "src/cccp/core.py")
        assert old_file in env.sftp.files
        assert posixpath.join(b2.release_dir, "src/cccp/core.py") not in env.sftp.files
        assert posixpath.join(b1.release_dir, ".complete") in env.sftp.files
    env.pool.close()


def test_second_ensure_is_remote_cache_hit_without_uploads(tmp_path):
    make_tree(tmp_path)
    env = make_env()
    manifest = build_release_manifest(tmp_path)
    state_dir = tmp_path / "state"
    with patch_client(env):
        b1 = ensure_node_release(
            env.node,
            manifest,
            stager=env.stager,
            ssh=env.pool,
            state_dir=state_dir,
            project_root=tmp_path,
        )
        release_release_ref(env.node, b1.release_id, b1.ref_id, stager=env.stager, ssh=env.pool)
        env.sftp.put_log.clear()
        env.sftp.write_log.clear()
        env.sftp.rename_log.clear()
        b2 = ensure_node_release(
            env.node,
            manifest,
            stager=env.stager,
            ssh=env.pool,
            state_dir=state_dir,
            project_root=tmp_path,
        )
        # Post-publish write prohibition: verified reads only. The only
        # write is b2's own coordination ref file under releases/.refs/.
        refs_prefix = posixpath.join(releases_root(env.node), ".refs") + "/"
        assert env.sftp.put_log == []
        assert [p for p in env.sftp.write_log if not p.startswith(refs_prefix)] == []
        assert env.sftp.rename_log == []
        assert b2.release_dir == b1.release_dir
        release_release_ref(env.node, b2.release_id, b2.ref_id, stager=env.stager, ssh=env.pool)
    env.pool.close()


def test_publish_uses_publish_lock_and_never_overwrites(tmp_path):
    """.publish.lock serialises publish; remote_rename(must_not_exist=True)
    adopted-existing path never renames over / nests into a published dir."""
    make_tree(tmp_path)
    env = make_env()
    manifest = build_release_manifest(tmp_path)
    state_dir = tmp_path / "state"
    with patch_client(env):
        b1 = ensure_node_release(
            env.node,
            manifest,
            stager=env.stager,
            ssh=env.pool,
            state_dir=state_dir,
            project_root=tmp_path,
        )
        release_release_ref(env.node, b1.release_id, b1.ref_id, stager=env.stager, ssh=env.pool)
        lock_acquires = [
            c for c in env.client.executed_commands if "mkdir" in c and ".publish.lock" in c
        ]
        assert lock_acquires, "publish must be serialised via releases/.publish.lock"
        assert any("rmdir" in c and ".publish.lock" in c for c in env.client.executed_commands)
        assert len(env.sftp.rename_log) == 1

        # force=True skips the fast paths but still must not overwrite the
        # published (immutable) dir — it verifies and adopts it.
        b2 = ensure_node_release(
            env.node,
            manifest,
            stager=env.stager,
            ssh=env.pool,
            state_dir=state_dir,
            project_root=tmp_path,
            force=True,
        )
        assert b2.release_dir == b1.release_dir
        assert len(env.sftp.rename_log) == 1, "published dir must never be re-renamed"
        # No staging/lock dir nested inside the published release.
        nested = [
            p for p in env.sftp.files if p.startswith(b1.release_dir + "/") and ".staging" in p
        ]
        assert nested == []
        release_release_ref(env.node, b2.release_id, b2.ref_id, stager=env.stager, ssh=env.pool)
    env.pool.close()


def test_publish_refuses_overwrite_of_corrupt_release(tmp_path):
    """A published dir whose content no longer matches the manifest raises
    ReleaseError and is NEVER rewritten (immutability, even when corrupt)."""
    make_tree(tmp_path)
    env = make_env()
    manifest = build_release_manifest(tmp_path)
    state_dir = tmp_path / "state"
    with patch_client(env):
        b1 = ensure_node_release(
            env.node,
            manifest,
            stager=env.stager,
            ssh=env.pool,
            state_dir=state_dir,
            project_root=tmp_path,
        )
        release_release_ref(env.node, b1.release_id, b1.ref_id, stager=env.stager, ssh=env.pool)
        victim = posixpath.join(b1.release_dir, "src/cccp/core.py")
        env.sftp.files[victim] = b"tampered"
        env.sftp.put_log.clear()

        with pytest.raises(ReleaseError, match="refusing to overwrite"):
            ensure_node_release(
                env.node,
                manifest,
                stager=env.stager,
                ssh=env.pool,
                state_dir=state_dir,
                project_root=tmp_path,
            )
        # The corrupt bytes were left exactly as found — no repair/overwrite.
        assert env.sftp.files[victim] == b"tampered"
        assert victim not in env.sftp.put_log
        assert not [
            p for p in env.sftp.files if p.startswith(b1.release_dir + "/") and ".staging" in p
        ]
    env.pool.close()


def test_remote_rename_must_not_exist_refuses_existing_target():
    env = make_env()
    with patch_client(env):
        env.sftp.dirs.add("/src/dir")
        _mkdirs(env.sftp, "/dst/dir")
        with pytest.raises(FileExistsError):
            env.stager.remote_rename(env.node, "/src/dir", "/dst/dir", must_not_exist=True)
        assert "/src/dir" in env.sftp.dirs  # untouched
        # Non-existing target renames cleanly.
        env.stager.remote_rename(env.node, "/src/dir", "/dst/dir2", must_not_exist=True)
        assert "/src/dir" not in env.sftp.dirs and "/dst/dir2" in env.sftp.dirs
    env.pool.close()


def test_remote_sha256_primitive(tmp_path):
    env = make_env()
    with patch_client(env):
        env.sftp.files["/some/file.bin"] = b"hello"
        assert (
            env.stager.remote_sha256(env.node, "/some/file.bin")
            == hashlib.sha256(b"hello").hexdigest()
        )
        with pytest.raises(FileNotFoundError):
            env.stager.remote_sha256(env.node, "/missing/file.bin")
    env.pool.close()


def test_node_side_defaults_yaml_parses_from_release_root(tmp_path):
    make_tree(tmp_path)
    env = make_env()
    manifest = build_release_manifest(tmp_path)
    with patch_client(env):
        binding = ensure_node_release(
            env.node,
            manifest,
            stager=env.stager,
            ssh=env.pool,
            state_dir=tmp_path / "state",
            project_root=tmp_path,
        )
        release_release_ref(
            env.node, binding.release_id, binding.ref_id, stager=env.stager, ssh=env.pool
        )
        node_defaults = posixpath.join(binding.release_dir, "config", "defaults.yaml")
        assert node_defaults in env.sftp.files
        parsed = yaml.safe_load(env.sftp.files[node_defaults].decode("utf-8"))
        assert isinstance(parsed, dict)
        assert parsed == yaml.safe_load((tmp_path / "config" / "defaults.yaml").read_text())
        # requirements-node.txt resolvable under the release root for bootstrap.
        node_reqs = posixpath.join(binding.release_dir, "requirements-node.txt")
        assert node_reqs in env.sftp.files
        assert env.sftp.files[node_reqs] == (tmp_path / "requirements-node.txt").read_bytes()
    env.pool.close()


def test_concurrent_staging_two_ensures_single_published_dir(tmp_path):
    """Two ensures, same id: each writes its own staging; exactly one
    published dir; no writes land in it after publish."""
    make_tree(tmp_path)
    env = make_env()
    manifest = build_release_manifest(tmp_path)
    state_dir = tmp_path / "state"
    real_publish_lock = release_mod._publish_lock
    publish_barrier = threading.Barrier(2, timeout=10)

    @contextmanager
    def slowed_publish_lock(ssh, node, **kw):
        # Both workers finish upload+verification before either publishes.
        publish_barrier.wait()
        with real_publish_lock(ssh, node, **kw):
            yield

    results: dict[str, ReleaseBinding] = {}
    errors: list[BaseException] = []

    def worker(name):
        try:
            results[name] = ensure_node_release(
                env.node,
                manifest,
                stager=env.stager,
                ssh=env.pool,
                state_dir=state_dir,
                project_root=tmp_path,
            )
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    with patch_client(env), patch.object(release_mod, "_publish_lock", slowed_publish_lock):
        threads = [threading.Thread(target=worker, args=(n,)) for n in ("a", "b")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)
        assert not errors, f"ensure failed under concurrency: {errors!r}"
        assert set(results) == {"a", "b"}

        # Each worker wrote its own staging dir.
        staging_dirs = {
            p.split("/.staging/")[1].split("/", 1)[0] for p in env.sftp.put_log if "/.staging/" in p
        }
        assert len(staging_dirs) == 2, f"expected 2 staging dirs, got {staging_dirs}"

        # Exactly one published dir, content matches the manifest.
        published = [
            d
            for d in env.sftp.dirs
            if d.startswith(releases_root(env.node) + "/")
            and d.count("/") == releases_root(env.node).count("/") + 1
            and not posixpath.basename(d).startswith(".")
        ]
        assert published == [posixpath.join(releases_root(env.node), manifest.release_id)]
        final = published[0]
        for rel, meta in manifest.files.items():
            blob = env.sftp.files[posixpath.join(final, rel)]
            assert hashlib.sha256(blob).hexdigest() == meta["sha256"]
        assert posixpath.join(final, ".complete") in env.sftp.files

        # Post-publish write prohibition: nobody wrote into the published dir
        # except the single .complete marker.
        final_prefix = final + "/"
        writes_into_final = [
            p for p in (env.sftp.put_log + env.sftp.write_log) if p.startswith(final_prefix)
        ]
        assert writes_into_final == [posixpath.join(final, ".complete")]

        # Both adopted/returned the same release; refs released by callers.
        for b in results.values():
            assert b.release_dir == final
            release_release_ref(env.node, b.release_id, b.ref_id, stager=env.stager, ssh=env.pool)
    env.pool.close()


# ====================================================================== #
# prune_releases — lock-scoped refresh + eligibility + action
# ====================================================================== #


def test_store_referenced_code_releases_scans_any_status(tmp_path):
    store = JobStore(tmp_path / "jobs.db")
    bind_release(store, "releaseAAAA", status=JobStatus.QUEUED)
    bind_release(store, "releaseBBBB", status=JobStatus.FAILED)
    store.create(
        JobRecord(
            id="plain",
            spec=JobSpec(workflow="singlepoint", input={"source": "CCO"}),
            status=JobStatus.RUNNING,
            work_dir="/tmp/x",
            result={"code_release": "releaseCCCC"},
        )
    )
    store.create(
        JobRecord(
            id="unbound",
            spec=JobSpec(workflow="singlepoint", input={"source": "CCO"}),
            status=JobStatus.COMPLETED,
            work_dir="/tmp/y",
            result={"remote": {"relative": "proj/task"}},
        )
    )
    assert store.referenced_code_releases() == {"releaseAAAA", "releaseBBBB", "releaseCCCC"}


def test_prune_keeps_referenced_releases(tmp_path):
    env = make_env()
    store = JobStore(tmp_path / "jobs.db")
    aged = time.time() - 48 * 3600
    rid_ref = "aabbccddeeff0011"
    rid_free = "1122334455667788"
    seed_release(env.sftp, env.node, rid_ref, {"src/x.py": b"a"}, mtime=aged)
    seed_release(env.sftp, env.node, rid_free, {"src/x.py": b"b"}, mtime=aged)
    bind_release(store, rid_ref, status=JobStatus.FAILED)  # any-status binding

    with patch_client(env):
        report = prune_releases(
            env.node,
            stager=env.stager,
            ssh=env.pool,
            job_store=store,
            retention_days=1,
            min_age_hours=24,
        )
    assert rid_ref in report.kept_referenced
    assert rid_free in report.pruned
    assert posixpath.join(releases_root(env.node), rid_ref) in env.sftp.dirs
    assert posixpath.join(releases_root(env.node), rid_free) not in env.sftp.dirs
    # Isolated first: the release lands in .trash before deletion.
    assert any(".trash/" in dst for _src, dst in env.sftp.rename_log), (
        "prune must isolate into releases/.trash before deleting"
    )
    env.pool.close()


def test_prune_keeps_active_ref_and_deletes_after_release(tmp_path):
    env = make_env()
    store = JobStore(tmp_path / "jobs.db")
    aged = time.time() - 48 * 3600
    rid = "a1a2a3a4a5a6a7a8"
    seed_release(env.sftp, env.node, rid, {"src/x.py": b"a"}, mtime=aged)

    with patch_client(env):
        ref_id = acquire_release_ref(env.node, rid, stager=env.stager, ssh=env.pool)
        report = prune_releases(
            env.node,
            stager=env.stager,
            ssh=env.pool,
            job_store=store,
            retention_days=1,
            min_age_hours=24,
        )
        assert rid in report.kept_active_ref
        assert posixpath.join(releases_root(env.node), rid) in env.sftp.dirs

        # Dropping the ref makes it eligible again.
        release_release_ref(env.node, rid, ref_id, stager=env.stager, ssh=env.pool)
        report2 = prune_releases(
            env.node,
            stager=env.stager,
            ssh=env.pool,
            job_store=store,
            retention_days=1,
            min_age_hours=24,
        )
        assert rid in report2.pruned
        assert posixpath.join(releases_root(env.node), rid) not in env.sftp.dirs
    env.pool.close()


def test_prune_keeps_in_window_release(tmp_path):
    env = make_env()
    store = JobStore(tmp_path / "jobs.db")
    fresh = time.time() - 60  # 1 minute old
    rid = "f1f2f3f4f5f6f7f8"
    seed_release(env.sftp, env.node, rid, {"src/x.py": b"a"}, mtime=fresh)
    with patch_client(env):
        report = prune_releases(
            env.node,
            stager=env.stager,
            ssh=env.pool,
            job_store=store,
            retention_days=0,
            min_age_hours=24,
        )
    assert rid in report.kept_in_window
    assert posixpath.join(releases_root(env.node), rid) in env.sftp.dirs
    env.pool.close()


def test_prune_retention_floor_also_protects(tmp_path):
    """retention_days and min_age_hours are both floors (window = max)."""
    env = make_env()
    store = JobStore(tmp_path / "jobs.db")
    aged_2d = time.time() - 48 * 3600
    rid = "e1e2e3e4e5e6e7e8"
    seed_release(env.sftp, env.node, rid, {"src/x.py": b"a"}, mtime=aged_2d)
    with patch_client(env):
        report = prune_releases(
            env.node,
            stager=env.stager,
            ssh=env.pool,
            job_store=store,
            retention_days=30,
            min_age_hours=24,  # 30-day floor protects a 2-day-old release
        )
    assert rid in report.kept_in_window
    assert posixpath.join(releases_root(env.node), rid) in env.sftp.dirs
    env.pool.close()


def test_prune_lock_refresh_new_binding_lands_before_lock(tmp_path):
    """A binding landing after prune starts but before the lock is taken
    must still protect the release (references refreshed INSIDE the lock)."""
    env = make_env()
    store = JobStore(tmp_path / "jobs.db")
    aged = time.time() - 48 * 3600
    rid = "b1b2b3b4b5b6b7b8"
    seed_release(env.sftp, env.node, rid, {"src/x.py": b"a"}, mtime=aged)

    real_refs_lock = release_mod._refs_lock
    injected = {"done": False}

    @contextmanager
    def injecting_refs_lock(node, ssh, **kw):
        if not injected["done"]:
            injected["done"] = True
            # Simulate: caller start → binding persisted → lock acquired.
            bind_release(store, rid, status=JobStatus.QUEUED)
        with real_refs_lock(node, ssh, **kw):
            yield

    with patch_client(env), patch.object(release_mod, "_refs_lock", injecting_refs_lock):
        report = prune_releases(
            env.node,
            stager=env.stager,
            ssh=env.pool,
            job_store=store,
            retention_days=1,
            min_age_hours=24,
        )
    assert injected["done"]
    assert rid in report.kept_referenced
    assert posixpath.join(releases_root(env.node), rid) in env.sftp.dirs
    env.pool.close()


def test_prune_eligibility_and_action_share_one_critical_section(tmp_path):
    """A DB reference injected AFTER the lock is taken (before the action)
    still protects; the coordination lock is entered exactly once."""
    env = make_env()
    store = JobStore(tmp_path / "jobs.db")
    aged = time.time() - 48 * 3600
    rid = "c1c2c3c4c5c6c7c8"
    seed_release(env.sftp, env.node, rid, {"src/x.py": b"a"}, mtime=aged)

    real_refs_lock = release_mod._refs_lock
    state = {"locks": 0, "injected": False}

    @contextmanager
    def in_lock_injecting(node, ssh, **kw):
        with real_refs_lock(node, ssh, **kw):
            state["locks"] += 1
            if not state["injected"]:
                state["injected"] = True
                bind_release(store, rid, status=JobStatus.QUEUED)
            yield

    with patch_client(env), patch.object(release_mod, "_refs_lock", in_lock_injecting):
        report = prune_releases(
            env.node,
            stager=env.stager,
            ssh=env.pool,
            job_store=store,
            retention_days=1,
            min_age_hours=24,
        )
    assert state["locks"] == 1, "refresh + eligibility + action must share ONE lock entry"
    assert rid in report.kept_referenced
    assert posixpath.join(releases_root(env.node), rid) in env.sftp.dirs
    env.pool.close()


def test_prune_isolates_to_trash_then_deletes(tmp_path):
    env = make_env()
    store = JobStore(tmp_path / "jobs.db")
    aged = time.time() - 48 * 3600
    rid = "d1d2d3d4d5d6d7d8"
    seed_release(env.sftp, env.node, rid, {"src/x.py": b"payload"}, mtime=aged)
    with patch_client(env):
        report = prune_releases(
            env.node,
            stager=env.stager,
            ssh=env.pool,
            job_store=store,
            retention_days=1,
            min_age_hours=24,
        )
    assert rid in report.pruned and not report.errors
    final = posixpath.join(releases_root(env.node), rid)
    assert final not in env.sftp.dirs
    assert not [p for p in env.sftp.files if p.startswith(final + "/")]
    # Fully removed (best-effort delete after isolate also removed the trash).
    assert not [p for p in env.sftp.files if ".trash/" in p]
    env.pool.close()


def test_concurrency_barrier_ref_hit_vs_gc(tmp_path):
    """Aged-old-release hit (ref acquisition) vs GC: critical sections are
    mutually exclusive and the release survives while the ref is held."""
    env = make_env()
    store = JobStore(tmp_path / "jobs.db")
    aged = time.time() - 48 * 3600
    rid = "9192939495969798"
    seed_release(env.sftp, env.node, rid, {"src/x.py": b"a"}, mtime=aged)

    real_lock_dir = release_mod._lock_dir
    counter = {"current": 0, "max": 0}
    counter_guard = threading.Lock()

    @contextmanager
    def counting_lock_dir(ssh, node, lock_path, **kw):
        with real_lock_dir(ssh, node, lock_path, **kw):
            with counter_guard:
                counter["current"] += 1
                counter["max"] = max(counter["max"], counter["current"])
            try:
                yield
            finally:
                with counter_guard:
                    counter["current"] -= 1

    ref_ready = threading.Event()
    gc_done = threading.Event()
    gc_reports: list = []
    errors: list[BaseException] = []

    def ref_side():
        try:
            with patch_client(env):
                ref_id = acquire_release_ref(env.node, rid, stager=env.stager, ssh=env.pool)
                ref_ready.set()
                assert gc_done.wait(10)
                release_release_ref(env.node, rid, ref_id, stager=env.stager, ssh=env.pool)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
            ref_ready.set()
            gc_done.set()

    def gc_side():
        try:
            assert ref_ready.wait(10)
            with patch_client(env):
                gc_reports.append(
                    prune_releases(
                        env.node,
                        stager=env.stager,
                        ssh=env.pool,
                        job_store=store,
                        retention_days=1,
                        min_age_hours=24,
                    )
                )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            gc_done.set()

    with patch.object(release_mod, "_lock_dir", counting_lock_dir):
        t_ref = threading.Thread(target=ref_side)
        t_gc = threading.Thread(target=gc_side)
        t_ref.start()
        t_gc.start()
        t_ref.join(timeout=20)
        t_gc.join(timeout=20)

    assert not errors, f"barrier scenario failed: {errors!r}"
    assert counter["max"] == 1, "ref acquisition and GC must be mutually exclusive"
    assert gc_reports and rid in gc_reports[0].kept_active_ref
    assert posixpath.join(releases_root(env.node), rid) in env.sftp.dirs
    env.pool.close()


# ====================================================================== #
# GC wiring: RemoteCleanup / cleanup_old_jobs / (B) decoupling / (C) path
# ====================================================================== #


def test_remote_cleanup_receives_job_store_from_manager():
    """DB-capable wiring: manager injects its store into RemoteCleanup."""
    from acp.scheduler.manager import JobManager

    node = make_node()
    config = make_config(node)

    def factory(n, timeout=30):
        return FakeSSHClient(FakeSFTP())

    with tempfile.TemporaryDirectory() as tmp:
        with patch.object(ssh_mod, "_create_client", side_effect=factory):
            mgr = JobManager(run_root=tmp, max_running=1, remote_config=config)
            assert mgr.remote_cleanup is not None
            assert mgr.remote_cleanup._job_store is mgr.store
            mgr.shutdown()
    env = make_env()
    cleanup = make_cleanup(env, job_store=None)
    assert cleanup._job_store is None
    env.pool.close()


def test_cleanup_old_jobs_passes_job_store_to_prune(tmp_path):
    env = make_env()
    store = JobStore(tmp_path / "jobs.db")
    cleanup = make_cleanup(env, job_store=store)
    spy = MagicMock()
    with patch_client(env), patch("acp.scheduler.remote.cleanup.prune_releases", spy):
        report = cleanup.cleanup_old_jobs(env.node, retention_days=1)
    assert spy.call_count == 1
    assert spy.call_args.kwargs["job_store"] is store
    assert spy.call_args.kwargs["retention_days"] == 1
    assert report.ok
    env.pool.close()


def test_cleanup_old_jobs_without_job_store_never_prunes():
    env = make_env()
    cleanup = make_cleanup(env, job_store=None)
    spy = MagicMock()
    with patch_client(env), patch("acp.scheduler.remote.cleanup.prune_releases", spy):
        cleanup.cleanup_old_jobs(env.node, retention_days=1)
    assert spy.call_count == 0, "prune_releases must not run without a DB handle"
    env.pool.close()


def test_cleanup_old_jobs_with_release_gc_false_never_prunes(tmp_path):
    env = make_env()
    store = JobStore(tmp_path / "jobs.db")
    cleanup = make_cleanup(env, job_store=store)
    spy = MagicMock()
    with patch_client(env), patch("acp.scheduler.remote.cleanup.prune_releases", spy):
        cleanup.cleanup_old_jobs(env.node, retention_days=1, with_release_gc=False)
    assert spy.call_count == 0
    env.pool.close()


def test_pre_submit_housekeeping_db_less_path_prune_spy_zero(tmp_path):
    """Choice (B): pre_submit_housekeeping → cleanup_old_jobs(
    with_release_gc=False) ⇒ prune_releases spy == 0 even on a DB-capable
    RemoteCleanup instance (no task-dir deletion, no release GC)."""
    env = make_env()
    store = JobStore(tmp_path / "jobs.db")
    cleanup = make_cleanup(env, job_store=store)
    set_disk_pct(env.client, 92)  # above cleanup threshold → sweep triggered

    observed_flags: list = []
    original = RemoteCleanup.cleanup_old_jobs

    def spy(self, node, *args, **kwargs):
        observed_flags.append(kwargs.get("with_release_gc", "<default>"))
        return original(self, node, *args, **kwargs)

    prune_spy = MagicMock()
    with (
        patch_client(env),
        patch("acp.scheduler.remote.cleanup.prune_releases", prune_spy),
        patch.object(RemoteCleanup, "cleanup_old_jobs", spy),
    ):
        decision = cleanup.pre_submit_housekeeping(env.node)

    assert prune_spy.call_count == 0, "DB-less pre-submit path must never prune releases"
    assert observed_flags == [False], (
        "pre_submit_housekeeping must call cleanup_old_jobs with with_release_gc=False"
    )
    assert decision.should_skip is False
    env.pool.close()


def test_production_trigger_reaches_release_gc():
    """(C) Production reachability: the JobManager startup trigger runs
    cleanup_old_jobs(..., with_release_gc=True) with the injected store and
    reaches prune_releases ≥1 time (release GC is NOT dead code)."""
    import acp.scheduler.manager as manager_mod
    from acp.scheduler.manager import JobManager

    node = make_node()
    config = make_config(node)
    spy = MagicMock()

    def factory(n, timeout=30):
        return FakeSSHClient(FakeSFTP())

    with tempfile.TemporaryDirectory() as tmp:
        with (
            patch.object(ssh_mod, "_create_client", side_effect=factory),
            patch.object(manager_mod, "_RELEASE_GC_STARTUP_DELAY", 0.0),
            patch("acp.scheduler.remote.cleanup.prune_releases", spy),
        ):
            mgr = JobManager(run_root=tmp, max_running=1, remote_config=config)
            deadline = time.time() + 10
            while spy.call_count == 0 and time.time() < deadline:
                time.sleep(0.05)
            assert spy.call_count >= 1, (
                "release GC not reached through the production startup trigger"
            )
            assert spy.call_args.kwargs.get("job_store") is mgr.store
            # Direct unit calls are NOT what this proves: the call arrived
            # via JobManager's own production wiring.
            assert mgr.remote_cleanup is not None
            mgr.shutdown()


def test_release_gc_reachable_through_background_retention_tick():
    """(C) second production surface: the periodic retention loop tick."""
    import acp.scheduler.manager as manager_mod
    from acp.scheduler.manager import JobManager

    node = make_node()
    config = make_config(node)
    spy = MagicMock()

    def factory(n, timeout=30):
        return FakeSSHClient(FakeSFTP())

    with tempfile.TemporaryDirectory() as tmp:
        with (
            patch.object(ssh_mod, "_create_client", side_effect=factory),
            patch.object(manager_mod, "_RELEASE_GC_STARTUP_DELAY", 3600.0),
            patch("acp.scheduler.remote.cleanup.prune_releases", spy),
        ):
            mgr = JobManager(run_root=tmp, max_running=1, remote_config=config)
            # Production tick (what _cleanup_loop invokes each interval).
            mgr._run_remote_release_gc()
            assert spy.call_count >= 1
            assert spy.call_args.kwargs.get("job_store") is mgr.store
            mgr.shutdown()


# ====================================================================== #
# E2E matrix (todo 9): cross-package move + ref-protected retention
# ====================================================================== #


def test_cross_package_move_temp_tree_new_file_set_old_release_untouched(tmp_path):
    """E2E ④: moving a ``src/acp`` file to a ``src/cccp`` path inside a
    TEMPORARY copy tree mints a new release with the correct file set;
    the old release stays byte-identical.  The live ``src/cccp/**`` tree
    is never touched — everything happens under tmp_path."""
    make_tree(tmp_path)
    env = make_env()
    state_dir = tmp_path / "state"
    m1 = build_release_manifest(tmp_path)
    assert "src/acp/engine.py" in m1.files
    assert "src/cccp/engine.py" not in m1.files

    with patch_client(env):
        b1 = ensure_node_release(
            env.node,
            m1,
            stager=env.stager,
            ssh=env.pool,
            state_dir=state_dir,
            project_root=tmp_path,
        )
        release_release_ref(env.node, b1.release_id, b1.ref_id, stager=env.stager, ssh=env.pool)
        snapshot = {
            p: blob for p, blob in env.sftp.files.items() if p.startswith(b1.release_dir + "/")
        }

        # Cross-package move inside the TEMP copy only.
        src = tmp_path / "src" / "acp" / "engine.py"
        dst = tmp_path / "src" / "cccp" / "engine.py"
        src.rename(dst)
        assert not (Path("src/cccp/engine.py")).exists(), "live tree must never gain this file"

        m2 = build_release_manifest(tmp_path)
        assert m2.release_id != m1.release_id
        assert "src/acp/engine.py" not in m2.files
        assert "src/cccp/engine.py" in m2.files
        assert (
            m2.files["src/cccp/engine.py"]["sha256"] == hashlib.sha256(b"VALUE = 1\n").hexdigest()
        )
        assert set(m2.files) == {
            p.relative_to(tmp_path).as_posix() for p in build_sync_file_list(tmp_path)
        }

        b2 = ensure_node_release(
            env.node,
            m2,
            stager=env.stager,
            ssh=env.pool,
            state_dir=state_dir,
            project_root=tmp_path,
        )
        # Old release unchanged (byte-for-byte) after the new publish.
        assert {
            p: blob for p, blob in env.sftp.files.items() if p.startswith(b1.release_dir + "/")
        } == snapshot
        assert posixpath.join(b1.release_dir, "src/acp/engine.py") in env.sftp.files
        assert posixpath.join(b2.release_dir, "src/cccp/engine.py") in env.sftp.files
        assert posixpath.join(b2.release_dir, "src/acp/engine.py") not in env.sftp.files
        assert posixpath.join(b2.release_dir, ".complete") in env.sftp.files
    env.pool.close()


def test_prune_keeps_queued_referenced_release_and_never_touches_shared_dir(tmp_path):
    """E2E ⑤: a QUEUED job's release (bound pre-bsub) survives prune; an
    unreferenced aged release is reclaimed; the dev-hatch shared dir
    (``unversioned-shared`` provenance) is outside retention entirely."""
    env = make_env()
    store = JobStore(tmp_path / "jobs.db")
    aged = time.time() - 48 * 3600
    rid_queued = "5152535455565758"
    rid_free = "6162636465666768"
    seed_release(env.sftp, env.node, rid_queued, {"src/x.py": b"q"}, mtime=aged)
    seed_release(env.sftp, env.node, rid_free, {"src/x.py": b"f"}, mtime=aged)
    bind_release(store, rid_queued, status=JobStatus.QUEUED)

    # Dev-escape-hatch job: provenance pinned to unversioned-shared.
    store.create(
        JobRecord(
            id="devhatch",
            spec=JobSpec(workflow="singlepoint", input={"source": "CCO"}),
            status=JobStatus.COMPLETED,
            work_dir="/tmp/dev",
            result={"remote": {"code_release": "unversioned-shared"}},
        )
    )
    # Shared dir (mutable, dev mode) with equally-aged content.
    shared_root = posixpath.join(env.node.remote_code_dir, "src", "acp")
    shared_file = posixpath.join(shared_root, "__init__.py")
    _mkdirs(env.sftp, shared_root)
    env.sftp.files[shared_file] = b'"""shared"""\n'
    env.sftp.dir_mtimes[env.node.remote_code_dir] = aged
    env.sftp.dir_mtimes[shared_root] = aged

    with patch_client(env):
        report = prune_releases(
            env.node,
            stager=env.stager,
            ssh=env.pool,
            job_store=store,
            retention_days=1,
            min_age_hours=24,
        )
    assert rid_queued in report.kept_referenced, "QUEUED-referenced release must survive"
    assert rid_free in report.pruned
    # Shared dir + contents never enter the retention deletion scope.
    assert shared_file in env.sftp.files
    assert shared_root in env.sftp.dirs
    assert env.node.remote_code_dir in env.sftp.dirs
    env.pool.close()
