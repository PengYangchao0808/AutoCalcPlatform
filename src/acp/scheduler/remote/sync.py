"""
Code Syncer
===========

Incrementally synchronises ACP source code to remote compute nodes via SFTP.
The sync set is:

* ``src/acp/`` — **excluding** the ``api/`` and ``scheduler/`` sub-packages
  (not needed on the execution side).
* ``src/cccp/`` — the full QC interface library (Computational Chemistry
  Connection Package; formerly ``conformer_search``).
* ``requirements-node.txt`` — the execution-node runtime dependency list,
  consumed by :meth:`NodeManager.bootstrap_node` to install deps on the node
  (see plan "node config portability").  Synced so the dependency set
  travels with the code and a node can be reproducibly provisioned.

**Not** synced: ``config/defaults.yaml`` (QC paths differ per node),
``frontend/``, ``tests/``, ``pyproject.toml``, ``bin/``.

Sync strategy: each file's **local** mtime is recorded in a per-node JSON
state file.  On subsequent syncs, only files whose local mtime has changed
(or that are new) are uploaded.  Remote mtime is never consulted because
SFTP ``put`` overwrites it with the upload time, making it unreliable.

Author: QCcalc Team
"""

from __future__ import annotations

import json
import logging
import os
import posixpath
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from acp.scheduler.remote.config import RemoteNode
from acp.scheduler.remote.sftp import _ensure_remote_dir
from acp.scheduler.remote.ssh import SSHConnectionPool

logger = logging.getLogger(__name__)

__all__ = ["CodeSyncer", "SyncResult"]

# Directories under ``src/acp/`` that are NOT needed on the execution side.
_ACP_EXCLUDE_DIRS: frozenset[str] = frozenset({"api", "scheduler"})

# Globally excluded patterns (applied everywhere).
_EXCLUDE_DIR_NAMES: frozenset[str] = frozenset(
    {"__pycache__", ".git", ".mypy_cache", ".ruff_cache"}
)
_EXCLUDE_FILE_SUFFIXES: tuple[str, ...] = (".pyc", ".pyo")


def _project_root() -> Path:
    """Return the repository root (parent of ``src/``)."""
    return Path(__file__).resolve().parents[4]


def _default_state_dir() -> Path:
    return Path.home() / ".acp" / "remote_sync"


@dataclass
class SyncResult:
    """Outcome of a single sync operation.

    Attributes:
        uploaded: Number of files actually transferred.
        skipped: Number of files that were unchanged.
        total: Total files in the sync set.
        node_name: Name of the target node.
        errors: List of per-file error messages (empty if all succeeded).
    """

    uploaded: int = 0
    skipped: int = 0
    total: int = 0
    node_name: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _build_sync_file_list(project_root: Path) -> list[Path]:
    """Collect the full set of local files that should be synced.

    Returns absolute paths sorted for deterministic ordering.
    """
    files: list[Path] = []

    # 1. src/acp/ — excluding api/ and scheduler/
    acp_root = project_root / "src" / "acp"
    if acp_root.is_dir():
        files.extend(_walk_dir(acp_root, exclude_dirs=_ACP_EXCLUDE_DIRS | _EXCLUDE_DIR_NAMES))

    # 2. src/cccp/ — full QC interface library (formerly conformer_search)
    cs_root = project_root / "src" / "cccp"
    if cs_root.is_dir():
        files.extend(_walk_dir(cs_root, exclude_dirs=_EXCLUDE_DIR_NAMES))

    # 3. requirements-node.txt — node runtime deps (for bootstrap_node()).
    reqs = project_root / "requirements-node.txt"
    if reqs.is_file():
        files.append(reqs)

    files.sort()
    return files


def _walk_dir(root: Path, exclude_dirs: frozenset[str]) -> list[Path]:
    """Recursively collect files under *root*, skipping *exclude_dirs*."""
    result: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Filter out excluded directory names in-place (prunes the walk).
        dirnames[:] = [d for d in dirnames if d not in exclude_dirs]
        for fname in filenames:
            if fname.endswith(_EXCLUDE_FILE_SUFFIXES):
                continue
            result.append(Path(dirpath) / fname)
    return result


