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
from acp_services.data.artifacts import (
    ArtifactPathRefusedError,
    is_safe_artifact_token,
    validate_artifact_relative_path,
)

__all__ = ["describe_job_artifacts"]

#: Hard bound so a polling status call cannot walk an arbitrarily large tree.
MAX_ARTIFACT_DESCRIPTORS = 50

#: A status poll may not pay for an arbitrarily large manifest.
MAX_MANIFEST_BYTES = 2 * 1024 * 1024


def _safe_relative(path: str) -> str | None:
    """Return the prepare-valid task-relative path, or None.

    Uses the same authority as ``prepare`` (``validate_artifact_relative_path``)
    so a discovered descriptor is always directly usable; Windows separators,
    absolute paths and traversal are refused there, not re-implemented here.
    """
    try:
        return validate_artifact_relative_path(str(path or ""))
    except (ArtifactPathRefusedError, ValueError):
        return None


def _safe_token(value: object) -> str | None:
    """A resource id prepare accepts — the same single authority."""
    text = str(value or "").strip()
    return text if text and is_safe_artifact_token(text) else None


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
    root = Path(task_dir)
    manifest_path = root / "RESULT" / MANIFEST_FILENAME
    try:
        if not manifest_path.is_file():
            return [], False
        if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
            return [], True
    except OSError:
        return [], False
    manifest = load_result_manifest(root)
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
