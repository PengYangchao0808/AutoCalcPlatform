"""
Phase 5 tests — RemoteCleanup (remote file lifecycle management).

Verifies retention-based cleanup and pre-submit disk-pressure housekeeping
without a real compute node, using mock SSH/SFTP fakes (same pattern as
Phase 1-4 tests):

- cleanup_old_jobs: removes old dirs, keeps fresh, dry-run, top-level
  files skipped, unknown mtime skipped, unsafe base path rejected,
  missing work dir, listdir failure, retention override, base-dir guard,
  freed-bytes estimate via ``du -sb``.
- pre_submit_housekeeping: low-disk no-op, cleanup-threshold triggers
  sweep, skip-threshold rejects, disk-query failure fails open,
  cleanup exception recorded.
- _is_safe_work_dir: path safety guards.
- RemoteJobRunner integration: cleanup disabled is a no-op, should_skip
  raises RemoteNodeUnavailableError, housekeeping crash fails open.
- JobManager.remote_cleanup wiring.
- Constructor threshold validation.

Run with: PYTHONPATH=src python3 tests/test_remote_phase5.py
"""

from __future__ import annotations

import io
import posixpath
import shlex
import stat
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from acp.scheduler.events import JobEventLog
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.remote import ssh as ssh_mod
from acp.scheduler.remote.cleanup import (
    DEFAULT_MAX_DIRS_PER_SWEEP,
    DISK_CLEANUP_THRESHOLD,
    DISK_SKIP_THRESHOLD,
    CleanupReport,
    HousekeepingDecision,
    RemoteCleanup,
    _format_bytes,
    _is_safe_work_dir,
)
from acp.scheduler.remote.config import RemoteExecutionConfig, RemoteNode
from acp.scheduler.remote.monitor import RemoteJobMonitor
from acp.scheduler.remote.runner import RemoteJobRunner, RemoteNodeUnavailableError
from acp.scheduler.remote.sftp import FileStager
from acp.scheduler.remote.ssh import SSHConnectionPool

# ====================================================================== #
# Mock infrastructure
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
    """SFTP fake supporting listdir_attr with controllable mtimes."""

    def __init__(self):
        # path -> (size, mtime, is_dir)
        self.entries: dict[str, tuple[int, float, bool]] = {}
        self.files: dict[str, bytes] = {}

    def _add_dir(self, path, mtime=0.0):
        self.entries[path] = (0, mtime, True)

    def _add_file(self, path, size=0, mtime=0.0, content=b""):
        self.entries[path] = (size, mtime, False)
        self.files[path] = content or (b"x" * size)

    def put(self, localpath, remotepath):
        with open(localpath, "rb") as f:
            data = f.read()
        self._add_file(remotepath, size=len(data), content=data)

    def file(self, remote_path, mode="r"):
        if "w" in mode or "a" in mode:
            f = FakeSFTPFile(b"", mode)
            original_path = remote_path

            def _on_close():
                data = f.getvalue()
                self.files[original_path] = data
                self.entries[original_path] = (len(data), time.time(), False)

            f.close = _on_close  # type: ignore[assignment]
            return f
        data = self.files.get(remote_path, b"")
        return FakeSFTPFile(data, mode)

    def stat(self, path):
        if path not in self.entries:
            raise FileNotFoundError(path)
        return self._attr(path)

    def _attr(self, path):
        size, mtime, is_dir = self.entries[path]
        a = MagicMock()
        a.st_size = size
        a.st_mtime = mtime
        a.st_mode = stat.S_IFDIR if is_dir else stat.S_IFREG
        return a

    def listdir_attr(self, path):
        prefix = path.rstrip("/") + "/" if path != "/" else "/"
        result = []
        for fpath in list(self.entries.keys()):
            if fpath == path:
                continue
            if fpath.startswith(prefix):
                rest = fpath[len(prefix) :]
                if "/" not in rest and rest:
                    size, mtime, is_dir = self.entries[fpath]
                    a = MagicMock()
                    a.filename = rest
                    a.st_size = size
                    a.st_mtime = mtime
                    a.st_mode = stat.S_IFDIR if is_dir else stat.S_IFREG
                    result.append(a)
        return result

    def listdir(self, path):
        return [a.filename for a in self.listdir_attr(path)]

    def mkdir(self, path):
        self._add_dir(path)

    def remove(self, path):
        self.entries.pop(path, None)
        self.files.pop(path, None)

    def close(self):
        pass


