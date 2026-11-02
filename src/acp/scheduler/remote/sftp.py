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
import tarfile
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

try:  # optional ``remote`` extra: importing this module must not crash a root install
    import paramiko
except ImportError:  # pragma: no cover - exercised without ``pip install acp[remote]``
    paramiko = None  # type: ignore[assignment]

from acp.scheduler.remote.config import RemoteNode

if TYPE_CHECKING:  # pragma: no cover - typing only (ssh.py needs the optional extra)
    from acp.scheduler.jobs import JobRecord
    from acp.scheduler.remote.ssh import SSHConnectionPool

logger = logging.getLogger(__name__)

__all__ = ["FileStager", "RemoteDirConflictError", "RemoteFileInfo"]

#: Timeout (seconds) for the remote ``tar`` extraction command; a release is a
#: few hundred small files, so this only guards a genuinely wedged node.
_UPLOAD_TAR_TIMEOUT = 600
#: Archive modes: executability is preserved, but setuid/setgid/sticky bits are
#: never written — the node must not gain privilege from an uploaded release.
_TAR_FILE_MODE = 0o644
_TAR_EXEC_MODE = 0o755


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

    def remote_rename(
        self, node: RemoteNode, src: str, dst: str, *, must_not_exist: bool = True
    ) -> None:
        """Rename *src* to *dst* with ``mv -n``/``mv -T`` semantics.

        With *must_not_exist* (default) the target is asserted absent
        BEFORE the rename so a POSIX ``mv`` can never nest *src* inside an
        existing *dst* directory or clobber it (D03 release publish and
        trash isolation both depend on this exclusivity).  Raises
        :class:`FileExistsError` when the target already exists; *src* is
        left untouched in every failure case.
        """
        src = _norm_remote(src)
        dst = _norm_remote(dst)
        with self._ssh.sftp_session(node) as sftp:
            parent = posixpath.dirname(dst)
            if parent:
                _ensure_remote_dir(sftp, parent)
            if must_not_exist:
                try:
                    sftp.stat(dst)
                except FileNotFoundError:
                    pass
                else:
                    raise FileExistsError(f"rename target already exists: {dst}")
            sftp.rename(src, dst)
            logger.debug(
                "Renamed %s:%s -> %s (must_not_exist=%s)", node.name, src, dst, must_not_exist
            )

    def remote_sha256(self, node: RemoteNode, remote_path: str) -> str:
        """Return the hex SHA-256 of *remote_path* via ``sha256sum``.

        Raises :class:`FileNotFoundError` when the remote file is missing
        or ``sha256sum`` fails — verification code treats that as "not
        present / not verified" rather than trusting a stale guess.
        """
        import shlex

        remote_path = _norm_remote(remote_path)
        code, out, err = self._ssh.execute(
            node, f"sha256sum {shlex.quote(remote_path)}", timeout=60
        )
        if code != 0:
            raise FileNotFoundError(
                f"remote sha256 failed for {remote_path} on {node.name}: "
                f"{(err or out).strip() or f'exit {code}'}"
            )
        digest = out.strip().split(None, 1)[0].lower() if out.strip() else ""
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise OSError(f"unparseable sha256sum output for {remote_path} on {node.name}: {out!r}")
        return digest

    #: Paths per ``sha256sum`` invocation for batched verification.
    _SHA256_BATCH = 96

    def remote_sha256_many(
        self, node: RemoteNode, remote_paths: list[str]
    ) -> dict[str, str] | None:
        """Batched ``sha256sum`` over many paths (one SSH command per chunk).

        Release verification used to run one SSH command per file, which costs a
        full round trip each (~1 s on a real cluster link): a 600-file source
        release therefore needed ~10 minutes of verification on **every**
        submission.  One command per chunk keeps the same per-file digest
        comparison at a fraction of the latency.

        Returns:
            ``path -> hex digest`` for the paths ``sha256sum`` reported, or
            ``None`` when the batch could not run (caller falls back to the
            per-file primitive).  Missing files are simply absent from the map,
            so the caller's equality check still fails closed.
        """
        import shlex

        normalized = [_norm_remote(path) for path in remote_paths]
        digests: dict[str, str] = {}
        for start in range(0, len(normalized), self._SHA256_BATCH):
            chunk = normalized[start : start + self._SHA256_BATCH]
            command = "sha256sum -- " + " ".join(shlex.quote(path) for path in chunk)
            try:
                code, out, err = self._ssh.execute(node, command, timeout=120)
            except Exception:
                logger.debug("batched sha256sum failed on %s", node.name, exc_info=True)
                return None
            if code != 0 and not out.strip():
                # A wholly failed command (not "one file is missing", which
                # still prints the digests it could compute).
                logger.debug(
                    "batched sha256sum exited %s on %s: %s", code, node.name, (err or out)[:400]
                )
                return None
            for line in out.splitlines():
                parts = line.strip().split(None, 1)
                if len(parts) != 2:
                    continue
                digest, name = parts[0].lower(), parts[1].strip()
                if name.startswith("*"):
                    name = name[1:]
                if len(digest) == 64 and all(c in "0123456789abcdef" for c in digest):
                    digests[name] = digest
        return digests

    def upload_files(self, node: RemoteNode, items: list[tuple[Path, str]]) -> None:
        """Upload many files over **one** SFTP session.

        A source release is hundreds of small files; opening one session per file
        dominated submission latency on a real link.  The chunk size is the
        caller's; this primitive only removes the per-file session.
        """
        if not items:
            return
        with self._ssh.sftp_session(node) as sftp:
            for local_path, remote_path in items:
                local = Path(local_path)
                if not local.is_file():
                    raise FileNotFoundError(f"Local file not found: {local}")
                target = _norm_remote(remote_path)
                parent = posixpath.dirname(target)
                if parent:
                    _ensure_remote_dir(sftp, parent)
                sftp.put(str(local), target)
                logger.debug("Uploaded %s -> %s:%s", local, node.name, target)

    def upload_tar_stream(
        self,
        node: RemoteNode,
        items: list[tuple[Path, str]],
        remote_root: str,
    ) -> bool:
        """Upload *items* as one local tar, unpacked under *remote_root*.

        A source release is hundreds of small files; even over one SFTP session
        the per-file ``put`` round trips dominate submission latency.  This
        primitive writes the whole set into a local temporary tar (archive names
        are the remote paths relative to *remote_root*), uploads it once and
        lets the node unpack it with a single ``tar -xf ... -C`` command.

        The caller must treat ``False`` as "use the per-file path": every
        failure (refused item set, tar build, upload, remote command) returns
        ``False`` and never raises.  The batch is only a transport optimisation
        — it is never the authority on content (callers verify digests after).

        Args:
            node: Target remote node.
            items: ``(local file, remote destination)`` pairs.  Each remote
                destination must live under *remote_root*; absolute paths,
                ``..`` traversal, empty names, duplicate names and symlinked
                local files are refused.
            remote_root: Remote extraction root (the release staging dir).

        Returns:
            ``True`` when the remote extraction command succeeded, otherwise
            ``False``.
        """
        if not items:
            return True
        root = _norm_remote(remote_root)
        try:
            members = _stream_tar_members(items, root)
        except (OSError, ValueError) as exc:
            logger.warning("Refusing tar upload to %s:%s: %s", node.name, root, exc)
            return False
        remote_tar = posixpath.join(root, f".acp_upload_{uuid.uuid4().hex[:12]}.tar")
        local_tar: str | None = None
        uploaded = False
        try:
            local_tar = _write_stream_tar(members)
            self.upload_file(node, Path(local_tar), remote_tar)
            uploaded = True
            import shlex

            command = (
                f"mkdir -p {shlex.quote(root)} && "
                f"tar -xf {shlex.quote(remote_tar)} -C {shlex.quote(root)} && "
                f"rm -f {shlex.quote(remote_tar)}"
            )
            code, _out, err = self._ssh.execute(node, command, timeout=_UPLOAD_TAR_TIMEOUT)
            if code != 0:
                logger.warning(
                    "Remote tar extraction failed on %s:%s (exit %s): %s",
                    node.name,
                    root,
                    code,
                    (err or "").strip()[:300],
                )
                self._remove_uploaded_tar(node, remote_tar)
                return False
            logger.debug("Unpacked %d files via tar stream on %s:%s", len(members), node.name, root)
            return True
        except Exception:  # noqa: BLE001 — documented fallback: caller re-uploads per file
            logger.warning(
                "Tar stream upload to %s:%s failed; falling back to per-file upload",
                node.name,
                root,
                exc_info=True,
            )
            if uploaded:
                self._remove_uploaded_tar(node, remote_tar)
            return False
        finally:
            if local_tar is not None:
                try:
                    os.unlink(local_tar)
                except OSError:
                    logger.debug("Could not remove local tar %s", local_tar, exc_info=True)

    def _remove_uploaded_tar(self, node: RemoteNode, remote_tar: str) -> None:
        """Best-effort removal of a temporary uploaded tar (never raises)."""
        try:
            self.remove_file(node, remote_tar)
        except Exception:  # noqa: BLE001 — cleanup must not mask the upload failure
            logger.debug(
                "Could not remove temporary tar %s:%s", node.name, remote_tar, exc_info=True
            )

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


