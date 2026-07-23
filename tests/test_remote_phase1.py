"""
Phase 1 functional tests — verify SSH/SFTP/sync logic without a real node.

Uses mock paramiko to test:
- SSHConnectionPool borrow/release/retry/thread-safety
- FileStager upload/download/tail offset tracking
- CodeSyncer state recording + incremental sync + exclusion rules
- RemoteNode/RemoteExecutionConfig config parsing + env-var override

Run with: PYTHONPATH=src python3 tests/test_remote_phase1.py
"""

from __future__ import annotations

import io
import os
import stat
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from acp.scheduler.remote import ssh as ssh_mod

# We must set env vars BEFORE importing, but resolved_password() reads at
# call time, so importing first is fine.
from acp.scheduler.remote.config import RemoteExecutionConfig, RemoteNode
from acp.scheduler.remote.sftp import FileStager
from acp.scheduler.remote.ssh import SSHConnectionPool, SSHExecutionError
from acp.scheduler.remote.sync import CodeSyncer, SyncResult, _build_sync_file_list, _project_root

# ====================================================================== #
# Helpers: build a mock paramiko SFTPClient / SSHClient
# ====================================================================== #


class FakeSFTPFile(io.BytesIO):
    """A fake SFTP file object supporting seek/read/write/close.

    Mimics paramiko.SFTPFile: in text mode ("w"/"r") accepts/returns str,
    in binary mode accepts/returns bytes.
    """

    def __init__(self, data: bytes = b"", mode: str = "rb"):
        super().__init__(data if "b" in mode else b"")
        self.mode = mode
        self._closed = False

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
        self._closed = True


class FakeSFTP:
    """In-memory mock of paramiko.SFTPClient."""

    def __init__(self):
        # remote_path -> bytes content
        self.files: dict[str, bytes] = {}
        # remote_path -> stat result (size, mtime, mode)
        self.attrs: dict[str, MagicMock] = {}
        self.dirs: set[str] = set()
        self.put_calls: list[tuple[str, str]] = []

    def put(self, localpath, remotepath):
        self.put_calls.append((localpath, remotepath))
        with open(localpath, "rb") as f:
            self.files[remotepath] = f.read()
        self._set_attr(remotepath, size=len(self.files[remotepath]))

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

            # Capture writes on close
            def _on_close():
                self.files[original_path] = f.getvalue()
                self._set_attr(original_path, size=len(f.getvalue()))

            f.close = _on_close  # type: ignore[assignment]
            return f
        return FakeSFTPFile(data, mode)

    def stat(self, path):
        if path in self.dirs:
            a = MagicMock()
            a.st_size = 0
            a.st_mtime = 0.0
            a.st_mode = stat.S_IFDIR
            return a
        if path not in self.attrs and path not in self.files:
            raise FileNotFoundError(path)
        return self.attrs.get(path) or self._set_attr(path)

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

    def remove(self, path):
        self.files.pop(path, None)
        self.attrs.pop(path, None)

    def close(self):
        pass


class FakeSSHClient:
    """Mock SSHClient that returns canned exec_command results."""

    def __init__(self, node_name: str, fake_sftp: FakeSFTP):
        self.node_name = node_name
        self.fake_sftp = fake_sftp
        self.closed = False
        self.exec_calls: list[str] = []
        self._transport = MagicMock()
        self._transport.is_active.return_value = True
        # Commands -> (exit_code, stdout, stderr)
        self.cmd_results: dict[str, tuple[int, str, str]] = {}

    def set_missing_host_key_policy(self, policy):
        pass

    def connect(self, **kwargs):
        pass

    def get_transport(self):
        return self._transport

    def exec_command(self, command, timeout=None):
        self.exec_calls.append(command)
        result = self.cmd_results.get(command, (0, "", ""))
        stdin = MagicMock()
        stdout = MagicMock()
        stderr = MagicMock()
        stdout.read.return_value = result[1].encode("utf-8")
        stderr.read.return_value = result[2].encode("utf-8")
        stdout.channel = MagicMock()
        stdout.channel.recv_exit_status.return_value = result[0]
        return stdin, stdout, stderr

    def open_sftp(self):
        return self.fake_sftp

    def close(self):
        self.closed = True


