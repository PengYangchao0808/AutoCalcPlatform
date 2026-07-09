"""Project persistence for the ACP scheduler."""

from __future__ import annotations

import json
import shutil
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from acp.scheduler.migrations import migrate
from acp.scheduler.store import JobStore

_DEFAULT_PROJECT_ID = "uncategorized"
_DEFAULT_PROJECT_NAME = "Uncategorized"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProjectManager:
    """SQLite-backed CRUD manager for workbench projects."""

    def __init__(self, store: JobStore, run_root: Path):
        self.store = store
        self.run_root = Path(run_root)
        self.run_root.mkdir(parents=True, exist_ok=True)
        self.db_path = store.db_path
        self._lock = threading.Lock()
        migrate(self.db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def create_project(
        self,
        name: str,
        description: str = "",
        tags: list[str] | None = None,
        settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._create_with_id(
            str(uuid.uuid4()),
            name=name,
            description=description,
            tags=tags,
            settings=settings,
        )

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM projects WHERE project_id=?",
                (project_id,),
            ).fetchone()
        return _row_to_project(row) if row is not None else None

    def list_projects(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM projects ORDER BY updated_at DESC, created_at DESC"
            ).fetchall()
        return [_row_to_project(row) for row in rows]

    def update_project(self, project_id: str, **kwargs: Any) -> dict[str, Any] | None:
        project = self.get_project(project_id)
        if project is None:
            return None

        updates: dict[str, Any] = {}
        for field in ("name", "description", "tags", "settings"):
            if field in kwargs and kwargs[field] is not None:
                updates[field] = kwargs[field]
        if not updates:
            return project

        now = _utc_now_iso()
        merged = dict(project)
        merged.update(updates)

        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE projects
                SET name=?, description=?, tags=?, settings=?, updated_at=?
                WHERE project_id=?
                """,
                (
                    str(merged["name"]),
                    str(merged["description"]),
                    json.dumps(_normalize_tags(merged.get("tags")), ensure_ascii=False),
                    json.dumps(_normalize_settings(merged.get("settings")), ensure_ascii=False),
                    now,
                    project_id,
                ),
            )
            conn.commit()
        return self.get_project(project_id)

    def delete_project(self, project_id: str, delete_data: bool = False) -> bool:
        project = self.get_project(project_id)
        if project is None:
            return False

        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM projects WHERE project_id=?", (project_id,))
            conn.commit()

        if delete_data:
            shutil.rmtree(Path(project["run_root"]), ignore_errors=True)
        return True

    def ensure_default_project(self) -> str:
        project = self.get_project(_DEFAULT_PROJECT_ID)
        if project is not None:
            return str(project["project_id"])

        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM projects WHERE name=? ORDER BY created_at ASC LIMIT 1",
                (_DEFAULT_PROJECT_NAME,),
            ).fetchone()
        if row is not None:
            return str(row["project_id"])

        created = self._create_with_id(
            _DEFAULT_PROJECT_ID,
            name=_DEFAULT_PROJECT_NAME,
            description="",
            tags=[],
            settings={},
        )
        return str(created["project_id"])

    def _create_with_id(
        self,
        project_id: str,
        *,
        name: str,
        description: str,
        tags: list[str] | None,
        settings: dict[str, Any] | None,
    ) -> dict[str, Any]:
        now = _utc_now_iso()
        project_dir = (self.run_root / project_id).resolve()
        project_dir.mkdir(parents=True, exist_ok=True)

        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO projects (
                    project_id, name, description, tags, run_root, settings, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    name,
                    description,
                    json.dumps(_normalize_tags(tags), ensure_ascii=False),
                    str(project_dir),
                    json.dumps(_normalize_settings(settings), ensure_ascii=False),
                    now,
                    now,
                ),
            )
            conn.commit()

        project = self.get_project(project_id)
        if project is None:
            raise RuntimeError(f"Failed to create project {project_id}")
        return project


def _normalize_tags(tags: Any) -> list[str]:
    if tags is None:
        return []
    if isinstance(tags, list):
        return [str(tag) for tag in tags]
    return [str(tags)]


def _normalize_settings(settings: Any) -> dict[str, Any]:
    if settings is None:
        return {}
    if isinstance(settings, dict):
        return dict(settings)
    return {}


def _row_to_project(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "project_id": row["project_id"],
        "name": row["name"],
        "description": row["description"],
        "tags": json.loads(row["tags"]) if row["tags"] else [],
        "run_root": row["run_root"],
        "settings": json.loads(row["settings"]) if row["settings"] else {},
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


__all__ = ["ProjectManager"]
