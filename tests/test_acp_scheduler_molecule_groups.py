"""Tests for molecule groups, aliases, merge, and suggestions (T8)."""

from __future__ import annotations

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


def _db_row(client: TestClient, task_id: str) -> dict[str, Any]:
    db_path = client.app.state.job_manager.store.db_path
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    assert row is not None
    return dict(row)


def _db_molecule_keys(client: TestClient, project_id: str) -> list[str]:
    db_path = client.app.state.job_manager.store.db_path
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            "SELECT molecule_key FROM tasks WHERE project_id=? ORDER BY molecule_key",
            (project_id,),
        ).fetchall()
    return [r[0] for r in rows]


def _seed_molecules(client: TestClient, project_id: str) -> list[str]:
    body = _batch_create(
        client,
        [
            {
                "molecule_name": "BCB_ALLENE",
                "task_name": "opt1",
                "workflow": "fake",
                "input": {},
                "method": {},
            },
            {
                "molecule_name": "BCB-Allene",
                "task_name": "opt2",
                "workflow": "fake",
                "input": {},
                "method": {},
            },
            {
                "molecule_name": "BCB ALLENE",
                "task_name": "opt3",
                "workflow": "fake",
                "input": {},
                "method": {},
            },
            {
                "molecule_name": "EtOH",
                "task_name": "opt4",
                "workflow": "fake",
                "input": {},
                "method": {},
            },
            {
                "molecule_name": "abc",
                "task_name": "opt5",
                "workflow": "fake",
                "input": {},
                "method": {},
            },
            {
                "molecule_name": "ABC",
                "task_name": "opt6",
                "workflow": "fake",
                "input": {},
                "method": {},
            },
        ],
        project_id=project_id,
    )
    return [c["task_id"] for c in body["created"]]


# ── ① Alias resolve: hit and miss ─────────────────────────────────────


def test_resolve_molecule_key_alias_hit(client: TestClient) -> None:
    pid = _default_project_id(client)
    task_ids = _seed_molecules(client, pid)
    assert len(task_ids) == 6

    db_path = client.app.state.job_manager.store.db_path
    with sqlite3.connect(str(db_path)) as conn:
        from acp.scheduler.molecule_groups import resolve_molecule_key

        now = "2026-01-01T00:00:00+00:00"
        conn.execute(
            "INSERT INTO molecule_aliases (project_id, alias_key, group_key, created_at) "
            "VALUES (?, ?, ?, ?)",
            (pid, "BCB-Allene", "BCB_ALLENE", now),
        )
        conn.commit()

        result_hit = resolve_molecule_key(conn, pid, "BCB-Allene")
        assert result_hit == "BCB_ALLENE"

        result_miss = resolve_molecule_key(conn, pid, "UnknownMol")
        assert result_miss == "UnknownMol"


def test_resolve_molecule_key_no_alias(client: TestClient) -> None:
    pid = _default_project_id(client)
    db_path = client.app.state.job_manager.store.db_path
    with sqlite3.connect(str(db_path)) as conn:
        from acp.scheduler.molecule_groups import resolve_molecule_key

        result = resolve_molecule_key(conn, pid, "Ethanol")
        assert result == "Ethanol"


# ── ② Merge rewrites tasks.molecule_key AND task-view grouping merges ──