def make_mock_pool(ssh_pool: SSHConnectionPool, node: RemoteNode, fake_sftp: FakeSFTP):
    """Patch _create_client to return FakeSSHClient instances."""
    clients: list[FakeSSHClient] = []

    def factory(n, timeout=30):
        c = FakeSSHClient(n.name, fake_sftp)
        clients.append(c)
        return c

    return factory, clients


# ====================================================================== #
# Tests
# ====================================================================== #


def test_remote_node_env_var_password():
    """resolved_password() honours ACP_REMOTE_PASSWORD_<NAME> env var."""
    node = RemoteNode(
        name="compute-01", host="h", username="u", remote_work_dir="/w", remote_code_dir="/c"
    )
    env_key = "ACP_REMOTE_PASSWORD_COMPUTE_01"
    old = os.environ.pop(env_key, None)
    try:
        assert node.resolved_password() is None
        os.environ[env_key] = "s3cret"
        assert node.resolved_password() == "s3cret"
        del os.environ[env_key]
        # Falls back to .password field
        node2 = RemoteNode(
            name="compute-01",
            host="h",
            username="u",
            remote_work_dir="/w",
            remote_code_dir="/c",
            password="fallback",
        )
        assert node2.resolved_password() == "fallback"
        os.environ[env_key] = "override"
        assert node2.resolved_password() == "override"
    finally:
        if env_key in os.environ:
            del os.environ[env_key]
        if old is not None:
            os.environ[env_key] = old
    print("  [OK] RemoteNode env-var password override")


def test_remote_node_from_config_dict():
    """from_config_dict parses required + optional fields."""
    node = RemoteNode.from_config_dict(
        {
            "name": "n1",
            "host": "10.0.0.1",
            "username": "u",
            "remote_work_dir": "/w",
            "remote_code_dir": "/c",
            "port": 2222,
            "max_concurrent_jobs": 10,
            "enabled": False,
            "key_file": "~/.ssh/key",
        }
    )
    assert node.name == "n1"
    assert node.port == 2222
    assert node.max_concurrent_jobs == 10
    assert node.enabled is False
    assert node.key_file == "~/.ssh/key"

    # Missing required key raises
    try:
        RemoteNode.from_config_dict({"name": "x"})
        assert False, "should have raised"
    except ValueError as e:
        assert "host" in str(e)
    print("  [OK] RemoteNode.from_config_dict parsing + validation")


def test_remote_execution_config():
    cfg = RemoteExecutionConfig.from_config_dict(
        {
            "execution_mode": "remote",
            "poll_interval": 15,
            "retention_days": 90,
            "auto_sync": False,
            "nodes": [
                {
                    "name": "n1",
                    "host": "h1",
                    "username": "u1",
                    "remote_work_dir": "/w",
                    "remote_code_dir": "/c",
                },
                {
                    "name": "n2",
                    "host": "h2",
                    "username": "u2",
                    "remote_work_dir": "/w",
                    "remote_code_dir": "/c",
                    "enabled": False,
                },
            ],
        }
    )
    assert cfg.is_remote is True
    assert cfg.poll_interval == 15
    assert cfg.retention_days == 90
    assert cfg.auto_sync is False
    assert len(cfg.nodes) == 2
    assert len(cfg.enabled_nodes) == 1
    assert cfg.get_node("n2").enabled is False
    assert cfg.get_node("nope") is None

    # Empty config -> local
    local = RemoteExecutionConfig.from_config_dict({})
    assert local.is_remote is False
    assert local.execution_mode == "local"
    print("  [OK] RemoteExecutionConfig parsing + is_remote + enabled_nodes")


