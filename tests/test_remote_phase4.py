"""
Phase 4 tests — RemoteResultFetcher (on-demand remote file/log retrieval).

Verifies the fetcher logic without a real compute node, using mock
SSH/SFTP fakes (same pattern as Phase 1/2 tests):
- is_remote_job / resolve: metadata extraction + error cases
- list_files / file_exists / read_file: SFTP delegation
- stream_file: chunked streaming concatenates to full content
- log_tail: tail lines, missing file, head-truncation, lines limit
- _safe_join: path-traversal blocked, nested subdirs allowed
- JobManager.remote_fetcher wiring

Run with: PYTHONPATH=src python3 tests/test_remote_phase4.py
"""

from __future__ import annotations

import io
import stat
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.remote import ssh as ssh_mod
from acp.scheduler.remote.config import RemoteExecutionConfig, RemoteNode
from acp.scheduler.remote.fetcher import (
    NotARemoteJobError,
    RemoteFileError,
    RemotePreviewConfig,
    RemoteResultFetcher,
    _safe_join,
)
from acp.scheduler.remote.sftp import FileStager
from acp.scheduler.remote.ssh import SSHConnectionPool

# ====================================================================== #
# Mock infrastructure (mirrors Phase 1/2 fakes)
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
    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.attrs: dict[str, MagicMock] = {}
        self.dirs: set[str] = set()

    def put(self, localpath, remotepath):
        with open(localpath, "rb") as f:
            self.files[remotepath] = f.read()
        self._set_attr(remotepath, size=len(self.files[remotepath]))

    def get(self, remotepath, localpath):
        if remotepath not in self.files:
            raise FileNotFoundError(remotepath)
        with open(localpath, "wb") as f:
            f.write(self.files[remotepath])

    def file(self, remote_path, mode="r"):
        if "w" in mode or "a" in mode:
            f = FakeSFTPFile(b"", mode)
            original_path = remote_path

            def _on_close():
                self.files[original_path] = f.getvalue()
                self._set_attr(original_path, size=len(f.getvalue()))

            f.close = _on_close  # type: ignore[assignment]
            return f
        # Read mode: raise like real paramiko when the file is absent.
        if remote_path not in self.files:
            raise FileNotFoundError(remote_path)
        return FakeSFTPFile(self.files[remote_path], mode)

    def stat(self, path):
        if path in self.dirs:
            a = MagicMock()
            a.st_size = 0
            a.st_mtime = 0.0
            a.st_mode = stat.S_IFDIR
            return a
        if path in self.files:
            existing_mtime = 0.0
            if path in self.attrs:
                existing_mtime = self.attrs[path].st_mtime or 0.0
            return self._set_attr(path, size=len(self.files[path]), mtime=existing_mtime)
        if path in self.attrs:
            return self.attrs[path]
        raise FileNotFoundError(path)

    def _set_attr(self, path, size=0, mtime=0.0, is_dir=False):
        a = MagicMock()
        a.st_size = size
        a.st_mtime = mtime
        a.st_mode = stat.S_IFDIR if is_dir else stat.S_IFREG
        self.attrs[path] = a
        return a

    def listdir_attr(self, path):
        """Return SFTPAttributes-like objects in a single round-trip (paramiko style)."""
        prefix = path.rstrip("/") + "/" if path != "/" else "/"
        is_known_dir = path in self.dirs
        has_children = any(f.startswith(prefix) for f in self.files)
        if not is_known_dir and not has_children:
            raise FileNotFoundError(path)
        result = []
        for fpath in set(self.files.keys()) | self.dirs:
            if fpath.startswith(prefix):
                rest = fpath[len(prefix) :]
                if "/" not in rest and rest:
                    if fpath in self.dirs:
                        a = MagicMock()
                        a.filename = rest
                        a.st_size = 0
                        a.st_mtime = 0.0
                        a.st_mode = stat.S_IFDIR
                    else:
                        size = len(self.files.get(fpath, b""))
                        a = MagicMock()
                        a.filename = rest
                        a.st_size = size
                        a.st_mtime = self.attrs.get(fpath, MagicMock()).st_mtime or 0.0
                        a.st_mode = stat.S_IFREG
                    result.append(a)
        return result

    def listdir(self, path):
        prefix = path.rstrip("/") + "/" if path != "/" else "/"
        is_known_dir = path in self.dirs
        has_children = any(f.startswith(prefix) for f in self.files)
        if not is_known_dir and not has_children:
            raise FileNotFoundError(path)
        names = []
        for fpath in set(self.files.keys()) | self.dirs:
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
    def __init__(self, fake_sftp: FakeSFTP):
        self.fake_sftp = fake_sftp
        self.closed = False
        self._transport = MagicMock()
        self._transport.is_active.return_value = True

    def set_missing_host_key_policy(self, policy):
        pass

    def connect(self, **kwargs):
        pass

    def get_transport(self):
        return self._transport

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