def test_merge_rewrites_task_keys_and_groups(client: TestClient) -> None:
    pid = _default_project_id(client)
    task_ids = _seed_molecules(client, pid)

    keys_before = _db_molecule_keys(client, pid)
    assert "BCB_ALLENE" in keys_before
    assert "BCB-Allene" in keys_before
    assert "BCB ALLENE" in keys_before

    r = client.post(
        f"/api/v2/projects/{pid}/molecule-groups/merge",
        json={
            "alias_keys": ["BCB-Allene", "BCB ALLENE"],
            "target_key": "BCB_ALLENE",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["updated"] == 2

    for tid in task_ids[:3]:
        row = _db_row(client, tid)
        assert row["molecule_key"] == "BCB_ALLENE"

    from acp.scheduler.task_views import (
        ArchivedFilter,
        GroupBy,
        TaskSort,
        TaskViewQuery,
        query_project_tasks,
    )
    from acp.scheduler.tasks import TaskIndex

    idx = TaskIndex(client.app.state.job_manager.store.db_path)
    q = TaskViewQuery(
        project_id=pid,
        group_by=GroupBy.molecule,
        sort=TaskSort.created_desc,
        archived=ArchivedFilter.exclude,
    )
    result = query_project_tasks(idx, q)
    groups = result["groups"]
    bcb_group = [g for g in groups if g["key"] == "BCB_ALLENE"]
    assert len(bcb_group) == 1
    assert bcb_group[0]["count"] == 3


# ── ③ Suggestions produce correct pairs, grouping UNCHANGED ───────────


def test_suggestions_casefold_and_separator(client: TestClient) -> None:
    pid = _default_project_id(client)
    _seed_molecules(client, pid)

    r = client.get(f"/api/v2/projects/{pid}/molecule-groups/suggestions")
    assert r.status_code == 200
    suggestions = r.json()

    has_casefold = any(s["reason"] == "casefold-equal" for s in suggestions)
    has_separator = any(s["reason"] == "separator-normalized-equal" for s in suggestions)
    assert has_casefold, f"Expected casefold-equal suggestion, got: {suggestions}"
    assert has_separator, f"Expected separator-normalized-equal suggestion, got: {suggestions}"

    keys = _db_molecule_keys(client, pid)
    assert "BCB_ALLENE" in keys
    assert "BCB-Allene" in keys
    assert "BCB ALLENE" in keys
    assert "EtOH" in keys
    assert "abc" in keys
    assert "ABC" in keys


# ── ④ Alias removal falls back to original key ─────────────────────────


def test_alias_removal_fallback(client: TestClient) -> None:
    from acp.scheduler.naming import molecule_group_key

    pid = _default_project_id(client)
    task_ids = _seed_molecules(client, pid)

    r = client.post(
        f"/api/v2/projects/{pid}/molecule-groups/merge",
        json={
            "alias_keys": ["BCB-Allene", "BCB ALLENE"],
            "target_key": "BCB_ALLENE",
        },
    )
    assert r.status_code == 200

    r = client.delete(f"/api/v2/projects/{pid}/molecule-groups/alias/BCB-Allene")
    assert r.status_code == 200
    assert r.json()["updated"] == 1

    row = _db_row(client, task_ids[1])
    assert row["molecule_key"] == molecule_group_key("BCB-Allene")
    assert row["molecule_key"] == "BCB-Allene"


# ── ⑤ Migration 015 idempotent ────────────────────────────────────────


def test_migration_015_idempotent(tmp_path: Path) -> None:
    from acp.scheduler.migrations import migrate

    db_path = tmp_path / "test.db"
    migrate(db_path)
    migrate(db_path)

    with sqlite3.connect(str(db_path)) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    assert "molecule_groups" in tables
    assert "molecule_aliases" in tables


# ── API: list molecule groups ───────────────────────────────────────────


def test_list_molecule_groups(client: TestClient) -> None:
    pid = _default_project_id(client)
    _seed_molecules(client, pid)

    r = client.get(f"/api/v2/projects/{pid}/molecule-groups")
    assert r.status_code == 200
    groups = r.json()
    assert isinstance(groups, list)

    keys = [g["group_key"] for g in groups]
    assert "BCB_ALLENE" in keys
    assert "BCB-Allene" in keys
    assert "BCB ALLENE" in keys
    assert "EtOH" in keys
    assert "abc" in keys
    assert "ABC" in keys


def test_list_molecule_groups_404(client: TestClient) -> None:
    r = client.get("/api/v2/projects/nonexistent/molecule-groups")
    assert r.status_code == 404


# ── API: merge ──────────────────────────────────────────────────────────


def test_merge_endpoint(client: TestClient) -> None:
    pid = _default_project_id(client)
    _seed_molecules(client, pid)

    r = client.post(
        f"/api/v2/projects/{pid}/molecule-groups/merge",
        json={
            "alias_keys": ["BCB-Allene", "BCB ALLENE"],
            "target_key": "BCB_ALLENE",
        },
    )
    assert r.status_code == 200
    assert r.json()["updated"] == 2

    db_path = client.app.state.job_manager.store.db_path
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            "SELECT molecule_key FROM tasks WHERE project_id=?",
            (pid,),
        ).fetchall()
    keys = {r[0] for r in rows}
    assert "BCB-Allene" not in keys
    assert "BCB ALLENE" not in keys
    assert "BCB_ALLENE" in keys


def test_merge_400_unknown_target(client: TestClient) -> None:
    pid = _default_project_id(client)
    _seed_molecules(client, pid)

    r = client.post(
        f"/api/v2/projects/{pid}/molecule-groups/merge",
        json={
            "alias_keys": ["BCB-Allene"],
            "target_key": "totally_unknown_key",
        },
    )
    assert r.status_code == 400


def test_merge_404_project(client: TestClient) -> None:
    r = client.post(
        "/api/v2/projects/nonexistent/molecule-groups/merge",
        json={"alias_keys": ["a"], "target_key": "b"},
    )
    assert r.status_code == 404


def test_merge_422_empty_alias_keys(client: TestClient) -> None:
    pid = _default_project_id(client)
    r = client.post(
        f"/api/v2/projects/{pid}/molecule-groups/merge",
        json={"alias_keys": [], "target_key": "x"},
    )
    assert r.status_code == 422


# ── API: suggestions ────────────────────────────────────────────────────


def test_suggestions_endpoint(client: TestClient) -> None:
    pid = _default_project_id(client)
    _seed_molecules(client, pid)

    r = client.get(f"/api/v2/projects/{pid}/molecule-groups/suggestions")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) >= 2

    reasons = [s["reason"] for s in data]
    assert "casefold-equal" in reasons
    assert "separator-normalized-equal" in reasons