def test_ssh_pool_borrow_release():
    """Borrow then release returns the same alive client."""
    node = RemoteNode(
        name="t1",
        host="h",
        username="u",
        remote_work_dir="/w",
        remote_code_dir="/c",
        max_concurrent_jobs=2,
    )
    pool = SSHConnectionPool()
    fake_sftp = FakeSFTP()
    factory, clients = make_mock_pool(pool, node, fake_sftp)

    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        c1 = pool.borrow(node)
        assert len(clients) == 1
        pool.release(node, c1)
        # Reusing should NOT create a new client (same one returned)
        c2 = pool.borrow(node)
        assert c2 is c1
        assert len(clients) == 1
        pool.release(node, c2)
    pool.close()
    print("  [OK] SSHConnectionPool borrow/release reuses connections")


def test_ssh_pool_max_concurrency():
    """Pool capacity = max_concurrent_jobs; extra borrows block."""
    node = RemoteNode(
        name="t2",
        host="h",
        username="u",
        remote_work_dir="/w",
        remote_code_dir="/c",
        max_concurrent_jobs=2,
    )
    pool = SSHConnectionPool()
    fake_sftp = FakeSFTP()
    factory, _ = make_mock_pool(pool, node, fake_sftp)

    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        c1 = pool.borrow(node)
        c2 = pool.borrow(node)

        # Third borrow should block — launch in a thread that releases after a delay
        def delayed_release():
            time.sleep(0.1)
            pool.release(node, c1)

        threading.Thread(target=delayed_release).start()
        # This should block ~0.1s then succeed
        start = time.time()
        c3 = pool.borrow(node, timeout=2.0)
        elapsed = time.time() - start
        assert c3 is c1  # got the released one
        assert elapsed >= 0.05  # actually blocked
        pool.release(node, c2)
        pool.release(node, c3)
    pool.close()
    print("  [OK] SSHConnectionPool enforces max_concurrent_jobs limit (blocking)")


def test_ssh_pool_thread_safety():
    """Many threads borrowing/releasing concurrently don't crash or leak."""
    node = RemoteNode(
        name="t3",
        host="h",
        username="u",
        remote_work_dir="/w",
        remote_code_dir="/c",
        max_concurrent_jobs=5,
    )
    pool = SSHConnectionPool()
    fake_sftp = FakeSFTP()
    factory, _ = make_mock_pool(pool, node, fake_sftp)

    errors: list[Exception] = []

    def worker():
        try:
            with patch.object(ssh_mod, "_create_client", side_effect=factory):
                c = pool.borrow(node, timeout=5)
                time.sleep(0.01)
                pool.release(node, c)
        except Exception as e:
            errors.append(e)

    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
    assert not errors, f"Thread errors: {errors}"
    pool.close()
    print("  [OK] SSHConnectionPool thread-safe under 20 concurrent borrowers")


def test_ssh_execute_retry():
    """execute() retries on failure then raises SSHExecutionError."""
    node = RemoteNode(name="t4", host="h", username="u", remote_work_dir="/w", remote_code_dir="/c")
    pool = SSHConnectionPool()
    call_count = [0]

    def failing_factory(n, timeout=30):
        call_count[0] += 1
        c = MagicMock()
        c.exec_command.side_effect = OSError("connection lost")
        c.get_transport.return_value.is_active.return_value = True
        return c

    with patch.object(ssh_mod, "_create_client", side_effect=failing_factory):
        try:
            pool.execute(node, "echo hi")
            assert False, "should have raised SSHExecutionError"
        except SSHExecutionError as e:
            assert "t4" in str(e)
        # _MAX_RETRIES + 1 attempts
        assert call_count[0] == 3
    pool.close()
    print(f"  [OK] SSHConnectionPool.execute retries {call_count[0]}x then raises")


def test_ssh_execute_success():
    """execute() returns (exit_code, stdout, stderr) on success."""
    node = RemoteNode(name="t5", host="h", username="u", remote_work_dir="/w", remote_code_dir="/c")
    pool = SSHConnectionPool()

    def factory(n, timeout=30):
        c = MagicMock()
        stdin = MagicMock()
        stdout = MagicMock()
        stderr = MagicMock()
        stdout.read.return_value = b"hello\n"
        stderr.read.return_value = b""
        stdout.channel.recv_exit_status.return_value = 0
        c.exec_command.return_value = (stdin, stdout, stderr)
        c.get_transport.return_value.is_active.return_value = True
        return c

    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        code, out, err = pool.execute(node, "echo hello")
        assert code == 0
        assert out == "hello\n"
        assert err == ""
    pool.close()
    print("  [OK] SSHConnectionPool.execute returns (exit, stdout, stderr)")


