"""Tests for the ensemble generation workflow."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np
import pytest

from acp.backends.censo_backend import (
    CensoConformerRecord,
    CensoRunResult,
)
from acp.core.models import StructureEnsemble
from cccp.calculation.requests import TaskKind
from cccp.calculation.results import TaskResult
from cccp.qc.interfaces.censo import part_index

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
        conf_id="CONF1",
        frame_index=0,
        energy=-154.912345,
        gsolv=-0.004521,
        grrho=0.082341,
        gtot=-154.834525,
        coordinates=np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 1.089], [1.027, 0.0, -0.363]]),
        symbols=["C", "H", "H"],
    )
    rec2 = CensoConformerRecord(
        conf_id="CONF2",
        frame_index=1,
        energy=-154.911876,
        gsolv=-0.004612,
        grrho=0.082455,
        gtot=-154.834033,
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


def _fake_censo_refine(result: CensoRunResult) -> Any:
    """Patch side effect for ``energy_shared.run_censo_refine``.

    Writes the final-part CENSO JSON/XYZ artifacts (the
    ``<idx>_<FINAL_PART>`` convention of the censo_refine task) and returns
    the typed task result.
    """

    def _run(request: Any, *, context: Any = None) -> TaskResult:
        run_dir = Path(request.output_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        part = result.final_part
        json_path = run_dir / f"{part_index(part)}_{part.upper()}.json"
        xyz_path = run_dir / f"{part_index(part)}_{part.upper()}.xyz"
        payload = {
            rec.conf_id: {
                "energy": rec.energy,
                "gsolv": rec.gsolv,
                "grrho": rec.grrho,
                "gtot": rec.gtot,
            }
            for rec in result.records
        }
        json_path.write_text(json.dumps({"data": payload}), encoding="utf-8")
        lines: list[str] = []
        for rec in result.records:
            lines.append(str(len(rec.symbols)))
            lines.append(rec.conf_id)
            lines.extend(
                f"{sym} {x:.6f} {y:.6f} {z:.6f}"
                for sym, (x, y, z) in zip(rec.symbols, rec.coordinates)
            )
        xyz_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return TaskResult(
            task=TaskKind.CENSO_REFINE,
            status="completed",
            complete=True,
            metadata={
                "preset": result.preset,
                "final_part": part,
                "temperature_k": result.temperature,
                "record_count": len(result.records),
            },
        )

    return _run


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
        id="test",
        charge=0,
        multiplicity=1,
        symbols=["C", "H", "H"],
        coordinates=[[0.0, 0.0, 0.0], [0.0, 0.0, 1.089], [1.027, 0.0, -0.363]],
    )
    ensemble = _build_ensemble_from_censo(mock_censo_result, structure)
    _write_ensemble_outputs(ensemble, tmp_path, mock_censo_result)

    # Check XYZ file exists and is valid
    xyz_path = tmp_path / "RESULT" / "ensembles" / "ensemble.xyz"
    assert xyz_path.exists()
    content = xyz_path.read_text()
    assert "conf000" in content
    assert "conf001" in content

    # Check JSON file
    json_path = tmp_path / "RESULT" / "ensembles" / "ensemble.json"
    assert json_path.exists()
    data = json.loads(json_path.read_text())
    assert data["n_conformers"] == 2
    assert data["preset"] == "censo-light"
    assert len(data["conformers"]) == 2
    assert data["conformers"][0]["conf_id"] == "CONF1"

    # Check CSV file
    csv_path = tmp_path / "RESULT" / "ensembles" / "ensemble.csv"
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
    xyz.write_text(
        "3\nFrame 0\nC 0 0 0\nH 0 0 1\nH 1 0 0\n3\nFrame 1\nC 0 0 0\nH 0 0 1\nH -1 0 0\n"
    )
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


def test_ensemble_retired_replaced_by_confsearch_registry_entry() -> None:
    from acp.workflows.registry import get_workflow_entry

    assert get_workflow_entry("ensemble") is None
    confsearch = get_workflow_entry("Confsearch")
    assert confsearch is not None
    assert confsearch.name == "Confsearch"
    assert "censo" in confsearch.requires_binaries


def test_ensemble_retired_from_supported_workflows() -> None:
    from acp.scheduler.jobs import SUPPORTED_WORKFLOWS

    assert "ensemble" not in SUPPORTED_WORKFLOWS
    assert "Confsearch" in SUPPORTED_WORKFLOWS


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

    with patch(
        "acp.workflows.energy_shared.run_censo_refine",
        side_effect=_fake_censo_refine(mock_censo_result),
    ) as mock_censo:
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
    extras = mock_censo.call_args.kwargs["context"].capability_extras
    assert extras["preset"] == "censo-light"

    # Check that ensemble outputs exist
    out_root = tmp_path / "out" / "input"
    assert (out_root / "RESULT" / "ensembles" / "ensemble.xyz").exists()
    assert (out_root / "RESULT" / "ensembles" / "ensemble.json").exists()
    assert (out_root / "RESULT" / "ensembles" / "ensemble.csv").exists()


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


# ---------------------------------------------------------------------------
# T08b — stage-split solvent models on the shared Confsearch engines
# ---------------------------------------------------------------------------


def _single_frame_xyz(path: Path) -> Path:
    path.write_text("3\nSingle\nC 0 0 0\nH 0 0 1.089\nH 1.027 0 -0.363\n", encoding="utf-8")
    return path


def _crest_args(solvent: str | None, solvent_model: str) -> list[str]:
    """Legality proof at the CREST interface level (not just no-raise)."""
    from cccp.backends.crest import CrestBackend

    backend = CrestBackend(
        config={"executables": {"crest": {"path": "crest"}}},
        gfn_level=2,
        solvent=solvent,
        solvent_model=solvent_model,
    )
    return backend._interface._solvent_args()


@pytest.mark.parametrize("dft_model", ["smd", "cpcm"])
def test_ensemble_crest_never_receives_dft_model(
    tmp_path: Path,
    sample_config: dict[str, Any],
    mock_censo_result: CensoRunResult,
    dft_model: str,
) -> None:
    """CREST/xTB gets a legal sampling model; CENSO keeps the configured
    DFT model (the historical ``none → smd`` fallback never leaks across)."""
    from acp.workflows.ensemble import run_ensemble_generation

    input_xyz = _single_frame_xyz(tmp_path / "input.xyz")
    config = {**sample_config, "theory": {"preoptimization": {"solvent_model": dft_model}}}
    crest_calls: list[dict[str, Any]] = []

    def fake_crest(cfg: Any, xyz: Path, out_dir: Path, **kwargs: Any) -> Path:
        crest_calls.append(dict(kwargs))
        return xyz

    with (
        patch("acp.workflows.ensemble._crest_search_via_task", side_effect=fake_crest),
        patch(
            "acp.workflows.energy_shared.run_censo_refine",
            side_effect=_fake_censo_refine(mock_censo_result),
        ) as mock_censo,
    ):
        result = run_ensemble_generation(
            input_source=str(input_xyz),
            output_dir=str(tmp_path / "out"),
            preset="censo-light",
            config=config,
            solvent="water",
        )

    assert result.status == "completed", result.error
    assert crest_calls, "CREST must run for a single-frame input"
    assert crest_calls[0]["solvent"] == "water"
    assert crest_calls[0]["solvent_model"] == "alpb"
    assert _crest_args(crest_calls[0]["solvent"], crest_calls[0]["solvent_model"]) == [
        "--alpb",
        "water",
    ]
    extras = mock_censo.call_args.kwargs["context"].capability_extras
    assert extras["solvent"] == "water"
    assert extras["solvent_model"] == dft_model


def test_ensemble_censo_zero_sampling_model_parametrized(
    tmp_path: Path,
    sample_config: dict[str, Any],
) -> None:
    """xtb-crest Confsearch path (censo-zero): the dedicated key is the
    explicit sampling config — ``none`` stays gas, ``gbsa`` is used as given."""
    from acp.workflows.ensemble import run_ensemble_generation

    cases: list[tuple[str | None, str]] = [(None, "alpb"), ("none", "none"), ("gbsa", "gbsa")]
    for dedicated, expected in cases:
        input_xyz = _single_frame_xyz(tmp_path / f"input_{expected}.xyz")
        nmr_section = {} if dedicated is None else {"sampling_solvent_model": dedicated}
        config = {**sample_config, "nmr": nmr_section}
        crest_calls: list[dict[str, Any]] = []

        def fake_crest(cfg: Any, xyz: Path, out_dir: Path, **kwargs: Any) -> Path:
            crest_calls.append(dict(kwargs))
            return xyz

        with patch("acp.workflows.ensemble._crest_search_via_task", side_effect=fake_crest):
            result = run_ensemble_generation(
                input_source=str(input_xyz),
                output_dir=str(tmp_path / f"out_{expected}"),
                preset="censo-zero",
                config=config,
                solvent="water",
            )

        assert result.status == "completed", result.error
        assert crest_calls[0]["solvent"] == "water"
        assert crest_calls[0]["solvent_model"] == expected
        assert _crest_args("water", expected) == (
            [] if expected == "none" else [f"--{expected}", "water"]
        )


def test_ensemble_gas_phase_without_solvent_unchanged(
    tmp_path: Path,
    sample_config: dict[str, Any],
    mock_censo_result: CensoRunResult,
) -> None:
    """No solvent anywhere → both stages stay gas phase (no default)."""
    from acp.workflows.ensemble import run_ensemble_generation

    input_xyz = _single_frame_xyz(tmp_path / "input.xyz")
    crest_calls: list[dict[str, Any]] = []

    def fake_crest(cfg: Any, xyz: Path, out_dir: Path, **kwargs: Any) -> Path:
        crest_calls.append(dict(kwargs))
        return xyz

    with (
        patch("acp.workflows.ensemble._crest_search_via_task", side_effect=fake_crest),
        patch(
            "acp.workflows.energy_shared.run_censo_refine",
            side_effect=_fake_censo_refine(mock_censo_result),
        ) as mock_censo,
    ):
        result = run_ensemble_generation(
            input_source=str(input_xyz),
            output_dir=str(tmp_path / "out"),
            preset="censo-light",
            config=sample_config,
        )

    assert result.status == "completed", result.error
    assert crest_calls[0]["solvent"] is None
    assert crest_calls[0]["solvent_model"] == "none"
    extras = mock_censo.call_args.kwargs["context"].capability_extras
    assert "solvent" not in extras
    assert extras["solvent_model"] == "none"


def test_ensemble_unknown_sampling_model_fails_strictly(
    tmp_path: Path,
    sample_config: dict[str, Any],
) -> None:
    """A malformed dedicated-key value fails the run before any QC — never
    silently coerced to a legal model."""
    from acp.workflows.ensemble import run_ensemble_generation

    input_xyz = _single_frame_xyz(tmp_path / "input.xyz")
    config = {**sample_config, "nmr": {"sampling_solvent_model": "mdm"}}
    with patch("acp.workflows.ensemble._crest_search_via_task") as mock_crest:
        result = run_ensemble_generation(
            input_source=str(input_xyz),
            output_dir=str(tmp_path / "out"),
            preset="censo-light",
            config=config,
            solvent="water",
        )

    assert result.status == "failed"
    assert "sampling_solvent_model" in (result.error or "")
    mock_crest.assert_not_called()
