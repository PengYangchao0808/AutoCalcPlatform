"""Tests for normalized energy-workspace projections."""

# pyright: reportMissingTypeArgument=false, reportAny=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportUnusedCallResult=false, reportImplicitStringConcatenation=false, reportIndexIssue=false
from __future__ import annotations

import json
from pathlib import Path

from acp.results.energy_graph import (
    build_energy_graph_from_job,
    build_optimization_energy_graph,
    build_s2_energy_graph,
)


def _s2_payload() -> dict:
    return {
        "status": "ready_for_review",
        "protocol": {"coordinate": {"kind": "distance", "unit": "angstrom"}},
        "scan": {
            "quality": {"scan_complete": True},
            "frames": [
                {
                    "index": 0,
                    "target_coordinate": 1.2,
                    "actual_coordinate": 1.21,
                    "geometry_path": "frame_000.xyz",
                    "scan_energy_hartree": -100.0,
                    "single_point_energy_hartree": -100.1,
                    "optimization_converged": True,
                    "single_point_status": "completed",
                },
                {
                    "index": 1,
                    "target_coordinate": 1.4,
                    "actual_coordinate": 1.39,
                    "geometry_path": "frame_001.xyz",
                    "scan_energy_hartree": -99.9,
                    "single_point_energy_hartree": -99.8,
                    "optimization_converged": False,
                    "single_point_status": "failed",
                },
            ],
        },
        "energy_profile": {
            "energy_source": "single_point",
            "relative_energies_kcal_mol": [0.0, 18.8],
            "raw_hartree": [-100.1, -99.8],
            "sp_incomplete": True,
        },
        "recommendations": {
            "ts": [
                {
                    "candidate_id": "ts_guess_001",
                    "kind": "ts",
                    "frame_index": 1,
                    "confidence": "high",
                    "reason": "local maximum",
                }
            ],
            "intermediates": [],
        },
        "review": {"selected_ts": ["ts_guess_001"], "selected_intermediates": []},
    }


def test_s2_projection_contains_series_nodes_and_annotations() -> None:
    graph = build_s2_energy_graph("job-1", _s2_payload())

    assert graph["view_type"] == "scan"
    assert graph["default_series"] == "single_point_energy"
    assert {item["id"] for item in graph["series"]} >= {
        "relative_energy",
        "scan_energy",
        "single_point_energy",
        "convergence",
    }
    assert len(graph["nodes"]) == 2
    assert any(item["type"] == "ts" and item["selected"] for item in graph["annotations"])
    assert any(item["type"] == "failed" for item in graph["annotations"])
    assert any(item["type"] == "minimum" for item in graph["annotations"])


def test_pes_profile_v2_projection_uses_canonical_fields() -> None:
    payload = {
        "schema_version": "pes_profile_v2",
        "workflow": "PESsearch",
        "mode": "bond_length_scan",
        "status": "completed",
        "coordinate": {"kind": "distance", "unit": "angstrom"},
        "protocol": {"coordinate": {"kind": "distance", "unit": "angstrom"}},
        "scan_dir": "WORK/07_PATH/pes_scan_001",
        "frames": [
            {
                "index": 0,
                "target_coordinate": 1.2,
                "actual_coordinate": 1.2,
                "geometry_path": "scan_frames/frame_000.xyz",
                "scan_energy_hartree": -10.0,
                "optimization_converged": True,
                "single_point_status": "skipped",
            }
        ],
        "profile": {
            "energy_source": "scan",
            "relative_energies_kcal_mol": [0.0],
            "raw_hartree": [-10.0],
        },
        "quality": {"scan_complete": True},
        "ts_candidates": [],
        "int_candidates": [],
    }

    graph = build_energy_graph_from_job(
        "pes-job",
        workflow="PESsearch",
        method={"mode": "bond_length_scan"},
        work_dir=Path("."),
        s2_payload=payload,
    )

    assert graph["title"] == "PESsearch 扫描能量"
    assert graph["source"] == "RESULT/pes_search/pes_profile.json"
    assert graph["nodes"][0]["geometry_ref"] == "scan_frames/frame_000.xyz"


def test_optimization_projection_reads_existing_result_product(tmp_path: Path) -> None:
    path = tmp_path / "RESULT" / "trajectories" / "optimization.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "scf_energies": [-10.0, -10.1, -10.2],
                "gradients_rms": [0.2, 0.05, 0.001],
                "converged": True,
            }
        ),
        encoding="utf-8",
    )

    graph = build_optimization_energy_graph("job-2", tmp_path)

    assert graph is not None
    assert graph["view_type"] == "optimization"
    assert graph["complete"] is True
    assert len(graph["nodes"]) == 3
    assert {item["id"] for item in graph["series"]} == {
        "relative_energy",
        "scf_energy",
        "rms_gradient",
    }