class FakeSSHClient:
    """SSH fake whose exec_command handles rm -rf and du -sb."""

    def __init__(self, fake_sftp: FakeSFTP):
        self.fake_sftp = fake_sftp
        self.closed = False
        self._transport = MagicMock()
        self._transport.is_active.return_value = True
        self.executed_commands: list[str] = []
        self.cmd_handler = None

    def set_missing_host_key_policy(self, policy):
        pass

    def connect(self, **kwargs):
        pass

    def get_transport(self):
        return self._transport

    def exec_command(self, command, timeout=None):
        self.executed_commands.append(command)
        if self.cmd_handler is not None:
            code, out, err = self.cmd_handler(command)
        else:
            code, out, err = self._default_handler(command)
        stdin = MagicMock()
        stdout = MagicMock()
        stderr = MagicMock()
        stdout.read.return_value = out.encode("utf-8")
        stderr.read.return_value = err.encode("utf-8")
        stdout.channel = MagicMock()
        stdout.channel.recv_exit_status.return_value = code
        return stdin, stdout, stderr

    def _default_handler(self, command: str) -> tuple[int, str, str]:
        # rm -rf <path> — remove the subtree from the fake fs.
        stripped = command.strip()
        if stripped.startswith("rm -rf"):
            target = stripped[len("rm -rf") :].strip()
            target = shlex.split(target)[0] if shlex.split(target) else ""
            self._remove_tree(target)
            return 0, "", ""
        # du -sb <path>
        if stripped.startswith("du -sb"):
            target = stripped[len("du -sb") :].strip()
            target = shlex.split(target)[0] if shlex.split(target) else ""
            size = self._tree_size(target)
            return 0, f"{size}\t{target}\n", ""
        # df -P <path> | ... — handled by tests via cmd_handler override.
        return 0, "", ""

    def _remove_tree(self, target: str) -> None:
        target = posixpath.normpath(target)
        sftp = self.fake_sftp
        for fpath in list(sftp.entries.keys()):
            norm = posixpath.normpath(fpath)
            if norm == target or norm.startswith(target.rstrip("/") + "/"):
                sftp.entries.pop(fpath, None)
                sftp.files.pop(fpath, None)

    def _tree_size(self, target: str) -> int:
        target = posixpath.normpath(target)
        sftp = self.fake_sftp
        total = 0
        for fpath, (size, _mt, _isdir) in sftp.entries.items():
            norm = posixpath.normpath(fpath)
            if norm == target or norm.startswith(target.rstrip("/") + "/"):
                if not _isdir:
                    total += size
        return total

    def open_sftp(self):
        return self.fake_sftp

    def close(self):
        self.closed = True


# ====================================================================== #
# Fixtures
# ====================================================================== #


def make_node(name="compute-01", **kw) -> RemoteNode:
    defaults = dict(
        name=name,
        host="10.0.0.1",
        username="testuser",
        remote_work_dir="/scratch/test/acp_jobs",
        remote_code_dir="/home/test/acp_code",
        max_concurrent_jobs=3,
        host_key_policy="auto_add",
    )
    defaults.update(kw)
    return RemoteNode(**defaults)


def make_config(node: RemoteNode, **kw) -> RemoteExecutionConfig:
    defaults = dict(execution_mode="remote", nodes=[node], retention_days=180)
    defaults.update(kw)
    return RemoteExecutionConfig(**defaults)


def make_cleanup(
    sftp: FakeSFTP,
    node: RemoteNode,
    *,
    cleanup_threshold=DISK_CLEANUP_THRESHOLD,
    skip_threshold=DISK_SKIP_THRESHOLD,
    retention_days=180,
) -> tuple[RemoteCleanup, SSHConnectionPool, FakeSSHClient]:
    """Build a RemoteCleanup backed by fakes. Returns (cleanup, pool, client)."""
    pool = SSHConnectionPool()
    client = FakeSSHClient(sftp)

    def factory(n, timeout=30):
        return client

    stager = FileStager(pool)
    config = make_config(node, retention_days=retention_days)
    monitor = RemoteJobMonitor(pool, stager)
    cleanup = RemoteCleanup(
        ssh_pool=pool,
        stager=stager,
        remote_config=config,
        monitor=monitor,
        cleanup_threshold=cleanup_threshold,
        skip_threshold=skip_threshold,
    )
    return cleanup, pool, client


