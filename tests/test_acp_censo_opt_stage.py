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

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np
import pytest

from acp.backends.censo_backend import CensoConformerRecord, CensoRunResult
from cccp.calculation.contracts import ArtifactRef
from cccp.calculation.requests import TaskKind
from cccp.calculation.results import TaskResult
from cccp.qc.interfaces.censo import CensoInterface, part_index

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _stub_censo_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the centralized resolver so CENSO tests run without the binary."""

    def _resolve(name: str, configured_path: str | Path | None = None) -> Path | None:
        return Path("/usr/bin/censo") if name == "censo" else None

    monkeypatch.setattr("cccp.qc.interfaces.censo.resolve_executable", _resolve)


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
        coordinates=np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 1.089], [1.027, 0.0, -0.363]]),
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


def _task_result(
    task: TaskKind,
    *,
    energy: float | None = None,
    coordinates: Any = None,
    symbols: Any = None,
    log: str | None = None,
    log_type: str = "log",
    status: str = "completed",
    errors: tuple[str, ...] = (),
    metadata: dict[str, Any] | None = None,
) -> TaskResult:
    artifacts = (ArtifactRef(path=Path(log), type=log_type),) if log else ()
    return TaskResult(
        task=task,
        status=status,
        complete=status == "completed",
        errors=errors,
        energy_hartree=energy,
        coordinates=(
            tuple(tuple(float(c) for c in row) for row in coordinates)
            if coordinates is not None
            else None
        ),
        symbols=tuple(symbols) if symbols is not None else None,
        artifacts=artifacts,
        metadata=dict(metadata or {}),
    )


def _mock_opt_result() -> TaskResult:
    return _task_result(
        TaskKind.OPTIMIZE,
        energy=-154.90,
        coordinates=[[0.0, 0.0, 0.0], [0.0, 0.0, 1.09], [1.03, 0.0, -0.36]],
        symbols=["C", "H", "H"],
        log="/tmp/opt.out",
    )


def _mock_freq_result() -> TaskResult:
    return _task_result(TaskKind.FREQUENCY, log="/tmp/freq.out", log_type="frequency_log")


def _mock_sp_result() -> TaskResult:
    return _task_result(TaskKind.SINGLEPOINT, energy=-155.001234, log="/tmp/sp.out")


def _mock_shermo_result(values: dict[str, Any] | None) -> TaskResult:
    if values is None:
        return _task_result(
            TaskKind.THERMOCHEMISTRY,
            status="failed",
            errors=("Shermo returned no thermochemistry data",),
        )
    return _task_result(TaskKind.THERMOCHEMISTRY, metadata=dict(values))


def _fake_censo_refine(result: CensoRunResult) -> Any:
    """Patch side effect for ``energy_shared.run_censo_refine``.

    Writes the final-part CENSO JSON/XYZ artifacts (the
    ``<idx>_<FINAL_PART>`` convention of the censo_refine task) and returns
    the typed task result so ``censo_refine_via_task`` reconstructs the real
    ``CensoRunResult`` end to end.
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
    *,
    opt_result: TaskResult | None = None,
    shermo_return: Any = None,
    **kwargs: Any,
):
    pytest.importorskip("acp.workflows.energy")
    from acp.workflows.energy import run_conformer_energy

    censo_ctx = (
        patch(
            "acp.workflows.energy_shared.run_censo_refine",
            side_effect=_fake_censo_refine(censo_result),
        )
        if censo_result is not None
        else patch("acp.workflows.energy_shared.run_censo_refine")
    )

    with (
        censo_ctx as mock_censo,
        patch(
            "acp.workflows.energy_shared.run_optimize",
            return_value=opt_result if opt_result is not None else _mock_opt_result(),
        ) as mock_opt,
        patch(
            "acp.workflows.energy_shared.run_frequency",
            return_value=_mock_freq_result(),
        ) as mock_freq,
        patch(
            "acp.workflows.energy_shared.run_singlepoint",
            return_value=_mock_sp_result(),
        ) as mock_sp,
        patch(
            "acp.workflows.energy_shared.run_thermochemistry",
            return_value=_mock_shermo_result(shermo_return),
        ) as mock_shermo,
    ):
        result = run_conformer_energy(
            input_source=str(multiframe_xyz),
            output_dir=str(tmp_path / "out"),
            config=_make_config(),
            **kwargs,
        )
    return result, mock_censo, mock_opt, mock_freq, mock_sp, mock_shermo


