"""Round-9 M11: one ``max_running`` authority across the discovery surfaces.

Round-5 PR-08 published ``limits.max_running = {"status": "legacy_noop",
"effective_config": ...}`` while the capacity block published the effective
integer, so a client saw two mutually exclusive answers for the same knob.
Round-9 M11 replaces that with one explicit projection:

``limits.max_running = {"value": <int>, "source": "registry.local.max_jobs",
"legacy_env": "ACP_MAX_RUNNING", "legacy_in_effect": false}``

and ``capacity.concurrency.max_running`` / ``max_running_source`` must carry the
same value and the same source.  ``limits.local.max_jobs`` stays the authority,
``detail=summary`` and ``detail=full`` publish the identical limits block, and
the legacy environment value never changes any of it.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.test_acp_api_v1 import make_client


@pytest.fixture()
def client(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ACP_MAX_RUNNING", raising=False)
    with make_client(tmp_path, monkeypatch, max_running=2) as test_client:
        yield test_client


def _full(client: TestClient) -> dict[str, Any]:
    response = client.get("/api/capabilities")
    assert response.status_code == 200, response.text
    return response.json()


def _summary(client: TestClient) -> dict[str, Any]:
    response = client.get("/api/capabilities", params={"detail": "summary"})
    assert response.status_code == 200, response.text
    return response.json()


def test_limits_publish_one_effective_max_running(client: TestClient) -> None:
    manager = client.app.state.job_manager
    effective = manager.registry.local.max_jobs
    limits = _full(client)["limits"]
    assert limits["local"]["max_jobs"] == effective
    assert limits["local"]["source"] == "registry.local.max_jobs"
    assert limits["max_running"] == {
        "value": effective,
        "source": "registry.local.max_jobs",
        "legacy_env": "ACP_MAX_RUNNING",
        "legacy_in_effect": False,
    }
    # the mutually exclusive legacy block is gone
    assert "status" not in limits["max_running"]
    assert "effective_config" not in limits["max_running"]


def test_capacity_concurrency_carries_the_same_value_and_source(
    client: TestClient,
) -> None:
    body = _full(client)
    limits = body["limits"]
    concurrency = body["capacity"]["concurrency"]
    assert concurrency["max_running"] == limits["max_running"]["value"]
    assert concurrency["max_running"] == limits["local"]["max_jobs"]
    assert concurrency["max_running_source"] == limits["max_running"]["source"]
    assert concurrency["slots_free"] == max(
        0, int(limits["max_running"]["value"]) - int(concurrency["running"])
    )


def test_legacy_environment_value_never_changes_the_effective_limit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    effective = client.app.state.job_manager.registry.local.max_jobs
    monkeypatch.setenv("ACP_MAX_RUNNING", "17")
    body = _full(client)
    assert body["limits"]["max_running"]["value"] == effective
    assert body["limits"]["max_running"]["legacy_in_effect"] is False
    assert body["limits"]["local"]["max_jobs"] == effective
    assert body["capacity"]["concurrency"]["max_running"] == effective


def test_summary_and_full_publish_the_same_limits_projection(
    client: TestClient,
) -> None:
    full = _full(client)
    summary = _summary(client)
    assert summary["limits"] == full["limits"]
    assert (
        summary["capacity"]["concurrency"]["max_running"]
        == full["capacity"]["concurrency"]["max_running"]
        == full["limits"]["max_running"]["value"]
    )


def test_effective_max_running_projection_has_one_fallback_chain() -> None:
    from acp.api.capacity import effective_max_running

    registry_manager = SimpleNamespace(
        registry=SimpleNamespace(local=SimpleNamespace(max_jobs=6)),
        max_running=2,
    )
    assert effective_max_running(registry_manager) == (6, "registry.local.max_jobs")

    legacy_manager = SimpleNamespace(registry=None, max_running=3)
    assert effective_max_running(legacy_manager) == (3, "manager.max_running")

    assert effective_max_running(SimpleNamespace(registry=None, max_running=0)) == (None, "")
    # a boolean is never a concurrency limit
    broken = SimpleNamespace(
        registry=SimpleNamespace(local=SimpleNamespace(max_jobs=True)), max_running=None
    )
    assert effective_max_running(broken) == (None, "")
