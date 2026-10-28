"""B03-2 (todo 20) — availability / freshness / observed_at + independent revisions.

The projection and the v2 task view must expose three orthogonal semantics that
did not exist before (review P1-8):

* **availability** — is the resource prepared (ready / preparing / unavailable);
* **freshness + basis_revision** — has the projection kept up, compared per owner
  dimension (job / attempt / name / catalog / organization) **independently**;
* **observed_at** — a diagnostic timestamp that never decides new/old.

Combined acceptance cases pinned here:

* resource ready but projection stale -> base facts still shown, marked stale;
* no projection but jobs facts visible -> base facts + unavailable/unknown;
* old attempt projection late -> discarded, not overwriting newer facts;
* clock skew but correct revision -> still fresh.

Plus a monotonicity negative (a forged earlier ``observed_at`` is rejected) and
the independent-owner-revision rule (no cross-owner substitution).
"""

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


def _seed_running_job(
    client: TestClient,
    *,
    job_id: str,
    work_dir: Path,
    state: dict[str, Any] | None = None,
    revision: int = 0,
    attempt: int = 1,
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
        attempt=attempt,
        revision=revision,
    )
    manager.store.create(record)
    manager.tasks.sync_from_job(record)
    return record


def _bump_jobs_revision(manager: Any, job_id: str, revision: int) -> None:
    with sqlite3.connect(str(manager.store.db_path)) as conn:
        conn.execute("UPDATE jobs SET revision=? WHERE id=?", (revision, job_id))
        conn.commit()


def _query_rows(client: TestClient) -> list[dict[str, Any]]:
    resp = client.get("/api/v2/task-view?group_by=none")
    assert resp.status_code == 200, resp.text
    return [job for group in resp.json()["groups"] for job in group["jobs"]]


def _row(rows: list[dict[str, Any]], job_id: str) -> dict[str, Any]:
    for row in rows:
        if row["id"] == job_id:
            return row
    raise AssertionError(f"job {job_id} not present in task view")


def _jobs_dump(manager: Any) -> list[tuple[Any, ...]]:
    with sqlite3.connect(str(manager.store.db_path)) as conn:
        return conn.execute(
            "SELECT id, status, revision, attempt, spec_json, result_json FROM jobs ORDER BY id"
        ).fetchall()


class _Source:
    """Deterministic projection source for store-level tests."""

    def __init__(self, projections: list[Any]) -> None:
        self._projections = projections

    def collect(self, task_ids: list[str]) -> list[Any]:
        wanted = set(task_ids)
        return [p for p in self._projections if p.task_id in wanted]


# --------------------------------------------------------------------------- #
# freshness.py — pure semantics
# --------------------------------------------------------------------------- #


def test_compare_revisions_compares_each_owner_dimension_independently() -> None:
    from acp_services.runtime.freshness import compare_revisions

    verdict = compare_revisions({"job": 5, "name": 2}, {"job": 5, "name": 3})
    assert verdict.freshness == "stale"
    assert verdict.stale_dimensions == ("name",)
    assert set(verdict.compared_dimensions) == {"job", "name"}

    ok = compare_revisions({"job": 5, "name": 2}, {"job": 5, "name": 2})
    assert ok.freshness == "fresh"
    assert ok.stale_dimensions == ()


def test_compare_revisions_no_cross_owner_substitution() -> None:
    from acp_services.runtime.freshness import compare_revisions

    matching_job_cannot_vouch_for_name = compare_revisions(
        {"job": 5, "name": 1}, {"job": 5, "name": 4}
    )
    assert matching_job_cannot_vouch_for_name.freshness == "stale"
    assert matching_job_cannot_vouch_for_name.stale_dimensions == ("name",)

    matching_attempt_cannot_vouch_for_job = compare_revisions(
        {"attempt": 1, "job": 1}, {"attempt": 1, "job": 9}
    )
    assert matching_attempt_cannot_vouch_for_job.freshness == "stale"
    assert matching_attempt_cannot_vouch_for_job.stale_dimensions == ("job",)

    unclaimed_current_revision_is_not_a_mismatch = compare_revisions(
        {"name": 2}, {"name": 2, "job": 99}
    )
    assert unclaimed_current_revision_is_not_a_mismatch.freshness == "fresh"