def patch_client(factory):
    return patch.object(ssh_mod, "_create_client", side_effect=factory)


# ====================================================================== #
# _is_safe_work_dir
# ====================================================================== #


def test_safe_work_dir_valid():
    assert _is_safe_work_dir("/scratch/test/acp_jobs") is True
    assert _is_safe_work_dir("/data/acp_jobs") is True


def test_safe_work_dir_rejects_root():
    assert _is_safe_work_dir("/") is False
    assert _is_safe_work_dir("") is False
    assert _is_safe_work_dir(".") is False
    assert _is_safe_work_dir("..") is False
    assert _is_safe_work_dir("~") is False


def test_safe_work_dir_rejects_shallow():
    # Only one component under root — too shallow.
    assert _is_safe_work_dir("/scratch") is False


def test_safe_work_dir_rejects_relative():
    assert _is_safe_work_dir("relative/path") is False


# ====================================================================== #
# cleanup_old_jobs
# ====================================================================== #


def _setup_workdir(sftp: FakeSFTP, base: str):
    """Populate a fake remote_work_dir with old + fresh + file entries."""
    sftp._add_dir(base, mtime=time.time())
    cutoff_old = time.time() - 200 * 86400  # 200 days ago
    cutoff_fresh = time.time() - 10 * 86400  # 10 days ago
    # Old job dirs (should be removed).
    sftp._add_dir(posixpath.join(base, "old_job_1"), mtime=cutoff_old)
    sftp._add_dir(posixpath.join(base, "old_job_2"), mtime=cutoff_old)
    sftp._add_file(
        posixpath.join(base, "old_job_1", "result.xyz"),
        size=1000,
        mtime=cutoff_old,
    )
    sftp._add_file(
        posixpath.join(base, "old_job_2", "result.xyz"),
        size=2000,
        mtime=cutoff_old,
    )
    # Fresh job dir (should be kept).
    sftp._add_dir(posixpath.join(base, "fresh_job"), mtime=cutoff_fresh)
    sftp._add_file(
        posixpath.join(base, "fresh_job", "result.xyz"),
        size=500,
        mtime=cutoff_fresh,
    )
    # Stray top-level file (should be left alone).
    sftp._add_file(posixpath.join(base, "README.txt"), size=42, mtime=cutoff_old)


def test_cleanup_removes_old_keeps_fresh():
    sftp = FakeSFTP()
    node = make_node()
    base = node.remote_work_dir
    _setup_workdir(sftp, base)

    cleanup, pool, client = make_cleanup(sftp, node)
    with patch_client(lambda n, timeout=30: client):
        report = cleanup.cleanup_old_jobs(node, retention_days=180)

    assert report.ok
    assert len(report.removed_dirs) == 2
    assert any("old_job_1" in d for d in report.removed_dirs)
    assert any("old_job_2" in d for d in report.removed_dirs)
    assert report.freed_bytes_est == 3000  # 1000 + 2000
    # Fresh dir still present.
    assert posixpath.join(base, "fresh_job") in sftp.entries
    # Stray file untouched.
    assert posixpath.join(base, "README.txt") in sftp.entries
    # Old dirs + their contents gone.
    assert posixpath.join(base, "old_job_1") not in sftp.entries
    assert posixpath.join(base, "old_job_2") not in sftp.entries
    pool.close()
    print("  [OK] cleanup_old_jobs: removed 2 old dirs, kept fresh + stray file")


def test_cleanup_dry_run_no_mutation():
    sftp = FakeSFTP()
    node = make_node()
    base = node.remote_work_dir
    _setup_workdir(sftp, base)

    cleanup, pool, client = make_cleanup(sftp, node)
    with patch_client(lambda n, timeout=30: client):
        report = cleanup.cleanup_old_jobs(node, retention_days=180, dry_run=True)

    assert report.dry_run is True
    assert len(report.removed_dirs) == 2
    assert report.freed_bytes_est == 3000
    # Nothing actually deleted.
    assert posixpath.join(base, "old_job_1") in sftp.entries
    assert posixpath.join(base, "old_job_2") in sftp.entries
    pool.close()
    print("  [OK] cleanup_old_jobs: dry_run reports but does not delete")


