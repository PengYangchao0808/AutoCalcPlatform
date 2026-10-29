"""v2 unified storage-access backends (design doc §9.2/§9.3, §14 Phase 4)."""

from __future__ import annotations

import logging
import posixpath
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from acp.scheduler.remote.config import RemoteNode
    from acp.scheduler.remote.sftp import FileStager
    from acp.storage.mapping import NodePathMapping

logger = logging.getLogger(__name__)

__all__ = [
    "LocalStorageBackend",
    "NodeAgentStorageBackend",
    "SftpStorageBackend",
    "StorageEntry",
    "StorageError",
    "StorageNotFoundError",
    "TaskStorageBackend",
    "open_storage",
]

_AGENT_UNAVAILABLE = "NodeAgent storage backend requires a running node agent (not yet deployed)"


class StorageError(Exception):
    """Base error for storage-access failures."""


class StorageNotFoundError(StorageError):
    """Raised when the accessed path does not exist."""


@dataclass(frozen=True)
class StorageEntry:
    """One directory entry (file or subdirectory) in a storage listing."""

    name: str
    is_dir: bool
    size: int
    mtime: float


def _sort_entries(entries: list[StorageEntry]) -> list[StorageEntry]:
    """Sort entries dirs-first then by name (matches scheduler file listings)."""
    return sorted(entries, key=lambda e: (not e.is_dir, e.name))


def _norm_remote(path: str) -> str:
    """Normalise a remote POSIX path (mirrors sftp.py ``_norm_remote``)."""
    return posixpath.normpath(path) if path else path


class TaskStorageBackend(ABC):
    """Unified read/access interface over one task's storage location (§9.3).

    Implementations address the node where a task's files physically live:
    local filesystem, SSH/SFTP, or (future) an HTTP node agent.  Paths are
    ``str``; local backends accept absolute or root-relative paths, remote
    backends expect POSIX paths.
    """

    @abstractmethod
    def exists(self, path: str) -> bool:
        """Return True if *path* exists."""
        raise NotImplementedError

    @abstractmethod
    def is_dir(self, path: str) -> bool:
        """Return True if *path* exists and is a directory."""
        raise NotImplementedError

    @abstractmethod
    def list_dir(self, path: str) -> list[StorageEntry]:
        """List immediate children of directory *path* (dirs first, sorted)."""
        raise NotImplementedError

    @abstractmethod
    def read_text(self, path: str, max_bytes: int | None = None) -> str:
        """Read *path* as text; when *max_bytes* is given, only the tail."""
        raise NotImplementedError

    @abstractmethod
    def read_bytes(self, path: str) -> bytes:
        """Read the full binary contents of *path*."""
        raise NotImplementedError

    @abstractmethod
    def download(self, remote_path: str, local_path: Path) -> Path:
        """Copy *remote_path* to *local_path*; returns the local path."""
        raise NotImplementedError

    @abstractmethod
    def upload(self, local_path: Path, remote_path: str) -> None:
        """Copy *local_path* to *remote_path*."""
        raise NotImplementedError


