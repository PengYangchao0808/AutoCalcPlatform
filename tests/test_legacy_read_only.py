"""Tests for the historical manifest compatibility readers."""

from __future__ import annotations

from pathlib import Path

import pytest

from acp.compat.legacy.manifests import (
    read_batch_calculation_manifest,
    read_reaction_definition,
    read_refinement_manifest,
    read_result_summary,
    read_s2_candidate_manifest,
    read_s2_path_manifest,
    read_s2_review,
    read_s3_lowconfirm_manifest,
    read_s4_highconfirm_manifest,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_read_s2_path_manifest_fixture() -> None:
    payload = read_s2_path_manifest(FIXTURES / "legacy_s2_path_manifest.json")

    assert payload["schema_version"] == "s2_path_v2"
    assert payload["mode"] == "bond_length_scan"
    recommendations = payload["recommendations"]
    assert isinstance(recommendations, dict)
    ts_rows = recommendations["ts"]
    assert isinstance(ts_rows, list)
    first_ts = ts_rows[0]
    assert isinstance(first_ts, dict)
    assert first_ts["candidate_id"] == "ts_guess_001"


def test_read_s3_lowconfirm_manifest_fixture() -> None:
    payload = read_s3_lowconfirm_manifest(FIXTURES / "legacy_s3_lowconfirm_manifest.json")

    assert payload["workflow"] == "Lowconfirm"
    candidates = payload["candidates"]
    assert isinstance(candidates, list)
    first_candidate = candidates[0]
    assert isinstance(first_candidate, dict)
    assert first_candidate["id"] == "ts_guess_001"


def test_read_s4_highconfirm_manifest_fixture() -> None:
    payload = read_s4_highconfirm_manifest(FIXTURES / "legacy_s4_highconfirm_manifest.json")

    assert payload["workflow"] == "Highconfirm"
    candidates = payload["candidates"]
    assert isinstance(candidates, list)
    first_candidate = candidates[0]
    assert isinstance(first_candidate, dict)
    assert first_candidate["id"] == "ts_guess_001"


def test_read_refinement_manifest_fixture() -> None:
    payload = read_refinement_manifest(FIXTURES / "legacy_refinement_manifest.json")

    assert payload["schema_version"] == "refinement_manifest_v1"
    assert payload["stage"] == "S3"
    structures = payload["structures"]
    assert isinstance(structures, list)
    first_structure = structures[0]
    assert isinstance(first_structure, dict)
    assert first_structure["id"] == "ts_guess_001"


def test_read_batch_calculation_manifest_fixture() -> None:
    payload = read_batch_calculation_manifest(FIXTURES / "legacy_batch_calculation_manifest.json")

    assert payload is not None
    assert payload["kind"] == "batch_calculation_manifest"
    items = payload["items"]
    assert isinstance(items, list)
    first_item = items[0]
    assert isinstance(first_item, dict)
    assert first_item["status"] == "completed"


def test_read_result_summary_fixture() -> None:
    payload = read_result_summary(FIXTURES / "legacy_result_summary.json")

    assert payload["workflow"] == "PESsearch"
    products = payload["products"]
    assert isinstance(products, list)
    first_product = products[0]
    assert isinstance(first_product, dict)
    assert first_product["path"] == "mechanism/ts_guess_001.xyz"


def test_read_reaction_definition_fixture() -> None:
    payload = read_reaction_definition(FIXTURES / "legacy_reaction_definition.json")

    reactants = payload["reactants"]
    assert isinstance(reactants, list)
    first_reactant = reactants[0]
    assert isinstance(first_reactant, dict)
    assert first_reactant["smiles"] == "CC"
    products = payload["products"]
    assert isinstance(products, list)
    first_product = products[0]
    assert isinstance(first_product, dict)
    assert first_product["smiles"] == "C=C"
    mapping = payload["mapping"]
    assert isinstance(mapping, list)
    assert mapping[0] == {"reactant_index": 0, "product_index": 0}
    config_hash = payload["config_hash"]
    assert isinstance(config_hash, str)
    assert config_hash.startswith("sha256:")


def test_read_s2_review_fixture() -> None:
    payload = read_s2_review(FIXTURES / "legacy_s2_review.json")

    assert payload is not None
    candidates = payload["candidates"]
    assert isinstance(candidates, list)
    first_candidate = candidates[0]
    assert isinstance(first_candidate, dict)
    assert first_candidate["candidate_id"] == "ts_guess_001"
    assert payload["selected"] == ["ts_guess_001"]


def test_read_s2_candidate_manifest_fixture() -> None:
    payload = read_s2_candidate_manifest(FIXTURES / "legacy_s2_candidate_manifest.json")

    assert payload is not None
    candidates = payload["candidates"]
    assert isinstance(candidates, list)
    first_candidate = candidates[0]
    assert isinstance(first_candidate, dict)
    assert first_candidate["candidate_id"] == "ts_guess_001"
    assert payload["selected"] == ["ts_guess_001"]


def test_reaction_definition_bad_hash_raises(tmp_path: Path) -> None:
    source = FIXTURES / "legacy_reaction_definition.json"
    text = source.read_text(encoding="utf-8").replace(
        "sha256:2321da94bdccf43666be8b7dfa9f60a7de3f410cb16115e34d37ccadf37a2c3b",
        "sha256:tampered",
    )
    path = tmp_path / "reaction.json"
    _ = path.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError, match="hash"):
        _ = read_reaction_definition(path)