def test_sftp_upload_download_text():
    """FileStager.upload_text + read_remote_text round-trip."""
    node = RemoteNode(name="s1", host="h", username="u", remote_work_dir="/w", remote_code_dir="/c")
    pool = SSHConnectionPool()
    fake_sftp = FakeSFTP()
    stager = FileStager(pool)

    def factory(n, timeout=30):
        c = MagicMock()
        c.open_sftp.return_value = fake_sftp
        c.get_transport.return_value.is_active.return_value = True
        return c

    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        stager.upload_text(node, "hello world\n", "/remote/path/log.txt")
        content = stager.read_remote_text(node, "/remote/path/log.txt")
        assert content == "hello world\n"
    pool.close()
    print("  [OK] FileStager upload_text + read_remote_text round-trip")


def test_sftp_tail_log_incremental():
    """tail_log respects offset and returns new content + new offset."""
    node = RemoteNode(name="s2", host="h", username="u", remote_work_dir="/w", remote_code_dir="/c")
    pool = SSHConnectionPool()
    fake_sftp = FakeSFTP()
    stager = FileStager(pool)

    def factory(n, timeout=30):
        c = MagicMock()
        c.open_sftp.return_value = fake_sftp
        c.get_transport.return_value = True
        return c

    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        # Write initial content
        stager.upload_text(node, "line1\nline2\n", "/log.txt")
        # Tail from 0
        data, off1 = stager.tail_log(node, "/log.txt", offset=0)
        assert data == b"line1\nline2\n"
        assert off1 == 12
        # No new content
        data, off2 = stager.tail_log(node, "/log.txt", offset=off1)
        assert data == b""
        assert off2 == off1
        # Append more (simulating growth)
        stager.upload_text(node, "line1\nline2\nline3\nline4\n", "/log.txt")
        data, off3 = stager.tail_log(node, "/log.txt", offset=off2)
        assert data == b"line3\nline4\n"
        assert off3 == 24
    pool.close()
    print("  [OK] FileStager.tail_log incremental offset tracking")


def test_sftp_upload_directory():
    """upload_directory recursively uploads a local tree."""
    node = RemoteNode(name="s3", host="h", username="u", remote_work_dir="/w", remote_code_dir="/c")
    pool = SSHConnectionPool()
    fake_sftp = FakeSFTP()
    stager = FileStager(pool)

    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / "inputs"
        (local / "sub").mkdir(parents=True)
        (local / "a.txt").write_text("A")
        (local / "sub" / "b.txt").write_text("B")

        def factory(n, timeout=30):
            c = MagicMock()
            c.open_sftp.return_value = fake_sftp
            c.get_transport.return_value.is_active.return_value = True
            return c

        with patch.object(ssh_mod, "_create_client", side_effect=factory):
            stager.upload_directory(node, local, "/remote/in")
            assert "/remote/in/a.txt" in fake_sftp.files
            assert "/remote/in/sub/b.txt" in fake_sftp.files
            assert fake_sftp.files["/remote/in/a.txt"] == b"A"
            assert fake_sftp.files["/remote/in/sub/b.txt"] == b"B"
    pool.close()
    print("  [OK] FileStager.upload_directory recursive")


def test_sync_file_list_excludes_api_scheduler():
    """_build_sync_file_list excludes src/acp/api and src/acp/scheduler."""
    root = _project_root()
    files = _build_sync_file_list(root)
    files_str = [str(f) for f in files]
    acp_files = [f for f in files_str if "/src/acp/" in f.replace("\\", "/")]
    assert not any("/api/" in f for f in acp_files), "api/ should be excluded"
    assert not any("/scheduler/" in f for f in acp_files), "scheduler/ should be excluded"
    assert any("conformer_search" in f for f in files_str), "conformer_search should be included"
    assert not any("run_g16_worker.sh" in f for f in files_str), (
        "run_g16_worker.sh should be removed"
    )
    assert not any("__pycache__" in f for f in files_str)
    print(f"  [OK] _build_sync_file_list: {len(files)} files, api/scheduler excluded")