def test_legacy_energy_projection_merges_thermo_sources(tmp_path: Path) -> None:
    result_dir = tmp_path / "RESULT"
    energy_dir = result_dir / "energies"
    energy_dir.mkdir(parents=True)
    ensemble_payload = {
        "method": "dft_table",
        "temperature_k": 298.15,
        "total_gibbs_hartree": -10.0,
        "conformers": [
            {
                "conf_id": "CONF1",
                "gibbs_hartree": -10.0,
                "delta_gibbs_kcal_mol": 0.0,
                "weight": 0.7,
            },
            {
                "conf_id": "CONF2",
                "gibbs_hartree": -9.99,
                "delta_gibbs_kcal_mol": 6.275,
                "weight": 0.2,
            },
            {
                "conf_id": "CONF3",
                "gibbs_hartree": -9.98,
                "delta_gibbs_kcal_mol": 12.55,
                "weight": 0.1,
            },
        ],
    }
    (energy_dir / "ensemble_thermo.json").write_text(json.dumps(ensemble_payload), encoding="utf-8")
    (energy_dir / "conformer_thermo.csv").write_text(
        "index,rank,energy_hartree,gibbs_correction,gibbs_hartree,h_correction,u_correction,"
        "s_total,g_conc,weight,source\n"
        "0,1,-10.1,0,-10.0,0,0,0,0,0.7,CONF1\n"
        "1,2,-9.9,0,-9.99,0,0,0,0,0.2,CONF2\n"
        "2,3,-9.8,0,-9.98,0,0,0,0,0.1,CONF3\n"
        "TOTAL,,,,,,,,,,ensemble_total\n",
        encoding="utf-8",
    )
    (result_dir / "result_manifest.json").write_text(
        json.dumps(
            {
                "products": [
                    {
                        "id": "CONF1",
                        "kind": "structure",
                        "path": "structures/CONF1.xyz",
                    }
                ],
                "status": "completed",
                "version": 2,
                "workflow": "energy",
            }
        ),
        encoding="utf-8",
    )

    graph = build_energy_graph_from_job("job", workflow="energy", method=None, work_dir=tmp_path)

    assert graph["view_type"] == "conformer"
    assert graph["default_series"] == "relative_gibbs"
    assert [node["x"] for node in graph["nodes"]] == [1, 2, 3]
    assert len(graph["nodes"]) == 3
    assert any(
        item["type"] == "minimum" and item["frame_index"] == 0 for item in graph["annotations"]
    )
    assert "boltzmann_weight" in {item["id"] for item in graph["series"]}
    assert graph["source"] == "RESULT/energies/ensemble_thermo.json"
    assert graph["nodes"][0]["geometry_ref"] == "RESULT/structures/CONF1.xyz"


def test_legacy_single_conformer_energy_projection(tmp_path: Path) -> None:
    energy_dir = tmp_path / "RESULT" / "energies"
    energy_dir.mkdir(parents=True)
    (energy_dir / "ensemble_thermo.json").write_text(
        json.dumps(
            {
                "method": "dft_table",
                "temperature_k": 298.15,
                "total_gibbs_hartree": -671.0468225,
                "conformers": [
                    {
                        "conf_id": "CONF1",
                        "gibbs_hartree": -671.0468225,
                        "delta_gibbs_kcal_mol": 0.0,
                        "weight": 1.0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (energy_dir / "conformer_thermo.csv").write_text(
        "index,rank,energy_hartree,gibbs_correction,gibbs_hartree,h_correction,u_correction,"
        "s_total,g_conc,weight,source\n"
        "0,1,-671.2467735882,-671.0498413000,-671.0468225000,-670.9996560000,"
        "-671.0006002000,441.93,-671.0468225000,1.000000,CONF1\n"
        "TOTAL,,,,-671.0468225000,,,,,,ensemble_total\n",
        encoding="utf-8",
    )

    graph = build_energy_graph_from_job("single", workflow="energy", method=None, work_dir=tmp_path)

    assert graph["view_type"] == "conformer"
    assert len(graph["nodes"]) == 1
    assert graph["nodes"][0]["label"] == "CONF1"
    assert graph["nodes"][0]["x"] == 1


def test_confsearch_manifest_builds_conformer_energy_projection(tmp_path: Path) -> None:
    manifest_dir = tmp_path / "RESULT" / "confsearch"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "confsearch_manifest.json").write_text(
        json.dumps(
            {
                "version": 1,
                "status": "completed",
                "conformers": [
                    {
                        "conf_id": "CONF1",
                        "geometry": "conformers/CONF1.xyz",
                        "energy_hartree": -10.1,
                        "free_energy_hartree": -10.0,
                        "relative_energy_kcal": 0.0,
                        "boltzmann_weight": 0.8,
                        "rank": 1,
                    },
                    {
                        "conf_id": "CONF2",
                        "geometry": "conformers/CONF2.xyz",
                        "energy_hartree": -10.0,
                        "free_energy_hartree": -9.99,
                        "relative_energy_kcal": 6.275,
                        "boltzmann_weight": 0.2,
                        "rank": 2,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    graph = build_energy_graph_from_job(
        "confsearch", workflow="Confsearch", method=None, work_dir=tmp_path
    )

    assert graph["view_type"] == "conformer"
    assert graph["default_series"] == "relative_gibbs"
    assert len(graph["nodes"]) == 2
    assert graph["nodes"][0]["geometry_ref"].startswith("RESULT/confsearch/")
    assert {item["id"] for item in graph["series"]} == {
        "relative_gibbs",
        "gibbs_energy",
        "absolute_energy",
        "boltzmann_weight",
    }


def test_energy_workflow_without_data_returns_unavailable_projection(tmp_path: Path) -> None:
    graph = build_energy_graph_from_job(
        "missing", workflow="energy", method=None, work_dir=tmp_path
    )

    assert graph["view_type"] == "unsupported"
    assert graph["status"] == "unavailable"
    assert graph["metadata"]["reason"] == "energy_data_missing"


def test_unrecognized_workflow_returns_unavailable_projection(tmp_path: Path) -> None:
    graph = build_energy_graph_from_job("nmr-job", workflow="nmr", method=None, work_dir=tmp_path)

    assert graph["view_type"] == "unsupported"
    assert graph["status"] == "unavailable"
    assert graph["metadata"] == {
        "reason": "workflow_has_no_energy_graph",
        "workflow": "nmr",
    }
