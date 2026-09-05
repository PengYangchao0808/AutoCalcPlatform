"""Tests for acp.storage.backend — unified storage access (design doc §14 Phase 4)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from acp.storage.backend import (
    LocalStorageBackend,
    NodeAgentStorageBackend,
    SftpStorageBackend,
    StorageEntry,
    StorageError,
    StorageNotFoundError,
    TaskStorageBackend,
    open_storage,
)
from acp.storage.mapping import NodePathMapping


def _remote_entry(name: str, is_dir: bool, size: int = 0, mtime: float = 1.0) -> SimpleNamespace:
    return SimpleNamespace(name=name, is_dir=is_dir, size=size, mtime=mtime)


class TestLocalStorageBackend:
    def test_roundtrip(self, tmp_path: Path) -> None:
        backend = LocalStorageBackend()
        (tmp_path / "sub").mkdir()
        (tmp_path / "b.txt").write_text("hello", encoding="utf-8")
        (tmp_path / "a.txt").write_text("0123456789", encoding="utf-8")

        assert backend.exists(str(tmp_path)) is True
        assert backend.is_dir(str(tmp_path)) is True
        assert backend.is_dir(str(tmp_path / "a.txt")) is False
        assert backend.exists(str(tmp_path / "missing.txt")) is False

        entries = backend.list_dir(str(tmp_path))
        assert [e.name for e in entries] == ["sub", "a.txt", "b.txt"]
        assert [e.is_dir for e in entries] == [True, False, False]
        file_entry = entries[1]
        assert isinstance(file_entry, StorageEntry)
        assert file_entry.size == 10
        assert file_entry.mtime > 0

        assert backend.read_text(str(tmp_path / "a.txt")) == "0123456789"
        assert backend.read_bytes(str(tmp_path / "a.txt")) == b"0123456789"

    def test_list_dir_sorts_dirs_first_then_name(self, tmp_path: Path) -> None:
        backend = LocalStorageBackend()
        for name in ("z_dir", "a_dir"):
            (tmp_path / name).mkdir()
        for name in ("z.txt", "a.txt", "m.txt"):
            (tmp_path / name).write_text("x", encoding="utf-8")
        entries = backend.list_dir(str(tmp_path))
        assert [e.name for e in entries] == ["a_dir", "z_dir", "a.txt", "m.txt", "z.txt"]

    def test_storage_entry_is_frozen(self) -> None:
        entry = StorageEntry(name="a", is_dir=False, size=1, mtime=2.0)
        with pytest.raises(Exception):
            entry.size = 99  # type: ignore[misc]

    def test_read_text_tail_truncation(self, tmp_path: Path) -> None:
        backend = LocalStorageBackend()
        target = tmp_path / "log.txt"
        target.write_text("0123456789", encoding="utf-8")
        assert backend.read_text(str(target), max_bytes=4) == "6789"
        assert backend.read_text(str(target), max_bytes=100) == "0123456789"
        assert backend.read_text(str(target), max_bytes=0) == ""
        assert backend.read_text(str(target)) == "0123456789"

    def test_missing_raises_not_found(self, tmp_path: Path) -> None:
        backend = LocalStorageBackend()
        missing = str(tmp_path / "nope.txt")
        with pytest.raises(StorageNotFoundError):
            backend.read_text(missing)
        with pytest.raises(StorageNotFoundError):
            backend.read_bytes(missing)
        with pytest.raises(StorageNotFoundError):
            backend.download(missing, tmp_path / "out.bin")
        with pytest.raises(StorageNotFoundError):
            backend.list_dir(str(tmp_path / "nope_dir"))

    def test_list_dir_on_file_raises(self, tmp_path: Path) -> None:
        backend = LocalStorageBackend()
        target = tmp_path / "f.txt"
        target.write_text("x", encoding="utf-8")
        with pytest.raises(StorageError):
            backend.list_dir(str(target))

    def test_upload_download(self, tmp_path: Path) -> None:
        backend = LocalStorageBackend()
        src = tmp_path / "src.txt"
        src.write_text("payload", encoding="utf-8")

        backend.upload(src, str(tmp_path / "out" / "dst.txt"))
        dst = tmp_path / "out" / "dst.txt"
        assert dst.read_text(encoding="utf-8") == "payload"

        fetched = backend.download(str(dst), tmp_path / "fetch" / "copy.txt")
        assert isinstance(fetched, Path)
        assert fetched.read_text(encoding="utf-8") == "payload"

    def test_upload_missing_local_raises(self, tmp_path: Path) -> None:
        backend = LocalStorageBackend()
        with pytest.raises(StorageNotFoundError):
            backend.upload(tmp_path / "gone.txt", str(tmp_path / "dst.txt"))

    def test_root_anchors_relative_paths(self, tmp_path: Path) -> None:
        (tmp_path / "inner.txt").write_text("rel", encoding="utf-8")
        backend = LocalStorageBackend(root=tmp_path)
        assert backend.exists("inner.txt") is True
        assert backend.read_text("inner.txt") == "rel"


class TestSftpStorageBackend:
    def _backend(self) -> tuple[SftpStorageBackend, MagicMock, MagicMock]:
        stager = MagicMock()
        node = MagicMock()
        return SftpStorageBackend(stager=stager, node=node), stager, node

    def test_upload_delegates(self, tmp_path: Path) -> None:
        backend, stager, node = self._backend()
        local = tmp_path / "up.txt"
        local.write_text("data", encoding="utf-8")
        backend.upload(local, "jobs/task1/input.xyz")
        stager.upload_file.assert_called_once_with(node, local, "jobs/task1/input.xyz")

    def test_upload_missing_local_raises(self, tmp_path: Path) -> None:
        backend, stager, _node = self._backend()
        with pytest.raises(StorageNotFoundError):
            backend.upload(tmp_path / "gone.txt", "jobs/task1/x")
        stager.upload_file.assert_not_called()

    def test_download_delegates_and_returns_path(self, tmp_path: Path) -> None:
        backend, stager, node = self._backend()
        dst = tmp_path / "fetched.txt"
        result = backend.download("jobs/task1/RESULT/result_manifest.json", dst)
        assert result == dst
        stager.download_file.assert_called_once_with(
            node, "jobs/task1/RESULT/result_manifest.json", dst
        )

    def test_exists_delegates(self) -> None:
        backend, stager, node = self._backend()
        stager.remote_exists.return_value = True
        assert backend.exists("jobs/task1") is True
        stager.remote_exists.assert_called_once_with(node, "jobs/task1")
        stager.remote_exists.return_value = False
        assert backend.exists("jobs/missing") is False

    def test_list_dir_maps_and_sorts_entries(self) -> None:
        backend, stager, _node = self._backend()
        stager.list_remote_dir.return_value = [
            _remote_entry("z.txt", False, size=5, mtime=3.0),
            _remote_entry("sub", True),
            _remote_entry("a.txt", False, size=2, mtime=1.0),
        ]
        entries = backend.list_dir("jobs/task1")
        assert [e.name for e in entries] == ["sub", "a.txt", "z.txt"]
        assert [e.is_dir for e in entries] == [True, False, False]
        assert [e.size for e in entries] == [0, 2, 5]
        assert all(isinstance(e, StorageEntry) for e in entries)

    def test_is_dir_via_parent_listing(self) -> None:
        backend, stager, _node = self._backend()
        stager.list_remote_dir.return_value = [
            _remote_entry("WORK", True),
            _remote_entry("task.json", False, size=10),
        ]
        assert backend.is_dir("jobs/task1/WORK") is True
        assert backend.is_dir("jobs/task1/task.json") is False
        assert backend.is_dir("jobs/task1/unknown") is False

    def test_read_text_and_bytes(self) -> None:
        backend, stager, _node = self._backend()
        stager.read_remote_file.return_value = b"0123456789"
        assert backend.read_text("jobs/task1/stdout.log") == "0123456789"
        assert backend.read_text("jobs/task1/stdout.log", max_bytes=4) == "6789"
        assert backend.read_bytes("jobs/task1/stdout.log") == b"0123456789"

    def test_missing_wrapped_as_not_found(self) -> None:
        backend, stager, _node = self._backend()
        stager.read_remote_file.side_effect = FileNotFoundError("no such file")
        with pytest.raises(StorageNotFoundError):
            backend.read_bytes("jobs/task1/gone")
        stager.list_remote_dir.side_effect = FileNotFoundError("no such dir")
        with pytest.raises(StorageNotFoundError):
            backend.list_dir("jobs/gone")

    def test_stager_failure_wrapped_as_storage_error(self, tmp_path: Path) -> None:
        backend, stager, _node = self._backend()
        for method, call in (
            ("read_remote_file", backend.read_bytes),
            ("list_remote_dir", backend.list_dir),
            ("download_file", lambda p: backend.download(p, tmp_path / "x")),
        ):
            getattr(stager, method).side_effect = OSError("ssh boom")
            with pytest.raises(StorageError, match="ssh boom"):
                call("jobs/task1/x")
        local = tmp_path / "local.txt"
        local.write_text("x", encoding="utf-8")
        stager.upload_file.side_effect = RuntimeError("pool closed")
        with pytest.raises(StorageError, match="pool closed"):
            backend.upload(local, "jobs/task1/x")


class TestNodeAgentStorageBackend:
    def test_every_op_raises(self) -> None:
        backend = NodeAgentStorageBackend(base_url="http://node-a:9000")
        assert backend.base_url == "http://node-a:9000"
        with pytest.raises(StorageError, match="not yet deployed"):
            backend.exists("x")
        with pytest.raises(StorageError, match="not yet deployed"):
            backend.is_dir("x")
        with pytest.raises(StorageError, match="not yet deployed"):
            backend.list_dir("x")
        with pytest.raises(StorageError, match="not yet deployed"):
            backend.read_text("x")
        with pytest.raises(StorageError, match="not yet deployed"):
            backend.read_text("x", max_bytes=10)
        with pytest.raises(StorageError, match="not yet deployed"):
            backend.read_bytes("x")
        with pytest.raises(StorageError, match="not yet deployed"):
            backend.download("x", Path("/tmp/x"))
        with pytest.raises(StorageError, match="not yet deployed"):
            backend.upload(Path("/tmp/x"), "x")


class TestOpenStorageFactory:
    def test_default_is_local(self) -> None:
        assert isinstance(open_storage(), LocalStorageBackend)
        assert isinstance(open_storage(None, storage_mode=None), LocalStorageBackend)

    def test_mapping_mode_local(self) -> None:
        mapping = NodePathMapping(task_id="t1", storage_node="local", storage_path="/tmp/t1")
        assert isinstance(open_storage(mapping), LocalStorageBackend)

    def test_mapping_mode_sftp(self) -> None:
        mapping = NodePathMapping(
            task_id="t1", storage_node="node_a", storage_mode="sftp", storage_path="/scratch/t1"
        )
        stager = MagicMock()
        node = MagicMock()
        backend = open_storage(mapping, stager=stager, node=node)
        assert isinstance(backend, SftpStorageBackend)

    def test_sftp_requires_stager_and_node(self) -> None:
        with pytest.raises(StorageError, match="stager"):
            open_storage(storage_mode="sftp")
        with pytest.raises(StorageError, match="stager"):
            open_storage(storage_mode="sftp", stager=MagicMock())
        with pytest.raises(StorageError, match="stager"):
            open_storage(storage_mode="sftp", node=MagicMock())

    def test_agent_mode(self) -> None:
        backend = open_storage(storage_mode="agent", node="http://node-b:9000")
        assert isinstance(backend, NodeAgentStorageBackend)
        assert backend.base_url == "http://node-b:9000"
        node = SimpleNamespace(host="10.0.0.5", name="node_b")
        backend = open_storage(storage_mode="agent", node=node)
        assert backend.base_url == "10.0.0.5"

    def test_explicit_mode_overrides_mapping(self) -> None:
        mapping = NodePathMapping(
            task_id="t1", storage_node="node_a", storage_mode="local", storage_path="/tmp/t1"
        )
        backend = open_storage(mapping, storage_mode="agent", node="http://x")
        assert isinstance(backend, NodeAgentStorageBackend)

    def test_unknown_mode_rejected(self) -> None:
        with pytest.raises(StorageError, match="unknown storage_mode"):
            open_storage(storage_mode="nfs")


class TestTaskStorageBackendABC:
    def test_cannot_instantiate_abstract_base(self) -> None:
        with pytest.raises(TypeError):
            TaskStorageBackend()  # type: ignore[abstract]

    def test_implementations_are_backends(self, tmp_path: Path) -> None:
        assert isinstance(LocalStorageBackend(), TaskStorageBackend)
        sftp = SftpStorageBackend(stager=MagicMock(), node=MagicMock())
        assert isinstance(sftp, TaskStorageBackend)
        assert isinstance(NodeAgentStorageBackend(), TaskStorageBackend)