def test_cleanup_uses_config_retention_default():
    sftp = FakeSFTP()
    node = make_node()
    base = node.remote_work_dir
    _setup_workdir(sftp, base)

    # retention_days=None should fall back to config (180).
    cleanup, pool, client = make_cleanup(sftp, node, retention_days=180)
    with patch_client(lambda n, timeout=30: client):
        report = cleanup.cleanup_old_jobs(node)
    assert report.retention_days == 180
    assert len(report.removed_dirs) == 2
    pool.close()
    print("  [OK] cleanup_old_jobs: uses config retention_days when None")


def test_cleanup_retention_override_keeps_recently_old():
    sftp = FakeSFTP()
    node = make_node()
    base = node.remote_work_dir
    # 50-day-old dir — would survive a 180-day retention but not a 30-day one.
    sftp._add_dir(base, mtime=time.time())
    sftp._add_dir(posixpath.join(base, "fifty_days"), mtime=time.time() - 50 * 86400)

    cleanup, pool, client = make_cleanup(sftp, node)
    with patch_client(lambda n, timeout=30: client):
        report = cleanup.cleanup_old_jobs(node, retention_days=30)
    assert len(report.removed_dirs) == 1
    pool.close()
    print("  [OK] cleanup_old_jobs: retention_days override honoured")


def test_cleanup_skips_unknown_mtime():
    sftp = FakeSFTP()
    node = make_node()
    base = node.remote_work_dir
    sftp._add_dir(base, mtime=time.time())
    # mtime=0 → unknown, must be skipped.
    sftp._add_dir(posixpath.join(base, "unknown_age"), mtime=0.0)

    cleanup, pool, client = make_cleanup(sftp, node)
    with patch_client(lambda n, timeout=30: client):
        report = cleanup.cleanup_old_jobs(node, retention_days=10)
    assert report.skipped == 1
    assert len(report.removed_dirs) == 0
    assert posixpath.join(base, "unknown_age") in sftp.entries
    pool.close()
    print("  [OK] cleanup_old_jobs: skips dirs with unknown mtime")


def test_cleanup_unsafe_base_path():
    sftp = FakeSFTP()
    # remote_work_dir = "/" → rejected.
    node = make_node(remote_work_dir="/")
    cleanup, pool, client = make_cleanup(sftp, node)
    with patch_client(lambda n, timeout=30: client):
        report = cleanup.cleanup_old_jobs(node)
    assert not report.ok
    assert len(report.errors) == 1
    assert "unsafe" in report.errors[0]
    assert len(report.removed_dirs) == 0
    pool.close()
    print("  [OK] cleanup_old_jobs: unsafe remote_work_dir rejected")


def test_cleanup_missing_work_dir():
    sftp = FakeSFTP()
    node = make_node()
    cleanup, pool, client = make_cleanup(sftp, node)
    # No entries at all → list_remote_dir raises FileNotFoundError.
    with patch_client(lambda n, timeout=30: client):
        report = cleanup.cleanup_old_jobs(node)
    assert report.ok  # no errors — missing dir is not an error
    assert len(report.removed_dirs) == 0
    pool.close()
    print("  [OK] cleanup_old_jobs: missing remote_work_dir → empty report")


def test_cleanup_listdir_failure_recorded():
    sftp = FakeSFTP()
    node = make_node()
    cleanup, pool, client = make_cleanup(sftp, node)

    # Force list_remote_dir to raise a non-FileNotFoundError.
    def boom(self, n, p):
        raise OSError("permission denied")

    with patch_client(lambda n, timeout=30: client):
        with patch.object(FileStager, "list_remote_dir", boom):
            report = cleanup.cleanup_old_jobs(node)
    assert not report.ok
    assert any("list_remote_dir" in e for e in report.errors)
    pool.close()
    print("  [OK] cleanup_old_jobs: listdir failure recorded in report")


def test_cleanup_never_removes_base_dir():
    sftp = FakeSFTP()
    node = make_node()
    base = node.remote_work_dir
    # Base dir itself is "old".
    sftp._add_dir(base, mtime=time.time() - 400 * 86400)
    sftp._add_dir(posixpath.join(base, "old_job"), mtime=time.time() - 400 * 86400)

    cleanup, pool, client = make_cleanup(sftp, node)
    with patch_client(lambda n, timeout=30: client):
        report = cleanup.cleanup_old_jobs(node, retention_days=180)
    # Base must survive even though it's old.
    assert base in sftp.entries
    assert len(report.removed_dirs) == 1  # only old_job
    pool.close()
    print("  [OK] cleanup_old_jobs: never removes the base dir")