def _local_to_remote_path(project_root: Path, local_path: Path, remote_code_dir: str) -> str:
    """Map a local absolute path to its remote destination under *remote_code_dir*."""
    rel = local_path.relative_to(project_root)
    return posixpath.join(remote_code_dir, rel.as_posix())


class CodeSyncer:
    """Incremental code synchroniser for remote compute nodes.

    The per-node sync state (a JSON mapping of relative path -> local mtime)
    is stored under *state_dir* (default ``~/.acp/remote_sync/``).
    """

    def __init__(self, ssh_pool: SSHConnectionPool, state_dir: Path | None = None) -> None:
        self._ssh = ssh_pool
        self._state_dir = Path(state_dir) if state_dir else _default_state_dir()
        self._project_root = _project_root()

    # ------------------------------------------------------------------ #
    # State file I/O
    # ------------------------------------------------------------------ #

    def _state_file(self, node: RemoteNode) -> Path:
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in node.name)
        return self._state_dir / f"{safe_name}.json"

    def _load_state(self, node: RemoteNode) -> dict[str, float]:
        path = self._state_file(node)
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k): float(v) for k, v in data.items()}
        except (OSError, ValueError, TypeError):
            logger.warning("Corrupt sync state for node %s, starting fresh", node.name)
        return {}

    def _save_state(self, node: RemoteNode, state: dict[str, float]) -> None:
        """Atomically write the per-node sync state (temp + rename)."""
        path = self._state_file(node)
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(state, indent=2, sort_keys=True)
        # Write to a temp file in the same directory, then atomically replace.
        # Prevents a corrupt state file if the process is killed mid-write
        # (plan P2-12).
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".sync_state_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_name, str(path))
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def check_sync_needed(self, node: RemoteNode) -> bool:
        """Return True if any local file's mtime differs from the last sync."""
        state = self._load_state(node)
        for local_path in _build_sync_file_list(self._project_root):
            rel = local_path.relative_to(self._project_root).as_posix()
            try:
                mtime = local_path.stat().st_mtime
            except OSError:
                continue
            if state.get(rel) != mtime:
                return True
        return False

    def sync_code(self, node: RemoteNode, force: bool = False) -> SyncResult:
        """Synchronise code to *node*.

        Args:
            node: Target remote node.
            force: If True, upload all files regardless of mtime.

        Returns:
            :class:`SyncResult` summarising the operation.
        """
        all_files = _build_sync_file_list(self._project_root)
        old_state = {} if force else self._load_state(node)
        new_state: dict[str, float] = {}

        result = SyncResult(total=len(all_files), node_name=node.name)
        to_upload: list[tuple[Path, str, str]] = []  # (local, remote, rel)

        for local_path in all_files:
            rel = local_path.relative_to(self._project_root).as_posix()
            try:
                mtime = local_path.stat().st_mtime
            except OSError as exc:
                result.errors.append(f"stat failed: {rel}: {exc}")
                continue

            new_state[rel] = mtime
            if not force and old_state.get(rel) == mtime:
                result.skipped += 1
                continue

            remote_path = _local_to_remote_path(
                self._project_root, local_path, node.remote_code_dir
            )
            to_upload.append((local_path, remote_path, rel))

        if not to_upload:
            # Nothing changed — still save state to record the set.
            self._save_state(node, new_state)
            logger.info("Code sync: node=%s, all %d files up to date", node.name, result.total)
            return result

        logger.info(
            "Code sync: node=%s, uploading %d/%d files", node.name, len(to_upload), result.total
        )

        with self._ssh.sftp_session(node) as sftp:
            for local_path, remote_path, rel in to_upload:
                parent = posixpath.dirname(remote_path)
                if parent:
                    _ensure_remote_dir(sftp, parent)
                try:
                    sftp.put(str(local_path), remote_path)
                    result.uploaded += 1
                    logger.debug("Synced %s -> %s:%s", rel, node.name, remote_path)
                except OSError as exc:
                    result.errors.append(f"upload failed: {rel}: {exc}")
                    # Don't update mtime for failed files — retry next time.
                    new_state.pop(rel, None)

        self._save_state(node, new_state)
        return result
