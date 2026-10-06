"""Real ORCA 6.1.1 CASSCF fixtures — D5 defect regressions (T02, fixed in T09).

Expected values come from the audit's independent decode
(``docs/reports/ACP_Real_QC_Gap_Audit_20261006.json`` sections ``D5`` and
``D5_scf_false_positive``) — never from the parser under test.

Post-T09 invariants covered here:
* real CASSCF positive: ``converged`` is ``True``, natural occupations are
  ``[1.99733, 0.00267]`` (last ``N(occ)=`` print after the CAS markers) and
  ROOT 0 parses from the hyphenless ``CASSCF RESULTS`` section;
* SCF-only probe: ``converged`` is ``False`` and the CAS energy never
  masquerades from an unrelated ``FINAL SINGLE POINT ENERGY`` (audit D5
  false positive);
* truncation before the markers: no convergence, no energy, no occupations;
* truncation after the markers but before the results section: the energy
  marker alone does not prove convergence;
* multi-job blocks: the last CAS-bearing block wins; a later plain-SP job
  must not supply the CAS energy.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cccp.qc.interfaces.orca import parse_casscf_output

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "qc" / "orca61"
CASSCF_OUT = FIXTURE_DIR / "casscf_water.out"
SCF_ONLY_OUT = FIXTURE_DIR / "scf_without_casscf.out"

# Independent decode (audit D5 "actual_markers" / "casscf_energy").
CAS_ENERGY_MARKER = "---- THE CAS-SCF ENERGY   HAS CONVERGED ----"
CAS_GRADIENT_MARKER = "---- THE CAS-SCF GRADIENT HAS CONVERGED ----"
ROOT0_CAS_ENERGY = -75.976220169701
EXPECTED_NATURAL_OCCUPATIONS = [1.99733, 0.00267]


def test_casscf_water_convergence_markers_present() -> None:
    """Both CAS-SCF convergence markers exist verbatim in the real output."""
    text = CASSCF_OUT.read_text(encoding="utf-8", errors="replace")
    assert CAS_ENERGY_MARKER in text
    assert CAS_GRADIENT_MARKER in text


def test_casscf_water_root0_energy_parsed() -> None:
    """ROOT 0 CAS energy is recovered, bound to the CAS section."""
    result = parse_casscf_output(CASSCF_OUT)
    assert result["casscf_energy"] == pytest.approx(ROOT0_CAS_ENERGY, abs=1e-9)


def test_casscf_water_converged() -> None:
    """Real converged CASSCF run must be flagged converged.

    The ORCA 6.1.1 ``THE CAS-SCF ENERGY/GRADIENT HAS CONVERGED`` markers
    plus the ``CASSCF RESULTS`` section prove convergence (audit D5 used
    to report ``False``).
    """
    result = parse_casscf_output(CASSCF_OUT)
    assert result["converged"] is True


def test_casscf_water_natural_occupations() -> None:
    """Natural occupations of the converged CAS run (audit D5 used to be empty)."""
    result = parse_casscf_output(CASSCF_OUT)
    assert result["natural_occupations"] == pytest.approx(EXPECTED_NATURAL_OCCUPATIONS, abs=1e-5)


def test_casscf_water_root0_section_entry() -> None:
    """The hyphenless ``CASSCF RESULTS`` section yields the ROOT 0 entry."""
    result = parse_casscf_output(CASSCF_OUT)
    roots = result["casscf_roots"]
    assert len(roots) == 1
    assert roots[0]["multiplicity"] == 1
    assert roots[0]["root"] == 0
    assert roots[0]["casscf_energy_hartree"] == pytest.approx(-75.9762201697, abs=1e-9)


def test_scf_without_casscf_not_converged() -> None:
    """An SCF-only output must never count as a converged CASSCF result.

    The legacy ``THE SCF HAS CONVERGED`` fallback used to match plain SCF
    text (audit ``D5_scf_false_positive``), and its
    ``FINAL SINGLE POINT ENERGY`` used to surface as a CAS energy.
    """
    result = parse_casscf_output(SCF_ONLY_OUT)
    assert result["converged"] is False
    assert result["casscf_energy"] is None
    assert result["natural_occupations"] == []


def test_truncated_casscf_output_not_converged(tmp_path: Path) -> None:
    """Truncation before the CAS-SCF results markers: no convergence, no energy.

    Controlled malformed input: cut everything from the first CAS-SCF marker
    onward (markers at line 726, final energy at line 1161 of the fixture).
    """
    text = CASSCF_OUT.read_text(encoding="utf-8", errors="replace")
    cut = text.index(CAS_ENERGY_MARKER)
    truncated = tmp_path / "casscf_truncated.out"
    truncated.write_bytes(text[:cut].encode("utf-8"))

    result = parse_casscf_output(truncated)
    assert result["converged"] is False
    assert result["casscf_energy"] is None
    assert result["natural_occupations"] == []


def test_marker_without_final_results_not_converged(tmp_path: Path) -> None:
    """Energy marker present but no final CAS results ⇒ NOT converged.

    Controlled malformed input: cut everything from the ``CASSCF RESULTS``
    section header onward, leaving both convergence markers and the
    iteration ``N(occ)=`` prints but no final CAS facts (no results
    section, no final energy, no ROOT entries).
    """
    text = CASSCF_OUT.read_text(encoding="utf-8", errors="replace")
    cut = text.index("CASSCF RESULTS")
    truncated = tmp_path / "casscf_marker_only.out"
    truncated.write_bytes(text[:cut].encode("utf-8"))

    result = parse_casscf_output(truncated)
    assert CAS_ENERGY_MARKER in text[:cut]
    assert result["converged"] is False
    assert result["casscf_energy"] is None
    assert result["casscf_roots"] == []


def test_multi_job_last_cas_block_wins(tmp_path: Path) -> None:
    """A later plain-SP job must not overwrite the CAS energy (stale-block probe).

    Job 1 is a converged CASSCF run; job 2 is a plain SCF single point with
    its own ``FINAL SINGLE POINT ENERGY``.  The CAS result comes from the
    last CAS-bearing block; the SCF-only block is ignored.
    """
    cas_job = """