class LocalStorageBackend(TaskStorageBackend):
    """Direct filesystem access (storage_mode ``"local"``)."""

    def __init__(self, root: Path | str | None = None) -> None:
        """Args: root — optional base directory that relative paths resolve against."""
        self._root = Path(root).expanduser() if root is not None else None

    def _resolve(self, path: str) -> Path:
        candidate = Path(path).expanduser()
        if self._root is not None and not candidate.is_absolute():
            candidate = self._root / candidate
        return candidate

    def exists(self, path: str) -> bool:
        try:
            return self._resolve(path).exists()
        except OSError as exc:
            raise StorageError(f"exists failed for {path!r}: {exc}") from exc

    def is_dir(self, path: str) -> bool:
        try:
            return self._resolve(path).is_dir()
        except OSError as exc:
            raise StorageError(f"is_dir failed for {path!r}: {exc}") from exc

    def list_dir(self, path: str) -> list[StorageEntry]:
        target = self._resolve(path)
        try:
            if not target.exists():
                raise StorageNotFoundError(f"no such directory: {path}")
            if not target.is_dir():
                raise StorageError(f"not a directory: {path}")
            children = list(target.iterdir())
        except StorageError:
            raise
        except OSError as exc:
            raise StorageError(f"failed to list {path!r}: {exc}") from exc
        entries: list[StorageEntry] = []
        for child in children:
            try:
                stat = child.stat()
                is_dir = child.is_dir()
            except OSError:
                logger.debug("list_dir: skipping vanished entry %s", child)
                continue
            entries.append(
                StorageEntry(
                    name=child.name,
                    is_dir=is_dir,
                    size=0 if is_dir else stat.st_size,
                    mtime=stat.st_mtime,
                )
            )
        return _sort_entries(entries)

    def read_text(self, path: str, max_bytes: int | None = None) -> str:
        target = self._resolve(path)
        try:
            if max_bytes is None:
                return target.read_bytes().decode("utf-8", errors="replace")
            size = target.stat().st_size
            with target.open("rb") as fh:
                if size > max_bytes:
                    fh.seek(size - max_bytes)
                data = fh.read()
        except FileNotFoundError as exc:
            raise StorageNotFoundError(f"no such file: {path}") from exc
        except OSError as exc:
            raise StorageError(f"read failed for {path!r}: {exc}") from exc
        return data.decode("utf-8", errors="replace")

    def read_bytes(self, path: str) -> bytes:
        try:
            return self._resolve(path).read_bytes()
        except FileNotFoundError as exc:
            raise StorageNotFoundError(f"no such file: {path}") from exc
        except OSError as exc:
            raise StorageError(f"read failed for {path!r}: {exc}") from exc

    def download(self, remote_path: str, local_path: Path) -> Path:
        src = self._resolve(remote_path)
        dst = Path(local_path).expanduser()
        try:
            if not src.is_file():
                raise StorageNotFoundError(f"no such file: {remote_path}")
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
        except StorageError:
            raise
        except OSError as exc:
            raise StorageError(f"download failed {remote_path!r} -> {dst}: {exc}") from exc
        return dst

    def upload(self, local_path: Path, remote_path: str) -> None:
        src = Path(local_path).expanduser()
        dst = self._resolve(remote_path)
        try:
            if not src.is_file():
                raise StorageNotFoundError(f"no such local file: {local_path}")
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
        except StorageError:
            raise
        except OSError as exc:
            raise StorageError(f"upload failed {src} -> {remote_path!r}: {exc}") from exc


class SftpStorageBackend(TaskStorageBackend):
    """SFTP access via the scheduler :class:`FileStager` (storage_mode ``"sftp"``).

    Thin delegation layer — all SSH/SFTP work stays in
    :mod:`acp.scheduler.remote.sftp` (requires the ``remote`` extra at
    runtime; this module never imports paramiko directly).

    Args:
        stager: ``FileStager`` bound to an SSH connection pool.
        node: ``RemoteNode`` the task's files live on.
    """

    def __init__(self, stager: FileStager, node: RemoteNode) -> None:
        self._stager = stager
        self._node = node

    def exists(self, path: str) -> bool:
        try:
            return bool(self._stager.remote_exists(self._node, _norm_remote(path)))
        except Exception as exc:
            raise StorageError(f"sftp exists failed for {path!r}: {exc}") from exc

    def is_dir(self, path: str) -> bool:
        remote = _norm_remote(path)
        name = posixpath.basename(remote)
        if not name:
            return self.exists(remote)  # POSIX root is a directory when present
        parent = posixpath.dirname(remote)
        try:
            entries = self._stager.list_remote_dir(self._node, parent)
        except Exception:
            logger.debug("is_dir: parent listing failed for %r", path, exc_info=True)
            return False
        for entry in entries:
            if getattr(entry, "name", None) == name:
                return bool(getattr(entry, "is_dir", False))
        return False

    def list_dir(self, path: str) -> list[StorageEntry]:
        remote = _norm_remote(path)
        try:
            raw = self._stager.list_remote_dir(self._node, remote)
        except FileNotFoundError as exc:
            raise StorageNotFoundError(f"no such directory: {path}") from exc
        except Exception as exc:
            raise StorageError(f"sftp list failed for {path!r}: {exc}") from exc
        entries = [
            StorageEntry(
                name=str(getattr(e, "name", "")),
                is_dir=bool(getattr(e, "is_dir", False)),
                size=int(getattr(e, "size", 0) or 0),
                mtime=float(getattr(e, "mtime", 0.0) or 0.0),
            )
            for e in raw
        ]
        return _sort_entries(entries)

    def read_text(self, path: str, max_bytes: int | None = None) -> str:
        data = self.read_bytes(path)
        if max_bytes is not None and len(data) > max_bytes:
            data = data[len(data) - max_bytes :]
        return data.decode("utf-8", errors="replace")

    def read_bytes(self, path: str) -> bytes:
        try:
            return bytes(self._stager.read_remote_file(self._node, _norm_remote(path)))
        except FileNotFoundError as exc:
            raise StorageNotFoundError(f"no such file: {path}") from exc
        except Exception as exc:
            raise StorageError(f"sftp read failed for {path!r}: {exc}") from exc

    def download(self, remote_path: str, local_path: Path) -> Path:
        dst = Path(local_path).expanduser()
        try:
            self._stager.download_file(self._node, _norm_remote(remote_path), dst)
        except FileNotFoundError as exc:
            raise StorageNotFoundError(f"no such file: {remote_path}") from exc
        except Exception as exc:
            raise StorageError(f"sftp download failed for {remote_path!r}: {exc}") from exc
        return dst

    def upload(self, local_path: Path, remote_path: str) -> None:
        src = Path(local_path).expanduser()
        try:
            if not src.is_file():
                raise StorageNotFoundError(f"no such local file: {local_path}")
            self._stager.upload_file(self._node, src, _norm_remote(remote_path))
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError(f"sftp upload failed for {remote_path!r}: {exc}") from exc


