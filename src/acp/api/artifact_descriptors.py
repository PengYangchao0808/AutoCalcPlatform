"""Bounded, read-only artifact discovery for the task status payload.

The manifest (``RESULT/result_manifest.json``) is the product index; it does
not carry digest/size, so this projection never invents them.  A descriptor is
*not* a prepared ``ArtifactRef``: ``acp_prepare_artifact`` is still the step
that produces the frozen reference, and only its answer is readable.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from acp.results.manifest import MANIFEST_FILENAME, load_result_manifest

__all__ = ["describe_job_artifacts"]

#: Hard bound so a polling status call cannot walk an arbitrarily large tree.
MAX_ARTIFACT_DESCRIPTORS = 50


def _safe_relative(path: str) -> str | None:
    """Return a task-relative path or None (absolute/traversal/invalid)."""
    text = str(path or "").strip()
    if not text or "\x00" in text:
        return None
    candidate = PurePosixPath(text)
    if candidate.is_absolute() or any(part in {"..", ""} for part in candidate.parts):
        return None
    return candidate.as_posix()


def describe_job_artifacts(
    task_dir: str | Path | None,
    *,
    limit: int = MAX_ARTIFACT_DESCRIPTORS,
) -> tuple[list[dict[str, Any]], bool]:
    """Return ``(descriptors, truncated)`` for one task's manifest products.

    Only ``RESULT/``-relative product paths are published; unknown sizes stay
    ``None`` and ``prepared`` stays ``False`` (the caller has not prepared it).
    """
    if not task_dir:
        return [], False
    manifest = load_result_manifest(Path(task_dir))
    if manifest is None:
        return [], False
    bounded = max(1, int(limit))
    descriptors: list[dict[str, Any]] = []
    for product in manifest.products:
        relative = _safe_relative(getattr(product, "path", ""))
        if relative is None:
            continue
        descriptors.append(
            {
                "resource_id": str(product.id),
                "relative_path": f"RESULT/{relative}",
                "kind": getattr(product.kind, "value", str(product.kind)),
                "label": str(getattr(product, "label", "")),
                "size_bytes": None,
                "prepared": False,
            }
        )
    return descriptors[:bounded], len(descriptors) > bounded


_ = MANIFEST_FILENAME  # re-exported contract name, kept importable for callers