Program Version 6.1.1  -  RELEASE   -
CAS-SCF ITERATIONS
   N(occ)=  1.95000 0.05000
                    ---- THE CAS-SCF ENERGY   HAS CONVERGED ----
                    ---- THE CAS-SCF GRADIENT HAS CONVERGED ----
   N(occ)=  1.91000 0.09000
--------------
CASSCF RESULTS
--------------
Final CASSCF energy       : -76.500000000 Eh
---------------------------------------------
CAS-SCF STATES FOR BLOCK  0 MULT= 1 NROOTS= 1
---------------------------------------------
ROOT   0:  E=     -76.5000000000 Eh
FINAL SINGLE POINT ENERGY       -76.5000000000
                             ****ORCA TERMINATED NORMALLY****
"""
    sp_job = """
Program Version 6.1.1  -  RELEASE   -
THE SCF HAS CONVERGED
FINAL SINGLE POINT ENERGY -70.125
                             ****ORCA TERMINATED NORMALLY****
"""
    multi = tmp_path / "casscf_multijob.out"
    multi.write_text(cas_job + sp_job, encoding="utf-8")

    result = parse_casscf_output(multi)
    assert result["converged"] is True
    assert result["casscf_energy"] == pytest.approx(-76.5, abs=1e-9)
    assert result["natural_occupations"] == pytest.approx([1.91, 0.09], abs=1e-6)
    assert result["casscf_roots"][0]["root"] == 0
    assert result["casscf_roots"][0]["casscf_energy_hartree"] == pytest.approx(-76.5, abs=1e-9)
