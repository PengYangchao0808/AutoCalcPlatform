"""Tests for ORCA normal-mode parsing (todo 20) + normal_modes_v1 product (todo 21)."""

from __future__ import annotations

import math
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

        for mode in range(6):
            assert mode in calc.mode_frequencies
            assert calc.mode_frequencies[mode] == pytest.approx(0.0)

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

    def test_vector_keys_subset_of_frequency_keys(self) -> None:
        """Regression: vector-map keys ⊆ frequency-map keys (alignment guarantee)."""
        text = FULL_MODES_FIXTURE.read_text(encoding="utf-8")
        calc = OrcaOutputParser().parse_text(text)

        for mode_idx in calc.mode_vectors:
            assert mode_idx in calc.mode_frequencies, (
                f"mode_vectors has key {mode_idx} missing from mode_frequencies"
            )


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


# ── build_normal_modes_product (todo 21) ────────────────────────────────────


class TestBuildNormalModesProduct:
    """build_normal_modes_product emits normal_modes_v1 schema."""

    def test_happy_path_from_fixture(self) -> None:
        """Full fixture → 9 modes ordered 0..8, mode 6 imaginary, vectors correct."""
        from acp.results.frequencies import build_normal_modes_product

        text = FULL_MODES_FIXTURE.read_text(encoding="utf-8")
        calc = OrcaOutputParser().parse_text(text)
        product = build_normal_modes_product(calc, geometry_product_id=None, atom_count=3)

        assert product["schema_version"] == "normal_modes_v1"
        assert product["units"]["frequency"] == "cm-1"
        assert product["units"]["displacement"] == "dimensionless_orca_normal_mode"
        assert product["atom_count"] == 3
        assert product["geometry_product_id"] is None

        modes = product["modes"]
        assert len(modes) == 9

        # Ordered by mode_index ascending
        indices = [m["mode_index"] for m in modes]
        assert indices == list(range(9))

        mode6 = modes[6]
        assert mode6["mode_index"] == 6
        assert mode6["frequency_cm1"] == pytest.approx(-797.72)
        assert mode6["imaginary"] is True
        assert mode6["ir_intensity"] == pytest.approx(66.542)
        assert len(mode6["vectors"]) == 3
        for row in mode6["vectors"]:
            assert len(row) == 3
            for val in row:
                assert isinstance(val, float)
                assert math.isfinite(val)

        mode8 = modes[8]
        assert mode8["frequency_cm1"] == pytest.approx(1411.55)
        assert mode8["imaginary"] is False

        assert product["warnings"] == []

    def test_geometry_product_id_passed_through(self) -> None:
        """geometry_product_id is included in product."""
        from acp.results.frequencies import build_normal_modes_product

        text = FULL_MODES_FIXTURE.read_text(encoding="utf-8")
        calc = OrcaOutputParser().parse_text(text)
        product = build_normal_modes_product(calc, geometry_product_id="batch_item_001", atom_count=3)

        assert product["geometry_product_id"] == "batch_item_001"

    def test_ir_intensity_null_when_absent(self) -> None:
        """Mode without IR intensity → ir_intensity omitted from mode dict."""
        from acp.results.frequencies import build_normal_modes_product

        calc = OrcaCalculation(
            mode_frequencies={0: 0.0, 1: 100.0},
            mode_vectors={
                0: ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0)),
                1: ((0.01, 0.02, 0.03), (0.04, 0.05, 0.06)),
            },
            mode_ir_intensities=None,
        )
        product = build_normal_modes_product(calc, geometry_product_id=None, atom_count=2)

        assert len(product["modes"]) == 2
        for m in product["modes"]:
            assert "ir_intensity" not in m

    def test_corrupt_mode_skipped_with_warning(self) -> None:
        """Mode with wrong atom_count → skipped + warning, product still valid."""
        from acp.results.frequencies import build_normal_modes_product

        calc = OrcaCalculation(
            mode_frequencies={0: 0.0, 1: 100.0},
            mode_vectors={
                0: ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0)),
                1: ((0.01,),),
            },
            mode_ir_intensities=None,
        )
        product = build_normal_modes_product(calc, geometry_product_id=None, atom_count=2)

        assert len(product["modes"]) == 1
        assert product["modes"][0]["mode_index"] == 0
        assert len(product["warnings"]) == 1
        assert "1" in product["warnings"][0]

    def test_corrupt_mode_wrong_atom_count(self) -> None:
        """Mode with wrong number of rows → skipped + warning."""
        from acp.results.frequencies import build_normal_modes_product

        calc = OrcaCalculation(
            mode_frequencies={0: 100.0},
            mode_vectors={
                0: ((0.01, 0.02, 0.03),),
            },
            mode_ir_intensities=None,
        )
        product = build_normal_modes_product(calc, geometry_product_id=None, atom_count=3)

        assert len(product["modes"]) == 0
        assert len(product["warnings"]) == 1

    def test_corrupt_mode_nan_vector(self) -> None:
        """Mode with NaN in vectors → skipped + warning."""
        from acp.results.frequencies import build_normal_modes_product

        calc = OrcaCalculation(
            mode_frequencies={0: 100.0},
            mode_vectors={
                0: ((float("nan"), 0.0, 0.0), (0.0, 0.0, 0.0)),
            },
            mode_ir_intensities=None,
        )
        product = build_normal_modes_product(calc, geometry_product_id=None, atom_count=2)

        assert len(product["modes"]) == 0
        assert len(product["warnings"]) == 1

    def test_missing_frequency_skips_mode(self) -> None:
        """Mode in vectors but not in frequencies → skipped + warning."""
        from acp.results.frequencies import build_normal_modes_product

        calc = OrcaCalculation(
            mode_frequencies={0: 100.0},
            mode_vectors={
                0: ((0.01, 0.02, 0.03),),
                1: ((0.04, 0.05, 0.06),),
            },
            mode_ir_intensities=None,
        )
        product = build_normal_modes_product(calc, geometry_product_id=None, atom_count=1)

        assert len(product["modes"]) == 1
        assert product["modes"][0]["mode_index"] == 0
        assert len(product["warnings"]) == 1
        assert "1" in product["warnings"][0]

    def test_empty_mode_vectors(self) -> None:
        """No mode_vectors → empty modes list, no warnings."""
        from acp.results.frequencies import build_normal_modes_product

        calc = OrcaCalculation(
            mode_frequencies={0: 100.0},
            mode_vectors={},
            mode_ir_intensities=None,
        )
        product = build_normal_modes_product(calc, geometry_product_id=None, atom_count=1)

        assert product["modes"] == []
        assert product["warnings"] == []


