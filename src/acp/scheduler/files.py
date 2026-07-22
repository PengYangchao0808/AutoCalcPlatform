"""
Scheduler Files
===============

Build a result-file manifest for a job's work directory. Used by the
``/api/jobs/{id}/files`` endpoint to enumerate downloadable artifacts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_MAX_FILE_BYTES = 200 * 1024 * 1024
_IGNORED_SUFFIXES = {".tmp", ".pyc"}
_IGNORED_NAMES = {"__pycache__"}


def build_manifest(
    work_dir: Path | str, relative_path: str | None = None
) -> dict[str, Any]:
    """List immediate children of a directory within a job work directory.

    Args:
        work_dir: Job work directory root.
        relative_path: Subdirectory path relative to ``work_dir`` to list.
            If ``None``, lists top-level contents of ``work_dir``.  Only
            the **immediate** children (one level deep) are returned;
            descendant directories are **not** recursed into.

    Returns entries whose ``path`` is always relative to ``work_dir``.
    """
    root = Path(work_dir)
    if not root.exists():
        return {"work_dir": str(root), "files": [], "truncated": False}

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
            return {"work_dir": str(root), "files": [], "truncated": False}

    files: list[dict[str, Any]] = []
    try:
        entries = sorted(target.iterdir())
    except OSError:
        return {"work_dir": str(root), "files": [], "truncated": False}

    for path in entries:
        if path.name in _IGNORED_NAMES or path.suffix in _IGNORED_SUFFIXES:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        file_path = str(path.relative_to(root))
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
    return {"work_dir": str(root), "files": files, "truncated": False}


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
