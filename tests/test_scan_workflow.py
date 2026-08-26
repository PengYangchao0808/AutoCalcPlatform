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
