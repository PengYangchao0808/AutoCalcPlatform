# ruff: noqa: F811 - the imported pytest fixture is requested by name
"""Round-9 M4a: an unknown analysis state is never reported as "unavailable".

The external agent saw a contradiction in one discovery surface: with
``detail="summary"`` the analysis capabilities looked unavailable while
``detail="full"`` reported ``executable: true`` for ``vibration.analyze``.  The
root cause was in the MCP adapter: it projected a **freshly constructed empty**
``AnalysisCapabilityRegistry`` and wrote that table into every payload that did
not already carry one.

An adapter cannot read the Controller's application registry, so the honest
answer is ``status: "unknown"`` — never ``unavailable``/``registered: false``,
which claims a negative this layer cannot know.  Pinned here:

* a missing projection is ``status: "unknown"`` with a reason and a pointer to
  ``detail="full"``, in both details (so they cannot contradict each other);
* a provided projection is forwarded (summary keeps the four decision fields,
  full keeps the row), and both details agree per ``analysis_id``;
* the adapter no longer constructs a registry at all;
* over the real Controller, a summary/unknown beside a full/executable is a
  "unknown vs known", not a "unavailable vs executable" contradiction.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from acp_services.entrypoints import mcp
from tests.test_acp2_runtime_mcp_backends import (  # noqa: F401 - pytest fixture
    _LiveController,
    _runtime,
    _tool,
    live_controller,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MCP_PATH = _REPO_ROOT / "src" / "acp_services" / "entrypoints" / "mcp.py"


class _Capabilities:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def capabilities(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(dict(kwargs))
        data = dict(self.payload)
        if kwargs.get("detail") is not None:
            data.setdefault("detail", kwargs["detail"])
        return data


def _server(payload: dict[str, Any]) -> mcp.McpServer:
    return mcp.McpServer(
        backends=mcp.McpBackends(
            capabilities=_Capabilities(payload),
            preflight=object(),  # type: ignore[arg-type]
            submit=object(),  # type: ignore[arg-type]
            status=object(),  # type: ignore[arg-type]
            cancel=object(),  # type: ignore[arg-type]
            artifacts=object(),  # type: ignore[arg-type]
            analysis=object(),  # type: ignore[arg-type]
        )
    )


def _capabilities(server: mcp.McpServer, arguments: dict[str, Any]) -> dict[str, Any]:
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": mcp.McpTool.CAPABILITIES, "arguments": arguments},
        }
    )
    assert response is not None, response
    result = response["result"]
    assert result["isError"] is False, result
    return result["structuredContent"]["data"]["analysis_capabilities"]


# ---------------------------------------------------------------------------
# A missing projection is unknown, in both details
# ---------------------------------------------------------------------------


def test_a_missing_projection_is_unknown_not_unavailable() -> None:
    projection = _capabilities(_server({"workflows": ["singlepoint"]}), {})
    assert isinstance(projection, dict)
    assert projection["status"] == "unknown"
    assert projection["unknown_reason"] == "controller_projection_missing"
    assert projection["source"] == "controller_capabilities"
    assert projection["detail_tool"]["arguments"] == {"detail": "full"}
    assert projection["capability_ids"] == ["vibration.compute", "vibration.analyze"]
    # The fabricated negative is exactly what must not appear.
    encoded = str(projection)
    assert "unavailable" not in encoded
    assert "registered': False" not in encoded


def test_summary_and_full_do_not_contradict_each_other() -> None:
    for detail in ("summary", "full"):
        projection = _capabilities(_server({"workflows": []}), {"detail": detail})
        assert isinstance(projection, dict), detail
        assert projection["status"] == "unknown", detail
    summary = _capabilities(_server({"workflows": []}), {"detail": "summary"})
    full = _capabilities(_server({"workflows": []}), {"detail": "full"})
    assert summary == full


def test_a_workflow_scoped_read_does_not_invent_rows() -> None:
    projection = _capabilities(
        _server({"workflow_contract": {"workflow": "Confsearch"}}),
        {"workflow": "Confsearch"},
    )
    assert projection["status"] == "unknown"


# ---------------------------------------------------------------------------
# A provided projection is forwarded; both details agree per id
# ---------------------------------------------------------------------------


_ROWS = [
    {
        "analysis_id": "vibration.compute",
        "state": "registered",
        "registered": True,
        "executable": False,
        "version": "1.0.0",
        "owner": "A",
    },
    {
        "analysis_id": "vibration.analyze",
        "state": "executable",
        "registered": True,
        "executable": True,
        "version": "1.0.0",
        "owner": "A",
    },
]


def test_a_provided_projection_is_forwarded_and_details_agree_per_id() -> None:
    payload = {"workflows": [], "analysis_capabilities": _ROWS}
    summary = _capabilities(_server(payload), {"detail": "summary"})
    full = _capabilities(_server(payload), {"detail": "full"})

    def decisions(rows: list[dict[str, Any]]) -> dict[str, tuple[Any, Any, Any]]:
        return {
            row["analysis_id"]: (row.get("state"), row.get("registered"), row.get("executable"))
            for row in rows
        }

    assert decisions(summary) == decisions(full)
    # Summary is the bounded view: the four decision fields, no long description.
    assert set(summary[0]) == {"analysis_id", "state", "registered", "executable"}
    # Full keeps the whole row.
    assert full[0]["version"] == "1.0.0"
    assert full[0]["owner"] == "A"


def test_a_declared_status_projection_is_forwarded_unchanged() -> None:
    projection = {"status": "partial", "reason": "plugin probe pending"}
    data = _capabilities(_server({"analysis_capabilities": projection}), {})
    assert data == projection


# ---------------------------------------------------------------------------
# Guard: no second (empty) registry in the adapter
# ---------------------------------------------------------------------------


def test_the_adapter_no_longer_constructs_a_registry() -> None:
    source = _MCP_PATH.read_text(encoding="utf-8")
    assert "AnalysisCapabilityRegistry(" not in source
    assert "capability_projection(" not in source


# ---------------------------------------------------------------------------
# Real Controller: unknown/known, never unavailable/executable
# ---------------------------------------------------------------------------


def _summary_analysis_view(data: dict[str, Any]) -> Any:
    """The summary's analysis view, or ``None`` when the budget clipped it.

    The final-surface budget clips ``analysis_capabilities`` first and records
    the omission explicitly (``omitted`` + ``continuation`` -> ``detail=full``),
    which is also non-contradictory — a missing block is not a negative claim.
    """
    view = data.get("analysis_capabilities")
    if view is None:
        assert "analysis_capabilities" in data.get("omitted", []), data
        assert data["continuation"]["arguments"] == {"detail": "full"}
    return view


def test_real_controller_summary_is_unknown_and_full_is_known(
    live_controller: _LiveController,
) -> None:
    runtime = _runtime(live_controller)
    summary = _tool(runtime, "acp_capabilities", {"detail": "summary"})
    assert summary["isError"] is False, summary
    summary_data = summary["structuredContent"]["data"]
    summary_view = _summary_analysis_view(summary_data)

    full = _tool(runtime, "acp_capabilities", {"detail": "full"})
    assert full["isError"] is False, full
    rows = full["structuredContent"]["data"]["analysis_capabilities"]
    states = {row["analysis_id"]: row["state"] for row in rows}
    assert states.get("vibration.analyze") in {"executable", "registered", "unavailable"}
    # A declared compute capability is never executable on the analysis surface.
    assert states.get("vibration.compute") != "executable"
    if summary_view is not None:
        # A summary that does report the projection must not claim the opposite.
        assert summary_view["status"] == "unknown"
        assert "unavailable" not in str(summary_view)


def test_real_controller_full_and_summary_never_disagree_on_a_reported_state(
    live_controller: _LiveController,
) -> None:
    """If summary forwards rows at all, they must match full state-for-state."""
    runtime = _runtime(live_controller)
    summary = _tool(runtime, "acp_capabilities", {"detail": "summary"})
    full = _tool(runtime, "acp_capabilities", {"detail": "full"})
    summary_view = _summary_analysis_view(summary["structuredContent"]["data"])
    full_rows = full["structuredContent"]["data"]["analysis_capabilities"]
    if isinstance(summary_view, list):
        assert {row["analysis_id"]: row["state"] for row in summary_view} == {
            row["analysis_id"]: row["state"] for row in full_rows
        }
    elif isinstance(summary_view, dict):
        assert summary_view["status"] == "unknown"
        assert summary_view["detail_tool"]["arguments"] == {"detail": "full"}


if __name__ == "__main__":  # pragma: no cover - manual probe
    pytest.main([__file__, "-q"])
