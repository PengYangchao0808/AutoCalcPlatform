"""Tests for the CENSO optimization stage semantics (energy workflow).

Covers the doc's opt-stage acceptance items:
- default (opt on): rank1 goes through ORCA opt+freq with identical
  method/basis (v7 consistency rule);
- --no-opt: rank1 skips ORCA opt/freq (cheap RSH//xTB path);
- censo-zero opt-on: CENSO Part2/Part3 never triggered;
- censo-default: CENSO called with --optimization, survivors get
  freq+Shermo each;
- default opt functional is r2SCAN-3c;
- levels thermo.scale_factor is passed through to run_shermo(scl_zpe=...).

Also covers the CensoBackend extensions added for the energy workflow
(include_refinement / nconf / part_overrides).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from acp.backends.censo_backend import CensoBackend, CensoConformerRecord, CensoRunResult
from cccp.qc.interfaces.censo import CensoInterface

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "executables": {
            "censo": {"path": "censo"},
            "orca": {"path": "orca"},
            "xtb": {"path": "xtb"},
            "shermo": {"path": "Shermo"},
        },
        "resources": {"nproc": 4},
        "censo": {"preset": "censo-light", "temperature": 298.15},
    }
    config.update(overrides)
    return config


def _make_record(conf_id: str, frame_index: int, gtot: float) -> CensoConformerRecord:
    return CensoConformerRecord(
        conf_id=conf_id,
        frame_index=frame_index,
        energy=gtot + 0.08,
        gsolv=0.0,
        grrho=-0.08,
        gtot=gtot,
        coordinates=np.array(
            [[0.0, 0.0, 0.0], [0.0, 0.0, 1.089], [1.027, 0.0, -0.363]]
        ),
        symbols=["C", "H", "H"],
    )


def _screening_result() -> CensoRunResult:
    result = CensoRunResult(
        preset="censo-light",
        records=[_make_record("CONF1", 0, -154.84), _make_record("CONF2", 1, -154.83)],
        final_part="screening",
        temperature=298.15,
    )
    result.sort_by_gtot()
    return result


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


def _mock_orca_backend_cls(orca: MagicMock) -> MagicMock:
    """Return a fake ``get_backend("orca")`` class that yields *orca*."""
    backend_cls = MagicMock()
    backend_cls.return_value = orca
    return backend_cls


_SHERMO_OK = {
    "g_sum": -154.95,
    "g_conc": None,
    "h_sum": None,
    "u_sum": None,
    "s_total": None,
}


@pytest.fixture
def multiframe_xyz(tmp_path: Path) -> Path:
    xyz = tmp_path / "input.xyz"
    xyz.write_text(
        "3\nFrame 0\nC 0 0 0\nH 0 0 1.089\nH 1.027 0 -0.363\n"
        "3\nFrame 1\nC 0 0 0\nH 0 0 1.089\nH -1.027 0 -0.363\n"
    )
    return xyz


def _run_energy(
    tmp_path: Path,
    multiframe_xyz: Path,
    censo_result: CensoRunResult | None,
    orca: MagicMock,
    shermo_return: Any = None,
    **kwargs: Any,
):
    from acp.workflows.energy import run_conformer_energy

    with (
        patch("acp.workflows.energy.CensoBackend") as mock_backend_cls,
        patch("acp.workflows.energy_shared.get_backend", return_value=_mock_orca_backend_cls(orca)) as mock_get_backend,
        patch("acp.workflows.energy_shared.run_shermo", return_value=shermo_return) as mock_shermo,
    ):
        backend = MagicMock()
        if censo_result is not None:
            backend.refine_ensemble.return_value = censo_result
        mock_backend_cls.return_value = backend

        result = run_conformer_energy(
            input_source=str(multiframe_xyz),
            output_dir=str(tmp_path / "out"),
            config=_make_config(),
            **kwargs,
        )
    return result, backend, mock_get_backend, mock_shermo


# ---------------------------------------------------------------------------
# Consistency rule: opt and freq use the same method/basis
# ---------------------------------------------------------------------------


def test_opt_and_freq_use_same_method_and_basis(
    tmp_path: Path, multiframe_xyz: Path,
) -> None:
    orca = _mock_orca_instance()
    result, _, _, _ = _run_energy(
        tmp_path, multiframe_xyz, _screening_result(), orca,
        shermo_return=dict(_SHERMO_OK), preset="censo-light",
    )
    assert result.status == "completed"

    opt_kwargs = orca.optimize.call_args.kwargs
    freq_kwargs = orca.frequency.call_args.kwargs
    assert opt_kwargs["method"] == freq_kwargs["method"]
    assert opt_kwargs["basis"] == freq_kwargs["basis"]

    # SP runs at the refinement level, not at the opt level
    # (config sources use CENSO-style lowercase; ORCA keywords are
    # case-insensitive, so compare case-insensitively)
    sp_kwargs = orca.single_point.call_args.kwargs
    assert sp_kwargs["method"].lower() == "wb97m-v"
    assert sp_kwargs["basis"].lower() == "def2-tzvpp"


def test_default_opt_functional_is_r2scan3c(
    tmp_path: Path, multiframe_xyz: Path,
) -> None:
    orca = _mock_orca_instance()
    result, _, _, _ = _run_energy(
        tmp_path, multiframe_xyz, _screening_result(), orca,
        shermo_return=dict(_SHERMO_OK), preset="censo-light",
    )
    assert result.status == "completed"
    assert orca.optimize.call_args.kwargs["method"] == "r2SCAN-3c"
    assert orca.frequency.call_args.kwargs["method"] == "r2SCAN-3c"


def test_levels_dft_opt_functional_override(
    tmp_path: Path, multiframe_xyz: Path,
) -> None:
    orca = _mock_orca_instance()
    result, _, _, _ = _run_energy(
        tmp_path, multiframe_xyz, _screening_result(), orca,
        shermo_return=dict(_SHERMO_OK), preset="censo-light",
        levels={"dft_opt": {"functional": "B97-3c"}},
    )
    assert result.status == "completed"
    assert orca.optimize.call_args.kwargs["method"] == "B97-3c"
    assert orca.frequency.call_args.kwargs["method"] == "B97-3c"


def test_levels_refinement_sp_override(
    tmp_path: Path, multiframe_xyz: Path,
) -> None:
    orca = _mock_orca_instance()
    result, _, _, _ = _run_energy(
        tmp_path, multiframe_xyz, _screening_result(), orca,
        shermo_return=dict(_SHERMO_OK), preset="censo-light",
        levels={"refinement_sp": {"functional": "DLPNO-CCSD(T)", "basis": "def2-TZVPP"}},
    )
    assert result.status == "completed"
    assert orca.single_point.call_args.kwargs["method"] == "DLPNO-CCSD(T)"


# ---------------------------------------------------------------------------
# ZPE scale factor passthrough (v8 end-to-end)
# ---------------------------------------------------------------------------


def test_thermo_scale_factor_reaches_run_shermo(
    tmp_path: Path, multiframe_xyz: Path,
) -> None:
    orca = _mock_orca_instance()
    result, _, _, mock_shermo = _run_energy(
        tmp_path, multiframe_xyz, _screening_result(), orca,
        shermo_return=dict(_SHERMO_OK), preset="censo-light",
        levels={"thermo": {"scale_factor": 0.98}},
    )
    assert result.status == "completed"
    assert mock_shermo.call_args.kwargs["scl_zpe"] == pytest.approx(0.98)


def test_thermo_scale_factor_default_fallback(
    tmp_path: Path, multiframe_xyz: Path,
) -> None:
    orca = _mock_orca_instance()
    result, _, _, mock_shermo = _run_energy(
        tmp_path, multiframe_xyz, _screening_result(), orca,
        shermo_return=dict(_SHERMO_OK), preset="censo-light",
    )
    assert result.status == "completed"
    assert mock_shermo.call_args.kwargs["scl_zpe"] == pytest.approx(0.9905)


# ---------------------------------------------------------------------------
# Opt on/off path switching
# ---------------------------------------------------------------------------


def test_no_opt_skips_orca_entirely(tmp_path: Path, multiframe_xyz: Path) -> None:
    refinement = CensoRunResult(
        preset="censo-light",
        records=[_make_record("CONF1", 0, -154.85)],
        final_part="refinement",
        temperature=298.15,
    )
    orca = _mock_orca_instance()
    result, backend, mock_get_backend, mock_shermo = _run_energy(
        tmp_path, multiframe_xyz, refinement, orca,
        preset="censo-light", no_opt=True,
    )
    assert result.status == "completed"
    mock_get_backend.assert_not_called()
    mock_shermo.assert_not_called()
    assert backend.refine_ensemble.call_args.kwargs["include_refinement"] is True


def test_config_can_disable_opt_stage(tmp_path: Path, multiframe_xyz: Path) -> None:
    """censo.optimization.enabled=false in config behaves like --no-opt."""
    from acp.workflows.energy import run_conformer_energy

    refinement = CensoRunResult(
        preset="censo-light",
        records=[_make_record("CONF1", 0, -154.85)],
        final_part="refinement",
        temperature=298.15,
    )
    cfg = _make_config()
    cfg["censo"]["optimization"] = {"enabled": False}
    orca = _mock_orca_instance()

    with (
        patch("acp.workflows.energy.CensoBackend") as mock_backend_cls,
        patch("acp.workflows.energy_shared.get_backend", return_value=_mock_orca_backend_cls(orca)) as mock_get_backend,
        patch("acp.workflows.energy_shared.run_shermo"),
    ):
        backend = MagicMock()
        backend.refine_ensemble.return_value = refinement
        mock_backend_cls.return_value = backend

        result = run_conformer_energy(
            input_source=str(multiframe_xyz),
            output_dir=str(tmp_path / "out"),
            preset="censo-light",
            config=cfg,
        )

    assert result.status == "completed"
    assert result.metadata["opt_enabled"] is False
    mock_get_backend.assert_not_called()


def test_zero_opt_on_does_not_trigger_censo_parts(
    tmp_path: Path, multiframe_xyz: Path,
) -> None:
    orca = _mock_orca_instance()
    result, backend, _, _ = _run_energy(
        tmp_path, multiframe_xyz, None, orca,
        shermo_return=dict(_SHERMO_OK), preset="censo-zero",
    )
    assert result.status == "completed"
    backend.refine_ensemble.assert_not_called()


def test_default_preset_runs_censo_optimization_and_survivor_thermo(
    tmp_path: Path, multiframe_xyz: Path,
) -> None:
    refinement = CensoRunResult(
        preset="censo-default",
        records=[_make_record("CONF1", 0, -154.85), _make_record("CONF3", 2, -154.84)],
        final_part="refinement",
        temperature=298.15,
    )
    orca = _mock_orca_instance()
    result, backend, _, mock_shermo = _run_energy(
        tmp_path, multiframe_xyz, refinement, orca,
        shermo_return=dict(_SHERMO_OK), preset="censo-default",
    )
    assert result.status == "completed"
    assert backend.refine_ensemble.call_args.kwargs["preset"] == "censo-default"
    # Same-level freq + Shermo for every survivor
    assert orca.frequency.call_count == 2
    assert mock_shermo.call_count == 2
    orca.optimize.assert_not_called()


def test_opt_failure_fails_workflow(tmp_path: Path, multiframe_xyz: Path) -> None:
    orca = _mock_orca_instance()
    orca.optimize.return_value.success = False
    orca.optimize.return_value.error_message = "SCF did not converge"

    result, _, _, _ = _run_energy(
        tmp_path, multiframe_xyz, _screening_result(), orca,
        preset="censo-light",
    )
    assert result.status == "failed"
    assert "optimization failed" in (result.error or "")


# ---------------------------------------------------------------------------
# CensoBackend extensions: include_refinement / nconf / part_overrides
# ---------------------------------------------------------------------------


def test_build_cli_nconf_flag(tmp_path: Path) -> None:
    interface = CensoInterface(_make_config())
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("1\n\nH 0 0 0\n")
    rcfile = tmp_path / "censo2rc"
    rcfile.write_text("")

    preset = interface.resolve_preset("censo-zero")
    cmd = interface.build_cli(
        input_xyz, rcfile, preset,
        nproc=4, temperature=298.15, solvent=None, nconf=1,
    )
    assert "-n" in cmd
    assert cmd[cmd.index("-n") + 1] == "1"
    assert "--refinement" in cmd


def test_build_cli_no_nconf_by_default(tmp_path: Path) -> None:
    interface = CensoInterface(_make_config())
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("1\n\nH 0 0 0\n")
    rcfile = tmp_path / "censo2rc"
    rcfile.write_text("")

    preset = interface.resolve_preset("censo-light")
    cmd = interface.build_cli(
        input_xyz, rcfile, preset,
        nproc=4, temperature=298.15, solvent=None,
    )
    assert "-n" not in cmd


def test_refine_ensemble_include_refinement_appends_part(tmp_path: Path) -> None:
    """include_refinement=True must add --refinement to the CLI call."""
    interface = CensoInterface(_make_config())
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("1\n\nH 0 0 0\n")

    captured: dict[str, Any] = {}

    def fake_run(cmd, **kwargs):  # noqa: ANN001
        captured["cmd"] = cmd
        raise FileNotFoundError("stop here")

    with (
        patch("cccp.qc.interfaces.censo.shutil.which", return_value="/usr/bin/censo"),
        patch("cccp.qc.interfaces.censo.subprocess.run", side_effect=fake_run),
        pytest.raises(Exception),
    ):
        interface.refine_ensemble(
            input_xyz, tmp_path / "censo",
            preset="censo-light",
            include_refinement=True,
        )

    assert "--refinement" in captured["cmd"]
    assert "--prescreening" in captured["cmd"]
    assert "--screening" in captured["cmd"]


def test_refine_ensemble_part_overrides_reach_rcfile(tmp_path: Path) -> None:
    interface = CensoInterface(_make_config())
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("1\n\nH 0 0 0\n")
    censo_dir = tmp_path / "censo"

    with (
        patch("cccp.qc.interfaces.censo.shutil.which", return_value="/usr/bin/censo"),
        patch(
            "cccp.qc.interfaces.censo.subprocess.run",
            side_effect=FileNotFoundError("stop here"),
        ),
        pytest.raises(Exception),
    ):
        interface.refine_ensemble(
            input_xyz, censo_dir,
            preset="censo-light",
            include_refinement=True,
            part_overrides={"refinement": {"func": "dlpno-ccsd(t)"}},
        )

    rcfile_content = (censo_dir / "censo2rc").read_text()
    assert "func = dlpno-ccsd(t)" in rcfile_content


def test_part_overrides_do_not_mutate_preset_definitions(tmp_path: Path) -> None:
    """Presets are deep-copied — overrides must not leak into module state."""
    from cccp.qc.interfaces.censo import CENSO_PRESETS

    interface = CensoInterface(_make_config())
    preset = interface.resolve_preset("censo-light")
    preset["screening"]["func"] = "mutated"
    assert CENSO_PRESETS["censo-light"]["screening"]["func"] == "b97-3c"
