# ruff: noqa: F811 - the imported pytest fixture is requested by name
"""Round-7 #7e: ``acp_cancel`` returns a compact ack, not the Controller row.

Complaint: the MCP cancel result was ~50 KB because it echoed the whole
``V1JobRecordModel`` (xyz spec, admission, provenance, artifact descriptors).
The fix projects the MCP surface to identity/state facts only; the HTTP route
is untouched.  This test runs the **real** Controller (uvicorn on a loopback
port, real ``JobManager``/``JobStore``) and the real
``acp.services_mcp_bootstrap`` composition, so the projection is exercised
against the actual cancel record instead of a hand-trimmed fake.

No science runs: the B06 harness disables the submission thread, so the job
stays ``QUEUED`` and the cancel is a synchronous control transition.
"""

from __future__ import annotations

import json
from typing import Any

from tests.test_acp2_runtime_mcp_backends import (  # noqa: F401 - pytest fixture
    _SUBMIT,
    _client,
    _LiveController,
    _runtime,
    _tool,
    live_controller,
)

_CANCEL_KEYS = {
    "job_id",
    "attempt",
    "reference",
    "state",
    "status",
    "cancelled",
    "already_terminal",
    "terminal_state",
    "cancel_requested",
    "updated_at",
    "spec_ref",
    "trace_ref",
}


def _submit(live: _LiveController) -> dict[str, Any]:
    return dict(
        _client(live).submit(
            {
                **_SUBMIT,
                "name": "round7-cancel",
                "idempotency_key": "round7-cancel-0001",
            }
        )
    )


def test_acp_cancel_returns_a_compact_ack_over_the_real_controller(
    live_controller: _LiveController,
) -> None:
    created = _submit(live_controller)
    job_id = created["job_id"]

    result = _tool(_runtime(live_controller), "acp_cancel", {"reference": job_id})
    assert result["isError"] is False
    data = result["structuredContent"]["data"]

    assert set(data) <= _CANCEL_KEYS
    assert data["job_id"] == job_id
    assert data["reference"] == job_id
    assert data["attempt"] == 1
    assert data["state"] in {"cancelled", "cancelling"}
    assert data["cancelled"] is True
    assert data["cancel_requested"] is True
    serialized = json.dumps(result, ensure_ascii=False)
    for withheld in ("spec", "xyz_text", "admission", "provenance", "artifacts"):
        assert withheld not in serialized
    assert len(serialized) < 4096


def test_http_cancel_route_still_returns_the_full_record(
    live_controller: _LiveController,
) -> None:
    """The fix is MCP-only: the HTTP route keeps its ``V1JobRecordModel``."""
    created = _submit(live_controller)
    job_id = created["job_id"]

    http_record = dict(_client(live_controller).cancel(job_id))
    assert http_record["id"] == job_id
    # The full job row (and the spec body) is still what HTTP answers with.
    assert isinstance(http_record.get("spec"), dict)
    assert http_record["spec"]["workflow"] == "singlepoint"
    assert http_record["status"] in {"cancelled", "cancelling"}
