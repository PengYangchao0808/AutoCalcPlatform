"""Real ORCA 6.1.1 CASSCF fixtures — D5 defect regressions (T02).

Expected values come from the audit's independent decode
(``docs/reports/ACP_Real_QC_Gap_Audit_20261006.json`` sections ``D5`` and
``D5_scf_false_positive``) — never from the parser under test.

xfail discipline: only assertions that currently fail carry
``xfail(strict=True)``:
* real CASSCF positive: ``converged`` is ``False`` and natural occupations
  are empty (markers/energy parse today and stay normal tests);
* SCF-only probe: ``converged`` is ``True`` (false positive, audit D5).
The truncation negative currently behaves correctly, so it runs as a normal
test with NO marker. When T09 lands the fix these markers must be removed;
strict XPASS turns any leftover marker red.
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
    """ROOT 0 CAS energy is recovered (currently parsed, stays a normal test)."""
    result = parse_casscf_output(CASSCF_OUT)
    assert result["casscf_energy"] == pytest.approx(ROOT0_CAS_ENERGY, abs=1e-9)


@pytest.mark.xfail(
    strict=True,
    reason="D5 real CASSCF output reported as unconverged despite CAS-SCF markers — see T09",
)
def test_casscf_water_converged() -> None:
    """Real converged CASSCF run must be flagged converged.

    Currently ``False``: the parser only looks for
    ``ORBITAL OPTIMIZATION HAS CONVERGED`` / ``THE SCF HAS CONVERGED``,
    neither of which appears in a CASSCF output (audit D5).
    """
    result = parse_casscf_output(CASSCF_OUT)
    assert result["converged"] is True


@pytest.mark.xfail(
    strict=True,
    reason="D5 natural occupations not decoded from real CASSCF output — see T09",
)
def test_casscf_water_natural_occupations() -> None:
    """Natural occupations of the converged CAS run (currently empty, audit D5)."""
    result = parse_casscf_output(CASSCF_OUT)
    assert result["natural_occupations"] == pytest.approx(EXPECTED_NATURAL_OCCUPATIONS, abs=1e-5)


@pytest.mark.xfail(
    strict=True,
    reason="D5 SCF-only output false positive in CASSCF convergence check — see T09",
)
def test_scf_without_casscf_not_converged() -> None:
    """An SCF-only output must never count as a converged CASSCF result.

    Currently ``True`` because the fallback ``THE SCF HAS CONVERGED``
    matches plain SCF text (audit ``D5_scf_false_positive``).
    """
    result = parse_casscf_output(SCF_ONLY_OUT)
    assert result["converged"] is False


def test_truncated_casscf_output_not_converged(tmp_path: Path) -> None:
    """Truncation before the CAS-SCF results markers: no convergence, no energy.

    Controlled malformed input: cut everything from the first CAS-SCF marker
    onward (markers at line 726, final energy at line 1161 of the fixture).
    Current behavior already returns ``converged=False`` / ``casscf_energy=None``,
    so this runs as a NORMAL test (an xfail marker here would XPASS and fail).
    """
    text = CASSCF_OUT.read_text(encoding="utf-8", errors="replace")
    cut = text.index(CAS_ENERGY_MARKER)
    truncated = tmp_path / "casscf_truncated.out"
    truncated.write_bytes(text[:cut].encode("utf-8"))

    result = parse_casscf_output(truncated)
    assert result["converged"] is False
    assert result["casscf_energy"] is None
    assert result["natural_occupations"] == []
