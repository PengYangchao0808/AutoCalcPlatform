"""Remote storage identity — derive remote job dirs from allocated storage.

Single source of truth for where a job's directory lives on a remote node:
``record.work_dir`` relative to the run root (project leaf + task leaf,
including any ``__NN`` dedupe suffix), joined under ``node.remote_work_dir``.

Contract (plan todo 4 / contract B):

* ``result["remote_dir"]`` is the **only** path key.
* ``result["remote"]`` holds metadata only (``schema``/``relative``/
  ``attempt``/``submit_state``/...) and always stays in sync with
  ``remote_dir``.
* Storage identity (the relative path) is stable across attempts — rerun /
  continue / edit-recalculate reuse the same remote directory.

Author: QCcalc Team
"""

from __future__ import annotations

import posixpath
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from acp.scheduler.jobs import JobRecord
    from acp.scheduler.remote.config import RemoteNode

__all__ = [
    "compose_remote_dir",
    "resolve_remote_dir",
    "storage_relative_path",
    "stored_remote_dir",
]

# How the current resolution was derived — recorded in
# ``result["remote"]["path_source"]`` so callers can emit the right audit
# event without re-probing.
_PATH_SOURCE_STORED = "stored"
_PATH_SOURCE_DERIVED = "derived"
_PATH_SOURCE_LEGACY_FLAT = "legacy_flat"
_PATH_SOURCE_FALLBACK = "fallback"


def storage_relative_path(record: JobRecord, run_root: str | Path) -> str:
    """Return *record.work_dir* relative to *run_root* (POSIX separators).

    Includes the project leaf and any ``__NN`` dedupe suffix, e.g.
    ``"projA/mol_opt_remark__02"``.

    Raises:
        ValueError: If the work dir is not inside *run_root*.
    """
    rel = Path(record.work_dir).relative_to(Path(run_root))
    return posixpath.normpath(rel.as_posix())


def compose_remote_dir(relative: str, node: RemoteNode | object) -> str:
    """Join *relative* under ``node.remote_work_dir``."""
    base = str(getattr(node, "remote_work_dir"))
    return posixpath.normpath(posixpath.join(base, str(relative)))


def stored_remote_dir(record: JobRecord, node: RemoteNode | object) -> str | None:
    """Return the persisted mapping (steps 1-3) without probing the node.

    Used by the manager to hand the already-resolved value to
    ``submit_remote``.  Returns ``None`` when the record carries no
    mapping yet (bare/legacy records need the ownership probe, which
    requires SSH — only the runner performs it).
    """
    result = record.result or {}
    stored = result.get("remote_dir")
    if stored:
        return str(stored)
    meta = result.get("remote")
    if isinstance(meta, dict):
        rel = meta.get("relative")
        if isinstance(rel, str) and rel.strip():
            return compose_remote_dir(rel, node)
    return None


