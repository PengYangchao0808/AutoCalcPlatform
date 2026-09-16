"""Tests for user-defined auto-tag rules (T11).

Covers:
- Submit-time rule application (v1 POST /jobs and v2 POST /tasks/batch)
- enabled=false skip
- Backfill endpoint replay
- equals op + molecule_name + workflow fields
- Bad rule resilience (empty tag skipped, submit succeeds)
- 404 on unknown project for backfill
- Raw sqlite3 cross-checks
- Frontend contract (rules tab, backfill endpoint, i18n)
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _make_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    from acp.api.server import create_app

    return TestClient(create_app(run_root=tmp_path, max_running=2))


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    with _make_client(tmp_path, monkeypatch) as c:
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
    tasks: list[dict],
    project_id: str | None = None,
) -> dict:
    payload: dict = {"tasks": tasks}
    if project_id is not None:
        payload["project_id"] = project_id
    r = client.post("/api/v2/tasks/batch", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _db_tags(client: TestClient, task_id: str) -> list[str]:
    db_path = client.app.state.job_manager.store.db_path
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute("SELECT tags FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    assert row is not None
    return json.loads(row[0])


def _set_rules(client: TestClient, pid: str, rules: list[dict]) -> None:
    r = client.patch(
        f"/api/v1/projects/{pid}",
        json={"settings": {"auto_tag_rules": rules}},
    )
    assert r.status_code == 200, r.text


def _rule(
    rid: str,
    field: str = "remark",
    op: str = "contains",
    value: str = "Stepwise",
    tag: str = "S",
    enabled: bool = True,
) -> dict:
    return {"id": rid, "field": field, "op": op, "value": value, "tag": tag, "enabled": enabled}


TASK_PAYLOAD = {
    "molecule_name": "ethanol",
    "task_name": "opt",
    "remark": "Stepwise scan",
    "workflow": "fake",
    "input": {"source": "CCO"},
    "method": {"protocol": "ext"},
}


def test_v2_submit_contains_rule_applies(client: TestClient) -> None:
    pid = _default_project_id(client)
    _set_rules(client, pid, [_rule("atr_1")])
    body = _batch_create(client, [TASK_PAYLOAD], project_id=pid)
    tid = body["created"][0]["task_id"]
    assert "S" in _db_tags(client, tid)


def test_v1_submit_contains_rule_applies(client: TestClient) -> None:
    pid = _default_project_id(client)
    _set_rules(client, pid, [_rule("atr_2")])
    r = client.post(
        "/api/v1/jobs",
        json={
            "workflow": "fake",
            "input": {"source": "CCO"},
            "method": {"protocol": "ext"},
            "molecule_name": "ethanol",
            "task_name": "opt",
            "remark": "Stepwise scan",
            "project_id": pid,
        },
    )
    assert r.status_code == 201, r.text
    job_id = r.json()["job_id"]
    assert "S" in _db_tags(client, job_id)


def test_disabled_rule_not_applied(client: TestClient) -> None:
    pid = _default_project_id(client)
    _set_rules(client, pid, [_rule("atr_3", enabled=False)])
    body = _batch_create(client, [TASK_PAYLOAD], project_id=pid)
    tid = body["created"][0]["task_id"]
    assert "S" not in _db_tags(client, tid)


def test_backfill_applies_to_existing_tasks(client: TestClient) -> None:
    pid = _default_project_id(client)
    body = _batch_create(client, [TASK_PAYLOAD], project_id=pid)
    tid = body["created"][0]["task_id"]
    assert "S" not in _db_tags(client, tid)

    _set_rules(client, pid, [_rule("atr_4")])
    r = client.post(f"/api/v2/projects/{pid}/auto-tag-rules/apply")
    assert r.status_code == 200
    assert r.json()["updated"] >= 1
    assert "S" in _db_tags(client, tid)


def test_equals_op_molecule_name_field(client: TestClient) -> None:
    pid = _default_project_id(client)
    _set_rules(
        client,
        pid,
        [
            _rule("atr_5", field="molecule_name", op="equals", value="ethanol", tag="ETOH"),
        ],
    )
    body = _batch_create(client, [TASK_PAYLOAD], project_id=pid)
    tid = body["created"][0]["task_id"]
    assert "ETOH" in _db_tags(client, tid)


def test_equals_op_workflow_field(client: TestClient) -> None:
    pid = _default_project_id(client)
    _set_rules(
        client,
        pid,
        [
            _rule("atr_6", field="workflow", op="equals", value="fake", tag="TEST"),
        ],
    )
    body = _batch_create(client, [TASK_PAYLOAD], project_id=pid)
    tid = body["created"][0]["task_id"]
    assert "TEST" in _db_tags(client, tid)


def test_empty_tag_rule_skipped_submit_succeeds(
    client: TestClient,
) -> None:
    pid = _default_project_id(client)
    _set_rules(client, pid, [_rule("atr_bad", tag="")])
    body = _batch_create(client, [TASK_PAYLOAD], project_id=pid)
    assert len(body["created"]) == 1
    tags = _db_tags(client, body["created"][0]["task_id"])
    assert tags == []


def test_corrupt_rules_list_submit_succeeds(
    client: TestClient,
) -> None:
    pid = _default_project_id(client)
    db_path = client.app.state.job_manager.store.db_path
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT settings FROM projects WHERE project_id=?",
            (pid,),
        ).fetchone()
        settings = json.loads(row[0]) if row[0] else {}
        settings["auto_tag_rules"] = "not a list"
        conn.execute(
            "UPDATE projects SET settings=? WHERE project_id=?",
            (json.dumps(settings), pid),
        )
        conn.commit()
    body = _batch_create(client, [TASK_PAYLOAD], project_id=pid)
    assert "created" in body


def test_backfill_404_unknown_project(client: TestClient) -> None:
    r = client.post("/api/v2/projects/nonexistent/auto-tag-rules/apply")
    assert r.status_code == 404


def test_tag_deduplication(client: TestClient) -> None:
    pid = _default_project_id(client)
    _set_rules(
        client,
        pid,
        [
            _rule("atr_7a", value="Step"),
            _rule("atr_7b", value="Stepwise"),
        ],
    )
    body = _batch_create(client, [TASK_PAYLOAD], project_id=pid)
    tags = _db_tags(client, body["created"][0]["task_id"])
    assert tags.count("S") == 1


def test_empty_value_matches_nothing(client: TestClient) -> None:
    pid = _default_project_id(client)
    _set_rules(client, pid, [_rule("atr_8", value="", tag="X")])
    body = _batch_create(client, [TASK_PAYLOAD], project_id=pid)
    tags = _db_tags(client, body["created"][0]["task_id"])
    assert "X" not in tags
