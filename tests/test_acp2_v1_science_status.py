"""B1-4 — ``GET /api/v1/science/tasks/{job_id}/attempts/{attempt}/status``.

These tests pin the read-only attempt-status contract of ``acp.api.v1_science``:

* the router gates explicitly (no ``_job_path_authorization`` dependency);
* the wire body is exactly ``{schema_version, task_ref, status, revision}``;
* current attempts come from the job record, historical attempts from their own
  archived ``execution_receipt.json`` (``receipt.attempt`` must match the path);
* an unprovable historical/remote attempt is a 409 ``resource_not_ready`` with
  reason ``attempt_history_unavailable`` (never a new error code);
* unknown jobs, future attempts and cross-project reads are all the same 404;
* no GET mutates the job store (``manager.store.counts()`` is unchanged).

The router is mounted post-hoc on the live app because ``server.py`` mounting is
owned by a later wave.  ``create_app`` returns a real ``FastAPI`` instance, so
``include_router`` after lifespan start registers the route for both HTTP and
``app.openapi()``.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

_STATUS_PATH = "/api/v1/science/tasks/{job_id}/attempts/{attempt}/status"

_FORBIDDEN_TOP_KEYS = frozenset(
    {"work_dir", "pid", "host", "remote_job_id", "state", "attempt", "job_id", "task_id"}
)
_FORBIDDEN_TASK_REF_KEYS = frozenset({"release", "work_dir", "pid", "host", "remote_job_id"})


def _set_environment(monkeypatch: pytest.MonkeyPatch, run_root: Path) -> None:
    """Set the pre-import environment required by ``create_app``."""
    monkeypatch.setenv("ACP_PUBLIC_ORIGIN", "")
    monkeypatch.setenv("ACP_RUN_ROOT", str(run_root))
    monkeypatch.setenv("ACP_DEPLOYMENT_TIER", "local-single")
    monkeypatch.setenv("ACP_MAX_RUNNING", "1")


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    _set_environment(monkeypatch, tmp_path)
    from acp.api import v1_science
    from acp.api.server import create_app

    app = create_app(run_root=tmp_path, max_running=1)
    with TestClient(app) as test_client:
        # Later wave owns server.py mounting; mount on the live app here.
        test_client.app.include_router(v1_science.router, prefix="/api/v1")
        test_client.app.openapi_schema = None
        yield test_client


def _seed_completed_job(
    client: TestClient,
    *,
    job_id: str,
    work_dir: Path,
    attempt: int = 1,
    revision: int = 3,
    project_id: str = "proj-a",
) -> Any:
    """Seed one COMPLETED job + its task row (mirrors the projection fixtures)."""
    from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus

    manager = client.app.state.job_manager
    work_dir.mkdir(parents=True, exist_ok=True)
    spec = JobSpec(
        workflow="Confsearch",
        name=job_id,
        molecule_name="CCO",
        task_name="opt",
        remark="r",
        project_id=project_id,
        input={"source": "CCO"},
    )
    record = JobRecord(
        id=job_id,
        spec=spec,
        status=JobStatus.COMPLETED,
        work_dir=str(work_dir),
        project_id=project_id,
        attempt=attempt,
        revision=revision,
    )
    manager.store.create(record)
    manager.tasks.sync_from_job(record)
    return record


def _get(client: TestClient, url: str) -> Any:
    """GET *url* and prove the call had no job-store side effect."""
    manager = client.app.state.job_manager
    before = manager.store.counts()
    response = client.get(url)
    after = manager.store.counts()
    assert after == before, f"GET {url} changed store counts: {before} -> {after}"
    return response


def _write_receipt(archive_root: Path, *, attempt: int, state: str, revision: int) -> None:
    archive_root.mkdir(parents=True, exist_ok=True)
    (archive_root / "execution_receipt.json").write_text(
        json.dumps(
            {
                "attempt": attempt,
                "state": state,
                "written_at": "2026-10-24T00:00:00+00:00",
                "revision": revision,
            }
        ),
        encoding="utf-8",
    )


def test_current_attempt_returns_filtered_status(client: TestClient, tmp_path: Path) -> None:
    job_id = "science-current"
    _seed_completed_job(
        client,
        job_id=job_id,
        work_dir=tmp_path / "current",
        attempt=1,
        revision=3,
    )

    response = _get(client, _STATUS_PATH.format(job_id=job_id, attempt=1))

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"schema_version", "task_ref", "status", "revision"}
    assert body["task_ref"] == {"job_id": job_id, "attempt": 1, "task_id": job_id}
    assert body["status"] == "completed"
    assert body["revision"] == 3
    assert body["schema_version"] == 1
    assert not (_FORBIDDEN_TOP_KEYS & set(body))
    assert not (_FORBIDDEN_TASK_REF_KEYS & set(body["task_ref"]))
    assert "release" not in body["task_ref"]


def test_historical_attempt_reads_own_receipt(client: TestClient, tmp_path: Path) -> None:
    job_id = "science-historical"
    work_dir = tmp_path / "historical"
    _seed_completed_job(
        client,
        job_id=job_id,
        work_dir=work_dir,
        attempt=2,
        revision=4,
    )
    _write_receipt(
        work_dir / "WORK" / "00_RUNTIME" / "attempts" / "1",
        attempt=1,
        state="completed",
        revision=2,
    )

    response = _get(client, _STATUS_PATH.format(job_id=job_id, attempt=1))

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"schema_version", "task_ref", "status", "revision"}
    assert body["status"] == "completed"
    assert body["task_ref"] == {"job_id": job_id, "attempt": 1, "task_id": None}
    assert isinstance(body["revision"], int)


def test_historical_receipt_attempt_mismatch_is_unprovable(
    client: TestClient, tmp_path: Path
) -> None:
    job_id = "science-mismatch"
    work_dir = tmp_path / "mismatch"
    _seed_completed_job(
        client,
        job_id=job_id,
        work_dir=work_dir,
        attempt=3,
        revision=5,
    )
    # Archive under attempts/2 carries a receipt for a different attempt.
    _write_receipt(
        work_dir / "WORK" / "00_RUNTIME" / "attempts" / "2",
        attempt=1,
        state="completed",
        revision=1,
    )

    response = _get(client, _STATUS_PATH.format(job_id=job_id, attempt=2))

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "resource_not_ready"
    assert detail["params"]["reason"] == "attempt_history_unavailable"


def test_attempt_zero_is_input_invalid(client: TestClient, tmp_path: Path) -> None:
    job_id = "science-zero"
    _seed_completed_job(client, job_id=job_id, work_dir=tmp_path / "zero")

    response = _get(client, _STATUS_PATH.format(job_id=job_id, attempt=0))

    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "input_invalid"
    assert detail["params"] == {"field": "attempt"}


def test_unknown_job_is_not_found(client: TestClient, tmp_path: Path) -> None:
    _seed_completed_job(client, job_id="science-known", work_dir=tmp_path / "known")

    response = _get(client, _STATUS_PATH.format(job_id="science-missing", attempt=1))

    assert response.status_code == 404, response.text
    assert "Job not found" in str(response.json()["detail"])


def test_future_attempt_is_not_found(client: TestClient, tmp_path: Path) -> None:
    job_id = "science-future"
    _seed_completed_job(client, job_id=job_id, work_dir=tmp_path / "future", attempt=1)

    response = _get(client, _STATUS_PATH.format(job_id=job_id, attempt=9))

    assert response.status_code == 404, response.text
    assert "Job not found" in str(response.json()["detail"])


def test_cross_project_reads_are_not_found(client: TestClient, tmp_path: Path) -> None:
    from acp_services.workspace.server_identity import ServerIdentity

    own = _seed_completed_job(
        client,
        job_id="science-proj-a",
        work_dir=tmp_path / "proj-a",
        project_id="proj-a",
    )
    _seed_completed_job(
        client,
        job_id="science-proj-b",
        work_dir=tmp_path / "proj-b",
        project_id="proj-b",
    )

    client.app.state.server_identity = ServerIdentity.build(verified_project=own.project_id)

    mismatch_hint = _get(
        client,
        _STATUS_PATH.format(job_id=own.id, attempt=1) + "?project_id=proj-b",
    )
    assert mismatch_hint.status_code == 404, mismatch_hint.text
    assert mismatch_hint.json()["detail"] == "Project not found"

    foreign_job = _get(client, _STATUS_PATH.format(job_id="science-proj-b", attempt=1))
    assert foreign_job.status_code == 404, foreign_job.text
    assert "Job not found" in str(foreign_job.json()["detail"])

    own_job = _get(
        client,
        _STATUS_PATH.format(job_id=own.id, attempt=1) + "?project_id=proj-a",
    )
    assert own_job.status_code == 200, own_job.text


def test_remote_historical_unprovable_is_409(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from acp_services.data.read_root import ReadRoot

    job_id = "science-remote"
    _seed_completed_job(client, job_id=job_id, work_dir=tmp_path / "remote", attempt=2)

    class _FakeResolver:
        def resolve(self, job_id: str, attempt: int | None = None) -> ReadRoot:
            return ReadRoot(
                job_id=job_id,
                path=str(tmp_path),
                is_remote=True,
                catalog_ready=False,
                found=False,
                attempt=attempt,
                historical=True,
                reason="historical_attempt_missing",
            )

    def _fake_build_read_root_resolver(**_kwargs: Any) -> _FakeResolver:
        return _FakeResolver()

    from acp.api import v1_science

    monkeypatch.setattr(
        v1_science,
        "build_read_root_resolver",
        _fake_build_read_root_resolver,
    )

    response = _get(client, _STATUS_PATH.format(job_id=job_id, attempt=1))

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "resource_not_ready"
    assert detail["params"]["reason"] == "attempt_history_unavailable"


def test_route_is_registered_in_openapi(client: TestClient) -> None:
    paths = client.app.openapi()["paths"]
    expected = "/api/v1/science/tasks/{job_id}/attempts/{attempt}/status"
    assert expected in paths, sorted(paths)
    assert "get" in paths[expected]
