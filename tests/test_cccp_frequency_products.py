"""T19: cccp vibration-product builders (single implementation) stay equivalent.

The builders moved verbatim from ``acp.results.frequencies`` to
:mod:`cccp.analysis.frequency_products`.  These tests freeze the product JSON
through the **real parse chain** (ORCA fixture -> :class:`OrcaOutputParser`)
and assert the ACP public projection matches the CCCP implementation in both
the live dual module state and the post-patch forwarding state.
"""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "structure_viewer"
FULL_MODES_FIXTURE = FIXTURES / "orca_freq_modes_full.txt"


def _parse_fixture():
    from cccp.analysis.orca_parser import OrcaOutputParser

    return OrcaOutputParser().parse_text(FULL_MODES_FIXTURE.read_text(encoding="utf-8"))


class TestCccpNormalModesProduct:
    """The cccp builder emits the frozen ``normal_modes_v1`` schema."""

    def test_real_parse_chain_happy_path(self) -> None:
        from cccp.analysis.frequency_products import build_normal_modes_product

        product = build_normal_modes_product(
            _parse_fixture(), geometry_product_id=None, atom_count=3
        )

        assert product["schema_version"] == "normal_modes_v1"
        assert product["units"] == {
            "frequency": "cm-1",
            "displacement": "dimensionless_orca_normal_mode",
        }
        assert product["atom_count"] == 3
        assert product["geometry_product_id"] is None
        assert product["warnings"] == []
        assert [m["mode_index"] for m in product["modes"]] == list(range(9))
        assert product["modes"][6]["frequency_cm1"] == pytest.approx(-797.72)
        assert product["modes"][6]["imaginary"] is True
        assert product["modes"][6]["ir_intensity"] == pytest.approx(66.542)
        assert product["modes"][8]["imaginary"] is False

    def test_native_mode_numbering_never_renumbered(self) -> None:
        from cccp.analysis.frequency_products import build_normal_modes_product

        calc = _parse_fixture()
        product = build_normal_modes_product(calc, geometry_product_id=None, atom_count=3)

        # Zero modes 0..5 are preserved with their ORCA-native indices.
        assert {m["mode_index"] for m in product["modes"]} == set(calc.mode_frequencies)
        assert [m["mode_index"] for m in product["modes"]] == sorted(calc.mode_frequencies)

    def test_bad_mode_skipped_with_reason_not_empty_success(self) -> None:
        from cccp.analysis.frequency_products import build_normal_modes_product
        from cccp.analysis.orca_parser import OrcaCalculation

        calc = OrcaCalculation(
            mode_frequencies={0: 100.0},
            mode_vectors={0: ((0.01, 0.02, 0.03),)},
            mode_ir_intensities=None,
        )
        product = build_normal_modes_product(calc, geometry_product_id=None, atom_count=3)

        assert product["modes"] == []
        assert product["warnings"] == ["Mode 0: len(vectors)=1 != atom_count=3; skipped"]

    def test_missing_frequency_skipped_with_reason(self) -> None:
        from cccp.analysis.frequency_products import build_normal_modes_product
        from cccp.analysis.orca_parser import OrcaCalculation

        calc = OrcaCalculation(
            mode_frequencies={0: 100.0},
            mode_vectors={0: ((0.01, 0.02, 0.03),), 1: ((0.04, 0.05, 0.06),)},
            mode_ir_intensities=None,
        )
        product = build_normal_modes_product(calc, geometry_product_id=None, atom_count=1)

        assert [m["mode_index"] for m in product["modes"]] == [0]
        assert product["warnings"] == ["Mode 1: frequency missing from mode_frequencies; skipped"]

    def test_non_finite_vector_skipped(self) -> None:
        from cccp.analysis.frequency_products import build_normal_modes_product
        from cccp.analysis.orca_parser import OrcaCalculation

        calc = OrcaCalculation(
            mode_frequencies={0: 100.0},
            mode_vectors={0: ((float("nan"), 0.0, 0.0), (0.0, 0.0, 0.0))},
            mode_ir_intensities=None,
        )
        product = build_normal_modes_product(calc, geometry_product_id=None, atom_count=2)

        assert product["modes"] == []
        assert product["warnings"] == ["Mode 0: non-finite value in vectors; skipped"]

    def test_empty_vectors_yield_empty_product_without_warning(self) -> None:
        from cccp.analysis.frequency_products import build_normal_modes_product
        from cccp.analysis.orca_parser import OrcaCalculation

        calc = OrcaCalculation(
            mode_frequencies={0: 100.0}, mode_vectors={}, mode_ir_intensities=None
        )
        product = build_normal_modes_product(calc, geometry_product_id=None, atom_count=1)

        assert product["modes"] == []
        assert product["warnings"] == []


class TestAcpProjectionEquivalence:
    """ACP public projection equals the CCCP implementation on fixed inputs."""

    def test_normal_modes_product_matches_acp(self) -> None:
        from acp.results.frequencies import build_normal_modes_product as acp_build
        from cccp.analysis.frequency_products import build_normal_modes_product as cccp_build

        calc = _parse_fixture()
        assert acp_build(calc, geometry_product_id="batch_item_001", atom_count=3) == cccp_build(
            calc, geometry_product_id="batch_item_001", atom_count=3
        )

    def test_frequency_report_matches_acp(self) -> None:
        from acp.results.frequencies import build_frequency_report as acp_report
        from cccp.analysis.frequency_products import build_frequency_report as cccp_report

        calc = _parse_fixture()
        assert acp_report(calc) == cccp_report(calc)

    def test_frequency_report_backward_compatible_without_modes(self) -> None:
        from cccp.analysis.frequency_products import build_frequency_report
        from cccp.analysis.orca_parser import OrcaCalculation

        report = build_frequency_report(OrcaCalculation(frequencies=[1615.84]))

        assert report["normal_modes_available"] is False
        assert "normal_modes" not in report
        assert report["frequencies"] == [1615.84]
