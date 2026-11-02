"""Round-9 M3: the bounded local-single ``agent-self`` publish path.

A pure-MCP session on a ``local-single`` deployment has no administrator to
mint an authorization, so an agent publication used to be a permanent typed
refusal.  The Controller may now mint one bounded record for its own trusted
operator entry and record the grant; everything else keeps the strict path:

* the record is ``origin="agent-self"``, one job/attempt, one action, at most
  ``SELECTION_SELF_SIGNED_MAX_CANDIDATES`` candidates and a short TTL, stored in
  the **same** selection store (no second store) and consumed through the
  normal validate/consume path;
* a caller-supplied ``authorization_id`` is never replaced by a self-signed one;
* an authenticated API-key principal, a non-``local-single`` tier and
  ``ACP_SELECTION_AUTOPUBLISH=0`` keep the typed refusal;
* the response says ``authorization_origin`` and the job event stream records
  ``selection.self_signed``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from acp_services.contracts.access import LOCAL_PRINCIPAL_ID, Principal, PrincipalKind
from acp_services.contracts.selection import (
    SELECTION_AUTHORIZATION_SCHEMA,
    SELECTION_SELF_SIGNED_MAX_CANDIDATES,
    SELECTION_SELF_SIGNED_ORIGIN,
    SELECTION_SELF_SIGNED_POLICY_REVISION,
    SELECTION_SELF_SIGNED_TTL_SECONDS,
    SelectionAuthorization,
    SelectionLimits,
)
from acp_services.data.research_resources import ResearchResourceStore
from acp_services.runtime.selection_authorization import (
    SelectionAuthorizationService,
    authorization_resource_id,
)
from acp_services.workspace.command_identity import (
    CommandIdentity,
    PermissionScope,
    SubjectKind,
)
from acp_services.workspace.server_identity import ServerIdentity
from tests.test_acp_api_pes_review import _make_pes_task, _register_job
from tests.test_acp_api_v1 import make_client

JOB_ID = "20260903_001_PESsearch"


@pytest.fixture()
def api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ACP_SELECTION_AUTOPUBLISH", raising=False)
    with make_client(tmp_path, monkeypatch, max_running=1) as test_client:
        yield test_client


def _register(api_client: TestClient, tmp_path: Path, *, job_id: str = JOB_ID) -> str:
    work_dir = _make_pes_task(tmp_path / f"tree_{job_id}")
    return _register_job(api_client, tmp_path, work_dir, job_id=job_id)


def _publish_body(
    *, job_id: str = JOB_ID, candidates: list[dict[str, Any]] | None = None, **extra: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "candidates": candidates
        if candidates is not None
        else [
            {"frame_index": 1, "role": "TS"},
            {"frame_index": 2, "role": "INT"},
        ],
        "selector": "agent",
        "source_binding": {"job_id": job_id, "attempt": 1},
        "idempotency_key": "round9-self-signed-1",
    }
    body.update(extra)
    return body


def _authorization_rows(api_client: TestClient) -> list[Any]:
    store: ResearchResourceStore = api_client.app.state.research_resources
    return store.list_heads(
        project_id="uncategorized",
        schema=SELECTION_AUTHORIZATION_SCHEMA,
        limit=100,
    )


def _self_signed_rows(api_client: TestClient) -> list[Any]:
    return [
        row
        for row in _authorization_rows(api_client)
        if str(row.document.get("origin") or "") == SELECTION_SELF_SIGNED_ORIGIN
    ]


def _events(api_client: TestClient, job_id: str) -> list[dict[str, Any]]:
    log = api_client.app.state.job_manager.event_log(job_id)
    assert log is not None
    return log.read_all()


def _stored_authorization_id(api_client: TestClient, job_id: str) -> str:
    from acp.calculations.pes.review import load_pes_review

    work_dir = Path(api_client.app.state.job_manager.get(job_id).work_dir)
    review = load_pes_review(work_dir)
    assert review is not None
    return str(review["authorization"]["authorization_id"])


# --------------------------------------------------------------------------- #
# the recorded self-signed path
# --------------------------------------------------------------------------- #


def test_local_single_publish_without_authorization_mints_and_records(
    api_client: TestClient, tmp_path: Path
) -> None:
    from acp.calculations.pes.review import load_pes_review

    job_id = _register(api_client, tmp_path)
    response = api_client.post(f"/api/v1/jobs/{job_id}/pes/review", json=_publish_body())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["revision"] == 1
    assert body["publication"]["status"] == "published"
    # the response says where the consumed authority came from
    assert body["publication"]["authorization_origin"] == "agent-self"

    work_dir = Path(api_client.app.state.job_manager.get(job_id).work_dir)
    review = load_pes_review(work_dir)
    assert review is not None
    assert review["selector"] == "agent"
    authorization = review["authorization"]
    assert authorization["authorization_origin"] == SELECTION_SELF_SIGNED_ORIGIN
    assert authorization["policy_revision"] == SELECTION_SELF_SIGNED_POLICY_REVISION
    authorization_id = str(authorization["authorization_id"])
    assert authorization_id.startswith("self-")

    # the durable record lives in the existing selection store
    record = api_client.app.state.research_resources.read(
        authorization_resource_id(authorization_id),
        principal_id=LOCAL_PRINCIPAL_ID,
        project_id="uncategorized",
    )
    document = record.document
    assert document["origin"] == SELECTION_SELF_SIGNED_ORIGIN
    assert document["policy_revision"] == SELECTION_SELF_SIGNED_POLICY_REVISION
    assert document["source_job_id"] == job_id
    assert document["allowed_attempts"] == [1]
    assert document["actions"] == ["publish_pes_selection"]
    assert document["workflow"] == "PESsearch"
    assert document["limits"]["max_candidates"] == 2
    assert document["limits"]["used_candidates"] == 2
    valid_until = datetime.fromisoformat(document["valid_until"])
    remaining = (valid_until - datetime.now(timezone.utc)).total_seconds()
    assert 0 < remaining <= SELECTION_SELF_SIGNED_TTL_SECONDS + 5

    # one audit event, bound to the same job/attempt/authorization
    events = [
        event for event in _events(api_client, job_id) if event["type"] == "selection.self_signed"
    ]
    assert len(events) == 1
    event = events[0]
    assert event["job_id"] == job_id
    assert event["authorization_id"] == authorization_id
    assert event["attempt"] == 1
    assert [row["frame_index"] for row in event["candidates"]] == [1, 2]
    assert [row["role"] for row in event["candidates"]] == ["TS", "INT"]


def test_each_publication_mints_its_own_single_use_record(
    api_client: TestClient, tmp_path: Path
) -> None:
    job_id = _register(api_client, tmp_path)
    first = api_client.post(f"/api/v1/jobs/{job_id}/pes/review", json=_publish_body())
    assert first.status_code == 200, first.text
    second = api_client.post(
        f"/api/v1/jobs/{job_id}/pes/review",
        json=_publish_body(
            candidates=[{"frame_index": 0, "role": "TS"}],
            idempotency_key="round9-self-signed-2",
        ),
    )
    assert second.status_code == 200, second.text
    assert second.json()["revision"] == 2
    ids = {row.document["authorization_id"] for row in _self_signed_rows(api_client)}
    assert len(ids) == 2
    # each record consumed exactly its own publication
    for row in _self_signed_rows(api_client):
        used = int(row.document["limits"]["used_candidates"])
        max_candidates = int(row.document["limits"]["max_candidates"])
        assert used == max_candidates


def test_candidate_cap_is_enforced_by_the_minted_record(
    api_client: TestClient, tmp_path: Path
) -> None:
    job_id = _register(api_client, tmp_path)
    too_many = [
        {"frame_index": index % 3, "role": "TS"}
        for index in range(SELECTION_SELF_SIGNED_MAX_CANDIDATES + 1)
    ]
    response = api_client.post(
        f"/api/v1/jobs/{job_id}/pes/review", json=_publish_body(candidates=too_many)
    )
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "quota_exceeded"
    # the grant is bounded even when minted: the cap, not the request size
    assert [row.document["limits"]["max_candidates"] for row in _self_signed_rows(api_client)] == [
        SELECTION_SELF_SIGNED_MAX_CANDIDATES
    ]


def test_self_signed_record_is_bound_to_its_source_job(
    api_client: TestClient, tmp_path: Path
) -> None:
    job_id = _register(api_client, tmp_path)
    other_job = _register(api_client, tmp_path, job_id="20260903_002_PESsearch")
    first = api_client.post(f"/api/v1/jobs/{job_id}/pes/review", json=_publish_body())
    assert first.status_code == 200, first.text
    assert first.json()["publication"]["authorization_origin"] == "agent-self"
    authorization_id = _stored_authorization_id(api_client, job_id)

    replay = api_client.post(
        f"/api/v1/jobs/{other_job}/pes/review",
        json=_publish_body(job_id=other_job, authorization_id=authorization_id),
    )
    assert replay.status_code == 403, replay.text
    assert replay.json()["detail"]["code"] == "source_mismatch"


# --------------------------------------------------------------------------- #
# the guardrails stay
# --------------------------------------------------------------------------- #


def test_caller_supplied_authorization_is_never_replaced(
    api_client: TestClient, tmp_path: Path
) -> None:
    job_id = _register(api_client, tmp_path)
    before = len(_authorization_rows(api_client))
    response = api_client.post(
        f"/api/v1/jobs/{job_id}/pes/review",
        json=_publish_body(
            authorization_id="auth-does-not-exist",
            authorization_origin="agent-self",
        ),
    )
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "authorization_not_found"
    # no record was minted and no audit event was written
    assert len(_authorization_rows(api_client)) == before
    assert [
        event for event in _events(api_client, job_id) if event["type"] == "selection.self_signed"
    ] == []


def test_a_body_cannot_declare_a_self_signed_record(api_client: TestClient, tmp_path: Path) -> None:
    """The origin is server-owned; a create body can never mint ``agent-self``."""
    job_id = _register(api_client, tmp_path)
    response = api_client.post(
        "/api/v1/selection-authorizations",
        json={
            "source_job_id": job_id,
            "valid_until": "2099-01-01T00:00:00+00:00",
            "actions": ["publish_pes_selection"],
            "limits": {"max_candidates": 2},
            "origin": SELECTION_SELF_SIGNED_ORIGIN,  # ignored, never trusted
        },
    )
    assert response.status_code == 201, response.text
    document = response.json()["authorization"]
    assert document["origin"] == "operator"
    assert document["policy_revision"] != SELECTION_SELF_SIGNED_POLICY_REVISION


def test_operator_authorization_reports_the_operator_origin(
    api_client: TestClient, tmp_path: Path
) -> None:
    job_id = _register(api_client, tmp_path)
    service = SelectionAuthorizationService(api_client.app.state.research_resources)
    service.create(
        SelectionAuthorization(
            authorization_id="auth-operator",
            owner_principal_id=LOCAL_PRINCIPAL_ID,
            owner_project_id="uncategorized",
            policy_revision="acp2.selection.v1",
            valid_until="2099-01-01T00:00:00+00:00",
            actions=("publish_pes_selection",),
            source_job_id=job_id,
            allowed_attempts=(1,),
            workflow="PESsearch",
            roles=("TS", "INT"),
            limits=SelectionLimits(max_candidates=3),
        ),
        is_admin=True,
        principal_id=LOCAL_PRINCIPAL_ID,
        project_id="uncategorized",
    )
    response = api_client.post(
        f"/api/v1/jobs/{job_id}/pes/review",
        json=_publish_body(authorization_id="auth-operator"),
    )
    assert response.status_code == 200, response.text
    assert response.json()["publication"]["authorization_origin"] == "operator"
    # the caller-supplied path never mints a second record
    assert _self_signed_rows(api_client) == []


def test_autopublish_switch_off_keeps_the_typed_refusal(
    api_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = _register(api_client, tmp_path)
    monkeypatch.setenv("ACP_SELECTION_AUTOPUBLISH", "0")
    response = api_client.post(f"/api/v1/jobs/{job_id}/pes/review", json=_publish_body())
    assert response.status_code == 403, response.text
    assert "disabled" in str(response.json()["detail"])
    assert _self_signed_rows(api_client) == []


def test_manual_publication_still_rejects_an_authorization_id(
    api_client: TestClient, tmp_path: Path
) -> None:
    job_id = _register(api_client, tmp_path)
    response = api_client.post(
        f"/api/v1/jobs/{job_id}/pes/review",
        json={"candidates": [{"frame_index": 1, "role": "TS"}], "authorization_id": "auth-x"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["params"]["rule"] == "inconsistent_selector"


# --------------------------------------------------------------------------- #
# the local-single operator guard (unit-level)
# --------------------------------------------------------------------------- #


def _fake_request(server_identity: ServerIdentity) -> Any:
    return SimpleNamespace(
        state=SimpleNamespace(),
        app=SimpleNamespace(state=SimpleNamespace(server_identity=server_identity)),
    )


def test_only_the_local_single_operator_entry_may_self_sign() -> None:
    from acp.api import v1_routes

    local_server = ServerIdentity.build()
    local_request = _fake_request(local_server)
    operator = CommandIdentity.from_server(local_server)
    assert operator.subject_kind == SubjectKind.OPERATOR
    assert v1_routes._self_signed_publication_allowed(local_request, operator) is True

    # an authenticated API-key principal never qualifies (B15 path)
    api_user = CommandIdentity.from_server(
        local_server,
        subject_kind=SubjectKind.USER,
        permission_scope=PermissionScope.API_KEY,
    )
    assert v1_routes._self_signed_publication_allowed(local_request, api_user) is False

    # nor does a non-local-single deployment tier, even for the local principal
    lab_server = ServerIdentity.from_principal(
        Principal(
            principal_id=LOCAL_PRINCIPAL_ID,
            kind=PrincipalKind.USER,
            tenant_id="tenant-lab",
            deployment_tier="lab",
        )
    )
    lab_operator = CommandIdentity.from_server(lab_server)
    assert lab_operator.subject_kind == SubjectKind.OPERATOR
    assert (
        v1_routes._self_signed_publication_allowed(_fake_request(lab_server), lab_operator) is False
    )
