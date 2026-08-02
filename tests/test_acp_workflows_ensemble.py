"""Tests for the ensemble generation workflow."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from acp.backends.censo_backend import (
    CensoConformerRecord,
    CensoRunResult,
)
from acp.core.models import StructureEnsemble


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_config() -> dict[str, Any]:
    return {
        "executables": {
            "censo": {"path": "censo"},
            "orca": {"path": "orca"},
            "xtb": {"path": "xtb"},
            "crest": {"path": "crest"},
        },
        "resources": {"nproc": 4},
        "censo": {"preset": "censo-light", "temperature": 298.15},
    }


@pytest.fixture
def mock_censo_result() -> CensoRunResult:
    """Build a CensoRunResult that mimics CENSO screening output."""
    rec1 = CensoConformerRecord(
        conf_id="CONF1", frame_index=0,
        energy=-154.912345, gsolv=-0.004521, grrho=0.082341, gtot=-154.834525,
        coordinates=np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 1.089], [1.027, 0.0, -0.363]]),
        symbols=["C", "H", "H"],
    )
    rec2 = CensoConformerRecord(
        conf_id="CONF2", frame_index=1,
        energy=-154.911876, gsolv=-0.004612, grrho=0.082455, gtot=-154.834033,
        coordinates=np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 1.089], [-1.027, 0.0, -0.363]]),
        symbols=["C", "H", "H"],
    )
    result = CensoRunResult(
        preset="censo-light",
        records=[rec1, rec2],
        final_part="screening",
        temperature=298.15,
    )
    result.sort_by_gtot()
    return result


# ---------------------------------------------------------------------------
# Import / lazy registration
# ---------------------------------------------------------------------------


def test_ensemble_workflow_registered_in_lazy_sources() -> None:
    from acp.workflows import _LAZY_SOURCES
    assert "run_ensemble_generation" in _LAZY_SOURCES
    assert _LAZY_SOURCES["run_ensemble_generation"] == "acp.workflows.ensemble"


def test_ensemble_module_importable() -> None:
    from acp.workflows.ensemble import run_ensemble_generation
    assert callable(run_ensemble_generation)


# ---------------------------------------------------------------------------
# _build_ensemble_from_censo
# ---------------------------------------------------------------------------


def test_build_ensemble_from_censo(mock_censo_result: CensoRunResult) -> None:
    from acp.core.models import Structure
    from acp.workflows.ensemble import _build_ensemble_from_censo

    structure = Structure(
        id="ethanol",
        charge=0,
        multiplicity=1,
        symbols=["C", "H", "H"],
        coordinates=[[0.0, 0.0, 0.0], [0.0, 0.0, 1.089], [1.027, 0.0, -0.363]],
    )

    ensemble = _build_ensemble_from_censo(mock_censo_result, structure)
    assert isinstance(ensemble, StructureEnsemble)
    assert len(ensemble.records) == 2

    # Records should be sorted by gtot (lowest first)
    assert ensemble.records[0].free_energy_hartree == pytest.approx(-154.834525)
    assert ensemble.records[1].free_energy_hartree == pytest.approx(-154.834033)

    # Boltzmann weights should be present and sum to 1
    weights = [r.weight for r in ensemble.records]
    assert all(w is not None for w in weights)
    assert sum(weights) == pytest.approx(1.0, abs=1e-6)

    # Properties carried through
    assert ensemble.records[0].properties.get("gtot") == pytest.approx(-154.834525)


# ---------------------------------------------------------------------------
# _write_ensemble_outputs
# ---------------------------------------------------------------------------


def test_write_ensemble_outputs(tmp_path: Path, mock_censo_result: CensoRunResult) -> None:
    from acp.core.models import Structure
    from acp.workflows.ensemble import _build_ensemble_from_censo, _write_ensemble_outputs

    structure = Structure(
        id="test", charge=0, multiplicity=1,
        symbols=["C", "H", "H"],
        coordinates=[[0.0, 0.0, 0.0], [0.0, 0.0, 1.089], [1.027, 0.0, -0.363]],
    )
    ensemble = _build_ensemble_from_censo(mock_censo_result, structure)
    _write_ensemble_outputs(ensemble, tmp_path, mock_censo_result)

    # Check XYZ file exists and is valid
    xyz_path = tmp_path / "ensemble" / "ensemble.xyz"
    assert xyz_path.exists()
    content = xyz_path.read_text()
    assert "conf000" in content
    assert "conf001" in content

    # Check JSON file
    json_path = tmp_path / "ensemble" / "ensemble.json"
    assert json_path.exists()
    data = json.loads(json_path.read_text())
    assert data["n_conformers"] == 2
    assert data["preset"] == "censo-light"
    assert len(data["conformers"]) == 2
    assert data["conformers"][0]["conf_id"] == "CONF1"

    # Check CSV file
    csv_path = tmp_path / "ensemble" / "ensemble.csv"
    assert csv_path.exists()
    csv_content = csv_path.read_text()
    assert "conf_id" in csv_content
    assert "CONF1" in csv_content
    assert "CONF2" in csv_content


# ---------------------------------------------------------------------------
# _is_multiframe_xyz
# ---------------------------------------------------------------------------


def test_is_multiframe_xyz_true(tmp_path: Path) -> None:
    from acp.workflows.ensemble import _is_multiframe_xyz

    xyz = tmp_path / "multi.xyz"
    xyz.write_text("3\nFrame 0\nC 0 0 0\nH 0 0 1\nH 1 0 0\n3\nFrame 1\nC 0 0 0\nH 0 0 1\nH -1 0 0\n")
    assert _is_multiframe_xyz(xyz) is True


def test_is_multiframe_xyz_single_frame(tmp_path: Path) -> None:
    from acp.workflows.ensemble import _is_multiframe_xyz

    xyz = tmp_path / "single.xyz"
    xyz.write_text("3\nSingle\nC 0 0 0\nH 0 0 1\nH 1 0 0\n")
    assert _is_multiframe_xyz(xyz) is False


def test_is_multiframe_xyz_not_xyz(tmp_path: Path) -> None:
    from acp.workflows.ensemble import _is_multiframe_xyz

    txt = tmp_path / "data.txt"
    txt.write_text("not xyz")
    assert _is_multiframe_xyz(txt) is False


def test_is_multiframe_xyz_nonexistent(tmp_path: Path) -> None:
    from acp.workflows.ensemble import _is_multiframe_xyz
    assert _is_multiframe_xyz(tmp_path / "missing.xyz") is False


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


def test_ensemble_subparser_registered() -> None:
    """Verify the CLI parser has an ensemble subcommand."""
    from acp.cli import build_parser

    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["run", "--help"])
    assert exc.value.code == 0


def test_ensemble_help_output() -> None:
    from acp.cli import build_parser

    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["run", "ensemble", "--help"])
    assert exc.value.code == 0


# ---------------------------------------------------------------------------
# Workflow registry
# ---------------------------------------------------------------------------


def test_ensemble_in_workflow_registry() -> None:
    from acp.workflows.registry import get_workflow_entry

    entry = get_workflow_entry("ensemble")
    assert entry is not None
    assert entry.name == "ensemble"
    assert "censo" in entry.requires_binaries


def test_ensemble_in_supported_workflows() -> None:
    from acp.scheduler.jobs import SUPPORTED_WORKFLOWS
    assert "ensemble" in SUPPORTED_WORKFLOWS


# ---------------------------------------------------------------------------
# run_ensemble_generation — integration with mocks
# ---------------------------------------------------------------------------


def test_run_ensemble_generation_with_multi_frame_xyz(
    tmp_path: Path,
    sample_config: dict[str, Any],
    mock_censo_result: CensoRunResult,
) -> None:
    """Multi-frame XYZ input skips CREST and goes directly to CENSO."""
    from acp.workflows.ensemble import run_ensemble_generation

    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text(
        "3\nFrame 0\nC 0 0 0\nH 0 0 1.089\nH 1.027 0 -0.363\n"
        "3\nFrame 1\nC 0 0 0\nH 0 0 1.089\nH -1.027 0 -0.363\n"
    )

    with (
        patch("cccp.qc.interfaces.censo.shutil.which", return_value="/usr/bin/censo"),
        patch.object(
            type("MockBackend", (), {"refine_ensemble": lambda *a, **kw: mock_censo_result})(),
            "refine_ensemble",
            return_value=mock_censo_result,
        ),
        patch("acp.workflows.ensemble.CensoBackend") as mock_backend_cls,
    ):
        mock_backend = MagicMock()
        mock_backend.refine_ensemble.return_value = mock_censo_result
        mock_backend_cls.return_value = mock_backend

        result = run_ensemble_generation(
            input_source=str(input_xyz),
            output_dir=str(tmp_path / "out"),
            preset="censo-light",
            config=sample_config,
        )

    assert result.status == "completed"
    assert result.metadata is not None
    assert result.metadata["n_conformers"] == 2
    assert result.metadata["preset"] == "censo-light"

    # Check that ensemble outputs exist
    out_root = tmp_path / "out" / "input"
    assert (out_root / "ensemble" / "ensemble.xyz").exists()
    assert (out_root / "ensemble" / "ensemble.json").exists()
    assert (out_root / "ensemble" / "ensemble.csv").exists()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_run_ensemble_generation_invalid_input(tmp_path: Path) -> None:
    """Non-existent input file should still produce a failed result."""
    from acp.workflows.ensemble import run_ensemble_generation

    result = run_ensemble_generation(
        input_source=str(tmp_path / "nonexistent.xyz"),
        output_dir=str(tmp_path / "out"),
    )
    assert result.status == "failed"
