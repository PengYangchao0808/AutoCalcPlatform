"""Tests for P3 task lineage browsing API (T12)."""

from __future__ import annotations

import hashlib
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
    client: TestClient, tasks: list[dict[str, Any]], project_id: str | None = None
) -> list[str]:
    payload: dict[str, Any] = {"tasks": tasks}
    if project_id is not None:
        payload["project_id"] = project_id
    r = client.post("/api/v2/tasks/batch", json=payload)
    assert r.status_code == 201, r.text
    return [c["task_id"] for c in r.json()["created"]]


def _db_hex_snapshot(client: TestClient) -> dict[str, str]:
    db_path = client.app.state.job_manager.store.db_path
    with sqlite3.connect(str(db_path)) as conn:
        jobs_blob = conn.execute("SELECT * FROM jobs").fetchall()
        tasks_blob = conn.execute("SELECT * FROM tasks").fetchall()
    return {
        "jobs": hashlib.sha256(str(jobs_blob).encode()).hexdigest(),
        "tasks": hashlib.sha256(str(tasks_blob).encode()).hexdigest(),
    }


def _rewrite_spec_json(client: TestClient, task_id: str, new_input: dict[str, Any]) -> None:
    db_path = client.app.state.job_manager.store.db_path
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute("SELECT spec_json FROM jobs WHERE id=?", (task_id,)).fetchone()
        assert row is not None
        spec = json.loads(row[0])
        spec["input"] = new_input
        conn.execute("UPDATE jobs SET spec_json=? WHERE id=?", (json.dumps(spec), task_id))
        conn.commit()


def _create_chain(client: TestClient, project_id: str) -> tuple[str, str, str]:
    task_ids = _batch_create(
        client,
        [
            {
                "molecule_name": "ethanol",
                "task_name": "confsearch",
                "workflow": "Confsearch",
                "input": {"source": "CCO"},
            },
            {
                "molecule_name": "ethanol",
                "task_name": "pes",
                "workflow": "PESsearch",
                "input": {"source": "CCO"},
            },
            {
                "molecule_name": "ethanol",
                "task_name": "batch",
                "workflow": "BatchOptimize",
                "input": {"source": "CCO"},
            },
        ],
        project_id=project_id,
    )
    a_id, b_id, c_id = task_ids[0], task_ids[1], task_ids[2]

    _rewrite_spec_json(
        client, b_id, {"source": {"source_type": "task_artifact", "source_job_id": a_id}}
    )
    _rewrite_spec_json(
        client, c_id, {"source": {"source_type": "task_artifact", "source_job_id": b_id}}
    )

    return a_id, b_id, c_id


# ── ① B upstream contains A; C upstream contains B and A ──────────────


def test_upstream_chain(client: TestClient) -> None:
    pid = _default_project_id(client)
    a_id, b_id, c_id = _create_chain(client, pid)

    r = client.get(f"/api/v2/tasks/{b_id}/lineage")
    assert r.status_code == 200
    body = r.json()
    assert body["task_id"] == b_id
    upstream_ids = [n["task_id"] for n in body["upstream"]]
    assert a_id in upstream_ids
    a_node = [n for n in body["upstream"] if n["task_id"] == a_id][0]
    assert a_node["depth"] == 1
    assert "source_job_id" in a_node["relation"]

    r = client.get(f"/api/v2/tasks/{c_id}/lineage")
    assert r.status_code == 200
    body = r.json()
    upstream_ids = [n["task_id"] for n in body["upstream"]]
    assert b_id in upstream_ids
    assert a_id in upstream_ids
    depths = {n["task_id"]: n["depth"] for n in body["upstream"]}
    assert depths[b_id] == 1
    assert depths[a_id] == 2


# ── ② A downstream contains B and C ──────────────────────────────────


def test_downstream_discovery(client: TestClient) -> None:
    pid = _default_project_id(client)
    a_id, b_id, c_id = _create_chain(client, pid)

    r = client.get(f"/api/v2/tasks/{a_id}/lineage")
    assert r.status_code == 200
    body = r.json()
    downstream_ids = [n["task_id"] for n in body["downstream"]]
    assert b_id in downstream_ids
    assert c_id not in downstream_ids

    r = client.get(f"/api/v2/tasks/{b_id}/lineage")
    assert r.status_code == 200
    body = r.json()
    downstream_ids = [n["task_id"] for n in body["downstream"]]
    assert c_id in downstream_ids


# ── ③ Self-cycle returns finite result ────────────────────────────────