def test_compare_revisions_partial_basis_ignores_unclaimed_dimensions() -> None:
    from acp_services.runtime.freshness import compare_revisions

    verdict = compare_revisions({"job": 5}, {"job": 5, "attempt": 7, "name": 8})
    assert verdict.freshness == "fresh"
    assert verdict.compared_dimensions == ("job",)

    empty_basis = compare_revisions({}, {"job": 9})
    assert empty_basis.freshness == "fresh"


def test_compare_revisions_ignores_non_integer_and_missing_values() -> None:
    from acp_services.runtime.freshness import compare_revisions

    verdict = compare_revisions({"job": None, "name": "bad", "attempt": 1}, {"attempt": 2})
    assert verdict.freshness == "stale"
    assert verdict.stale_dimensions == ("attempt",)


def test_resolve_ready_but_stale() -> None:
    from acp_services.runtime.freshness import resolve_projection_state

    state = resolve_projection_state(
        has_projection=True,
        basis_revisions={"job": 4},
        current_revisions={"job": 5},
        observed_at="2026-10-19T00:00:00+00:00",
    )
    assert state.availability == "ready"
    assert state.freshness == "stale"
    assert state.stale_dimensions == ("job",)
    assert state.basis_revision == {"job": 4}


def test_resolve_missing_projection_is_unavailable_unknown() -> None:
    from acp_services.runtime.freshness import resolve_projection_state

    state = resolve_projection_state(
        has_projection=False,
        current_revisions={"job": 5},
    )
    assert state.availability == "unavailable"
    assert state.freshness == "unknown"
    assert state.basis_revision == {}


def test_resolve_preparing_when_refresh_pending() -> None:
    from acp_services.runtime.freshness import resolve_projection_state

    state = resolve_projection_state(
        has_projection=False,
        current_revisions={"job": 5},
        preparing=True,
    )
    assert state.availability == "preparing"
    assert state.freshness == "unknown"


def test_resolve_freshly_enriched_is_ready_and_fresh() -> None:
    from acp_services.runtime.freshness import resolve_projection_state

    state = resolve_projection_state(
        has_projection=False,
        current_revisions={"job": 5, "name": 2},
        freshly_enriched=True,
    )
    assert state.availability == "ready"
    assert state.freshness == "fresh"
    assert state.basis_revision == {"job": 5, "name": 2}


def test_observed_at_never_decides_freshness_under_clock_skew() -> None:
    from acp_services.runtime.freshness import resolve_projection_state

    older = resolve_projection_state(
        has_projection=True,
        basis_revisions={"job": 5},
        current_revisions={"job": 5},
        observed_at="2000-01-01T00:00:00+00:00",
    )
    later = resolve_projection_state(
        has_projection=True,
        basis_revisions={"job": 5},
        current_revisions={"job": 5},
        observed_at="2099-01-01T00:00:00+00:00",
    )
    assert older.freshness == later.freshness == "fresh"


def test_observation_monotonicity_rejects_forged_earlier_timestamp() -> None:
    from acp_services.runtime.freshness import (
        ProjectionObservationError,
        validate_observation_monotonicity,
    )

    with pytest.raises(ProjectionObservationError):
        validate_observation_monotonicity(
            "2026-10-19T09:59:00+00:00",
            "2026-10-19T10:00:00+00:00",
        )


def test_observation_monotonicity_allows_forward_and_unparseable() -> None:
    from acp_services.runtime.freshness import validate_observation_monotonicity

    validate_observation_monotonicity(
        "2026-10-19T10:00:01+00:00",
        "2026-10-19T10:00:00+00:00",
    )
    validate_observation_monotonicity("", "2026-10-19T10:00:00+00:00")
    validate_observation_monotonicity("not-a-time", "2026-10-19T10:00:00+00:00")


def test_incoming_is_late_detects_old_attempt() -> None:
    from acp_services.runtime.freshness import incoming_is_late

    assert incoming_is_late({"job": 5, "attempt": 1}, {"job": 5, "attempt": 2})
    assert not incoming_is_late({"job": 6, "attempt": 2}, {"job": 5, "attempt": 2})
    assert not incoming_is_late({"job": 5, "attempt": 2}, {"job": 5, "attempt": 2})


