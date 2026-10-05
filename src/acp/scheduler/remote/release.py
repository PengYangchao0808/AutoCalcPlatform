"""
Content-Hashed Remote Code Releases (D03)
=========================================

Immutable, content-addressed snapshots of the ACP source tree on remote
compute nodes.  A release is identified by ``release_id =
sha256(canonical JSON over the files map only)[:16]`` — mtimes and git
metadata never participate, so the same content always maps to the same
release regardless of checkout state.

Publish protocol (verify-then-publish):

1. :func:`ensure_node_release` first takes a temporary reference
   (:func:`acquire_release_ref`) so GC cannot reclaim the release while it
   is being verified/bound; the reference is released on EVERY failure
   path and kept on success for the caller (job binding window, todo 8).
2. A cache hit requires ``releases/<id>/.complete`` to exist AND the whole
   directory to re-verify against the manifest (``sha256sum`` per file).
3. Otherwise every file is uploaded to a private staging dir
   ``releases/.staging/<id>.<uuid>/`` — the final directory is NEVER
   written directly.  Staging is reused across retries so a failed upload
   only re-sends the missing files.
4. After staging verifies, the publish runs under the node-level
   ``releases/.publish.lock``: ``remote_rename(must_not_exist=True)``
   atomically promotes staging → ``releases/<id>`` (refusing to overwrite
   or nest into an existing target), then ``.complete`` is written LAST.
   After that the directory is immutable — writers only ever write their
   own staging, readers only read ``.complete`` dirs.

:func:`prune_releases` shares the same node-level coordination lock
(``releases/.refs.lock`` + an in-process registry lock) as
:func:`acquire_release_ref`/:func:`release_release_ref`.  Inside ONE
critical section it refreshes references (DB any-status scan + active ref
files), evaluates eligibility (referenced ∪ active refs ∪ age floors) and
isolates/deletes — the lock is never released between check and action.

Author: QCcalc Team
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import posixpath
import shlex
import subprocess
import tempfile
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from acp.scheduler.remote.config import RemoteNode
from acp.scheduler.remote.sftp import FileStager
from acp.scheduler.remote.ssh import SSHConnectionPool
from acp.scheduler.remote.sync import _project_root, build_sync_file_list

if TYPE_CHECKING:  # pragma: no cover - typing only
    from acp.scheduler.store import JobStore

logger = logging.getLogger(__name__)

__all__ = [
    "COMPLETE_MARKER",
    "SCHEMA_VERSION",
    "PruneReport",
    "ReleaseBinding",
    "ReleaseError",
    "ReleaseLockTimeoutError",
    "ReleaseManifest",
    "acquire_release_ref",
    "build_release_manifest",
    "ensure_node_release",
    "prune_releases",
    "release_release_ref",
    "releases_root",
]

SCHEMA_VERSION = 1
COMPLETE_MARKER = ".complete"
STAGING_DIRNAME = ".staging"
REFS_DIRNAME = ".refs"
TRASH_DIRNAME = ".trash"
REFS_LOCK_NAME = ".refs.lock"
PUBLISH_LOCK_NAME = ".publish.lock"

# Seconds a single lock acquisition may spin before giving up.  A crashed
# holder leaves a stale lock dir — better to fail the operation than to
# delete a lock we do not own.
_LOCK_TIMEOUT = 60.0
_LOCK_POLL = 0.05


class ReleaseError(RuntimeError):
    """A release could not be verified, published, or reclaimed."""


class ReleaseLockTimeoutError(ReleaseError):
    """A node-level coordination lock could not be acquired in time."""


@dataclass(frozen=True)
class ReleaseManifest:
    """Content-derived identity of one code release.

    Attributes:
        schema_version: Manifest format version (``SCHEMA_VERSION``).
        release_id: ``sha256(canonical JSON over ``files`` only)[:16]``.
        files: ``relative posix path -> {"sha256": <hex>, "size": <bytes>}``.
        requirements_sha256: Digest of ``requirements-node.txt``.
        defaults_sha256: Digest of ``config/defaults.yaml``.
        git_commit: Best-effort ``git rev-parse HEAD`` (metadata only).
        dirty: Best-effort ``git status --porcelain`` non-empty flag.
            ``None``/failure degrades to ``True`` (metadata only — neither
            field participates in ``release_id``).
    """

    schema_version: int
    release_id: str
    files: dict[str, dict[str, str | int]]
    requirements_sha256: str
    defaults_sha256: str
    git_commit: str | None
    dirty: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "release_id": self.release_id,
            "files": {rel: dict(meta) for rel, meta in self.files.items()},
            "requirements_sha256": self.requirements_sha256,
            "defaults_sha256": self.defaults_sha256,
            "git_commit": self.git_commit,
            "dirty": self.dirty,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReleaseManifest:
        return cls(
            schema_version=int(data.get("schema_version", 0)),
            release_id=str(data["release_id"]),
            files={str(k): dict(v) for k, v in dict(data["files"]).items()},
            requirements_sha256=str(data.get("requirements_sha256", "")),
            defaults_sha256=str(data.get("defaults_sha256", "")),
            git_commit=data.get("git_commit"),
            dirty=bool(data.get("dirty", True)),
        )


@dataclass(frozen=True)
class ReleaseBinding:
    """A verified release directory on a node plus its temporary reference.

    Attributes:
        node: Node name the release lives on.
        release_id: Content hash identity.
        release_dir: Absolute remote directory (``.../releases/<id>``).
        ref_id: Token of the temporary reference kept from ensure — the
            caller must call :func:`release_release_ref` after its bind
            window (or when done) so GC can reclaim later.
        source: ``"remote"`` (verified existing), ``"published"`` (this
            call promoted staging) or ``"adopted"`` (raced an in-flight
            publish and verified the winner's directory).
    """

    node: str
    release_id: str
    release_dir: str
    ref_id: str
    source: str


@dataclass
class PruneReport:
    """Outcome of one :func:`prune_releases` sweep on one node."""

    node: str
    retention_days: int
    min_age_hours: int
    pruned: list[str] = field(default_factory=list)
    kept_referenced: list[str] = field(default_factory=list)
    kept_active_ref: list[str] = field(default_factory=list)
    kept_in_window: list[str] = field(default_factory=list)
    kept_unknown_age: int = 0
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "node": self.node,
            "retention_days": self.retention_days,
            "min_age_hours": self.min_age_hours,
            "pruned": list(self.pruned),
            "kept_referenced": list(self.kept_referenced),
            "kept_active_ref": list(self.kept_active_ref),
            "kept_in_window": list(self.kept_in_window),
            "kept_unknown_age": self.kept_unknown_age,
            "errors": list(self.errors),
            "dry_run": self.dry_run,
            "ok": self.ok,
        }


# ---------------------------------------------------------------------- #
# Path helpers
# ---------------------------------------------------------------------- #


def releases_root(node: RemoteNode) -> str:
    """Absolute remote directory holding all releases for *node*."""
    return posixpath.join(node.remote_code_dir, "releases")


def release_dir(node: RemoteNode, release_id: str) -> str:
    return posixpath.join(releases_root(node), release_id)


def _refs_dir(node: RemoteNode) -> str:
    return posixpath.join(releases_root(node), REFS_DIRNAME)


def _ref_path(node: RemoteNode, release_id: str, ref_id: str) -> str:
    return posixpath.join(_refs_dir(node), f"{release_id}.{ref_id}.ref")


def _safe_node_name(node: RemoteNode) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in node.name)


# ---------------------------------------------------------------------- #
# Manifest construction
# ---------------------------------------------------------------------- #


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _git_metadata(project_root: Path) -> tuple[str | None, bool]:
    """Best-effort ``(commit, dirty)`` — metadata only, never blocking."""
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=10,
        )
        commit = head.stdout.strip() if head.returncode == 0 else None
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=15,
        )
        dirty = True if status.returncode != 0 else bool(status.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return None, True
    return (commit or None), dirty


def build_release_manifest(project_root: Path | str) -> ReleaseManifest:
    """Build the content manifest for the current tree at *project_root*.

    The file set is exactly :func:`build_sync_file_list` (``src/acp``
    excluding ``api``/``scheduler``, ``src/cccp``,
    ``requirements-node.txt``, ``config/defaults.yaml``).  ``release_id``
    covers the ``files`` map ONLY — git commit/dirty are recorded as
    best-effort metadata and never influence identity.
    """
    root = Path(project_root)
    files: dict[str, dict[str, str | int]] = {}
    for path in build_sync_file_list(root):
        rel = path.relative_to(root).as_posix()
        digest, size = _sha256_file(path)
        files[rel] = {"sha256": digest, "size": size}

    release_id = hashlib.sha256(_canonical_json(files).encode("utf-8")).hexdigest()[:16]
    git_commit, dirty = _git_metadata(root)
    return ReleaseManifest(
        schema_version=SCHEMA_VERSION,
        release_id=release_id,
        files=files,
        requirements_sha256=str(files.get("requirements-node.txt", {}).get("sha256", "")),
        defaults_sha256=str(files.get("config/defaults.yaml", {}).get("sha256", "")),
        git_commit=git_commit,
        dirty=dirty,
    )


# ---------------------------------------------------------------------- #
# Coordination locks (shared by refs + prune) and publish lock
# ---------------------------------------------------------------------- #

# One in-process lock per node: serialises local threads before they race
# for the remote lock dir (cheap fast path; the remote lock remains the
# cross-process authority).
_NODE_LOCKS: dict[str, threading.Lock] = {}
_NODE_LOCKS_GUARD = threading.Lock()


def _node_proc_lock(node: RemoteNode) -> threading.Lock:
    with _NODE_LOCKS_GUARD:
        lock = _NODE_LOCKS.get(node.name)
        if lock is None:
            lock = threading.Lock()
            _NODE_LOCKS[node.name] = lock
        return lock


@contextmanager
def _lock_dir(
    ssh: SSHConnectionPool,
    node: RemoteNode,
    lock_path: str,
    *,
    timeout: float = _LOCK_TIMEOUT,
) -> Iterator[None]:
    """Exclusive mkdir-based lock at *lock_path* (released via rmdir)."""
    parent = posixpath.dirname(lock_path)
    acquire = f"mkdir -p {shlex.quote(parent)} && mkdir {shlex.quote(lock_path)}"
    deadline = time.monotonic() + timeout
    while True:
        code, _out, _err = ssh.execute(node, acquire, timeout=30)
        if code == 0:
            break
        if time.monotonic() >= deadline:
            raise ReleaseLockTimeoutError(
                f"timed out after {timeout:.0f}s waiting for lock {lock_path} on {node.name}"
            )
        time.sleep(_LOCK_POLL)
    try:
        yield
    finally:
        try:
            ssh.execute(node, f"rmdir {shlex.quote(lock_path)}", timeout=30)
        except Exception as exc:  # logged — a leaked dir only blocks future ops
            logger.warning("Failed to release lock %s on %s: %s", lock_path, node.name, exc)


@contextmanager
def _refs_lock(
    node: RemoteNode,
    ssh: SSHConnectionPool,
    *,
    timeout: float = _LOCK_TIMEOUT,
) -> Iterator[None]:
    """Node-level reference coordination lock (``releases/.refs.lock``).

    Shared by :func:`acquire_release_ref`, :func:`release_release_ref`
    and :func:`prune_releases` — ref refresh, eligibility and the delete
    action all happen while this lock is held.
    """
    with _node_proc_lock(node):
        with _lock_dir(
            ssh, node, posixpath.join(releases_root(node), REFS_LOCK_NAME), timeout=timeout
        ):
            yield


@contextmanager
def _publish_lock(
    ssh: SSHConnectionPool,
    node: RemoteNode,
    *,
    timeout: float = _LOCK_TIMEOUT,
) -> Iterator[None]:
    """Node-level publish serialisation (``releases/.publish.lock``)."""
    with _node_proc_lock(node):
        with _lock_dir(
            ssh,
            node,
            posixpath.join(releases_root(node), PUBLISH_LOCK_NAME),
            timeout=timeout,
        ):
            yield


# ---------------------------------------------------------------------- #
# Reference registry (in-process) + remote ref files
# ---------------------------------------------------------------------- #

# (node.name, release_id) -> {ref_id, ...} — refreshed against the remote
# ref files inside the coordination lock; a remote-only process crash can
# leave a stale .ref file behind, which conservatively protects the release.
_REF_REGISTRY: dict[tuple[str, str], set[str]] = {}
_REF_REGISTRY_GUARD = threading.Lock()

# (node.name, release_id) -> staging dir currently claimed by this process.
# Prevents two concurrent ensures from sharing one staging dir while still
# allowing a FAILED attempt's staging to be reused by the next ensure.
_ACTIVE_STAGING: dict[tuple[str, str], str] = {}
_STAGING_GUARD = threading.Lock()


def acquire_release_ref(
    node: RemoteNode,
    release_id: str,
    *,
    stager: FileStager,
    ssh: SSHConnectionPool,
    timeout: float = _LOCK_TIMEOUT,
) -> str:
    """Take a temporary reference protecting *release_id* from GC.

    Writes ``releases/.refs/<id>.<uuid>.ref`` and registers the token in
    the in-process registry, both under the shared coordination lock.
    Returns the ref token — release it with :func:`release_release_ref`.
    """
    ref_id = uuid.uuid4().hex[:12]
    key = (node.name, release_id)
    with _refs_lock(node, ssh, timeout=timeout):
        with _REF_REGISTRY_GUARD:
            _REF_REGISTRY.setdefault(key, set()).add(ref_id)
        try:
            payload = json.dumps(
                {
                    "release_id": release_id,
                    "ref_id": ref_id,
                    "node": node.name,
                    "pid": os.getpid(),
                    "created_at": time.time(),
                },
                sort_keys=True,
            )
            stager.upload_text(node, payload, _ref_path(node, release_id, ref_id))
        except BaseException:
            with _REF_REGISTRY_GUARD:
                refs = _REF_REGISTRY.get(key)
                if refs is not None:
                    refs.discard(ref_id)
                    if not refs:
                        _REF_REGISTRY.pop(key, None)
            raise
    return ref_id


def release_release_ref(
    node: RemoteNode,
    release_id: str,
    ref_id: str,
    *,
    stager: FileStager,
    ssh: SSHConnectionPool,
    timeout: float = _LOCK_TIMEOUT,
) -> None:
    """Drop a temporary reference (idempotent; safe on failure paths)."""
    key = (node.name, release_id)
    with _REF_REGISTRY_GUARD:
        refs = _REF_REGISTRY.get(key)
        if refs is not None:
            refs.discard(ref_id)
            if not refs:
                _REF_REGISTRY.pop(key, None)
    with _refs_lock(node, ssh, timeout=timeout):
        try:
            stager.remove_file(node, _ref_path(node, release_id, ref_id))
        except OSError:
            # Stale remote ref files only over-protect the release (GC
            # keeps it) — never fail the caller for a cleanup hiccup.
            logger.warning(
                "Could not remove ref file for %s on %s",
                release_id,
                node.name,
                exc_info=True,
            )


def _active_ref_release_ids(node: RemoteNode, stager: FileStager) -> set[str]:
    """Refresh active references: in-process registry ∪ remote ref files."""
    ids: set[str] = set()
    with _REF_REGISTRY_GUARD:
        for (node_name, release_id), refs in _REF_REGISTRY.items():
            if node_name == node.name and refs:
                ids.add(release_id)
    try:
        entries = stager.list_remote_dir(node, _refs_dir(node))
    except FileNotFoundError:
        entries = []
    for entry in entries:
        if not entry.name.endswith(".ref"):
            continue
        base = entry.name[: -len(".ref")]
        release_id = base.split(".", 1)[0]
        if release_id:
            ids.add(release_id)
    return ids


def _db_referenced_release_ids(job_store: JobStore) -> set[str]:
    """Release ids bound by jobs in ANY status (fail-closed on errors)."""
    return set(job_store.referenced_code_releases())


# ---------------------------------------------------------------------- #
# Verification helpers
# ---------------------------------------------------------------------- #


def _complete_marker_ok(
    node: RemoteNode, directory: str, release_id: str, stager: FileStager
) -> bool:
    marker = posixpath.join(directory, COMPLETE_MARKER)
    try:
        raw = stager.read_remote_text(node, marker)
    except OSError:
        return False
    if not raw.strip():
        return False
    try:
        data = json.loads(raw)
    except ValueError:
        return False
    if not isinstance(data, dict):
        return False
    return data.get("release_id") == release_id


def _verify_release_dir(
    node: RemoteNode, manifest: ReleaseManifest, directory: str, stager: FileStager
) -> bool:
    """Per-file ``sha256sum`` comparison of *directory* against *manifest*."""
    for rel, meta in manifest.files.items():
        target = posixpath.join(directory, rel)
        try:
            digest = stager.remote_sha256(node, target)
        except OSError:
            return False
        if digest != str(meta["sha256"]):
            return False
    return True


def _is_published(node: RemoteNode, manifest: ReleaseManifest, stager: FileStager) -> bool:
    directory = release_dir(node, manifest.release_id)
    return _complete_marker_ok(node, directory, manifest.release_id, stager) and (
        _verify_release_dir(node, manifest, directory, stager)
    )


def _marker_json(manifest: ReleaseManifest) -> str:
    return json.dumps(manifest.to_dict(), sort_keys=True, indent=2)


# ---------------------------------------------------------------------- #
# Local verified cache
# ---------------------------------------------------------------------- #


def _cache_path(state_dir: Path, node: RemoteNode) -> Path:
    return Path(state_dir) / "releases" / f"{_safe_node_name(node)}.json"


def _load_cache(state_dir: Path, node: RemoteNode) -> dict[str, Any]:
    path = _cache_path(state_dir, node)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _record_verified(state_dir: Path, node: RemoteNode, release_id: str) -> None:
    path = _cache_path(state_dir, node)
    cache = _load_cache(state_dir, node)
    cache[release_id] = {"verified_at": time.time()}
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".release_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, sort_keys=True, indent=2)
        os.replace(tmp_name, str(path))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _drop_stale_cache(state_dir: Path, node: RemoteNode, release_id: str) -> None:
    path = _cache_path(state_dir, node)
    cache = _load_cache(state_dir, node)
    if release_id in cache:
        cache.pop(release_id, None)
        try:
            path.write_text(json.dumps(cache, sort_keys=True, indent=2), encoding="utf-8")
        except OSError:
            logger.debug("Could not update release cache %s", path, exc_info=True)


# ---------------------------------------------------------------------- #
# Staging
# ---------------------------------------------------------------------- #


def _claim_staging(node: RemoteNode, stager: FileStager, release_id: str, *, force: bool) -> str:
    """Claim a staging dir — reuse a leftover one, or create a fresh uuid.

    Concurrent ensures in this process each get their OWN staging dir
    (an active lease blocks reuse); after a failed attempt the lease is
    released so the next ensure resumes the same dir and only re-sends
    missing files.
    """
    key = (node.name, release_id)
    staging_root = posixpath.join(releases_root(node), STAGING_DIRNAME)
    with _STAGING_GUARD:
        if not force and key not in _ACTIVE_STAGING:
            try:
                entries = stager.list_remote_dir(node, staging_root)
            except FileNotFoundError:
                entries = []
            candidates = sorted(
                e.name for e in entries if e.is_dir and e.name.startswith(release_id + ".")
            )
            if candidates:
                staging = posixpath.join(staging_root, candidates[0])
                _ACTIVE_STAGING[key] = staging
                return staging
        staging = posixpath.join(staging_root, f"{release_id}.{uuid.uuid4().hex[:12]}")
        _ACTIVE_STAGING[key] = staging
        return staging


def _release_staging_claim(node: RemoteNode, release_id: str) -> None:
    with _STAGING_GUARD:
        _ACTIVE_STAGING.pop((node.name, release_id), None)


def _upload_missing(
    node: RemoteNode,
    manifest: ReleaseManifest,
    project_root: Path,
    staging: str,
    stager: FileStager,
    *,
    force: bool,
) -> int:
    """Upload manifest files into *staging*, skipping verified ones."""
    uploaded = 0
    for rel, meta in manifest.files.items():
        remote = posixpath.join(staging, rel)
        if not force:
            try:
                current = stager.remote_sha256(node, remote)
            except OSError:
                current = None
            if current == str(meta["sha256"]):
                continue
        stager.upload_file(node, project_root / rel, remote)
        uploaded += 1
    return uploaded


# ---------------------------------------------------------------------- #
# ensure_node_release
# ---------------------------------------------------------------------- #


def ensure_node_release(
    node: RemoteNode,
    manifest: ReleaseManifest,
    *,
    stager: FileStager,
    ssh: SSHConnectionPool,
    state_dir: Path | str,
    force: bool = False,
    project_root: Path | str | None = None,
    timeout: float = _LOCK_TIMEOUT,
) -> ReleaseBinding:
    """Ensure *manifest* is published (verified + immutable) on *node*.

    Takes the temporary reference FIRST and releases it on every failure
    path; on success the ref is kept in the returned binding for the
    caller's bind window (todo 8 releases it).  Never writes the final
    directory directly and never writes after ``.complete`` — content is
    staged, verified, then exclusively published under
    ``releases/.publish.lock``.

    Args:
        node: Target remote node.
        manifest: Content manifest (see :func:`build_release_manifest`).
        stager: SFTP stager used for uploads/text writes.
        ssh: Connection pool for locks + ``sha256sum`` verification.
        state_dir: Base dir for the local verified cache
            (``~/.acp/remote_sync``).
        force: Skip cache/staging fast paths (still cannot overwrite an
            already-published immutable release).
        project_root: Local tree to upload from (defaults to the
            repository root).
        timeout: Per-lock acquisition timeout.

    Returns:
        :class:`ReleaseBinding` with the temp ref the caller must release.

    Raises:
        ReleaseError: Published directory exists but fails verification
            (never overwritten), or staging fails verification.
        OSError: Propagated upload/transport failures (no ``.complete``,
            no binding, ref released).
    """
    release_id = manifest.release_id
    state_path = Path(state_dir)
    ref_id = acquire_release_ref(node, release_id, stager=stager, ssh=ssh, timeout=timeout)
    try:
        final = release_dir(node, release_id)

        if not force and release_id in _load_cache(state_path, node):
            # Cache is bookkeeping only — a hit still needs the remote
            # `.complete` marker AND a full re-verification to hold.
            if _is_published(node, manifest, stager):
                _record_verified(state_path, node, release_id)
                return ReleaseBinding(
                    node=node.name,
                    release_id=release_id,
                    release_dir=final,
                    ref_id=ref_id,
                    source="remote",
                )
            _drop_stale_cache(state_path, node, release_id)
        elif not force and _is_published(node, manifest, stager):
            _record_verified(state_path, node, release_id)
            return ReleaseBinding(
                node=node.name,
                release_id=release_id,
                release_dir=final,
                ref_id=ref_id,
                source="remote",
            )

        root = Path(project_root) if project_root is not None else _project_root()
        staging = _claim_staging(node, stager, release_id, force=force)
        try:
            _upload_missing(node, manifest, root, staging, stager, force=force)
            if not _verify_release_dir(node, manifest, staging, stager):
                raise ReleaseError(
                    f"staging verification failed for release {release_id} on {node.name}"
                )

            source = "published"
            with _publish_lock(ssh, node, timeout=timeout):
                if stager.remote_exists(node, final):
                    # Never overwrite or nest into an existing target.
                    if not _verify_release_dir(node, manifest, final, stager):
                        raise ReleaseError(
                            f"release {release_id} already exists on {node.name} "
                            "but fails verification; refusing to overwrite an "
                            "immutable release directory"
                        )
                    if not _complete_marker_ok(node, final, release_id, stager):
                        # Interrupted publish (rename done, marker not yet
                        # written): content matches the manifest, so finish
                        # by writing the marker — still the LAST content write.
                        stager.upload_text(
                            node, _marker_json(manifest), posixpath.join(final, COMPLETE_MARKER)
                        )
                    stager.remove_remote_dir(node, staging)
                    source = "adopted"
                else:
                    stager.remote_rename(node, staging, final, must_not_exist=True)
                    stager.upload_text(
                        node, _marker_json(manifest), posixpath.join(final, COMPLETE_MARKER)
                    )

            _record_verified(state_path, node, release_id)
            return ReleaseBinding(
                node=node.name,
                release_id=release_id,
                release_dir=final,
                ref_id=ref_id,
                source=source,
            )
        finally:
            _release_staging_claim(node, release_id)
    except BaseException:
        release_release_ref(node, release_id, ref_id, stager=stager, ssh=ssh, timeout=timeout)
        raise


# ---------------------------------------------------------------------- #
# prune_releases
# ---------------------------------------------------------------------- #


def prune_releases(
    node: RemoteNode,
    *,
    stager: FileStager,
    ssh: SSHConnectionPool,
    job_store: JobStore,
    retention_days: int,
    min_age_hours: int = 24,
    dry_run: bool = False,
    timeout: float = _LOCK_TIMEOUT,
) -> PruneReport:
    """Reclaim unreferenced, aged-out releases from *node*.

    Runs ENTIRELY inside the shared coordination lock:

    1. refresh references (DB any-status scan + active ref files),
    2. eligibility = NOT referenced ∩ NO active ref ∩ BOTH age floors
       (``retention_days`` and ``min_age_hours`` — the stricter window
       wins; ``retention_days <= 0`` removes that floor),
    3. isolate to ``releases/.trash/<id>.<ts>`` then best-effort delete
       (a failed isolate leaves the release in place).

    The lock is never released between refresh, check and action, so a
    binding landing at any point during the sweep still protects its
    release.  A failed DB refresh aborts the sweep (fail-closed: never
    delete against a stale snapshot).

    Args:
        node: Target remote node.
        stager: SFTP stager for listing/renaming.
        ssh: Connection pool for the coordination lock.
        job_store: DB handle exposing ``referenced_code_releases()``.
        retention_days: Job-retention floor in days (``<= 0`` disables it).
        min_age_hours: Minimum release age before it may be reclaimed.
        dry_run: Report eligibility without isolating/deleting.
        timeout: Lock acquisition timeout.

    Returns:
        :class:`PruneReport` describing kept/pruned releases.
    """
    report = PruneReport(
        node=node.name,
        retention_days=retention_days,
        min_age_hours=min_age_hours,
        dry_run=dry_run,
    )
    root = releases_root(node)

    with _refs_lock(node, ssh, timeout=timeout):
        # ---- 1. refresh inside the critical section --------------------
        referenced = _db_referenced_release_ids(job_store)
        active = _active_ref_release_ids(node, stager)

        try:
            entries = stager.list_remote_dir(node, root)
        except FileNotFoundError:
            entries = []

        # ---- 2. eligibility (same critical section) ---------------------
        now = time.time()
        retention_window = retention_days * 86400 if retention_days > 0 else 0
        window = max(retention_window, min_age_hours * 3600)

        for entry in sorted(entries, key=lambda e: e.name):
            if not entry.is_dir or entry.name.startswith("."):
                continue
            rid = entry.name
            if rid in referenced:
                report.kept_referenced.append(rid)
                continue
            if rid in active:
                report.kept_active_ref.append(rid)
                continue
            if entry.mtime <= 0:
                # Unknown age — never a deletion basis.
                report.kept_unknown_age += 1
                continue
            if entry.mtime > now - window:
                report.kept_in_window.append(rid)
                continue

            # ---- 3. isolate + delete (same critical section) ------------
            if dry_run:
                report.pruned.append(rid)
                continue
            src = posixpath.join(root, rid)
            trash = posixpath.join(root, TRASH_DIRNAME, f"{rid}.{int(now)}")
            try:
                stager.remote_rename(node, src, trash, must_not_exist=True)
            except OSError as exc:
                report.errors.append(f"{rid}: isolate failed: {exc}")
                logger.warning("Release prune could not isolate %s on %s: %s", rid, node.name, exc)
                continue
            stager.remove_remote_dir(node, trash)
            report.pruned.append(rid)
            logger.info("Release prune: removed %s:%s", node.name, rid)

    if report.pruned or report.errors:
        logger.info(
            "Release prune on %s: %d removed, %d referenced, %d ref-held, "
            "%d in-window, %d error(s)",
            node.name,
            len(report.pruned),
            len(report.kept_referenced),
            len(report.kept_active_ref),
            len(report.kept_in_window),
            len(report.errors),
        )
    return report
