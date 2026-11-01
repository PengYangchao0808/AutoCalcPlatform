"""Round-7 #7d — DLPNO-CCSD(T) energy labels (E(0) is the SCF reference).

An ORCA DLPNO-CCSD(T) output carries four different numbers that differ by
hundreds of kcal/mol:

* ``E(0)``   — the SCF *reference* energy,
* ``E(TOT)`` — the CCSD total,
* ``E(T)``   — the perturbative-triples correction,
* ``FINAL SINGLE POINT ENERGY`` — the CCSD(T) total (the reported result).

The defect: nothing named the difference, so ``E(0)`` could be read (and
reported) as the correlated energy.  This module locks the named energy
report: the result is the final single-point energy, every DLPNO number is
labelled metadata, and the SCF reference can never become the reported
result.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from cccp.calculation._common import qc_metadata_json
from cccp.qc.interfaces.orca import (
    OrcaEnergyReport,
    ORCAInterface,
    energy_report_for,
    parse_energy_report,
)

_XYZ = """3
water
O 0.000000 0.000000 0.000000
H 0.960000 0.000000 0.000000
H -0.240000 0.930000 0.000000
"""

_E0 = -76.419737530
_ETOT = -76.327836000
_ET = -0.003120000
_FINAL = -76.330956000

#: Realistic ORCA DLPNO-CCSD(T) excerpt: SCF final energy, the DLPNO
#: decomposition (all three numbers) and the post-correlation final energy.
_DLPNO_OUTPUT = f"""\
                 *****************
                 * O   R   C   A *
                 *****************

Program Version 6.1.1  -  RELEASE   -

-----------------------  SCF CONVERGED AFTER 12 CYCLES -----------------------
  Total Energy       :          {_E0} Eh

FINAL SINGLE POINT ENERGY       {_E0}

-------------------------   DLPNO-CCSD(T) RESULTS  -------------------------

Total number of pairs                          ...
E(0)                                       ...      {_E0}
E(TOT)                                     ...      {_ETOT}
E(T)                                       ...       {_ET}

FINAL SINGLE POINT ENERGY       {_FINAL}

ORCA TERMINATED NORMALLY
"""

#: Broken shape: the output has a DLPNO block but no post-correlation final
#: line, so the last FINAL SINGLE POINT ENERGY is the SCF reference.
_DLPNO_NO_FINAL = f"""\
Program Version 6.1.1  -  RELEASE   -

FINAL SINGLE POINT ENERGY       {_E0}

DLPNO-CCSD(T) RESULTS
E(0)                                       ...      {_E0}
E(TOT)                                     ...      {_ETOT}
E(T)                                       ...       {_ET}

ORCA TERMINATED NORMALLY
"""


def _bare_orca(method: str = "DLPNO-CCSD(T)", basis: str = "def2-TZVPP") -> ORCAInterface:
    iface = ORCAInterface.__new__(ORCAInterface)
    iface.method = method
    iface.basis = basis
    iface.solvent = None
    iface.solvent_model = "none"
    iface.maxcore = 1000
    iface.nproc = 1
    iface.mem_mb = iface.maxcore * iface.nproc
    iface._maxcore_safety = 0.8
    iface._maxcore_pin = iface.maxcore
    iface.charge = 0
    iface.multiplicity = 1
    iface.config = {}
    iface.executable = None
    return iface


# ── parser ──────────────────────────────────────────────────────────────


def test_final_single_point_energy_is_the_dlpno_result() -> None:
    report = parse_energy_report(_DLPNO_OUTPUT, method="DLPNO-CCSD(T)")

    assert isinstance(report, OrcaEnergyReport)
    assert report.result_hartree == pytest.approx(_FINAL)
    assert report.result_source == "final_single_point_energy"
    assert "DLPNO-CCSD(T) total" in report.result_label
    assert report.final_single_point_energy_hartree == pytest.approx(_FINAL)


def test_dlpno_numbers_are_kept_as_labelled_metadata() -> None:
    report = parse_energy_report(_DLPNO_OUTPUT, method="DLPNO-CCSD(T)")
    metadata = report.to_metadata()

    assert metadata["e0_hartree"] == pytest.approx(_E0)
    assert metadata["e_tot_hartree"] == pytest.approx(_ETOT)
    assert metadata["e_t_hartree"] == pytest.approx(_ET)
    assert metadata["scf_reference_hartree"] == pytest.approx(_E0)
    assert metadata["e0_label"] == "SCF reference energy (E(0))"
    assert metadata["scf_reference_label"] == "SCF reference energy (E(0))"
    assert "CCSD" in metadata["e_tot_label"]
    assert "triples" in metadata["e_t_label"]
    assert metadata["dlpno_detected"] is True
    # JSON-safe for manifest/provenance projection.
    projected = qc_metadata_json(metadata)
    assert projected["e0_hartree"] == pytest.approx(_E0)
    assert projected["result_energy_hartree"] == pytest.approx(_FINAL)


def test_scf_reference_is_never_the_reported_result() -> None:
    report = parse_energy_report(_DLPNO_OUTPUT, method="DLPNO-CCSD(T)")
    assert report.result_hartree != pytest.approx(_E0)
    assert report.result_hartree != pytest.approx(report.scf_reference_hartree)
    assert report.e0_hartree == pytest.approx(_E0)


def test_broken_shape_without_post_correlation_total_uses_e_tot_plus_e_t() -> None:
    report = parse_energy_report(_DLPNO_NO_FINAL, method="DLPNO-CCSD(T)")

    # The raw final line IS the SCF reference: it must not be reported.
    assert report.final_single_point_energy_hartree == pytest.approx(_E0)
    assert report.consistency == "final_matches_scf_reference"
    assert report.result_source == "e_tot_plus_e_t"
    assert report.result_hartree == pytest.approx(_ETOT + _ET)
    assert "NOT used" in report.result_label


def test_broken_shape_without_triples_refuses_to_report_an_energy() -> None:
    text = f"""\
