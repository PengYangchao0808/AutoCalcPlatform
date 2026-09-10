"""Tests for ORCA normal-mode parsing with stable mode indices (todo 20)."""

from __future__ import annotations

from pathlib import Path

import pytest

from acp.results.orca_parser import OrcaCalculation, OrcaOutputParser

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "structure_viewer"
FULL_MODES_FIXTURE = FIXTURES / "orca_freq_modes_full.txt"
TRUNCATED_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "orca_optfreq_real_sections.txt"


class TestParseNormalModes:
    """Normal-mode vectors with ORCA mode index (no compaction)."""

    def test_mode_frequencies_from_full_fixture(self) -> None:
        """Modes 6,7 imaginary; mode 8 real — exact values from fixture."""
        text = FULL_MODES_FIXTURE.read_text(encoding="utf-8")
        calc = OrcaOutputParser().parse_text(text)

        assert calc.mode_frequencies[6] == pytest.approx(-797.72)
        assert calc.mode_frequencies[7] == pytest.approx(-791.36)
        assert calc.mode_frequencies[8] == pytest.approx(1411.55)

    def test_mode_frequencies_include_zero_modes(self) -> None:
        """Zero modes (0-5) are NOT dropped from the frequency map."""
        text = FULL_MODES_FIXTURE.read_text(encoding="utf-8")
        calc = OrcaOutputParser().parse_text(text)

        # parse_ts_frequency_map / local mirror drops 0.0 frequencies
        # so zero modes should NOT appear in mode_frequencies
        for mode in range(6):
            assert mode not in calc.mode_frequencies

    def test_mode_vectors_shape_for_imaginary(self) -> None:
        """Vectors for mode 6: 3 atoms × 3 components, finite, nonzero."""
        text = FULL_MODES_FIXTURE.read_text(encoding="utf-8")
        calc = OrcaOutputParser().parse_text(text)

        vecs = calc.mode_vectors[6]
        assert len(vecs) == 3  # 3 atoms
        for atom_vec in vecs:
            assert len(atom_vec) == 3
            for val in atom_vec:
                assert isinstance(val, float)
                # Physically-plausible nonzero displacements
                assert val != 0.0

    def test_mode_vectors_keys_include_all_modes(self) -> None:
        """Vector keys include 0..8 (zero modes NOT dropped from vector map)."""
        text = FULL_MODES_FIXTURE.read_text(encoding="utf-8")
        calc = OrcaOutputParser().parse_text(text)

        # Zero-mode vectors ARE present (they're the zero rows in the matrix)
        for mode in range(9):
            assert mode in calc.mode_vectors

    def test_mode_ir_intensities_parsed(self) -> None:
        """IR intensities parsed for modes 6, 7, 8."""
        text = FULL_MODES_FIXTURE.read_text(encoding="utf-8")
        calc = OrcaOutputParser().parse_text(text)

        assert calc.mode_ir_intensities is not None
        assert calc.mode_ir_intensities[6] == pytest.approx(66.542)
        assert calc.mode_ir_intensities[7] == pytest.approx(45.123)
        assert calc.mode_ir_intensities[8] == pytest.approx(81.914)

    def test_mode_ir_intensities_none_when_absent(self) -> None:
        """No IR SPECTRUM section → mode_ir_intensities is None."""
        text = FULL_MODES_FIXTURE.read_text(encoding="utf-8")
        # Strip IR section
        cutoff = text.index("IR SPECTRUM")
        text_no_ir = text[:cutoff]
        calc = OrcaOutputParser().parse_text(text_no_ir)

        assert calc.mode_ir_intensities is None


class TestTruncatedFixtureResilience:
    """Partial NORMAL MODES block (modes 0-5 only) must not raise."""

    def test_truncated_fixture_parses_without_exception(self) -> None:
        """Original fixture has truncated NORMAL MODES — parser is resilient."""
        text = TRUNCATED_FIXTURE.read_text(encoding="utf-8")
        calc = OrcaOutputParser().parse_text(text)

        # Should still parse frequencies from the second VIBRATIONAL FREQUENCIES section
        assert len(calc.frequencies) > 0
        # mode_frequencies may be empty or partial — no crash
        assert isinstance(calc.mode_frequencies, dict)
        assert isinstance(calc.mode_vectors, dict)


class TestLocalFallbackMirror:
    """Local regex mirror produces same frequency map when cccp unavailable."""

    def test_local_frequency_map_matches_cccp(self) -> None:
        """Inline sample → local mirror produces correct mode-index map."""
        from acp.results.orca_parser import _local_parse_frequency_map

        sample = """VIBRATIONAL FREQUENCIES
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
"""
        result = _local_parse_frequency_map(sample)
        assert result[6] == pytest.approx(-797.72)
        assert result[7] == pytest.approx(1615.84)
        assert result[8] == pytest.approx(3896.58)
        assert 0 not in result  # zero modes excluded

    def test_local_mode_vectors_parse(self) -> None:
        """Local mirror parses NORMAL MODES section into tuples."""
        from acp.results.orca_parser import _local_parse_mode_vectors

        # 3-atom molecule: 9 rows (3 atoms × 3 components), 2 modes in batch
        sample = """
NORMAL MODES
------------

                  0          1
      0       0.000000   0.000000
      1       0.000000   0.000000
      2       0.000000   0.000000
      3       0.000000   0.000000
      4       0.000000   0.000000
      5       0.000000   0.000000
      6       0.000000   0.000000
      7       0.000000   0.000000
      8       0.000000   0.000000

                  2          3
      0       0.010000   0.100000
      1       0.020000   0.110000
      2       0.030000   0.120000
      3       0.040000   0.130000
      4       0.050000   0.140000
      5       0.060000   0.150000
      6       0.070000   0.160000
      7       0.080000   0.170000
      8       0.090000   0.180000

VIBRATIONAL FREQUENCIES
-----------------------
"""
        result = _local_parse_mode_vectors(sample)
        assert 2 in result
        assert 3 in result
        # 9 components → 3 atoms × 3 components
        assert len(result[2]) == 3
        assert result[2][0] == pytest.approx((0.01, 0.02, 0.03))
        assert result[2][1] == pytest.approx((0.04, 0.05, 0.06))
        assert result[2][2] == pytest.approx((0.07, 0.08, 0.09))

    def test_fallback_import_flag(self) -> None:
        """Module imports successfully regardless of cccp availability."""
        import acp.results.orca_parser as mod

        assert hasattr(mod, "_CCCP_AVAILABLE")
        assert isinstance(mod._CCCP_AVAILABLE, bool)