def make_config(node: RemoteNode) -> RemoteExecutionConfig:
    return RemoteExecutionConfig(execution_mode="remote", nodes=[node])


def make_fetcher(sftp: FakeSFTP, node: RemoteNode):
    """Build a fetcher backed by a FakeSFTP via patched _create_client."""
    pool = SSHConnectionPool()
    client = FakeSSHClient(sftp)

    def factory(n, timeout=30):
        return client

    stager = FileStager(pool)
    config = make_config(node)
    fetcher = RemoteResultFetcher(ssh_pool=pool, stager=stager, remote_config=config)
    return fetcher, pool, factory


def make_remote_record(
    job_id: str = "20260713_001_test",
    node: str = "compute-01",
    remote_dir: str = "/scratch/test/acp_jobs/20260713_001_test",
) -> JobRecord:
    """Build a JobRecord with remote execution metadata in result."""
    return JobRecord(
        id=job_id,
        spec=JobSpec(workflow="conformer", input={"source": "CCO"}),
        status=JobStatus.COMPLETED,
        work_dir="/tmp/fake",
        result={
            "node": node,
            "host": "10.0.0.1",
            "remote_dir": remote_dir,
            "lsf_job_id": "12345",
            "exit_code": 0,
        },
    )


# ====================================================================== #
# is_remote_job / resolve
# ====================================================================== #


def test_is_remote_job_true():
    fetcher, pool, factory = make_fetcher(FakeSFTP(), make_node())
    record = make_remote_record()
    assert fetcher.is_remote_job(record) is True
    pool.close()
    print("  [OK] is_remote_job: True with node+remote_dir")


def test_is_remote_job_false_no_result():
    fetcher, pool, factory = make_fetcher(FakeSFTP(), make_node())
    record = JobRecord(
        id="x",
        spec=JobSpec(workflow="conformer", input={}),
        status=JobStatus.COMPLETED,
        work_dir="/tmp",
        result=None,
    )
    assert fetcher.is_remote_job(record) is False
    pool.close()
    print("  [OK] is_remote_job: False with no result")


def test_is_remote_job_false_partial():
    fetcher, pool, factory = make_fetcher(FakeSFTP(), make_node())
    record = JobRecord(
        id="x",
        spec=JobSpec(workflow="conformer", input={}),
        status=JobStatus.COMPLETED,
        work_dir="/tmp",
        result={"node": "compute-01"},  # missing remote_dir
    )
    assert fetcher.is_remote_job(record) is False
    pool.close()
    print("  [OK] is_remote_job: False with partial metadata")


def test_resolve_success():
    node = make_node()
    fetcher, pool, factory = make_fetcher(FakeSFTP(), node)
    record = make_remote_record()
    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        resolved_node, remote_dir = fetcher.resolve(record)
    assert resolved_node is node
    assert remote_dir == "/scratch/test/acp_jobs/20260713_001_test"
    pool.close()
    print("  [OK] resolve: returns configured node + remote_dir")


def test_resolve_no_metadata_raises():
    fetcher, pool, factory = make_fetcher(FakeSFTP(), make_node())
    record = JobRecord(
        id="local_job",
        spec=JobSpec(workflow="conformer", input={}),
        status=JobStatus.COMPLETED,
        work_dir="/tmp",
        result=None,
    )
    with pytest.raises(NotARemoteJobError):
        fetcher.resolve(record)
    pool.close()
    print("  [OK] resolve: NotARemoteJobError when no metadata")


def test_resolve_unknown_node_raises():
    fetcher, pool, factory = make_fetcher(FakeSFTP(), make_node())
    record = make_remote_record(node="gone-node")
    with pytest.raises(RemoteFileError, match="gone-node"):
        fetcher.resolve(record)
    pool.close()
    print("  [OK] resolve: RemoteFileError for unknown node")


# ====================================================================== #
# list_files
# ====================================================================== #


