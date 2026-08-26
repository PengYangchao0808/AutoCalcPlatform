"""Regression tests for ORCA TS/IRC helper extensions."""

# pyright: reportAny=false, reportExplicitAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnusedCallResult=false

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from cccp.qc.interfaces.constraints import (
    AngleConstraint,
    CoordinateSpec,
    DihedralConstraint,
    DistanceConstraint,
    orca_constraint_block,
)
from cccp.qc.interfaces.orca import ORCAInterface
from cccp.qc.interfaces.orca_ts import irc_block, ts_geom_block, ts_opt_route

TS_COORDINATES = np.array(
    [
        [0.0, 0.0, 0.0],
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
    ]
)
TS_SYMBOLS = ["H", "C", "H"]
DISPLACEMENT_MODE = np.array(
    [
        [1.0, 0.0, 0.0],
        [0.0, 0.0, 0.0],
        [0.0, 0.0, 0.0],
    ]
)
PARSED_MODE_VECTOR = np.array(
    [
        [-0.1, 0.0, 0.1],
        [0.2, -0.3, 0.4],
        [-0.5, 0.6, -0.7],
    ]
)

TS_OUTPUT = """FINAL SINGLE POINT ENERGY      -150.123456
CARTESIAN COORDINATES (ANGSTROEM)
-------------------
H      0.0000000000    0.0000000000    0.0000000000
C      1.0000000000    0.0000000000    0.0000000000
H      0.0000000000    1.0000000000    0.0000000000
-------------------

------------
NORMAL MODES
------------

These modes are the Cartesian displacements weighted by the diagonal matrix
M(i,i)=1/sqrt(m[i]) where m[i] is the mass of the displaced atom

                  0          1          2          3          4          5
      0       0.000000   0.000000   0.000000   0.000000   0.000000   0.000000
      1       0.000000   0.000000   0.000000   0.000000   0.000000   0.000000
      2       0.000000   0.000000   0.000000   0.000000   0.000000   0.000000
      3       0.000000   0.000000   0.000000   0.000000   0.000000   0.000000
      4       0.000000   0.000000   0.000000   0.000000   0.000000   0.000000
      5       0.000000   0.000000   0.000000   0.000000   0.000000   0.000000
      6       0.000000   0.000000   0.000000   0.000000   0.000000   0.000000
      7       0.000000   0.000000   0.000000   0.000000   0.000000   0.000000
      8       0.000000   0.000000   0.000000   0.000000   0.000000   0.000000

                  6          7          8
      0      -0.100000   0.010000   0.020000
      1       0.000000   0.011000   0.021000
      2       0.100000   0.012000   0.022000
      3       0.200000   0.013000   0.023000
      4      -0.300000   0.014000   0.024000
      5       0.400000   0.015000   0.025000
      6      -0.500000   0.016000   0.026000
      7       0.600000   0.017000   0.027000
      8      -0.700000   0.018000   0.028000

VIBRATIONAL FREQUENCIES
-----------------------

Scaling factor for frequencies =  1.000000000  (already applied!)

   0:         0.00 cm**-1
   1:         0.00 cm**-1
   2:         0.00 cm**-1
   3:         0.00 cm**-1
   4:         0.00 cm**-1
   5:         0.00 cm**-1
   6:      -500.00 cm**-1 ***imaginary mode***
   7:       120.00 cm**-1
   8:       250.00 cm**-1

****ORCA-CHEMISTRY JOB DONE****
"""

IRC_OUTPUT = """IRC forward direction
IRC reverse direction
****ORCA-CHEMISTRY JOB DONE****
"""

SCAN_OUTPUT = """RELAXED SURFACE SCAN STEP 1
CARTESIAN COORDINATES (ANGSTROEM)
-------------------
H      0.0000000000    0.0000000000    0.0000000000
C      1.5000000000    0.0000000000    0.0000000000
H      0.0000000000    1.0000000000    0.0000000000
-------------------

RELAXED SURFACE SCAN STEP 2
CARTESIAN COORDINATES (ANGSTROEM)
-------------------
H      0.0000000000    0.0000000000    0.0000000000
C      2.4500000000    0.0000000000    0.0000000000
H      0.0000000000    1.0000000000    0.0000000000
-------------------

RELAXED SURFACE SCAN STEP 3
CARTESIAN COORDINATES (ANGSTROEM)
-------------------
H      0.0000000000    0.0000000000    0.0000000000
C      3.4000000000    0.0000000000    0.0000000000
H      0.0000000000    1.0000000000    0.0000000000
-------------------

The Calculated Surface using the RELAXED SURFACE SCAN
-----------------------------------------------------
  1    1.50000000   -100.00000000
  2    2.45000000    -99.95000000
  3    3.40000000    -99.90000000

****ORCA-CHEMISTRY JOB DONE****
"""