def test_self_cycle_terminates(client: TestClient) -> None:
    pid = _default_project_id(client)
    task_ids = _batch_create(
        client,
        [
            {
                "molecule_name": "cyclic",
                "task_name": "opt",
                "workflow": "fake",
                "input": {"source": "X"},
            }
        ],
        project_id=pid,
    )
    task_id = task_ids[0]
    _rewrite_spec_json(
        client, task_id, {"source": {"source_type": "task_artifact", "source_job_id": task_id}}
    )

    r = client.get(f"/api/v2/tasks/{task_id}/lineage")
    assert r.status_code == 200
    body = r.json()
    assert body["upstream"] == []


# ── FIX 4a: corrupt row whose spec_json CONTAINS task_id as substring ──


def test_corrupt_in_downstream_like_path(client: TestClient) -> None:
    pid = _default_project_id(client)
    task_ids = _batch_create(
        client,
        [
            {
                "molecule_name": "ethanol",
                "task_name": "confsearch",
                "workflow": "Confsearch",
                "input": {"source": "CCO"},
            },
            {
                "molecule_name": "ethanol",
                "task_name": "pes",
                "workflow": "PESsearch",
                "input": {"source": "CCO"},
            },
        ],
        project_id=pid,
    )
    clean_id, corrupt_id = task_ids[0], task_ids[1]

    truncated = (
        '{"input": {"source": {"source_job_id": "'
        + clean_id
        + '"'
    )
    db_path = client.app.state.job_manager.store.db_path
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE jobs SET spec_json=? WHERE id=?",
            (truncated, corrupt_id),
        )
        conn.commit()

    r = client.get(f"/api/v2/tasks/{clean_id}/lineage")
    assert r.status_code == 200
    body = r.json()
    downstream_ids = [n["task_id"] for n in body["downstream"]]
    assert corrupt_id not in downstream_ids


# ── FIX 4b: upstream parent corrupt ───────────────────────────────────


def test_upstream_parent_corrupt(client: TestClient) -> None:
    pid = _default_project_id(client)
    task_ids = _batch_create(
        client,
        [
            {
                "molecule_name": "ethanol",
                "task_name": "confsearch",
                "workflow": "Confsearch",
                "input": {"source": "CCO"},
            },
            {
                "molecule_name": "ethanol",
                "task_name": "pes",
                "workflow": "PESsearch",
                "input": {"source": "CCO"},
            },
        ],
        project_id=pid,
    )
    parent_id, child_id = task_ids[0], task_ids[1]

    db_path = client.app.state.job_manager.store.db_path
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE jobs SET spec_json=? WHERE id=?",
            ("NOT_VALID_JSON{", parent_id),
        )
        conn.commit()

    _rewrite_spec_json(
        client,
        child_id,
        {"source": {"source_type": "task_artifact", "source_job_id": parent_id}},
    )

    r = client.get(f"/api/v2/tasks/{child_id}/lineage")
    assert r.status_code == 200
    body = r.json()
    upstream_ids = [n["task_id"] for n in body["upstream"]]
    assert parent_id not in upstream_ids


# ── FIX 4c: queried task itself corrupt → 200 empty ──────────────────


def test_self_corrupt_returns_empty(client: TestClient) -> None:
    pid = _default_project_id(client)
    task_ids = _batch_create(
        client,
        [
            {
                "molecule_name": "ethanol",
                "task_name": "pes",
                "workflow": "PESsearch",
                "input": {"source": "CCO"},
            }
        ],
        project_id=pid,
    )
    task_id = task_ids[0]

    db_path = client.app.state.job_manager.store.db_path
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE jobs SET spec_json=? WHERE id=?",
            ("NOT_VALID_JSON{", task_id),
        )
        conn.commit()

    r = client.get(f"/api/v2/tasks/{task_id}/lineage")
    assert r.status_code == 200
    body = r.json()
    assert body["task_id"] == task_id
    assert body["upstream"] == []
    assert body["downstream"] == []


# ── FIX 5: diamond-duplicate → A appears exactly once ─────────────────