def test_suggestions_empty_project(client: TestClient) -> None:
    pid = _default_project_id(client)
    r = client.get(f"/api/v2/projects/{pid}/molecule-groups/suggestions")
    assert r.status_code == 200
    assert r.json() == []


# ── API: delete alias ──────────────────────────────────────────────────


def test_delete_alias(client: TestClient) -> None:
    pid = _default_project_id(client)
    task_ids = _seed_molecules(client, pid)

    r = client.post(
        f"/api/v2/projects/{pid}/molecule-groups/merge",
        json={
            "alias_keys": ["BCB-Allene", "BCB ALLENE"],
            "target_key": "BCB_ALLENE",
        },
    )
    assert r.status_code == 200

    r = client.delete(f"/api/v2/projects/{pid}/molecule-groups/alias/BCB-Allene")
    assert r.status_code == 200
    assert r.json()["updated"] == 1

    from acp.scheduler.naming import molecule_group_key

    row = _db_row(client, task_ids[1])
    assert row["molecule_key"] == molecule_group_key("BCB-Allene")


def test_delete_alias_404(client: TestClient) -> None:
    pid = _default_project_id(client)
    r = client.delete(f"/api/v2/projects/{pid}/molecule-groups/alias/nonexistent")
    assert r.status_code == 404


def test_delete_alias_404_project(client: TestClient) -> None:
    r = client.delete("/api/v2/projects/nonexistent/molecule-groups/alias/x")
    assert r.status_code == 404


# ── Raw sqlite3 cross-checks (adversarial) ─────────────────────────────


def test_merge_raw_sqlite_cross_check(client: TestClient) -> None:
    pid = _default_project_id(client)
    _seed_molecules(client, pid)

    client.post(
        f"/api/v2/projects/{pid}/molecule-groups/merge",
        json={
            "alias_keys": ["BCB-Allene", "BCB ALLENE"],
            "target_key": "BCB_ALLENE",
        },
    )

    db_path = client.app.state.job_manager.store.db_path
    with sqlite3.connect(str(db_path)) as conn:
        cnt = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE project_id=? AND molecule_key=?",
            (pid, "BCB_ALLENE"),
        ).fetchone()[0]
    assert cnt == 3

    with sqlite3.connect(str(db_path)) as conn:
        alias_rows = conn.execute(
            "SELECT alias_key, group_key FROM molecule_aliases WHERE project_id=?",
            (pid,),
        ).fetchall()
    alias_map = {r[0]: r[1] for r in alias_rows}
    assert alias_map.get("BCB-Allene") == "BCB_ALLENE"
    assert alias_map.get("BCB ALLENE") == "BCB_ALLENE"


# ── Re-merge UPSERT: A,B→T then T,C→T2 chains correctly ──────────────


