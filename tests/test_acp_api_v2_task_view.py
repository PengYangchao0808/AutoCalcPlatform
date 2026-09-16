"""Tests for the v2 task-view endpoint, PATCH metadata, and batch batch_id (T3)."""

from __future__ import annotations

import json
import os
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


def _create_task(
    client: TestClient,
    *,
    molecule_name: str = "ethanol",
    task_name: str = "opt",
    remark: str = "",
    workflow: str = "fake",
) -> dict[str, Any]:
    body = _batch_create(
        client,
        [{
            "molecule_name": molecule_name,
            "task_name": task_name,
            "remark": remark,
            "workflow": workflow,
            "input": {"source": "CCO"},
            "method": {"protocol": "ext"},
        }],
    )
    created = body["created"]
    assert len(created) == 1
    return created[0]


def _create_multi_tasks(client: TestClient, project_id: str) -> list[dict[str, Any]]:
    body = _batch_create(
        client,
        [
            {
                "molecule_name": "ethanol",
                "task_name": "opt",
                "remark": "final",
                "workflow": "fake",
                "input": {"source": "CCO"},
                "method": {"protocol": "ext"},
            },
            {
                "molecule_name": "methanol",
                "task_name": "freq",
                "remark": "vibrations",
                "workflow": "fake",
                "input": {"source": "CO"},
                "method": {"protocol": "ext"},
            },
            {
                "molecule_name": "ethanol",
                "task_name": "sp",
                "remark": "single point",
                "workflow": "fake",
                "input": {"source": "CCO"},
                "method": {"protocol": "ext"},
            },
        ],
        project_id=project_id,
    )
    return body["created"]


def _write_state_json(work_dir: Path, data: dict[str, Any]) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "state.json").write_text(json.dumps(data), encoding="utf-8")


# ── ① task-view happy path ─────────────────────────────────────────────


def test_task_view_default_molecule_grouping(client: TestClient) -> None:
    pid = _default_project_id(client)
    _create_multi_tasks(client, pid)

    r = client.get(f"/api/v2/task-view?project_id={pid}")
    assert r.status_code == 200
    body = r.json()

    assert body["total"] == 3
    assert len(body["groups"]) >= 1
    for g in body["groups"]:
        assert "key" in g
        assert "count" in g
        assert "jobs" in g

    molecule_keys = {g["key"] for g in body["groups"]}
    assert "ethanol" in molecule_keys or any("ethanol" in k for k in molecule_keys)

    counts = body["counts"]
    for status_key in ("queued", "running", "completed", "failed", "cancelled", "pending"):
        assert status_key in counts

    assert "facets" in body
    assert "statuses" in body["facets"]


def test_task_view_created_desc_order(client: TestClient) -> None:
    pid = _default_project_id(client)
    _create_multi_tasks(client, pid)

    r = client.get(f"/api/v2/task-view?project_id={pid}&sort=created_desc")
    assert r.status_code == 200
    body = r.json()
    all_jobs = []
    for g in body["groups"]:
        all_jobs.extend(g["jobs"])
    if len(all_jobs) >= 2:
        assert all_jobs[0]["created_at"] >= all_jobs[-1]["created_at"]


# ── ② invalid params → 422 ────────────────────────────────────────────


def test_task_view_invalid_group_by_returns_422(client: TestClient) -> None:
    r = client.get("/api/v2/task-view?group_by=invalid")
    assert r.status_code == 422


def test_task_view_invalid_sort_returns_422(client: TestClient) -> None:
    r = client.get("/api/v2/task-view?sort=invalid")
    assert r.status_code == 422


def test_task_view_invalid_archived_returns_422(client: TestClient) -> None:
    r = client.get("/api/v2/task-view?archived=invalid")
    assert r.status_code == 422


# ── ③ unknown project → 404 ───────────────────────────────────────────


def test_task_view_unknown_project_returns_404(client: TestClient) -> None:
    r = client.get("/api/v2/task-view?project_id=nonexistent")
    assert r.status_code == 404


def test_task_view_no_project_returns_all(client: TestClient) -> None:
    pid = _default_project_id(client)
    _create_multi_tasks(client, pid)
    r = client.get("/api/v2/task-view")
    assert r.status_code == 200
    assert r.json()["total"] == 3


# ── ④ PATCH happy path ────────────────────────────────────────────────


