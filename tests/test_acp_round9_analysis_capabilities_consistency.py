"""Round-9 M4a (Controller side): one analysis-capability answer per scope.

The full capability payload publishes the frozen rows
(``analysis_id``/``state``/``registered``/``executable``/``version``/``owner``).
The bounded ``detail=summary`` payload must not contradict them: it publishes
the *same ids and the same states* in a compact ``id -> state`` table (the
navigation scope's contract) instead of a fabricated ``unavailable`` /
``registered: false``.  Both projections read the one injected application
registry — never a fresh empty one — and ``vibration.compute`` stays
``registered: true, executable: false``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from acp_services.runtime.analysis_capabilities import AnalysisCapabilityRegistry
from tests.test_acp_api_v1 import make_client


@pytest.fixture()
def client(tmp_path, monkeypatch: pytest.MonkeyPatch):
    with make_client(tmp_path, monkeypatch, max_running=1) as test_client:
        yield test_client


def _full(client: TestClient) -> dict[str, Any]:
    response = client.get("/api/capabilities")
    assert response.status_code == 200, response.text
    return response.json()


def _summary(client: TestClient) -> dict[str, Any]:
    response = client.get("/api/capabilities", params={"detail": "summary"})
    assert response.status_code == 200, response.text
    return response.json()


def _full_states(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row["analysis_id"]): dict(row) for row in body["analysis_capabilities"]}


def test_summary_and_full_publish_the_same_analysis_states(client: TestClient) -> None:
    full = _full(client)
    summary = _summary(client)
    rows = _full_states(full)
    assert rows, "the live Controller must project its analysis registry"
    assert summary["analysis_capabilities"] == {
        analysis_id: row["state"] for analysis_id, row in rows.items()
    }
    # both scopes enumerate the same ids (the registry's set, no fabrication)
    assert set(summary["analysis_capabilities"]) == set(rows)


def test_registered_capabilities_are_never_reported_unavailable(
    client: TestClient,
) -> None:
    rows = _full_states(_full(client))
    summary = _summary(client)["analysis_capabilities"]
    assert rows["vibration.compute"]["state"] == "registered"
    assert rows["vibration.compute"]["registered"] is True
    assert rows["vibration.compute"]["executable"] is False
    assert rows["vibration.analyze"]["executable"] is True
    assert "unavailable" not in summary.values()
    assert all(row["state"] != "unavailable" for row in rows.values())


def test_summary_points_at_the_full_rows(client: TestClient) -> None:
    summary = _summary(client)
    assert summary["navigation"]["capability_detail"] == {
        "tool": "acp_capabilities",
        "arguments": {"detail": "full"},
    }


def test_both_scopes_read_the_injected_registry(client: TestClient) -> None:
    """Mutating the injected registry changes both projections together."""
    registry = client.app.state.analysis_registry
    assert isinstance(registry, AnalysisCapabilityRegistry)
    capability = registry.get("vibration.analyze")
    assert capability is not None
    registry.unregister("vibration.analyze")
    try:
        full_rows = _full_states(_full(client))
        summary = _summary(client)["analysis_capabilities"]
        assert full_rows["vibration.analyze"]["state"] == "unavailable"
        assert full_rows["vibration.analyze"]["registered"] is False
        assert summary["vibration.analyze"] == "unavailable"
        # the other capability is untouched: this is the registry, not a table
        assert summary["vibration.compute"] == "registered"
    finally:
        registry.register(capability)
    restored = _full_states(_full(client))
    assert restored["vibration.analyze"]["state"] == "executable"
    assert _summary(client)["analysis_capabilities"]["vibration.analyze"] == "executable"


def test_a_controller_without_a_registry_is_declared_unavailable_by_both_scopes() -> None:
    """No injected registry is a process fact, not an "unknown" capability.

    ``unavailable`` here is the honest answer (this process wired no analysis),
    and both scopes derive it from the same projection.  The bounded scope must
    say ``status: "unknown" + detail_hint`` only when it cannot afford to
    compute the projection at all — never fabricate a negative state.
    """
    from acp.api import routes, science_catalog

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    summary = routes._analysis_capabilities_summary(request)
    full = {
        str(row["analysis_id"]): str(row["state"])
        for row in science_catalog.analysis_capabilities(SimpleNamespace())
    }
    assert summary == full
    assert set(summary.values()) == {"unavailable"}
