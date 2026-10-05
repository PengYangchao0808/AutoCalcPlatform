"""
SFTP File Staging
=================

High-level SFTP helpers for uploading job inputs, downloading individual
result files on demand, and incrementally tailing remote log files.  All
operations go through :class:`~acp.scheduler.remote.ssh.SSHConnectionPool` so
they are thread-safe and connection-pooled.

Author: QCcalc Team
"""

from __future__ import annotations

import json
import logging
import os
import posixpath
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import paramiko

from acp.scheduler.remote.config import RemoteNode
from acp.scheduler.remote.ssh import SSHConnectionPool

if TYPE_CHECKING:  # pragma: no cover - typing only
    from acp.scheduler.jobs import JobRecord

logger = logging.getLogger(__name__)

__all__ = ["FileStager", "RemoteDirConflictError", "RemoteFileInfo"]


class RemoteDirConflictError(RuntimeError):
    """Remote job directory exists but is not owned by this job.

    Raised by :meth:`FileStager.claim_remote_job_dir` when the directory
    carries foreign (or unreadable) ``job.json``/``task.json`` ownership
    markers.  The directory is **never** deleted or modified — retention
    cleanup owns removal.
    """


@dataclass
class RemoteFileInfo:
    """Metadata for a single remote file or directory entry."""

    name: str
    size: int
    mtime: float
    is_dir: bool

    def to_dict(self) -> dict[str, int | float | str | bool]:
        return {
            "name": self.name,
            "size": self.size,
            "mtime": self.mtime,
            "is_dir": self.is_dir,
        }