def test_patch_remark_changes_task_row(client: TestClient) -> None:
    pid = _default_project_id(client)
    task = _create_task(client, molecule_name="ethanol", task_name="opt", remark="old")
    task_id = task["task_id"]

    r = client.patch(f"/api/v2/tasks/{task_id}", json={"remark": "new-remark"})
    assert r.status_code == 200

    r2 = client.get(f"/api/v2/tasks/{task_id}")
    assert r2.status_code == 200

    r3 = client.get(f"/api/v2/task-view?project_id={pid}&group_by=remark")
    assert r3.status_code == 200
    remark_keys = {g["key"] for g in r3.json()["groups"]}
    assert "new-remark" in remark_keys
    assert "old" not in remark_keys


def test_patch_molecule_name_recomputes_molecule_key(client: TestClient) -> None:
    pid = _default_project_id(client)
    task = _create_task(client, molecule_name="Ethanol", task_name="opt")
    task_id = task["task_id"]

    r = client.patch(f"/api/v2/tasks/{task_id}", json={"molecule_name": "METHANOL"})
    assert r.status_code == 200

    r2 = client.get(f"/api/v2/task-view?project_id={pid}&group_by=molecule")
    assert r2.status_code == 200
    keys = {g["key"] for g in r2.json()["groups"]}
    assert "methanol" in keys


def test_patch_tags_replaces(client: TestClient) -> None:
    task = _create_task(client, molecule_name="ethanol", task_name="opt")
    task_id = task["task_id"]

    r = client.patch(f"/api/v2/tasks/{task_id}", json={"tags": ["alpha", "beta"]})
    assert r.status_code == 200

    r2 = client.patch(f"/api/v2/tasks/{task_id}", json={"tags": ["gamma"]})
    assert r2.status_code == 200

    r3 = client.get(f"/api/v2/tasks/{task_id}")
    assert r3.status_code == 200


# ── ⑤ PATCH failures ──────────────────────────────────────────────────


def test_patch_unknown_id_returns_404(client: TestClient) -> None:
    r = client.patch("/api/v2/tasks/nonexistent", json={"remark": "x"})
    assert r.status_code == 404


def test_patch_oversize_remark_returns_422(client: TestClient) -> None:
    task = _create_task(client)
    r = client.patch(
        f"/api/v2/tasks/{task['task_id']}",
        json={"remark": "x" * 201},
    )
    assert r.status_code == 422


def test_patch_empty_tag_returns_422(client: TestClient) -> None:
    task = _create_task(client)
    r = client.patch(
        f"/api/v2/tasks/{task['task_id']}",
        json={"tags": ["", "valid"]},
    )
    assert r.status_code == 422


def test_patch_too_many_tags_returns_422(client: TestClient) -> None:
    task = _create_task(client)
    r = client.patch(
        f"/api/v2/tasks/{task['task_id']}",
        json={"tags": [f"t{i}" for i in range(21)]},
    )
    assert r.status_code == 422


def test_patch_oversize_tag_returns_422(client: TestClient) -> None:
    task = _create_task(client)
    r = client.patch(
        f"/api/v2/tasks/{task['task_id']}",
        json={"tags": ["a" * 33]},
    )
    assert r.status_code == 422


# ── ⑥ batch shared batch_id ───────────────────────────────────────────


def test_batch_items_share_batch_id(client: TestClient) -> None:
    pid = _default_project_id(client)
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
                "task_name": "sp",
                "workflow": "fake",
                "input": {"source": "CO"},
                "method": {"protocol": "ext"},
            },
        ],
        project_id=pid,
    )
    created = body["created"]
    assert len(created) == 2

    task_ids = [c["task_id"] for c in created]
    r1 = client.get(f"/api/v2/tasks/{task_ids[0]}")
    r2 = client.get(f"/api/v2/tasks/{task_ids[1]}")
    assert r1.status_code == 200
    assert r2.status_code == 200

    tid1 = task_ids[0]
    tid2 = task_ids[1]
    assert tid1 != tid2

    r_view = client.get(f"/api/v2/task-view?project_id={pid}&group_by=batch")
    assert r_view.status_code == 200
    groups = r_view.json()["groups"]
    batch_groups = [g for g in groups if g["key"] != "__singles__"]
    assert len(batch_groups) >= 1
    assert batch_groups[0]["count"] == 2


