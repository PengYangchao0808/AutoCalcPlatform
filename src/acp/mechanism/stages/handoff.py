"""Cross-job artifact handoff for the four-stage mechanism flow (plan §8).

A downstream stage never receives arbitrary absolute paths — it receives an
artifact reference (``source_job_id`` + ``relative_path`` + ``sha256`` +
``kind`` + ``stage``). The server (API submission / scheduler runner)
resolves the source job, verifies the reference, and materializes a local
handoff copy; the stage runners then read the copy.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path

from acp.confsearch.shared.artifacts import (
    copy_tree_items,
    sha256_label,
    sha256_matches,
)

logger = logging.getLogger(__name__)

S1_MANIFEST_KIND = "confsearch_manifest"
S2_MANIFEST_KIND = "s2_path_manifest"
S3_MANIFEST_KIND = "s3_lowconfirm_manifest"

STAGE_SOURCE_KINDS: dict[str, str] = {
    "S2": S1_MANIFEST_KIND,
    "S3": S2_MANIFEST_KIND,
    "S4": S3_MANIFEST_KIND,
}

_MANIFEST_SIGNATURES: dict[str, tuple[str, str]] = {
    # kind -> (schema_version, workflow)
    S1_MANIFEST_KIND: ("confsearch_v1", "Confsearch"),
    S2_MANIFEST_KIND: ("s2_path_v1", "PESsearch"),
    S3_MANIFEST_KIND: ("s3_lowconfirm_v1", "Lowconfirm"),
}

HANDOFF_PAYLOAD_DIRS: tuple[str, ...] = (
    "conformers",
    "refinement",
    "ts_guesses",
    "intermediate_guesses",
    "path",
    "optimized",
    "frequencies",
    "irc",
    "single_points",
    "thermo",
)

JOBS_DB_FILENAME = "acp_jobs.db"


class ArtifactRefError(ValueError):
    """Raised when an artifact reference fails validation."""


def expected_source_kind(stage: str) -> str:
    """Return the manifest kind a stage must be fed from."""
    try:
        return STAGE_SOURCE_KINDS[stage.upper()]
    except KeyError as exc:
        raise ArtifactRefError(f"Unknown mechanism stage {stage!r}") from exc


def jobs_root() -> Path:
    """Root directory holding scheduler job dirs (ACP_RUN_ROOT or cwd).

    A stale ``ACP_RUN_ROOT`` pointing at a removed directory falls back to
    the cwd so handoff resolution survives leftover environment state.
    """
    env = os.environ.get("ACP_RUN_ROOT")
    if env:
        candidate = Path(env).expanduser()
        if candidate.is_dir():
            return candidate.resolve()
    return Path.cwd().resolve()


def resolve_source_job_work_dir(source_job_id: str, root: Path | None = None) -> Path:
    """Resolve a job id to its work directory.

    Order: a direct path (when *source_job_id* is an existing directory) →
    the scheduler SQLite store → a bounded scan of *root* for a matching
    ``job.json``.

    Raises:
        ArtifactRefError: When the job cannot be located.
    """
    direct = Path(source_job_id).expanduser()
    if direct.is_dir():
        return direct.resolve()

    base = (root or jobs_root()).resolve()
    db_path = base / JOBS_DB_FILENAME
    if db_path.is_file():
        try:
            with sqlite3.connect(str(db_path)) as conn:
                row = conn.execute(
                    "SELECT work_dir FROM jobs WHERE id=?",
                    (source_job_id,),
                ).fetchone()
            if row and row[0] and Path(str(row[0])).is_dir():
                return Path(str(row[0])).resolve()
        except sqlite3.Error as exc:
            logger.debug("jobs DB lookup failed for %s: %s", source_job_id, exc)

    for job_json in sorted(base.rglob("job.json")):
        if len(job_json.relative_to(base).parts) > 4:
            continue
        try:
            payload = json.loads(job_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and str(payload.get("id") or "") == source_job_id:
            return job_json.parent.resolve()
    raise ArtifactRefError(f"Cannot resolve source job {source_job_id!r} under {base}")


def validate_stage_artifact(
    *,
    source_job_id: str | None,
    relative_path: str,
    sha256: str | None,
    kind: str,
    stage: str,
    work_dir: Path | None = None,
) -> Path:
    """Validate one artifact reference and return the manifest path.

    Checks (plan §8): file exists inside the source job, sha256 matches,
    manifest type matches the expected kind, and the stage relation is the
    documented predecessor.
    """
    expected_kind = expected_source_kind(stage)
    if kind != expected_kind:
        raise ArtifactRefError(
            f"Stage {stage} requires a {expected_kind!r} artifact, got kind={kind!r}"
        )
    signature = _MANIFEST_SIGNATURES[kind]

    if work_dir is None:
        if not source_job_id:
            raise ArtifactRefError("Artifact reference needs source_job_id or work_dir")
        work_dir = resolve_source_job_work_dir(source_job_id)

    candidate = (work_dir / relative_path).resolve()
    try:
        candidate.relative_to(work_dir.resolve())
    except ValueError as exc:
        raise ArtifactRefError(
            f"Artifact path escapes the source job directory: {relative_path}"
        ) from exc
    if not candidate.is_file():
        raise ArtifactRefError(f"Source artifact not found: {candidate}")

    if sha256 and not sha256_matches(candidate, sha256):
        raise ArtifactRefError(f"sha256 mismatch for {candidate}: expected {sha256_label(sha256)}")

    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactRefError(f"Source artifact is not readable JSON: {candidate}") from exc
    if not isinstance(payload, dict):
        raise ArtifactRefError(f"Source artifact is not a JSON object: {candidate}")
    schema_ok = str(payload.get("schema_version") or "") == signature[0]
    workflow_ok = str(payload.get("workflow") or "") == signature[1]
    if not (schema_ok and workflow_ok):
        raise ArtifactRefError(
            f"Artifact {relative_path} is not a {kind} "
            f"(schema_version={payload.get('schema_version')!r}, "
            f"workflow={payload.get('workflow')!r})"
        )
    return candidate


def copy_handoff_payload(manifest_path: Path, target_dir: Path) -> Path:
    """Copy a manifest plus its referenced payload dirs into *target_dir*.

    Geometry references inside stage manifests are relative to the
    manifest's directory (§8); copying the known payload dirs preserves
    that structure so the downstream runner resolves them unchanged.
    Returns the copied manifest path.
    """
    source_dir = manifest_path.parent
    names = [manifest_path.name]
    for item in HANDOFF_PAYLOAD_DIRS:
        if (source_dir / item).is_dir():
            names.append(item)
    for extra in ("ensemble.xyz", "ensemble.csv", "path_profile.json"):
        if (source_dir / extra).is_file():
            names.append(extra)
    copy_tree_items(source_dir, target_dir, names=names)
    copied = target_dir / manifest_path.name
    if not copied.is_file():
        # The manifest itself is mandatory — a direct copy fallback guards
        # against a partial tree copy.
        copied.parent.mkdir(parents=True, exist_ok=True)
        copied.write_bytes(manifest_path.read_bytes())
    return copied


__all__ = [
    "HANDOFF_PAYLOAD_DIRS",
    "ArtifactRefError",
    "S1_MANIFEST_KIND",
    "S2_MANIFEST_KIND",
    "S3_MANIFEST_KIND",
    "STAGE_SOURCE_KINDS",
    "copy_handoff_payload",
    "expected_source_kind",
    "jobs_root",
    "resolve_source_job_work_dir",
    "validate_stage_artifact",
]