class FileStager:
    """SFTP file transfer helper bound to a connection pool.

    Each method borrows an SFTP session from the pool for the duration of the
    operation, so callers never manage connections directly.
    """

    def __init__(self, ssh_pool: SSHConnectionPool) -> None:
        self._ssh = ssh_pool

    # ------------------------------------------------------------------ #
    # Upload
    # ------------------------------------------------------------------ #

    def upload_directory(self, node: RemoteNode, local_dir: Path, remote_dir: str) -> None:
        """Recursively upload *local_dir* to *remote_dir* on *node*.

        Creates remote directories as needed.  Symlinks are followed.
        """
        local_dir = Path(local_dir)
        if not local_dir.is_dir():
            raise FileNotFoundError(f"Local directory not found: {local_dir}")

        remote_dir = _norm_remote(remote_dir)
        with self._ssh.sftp_session(node) as sftp:
            _ensure_remote_dir(sftp, remote_dir)
            for root, _dirs, files in os.walk(local_dir):
                rel = Path(root).relative_to(local_dir)
                if str(rel) == ".":
                    remote_root = remote_dir
                else:
                    remote_root = posixpath.join(remote_dir, rel.as_posix())
                _ensure_remote_dir(sftp, remote_root)
                for fname in files:
                    local_path = Path(root) / fname
                    remote_path = posixpath.join(remote_root, fname)
                    sftp.put(str(local_path), remote_path)
                    logger.debug("Uploaded %s -> %s:%s", local_path, node.name, remote_path)

    def upload_file(self, node: RemoteNode, local_path: Path, remote_path: str) -> None:
        """Upload a single file."""
        local_path = Path(local_path)
        if not local_path.is_file():
            raise FileNotFoundError(f"Local file not found: {local_path}")
        remote_path = _norm_remote(remote_path)
        with self._ssh.sftp_session(node) as sftp:
            parent = posixpath.dirname(remote_path)
            if parent:
                _ensure_remote_dir(sftp, parent)
            sftp.put(str(local_path), remote_path)
            logger.debug("Uploaded %s -> %s:%s", local_path, node.name, remote_path)

    def upload_text(self, node: RemoteNode, content: str, remote_path: str) -> None:
        """Upload string *content* to *remote_path* on *node*."""
        remote_path = _norm_remote(remote_path)
        with self._ssh.sftp_session(node) as sftp:
            parent = posixpath.dirname(remote_path)
            if parent:
                _ensure_remote_dir(sftp, parent)
            with sftp.file(remote_path, "w") as f:
                f.write(content)
            logger.debug("Uploaded %d bytes text -> %s:%s", len(content), node.name, remote_path)

    # ------------------------------------------------------------------ #
    # Download
    # ------------------------------------------------------------------ #

    def download_file(self, node: RemoteNode, remote_path: str, local_path: Path) -> None:
        """Download a single remote file to *local_path*."""
        remote_path = _norm_remote(remote_path)
        local_path = Path(local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        with self._ssh.sftp_session(node) as sftp:
            sftp.get(remote_path, str(local_path))
            logger.debug("Downloaded %s:%s -> %s", node.name, remote_path, local_path)

    def read_remote_file(self, node: RemoteNode, remote_path: str) -> bytes:
        """Read the full contents of *remote_path* and return as bytes."""
        remote_path = _norm_remote(remote_path)
        with self._ssh.sftp_session(node) as sftp:
            with sftp.file(remote_path, "rb") as f:
                return f.read()

    def read_remote_text(self, node: RemoteNode, remote_path: str) -> str:
        """Read *remote_path* as a UTF-8 string."""
        return self.read_remote_file(node, remote_path).decode("utf-8", errors="replace")

    # ------------------------------------------------------------------ #
    # Listing
    # ------------------------------------------------------------------ #

    def list_remote_dir(self, node: RemoteNode, remote_path: str) -> list[RemoteFileInfo]:
        """List entries in *remote_path*, returning metadata for each.

        Uses :meth:`paramiko.SFTPClient.listdir_attr` to retrieve names and
        attributes in a single SFTP round-trip (O(1) instead of O(n)).
        """
        remote_path = _norm_remote(remote_path)
        with self._ssh.sftp_session(node) as sftp:
            entries: list[RemoteFileInfo] = []
            attrs = sftp.listdir_attr(remote_path)
            for attr in attrs:
                name = getattr(attr, "filename", "")
                if not name:
                    continue
                entries.append(
                    RemoteFileInfo(
                        name=name,
                        size=int(attr.st_size or 0),
                        mtime=float(attr.st_mtime or 0.0),
                        is_dir=stat.S_ISDIR(attr.st_mode or 0),
                    )
                )
            return entries

    def remote_exists(self, node: RemoteNode, remote_path: str) -> bool:
        """Check whether *remote_path* exists on *node*."""
        remote_path = _norm_remote(remote_path)
        with self._ssh.sftp_session(node) as sftp:
            try:
                sftp.stat(remote_path)
                return True
            except FileNotFoundError:
                return False

    # ------------------------------------------------------------------ #
    # Incremental log tailing
    # ------------------------------------------------------------------ #

    def tail_log(
        self,
        node: RemoteNode,
        remote_path: str,
        offset: int = 0,
        max_size: int = 8 * 1024 * 1024,
    ) -> tuple[bytes, int]:
        """Read *remote_path* from *offset* onward.

        Returns ``(new_bytes, new_offset)`` where *new_offset* is the byte
        position after the read (i.e. ``offset + len(data)``).  Pass
        *new_offset* as *offset* on the next call to get only newly-appended
        content.

        Args:
            node: Target remote node.
            remote_path: Path to the log file on the node.
            offset: Byte offset to start reading from.
            max_size: Maximum number of bytes to read in one call.  Caps
                memory use when tailing very large log files (default 8 MB).
        """
        remote_path = _norm_remote(remote_path)
        with self._ssh.sftp_session(node) as sftp:
            try:
                attr = sftp.stat(remote_path)
            except FileNotFoundError:
                return b"", offset

            file_size = int(attr.st_size or 0)
            if file_size <= offset:
                return b"", offset

            with sftp.file(remote_path, "rb") as f:
                f.seek(offset)
                data = f.read(max_size)
            # Return offset + len(data) (NOT file_size): if the file grows
            # between stat() and read(), file_size would over-report and
            # cause the next call to re-read content we already consumed.
            return data, offset + len(data)

    def tail_log_text(
        self,
        node: RemoteNode,
        remote_path: str,
        offset: int = 0,
        max_size: int = 8 * 1024 * 1024,
    ) -> tuple[str, int]:
        """Like :meth:`tail_log` but returns decoded text."""
        data, new_offset = self.tail_log(node, remote_path, offset=offset, max_size=max_size)
        return data.decode("utf-8", errors="replace"), new_offset

    # ------------------------------------------------------------------ #
    # Misc
    # ------------------------------------------------------------------ #

    def remove_file(self, node: RemoteNode, remote_path: str) -> None:
        """Delete a single remote file (no-op if it doesn't exist)."""
        remote_path = _norm_remote(remote_path)
        with self._ssh.sftp_session(node) as sftp:
            try:
                sftp.remove(remote_path)
            except FileNotFoundError:
                pass

    def remove_remote_dir(self, node: RemoteNode, remote_path: str) -> None:
        """Recursively delete *remote_path* via ``rm -rf`` over SSH.

        Used to clean up partially-prepared job directories when submission
        fails before the job starts running (plan P2-1).  Failures are
        logged but never raised — cleanup is best-effort.
        """
        remote_path = _norm_remote(remote_path)
        import shlex

        cmd = f"rm -rf {shlex.quote(remote_path)}"
        try:
            self._ssh.execute(node, cmd, timeout=60)
        except Exception:
            logger.warning(
                "Best-effort cleanup of %s:%s failed", node.name, remote_path, exc_info=True
            )

    def make_remote_dir(self, node: RemoteNode, remote_path: str) -> None:
        """Create *remote_path* (and parents) on *node*."""
        remote_path = _norm_remote(remote_path)
        with self._ssh.sftp_session(node) as sftp:
            _ensure_remote_dir(sftp, remote_path)

    def rename_remote(self, node: RemoteNode, src: str, dst: str) -> None:
        """Rename *src* to *dst* on *node* (parents ensured).

        Refuses to overwrite an existing *dst* (attempt receipts must never
        clobber a previous archive).  Used only for attempt-receipt
        archiving and retention moves — submission never renames a foreign
        directory.
        """
        src = _norm_remote(src)
        dst = _norm_remote(dst)
        with self._ssh.sftp_session(node) as sftp:
            parent = posixpath.dirname(dst)
            if parent:
                _ensure_remote_dir(sftp, parent)
            try:
                sftp.stat(dst)
            except FileNotFoundError:
                pass
            else:
                raise FileExistsError(f"rename target already exists: {dst}")
            sftp.rename(src, dst)
            logger.debug("Renamed %s:%s -> %s", node.name, src, dst)

    def claim_remote_job_dir(self, node: RemoteNode, remote_job_dir: str, record: JobRecord) -> str:
        """Exclusively claim *remote_job_dir* for *record*.

        Recursively ensures the parents, then creates the final directory.
        When the directory already exists, its ``job.json``/``task.json``
        ownership markers decide:

        * same ``job_id`` (any attempt — contract B reuses the storage dir
          across attempts) → ``"reused"``;
        * foreign ``job_id`` or unreadable markers →
          :class:`RemoteDirConflictError` — the directory is **never**
          deleted or modified here.

        Returns:
            ``"created"`` (newly made — deletable on submission failure)
            or ``"reused"`` (never deletable).
        """
        remote_path = _norm_remote(remote_job_dir)
        with self._ssh.sftp_session(node) as sftp:
            created = False
            try:
                sftp.stat(remote_path)
            except FileNotFoundError:
                parent = posixpath.dirname(remote_path)
                if parent:
                    _ensure_remote_dir(sftp, parent)
                try:
                    sftp.mkdir(remote_path)
                    created = True
                except OSError:
                    # Lost a creation race — fall through to ownership check.
                    created = False
            if created:
                return "created"

            owner = _read_owner_identity(sftp, remote_path)
            if owner is not None and owner[0] == record.id:
                return "reused"
            if owner is None:
                detail = "markers missing or unreadable"
            else:
                detail = f"owned by foreign job {owner[0]!r} (attempt {owner[1]})"
            raise RemoteDirConflictError(
                f"Remote dir {remote_path} on {node.name} already exists and is "
                f"not owned by job {record.id!r}: {detail}. Refusing to submit "
                "into (or delete) a conflicting directory."
            )


# ---------------------------------------------------------------------- #
# Internal helpers
# ---------------------------------------------------------------------- #


def _norm_remote(path: str) -> str:
    """Normalise a remote POSIX path (expand ``~`` is left to the server)."""
    return posixpath.normpath(path) if path else path


def _read_owner_identity(
    sftp: paramiko.SFTPClient, remote_dir: str
) -> tuple[str, int | None] | None:
    """Read the ``(job_id, attempt)`` ownership markers of *remote_dir*.

    Returns ``None`` when neither marker exists or parses — treated as an
    unreadable owner (conflict) by the claim.
    """
    for marker in ("job.json", "task.json"):
        try:
            with sftp.file(posixpath.join(remote_dir, marker), "rb") as f:
                raw = f.read()
        except (FileNotFoundError, OSError):
            continue
        if not raw:
            continue
        try:
            payload: Any = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError:
            continue
        if not isinstance(payload, dict):
            continue
        owner = payload.get("id") or payload.get("task_id")
        if isinstance(owner, str) and owner:
            attempt = payload.get("attempt")
            return owner, attempt if isinstance(attempt, int) else None
    return None


def _ensure_remote_dir(sftp: paramiko.SFTPClient, remote_dir: str) -> None:
    """Recursively create *remote_dir* via SFTP (like ``mkdir -p``)."""
    if not remote_dir or remote_dir == ".":
        return
    # Walk component by component.
    parts = [p for p in remote_dir.split("/") if p]
    if remote_dir.startswith("/"):
        current = "/"
    else:
        current = ""
    for part in parts:
        current = posixpath.join(current, part) if current else part
        if not current:
            continue
        try:
            sftp.stat(current)
        except FileNotFoundError:
            try:
                sftp.mkdir(current)
            except OSError:
                # Race or permission — stat again to confirm.
                try:
                    sftp.stat(current)
                except FileNotFoundError:
                    raise