def test_diamond_dedup(client: TestClient) -> None:
    pid = _default_project_id(client)
    task_ids = _batch_create(
        client,
        [
            {
                "molecule_name": "ethanol",
                "task_name": "confsearch",
                "workflow": "Confsearch",
                "input": {"source": "CCO"},
            },
            {
                "molecule_name": "ethanol",
                "task_name": "pes",
                "workflow": "PESsearch",
                "input": {"source": "CCO"},
            },
            {
                "molecule_name": "ethanol",
                "task_name": "batch",
                "workflow": "BatchOptimize",
                "input": {"source": "CCO"},
            },
            {
                "molecule_name": "ethanol",
                "task_name": "irc",
                "workflow": "irc",
                "input": {"source": "CCO"},
            },
        ],
        project_id=pid,
    )
    x_id, b_id, c_id, a_id = task_ids[0], task_ids[1], task_ids[2], task_ids[3]

    _rewrite_spec_json(
        client, b_id, {"source": {"source_type": "task_artifact", "source_job_id": a_id}}
    )
    _rewrite_spec_json(
        client, c_id, {"source": {"source_type": "structure_asset", "asset_id": a_id}}
    )
    _rewrite_spec_json(
        client, x_id, {
            "source": {"source_type": "task_artifact", "source_job_id": b_id},
            "from": {"source_job_id": c_id},
        }
    )

    r = client.get(f"/api/v2/tasks/{x_id}/lineage")
    assert r.status_code == 200
    body = r.json()
    upstream_ids = [n["task_id"] for n in body["upstream"]]
    assert b_id in upstream_ids
    assert c_id in upstream_ids
    assert a_id in upstream_ids
    assert upstream_ids.count(a_id) == 1


# ── FIX 6a: top-level input.source_job_id shape ──────────────────────


def test_top_level_source_job_id(client: TestClient) -> None:
    pid = _default_project_id(client)
    task_ids = _batch_create(
        client,
        [
            {
                "molecule_name": "ethanol",
                "task_name": "confsearch",
                "workflow": "Confsearch",
                "input": {"source": "CCO"},
            },
            {
                "molecule_name": "ethanol",
                "task_name": "pes",
                "workflow": "PESsearch",
                "input": {"source": "CCO"},
            },
        ],
        project_id=pid,
    )
    upstream_id, downstream_id = task_ids[0], task_ids[1]

    _rewrite_spec_json(
        client, downstream_id, {"source_job_id": upstream_id}
    )

    r = client.get(f"/api/v2/tasks/{downstream_id}/lineage")
    assert r.status_code == 200
    body = r.json()
    upstream_ids = [n["task_id"] for n in body["upstream"]]
    assert upstream_id in upstream_ids
    relations = [n["relation"] for n in body["upstream"]]
    assert any("input.source_job_id" in rel for rel in relations)


# ── FIX 6b: input.from.source_job_id shape ───────────────────────────


def test_from_dict_source_job_id(client: TestClient) -> None:
    pid = _default_project_id(client)
    task_ids = _batch_create(
        client,
        [
            {
                "molecule_name": "ethanol",
                "task_name": "confsearch",
                "workflow": "Confsearch",
                "input": {"source": "CCO"},
            },
            {
                "molecule_name": "ethanol",
                "task_name": "pes",
                "workflow": "PESsearch",
                "input": {"source": "CCO"},
            },
        ],
        project_id=pid,
    )
    upstream_id, downstream_id = task_ids[0], task_ids[1]

    _rewrite_spec_json(
        client, downstream_id, {"from": {"source_job_id": upstream_id}}
    )

    r = client.get(f"/api/v2/tasks/{downstream_id}/lineage")
    assert r.status_code == 200
    body = r.json()
    upstream_ids = [n["task_id"] for n in body["upstream"]]
    assert upstream_id in upstream_ids
    relations = [n["relation"] for n in body["upstream"]]
    assert any("input.from.source_job_id" in rel for rel in relations)


# ── ④ No-source task → empty lists, 200 ──────────────────────────────


def test_no_source_empty_lineage(client: TestClient) -> None:
    pid = _default_project_id(client)
    task_ids = _batch_create(
        client,
        [
            {
                "molecule_name": "solo",
                "task_name": "sp",
                "workflow": "fake",
                "input": {"source": "CCO"},
            }
        ],
        project_id=pid,
    )
    task_id = task_ids[0]

    r = client.get(f"/api/v2/tasks/{task_id}/lineage")
    assert r.status_code == 200
    body = r.json()
    assert body["upstream"] == []
    assert body["downstream"] == []


# ── ⑤ Unknown id → 404 ───────────────────────────────────────────────


def test_unknown_task_404(client: TestClient) -> None:
    r = client.get("/api/v2/tasks/nonexistent_id/lineage")
    assert r.status_code == 404


# ── ⑥ Corrupt spec_json row skipped ──────────────────────────────────


