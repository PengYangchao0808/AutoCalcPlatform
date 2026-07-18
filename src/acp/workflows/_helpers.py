"""Shared helper utilities for workflow modules.

Private helpers that are used by more than one workflow module.
"""

from __future__ import annotations


def sanitize_job_name(name: str) -> str:
    """Return a filesystem-safe job name."""
    cleaned = "".join(
        char if char.isalnum() or char in {"-", "_", "."} else "_" for char in name.strip()
    )
    return cleaned.strip("._") or "job"


__all__ = ["sanitize_job_name"]
