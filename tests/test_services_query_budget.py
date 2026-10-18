"""B03-3 (todo 21) — fixed list/detail request budgets.

The v2 task-view list must be a **single bounded query** built from the resolved
project scope + filters + pagination:

* the list path issues a fixed number of SQL statements, O(1) in the returned
  row count — never one statement per row (no N+1 detail fetch);
* the authoritative enrichment path reads the prepared projection snapshot in
  memory and performs zero per-row detail reads; the retained compatibility
  branch is the only per-row reader and is hard-capped at
  ``_LIST_DETAIL_FETCH_BUDGET``;
* ``total``/``counts``/``facets`` keep whole-scope semantics and ``offset`` /
  ``next_offset`` pagination boundaries are preserved.

These tests also record the large-list pagination and large-project COUNT
measurements for the evidence file.
"""

from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


def _setup_index(tmp_path: Path, n: int, *, project_id: str = "p1") -> Any:
    from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
    from acp.scheduler.migrations import migrate
    from acp.scheduler.store import JobStore
    from acp.scheduler.tasks import TaskIndex

    db = tmp_path / "budget.db"
    migrate(db)
    store = JobStore(db)
    idx = TaskIndex(db)
    statuses = [JobStatus.RUNNING, JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.PAUSED]
    for i in range(n):
        spec = JobSpec(
            workflow="Confsearch",
            name=f"task{i}",
            molecule_name="CCO",
            task_name="x",
            remark="r",
            project_id=project_id,
            input={"molecule_name": "CCO"},
        )
        record = JobRecord(
            id=f"task{i:04d}",
            spec=spec,
            status=statuses[i % len(statuses)],
            work_dir=str(tmp_path / f"task{i:04d}"),
            project_id=project_id,
            attempt=1,
            revision=i,
        )
        store.create(record)
        idx.sync_from_job(record)
    return idx, db


def _reference_counts(db_path: Path, project_id: str) -> tuple[int, dict[str, int]]:
    """Independent SQL reference for the whole-scope total + status counts."""
    with sqlite3.connect(str(db_path)) as conn:
        total = conn.execute(
            "SELECT COUNT(DISTINCT t.task_id) FROM tasks t "
            "JOIN jobs jj ON jj.id = t.job_id "
            "WHERE t.project_id = ? AND t.archived = 0",
            (project_id,),
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT jj.status AS status, COUNT(DISTINCT t.task_id) FROM tasks t "
            "JOIN jobs jj ON jj.id = t.job_id "
            "WHERE t.project_id = ? AND t.archived = 0 "
            "GROUP BY jj.status",
            (project_id,),
        ).fetchall()
    return int(total), {str(row[0]): int(row[1]) for row in rows}


def _make_client(tmp_path: Path) -> TestClient:
    os.environ["ACP_RUN_ROOT"] = str(tmp_path)
    from acp.api.server import create_app

    return TestClient(create_app(run_root=tmp_path, max_running=2))


@pytest.fixture()
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    with _make_client(tmp_path) as c:
        yield c


def _seed_running_job(client: TestClient, *, job_id: str, work_dir: Path) -> None:
    from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus

    manager = client.app.state.job_manager
    work_dir.mkdir(parents=True, exist_ok=True)
    spec = JobSpec(
        workflow="Confsearch",
        name=job_id,
        molecule_name="CCO",
        task_name="opt",
        remark="r",
        project_id="",
        input={"source": "CCO"},
    )
    record = JobRecord(
        id=job_id,
        spec=spec,
        status=JobStatus.RUNNING,
        work_dir=str(work_dir),
        project_id="",
        attempt=1,
        revision=1,
    )
    manager.store.create(record)
    manager.tasks.sync_from_job(record)


