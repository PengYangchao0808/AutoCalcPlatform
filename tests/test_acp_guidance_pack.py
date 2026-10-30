"""Guidance content pack integrity (plan 2026-10-30 §4/§15.1).

Machine checks that keep the pack honest:

* every catalog-active workflow has exactly one workflow guide (no hand-list);
* first-batch coverage labels match the plan's four composite scenarios;
* entries are versioned, digested and inside the §4.2 byte budgets;
* topic references resolve (one level of indirection, no recursion);
* the published workflow-contract examples pass the real shared validator.
"""

from __future__ import annotations

import json

import pytest

from acp.api.guidance_pack import (
    FIRST_BATCH_WORKFLOWS,
    GUIDE_FORMAT,
    MAX_GUIDE_BYTES,
    MAX_INDEX_CARD_BYTES,
    PACK_VERSION,
    audit_pack,
    get_guide,
    guidance_index,
    guide_ids,
    read_guide,
    workflow_guide_map,
)
from acp.api.submission_contract import SubmissionContractError
from acp.api.workflow_contracts import describe_workflow
from acp.catalog import WORKFLOW_CATALOG

_ACTIVE = tuple(
    str(entry["id"])
    for entry in WORKFLOW_CATALOG
    if entry.get("status", "active") == "active" and entry.get("visible", True)
)


def test_every_active_workflow_has_exactly_one_guide() -> None:
    audit = audit_pack()
    assert audit["missing_guides"] == []
    assert audit["extra_guides"] == []
    assert set(audit["guided_workflows"]) == set(_ACTIVE)


def test_first_batch_coverage_labels_are_honest() -> None:
    mapping = workflow_guide_map()
    for workflow in _ACTIVE:
        entry = get_guide(mapping[workflow])
        expected = "first_batch" if workflow in FIRST_BATCH_WORKFLOWS else "documented_partial"
        assert entry.coverage == expected, (workflow, entry.coverage)


def test_entries_are_versioned_and_digested() -> None:
    for guide_id in guide_ids():
        payload = read_guide(guide_id)
        assert payload["format"] == GUIDE_FORMAT
        assert payload["version"], guide_id
        assert payload["digest"].startswith("sha256:") and len(payload["digest"]) == 71
        assert payload["size_bytes"] > 0


def test_byte_budgets_hold() -> None:
    for guide_id in guide_ids():
        assert read_guide(guide_id)["size_bytes"] <= MAX_GUIDE_BYTES, guide_id
    index = guidance_index()
    assert index["pack_version"] == PACK_VERSION
    for card in index["guides"]:
        assert len(json.dumps(card).encode("utf-8")) <= MAX_INDEX_CARD_BYTES, card["guide_id"]


def test_topic_references_resolve_one_level() -> None:
    valid = set(guide_ids())
    for guide_id in guide_ids():
        entry = get_guide(guide_id)
        for topic in entry.topics:
            assert topic in valid, (guide_id, topic)
            # one level only: a topic never references another guide
            assert get_guide(topic).topics == (), topic


def test_budget_omissions_are_explicit_never_silent() -> None:
    full = read_guide("workflow.PESsearch")
    clipped = read_guide("workflow.PESsearch", max_bytes=16)
    assert clipped["omitted_reason"] == "budget"
    assert clipped["sections"] == {}
    assert clipped["omitted"] == list(full["sections"])
    assert clipped["hint"]


def test_section_read_and_unknown_section() -> None:
    payload = read_guide("workflow.PESsearch", section="purpose")
    assert set(payload["sections"]) == {"purpose"}
    assert "steps" in payload["omitted"]
    with pytest.raises(ValueError, match="no section"):
        read_guide("workflow.PESsearch", section="nope")


def test_published_contract_examples_pass_the_real_validator() -> None:
    """Plan §4.3: an example the projection publishes must pass the real check."""
    from acp.api.submission_contract import validate_workflow_submission

    for workflow in ("Confsearch", "BatchOptimize", "PESsearch", "scan"):
        block = describe_workflow(workflow)
        assert block["examples"], workflow
        for example in block["examples"]:
            validate_workflow_submission(
                example["workflow"],
                input=example.get("input"),
                method=example.get("method"),
                resources=example.get("resources"),
            )


def test_unknown_guide_id_is_a_clean_key_error() -> None:
    with pytest.raises(KeyError):
        get_guide("workflow.nope")