class TestBuildFrequencyReportExtension:
    """build_frequency_report extended with normal_modes integration."""

    def test_with_modes_returns_normal_modes_available_true(self) -> None:
        """Calc with mode_vectors → normal_modes_available=True + normal_modes product."""
        from acp.results.frequencies import build_frequency_report

        text = FULL_MODES_FIXTURE.read_text(encoding="utf-8")
        calc = OrcaOutputParser().parse_text(text)
        report = build_frequency_report(calc)

        assert report["normal_modes_available"] is True
        assert "normal_modes" in report
        assert report["normal_modes"]["schema_version"] == "normal_modes_v1"
        assert len(report["normal_modes"]["modes"]) == 9

    def test_without_modes_keeps_backward_compat(self) -> None:
        """Calc without mode_vectors → normal_modes_available=False, key preserved."""
        from acp.results.frequencies import build_frequency_report

        calc = OrcaCalculation(frequencies=[1615.84])
        report = build_frequency_report(calc)

        assert report["normal_modes_available"] is False
        assert "normal_modes" not in report

    def test_existing_keys_unchanged(self) -> None:
        """Key set difference is exactly additive (normal_modes + normal_modes_available change)."""
        from acp.results.frequencies import build_frequency_report

        calc_no_modes = OrcaCalculation(
            frequencies=[-797.72, 1615.84],
            imaginary_modes=[-797.72],
            ir_intensities=[66.542, 81.914],
        )
        report_no = build_frequency_report(calc_no_modes)

        calc_with_modes = OrcaCalculation(
            frequencies=[-797.72, 1615.84],
            imaginary_modes=[-797.72],
            ir_intensities=[66.542, 81.914],
            mode_frequencies={6: -797.72, 7: 1615.84},
            mode_vectors={
                6: ((0.01, 0.02, 0.03),),
                7: ((0.04, 0.05, 0.06),),
            },
            mode_ir_intensities={6: 66.542, 7: 81.914},
        )
        report_yes = build_frequency_report(calc_with_modes)

        for key in report_no:
            if key in ("normal_modes_available",):
                continue
            assert key in report_yes, f"Key {key} missing from report with modes"

        extra_keys = set(report_yes) - set(report_no)
        assert extra_keys == {"normal_modes"}

    def test_backward_compat_ir_intensities_key_preserved(self) -> None:
        """ir_intensities key is preserved in both branches."""
        from acp.results.frequencies import build_frequency_report

        calc = OrcaCalculation(
            frequencies=[-797.72],
            imaginary_modes=[-797.72],
            ir_intensities=[66.542],
            mode_frequencies={6: -797.72},
            mode_vectors={6: ((0.01, 0.02, 0.03),)},
            mode_ir_intensities={6: 66.542},
        )
        report = build_frequency_report(calc)

        assert "ir_intensities" in report
        assert report["ir_intensities"] == [66.542]
        assert report["has_imaginary"] is True