def test_codesyncer_incremental_sync():
    """CodeSyncer uploads changed files only, records mtime state."""
    node = RemoteNode(
        name="sync1", host="h", username="u", remote_work_dir="/w", remote_code_dir="/code"
    )
    pool = SSHConnectionPool()
    fake_sftp = FakeSFTP()

    def factory(n, timeout=30):
        c = MagicMock()
        c.open_sftp.return_value = fake_sftp
        c.get_transport.return_value.is_active.return_value = True
        return c

    with tempfile.TemporaryDirectory() as state_tmp:
        syncer = CodeSyncer(pool, state_dir=Path(state_tmp))
        with patch.object(ssh_mod, "_create_client", side_effect=factory):
            with patch("acp.scheduler.remote.sync._project_root", return_value=_project_root()):
                # Force-sync: all files
                r1 = syncer.sync_code(node, force=True)
                assert r1.ok
                assert r1.uploaded == r1.total
                assert r1.skipped == 0
                first_uploaded = r1.uploaded
                first_state = syncer._load_state(node)
                assert len(first_state) == r1.total

                # Second sync: nothing changed
                r2 = syncer.sync_code(node)
                assert r2.uploaded == 0
                assert r2.skipped == r2.total

                # Touch one file by rewriting it with same content (mtime changes)
                # Simulate: modify the state so one file looks stale.
                one_rel = next(iter(first_state))
                first_state[one_rel] = 0.0  # forces re-upload
                syncer._save_state(node, first_state)
                r3 = syncer.sync_code(node)
                assert r3.uploaded == 1
                assert r3.skipped == r3.total - 1
    pool.close()
    print(f"  [OK] CodeSyncer incremental: first={first_uploaded}, 2nd=0, touched=1")


def test_codesyncer_check_sync_needed():
    node = RemoteNode(
        name="sync2", host="h", username="u", remote_work_dir="/w", remote_code_dir="/code"
    )
    pool = SSHConnectionPool()
    fake_sftp = FakeSFTP()

    def factory(n, timeout=30):
        c = MagicMock()
        c.open_sftp.return_value = fake_sftp
        c.get_transport.return_value.is_active.return_value = True
        return c

    with tempfile.TemporaryDirectory() as state_tmp:
        syncer = CodeSyncer(pool, state_dir=Path(state_tmp))
        with patch.object(ssh_mod, "_create_client", side_effect=factory):
            with patch("acp.scheduler.remote.sync._project_root", return_value=_project_root()):
                # Fresh state -> sync needed
                assert syncer.check_sync_needed(node) is True
                syncer.sync_code(node, force=True)
                # State recorded -> not needed
                assert syncer.check_sync_needed(node) is False
                # Corrupt a state entry
                state = syncer._load_state(node)
                one = next(iter(state))
                state[one] = 0.0
                syncer._save_state(node, state)
                assert syncer.check_sync_needed(node) is True
    pool.close()
    print("  [OK] CodeSyncer.check_sync_needed detects changes")


def test_syncresult_ok_property():
    r = SyncResult()
    assert r.ok is True
    r.errors.append("x")
    assert r.ok is False
    print("  [OK] SyncResult.ok property")


# ====================================================================== #
# P0 fix tests
# ====================================================================== #


