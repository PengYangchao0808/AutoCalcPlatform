"""Tests for ORCA normal-opt controls and SCF keyword rendering.

Covers the contract keys emitted by the batch engine:
  Optimize: geom_maxiter / max_cycles / opt_level / trust_radius / initial_hessian
  All steps: scf_maxiter / scf_convergence / scf_strategy
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch, MagicMock

import numpy as np
import pytest

from cccp.qc.interfaces.orca import ORCAInterface, render_scf_block


def _make_config(**overrides: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "executables": {"orca": {"path": "orca"}},
        "resources": {"nproc": 4},
    }
    config.update(overrides)
    return config


_SYMBOLS = ["C", "H", "H", "H", "H"]
_COORDS = np.array([
    [0.0, 0.0, 0.0],
    [0.63, 0.63, 0.63],
    [-0.63, -0.63, 0.63],
    [-0.63, 0.63, -0.63],
    [0.63, -0.63, -0.63],
], dtype=float)


class TestNormalOptGeomBlock:
    def test_geom_maxiter_rendered(self) -> None:
        orca = ORCAInterface(_make_config(), method="r2SCAN-3c")
        blocks, _ = orca._build_input_blocks("opt", geom_maxiter=250, symbols=_SYMBOLS)
        assert "MaxIter 250" in blocks

    def test_max_cycles_fallback(self) -> None:
        orca = ORCAInterface(_make_config(), method="r2SCAN-3c")
        input_file = Path("/dev/null")
        with (
            patch.object(orca, "_run_orca", return_value=True),
            patch.object(orca, "_build_input_blocks", wraps=orca._build_input_blocks) as mock_build,
        ):
            orca.optimize(_COORDS, _SYMBOLS, output_dir=Path("/tmp"), max_cycles=200)
            call_kwargs = mock_build.call_args
            assert call_kwargs[1].get("geom_maxiter") == 200 or call_kwargs.kwargs.get("geom_maxiter") == 200

    def test_trust_radius_rendered(self) -> None:
        orca = ORCAInterface(_make_config(), method="r2SCAN-3c")
        blocks, _ = orca._build_input_blocks("opt", trust_radius=0.15, symbols=_SYMBOLS)
        assert "Trust 0.15" in blocks

    def test_trust_radius_format(self) -> None:
        orca = ORCAInterface(_make_config(), method="r2SCAN-3c")
        blocks, _ = orca._build_input_blocks("opt", trust_radius=0.3, symbols=_SYMBOLS)
        assert "Trust 0.3" in blocks

    def test_calc_hess_rendered(self) -> None:
        orca = ORCAInterface(_make_config(), method="r2SCAN-3c")
        blocks, _ = orca._build_input_blocks(
            "opt", initial_hessian="calculate", symbols=_SYMBOLS,
        )
        assert "Calc_Hess true" in blocks

    def test_calc_hess_absent_for_model(self) -> None:
        orca = ORCAInterface(_make_config(), method="r2SCAN-3c")
        blocks, _ = orca._build_input_blocks(
            "opt", initial_hessian="model", symbols=_SYMBOLS,
        )
        assert "Calc_Hess" not in blocks

    def test_calc_hess_absent_for_none(self) -> None:
        orca = ORCAInterface(_make_config(), method="r2SCAN-3c")
        blocks, _ = orca._build_input_blocks("opt", symbols=_SYMBOLS)
        assert "Calc_Hess" not in blocks

    def test_opt_level_tight(self) -> None:
        orca = ORCAInterface(_make_config(), method="r2SCAN-3c")
        blocks, _ = orca._build_input_blocks("opt", opt_level="tight", symbols=_SYMBOLS)
        route_line = blocks.splitlines()[0]
        assert "TightOpt" in route_line

    def test_opt_level_verytight(self) -> None:
        orca = ORCAInterface(_make_config(), method="r2SCAN-3c")
        blocks, _ = orca._build_input_blocks("opt", opt_level="verytight", symbols=_SYMBOLS)
        route_line = blocks.splitlines()[0]
        assert "VeryTightOpt" in route_line

    def test_opt_level_normal_no_keyword(self) -> None:
        orca = ORCAInterface(_make_config(), method="r2SCAN-3c")
        blocks, _ = orca._build_input_blocks("opt", opt_level="normal", symbols=_SYMBOLS)
        route_line = blocks.splitlines()[0]
        assert "TightOpt" not in route_line
        assert "VeryTightOpt" not in route_line
        assert "LooseOpt" not in route_line

    def test_opt_level_loose(self) -> None:
        orca = ORCAInterface(_make_config(), method="r2SCAN-3c")
        blocks, _ = orca._build_input_blocks("opt", opt_level="loose", symbols=_SYMBOLS)
        route_line = blocks.splitlines()[0]
        assert "LooseOpt" in route_line

    def test_geom_block_order(self) -> None:
        """Calc_Hess → Recalc_Hess → Trust → MaxIter order in %geom."""
        orca = ORCAInterface(_make_config(), method="r2SCAN-3c")
        blocks, _ = orca._build_input_blocks(
            "opt",
            initial_hessian="calculate",
            trust_radius=0.15,
            geom_maxiter=300,
            symbols=_SYMBOLS,
        )
        geom_start = blocks.index("%geom")
        geom_end = blocks.index("end", geom_start)
        geom_block = blocks[geom_start:geom_end]
        calc_pos = geom_block.find("Calc_Hess")
        trust_pos = geom_block.find("Trust")
        maxiter_pos = geom_block.find("MaxIter")
        assert calc_pos < trust_pos < maxiter_pos


class TestScfControls:
    def test_scf_maxiter_in_block(self) -> None:
        orca = ORCAInterface(_make_config(), method="r2SCAN-3c")
        blocks, _ = orca._build_input_blocks("opt", scf_maxiter=500, symbols=_SYMBOLS)
        assert "MaxIter 500" in blocks
        assert "%scf" in blocks

    def test_scf_maxiter_in_sp(self) -> None:
        orca = ORCAInterface(_make_config(), method="r2SCAN-3c")
        blocks, _ = orca._build_input_blocks("sp", scf_maxiter=600, symbols=_SYMBOLS)
        assert "MaxIter 600" in blocks

    def test_scf_maxiter_in_freq(self) -> None:
        orca = ORCAInterface(_make_config(), method="r2SCAN-3c")
        blocks, _ = orca._build_input_blocks("freq", scf_maxiter=400, symbols=_SYMBOLS)
        assert "MaxIter 400" in blocks

    def test_scf_convergence_tight(self) -> None:
        orca = ORCAInterface(_make_config(), method="r2SCAN-3c")
        blocks, _ = orca._build_input_blocks("sp", scf_convergence="tight", symbols=_SYMBOLS)
        route_line = blocks.splitlines()[0]
        assert "TightSCF" in route_line

    def test_scf_convergence_verytight(self) -> None:
        orca = ORCAInterface(_make_config(), method="r2SCAN-3c")
        blocks, _ = orca._build_input_blocks("sp", scf_convergence="verytight", symbols=_SYMBOLS)
        route_line = blocks.splitlines()[0]
        assert "VeryTightSCF" in route_line

    def test_scf_convergence_not_duplicated_for_dlpno(self) -> None:
        """TightSCF not duplicated when DLPNO-CCSD(T) already adds it."""
        orca = ORCAInterface(_make_config(), method="DLPNO-CCSD(T)")
        blocks, _ = orca._build_input_blocks(
            "sp",
            scf_convergence="tight",
            basis="def2-TZVPP",
            aux_j_basis="def2/J",
            aux_c_basis="def2-TZVPP/C",
            symbols=_SYMBOLS,
        )
        route_line = blocks.splitlines()[0]
        assert route_line.count("TightSCF") == 1

    def test_scf_strategy_slowconv(self) -> None:
        orca = ORCAInterface(_make_config(), method="r2SCAN-3c")
        blocks, _ = orca._build_input_blocks("sp", scf_strategy="slowconv", symbols=_SYMBOLS)
        route_line = blocks.splitlines()[0]
        assert "SlowConv" in route_line

    def test_scf_strategy_soscf(self) -> None:
        orca = ORCAInterface(_make_config(), method="r2SCAN-3c")
        blocks, _ = orca._build_input_blocks("sp", scf_strategy="soscf", symbols=_SYMBOLS)
        route_line = blocks.splitlines()[0]
        assert "SOSCF" in route_line

    def test_scf_strategy_normal_no_keyword(self) -> None:
        orca = ORCAInterface(_make_config(), method="r2SCAN-3c")
        blocks, _ = orca._build_input_blocks("sp", scf_strategy="normal", symbols=_SYMBOLS)
        route_line = blocks.splitlines()[0]
        assert "SlowConv" not in route_line
        assert "SOSCF" not in route_line


class TestRenderScfBlockMaxiter:
    def test_maxiter_in_scf_block(self) -> None:
        block = render_scf_block({"maxiter": 500})
        assert block is not None
        assert "MaxIter 500" in block

    def test_maxiter_zero_ignored(self) -> None:
        block = render_scf_block({"maxiter": 0})
        assert block is None or "Maxiter" not in block

    def test_maxiter_negative_ignored(self) -> None:
        block = render_scf_block({"maxiter": -1})
        assert block is None or "Maxiter" not in block

    def test_maxiter_combined_with_existing(self) -> None:
        block = render_scf_block({"hf_typ": "UHF", "maxiter": 300})
        assert block is not None
        assert "HFTyp UHF" in block
        assert "MaxIter 300" in block


class TestDefaultsUnchanged:
    def test_no_controls_no_extra_lines(self) -> None:
        orca = ORCAInterface(_make_config(), method="r2SCAN-3c")
        blocks, _ = orca._build_input_blocks("opt", symbols=_SYMBOLS)
        assert "MaxIter" not in blocks
        assert "Trust" not in blocks
        assert "Calc_Hess" not in blocks
        assert "TightOpt" not in blocks
        assert "VeryTightOpt" not in blocks
        assert "LooseOpt" not in blocks
        assert "SlowConv" not in blocks
        assert "SOSCF" not in blocks
        assert "TightSCF" not in blocks
        assert "NormalSCF" not in blocks
        assert blocks.splitlines()[0] == "! r2SCAN-3c Opt"

    def test_sp_no_controls_clean(self) -> None:
        orca = ORCAInterface(_make_config(), method="r2SCAN-3c")
        blocks, _ = orca._build_input_blocks("sp", symbols=_SYMBOLS)
        assert "%geom" not in blocks
        assert "MaxIter" not in blocks
        assert "TightSCF" not in blocks

    def test_freq_no_controls_clean(self) -> None:
        orca = ORCAInterface(_make_config(), method="r2SCAN-3c")
        blocks, _ = orca._build_input_blocks("freq", symbols=_SYMBOLS)
        assert "%geom" not in blocks
        assert "MaxIter" not in blocks
