"""Provenance blocks for Confsearch manifests (§5)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifacts import file_sha256


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def input_block(
    input_source: str,
    charge: int | None,
    multiplicity: int | None,
) -> dict[str, Any]:
    """Build the manifest ``input`` block with a content hash when possible."""
    block: dict[str, Any] = {
        "source": input_source,
        "charge": charge if charge is not None else 0,
        "multiplicity": multiplicity if multiplicity is not None else 1,
    }
    path = Path(input_source)
    if path.is_file():
        block["input_hash"] = file_sha256(path)
    return block


def source_artifact_ref(
    source_job_id: str | None,
    relative_path: str,
    path: Path,
    kind: str,
    stage: str,
) -> dict[str, Any]:
    """Standard cross-job artifact reference (§8)."""
    return {
        "source_job_id": source_job_id,
        "relative_path": relative_path,
        "sha256": file_sha256(path),
        "kind": kind,
        "stage": stage,
    }


def provenance_block(
    protocol: str,
    profile: str,
    refinement_policy: str,
    backend: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    block: dict[str, Any] = {
        "engine": "acp-confsearch",
        "protocol": protocol,
        "profile": profile,
        "refinement_policy": refinement_policy,
        "backend": backend,
        "created_at": utc_now_iso(),
    }
    if extra:
        block.update(extra)
    return block


__all__ = [
    "input_block",
    "provenance_block",
    "source_artifact_ref",
    "utc_now_iso",
]
