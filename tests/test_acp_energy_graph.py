"""Tests for normalized energy-workspace projections."""

# pyright: reportMissingTypeArgument=false, reportAny=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportUnusedCallResult=false, reportImplicitStringConcatenation=false, reportIndexIssue=false
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from acp.results.energy_graph import (
    build_conformer_energy_graph,
    build_energy_graph_from_job,
    build_mechanism_energy_graph,
    build_optimization_energy_graph,
    build_s2_energy_graph,
    build_scan_trajectory_energy_graph,
    find_optimization_trajectory,
)

NODE_WIRE_KEYS = {
    "id",
    "label",
    "type",
    "frame_index",
    "x",
    "energy",
    "status",
    "geometry_ref",
    "metadata",
}
ANNOTATION_WIRE_KEYS = {
    "id",
    "type",
    "label",
    "frame_index",
    "x",
    "y",
    "status",
    "geometry_ref",
    "selected",
}
ANNOTATION_CANDIDATE_KEYS = ANNOTATION_WIRE_KEYS | {
    "active",
    "saved",
    "candidate_id",
    "recommended_type",
    "selection_source",
    "confidence",
    "reason",
}


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


def _scan_trajectory_payload() -> dict[str, Any]:
    indices = [0, 1, 2, 4, 5, 6, 7, 8]
    energies = [-10.0, -9.8, -10.1, None, -10.2, -10.05, -10.3, -10.0]
    return {
        "workflow": "scan",
        "frame_count": len(indices),
        "successful_frame_count": len(indices),
        "frames": [
            {
                "index": index,
                "path": f"structures/scan_frame_{index:03d}.xyz",
                "progress": position / (len(indices) - 1),
                "energy_hartree": energy,
                "coordinate_values": {"distance": 1.0 + position * 0.1},
            }
            for position, (index, energy) in enumerate(zip(indices, energies, strict=True))
        ],
    }


def test_standalone_scan_projection_preserves_frame_indices_and_annotations(
    tmp_path: Path,
) -> None:
    # Given: a result trajectory with one failed frame and a skipped frame index.
    trajectory_path = tmp_path / "RESULT" / "trajectories" / "scan_trajectory.json"
    trajectory_path.parent.mkdir(parents=True)
    trajectory_path.write_text(json.dumps(_scan_trajectory_payload()), encoding="utf-8")

    # When: the standalone scan trajectory is projected for the energy viewer.
    graph = build_scan_trajectory_energy_graph("scan-job", tmp_path)

    # Then: the projection keeps persisted indices and marks the failed frame.
    assert graph is not None
    assert graph["view_type"] == "scan"
    assert graph["title"] == "扫描能量剖面"
    assert graph["x_axis"] == {"label": "扫描距离", "unit": "Å"}
    assert len(graph["nodes"]) == 8
    assert [node["frame_index"] for node in graph["nodes"]] == [0, 1, 2, 4, 5, 6, 7, 8]
    assert all(node["geometry_ref"].startswith("RESULT/") for node in graph["nodes"])
    assert graph["nodes"][3]["energy"] is None
    assert graph["nodes"][3]["status"] == "failed"
    relative = next(item for item in graph["series"] if item["id"] == "relative_energy")
    finite_relative = [value for value in relative["values"] if value is not None]
    assert min(finite_relative) == pytest.approx(0.0)
    assert graph["default_series"] == "relative_energy"
    assert any(
        item["type"] == "minimum" and item["frame_index"] == 7 and item["y"] == 0.0
        for item in graph["annotations"]
    )
    assert any(
        item["type"] == "failed" and item["frame_index"] == 4 for item in graph["annotations"]
    )


def test_standalone_scan_missing_trajectory_returns_unavailable_projection(tmp_path: Path) -> None:
    # Given: a scan job without its result trajectory.
    # When: the workflow dispatch requests its energy graph.
    graph = build_energy_graph_from_job(
        "missing-scan", workflow="scan", method=None, work_dir=tmp_path
    )

    # Then: the caller receives the standard unavailable projection.
    assert graph["view_type"] == "unsupported"
    assert graph["status"] == "unavailable"
    assert graph["metadata"] == {"reason": "energy_data_missing", "workflow": "scan"}


@pytest.mark.parametrize(
    "content",
    [
        "{",
        json.dumps({"workflow": "scan"}),
        json.dumps({"frames": []}),
        json.dumps({"frames": [{}]}),
    ],
)
def test_standalone_scan_malformed_trajectory_returns_none(tmp_path: Path, content: str) -> None:
    # Given: corrupt JSON or a trajectory with no usable indexed frames.
    trajectory_path = tmp_path / "RESULT" / "trajectories" / "scan_trajectory.json"
    trajectory_path.parent.mkdir(parents=True)
    trajectory_path.write_text(content, encoding="utf-8")

    # When: the standalone scan builder reads the trajectory.
    graph = build_scan_trajectory_energy_graph("malformed-scan", tmp_path)

    # Then: malformed input is treated as missing graph data.
    assert graph is None


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

    assert graph["title"] == "PES 扫描能量"
    assert graph["source"] == "RESULT/pes_search/pes_profile.json"
    assert graph["nodes"][0]["geometry_ref"] == "scan_frames/frame_000.xyz"