def test_corrupt_spec_json_skipped(client: TestClient) -> None:
    pid = _default_project_id(client)
    task_ids = _batch_create(
        client,
        [
            {
                "molecule_name": "ethanol",
                "task_name": "confsearch",
                "workflow": "Confsearch",
                "input": {"source": "CCO"},
            },
            {
                "molecule_name": "ethanol",
                "task_name": "pes",
                "workflow": "PESsearch",
                "input": {"source": "CCO"},
            },
        ],
        project_id=pid,
    )
    clean_id, corrupt_id = task_ids[0], task_ids[1]

    db_path = client.app.state.job_manager.store.db_path
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE jobs SET spec_json=? WHERE id=?",
            ("NOT_VALID_JSON{", corrupt_id),
        )
        conn.commit()

    r = client.get(f"/api/v2/tasks/{clean_id}/lineage")
    assert r.status_code == 200
    body = r.json()
    assert body["task_id"] == clean_id
    assert isinstance(body["upstream"], list)
    assert isinstance(body["downstream"], list)


# ── ⑦ Read-only: jobs/tasks tables byte-identical before/after ────────


def test_read_only_snapshot(client: TestClient) -> None:
    pid = _default_project_id(client)
    a_id, b_id, c_id = _create_chain(client, pid)

    snapshot_before = _db_hex_snapshot(client)

    client.get(f"/api/v2/tasks/{a_id}/lineage")
    client.get(f"/api/v2/tasks/{b_id}/lineage")
    client.get(f"/api/v2/tasks/{c_id}/lineage")

    snapshot_after = _db_hex_snapshot(client)
    assert snapshot_before["jobs"] == snapshot_after["jobs"]
    assert snapshot_before["tasks"] == snapshot_after["tasks"]


# ── ⑧ artifact_path relation is recorded ──────────────────────────────


def test_artifact_path_relation(client: TestClient) -> None:
    pid = _default_project_id(client)
    task_ids = _batch_create(
        client,
        [
            {
                "molecule_name": "ethanol",
                "task_name": "confsearch",
                "workflow": "Confsearch",
                "input": {"source": "CCO"},
            },
            {
                "molecule_name": "ethanol",
                "task_name": "pes",
                "workflow": "PESsearch",
                "input": {"source": "CCO"},
            },
        ],
        project_id=pid,
    )
    upstream_id, downstream_id = task_ids[0], task_ids[1]

    _rewrite_spec_json(
        client,
        downstream_id,
        {
            "source": {
                "source_type": "task_artifact",
                "source_job_id": upstream_id,
                "artifact_path": "RESULT/pes.json",
            }
        },
    )

    r = client.get(f"/api/v2/tasks/{downstream_id}/lineage")
    assert r.status_code == 200
    body = r.json()
    relations = [n["relation"] for n in body["upstream"]]
    assert any("source_job_id" in rel for rel in relations)
    assert not any("artifact_path" in rel for rel in relations)


# ── ⑨ asset_id relation is recorded ──────────────────────────────────


def test_asset_id_relation(client: TestClient) -> None:
    pid = _default_project_id(client)
    task_ids = _batch_create(
        client,
        [
            {
                "molecule_name": "ethanol",
                "task_name": "confsearch",
                "workflow": "Confsearch",
                "input": {"source": "CCO"},
            },
            {
                "molecule_name": "ethanol",
                "task_name": "pes",
                "workflow": "PESsearch",
                "input": {"source": "CCO"},
            },
        ],
        project_id=pid,
    )
    upstream_id, downstream_id = task_ids[0], task_ids[1]

    _rewrite_spec_json(
        client,
        downstream_id,
        {
            "source": {
                "source_type": "structure_asset",
                "asset_id": upstream_id,
                "asset_path": "structures/mol.xyz",
            }
        },
    )

    r = client.get(f"/api/v2/tasks/{downstream_id}/lineage")
    assert r.status_code == 200
    body = r.json()
    relations = [n["relation"] for n in body["upstream"]]
    assert any("asset_id" in rel for rel in relations)


# ── ⑩ Stale reference skip ───────────────────────────────────────────


def test_stale_reference_skipped(client: TestClient) -> None:
    pid = _default_project_id(client)
    task_ids = _batch_create(
        client,
        [
            {
                "molecule_name": "ethanol",
                "task_name": "pes",
                "workflow": "PESsearch",
                "input": {"source": "CCO"},
            }
        ],
        project_id=pid,
    )
    task_id = task_ids[0]
    _rewrite_spec_json(
        client,
        task_id,
        {"source": {"source_type": "task_artifact", "source_job_id": "nonexistent_upstream"}},
    )

    r = client.get(f"/api/v2/tasks/{task_id}/lineage")
    assert r.status_code == 200
    body = r.json()
    assert body["upstream"] == []
