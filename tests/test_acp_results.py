"""Tests for the v2 result-parsing package (design doc §14 Phase 5)."""

from __future__ import annotations

import json
from pathlib import Path

import acp.results
from acp.results import (
    CrestEnsemble,
    OrcaCalculation,
    OrcaOutputParser,
    build_frequency_report,
    build_optimization_trajectory,
    build_thermo_report,
    find_products,
    load_result_manifest,
    parse_crest_ensemble,
    parse_xtb_energy,
    parse_xtb_opt_converged,
)
from acp.storage.manifest import ProductKind, ResultManifest

REAL_FREQ_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "orca_optfreq_real_sections.txt"

ORCA_SP_OUTPUT = """                                 *****************
                                 * O R C A 5.0  *
                                 *****************

SCF ITERATIONS
 ITER       Energy            Delta-E        Max-DP      RMS-DP
   0  E=    -76.36347774     -7.636e+01     6.0e-02     1.5e-02
   1  E=    -76.42017634     -5.670e-02     1.2e-02     3.1e-03
   2  E=    -76.42085153     -6.752e-04     5.1e-04     1.3e-04
SCF CONVERGED AFTER 3 ITERATIONS

The HOMO      is:    -0.318778 Eh       -8.6763 eV
The LUMO      is:     0.078437 Eh        2.1342 eV

FINAL SINGLE POINT ENERGY    -76.420851529803

****ORCA-CHEMISTRY JOB DONE****
"""

ORCA_OPT_OUTPUT = """GEOMETRY OPTIMIZATION CYCLE   1
SCF ITERATIONS
   0  E=    -76.36347774
SCF CONVERGED AFTER 1 ITERATIONS
ConvergenceCheck:
 RMS gradient    .... 5.7801e-05 1.0000e-05 F
 Max gradient    .... 2.6231e-04 2.0000e-04 F

GEOMETRY OPTIMIZATION CYCLE   2
SCF ITERATIONS
   0  E=    -76.42017634
SCF CONVERGED AFTER 1 ITERATIONS
ConvergenceCheck:
 RMS gradient    .... 9.4003e-07 1.0000e-05 T
 Max gradient    .... 3.1001e-06 2.0000e-04 T

HURRAY, THE OPTIMIZATION HAS CONVERGED

FINAL SINGLE POINT ENERGY    -76.420851529803
"""

ORCA_FREQ_OUTPUT = """VIBRATIONAL FREQUENCIES
-----------------------

   0:         0.00 cm**-1
   1:         0.00 cm**-1
   2:         0.00 cm**-1
   3:         0.00 cm**-1
   4:         0.00 cm**-1
   5:         0.00 cm**-1
   6:      -797.72 cm**-1 ***imaginary mode***
   7:      1615.84 cm**-1
   8:      3896.58 cm**-1

------------
IR SPECTRUM
------------

 Mode    freq (cm**-1)   eps      T**2     TX       TY       TZ   intensity (km/mol)  (rel)
   6:       -797.72   2.31e-05  1.02e-04  -0.004  -0.010  -0.001    66.542   0.812
   7:       1615.84   4.79e-04  1.32e-03   0.032  -0.008  -0.020    81.914   1.000
   8:       3896.58   1.05e-05  3.10e-05   0.004   0.005  -0.002     1.924   0.023
"""

ORCA_ORBITAL_TABLE = """--------------------------
ORBITAL ENERGIES
--------------------------

  NO   OCC          E(Eh)            E(eV)
   0   2.0000     -19.038598     -517.9559
   1   2.0000      -0.599872      -16.3275
   2   2.0000      -0.286361       -7.7925
   3   0.0000       0.061252        1.6665
   4   0.0000       0.103263        2.8101

FINAL SINGLE POINT ENERGY    -76.420851529803
"""

XTB_OPT_OUTPUT = """         ::                       ::                       ::
   --------------------------------------------------------------
    | .................................TOTAL ENERGY  -11.39433937 Eh
    ...........................................................
         ...

HURRAY, GEOMETRY OPTIMIZATION CONVERGED AFTER 43 ITERATIONS!
"""

XTB_SP_OUTPUT = """       ...........................
         converged SCF energy  -11.39433937 Eh
       ...........................

TOTAL ENERGY  -11.39433937 Eh
"""

