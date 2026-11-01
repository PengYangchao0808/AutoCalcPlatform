"""Round-8 一.2: status descriptor -> prepare -> read is one copy-paste chain.

The external agent's complaint was structural: ``acp_read_artifact`` requires the
five-tuple ``(task_id, attempt, artifact_id, generation, digest)``, while the
status descriptor only names a manifest product.  The only producer of
generation/digest is ``acp_prepare_artifact`` — so the descriptor must point at
that call, the prepare answer must already be the read request, and nothing may
be invented in between.

Pinned here:

* a descriptor carries the exact ``prepare`` arguments (``resource_id`` ->
  ``artifact_id``, plus the job id), a real on-disk ``size_bytes`` (``None`` on
  error), and ``prepared`` stays ``False``;
* building descriptors is **zero-side-effect** (no operation, no prepare, no new
  files under the read root);
* descriptor -> ``acp_prepare_artifact`` -> ``acp_read_artifact`` runs entirely
  through the MCP surface, and the projected ``read.arguments.artifact_ref`` is
  accepted verbatim by the frozen read validator.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from acp.api import artifact_descriptors as descriptors

_JOB_ID = "20261031_120000_001_frequency"


# --------------------------------------------------------------------------- #
# fixtures: a real task dir with a real manifest + product file
# --------------------------------------------------------------------------- #


def _write_task(tmp_path: Path) -> Path:
    result = tmp_path / "RESULT"
    product = result / "frequencies" / "normal_modes.json"
    product.parent.mkdir(parents=True)
    body = json.dumps({"modes": [1, 2, 3]}, ensure_ascii=False).encode("utf-8")
    product.write_bytes(body)
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
                "path": "frequencies/normal_modes.json",
            }
        ],
    }
    (result / "result_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return tmp_path


def _snapshot(root: Path) -> list[str]:
    return sorted(str(path.relative_to(root)) for path in root.rglob("*"))


# --------------------------------------------------------------------------- #
# descriptor is the pre-prepare view but actionable
# --------------------------------------------------------------------------- #


def test_descriptor_has_a_real_size_and_the_prepare_step(tmp_path: Path) -> None:
    task = _write_task(tmp_path)
    size = (task / "RESULT/frequencies/normal_modes.json").stat().st_size
    items, truncated = descriptors.describe_job_artifacts(task, attempt=2, job_id=_JOB_ID)
    assert truncated is False
    assert len(items) == 1
    item = items[0]
    assert item["resource_id"] == "normal_modes"
    assert item["relative_path"] == "RESULT/frequencies/normal_modes.json"
    assert item["size_bytes"] == size
    assert item["prepared"] is False
    assert item["attempt"] == 2
    assert item["prepare"] == {
        "tool": "acp_prepare_artifact",
        "arguments": {
            "job_id": _JOB_ID,
            "purpose": "artifact",
            "artifact_id": "normal_modes",
        },
    }
    # The descriptor's own actionable id is the artifact_id prepare expects.
    assert item["prepare"]["arguments"]["artifact_id"] == item["resource_id"]


def test_descriptor_job_id_is_omitted_rather_than_invented(tmp_path: Path) -> None:
    task = _write_task(tmp_path)
    items, _ = descriptors.describe_job_artifacts(task)
    assert items[0]["prepare"]["arguments"] == {
        "purpose": "artifact",
        "artifact_id": "normal_modes",
    }


def test_missing_product_file_keeps_the_size_unknown(tmp_path: Path) -> None:
    task = _write_task(tmp_path)
    (task / "RESULT/frequencies/normal_modes.json").unlink()
    items, _ = descriptors.describe_job_artifacts(task)
    assert items[0]["size_bytes"] is None


def test_unsafe_paths_and_tokens_are_never_published(tmp_path: Path) -> None:
    task = _write_task(tmp_path)
    manifest = json.loads((task / "RESULT/result_manifest.json").read_text())
    manifest["products"] = [
        {"id": "traversal", "kind": "file", "label": "x", "path": "../secret.txt"},
        {"id": "absolute", "kind": "file", "label": "x", "path": "/etc/passwd"},
        {
            "id": "windows",
            "kind": "file",
            "label": "x",
            "path": "..\\secret.txt",
        },
        {"id": "bad token", "kind": "file", "label": "x", "path": "f.json"},
        {"id": "safe_id", "kind": "file", "label": "x", "path": "frequencies/f.json"},
    ]
    (task / "RESULT/result_manifest.json").write_text(json.dumps(manifest))
    items, _ = descriptors.describe_job_artifacts(task, job_id=_JOB_ID)
    assert [item["resource_id"] for item in items] == ["safe_id"]


def test_building_descriptors_has_zero_side_effects(tmp_path: Path) -> None:
    task = _write_task(tmp_path)
    before = _snapshot(task)
    descriptors.describe_job_artifacts(task, attempt=1, job_id=_JOB_ID)
    assert _snapshot(task) == before
    # no prepare operation store, no artifact-store, no transfer staging
    assert not (task / "WORK/00_RUNTIME/operations").exists()
    assert not (task / "artifact-store").exists()


# --------------------------------------------------------------------------- #
# descriptor -> prepare -> read through the MCP surface
# --------------------------------------------------------------------------- #


class _FakeCapabilities:
    def capabilities(self, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        return {"workflows": []}


class _Unused:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"must not reach the {name} port")


class _FakeArtifacts:
    """A Controller-shaped prepare/read port (no network, no store)."""

    def __init__(self, *, job_id: str, artifact_id: str, body: bytes) -> None:
        self._job_id = job_id
        self._artifact_id = artifact_id
        self._body = body
        self.read_requests: list[dict[str, Any]] = []

    def prepare(self, request: Any, *, idempotency_key: str, workspace_id: str) -> dict[str, Any]:
        del idempotency_key, workspace_id
        assert request["purpose"] == "artifact"
        assert request["artifact_id"] == self._artifact_id
        digest = "sha256:" + hashlib.sha256(self._body).hexdigest()
        return {
            "schema_version": "acp2.prepare.v1",
            "status": "ready",
            "purpose": "artifact",
            "job_id": self._job_id,
            "attempt": 1,
            "artifact_id": self._artifact_id,
            "generation": 2,
            "operation_ref": {"kind": "operation", "id": "op_round8"},
            "artifact_ref": {
                "schema": "acp2.artifact-ref.v1",
                "version": 1,
                "job_id": self._job_id,
                "attempt": 1,
                "resource_id": self._artifact_id,
                "generation": 2,
                "digest": digest,
                "size_bytes": len(self._body),
                "media_type": "application/json",
                "relative_path": "RESULT/frequencies/normal_modes.json",
            },
        }

    def read(self, request: Any, *, workspace_id: str) -> dict[str, Any]:
        del workspace_id
        from acp_services.entrypoints.application_params import normalize_read

        # The frozen read validator is the acceptance criterion: the projected
        # ref must pass it verbatim.
        normalized = normalize_read(dict(request))
        self.read_requests.append(normalized)
        ref = dict(normalized["artifact_ref"])
        return {
            "artifact_ref": ref,
            "body": self._body.decode("utf-8"),
            "encoding": "identity",
            "digest": ref["digest"],
            "size_bytes": len(self._body),
            "generation": ref["generation"],
            "truncated": False,
        }


def _server(artifacts: _FakeArtifacts) -> Any:
    from acp_services.entrypoints import mcp

    unused = _Unused()
    return mcp.McpServer(
        backends=mcp.McpBackends(
            capabilities=_FakeCapabilities(),
            preflight=unused,
            submit=unused,
            status=unused,
            cancel=unused,
            artifacts=artifacts,
            analysis=unused,
        )
    )


def _call(server: Any, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )
    assert response is not None and "result" in response, response
    return response["result"]


def test_descriptor_prepare_read_chain_is_copy_paste(tmp_path: Path) -> None:
    from acp_services.entrypoints import mcp
    from acp_services.entrypoints.application_params import (
        ARTIFACT_REF_IDENTITY_FIELDS,
        normalize_artifact_ref,
    )

    task = _write_task(tmp_path)
    body = (task / "RESULT/frequencies/normal_modes.json").read_bytes()
    descriptors_list, _ = descriptors.describe_job_artifacts(task, attempt=1, job_id=_JOB_ID)
    descriptor = descriptors_list[0]

    port = _FakeArtifacts(job_id=_JOB_ID, artifact_id=descriptor["resource_id"], body=body)
    server = _server(port)

    prepared = _call(server, mcp.McpTool.PREPARE_ARTIFACT, dict(descriptor["prepare"]["arguments"]))
    assert prepared["isError"] is False, prepared
    data = prepared["structuredContent"]["data"]
    assert data["read"]["tool"] == mcp.McpTool.READ_ARTIFACT
    projected = data["read"]["arguments"]["artifact_ref"]
    assert set(ARTIFACT_REF_IDENTITY_FIELDS) <= set(projected)
    # And the read validator accepts the projection as-is.
    assert normalize_artifact_ref(projected) == projected

    read = _call(
        server,
        mcp.McpTool.READ_ARTIFACT,
        dict(data["read"]["arguments"]),
    )
    assert read["isError"] is False, read
    assert read["structuredContent"]["data"]["body"] == body.decode("utf-8")
    assert port.read_requests and port.read_requests[0]["artifact_ref"]["task_id"] == _JOB_ID


def test_prepare_without_a_committed_digest_has_no_read_step() -> None:
    """An uncommitted prepare must not advertise a reference that would fail."""

    class _Pending:
        def prepare(self, request: Any, *, idempotency_key: str, workspace_id: str) -> dict:
            del request, idempotency_key, workspace_id
            return {
                "purpose": "artifact",
                "job_id": _JOB_ID,
                "attempt": 1,
                "artifact_id": "normal_modes",
                "operation_ref": {"kind": "operation", "id": "op_pending"},
            }

        def read(self, request: Any, *, workspace_id: str) -> dict:  # pragma: no cover
            raise AssertionError("nothing to read")

    from acp_services.entrypoints import mcp

    result = _call(
        _server(_Pending()),  # type: ignore[arg-type]
        mcp.McpTool.PREPARE_ARTIFACT,
        {"job_id": _JOB_ID, "purpose": "artifact", "artifact_id": "normal_modes"},
    )
    data = result["structuredContent"]["data"]
    assert "read" not in data
    assert data["operation_ref"]["id"] == "op_pending"


@pytest.mark.parametrize("kind", ["manifest_missing", "manifest_unreadable"])
def test_status_level_flow_survives_a_broken_manifest(tmp_path: Path, kind: str) -> None:
    task = tmp_path / "task"
    (task / "RESULT").mkdir(parents=True)
    if kind == "manifest_unreadable":
        (task / "RESULT/result_manifest.json").write_text("{not json", encoding="utf-8")
    items, truncated = descriptors.describe_job_artifacts(task, job_id=_JOB_ID)
    assert items == [] and truncated is False
