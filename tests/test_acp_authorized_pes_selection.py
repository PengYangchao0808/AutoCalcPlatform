"""Round-5 PR-06: authorized agent publication and Batch consumption.

Verifies the plan's §10.3 first batch: bounded authorization in the Data store
(never self-created), every scope check (project/principal/expiry/revocation/
role/method/resource/source attempt/quota), publication with the authorization
receipt, and Batch consumption that requires the receipt and pins the consumed
publication version (lineage).  Historical manual selections keep their
semantics; a plain scan cannot borrow this PES-only service.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from acp_services.contracts.selection import (
    SelectionAuthorization,
    SelectionLimits,
    SelectionPublicationRequest,
    SelectionSourceBinding,
)
from acp_services.data.research_resources import ResearchResourceStore
from acp_services.runtime.selection_authorization import (
    SelectionAuthorizationError,
    SelectionAuthorizationService,
)

FIXED_NOW = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)
PRINCIPAL = "principal-admin"
PROJECT = "proj-1"


def _xyz(comment: str = "frame") -> str:
    return "3\n" + comment + "\nO 0.0 0.0 0.0\nH 0.0 0.0 0.96\nH 0.0 0.0 -0.96\n"


@pytest.fixture()
def pes_task(tmp_path: Path) -> Path:
    root = tmp_path / "task"
    scan_dir = root / "WORK" / "07_PATH" / "pes_scan_001" / "scan_frames"
    scan_dir.mkdir(parents=True)
    for index in range(3):
        (scan_dir / f"frame_{index:03d}.xyz").write_text(_xyz(), encoding="utf-8")
    frames = [
        {
            "index": index,
            "target_coordinate": 1.0 + index * 0.1,
            "actual_coordinate": 1.0 + index * 0.1,
            "geometry_path": f"scan_frames/frame_{index:03d}.xyz",
            "scan_energy_hartree": -76.0 - index * 0.01,
        }
        for index in range(3)
    ]
    profile = {
        "schema_version": "pes_profile_v2",
        "workflow": "PESsearch",
        "mode": "bond_length_scan",
        "status": "completed",
        "scan_dir": "WORK/07_PATH/pes_scan_001",
        "frames": frames,
        "frames_count": 3,
        "ts_candidates": [],
        "int_candidates": [],
    }
    pes_dir = root / "RESULT" / "pes_search"
    pes_dir.mkdir(parents=True)
    (pes_dir / "pes_profile.json").write_text(json.dumps(profile), encoding="utf-8")
    return root


@pytest.fixture()
def store(tmp_path: Path) -> ResearchResourceStore:
    return ResearchResourceStore(tmp_path / "research.db")


@pytest.fixture()
def authorization() -> SelectionAuthorization:
    return SelectionAuthorization(
        authorization_id="auth-1",
        owner_principal_id=PRINCIPAL,
        owner_project_id=PROJECT,
        policy_revision="policy-1",
        valid_until=(FIXED_NOW + timedelta(days=1)).isoformat(timespec="seconds"),
        actions=("publish_pes_selection", "batch_consume"),
        source_job_id="job_001",
        allowed_attempts=(1,),
        workflow="PESsearch",
        roles=("TS", "INT"),
        methods=("GFN2-xTB", "r2SCAN-3c"),
        nproc_max=8,
        limits=SelectionLimits(max_candidates=4, max_batch_jobs=2, max_retries=2),
    )


def _request(**overrides) -> SelectionPublicationRequest:
    candidates = overrides.pop(
        "candidates",
        (
            {"frame_index": 1, "role": "TS", "name": "frame_001__TAG_TS"},
            {"frame_index": 2, "role": "INT"},
        ),
    )
    kwargs = {
        "authorization_id": "auth-1",
        "selector": "agent",
        "actor_principal_id": PRINCIPAL,
        "project_id": PROJECT,
        "source": SelectionSourceBinding(job_id="job_001", attempt=1, profile_digest="p1"),
        "workflow": "PESsearch",
        "candidates": candidates,
        "method": "r2SCAN-3c",
        "nproc": 4,
    }
    kwargs.update(overrides)
    return SelectionPublicationRequest(**kwargs)


def _service(store: ResearchResourceStore, authorization: SelectionAuthorization) -> SelectionAuthorizationService:
    service = SelectionAuthorizationService(store, clock=lambda: FIXED_NOW)
    service.create(authorization, is_admin=True, principal_id=PRINCIPAL, project_id=PROJECT)
    return service


# --------------------------------------------------------------------------- #
# authorization lifecycle
# --------------------------------------------------------------------------- #


def test_agent_cannot_create_or_widen_an_authorization(
    store: ResearchResourceStore, authorization: SelectionAuthorization
) -> None:
    service = SelectionAuthorizationService(store, clock=lambda: FIXED_NOW)
    with pytest.raises(SelectionAuthorizationError) as excinfo:
        service.create(authorization, is_admin=False, principal_id="agent-1", project_id=PROJECT)
    assert excinfo.value.code == "permission_denied"


def test_authorized_publication_and_batch_consumption(
    pes_task: Path, store: ResearchResourceStore, authorization: SelectionAuthorization
) -> None:
    from acp.calculations.pes.review import load_pes_review, save_pes_review

    service = _service(store, authorization)
    request = _request()
    decision = service.validate(request)
    assert decision.allowed
    payload = save_pes_review(
        pes_task,
        job_id="job_001",
        candidates=list(request.candidates),
        selector="agent",
        actor=request.actor_principal_id,
        authorization=decision.to_dict(),
        source_binding=request.source.to_dict(),
        idempotency_key="pub-1",
        now=FIXED_NOW,
    )
    assert payload["selector"] == "agent"
    assert payload["selected"][0]["selection_source"] == "agent"
    assert payload["authorization"]["authorization_id"] == "auth-1"
    assert payload["source_binding"]["attempt"] == 1

    consumed = service.consume(request, decision, action_digest="pub-1")
    assert consumed["limits"]["used_candidates"] == 2
    # replay with the same digest never double-charges
    service.consume(request, decision, action_digest="pub-1")
    again = store.read(
        "selection-auth:auth-1", principal_id=PRINCIPAL, project_id=PROJECT
    ).document
    assert again["limits"]["used_candidates"] == 2

    from acp.calculations.batch.loaders import load_items_from_result_manifest

    items = load_items_from_result_manifest(pes_task)
    assert len(items) == 2
    assert items[0].source_type == "pes_publication"
    assert "auth=auth-1@" in items[0].source_ref
    assert load_pes_review(pes_task)["revision"] == 1


def test_scope_checks_refuse_the_publication(
    store: ResearchResourceStore, authorization: SelectionAuthorization
) -> None:
    service = _service(store, authorization)

    def error_for(**overrides) -> str:
        latest_attempt = overrides.pop("latest_attempt", 1)
        with pytest.raises(SelectionAuthorizationError) as excinfo:
            service.validate(_request(**overrides), latest_attempt=latest_attempt)
        return excinfo.value.code

    # cross-project reads share the not-found answer (existence never leaks)
    assert error_for(project_id="proj-2") == "authorization_not_found"
    assert error_for(actor_principal_id="someone-else") == "principal_mismatch"
    assert error_for(workflow="scan") == "workflow_mismatch"
    assert error_for(candidates=({"frame_index": 1, "role": "TS"},), method="B3LYP") == "method_not_allowed"
    assert error_for(nproc=64) == "resource_exceeded"
    assert error_for(latest_attempt=2) == "stale_source"


def test_expired_and_revoked_authorizations_are_refused(
    store: ResearchResourceStore, authorization: SelectionAuthorization
) -> None:
    service = _service(store, authorization)
    service._clock = lambda: FIXED_NOW + timedelta(days=2)
    with pytest.raises(SelectionAuthorizationError) as excinfo:
        service.validate(_request())
    assert excinfo.value.code == "authorization_expired"

    store.revoke(
        "selection-auth:auth-1",
        principal_id=PRINCIPAL,
        project_id=PROJECT,
        reason="test",
    )
    service._clock = lambda: FIXED_NOW
    with pytest.raises(SelectionAuthorizationError) as excinfo:
        service.validate(_request())
    assert excinfo.value.code == "authorization_revoked"


def test_candidate_quota_is_persistent_and_enforced(
    store: ResearchResourceStore, authorization: SelectionAuthorization
) -> None:
    service = _service(store, authorization)
    request = _request()
    service.consume(request, service.validate(request), action_digest="run-1")
    service.consume(request, service.validate(request), action_digest="run-2")
    with pytest.raises(SelectionAuthorizationError) as excinfo:
        service.validate(request)
    assert excinfo.value.code == "quota_exceeded"
    # a fresh service over the same persistent store keeps the usage
    reopened = SelectionAuthorizationService(
        ResearchResourceStore(Path(store.db_path)), clock=lambda: FIXED_NOW
    )
    with pytest.raises(SelectionAuthorizationError) as excinfo:
        reopened.validate(request)
    assert excinfo.value.code == "quota_exceeded"


# --------------------------------------------------------------------------- #
# Batch consumption: receipt required, manual stays manual
# --------------------------------------------------------------------------- #


def test_loader_refuses_an_agent_product_without_its_receipt(pes_task: Path) -> None:
    from acp.calculations.batch.loaders import load_items_from_result_manifest
    from acp.calculations.pes.review import save_pes_review
    from acp.storage.manifest import ResultManifest

    save_pes_review(
        pes_task,
        job_id="job_001",
        candidates=[{"frame_index": 1, "role": "TS"}],
        selector="agent",
        actor=PRINCIPAL,
        authorization={
            "authorization_id": "auth-1",
            "authorization_revision": 1,
            "authorization_digest": "d1",
            "policy_revision": "policy-1",
        },
        source_binding={"job_id": "job_001", "attempt": 1},
        now=FIXED_NOW,
    )
    manifest = ResultManifest.read(pes_task / "RESULT")
    for product in manifest.products:
        product.metadata.pop("authorization_id", None)
    manifest.write(pes_task / "RESULT")

    with pytest.raises(ValueError, match="authorization/publication receipt"):
        load_items_from_result_manifest(pes_task)


def test_agent_publication_without_receipt_is_refused_by_the_writer(pes_task: Path) -> None:
    from acp.calculations.pes.review import PesReviewError, save_pes_review

    with pytest.raises(PesReviewError, match="authorization decision receipt"):
        save_pes_review(
            pes_task,
            job_id="job_001",
            candidates=[{"frame_index": 1, "role": "TS"}],
            selector="agent",
            actor=PRINCIPAL,
            now=FIXED_NOW,
        )


def test_manual_selection_keeps_its_historical_semantics(pes_task: Path) -> None:
    from acp.calculations.batch.loaders import load_items_from_result_manifest
    from acp.calculations.pes.review import save_pes_review

    save_pes_review(
        pes_task,
        job_id="job_001",
        candidates=[{"frame_index": 1, "role": "TS"}],
        now=FIXED_NOW,
    )
    items = load_items_from_result_manifest(pes_task)
    assert len(items) == 1
    assert items[0].source_type != "pes_publication"
    assert "auth=" not in items[0].source_ref