def test_re_merge_upsert_chains_aliases(client: TestClient) -> None:
    pid = _default_project_id(client)
    _batch_create(
        client,
        [
            {
                "molecule_name": "A",
                "task_name": "t1",
                "workflow": "fake",
                "input": {},
                "method": {},
            },
            {
                "molecule_name": "B",
                "task_name": "t2",
                "workflow": "fake",
                "input": {},
                "method": {},
            },
            {
                "molecule_name": "C",
                "task_name": "t3",
                "workflow": "fake",
                "input": {},
                "method": {},
            },
            {
                "molecule_name": "T",
                "task_name": "t4",
                "workflow": "fake",
                "input": {},
                "method": {},
            },
            {
                "molecule_name": "T2",
                "task_name": "t5",
                "workflow": "fake",
                "input": {},
                "method": {},
            },
        ],
        project_id=pid,
    )

    r = client.post(
        f"/api/v2/projects/{pid}/molecule-groups/merge",
        json={"alias_keys": ["A", "B"], "target_key": "T"},
    )
    assert r.status_code == 200
    assert r.json()["updated"] == 2

    r = client.post(
        f"/api/v2/projects/{pid}/molecule-groups/merge",
        json={"alias_keys": ["T", "C"], "target_key": "T2"},
    )
    assert r.status_code == 200
    assert r.json()["updated"] == 4

    db_path = client.app.state.job_manager.store.db_path
    with sqlite3.connect(str(db_path)) as conn:
        keys = [
            r[0]
            for r in conn.execute(
                "SELECT molecule_key FROM tasks WHERE project_id=? ORDER BY task_id",
                (pid,),
            ).fetchall()
        ]
    assert keys == ["T2", "T2", "T2", "T2", "T2"]

    with sqlite3.connect(str(db_path)) as conn:
        alias_rows = conn.execute(
            "SELECT alias_key, group_key FROM molecule_aliases WHERE project_id=?",
            (pid,),
        ).fetchall()
    alias_map = {r[0]: r[1] for r in alias_rows}
    assert alias_map["T"] == "T2"
    assert alias_map["C"] == "T2"
    assert alias_map["A"] == "T"
    assert alias_map["B"] == "T"


# ── Legacy alias preservation: casefolded alias survives refresh ──────


def test_legacy_casefolded_alias_preserved_after_refresh(tmp_path: Path) -> None:
    from acp.scheduler.migrations import migrate

    db_path = tmp_path / "test.db"
    migrate(db_path)

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO tasks "
            "(task_id, job_id, project_id, molecule_name, task_name, "
            "remark, display_name, workflow, task_dir_name, status, "
            "storage_mode, layout_version, created_at, updated_at, "
            "molecule_key, tags, archived) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 2, ?, ?, ?, ?, 0)",
            (
                "t1",
                "t1",
                "proj1",
                "BCB-Allene",
                "opt",
                "",
                "dir_t1",
                "Confsearch",
                "dir_t1",
                "completed",
                "local",
                "2026-01-01",
                "2026-01-01",
                "bcb_allene",
                "[]",
            ),
        )
        conn.execute(
            "INSERT INTO molecule_aliases "
            "(project_id, alias_key, group_key, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("proj1", "bcb-allene", "bcb_allene", "2026-01-01"),
        )
        conn.execute(
            "DELETE FROM _schema_migrations WHERE id='016'",
        )
        conn.commit()

    migrate(db_path)

    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT molecule_key FROM tasks WHERE task_id='t1'",
        ).fetchone()
    assert row[0] == "bcb_allene", f"Legacy merge key should survive, got {row[0]}"


# ── Mixed matrix: alias + case-preserved + no-alias ──────────────────