# --------------------------------------------------------------------------- #
# projections.py store behavior
# --------------------------------------------------------------------------- #


def test_projection_roundtrip_carries_basis_revisions_and_availability() -> None:
    from acp_services.runtime.projections import TaskRowProjection

    projection = TaskRowProjection(
        task_id="t",
        fields={"stage_index": 1},
        basis_revisions={"job": 3, "attempt": 2},
        availability="ready",
    )
    rebuilt = TaskRowProjection.from_dict(projection.to_dict())
    assert rebuilt.basis_revisions == {"job": 3, "attempt": 2}
    assert rebuilt.availability == "ready"


def test_annotate_rows_ready_but_stale_keeps_base_facts() -> None:
    from acp_services.runtime.projections import TaskProjectionStore, TaskRowProjection

    store = TaskProjectionStore(
        source=_Source(
            [
                TaskRowProjection(
                    task_id="t",
                    fields={"stage_index": 4},
                    basis_revisions={"job": 4, "attempt": 1},
                    availability="ready",
                )
            ]
        ),
        auto_refresh=False,
    )
    assert store.refresh(["t"]) == 1

    row = {"id": "t", "status": "running", "molecule_name": "CCO", "revision": 5, "attempt": 1}
    store.enrich_rows([row])
    store.annotate_rows([row], current_revisions={"t": {"job": 5, "attempt": 1}})

    assert row["availability"] == "ready"
    assert row["freshness"] == "stale"
    assert row["stale_dimensions"] == ["job"]
    assert row["basis_revision"] == {"job": 4, "attempt": 1}
    assert row["status"] == "running"
    assert row["molecule_name"] == "CCO"
    assert row["revision"] == 5


def test_annotate_rows_missing_projection_shows_base_facts() -> None:
    from acp_services.runtime.projections import TaskProjectionStore

    store = TaskProjectionStore(source=None, auto_refresh=False)
    row = {
        "id": "missing",
        "status": "running",
        "molecule_name": "CCO",
        "revision": 3,
        "attempt": 1,
        "stage_index": None,
    }
    covered = store.annotate_rows([row], current_revisions={"missing": {"job": 3}})

    assert covered == 0
    assert row["availability"] == "unavailable"
    assert row["freshness"] == "unknown"
    assert row["basis_revision"] == {}
    assert row["observed_at"] == ""
    assert row["status"] == "running"
    assert row["molecule_name"] == "CCO"
    assert row["stage_index"] is None


def test_annotate_rows_late_old_attempt_projection_is_stale_without_overwrite() -> None:
    from acp_services.runtime.projections import TaskProjectionStore

    store = TaskProjectionStore(
        source=_Source([_projection("late", {"stage_index": 9}, {"job": 4, "attempt": 1})]),
        auto_refresh=False,
    )
    assert store.refresh(["late"]) == 1
    row = {"id": "late", "status": "running", "attempt": 2, "revision": 5, "stage_index": None}

    store.annotate_rows([row], current_revisions={"late": {"job": 5, "attempt": 2}})

    assert row["freshness"] == "stale"
    assert set(row["stale_dimensions"]) == {"job", "attempt"}
    assert row["attempt"] == 2
    assert row["revision"] == 5
    assert row["status"] == "running"


def test_annotate_rows_clock_skew_correct_revision_is_fresh() -> None:
    from acp_services.runtime.projections import TaskProjectionStore

    store = TaskProjectionStore(
        source=_Source(
            [
                _projection(
                    "skew",
                    {"stage_index": 2},
                    {"job": 7, "name": 1},
                    observed_at="1999-12-31T23:59:59+00:00",
                )
            ]
        ),
        auto_refresh=False,
    )
    assert store.refresh(["skew"]) == 1
    row = {"id": "skew", "status": "running", "revision": 7, "name_revision": 1}

    store.annotate_rows([row], current_revisions={"skew": {"job": 7, "name": 1}})

    assert row["freshness"] == "fresh"
    assert row["availability"] == "ready"
    assert row["observed_at"] == "1999-12-31T23:59:59+00:00"