def test_describe_workflow_carries_guide_refs_for_all_active_workflows() -> None:
    mapping = workflow_guide_map()
    for workflow in _ACTIVE:
        block = describe_workflow(workflow)
        assert block["guide_ref"]["guide_id"] == mapping[workflow], workflow
        assert block["guide_ref"]["pack_version"] == PACK_VERSION
        assert block["guide_ref"]["tool"] == "acp_get_guidance"
    # Coverage stays honest: only the staged four publish a schema.
    published = {wf: describe_workflow(wf)["coverage"] for wf in _ACTIVE}
    assert published["Confsearch"] == "partial"
    assert published["BatchOptimize"] == "partial"
    assert published["PESsearch"] == "partial"
    assert published["scan"] == "partial"
    assert published["irc"] == "not_provided"


def test_pessearch_rejects_method_levels_and_bad_enums() -> None:
    from acp.api.submission_contract import validate_workflow_submission

    base_input = {
        "source": {"source_type": "xyz_text", "xyz_text": "3\n\nO 0 0 0\nH 1 0 0\nH 0 1 0"},
        "coordinate": {
            "kind": "distance",
            "atoms": [0, 1],
            "start": 1.0,
            "end": 2.4,
            "n_points": 9,
        },
    }
    validate_workflow_submission(
        "PESsearch", input=base_input, method={"mode": "bond_length_scan"}, resources=None
    )
    with pytest.raises(SubmissionContractError) as excinfo:
        validate_workflow_submission(
            "PESsearch",
            input=base_input,
            method={"mode": "bond_length_scan", "levels": {"dft_opt": {}}},
            resources=None,
        )
    assert excinfo.value.params()["rule"] == "unknown_field"
    with pytest.raises(SubmissionContractError) as excinfo:
        validate_workflow_submission(
            "PESsearch", input=base_input, method={"strategy": "voodoo"}, resources=None
        )
    assert excinfo.value.params()["rule"] == "invalid_enum"


def test_wire_syntax_guidance_present_v1_1_0() -> None:
    """v1.1.0: the DA validation findings must stay in the published guides.

    The untagged-inline-role trap (job 20261030_100432_005: silent INT
    semantics + default method) and the unit/bond-side vocabulary are
    guidance-level fixes; dropping these passages would reintroduce the
    prompt-side gap (reports/acp2_mcp_guidance_20261030).
    """
    batch = read_guide("workflow.BatchOptimize")
    assert batch["version"] == "1.1.0"
    steps = batch["sections"]["steps"]
    assert 'role:"ts"' in steps
    assert "默认按 INT" in steps
    assert "from_artifact" in steps and "selected_ids" in steps
    assert "不会自动套用到未标角色的 item" in steps
    pes = read_guide("workflow.PESsearch")
    assert pes["version"] == "1.1.0"
    pes_steps = pes["sections"]["steps"]
    assert "angstrom" in pes_steps and "degree" in pes_steps
    assert "产物侧拉伸已成键" in pes_steps


def test_task_instructions_are_complete_and_routable() -> None:
    """Task-level routing entries (goal -> chain), v1.3.0.

    Each task instruction must be self-contained for its decision points and
    must only *reference* workflow guides — never duplicate parameter authority.
    """
    from acp.api.guidance_pack import guidance_index

    tasks = [card for card in guidance_index()["guides"] if card["kind"] == "task"]
    ids = {card["guide_id"] for card in tasks}
    assert ids == {
        "task.find_transition_state",
        "task.verify_reaction_path",
        "task.conformer_and_energy",
        "task.barrier_high_accuracy",
        "task.multireference_analysis",
    }
    required_sections = {"when_to_use", "chain", "decisions", "completion", "stop", "not_supported"}
    known = set(guide_ids())
    for guide_id in ids:
        payload = read_guide(guide_id)
        assert set(payload["sections"]) == required_sections, guide_id
        # referenced topics/workflows resolve
        for topic in payload["topics"]:
            assert topic in known, (guide_id, topic)
        # chain text must point at workflow guides explicitly
        assert "workflow." in payload["sections"]["chain"], guide_id


def test_task_multireference_names_the_scientific_decisions() -> None:
    body = read_guide("task.multireference_analysis")["sections"]["when_to_use"]
    assert "活性空间" in body and "询问" in body
    chain = read_guide("task.multireference_analysis")["sections"]["chain"]
    assert "CASSCF" in chain and "NEVPT2" in chain
    stop = read_guide("task.multireference_analysis")["sections"]["stop"]
    assert "收敛" in stop
