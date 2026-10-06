"""Tests for the conformer energy workflow (acp run energy)."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np
import pytest

from acp.backends.censo_backend import CensoConformerRecord, CensoRunResult
from acp.core.models import HARTREE_TO_KCAL
from acp.workflows.ensemble_thermo import ensemble_total_gibbs as ensemble_total_gibbs_import
from cccp.calculation.contracts import ArtifactRef
from cccp.calculation.requests import TaskKind
from cccp.calculation.results import ConformerSearchPayload, TaskResult
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
            "shermo": {"path": "Shermo"},
        },
        "resources": {"nproc": 4},
        "censo": {"preset": "censo-light", "temperature": 298.15},
        "thermo": {
            "temperature_k": 298.15,
            "pressure_atm": 1.0,
            "scl_zpe": 0.9905,
            "shermo_ilowfreq": 2,
            "shermo_imagreal": 0,
            "shermo_conc": 1.0,
        },
    }


def _make_record(conf_id: str, frame_index: int, gtot: float) -> CensoConformerRecord:
    return CensoConformerRecord(
        conf_id=conf_id,
        frame_index=frame_index,
        energy=gtot + 0.08,
        gsolv=-0.004,
        grrho=-0.076,
        gtot=gtot,
        coordinates=np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 1.089], [1.027, 0.0, -0.363]]),
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
    the typed task result, so the ``CensoRunResult`` reconstruction is
    exercised end to end.
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


def test_energy_retired_replaced_by_confsearch_registry_entry() -> None:
    from acp.workflows.registry import get_workflow_entry

    assert get_workflow_entry("energy") is None
    confsearch = get_workflow_entry("Confsearch")
    assert confsearch is not None
    assert {"crest", "censo", "orca"} <= set(confsearch.requires_binaries)


def test_energy_retired_from_supported_workflows() -> None:
    from acp.scheduler.jobs import SUPPORTED_WORKFLOWS

    assert "energy" not in SUPPORTED_WORKFLOWS
    assert "Confsearch" in SUPPORTED_WORKFLOWS


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
    """energy 已退役：CLI 拒绝新建（rc=2），rank1/full-ensemble 语义迁移到 Confsearch。"""
    from acp.cli import main

    with (
        patch("acp.cli._build_config", return_value={}),
        patch("acp.workflows.energy.run_conformer_energy") as m,
    ):
        rc = main(["run", "energy", "--input", "CCO", "--output", str(tmp_path / "a")])
        assert rc == 2
        rc2 = main(
            [
                "run",
                "energy",
                "--input",
                "CCO",
                "--output",
                str(tmp_path / "b"),
                "--full-ensemble",
            ]
        )
        assert rc2 == 2
    assert m.call_count == 0


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

    rc = main(
        [
            "run",
            "energy",
            "--input",
            "CCO",
            "--output",
            str(tmp_path),
            "--levels",
            "{not json",
        ]
    )
    assert rc == 2


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

    with (
        patch(
            "acp.workflows.energy_shared.run_censo_refine",
            side_effect=_fake_censo_refine(mock_screening_result),
        ) as mock_censo,
        patch(
            "acp.workflows.energy_shared.run_optimize", return_value=_mock_opt_result()
        ) as mock_opt,
        patch(
            "acp.workflows.energy_shared.run_frequency", return_value=_mock_freq_result()
        ) as mock_freq,
        patch(
            "acp.workflows.energy_shared.run_singlepoint", return_value=_mock_sp_result()
        ) as mock_sp,
        patch(
            "acp.workflows.energy_shared.run_thermochemistry",
            return_value=_mock_shermo_result(dict(_SHERMO_OK)),
        ) as mock_shermo,
    ):
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
    extras = mock_censo.call_args.kwargs["context"].capability_extras
    assert extras["preset"] == "censo-light"
    assert extras.get("include_refinement", False) is False

    # ACP handoff: opt → freq → SP → Shermo, one call per selected conformer
    assert mock_opt.call_count == 2
    assert mock_freq.call_count == 2
    assert mock_sp.call_count == 2
    assert mock_shermo.call_count == 2

    # Shermo consumed the SP energy
    assert mock_shermo.call_args.args[0].options.sp_energy_hartree == pytest.approx(-155.001234)

    # finalDFT products (2 frames / 2 rows + TOTAL row) + global min + screening ranking
    mol_dir = tmp_path / "out" / "input"
    assert (mol_dir / "RESULT" / "structures" / "all_conformers.xyz").exists()
    thermo_csv = mol_dir / "RESULT" / "energies" / "conformer_thermo.csv"
    assert thermo_csv.exists()
    lines = thermo_csv.read_text().strip().splitlines()
    assert lines[0].startswith("index,rank,energy_hartree,gibbs_correction,gibbs_hartree")
    assert len(lines) == 4  # header + both ensemble members + TOTAL row
    assert lines[-1].startswith("TOTAL,")
    assert (mol_dir / "RESULT" / "structures" / "input_global_min.xyz").exists()
    assert (mol_dir / "RESULT" / "reports" / "screening_ranking.csv").exists()
    # ensemble total Gibbs: both mock candidates share the same Shermo Gibbs
    # → equal weights 0.5/0.5 → G_total = G1 + kT·ln 0.5
    thermo_json = mol_dir / "RESULT" / "energies" / "ensemble_thermo.json"
    assert thermo_json.exists()
    summary = json.loads(thermo_json.read_text())
    assert summary["method"] == "dft_table"
    assert summary["rank1_weight"] == pytest.approx(0.5)
    g1 = _SHERMO_OK["g_conc"] if _SHERMO_OK["g_conc"] is not None else _SHERMO_OK["g_sum"]
    expected_total = (g1 + 3.166811563e-6 * 298.15 * math.log(0.5)) * HARTREE_TO_KCAL
    assert result.metadata["total_gibbs_kcal_mol"] == pytest.approx(expected_total, abs=1e-6)
    assert summary["total_gibbs_kcal_mol"] == pytest.approx(expected_total, abs=1e-6)
    assert summary["population_coverage"] == pytest.approx(1.0)

    # Opt task request carries the default opt functional
    assert mock_opt.call_args.args[0].level.method == "r2SCAN-3c"


def test_rank1_handoff_passes_configured_shermo_bin(
    tmp_path: Path,
    sample_config: dict[str, Any],
    mock_screening_result: CensoRunResult,
    multiframe_xyz: Path,
) -> None:
    """Regression: run_rank1_handoff must resolve executables.shermo.path.

    The rank1/cumulative handoff calls ``run_thermochemistry`` directly;
    without this wiring it fell back to the bare name ``"Shermo"`` and
    failed in stripped service environments even when ``~/.cccp.yaml``
    configured an absolute Shermo path.
    """
    from acp.workflows.energy import run_conformer_energy

    sample_config["executables"]["shermo"]["path"] = "/opt/shermo/Shermo"

    with (
        patch(
            "acp.workflows.energy_shared.run_censo_refine",
            side_effect=_fake_censo_refine(mock_screening_result),
        ),
        patch("acp.workflows.energy_shared.run_optimize", return_value=_mock_opt_result()),
        patch("acp.workflows.energy_shared.run_frequency", return_value=_mock_freq_result()),
        patch("acp.workflows.energy_shared.run_singlepoint", return_value=_mock_sp_result()),
        patch(
            "acp.workflows.energy_shared.run_thermochemistry",
            return_value=_mock_shermo_result(dict(_SHERMO_OK)),
        ) as mock_shermo,
    ):
        result = run_conformer_energy(
            input_source=str(multiframe_xyz),
            output_dir=str(tmp_path / "out"),
            preset="censo-light",
            config=sample_config,
        )

    assert result.status == "completed"
    assert mock_shermo.call_count >= 1
    assert mock_shermo.call_args.kwargs["context"].capability_extras["shermo_bin"] == (
        "/opt/shermo/Shermo"
    )


def test_energy_light_opt_on_rank1_is_lowest_gtot(
    tmp_path: Path,
    sample_config: dict[str, Any],
    mock_screening_result: CensoRunResult,
    multiframe_xyz: Path,
) -> None:
    """The lowest-gtot record (CONF1) must lead the selected ensemble."""
    from acp.workflows.energy import run_conformer_energy

    with (
        patch(
            "acp.workflows.energy_shared.run_censo_refine",
            side_effect=_fake_censo_refine(mock_screening_result),
        ),
        patch("acp.workflows.energy_shared.run_optimize", return_value=_mock_opt_result()),
        patch("acp.workflows.energy_shared.run_frequency", return_value=_mock_freq_result()),
        patch("acp.workflows.energy_shared.run_singlepoint", return_value=_mock_sp_result()),
        patch(
            "acp.workflows.energy_shared.run_thermochemistry",
            return_value=_mock_shermo_result(dict(_SHERMO_OK)),
        ),
    ):
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

    with (
        patch(
            "acp.workflows.energy_shared.run_censo_refine",
            side_effect=_fake_censo_refine(mock_refinement_result),
        ) as mock_censo,
        patch("acp.workflows.energy_shared.run_optimize") as mock_opt,
        patch("acp.workflows.energy_shared.run_frequency") as mock_freq,
        patch("acp.workflows.energy_shared.run_singlepoint") as mock_sp,
        patch("acp.workflows.energy_shared.run_thermochemistry") as mock_shermo,
    ):
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
    extras = mock_censo.call_args.kwargs["context"].capability_extras
    assert extras["include_refinement"] is True
    assert extras.get("nconf") is None
    mock_opt.assert_not_called()
    mock_freq.assert_not_called()
    mock_sp.assert_not_called()
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

    with (
        patch("acp.workflows.energy_shared.run_censo_refine") as mock_censo,
        patch(
            "acp.workflows.energy_shared.run_optimize", return_value=_mock_opt_result()
        ) as mock_opt,
        patch(
            "acp.workflows.energy_shared.run_frequency", return_value=_mock_freq_result()
        ) as mock_freq,
        patch(
            "acp.workflows.energy_shared.run_singlepoint", return_value=_mock_sp_result()
        ) as mock_sp,
        patch(
            "acp.workflows.energy_shared.run_thermochemistry",
            return_value=_mock_shermo_result(dict(_SHERMO_OK)),
        ) as mock_shermo,
    ):
        result = run_conformer_energy(
            input_source=str(multiframe_xyz),
            output_dir=str(tmp_path / "out"),
            preset="censo-zero",
            config=sample_config,
        )

    assert result.status == "completed"
    mock_censo.assert_not_called()
    assert mock_opt.call_count == 1
    assert mock_freq.call_count == 1
    assert mock_sp.call_count == 1
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

    with (
        patch(
            "acp.workflows.energy_shared.run_censo_refine",
            side_effect=_fake_censo_refine(refinement),
        ) as mock_censo,
        patch("acp.workflows.energy_shared.run_optimize") as mock_opt,
        patch("acp.workflows.energy_shared.run_frequency") as mock_freq,
        patch("acp.workflows.energy_shared.run_singlepoint") as mock_sp,
        patch("acp.workflows.energy_shared.run_thermochemistry") as mock_shermo,
    ):
        result = run_conformer_energy(
            input_source=str(multiframe_xyz),
            output_dir=str(tmp_path / "out"),
            preset="censo-zero",
            config=sample_config,
            no_opt=True,
        )

    assert result.status == "completed"
    extras = mock_censo.call_args.kwargs["context"].capability_extras
    assert extras["preset"] == "censo-zero"
    assert extras["nconf"] == 1
    assert extras["include_refinement"] is False
    mock_opt.assert_not_called()
    mock_freq.assert_not_called()
    mock_sp.assert_not_called()
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

    shermo_values = [
        {"g_sum": -154.955, "g_conc": None, "h_sum": None, "u_sum": None, "s_total": None},
        {"g_sum": -154.951, "g_conc": None, "h_sum": None, "u_sum": None, "s_total": None},
    ]

    with (
        patch(
            "acp.workflows.energy_shared.run_censo_refine",
            side_effect=_fake_censo_refine(mock_refinement_result),
        ) as mock_censo,
        patch("acp.workflows.energy_shared.run_optimize") as mock_opt,
        patch(
            "acp.workflows.energy_shared.run_frequency", return_value=_mock_freq_result()
        ) as mock_freq,
        patch("acp.workflows.energy_shared.run_singlepoint") as mock_sp,
        patch(
            "acp.workflows.energy_shared.run_thermochemistry",
            side_effect=[_mock_shermo_result(values) for values in shermo_values],
        ) as mock_shermo,
    ):
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

    extras = mock_censo.call_args.kwargs["context"].capability_extras
    assert extras["preset"] == "censo-default"

    # Geometry already optimized by CENSO Part2: no ACP opt/SP, only freq+Shermo
    mock_opt.assert_not_called()
    mock_sp.assert_not_called()
    assert mock_freq.call_count == 2
    assert mock_shermo.call_count == 2

    # Shermo consumed the refinement SP energies from CENSO JSON
    sp_energies = sorted(
        call.args[0].options.sp_energy_hartree for call in mock_shermo.call_args_list
    )
    expected = sorted(r.energy for r in mock_refinement_result.records)
    assert sp_energies == pytest.approx(expected)

    # Multi-frame outputs
    mol_dir = tmp_path / "out" / "input"
    lines = (
        (mol_dir / "RESULT" / "energies" / "conformer_thermo.csv").read_text().strip().splitlines()
    )
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

    with (
        patch(
            "acp.workflows.energy_shared.run_censo_refine",
            side_effect=_fake_censo_refine(mock_refinement_result),
        ),
        patch("acp.workflows.energy_shared.run_optimize"),
        patch("acp.workflows.energy_shared.run_frequency", return_value=_mock_freq_result()),
        patch("acp.workflows.energy_shared.run_singlepoint"),
        patch(
            "acp.workflows.energy_shared.run_thermochemistry",
            return_value=_mock_shermo_result(None),
        ),
    ):
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
    assert "conf_id=CONF1" in xyz_content

    csv_content = Path(outputs["thermo_csv"]).read_text()
    header = csv_content.splitlines()[0]
    assert header == (
        "index,rank,energy_hartree,gibbs_correction,gibbs_hartree,"
        "h_correction,u_correction,s_total,g_conc,weight,source"
    )
    assert Path(outputs["global_min_xyz"]).name == "mol_global_min.xyz"
    assert not (tmp_path / "finalDFT").exists()

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

    # role contract: global_min carries it; all other products must not.
    summary = json.loads((tmp_path / "result_summary.json").read_text())
    assert summary["version"] == 1
    products = {p["path"]: p for p in summary["products"]}
    assert products["RESULT/structures/mol_global_min.xyz"]["role"] == "final_stable_structure"
    for path, prod in products.items():
        if path != "RESULT/structures/mol_global_min.xyz":
            assert "role" not in prod


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


def test_final_outputs_external_table_xyz_mirrors_boltzmann(tmp_path: Path) -> None:
    """Workflow 2 regression: all_conformers.xyz carries every table conformer 1:1."""
    from acp.workflows.energy import _write_final_outputs

    candidates = [
        {
            "index": 0,
            "coordinates": np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, 1.0, 0.0]]),
            "symbols": ["C", "H", "H"],
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
    records = [
        _make_record("CONF1", 0, -154.834525),
        _make_record("CONF2", 1, -154.834033),
    ]
    outputs = _write_final_outputs(
        candidates,
        tmp_path,
        "mol",
        298.15,
        external_weights=ext_weights,
        external_total_gibbs=ensemble_total_gibbs_import(-154.95, 0.8442, 298.15),
        screening_records=records,
    )

    lines = Path(outputs["all_conformers_xyz"]).read_text().strip().splitlines()
    assert len(lines) == 2 * 5
    assert "conf_id=CONF1" in lines[1] and "Weight=0.8442" in lines[1]
    assert "conf_id=CONF2" in lines[6] and "Weight=0.1558" in lines[6]

    thermo = json.loads(Path(outputs["ensemble_thermo_json"]).read_text())
    frame_ids = [lines[1].split("conf_id=")[1], lines[6].split("conf_id=")[1]]
    assert frame_ids == [row["conf_id"] for row in thermo["conformers"]]
    assert [row["rank"] for row in thermo["conformers"]] == [1, 2]


def test_final_outputs_renumbering_and_markers(tmp_path: Path) -> None:
    """DFT re-ranking renumbers xyz/CSV/table so all numbering agrees."""
    from acp.workflows.energy import _write_final_outputs

    def cand(index: int, source: str, energy: float, gibbs: float) -> dict[str, Any]:
        return {
            "index": index,
            "coordinates": np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 1.0], [index, 1.0, 0.0]]),
            "symbols": ["C", "H", "H"],
            "energy": energy,
            "gibbs": gibbs,
            "gibbs_correction": None,
            "h_correction": None,
            "u_correction": None,
            "s_total": None,
            "g_conc": None,
            "source": source,
        }

    # handoff order (screening gtot): CONF2 first — but DFT gibbs flips them.
    candidates = [
        cand(0, "CONF2", -154.999, -154.990),
        cand(1, "CONF1", -155.001, -154.995),
    ]
    outputs = _write_final_outputs(candidates, tmp_path, "mol", 298.15)

    lines = Path(outputs["all_conformers_xyz"]).read_text().strip().splitlines()
    assert "Conformer 0" in lines[1] and "Rank=1" in lines[1] and "conf_id=CONF1" in lines[1]
    assert "Conformer 1" in lines[6] and "Rank=2" in lines[6] and "conf_id=CONF2" in lines[6]

    thermo = json.loads(Path(outputs["ensemble_thermo_json"]).read_text())
    assert [(row["rank"], row["conf_id"]) for row in thermo["conformers"]] == [
        (1, "CONF1"),
        (2, "CONF2"),
    ]

    csv_lines = Path(outputs["thermo_csv"]).read_text().strip().splitlines()
    assert [line.split(",")[:3] for line in csv_lines[1:3]] == [
        ["0", "1", "-155.0010000000"],
        ["1", "2", "-154.9990000000"],
    ]
    assert csv_lines[1].endswith("CONF1") and csv_lines[2].endswith("CONF2")


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

    with (
        patch(
            "acp.workflows.energy_shared.run_censo_refine",
            side_effect=_fake_censo_refine(mock_screening_result),
        ),
        patch(
            "acp.workflows.energy_shared.run_optimize", return_value=_mock_opt_result()
        ) as mock_opt,
        patch(
            "acp.workflows.energy_shared.run_frequency", return_value=_mock_freq_result()
        ) as mock_freq,
        patch(
            "acp.workflows.energy_shared.run_singlepoint", return_value=_mock_sp_result()
        ) as mock_sp,
        patch(
            "acp.workflows.energy_shared.run_thermochemistry",
            return_value=_mock_shermo_result(dict(_SHERMO_OK)),
        ) as mock_shermo,
    ):
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
    assert mock_opt.call_count == 1
    assert mock_freq.call_count == 1
    assert mock_sp.call_count == 1
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
    summary = json.loads((mol_dir / "RESULT" / "energies" / "ensemble_thermo.json").read_text())
    assert summary["method"] == "censo_table_rank1"
    assert summary["rank1_weight"] == pytest.approx(p1, abs=1e-6)
    assert summary["total_gibbs_hartree"] == pytest.approx(result.metadata["total_gibbs_hartree"])
    assert summary["population_coverage"] == pytest.approx(1.0)
    assert len(summary["conformers"]) == 2  # full CENSO table preserved

    bt = json.loads((mol_dir / "RESULT" / "ensembles" / "boltzmann_table.json").read_text())
    assert bt["source"] == "censo"
    assert set(bt["weights"]) == {"CONF1", "CONF2"}

    # all_conformers.xyz mirrors the Boltzmann table: one frame per screened
    # conformer (CONF1 fine DFT geometry, CONF2 screening geometry), table order.
    xyz_lines = (
        (mol_dir / "RESULT" / "structures" / "all_conformers.xyz").read_text().strip().splitlines()
    )
    assert len(xyz_lines) == 2 * 5  # 2 frames × (count + comment + 3 atoms)
    assert "conf_id=CONF1" in xyz_lines[1]
    assert "conf_id=CONF2" in xyz_lines[6]
    cen_weights = mock_screening_result.boltzmann_weights()
    assert f"Rank=1, Weight={cen_weights['CONF1']:.4f}" in xyz_lines[1]
    assert f"Rank=2, Weight={cen_weights['CONF2']:.4f}" in xyz_lines[6]
    assert [row["conf_id"] for row in summary["conformers"]] == ["CONF1", "CONF2"]

    # thermo CSV stays candidate-centric: header + 1 conformer + TOTAL row
    csv_lines = (
        (mol_dir / "RESULT" / "energies" / "conformer_thermo.csv").read_text().strip().splitlines()
    )
    assert len(csv_lines) == 3
    # single-conformer DFT table → weight 1.0
    assert result.ensemble.records[0].weight == pytest.approx(1.0)


def test_energy_rank1_xyz_mirrors_boltzmann_table(
    tmp_path: Path,
    sample_config: dict[str, Any],
    mock_screening_result: CensoRunResult,
    multiframe_xyz: Path,
) -> None:
    """Regression: rank1-only all_conformers.xyz lists every Boltzmann-table conformer.

    Frames and ``ensemble_thermo.json`` rows must agree on conf_id order,
    rank and weight (CONF1 carries the fine DFT geometry, CONF2 the
    screening geometry).
    """
    from acp.workflows.energy import run_conformer_energy

    with (
        patch(
            "acp.workflows.energy_shared.run_censo_refine",
            side_effect=_fake_censo_refine(mock_screening_result),
        ),
        patch("acp.workflows.energy_shared.run_optimize", return_value=_mock_opt_result()),
        patch("acp.workflows.energy_shared.run_frequency", return_value=_mock_freq_result()),
        patch("acp.workflows.energy_shared.run_singlepoint", return_value=_mock_sp_result()),
        patch(
            "acp.workflows.energy_shared.run_thermochemistry",
            return_value=_mock_shermo_result(dict(_SHERMO_OK)),
        ),
    ):
        result = run_conformer_energy(
            input_source=str(multiframe_xyz),
            output_dir=str(tmp_path / "out"),
            preset="censo-light",
            config=sample_config,
            rank1_only=True,
        )

    assert result.status == "completed"
    assert result.metadata["refined_conf_ids"] == ["CONF1"]
    mol_dir = tmp_path / "out" / "input"
    # high-accuracy DFT artifacts live in the conf_id-named stage dirs
    assert (mol_dir / "WORK" / "03_OPT" / "ORCA" / "CONF1").is_dir()
    assert (mol_dir / "WORK" / "04_FREQ" / "ORCA" / "CONF1").is_dir()
    assert not (mol_dir / "WORK" / "03_OPT" / "ORCA" / "conf_000").exists()
    xyz_text = (mol_dir / "RESULT" / "structures" / "all_conformers.xyz").read_text()
    summary = json.loads((mol_dir / "RESULT" / "energies" / "ensemble_thermo.json").read_text())
    table_rows = summary["conformers"]
    frame_lines = [line for line in xyz_text.splitlines() if "conf_id=" in line]
    assert [line.split("conf_id=")[1].strip() for line in frame_lines] == [
        row["conf_id"] for row in table_rows
    ]
    for line, row in zip(frame_lines, table_rows):
        assert f"Rank={row['rank']}," in line
        assert f"Weight={row['weight']:.4f}" in line


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

    with (
        patch(
            "acp.workflows.energy_shared.run_censo_refine",
            side_effect=_fake_censo_refine(refined),
        ) as mock_censo,
        patch("acp.workflows.energy_shared.run_optimize") as mock_opt,
        patch("acp.workflows.energy_shared.run_frequency"),
        patch("acp.workflows.energy_shared.run_singlepoint"),
        patch("acp.workflows.energy_shared.run_thermochemistry") as mock_shermo,
    ):
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
    mock_opt.assert_not_called()
    mock_shermo.assert_not_called()

    # CENSO restricted to the rank1 frame
    extras = mock_censo.call_args.kwargs["context"].capability_extras
    assert extras["nconf"] == 1
    assert extras["include_refinement"] is True

    # p₁ from the xTB table: ΔE = 2 kcal/mol → p1 = 1/(1+exp(-2/0.5925))
    p1 = 1.0 / (1.0 + math.exp(-2.0 / (3.166811563e-6 * 298.15 * HARTREE_TO_KCAL)))
    gtot1 = refined.records[0].gtot
    expected_total = (gtot1 + 3.166811563e-6 * 298.15 * math.log(p1)) * HARTREE_TO_KCAL
    assert result.metadata["total_gibbs_kcal_mol"] == pytest.approx(expected_total, abs=1e-6)

    mol_dir = tmp_path / "out" / "close"
    summary = json.loads((mol_dir / "RESULT" / "energies" / "ensemble_thermo.json").read_text())
    assert summary["method"] == "xtb_table_rank1"
    assert summary["rank1_weight"] == pytest.approx(p1, abs=1e-6)
    bt = json.loads((mol_dir / "RESULT" / "ensembles" / "boltzmann_table.json").read_text())
    assert bt["source"] == "xtb"
    assert set(bt["weights"]) == {"CONF1", "CONF2"}

    xyz_lines = (
        (mol_dir / "RESULT" / "structures" / "all_conformers.xyz").read_text().strip().splitlines()
    )
    assert len(xyz_lines) == 2 * 5
    assert "conf_id=CONF1" in xyz_lines[1]
    assert "conf_id=CONF2" in xyz_lines[6]


# ---------------------------------------------------------------------------
# Scheduler task-dir flattening (v2): job.json + task.json → flat layout
# ---------------------------------------------------------------------------


def test_energy_scheduler_task_dir_writes_flat(
    tmp_path: Path,
    sample_config: dict[str, Any],
    mock_screening_result: CensoRunResult,
    multiframe_xyz: Path,
) -> None:
    """Inside a scheduler task dir (job.json + task.json) the workflow must
    write directly at the task root — no ``{safe_name}`` subdir."""
    from acp.workflows.energy import run_conformer_energy

    out = tmp_path / "task_root"
    out.mkdir()
    (out / "job.json").write_text("placeholder")
    (out / "task.json").write_text("placeholder")

    with (
        patch(
            "acp.workflows.energy_shared.run_censo_refine",
            side_effect=_fake_censo_refine(mock_screening_result),
        ),
        patch("acp.workflows.energy_shared.run_optimize", return_value=_mock_opt_result()),
        patch("acp.workflows.energy_shared.run_frequency", return_value=_mock_freq_result()),
        patch("acp.workflows.energy_shared.run_singlepoint", return_value=_mock_sp_result()),
        patch(
            "acp.workflows.energy_shared.run_thermochemistry",
            return_value=_mock_shermo_result(dict(_SHERMO_OK)),
        ),
    ):
        result = run_conformer_energy(
            input_source=str(multiframe_xyz),
            output_dir=str(out),
            preset="censo-light",
            config=sample_config,
        )

    assert result.status == "completed"
    assert (out / "state.json").is_file()
    assert (out / "WORK" / "02_SEARCH").is_dir()
    assert (out / "RESULT" / "energies" / "ensemble_thermo.json").is_file()
    assert not (out / "input").exists()


def test_handoff_stage_dir_splits_freq_sp_thermo(tmp_path):
    from acp.workflows.energy_shared import _handoff_stage_dir

    conf_dir = tmp_path / "WORK" / "03_OPT" / "ORCA" / "conf_000"
    conf_dir.mkdir(parents=True)

    freq_dir = _handoff_stage_dir(conf_dir, "04_FREQ", "ORCA")
    assert freq_dir == tmp_path / "WORK" / "04_FREQ" / "ORCA" / "conf_000"
    assert freq_dir.is_dir()

    sp_dir = _handoff_stage_dir(conf_dir, "05_SP", "ORCA")
    assert sp_dir == tmp_path / "WORK" / "05_SP" / "ORCA" / "conf_000"

    thermo_dir = _handoff_stage_dir(conf_dir, "06_THERMO", "Shermo")
    assert thermo_dir == tmp_path / "WORK" / "06_THERMO" / "Shermo" / "conf_000"

    bare = tmp_path / "plain_conf"
    bare.mkdir()
    assert _handoff_stage_dir(bare, "04_FREQ", "ORCA") == bare


# ---------------------------------------------------------------------------
# cccp task-core adapters (todo 26a)
# ---------------------------------------------------------------------------


def test_part_template_tokens_round_trip() -> None:
    from acp.workflows.energy_shared import _part_template_tokens
    from cccp.qc.translation import render_censo_template_lines

    templates = {"screening": render_censo_template_lines(["PBE0", "def2-SVP", "EnGrad"])}
    tokens = _part_template_tokens(templates)
    assert tokens == {"screening": ["PBE0", "def2-SVP", "EnGrad"]}
    assert render_censo_template_lines(tokens["screening"]) == templates["screening"]


def test_crest_search_via_task_returns_ensemble_and_raises_on_failure(tmp_path: Path) -> None:
    from acp.workflows.energy_shared import crest_search_via_task

    input_xyz = tmp_path / "in.xyz"
    input_xyz.write_text("3\n\nC 0 0 0\nH 0 0 1\nH 1 0 0\n")
    ensemble_ref = ArtifactRef(path=tmp_path / "crest_conformers.xyz", type="ensemble")
    ok = TaskResult(
        task=TaskKind.CONFORMER_SEARCH,
        status="completed",
        complete=True,
        artifacts=(ensemble_ref,),
        payload=ConformerSearchPayload(ensemble_ref=ensemble_ref, conformer_count=1),
    )
    with patch("acp.workflows.energy_shared.run_conformer_search", return_value=ok) as mock_search:
        out = crest_search_via_task(
            {},
            input_xyz,
            tmp_path / "crest",
            charge=0,
            multiplicity=1,
            energy_window=6.0,
            output_name="mol",
            gfn_level=2,
        )
    assert out == tmp_path / "crest_conformers.xyz"
    request = mock_search.call_args.args[0]
    assert request.options.energy_window == pytest.approx(6.0)
    assert mock_search.call_args.kwargs["context"].capability_extras == {"output_name": "mol"}

    failed = TaskResult(
        task=TaskKind.CONFORMER_SEARCH,
        status="failed",
        complete=False,
        errors=("CREST output not found",),
    )
    with patch("acp.workflows.energy_shared.run_conformer_search", return_value=failed):
        with pytest.raises(RuntimeError, match="CREST search failed: CREST output not found"):
            crest_search_via_task(
                {},
                input_xyz,
                tmp_path / "crest",
                charge=0,
                multiplicity=1,
                energy_window=6.0,
                output_name="mol",
            )


def test_shermo_via_task_maps_metadata_and_returns_none_on_failure(tmp_path: Path) -> None:
    from acp.workflows.energy_shared import shermo_via_task

    with patch(
        "acp.workflows.energy_shared.run_thermochemistry",
        return_value=_mock_shermo_result(dict(_SHERMO_OK)),
    ) as mock_thermo:
        values = shermo_via_task(
            {},
            freq_log="/tmp/freq.out",
            sp_energy=-155.001234,
            thermo_dir=tmp_path,
            output_file=str(tmp_path / "Shermo.sum"),
            shermo_bin="/opt/shermo/Shermo",
            temperature_k=298.15,
            pressure_atm=1.0,
            scl_zpe=0.9905,
            ilowfreq=2,
            imagreal=0,
            conc=None,
        )
    assert values == dict(_SHERMO_OK)
    request = mock_thermo.call_args.args[0]
    assert request.options.sp_energy_hartree == pytest.approx(-155.001234)
    assert request.options.freq_log_path == Path("/tmp/freq.out")
    assert mock_thermo.call_args.kwargs["context"].capability_extras == {
        "output_file": str(tmp_path / "Shermo.sum"),
        "shermo_bin": "/opt/shermo/Shermo",
    }

    with patch(
        "acp.workflows.energy_shared.run_thermochemistry",
        return_value=_mock_shermo_result(None),
    ):
        assert (
            shermo_via_task(
                {},
                freq_log="/tmp/freq.out",
                sp_energy=-1.0,
                thermo_dir=tmp_path,
                output_file=str(tmp_path / "Shermo.sum"),
                shermo_bin="Shermo",
                temperature_k=298.15,
                pressure_atm=1.0,
                scl_zpe=1.0,
                ilowfreq=0,
                imagreal=0,
                conc=None,
            )
            is None
        )


# ---------------------------------------------------------------------------
# T08b — stage-split solvent models on the shared Confsearch engines
# ---------------------------------------------------------------------------


def _single_frame_xyz(path: Path) -> Path:
    path.write_text("3\nSingle\nC 0 0 0\nH 0 0 1.089\nH 1.027 0 -0.363\n", encoding="utf-8")
    return path


def _crest_backend_solvent_args(solvent: str | None, solvent_model: str) -> list[str]:
    """Legality proof at the CREST interface level (not just no-raise)."""
    from cccp.backends.crest import CrestBackend

    backend = CrestBackend(
        config={"executables": {"crest": {"path": "crest"}}},
        gfn_level=2,
        solvent=solvent,
        solvent_model=solvent_model,
    )
    return backend._interface._solvent_args()


def _run_energy_solvent_case(
    tmp_path: Path,
    sample_config: dict[str, Any],
    mock_screening_result: CensoRunResult,
    *,
    config: dict[str, Any],
    solvent: str | None = None,
    levels: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], Any]:
    from acp.workflows.energy import run_conformer_energy

    input_xyz = _single_frame_xyz(tmp_path / "single.xyz")
    crest_calls: list[dict[str, Any]] = []

    def fake_crest(cfg: Any, xyz: Path, out_dir: Path, **kwargs: Any) -> Path:
        crest_calls.append(dict(kwargs))
        return xyz

    with (
        patch("acp.workflows.energy._crest_search_via_task", side_effect=fake_crest),
        patch(
            "acp.workflows.energy_shared.run_censo_refine",
            side_effect=_fake_censo_refine(mock_screening_result),
        ) as mock_censo,
        patch("acp.workflows.energy_shared.run_optimize", return_value=_mock_opt_result()),
        patch("acp.workflows.energy_shared.run_frequency", return_value=_mock_freq_result()),
        patch("acp.workflows.energy_shared.run_singlepoint", return_value=_mock_sp_result()),
        patch(
            "acp.workflows.energy_shared.run_thermochemistry",
            return_value=_mock_shermo_result(dict(_SHERMO_OK)),
        ),
    ):
        result = run_conformer_energy(
            input_source=str(input_xyz),
            output_dir=str(tmp_path / "out"),
            preset="censo-light",
            config=config,
            solvent=solvent,
            levels=levels,
        )
    assert result.status == "completed", result.error
    assert crest_calls, "CREST must run for a single-frame input"
    return crest_calls, mock_censo


def test_energy_crest_never_receives_dft_model(
    tmp_path: Path,
    sample_config: dict[str, Any],
    mock_screening_result: CensoRunResult,
) -> None:
    """A configured DFT model reaches CENSO only; CREST gets ALPB."""
    config = {**sample_config, "theory": {"preoptimization": {"solvent_model": "cpcm"}}}
    crest_calls, mock_censo = _run_energy_solvent_case(
        tmp_path,
        sample_config,
        mock_screening_result,
        config=config,
        solvent="water",
    )
    assert crest_calls[0]["solvent"] == "water"
    assert crest_calls[0]["solvent_model"] == "alpb"
    assert _crest_backend_solvent_args("water", "alpb") == ["--alpb", "water"]
    extras = mock_censo.call_args.kwargs["context"].capability_extras
    assert extras["solvent"] == "water"
    assert extras["solvent_model"] == "cpcm"


def test_energy_levels_smd_override_keeps_crest_legal(
    tmp_path: Path,
    sample_config: dict[str, Any],
    mock_screening_result: CensoRunResult,
) -> None:
    """The wizard levels path (SMD on the refinement SP level) is the
    Confsearch rank1/cumulative crash path: CREST must still get a legal
    sampling model while CENSO keeps the levels DFT model."""
    crest_calls, mock_censo = _run_energy_solvent_case(
        tmp_path,
        sample_config,
        mock_screening_result,
        config=sample_config,
        levels={"refinement_sp": {"solvent_model": "SMD", "solvent": "water"}},
    )
    assert crest_calls[0]["solvent"] == "water"
    assert crest_calls[0]["solvent_model"] == "alpb"
    extras = mock_censo.call_args.kwargs["context"].capability_extras
    assert extras["solvent"] == "water"
    assert extras["solvent_model"] == "smd"


def test_energy_unknown_sampling_model_fails_strictly(
    tmp_path: Path,
    sample_config: dict[str, Any],
    mock_screening_result: CensoRunResult,
) -> None:
    """Malformed dedicated-key value → the run fails before any QC."""
    from acp.workflows.energy import run_conformer_energy

    input_xyz = _single_frame_xyz(tmp_path / "single.xyz")
    config = {**sample_config, "nmr": {"sampling_solvent_model": "mdm"}}
    with patch("acp.workflows.energy._crest_search_via_task") as mock_crest:
        result = run_conformer_energy(
            input_source=str(input_xyz),
            output_dir=str(tmp_path / "out"),
            preset="censo-light",
            config=config,
            solvent="water",
        )

    assert result.status == "failed"
    assert "sampling_solvent_model" in (result.error or "")
    mock_crest.assert_not_called()