def test_ssh_pool_borrow_no_infinite_recursion():
    """P0-1: all queued connections dead → no stack overflow.

    Scenario: pool full with dead connections, _create_client fails.
    borrow() must raise (not RecursionError) — the old code recursed
    on dead connections without bounds.
    """
    node = RemoteNode(
        name="p01",
        host="h",
        username="u",
        remote_work_dir="/w",
        remote_code_dir="/c",
        max_concurrent_jobs=2,
    )
    pool = SSHConnectionPool()

    # Pre-fill the pool with dead clients (queue maxsize = capacity).
    node_pool = ssh_mod._NodePool(node)
    for _ in range(node_pool._capacity):  # exactly capacity, not more
        d = MagicMock()
        d.get_transport.return_value = None
        node_pool._queue.put(d)
    node_pool._created = node_pool._capacity
    pool._pools[node.name] = node_pool

    # Mock _create_client to fail — borrow must raise, not recurse.
    with patch.object(ssh_mod, "_create_client", side_effect=OSError("no connection")):
        try:
            pool.borrow(node, timeout=0.5)
            assert False, "should have raised"
        except (SSHExecutionError, OSError):
            # Either is acceptable — the key assertion is no RecursionError.
            pass
        except RecursionError:
            assert False, "must not recurse — use bounded loop"
    pool.close()
    print("  [OK] P0-1: dead pool + create fails → raises (no infinite recursion)")


def test_tail_log_offset_is_offset_plus_len():
    """P0-2: tail_log returns offset+len(data), NOT file_size.

    Simulates file growth between stat() and read() to verify no
    over-reporting of the offset.
    """
    node = RemoteNode(
        name="p02", host="h", username="u", remote_work_dir="/w", remote_code_dir="/c"
    )
    pool = SSHConnectionPool()
    fake_sftp = FakeSFTP()
    stager = FileStager(pool)

    # Write 12 bytes: "line1\nline2\n"
    def factory(n, timeout=30):
        c = MagicMock()
        c.open_sftp.return_value = fake_sftp
        c.get_transport.return_value.is_active.return_value = True
        return c

    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        stager.upload_text(node, "line1\nline2\n", "/log.txt")

        # Now craft a scenario where stat reports size=12 but read returns
        # only the first 7 bytes (simulating file growing during read, or
        # a partial read). We patch the FakeSFTPFile to return truncated data.
        original_file = FakeSFTP.file.__get__(fake_sftp, FakeSFTP)

        def patched_file(self, remote_path, mode="r"):
            f = original_file(remote_path, mode)
            if "rb" in mode and remote_path == "/log.txt":
                f.read = lambda size=-1: b"line1\nl"  # only 7 bytes
            return f

        with patch.object(FakeSFTP, "file", patched_file):
            data, new_off = stager.tail_log(node, "/log.txt", offset=0)
        # Must return offset + len(data) = 0 + 7 = 7, NOT file_size=12
        assert data == b"line1\nl", repr(data)
        assert new_off == 7, f"expected 7, got {new_off} (file_size=12 would be wrong)"
    pool.close()
    print(f"  [OK] P0-2: tail_log offset = offset+len(data) = {new_off} (not file_size=12)")


def test_tail_log_normal_case_unchanged():
    """P0-2 regression: when file doesn't grow, offset still correct."""
    node = RemoteNode(
        name="p02b", host="h", username="u", remote_work_dir="/w", remote_code_dir="/c"
    )
    pool = SSHConnectionPool()
    fake_sftp = FakeSFTP()
    stager = FileStager(pool)

    def factory(n, timeout=30):
        c = MagicMock()
        c.open_sftp.return_value = fake_sftp
        c.get_transport.return_value.is_active.return_value = True
        return c

    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        stager.upload_text(node, "hello\n", "/log.txt")  # 6 bytes
        data, off = stager.tail_log(node, "/log.txt", offset=0)
        assert data == b"hello\n"
        assert off == 6  # 0 + 6
        # Second call: no new content
        data2, off2 = stager.tail_log(node, "/log.txt", offset=off)
        assert data2 == b"" and off2 == off
    pool.close()
    print("  [OK] P0-2 regression: normal tail_log unchanged (offset=6)")


def test_ssh_host_key_policy_reject():
    """P0-3: default host_key_policy='reject' uses RejectPolicy."""
    import paramiko

    from acp.scheduler.remote.ssh import _host_key_policy

    policy = _host_key_policy("reject")
    assert isinstance(policy, paramiko.RejectPolicy)
    print("  [OK] P0-3: host_key_policy='reject' → RejectPolicy")