def test_list_files():
    node = make_node()
    sftp = FakeSFTP()
    remote_dir = "/scratch/test/acp_jobs/job1"
    sftp.files[remote_dir + "/stdout.log"] = b"hello\n"
    sftp.files[remote_dir + "/finalDFT/energy.out"] = b"-154.94\n"
    sftp.dirs.add(remote_dir + "/finalDFT")
    sftp._set_attr(remote_dir + "/stdout.log", size=6, mtime=1000.0)
    fetcher, pool, factory = make_fetcher(sftp, node)
    record = make_remote_record(remote_dir=remote_dir)

    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        files = fetcher.list_files(record)

    names = {f.name for f in files}
    assert "stdout.log" in names
    assert "finalDFT" in names
    log_entry = next(f for f in files if f.name == "stdout.log")
    assert log_entry.size == 6
    assert log_entry.is_dir is False
    dir_entry = next(f for f in files if f.name == "finalDFT")
    assert dir_entry.is_dir is True
    pool.close()
    print("  [OK] list_files: lists files and dirs with metadata")


def test_list_files_missing_dir():
    node = make_node()
    sftp = FakeSFTP()
    fetcher, pool, factory = make_fetcher(sftp, node)
    record = make_remote_record(remote_dir="/nonexistent/path")

    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        with pytest.raises(FileNotFoundError):
            fetcher.list_files(record)
    pool.close()
    print("  [OK] list_files: FileNotFoundError for missing dir")


# ====================================================================== #
# file_exists / read_file
# ====================================================================== #


def test_file_exists_true():
    node = make_node()
    sftp = FakeSFTP()
    remote_dir = "/scratch/test/acp_jobs/job1"
    sftp.files[remote_dir + "/result.xyz"] = b"3\n\nC 0 0 0\n"
    fetcher, pool, factory = make_fetcher(sftp, node)
    record = make_remote_record(remote_dir=remote_dir)

    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        assert fetcher.file_exists(record, "result.xyz") is True
    pool.close()
    print("  [OK] file_exists: True for existing file")


def test_file_exists_false():
    node = make_node()
    sftp = FakeSFTP()
    fetcher, pool, factory = make_fetcher(sftp, node)
    record = make_remote_record()

    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        assert fetcher.file_exists(record, "nope.xyz") is False
    pool.close()
    print("  [OK] file_exists: False for missing file")


def test_read_file():
    node = make_node()
    sftp = FakeSFTP()
    remote_dir = "/scratch/test/acp_jobs/job1"
    content = b"\x00\x01\x02binary data"
    sftp.files[remote_dir + "/out.bin"] = content
    fetcher, pool, factory = make_fetcher(sftp, node)
    record = make_remote_record(remote_dir=remote_dir)

    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        data = fetcher.read_file(record, "out.bin")
    assert data == content
    pool.close()
    print("  [OK] read_file: returns full bytes")


def test_read_file_nested_subdir():
    node = make_node()
    sftp = FakeSFTP()
    remote_dir = "/scratch/test/acp_jobs/job1"
    sftp.files[remote_dir + "/finalDFT/sp.out"] = b"ORCA SP RESULT\n"
    fetcher, pool, factory = make_fetcher(sftp, node)
    record = make_remote_record(remote_dir=remote_dir)

    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        data = fetcher.read_file(record, "finalDFT/sp.out")
    assert data == b"ORCA SP RESULT\n"
    pool.close()
    print("  [OK] read_file: nested subdir path works")


def test_read_file_missing():
    node = make_node()
    sftp = FakeSFTP()
    fetcher, pool, factory = make_fetcher(sftp, node)
    record = make_remote_record()

    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        with pytest.raises(FileNotFoundError):
            fetcher.read_file(record, "ghost.txt")
    pool.close()
    print("  [OK] read_file: FileNotFoundError for missing file")


# ====================================================================== #
# stream_file
# ====================================================================== #


def test_stream_file_concatenates():
    node = make_node()
    sftp = FakeSFTP()
    remote_dir = "/scratch/test/acp_jobs/job1"
    content = b"AB" * 50000  # 100000 bytes, > 1 chunk (64KB)
    sftp.files[remote_dir + "/big.out"] = content
    fetcher, pool, factory = make_fetcher(sftp, node)
    record = make_remote_record(remote_dir=remote_dir)

    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        chunks = list(fetcher.stream_file(record, "big.out"))
    assert b"".join(chunks) == content
    assert len(chunks) > 1, "should yield multiple chunks"
    pool.close()
    print("  [OK] stream_file: chunks concatenate to full content")


