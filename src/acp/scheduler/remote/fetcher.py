"""
Remote Result Fetcher
=====================

On-demand retrieval of remote job files and logs over SFTP.  Used by the
API layer to satisfy user-initiated list / download / log-tail requests.

Nothing is fetched automatically — every method is triggered by an explicit
HTTP request.  This honours the project constraint that *all results stay
on the remote node* and are only retrieved one file at a time when the user
asks.

The fetcher shares the same :class:`SSHConnectionPool`, :class:`FileStager`,
and :class:`RemoteExecutionConfig` as the
:class:`~acp.scheduler.remote.runner.RemoteJobRunner`, so SSH connections
are pooled across both execution and retrieval.

Author: QCcalc Team
"""

from __future__ import annotations

import logging
import os.path
import posixpath
import stat as stat_mod
from collections.abc import Iterator
from dataclasses import dataclass

from acp.scheduler.jobs import JobRecord
from acp.scheduler.remote.config import RemoteExecutionConfig, RemoteNode
from acp.scheduler.remote.sftp import FileStager, RemoteFileInfo
from acp.scheduler.remote.ssh import SSHConnectionPool

logger = logging.getLogger(__name__)

__all__ = [
    "RemoteResultFetcher",
    "RemoteFileError",
    "NotARemoteJobError",
    "RemotePreviewConfig",
]

# Maximum bytes read from the end of a log file when computing a tail.
# Caps memory use for very large logs (only the last few MB are needed).
_LOG_TAIL_MAX_BYTES = 4 * 1024 * 1024
# Chunk size (bytes) yielded by :meth:`RemoteResultFetcher.stream_file`.
_STREAM_CHUNK = 64 * 1024
# Maximum file size (bytes) allowed by :meth:`read_file`.  Larger files
# should be retrieved via :meth:`stream_file` instead.
_MAX_READ_BYTES = 200 * 1024 * 1024
# Maximum bytes allowed for online text preview (50 MB).  Larger text files
# are automatically downgraded to tail/range mode.
_MAX_TEXT_PREVIEW_BYTES = 50 * 1024 * 1024
# Maximum total bytes allowed for a single archive download (5 GB).
_MAX_ARCHIVE_BYTES = 5 * 1024 * 1024 * 1024
# Maximum trailing lines for tail/range preview modes.
_MAX_TAIL_LINES = 5000


@dataclass(frozen=True)
class RemotePreviewConfig:
    """Limits for remote result preview / download (non-persistent)."""

    max_text_preview_bytes: int = _MAX_TEXT_PREVIEW_BYTES
    max_stream_read_bytes: int = _MAX_READ_BYTES
    max_archive_bytes: int = _MAX_ARCHIVE_BYTES
    max_tail_lines: int = _MAX_TAIL_LINES


class RemoteFileError(RuntimeError):
    """Base error for remote file retrieval operations."""


class NotARemoteJobError(RemoteFileError):
    """The job record carries no remote execution metadata."""


