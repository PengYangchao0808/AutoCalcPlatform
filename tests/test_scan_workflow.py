"""Behavior tests for the standalone relaxed-scan calculation primitive."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from acp.calculations.contracts import CalculationRequest, JsonValue, StructureArtifact
from acp.calculations.primitives.scan import ScanCoordinateError, run_scan
from acp.storage.manifest import ProductKind, ResultManifest
from tests.conftest import FakeBackend


def _request(tmp_path: Path, coordinate: str = "0,1,1.0,1.5") -> CalculationRequest:
    input_path = tmp_path / "input.xyz"
    input_path.write_text("2\ninput\nH 0.0 0.0 0.0\nH 0.0 0.0 1.0\n", encoding="utf-8")
    resources: dict[str, JsonValue] = {
        "backend": "orca",
        "output_dir": str(tmp_path / "WORK" / "07_PATH" / "ORCA"),
        "scan_coordinates": [coordinate],
        "scan_points": 3,
    }
    return CalculationRequest(
        input_artifact=StructureArtifact(
            path=input_path,
            elements=["H", "H"],
            source="test",
        ),
        method="r2SCAN-3c",
        resources=resources,
        workflow="scan",
        profile="default",
    )


def test_run_scan_writes_frames_and_trajectory_product(
    fake_backend: FakeBackend, tmp_path: Path
) -> None:
    # Given: a valid two-atom coordinate and the in-process fake backend.
    request = _request(tmp_path)

    # When: the relaxed-scan primitive runs.
    result = run_scan(request)

    # Then: the backend receives a compiled coordinate plan.
    assert result.status == "completed"
    assert fake_backend.calls[0].method == "relaxed_scan"
    plan = fake_backend.calls[0].kwargs["plan"]
    assert plan.points == 3
    assert plan.coordinates[0].atoms == (0, 1)

    # And: each frame is persisted under RESULT/structures.
    structures_dir = tmp_path / "RESULT" / "structures"
    frame_paths = sorted(structures_dir.glob("scan_frame_*.xyz"))
    assert len(frame_paths) == 3
    assert all(path.is_file() for path in frame_paths)

    # And: the manifest registers a trajectory product and its frame products.
    manifest = ResultManifest.read(tmp_path / "RESULT")
    trajectory_products = [
        product for product in manifest.products if product.kind == ProductKind.TRAJECTORY
    ]
    assert len(trajectory_products) == 1
    trajectory_path = tmp_path / "RESULT" / trajectory_products[0].path
    assert trajectory_path.is_file()
    payload = json.loads(trajectory_path.read_text(encoding="utf-8"))
    assert payload["frame_count"] == 3
    assert result.metadata["frame_count"] == 3


def test_run_scan_rejects_atom_index_before_backend(
    fake_backend: FakeBackend, tmp_path: Path
) -> None:
    # Given: a coordinate that references atom 9 in a two-atom input.
    request = _request(tmp_path, coordinate="1,9,0.9,1.5")

    # When / Then: validation reports the atom constraint without invoking QC.
    with pytest.raises(ScanCoordinateError, match=r"atom index 9"):
        run_scan(request)
    assert fake_backend.calls == []


def test_scan_cli_rejects_atom_index_with_usage_error(tmp_path: Path) -> None:
    input_path = tmp_path / "input.xyz"
    input_path.write_text("2\ninput\nH 0.0 0.0 0.0\nH 0.0 0.0 1.0\n", encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "acp.cli",
            "run",
            "scan",
            "--input",
            str(input_path),
            "--coordinate",
            "0,9,1.0,1.5",
            "--output",
            str(tmp_path / "out"),
            "--log-level",
            "ERROR",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 2
    assert "atom index 9" in f"{completed.stdout}\n{completed.stderr}"


def test_scan_cli_materializes_smiles_and_hands_task_the_xyz(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from types import SimpleNamespace

    from acp.cli import _handle_scan, build_parser
    from acp.storage.layout import TaskStorage

    captured: dict[str, object] = {}

    def _fake_run_scan(req, *, progress_reporter=None):
        captured["req"] = req
        return SimpleNamespace(status="completed", metadata={"frame_count": 3}, errors=[])

    monkeypatch.setattr("acp.workflows.simple.run_scan", _fake_run_scan)

    args = build_parser().parse_args(
        [
            "run",
            "scan",
            "--input",
            "CCO",
            "--coordinate",
            "0,1,1.0,1.4",
            "--scan-points",
            "3",
            "--output",
            str(tmp_path / "out"),
            "--log-level",
            "ERROR",
        ]
    )
    assert _handle_scan(args) == 0

    req = captured["req"]
    input_path = req.input_artifact.path
    assert input_path.name == "input.xyz"
    assert input_path.is_file()
    assert input_path.parent.parent.name == "out"
    assert req.input_artifact.source == "smiles"
    assert req.input_artifact.elements == ["C", "C", "O", "H", "H", "H", "H", "H", "H"]
    assert req.resources["charge"] == 0
    assert req.resources["multiplicity"] == 1
    assert int(input_path.read_text(encoding="utf-8").splitlines()[0]) == 9

    provenance = json.loads((input_path.parent / "input_source.json").read_text(encoding="utf-8"))
    assert provenance["smiles"] == "CCO"
    assert provenance["embedding"]["seed"] == 42
    assert provenance["atom_count"] == 9
    assert provenance["charge"] == 0
    assert provenance["multiplicity"] == 1
    # materialized file lives in the v2 task root beside WORK/RESULT
    assert TaskStorage(input_path.parent).work_dir().is_dir()


def test_scan_cli_smiles_rejects_out_of_range_atom_before_qc(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "acp.cli",
            "run",
            "scan",
            "--input",
            "CCO",
            "--coordinate",
            "0,9,1.0,1.5",
            "--scan-points",
            "2",
            "--output",
            str(tmp_path / "out"),
            "--log-level",
            "ERROR",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 2
    assert "atom index 9" in f"{completed.stdout}\n{completed.stderr}"
    materialized = sorted((tmp_path / "out").glob("*/input.xyz"))
    assert len(materialized) == 1
    assert materialized[0].read_text(encoding="utf-8").splitlines()[0] == "9"


def test_scan_cli_nonexistent_file_reports_failure(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "acp.cli",
            "run",
            "scan",
            "--input",
            str(tmp_path / "missing.xyz"),
            "--coordinate",
            "0,1,1.0,1.5",
            "--scan-points",
            "2",
            "--output",
            str(tmp_path / "out"),
            "--log-level",
            "ERROR",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 1
    assert "Input file not found" in f"{completed.stdout}\n{completed.stderr}"


def test_materialized_smiles_geometry_binds_scan_identity(tmp_path: Path) -> None:
    from acp.calculations.contracts import CalculationPlan, CalculationStep, StepKind
    from acp.calculations.identity import compute_identity
    from acp.storage.layout import TaskStorage
    from acp.workflows.simple import prepare_scan_input

    input_path = prepare_scan_input("CCO").materialize(TaskStorage(tmp_path))

    def _fingerprint() -> str:
        plan = CalculationPlan(
            workflow="scan",
            profile="default",
            items=[StructureArtifact(path=input_path)],
            steps=[
                CalculationStep(
                    kind=StepKind.SCAN,
                    spec={"scan_coordinates": ["0,1,1.0,1.4"]},
                )
            ],
        )
        return compute_identity(plan).plan_identity

    first = _fingerprint()
    original = input_path.read_text(encoding="utf-8")
    perturbed = original.replace("-0.8883105789", "-0.9883105789", 1)
    assert perturbed != original
    input_path.write_text(perturbed, encoding="utf-8")

    assert _fingerprint() != first