class NodeAgentStorageBackend(TaskStorageBackend):
    """Placeholder for HTTP node-agent access (storage_mode ``"agent"``, §9.3).

    The node agent is not yet deployed; every operation raises
    :class:`StorageError` so callers surface the gap explicitly.
    """

    def __init__(self, base_url: str = "") -> None:
        """Args: base_url — future agent endpoint, recorded for diagnostics."""
        self.base_url = base_url

    def _unavailable(self) -> StorageError:
        return StorageError(_AGENT_UNAVAILABLE)

    def exists(self, path: str) -> bool:
        raise self._unavailable()

    def is_dir(self, path: str) -> bool:
        raise self._unavailable()

    def list_dir(self, path: str) -> list[StorageEntry]:
        raise self._unavailable()

    def read_text(self, path: str, max_bytes: int | None = None) -> str:
        raise self._unavailable()

    def read_bytes(self, path: str) -> bytes:
        raise self._unavailable()

    def download(self, remote_path: str, local_path: Path) -> Path:
        raise self._unavailable()

    def upload(self, local_path: Path, remote_path: str) -> None:
        raise self._unavailable()


def _agent_base_url(node: Any) -> str:
    """Derive an agent base URL hint from a node object or URL string."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    return str(getattr(node, "host", None) or getattr(node, "name", "") or "")


def open_storage(
    mapping: NodePathMapping | None = None,
    *,
    storage_mode: str | None = None,
    node: Any = None,
    stager: Any = None,
) -> TaskStorageBackend:
    """Resolve a storage backend for a task mapping (design doc §9.3).

    Mode precedence: explicit *storage_mode* → ``mapping.storage_mode`` →
    ``"local"``.

    Args:
        mapping: Optional :class:`NodePathMapping` providing the default mode.
        storage_mode: Override; one of ``"local"`` / ``"sftp"`` / ``"agent"``.
        node: ``RemoteNode`` (sftp) or agent URL/node hint (agent).
        stager: ``FileStager`` instance required for ``"sftp"`` mode.

    Returns:
        A ready-to-use :class:`TaskStorageBackend`.

    Raises:
        StorageError: On unknown mode, or ``"sftp"`` without *stager*/*node*.
    """
    mode = storage_mode or (mapping.storage_mode if mapping is not None else None) or "local"
    if mode == "local":
        return LocalStorageBackend()
    if mode == "sftp":
        if stager is None or node is None:
            raise StorageError("sftp storage backend requires both `stager` and `node`")
        return SftpStorageBackend(stager=stager, node=node)
    if mode == "agent":
        return NodeAgentStorageBackend(base_url=_agent_base_url(node))
    raise StorageError(f"unknown storage_mode {mode!r}; expected 'local', 'sftp' or 'agent'")