FINAL SINGLE POINT ENERGY       {_E0}
DLPNO-CCSD(T) RESULTS
E(0)                                       ...      {_E0}
E(TOT)                                     ...      {_ETOT}
"""
    report = parse_energy_report(text, method="DLPNO-CCSD(T)")
    assert report.result_hartree is None
    assert report.result_source == "unresolved"
    assert report.consistency == "final_matches_scf_reference"
    assert "refusing" in report.result_label.lower()


def test_non_dlpno_run_keeps_the_plain_single_point_label() -> None:
    text = f"FINAL SINGLE POINT ENERGY       {_FINAL}\n"
    report = parse_energy_report(text, method="wB97X-D4")
    assert report.result_hartree == pytest.approx(_FINAL)
    assert report.result_label.startswith("single-point energy")
    assert report.result_source == "final_single_point_energy"
    assert report.dlpno_detected is False
    assert report.e0_hartree is None


def test_missing_output_file_yields_an_empty_report(tmp_path: Path) -> None:
    report = energy_report_for(tmp_path / "does-not-exist.out", method="DLPNO-CCSD(T)")
    assert report.result_hartree is None
    assert report.scf_reference_hartree is None


# ── QCResult integration ────────────────────────────────────────────────


def test_single_point_reports_the_correlated_total_with_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    iface = _bare_orca()

    def fake_run(self: ORCAInterface, input_file: Path, output_file: Path) -> bool:
        output_file.write_text(_DLPNO_OUTPUT, encoding="utf-8")
        return True

    monkeypatch.setattr(ORCAInterface, "_run_orca", fake_run)

    result = iface.single_point(
        np.asarray([[0.0, 0.0, 0.0], [0.96, 0.0, 0.0], [-0.24, 0.93, 0.0]]),
        ["O", "H", "H"],
        output_dir=tmp_path,
        output_name="dlpno",
        method="DLPNO-CCSD(T)",
        basis="def2-TZVPP",
    )

    assert result.success is True
    assert result.energy == pytest.approx(_FINAL)
    report = result.metadata["energy_report"]
    assert report["result_energy_hartree"] == pytest.approx(_FINAL)
    assert report["e0_hartree"] == pytest.approx(_E0)
    assert report["e0_label"] == "SCF reference energy (E(0))"
    assert report["e_tot_hartree"] == pytest.approx(_ETOT)
    assert report["e_t_hartree"] == pytest.approx(_ET)
    assert result.energy != report["scf_reference_hartree"]


def test_single_point_refuses_the_scf_reference_as_correlated_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    iface = _bare_orca()

    def fake_run(self: ORCAInterface, input_file: Path, output_file: Path) -> bool:
        output_file.write_text(_DLPNO_NO_FINAL, encoding="utf-8")
        return True

    monkeypatch.setattr(ORCAInterface, "_run_orca", fake_run)

    result = iface.single_point(
        np.asarray([[0.0, 0.0, 0.0], [0.96, 0.0, 0.0], [-0.24, 0.93, 0.0]]),
        ["O", "H", "H"],
        output_dir=tmp_path,
        output_name="dlpno_broken",
        method="DLPNO-CCSD(T)",
        basis="def2-TZVPP",
    )

    # Guard fallback: the correlated total is E(TOT)+E(T), never E(0).
    assert result.success is True
    assert result.energy == pytest.approx(_ETOT + _ET)
    assert result.metadata["energy_report"]["scf_reference_hartree"] == pytest.approx(_E0)
