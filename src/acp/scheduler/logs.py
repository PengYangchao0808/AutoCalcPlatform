"""
Scheduler Logs
==============

Read/tail helpers for per-job ``stdout.log`` / ``stderr.log``.
"""

from __future__ import annotations

from pathlib import Path


def read_log_tail(path: Path | str, lines: int = 300) -> list[str]:
    """Return up to ``lines`` trailing lines of a log file (best effort)."""
    p = Path(path)
    if not p.exists():
        return []
    try:
        all_lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return all_lines[-lines:] if len(all_lines) > lines else all_lines


def read_log_range(path: Path | str, offset: int = 0, max_lines: int = 2000) -> list[str]:
    """Return log lines starting at ``offset`` (line-indexed)."""
    p = Path(path)
    if not p.exists():
        return []
    try:
        all_lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return all_lines[offset : offset + max_lines]


__all__ = ["read_log_tail", "read_log_range"]
