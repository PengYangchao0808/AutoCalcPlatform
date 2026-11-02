"""Round-8 #1: one static table for the four real input-source shapes.

The same field name is not the same concept across workflows: ``input.source``
is a string in Confsearch, an object in PESsearch; ``input.source_type`` is the
simple chemistry enum in most entries and ``batch_structures`` in BatchOptimize;
IRC/tsmode take job references; the PES2TS trio takes a frozen request object.

These tests pin the *projection*:

* ``SOURCE_SHAPE_BY_WORKFLOW`` covers every active workflow with
  ``carrier``/``enum``/``notes``;
* every published contract block carries its shape (top level + the short
  ``input.source_shape`` key) and keeps ``additional_properties: false``;
* every published ``source_type`` enum is exactly one of the centralized
  ``SIMPLE_SOURCE_TYPES`` / ``BATCH_SOURCE_TYPES`` sets — a fourth spelling
  cannot appear in one surface only;
* the table never changes validation (it is a discovery projection).
"""

from __future__ import annotations

from typing import Any

import pytest

from acp.api.submission_contract import (
    BATCH_SOURCE_TYPES,
    SIMPLE_SOURCE_TYPES,
    SubmissionContractError,
    validate_workflow_submission,
)
from acp.api.workflow_contracts import (
    ACTIVE_WORKFLOWS,
    SOURCE_SHAPE_BY_WORKFLOW,
    describe_workflow,
)

_CARRIERS = {
    "source_type+inline_text",
    "source_object",
    "batch_structures",
    "source_reference",
    "frozen_request",
    "uncontracted",
}


def _published(wf: str) -> dict[str, Any]:
    return describe_workflow(wf)


# ── the table ───────────────────────────────────────────────────────────


def test_table_covers_every_active_workflow() -> None:
    assert set(SOURCE_SHAPE_BY_WORKFLOW) == set(ACTIVE_WORKFLOWS)
    assert len(SOURCE_SHAPE_BY_WORKFLOW) == len(ACTIVE_WORKFLOWS) == 15


def test_table_entries_are_complete_and_typed() -> None:
    for wf, entry in SOURCE_SHAPE_BY_WORKFLOW.items():
        assert set(entry) == {"carrier", "enum", "notes"}, wf
        assert entry["carrier"] in _CARRIERS, (wf, entry["carrier"])
        assert isinstance(entry["notes"], str) and entry["notes"], wf
        enum = entry["enum"]
        assert isinstance(enum, list), wf
        assert enum == sorted(set(enum)), wf


def test_simple_source_set_is_the_chemistry_envelope() -> None:
    assert SIMPLE_SOURCE_TYPES == {"smiles", "xyz_text", "structure_asset"}
    assert BATCH_SOURCE_TYPES == {"batch_structures"}
    assert not (SIMPLE_SOURCE_TYPES & BATCH_SOURCE_TYPES)


# ── the published contract blocks ──────────────────────────────────────


def test_every_block_exposes_its_shape() -> None:
    for wf in ACTIVE_WORKFLOWS:
        block = _published(wf)
        entry = SOURCE_SHAPE_BY_WORKFLOW[wf]
        assert block["source_shape"] == entry, wf
        if "input" in block:
            assert block["input"]["source_shape"] == entry["carrier"], wf


def test_published_input_blocks_stay_closed() -> None:
    for wf in ACTIVE_WORKFLOWS:
        block = _published(wf)
        input_block = block.get("input")
        if input_block is None:
            continue
        assert input_block["additional_properties"] is False, wf


def test_every_source_type_enum_is_one_of_the_two_central_sets() -> None:
    allowed = [sorted(SIMPLE_SOURCE_TYPES), sorted(BATCH_SOURCE_TYPES)]
    carriers = set()
    for wf in ACTIVE_WORKFLOWS:
        input_block = _published(wf).get("input") or {}
        properties = input_block.get("properties") or {}
        source_type = properties.get("source_type")
        if not isinstance(source_type, dict) or "enum" not in source_type:
            continue
        carriers.add(wf)
        assert source_type["enum"] in allowed, (wf, source_type["enum"])
    # The entries that publish the envelope (the frozen/reference/uncontracted
    # rows do not carry a source_type enum).
    assert carriers == {
        "Confsearch",
        "PESsearch",
        "BatchOptimize",
        "scan",
        "singlepoint",
        "optimize",
        "frequency",
        "xtb_optimize",
        "irc",
    }


@pytest.mark.parametrize(
    ("workflow", "carrier"),
    [
        ("Confsearch", "source_type+inline_text"),
        ("scan", "source_type+inline_text"),
        ("singlepoint", "source_type+inline_text"),
        ("optimize", "source_type+inline_text"),
        ("frequency", "source_type+inline_text"),
        ("xtb_optimize", "source_type+inline_text"),
        ("PESsearch", "source_object"),
        ("BatchOptimize", "batch_structures"),
        ("irc", "source_reference"),
        ("tsmode", "source_reference"),
        ("XtbPathSearch", "frozen_request"),
        ("OrcaGradient", "frozen_request"),
        ("XtbConstrainedOpt", "frozen_request"),
        ("casscf", "uncontracted"),
        ("nmr", "uncontracted"),
    ],
)
def test_carrier_of_each_workflow(workflow: str, carrier: str) -> None:
    assert SOURCE_SHAPE_BY_WORKFLOW[workflow]["carrier"] == carrier


def test_simple_envelope_enum_matches_the_central_set() -> None:
    for wf in ("Confsearch", "scan", "singlepoint", "optimize", "frequency", "xtb_optimize"):
        enum = _published(wf)["input"]["properties"]["source_type"]["enum"]
        assert enum == sorted(SIMPLE_SOURCE_TYPES), wf


def test_batch_enum_matches_the_central_set() -> None:
    enum = _published("BatchOptimize")["input"]["properties"]["source_type"]["enum"]
    assert enum == sorted(BATCH_SOURCE_TYPES)


def test_irc_notes_say_ts_source_is_server_derived() -> None:
    notes = SOURCE_SHAPE_BY_WORKFLOW["irc"]["notes"]
    assert "server-derived" in notes
    assert "ts_source" in notes


def test_batch_notes_state_the_items_pairing() -> None:
    notes = SOURCE_SHAPE_BY_WORKFLOW["BatchOptimize"]["notes"]
    assert "missing_source_type" in notes
    assert "from_artifact" in notes


# ── projection only: validation is unchanged ───────────────────────────


def test_table_does_not_participate_in_validation() -> None:
    # A payload that contradicts the Batch row is still refused by the contract
    # rule, not by the table.
    with pytest.raises(SubmissionContractError) as caught:
        validate_workflow_submission(
            "BatchOptimize", input={"items": [{"xyz": "H 0 0 0"}]}, method={}, resources={}
        )
    assert caught.value.rule == "missing_source_type"
    # And the documented Batch shape passes.
    validate_workflow_submission(
        "BatchOptimize",
        input={"source_type": "batch_structures", "items": [{"xyz": "H 0 0 0"}]},
        method={},
        resources={},
    )
