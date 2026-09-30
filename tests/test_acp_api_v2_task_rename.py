"""Tests for task custom-name PATCH (Wave 2, plan §5.1 / §6.1 / §7)."""

from __future__ import annotations

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


def _create_task(
    client: TestClient,
    *,
    molecule_name: str = "ethanol",
    task_name: str = "opt",
    remark: str = "",
    workflow: str = "fake",
) -> dict[str, Any]:
    r = client.post(
        "/api/v2/tasks/batch",
        json={
            "tasks": [
                {
                    "molecule_name": molecule_name,
                    "task_name": task_name,
                    "remark": remark,
                    "workflow": workflow,
                    "input": {"source": "CCO"},
                    "method": {"protocol": "ext"},
                }
            ]
        },
    )
    assert r.status_code == 201, r.text
    created = r.json()["created"]
    assert len(created) == 1
    return created[0]


# ── Happy path ──────────────────────────────────────────────────────


def test_patch_custom_name_happy_path(client: TestClient) -> None:
    task = _create_task(client)
    tid = task["task_id"]

    r = client.patch(
        f"/api/v2/tasks/{tid}",
        json={"custom_name": "My Custom Task", "expected_name_revision": 0},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["custom_name"] == "My Custom Task"
    assert body["resolved_name"] == "My Custom Task"
    assert body["default_name"] != ""
    assert body["name_revision"] == 1
    assert body["name_updated_at"] is not None

    detail = client.get(f"/api/v2/tasks/{tid}")
    assert detail.status_code == 200
    assert detail.json()["custom_name"] == "My Custom Task"
    assert detail.json()["resolved_name"] == "My Custom Task"


def test_patch_custom_name_bumps_revision(client: TestClient) -> None:
    task = _create_task(client)
    tid = task["task_id"]

    r1 = client.patch(
        f"/api/v2/tasks/{tid}",
        json={"custom_name": "Name1", "expected_name_revision": 0},
    )
    assert r1.json()["name_revision"] == 1

    r2 = client.patch(
        f"/api/v2/tasks/{tid}",
        json={"custom_name": "Name2", "expected_name_revision": 1},
    )
    assert r2.json()["name_revision"] == 2


# ── Omitted vs explicit-null distinction ─────────────────────────────


def test_patch_custom_name_omitted_leaves_unchanged(client: TestClient) -> None:
    task = _create_task(client)
    tid = task["task_id"]

    client.patch(
        f"/api/v2/tasks/{tid}",
        json={"custom_name": "Original", "expected_name_revision": 0},
    )

    r = client.patch(f"/api/v2/tasks/{tid}", json={"remark": "updated"})
    assert r.status_code == 200
    assert r.json()["custom_name"] == "Original"
    assert r.json()["name_revision"] == 1


def test_patch_custom_name_null_restores_default(client: TestClient) -> None:
    task = _create_task(client)
    tid = task["task_id"]

    client.patch(
        f"/api/v2/tasks/{tid}",
        json={"custom_name": "Custom", "expected_name_revision": 0},
    )

    r = client.patch(
        f"/api/v2/tasks/{tid}",
        json={"custom_name": None, "expected_name_revision": 1},
    )
    assert r.status_code == 200
    assert r.json()["custom_name"] is None
    assert r.json()["resolved_name"] == r.json()["default_name"]
    assert r.json()["name_revision"] == 2


# ── Missing expected_name_revision ──────────────────────────────────


def test_patch_custom_name_without_revision_returns_422(client: TestClient) -> None:
    task = _create_task(client)
    tid = task["task_id"]

    r = client.patch(f"/api/v2/tasks/{tid}", json={"custom_name": "New Name"})
    assert r.status_code == 422


# ── Stale revision → 409 ───────────────────────────────────────────


def test_patch_custom_name_stale_revision_returns_409(client: TestClient) -> None:
    task = _create_task(client)
    tid = task["task_id"]

    client.patch(
        f"/api/v2/tasks/{tid}",
        json={"custom_name": "First", "expected_name_revision": 0},
    )

    r = client.patch(
        f"/api/v2/tasks/{tid}",
        json={"custom_name": "Second", "expected_name_revision": 0},
    )
    assert r.status_code == 409
    body = r.json()
    detail = body.get("detail", body)
    assert detail["error"] == "name_revision_conflict"
    assert "current" in detail
    assert detail["current"]["name_revision"] == 1


# ── 404 unknown task ────────────────────────────────────────────────


def test_patch_custom_name_unknown_task_returns_404(client: TestClient) -> None:
    r = client.patch(
        "/api/v2/tasks/nonexistent",
        json={"custom_name": "Name", "expected_name_revision": 0},
    )
    assert r.status_code == 404


# ── Invalid names → 422 ─────────────────────────────────────────────


def test_patch_custom_name_empty_string_returns_422(client: TestClient) -> None:
    task = _create_task(client)
    tid = task["task_id"]

    r = client.patch(
        f"/api/v2/tasks/{tid}",
        json={"custom_name": "", "expected_name_revision": 0},
    )
    assert r.status_code == 422


def test_patch_custom_name_too_long_returns_422(client: TestClient) -> None:
    task = _create_task(client)
    tid = task["task_id"]

    r = client.patch(
        f"/api/v2/tasks/{tid}",
        json={"custom_name": "x" * 201, "expected_name_revision": 0},
    )
    assert r.status_code == 422


def test_patch_custom_name_control_char_returns_422(client: TestClient) -> None:
    task = _create_task(client)
    tid = task["task_id"]

    r = client.patch(
        f"/api/v2/tasks/{tid}",
        json={"custom_name": "Task\nName", "expected_name_revision": 0},
    )
    assert r.status_code == 422


# ── No-op same value does not bump revision ─────────────────────────


def test_patch_custom_name_same_value_noop(client: TestClient) -> None:
    task = _create_task(client)
    tid = task["task_id"]

    client.patch(
        f"/api/v2/tasks/{tid}",
        json={"custom_name": "Custom", "expected_name_revision": 0},
    )

    r = client.patch(
        f"/api/v2/tasks/{tid}",
        json={"custom_name": "Custom", "expected_name_revision": 1},
    )
    assert r.status_code == 200
    assert r.json()["name_revision"] == 1


# ── Old fields still work (regression) ──────────────────────────────


def test_patch_old_fields_still_work(client: TestClient) -> None:
    task = _create_task(client, molecule_name="eth", task_name="sp", remark="old")
    tid = task["task_id"]

    r = client.patch(
        f"/api/v2/tasks/{tid}",
        json={"molecule_name": "meth", "task_name": "opt", "remark": "new"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["molecule_name"] == "meth"
    assert body["task_name"] == "opt"
    assert body["remark"] == "new"
    assert body["custom_name"] is None
    assert body["name_revision"] == 0


def test_patch_custom_name_then_old_fields(client: TestClient) -> None:
    task = _create_task(client)
    tid = task["task_id"]

    r = client.patch(
        f"/api/v2/tasks/{tid}",
        json={
            "custom_name": "Renamed",
            "expected_name_revision": 0,
            "remark": "updated-remark",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["custom_name"] == "Renamed"
    assert body["remark"] == "updated-remark"
    assert body["name_revision"] == 1


# ── task_views query returns name fields ─────────────────────────────


def test_task_view_returns_name_fields(client: TestClient) -> None:
    pid = _default_project_id(client)
    task = _create_task(client, molecule_name="ethanol", task_name="opt")
    tid = task["task_id"]

    client.patch(
        f"/api/v2/tasks/{tid}",
        json={"custom_name": "My Custom", "expected_name_revision": 0},
    )

    r = client.get(f"/api/v2/task-view?project_id={pid}")
    assert r.status_code == 200
    body = r.json()

    all_jobs: list[dict[str, Any]] = []
    for g in body["groups"]:
        all_jobs.extend(g["jobs"])

    target = [j for j in all_jobs if j["id"] == tid]
    assert len(target) == 1
    job = target[0]
    assert job["custom_name"] == "My Custom"
    assert job["resolved_name"] == "My Custom"
    assert job["name_revision"] == 1


# ── task search matches custom_name ─────────────────────────────────


def test_task_search_matches_custom_name(client: TestClient) -> None:
    pid = _default_project_id(client)
    task = _create_task(client, molecule_name="ethanol", task_name="opt")
    tid = task["task_id"]

    client.patch(
        f"/api/v2/tasks/{tid}",
        json={"custom_name": "SuperUniqueName", "expected_name_revision": 0},
    )

    r = client.get(f"/api/v2/task-view?project_id={pid}&search=SuperUniqueName")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1


# ── v1 job list/detail contains name fields ─────────────────────────


def test_v1_job_list_contains_name_fields(client: TestClient) -> None:
    task = _create_task(client, molecule_name="ethanol", task_name="opt")
    tid = task["task_id"]

    client.patch(
        f"/api/v2/tasks/{tid}",
        json={"custom_name": "V1 Custom", "expected_name_revision": 0},
    )

    r = client.get("/api/v1/jobs")
    assert r.status_code == 200
    jobs = r.json()["jobs"]
    target = [j for j in jobs if j["id"] == tid]
    assert len(target) == 1
    job = target[0]
    assert job["custom_name"] == "V1 Custom"
    assert job["resolved_name"] == "V1 Custom"
    assert job["name_revision"] == 1


def test_v1_job_detail_contains_name_fields(client: TestClient) -> None:
    task = _create_task(client, molecule_name="ethanol", task_name="opt")
    tid = task["task_id"]

    client.patch(
        f"/api/v2/tasks/{tid}",
        json={"custom_name": "Detail Custom", "expected_name_revision": 0},
    )

    r = client.get(f"/api/v1/jobs/{tid}/detail")
    assert r.status_code == 200
    job = r.json()["job"]
    assert job["custom_name"] == "Detail Custom"
    assert job["resolved_name"] == "Detail Custom"
    assert job["name_revision"] == 1


def test_v1_job_list_without_custom_name_has_defaults(client: TestClient) -> None:
    task = _create_task(client, molecule_name="ethanol", task_name="opt")
    tid = task["task_id"]

    r = client.get("/api/v1/jobs")
    assert r.status_code == 200
    jobs = r.json()["jobs"]
    target = [j for j in jobs if j["id"] == tid]
    assert len(target) == 1
    job = target[0]
    assert job["custom_name"] is None
    assert job["default_name"] != ""
    assert job["resolved_name"] == job["default_name"]
    assert job["name_revision"] == 0
    assert job["name_updated_at"] is None