CREST_ENSEMBLE_XYZ = """3
-11.39433937
C     0.0000000000    0.0000000000    0.0000000000
O     1.2000000000    0.0000000000    0.0000000000
H    -0.6000000000    1.0000000000    0.0000000000
3
energy  -11.39211783  Gnorm  0.0008
C     0.0100000000    0.0000000000    0.0000000000
O     1.2100000000    0.0100000000    0.0000000000
H    -0.5900000000    1.0100000000    0.0100000000
3
-11.39144290
C     0.0200000000    0.0000000000    0.0000000000
O     1.2200000000    0.0000000000    0.0000000000
H    -0.5800000000    1.0200000000    0.0000000000
"""

THERMO_DICT = {
    "sp_energy_hartree": -76.4208515,
    "free_energy_hartree": -76.2442871,
    "free_energy_kcal_mol": -47827.9,
    "thermal_correction_u_hartree": 0.0583212,
    "total_enthalpy_hartree": -76.3573885,
    "total_gibbs_hartree": -76.2442871,
    "entropy": 0.0612334,
    "temperature_k": 298.15,
    "pressure_atm": 1.0,
    "success": True,
}


def test_orca_parse_singlepoint_energy_and_scf() -> None:
    calc = OrcaOutputParser().parse_text(ORCA_SP_OUTPUT)
    assert calc.success is True
    assert calc.final_energy_hartree == -76.420851529803
    assert calc.converged is True
    assert calc.n_opt_cycles == 0
    assert calc.scf_energies == [-76.36347774, -76.42017634, -76.42085153]


def test_orca_parse_homo_lumo_explicit_lines() -> None:
    calc = OrcaOutputParser().parse_text(ORCA_SP_OUTPUT)
    assert calc.homo_hartree == -0.318778
    assert calc.lumo_hartree == 0.078437


def test_orca_parse_optimization_convergence() -> None:
    calc = OrcaOutputParser().parse_text(ORCA_OPT_OUTPUT)
    assert calc.converged is True
    assert calc.n_opt_cycles == 2
    assert calc.final_energy_hartree == -76.420851529803
    assert calc.scf_energies == [-76.36347774, -76.42017634]
    assert calc.gradients_rms == [5.7801e-05, 9.4003e-07]


def test_orca_parse_unconverged_opt() -> None:
    text = ORCA_OPT_OUTPUT.replace(
        "HURRAY, THE OPTIMIZATION HAS CONVERGED", "THE OPTIMIZATION DID NOT CONVERGE"
    )
    calc = OrcaOutputParser().parse_text(text)
    assert calc.converged is False
    assert calc.n_opt_cycles == 2


def test_orca_parse_frequencies_with_one_imaginary() -> None:
    calc = OrcaOutputParser().parse_text(ORCA_FREQ_OUTPUT)
    assert calc.frequencies == [-797.72, 1615.84, 3896.58]
    assert calc.imaginary_modes == [-797.72]


def test_orca_parse_ir_intensities_aligned_with_frequencies() -> None:
    calc = OrcaOutputParser().parse_text(ORCA_FREQ_OUTPUT)
    assert calc.ir_intensities == [66.542, 81.914, 1.924]


def test_orca_parse_homo_lumo_from_orbital_table_fallback() -> None:
    calc = OrcaOutputParser().parse_text(ORCA_ORBITAL_TABLE)
    assert calc.homo_hartree == -0.286361
    assert calc.lumo_hartree == 0.061252


def test_orca_parse_last_energy_wins_on_multiple_matches() -> None:
    text = "FINAL SINGLE POINT ENERGY    -76.1\nFINAL SINGLE POINT ENERGY    -76.2\n"
    calc = OrcaOutputParser().parse_text(text)
    assert calc.final_energy_hartree == -76.2


def test_orca_parse_garbage_output_never_raises() -> None:
    calc = OrcaOutputParser().parse_text("random crash text\nno markers at all\n")
    assert calc.success is False
    assert calc.final_energy_hartree is None
    assert calc.converged is False
    assert calc.frequencies == []
    assert calc.scf_energies == []
    assert calc.homo_hartree is None
    assert calc.lumo_hartree is None


def test_orca_parse_missing_file_yields_default_record(tmp_path: Path) -> None:
    calc = OrcaOutputParser().parse(tmp_path / "nonexistent.out")
    assert calc == OrcaCalculation()


def test_orca_parse_real_fixture_uses_last_frequency_section() -> None:
    calc = OrcaOutputParser().parse(REAL_FREQ_FIXTURE)
    assert calc.frequencies == [1615.84, 3795.36, 3896.58]
    assert calc.imaginary_modes == []