def test_cleanup_report_to_dict():
    report = CleanupReport(
        node="n1",
        retention_days=30,
        removed_dirs=["/a", "/b"],
        skipped=1,
        errors=["e"],
        freed_bytes_est=100,
        dry_run=False,
    )
    d = report.to_dict()
    assert d["node"] == "n1"
    assert d["removed_dirs"] == ["/a", "/b"]
    assert d["freed_bytes_est"] == 100
    assert d["ok"] is False  # has error
    assert d["dry_run"] is False
    assert d["capped"] is False
    print("  [OK] CleanupReport.to_dict serialization")


def test_cleanup_respects_max_dirs_cap():
    """P1-2: max_dirs_per_sweep caps the number of removals per call."""
    sftp = FakeSFTP()
    node = make_node()
    base = node.remote_work_dir
    sftp._add_dir(base, mtime=time.time())
    old = time.time() - 400 * 86400
    # Create 10 old dirs; cap at 3.
    for i in range(10):
        d = posixpath.join(base, f"old_{i}")
        sftp._add_dir(d, mtime=old)
        sftp._add_file(posixpath.join(d, "f.bin"), size=100, mtime=old)

    cleanup, pool, client = make_cleanup(sftp, node)
    with patch_client(lambda n, timeout=30: client):
        report = cleanup.cleanup_old_jobs(node, retention_days=180, max_dirs_per_sweep=3)

    assert report.capped is True
    assert len(report.removed_dirs) == 3
    # The remaining 7 old dirs still exist for the next pass.
    remaining_old = [
        d
        for d, (_sz, _mt, is_dir) in sftp.entries.items()
        if is_dir and d.startswith(posixpath.join(base, "old_"))
    ]
    assert len(remaining_old) == 7
    pool.close()
    print("  [OK] cleanup_old_jobs: max_dirs_per_sweep caps removals + sets capped flag")


def test_cleanup_default_cap_is_100():
    assert DEFAULT_MAX_DIRS_PER_SWEEP == 100
    print("  [OK] DEFAULT_MAX_DIRS_PER_SWEEP == 100")


def test_cleanup_unlimited_when_zero():
    """max_dirs_per_sweep=0 means unlimited."""
    sftp = FakeSFTP()
    node = make_node()
    base = node.remote_work_dir
    sftp._add_dir(base, mtime=time.time())
    old = time.time() - 400 * 86400
    for i in range(5):
        d = posixpath.join(base, f"old_{i}")
        sftp._add_dir(d, mtime=old)

    cleanup, pool, client = make_cleanup(sftp, node)
    with patch_client(lambda n, timeout=30: client):
        report = cleanup.cleanup_old_jobs(node, retention_days=180, max_dirs_per_sweep=0)

    assert report.capped is False
    assert len(report.removed_dirs) == 5
    pool.close()
    print("  [OK] cleanup_old_jobs: max_dirs_per_sweep=0 → unlimited")


# ====================================================================== #
# pre_submit_housekeeping
# ====================================================================== #


def _set_disk_pct(client: FakeSSHClient, pct: int):
    """Make check_disk_usage (df -P ... | awk) return *pct*."""

    def handler(command):
        s = command.strip()
        if s.startswith("df"):
            # check_disk_usage parses: out.strip().rstrip("%").strip() -> int
            return 0, f"{pct}%\n", ""
        return client._default_handler(command)

    client.cmd_handler = handler


def test_housekeeping_low_disk_noop():
    sftp = FakeSFTP()
    node = make_node()
    cleanup, pool, client = make_cleanup(sftp, node)
    _set_disk_pct(client, 45)
    with patch_client(lambda n, timeout=30: client):
        decision = cleanup.pre_submit_housekeeping(node)
    assert decision.should_skip is False
    assert decision.cleanup is None
    assert decision.disk_usage_before == 45
    pool.close()
    print("  [OK] housekeeping: low disk → no cleanup, proceed")