def test_batch_with_existing_batch_id_not_overwritten(client: TestClient) -> None:
    pid = _default_project_id(client)
    body = _batch_create(
        client,
        [{
            "molecule_name": "ethanol",
            "task_name": "opt",
            "workflow": "fake",
            "input": {"source": "CCO"},
            "method": {"protocol": "ext"},
            "resources": {"batch_id": "custom_batch_123"},
        }],
        project_id=pid,
    )
    created = body["created"]
    assert len(created) == 1
    r = client.get(f"/api/v2/task-view?project_id={pid}&group_by=batch")
    assert r.status_code == 200
    for g in r.json()["groups"]:
        if g["key"] == "custom_batch_123":
            assert g["count"] == 1
            return
    raise AssertionError("custom batch_id not found in groups")


# ── ⑦ backward compat ─────────────────────────────────────────────────


def test_legacy_project_tasks_endpoint_unchanged(client: TestClient) -> None:
    pid = _default_project_id(client)
    _create_multi_tasks(client, pid)

    r = client.get(f"/api/v2/projects/{pid}/tasks")
    assert r.status_code == 200
    tasks = r.json()
    assert len(tasks) == 3
    for t in tasks:
        assert "task_id" in t
        assert "display_name" in t
        assert "molecule_name" in t
        assert "task_name" in t
        assert "workflow" in t
        assert "status" in t


# ── ⑧ active-row enrichment ───────────────────────────────────────────


def test_active_row_enrichment(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    pid = _default_project_id(client)
    task = _create_task(client, molecule_name="ethanol", task_name="opt")
    task_id = task["task_id"]

    r_detail = client.get(f"/api/v2/tasks/{task_id}")
    assert r_detail.status_code == 200
    work_dir = Path(r_detail.json()["work_dir"])

    _write_state_json(work_dir, {
        "stage_index": 2,
        "stage_total": 3,
        "stage_detail": "2/3 stages",
        "progress_state": "indeterminate",
        "current_stage": "compute",
    })

    from acp.api import v2_routes

    def _patched_enrich(request, rows):
        for row in rows:
            if row["id"] == task_id:
                row["stage_index"] = 2
                row["stage_total"] = 3
                row["stage_detail"] = "2/3 stages"
                row["progress_state"] = "indeterminate"
        return rows

    monkeypatch.setattr(v2_routes, "_enrich_active_rows", _patched_enrich)

    r = client.get(f"/api/v2/task-view?project_id={pid}")
    assert r.status_code == 200
    found = False
    for g in r.json()["groups"]:
        for j in g["jobs"]:
            if j["id"] == task_id:
                assert j.get("stage_index") == 2
                assert j.get("stage_total") == 3
                assert j.get("stage_detail") == "2/3 stages"
                found = True
    assert found, f"task {task_id} not found in task-view groups"


# ── edge: empty project ────────────────────────────────────────────────


def test_task_view_empty_project(client: TestClient) -> None:
    pid = _default_project_id(client)
    r = client.get(f"/api/v2/task-view?project_id={pid}")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["groups"] == []


# ── edge: group_limit truncation ───────────────────────────────────────


def test_task_view_group_limit_truncation(client: TestClient) -> None:
    pid = _default_project_id(client)
    _create_multi_tasks(client, pid)

    r = client.get(f"/api/v2/task-view?project_id={pid}&group_limit=1")
    assert r.status_code == 200
    body = r.json()
    assert body["truncated"] is True
    for g in body["groups"]:
        if g["count"] > 1:
            assert len(g["jobs"]) == 1
            assert g["truncated"] is True


# ── edge: comma-separated filter values ────────────────────────────────


def test_task_view_multi_status_filter(client: TestClient) -> None:
    pid = _default_project_id(client)
    _create_multi_tasks(client, pid)

    r = client.get(f"/api/v2/task-view?project_id={pid}&status=queued,running")
    assert r.status_code == 200
    body = r.json()
    for g in body["groups"]:
        for j in g["jobs"]:
            assert j["status"] in ("queued", "running")


# ── edge: PATCH then GET reflects in task-view ────────────────────────


def test_patch_reflected_in_task_view(client: TestClient) -> None:
    pid = _default_project_id(client)
    task = _create_task(client, molecule_name="ethanol", task_name="opt", remark="before")
    task_id = task["task_id"]

    client.patch(f"/api/v2/tasks/{task_id}", json={"remark": "after"})

    r = client.get(f"/api/v2/task-view?project_id={pid}&group_by=remark")
    assert r.status_code == 200
    remark_keys = {g["key"] for g in r.json()["groups"]}
    assert "after" in remark_keys
    assert "before" not in remark_keys