class RemoteResultFetcher:
    """On-demand fetcher for remote job files and logs.

    Bound to a :class:`SSHConnectionPool` and :class:`FileStager` (shared
    with the runner) plus a :class:`RemoteExecutionConfig` for resolving
    node names.  All methods borrow a connection transiently and are safe
    to share across request threads.
    """

    def __init__(
        self,
        ssh_pool: SSHConnectionPool,
        stager: FileStager,
        remote_config: RemoteExecutionConfig,
    ) -> None:
        self._ssh = ssh_pool
        self._stager = stager
        self._config = remote_config

    # ------------------------------------------------------------------ #
    # Resolution
    # ------------------------------------------------------------------ #

    def is_remote_job(self, record: JobRecord) -> bool:
        """True when *record* carries remote execution metadata."""
        result = record.result or {}
        return bool(result.get("node") and result.get("remote_dir"))

    def resolve(self, record: JobRecord) -> tuple[RemoteNode, str]:
        """Resolve the ``(node, remote_dir)`` pair for *record*.

        Args:
            record: A :class:`JobRecord` whose ``result`` was populated by
                the :class:`RemoteJobRunner` (contains ``node`` and
                ``remote_dir`` keys).

        Returns:
            The resolved :class:`RemoteNode` and the absolute remote job
            directory path.

        Raises:
            NotARemoteJobError: If the record has no remote metadata.
            RemoteFileError: If the recorded node is no longer configured.
        """
        result = record.result or {}
        node_name = result.get("node")
        remote_dir = result.get("remote_dir")
        if not node_name or not remote_dir:
            raise NotARemoteJobError(
                f"Job {record.id!r} has no remote execution metadata "
                f"(missing node/remote_dir in result)"
            )
        node = self._config.get_node(str(node_name))
        if node is None:
            raise RemoteFileError(
                f"Node {node_name!r} referenced by job {record.id!r} is not "
                f"in the current configuration"
            )
        return node, str(remote_dir)

    # ------------------------------------------------------------------ #
    # Listing
    # ------------------------------------------------------------------ #

    def list_files(self, record: JobRecord) -> list[RemoteFileInfo]:
        """List the top-level contents of the remote job directory."""
        node, remote_dir = self.resolve(record)
        return self._stager.list_remote_dir(node, remote_dir)

    def list_files_recursive(
        self,
        record: JobRecord,
        max_entries: int = 1000,
    ) -> tuple[list[RemoteFileInfo], bool]:
        """Recursively list files and directories under the remote job directory.

        Returns ``(entries, truncated)`` where *truncated* is ``True`` if the
        remote tree contains more than *max_entries* entries.
        """
        node, remote_dir = self.resolve(record)
        entries: list[RemoteFileInfo] = []
        with self._ssh.sftp_session(node) as sftp:
            _list_remote_dir_recursive(sftp, remote_dir, "", entries, max_entries)
        return entries, len(entries) >= max_entries

    def walk_remote_files(
        self,
        record: JobRecord,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
    ) -> Iterator[tuple[str, RemoteFileInfo]]:
        """Recursively enumerate files under the remote job directory.

        Args:
            record: Job record with remote execution metadata.
            include: Optional glob patterns (e.g. ``["*.log", "*.xyz"]``).
                When provided, only matching files are yielded.
            exclude: Optional glob patterns (e.g. ``["*.rwf", "*.chk"]``).
                Matching files are skipped even if they also match *include*.

        Yields:
            ``(relative_path, info)`` for each file.  Directories are not
            yielded; only regular files.
        """
        node, remote_dir = self.resolve(record)
        with self._ssh.sftp_session(node) as sftp:
            yield from _walk_remote_dir(sftp, remote_dir, "", include or ["*"], exclude or [])

    # ------------------------------------------------------------------ #
    # Single file
    # ------------------------------------------------------------------ #

    def file_exists(self, record: JobRecord, filename: str) -> bool:
        """Check whether *filename* exists inside the remote job directory."""
        node, remote_dir = self.resolve(record)
        return self._stager.remote_exists(node, _safe_join(remote_dir, filename))

    def file_stat(self, record: JobRecord, filename: str) -> RemoteFileInfo:
        """Return size / mtime metadata for *filename* in the remote job directory.

        Raises:
            FileNotFoundError: If the file does not exist on the remote node.
            RemoteFileError: If the path escapes the job directory.
        """
        node, remote_dir = self.resolve(record)
        path = _safe_join(remote_dir, filename)
        with self._ssh.sftp_session(node) as sftp:
            attr = sftp.stat(path)
            return RemoteFileInfo(
                name=posixpath.basename(filename),
                size=int(attr.st_size or 0),
                mtime=float(attr.st_mtime or 0.0),
                is_dir=stat_mod.S_ISDIR(attr.st_mode or 0),
            )

    def read_file(self, record: JobRecord, filename: str) -> bytes:
        """Read *filename* fully into memory (best for small/medium files).

        Refuses files larger than :data:`_MAX_READ_BYTES` (200 MB); use
        :meth:`stream_file` for larger downloads.
        """
        node, remote_dir = self.resolve(record)
        path = _safe_join(remote_dir, filename)
        with self._ssh.sftp_session(node) as sftp:
            try:
                attr = sftp.stat(path)
            except FileNotFoundError:
                raise
            size = int(attr.st_size or 0)
            if size > _MAX_READ_BYTES:
                raise RemoteFileError(
                    f"File {filename!r} is {size} bytes (limit {_MAX_READ_BYTES}). "
                    f"Use the streaming download endpoint instead."
                )
            with sftp.file(path, "rb") as f:
                return f.read()

    def stream_file(self, record: JobRecord, filename: str) -> Iterator[bytes]:
        """Yield *filename* in chunks for a streaming download.

        Borrows an SFTP session for the duration of the stream and returns
        it to the pool when the iterator is exhausted or closed.  Use this
        for potentially large files so they need not fit entirely in memory.
        """
        node, remote_dir = self.resolve(record)
        path = _safe_join(remote_dir, filename)
        with self._ssh.sftp_session(node) as sftp:
            with sftp.file(path, "rb") as f:
                while True:
                    chunk = f.read(_STREAM_CHUNK)
                    if not chunk:
                        break
                    yield chunk

    def read_range(self, record: JobRecord, filename: str, offset: int, limit: int) -> bytes:
        """Read a byte range from *filename*.

        Args:
            record: Job record with remote execution metadata.
            filename: Relative path inside the remote job directory.
            offset: Byte offset to start reading from (must be >= 0).
            limit: Maximum number of bytes to read (capped at
                :data:`_MAX_READ_BYTES`).

        Returns:
            The requested byte range, possibly shorter if the file ends
            before ``offset + limit``.

        Raises:
            FileNotFoundError: If the remote file does not exist.
            RemoteFileError: If *limit* exceeds the allowed maximum.
        """
        if offset < 0:
            raise RemoteFileError("offset must be non-negative")
        node, remote_dir = self.resolve(record)
        path = _safe_join(remote_dir, filename)
        with self._ssh.sftp_session(node) as sftp:
            with sftp.file(path, "rb") as f:
                _safe_seek(f, offset)
                return f.read(min(limit, _MAX_READ_BYTES))

    def read_tail(self, record: JobRecord, filename: str, lines: int = 500) -> str:
        """Return up to *lines* trailing lines of any remote text file.

        Reads at most :data:`_LOG_TAIL_MAX_BYTES` from the end of the file so
        large logs do not exhaust memory.  Returns an empty string if the file
        does not exist yet.

        Args:
            lines: Number of trailing lines to return (1 to 5000).
        """
        node, remote_dir = self.resolve(record)
        path = _safe_join(remote_dir, filename)
        with self._ssh.sftp_session(node) as sftp:
            try:
                attr = sftp.stat(path)
            except FileNotFoundError:
                return ""
            except OSError:
                return ""
            size = int(attr.st_size or 0)
            start = max(0, size - _LOG_TAIL_MAX_BYTES)
            with sftp.file(path, "rb") as f:
                _safe_seek(f, start)
                data = f.read()

        text = data.decode("utf-8", errors="replace")
        all_lines = text.splitlines()
        if start > 0 and all_lines:
            all_lines = all_lines[1:]
        if len(all_lines) > lines:
            all_lines = all_lines[-lines:]
        return "\n".join(all_lines)

    # ------------------------------------------------------------------ #
    # Log tail
    # ------------------------------------------------------------------ #

    def log_tail(self, record: JobRecord, log_name: str, lines: int = 100) -> str:
        """Return up to *lines* trailing lines of the remote log *log_name*.

        Reads at most :data:`_LOG_TAIL_MAX_BYTES` from the end of the file so
        large logs do not exhaust memory.  Returns an empty string if the log
        does not exist yet.
        """
        node, remote_dir = self.resolve(record)
        path = _safe_join(remote_dir, log_name)
        with self._ssh.sftp_session(node) as sftp:
            try:
                attr = sftp.stat(path)
            except FileNotFoundError:
                return ""
            except OSError:
                return ""
            size = int(attr.st_size or 0)
            start = max(0, size - _LOG_TAIL_MAX_BYTES)
            with sftp.file(path, "rb") as f:
                # Guard against TOCTOU: if the log was truncated between
                # stat() and open(), seeking past the new end could raise
                # or silently position at end (paramiko behaviour varies).
                # Clamp to actual length after open to be safe.
                _safe_seek(f, start)
                data = f.read()

        text = data.decode("utf-8", errors="replace")
        all_lines = text.splitlines()
        # If we truncated the head, drop the partial first line.
        if start > 0 and all_lines:
            all_lines = all_lines[1:]
        if len(all_lines) > lines:
            all_lines = all_lines[-lines:]
        return "\n".join(all_lines)


