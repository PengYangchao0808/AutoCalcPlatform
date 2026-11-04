# ruff: noqa: F811 - the imported pytest fixture is requested by name
"""Round-9 M10: an operation reference is never double-serialised.

The external agent's P2: ``acp_prepare_artifact`` returned
``{"kind": "operation", "id": "{'kind': 'operation', 'id': 'op_...'}"}``, so the
reference could not be sent back to ``acp_status``/``acp_cancel``.  Two points
are pinned here:

* the Producer (``acp.services_mcp_bootstrap._ControllerArtifacts.prepare``)
  passes the Controller's ``operation_ref`` Mapping through instead of
  ``str()``-ing it;
* the Consumer (``acp_services.entrypoints.mcp._reference_from`` /
  ``_normalise_reference``) parses a Mapping that arrives as a ``dict`` repr or
  JSON string, defensively and boundedly, so an old/foreign adapter cannot
  poison the id.

The closure is proven over the real Controller: prepare -> the returned
reference -> ``acp_status`` resolves the operation.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from tests.test_acp2_runtime_mcp_backends import (  # noqa: F401 - pytest fixture
    _client,
    _LiveController,
    _runtime,
    _tool,
    live_controller,
)

PROJECT = "uncategorized"
JOB_ID = "20261102_130000_000_frequency"
ATTEMPT = 1
ARTIFACT_ID = "normal_modes"
RELATIVE_PATH = "RESULT/frequencies/normal_modes.json"
PRODUCT_BODY = b'{"frequencies_cm1": [4401.2]}\n'


# ---------------------------------------------------------------------------
# Consumer: _reference_from / _normalise_reference
# ---------------------------------------------------------------------------


def test_mapping_reference_passes_through_as_a_mapping() -> None:
    from acp_services.entrypoints.mcp import _reference_from

    reference = _reference_from(
        {"reference": {"kind": "operation", "id": "op_abc123"}}, kind="operation"
    )
    assert reference == {"kind": "operation", "id": "op_abc123"}


def test_dict_repr_reference_is_parsed_instead_of_becoming_the_id() -> None:
    from acp_services.entrypoints.mcp import _reference_from

    record = {"reference": "{'kind': 'operation', 'id': 'op_repr'}"}
    reference = _reference_from(record, kind="operation")
    assert reference == {"kind": "operation", "id": "op_repr"}
    assert "{" not in reference["id"]
    assert "'" not in reference["id"]


def test_json_reference_string_is_parsed_too() -> None:
    from acp_services.entrypoints.mcp import _reference_from

    reference = _reference_from(
        {"reference": '{"kind": "operation", "id": "op_json"}'}, kind="operation"
    )
    assert reference == {"kind": "operation", "id": "op_json"}


def test_plain_string_id_is_kept_verbatim() -> None:
    from acp_services.entrypoints.mcp import _reference_from

    assert _reference_from({"reference": "op_plain"}, kind="operation") == {
        "kind": "operation",
        "id": "op_plain",
    }
    assert _reference_from({"job_id": "20261102_130000_000_frequency"}, kind="job") == {
        "kind": "job",
        "id": "20261102_130000_000_frequency",
    }


def test_a_non_mapping_record_that_is_a_dict_repr_is_parsed() -> None:
    from acp_services.entrypoints.mcp import _reference_from

    assert _reference_from("{'kind': 'operation', 'id': 'op_raw'}", kind="operation") == {
        "kind": "operation",
        "id": "op_raw",
    }


def test_an_overlong_braced_string_is_kept_verbatim_not_parsed() -> None:
    """The parser is bounded: a huge braced blob is not a reference mapping."""
    from acp_services.entrypoints.mcp import _REFERENCE_STRING_MAX, _normalise_reference

    text = "{" + "x" * (_REFERENCE_STRING_MAX + 1) + "}"
    assert _normalise_reference(text) == text


def test_a_broken_braced_string_is_kept_verbatim() -> None:
    from acp_services.entrypoints.mcp import _normalise_reference

    text = "{'kind': 'operation', 'id': }"
    assert _normalise_reference(text) == text


def test_cancel_acknowledgement_normalises_the_echoed_reference() -> None:
    from acp_services.entrypoints.mcp import _cancel_acknowledgement

    acknowledgement = _cancel_acknowledgement(
        {
            "status": "cancelled",
            "reference": "{'kind': 'operation', 'id': 'op_repr'}",
        },
        "op_repr",
    )
    assert acknowledgement["reference"] == {"kind": "operation", "id": "op_repr"}


# ---------------------------------------------------------------------------
# Producer: the adapter passes the Controller's mapping through
# ---------------------------------------------------------------------------


class _FakeControllerClient:
    def __init__(self, record: dict[str, Any]) -> None:
        self.record = record

    def prepare(self, payload: dict[str, Any]) -> dict[str, Any]:
        return dict(self.record)


def test_adapter_prepare_keeps_the_operation_ref_mapping() -> None:
    from acp.services_mcp_bootstrap import _ControllerArtifacts

    artifacts = _ControllerArtifacts(
        _FakeControllerClient(
            {
                "operation_id": "op_abc",
                "operation_ref": {"kind": "operation", "id": "op_abc"},
            }
        )
    )
    payload = artifacts.prepare(
        {"purpose": "artifact", "job_id": JOB_ID, "artifact_id": ARTIFACT_ID},
        idempotency_key="",
        workspace_id="ws",
    )
    assert payload["operation_ref"] == {"kind": "operation", "id": "op_abc"}
    assert payload["reference"] == {"kind": "operation", "id": "op_abc"}


def test_adapter_prepare_still_projects_a_bare_operation_id() -> None:
    from acp.services_mcp_bootstrap import _ControllerArtifacts

    artifacts = _ControllerArtifacts(_FakeControllerClient({"operation_id": "op_bare"}))
    payload = artifacts.prepare(
        {"purpose": "artifact", "job_id": JOB_ID, "artifact_id": ARTIFACT_ID},
        idempotency_key="",
        workspace_id="ws",
    )
    assert payload["reference"] == {"kind": "operation", "id": "op_bare"}


# ---------------------------------------------------------------------------
# Closure over the real Controller: prepare -> status
# ---------------------------------------------------------------------------


def _seed_job(live: _LiveController) -> None:
    from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus

    work_dir = live.manager.run_root / PROJECT / f"{JOB_ID}-{ATTEMPT}"
    (work_dir / "RESULT" / "frequencies").mkdir(parents=True, exist_ok=True)
    (work_dir / RELATIVE_PATH).write_bytes(PRODUCT_BODY)
    live.manager.store.create(
        JobRecord(
            id=JOB_ID,
            spec=JobSpec(workflow="frequency", name=JOB_ID, project_id=PROJECT),
            status=JobStatus.COMPLETED,
            work_dir=str(work_dir),
            attempt=ATTEMPT,
            project_id=PROJECT,
        )
    )


def _prepare_over_mcp(live: _LiveController) -> dict[str, Any]:
    runtime = _runtime(live)
    result = _tool(
        runtime,
        "acp_prepare_artifact",
        {
            "purpose": "artifact",
            "job_id": JOB_ID,
            "artifact_id": ARTIFACT_ID,
            "attempt": ATTEMPT,
            "generation": 1,
            "relative_path": RELATIVE_PATH,
        },
    )
    assert result["isError"] is False, result
    return result["structuredContent"]["data"]


def test_prepare_reference_is_usable_by_status_over_the_real_controller(
    live_controller: _LiveController,
) -> None:
    _seed_job(live_controller)
    prepared = _prepare_over_mcp(live_controller)

    reference = prepared["reference"]
    assert isinstance(reference, dict), reference
    assert reference["kind"] == "operation"
    assert "{" not in str(reference["id"])
    assert "'" not in str(reference["id"])

    deadline = time.monotonic() + 10.0
    data: dict[str, Any] = {}
    runtime = _runtime(live_controller)
    while time.monotonic() < deadline:
        result = _tool(runtime, "acp_status", {"reference": reference})
        assert result["isError"] is False, result
        data = result["structuredContent"]["data"]
        if data.get("state") in {"ready", "failed", "cancelled"}:
            break
        time.sleep(0.01)
    assert data.get("state") == "ready", data
    # The status projection echoes the id it resolved, never the mapping repr.
    assert str(data["reference"]) == reference["id"]


def test_a_stringified_reference_stays_an_opaque_id_on_the_input_surface(
    live_controller: _LiveController,
) -> None:
    """Round-8 K6 is preserved: inputs are not reinterpreted, only outputs are.

    Round-9 stopped *producing* a ``dict`` repr id; it deliberately does not
    decode a client-supplied stringified reference back into a real operation
    (that would make two distinct wire values alias one task).
    """
    _seed_job(live_controller)
    prepared = _prepare_over_mcp(live_controller)
    operation_id = str(prepared["operation_ref"]["id"])
    stringified = "{" + f"'kind': 'operation', 'id': '{operation_id}'" + "}"

    runtime = _runtime(live_controller)
    result = _tool(runtime, "acp_status", {"reference": stringified})
    assert result["isError"] is True, result
    error = result["structuredContent"]["error"]
    assert error["code"] == "not_found"
    assert error["retryable"] is False


def test_http_operation_response_is_the_source_of_truth(
    live_controller: _LiveController,
) -> None:
    """The Controller already answers ``{kind, id}``; MCP must not reshape it."""
    _seed_job(live_controller)
    prepared = _prepare_over_mcp(live_controller)
    operation_id = str(prepared["operation_ref"]["id"])
    deadline = time.monotonic() + 10.0
    operation: dict[str, Any] = {}
    while time.monotonic() < deadline:
        operation = dict(_client(live_controller).operation(operation_id))
        if operation.get("state") in {"ready", "failed", "cancelled"}:
            break
        time.sleep(0.01)
    assert operation.get("state") == "ready", operation
    assert operation["operation_ref"] == prepared["operation_ref"]


def test_status_rejects_a_bare_dict_repr_id() -> None:
    """Defence in depth: an unparsable reference is still a typed refusal."""
    from acp_services.entrypoints.mcp import McpToolError, _require_reference

    with pytest.raises(McpToolError) as excinfo:
        _require_reference({"reference": {"kind": "operation", "id": "  "}})
    assert excinfo.value.params["field"] == "reference"