# ---------------------------------------------------------------------------
# Consistency rule: opt and freq use the same method/basis
# ---------------------------------------------------------------------------


def test_opt_and_freq_use_same_method_and_basis(
    tmp_path: Path,
    multiframe_xyz: Path,
) -> None:
    result, _, mock_opt, mock_freq, mock_sp, _ = _run_energy(
        tmp_path,
        multiframe_xyz,
        _screening_result(),
        shermo_return=dict(_SHERMO_OK),
        preset="censo-light",
    )
    assert result.status == "completed"

    opt_level = mock_opt.call_args.args[0].level
    freq_level = mock_freq.call_args.args[0].level
    assert opt_level.method == freq_level.method
    assert opt_level.basis == freq_level.basis

    # SP runs at the refinement level, not at the opt level
    # (config sources use CENSO-style lowercase; ORCA keywords are
    # case-insensitive, so compare case-insensitively)
    sp_level = mock_sp.call_args.args[0].level
    assert sp_level.method.lower() == "wb97m-v"
    assert sp_level.basis.lower() == "def2-tzvpp"


def test_default_opt_functional_is_r2scan3c(
    tmp_path: Path,
    multiframe_xyz: Path,
) -> None:
    result, _, mock_opt, mock_freq, _, _ = _run_energy(
        tmp_path,
        multiframe_xyz,
        _screening_result(),
        shermo_return=dict(_SHERMO_OK),
        preset="censo-light",
    )
    assert result.status == "completed"
    assert mock_opt.call_args.args[0].level.method == "r2SCAN-3c"
    assert mock_freq.call_args.args[0].level.method == "r2SCAN-3c"


def test_levels_dft_opt_functional_override(
    tmp_path: Path,
    multiframe_xyz: Path,
) -> None:
    result, _, mock_opt, mock_freq, _, _ = _run_energy(
        tmp_path,
        multiframe_xyz,
        _screening_result(),
        shermo_return=dict(_SHERMO_OK),
        preset="censo-light",
        levels={"dft_opt": {"functional": "B97-3c"}},
    )
    assert result.status == "completed"
    assert mock_opt.call_args.args[0].level.method == "B97-3c"
    assert mock_freq.call_args.args[0].level.method == "B97-3c"


def test_levels_refinement_sp_override(
    tmp_path: Path,
    multiframe_xyz: Path,
) -> None:
    result, _, _, _, mock_sp, _ = _run_energy(
        tmp_path,
        multiframe_xyz,
        _screening_result(),
        shermo_return=dict(_SHERMO_OK),
        preset="censo-light",
        levels={"refinement_sp": {"functional": "DLPNO-CCSD(T)", "basis": "def2-TZVPP"}},
    )
    assert result.status == "completed"
    assert mock_sp.call_args.args[0].level.method == "DLPNO-CCSD(T)"


# ---------------------------------------------------------------------------
# ZPE scale factor passthrough (v8 end-to-end)
# ---------------------------------------------------------------------------


def test_thermo_scale_factor_reaches_run_shermo(
    tmp_path: Path,
    multiframe_xyz: Path,
) -> None:
    result, _, _, _, _, mock_shermo = _run_energy(
        tmp_path,
        multiframe_xyz,
        _screening_result(),
        shermo_return=dict(_SHERMO_OK),
        preset="censo-light",
        levels={"thermo": {"scale_factor": 0.98}},
    )
    assert result.status == "completed"
    assert mock_shermo.call_args.args[0].options.scl_zpe == pytest.approx(0.98)


