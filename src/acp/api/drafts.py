"""Persistent create-task drafts for the Workbench."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Body, HTTPException, Request

router = APIRouter()
_MAX_BYTES = 8 * 1024 * 1024


def _db(request: Request) -> Path:
    configured = getattr(request.app.state, "db_path", None)
    if configured:
        return Path(configured)
    return Path(request.app.state.job_manager.store.db_path)


@contextmanager
def _connect(request: Request):
    conn = sqlite3.connect(str(_db(request)), timeout=10)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute(
            """CREATE TABLE IF NOT EXISTS create_task_drafts (
                draft_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                project_id TEXT NOT NULL DEFAULT '',
                workflow TEXT NOT NULL DEFAULT '',
                structure_count INTEGER NOT NULL DEFAULT 0,
                snapshot_json TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        with conn:
            yield conn
    finally:
        conn.close()


def _summary(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "draft_id": row["draft_id"],
        "name": row["name"],
        "project_id": row["project_id"],
        "workflow": row["workflow"],
        "structure_count": row["structure_count"],
        "revision": row["revision"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


@router.get("/drafts")
def list_drafts(request: Request) -> dict[str, Any]:
    """List saved create-task drafts, newest first."""
    with _connect(request) as conn:
        rows = conn.execute(
            """SELECT draft_id,name,project_id,workflow,structure_count,revision,created_at,updated_at
               FROM create_task_drafts ORDER BY updated_at DESC"""
        ).fetchall()
    return {"items": [_summary(row) for row in rows]}


@router.post("/drafts", status_code=201)
def create_draft(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Create a durable draft snapshot."""
    return _save(request, str(uuid4()), body, create=True)


@router.get("/drafts/{draft_id}")
def get_draft(request: Request, draft_id: str) -> dict[str, Any]:
    """Return one snapshot and its metadata."""
    _valid_id(draft_id)
    with _connect(request) as conn:
        row = conn.execute(
            "SELECT * FROM create_task_drafts WHERE draft_id=?", (draft_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="draft_not_found")
    return {**_summary(row), "snapshot": json.loads(row["snapshot_json"])}


@router.put("/drafts/{draft_id}")
def update_draft(
    request: Request, draft_id: str, body: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    """Replace one snapshot, rejecting stale revisions."""
    _valid_id(draft_id)
    return _save(request, draft_id, body, create=False)


@router.delete("/drafts/{draft_id}")
def delete_draft(request: Request, draft_id: str) -> dict[str, bool]:
    """Delete one draft after submission or an explicit discard."""
    _valid_id(draft_id)
    with _connect(request) as conn:
        deleted = conn.execute(
            "DELETE FROM create_task_drafts WHERE draft_id=?", (draft_id,)
        ).rowcount
    if not deleted:
        raise HTTPException(status_code=404, detail="draft_not_found")
    return {"deleted": True}


def _valid_id(draft_id: str) -> None:
    try:
        UUID(draft_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="draft_not_found") from exc


def _save(request: Request, draft_id: str, body: dict[str, Any], *, create: bool) -> dict[str, Any]:
    snapshot = body.get("snapshot")
    if not isinstance(snapshot, dict):
        raise HTTPException(status_code=422, detail="snapshot_must_be_object")
    encoded = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > _MAX_BYTES:
        raise HTTPException(status_code=413, detail="draft_too_large")
    name = str(body.get("name") or "未命名草稿").strip()[:160]
    project_id = str(body.get("project_id") or "")[:128]
    workflow = str(body.get("workflow") or "")[:64]
    structures = snapshot.get("structures")
    count = len(structures) if isinstance(structures, list) else 0
    now = datetime.now(timezone.utc).isoformat()
    with _connect(request) as conn:
        if create:
            conn.execute(
                """INSERT INTO create_task_drafts
                   (draft_id,name,project_id,workflow,structure_count,snapshot_json,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (draft_id, name, project_id, workflow, count, encoded, now, now),
            )
        else:
            expected = body.get("expected_revision")
            if not isinstance(expected, int):
                raise HTTPException(status_code=422, detail="expected_revision_required")
            updated = conn.execute(
                """UPDATE create_task_drafts SET name=?,project_id=?,workflow=?,
                   structure_count=?,snapshot_json=?,revision=revision+1,updated_at=?
                   WHERE draft_id=? AND revision=?""",
                (name, project_id, workflow, count, encoded, now, draft_id, expected),
            ).rowcount
            if not updated:
                exists = conn.execute(
                    "SELECT 1 FROM create_task_drafts WHERE draft_id=?", (draft_id,)
                ).fetchone()
                raise HTTPException(
                    status_code=409 if exists else 404,
                    detail="revision_conflict" if exists else "draft_not_found",
                )
        row = conn.execute(
            "SELECT * FROM create_task_drafts WHERE draft_id=?", (draft_id,)
        ).fetchone()
    assert row is not None
    return _summary(row)


__all__ = ["router"]