# ---------------------------------------------------------------------- #
# Internal helpers
# ---------------------------------------------------------------------- #


def _safe_seek(f, position: int) -> None:
    """Seek *f* to *position*, clamping if the file is shorter than expected.

    Mitigates the TOCTOU race between :meth:`log_tail`'s ``stat()`` and
    ``open()`` when a running job truncates or rotates the log between the
    two calls.  If *position* is past the end, we silently seek to the end
    instead of failing.
    """
    try:
        f.seek(position)
    except OSError:
        try:
            f.seek(0, 2)  # seek to end
        except OSError:
            f.seek(0)
    except ValueError:
        f.seek(0, 2)


def _walk_remote_dir(
    sftp,
    base_dir: str,
    rel_prefix: str,
    include: list[str],
    exclude: list[str],
) -> Iterator[tuple[str, RemoteFileInfo]]:
    """Recursively walk *base_dir* and yield matching files."""
    import fnmatch

    try:
        entries = sftp.listdir_attr(base_dir)
    except FileNotFoundError:
        return
    for attr in entries:
        name = getattr(attr, "filename", "")
        if not name:
            continue
        rel = f"{rel_prefix}/{name}" if rel_prefix else name
        if stat_mod.S_ISDIR(attr.st_mode or 0):
            child_dir = posixpath.join(base_dir, name)
            yield from _walk_remote_dir(sftp, child_dir, rel, include, exclude)
            continue
        if any(fnmatch.fnmatch(rel, pat) for pat in exclude):
            continue
        if include != ["*"] and not any(fnmatch.fnmatch(rel, pat) for pat in include):
            continue
        yield (
            rel,
            RemoteFileInfo(
                name=name,
                size=int(attr.st_size or 0),
                mtime=float(attr.st_mtime or 0.0),
                is_dir=False,
            ),
        )