CONSTRAINED_OPT_OUTPUT = """FINAL SINGLE POINT ENERGY      -175.432100
CARTESIAN COORDINATES (ANGSTROEM)
-------------------
H      0.0000000000    0.0000000000    0.0000000000
C      1.5000000000    0.0000000000    0.0000000000
H      0.0000000000    1.0000000000    0.0000000000
-------------------
"""


def _extract_orca_input_coordinates(input_file: Path) -> NDArray[np.float64]:
    lines = input_file.read_text(encoding="utf-8").splitlines()
    start = lines.index(next(line for line in lines if line.startswith("* xyz "))) + 1
    end = lines.index("*", start)
    coords: list[list[float]] = []
    for line in lines[start:end]:
        parts = line.split()
        coords.append([float(parts[1]), float(parts[2]), float(parts[3])])
    return np.asarray(coords, dtype=np.float64)


def test_ts_block_builders_render_rescue_keywords() -> None:
    route = ts_opt_route("B3LYP", "def2-SVP", opt_level="tight")
    geom = ts_geom_block(ts_mode=3)

    assert "OptTS TightOpt" in route
    assert "TS_Mode {M 3} end" in geom


def test_ts_geom_block_renders_trust_keyword_and_optional_maxiter() -> None:
    default_geom = ts_geom_block()
    maxiter_geom = ts_geom_block(max_iter=60)
    model_hess_geom = ts_geom_block(initial_hessian="model")

    assert "Trust 0.15" in default_geom
    assert "TrustRadius" not in default_geom
    assert "MaxIter" not in default_geom
    assert "MaxIter 60" in maxiter_geom
    assert "Calc_Hess true" in default_geom
    assert "Calc_Hess" not in model_hess_geom


def test_ts_opt_route_normal_level_does_not_duplicate_opt_keyword() -> None:
    route = ts_opt_route("B3LYP", "def2-SVP", opt_level="normal")

    assert "OptTS" in route
    assert " TightOpt" not in route
    assert route.count("OptTS") == 1


def test_irc_block_is_conditional_for_hessian_and_midpoint_restart() -> None:
    with_hessian = irc_block("both", 25, hess_file_name="irc_case.hess")
    midpoint = irc_block("forward", 10, irc_midpoint_reseed=True)

    assert 'Hess_Filename "irc_case.hess"' in with_hessian
    assert "InitHess Read" in with_hessian
    assert "Direction Down" in midpoint
    assert "InitHess Read" not in midpoint


def test_orca_constraint_block_renders_one_based_constraints() -> None:
    block = orca_constraint_block(
        [
            DistanceConstraint(atoms=(0, 1), target=1.5),
            AngleConstraint(atoms=(0, 1, 2), target=120.0),
            DihedralConstraint(atoms=(0, 1, 2, 3), target=-60.0),
        ]
    )

    assert block == (
        "Constraints\n"
        "  { B 1 2 C 1.50000000 }\n"
        "  { A 1 2 3 C 120.00000000 }\n"
        "  { D 1 2 3 4 C -60.00000000 }\n"
        "end"
    )


