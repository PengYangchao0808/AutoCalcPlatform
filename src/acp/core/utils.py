"""Shared utility functions for ACP core."""

from __future__ import annotations

from pathlib import Path


def ensure_unique_dir(base_dir: str | Path) -> Path:
    """Create a unique directory by appending _1, _2, ... if *base_dir* exists."""
    base = Path(base_dir).resolve()
    if not base.exists():
        base.mkdir(parents=True)
        return base
    counter = 1
    while True:
        candidate = base.parent / f"{base.name}_{counter}"
        if not candidate.exists():
            candidate.mkdir(parents=True)
            return candidate
        counter += 1


__all__ = ["ensure_unique_dir"]
