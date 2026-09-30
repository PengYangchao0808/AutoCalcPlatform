"""Create-task draft persistence contract."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from acp.api.drafts import router


def test_draft_crud_and_revision_conflict(tmp_path: Path) -> None:
    app = FastAPI()
    app.state.db_path = str(tmp_path / "jobs.db")
    app.include_router(router, prefix="/api/v1")
    client = TestClient(app)
    snapshot = {"structures": [{"xyz": "1\nH\nH 0 0 0\n"}], "step": 2}

    created = client.post(
        "/api/v1/drafts",
        json={
            "name": "BS task",
            "project_id": "project-a",
            "workflow": "BatchOptimize",
            "snapshot": snapshot,
        },
    )
    assert created.status_code == 201
    draft = created.json()
    draft_id = draft["draft_id"]
    assert draft["structure_count"] == 1
    assert client.get("/api/v1/drafts").json()["items"][0]["draft_id"] == draft_id
    assert client.get(f"/api/v1/drafts/{draft_id}").json()["snapshot"] == snapshot

    changed = client.put(
        f"/api/v1/drafts/{draft_id}",
        json={
            "name": "BS task edited",
            "snapshot": snapshot,
            "expected_revision": draft["revision"],
        },
    )
    assert changed.status_code == 200
    assert changed.json()["revision"] == draft["revision"] + 1
    stale = client.put(
        f"/api/v1/drafts/{draft_id}",
        json={
            "name": "stale",
            "snapshot": snapshot,
            "expected_revision": draft["revision"],
        },
    )
    assert stale.status_code == 409
    assert client.delete(f"/api/v1/drafts/{draft_id}").status_code == 200
    assert client.get(f"/api/v1/drafts/{draft_id}").status_code == 404