def _count_enrich_snapshot(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    from acp.api import v1_routes

    calls = {"n": 0}
    real = v1_routes._enrich_job_snapshot

    def counting(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(v1_routes, "_enrich_job_snapshot", counting)
    return calls


# ---------------------------------------------------------------------------
# 1. SQL statement budget — O(1) in the page size
# ---------------------------------------------------------------------------


def test_list_sql_statement_count_is_bounded_and_row_independent(tmp_path: Path) -> None:
    from acp.scheduler.task_views import (
        LIST_SQL_BUDGET,
        GroupBy,
        TaskViewQuery,
        query_project_tasks,
    )

    idx, _ = _setup_index(tmp_path, 40)
    counts_by_page: dict[int, int] = {}
    for max_total in (1, 10, 40):
        result = query_project_tasks(
            idx, TaskViewQuery(project_id="p1", group_by=GroupBy.none, max_total=max_total)
        )
        statements = result["sql_statements"]
        assert isinstance(statements, int)
        assert statements <= LIST_SQL_BUDGET, (statements, LIST_SQL_BUDGET)
        counts_by_page[max_total] = statements
    assert len(set(counts_by_page.values())) == 1, counts_by_page


def test_budget_check_detects_injected_n_plus_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control: an injected per-row query must exceed the budget.

    If a future change reintroduces an N+1 detail fetch on the list path, the
    ``sql_statements <= LIST_SQL_BUDGET`` assertion must fail.  This injects the
    exact regression the budget guards against and proves the counter sees it.
    """
    from acp.scheduler import task_views

    idx, _ = _setup_index(tmp_path, 30)
    real_facets = task_views._build_facets

    def n_plus_1(conn: Any, q: Any, *, require_job_row: bool = False) -> Any:
        facets = real_facets(conn, q, require_job_row=require_job_row)
        page = conn.execute("SELECT task_id FROM tasks LIMIT ?", (30,)).fetchall()
        for row in page:
            conn.execute("SELECT * FROM jobs WHERE id = ?", (row["task_id"],))
        return facets

    monkeypatch.setattr(task_views, "_build_facets", n_plus_1)
    result = task_views.query_project_tasks(
        idx, task_views.TaskViewQuery(project_id="p1", group_by=task_views.GroupBy.none)
    )
    assert result["sql_statements"] > task_views.LIST_SQL_BUDGET, "budget must catch N+1"


def test_facets_off_costs_fewer_statements_but_still_bounded(tmp_path: Path) -> None:
    from acp.scheduler.task_views import LIST_SQL_BUDGET, TaskViewQuery, query_project_tasks

    idx, _ = _setup_index(tmp_path, 5)
    result = query_project_tasks(
        idx, TaskViewQuery(project_id="p1", include_facets=False, max_total=5)
    )
    assert result["facets"] == {}
    assert 0 < result["sql_statements"] <= LIST_SQL_BUDGET


# ---------------------------------------------------------------------------
# 2. counts / total semantics unchanged (reference SQL over the same data)
# ---------------------------------------------------------------------------


def test_counts_and_total_match_reference_and_are_page_independent(tmp_path: Path) -> None:
    from acp.scheduler.task_views import GroupBy, TaskViewQuery, query_project_tasks

    idx, db = _setup_index(tmp_path, 12)
    ref_total, ref_counts = _reference_counts(db, "p1")

    full = query_project_tasks(
        idx, TaskViewQuery(project_id="p1", group_by=GroupBy.none, max_total=12)
    )
    assert full["total"] == ref_total
    assert sum(full["counts"].values()) == full["total"]
    for status, expected in ref_counts.items():
        assert full["counts"].get(status, 0) == expected

    page = query_project_tasks(
        idx, TaskViewQuery(project_id="p1", group_by=GroupBy.none, max_total=3, offset=2)
    )
    assert page["total"] == full["total"] == ref_total
    assert page["counts"] == full["counts"]
    assert page["facets"] == full["facets"]


# ---------------------------------------------------------------------------
# 3. Pagination boundaries (offset / next_offset) preserved
# ---------------------------------------------------------------------------


def test_pagination_boundaries_partition_the_sorted_set(tmp_path: Path) -> None:
    from dataclasses import replace

    from acp.scheduler.task_views import GroupBy, TaskViewQuery, query_project_tasks

    idx, _ = _setup_index(tmp_path, 12)
    base = TaskViewQuery(project_id="p1", group_by=GroupBy.none)
    full = query_project_tasks(idx, base)
    expected = [row["id"] for row in full["groups"][0]["jobs"]]
    assert len(expected) == full["total"] == 12

    collected: list[str] = []
    for offset in range(0, full["total"], 5):
        result = query_project_tasks(idx, replace(base, max_total=5, offset=offset))
        assert result["total"] == full["total"]
        assert result["counts"] == full["counts"]
        assert result["offset"] == offset
        assert result["limit"] == 5
        page_rows = result["groups"][0]["jobs"]
        assert len(page_rows) <= 5
        collected.extend(row["id"] for row in page_rows)
        remaining = offset + len(page_rows) < full["total"]
        assert result["next_offset"] == (offset + 5 if remaining else None)
    assert collected == expected
    assert len(set(collected)) == len(collected)


def test_offset_past_end_is_empty_without_truncation(tmp_path: Path) -> None:
    from acp.scheduler.task_views import GroupBy, TaskViewQuery, query_project_tasks

    idx, _ = _setup_index(tmp_path, 4)
    result = query_project_tasks(
        idx, TaskViewQuery(project_id="p1", group_by=GroupBy.none, max_total=5, offset=99)
    )
    assert result["groups"][0]["jobs"] == []
    assert result["next_offset"] is None
    assert result["truncated"] is False
    assert result["total"] == 4


# ---------------------------------------------------------------------------
# 4. No per-row detail fetch on the authoritative list path
# ---------------------------------------------------------------------------


def test_projection_authoritative_list_performs_zero_detail_fetches(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from acp.api import v2_routes

    manager = client.app.state.job_manager
    for i in range(5):
        _seed_running_job(client, job_id=f"budget-{i}", work_dir=tmp_path / f"b{i}")

    store = v2_routes.install_task_projection(manager, auto_refresh=False)
    assert store.refresh([f"budget-{i}" for i in range(5)]) == 5
    monkeypatch.setattr(v2_routes, "_ENRICH_ACTIVE_ROWS_COMPAT", False)

    calls = _count_enrich_snapshot(monkeypatch)
    resp = client.get("/api/v2/task-view?group_by=none")
    assert resp.status_code == 200, resp.text
    rows = [job for group in resp.json()["groups"] for job in group["jobs"]]
    assert len(rows) == 5
    assert calls["n"] == 0, "authoritative list path must not read a per-row detail"


def test_list_request_runs_one_bounded_query(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from acp.api import v2_routes

    for i in range(3):
        _seed_running_job(client, job_id=f"one-q-{i}", work_dir=tmp_path / f"q{i}")
    v2_routes.install_task_projection(client.app.state.job_manager, auto_refresh=False)
    monkeypatch.setattr(v2_routes, "_ENRICH_ACTIVE_ROWS_COMPAT", False)

    from acp.scheduler import task_views

    calls = {"n": 0, "statements": []}
    real = task_views.query_project_tasks

    def counting(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        result = real(*args, **kwargs)
        calls["statements"].append(result["sql_statements"])
        return result

    monkeypatch.setattr(task_views, "query_project_tasks", counting)

    resp = client.get("/api/v2/task-view?group_by=none")
    assert resp.status_code == 200, resp.text
    assert calls["n"] == 1, "one list request == one list query"
    assert all(s <= task_views.LIST_SQL_BUDGET for s in calls["statements"])
    assert "sql_statements" not in resp.json(), "budget diagnostic must not leak to the wire"


# ---------------------------------------------------------------------------
# 5. Compatibility branch is hard-capped (never fans out per row)
# ---------------------------------------------------------------------------


def test_transitional_compat_detail_fetch_is_bounded(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from acp.api import v2_routes

    for i in range(10):
        _seed_running_job(client, job_id=f"cap-{i}", work_dir=tmp_path / f"c{i}")

    v2_routes.install_task_projection(client.app.state.job_manager, auto_refresh=False)
    monkeypatch.setattr(v2_routes, "_ENRICH_ACTIVE_ROWS_COMPAT", True)
    monkeypatch.setattr(v2_routes, "_LIST_DETAIL_FETCH_BUDGET", 3)

    calls = _count_enrich_snapshot(monkeypatch)
    resp = client.get("/api/v2/task-view?group_by=none")
    assert resp.status_code == 200, resp.text
    rows = [job for group in resp.json()["groups"] for job in group["jobs"]]
    assert len(rows) == 10
    assert calls["n"] <= 3, "compat branch must honour the detail-fetch budget"


# ---------------------------------------------------------------------------
# 6. Large-list pagination + large-project COUNT measurement (recorded)
# ---------------------------------------------------------------------------


def test_large_list_pagination_and_count_measurement(tmp_path: Path) -> None:
    from acp.scheduler.task_views import (
        LIST_SQL_BUDGET,
        GroupBy,
        TaskViewQuery,
        query_project_tasks,
    )

    idx, db = _setup_index(tmp_path, 300)

    start = time.perf_counter()
    page = query_project_tasks(
        idx, TaskViewQuery(project_id="p1", group_by=GroupBy.none, max_total=50, offset=200)
    )
    page_ms = (time.perf_counter() - start) * 1000.0

    start = time.perf_counter()
    total_result = query_project_tasks(
        idx,
        TaskViewQuery(
            project_id="p1",
            group_by=GroupBy.none,
            max_total=1,
            include_facets=False,
        ),
    )
    count_ms = (time.perf_counter() - start) * 1000.0

    assert page["sql_statements"] <= LIST_SQL_BUDGET
    assert count_ms < 5_000.0
    assert page["total"] == 300
    assert len(page["groups"][0]["jobs"]) == 50
    assert page["next_offset"] == 250

    ref_total, _ = _reference_counts(db, "p1")
    assert total_result["total"] == ref_total == 300

    # Recorded for the B03-3 evidence file: large-list page (offset 200, 50 rows)
    # and large-project whole-scope COUNT at n=300.  The todo-2 baseline has no
    # SQL-statement metric, so the SQL-count comparison is 未测 (method below).
    print(
        f"[B03-3 measurement] n=300 page_ms={page_ms:.2f} count_ms={count_ms:.2f} "
        f"page_statements={page['sql_statements']} budget={LIST_SQL_BUDGET}"
    )
