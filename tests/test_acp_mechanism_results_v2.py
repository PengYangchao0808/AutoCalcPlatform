"""Tests for the mechanism v2 RESULT view layer (results_v2.py)."""

from __future__ import annotations

import json
from pathlib import Path

from acp.mechanism.results_v2 import write_v2_result_layer


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _make_study(tmp_path: Path) -> tuple[Path, Path]:
    """Build a minimal frozen mechanism_study/<study_id> + task root."""
    task_root = tmp_path / "run" / "proj" / "task"
    study_dir = task_root / "mechanism_study" / "study_20260822_000000_abcd1234"
    study_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        study_dir / "study.json",
        {
            "study_id": "study_20260822_000000_abcd1234",
            "reactant_id": "state_reactant",
            "product_id": "state_product",
            "status": "completed",
            "quality": "high",
            "stable_states": [
                {"state_id": "state_reactant"},
                {"state_id": "state_product"},
            ],
        },
    )
    _write_json(
        study_dir / "network.json",
        {"nodes": [{"id": "state_reactant"}, {"id": "state_product"}], "edges": []},
    )
    _write_json(
        study_dir / "routes" / "state_reactant__route01" / "path_manifest.json",
        {
            "route_id": "route01",
            "source_state_id": "state_reactant",
            "target_state_id": "state_product",
            "fidelity": "s3",
            "status": "completed",
        },
    )
    _write_json(
        study_dir / "refinements" / "rf_0001" / "refinement_manifest.json",
        {"manifest_id": "rf_0001", "route_id": "route01", "fidelity": "s4", "status": "completed"},
    )
    (study_dir / "inputs").mkdir(parents=True, exist_ok=True)
    (study_dir / "inputs" / "state_reactant.xyz").write_text("1\n\nC 0 0 0\n", encoding="utf-8")
    return study_dir, task_root


def test_write_v2_result_layer_layout(tmp_path: Path) -> None:
    study_dir, task_root = _make_study(tmp_path)
    manifest = write_v2_result_layer(task_root)
    assert manifest is not None
    assert manifest.name == "result_manifest.json"

    result_dir = task_root / "RESULT"
    assert (result_dir / "mechanism" / "reaction_network.json").is_file()
    assert (result_dir / "mechanism" / "route_summary.json").is_file()
    assert (result_dir / "mechanism" / "ts_summary.json").is_file()
    assert (result_dir / "mechanism" / "irc_validation.json").is_file()
    assert (result_dir / "energies" / "energy_profile.json").is_file()
    assert (result_dir / "summary.json").is_file()
    assert (result_dir / "structures" / "state_reactant.xyz").is_file()

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["version"] == 2
    assert payload["workflow"] == "mechanism"
    kinds = {p["kind"] for p in payload["products"]}
    assert "report" in kinds
    assert "structure" in kinds


def test_write_v2_result_layer_frozen_subtree_untouched(tmp_path: Path) -> None:
    study_dir, task_root = _make_study(tmp_path)
    before = {
        str(p.relative_to(study_dir)): p.read_bytes() for p in study_dir.rglob("*") if p.is_file()
    }
    write_v2_result_layer(task_root)
    after = {
        str(p.relative_to(study_dir)): p.read_bytes() for p in study_dir.rglob("*") if p.is_file()
    }
    assert before == after


def test_write_v2_result_layer_missing_study_returns_none(tmp_path: Path) -> None:
    assert write_v2_result_layer(tmp_path / "task") is None


def test_write_v2_result_layer_empty_study_returns_none(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir(parents=True, exist_ok=True)
    assert write_v2_result_layer(empty) is None