def test_stream_file_small():
    node = make_node()
    sftp = FakeSFTP()
    remote_dir = "/scratch/test/acp_jobs/job1"
    sftp.files[remote_dir + "/tiny.txt"] = b"hi"
    fetcher, pool, factory = make_fetcher(sftp, node)
    record = make_remote_record(remote_dir=remote_dir)

    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        chunks = list(fetcher.stream_file(record, "tiny.txt"))
    assert b"".join(chunks) == b"hi"
    pool.close()
    print("  [OK] stream_file: small file in single chunk")


# ====================================================================== #
# log_tail
# ====================================================================== #


def test_log_tail_basic():
    node = make_node()
    sftp = FakeSFTP()
    remote_dir = "/scratch/test/acp_jobs/job1"
    sftp.files[remote_dir + "/stdout.log"] = b"line1\nline2\nline3\nline4\nline5\n"
    sftp._set_attr(remote_dir + "/stdout.log", size=35)
    fetcher, pool, factory = make_fetcher(sftp, node)
    record = make_remote_record(remote_dir=remote_dir)

    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        tail = fetcher.log_tail(record, "stdout.log", lines=3)
    assert tail == "line3\nline4\nline5"
    pool.close()
    print("  [OK] log_tail: returns last N lines")


def test_log_tail_missing_returns_empty():
    node = make_node()
    sftp = FakeSFTP()
    fetcher, pool, factory = make_fetcher(sftp, node)
    record = make_remote_record()

    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        tail = fetcher.log_tail(record, "stdout.log", lines=10)
    assert tail == ""
    pool.close()
    print("  [OK] log_tail: empty string for missing file")


def test_log_tail_fewer_lines_than_requested():
    node = make_node()
    sftp = FakeSFTP()
    remote_dir = "/scratch/test/acp_jobs/job1"
    sftp.files[remote_dir + "/stdout.log"] = b"only\nline\n"
    sftp._set_attr(remote_dir + "/stdout.log", size=10)
    fetcher, pool, factory = make_fetcher(sftp, node)
    record = make_remote_record(remote_dir=remote_dir)

    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        tail = fetcher.log_tail(record, "stdout.log", lines=100)
    assert tail == "only\nline"
    pool.close()
    print("  [OK] log_tail: returns all when fewer than requested")


def test_log_tail_drops_partial_first_line_on_truncation():
    """When the read starts mid-file, the partial first line is dropped."""
    node = make_node()
    sftp = FakeSFTP()
    remote_dir = "/scratch/test/acp_jobs/job1"
    # Build a log larger than _LOG_TAIL_MAX_BYTES so head is truncated.
    # Pad the first line so it exceeds the 4MB cap.
    big_head = b"x" * (5 * 1024 * 1024)
    content = big_head + b"\nkeep1\nkeep2\nkeep3\n"
    sftp.files[remote_dir + "/stdout.log"] = content
    sftp._set_attr(remote_dir + "/stdout.log", size=len(content))
    fetcher, pool, factory = make_fetcher(sftp, node)
    record = make_remote_record(remote_dir=remote_dir)

    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        tail = fetcher.log_tail(record, "stdout.log", lines=10)
    # The giant 'xxxx...' line is dropped because it was only partially read.
    assert tail == "keep1\nkeep2\nkeep3"
    assert "x" * 100 not in tail
    pool.close()
    print("  [OK] log_tail: drops partial first line on head truncation")


# ====================================================================== #
# file_stat / read_range / read_tail / walk_remote_files
# ====================================================================== #


def test_file_stat():
    node = make_node()
    sftp = FakeSFTP()
    remote_dir = "/scratch/test/acp_jobs/job1"
    sftp.files[remote_dir + "/energy.out"] = b"-154.94\n"
    sftp._set_attr(remote_dir + "/energy.out", mtime=1234567890.0)
    fetcher, pool, factory = make_fetcher(sftp, node)
    record = make_remote_record(remote_dir=remote_dir)

    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        info = fetcher.file_stat(record, "energy.out")
    assert info.name == "energy.out"
    assert info.size == 8
    assert info.mtime == 1234567890.0
    assert info.is_dir is False
    pool.close()
    print("  [OK] file_stat: returns size/mtime/is_dir")


