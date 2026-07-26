"""Artifact persistence and filesystem capture helpers."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from acp.scheduler.migrations import migrate
from acp.scheduler.provenance import ParserStatus

_EXTENSION_TYPE_MAP = {
    ".xyz": "xyz",
    ".gjf": "gjf_input",
    ".log": "output_log",
    ".out": "output_log",
    ".chk": "checkpoint",
    ".rwf": "checkpoint",
    ".cube": "cube",
    ".sdf": "sdf",
    ".mol": "molfile",
    ".json": "json",
    ".csv": "csv",
    ".xlsx": "spreadsheet",
    ".txt": "text",
}
_IGNORED_SUFFIXES = {".tmp", ".pyc", ".pid"}
_IGNORED_NAMES = {"__pycache__", ".DS_Store", "Thumbs.db"}
_IGNORED_PATTERNS = {"core.", "slurm-", ".nfs"}
_MAX_ARTIFACT_SIZE = 500 * 1024 * 1024


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Artifact:
    artifact_id: str
    task_id: str | None
    job_id: str
    artifact_type: str
    file_path: str
    checksum: str | None
    size_bytes: int
    parser_status: str = ParserStatus.PENDING.value
    mime_type: str | None = None
    metadata: dict[str, Any] | None = None
    created_at: str = ""


class ArtifactRegistry:
    """Thread-safe SQLite registry for job artifacts."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        migrate(self.db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def register(self, artifact: Artifact) -> None:
        if not artifact.created_at:
            artifact.created_at = _utc_now_iso()
        if artifact.mime_type is None:
            artifact.mime_type = mimetypes.guess_type(artifact.file_path)[0]
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO artifacts (
                    artifact_id, task_id, job_id, artifact_type, file_path, checksum,
                    size_bytes, parser_status, mime_type, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _artifact_to_row(artifact),
            )
            conn.commit()

    def get(self, artifact_id: str) -> Artifact | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM artifacts WHERE artifact_id=?",
                (artifact_id,),
            ).fetchone()
        return _row_to_artifact(row) if row is not None else None

    def list_by_job(self, job_id: str) -> list[Artifact]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM artifacts WHERE job_id=? ORDER BY created_at ASC, rowid ASC",
                (job_id,),
            ).fetchall()
        return [_row_to_artifact(row) for row in rows]

    def list_by_task(self, task_id: str) -> list[Artifact]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM artifacts WHERE task_id=? ORDER BY created_at ASC, rowid ASC",
                (task_id,),
            ).fetchall()
        return [_row_to_artifact(row) for row in rows]

    def delete(self, artifact_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cursor = conn.execute("DELETE FROM artifacts WHERE artifact_id=?", (artifact_id,))
            conn.commit()
        return cursor.rowcount > 0

    def list_by_job_and_type(self, job_id: str, artifact_type: str) -> list[Artifact]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM artifacts
                WHERE job_id=? AND artifact_type=?
                ORDER BY created_at ASC, rowid ASC
                """,
                (job_id, artifact_type),
            ).fetchall()
        return [_row_to_artifact(row) for row in rows]


def compute_checksum(filepath: Path) -> str:
    """Return a SHA256 checksum string for a file."""
    digest = hashlib.sha256()
    with filepath.open("rb") as handle:
        while True:
            chunk = handle.read(8192)
            if not chunk:
                break
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def infer_artifact_type(filepath: Path) -> str:
    """Infer an artifact type from filename conventions and extension."""
    name = filepath.name.lower()
    stem = filepath.stem.lower()
    suffix = filepath.suffix.lower()
    # Hessian resolution sidecar (plan §7.5): ``<calc>.hessian.json``
    # captures the resolved Recalc_Hess policy for replay/audit.
    if stem.endswith(".hessian") and suffix == ".json":
        return "hessian_resolution"
    if suffix == ".xyz" and any(token in stem for token in ("ensemble", "optimized", "opt")):
        return "optimized_xyz"
    if suffix == ".xyz" and "crest" in name:
        return "crest_xyz"
    if suffix in {".out", ".log"} and "orca" in name:
        return "orca_output"
    if "shermo" in name:
        return "shermo_output"
    if name in {"xtbopt.xyz", "xtboptok"}:
        return "xtb_output"
    return _EXTENSION_TYPE_MAP.get(suffix, "file")


def capture_stage_artifacts(
    registry: ArtifactRegistry,
    job_id: str,
    task_id: str | None,
    work_dir: Path,
    stage_dir: Path,
    snapshot_before: set[str] | None = None,
) -> list[Artifact]:
    """Scan a stage directory, diff it against a prior snapshot, and register new files."""
    work_root = Path(work_dir)
    stage_root = Path(stage_dir)
    if not stage_root.exists():
        return []

    known_files = snapshot_before or set()
    discovered: list[Artifact] = []
    for path in sorted(stage_root.rglob("*")):
        if path.is_dir() or _is_ignored(path):
            continue
        try:
            relative = str(path.relative_to(work_root))
            stat = path.stat()
        except (OSError, ValueError):
            continue
        if snapshot_before is not None and relative in known_files:
            continue
        if stat.st_size > _MAX_ARTIFACT_SIZE:
            continue

        try:
            stage_relative = str(stage_root.relative_to(work_root))
        except ValueError:
            stage_relative = str(stage_root)

        artifact = Artifact(
            artifact_id=str(uuid.uuid4()),
            task_id=task_id,
            job_id=job_id,
            artifact_type=infer_artifact_type(path),
            file_path=relative,
            checksum=compute_checksum(path),
            size_bytes=stat.st_size,
            parser_status=ParserStatus.PENDING.value,
            mime_type=mimetypes.guess_type(path.name)[0],
            metadata={"stage_dir": stage_relative},
            created_at=_utc_now_iso(),
        )
        registry.register(artifact)
        discovered.append(artifact)
    return discovered


def _artifact_to_row(artifact: Artifact) -> tuple[Any, ...]:
    return (
        artifact.artifact_id,
        artifact.task_id,
        artifact.job_id,
        artifact.artifact_type,
        artifact.file_path,
        artifact.checksum,
        artifact.size_bytes,
        artifact.parser_status,
        artifact.mime_type,
        json.dumps(artifact.metadata) if artifact.metadata is not None else None,
        artifact.created_at,
    )


def _row_to_artifact(row: sqlite3.Row) -> Artifact:
    return Artifact(
        artifact_id=row["artifact_id"],
        task_id=row["task_id"],
        job_id=row["job_id"],
        artifact_type=row["artifact_type"],
        file_path=row["file_path"],
        checksum=row["checksum"],
        size_bytes=row["size_bytes"],
        parser_status=row["parser_status"],
        mime_type=row["mime_type"],
        metadata=json.loads(row["metadata_json"]) if row["metadata_json"] else None,
        created_at=row["created_at"],
    )


def _is_ignored(path: Path) -> bool:
    if any(part in _IGNORED_NAMES for part in path.parts):
        return True
    if path.suffix.lower() in _IGNORED_SUFFIXES:
        return True
    name = path.name
    return any(name.startswith(pattern) for pattern in _IGNORED_PATTERNS)


__all__ = [
    "Artifact",
    "ArtifactRegistry",
    "ParserStatus",
    "capture_stage_artifacts",
    "compute_checksum",
    "infer_artifact_type",
]