def _list_remote_dir_recursive(
    sftp,
    base_dir: str,
    rel_prefix: str,
    result: list[RemoteFileInfo],
    max_entries: int,
) -> None:
    """Recursively collect all entries under *base_dir* into *result*."""
    try:
        entries = sftp.listdir_attr(base_dir)
    except FileNotFoundError:
        return
    for attr in entries:
        if len(result) >= max_entries:
            return
        name = getattr(attr, "filename", "")
        if not name:
            continue
        rel = f"{rel_prefix}/{name}" if rel_prefix else name
        is_dir = stat_mod.S_ISDIR(attr.st_mode or 0)
        result.append(
            RemoteFileInfo(
                name=rel,
                size=0 if is_dir else int(attr.st_size or 0),
                mtime=float(attr.st_mtime or 0.0),
                is_dir=is_dir,
            )
        )
        if is_dir:
            child_dir = posixpath.join(base_dir, name)
            _list_remote_dir_recursive(sftp, child_dir, rel, result, max_entries)


def _safe_join(remote_dir: str, filename: str) -> str:
    """Join *filename* onto *remote_dir* without escaping it.

    Blocks path-traversal attempts (e.g. ``../../etc/passwd``) by verifying
    that the normalised result stays under *remote_dir* via
    :func:`os.path.commonpath`.  This correctly handles edge cases like
    ``remote_dir == "/"`` where a naive ``startswith`` check would break.

    Raises:
        RemoteFileError: If *filename* resolves outside *remote_dir*.
    """
    joined = posixpath.normpath(posixpath.join(remote_dir, filename))
    norm_remote = posixpath.normpath(remote_dir)
    common = os.path.commonpath([norm_remote, joined])
    if common != norm_remote:
        raise RemoteFileError(f"Path {filename!r} escapes the remote job directory {remote_dir!r}")
    return joined
