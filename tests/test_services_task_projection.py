"""B03-1 (todo 19) — background task projections; queries read prepared snapshots.

The v2 task view must read the prepared projection snapshot and never scan/parse
``state.json`` on the query path.  These tests pin that contract against the
legacy per-request enrichment:

* the prepared projection is field-for-field equivalent to the old enrichment;
* with the projection authoritative, a query performs zero synchronous scans and
  still shows the prepared enrichment;
* a cold start (no projection) shows the jobs-authoritative base facts only;
* a background refresh makes new enrichment visible on the next query;
* clearing the projection shows base facts again with no reverse-scan fallback;
* building/reading the projection never writes the jobs authority.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

_ENRICH_FIELDS = (
    "stage_index",
    "stage_total",
    "stage_detail",
    "progress_state",
    "display_method",
    "live_status",
)


def _make_client(tmp_path: Path) -> TestClient:
    os.environ["ACP_RUN_ROOT"] = str(tmp_path)
    from acp.api.server import create_app

    return TestClient(create_app(run_root=tmp_path, max_running=2))


@pytest.fixture()
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    with _make_client(tmp_path) as c:
        yield c


def _seed_running_job(
    client: TestClient,
    *,
    job_id: str,
    work_dir: Path,
    state: dict[str, Any] | None = None,
    revision: int = 0,
) -> Any:
    from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus

    manager = client.app.state.job_manager
    work_dir.mkdir(parents=True, exist_ok=True)
    if state is not None:
        (work_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
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
        revision=revision,
    )
    manager.store.create(record)
    manager.tasks.sync_from_job(record)
    return record


def _fake_request(manager: Any) -> Any:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(job_manager=manager)))


def _query_rows(client: TestClient) -> list[dict[str, Any]]:
    resp = client.get("/api/v2/task-view?group_by=none")
    assert resp.status_code == 200, resp.text
    return [job for group in resp.json()["groups"] for job in group["jobs"]]


def _row(rows: list[dict[str, Any]], job_id: str) -> dict[str, Any]:
    for row in rows:
        if row["id"] == job_id:
            return row
    raise AssertionError(f"job {job_id} not present in task view")


def _count_find_workflow_state(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    from acp.api import v1_routes

    calls = {"n": 0}
    real = v1_routes.find_workflow_state

    def counting(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(v1_routes, "find_workflow_state", counting)
    return calls


def _jobs_dump(manager: Any) -> list[tuple[Any, ...]]:
    with sqlite3.connect(str(manager.store.db_path)) as conn:
        return conn.execute(
            "SELECT id, status, revision, attempt, spec_json, result_json FROM jobs ORDER BY id"
        ).fetchall()


def test_projection_is_field_equivalent_to_legacy_enrichment(
    client: TestClient, tmp_path: Path
) -> None:
    from acp.api import v2_routes
    from acp.scheduler.task_views import GroupBy, TaskViewQuery, query_project_tasks

    manager = client.app.state.job_manager
    _seed_running_job(
        client,
        job_id="proj-equiv",
        work_dir=tmp_path / "equiv",
        state={
            "stage_index": 2,
            "stage_total": 5,
            "stage_detail": "probe",
            "progress_state": "determinate",
            "current_stage": "opt",
        },
        revision=7,
    )

    result = query_project_tasks(manager.tasks, TaskViewQuery(group_by=GroupBy.none))
    base_rows = [row for group in result["groups"] for row in group["jobs"]]
    legacy_rows = [dict(row) for row in base_rows]
    legacy_covered = v2_routes._enrich_active_rows(_fake_request(manager), legacy_rows)
    assert legacy_covered  # the legacy branch enriched the running row

    store = v2_routes.install_task_projection(manager, auto_refresh=False)
    assert store.refresh(["proj-equiv"]) == 1
    snapshot_rows = [dict(row) for row in base_rows]
    assert store.enrich_rows(snapshot_rows) == 1

    legacy = _row(legacy_rows, "proj-equiv")
    snapshot = _row(snapshot_rows, "proj-equiv")
    for field in _ENRICH_FIELDS:
        assert legacy.get(field) == snapshot.get(field), field
    assert snapshot["stage_index"] == 2
    assert snapshot["stage_total"] == 5
    assert snapshot["stage_detail"] == "probe"


def test_query_path_has_zero_synchronous_scans(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from acp.api import v2_routes

    manager = client.app.state.job_manager
    _seed_running_job(
        client,
        job_id="proj-zero",
        work_dir=tmp_path / "zero",
        state={"stage_index": 3, "stage_total": 9, "stage_detail": "z"},
        revision=1,
    )

    store = v2_routes.install_task_projection(manager, auto_refresh=False)
    assert store.refresh(["proj-zero"]) == 1
    monkeypatch.setattr(v2_routes, "_ENRICH_ACTIVE_ROWS_COMPAT", False)

    calls = _count_find_workflow_state(monkeypatch)
    row = _row(_query_rows(client), "proj-zero")

    assert row["stage_index"] == 3
    assert row["stage_total"] == 9
    assert calls["n"] == 0, "query path must not scan/parse state.json"


def test_cold_start_shows_base_facts_before_projection(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from acp.api import v2_routes

    _seed_running_job(
        client,
        job_id="proj-cold",
        work_dir=tmp_path / "cold",
        state={"stage_index": 4, "stage_total": 4},
    )
    v2_routes.install_task_projection(client.app.state.job_manager, auto_refresh=False)
    monkeypatch.setattr(v2_routes, "_ENRICH_ACTIVE_ROWS_COMPAT", False)

    calls = _count_find_workflow_state(monkeypatch)
    row = _row(_query_rows(client), "proj-cold")

    assert row["status"] == "running"
    assert row["molecule_name"] == "CCO"
    assert row["stage_index"] is None
    assert calls["n"] == 0


def test_projection_update_is_visible_after_background_refresh(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from acp.api import v2_routes

    manager = client.app.state.job_manager
    _seed_running_job(
        client,
        job_id="proj-late",
        work_dir=tmp_path / "late",
        state={"stage_index": 6, "stage_total": 7, "stage_detail": "late-update"},
    )
    store = v2_routes.install_task_projection(manager, auto_refresh=False)
    monkeypatch.setattr(v2_routes, "_ENRICH_ACTIVE_ROWS_COMPAT", False)

    before = _row(_query_rows(client), "proj-late")
    assert before["stage_index"] is None

    assert store.refresh(["proj-late"]) == 1

    after = _row(_query_rows(client), "proj-late")
    assert after["stage_index"] == 6
    assert after["stage_detail"] == "late-update"


def test_cleared_projection_shows_base_facts_without_fallback(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from acp.api import v2_routes

    manager = client.app.state.job_manager
    _seed_running_job(
        client,
        job_id="proj-drop",
        work_dir=tmp_path / "drop",
        state={"stage_index": 1, "stage_total": 2},
    )
    store = v2_routes.install_task_projection(manager, auto_refresh=False)
    assert store.refresh(["proj-drop"]) == 1
    monkeypatch.setattr(v2_routes, "_ENRICH_ACTIVE_ROWS_COMPAT", False)

    assert _row(_query_rows(client), "proj-drop")["stage_index"] == 1

    store.clear()
    calls = _count_find_workflow_state(monkeypatch)
    row = _row(_query_rows(client), "proj-drop")

    assert row["status"] == "running"
    assert row["stage_index"] is None
    assert calls["n"] == 0, "no reverse-scan fallback when the projection is gone"


def test_projection_never_writes_jobs(client: TestClient, tmp_path: Path) -> None:
    from acp.api import v2_routes

    manager = client.app.state.job_manager
    _seed_running_job(
        client,
        job_id="proj-ro",
        work_dir=tmp_path / "ro",
        state={"stage_index": 8, "stage_total": 8},
        revision=3,
    )
    before = _jobs_dump(manager)

    store = v2_routes.install_task_projection(manager, auto_refresh=False)
    store.refresh(["proj-ro"])
    _query_rows(client)
    store.invalidate_many(["proj-ro"])
    store.invalidate_all()

    assert _jobs_dump(manager) == before


def test_store_snapshot_is_read_only_copy_and_invalidation_hooks(tmp_path: Path) -> None:
    from acp_services.runtime.projections import (
        PROJECTION_FIELDS,
        TaskProjectionStore,
        TaskRowProjection,
    )

    class _Source:
        def collect(self, task_ids: list[str]) -> list[TaskRowProjection]:
            return [
                TaskRowProjection(task_id=tid, fields={"stage_index": 2, "ignored": 99})
                for tid in task_ids
            ]

    store = TaskProjectionStore(source=_Source(), auto_refresh=False)
    assert store.refresh(["a", "a", "b"]) == 2

    snap = store.snapshot()
    assert set(snap) == {"a", "b"}
    assert "ignored" not in snap["a"].fields
    assert set(snap["a"].fields) <= set(PROJECTION_FIELDS)

    rows = [{"id": "a"}, {"id": "missing"}]
    assert store.enrich_rows(rows) == 1
    assert rows[0]["stage_index"] == 2
    assert "stage_index" not in rows[1]

    store.invalidate("a")
    assert not store.covers("a")
    assert store.covers("b")


def test_request_refresh_is_noop_when_auto_refresh_disabled() -> None:
    from acp_services.runtime.projections import TaskProjectionStore, TaskRowProjection

    class _Source:
        def __init__(self) -> None:
            self.calls = 0

        def collect(self, task_ids: list[str]) -> list[TaskRowProjection]:
            self.calls += 1
            return [TaskRowProjection(task_id=tid, fields={"stage_index": 1}) for tid in task_ids]

    source = _Source()
    store = TaskProjectionStore(source=source, auto_refresh=False)
    store.request_refresh(["x"])
    assert source.calls == 0
    assert store.refresh(["x"]) == 1
    assert source.calls == 1


def test_projection_from_dict_filters_unknown_fields() -> None:
    from acp_services.runtime.projections import TaskRowProjection

    projection = TaskRowProjection.from_dict(
        {
            "task_id": "t",
            "fields": {"stage_index": 1, "bogus": "x"},
            "basis_revision": 4,
            "observed_at": "now",
        }
    )
    assert projection.fields == {"stage_index": 1}
    assert projection.basis_revision == 4
    assert projection.to_dict()["task_id"] == "t"


def test_status_projection_surfaces_attempt_release_facts(tmp_path: Path) -> None:
    from acp_services.entrypoints.status_projection import project_task_status

    work = tmp_path / "facts"
    frozen = work / "WORK" / "00_RUNTIME" / "frozen"
    frozen.mkdir(parents=True)
    (frozen / "envelope.json").write_text(
        json.dumps(
            {
                "release_id": "2c7ffd56045b5bd5",
                "release_digest": (
                    "b59ec6b6b502ed70104baacb17b41873769813def34224840017792e438900b3"
                ),
                "plan_digest": "v2:3825a8cc8e1a1b83f54e0661109f8c20",
                "submission_id": "sub_ecbe6eb39d6861a0",
            }
        ),
        encoding="utf-8",
    )
    (work / "execution_receipt.json").write_text(
        json.dumps({"release_id": "2c7ffd56045b5bd5", "state": "completed"}),
        encoding="utf-8",
    )

    projection = project_task_status(
        {
            "job_id": "job-facts",
            "status": "completed",
            "attempt": 1,
            "revision": 6,
            "work_dir": str(work),
            # Simulate a legacy HTTP row that predates these fields.
            "release_id": None,
            "release_digest": None,
            "plan_digest": None,
            "submission_id": None,
        }
    )

    assert projection["release_id"] == "2c7ffd56045b5bd5"
    assert projection["release_digest"].startswith("b59ec6b6")
    assert projection["plan_digest"] == "v2:3825a8cc8e1a1b83f54e0661109f8c20"
    assert projection["submission_id"] == "sub_ecbe6eb39d6861a0"
    assert projection["task_ref"]["release"] == {
        "release_id": "2c7ffd56045b5bd5",
        "release_digest": projection["release_digest"],
    }


def test_http_job_row_projects_release_facts(client: TestClient, tmp_path: Path) -> None:
    work = tmp_path / "http-facts"
    record = _seed_running_job(client, job_id="http-facts", work_dir=work)
    frozen = work / "WORK" / "00_RUNTIME" / "frozen"
    frozen.mkdir(parents=True)
    (frozen / "envelope.json").write_text(
        json.dumps(
            {
                "release_id": "2c7ffd56045b5bd5",
                "release_digest": (
                    "b59ec6b6b502ed70104baacb17b41873769813def34224840017792e438900b3"
                ),
                "plan_digest": "v2:3825a8cc8e1a1b83f54e0661109f8c20",
                "submission_id": "sub_ecbe6eb39d6861a0",
            }
        ),
        encoding="utf-8",
    )

    response = client.get(f"/api/v1/jobs/{record.id}")
    assert response.status_code == 200, response.text
    row = response.json()
    assert row["release_id"] == "2c7ffd56045b5bd5"
    assert row["release_digest"].startswith("b59ec6b6")
    assert row["plan_digest"] == "v2:3825a8cc8e1a1b83f54e0661109f8c20"
    assert row["submission_id"] == "sub_ecbe6eb39d6861a0"
