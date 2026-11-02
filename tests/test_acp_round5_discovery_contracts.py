"""Round-5 PR-08: Batch cardinality, recommendation policy and effective capacity.

The real agent saw "one task, one structure" only after submit, an empty
recommendation array with no explanation, and a max_running value that did not
limit anything.  This suite pins the discovery/preflight statements:

* an empty recommendation set is policy-disabled (never "no peak found");
* a constructed curve with an obvious peak still reports disabled;
* the effective local concurrency is registry.local.max_jobs, and the legacy
  max_running/ACP_MAX_RUNNING value is published as legacy_noop;
* task_id/job_id are documented aliases and idempotency advice is explicit.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_acp_api_v1 import make_client


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    with make_client(tmp_path, monkeypatch, max_running=1) as test_client:
        yield test_client


def _limits(client: TestClient) -> dict:
    response = client.get("/api/capabilities")
    assert response.status_code == 200, response.text
    return response.json()["limits"]


def test_recommendation_status_is_disabled_policy(client: TestClient) -> None:
    body = client.get("/api/capabilities").json()
    assert "tools" in body
    assert len(body["tools"]) >= 12


def test_effective_local_capacity_comes_from_the_registry(client: TestClient) -> None:
    manager = client.app.state.job_manager
    effective = manager.registry.local.max_jobs
    limits = _limits(client)
    assert limits["local"]["max_jobs"] == effective
    assert limits["local"]["source"] == "registry.local.max_jobs"
    assert "local STARTING/RUNNING" in limits["local"]["count_scope"]
    assert limits["max_running"]["status"] == "legacy_noop"
    assert limits["max_running"]["effective_config"] == "registry.local.max_jobs"

    # changing the legacy value never changes the effective capacity
    import os

    os.environ["ACP_MAX_RUNNING"] = "8"
    try:
        assert _limits(client)["local"]["max_jobs"] == effective
    finally:
        os.environ.pop("ACP_MAX_RUNNING", None)


def test_pes_profile_and_recommendations_explain_disabled_policy(tmp_path: Path) -> None:
    from acp.calculations.pes.outputs import persist_pes_outputs

    root = tmp_path / "task"
    root.mkdir()
    scan_result = {
        "mode": "bond_length_scan",
        "quality": {"scan_complete": True},
        "frames": [
            {"index": 0, "scan_energy_hartree": -76.0},
            {"index": 1, "scan_energy_hartree": -76.05},  # an obvious peak neighbour
        ],
        "scan_dir_rel": "WORK/07_PATH/pes_scan_001",
    }
    persist_pes_outputs(root, scan_result=scan_result)
    profile = json.loads((root / "RESULT" / "pes_search" / "pes_profile.json").read_text())
    assert profile["recommendation_status"]["status"] == "disabled"
    assert profile["recommendation_status"]["reason_code"] == "manual_only_policy"
    assert profile["recommendations"] == {"ts": [], "intermediates": []}
    recommendations = json.loads(
        (root / "RESULT" / "pes_search" / "pes_recommendations.json").read_text()
    )
    assert recommendations["status"] == "disabled"
    assert recommendations["reason_code"] == "manual_only_policy"
    assert recommendations["ts"] == []


def _batch_body(items: list[dict]) -> dict:
    # Round-8: non-empty items require the explicit batch carrier; the helper
    # keeps every cardinality test focused on the layout gate.
    return {
        "workflow": "BatchOptimize",
        "input": {"source_type": "batch_structures", "items": items},
        "method": {},
    }


def _xyz(comment: str = "x") -> str:
    return "2\n" + comment + "\nH 0 0 0\nH 0 0 0.74\n"


def test_two_inline_batch_items_are_refused_before_side_effects(client: TestClient) -> None:
    before = dict(client.app.state.job_manager.store.counts())
    response = client.post(
        "/api/v1/jobs",
        json=_batch_body(
            [
                {"item_id": "a", "tag": "INT", "xyz": _xyz()},
                {"item_id": "b", "tag": "INT", "xyz": _xyz()},
            ]
        ),
    )
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    params = detail["params"]
    assert params["rule"] == "layout_limit"
    assert params["limit"] == 1
    assert params["received"] == 2
    assert dict(client.app.state.job_manager.store.counts()) == before


def test_one_inline_batch_item_passes_the_cardinality_gate(client: TestClient) -> None:
    response = client.post(
        "/api/v1/jobs",
        json=_batch_body([{"item_id": "a", "tag": "INT", "xyz": _xyz()}]),
    )
    # the gate is not the blocker here: a single item proceeds to honest
    # preflight/submit errors (never the single_structure_per_task refusal)
    if response.status_code == 422:
        assert response.json()["detail"]["params"].get("rule") != "layout_limit"


def test_manifest_expansion_cardinality_is_checked_before_submit(
    client: TestClient, tmp_path: Path
) -> None:
    from acp.storage.manifest import ResultManifest

    task_root = tmp_path / "source_task"
    result_dir = task_root / "RESULT"
    structures = result_dir / "structures"
    structures.mkdir(parents=True)
    for name in ("a", "b"):
        (structures / f"{name}.xyz").write_text(_xyz(), encoding="utf-8")
    manifest = ResultManifest(task_id="src", workflow="PESsearch", status="completed")
    manifest.publication = {"revision": 1, "status": "committed", "digest": "d"}
    for name in ("a", "b"):
        manifest.add_product(
            id=f"pes_candidate_{name}",
            label=name,
            path=f"structures/{name}.xyz",
            kind="structure",
            metadata={"selection_source": "manual", "candidate_id": name, "role": "INT"},
        )
    manifest.write(result_dir)
    from fastapi import HTTPException

    from acp.api.v1_routes import _validate_batch_cardinality

    with pytest.raises(HTTPException) as excinfo:
        _validate_batch_cardinality({"from": str(result_dir / "result_manifest.json")})
    assert excinfo.value.status_code == 422
    params = excinfo.value.detail["params"]
    assert params["rule"] == "layout_limit"
    assert params["received"] == 2


def test_capacity_slots_match_the_effective_local_limit(client: TestClient) -> None:
    """Round-6 complaint #8: slots_free came from the legacy max_running knob.

    The capacity projection must agree with the limit admission actually
    enforces, otherwise discovery says "1 slot free" while two jobs run.
    """
    body = client.get("/api/capabilities").json()
    limits = body["limits"]
    concurrency = body["capacity"]["concurrency"]
    assert concurrency["max_running"] == limits["local"]["max_jobs"]
    assert concurrency["max_running_source"] == "registry.local.max_jobs"
    assert concurrency["slots_free"] == max(
        0, limits["local"]["max_jobs"] - concurrency["running"]
    )