def test_ssh_host_key_policy_auto_add():
    """P0-3: host_key_policy='auto_add' uses AutoAddPolicy."""
    import paramiko

    from acp.scheduler.remote.ssh import _host_key_policy

    policy = _host_key_policy("auto_add")
    assert isinstance(policy, paramiko.AutoAddPolicy)
    print("  [OK] P0-3: host_key_policy='auto_add' → AutoAddPolicy")


def test_ssh_host_key_policy_warn():
    """P0-3: host_key_policy='warn' uses custom WarnThenAddPolicy."""
    from acp.scheduler.remote.ssh import _host_key_policy, _WarnThenAddPolicy

    policy = _host_key_policy("warn")
    assert isinstance(policy, _WarnThenAddPolicy)
    print("  [OK] P0-3: host_key_policy='warn' → _WarnThenAddPolicy")


def test_remote_node_host_key_policy_default():
    """P0-3: RemoteNode defaults to host_key_policy='reject'."""
    node = RemoteNode(name="n", host="h", username="u", remote_work_dir="/w", remote_code_dir="/c")
    assert node.host_key_policy == "reject"
    # from_config_dict
    node2 = RemoteNode.from_config_dict(
        {
            "name": "n",
            "host": "h",
            "username": "u",
            "remote_work_dir": "/w",
            "remote_code_dir": "/c",
        }
    )
    assert node2.host_key_policy == "reject"
    # explicit override
    node3 = RemoteNode.from_config_dict(
        {
            "name": "n",
            "host": "h",
            "username": "u",
            "remote_work_dir": "/w",
            "remote_code_dir": "/c",
            "host_key_policy": "auto_add",
        }
    )
    assert node3.host_key_policy == "auto_add"
    print("  [OK] P0-3: RemoteNode.host_key_policy default='reject', configurable via YAML")


def test_ssh_create_client_uses_node_policy():
    """P0-3: _create_client sets the policy from the node config."""
    node = RemoteNode(
        name="n",
        host="h",
        username="u",
        remote_work_dir="/w",
        remote_code_dir="/c",
        host_key_policy="auto_add",
    )
    with patch.object(ssh_mod.paramiko, "SSHClient") as mock_client_cls:
        mock_instance = mock_client_cls.return_value
        ssh_mod._create_client(node, timeout=5)
        # set_missing_host_key_policy called with AutoAddPolicy instance
        called_policy = mock_instance.set_missing_host_key_policy.call_args[0][0]
        import paramiko

        assert isinstance(called_policy, paramiko.AutoAddPolicy)
    print("  [OK] P0-3: _create_client respects node.host_key_policy")


# ====================================================================== #


def main():
    tests = [
        test_remote_node_env_var_password,
        test_remote_node_from_config_dict,
        test_remote_execution_config,
        test_ssh_pool_borrow_release,
        test_ssh_pool_max_concurrency,
        test_ssh_pool_thread_safety,
        test_ssh_execute_retry,
        test_ssh_execute_success,
        test_sftp_upload_download_text,
        test_sftp_tail_log_incremental,
        test_sftp_upload_directory,
        test_sync_file_list_excludes_api_scheduler,
        test_codesyncer_incremental_sync,
        test_codesyncer_check_sync_needed,
        test_syncresult_ok_property,
        # P0 fix tests
        test_ssh_pool_borrow_no_infinite_recursion,
        test_tail_log_offset_is_offset_plus_len,
        test_tail_log_normal_case_unchanged,
        test_ssh_host_key_policy_reject,
        test_ssh_host_key_policy_auto_add,
        test_ssh_host_key_policy_warn,
        test_remote_node_host_key_policy_default,
        test_ssh_create_client_uses_node_policy,
    ]
    failed = 0
    for t in tests:
        print(f"RUN  {t.__name__}")
        try:
            t()
        except Exception as e:
            failed += 1
            import traceback

            traceback.print_exc()
            print(f"  FAIL {t.__name__}: {e}")
    print(f"\n{'=' * 60}")
    print(f"Results: {len(tests) - failed} passed, {failed} failed (of {len(tests)})")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
