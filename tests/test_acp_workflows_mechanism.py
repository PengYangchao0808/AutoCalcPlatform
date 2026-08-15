# pyright: reportAny=false, reportPrivateUsage=false, reportPrivateImportUsage=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnknownMemberType=false, reportUnknownLambdaType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnusedVariable=false
"""Tests for the ACP mechanism workflow (reaction-path pipeline)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
from numpy.typing import NDArray

import acp.workflows.mechanism as mechanism_workflow
from acp.cli import build_parser
from acp.core.models import Structure
from acp.mechanism.candidates import select_candidates, select_primary_ts
from acp.mechanism.identity import classify_ts_identity, compute_mode_match_score
from acp.mechanism.models import PathPoint, PathResult
from acp.mechanism.presets import FIDELITY_PROFILES, resolve_fidelity_profile, resolve_strategy
from acp.mechanism.rescue import build_rescue_plan
from acp.scheduler.jobs import SUPPORTED_WORKFLOWS, JobSpec, mechanism_method_flags
from acp.scheduler.runner import JobRunner
from acp.scheduler.stage_tasks import get_stage_plan
from cccp.config import _get_default_config
from cccp.qc.interfaces.constraints import CoordinateSpec, ReactionCoordinatePlan

_MECHANISM_STAGES = [
    "prepare_reaction",
    "reactant_optimize",
    "product_optimize",
    "path_search",
    "candidate_refine",
    "ts_optimize",
    "ts_validate",
    "irc_validate",
    "energy_analysis",
]


def test_get_mechanism_stages_returns_9_stages() -> None:
    stages = mechanism_workflow.get_mechanism_stages()

    assert [stage.name for stage in stages] == _MECHANISM_STAGES


def test_mechanism_stage_plan_provider() -> None:
    plan = get_stage_plan(JobSpec(workflow="mechanism"))

    assert [stage.stage_name for stage in plan] == _MECHANISM_STAGES


def test_mechanism_in_supported_workflows() -> None:
    assert "mechanism" in SUPPORTED_WORKFLOWS


def test_mechanism_cli_help() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "acp.cli", "run", "mechanism", "--help"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "--input" in result.stdout
    assert "--output" in result.stdout
    assert "--product" in result.stdout
    assert "--ts-guess" in result.stdout
    assert "--preset" in result.stdout
    assert "--strategy" in result.stdout
    assert "--fidelity" in result.stdout
    assert "--routes" in result.stdout
    assert "--scan-points" in result.stdout
    assert "--irc-points" in result.stdout
    assert "--study-id" in result.stdout
    assert "--conformer-mode" in result.stdout
    assert "--max-elementary-steps" in result.stdout
    assert "--int-extension" in result.stdout
    assert "--promotion-policy" in result.stdout
    assert "--auto-converge" in result.stdout
    assert "placeholder" not in result.stdout.lower()


def test_mechanism_runner_cmd_legacy(tmp_path: Path) -> None:
    runner = JobRunner(python_executable="python")
    spec = JobSpec(
        workflow="mechanism",
        name="rxn",
        input={"source": "CCO", "charge": 1, "multiplicity": 2},
        resources={"nproc": 8, "mem": "16GB"},
        config_path="mechanism.yaml",
    )

    cmd = runner._build_cmd(spec, tmp_path)

    assert cmd == [
        "python",
        "-m",
        "acp.cli",
        "run",
        "mechanism",
        "--input",
        "CCO",
        "--output",
        str(tmp_path),
        "--name",
        "rxn",
        "--config",
        "mechanism.yaml",
        "--nproc",
        "8",
        "--mem",
        "16GB",
        "--charge",
        "1",
        "--multiplicity",
        "2",
    ]


def test_mechanism_runner_cmd_rph_payload(tmp_path: Path) -> None:
    runner = JobRunner(python_executable="python")
    spec = JobSpec(
        workflow="mechanism",
        name="rxn",
        input={
            "source": "C=C",
            "product": "CC",
            "routes": [
                {
                    "route_id": "r1",
                    "coordinate_plan": {
                        "coordinates": [
                            {
                                "id": "rc1",
                                "kind": "distance",
                                "atoms": [0, 1],
                                "start": 3.2,
                                "end": 1.55,
                            }
                        ],
                        "points": 21,
                    },
                    "path_strategy": "guided-scan",
                    "fidelity": "s3",
                }
            ],
        },
        method={"preset": "rph-s4"},
        resources={"nproc": 8, "mem": "16GB"},
    )

    cmd = runner._build_cmd(spec, tmp_path)

    assert "--product" in cmd
    assert "CC" in cmd
    assert "--routes" in cmd
    assert "--preset" in cmd and "rph-s4" in cmd


def test_mechanism_method_flags() -> None:
    assert mechanism_method_flags({"preset": "rph-s4"}) == ["--preset", "rph-s4"]
    assert mechanism_method_flags({"strategy": "rph-reverse", "fidelity": "s3"}) == [
        "--strategy",
        "rph-reverse",
        "--fidelity",
        "s3",
    ]
    assert mechanism_method_flags({"scan_points": 21, "irc_points": 30}) == [
        "--scan-points",
        "21",
        "--irc-points",
        "30",
    ]
    assert mechanism_method_flags(
        {
            "conformer_mode": "xtb-fast",
            "max_elementary_steps": 4,
            "promotion_policy": "rate_relevant",
            "study_id": "study_001",
            "int_extension": True,
            "auto_converge": True,
        }
    ) == [
        "--conformer-mode",
        "xtb-fast",
        "--max-elementary-steps",
        "4",
        "--promotion-policy",
        "rate_relevant",
        "--study-id",
        "study_001",
        "--int-extension",
        "--auto-converge",
    ]


def test_mechanism_method_flags_round_trip_parse() -> None:
    parser = build_parser()
    cli_args = [
        "run",
        "mechanism",
        "--input",
        "C=C",
        "--output",
        "./out",
        *mechanism_method_flags({"scan_points": 21, "irc_points": 30}),
    ]

    args = parser.parse_args(cli_args)

    assert args.scan_points == 21
    assert args.irc_points == 30


def test_mechanism_method_flags_round_trip_parse_study_controls() -> None:
    parser = build_parser()
    cli_args = [
        "run",
        "mechanism",
        "--input",
        "C=C",
        "--output",
        "./out",
        *mechanism_method_flags(
            {
                "conformer_mode": "xtb-fast",
                "max_elementary_steps": 4,
                "promotion_policy": "rate_relevant",
                "study_id": "study_001",
                "int_extension": True,
                "auto_converge": True,
            }
        ),
    ]

    args = parser.parse_args(cli_args)

    assert args.conformer_mode == "xtb-fast"
    assert args.max_elementary_steps == 4
    assert args.promotion_policy == "rate_relevant"
    assert args.study_id == "study_001"
    assert args.int_extension is True
    assert args.auto_converge is True


def test_mechanism_runner_cmd_study_payload(tmp_path: Path) -> None:
    runner = JobRunner(python_executable="python")
    spec = JobSpec(
        workflow="mechanism",
        name="rxn",
        input={"source": "C=C", "product": "CC"},
        method={
            "study_id": "study_001",
            "conformer_mode": "xtb-fast",
            "max_elementary_steps": 4,
            "promotion_policy": "rate_relevant",
            "int_extension": True,
            "auto_converge": True,
        },
        resources={"nproc": 8, "mem": "16GB"},
    )

    cmd = runner._build_cmd(spec, tmp_path)

    assert "--study-id" in cmd and "study_001" in cmd
    assert "--conformer-mode" in cmd and "xtb-fast" in cmd
    assert "--max-elementary-steps" in cmd and "4" in cmd
    assert "--promotion-policy" in cmd and "rate_relevant" in cmd
    assert "--int-extension" in cmd
    assert "--auto-converge" in cmd


def test_mechanism_coordinate_plan_interpolation() -> None:
    plan = ReactionCoordinatePlan(
        coordinates=(
            CoordinateSpec(id="rc1", kind="distance", atoms=(4, 12), start=3.2, end=1.55),
            CoordinateSpec(id="rc2", kind="distance", atoms=(7, 8), start=1.47, end=2.80),
            CoordinateSpec(
                id="rc3",
                kind="dihedral",
                atoms=(3, 4, 5, 6),
                role="freeze",
                start=45.0,
            ),
        ),
        points=21,
    )

    targets_mid = plan.coordinate_targets(10)
    assert abs(targets_mid["rc1"] - (3.2 + 0.5 * (1.55 - 3.2))) < 1e-9
    assert abs(targets_mid["rc2"] - (1.47 + 0.5 * (2.80 - 1.47))) < 1e-9
    assert targets_mid["rc3"] == 45.0  # freeze pinned at start
    constraints = plan.frame_constraints(0)
    assert len(constraints) == 3  # drive + freeze, monitor excluded


def test_mechanism_strategy_and_fidelity_presets() -> None:
    assert resolve_strategy(None) == "guided-scan"
    assert resolve_strategy("rph-reverse") == "rph-reverse"
    profile = resolve_fidelity_profile("guided-scan", "s4")
    assert profile.ts_method == "M062X"
    assert profile.sp_method == "wB97M-V"
    assert profile.irc_points == 40
    assert "s3" in FIDELITY_PROFILES and "s4" in FIDELITY_PROFILES


def test_mechanism_candidate_selection() -> None:
    energies = [0.0, 0.5, 1.0, 0.7, 0.3, 0.6, 0.4, 0.35, 0.32, 0.30, 0.28]
    points = [
        PathPoint(point_id=f"p{i:03d}", progress=i / 10, energies_hartree={"gfn2-xtb": e})
        for i, e in enumerate(energies)
    ]
    candidates = select_candidates(points, energy_key="gfn2-xtb")

    kinds = {c.kind for c in candidates}
    assert "ts_seed" in kinds
    assert "intermediate_seed" in kinds
    assert "endpoint" in kinds
    primary = select_primary_ts(
        PathResult(points=points, candidates=candidates, strategy="guided-scan", route_id="r")
    )
    assert primary is not None and primary.point_id == "p002"


def test_mechanism_mode_match_score() -> None:
    plan = ReactionCoordinatePlan(
        coordinates=(CoordinateSpec(id="rc1", kind="distance", atoms=(0, 1), start=3.0, end=1.5),)
    )
    geometry = np.array([[0.0, 0.0, 0.0], [3.0, 0.0, 0.0], [1.5, 1.0, 0.0]])

    driving_mode = np.zeros((3, 3))
    driving_mode[0, 0] = 0.6
    driving_mode[1, 0] = -0.6  # stretches atoms 0-1 (the drive coordinate)
    score = compute_mode_match_score(driving_mode, plan, geometry=geometry)
    assert score is not None and score >= 0.05

    non_driving_mode = np.zeros((3, 3))
    non_driving_mode[2, 1] = 0.5  # rotates atom 2, not in the drive coordinate
    score2 = compute_mode_match_score(non_driving_mode, plan, geometry=geometry)
    assert score2 is not None and score2 < 0.05

    identity = classify_ts_identity([-312.4], mode_match_score=score)
    assert identity.valid
    assert identity.imaginary_count == 1
    identity_bad = classify_ts_identity([-312.4], mode_match_score=score2)
    assert not identity_bad.valid


def test_mechanism_rescue_matrix() -> None:
    plan = build_rescue_plan("geometry_not_converged", "ts")
    assert [a.strategy for a in plan.actions] == [
        "fresh_hessian_restart",
        "ts_mode_directed",
        "calcall_opt",
    ]
    assert not plan.terminal
    terminal = build_rescue_plan("scf_failure", "ts")
    assert terminal.terminal


def _mock_backends(
    coords: NDArray[np.float64],
    symbols: list[str],
) -> tuple[MagicMock, MagicMock, dict[str, object]]:
    fake_xtb = MagicMock()
    scan_res = MagicMock()
    scan_res.points = []
    energies = [-74.0, -73.6, -73.0, -73.7, -74.2]  # peak at frame 2
    for i in range(5):
        p = MagicMock()
        p.frame_index = i
        p.progress = i / 4
        p.coordinates = coords.copy() + i * 0.1
        p.symbols = symbols
        p.energy_hartree = energies[i]
        p.success = True
        p.coordinate_values = {"rc1": 3.2 - i * 0.4}
        scan_res.points.append(p)
    scan_res.success = True
    scan_res.message = ""
    fake_xtb.relaxed_scan.return_value = scan_res

    fake_orca = MagicMock()
    fake_orca.optimize.return_value = MagicMock(
        success=True,
        coordinates=coords.copy(),
        energy=-75.0,
    )
    fake_orca.transition_state_opt.return_value = MagicMock(
        success=True,
        coordinates=coords.copy(),
        energy_hartree=-73.0,
        imaginary_frequencies=[-312.0],
        all_frequencies=[-312.0, 50.0, 80.0],
    )
    fake_orca.irc.return_value = MagicMock(
        success=True,
        forward_points=10,
        reverse_points=12,
        endpoints={"forward": "f", "reverse": "r"},
    )

    def fake_get_backend(name: str):
        if name == "xtb":
            return lambda cfg: fake_xtb
        if name == "orca":
            return lambda cfg: fake_orca
        raise KeyError(name)

    return fake_xtb, fake_orca, {"get_backend": fake_get_backend}


def test_run_mechanism_analysis_end_to_end(tmp_path: Path, monkeypatch) -> None:
    coords = np.array([[0.0, 0.0, 0.0], [1.2, 0.0, 0.0], [2.4, 0.0, 0.0]])
    symbols = ["C", "O", "O"]

    monkeypatch.setattr(
        mechanism_workflow,
        "load_config",
        lambda *args, **kwargs: _get_default_config(),
    )
    monkeypatch.setattr(
        mechanism_workflow.StructureReader,
        "read",
        lambda self, source, charge=None, multiplicity=None: Structure(
            id="mechanism_input",
            charge=int(charge or 0),
            multiplicity=int(multiplicity or 1),
            symbols=symbols,
            coordinates=coords,
            metadata={"smiles": "O=C=O"},
        ),
    )

    fake_xtb, fake_orca, fake = _mock_backends(coords, symbols)
    with patch("acp.backends.registry.get_backend", side_effect=fake["get_backend"]):
        routes = [
            {
                "route_id": "r1",
                "coordinate_plan": {
                    "coordinates": [
                        {
                            "id": "rc1",
                            "kind": "distance",
                            "atoms": [0, 2],
                            "start": 3.2,
                            "end": 1.55,
                        }
                    ],
                    "points": 5,
                },
                "path_strategy": "guided-scan",
                "fidelity": "s3",
            }
        ]
        result = mechanism_workflow.run_mechanism_analysis(
            "O=C=O",
            output_dir=tmp_path,
            name="co2",
            routes=routes,
            strategy="guided-scan",
            fidelity="s3",
        )

    assert result.status == "completed"
    assert result.ensemble is not None
    assert result.stages_completed == _MECHANISM_STAGES
    assert result.metadata["molecule_name"] == "co2"

    mech = result.metadata.get("mechanism", {})
    path_result = mech.get("path_result", {})
    assert path_result.get("strategy") == "guided-scan"
    candidate_kinds = {c.get("kind") for c in path_result.get("candidates", [])}
    assert "ts_seed" in candidate_kinds
    assert mech.get("ts_validation", {}).get("valid") is True

    irc_stage = result.metadata.get("stage_results", {}).get("irc_validate", {})
    assert irc_stage.get("forward_points") == 10

    profile = result.metadata.get("energy_profile", {})
    assert profile.get("reactant_id") == "co2"

    state_path = tmp_path / "co2" / "state.json"
    assert state_path.exists()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["status"] == "completed"
    assert list(state["stages"].keys()) == result.stages_completed


def test_run_mechanism_analysis_requires_route(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        mechanism_workflow,
        "load_config",
        lambda *args, **kwargs: _get_default_config(),
    )
    monkeypatch.setattr(
        mechanism_workflow.StructureReader,
        "read",
        lambda self, source, charge=None, multiplicity=None: Structure(
            id="mechanism_input",
            charge=0,
            multiplicity=1,
            symbols=["C", "H", "H", "H", "H"],
            coordinates=np.array(
                [
                    [0.0, 0.0, 0.0],
                    [0.6, 0.6, 0.6],
                    [-0.6, -0.6, 0.6],
                    [-0.6, 0.6, -0.6],
                    [0.6, -0.6, -0.6],
                ]
            ),
            metadata={"smiles": "C"},
        ),
    )

    result = mechanism_workflow.run_mechanism_analysis("C", output_dir=tmp_path, name="methane")

    # No route / no ts_guess → path_search raises and the workflow fails.
    assert result.status != "completed"