def test_relaxed_scan_writes_scants_input_and_parses_output(
    sample_config: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interface = ORCAInterface(sample_config)

    def _fake_run(_input_file: Path, output_file: Path) -> bool:
        _ = output_file.write_text(SCAN_OUTPUT, encoding="utf-8")
        return True

    monkeypatch.setattr(interface, "_run_orca", _fake_run)

    result = interface.relaxed_scan(
        TS_COORDINATES,
        TS_SYMBOLS,
        scan_coordinate=CoordinateSpec(
            id="rc1",
            kind="distance",
            atoms=(0, 1),
            start=1.5,
            end=3.4,
        ),
        points=3,
        output_dir=tmp_path,
        output_name="scan_case",
        solvent="acetone",
        solvent_model="ALPB",
    )

    input_text = (tmp_path / "scan_case.inp").read_text(encoding="utf-8")

    best_point = result.best_point()

    assert result.success is True
    assert len(result.points) == 3
    assert result.energies() == pytest.approx([-100.0, -99.95, -99.9])
    assert best_point is not None
    assert best_point.frame_index == 0
    assert result.points[2].coordinate_values["rc1"] == pytest.approx(3.4)
    assert result.points[1].coordinates is not None
    np.testing.assert_allclose(
        result.points[1].coordinates,
        np.array([[0.0, 0.0, 0.0], [2.45, 0.0, 0.0], [0.0, 1.0, 0.0]]),
    )
    assert "ScanTS" in input_text
    assert "ALPB(Acetone)" in input_text
    assert "%cpcm" not in input_text
    assert "B 0 1 = 1.50000000, 3.40000000, 3" in input_text


def test_constrained_optimize_writes_constraint_block_and_parses_output(
    sample_config: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interface = ORCAInterface(sample_config)

    def _fake_run(_input_file: Path, output_file: Path) -> bool:
        _ = output_file.write_text(CONSTRAINED_OPT_OUTPUT, encoding="utf-8")
        return True

    monkeypatch.setattr(interface, "_run_orca", _fake_run)

    result = interface.constrained_optimize(
        TS_COORDINATES,
        TS_SYMBOLS,
        constraints=[DistanceConstraint(atoms=(0, 1), target=1.5)],
        output_dir=tmp_path,
        output_name="warmup_case",
        method="B97-3c",
    )

    input_text = (tmp_path / "warmup_case.inp").read_text(encoding="utf-8")

    assert result.success is True
    assert result.energy == pytest.approx(-175.4321)
    assert result.coordinates is not None
    np.testing.assert_allclose(
        result.coordinates,
        np.array([[0.0, 0.0, 0.0], [1.5, 0.0, 0.0], [0.0, 1.0, 0.0]]),
    )
    assert "Constraints" in input_text
    assert "{ B 1 2 C 1.50000000 }" in input_text


def test_relaxed_scan_reports_missing_orca_binary(
    sample_config: dict[str, object],
    tmp_path: Path,
) -> None:
    interface = ORCAInterface(sample_config)
    interface.executable = None

    result = interface.relaxed_scan(
        TS_COORDINATES,
        TS_SYMBOLS,
        scan_coordinate=CoordinateSpec(
            id="rc1",
            kind="distance",
            atoms=(0, 1),
            start=1.5,
            end=3.4,
        ),
        points=3,
        output_dir=tmp_path,
    )

    assert result.success is False
    assert "ORCA executable not found" in result.message


def test_transition_state_opt_writes_rescue_kwargs_and_extracts_mode_vector(
    sample_config: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    interface = ORCAInterface(sample_config)

    def _fake_run(_input_file: Path, output_file: Path) -> bool:
        _ = output_file.write_text(TS_OUTPUT, encoding="utf-8")
        return True

    monkeypatch.setattr(interface, "_run_orca", _fake_run)

    with caplog.at_level("WARNING"):
        result = interface.transition_state_opt(
            TS_COORDINATES,
            TS_SYMBOLS,
            output_dir=tmp_path,
            output_name="ts_case",
            ts_mode=True,
            opt_level="tight",
            mode_displacement=0.3,
            mode_vector=DISPLACEMENT_MODE,
            mode_displacement_sign="minus",
            mystery_kw=1,
        )

    input_file = tmp_path / "ts_case.inp"
    input_text = input_file.read_text(encoding="utf-8")
    displaced_coords = _extract_orca_input_coordinates(input_file)

    assert result.success is True
    assert result.energy_hartree == pytest.approx(-150.123456)
    assert result.imaginary_frequencies == [-500.0]
    assert result.mode_vector is not None
    np.testing.assert_allclose(result.mode_vector, PARSED_MODE_VECTOR)
    assert "OptTS TightOpt" in input_text
    assert "TS_Mode {M 0} end" in input_text
    np.testing.assert_allclose(
        displaced_coords[0],
        np.array([-0.3, 0.0, 0.0], dtype=np.float64),
    )
    assert "Unused ORCA transition_state_opt kwargs" in caplog.text


def test_transition_state_opt_requires_mode_vector_for_displacement(
    sample_config: dict[str, object],
    tmp_path: Path,
) -> None:
    interface = ORCAInterface(sample_config)

    with pytest.raises(ValueError, match="mode_displacement requires a mode_vector"):
        _ = interface.transition_state_opt(
            TS_COORDINATES,
            TS_SYMBOLS,
            output_dir=tmp_path,
            mode_displacement=0.3,
        )


def test_irc_stages_hessian_and_populates_endpoint_geometries(
    sample_config: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interface = ORCAInterface(sample_config)
    hess_source = tmp_path / "source.hess"
    _ = hess_source.write_text("fake hessian", encoding="utf-8")

    def _fake_run(input_file: Path, output_file: Path) -> bool:
        output_file.write_text(IRC_OUTPUT, encoding="utf-8")
        _ = (input_file.parent / "irc_case_IRC_F.xyz").write_text(
            "3\nforward\nH 0.0 0.0 0.0\nC 1.1 0.0 0.0\nH 0.0 1.0 0.0\n",
            encoding="utf-8",
        )
        _ = (input_file.parent / "irc_case_IRC_B.xyz").write_text(
            "3\nreverse\nH 0.0 0.0 0.0\nC 0.9 0.0 0.0\nH 0.0 1.0 0.0\n",
            encoding="utf-8",
        )
        return True

    monkeypatch.setattr(interface, "_run_orca", _fake_run)

    result = interface.irc(
        TS_COORDINATES,
        TS_SYMBOLS,
        output_dir=tmp_path,
        output_name="irc_case",
        hess_file=hess_source,
    )

    input_text = (tmp_path / "irc_case.inp").read_text(encoding="utf-8")
    staged_hessian = tmp_path / "irc_case.hess"
    assert result.final_geometries

    assert result.success is True
    assert result.endpoints is not None
    assert set(result.endpoints) == {"forward", "reverse"}
    assert result.forward_points == 1
    assert result.reverse_points == 1
    assert 'Hess_Filename "irc_case.hess"' in input_text
    assert "InitHess Read" in input_text
    assert staged_hessian.read_text(encoding="utf-8") == "fake hessian"
    np.testing.assert_allclose(
        result.final_geometries["forward"],
        np.array([[0.0, 0.0, 0.0], [1.1, 0.0, 0.0], [0.0, 1.0, 0.0]]),
    )
    np.testing.assert_allclose(
        result.final_geometries["reverse"],
        np.array([[0.0, 0.0, 0.0], [0.9, 0.0, 0.0], [0.0, 1.0, 0.0]]),
    )


def test_irc_midpoint_reseed_changes_direction_and_skips_missing_hessian(
    sample_config: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    interface = ORCAInterface(sample_config)

    def _fake_run(_input_file: Path, output_file: Path) -> bool:
        _ = output_file.write_text(IRC_OUTPUT, encoding="utf-8")
        return True

    monkeypatch.setattr(interface, "_run_orca", _fake_run)

    with caplog.at_level("WARNING"):
        result = interface.irc(
            TS_COORDINATES,
            TS_SYMBOLS,
            output_dir=tmp_path,
            output_name="irc_midpoint",
            irc_midpoint_reseed=True,
            extra_kw=1,
        )

    input_text = (tmp_path / "irc_midpoint.inp").read_text(encoding="utf-8")

    assert result.success is True
    assert "Direction Down" in input_text
    assert "InitHess Read" not in input_text
    assert "Unused ORCA irc kwargs" in caplog.text


# ── relaxed scan output parsing (banner/geometry regression) ────────────


def _scan_coord_block(c_x: float) -> str:
    return (
        "CARTESIAN COORDINATES (ANGSTROEM)\n"
        "---------------------------------\n"
        "H      0.0000000000    0.0000000000    0.0000000000\n"
        f"C      {c_x:.7f}    0.0000000000    0.0000000000\n"
        "H      0.0000000000    1.0000000000    0.0000000000\n"
        "---------------------------------\n"
    )


def _decorated_scan_output() -> str:
    targets = [1.5, 2.45, 3.4]
    energies = [-100.0, -99.95, -99.9]
    parts: list[str] = []
    for step, (target, _energy) in enumerate(zip(targets, energies), start=1):
        parts.append(
            f"         *               RELAXED SURFACE SCAN STEP {step:>3}               *"
        )
        parts.append(_scan_coord_block(target - 0.35))
        parts.append(_scan_coord_block(target))
    parts.append("The Calculated Surface using the 'Actual Energy'")
    parts.append("-----------------------------------------------------")
    for index, (target, energy) in enumerate(zip(targets, energies), start=1):
        parts.append(f"  {index}  {target:.8f}  {energy:.8f}")
    parts.append("")
    parts.append("The Calculated Surface using the SCF energy")
    for index, target in enumerate(targets, start=1):
        parts.append(f"  {index}  {target:.8f}  0.00000000")
    parts.append(_scan_coord_block(9.99))
    parts.append("ORCA TERMINATED NORMALLY")
    return "\n".join(parts) + "\n"


def test_relaxed_scan_parses_decorated_banners_and_converged_cycles(
    sample_config: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interface = ORCAInterface(sample_config)

    def _fake_run(_input_file: Path, output_file: Path) -> bool:
        _ = output_file.write_text(_decorated_scan_output(), encoding="utf-8")
        return True

    monkeypatch.setattr(interface, "_run_orca", _fake_run)

    result = interface.relaxed_scan(
        TS_COORDINATES,
        TS_SYMBOLS,
        scan_coordinate=CoordinateSpec(id="rc1", kind="distance", atoms=(0, 1), start=1.5, end=3.4),
        points=3,
        output_dir=tmp_path,
        output_name="scan_case",
    )

    assert result.success is True
    assert len(result.points) == 3
    assert result.energies() == pytest.approx([-100.0, -99.95, -99.9])
    for point, expected_x in zip(result.points, [1.5, 2.45, 3.4]):
        assert point.coordinates is not None
        assert point.coordinates[1][0] == pytest.approx(expected_x)
    assert result.points[2].coordinates is not None
    assert result.points[2].coordinates[1][0] != pytest.approx(9.99)
    regenerated = (tmp_path / "scan_frames" / "frame_002.xyz").read_text(encoding="utf-8")
    assert "9.99" not in regenerated


def test_relaxed_scan_prefers_allxyz_trajectory(
    sample_config: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interface = ORCAInterface(sample_config)

    allxyz_targets = [1.55, 2.50, 3.45]
    allxyz_frames = []
    for target, energy in zip(allxyz_targets, [-100.0, -99.95, -99.9]):
        allxyz_frames.append(
            "3\n"
            f"Coordinates from ORCA-job scan Relaxed Surface Scan Step E {energy:.6f}\n"
            "H      0.000000      0.000000      0.000000\n"
            f"C      {target:.6f}      0.000000      0.000000\n"
            "H      0.000000      1.000000      0.000000\n"
        )

    def _fake_run(_input_file: Path, output_file: Path) -> bool:
        _ = output_file.write_text(_decorated_scan_output(), encoding="utf-8")
        (tmp_path / "scan_case.allxyz").write_text("".join(allxyz_frames), encoding="utf-8")
        return True

    monkeypatch.setattr(interface, "_run_orca", _fake_run)

    result = interface.relaxed_scan(
        TS_COORDINATES,
        TS_SYMBOLS,
        scan_coordinate=CoordinateSpec(id="rc1", kind="distance", atoms=(0, 1), start=1.5, end=3.4),
        points=3,
        output_dir=tmp_path,
        output_name="scan_case",
    )

    assert result.success is True
    for point, expected_x in zip(result.points, allxyz_targets):
        assert point.coordinates is not None
        assert point.coordinates[1][0] == pytest.approx(expected_x)


def test_parse_relaxed_scan_output_reparses_existing_log(
    sample_config: dict[str, object],
    tmp_path: Path,
) -> None:
    interface = ORCAInterface(sample_config)
    output_file = tmp_path / "scan_case.out"
    _ = output_file.write_text(_decorated_scan_output(), encoding="utf-8")

    result = interface.parse_relaxed_scan_output(
        output_file,
        scan_coordinate=CoordinateSpec(id="rc1", kind="distance", atoms=(0, 1), start=1.5, end=3.4),
        points=3,
        output_dir=tmp_path,
        output_name="scan_case",
    )

    assert result.success is True
    assert result.message == ""
    assert result.energies() == pytest.approx([-100.0, -99.95, -99.9])
    frame_001 = tmp_path / "scan_frames" / "frame_001.xyz"
    assert frame_001.is_file()
    frame_lines = frame_001.read_text(encoding="utf-8").splitlines()
    assert frame_lines[3].startswith("C")
    assert float(frame_lines[3].split()[1]) == pytest.approx(2.45)