def test_housekeeping_high_disk_triggers_cleanup_proceeds():
    sftp = FakeSFTP()
    node = make_node()
    base = node.remote_work_dir
    _setup_workdir(sftp, base)  # 2 old dirs

    cleanup, pool, client = make_cleanup(sftp, node)
    # Before: 92% (> cleanup threshold 90). After cleanup the test's
    # df handler still returns 92 (we don't model df reflecting deletions),
    # but 92 < skip(95) so we proceed.
    _set_disk_pct(client, 92)
    with patch_client(lambda n, timeout=30: client):
        decision = cleanup.pre_submit_housekeeping(node)
    assert decision.should_skip is False
    assert decision.cleanup is not None
    assert len(decision.cleanup.removed_dirs) == 2
    pool.close()
    print("  [OK] housekeeping: 92% → cleanup runs, proceeds (< 95%)")


def test_housekeeping_skip_above_threshold():
    sftp = FakeSFTP()
    node = make_node()
    base = node.remote_work_dir
    _setup_workdir(sftp, base)

    cleanup, pool, client = make_cleanup(sftp, node)
    _set_disk_pct(client, 97)  # > skip threshold even after cleanup
    with patch_client(lambda n, timeout=30: client):
        decision = cleanup.pre_submit_housekeeping(node)
    assert decision.should_skip is True
    assert "exceeds skip threshold" in decision.reason
    assert decision.cleanup is not None  # cleanup was attempted
    pool.close()
    print("  [OK] housekeeping: 97% → should_skip=True")


def test_housekeeping_skip_just_above_cleanup_no_old_jobs():
    sftp = FakeSFTP()
    node = make_node()
    base = node.remote_work_dir
    sftp._add_dir(base, mtime=time.time())  # empty, fresh
    cleanup, pool, client = make_cleanup(sftp, node)
    _set_disk_pct(client, 96)  # above both thresholds, nothing to clean
    with patch_client(lambda n, timeout=30: client):
        decision = cleanup.pre_submit_housekeeping(node)
    assert decision.should_skip is True
    assert decision.cleanup.removed_dirs == []
    pool.close()
    print("  [OK] housekeeping: 96% + nothing to clean → skip")


def test_housekeeping_disk_query_failure_fails_open():
    sftp = FakeSFTP()
    node = make_node()
    cleanup, pool, client = make_cleanup(sftp, node)

    # df handler raises → _safe_disk_usage returns 0.
    def handler(command):
        if command.strip().startswith("df"):
            raise OSError("SSH gone")
        return client._default_handler(command)

    client.cmd_handler = handler
    with patch_client(lambda n, timeout=30: client):
        decision = cleanup.pre_submit_housekeeping(node)
    assert decision.should_skip is False
    assert decision.disk_usage_before == 0
    pool.close()
    print("  [OK] housekeeping: disk query failure → fail-open (proceed)")


def test_housekeeping_cleanup_exception_recorded():
    sftp = FakeSFTP()
    node = make_node()
    cleanup, pool, client = make_cleanup(sftp, node)
    _set_disk_pct(client, 92)

    def boom(self, n, retention_days=None, dry_run=False):
        raise RuntimeError("boom")

    with patch_client(lambda n, timeout=30: client):
        with patch.object(RemoteCleanup, "cleanup_old_jobs", boom):
            decision = cleanup.pre_submit_housekeeping(node)
    # Cleanup raised but disk is 92 < 95, so we still proceed.
    assert decision.should_skip is False
    assert decision.cleanup is not None
    assert any("cleanup raised" in e for e in decision.cleanup.errors)
    pool.close()
    print("  [OK] housekeeping: cleanup crash recorded, proceeds (< skip)")


def test_housekeeping_decision_to_dict():
    decision = HousekeepingDecision(
        node="n1",
        should_skip=False,
        disk_usage_before=50,
        disk_usage_after=50,
        cleanup=None,
        reason="ok",
    )
    d = decision.to_dict()
    assert d["should_skip"] is False
    assert d["cleanup"] is None
    assert d["disk_usage_before"] == 50
    print("  [OK] HousekeepingDecision.to_dict serialization")