def test_legacy_refresh_mixed_matrix(tmp_path: Path) -> None:
    from acp.scheduler.migrations import migrate

    db_path = tmp_path / "test.db"
    migrate(db_path)

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO tasks "
            "(task_id, job_id, project_id, molecule_name, task_name, "
            "remark, display_name, workflow, task_dir_name, status, "
            "storage_mode, layout_version, created_at, updated_at, "
            "molecule_key, tags, archived) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 2, ?, ?, ?, ?, 0)",
            (
                "t_alias",
                "t_alias",
                "proj1",
                "BCB-Allene",
                "opt",
                "",
                "dir",
                "Confsearch",
                "dir",
                "completed",
                "local",
                "2026-01-01",
                "2026-01-01",
                "bcb_allene",
                "[]",
            ),
        )
        conn.execute(
            "INSERT INTO tasks "
            "(task_id, job_id, project_id, molecule_name, task_name, "
            "remark, display_name, workflow, task_dir_name, status, "
            "storage_mode, layout_version, created_at, updated_at, "
            "molecule_key, tags, archived) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 2, ?, ?, ?, ?, 0)",
            (
                "t_lower",
                "t_lower",
                "proj1",
                "EtOH",
                "opt",
                "",
                "dir",
                "Confsearch",
                "dir",
                "completed",
                "local",
                "2026-01-01",
                "2026-01-01",
                "etoh",
                "[]",
            ),
        )
        conn.execute(
            "INSERT INTO tasks "
            "(task_id, job_id, project_id, molecule_name, task_name, "
            "remark, display_name, workflow, task_dir_name, status, "
            "storage_mode, layout_version, created_at, updated_at, "
            "molecule_key, tags, archived) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 2, ?, ?, ?, ?, 0)",
            (
                "t_plain",
                "t_plain",
                "proj1",
                "MeOH",
                "opt",
                "",
                "dir",
                "Confsearch",
                "dir",
                "completed",
                "local",
                "2026-01-01",
                "2026-01-01",
                "MeOH",
                "[]",
            ),
        )
        conn.execute(
            "INSERT INTO molecule_aliases "
            "(project_id, alias_key, group_key, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("proj1", "bcb-allene", "bcb_allene", "2026-01-01"),
        )
        conn.execute(
            "DELETE FROM _schema_migrations WHERE id='016'",
        )
        conn.commit()

    migrate(db_path)

    with sqlite3.connect(str(db_path)) as conn:
        rows = {
            r[0]: r[1]
            for r in conn.execute(
                "SELECT task_id, molecule_key FROM tasks WHERE project_id='proj1' ORDER BY task_id",
            ).fetchall()
        }
    assert rows["t_alias"] == "bcb_allene", "alias-covered row keeps merge target"
    assert rows["t_lower"] == "EtOH", "all-lowercase legacy gets case-preserving key"
    assert rows["t_plain"] == "MeOH", "plain legacy row gets case-preserving key"

    migrate(db_path)

    with sqlite3.connect(str(db_path)) as conn:
        rows2 = {
            r[0]: r[1]
            for r in conn.execute(
                "SELECT task_id, molecule_key FROM tasks WHERE project_id='proj1' ORDER BY task_id",
            ).fetchall()
        }
    assert rows == rows2, "Idempotent: re-run produces identical results"


# ── Sticky aliases: new task gets alias target key ────────────────────


def test_sticky_alias_new_task_gets_target_key(client: TestClient) -> None:
    pid = _default_project_id(client)
    body = _batch_create(
        client,
        [
            {
                "molecule_name": "BCB-Allene",
                "task_name": "t1",
                "workflow": "fake",
                "input": {},
                "method": {},
            },
            {
                "molecule_name": "BCB_ALLENE",
                "task_name": "t2",
                "workflow": "fake",
                "input": {},
                "method": {},
            },
        ],
        project_id=pid,
    )
    assert len(body["created"]) == 2

    r = client.post(
        f"/api/v2/projects/{pid}/molecule-groups/merge",
        json={"alias_keys": ["BCB-Allene"], "target_key": "BCB_ALLENE"},
    )
    assert r.status_code == 200

    db_path = client.app.state.job_manager.store.db_path
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        idx_row = conn.execute(
            "SELECT * FROM tasks WHERE task_id=?",
            (body["created"][0]["task_id"],),
        ).fetchone()
    assert idx_row is not None

    from acp.scheduler.tasks import TaskIndex

    idx = TaskIndex(db_path)
    key = idx.compute_molecule_key(pid, "BCB-Allene")
    assert key == "BCB_ALLENE", f"Alias resolution should return target, got {key}"

    key_no_alias = idx.compute_molecule_key(pid, "EtOH")
    assert key_no_alias == "EtOH", "No alias returns case-preserving key"

    key_no_project = idx.compute_molecule_key(None, "BCB-Allene")
    assert key_no_project == "BCB-Allene", "No project returns bare molecule_group_key"
