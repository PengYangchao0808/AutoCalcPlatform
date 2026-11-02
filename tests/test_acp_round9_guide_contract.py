"""Round-9 guide contract: the sentences an agent acts on must be true.

Each assertion pins a fact that a batch implemented this round, so the guides
cannot drift back to the pre-round-9 behaviour while the code moves forward.
"""

from __future__ import annotations

from acp.api.guidance_pack import MAX_GUIDE_BYTES, PACK_VERSION, _GUIDES, guidance_index


def _body(guide_id: str) -> str:
    return "\n".join(_GUIDES[guide_id].sections.values())


def test_pack_version_and_budget() -> None:
    assert PACK_VERSION == "1.6.0"
    for card in guidance_index()["guides"]:
        assert card["size_bytes"] <= MAX_GUIDE_BYTES, card["guide_id"]


def test_artifact_guide_documents_masking_and_pager() -> None:
    body = _body("topic.artifact_locating")
    assert "body_masked" in body and "masked_digest" in body
    assert "digest_scope=artifact" in body
    assert "artifact_kind=structure" in body
    assert "not_ready_yet" in body and "invalid_parameter" in body
    assert "prepared=true" in body and "prepared_unknown_reason" in body
    # the old dead end must be gone
    assert "artifact_body_redacted" not in body


def test_pes_guide_publishes_frames_and_manifest() -> None:
    body = _body("workflow.PESsearch")
    assert "result_manifest.json" in body
    assert "frame_NNN" in body or "frame_" in body
    assert "selection_source" in body
    assert "先发布再消费" in body


def test_batch_guide_names_the_per_item_energy_product() -> None:
    body = _body("workflow.BatchOptimize")
    assert "RESULT/energy/<item>_<step>.json" in body
    assert "energy_report" in body


def test_ts_task_guide_offers_the_local_single_self_signed_path() -> None:
    body = _body("task.find_transition_state")
    assert "agent-self" in body
    assert "ACP_SELECTION_AUTOPUBLISH" in body
    assert "selection.self_signed" in body
    assert "不转发给执行链" not in body  # retired wording: cancel note moved


def test_failure_and_freshness_facts_are_documented() -> None:
    body = _body("topic.artifact_locating")
    assert "diagnostic_message" in body
    assert "observed_age_seconds" in body and "stale" in body
    assert "normal_modes_v1" in body
    assert "status=unknown" in body


def test_limits_shape_is_documented_once() -> None:
    body = _body("topic.artifact_locating")
    assert "legacy_in_effect" in body
    assert "legacy_noop" in body  # named as the retired shape, not as a status
