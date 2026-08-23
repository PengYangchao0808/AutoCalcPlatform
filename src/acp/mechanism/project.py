# pyright: reportAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportExplicitAny=false, reportUnusedCallResult=false
"""Mechanism project model — links four stage jobs (Confsearch/PESsearch/Lowconfirm/Highconfirm).

Design doc: docs/ACP_Confsearch_Manual_Mechanism_Modification_Plan.md §9
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MechanismProjectStatus(str, Enum):
    """Project-level lifecycle states (design §9)."""

    CREATED = "created"
    S1_READY = "s1_ready"
    S2_READY = "s2_ready"
    S3_READY = "s3_ready"
    COMPLETED = "completed"
    BLOCKED = "blocked"

    @property
    def is_terminal(self) -> bool:
        return self in (MechanismProjectStatus.COMPLETED, MechanismProjectStatus.BLOCKED)


# Workflow → stage mapping for the four stage jobs.
_WORKFLOW_STAGE_MAP: dict[str, str] = {
    "Confsearch": "s1",
    "PESsearch": "s2",
    "Lowconfirm": "s3",
    "Highconfirm": "s4",
}

# Stage completion → next project status.
_STAGE_NEXT_STATUS: dict[str, MechanismProjectStatus] = {
    "s1": MechanismProjectStatus.S1_READY,
    "s2": MechanismProjectStatus.S2_READY,
    "s3": MechanismProjectStatus.S3_READY,
}


@dataclass
class MechanismProject:
    """Data class for a mechanism research project (design §9).

    Attributes:
        project_id: Unique identifier.
        name: Human-readable project name.
        reaction_definition_hash: Content hash of the locked reaction definition.
        charge: Molecular charge.
        multiplicity: Spin multiplicity.
        status: Current project-level status.
        stage_jobs: Mapping of stage key (s1/s2/s3/s4) to job id (or None).
        created_at: ISO-8601 creation timestamp.
        updated_at: ISO-8601 last-update timestamp.
    """

    project_id: str
    name: str
    reaction_definition_hash: str = ""
    charge: int = 0
    multiplicity: int = 1
    status: MechanismProjectStatus = MechanismProjectStatus.CREATED
    stage_jobs: dict[str, str | None] = field(
        default_factory=lambda: {"s1": None, "s2": None, "s3": None, "s4": None}
    )
    created_at: str = ""
    updated_at: str = ""


class MechanismProjectStore:
    """SQLite-backed CRUD + state-machine for :class:`MechanismProject`."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        from acp.scheduler.migrations import migrate

        migrate(self.db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # ── CRUD ────────────────────────────────────────────────────────────

    def create(
        self,
        name: str,
        reaction_definition_hash: str = "",
        charge: int = 0,
        multiplicity: int = 1,
    ) -> MechanismProject:
        """Create a new mechanism project."""
        now = _utc_now_iso()
        project = MechanismProject(
            project_id=str(uuid.uuid4()),
            name=name,
            reaction_definition_hash=reaction_definition_hash,
            charge=charge,
            multiplicity=multiplicity,
            status=MechanismProjectStatus.CREATED,
            stage_jobs={"s1": None, "s2": None, "s3": None, "s4": None},
            created_at=now,
            updated_at=now,
        )
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO mechanism_projects
                    (project_id, name, reaction_definition_hash, charge, multiplicity,
                     status, s1_job_id, s2_job_id, s3_job_id, s4_job_id,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project.project_id,
                    project.name,
                    project.reaction_definition_hash,
                    project.charge,
                    project.multiplicity,
                    project.status.value,
                    None,
                    None,
                    None,
                    None,
                    project.created_at,
                    project.updated_at,
                ),
            )
            conn.commit()
        return project

    def get(self, project_id: str) -> MechanismProject | None:
        """Retrieve a project by id."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM mechanism_projects WHERE project_id=?",
                (project_id,),
            ).fetchone()
        return _row_to_project(row) if row else None

    def list_all(self, limit: int = 200) -> list[MechanismProject]:
        """List all projects, newest first."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM mechanism_projects ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_project(r) for r in rows]

    def set_stage_job(self, project_id: str, stage: str, job_id: str) -> bool:
        """Bind a job to a stage. Returns False if project not found."""
        col = f"{stage}_job_id"
        if stage not in ("s1", "s2", "s3", "s4"):
            raise ValueError(f"Invalid stage: {stage}")
        now = _utc_now_iso()
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                f"UPDATE mechanism_projects SET {col}=?, updated_at=? WHERE project_id=?",
                (job_id, now, project_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    # ── State machine ───────────────────────────────────────────────────

    def advance_for_job(
        self,
        project_id: str,
        job_id: str,
        workflow: str,
        job_status: str,
    ) -> MechanismProject | None:
        """Advance project state when a stage job reaches a terminal state.

        Args:
            project_id: The mechanism project id.
            job_id: The job that transitioned.
            workflow: The job's workflow name.
            job_status: Terminal status value (``completed``, ``failed``, ``cancelled``).

        Returns:
            Updated project, or None if project not found or job doesn't belong.
        """
        stage = _WORKFLOW_STAGE_MAP.get(workflow)
        if stage is None:
            return None

        project = self.get(project_id)
        if project is None:
            return None

        # Verify job belongs to this project.
        if project.stage_jobs.get(stage) != job_id:
            logger.debug(
                "Job %s not bound to stage %s of project %s; skipping advance",
                job_id,
                stage,
                project_id,
            )
            return None

        if job_status == "completed":
            return self._advance_completed(project, stage)
        if job_status in ("failed", "cancelled"):
            return self._advance_blocked(project)
        return None

    def _advance_completed(
        self, project: MechanismProject, stage: str
    ) -> MechanismProject:
        """Transition project after a stage completes successfully."""
        if project.status.is_terminal:
            return project

        if stage == "s4":
            new_status = MechanismProjectStatus.COMPLETED
        else:
            new_status = _STAGE_NEXT_STATUS.get(
                stage, MechanismProjectStatus.CREATED
            )

        now = _utc_now_iso()
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE mechanism_projects SET status=?, updated_at=? WHERE project_id=?",
                (new_status.value, now, project.project_id),
            )
            conn.commit()
        project.status = new_status
        project.updated_at = now
        return project

    def _advance_blocked(self, project: MechanismProject) -> MechanismProject:
        """Transition project to BLOCKED (only if not already terminal)."""
        if project.status.is_terminal:
            return project

        now = _utc_now_iso()
        new_status = MechanismProjectStatus.BLOCKED
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE mechanism_projects SET status=?, updated_at=? WHERE project_id=?",
                (new_status.value, now, project.project_id),
            )
            conn.commit()
        project.status = new_status
        project.updated_at = now
        return project


def _row_to_project(row: sqlite3.Row) -> MechanismProject:
    return MechanismProject(
        project_id=row["project_id"],
        name=row["name"],
        reaction_definition_hash=row["reaction_definition_hash"],
        charge=row["charge"],
        multiplicity=row["multiplicity"],
        status=MechanismProjectStatus(row["status"]),
        stage_jobs={
            "s1": row["s1_job_id"],
            "s2": row["s2_job_id"],
            "s3": row["s3_job_id"],
            "s4": row["s4_job_id"],
        },
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


__all__ = [
    "MechanismProject",
    "MechanismProjectStatus",
    "MechanismProjectStore",
]