def test_orca_parse_from_path(tmp_path: Path) -> None:
    path = tmp_path / "sp.out"
    path.write_text(ORCA_SP_OUTPUT, encoding="utf-8")
    calc = OrcaOutputParser().parse(path)
    assert calc.final_energy_hartree == -76.420851529803


def test_xtb_parse_energy_from_text() -> None:
    assert parse_xtb_energy(XTB_OPT_OUTPUT) == -11.39433937


def test_xtb_parse_energy_takes_last_match() -> None:
    text = "TOTAL ENERGY  -11.3 Eh\nTOTAL ENERGY  -11.39433937 Eh\n"
    assert parse_xtb_energy(text) == -11.39433937


def test_xtb_parse_energy_from_path(tmp_path: Path) -> None:
    path = tmp_path / "xtb.out"
    path.write_text(XTB_SP_OUTPUT, encoding="utf-8")
    assert parse_xtb_energy(path) == -11.39433937


def test_xtb_parse_energy_missing_returns_none() -> None:
    assert parse_xtb_energy("no energy here") is None


def test_xtb_parse_opt_converged_hurray() -> None:
    assert parse_xtb_opt_converged(XTB_OPT_OUTPUT) is True


def test_xtb_parse_opt_converged_sp_output_is_false() -> None:
    assert parse_xtb_opt_converged(XTB_SP_OUTPUT) is False


def test_xtb_parse_opt_converged_empty_is_none() -> None:
    assert parse_xtb_opt_converged("") is None


def test_xtb_parse_opt_converged_from_path(tmp_path: Path) -> None:
    path = tmp_path / "xtb_opt.out"
    path.write_text(XTB_OPT_OUTPUT, encoding="utf-8")
    assert parse_xtb_opt_converged(path) is True


def test_crest_parse_ensemble_three_frames(tmp_path: Path) -> None:
    path = tmp_path / "conformers.xyz"
    path.write_text(CREST_ENSEMBLE_XYZ, encoding="utf-8")
    ensemble = parse_crest_ensemble(path)
    assert ensemble.n_conformers == 3
    assert ensemble.energies == [-11.39433937, -11.39211783, -11.39144290]
    assert ensemble.titles == [
        "-11.39433937",
        "energy  -11.39211783  Gnorm  0.0008",
        "-11.39144290",
    ]


def test_crest_parse_ensemble_title_without_energy(tmp_path: Path) -> None:
    content = "1\nsome title\nH 0.0 0.0 0.0\n"
    path = tmp_path / "one.xyz"
    path.write_text(content, encoding="utf-8")
    ensemble = parse_crest_ensemble(path)
    assert ensemble.n_conformers == 1
    assert ensemble.energies == [0.0]
    assert ensemble.titles == ["some title"]


def test_crest_parse_ensemble_drops_trailing_incomplete_frame(tmp_path: Path) -> None:
    content = CREST_ENSEMBLE_XYZ + "3\ntruncated frame title\nC 0.0 0.0 0.0\n"
    path = tmp_path / "trunc.xyz"
    path.write_text(content, encoding="utf-8")
    ensemble = parse_crest_ensemble(path)
    assert ensemble.n_conformers == 3


def test_crest_parse_ensemble_missing_file(tmp_path: Path) -> None:
    assert parse_crest_ensemble(tmp_path / "missing.xyz") == CrestEnsemble()


def test_manifest_load_present(tmp_path: Path) -> None:
    manifest = ResultManifest(task_id="t1", workflow="opt", status="done")
    manifest.add_product(
        "opt_traj", "Opt trajectory", "trajectories/optimization.xyz", ProductKind.TRAJECTORY
    )
    manifest.add_product(
        "final_struct", "Final structure", "structures/final.xyz", ProductKind.STRUCTURE
    )
    manifest.write(tmp_path / "RESULT")
    loaded = load_result_manifest(tmp_path)
    assert loaded is not None
    assert loaded.task_id == "t1"
    assert len(loaded.products) == 2


def test_manifest_load_missing_returns_none(tmp_path: Path) -> None:
    assert load_result_manifest(tmp_path) is None


def test_manifest_load_corrupt_returns_none(tmp_path: Path) -> None:
    result_dir = tmp_path / "RESULT"
    result_dir.mkdir()
    (result_dir / "result_manifest.json").write_text("{ not json", encoding="utf-8")
    assert load_result_manifest(tmp_path) is None


