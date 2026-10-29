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


def _safe_token(value: object) -> str | None:
    """A resource id that :func:`prepare` will accept (no path syntax)."""
    text = str(value or "").strip()
    if not text or "\x00" in text or "/" in text or "\\" in text or ".." in text:
        return None
    return text


def describe_job_artifacts(
    task_dir: str | Path | None,
    *,
    limit: int = MAX_ARTIFACT_DESCRIPTORS,
    attempt: int | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Return ``(descriptors, truncated)`` for one task's manifest products.

    * ``task_dir`` is the caller's controlled read root for the **current**
      attempt (never a walk from the run root).
    * Only ``RESULT/``-relative paths and prepare-safe resource ids are
      published; unknown sizes stay ``None`` and ``prepared`` stays ``False``.
    * Iteration stops at ``limit + 1`` products: the manifest's size is not
      allowed to cost more than the bound.
    """
    if not task_dir:
        return [], False
    manifest = load_result_manifest(Path(task_dir))
    if manifest is None:
        return [], False
    bounded = max(1, int(limit))
    descriptors: list[dict[str, Any]] = []
    truncated = False
    for index, product in enumerate(manifest.products):
        if index >= bounded:
            truncated = True
            break
        relative = _safe_relative(getattr(product, "path", ""))
        resource_id = _safe_token(getattr(product, "id", ""))
        if relative is None or resource_id is None:
            continue
        descriptor: dict[str, Any] = {
            "resource_id": resource_id,
            "relative_path": f"RESULT/{relative}",
            "kind": getattr(product.kind, "value", str(product.kind)),
            "label": str(getattr(product, "label", "")),
            "size_bytes": None,
            "prepared": False,
        }
        if attempt is not None:
            descriptor["attempt"] = int(attempt)
        descriptors.append(descriptor)
    return descriptors, truncated


_ = MANIFEST_FILENAME  # re-exported contract name, kept importable for callers
