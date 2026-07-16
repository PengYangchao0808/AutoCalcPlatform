# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
"""Tests for ACP NMR output parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from acp.nmr import parse_gaussian_nmr_log, parse_nmr_output, parse_orca_nmr_log


def _fixture_path() -> Path:
    return Path(__file__).parent / "fixtures" / "nmr" / "gaussian_nmr.log"


def _methane_fixture_path() -> Path:
    return Path(__file__).parent / "fixtures" / "nmr" / "methane_nmr.log"


def test_parse_gaussian_nmr_log_reads_last_section() -> None:
    shieldings = parse_gaussian_nmr_log(_fixture_path(), expected_symbols=["C", "H"])

    assert len(shieldings) == 2
    assert [shielding.atom_index for shielding in shieldings] == [1, 2]
    assert [shielding.symbol for shielding in shieldings] == ["C", "H"]
    assert shieldings[0].isotropic_ppm == pytest.approx(182.7654)
    assert shieldings[0].anisotropy_ppm == pytest.approx(12.3456)
    assert shieldings[0].tensor_components_ppm == {
        "XX": pytest.approx(190.1111),
        "XY": pytest.approx(0.0100),
        "XZ": pytest.approx(-0.0200),
        "YX": pytest.approx(0.0100),
        "YY": pytest.approx(180.2840),
        "YZ": pytest.approx(0.0300),
        "ZX": pytest.approx(-0.0200),
        "ZY": pytest.approx(0.0300),
        "ZZ": pytest.approx(177.9012),
    }
    assert shieldings[1].isotropic_ppm == pytest.approx(30.8765)
    assert shieldings[1].tensor_components_ppm["YY"] == pytest.approx(29.9000)


def test_parse_gaussian_nmr_log_reads_methane_section() -> None:
    shieldings = parse_gaussian_nmr_log(
        _methane_fixture_path(),
        expected_symbols=["C", "H", "H", "H", "H"],
    )

    assert len(shieldings) == 5
    assert [shielding.atom_index for shielding in shieldings] == [1, 2, 3, 4, 5]
    assert [shielding.symbol for shielding in shieldings] == ["C", "H", "H", "H", "H"]
    assert shieldings[0].isotropic_ppm == pytest.approx(192.6845)
    assert shieldings[0].tensor_components_ppm["ZZ"] == pytest.approx(192.5401)
    assert [shielding.isotropic_ppm for shielding in shieldings[1:]] == pytest.approx(
        [31.6551, 31.6442, 31.6508, 31.6466]
    )
    assert shieldings[-1].anisotropy_ppm == pytest.approx(0.0452)


def test_parse_nmr_output_dispatches_gaussian_parser() -> None:
    shieldings = parse_nmr_output("gaussian", _fixture_path(), expected_symbols=["C", "H"])

    assert [shielding.isotropic_ppm for shielding in shieldings] == pytest.approx(
        [182.7654, 30.8765]
    )


@pytest.mark.parametrize(
    ("expected_symbols", "message"),
    [
        (["C"], "atom count"),
        (["H", "C"], "symbols do not match"),
    ],
)
def test_parse_gaussian_nmr_log_validates_expected_symbols(
    expected_symbols: list[str],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _ = parse_gaussian_nmr_log(_fixture_path(), expected_symbols=expected_symbols)


def test_parse_gaussian_nmr_log_raises_when_section_missing(tmp_path: Path) -> None:
    log_file = tmp_path / "missing_nmr.log"
    _ = log_file.write_text("SCF Done\nNo shielding data here\n", encoding="utf-8")

    with pytest.raises(ValueError, match="shielding section"):
        _ = parse_gaussian_nmr_log(log_file)


def _write_fake_orca_nmr_log(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(
        "\n".join(
            [
                "* Single Point Calculation *",
                "",
                "                       NMR SHIELDING TENSOR (PPM)",
                "",
                "  Nucleus   1C:     isotropic=   150.1234   anisotropy=    10.5678",
                "  XX=  155.2345   YX=   0.1234   ZX=   0.2345",
                "  XY=   0.1234   YY=  145.6789   ZY=   0.3456",
                "  XZ=   0.2345   YZ=   0.3456   ZZ=  149.4567",
                "",
                "  Nucleus   2H:     isotropic=    28.9012   anisotropy=     2.3456",
                "  XX=  30.0000   YX=   0.0000   ZX=   0.0000",
                "  XY=   0.0000   YY=  27.0000   ZY=   0.0000",
                "  XZ=   0.0000   YZ=   0.0000   ZZ=  29.0000",
                "",
                "****ORCA-CHEMISTRY JOB DONE****",
            ]
        ),
        encoding="utf-8",
    )


def test_parse_orca_nmr_log_reads_shielding_section(tmp_path: Path) -> None:
    log_file = tmp_path / "orca_nmr.out"
    _write_fake_orca_nmr_log(log_file)

    shieldings = parse_orca_nmr_log(log_file, expected_symbols=["C", "H"])

    assert len(shieldings) == 2
    assert [shielding.atom_index for shielding in shieldings] == [1, 2]
    assert [shielding.symbol for shielding in shieldings] == ["C", "H"]
    assert shieldings[0].isotropic_ppm == pytest.approx(150.1234)
    assert shieldings[0].anisotropy_ppm == pytest.approx(10.5678)
    assert shieldings[0].tensor_components_ppm == {
        "XX": pytest.approx(155.2345),
        "XY": pytest.approx(0.1234),
        "XZ": pytest.approx(0.2345),
        "YX": pytest.approx(0.1234),
        "YY": pytest.approx(145.6789),
        "YZ": pytest.approx(0.3456),
        "ZX": pytest.approx(0.2345),
        "ZY": pytest.approx(0.3456),
        "ZZ": pytest.approx(149.4567),
    }
    assert shieldings[1].isotropic_ppm == pytest.approx(28.9012)
    assert shieldings[1].tensor_components_ppm["ZZ"] == pytest.approx(29.0000)


def test_parse_nmr_output_dispatches_orca_parser(tmp_path: Path) -> None:
    log_file = tmp_path / "orca_nmr_dispatch.out"
    _write_fake_orca_nmr_log(log_file)

    shieldings = parse_nmr_output("orca", log_file, expected_symbols=["C", "H"])

    assert [shielding.isotropic_ppm for shielding in shieldings] == pytest.approx(
        [150.1234, 28.9012]
    )


def test_parse_orca_nmr_log_raises_when_section_missing(tmp_path: Path) -> None:
    log_file = tmp_path / "missing_orca_nmr.log"
    _ = log_file.write_text("SCF Done\nNo NMR data here\n", encoding="utf-8")

    with pytest.raises(ValueError, match="ORCA NMR shielding section"):
        _ = parse_orca_nmr_log(log_file)