def test_manifest_find_products_filters_by_kind(tmp_path: Path) -> None:
    manifest = ResultManifest(task_id="t1", workflow="optfreq")
    manifest.add_product("struct", "Final structure", "structures/final.xyz", ProductKind.STRUCTURE)
    manifest.add_product(
        "freqs", "Frequency modes", "frequencies/frequencies.json", ProductKind.FREQUENCY_MODES
    )
    manifest.add_product(
        "traj", "Opt trajectory", "trajectories/optimization.xyz", ProductKind.TRAJECTORY
    )
    assert [p.id for p in find_products(manifest, "structure")] == ["struct"]
    assert [p.id for p in find_products(manifest, "frequency_modes")] == ["freqs"]
    assert find_products(manifest, "report") == []
    assert find_products(manifest, "bogus_kind") == []


def test_optimization_trajectory_report() -> None:
    calc = OrcaOutputParser().parse_text(ORCA_OPT_OUTPUT)
    report = build_optimization_trajectory(calc)
    assert report == {
        "scf_energies": [-76.36347774, -76.42017634],
        "converged": True,
        "n_cycles": 2,
        "gradients_rms": [5.7801e-05, 9.4003e-07],
    }


def test_optimization_trajectory_report_without_gradients() -> None:
    report = build_optimization_trajectory(OrcaCalculation(scf_energies=[-1.0]))
    assert report == {
        "scf_energies": [-1.0],
        "converged": False,
        "n_cycles": 0,
        "gradients_rms": [],
    }


def test_frequency_report_with_imaginary() -> None:
    calc = OrcaOutputParser().parse_text(ORCA_FREQ_OUTPUT)
    report = build_frequency_report(calc)
    assert report == {
        "frequencies": [-797.72, 1615.84, 3896.58],
        "imaginary_modes": [-797.72],
        "ir_intensities": [66.542, 81.914, 1.924],
        "has_imaginary": True,
        "normal_modes_available": False,
    }


def test_frequency_report_without_ir_or_imaginary() -> None:
    report = build_frequency_report(OrcaCalculation(frequencies=[1615.84]))
    assert report["ir_intensities"] is None
    assert report["has_imaginary"] is False
    assert report["normal_modes_available"] is False


def test_thermo_report_from_dict_mirroring_simple_writer() -> None:
    report = build_thermo_report(THERMO_DICT)
    assert report == {
        "scf_energy_hartree": -76.4208515,
        "zpe_included": True,
        "enthalpy_hartree": -76.3573885,
        "gibbs_hartree": -76.2442871,
        "entropy": 0.0612334,
        "temperature_k": 298.15,
        "unit_note": "hartree unless noted",
    }


def test_thermo_report_from_path(tmp_path: Path) -> None:
    path = tmp_path / "thermo.json"
    path.write_text(json.dumps(THERMO_DICT), encoding="utf-8")
    report = build_thermo_report(path)
    assert report["scf_energy_hartree"] == -76.4208515
    assert report["gibbs_hartree"] == -76.2442871


def test_thermo_report_gibbs_falls_back_to_free_energy() -> None:
    report = build_thermo_report({"sp_energy_hartree": -1.0, "free_energy_hartree": -0.9})
    assert report["gibbs_hartree"] == -0.9
    assert report["zpe_included"] is False


def test_thermo_report_missing_keys_become_none() -> None:
    report = build_thermo_report({})
    assert report["scf_energy_hartree"] is None
    assert report["enthalpy_hartree"] is None
    assert report["gibbs_hartree"] is None
    assert report["entropy"] is None
    assert report["temperature_k"] is None
    assert report["unit_note"] == "hartree unless noted"


def test_thermo_report_missing_file_becomes_all_none(tmp_path: Path) -> None:
    report = build_thermo_report(tmp_path / "missing_thermo.json")
    assert report["scf_energy_hartree"] is None
    assert report["zpe_included"] is False


def test_package_reexports_all_public_symbols() -> None:
    expected = {
        "MANIFEST_FILENAME",
        "CrestEnsemble",
        "OrcaCalculation",
        "OrcaOutputParser",
        "Product",
        "ProductKind",
        "ResultManifest",
        "build_frequency_report",
        "build_optimization_trajectory",
        "build_thermo_report",
        "find_products",
        "load_result_manifest",
        "parse_crest_ensemble",
        "parse_xtb_energy",
        "parse_xtb_opt_converged",
    }
    assert set(acp.results.__all__) == expected
    for name in expected:
        assert getattr(acp.results, name) is not None
