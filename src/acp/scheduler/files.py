"""
Scheduler Files
===============

Build a result-file manifest for a job's work directory. Used by the
``/api/jobs/{id}/files`` endpoint to enumerate downloadable artifacts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_MAX_FILE_BYTES = 200 * 1024 * 1024
_IGNORED_SUFFIXES = {".tmp", ".pyc"}
_IGNORED_NAMES = {"__pycache__"}
_RESULT_SUMMARY_FILENAME = "result_summary.json"


def build_manifest(
    work_dir: Path | str,
    relative_path: str | None = None,
    view: str = "raw",
) -> dict[str, Any]:
    """List immediate children of a directory within a job work directory.

    Args:
        work_dir: Job work directory root.
        relative_path: Subdirectory path relative to ``work_dir`` to list.
            If ``None``, lists top-level contents of ``work_dir``.  Only
            the **immediate** children (one level deep) are returned;
            descendant directories are **not** recursed into.
        view: ``"raw"`` (default) lists the disk as-is. ``"summary"``
            additionally surfaces products declared by each workflow's
            ``result_summary.json`` as a ``pinned`` array at the top level.

    Returns entries whose ``path`` is always relative to ``work_dir``.
    """
    root = Path(work_dir)
    if not root.exists():
        return {"work_dir": str(root), "files": [], "truncated": False, "pinned": []}

    target = root
    if relative_path is not None:
        try:
            target = (root / relative_path).resolve()
        except (ValueError, OSError):
            target = root
        try:
            target.relative_to(root)
        except ValueError:
            target = root
        if not target.is_dir():
            return {"work_dir": str(root), "files": [], "truncated": False, "pinned": []}

    files: list[dict[str, Any]] = []
    try:
        entries = sorted(target.iterdir())
    except OSError:
        return {"work_dir": str(root), "files": [], "truncated": False, "pinned": []}

    for path in entries:
        if path.name in _IGNORED_NAMES or path.suffix in _IGNORED_SUFFIXES:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        file_path = path.relative_to(root).as_posix()
        if path.is_dir():
            files.append(
                {
                    "path": file_path,
                    "size": 0,
                    "modified": stat.st_mtime,
                    "is_dir": True,
                }
            )
            continue
        if stat.st_size > _MAX_FILE_BYTES:
            continue
        files.append(
            {
                "path": file_path,
                "size": stat.st_size,
                "modified": stat.st_mtime,
                "is_dir": False,
            }
        )

    pinned: list[dict[str, Any]] = []
    if view == "summary" and relative_path is None:
        pinned = _collect_pinned(root)

    return {"work_dir": str(root), "files": files, "truncated": False, "pinned": pinned}


def _collect_pinned(root: Path) -> list[dict[str, Any]]:
    """Read every ``result_summary.json`` under ``root`` into pinned entries.

    Paths in the summaries are relative to their own directory; they are
    re-based to ``root`` so the frontend can open them directly.  Only
    existing files are surfaced; broken pointers are dropped silently.
    """
    pinned: list[dict[str, Any]] = []
    try:
        candidates = list(root.rglob(_RESULT_SUMMARY_FILENAME))
    except OSError:
        return pinned
    for summary_path in sorted(candidates):
        try:
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        base = summary_path.parent
        for item in payload.get("products") or []:
            if not isinstance(item, dict) or not item.get("path"):
                continue
            abs_path = base / str(item["path"])
            try:
                rel = abs_path.relative_to(root)
            except ValueError:
                continue
            if not abs_path.is_file():
                continue
            pinned.append(
                {
                    "label": str(item.get("label") or item["path"]),
                    "path": str(rel),
                    "kind": str(item.get("kind") or "file"),
                }
            )
    return pinned


def resolve_safe(work_dir: Path | str, relative: str) -> Path | None:
    """Resolve ``relative`` under ``work_dir`` without escaping it (None if unsafe)."""
    root = Path(work_dir).resolve()
    try:
        target = (root / relative).resolve()
    except (ValueError, OSError):
        return None
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return target if target.is_file() else None


__all__ = ["build_manifest", "resolve_safe"]
