"""Round-8 guide contract: user method correction, artifact chain, IRC source.

These are string-level guards: the guides are the agent's only instruction
surface, so a stale sentence is a real defect (round-8 complaint list).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from acp.api.guidance_pack import MAX_GUIDE_BYTES, PACK_VERSION, _GUIDES, guidance_index

ROOT = Path(__file__).resolve().parents[1]


def _body(guide_id: str) -> str:
    entry = _GUIDES[guide_id]
    return "\n".join(entry.sections.values())


def test_pack_version_is_current() -> None:
    assert PACK_VERSION == "1.5.0"
    for card in guidance_index()["guides"]:
        assert card["size_bytes"] <= MAX_GUIDE_BYTES, card["guide_id"]


def test_optimization_default_is_r2scan3c_and_wb97mv_is_not_recommended() -> None:
    """USER CORRECTION: r2SCAN-3c is the optimization default for most systems."""
    body = _body("topic.method_selection")
    assert "不推荐用 wB97M-V 做几何优化" in body
    assert "默认 r2SCAN-3c" in body
    # the round-6/7 size ladder that allowed wB97M-V optimization must be gone
    assert "小体系：允许上 wB97M-V" not in body
    assert "小体系允许 wB97M-V" not in body
    # the single-point default stays wB97M-V
    assert "单点默认 wB97M-V" in body

    optimize = _body("workflow.optimize")
    assert "r2SCAN-3c" in optimize and "不推荐 wB97M-V" in optimize

    barrier = _body("task.barrier_high_accuracy")
    assert "默认 r2SCAN-3c" in barrier


def test_artifact_guide_teaches_prepare_then_read() -> None:
    """The descriptor is not an ArtifactRef; the five-tuple comes from prepare."""
    body = _body("topic.artifact_locating")
    assert "描述符" in body
    assert "acp_prepare_artifact" in body and "acp_read_artifact" in body
    assert "五元组" in body and "的回包" in body
    # the round-7 wording that claimed the descriptor carries generation/digest
    assert "描述符（artifact_id、generation" not in body
    assert "本地已终态且描述符可用时可直接 read" not in body
    # local-single visibility is documented next to the redaction boundary
    assert "local-single" in body and "idempotency_key" in body


def test_irc_guide_says_ts_source_is_server_derived() -> None:
    body = _body("workflow.irc")
    assert "不要手写 ts_source" in body
    assert "irc_ts_source_v1" in body
    assert "runtime_derived" in body
    assert "irc_ts_source_partial" in body


def test_source_shape_table_is_documented() -> None:
    body = _body("topic.inputs_electronic_state")
    for needle in ("PESsearch", "BatchOptimize", "source_type", "batch_structures"):
        assert needle in body, needle


def test_comparability_covers_naming_and_absolute_totals() -> None:
    body = _body("topic.method_energy_comparability")
    assert "InChIKey" in body
    assert "绝对总能" in body


def test_batch_guide_covers_ts_only_and_levels() -> None:
    body = _body("workflow.BatchOptimize")
    assert "missing_source_type" in body
    assert "missing_role_mirror" in body
    assert "hessian_resolution" in body


def test_find_transition_state_summary_does_not_claim_publish_is_mandatory() -> None:
    summary = _GUIDES["task.find_transition_state"].summary
    body = _body("task.find_transition_state")
    assert "有授权才 publish" in summary
    assert "inline role" in summary or "inline" in body
    assert "必经" not in summary


# --------------------------------------------------------------------------- #
# fields the external agent explicitly praised must survive this round
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("relative_path", "needle"),
    [
        ("src/acp/calculations/batch/effective_config.py", "orca_summary"),
        ("src/acp/calculations/batch/engine.py", "method_fingerprint"),
        ("src/acp/api/job_status_view.py", "live_status"),
        ("src/acp/calculations/pes/scan.py", "reaction_progress"),
        ("src/acp/scheduler/structure_source_store.py", "ts_validation"),
    ],
)
def test_praised_fields_still_exist(relative_path: str, needle: str) -> None:
    text = (ROOT / relative_path).read_text(encoding="utf-8")
    assert needle in text, f"{relative_path} lost {needle}"
