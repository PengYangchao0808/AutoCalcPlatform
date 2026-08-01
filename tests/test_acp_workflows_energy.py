"""Tests for the conformer energy workflow (acp run energy)."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from acp.backends.censo_backend import CensoConformerRecord, CensoRunResult
from acp.core.models import HARTREE_TO_KCAL
from acp.workflows.ensemble_thermo import ensemble_total_gibbs as ensemble_total_gibbs_import

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
            "shermo": {"path": "Shermo"},
        },
        "resources": {"nproc": 4},
        "censo": {"preset": "censo-light", "temperature": 298.15},
    }


def _make_record(conf_id: str, frame_index: int, gtot: float) -> CensoConformerRecord:
    return CensoConformerRecord(
        conf_id=conf_id,
        frame_index=frame_index,
        energy=gtot + 0.08,
        gsolv=-0.004,
        grrho=-0.076,
        gtot=gtot,
        coordinates=np.array(
            [[0.0, 0.0, 0.0], [0.0, 0.0, 1.089], [1.027, 0.0, -0.363]]
        ),
        symbols=["C", "H", "H"],
    )


@pytest.fixture
def mock_screening_result() -> CensoRunResult:
    result = CensoRunResult(
        preset="censo-light",
        records=[
            _make_record("CONF1", 0, -154.834525),
            _make_record("CONF2", 1, -154.834033),
        ],
        final_part="screening",
        temperature=298.15,
    )
    result.sort_by_gtot()
    return result


@pytest.fixture
def mock_refinement_result() -> CensoRunResult:
    result = CensoRunResult(
        preset="censo-light",
        records=[
            _make_record("CONF1", 0, -154.850111),
            _make_record("CONF2", 1, -154.849001),
        ],
        final_part="refinement",
        temperature=298.15,
    )
    result.sort_by_gtot()
    return result


@pytest.fixture
def multiframe_xyz(tmp_path: Path) -> Path:
    # Title-line xTB energies far apart (~31 kcal/mol) so censo-zero
    # cumulative-Boltzmann selection deterministically keeps only frame 1.
    xyz = tmp_path / "input.xyz"
    xyz.write_text(
        "3\n-154.80000000\nC 0 0 0\nH 0 0 1.089\nH 1.027 0 -0.363\n"
        "3\n-154.75000000\nC 0 0 0\nH 0 0 1.089\nH -1.027 0 -0.363\n"
    )
    return xyz


def _mock_orca_instance() -> MagicMock:
    orca = MagicMock()
    opt_result = MagicMock()
    opt_result.success = True
    opt_result.coordinates = np.array(
        [[0.0, 0.0, 0.0], [0.0, 0.0, 1.09], [1.03, 0.0, -0.36]]
    )
    opt_result.symbols = ["C", "H", "H"]
    opt_result.energy = -154.90
    opt_result.log_file = Path("/tmp/opt.out")
    opt_result.error_message = None
    orca.optimize.return_value = opt_result

    freq_result = MagicMock()
    freq_result.success = True
    freq_result.log_file = Path("/tmp/freq.out")
    freq_result.error_message = None
    orca.frequency.return_value = freq_result

    sp_result = MagicMock()
    sp_result.success = True
    sp_result.energy = -155.001234
    sp_result.log_file = Path("/tmp/sp.out")
    sp_result.error_message = None
    orca.single_point.return_value = sp_result
    return orca


_SHERMO_OK = {
    "g_sum": -154.950123,
    "g_conc": None,
    "h_sum": -154.90,
    "u_sum": -154.91,
    "s_total": 0.03,
}


# ---------------------------------------------------------------------------
# Import / lazy registration
# ---------------------------------------------------------------------------


def test_energy_workflow_registered_in_lazy_sources() -> None:
    from acp.workflows import _LAZY_SOURCES

    assert "run_conformer_energy" in _LAZY_SOURCES
    assert _LAZY_SOURCES["run_conformer_energy"] == "acp.workflows.energy"


def test_energy_module_importable() -> None:
    from acp.workflows.energy import run_conformer_energy

    assert callable(run_conformer_energy)


# ---------------------------------------------------------------------------
# Registry / scheduler integration
# ---------------------------------------------------------------------------


def test_energy_in_workflow_registry() -> None:
    from acp.workflows.registry import get_workflow_entry

    entry = get_workflow_entry("energy")
    assert entry is not None
    assert entry.name == "energy"
    assert "censo" in entry.requires_binaries
    assert "crest" in entry.requires_binaries
    assert "orca" in entry.requires_binaries


def test_energy_in_supported_workflows() -> None:
    from acp.scheduler.jobs import SUPPORTED_WORKFLOWS

    assert "energy" in SUPPORTED_WORKFLOWS


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


def test_energy_subparser_registered() -> None:
    from acp.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["run", "energy", "--input", "CCO"])
    assert args.workflow == "energy"
    assert args.preset == "censo-light"
    assert args.no_opt is False
    assert args.full_ensemble is False


def test_energy_cli_rank1_only_default_and_optout(tmp_path: Path) -> None:
    """CLI 默认开启 rank1-only；--full-ensemble 显式关闭并透传 workflow 层。"""
    from unittest.mock import MagicMock

    from acp.cli import main

    calls: list[bool] = []

    def fake_run(**kwargs: Any) -> MagicMock:
        calls.append(kwargs.get("rank1_only"))
        res = MagicMock()
        res.status = "completed"
        res.metadata = {}
        return res

    with (
        patch("acp.cli._build_config", return_value={}),
        patch("acp.workflows.energy.run_conformer_energy", side_effect=fake_run) as m,
    ):
        rc = main(["run", "energy", "--input", "CCO", "--output", str(tmp_path / "a")])
        assert rc == 0
        rc2 = main(
            [
                "run", "energy", "--input", "CCO",
                "--output", str(tmp_path / "b"),
                "--full-ensemble",
            ]
        )
        assert rc2 == 0
    assert m.call_count == 2
    assert calls == [True, False]


def test_energy_help_output() -> None:
    from acp.cli import build_parser

    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["run", "energy", "--help"])
    assert exc.value.code == 0


def test_energy_invalid_preset_rejected() -> None:
    from acp.cli import build_parser

    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "energy", "--input", "CCO", "--preset", "bogus"])


def test_energy_invalid_levels_json_returns_error(tmp_path: Path) -> None:
    from acp.cli import main

    rc = main([
        "run", "energy", "--input", "CCO",
        "--output", str(tmp_path),
        "--levels", "{not json",
    ])
    assert rc == 1


def test_parse_levels_json_valid() -> None:
    from acp.cli import _parse_levels_json

    parsed = _parse_levels_json('{"thermo":{"scale_factor":0.98}}')
    assert parsed == {"thermo": {"scale_factor": 0.98}}


def test_parse_levels_json_non_object() -> None:
    from acp.cli import _parse_levels_json

    assert _parse_levels_json("[1,2]") is None
    assert _parse_levels_json(None) is None


# ---------------------------------------------------------------------------
# Levels resolution
# ---------------------------------------------------------------------------


def test_resolve_levels_defaults() -> None:
    from acp.workflows.energy import _resolve_levels

    resolved = _resolve_levels({}, None)
    assert resolved["opt_method"] == "r2SCAN-3c"
    assert resolved["sp_method"] == "wB97M-V"
    assert resolved["sp_basis"] == "def2-TZVPP"
    assert resolved["scl_zpe"] == pytest.approx(0.9905)
    assert resolved["temperature_k"] == pytest.approx(298.15)


def test_resolve_levels_overrides() -> None:
    from acp.workflows.energy import _resolve_levels

    resolved = _resolve_levels(
        {"thermo": {"scl_zpe": 0.9905}},
        {
            "dft_opt": {"functional": "B97-3c"},
            "refinement_sp": {"functional": "DLPNO-CCSD(T)", "basis": "def2-TZVPP"},
            "screening_sp": {"functional": "PBE0", "basis": "def2-SVP"},
            "thermo": {"scale_factor": 0.98, "temperature": 310.0},
        },
    )
    assert resolved["opt_method"] == "B97-3c"
    assert resolved["sp_method"] == "DLPNO-CCSD(T)"
    assert resolved["screening_overrides"] == {"func": "pbe0", "basis": "def2-svp"}
    assert resolved["refinement_overrides"]["func"] == "dlpno-ccsd(t)"
    assert resolved["scl_zpe"] == pytest.approx(0.98)
    assert resolved["temperature_k"] == pytest.approx(310.0)


def test_resolve_levels_config_fallback() -> None:
    from acp.workflows.energy import _resolve_levels

    cfg = {
        "censo": {
            "refinement_func": "wB97X-D4",
            "refinement_basis": "def2-TZVP",
            "optimization": {"functional": "PBE0"},
        },
        "thermo": {"scl_zpe": 0.97, "temperature_k": 300.0},
    }
    resolved = _resolve_levels(cfg, None)
    assert resolved["opt_method"] == "PBE0"
    assert resolved["sp_method"] == "wB97X-D4"
    assert resolved["sp_basis"] == "def2-TZVP"
    assert resolved["scl_zpe"] == pytest.approx(0.97)
    assert resolved["temperature_k"] == pytest.approx(300.0)


def test_resolve_levels_recalc_hess_passthrough() -> None:
    """energy._resolve_levels normalises dft_opt.recalc_hess and exposes
    it as opt_recalc_hess (plan §10.2 / AC11)."""
    from acp.workflows.energy import _resolve_levels

    # Omitted → follow config (None).
    assert _resolve_levels({}, None)["opt_recalc_hess"] is None
    # auto / 0 / N pass through normalised.
    assert _resolve_levels({}, {"dft_opt": {"recalc_hess": "auto"}})["opt_recalc_hess"] == "auto"
    assert _resolve_levels({}, {"dft_opt": {"recalc_hess": 0}})["opt_recalc_hess"] == 0
    assert _resolve_levels({}, {"dft_opt": {"recalc_hess": 15}})["opt_recalc_hess"] == 15
    # Numeric strings are accepted by the normaliser.
    assert _resolve_levels({}, {"dft_opt": {"recalc_hess": "5"}})["opt_recalc_hess"] == 5


def test_resolve_levels_recalc_hess_invalid_raises() -> None:
    """Invalid recalc_hess surfaces as a ValueError with context."""
    from acp.workflows.energy import _resolve_levels

    with pytest.raises(ValueError, match="dft_opt.recalc_hess"):
        _resolve_levels({}, {"dft_opt": {"recalc_hess": "fast"}})


# ---------------------------------------------------------------------------
# Preset validation
# ---------------------------------------------------------------------------


def test_run_energy_unknown_preset(tmp_path: Path) -> None:
    from acp.workflows.energy import run_conformer_energy

    result = run_conformer_energy(
        input_source="CCO",
        output_dir=str(tmp_path),
        preset="not-a-preset",
    )
    assert result.status == "failed"
    assert "Unknown preset" in (result.error or "")


def test_run_energy_invalid_input(tmp_path: Path) -> None:
    from acp.workflows.energy import run_conformer_energy

    result = run_conformer_energy(
        input_source=str(tmp_path / "missing.xyz"),
        output_dir=str(tmp_path / "out"),
    )
    assert result.status == "failed"


# ---------------------------------------------------------------------------
# censo-light (opt on, default): CENSO -P -S → rank1 → ACP handoff
# ---------------------------------------------------------------------------


def test_energy_light_opt_on_end_to_end(
    tmp_path: Path,
    sample_config: dict[str, Any],
    mock_screening_result: CensoRunResult,
    multiframe_xyz: Path,
) -> None:
    from acp.workflows.energy import run_conformer_energy

    orca = _mock_orca_instance()

    with (
        patch("acp.workflows.energy.CensoBackend") as mock_backend_cls,
        patch("acp.workflows.energy_shared.ORCAInterface", return_value=orca) as mock_orca_cls,
        patch(
            "acp.workflows.energy_shared.run_shermo", return_value=dict(_SHERMO_OK)
        ) as mock_shermo,
    ):
        backend = MagicMock()
        backend.refine_ensemble.return_value = mock_screening_result
        mock_backend_cls.return_value = backend

        result = run_conformer_energy(
            input_source=str(multiframe_xyz),
            output_dir=str(tmp_path / "out"),
            preset="censo-light",
            config=sample_config,
        )

    assert result.status == "completed"
    assert result.metadata["preset"] == "censo-light"
    assert result.metadata["opt_enabled"] is True
    # v15 semantics: both screening records (ΔG ≈ 0.31 kcal/mol) fall inside
    # the 99% cumulative Boltzmann window → 2-conformer ensemble
    assert result.metadata["n_conformers"] == 2
    assert result.metadata["refinement_threshold"] == pytest.approx(0.99)

    # CENSO invoked with the light preset, no refinement appended
    _, kwargs = backend.refine_ensemble.call_args
    assert kwargs["preset"] == "censo-light"
    assert kwargs.get("include_refinement", False) is False

    # ACP handoff: opt → freq → SP → Shermo, one call per selected conformer
    assert orca.optimize.call_count == 2
    assert orca.frequency.call_count == 2
    assert orca.single_point.call_count == 2
    assert mock_shermo.call_count == 2

    # Shermo consumed the SP energy
    assert mock_shermo.call_args.kwargs["sp_energy"] == pytest.approx(-155.001234)

    # finalDFT products (2 frames / 2 rows + TOTAL row) + global min + screening ranking
    mol_dir = tmp_path / "out" / "input"
    assert (mol_dir / "finalDFT" / "all_conformers.xyz").exists()
    thermo_csv = mol_dir / "finalDFT" / "conformer_thermo.csv"
    assert thermo_csv.exists()
    lines = thermo_csv.read_text().strip().splitlines()
    assert lines[0].startswith("index,rank,energy_hartree,gibbs_correction,gibbs_hartree")
    assert len(lines) == 4  # header + both ensemble members + TOTAL row
    assert lines[-1].startswith("TOTAL,")
    assert (mol_dir / "input_global_min.xyz").exists()
    assert (mol_dir / "ensemble" / "screening_ranking.csv").exists()
    # ensemble total Gibbs: both mock candidates share the same Shermo Gibbs
    # → equal weights 0.5/0.5 → G_total = G1 + kT·ln 0.5
    thermo_json = mol_dir / "finalDFT" / "ensemble_thermo.json"
    assert thermo_json.exists()
    summary = json.loads(thermo_json.read_text())
    assert summary["method"] == "dft_table"
    assert summary["rank1_weight"] == pytest.approx(0.5)
    g1 = _SHERMO_OK["g_conc"] if _SHERMO_OK["g_conc"] is not None else _SHERMO_OK["g_sum"]
    expected_total = (g1 + 3.166811563e-6 * 298.15 * math.log(0.5)) * HARTREE_TO_KCAL
    assert result.metadata["total_gibbs_kcal_mol"] == pytest.approx(expected_total, abs=1e-6)
    assert summary["total_gibbs_kcal_mol"] == pytest.approx(expected_total, abs=1e-6)
    assert summary["population_coverage"] == pytest.approx(1.0)

    # ORCAInterface constructed with the default opt functional
    _, orca_kwargs = mock_orca_cls.call_args
    assert orca_kwargs["method"] == "r2SCAN-3c"


def test_energy_light_opt_on_rank1_is_lowest_gtot(
    tmp_path: Path,
    sample_config: dict[str, Any],
    mock_screening_result: CensoRunResult,
    multiframe_xyz: Path,
) -> None:
    """The lowest-gtot record (CONF1) must lead the selected ensemble."""
    from acp.workflows.energy import run_conformer_energy

    orca = _mock_orca_instance()

    with (
        patch("acp.workflows.energy.CensoBackend") as mock_backend_cls,
        patch("acp.workflows.energy_shared.ORCAInterface", return_value=orca),
        patch("acp.workflows.energy_shared.run_shermo", return_value=dict(_SHERMO_OK)),
    ):
        backend = MagicMock()
        backend.refine_ensemble.return_value = mock_screening_result
        mock_backend_cls.return_value = backend

        result = run_conformer_energy(
            input_source=str(multiframe_xyz),
            output_dir=str(tmp_path / "out"),
            preset="censo-light",
            config=sample_config,
        )

    assert result.status == "completed"
    rec = result.ensemble.records[0]
    assert rec.structure.metadata["source"] == "CONF1"
    # both conformers selected; identical mock Shermo Gibbs → equal weights
    assert rec.weight == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# censo-light --no-opt: single CENSO call -P -S -R, no ORCA
# ---------------------------------------------------------------------------


def test_energy_light_no_opt_cheap_path(
    tmp_path: Path,
    sample_config: dict[str, Any],
    mock_refinement_result: CensoRunResult,
    multiframe_xyz: Path,
) -> None:
    from acp.workflows.energy import run_conformer_energy

    orca = _mock_orca_instance()

    with (
        patch("acp.workflows.energy.CensoBackend") as mock_backend_cls,
        patch("acp.workflows.energy_shared.ORCAInterface", return_value=orca),
        patch("acp.workflows.energy_shared.run_shermo") as mock_shermo,
    ):
        backend = MagicMock()
        backend.refine_ensemble.return_value = mock_refinement_result
        mock_backend_cls.return_value = backend

        result = run_conformer_energy(
            input_source=str(multiframe_xyz),
            output_dir=str(tmp_path / "out"),
            preset="censo-light",
            config=sample_config,
            no_opt=True,
        )

    assert result.status == "completed"
    assert result.metadata["opt_enabled"] is False
    # v15: refined records (ΔG ≈ 0.70 kcal/mol) both inside the 99% window
    assert result.metadata["n_conformers"] == 2

    # CENSO called once with refinement appended; no ORCA/Shermo involvement
    _, kwargs = backend.refine_ensemble.call_args
    assert kwargs["include_refinement"] is True
    assert kwargs["nconf"] is None
    orca.optimize.assert_not_called()
    orca.frequency.assert_not_called()
    orca.single_point.assert_not_called()
    mock_shermo.assert_not_called()

    # gibbs comes straight from CENSO gtot
    rec = result.ensemble.records[0]
    assert rec.free_energy_hartree == pytest.approx(-154.850111)


# ---------------------------------------------------------------------------
# censo-zero paths
# ---------------------------------------------------------------------------


def test_energy_zero_opt_on_bypasses_censo(
    tmp_path: Path,
    sample_config: dict[str, Any],
    multiframe_xyz: Path,
) -> None:
    """censo-zero opt-on: xTB passthrough selection, no CENSO CLI at all.

    The fixture's title energies are ~31 kcal/mol apart, so the cumulative
    99% Boltzmann window keeps only the lowest frame.
    """
    from acp.workflows.energy import run_conformer_energy

    orca = _mock_orca_instance()

    with (
        patch("acp.workflows.energy.CensoBackend") as mock_backend_cls,
        patch("acp.workflows.energy_shared.ORCAInterface", return_value=orca),
        patch(
            "acp.workflows.energy_shared.run_shermo", return_value=dict(_SHERMO_OK)
        ) as mock_shermo,
    ):
        backend = MagicMock()
        mock_backend_cls.return_value = backend

        result = run_conformer_energy(
            input_source=str(multiframe_xyz),
            output_dir=str(tmp_path / "out"),
            preset="censo-zero",
            config=sample_config,
        )

    assert result.status == "completed"
    mock_backend_cls.assert_not_called()
    backend.refine_ensemble.assert_not_called()
    assert orca.optimize.call_count == 1
    assert orca.frequency.call_count == 1
    assert orca.single_point.call_count == 1
    assert mock_shermo.call_count == 1
    assert result.metadata["n_conformers"] == 1


def test_energy_zero_no_opt_censo_nconf1(
    tmp_path: Path,
    sample_config: dict[str, Any],
    multiframe_xyz: Path,
) -> None:
    """censo-zero --no-opt: CENSO -n N --refinement (N from xTB preselection).

    With well-separated title energies only frame 1 survives → -n 1.
    """
    from acp.workflows.energy import run_conformer_energy

    refinement = CensoRunResult(
        preset="censo-zero",
        records=[_make_record("CONF1", 0, -154.860000)],
        final_part="refinement",
        temperature=298.15,
    )

    orca = _mock_orca_instance()

    with (
        patch("acp.workflows.energy.CensoBackend") as mock_backend_cls,
        patch("acp.workflows.energy_shared.ORCAInterface", return_value=orca),
        patch("acp.workflows.energy_shared.run_shermo") as mock_shermo,
    ):
        backend = MagicMock()
        backend.refine_ensemble.return_value = refinement
        mock_backend_cls.return_value = backend

        result = run_conformer_energy(
            input_source=str(multiframe_xyz),
            output_dir=str(tmp_path / "out"),
            preset="censo-zero",
            config=sample_config,
            no_opt=True,
        )

    assert result.status == "completed"
    _, kwargs = backend.refine_ensemble.call_args
    assert kwargs["preset"] == "censo-zero"
    assert kwargs["nconf"] == 1
    assert kwargs["include_refinement"] is False
    orca.optimize.assert_not_called()
    mock_shermo.assert_not_called()


# ---------------------------------------------------------------------------
# censo-default: full Part0–3 + same-level freq + Shermo per survivor
# ---------------------------------------------------------------------------


def test_energy_default_full_funnel(
    tmp_path: Path,
    sample_config: dict[str, Any],
    mock_refinement_result: CensoRunResult,
    multiframe_xyz: Path,
) -> None:
    from acp.workflows.energy import run_conformer_energy

    orca = _mock_orca_instance()
    shermo_values = [
        {"g_sum": -154.955, "g_conc": None, "h_sum": None, "u_sum": None, "s_total": None},
        {"g_sum": -154.951, "g_conc": None, "h_sum": None, "u_sum": None, "s_total": None},
    ]

    with (
        patch("acp.workflows.energy.CensoBackend") as mock_backend_cls,
        patch("acp.workflows.energy_shared.ORCAInterface", return_value=orca),
        patch("acp.workflows.energy_shared.run_shermo", side_effect=shermo_values) as mock_shermo,
    ):
        backend = MagicMock()
        backend.refine_ensemble.return_value = mock_refinement_result
        mock_backend_cls.return_value = backend

        result = run_conformer_energy(
            input_source=str(multiframe_xyz),
            output_dir=str(tmp_path / "out"),
            preset="censo-default",
            config=sample_config,
            no_opt=True,  # must be ignored for censo-default (Part2 always on)
        )

    assert result.status == "completed"
    assert result.metadata["opt_enabled"] is True
    assert result.metadata["n_conformers"] == 2

    _, kwargs = backend.refine_ensemble.call_args
    assert kwargs["preset"] == "censo-default"

    # Geometry already optimized by CENSO Part2: no ACP opt/SP, only freq+Shermo
    orca.optimize.assert_not_called()
    orca.single_point.assert_not_called()
    assert orca.frequency.call_count == 2
    assert mock_shermo.call_count == 2

    # Shermo consumed the refinement SP energies from CENSO JSON
    sp_energies = sorted(
        c.kwargs["sp_energy"] for c in mock_shermo.call_args_list
    )
    expected = sorted(r.energy for r in mock_refinement_result.records)
    assert sp_energies == pytest.approx(expected)

    # Multi-frame outputs
    mol_dir = tmp_path / "out" / "input"
    lines = (mol_dir / "finalDFT" / "conformer_thermo.csv").read_text().strip().splitlines()
    assert len(lines) == 4  # header + 2 conformers + TOTAL row
    weights = [r.weight for r in result.ensemble.records]
    assert sum(weights) == pytest.approx(1.0, abs=1e-6)


def test_energy_default_shermo_failure_falls_back_to_gtot(
    tmp_path: Path,
    sample_config: dict[str, Any],
    mock_refinement_result: CensoRunResult,
    multiframe_xyz: Path,
) -> None:
    from acp.workflows.energy import run_conformer_energy

    orca = _mock_orca_instance()

    with (
        patch("acp.workflows.energy.CensoBackend") as mock_backend_cls,
        patch("acp.workflows.energy_shared.ORCAInterface", return_value=orca),
        patch("acp.workflows.energy_shared.run_shermo", return_value=None),
    ):
        backend = MagicMock()
        backend.refine_ensemble.return_value = mock_refinement_result
        mock_backend_cls.return_value = backend

        result = run_conformer_energy(
            input_source=str(multiframe_xyz),
            output_dir=str(tmp_path / "out"),
            preset="censo-default",
            config=sample_config,
        )

    assert result.status == "completed"
    # Fallback: gibbs = CENSO gtot
    gibbs = sorted(r.free_energy_hartree for r in result.ensemble.records)
    expected = sorted(r.gtot for r in mock_refinement_result.records)
    assert gibbs == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Output format compatibility
# ---------------------------------------------------------------------------


def test_final_outputs_format(tmp_path: Path) -> None:
    from acp.workflows.energy import _write_final_outputs

    candidates = [
        {
            "index": 0,
            "coordinates": np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 1.0]]),
            "symbols": ["C", "H"],
            "energy": -155.001,
            "gibbs": -154.95,
            "gibbs_correction": -154.95,
            "h_correction": None,
            "u_correction": None,
            "s_total": None,
            "g_conc": None,
            "source": "CONF1",
        },
    ]
    outputs = _write_final_outputs(candidates, tmp_path, "mol", 298.15)

    xyz_content = Path(outputs["all_conformers_xyz"]).read_text()
    assert xyz_content.startswith("2\n")
    assert "Conformer 0, E=-155.001000, Rank=1, Weight=1.0000" in xyz_content

    csv_content = Path(outputs["thermo_csv"]).read_text()
    header = csv_content.splitlines()[0]
    assert header == (
        "index,rank,energy_hartree,gibbs_correction,gibbs_hartree,"
        "h_correction,u_correction,s_total,g_conc,weight,source"
    )
    assert Path(outputs["global_min_xyz"]).name == "mol_global_min.xyz"

    # New ensemble-total outputs (workflow 1, single conformer → p1 = 1.0)
    assert outputs["total_gibbs_hartree"] == pytest.approx(-154.95)
    assert outputs["total_gibbs_kcal_mol"] == pytest.approx(-154.95 * 627.5094740631)
    thermo = json.loads(Path(outputs["ensemble_thermo_json"]).read_text())
    assert thermo["method"] == "dft_table"
    assert thermo["rank1_weight"] == pytest.approx(1.0)
    assert thermo["total_gibbs_hartree"] == pytest.approx(-154.95)
    assert thermo["population_coverage"] == pytest.approx(1.0)
    assert thermo["censo_reference_gibbs_hartree"] is None
    assert "boltzmann_table_json" not in outputs


def test_final_outputs_external_table_workflow2(tmp_path: Path) -> None:
    """Workflow 2: external CENSO table drives p₁/S_mix; G₁ is the fine DFT value."""
    from acp.workflows.energy import _write_final_outputs

    candidates = [
        {
            "index": 0,
            "coordinates": np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 1.0]]),
            "symbols": ["C", "H"],
            "energy": -155.001,
            "gibbs": -154.95,
            "gibbs_correction": None,
            "h_correction": None,
            "u_correction": None,
            "s_total": None,
            "g_conc": None,
            "source": "CONF1",
        },
    ]
    ext_weights = {"CONF1": 0.8442, "CONF2": 0.1558}
    ext_total = ensemble_total_gibbs_import(-154.95, 0.8442, 298.15)
    ext_total_censo = ensemble_total_gibbs_import(-154.83, 0.8442, 298.15)
    outputs = _write_final_outputs(
        candidates,
        tmp_path,
        "mol",
        298.15,
        external_weights=ext_weights,
        external_total_gibbs=ext_total,
        external_total_gibbs_censo=ext_total_censo,
    )

    thermo = json.loads(Path(outputs["ensemble_thermo_json"]).read_text())
    assert thermo["method"] == "censo_table_rank1"
    assert thermo["rank1_weight"] == pytest.approx(0.8442)
    assert thermo["total_gibbs_hartree"] == pytest.approx(ext_total)
    assert thermo["population_coverage"] == pytest.approx(1.0)
    assert thermo["censo_reference_gibbs_hartree"] == pytest.approx(ext_total_censo)
    assert len(thermo["conformers"]) == 2
    # only rank1 carries a fine DFT Gibbs
    by_id = {row["conf_id"]: row for row in thermo["conformers"]}
    assert by_id["CONF1"]["gibbs_hartree"] == pytest.approx(-154.95)
    assert by_id["CONF2"]["gibbs_hartree"] is None

    bt = json.loads(Path(outputs["boltzmann_table_json"]).read_text())
    assert bt["source"] == "censo"
    assert bt["weights"]["CONF1"] == pytest.approx(0.8442)
    assert bt["weights"]["CONF2"] == pytest.approx(0.1558)


def test_screening_ranking_csv(tmp_path: Path, mock_screening_result: CensoRunResult) -> None:
    from acp.workflows.energy import _write_screening_ranking

    path = _write_screening_ranking(mock_screening_result, tmp_path)
    content = Path(path).read_text()
    assert "conf_id" in content
    assert "CONF1" in content
    assert "CONF2" in content

# ---------------------------------------------------------------------------
# rank1_only (workflow 2): fine DFT on rank1 only + CENSO-table total G
# ---------------------------------------------------------------------------


def test_energy_rank1_only_light_opt_on(
    tmp_path: Path,
    sample_config: dict[str, Any],
    mock_screening_result: CensoRunResult,
    multiframe_xyz: Path,
) -> None:
    """--rank1-only: single handoff on CENSO rank1; G_total from CENSO table.

    The screening fixture spans ΔG ≈ 0.31 kcal/mol; the fine DFT G₁ (mock
    Shermo) enters G_total = G₁ + kT·ln p₁.
    """
    from acp.workflows.energy import run_conformer_energy

    orca = _mock_orca_instance()

    with (
        patch("acp.workflows.energy.CensoBackend") as mock_backend_cls,
        patch("acp.workflows.energy_shared.ORCAInterface", return_value=orca),
        patch(
            "acp.workflows.energy_shared.run_shermo", return_value=dict(_SHERMO_OK)
        ) as mock_shermo,
    ):
        backend = MagicMock()
        backend.refine_ensemble.return_value = mock_screening_result
        mock_backend_cls.return_value = backend

        result = run_conformer_energy(
            input_source=str(multiframe_xyz),
            output_dir=str(tmp_path / "out"),
            preset="censo-light",
            config=sample_config,
            rank1_only=True,
        )

    assert result.status == "completed"
    assert result.metadata["rank1_only"] is True
    assert result.metadata["n_conformers"] == 1

    # only rank1 received the fine DFT treatment
    assert orca.optimize.call_count == 1
    assert orca.frequency.call_count == 1
    assert orca.single_point.call_count == 1
    assert mock_shermo.call_count == 1

    # G_total = G1(fine) + kT·ln p1(CENSO table)
    mol_dir = tmp_path / "out" / "input"
    cen_weights = mock_screening_result.boltzmann_weights()
    p1 = cen_weights["CONF1"]
    g1 = _SHERMO_OK["g_conc"] if _SHERMO_OK["g_conc"] is not None else _SHERMO_OK["g_sum"]
    expected_total = (g1 + 3.166811563e-6 * 298.15 * math.log(p1)) * HARTREE_TO_KCAL
    assert result.metadata["total_gibbs_kcal_mol"] == pytest.approx(expected_total, abs=1e-6)
    # CENSO-level reference: gtot1 + kT·ln p1
    gtot1 = mock_screening_result.records[0].gtot
    expected_censo = (gtot1 + 3.166811563e-6 * 298.15 * math.log(p1)) * HARTREE_TO_KCAL
    assert result.metadata["total_gibbs_censo_hartree"] == pytest.approx(
        expected_censo / HARTREE_TO_KCAL, abs=1e-6
    )

    # outputs: ensemble_thermo.json (censo_table_rank1) + boltzmann_table.json
    summary = json.loads((mol_dir / "finalDFT" / "ensemble_thermo.json").read_text())
    assert summary["method"] == "censo_table_rank1"
    assert summary["rank1_weight"] == pytest.approx(p1, abs=1e-6)
    assert summary["total_gibbs_hartree"] == pytest.approx(
        result.metadata["total_gibbs_hartree"]
    )
    assert summary["population_coverage"] == pytest.approx(1.0)
    assert len(summary["conformers"]) == 2  # full CENSO table preserved

    bt = json.loads((mol_dir / "ensemble" / "boltzmann_table.json").read_text())
    assert bt["source"] == "censo"
    assert set(bt["weights"]) == {"CONF1", "CONF2"}

    # single-frame outputs: 1 frame + TOTAL row
    xyz_lines = (mol_dir / "finalDFT" / "all_conformers.xyz").read_text().strip().splitlines()
    assert xyz_lines[0] == "3"
    csv_lines = (mol_dir / "finalDFT" / "conformer_thermo.csv").read_text().strip().splitlines()
    assert len(csv_lines) == 3  # header + 1 conformer + TOTAL row
    # single-conformer DFT table → weight 1.0
    assert result.ensemble.records[0].weight == pytest.approx(1.0)


def test_energy_rank1_only_cheap_path(
    tmp_path: Path,
    sample_config: dict[str, Any],
    multiframe_xyz: Path,
) -> None:
    """--rank1-only + --no-opt: CENSO refines 1 frame; p table from xTB.

    With a ΔE = 2 kcal/mol two-frame ensemble, CENSO receives -n 1; the
    Boltzmann weights come from the xTB passthrough of the full ensemble
    (the 1-record CENSO table would degenerate to p₁ = 1).
    """
    from acp.workflows.energy import run_conformer_energy

    # 2 frames, ΔE = 2.0 kcal/mol — non-degenerate xTB table
    xyz = tmp_path / "close.xyz"
    e2 = -154.800000 + 2.0 / HARTREE_TO_KCAL
    xyz.write_text(
        "3\n-154.80000000\nC 0 0 0\nH 0 0 1.089\nH 1.027 0 -0.363\n"
        f"3\n{e2:.8f}\nC 0 0 0\nH 0 0 1.089\nH -1.027 0 -0.363\n"
    )

    refined = CensoRunResult(
        preset="censo-light",
        records=[_make_record("CONF1", 0, -154.850111)],
        final_part="refinement",
        temperature=298.15,
    )

    orca = _mock_orca_instance()
    with (
        patch("acp.workflows.energy.CensoBackend") as mock_backend_cls,
        patch("acp.workflows.energy_shared.ORCAInterface", return_value=orca),
        patch("acp.workflows.energy_shared.run_shermo") as mock_shermo,
    ):
        backend = MagicMock()
        backend.refine_ensemble.return_value = refined
        mock_backend_cls.return_value = backend

        result = run_conformer_energy(
            input_source=str(xyz),
            output_dir=str(tmp_path / "out"),
            preset="censo-light",
            config=sample_config,
            no_opt=True,
            rank1_only=True,
        )

    assert result.status == "completed"
    assert result.metadata["n_conformers"] == 1
    orca.optimize.assert_not_called()
    mock_shermo.assert_not_called()

    # CENSO restricted to the rank1 frame
    _, kwargs = backend.refine_ensemble.call_args
    assert kwargs["nconf"] == 1
    assert kwargs["include_refinement"] is True

    # p₁ from the xTB table: ΔE = 2 kcal/mol → p1 = 1/(1+exp(-2/0.5925))
    p1 = 1.0 / (1.0 + math.exp(-2.0 / (3.166811563e-6 * 298.15 * HARTREE_TO_KCAL)))
    gtot1 = refined.records[0].gtot
    expected_total = (gtot1 + 3.166811563e-6 * 298.15 * math.log(p1)) * HARTREE_TO_KCAL
    assert result.metadata["total_gibbs_kcal_mol"] == pytest.approx(expected_total, abs=1e-6)

    mol_dir = tmp_path / "out" / "close"
    summary = json.loads((mol_dir / "finalDFT" / "ensemble_thermo.json").read_text())
    assert summary["method"] == "xtb_table_rank1"
    assert summary["rank1_weight"] == pytest.approx(p1, abs=1e-6)
    bt = json.loads((mol_dir / "ensemble" / "boltzmann_table.json").read_text())
    assert bt["source"] == "xtb"
    assert set(bt["weights"]) == {"CONF1", "CONF2"}
