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


def build_manifest(work_dir: Path | str) -> dict[str, Any]:
    """Walk a job work directory and return a structured file manifest."""
    root = Path(work_dir)
    if not root.exists():
        return {"work_dir": str(root), "files": [], "truncated": False}

    files: list[dict[str, Any]] = []
    truncated = False
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        if path.name in _IGNORED_NAMES or path.suffix in _IGNORED_SUFFIXES:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_size > _MAX_FILE_BYTES:
            continue
        files.append(
            {
                "path": str(path.relative_to(root)),
                "size": stat.st_size,
                "modified": stat.st_mtime,
            }
        )
        if len(files) >= 1000:
            truncated = True
            break
    return {"work_dir": str(root), "files": files, "truncated": truncated}


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
