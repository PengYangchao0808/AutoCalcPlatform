"""CENSO-specific ACP stage-list tests."""

from __future__ import annotations

from acp.workflows.conformer import get_protocol_stages
from conformer_search.config import _get_default_config


def test_get_protocol_stages_for_censo_full_includes_all_censo_parts() -> None:
    """The full CENSO protocol exposes Part0–Part3 stage wrappers."""
    stages = get_protocol_stages("censo-full", config=_get_default_config())

    assert [stage.name for stage in stages] == [
        "embed_smiles",
        "crest_search",
        "isostat_cluster",
        "censo_part0",
        "censo_part1",
        "censo_part2",
        "censo_part3",
    ]


def test_get_protocol_stages_for_censo_zero_only_includes_enabled_parts() -> None:
    """The zero-variant CENSO protocol skips disabled Part1/Part2 wrappers."""
    stages = get_protocol_stages("censo-zero", config=_get_default_config())

    assert [stage.name for stage in stages] == [
        "embed_smiles",
        "crest_search",
        "isostat_cluster",
        "censo_part0",
        "censo_part3",
    ]