def test_housekeeping_after_probe_failure_is_conservative():
    """P1-1: if before=96% but the after-probe fails (0), we must NOT proceed.

    Without the conservative fallback the code would see after=0 < 95 and
    submit on a genuinely full disk → ENOSPC mid-job.
    """
    sftp = FakeSFTP()
    node = make_node()
    cleanup, pool, client = make_cleanup(sftp, node)

    call_count = {"n": 0}

    def handler(command):
        s = command.strip()
        if s.startswith("df"):
            call_count["n"] += 1
            # First df (before): 96%.  Second df (after cleanup): fail → 0.
            if call_count["n"] == 1:
                return 0, "96%\n", ""
            return 0, "0%\n", ""  # simulated probe failure
        return client._default_handler(command)

    client.cmd_handler = handler
    with patch_client(lambda n, timeout=30: client):
        decision = cleanup.pre_submit_housekeeping(node)

    # before=96 triggered cleanup; after-probe "failed" (0) but we must be
    # conservative and treat after as 96 → should_skip=True.
    assert decision.disk_usage_before == 96
    assert decision.should_skip is True
    pool.close()
    print("  [OK] housekeeping: after-probe failure is conservative (uses before)")


def test_constructor_threshold_validation():
    node = make_node()
    pool = SSHConnectionPool()
    stager = FileStager(pool)
    config = make_config(node)
    monitor = RemoteJobMonitor(pool, stager)
    with pytest.raises(ValueError, match="must not exceed"):
        RemoteCleanup(
            ssh_pool=pool,
            stager=stager,
            remote_config=config,
            monitor=monitor,
            cleanup_threshold=98,
            skip_threshold=95,
        )
    pool.close()
    print("  [OK] constructor: cleanup_threshold > skip_threshold raises")


# ====================================================================== #
# Runner integration
# ====================================================================== #


def _make_runner(sftp, node, cleanup=None):
    """Build a RemoteJobRunner with stubbed dependencies (no real submission)."""
    pool = SSHConnectionPool()
    client = FakeSSHClient(sftp)
    stager = FileStager(pool)
    config = make_config(node)
    monitor = RemoteJobMonitor(pool, stager)
    syncer = MagicMock()  # code sync stubbed out
    syncer.check_sync_needed.return_value = False
    runner = RemoteJobRunner(
        ssh_pool=pool,
        remote_config=config,
        stager=stager,
        monitor=monitor,
        code_syncer=syncer,
        cleanup=cleanup,
    )
    return runner, pool, client


def test_runner_housekeeping_disabled_is_noop():
    sftp = FakeSFTP()
    node = make_node()
    runner, pool, client = _make_runner(sftp, node, cleanup=None)

    events: list[dict] = []

    class FakeLog:
        def append(self, event, **kw):
            events.append({"event": event, **kw})

    with patch_client(lambda n, timeout=30: client):
        runner._pre_submit_housekeeping(node, FakeLog(), "job1")  # type: ignore[arg-type]

    # No housekeeping event emitted (cleanup is None).
    assert not any(e["event"] == "remote.housekeeping" for e in events)
    pool.close()
    print("  [OK] runner: housekeeping disabled → no-op, no event")


def test_runner_housekeeping_proceeds():
    sftp = FakeSFTP()
    node = make_node()
    cleanup, pool_c, client_c = make_cleanup(sftp, node)
    runner, pool, client = _make_runner(sftp, node, cleanup=cleanup)
    _set_disk_pct(client, 40)

    events: list[dict] = []

    class FakeLog:
        def append(self, event, **kw):
            events.append({"event": event, **kw})

    with patch_client(lambda n, timeout=30: client):
        runner._pre_submit_housekeeping(node, FakeLog(), "job1")  # type: ignore[arg-type]

    hk = [e for e in events if e["event"] == "remote.housekeeping"]
    assert len(hk) == 1
    assert hk[0]["should_skip"] is False
    assert hk[0]["disk_before"] == 40
    pool.close()
    pool_c.close()
    print("  [OK] runner: housekeeping proceeds + emits event")


