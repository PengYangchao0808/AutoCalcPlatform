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

# v2 manifests (bond_length_scan mode) are valid S2 handoff inputs (§12.3).
_S2_ACCEPTED_SCHEMAS: frozenset[str] = frozenset({"s2_path_v1", "s2_path_v2"})

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

# Editable-candidate artifacts (s2_candidate_v1): the sibling candidate
# manifest lives in RESULT/mechanism/, the materialized XYZ one level up in
# RESULT/structures/s2_candidates/ (geometry refs are RESULT-relative).
S2_CANDIDATE_MANIFEST_NAME = "s2_candidate_manifest.json"
S2_CANDIDATE_GEOMETRY_DIR = ("structures", "s2_candidates")

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
    accepted_schemas = _S2_ACCEPTED_SCHEMAS if kind == S2_MANIFEST_KIND else {signature[0]}
    schema_ok = str(payload.get("schema_version") or "") in accepted_schemas
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
    S2 handoffs additionally ship the sibling ``s2_candidate_manifest.json``
    and the materialized candidate structures
    (``RESULT/structures/s2_candidates/`` → ``target/structures/``) so the
    batch intake can load the user-confirmed candidates self-contained
    (batch plan §5).
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
    candidate_manifest = source_dir / "s2_candidate_manifest.json"
    if candidate_manifest.is_file():
        names.append(candidate_manifest.name)
    copy_tree_items(source_dir, target_dir, names=names)
    copied = target_dir / manifest_path.name
    if not copied.is_file():
        # The manifest itself is mandatory — a direct copy fallback guards
        # against a partial tree copy.
        copied.parent.mkdir(parents=True, exist_ok=True)
        copied.write_bytes(manifest_path.read_bytes())
    _copy_s2_candidate_structures(source_dir, target_dir)
    _copy_v2_scan_frames(manifest_path, source_dir, target_dir)
    return copied


def _copy_s2_candidate_structures(source_dir: Path, target_dir: Path) -> None:
    """Stage ``RESULT/structures/s2_candidates`` next to a copied manifest.

    The candidate manifest references geometries as
    ``structures/s2_candidates/<id>.xyz`` relative to the RESULT root; the
    manifest itself is copied into ``<target>/`` so the structures land at
    ``<target>/structures/s2_candidates/`` and the
    ``manifest.parent / ref`` probe resolves unchanged.
    """
    structures_root = (source_dir / ".." / "structures").resolve()
    candidates_dir = structures_root / "s2_candidates"
    if not candidates_dir.is_dir():
        return
    names = sorted(item.name for item in candidates_dir.iterdir() if item.is_file())
    copy_tree_items(candidates_dir, target_dir / "structures" / "s2_candidates", names=names)


def _copy_s2_candidates(source_dir: Path, target_dir: Path) -> None:
    """Stage the editable-candidate artifacts next to the copied manifest.

    ``s2_candidate_manifest.json`` sits beside the S2 manifest; the
    materialized candidate XYZ sit under ``RESULT/structures/s2_candidates``
    and are copied into the same RESULT-relative position inside the
    handoff target so downstream geometry refs resolve unchanged.
    """
    sibling = source_dir / S2_CANDIDATE_MANIFEST_NAME
    if sibling.is_file():
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / S2_CANDIDATE_MANIFEST_NAME).write_bytes(sibling.read_bytes())
    geometry_source = source_dir.parent.joinpath(*S2_CANDIDATE_GEOMETRY_DIR)
    if geometry_source.is_dir():
        geometry_names = sorted(item.name for item in geometry_source.iterdir() if item.is_file())
        copy_tree_items(
            geometry_source,
            target_dir.joinpath(*S2_CANDIDATE_GEOMETRY_DIR),
            names=geometry_names,
        )


def _copy_v2_scan_frames(manifest_path: Path, source_dir: Path, target_dir: Path) -> None:
    """Flatten a v2 manifest's ``scan_frames`` dir next to the copied manifest.

    v2 geometry refs (``scan_frames/frame_NNN.xyz``) are relative to the
    scan dir (``WORK/02_SEARCH/s2_bond_scan_001``), which lives outside
    ``RESULT/mechanism/``. Copying the frames into the handoff target makes
    the refs resolve identically for downstream S3 stages (§12.3).
    """
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(payload, dict) or payload.get("schema_version") != "s2_path_v2":
        return
    scan_dir_rel = str((payload.get("scan") or {}).get("scan_dir") or "")
    if not scan_dir_rel:
        return
    job_root = source_dir.parent.parent
    frames_source = (job_root / scan_dir_rel / "scan_frames").resolve()
    if not frames_source.is_dir():
        return
    names = sorted(item.name for item in frames_source.iterdir() if item.is_file())
    copy_tree_items(frames_source, target_dir / "scan_frames", names=names)


__all__ = [
    "HANDOFF_PAYLOAD_DIRS",
    "ArtifactRefError",
    "S1_MANIFEST_KIND",
    "S2_CANDIDATE_MANIFEST_NAME",
    "S2_MANIFEST_KIND",
    "S3_MANIFEST_KIND",
    "STAGE_SOURCE_KINDS",
    "copy_handoff_payload",
    "expected_source_kind",
    "jobs_root",
    "resolve_source_job_work_dir",
    "validate_stage_artifact",
]
