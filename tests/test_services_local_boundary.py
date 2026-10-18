"""Local-single security boundary tests (B01-5 / plan todo 10).

Covers the pure Origin/CSRF decision core
(``acp_services.workspace.boundary``) and its minimal wiring in
``acp.api.server``:

* forged / cross-site ``Origin`` on a write -> denied (403);
* a ``null``/malformed ``Origin`` -> denied;
* three legitimate channels allowed: legacy same-origin page,
  no-Origin authorized CLI/machine request, SSE event stream;
* the deployment tier is process/config-only: a request can never downgrade it;
* ``lab`` / ``hosted`` are refused when auth/isolation capabilities are missing,
  with typed error codes.

The pure-core section imports only ``acp_services`` (the dependency-direction
rule: ``acp_services`` never imports ``acp``).
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from acp_services.contracts.access import DeploymentTier
from acp_services.workspace import boundary

_HOST = "127.0.0.1:8765"
_ORIGIN = "http://127.0.0.1:8765"


# ---------------------------------------------------------------------------
# Pure Origin/CSRF core
# ---------------------------------------------------------------------------


def test_same_origin_write_allowed() -> None:
    decision = boundary.evaluate_request(method="POST", origin=_ORIGIN, host=_HOST)
    assert decision.allowed is True
    assert decision.reason_code == boundary.BoundaryReason.SAME_ORIGIN
    assert decision.http_status == 200


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_forged_cross_site_origin_write_denied(method: str) -> None:
    decision = boundary.evaluate_request(method=method, origin="http://evil.example", host=_HOST)
    assert decision.allowed is False
    assert decision.reason_code == boundary.BoundaryReason.CROSS_ORIGIN
    assert decision.http_status == boundary.DENY_HTTP_STATUS == 403


def test_cross_site_with_different_port_denied() -> None:
    decision = boundary.evaluate_request(method="POST", origin="http://127.0.0.1:9999", host=_HOST)
    assert decision.allowed is False
    assert decision.reason_code == boundary.BoundaryReason.CROSS_ORIGIN


def test_null_origin_denied() -> None:
    decision = boundary.evaluate_request(method="POST", origin="null", host=_HOST)
    assert decision.allowed is False
    assert decision.reason_code == boundary.BoundaryReason.NULL_ORIGIN


@pytest.mark.parametrize("origin", ["://nope", "not a url", "javascript:alert(1)"])
def test_malformed_origin_denied(origin: str) -> None:
    decision = boundary.evaluate_request(method="POST", origin=origin, host=_HOST)
    assert decision.allowed is False
    assert decision.reason_code == boundary.BoundaryReason.ORIGIN_MALFORMED


def test_same_origin_match_is_scheme_port_aware() -> None:
    assert boundary.origin_matches_host("http://host", "host") is True
    assert boundary.origin_matches_host("http://host:8765", "host:8765") is True
    assert boundary.origin_matches_host("https://host", "host") is False
    assert boundary.origin_matches_host("http://host:8765", "host") is False
    assert boundary.origin_matches_host("null", "host") is False
    assert boundary.origin_matches_host(None, "host") is False


# --- three legitimate channels -------------------------------------------


def test_channel_same_origin_legacy_page_allowed() -> None:
    decision = boundary.evaluate_request(method="POST", origin=_ORIGIN, host=_HOST)
    assert decision.allowed is True


def test_channel_no_origin_authorized_cli_allowed() -> None:
    decision = boundary.evaluate_request(
        method="POST",
        origin=None,
        host=_HOST,
        client_host="127.0.0.1",
    )
    assert decision.allowed is True
    assert decision.reason_code == boundary.BoundaryReason.NO_ORIGIN_AUTHORIZED


def test_channel_no_origin_auth_header_allowed() -> None:
    decision = boundary.evaluate_request(
        method="POST",
        origin=None,
        host=_HOST,
        has_auth_header=True,
        client_host="10.1.2.3",
        server_is_loopback=False,
        tier=DeploymentTier.HOSTED,
    )
    assert decision.allowed is True


def test_channel_no_origin_local_cli_marker_allowed() -> None:
    decision = boundary.evaluate_request(
        method="POST",
        origin=None,
        host=_HOST,
        local_cli_marker=True,
        client_host="10.1.2.3",
        server_is_loopback=False,
        tier=DeploymentTier.LAB,
    )
    assert decision.allowed is True


def test_channel_event_stream_allowed() -> None:
    decision = boundary.evaluate_request(
        method="GET",
        origin="http://evil.example",
        host=_HOST,
        is_event_stream=True,
    )
    assert decision.allowed is True
    assert decision.reason_code == boundary.BoundaryReason.EVENT_STREAM


def test_no_origin_unauthorized_denied_on_public_binding() -> None:
    decision = boundary.evaluate_request(
        method="POST",
        origin=None,
        host="0.0.0.0:8765",
        client_host="203.0.113.9",
        server_is_loopback=False,
        tier=DeploymentTier.HOSTED,
    )
    assert decision.allowed is False
    assert decision.reason_code == boundary.BoundaryReason.NO_ORIGIN_UNAUTHORIZED
    assert decision.http_status == 403


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_safe_methods_always_allowed(method: str) -> None:
    decision = boundary.evaluate_request(method=method, origin="http://evil.example", host=_HOST)
    assert decision.allowed is True
    assert decision.reason_code == boundary.BoundaryReason.SAFE_METHOD


def test_origin_policy_binds_config() -> None:
    policy = boundary.OriginPolicy(tier=DeploymentTier.HOSTED, server_is_loopback=False)
    denied = policy.evaluate(method="POST", origin=None, host="x", client_host="203.0.113.9")
    assert denied.allowed is False
    allowed = policy.evaluate(method="POST", origin=_ORIGIN, host=_HOST)
    assert allowed.allowed is True


# ---------------------------------------------------------------------------
# Tier capability gate
# ---------------------------------------------------------------------------


def test_local_single_requires_no_capability() -> None:
    boundary.require_tier_capabilities(DeploymentTier.LOCAL_SINGLE, ())
    verdict = boundary.tier_capability_verdict(DeploymentTier.LOCAL_SINGLE)
    assert verdict.allowed is True


@pytest.mark.parametrize("tier", [DeploymentTier.LAB, DeploymentTier.HOSTED])
def test_public_tier_without_capabilities_refused(tier: str) -> None:
    with pytest.raises(boundary.TierCapabilityError) as excinfo:
        boundary.require_tier_capabilities(tier, ())
    assert excinfo.value.code == boundary.TierErrorCode.CAPABILITY_MISSING
    assert excinfo.value.missing == (
        boundary.TierCapability.AUTHENTICATION,
        boundary.TierCapability.ISOLATION,
    )


def test_public_tier_with_partial_capabilities_refused() -> None:
    with pytest.raises(boundary.TierCapabilityError) as excinfo:
        boundary.require_tier_capabilities(
            DeploymentTier.HOSTED, (boundary.TierCapability.AUTHENTICATION,)
        )
    assert excinfo.value.missing == (boundary.TierCapability.ISOLATION,)


def test_public_tier_with_all_capabilities_enabled() -> None:
    boundary.require_tier_capabilities(
        DeploymentTier.HOSTED,
        (boundary.TierCapability.AUTHENTICATION, boundary.TierCapability.ISOLATION),
    )


def test_unknown_tier_refused() -> None:
    with pytest.raises(boundary.TierCapabilityError) as excinfo:
        boundary.require_tier_capabilities("internet", ())
    assert excinfo.value.code == boundary.TierErrorCode.TIER_UNKNOWN


def test_request_downgrade_denied() -> None:
    verdict = boundary.evaluate_tier_request(
        configured_tier=DeploymentTier.HOSTED,
        requested_tier=DeploymentTier.LOCAL_SINGLE,
        capabilities=(boundary.TierCapability.AUTHENTICATION, boundary.TierCapability.ISOLATION),
    )
    assert verdict.allowed is False
    assert verdict.error_code == boundary.TierErrorCode.TIER_DOWNGRADE_ATTEMPT
    assert verdict.effective_tier == DeploymentTier.HOSTED


def test_request_upgrade_attempt_denied() -> None:
    verdict = boundary.evaluate_tier_request(
        configured_tier=DeploymentTier.LOCAL_SINGLE,
        requested_tier=DeploymentTier.HOSTED,
    )
    assert verdict.allowed is False
    assert verdict.error_code == boundary.TierErrorCode.TIER_OVERRIDE_ATTEMPT


def test_tier_request_capability_missing_denied() -> None:
    verdict = boundary.evaluate_tier_request(configured_tier=DeploymentTier.HOSTED, capabilities=())
    assert verdict.allowed is False
    assert verdict.error_code == boundary.TierErrorCode.CAPABILITY_MISSING


def test_tier_downgrade_helper() -> None:
    assert boundary.tier_is_downgrade(DeploymentTier.LOCAL_SINGLE, DeploymentTier.HOSTED) is True
    assert boundary.tier_is_downgrade(DeploymentTier.HOSTED, DeploymentTier.LOCAL_SINGLE) is False
    assert boundary.tier_is_downgrade("mystery", DeploymentTier.HOSTED) is False


# ---------------------------------------------------------------------------
# Minimal server wiring
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    monkeypatch.delenv("ACP_DEPLOYMENT_TIER", raising=False)
    monkeypatch.delenv("ACP_HOST", raising=False)
    from acp.api.server import create_app

    with TestClient(create_app(run_root=tmp_path, max_running=1)) as test_client:
        yield test_client


def test_page_loads(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200


def test_same_origin_write_passes(client: TestClient) -> None:
    same_origin = str(client.base_url).rstrip("/")
    response = client.post(
        "/api/v1/projects",
        json={"name": "Boundary", "description": "same origin", "tags": []},
        headers={"Origin": same_origin},
    )
    assert response.status_code != 403


def test_no_origin_machine_write_passes(client: TestClient) -> None:
    response = client.post(
        "/api/v1/projects",
        json={"name": "Machine", "description": "cli", "tags": []},
    )
    assert response.status_code != 403


def test_forged_origin_write_is_403(client: TestClient) -> None:
    response = client.post(
        "/api/v1/projects",
        json={"name": "Evil", "description": "csrf", "tags": []},
        headers={"Origin": "http://evil.example"},
    )
    assert response.status_code == 403
    body = response.json()
    assert body["detail"]["code"] == boundary.BoundaryReason.CROSS_ORIGIN


def test_cross_site_origin_write_is_403(client: TestClient) -> None:
    response = client.post(
        "/api/v1/jobs",
        json={"workflow": "fake", "name": "x", "input": {"source": "CCO"}},
        headers={"Origin": "http://attacker.test"},
    )
    assert response.status_code == 403


def test_event_stream_get_not_blocked(client: TestClient) -> None:
    response = client.get(
        "/api/jobs/missing-job/events",
        headers={"Origin": "http://evil.example", "Accept": "text/event-stream"},
    )
    assert response.status_code != 403


def test_hosted_tier_without_capabilities_refuses_to_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    monkeypatch.setenv("ACP_DEPLOYMENT_TIER", "hosted")
    monkeypatch.delenv("ACP_TIER_CAPABILITIES", raising=False)
    from acp.api.server import create_app

    with pytest.raises(boundary.TierCapabilityError) as excinfo:
        create_app(run_root=tmp_path)
    assert excinfo.value.code == boundary.TierErrorCode.CAPABILITY_MISSING
    assert excinfo.value.tier == DeploymentTier.HOSTED


def test_hosted_tier_with_capabilities_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    monkeypatch.setenv("ACP_DEPLOYMENT_TIER", "hosted")
    monkeypatch.setenv("ACP_TIER_CAPABILITIES", "authentication,isolation")
    from acp.api.server import create_app

    application = create_app(run_root=tmp_path)
    assert application is not None