def test_runner_housekeeping_skip_raises():
    sftp = FakeSFTP()
    node = make_node()
    cleanup, pool_c, _client_c = make_cleanup(sftp, node)
    runner, pool, client = _make_runner(sftp, node, cleanup=cleanup)
    _set_disk_pct(client, 97)

    events: list[dict] = []

    class FakeLog:
        def append(self, event, **kw):
            events.append({"event": event, **kw})

    with patch_client(lambda n, timeout=30: client):
        with pytest.raises(RemoteNodeUnavailableError, match="skipped"):
            runner._pre_submit_housekeeping(node, FakeLog(), "job1")  # type: ignore[arg-type]

    hk = [e for e in events if e["event"] == "remote.housekeeping"]
    assert len(hk) == 1
    assert hk[0]["should_skip"] is True
    pool.close()
    pool_c.close()
    print("  [OK] runner: housekeeping skip → RemoteNodeUnavailableError")


def test_runner_housekeeping_crash_fails_open():
    sftp = FakeSFTP()
    node = make_node()
    cleanup, pool_c, _client_c = make_cleanup(sftp, node)
    runner, pool, client = _make_runner(sftp, node, cleanup=cleanup)

    events: list[dict] = []

    class FakeLog:
        def append(self, event, **kw):
            events.append({"event": event, **kw})

    def boom(n):
        raise RuntimeError("disk probe exploded")

    with patch_client(lambda n, timeout=30: client):
        with patch.object(RemoteCleanup, "pre_submit_housekeeping", boom):
            # Must NOT raise — fail-open.
            runner._pre_submit_housekeeping(node, FakeLog(), "job1")  # type: ignore[arg-type]

    err_events = [e for e in events if e["event"] == "remote.housekeeping_error"]
    assert len(err_events) == 1
    pool.close()
    pool_c.close()
    print("  [OK] runner: housekeeping crash → fail-open + error event")


def test_run_remote_skips_on_full_disk():
    """End-to-end: run() fails the job when housekeeping rejects the node."""
    sftp = FakeSFTP()
    node = make_node()
    cleanup, pool_c, _client_c = make_cleanup(sftp, node)
    runner, pool, client = _make_runner(sftp, node, cleanup=cleanup)
    _set_disk_pct(client, 98)

    record = JobRecord(
        id="20260713_001_full",
        spec=JobSpec(workflow="conformer", input={"source": "CCO"}),
        status=JobStatus.RUNNING,
        work_dir=tempfile.mkdtemp(),
    )

    event_log = JobEventLog(Path(record.work_dir) / "events.jsonl")
    cancel = threading.Event()

    with patch_client(lambda n, timeout=30: client):
        exit_code = runner.run(record, event_log, cancel)

    assert exit_code == 1
    # run() does not set record.status (the manager does), but it must
    # populate record.error with the housekeeping skip reason.
    assert record.error is not None
    assert "skipped" in record.error or "disk" in record.error.lower()
    pool.close()
    pool_c.close()
    print("  [OK] run(): full-disk skip → exit_code=1 + error recorded")


# ====================================================================== #
# Manager wiring
# ====================================================================== #


def test_manager_remote_cleanup_wired_when_remote():
    """JobManager.remote_cleanup is set when remote execution is enabled."""
    from acp.scheduler.manager import JobManager

    node = make_node()
    config = make_config(node)

    def factory(n, timeout=30):
        return FakeSSHClient(FakeSFTP())

    with tempfile.TemporaryDirectory() as tmp:
        with patch.object(ssh_mod, "_create_client", side_effect=factory):
            mgr = JobManager(run_root=tmp, max_running=1, remote_config=config)
            assert mgr.remote_cleanup is not None
            assert mgr.remote_runner is not None
            mgr.shutdown()
    print("  [OK] JobManager.remote_cleanup wired when remote enabled")


def test_manager_remote_cleanup_none_when_local():
    from acp.scheduler.manager import JobManager

    with tempfile.TemporaryDirectory() as tmp:
        mgr = JobManager(run_root=tmp, max_running=1, remote_config=None)
        assert mgr.remote_cleanup is None
        assert mgr.remote_runner is None
        mgr.shutdown()
    print("  [OK] JobManager.remote_cleanup None when remote disabled")


# ====================================================================== #
# _format_bytes
# ====================================================================== #


def test_format_bytes():
    assert _format_bytes(0) == "0 B"
    assert _format_bytes(512) == "512 B"
    assert _format_bytes(2048) == "2.0 KiB"
    assert _format_bytes(1048576) == "1.0 MiB"
    assert "GiB" in _format_bytes(1073741824)
    print("  [OK] _format_bytes human-readable units")


# ====================================================================== #
# Main
# ====================================================================== #


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v", "-s"]))