def test_four_view_projections_preserve_pre_migration_wire_key_sets(tmp_path: Path) -> None:
    # Given: one representative projection for each pre-migration graph builder.
    optimization_path = tmp_path / "RESULT" / "trajectories" / "optimization.json"
    optimization_path.parent.mkdir(parents=True)
    optimization_path.write_text(
        json.dumps(_cycle_payload([{"cycle": 1, "energy_hartree": -10.0}]))
    )
    conformer_path = tmp_path / "RESULT" / "confsearch" / "confsearch_manifest.json"
    conformer_path.parent.mkdir(parents=True)
    conformer_path.write_text(
        json.dumps(
            {
                "status": "completed",
                "conformers": [
                    {
                        "conf_id": "CONF1",
                        "free_energy_hartree": -10.0,
                        "relative_energy_kcal": 0.0,
                        "rank": 1,
                    }
                ],
            }
        )
    )
    mechanism = build_mechanism_energy_graph(
        "mechanism",
        {
            "mechanism_profile": {
                "routes": [
                    {
                        "route_id": "route-1",
                        "status": "completed",
                        "methods": {
                            "orca": [
                                {
                                    "point_id": "p0",
                                    "progress": 0.0,
                                    "energy_hartree": -10.0,
                                }
                            ]
                        },
                        "refined_stationary_points": [
                            {"point_id": "p0", "role": "ts", "canonical": True}
                        ],
                    }
                ]
            }
        },
    )
    scan_payload = _s2_payload()
    scan_payload["schema_version"] = "pes_profile_v2"
    graphs = [
        build_s2_energy_graph("scan", scan_payload),
        build_optimization_energy_graph("optimization", tmp_path),
        build_conformer_energy_graph("conformer", tmp_path),
        mechanism,
    ]

    # When: each projection is inspected at the frontend wire boundary.
    expected_annotation_key_sets = [
        {frozenset(ANNOTATION_WIRE_KEYS), frozenset(ANNOTATION_CANDIDATE_KEYS)},
        {frozenset(ANNOTATION_WIRE_KEYS)},
        {frozenset(ANNOTATION_WIRE_KEYS)},
        {frozenset(ANNOTATION_WIRE_KEYS)},
    ]
    for graph, expected_keys in zip(graphs, expected_annotation_key_sets, strict=True):
        assert graph is not None
        assert graph["nodes"]
        assert {frozenset(node) for node in graph["nodes"]} == {frozenset(NODE_WIRE_KEYS)}
        assert {frozenset(annotation) for annotation in graph["annotations"]} == expected_keys

    # Then: registry-backed titles replace only the two stale labels.
    assert graphs[0]["title"] == "PES 扫描能量"
    assert graphs[1]["title"] == "几何优化轨迹"
    assert graphs[2]["title"] == "构象能量分布"
    assert graphs[3]["title"] == "反应路径能量图"


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
    assert graph["title"] == "几何优化轨迹"
    assert graph["complete"] is True
    assert len(graph["nodes"]) == 3
    assert {item["id"] for item in graph["series"]} == {
        "relative_energy",
        "scf_energy",
        "rms_gradient",
    }
    assert graph["metadata"]["quality"]["status"] == "complete"


def test_public_optimization_trajectory_lookup_returns_empty_result(tmp_path: Path) -> None:
    assert find_optimization_trajectory(tmp_path) == (None, None)


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
                    {
                        "cycle": 1,
                        "energy_hartree": -10.0,
                        "rms_gradient": 0.2,
                        "max_gradient": 0.4,
                        "rms_displacement": 0.01,
                        "max_displacement": 0.02,
                    },
                    {
                        "cycle": 2,
                        "energy_hartree": -10.1,
                        "rms_gradient": 0.01,
                        "max_gradient": 0.02,
                        "rms_displacement": 0.005,
                        "max_displacement": 0.01,
                    },
                    {
                        "cycle": 3,
                        "energy_hartree": -10.2,
                        "rms_gradient": 0.001,
                        "max_gradient": 0.002,
                        "rms_displacement": 0.0005,
                        "max_displacement": 0.001,
                    },
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
                [
                    {
                        "cycle": 1,
                        "energy_hartree": -10.0,
                        "rms_gradient": 0.001,
                        "max_gradient": 0.002,
                    }
                ]
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


def _opt_payload(cycles, **overrides):
    """Build an optimization trajectory payload for min/max annotation tests."""
    payload = {
        "schema_version": 1,
        "status": "completed",
        "converged": True,
        "current_cycle": len(cycles),
        "cycles": cycles,
    }
    payload.update(overrides)
    return payload