def test_thermo_scale_factor_default_fallback(
    tmp_path: Path,
    multiframe_xyz: Path,
) -> None:
    result, _, _, _, _, mock_shermo = _run_energy(
        tmp_path,
        multiframe_xyz,
        _screening_result(),
        shermo_return=dict(_SHERMO_OK),
        preset="censo-light",
    )
    assert result.status == "completed"
    assert mock_shermo.call_args.args[0].options.scl_zpe == pytest.approx(0.9905)


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
    result, mock_censo, mock_opt, mock_freq, mock_sp, mock_shermo = _run_energy(
        tmp_path,
        multiframe_xyz,
        refinement,
        preset="censo-light",
        no_opt=True,
    )
    assert result.status == "completed"
    mock_opt.assert_not_called()
    mock_freq.assert_not_called()
    mock_sp.assert_not_called()
    mock_shermo.assert_not_called()
    extras = mock_censo.call_args.kwargs["context"].capability_extras
    assert extras["include_refinement"] is True


def test_config_can_disable_opt_stage(tmp_path: Path, multiframe_xyz: Path) -> None:
    """censo.optimization.enabled=false in config behaves like --no-opt."""
    pytest.importorskip("acp.workflows.energy")
    from acp.workflows.energy import run_conformer_energy

    refinement = CensoRunResult(
        preset="censo-light",
        records=[_make_record("CONF1", 0, -154.85)],
        final_part="refinement",
        temperature=298.15,
    )
    cfg = _make_config()
    cfg["censo"]["optimization"] = {"enabled": False}

    with (
        patch(
            "acp.workflows.energy_shared.run_censo_refine",
            side_effect=_fake_censo_refine(refinement),
        ) as mock_censo,
        patch("acp.workflows.energy_shared.run_optimize") as mock_opt,
        patch("acp.workflows.energy_shared.run_frequency"),
        patch("acp.workflows.energy_shared.run_singlepoint"),
        patch("acp.workflows.energy_shared.run_thermochemistry"),
    ):
        result = run_conformer_energy(
            input_source=str(multiframe_xyz),
            output_dir=str(tmp_path / "out"),
            preset="censo-light",
            config=cfg,
        )

    assert result.status == "completed"
    assert result.metadata["opt_enabled"] is False
    mock_opt.assert_not_called()
    assert mock_censo.call_args.kwargs["context"].capability_extras["include_refinement"] is True


def test_zero_opt_on_does_not_trigger_censo_parts(
    tmp_path: Path,
    multiframe_xyz: Path,
) -> None:
    result, mock_censo, _, _, _, _ = _run_energy(
        tmp_path,
        multiframe_xyz,
        None,
        shermo_return=dict(_SHERMO_OK),
        preset="censo-zero",
    )
    assert result.status == "completed"
    mock_censo.assert_not_called()


def test_default_preset_runs_censo_optimization_and_survivor_thermo(
    tmp_path: Path,
    multiframe_xyz: Path,
) -> None:
    refinement = CensoRunResult(
        preset="censo-default",
        records=[_make_record("CONF1", 0, -154.85), _make_record("CONF3", 2, -154.84)],
        final_part="refinement",
        temperature=298.15,
    )
    result, mock_censo, mock_opt, mock_freq, _, mock_shermo = _run_energy(
        tmp_path,
        multiframe_xyz,
        refinement,
        shermo_return=dict(_SHERMO_OK),
        preset="censo-default",
    )
    assert result.status == "completed"
    assert mock_censo.call_args.kwargs["context"].capability_extras["preset"] == "censo-default"
    # Same-level freq + Shermo for every survivor
    assert mock_freq.call_count == 2
    assert mock_shermo.call_count == 2
    mock_opt.assert_not_called()


def test_opt_failure_fails_workflow(tmp_path: Path, multiframe_xyz: Path) -> None:
    failed_opt = _task_result(
        TaskKind.OPTIMIZE,
        status="failed",
        errors=("SCF did not converge",),
    )

    result, _, _, _, _, _ = _run_energy(
        tmp_path,
        multiframe_xyz,
        _screening_result(),
        opt_result=failed_opt,
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
        input_xyz,
        rcfile,
        preset,
        nproc=4,
        temperature=298.15,
        solvent=None,
        nconf=1,
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
        input_xyz,
        rcfile,
        preset,
        nproc=4,
        temperature=298.15,
        solvent=None,
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
            input_xyz,
            tmp_path / "censo",
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
            input_xyz,
            censo_dir,
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
