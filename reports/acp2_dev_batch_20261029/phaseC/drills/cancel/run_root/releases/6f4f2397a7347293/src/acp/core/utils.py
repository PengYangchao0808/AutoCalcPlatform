"""Shared utility functions for ACP core."""

from __future__ import annotations

from pathlib import Path


def ensure_unique_dir(base_dir: str | Path) -> Path:
    """Create a unique directory by appending _1, _2, ... if *base_dir* exists."""
    base = Path(base_dir).resolve()
    try:
        base.mkdir(parents=True, exist_ok=False)
        return base
    except FileExistsError:
        pass

    counter = 1
    while counter < 100000:
        candidate = base.parent / f"{base.name}_{counter}"
        try:
            candidate.mkdir(parents=True, exist_ok=False)
            return candidate
        except FileExistsError:
            counter += 1

    raise RuntimeError(
        f"Failed to create unique directory for {base_dir} after {counter} attempts"
    )


__all__ = ["ensure_unique_dir"]
