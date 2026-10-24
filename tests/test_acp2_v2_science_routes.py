"""Route-level tests for the read-only ACP2 science router (B1-2/3/5/6).

Pins the four endpoints of ``acp.api.v2_science`` mounted post-hoc on a live
app (``server.py`` mounting is a separate lane):

* capabilities are metadata only (8 tools, 3 questions, no fixture data);
* preflight is zero-side-effect and its fingerprint is the plan digest;
* an attempt view has no ``task_ref.release`` and is read-only;
* an artifact read maps every refusal onto the frozen error codes;
* the project boundary returns 403 for an unknown project and 404 for a
  verified-project mismatch.

Every GET is wrapped to prove ``manager.store.counts()`` is unchanged.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from acp.storage.manifest import ResultManifest
from acp_services.entrypoints.mcp import TOOL_ALLOWLIST

_CAPABILITIES = "/api/v2/science/capabilities"
_PREFLIGHT = "/api/v2/science/preflight"
_VIEW = "/api/v2/science/tasks/{job_id}/attempts/{attempt}/view"
_ARTIFACT = "/api/v2/science/tasks/{job_id}/attempts/{attempt}/artifacts/{artifact_id}/read"

_DIGEST = "sha256:" + "0" * 64


def _set_environment(monkeypatch: pytest.MonkeyPatch, run_root: Path) -> None:
    monkeypatch.setenv("ACP_PUBLIC_ORIGIN", "")
    monkeypatch.setenv("ACP_RUN_ROOT", str(run_root))
    monkeypatch.setenv("ACP_DEPLOYMENT_TIER", "local-single")
    monkeypatch.setenv("ACP_MAX_RUNNING", "1")


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    _set_environment(monkeypatch, tmp_path)
    from acp.api import v2_science
    from acp.api.server import create_app

    app = create_app(run_root=tmp_path, max_running=1)
    with TestClient(app) as test_client:
        test_client.app.include_router(v2_science.router, prefix="/api/v2")
        test_client.app.openapi_schema = None
        test_client.app.state.science_capability_descriptors = {"capabilities": []}
        test_client.app.state.science_capability_domain = {"domain": "test"}
        yield test_client


def _seed_completed_job(
    client: TestClient,
    *,
    job_id: str,
    work_dir: Path,
    attempt: int = 1,
    revision: int = 3,
    project_id: str = "uncategorized",
    manifest: bool = True,
) -> Any:
    from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus

    manager = client.app.state.job_manager
    work_dir.mkdir(parents=True, exist_ok=True)
    if manifest:
        document = ResultManifest(task_id=job_id, workflow="optimize", status="completed")
        document.add_product(
            id="step_0_optimize",
            label="Optimized structure",
            path="opt.xyz",
            kind="structure",
        )
        document.write(work_dir / "RESULT")
    spec = JobSpec(
        workflow="optimize",
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
    manager = client.app.state.job_manager
    before = manager.store.counts()
    response = client.get(url)
    after = manager.store.counts()
    assert after == before, f"GET {url} changed store counts: {before} -> {after}"
    return response


def _post(client: TestClient, url: str, payload: dict[str, Any]) -> Any:
    manager = client.app.state.job_manager
    before = manager.store.counts()
    response = client.post(url, json=payload)
    after = manager.store.counts()
    assert after == before, f"POST {url} changed store counts: {before} -> {after}"
    return response


def _singlepoint_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "workflow": "singlepoint",
        "input": {
            "source_type": "smiles",
            "source": "CCO",
            "charge": 0,
            "multiplicity": 1,
        },
        "method": {
            "schema_id": "dft_singlepoint",
            "profile_id": "default",
            "levels": {
                "single_point": {
                    "engine": "orca",
                    "functional": "r2SCAN-3c",
                    "basis": "def2-TZVP",
                }
            },
        },
        "resources": {"nproc": 2, "mem": "2GB"},
        "molecule_name": "CCO",
        "remark": "",
    }
    payload.update(overrides)
    return payload


def test_capabilities_are_metadata_only(client: TestClient) -> None:
    response = _get(client, f"{_CAPABILITIES}?project_id=uncategorized")

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"protocol_version", "tools", "capabilities"}
    assert isinstance(body["protocol_version"], str) and body["protocol_version"]
    assert len(body["tools"]) == 8
    assert set(body["tools"]) == set(TOOL_ALLOWLIST)
    questions = body["capabilities"]["questions"]
    assert len(questions) == 3
    assert [item["workflow"] for item in questions] == ["singlepoint", "optimize", "frequency"]
    assert "fixture" not in json.dumps(body)


def test_preflight_fingerprint_is_the_plan_digest(client: TestClient) -> None:
    response = _post(
        client,
        f"{_PREFLIGHT}?project_id=uncategorized",
        _singlepoint_payload(),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["executed"] is False
    assert body["metering_consumed"] is False
    assert body["transfers_attempted"] is False
    assert body["plan"]["workflow"] == "singlepoint"
    assert body["plan"]["project_id"] == "uncategorized"
    assert body["preflight_fingerprint"] == body["plan"]["plan_digest"]
    assert isinstance(body["preflight_fingerprint"], str)
    assert body["estimate"]["nproc"] == 2
    assert body["estimate"]["feasible"] is True
    assert body["estimate"]["reason"] == ""


def test_preflight_resolves_workflow_from_question_id(client: TestClient) -> None:
    payload = _singlepoint_payload()
    payload.pop("workflow")
    payload["question_id"] = "geometry_optimization"

    response = _post(client, f"{_PREFLIGHT}?project_id=uncategorized", payload)

    assert response.status_code == 200, response.text
    assert response.json()["plan"]["workflow"] == "optimize"


def test_preflight_unresolvable_workflow_is_400(client: TestClient) -> None:
    payload = _singlepoint_payload()
    payload.pop("workflow")
    payload["question_id"] = "no-such-question"

    response = _post(client, f"{_PREFLIGHT}?project_id=uncategorized", payload)

    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "input_invalid"
    assert detail["params"] == {"field": "workflow"}


def test_view_is_read_only_and_has_no_release(client: TestClient, tmp_path: Path) -> None:
    job_id = "science-view"
    _seed_completed_job(client, job_id=job_id, work_dir=tmp_path / "view")

    response = _get(
        client,
        _VIEW.format(job_id=job_id, attempt=1) + "?project_id=uncategorized",
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["task_ref"]["job_id"] == job_id
    assert body["task_ref"]["attempt"] == 1
    assert "release" not in body["task_ref"]
    assert set(body["task_ref"]) == {"job_id", "attempt", "task_id"}
    assert body["read_only"] is True
    assert body["not_authoritative_for_science"] is True
    assert body["manifest_generation"] == 0
    assert [item["artifact_id"] for item in body["artifacts"]] == ["step_0_optimize"]
    assert body["provenance"]["digest"].startswith("sha256:")


def test_view_missing_attempt_is_404(client: TestClient, tmp_path: Path) -> None:
    job_id = "science-view-missing"
    _seed_completed_job(client, job_id=job_id, work_dir=tmp_path / "missing")

    response = _get(
        client,
        _VIEW.format(job_id=job_id, attempt=2) + "?project_id=uncategorized",
    )

    assert response.status_code == 404, response.text


def test_view_rejects_attempt_below_one(client: TestClient, tmp_path: Path) -> None:
    job_id = "science-view-invalid"
    _seed_completed_job(client, job_id=job_id, work_dir=tmp_path / "invalid")

    response = _get(
        client,
        f"/api/v2/science/tasks/{job_id}/attempts/0/view?project_id=uncategorized",
    )

    assert response.status_code == 400, response.text
    assert response.json()["detail"]["params"] == {"field": "attempt"}


def test_artifact_slash_id_is_path_traversal_400(client: TestClient, tmp_path: Path) -> None:
    job_id = "science-artifact-slash"
    _seed_completed_job(client, job_id=job_id, work_dir=tmp_path / "artifact")

    response = _get(
        client,
        f"/api/v2/science/tasks/{job_id}/attempts/1/artifacts/a/b/read"
        f"?project_id=uncategorized&generation=1&digest={_DIGEST}",
    )

    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "input_invalid"
    assert detail["params"]["rule"] == "path_traversal"


def test_artifact_read_requires_digest(client: TestClient, tmp_path: Path) -> None:
    job_id = "science-artifact-digest"
    _seed_completed_job(client, job_id=job_id, work_dir=tmp_path / "digest")

    response = _get(
        client,
        f"/api/v2/science/tasks/{job_id}/attempts/1/artifacts/step_0_optimize/read"
        "?project_id=uncategorized&generation=1",
    )

    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert detail["params"] == {"field": "digest", "rule": "required"}


def test_artifact_unprepared_is_409_not_prepared(client: TestClient, tmp_path: Path) -> None:
    job_id = "science-artifact-unprepared"
    _seed_completed_job(client, job_id=job_id, work_dir=tmp_path / "unprepared")

    response = _get(
        client,
        f"/api/v2/science/tasks/{job_id}/attempts/1/artifacts/step_0_optimize/read"
        f"?project_id=uncategorized&generation=1&digest={_DIGEST}",
    )

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "resource_not_ready"
    assert detail["params"]["reason"] == "not_prepared"
    assert detail["retryable"] is True


def test_unknown_project_is_403(client: TestClient) -> None:
    response = _get(client, f"{_CAPABILITIES}?project_id=ghost-project")

    assert response.status_code == 403, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "access_denied"
    assert detail["params"]["rule"] == "project_scope_mismatch"


def test_verified_project_mismatch_is_404(client: TestClient) -> None:
    from acp_services.workspace.server_identity import ServerIdentity

    client.app.state.server_identity = ServerIdentity.build(verified_project="other")

    response = _get(client, f"{_CAPABILITIES}?project_id=uncategorized")

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Project not found"


def test_openapi_exposes_the_four_science_templates(client: TestClient) -> None:
    paths = client.app.openapi()["paths"]

    assert "/api/v2/science/capabilities" in paths
    assert "/api/v2/science/preflight" in paths
    assert "/api/v2/science/tasks/{job_id}/attempts/{attempt}/view" in paths
    artifact_paths = [path for path in paths if path.endswith("/artifacts/{artifact_id}/read")]
    assert len(artifact_paths) == 1


def test_artifact_read_prepared_body_is_200(client: TestClient, tmp_path: Path) -> None:
    from acp_services.data.artifacts import ArtifactRef, ArtifactStore
    from acp_services.data.contracts import Availability, ResourceKind
    from acp_services.data.operations import (
        PrepareCompletion,
        PrepareRequest,
    )

    job_id = "science-artifact-prepared"
    _seed_completed_job(client, job_id=job_id, work_dir=tmp_path / "prepared")
    manager = client.app.state.job_manager
    service = manager._prepare_operations
    store = ArtifactStore(manager.run_root / "artifact-store")

    operation = service.enqueue(
        PrepareRequest(
            job_id=job_id,
            attempt=1,
            resource_id="step_0_optimize",
            resource_kind=ResourceKind.ARTIFACT,
            resource_digest="content-1",
            permission_scope="local-single",
            resource_generation=1,
            payload={"purpose": "artifact"},
        )
    )
    lease = service.lease_next()
    assert lease is not None
    published = store.publish(
        ArtifactRef(
            job_id=job_id,
            attempt=1,
            resource_id="step_0_optimize",
            generation=1,
            availability=Availability.PREPARING,
            relative_path="opt.xyz",
        ),
        b"prepared-body",
    )
    service.complete(
        lease,
        PrepareCompletion(
            availability=Availability.READY,
            result={"purpose": "artifact"},
            size_bytes=published.size_bytes,
            sha256=published.digest.removeprefix("sha256:"),
        ),
    )

    response = _get(
        client,
        f"/api/v2/science/tasks/{job_id}/attempts/1/artifacts/step_0_optimize/read"
        f"?project_id=uncategorized&generation={operation.resource_generation}"
        f"&digest={published.digest}",
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["digest"] == published.digest
    assert body["generation"] == 1
    assert body["body"] == "prepared-body"
    assert body["truncated"] is False