def test_file_stat_missing():
    node = make_node()
    sftp = FakeSFTP()
    fetcher, pool, factory = make_fetcher(sftp, node)
    record = make_remote_record()

    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        with pytest.raises(FileNotFoundError):
            fetcher.file_stat(record, "missing.txt")
    pool.close()
    print("  [OK] file_stat: FileNotFoundError for missing file")


def test_read_range():
    node = make_node()
    sftp = FakeSFTP()
    remote_dir = "/scratch/test/acp_jobs/job1"
    content = b"0123456789"
    sftp.files[remote_dir + "/digits.txt"] = content
    fetcher, pool, factory = make_fetcher(sftp, node)
    record = make_remote_record(remote_dir=remote_dir)

    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        data = fetcher.read_range(record, "digits.txt", offset=3, limit=4)
    assert data == b"3456"
    pool.close()
    print("  [OK] read_range: returns requested byte range")


def test_read_range_past_end():
    node = make_node()
    sftp = FakeSFTP()
    remote_dir = "/scratch/test/acp_jobs/job1"
    sftp.files[remote_dir + "/digits.txt"] = b"0123456789"
    fetcher, pool, factory = make_fetcher(sftp, node)
    record = make_remote_record(remote_dir=remote_dir)

    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        data = fetcher.read_range(record, "digits.txt", offset=8, limit=10)
    assert data == b"89"
    pool.close()
    print("  [OK] read_range: short read when range exceeds file size")


def test_read_range_negative_offset():
    node = make_node()
    sftp = FakeSFTP()
    remote_dir = "/scratch/test/acp_jobs/job1"
    sftp.files[remote_dir + "/digits.txt"] = b"0123456789"
    fetcher, pool, factory = make_fetcher(sftp, node)
    record = make_remote_record(remote_dir=remote_dir)

    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        with pytest.raises(RemoteFileError, match="offset must be non-negative"):
            fetcher.read_range(record, "digits.txt", offset=-1, limit=4)
    pool.close()
    print("  [OK] read_range: negative offset rejected")


def test_read_tail():
    node = make_node()
    sftp = FakeSFTP()
    remote_dir = "/scratch/test/acp_jobs/job1"
    sftp.files[remote_dir + "/output.log"] = b"a\nb\nc\nd\ne\n"
    fetcher, pool, factory = make_fetcher(sftp, node)
    record = make_remote_record(remote_dir=remote_dir)

    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        tail = fetcher.read_tail(record, "output.log", lines=3)
    assert tail == "c\nd\ne"
    pool.close()
    print("  [OK] read_tail: returns last N lines of any text file")


def test_read_tail_missing_returns_empty():
    node = make_node()
    sftp = FakeSFTP()
    fetcher, pool, factory = make_fetcher(sftp, node)
    record = make_remote_record()

    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        tail = fetcher.read_tail(record, "output.log", lines=10)
    assert tail == ""
    pool.close()
    print("  [OK] read_tail: empty string for missing file")


def test_walk_remote_files():
    node = make_node()
    sftp = FakeSFTP()
    remote_dir = "/scratch/test/acp_jobs/job1"
    sftp.files[remote_dir + "/stdout.log"] = b"ok\n"
    sftp.files[remote_dir + "/finalDFT/energy.out"] = b"-154.94\n"
    sftp.dirs.add(remote_dir + "/finalDFT")
    fetcher, pool, factory = make_fetcher(sftp, node)
    record = make_remote_record(remote_dir=remote_dir)

    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        files = list(fetcher.walk_remote_files(record))
    names = {rel for rel, _ in files}
    assert "stdout.log" in names
    assert "finalDFT/energy.out" in names
    pool.close()
    print("  [OK] walk_remote_files: recursively lists files")


def test_walk_remote_files_with_exclude():
    node = make_node()
    sftp = FakeSFTP()
    remote_dir = "/scratch/test/acp_jobs/job1"
    sftp.files[remote_dir + "/stdout.log"] = b"ok\n"
    sftp.files[remote_dir + "/job.rwf"] = b"binary"
    sftp.files[remote_dir + "/job.chk"] = b"binary"
    fetcher, pool, factory = make_fetcher(sftp, node)
    record = make_remote_record(remote_dir=remote_dir)

    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        files = list(fetcher.walk_remote_files(record, exclude=["*.rwf", "*.chk"]))
    names = {rel for rel, _ in files}
    assert "stdout.log" in names
    assert "job.rwf" not in names
    assert "job.chk" not in names
    pool.close()
    print("  [OK] walk_remote_files: exclude patterns filter files")


