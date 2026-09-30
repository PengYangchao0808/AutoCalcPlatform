"""Tests for P2 tag registry + batch operations API (T7)."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


def _make_client(tmp_path: Path) -> TestClient:
    os.environ["ACP_RUN_ROOT"] = str(tmp_path)
    from acp.api.server import create_app

    return TestClient(create_app(run_root=tmp_path, max_running=2))


@pytest.fixture()
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    with _make_client(tmp_path) as c:
        yield c


def _default_project_id(client: TestClient) -> str:
    r = client.get("/api/v2/projects")
    assert r.status_code == 200
    for p in r.json():
        if p["name"] == "Uncategorized":
            return str(p["project_id"])
    raise AssertionError("default project missing")


def _batch_create(
    client: TestClient,
    tasks: list[dict[str, Any]],
    project_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"tasks": tasks}
    if project_id is not None:
        payload["project_id"] = project_id
    r = client.post("/api/v2/tasks/batch", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _create_tagged_tasks(client: TestClient, project_id: str) -> list[str]:
    """Create 4 tasks with mixed tags for testing. Returns task_ids."""
    body = _batch_create(
        client,
        [
            {
                "molecule_name": "ethanol",
                "task_name": "opt",
                "workflow": "fake",
                "input": {"source": "CCO"},
                "method": {"protocol": "ext"},
            },
            {
                "molecule_name": "methanol",
                "task_name": "freq",
                "workflow": "fake",
                "input": {"source": "CO"},
                "method": {"protocol": "ext"},
            },
            {
                "molecule_name": "ethanol",
                "task_name": "sp",
                "workflow": "fake",
                "input": {"source": "CCO"},
                "method": {"protocol": "ext"},
            },
            {
                "molecule_name": "propanol",
                "task_name": "opt",
                "workflow": "fake",
                "input": {"source": "CCCO"},
                "method": {"protocol": "ext"},
            },
        ],
        project_id=project_id,
    )
    task_ids = [c["task_id"] for c in body["created"]]
    assert len(task_ids) == 4

    # Tag task 0: ["alpha", "beta"]
    r = client.patch(f"/api/v2/tasks/{task_ids[0]}", json={"tags": ["alpha", "beta"]})
    assert r.status_code == 200
    # Tag task 1: ["alpha", "gamma"]
    r = client.patch(f"/api/v2/tasks/{task_ids[1]}", json={"tags": ["alpha", "gamma"]})
    assert r.status_code == 200
    # Tag task 2: ["beta"]
    r = client.patch(f"/api/v2/tasks/{task_ids[2]}", json={"tags": ["beta"]})
    assert r.status_code == 200
    # Tag task 3: ["alpha", "beta", "gamma"] — shared tag + unicode tag
    r = client.patch(
        f"/api/v2/tasks/{task_ids[3]}",
        json={"tags": ["alpha", "beta", "gamma", "标签"]},
    )
    assert r.status_code == 200

    return task_ids


def _db_tags(client: TestClient, task_id: str) -> list[str]:
    """Read raw tags JSON from DB for cross-check."""
    db_path = client.app.state.job_manager.store.db_path
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute("SELECT tags FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    assert row is not None
    return json.loads(row[0])


def _db_row(client: TestClient, task_id: str) -> dict[str, Any]:
    """Read full task row from DB."""
    db_path = client.app.state.job_manager.store.db_path
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    assert row is not None
    return dict(row)


# ── ① Tags aggregation counts shared tags correctly ──────────────────


def test_tags_aggregation_counts(client: TestClient) -> None:
    pid = _default_project_id(client)
    _create_tagged_tasks(client, pid)

    r = client.get(f"/api/v2/projects/{pid}/tags")
    assert r.status_code == 200
    tags = {item["tag"]: item["count"] for item in r.json()}

    assert tags["alpha"] == 3
    assert tags["beta"] == 3
    assert tags["gamma"] == 2
    assert tags["标签"] == 1


# ── ② Rename — old name gone, task count unchanged, updated correct ──


def test_tags_rename(client: TestClient) -> None:
    pid = _default_project_id(client)
    task_ids = _create_tagged_tasks(client, pid)

    r = client.post(
        f"/api/v2/projects/{pid}/tags/rename",
        json={"source": "alpha", "target": "renamed"},
    )
    assert r.status_code == 200
    assert r.json()["updated"] == 3

    # Old name gone from tag list
    r2 = client.get(f"/api/v2/projects/{pid}/tags")
    tag_names = {item["tag"] for item in r2.json()}
    assert "alpha" not in tag_names
    assert "renamed" in tag_names
    assert tag_names == {"renamed", "beta", "gamma", "标签"}

    # Task count unchanged
    r3 = client.get(f"/api/v2/task-view?project_id={pid}")
    assert r3.status_code == 200
    assert r3.json()["total"] == 4

    # Raw sqlite3 cross-check
    for tid in task_ids:
        tags = _db_tags(client, tid)
        assert "alpha" not in tags
        if tid == task_ids[0]:
            assert "renamed" in tags
        if tid == task_ids[1]:
            assert "renamed" in tags
        if tid == task_ids[3]:
            assert "renamed" in tags


# ── ③ Merge 3→1 ─────────────────────────────────────────────────────


def test_tags_merge(client: TestClient) -> None:
    pid = _default_project_id(client)
    task_ids = _create_tagged_tasks(client, pid)

    r = client.post(
        f"/api/v2/projects/{pid}/tags/merge",
        json={"sources": ["alpha", "beta", "gamma"], "target": "merged"},
    )
    assert r.status_code == 200
    assert r.json()["updated"] >= 1

    r2 = client.get(f"/api/v2/projects/{pid}/tags")
    tag_names = {item["tag"] for item in r2.json()}
    assert "alpha" not in tag_names
    assert "beta" not in tag_names
    assert "gamma" not in tag_names
    assert "merged" in tag_names
    assert "标签" in tag_names

    # Raw sqlite3 cross-check: task 0 should have ["merged"]
    tags0 = _db_tags(client, task_ids[0])
    assert tags0 == ["merged"]

    # Task 3 should have ["merged", "标签"]
    tags3 = _db_tags(client, task_ids[3])
    assert "merged" in tags3
    assert "标签" in tags3


# ── ④ Delete only unmarks (task rows intact) ─────────────────────────


def test_tags_delete_only_unmarks(client: TestClient) -> None:
    pid = _default_project_id(client)
    _create_tagged_tasks(client, pid)

    r = client.post(
        f"/api/v2/projects/{pid}/tags/delete",
        json={"tag": "beta"},
    )
    assert r.status_code == 200
    assert r.json()["updated"] == 3

    # Task count unchanged
    r2 = client.get(f"/api/v2/task-view?project_id={pid}")
    assert r2.json()["total"] == 4

    # beta gone from tag list
    r3 = client.get(f"/api/v2/projects/{pid}/tags")
    tag_names = {item["tag"] for item in r3.json()}
    assert "beta" not in tag_names

    # Raw sqlite3: all 4 task rows still exist
    db_path = client.app.state.job_manager.store.db_path
    with sqlite3.connect(str(db_path)) as conn:
        count = conn.execute("SELECT COUNT(*) FROM tasks WHERE project_id=?", (pid,)).fetchone()[0]
    assert count == 4


# ── ⑤ Batch add/remove tags idempotent ───────────────────────────────


def test_batch_add_tags_idempotent(client: TestClient) -> None:
    pid = _default_project_id(client)
    task_ids = _create_tagged_tasks(client, pid)
    tid = task_ids[0]

    # Add "alpha" again — already has it
    r = client.post(
        "/api/v2/tasks/batch-ops",
        json={
            "task_ids": [tid],
            "op": "add_tags",
            "payload": {"tags": ["alpha"]},
        },
    )
    assert r.status_code == 200
    result = r.json()
    assert result["updated"] == 0
    assert result["results"][0]["ok"] is True

    # Raw sqlite3: unchanged
    tags = _db_tags(client, tid)
    assert tags == ["alpha", "beta"]


def test_batch_remove_tags(client: TestClient) -> None:
    pid = _default_project_id(client)
    task_ids = _create_tagged_tasks(client, pid)
    tid = task_ids[3]

    r = client.post(
        "/api/v2/tasks/batch-ops",
        json={
            "task_ids": [tid],
            "op": "remove_tags",
            "payload": {"tags": ["alpha"]},
        },
    )
    assert r.status_code == 200
    assert r.json()["updated"] == 1

    tags = _db_tags(client, tid)
    assert "alpha" not in tags
    assert "beta" in tags
    assert "gamma" in tags
    assert "标签" in tags


# ── ⑥ Archive with active task → 400 + offending ids ─────────────────


def test_archive_active_task_returns_400(client: TestClient) -> None:
    pid = _default_project_id(client)
    task_ids = _create_tagged_tasks(client, pid)

    # All tasks are "queued" (active) — archive should be rejected
    r = client.post(
        "/api/v2/tasks/batch-ops",
        json={
            "task_ids": task_ids[:2],
            "op": "archive",
            "payload": {},
        },
    )
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "Cannot archive active tasks" in detail
    for tid in task_ids[:2]:
        assert tid in detail

    # Raw sqlite3: no task archived
    for tid in task_ids[:2]:
        row = _db_row(client, tid)
        assert row["archived"] == 0


def test_archive_terminal_succeeds(client: TestClient) -> None:
    pid = _default_project_id(client)
    task_ids = _create_tagged_tasks(client, pid)

    # Mark first two tasks as completed (terminal)
    manager = client.app.state.job_manager
    for tid in task_ids[:2]:
        rec = manager.store.get(tid)
        assert rec is not None
        from acp.scheduler.jobs import JobStatus

        rec.status = JobStatus.COMPLETED
        manager.store.update(rec)
        manager.tasks.update_status(tid, "completed")

    r = client.post(
        "/api/v2/tasks/batch-ops",
        json={
            "task_ids": task_ids[:2],
            "op": "archive",
            "payload": {},
        },
    )
    assert r.status_code == 200
    assert r.json()["updated"] == 2

    # Raw sqlite3: archived
    for tid in task_ids[:2]:
        row = _db_row(client, tid)
        assert row["archived"] == 1

    # Unarchived tasks still 0
    for tid in task_ids[2:]:
        row = _db_row(client, tid)
        assert row["archived"] == 0


# ── ⑦ set_molecule_name recomputes molecule_key ──────────────────────


def test_batch_set_molecule_name_recomputes_key(client: TestClient) -> None:
    pid = _default_project_id(client)
    task_ids = _create_tagged_tasks(client, pid)
    tid = task_ids[0]

    r = client.post(
        "/api/v2/tasks/batch-ops",
        json={
            "task_ids": [tid],
            "op": "set_molecule_name",
            "payload": {"molecule_name": "IsoButanol"},
        },
    )
    assert r.status_code == 200
    assert r.json()["updated"] == 1

    row = _db_row(client, tid)
    assert row["molecule_name"] == "IsoButanol"
    assert row["molecule_key"] == "IsoButanol"


# ── ⑧ Unknown task_id → ok:false not 500 ─────────────────────────────


def test_batch_unknown_task_id_returns_ok_false(client: TestClient) -> None:
    pid = _default_project_id(client)
    task_ids = _create_tagged_tasks(client, pid)

    r = client.post(
        "/api/v2/tasks/batch-ops",
        json={
            "task_ids": [task_ids[0], "nonexistent_id"],
            "op": "remove_tags",
            "payload": {"tags": ["alpha"]},
        },
    )
    assert r.status_code == 200
    results = r.json()["results"]
    assert len(results) == 2

    ok_result = next(r for r in results if r["task_id"] == task_ids[0])
    assert ok_result["ok"] is True

    bad_result = next(r for r in results if r["task_id"] == "nonexistent_id")
    assert bad_result["ok"] is False
    assert "not found" in bad_result["error"].lower()


# ── Edge: >500 task_ids → 422 ────────────────────────────────────────


def test_batch_ops_over_500_returns_422(client: TestClient) -> None:
    r = client.post(
        "/api/v2/tasks/batch-ops",
        json={
            "task_ids": [f"t{i}" for i in range(501)],
            "op": "archive",
            "payload": {},
        },
    )
    assert r.status_code == 422


# ── Edge: corrupt tags JSON → rewrite_tags treats as [] ──────────────


def test_corrupt_tags_json_treated_as_empty(client: TestClient) -> None:
    pid = _default_project_id(client)
    task_ids = _create_tagged_tasks(client, pid)
    tid = task_ids[0]

    db_path = client.app.state.job_manager.store.db_path
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE tasks SET tags=? WHERE task_id=?",
            ("not valid json{", tid),
        )
        conn.commit()

    # GET /tags should not 500 — corrupt rows treated as []
    r = client.get(f"/api/v2/projects/{pid}/tags")
    assert r.status_code == 200

    # batch add_tags should work — adds to []
    r2 = client.post(
        "/api/v2/tasks/batch-ops",
        json={
            "task_ids": [tid],
            "op": "add_tags",
            "payload": {"tags": ["recovered"]},
        },
    )
    assert r2.status_code == 200
    tags = _db_tags(client, tid)
    assert tags == ["recovered"]


# ── Edge: rename same source==target → 0 updated ─────────────────────


def test_rename_same_source_target_noop(client: TestClient) -> None:
    pid = _default_project_id(client)
    _create_tagged_tasks(client, pid)

    r = client.post(
        f"/api/v2/projects/{pid}/tags/rename",
        json={"source": "alpha", "target": "alpha"},
    )
    assert r.status_code == 200
    assert r.json()["updated"] == 0


# ── Edge: merge where sources contain target → 422 ───────────────────


def test_merge_sources_contain_target_returns_422(client: TestClient) -> None:
    pid = _default_project_id(client)
    _create_tagged_tasks(client, pid)

    r = client.post(
        f"/api/v2/projects/{pid}/tags/merge",
        json={"sources": ["alpha", "beta"], "target": "alpha"},
    )
    assert r.status_code == 422


# ── Edge: unknown project → 404 ──────────────────────────────────────


def test_tags_unknown_project_returns_404(client: TestClient) -> None:
    r = client.get("/api/v2/projects/nonexistent/tags")
    assert r.status_code == 404

    r2 = client.post(
        "/api/v2/projects/nonexistent/tags/rename",
        json={"source": "a", "target": "b"},
    )
    assert r2.status_code == 404


# ── Edge: batch archive with mix of terminal and active → 400 ─────────


def test_archive_mixed_terminal_active_rejects_all(client: TestClient) -> None:
    pid = _default_project_id(client)
    task_ids = _create_tagged_tasks(client, pid)

    manager = client.app.state.job_manager
    rec = manager.store.get(task_ids[0])
    assert rec is not None
    from acp.scheduler.jobs import JobStatus

    rec.status = JobStatus.COMPLETED
    manager.store.update(rec)
    manager.tasks.update_status(task_ids[0], "completed")

    r = client.post(
        "/api/v2/tasks/batch-ops",
        json={
            "task_ids": task_ids[:3],
            "op": "archive",
            "payload": {},
        },
    )
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert task_ids[1] in detail
    assert task_ids[2] in detail
    assert task_ids[0] not in detail

    # Raw sqlite3: NONE archived (no partial execution)
    for tid in task_ids[:3]:
        row = _db_row(client, tid)
        assert row["archived"] == 0


# ── Edge: batch add_tags with empty tags → 422 ───────────────────────


def test_batch_add_tags_empty_returns_422(client: TestClient) -> None:
    pid = _default_project_id(client)
    task_ids = _create_tagged_tasks(client, pid)

    r = client.post(
        "/api/v2/tasks/batch-ops",
        json={
            "task_ids": [task_ids[0]],
            "op": "add_tags",
            "payload": {"tags": ["", "valid"]},
        },
    )
    assert r.status_code == 200
    result = r.json()
    assert result["results"][0]["ok"] is False
    assert "empty" in result["results"][0]["error"].lower()
