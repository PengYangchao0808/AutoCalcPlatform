"""Bounded discovery scopes (round-4 plan, batch A).

Pins the three response scopes of ``acp_capabilities``:

* old no-arg call keeps the historical full shape (compatibility);
* ``detail="summary"`` is a bounded navigation package (< 8 KiB serialized
  UTF-8) whose head carries the discovery protocol;
* ``workflow=<id>`` returns only that contract plus small public refs (never
  the 60+ KiB payload, which made the second read truncated like the first);
* ``not_provided`` entries carry a structured fallback (missing capability,
  supported entries, message).
"""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

_SUMMARY_LIMIT = 8192


def _set_environment(monkeypatch: pytest.MonkeyPatch, run_root: Path) -> None:
    monkeypatch.setenv("ACP_PUBLIC_ORIGIN", "")
    monkeypatch.setenv("ACP_RUN_ROOT", str(run_root))
    monkeypatch.setenv("ACP_DEPLOYMENT_TIER", "local-single")
    monkeypatch.setenv("ACP_MAX_RUNNING", "1")


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    _set_environment(monkeypatch, tmp_path)
    from acp.api.server import create_app

    app = create_app(run_root=tmp_path, max_running=1)
    with TestClient(app) as test_client:
        yield test_client


def _bytes(payload: Any) -> int:
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def test_no_arg_call_keeps_the_historical_full_shape(client: TestClient) -> None:
    body = client.get("/api/capabilities").json()
    # the big blocks old clients rely on are still there
    for key in ("capability_descriptors", "admission", "execution_limits", "limits", "tools"):
        assert key in body, key
    assert body.get("detail") != "summary"
    assert _bytes(body) > 20_000  # this scope is intentionally the full one


def test_explicit_full_matches_the_no_arg_shape(client: TestClient) -> None:
    plain = client.get("/api/capabilities").json()
    full = client.get("/api/capabilities", params={"detail": "full"}).json()
    assert set(full) >= set(plain)


def test_summary_is_bounded_and_heads_the_navigation(client: TestClient) -> None:
    body = client.get("/api/capabilities", params={"detail": "summary"}).json()
    assert body["detail"] == "summary"
    assert _bytes(body) < _SUMMARY_LIMIT, _bytes(body)
    # head: the discovery protocol is the first thing an agent reads
    head = json.dumps(body, ensure_ascii=False)[:2048]
    assert "task_recipe" in head
    assert "do not read platform source" in head
    # the navigation package carries the indexes and the measured capacity
    assert body["navigation"]["workflow_contract"]["arguments"] == {"workflow": "<id>"}
    assert len(body["workflow_contracts"]) == 15
    assert len(body["task_instructions"]) == 5
    assert body["capacity"]["schema"] == "acp2.capacity.view.v1"
    assert len(body["capability_index"]) >= 1
    # and it does NOT smuggle the oversized blocks back in
    assert "capability_descriptors" not in body
    assert "execution_limits" not in body
    assert _bytes(body["admission"]) < 600


def test_workflow_scope_returns_one_contract_only(client: TestClient) -> None:
    body = client.get("/api/capabilities", params={"workflow": "PESsearch"}).json()
    assert body["workflow_contract"]["workflow"] == "PESsearch"
    assert body["workflow_contract"]["coverage"] == "partial"
    # public refs stay; the heavy blocks never ride along
    for key in ("workflow_contract", "workflows", "tools", "protocol_version"):
        assert key in body, key
    for key in ("admission", "capability_descriptors", "task_instructions", "capacity"):
        assert key not in body, key
    assert _bytes(body) < 10_000


def test_workflow_scope_unknown_id_is_typed(client: TestClient) -> None:
    response = client.get("/api/capabilities", params={"workflow": "nope"})
    assert response.status_code == 422
    params = response.json()["detail"]["params"]
    assert params["rule"] == "unknown_workflow"
    assert "PESsearch" in params["allowed"]


def test_not_provided_entries_carry_a_structured_fallback(client: TestClient) -> None:
    index = client.get("/api/capabilities", params={"detail": "summary"}).json()[
        "workflow_contracts"
    ]
    irc = index["irc"]
    assert irc["coverage"] == "not_provided"
    fallback = irc["fallback"]
    assert fallback["status"] == "uncontracted"
    assert "acp run irc" in fallback["supported_entries"]
    assert "report the gap" in fallback["message"]
    # contracted entries carry no fallback
    assert "fallback" not in index["PESsearch"]


def test_invalid_detail_value_is_typed(client: TestClient) -> None:
    response = client.get("/api/capabilities", params={"detail": "verbose"})
    assert response.status_code == 422
    assert response.json()["detail"]["params"]["rule"] == "invalid_enum"


def test_post_body_accepts_detail(client: TestClient) -> None:
    response = client.post("/api/capabilities", json={"detail": "summary"})
    assert response.status_code == 200
    assert response.json()["detail"] == "summary"
