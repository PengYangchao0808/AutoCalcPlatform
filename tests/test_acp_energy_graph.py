"""Tests for normalized energy-workspace projections."""

# pyright: reportMissingTypeArgument=false, reportAny=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportUnusedCallResult=false, reportImplicitStringConcatenation=false, reportIndexIssue=false
from __future__ import annotations

import json
from pathlib import Path

import pytest

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
    assert graph["metadata"]["quality"]["status"] == "complete"


def _cycle_payload(cycles: list[dict], **overrides) -> dict:
    payload = {
        "schema_version": 1,
        "item_id": "TS1",
        "status": "completed",
        "converged": True,
        "current_cycle": len(cycles),
        "cycles": cycles,
    }
    payload.update(overrides)
    return payload


def test_optimization_projection_includes_step_derivatives_and_quality(tmp_path: Path) -> None:
    path = tmp_path / "RESULT" / "trajectories" / "optimization.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            _cycle_payload(
                [
                    {"cycle": 1, "energy_hartree": -10.0, "rms_gradient": 0.2,
                     "max_gradient": 0.4, "rms_displacement": 0.01, "max_displacement": 0.02},
                    {"cycle": 2, "energy_hartree": -10.1, "rms_gradient": 0.01,
                     "max_gradient": 0.02, "rms_displacement": 0.005, "max_displacement": 0.01},
                    {"cycle": 3, "energy_hartree": -10.2, "rms_gradient": 0.001,
                     "max_gradient": 0.002, "rms_displacement": 0.0005, "max_displacement": 0.001},
                ],
                thresholds={"rms_gradient": 1e-4, "max_gradient": 3e-4},
            )
        ),
        encoding="utf-8",
    )

    graph = build_optimization_energy_graph("job-opt", tmp_path)

    assert graph is not None
    series_ids = {item["id"] for item in graph["series"]}
    assert series_ids >= {
        "relative_energy",
        "delta_energy",
        "rms_gradient_delta",
        "max_gradient_delta",
        "rms_displacement",
        "max_displacement",
    }
    quality = graph["metadata"]["quality"]
    assert quality["status"] == "complete"
    assert quality["n_cycles"] == 3
    assert quality["issues"] == []
    assert graph["metadata"]["thresholds"]["rms_gradient"] == pytest.approx(1e-4)
    delta_series = next(item for item in graph["series"] if item["id"] == "rms_gradient_delta")
    assert delta_series["values"][0] is None
    assert delta_series["values"][1] == pytest.approx(0.01 - 0.2)
    assert delta_series["values"][2] == pytest.approx(0.001 - 0.01)
    displacement = next(item for item in graph["series"] if item["id"] == "rms_displacement")
    assert displacement["unit"] == "bohr"


def test_optimization_projection_flags_single_cycle_as_partial(tmp_path: Path) -> None:
    """A converged lone cycle renders as a flat zero line; surface the doubt."""
    path = tmp_path / "RESULT" / "trajectories" / "optimization.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            _cycle_payload(
                [{"cycle": 1, "energy_hartree": -10.0, "rms_gradient": 0.001,
                  "max_gradient": 0.002}]
            )
        ),
        encoding="utf-8",
    )

    graph = build_optimization_energy_graph("job-one", tmp_path)

    assert graph is not None
    assert graph["complete"] is True
    assert graph["nodes"][0]["energy"] == 0.0
    quality = graph["metadata"]["quality"]
    assert quality["status"] == "partial"
    assert "single_cycle" in quality["issues"]


def test_optimization_projection_marks_energy_gaps_partial(tmp_path: Path) -> None:
    path = tmp_path / "RESULT" / "trajectories" / "optimization.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            _cycle_payload(
                [
                    {"cycle": 1, "energy_hartree": -10.0, "rms_gradient": 0.2},
                    {"cycle": 2, "rms_gradient": 0.01},
                    {"cycle": 3, "energy_hartree": -10.2, "rms_gradient": 0.001},
                ]
            )
        ),
        encoding="utf-8",
    )

    graph = build_optimization_energy_graph("job-gap", tmp_path)

    assert graph is not None
    quality = graph["metadata"]["quality"]
    assert quality["status"] == "partial"
    assert "energy_missing" in quality["issues"]
    assert quality["counts"]["energy_hartree"] == 2


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


def test_s2_projection_sanitizes_nan_actual_coordinates() -> None:
    payload = _s2_payload()
    payload["scan"]["frames"][0]["actual_coordinate"] = float("nan")
    payload["scan"]["frames"][0]["actual_coordinates"] = {"distance": float("nan")}
    payload["scan"]["frames"][0]["target_coordinates"] = {"distance": 1.2}
    payload["scan"]["frames"][1]["actual_coordinate"] = float("inf")
    payload["scan"]["quality"]["max_constraint_residual"] = float("nan")

    graph = build_s2_energy_graph("job-nan", payload)

    # Strict JSON compliance — what the FastAPI encoder requires.
    json.dumps(graph, allow_nan=False)
    metadata = graph["nodes"][0]["metadata"]
    assert metadata["actual_coordinate"] is None
    assert metadata["actual_coordinates"] == {"distance": None}
    assert metadata["target_coordinates"] == {"distance": 1.2}
    assert graph["nodes"][1]["metadata"]["actual_coordinate"] is None
    assert graph["metadata"]["max_constraint_residual"] is None


def test_build_energy_graph_from_job_sanitizes_nan_in_pes_payload(tmp_path: Path) -> None:
    payload = _s2_payload()
    payload["scan"]["frames"][0]["actual_coordinate"] = float("nan")
    payload["provenance"] = {"worst_residual": float("nan")}

    graph = build_energy_graph_from_job(
        "pes-nan",
        workflow="PESsearch",
        method={"mode": "bond_length_scan"},
        work_dir=tmp_path,
        s2_payload=payload,
    )

    json.dumps(graph, allow_nan=False)
    assert graph["view_type"] == "scan"
    assert graph["provenance"] == {"worst_residual": None}
    assert graph["nodes"][0]["metadata"]["actual_coordinate"] is None
