"""Round-9 M6: the status descriptor's ``prepare`` template works verbatim.

The external-agent complaint (round-9 M6) was narrower than round-8: the
descriptor already pointed at ``acp_prepare_artifact``, but the arguments it
carried (``{job_id, purpose, artifact_id}``) were rejected later — the executor
needs the manifest product's ``relative_path``, and an empty one surfaced as a
bare ``invalid_parameter``.

Pinned here:

* the template carries ``relative_path`` (the manifest product path, exactly the
  path prepare/read validate) and ``media_type`` when the extension implies one;
* every template field is accepted by ``normalize_prepare`` and declared by
  ``PREPARE_FIELD_POLICY``/``V1PrepareRequest`` — the template cannot drift from
  the request contract again;
* copy-pasting the template into the real Controller
  (``POST /api/v1/jobs/{id}/prepare``) prepares the artifact, and the returned
  five-tuple reads the same bytes — end to end, no hand-written path;
* the server-side derivation (``resolve_prepare_target``) accepts an omitted
  path only when the manifest resolves the id, and never lets a client path
  override the manifest.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from acp.api import artifact_descriptors as descriptors
from acp_services.entrypoints.application_params import (
    PREPARE_FIELD_POLICY,
    media_type_for_path,
    normalize_prepare,
)

_JOB_ID = "20261102_101500_001_frequency"
_PRODUCT_PATH = "frequencies/normal_modes.json"
_RELATIVE_PATH = f"RESULT/{_PRODUCT_PATH}"
_BODY = json.dumps({"modes": [{"frequency_cm1": 1234.5}]}, ensure_ascii=False).encode("utf-8")


# --------------------------------------------------------------------------- #
# fixtures: a real task dir, a real job row, a real Controller
# --------------------------------------------------------------------------- #


def _write_task(root: Path) -> Path:
    result = root / "RESULT"
    product = result / _PRODUCT_PATH
    product.parent.mkdir(parents=True)
    product.write_bytes(_BODY)
    manifest = {
        "schema": "acp2.result-manifest.v2",
        "version": 2,
        "task_id": _JOB_ID,
        "workflow": "frequency",
        "status": "completed",
        "products": [
            {
                "id": "normal_modes",
                "kind": "frequency_modes",
                "label": "Normal modes",
                "path": _PRODUCT_PATH,
            }
        ],
    }
    (result / "result_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    from acp.api.server import create_app

    with TestClient(create_app(run_root=tmp_path, max_running=1)) as test_client:
        yield test_client


def _seed_job(client: TestClient, root: Path) -> Path:
    from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus

    work_dir = root / "uncategorized" / f"{_JOB_ID}-1"
    _write_task(work_dir)
    client.app.state.job_manager.store.create(
        JobRecord(
            id=_JOB_ID,
            spec=JobSpec(workflow="frequency", name="freq-template"),
            status=JobStatus.COMPLETED,
            work_dir=str(work_dir),
            attempt=1,
            project_id="uncategorized",
        )
    )
    return work_dir


def _wait_operation(
    client: TestClient, operation_id: str, *, timeout: float = 5.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    body: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/operations/{operation_id}")
        assert response.status_code == 200, response.text
        body = response.json()
        if body["state"] in {"ready", "failed", "cancelled"}:
            return body
        time.sleep(0.01)
    raise AssertionError(f"operation did not settle: {body}")


def _status_artifact(client: TestClient) -> dict[str, Any]:
    response = client.get(f"/api/v1/jobs/{_JOB_ID}")
    assert response.status_code == 200, response.text
    artifacts = response.json()["artifacts"]
    assert len(artifacts) == 1, artifacts
    return artifacts[0]


# --------------------------------------------------------------------------- #
# 1. template completeness and contract closure
# --------------------------------------------------------------------------- #


def test_template_carries_the_manifest_path_and_media_type(tmp_path: Path) -> None:
    task = _write_task(tmp_path)
    items, truncated = descriptors.describe_job_artifacts(task, attempt=1, job_id=_JOB_ID)
    assert truncated is False
    arguments = items[0]["prepare"]["arguments"]
    assert arguments == {
        "job_id": _JOB_ID,
        "purpose": "artifact",
        "artifact_id": "normal_modes",
        "relative_path": _RELATIVE_PATH,
        "media_type": "application/json",
    }
    # The template path is exactly what the prepare/read authority accepts.
    from acp_services.data.artifacts import validate_artifact_relative_path

    assert validate_artifact_relative_path(arguments["relative_path"]) == _RELATIVE_PATH
    assert (task / arguments["relative_path"]).read_bytes() == _BODY


def test_every_template_field_is_accepted_by_the_prepare_contract(tmp_path: Path) -> None:
    """M6 schema closure: a template field the DTO/policy forgets fails here."""
    from acp.api.v1_schemas import V1PrepareRequest

    task = _write_task(tmp_path)
    arguments = descriptors.describe_job_artifacts(task, attempt=1, job_id=_JOB_ID)[0][0][
        "prepare"
    ]["arguments"]
    # 1) declared by the frozen field policy and the request DTO
    assert set(arguments) <= set(PREPARE_FIELD_POLICY)
    assert set(arguments) <= set(V1PrepareRequest.model_fields)
    # 2) the single normaliser accepts the template unchanged
    normalized = normalize_prepare(arguments)
    for field in ("job_id", "purpose", "artifact_id", "relative_path", "media_type"):
        assert normalized[field] == arguments[field]
    # 3) the DTO itself accepts the template (extra="forbid" would catch a typo)
    dumped = V1PrepareRequest(**arguments).model_dump(exclude_none=True)
    dumped.pop("schema_version")
    assert dumped == arguments


def test_omitted_media_type_is_derived_from_the_extension() -> None:
    assert media_type_for_path("RESULT/frequencies/normal_modes.json") == "application/json"
    assert media_type_for_path("RESULT/frames/frame_003.xyz") == "chemical/x-xyz"
    assert media_type_for_path("RESULT/normal_termination.out") == "text/plain"
    assert media_type_for_path("RESULT/blob.bin") is None
    prepared = normalize_prepare(
        {
            "purpose": "artifact",
            "job_id": _JOB_ID,
            "artifact_id": "normal_modes",
            "relative_path": _RELATIVE_PATH,
        }
    )
    assert prepared["media_type"] == "application/json"
    # A caller-supplied media type always wins over the derived one.
    declared = normalize_prepare(
        {
            "purpose": "artifact",
            "job_id": _JOB_ID,
            "artifact_id": "normal_modes",
            "relative_path": _RELATIVE_PATH,
            "media_type": "application/vnd.acp+json",
        }
    )
    assert declared["media_type"] == "application/vnd.acp+json"


# --------------------------------------------------------------------------- #
# 2. copy-paste end to end through the real Controller
# --------------------------------------------------------------------------- #


def test_status_template_copy_paste_prepares_and_reads(client: TestClient, tmp_path: Path) -> None:
    _seed_job(client, tmp_path)
    descriptor = _status_artifact(client)
    assert descriptor["relative_path"] == _RELATIVE_PATH
    assert descriptor["prepared"] is False
    template = dict(descriptor["prepare"]["arguments"])

    prepared = client.post(f"/api/v1/jobs/{_JOB_ID}/prepare", json=template)
    assert prepared.status_code == 200, prepared.text
    operation_id = prepared.json()["operation_id"]
    settled = _wait_operation(client, operation_id)
    assert settled["state"] == "ready", settled
    digest = settled["artifact_ref"]["digest"]
    assert digest == "sha256:" + hashlib.sha256(_BODY).hexdigest()
    assert settled["size_bytes"] == len(_BODY)

    read = client.get(
        f"/api/v1/jobs/{_JOB_ID}/prepared/{descriptor['resource_id']}",
        params={"attempt": 1, "expected_revision": 1, "digest": digest},
    )
    assert read.status_code == 200, read.text
    payload = read.json()
    assert payload["body"].encode("utf-8") == _BODY
    assert payload["digest"] == digest


def test_status_template_without_media_type_still_prepares(
    client: TestClient, tmp_path: Path
) -> None:
    """``media_type`` is advisory: dropping it changes no committed fact."""
    _seed_job(client, tmp_path)
    template = dict(_status_artifact(client)["prepare"]["arguments"])
    template.pop("media_type")
    prepared = client.post(f"/api/v1/jobs/{_JOB_ID}/prepare", json=template)
    assert prepared.status_code == 200, prepared.text
    settled = _wait_operation(client, prepared.json()["operation_id"])
    assert settled["state"] == "ready", settled
    assert settled["artifact_ref"]["media_type"] in {
        "application/json",
        "application/octet-stream",
    }
    assert settled["artifact_ref"]["relative_path"] == _RELATIVE_PATH


# --------------------------------------------------------------------------- #
# 3. server-side derivation (the same authority, no client path)
# --------------------------------------------------------------------------- #


def test_derivation_resolves_an_omitted_relative_path(tmp_path: Path) -> None:
    task = _write_task(tmp_path)
    target = descriptors.resolve_prepare_target(task, artifact_id="normal_modes")
    assert target.relative_path == _RELATIVE_PATH
    assert target.media_type == "application/json"


def test_derivation_keeps_the_manifest_path_when_the_client_agrees(tmp_path: Path) -> None:
    task = _write_task(tmp_path)
    target = descriptors.resolve_prepare_target(
        task, artifact_id="normal_modes", relative_path=_RELATIVE_PATH
    )
    assert target.relative_path == _RELATIVE_PATH


def test_derivation_never_lets_a_client_path_override_the_manifest(tmp_path: Path) -> None:
    task = _write_task(tmp_path)
    with pytest.raises(descriptors.PrepareTargetRefusedError) as refused:
        descriptors.resolve_prepare_target(
            task,
            artifact_id="normal_modes",
            relative_path="RESULT/other/body.json",
        )
    assert refused.value.code == "revision_conflict"
    assert refused.value.params == {
        "field": "relative_path",
        "rule": "manifest_mismatch",
        "manifest_relative_path": _RELATIVE_PATH,
    }
    assert refused.value.retryable is False