def test_store_discards_late_projection_so_it_never_overwrites_newer_facts() -> None:
    from acp_services.runtime.projections import TaskProjectionStore, TaskRowProjection

    newer = TaskRowProjection(
        task_id="t", fields={"stage_index": 9}, basis_revisions={"job": 5, "attempt": 2}
    )
    older = TaskRowProjection(
        task_id="t", fields={"stage_index": 1}, basis_revisions={"job": 5, "attempt": 1}
    )
    store = TaskProjectionStore(source=_Source([newer, older]), auto_refresh=False)
    store.refresh(["t"])
    assert store.enrichment_for("t") == {"stage_index": 9}

    store2 = TaskProjectionStore(source=_Source([older]), auto_refresh=False)
    store2.refresh(["t"])
    assert store2.enrichment_for("t") == {"stage_index": 1}

    store3 = TaskProjectionStore(source=_Source([newer]), auto_refresh=False)
    store3.refresh(["t"])
    store3.refresh(["t"])
    assert store3.enrichment_for("t") == {"stage_index": 9}


def test_annotate_rows_never_writes_jobs(client: TestClient, tmp_path: Path) -> None:
    from acp.api import v2_routes

    manager = client.app.state.job_manager
    _seed_running_job(
        client,
        job_id="ann-ro",
        work_dir=tmp_path / "ann-ro",
        state={"stage_index": 1},
        revision=2,
    )
    before = _jobs_dump(manager)
    store = v2_routes.install_task_projection(manager, auto_refresh=False)
    store.refresh(["ann-ro"])
    _query_rows(client)
    store.annotate_rows([{"id": "ann-ro"}], current_revisions={"ann-ro": {"job": 9}})
    assert _jobs_dump(manager) == before


# --------------------------------------------------------------------------- #
# v2 API integration — three states on one response
# --------------------------------------------------------------------------- #


def test_task_view_presents_ready_stale_and_missing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from acp.api import v2_routes

    manager = client.app.state.job_manager
    store = v2_routes.install_task_projection(manager, auto_refresh=False)

    _seed_running_job(
        client,
        job_id="state-stale",
        work_dir=tmp_path / "stale",
        state={"stage_index": 2, "stage_total": 5},
        revision=1,
    )
    assert store.refresh(["state-stale"]) == 1
    _bump_jobs_revision(manager, "state-stale", 2)

    _seed_running_job(
        client,
        job_id="state-missing",
        work_dir=tmp_path / "missing",
        state={"stage_index": 1},
        revision=1,
    )
    monkeypatch.setattr(v2_routes, "_ENRICH_ACTIVE_ROWS_COMPAT", False)

    rows = _query_rows(client)
    stale = _row(rows, "state-stale")
    missing = _row(rows, "state-missing")

    assert stale["availability"] == "ready"
    assert stale["freshness"] == "stale"
    assert stale["basis_revision"]["job"] == 1
    assert "job" in stale["stale_dimensions"]
    assert stale["observed_at"]
    assert stale["status"] == "running"
    assert stale["molecule_name"] == "CCO"

    assert missing["availability"] == "unavailable"
    assert missing["freshness"] == "unknown"
    assert missing["basis_revision"] == {}
    assert missing["stage_index"] is None
    assert missing["status"] == "running"
    assert missing["molecule_name"] == "CCO"


def test_task_view_rows_always_carry_annotation_fields(client: TestClient, tmp_path: Path) -> None:
    from acp.api import v2_routes

    manager = client.app.state.job_manager
    _seed_running_job(
        client,
        job_id="annot-fields",
        work_dir=tmp_path / "fields",
        state={"stage_index": 3},
        revision=1,
    )
    store = v2_routes.install_task_projection(manager, auto_refresh=False)
    assert store.refresh(["annot-fields"]) == 1
    rows = _query_rows(client)
    row = _row(rows, "annot-fields")
    for key in ("availability", "freshness", "basis_revision", "observed_at", "stale_dimensions"):
        assert key in row, key
    assert row["basis_revision"]  # covered by the prepared snapshot -> current basis


def _projection(
    task_id: str,
    fields: dict[str, Any],
    basis: dict[str, int],
    *,
    observed_at: str = "",
) -> Any:
    from acp_services.runtime.projections import TaskRowProjection

    return TaskRowProjection(
        task_id=task_id,
        fields=fields,
        basis_revisions=basis,
        availability="ready",
        observed_at=observed_at,
    )