def resolve_remote_dir(
    record: JobRecord,
    node: RemoteNode | object,
    *,
    explicit: str | None = None,
    probe: Callable[[str], bool] | None = None,
    run_root: str | Path | None = None,
) -> tuple[str, bool]:
    """Resolve the remote job directory for *record* on *node*.

    Resolution order:

    1. ``explicit`` argument (the manager's already-resolved value).
    2. ``record.result["remote_dir"]`` — the single persisted path key.
    3. Compose from ``record.result["remote"]["relative"]`` and write
       ``remote_dir`` back (keys stay in sync).
    4. Legacy in-flight dual-candidate probe: the new-style relative path
       *and* the old flat ``remote_work_dir/<task_dir_name or record.id>``
       (ownership decided by ``job.json``/``task.json`` ``job_id``/attempt
       through *probe*).  A flat hit is recorded as
       ``path_source="legacy_flat"`` for the ``remote.path_legacy_flat``
       event.
    5. Neither candidate owned → legacy fallback
       ``remote_work_dir/<task_dir_name or record.id>`` with
       ``fallback=True``; the exclusive claim at submit time decides
       created / reused / conflict (a foreign owner raises
       ``RemoteDirConflictError`` — never a delete).

    Only ever writes the single ``remote_dir`` path key; ``result["remote"]``
    holds metadata only and is kept in sync.

    Args:
        record: Job record whose storage identity is being resolved.
        node: Remote node (needs ``remote_work_dir``).
        explicit: Already-resolved dir (manager-supplied), highest priority.
        probe: Ownership predicate for the dual-candidate probe; returns
            ``True`` only when the remote dir's markers name this job.
        run_root: Run root for deriving the new-style relative path.  When
            omitted the last two components of ``record.work_dir`` are used
            (project leaf + task leaf).

    Returns:
        ``(remote_dir, fallback)``.
    """
    if explicit:
        _mark_path_source(record, _PATH_SOURCE_STORED)
        return posixpath.normpath(str(explicit)), False

    result = record.result or {}
    stored = result.get("remote_dir")
    if stored:
        _mark_path_source(record, _PATH_SOURCE_STORED)
        return str(stored), False

    meta = result.get("remote")
    rel = meta.get("relative") if isinstance(meta, dict) else None
    if isinstance(rel, str) and rel.strip():
        path = compose_remote_dir(rel, node)
        _sync_path_keys(record, path, rel)
        _mark_path_source(record, _PATH_SOURCE_STORED)
        return path, False

    # --- Step 4/5: legacy in-flight dual-candidate probe ---
    derived_rel = _derived_relative(record, run_root)
    cand_new = compose_remote_dir(derived_rel, node)
    if probe is not None and probe(cand_new):
        _sync_path_keys(record, cand_new, derived_rel)
        _mark_path_source(record, _PATH_SOURCE_DERIVED)
        return cand_new, False

    flat_leaf = _flat_leaf(record)
    cand_flat = compose_remote_dir(flat_leaf, node)
    if cand_flat != cand_new and probe is not None and probe(cand_flat):
        # Legacy flat dir owned by this job — persist it as the mapping so
        # every later read (fetcher/API/IRC cache) resolves the same value.
        _sync_path_keys(record, cand_flat, flat_leaf)
        _mark_path_source(record, _PATH_SOURCE_LEGACY_FLAT)
        return cand_flat, False

    # Very old pre-v2 layouts used the record id as the flat leaf.
    if record.id and flat_leaf != record.id:
        cand_id = compose_remote_dir(record.id, node)
        if probe is not None and probe(cand_id):
            _sync_path_keys(record, cand_id, record.id)
            _mark_path_source(record, _PATH_SOURCE_LEGACY_FLAT)
            return cand_id, False

    # Neither candidate is owned → legacy fallback; the exclusive claim
    # (created / reused / RemoteDirConflictError) decides at submit time.
    _sync_path_keys(record, cand_flat, flat_leaf)
    _mark_path_source(record, _PATH_SOURCE_FALLBACK)
    return cand_flat, True


# ---------------------------------------------------------------------- #
# Internals
# ---------------------------------------------------------------------- #


def _sync_path_keys(record: JobRecord, path: str, relative: str) -> None:
    """Write the single path key + keep ``remote`` metadata in sync."""
    result = dict(record.result or {})
    result["remote_dir"] = path
    meta = dict(result.get("remote") or {})
    meta.setdefault("schema", 1)
    meta["relative"] = relative
    result["remote"] = meta
    record.result = result


def _mark_path_source(record: JobRecord, source: str) -> None:
    result = dict(record.result or {})
    meta = dict(result.get("remote") or {})
    meta["path_source"] = source
    result["remote"] = meta
    record.result = result


def _derived_relative(record: JobRecord, run_root: str | Path | None) -> str:
    """New-style relative path for *record* (project leaf + task leaf)."""
    work_dir = Path(record.work_dir)
    if run_root is not None:
        try:
            return posixpath.normpath(work_dir.relative_to(Path(run_root)).as_posix())
        except ValueError:
            pass
    parts = work_dir.parts
    if len(parts) >= 2:
        return posixpath.normpath(posixpath.join(parts[-2], parts[-1]))
    return posixpath.normpath(parts[-1]) if parts else posixpath.normpath(".")


def _flat_leaf(record: JobRecord) -> str:
    """Legacy flat leaf: v2 ``task_dir_name`` (``record.id`` fallback)."""
    spec = record.spec
    if spec is not None and getattr(spec, "uses_v2_naming", False):
        return spec.task_dir_name()
    return record.id