def _tar_member_name(remote_path: str, remote_root: str) -> str:
    """Return the archive name for *remote_path*, relative to *remote_root*.

    Raises:
        ValueError: when the destination is outside *remote_root*, is absolute,
            is empty, contains a NUL byte, or escapes the extraction root via
            ``..``.  Archive names are the only place a tar upload could write
            outside the staging dir, so they are validated on the way IN.
    """
    root = _norm_remote(remote_root)
    target = _norm_remote(remote_path)
    prefix = root.rstrip("/") + "/" if root != "/" else "/"
    if not target.startswith(prefix):
        raise ValueError(f"tar destination {remote_path!r} is outside {remote_root!r}")
    relative = target[len(prefix) :]
    if not relative:
        raise ValueError("tar member name must not be empty")
    if "\x00" in relative:
        raise ValueError(f"tar member name {relative!r} contains a NUL byte")
    if relative.startswith("/") or posixpath.isabs(relative):
        raise ValueError(f"tar member name {relative!r} must be relative")
    normalized = posixpath.normpath(relative)
    if normalized != relative or normalized == ".." or normalized.startswith("../"):
        raise ValueError(f"tar member name {relative!r} escapes the extraction root")
    return relative


def _stream_tar_members(items: list[tuple[Path, str]], remote_root: str) -> list[tuple[Path, str]]:
    """Validate *items* and return ``(local file, archive name)`` pairs."""
    members: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for local_path, remote_path in items:
        local = Path(local_path)
        if local.is_symlink():
            # tar would archive the link itself; the per-file SFTP path follows
            # it.  Refuse in the batch primitive so the caller re-uploads.
            raise ValueError(f"refusing to archive symlink {local}")
        if not local.is_file():
            raise ValueError(f"local file not found: {local}")
        name = _tar_member_name(remote_path, remote_root)
        if name in seen:
            raise ValueError(f"duplicate tar member {name!r}")
        seen.add(name)
        members.append((local, name))
    return members


def _write_stream_tar(members: list[tuple[Path, str]]) -> str:
    """Write *members* into a fresh local temporary tar; return its path.

    Modes are normalised to 0644/0755 (executability preserved) so no setuid,
    setgid or sticky bit can be smuggled onto the node.
    """
    fd, tar_name = tempfile.mkstemp(prefix="acp-upload-", suffix=".tar")
    os.close(fd)
    try:
        with tarfile.open(tar_name, "w") as archive:
            for local, arcname in members:
                info = archive.gettarinfo(str(local), arcname=arcname)
                info.mode = _TAR_EXEC_MODE if (info.mode & 0o111) else _TAR_FILE_MODE
                info.uid = 0
                info.gid = 0
                info.uname = ""
                info.gname = ""
                with local.open("rb") as handle:
                    archive.addfile(info, handle)
    except BaseException:
        try:
            os.unlink(tar_name)
        except OSError:
            pass
        raise
    return tar_name


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