def _write_opt(tmp_path, payload):
    path = tmp_path / "RESULT" / "trajectories" / "optimization.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_optimization_five_cycle_annotations_min_max(tmp_path):
    """Five distinct-energy cycles produce both minimum and maximum annotations."""
    _write_opt(
        tmp_path,
        _opt_payload(
            [
                {"cycle": 1, "energy_hartree": -10.0},
                {"cycle": 2, "energy_hartree": -10.1},
                {"cycle": 3, "energy_hartree": -9.9},
                {"cycle": 4, "energy_hartree": -10.2},
                {"cycle": 5, "energy_hartree": -10.05},
            ]
        ),
    )

    graph = build_optimization_energy_graph("job-five", tmp_path)

    assert graph is not None
    annotations = graph["annotations"]
    ann_types = [a["type"] for a in annotations]
    assert ann_types.count("minimum") == 1
    assert ann_types.count("maximum") == 1
    min_ann = next(a for a in annotations if a["type"] == "minimum")
    max_ann = next(a for a in annotations if a["type"] == "maximum")
    assert min_ann["label"] == "最低能量周期"
    assert max_ann["label"] == "最高能量周期"
    assert min_ann["selected"] is False
    assert max_ann["selected"] is False
    # Cycle 4 has energy -10.2 (lowest); cycle 3 has -9.9 (highest)
    assert min_ann["frame_index"] == 3
    assert max_ann["frame_index"] == 2
    assert min_ann["x"] == 4.0
    assert max_ann["x"] == 3.0
    assert min_ann["y"] is not None
    assert max_ann["y"] is not None


def test_optimization_single_cycle_only_minimum(tmp_path):
    """Single-cycle trajectory emits minimum only, no maximum."""
    _write_opt(
        tmp_path,
        _opt_payload([{"cycle": 1, "energy_hartree": -10.0}]),
    )

    graph = build_optimization_energy_graph("job-one", tmp_path)

    assert graph is not None
    annotations = graph["annotations"]
    ann_types = [a["type"] for a in annotations]
    assert "minimum" in ann_types
    assert "maximum" not in ann_types
    assert len([a for a in annotations if a["type"] == "minimum"]) == 1


def test_optimization_all_none_energies_no_annotations(tmp_path):
    """All-None energies produce no min/max annotations without raising."""
    _write_opt(
        tmp_path,
        _opt_payload(
            [
                {"cycle": 1, "energy_hartree": None},
                {"cycle": 2, "energy_hartree": None},
            ]
        ),
    )

    graph = build_optimization_energy_graph("job-allnone", tmp_path)

    assert graph is not None
    annotations = graph["annotations"]
    assert all(a["type"] not in {"minimum", "maximum"} for a in annotations)


def test_optimization_min_max_coincide_skips_maximum(tmp_path):
    """When minimum and maximum coincide, only minimum is emitted."""
    _write_opt(
        tmp_path,
        _opt_payload(
            [
                {"cycle": 1, "energy_hartree": -10.0},
                {"cycle": 2, "energy_hartree": -10.0},
            ]
        ),
    )

    graph = build_optimization_energy_graph("job-same", tmp_path)

    assert graph is not None
    annotations = graph["annotations"]
    ann_types = [a["type"] for a in annotations]
    assert "minimum" in ann_types
    assert "maximum" not in ann_types
    assert len([a for a in annotations if a["type"] == "minimum"]) == 1


def test_optimization_annotations_do_not_alter_series_x_axis_nodes(tmp_path):
    """Annotations are additive: series, x_axis, and node metadata are preserved."""
    _write_opt(
        tmp_path,
        _opt_payload(
            [
                {"cycle": 1, "energy_hartree": -10.0, "rms_gradient": 0.1},
                {"cycle": 2, "energy_hartree": -10.1, "rms_gradient": 0.01},
                {"cycle": 3, "energy_hartree": -9.9, "rms_gradient": 0.001},
            ]
        ),
    )

    graph = build_optimization_energy_graph("job-preserve", tmp_path)

    assert graph is not None
    assert len(graph["nodes"]) == 3
    assert graph["x_axis"] == {"label": "优化周期", "unit": "cycle"}
    series_ids = {item["id"] for item in graph["series"]}
    assert "relative_energy" in series_ids
    assert "scf_energy" in series_ids
    # Nodes still have the same wire shape
    for node in graph["nodes"]:
        assert set(node.keys()) == NODE_WIRE_KEYS
    # Annotations do not carry node metadata keys
    for ann in graph["annotations"]:
        assert ann["type"] in {"minimum", "maximum"}


def test_optimization_min_max_annotations_wire_key_parity(tmp_path):
    """Min/max annotations have exactly the standard annotation wire keys."""
    _write_opt(
        tmp_path,
        _opt_payload(
            [
                {"cycle": 1, "energy_hartree": -10.0},
                {"cycle": 2, "energy_hartree": -10.1},
                {"cycle": 3, "energy_hartree": -9.9},
            ]
        ),
    )

    graph = build_optimization_energy_graph("job-wire", tmp_path)

    assert graph is not None
    for ann in graph["annotations"]:
        assert frozenset(ann) == frozenset(ANNOTATION_WIRE_KEYS)