def test_walk_remote_files_with_include():
    node = make_node()
    sftp = FakeSFTP()
    remote_dir = "/scratch/test/acp_jobs/job1"
    sftp.files[remote_dir + "/stdout.log"] = b"ok\n"
    sftp.files[remote_dir + "/result.xyz"] = b"3\n\nC\n"
    sftp.files[remote_dir + "/energy.out"] = b"-154.94\n"
    fetcher, pool, factory = make_fetcher(sftp, node)
    record = make_remote_record(remote_dir=remote_dir)

    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        files = list(fetcher.walk_remote_files(record, include=["*.log", "*.xyz"]))
    names = {rel for rel, _ in files}
    assert "stdout.log" in names
    assert "result.xyz" in names
    assert "energy.out" not in names
    pool.close()
    print("  [OK] walk_remote_files: include patterns filter files")


# ====================================================================== #
# Preview / archive configuration limits
# ====================================================================== #


def test_remote_preview_config_defaults():
    cfg = RemotePreviewConfig()
    assert cfg.max_text_preview_bytes == 50 * 1024 * 1024
    assert cfg.max_stream_read_bytes == 200 * 1024 * 1024
    assert cfg.max_archive_bytes == 5 * 1024 * 1024 * 1024
    assert cfg.max_tail_lines == 5000
    print("  [OK] RemotePreviewConfig: defaults match plan limits")


# ====================================================================== #
# Path traversal guard (_safe_join)
# ====================================================================== #


def test_safe_join_normal():
    assert _safe_join("/scratch/jobs/1", "out.xyz") == "/scratch/jobs/1/out.xyz"


def test_safe_join_nested():
    assert _safe_join("/scratch/jobs/1", "finalDFT/sp.out") == "/scratch/jobs/1/finalDFT/sp.out"


def test_safe_join_traversal_blocked():
    with pytest.raises(RemoteFileError):
        _safe_join("/scratch/jobs/1", "../../etc/passwd")


def test_safe_join_absolute_blocked():
    with pytest.raises(RemoteFileError):
        _safe_join("/scratch/jobs/1", "/etc/shadow")


def test_safe_join_dotdot_into_parent_blocked():
    with pytest.raises(RemoteFileError):
        _safe_join("/scratch/jobs/1", "../2/secret")


def test_read_file_traversal_blocked():
    node = make_node()
    sftp = FakeSFTP()
    fetcher, pool, factory = make_fetcher(sftp, node)
    record = make_remote_record()

    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        with pytest.raises(RemoteFileError):
            fetcher.read_file(record, "../../etc/passwd")
    pool.close()
    print("  [OK] read_file: path traversal blocked")


def test_file_exists_traversal_blocked():
    node = make_node()
    sftp = FakeSFTP()
    fetcher, pool, factory = make_fetcher(sftp, node)
    record = make_remote_record()

    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        with pytest.raises(RemoteFileError):
            fetcher.file_exists(record, "../../../etc/shadow")
    pool.close()
    print("  [OK] file_exists: path traversal blocked")


# ====================================================================== #
# JobManager wiring
# ====================================================================== #


def test_manager_remote_fetcher_none_when_local():
    """A local-only JobManager has remote_fetcher == None."""
    from acp.scheduler.manager import JobManager

    with tempfile.TemporaryDirectory() as tmp:
        manager = JobManager(run_root=tmp, max_running=1)
        assert manager.remote_fetcher is None
        manager.shutdown()


def test_manager_remote_fetcher_built_when_remote():
    """An enabled remote JobManager exposes a RemoteResultFetcher."""
    from acp.scheduler.manager import JobManager

    node = make_node()
    config = make_config(node)
    with tempfile.TemporaryDirectory() as tmp:
        manager = JobManager(run_root=tmp, max_running=1, remote_config=config)
        assert manager.remote_fetcher is not None
        assert manager.remote_fetcher is manager._remote_fetcher
        manager.shutdown()
    print("  [OK] JobManager.remote_fetcher: built when remote enabled")


# ====================================================================== #
# Runner
# ====================================================================== #


if __name__ == "__main__":
    import sys

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception:
            failed += 1
            print(f"  [FAIL] {test.__name__}")
            import traceback

            traceback.print_exc()
    print(f"\n{passed}/{passed + failed} tests passed")
    sys.exit(1 if failed else 0)
