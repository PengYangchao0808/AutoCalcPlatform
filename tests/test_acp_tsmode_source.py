"""Frequency-source loading and validation tests (plan §4.3, §7.3)."""

from __future__ import annotations

import numpy as np
import pytest

from acp.calculations.tsmode.contracts import (
    FREQUENCY_SOURCE_INCOMPLETE,
    HESSIAN_MISSING,
    SOURCE_GEOMETRY_MISMATCH,
    TsmodeError,
)
from acp.calculations.tsmode.source import (
    compute_hessian_modes,
    kabsch_rmsd,
    load_bundle_from_files,
    snapshot_bundle_files,
    verify_snapshot_hashes,
)
from cccp.qc.interfaces.hess_file import HessFileError, parse_orca_hess_file
from cccp.utils.constants import BOHR_TO_ANGSTROM
from tests.tsmode_synthetic import (
    ELEMENTS,
    MASSES,
    build_molecule,
    make_consistent_pair,
    write_hess_file,
)


@pytest.fixture()
def consistent_pair(tmp_path):
    return make_consistent_pair(tmp_path)


class TestHessFileParser:
    def test_parses_atoms_coords_hessian(self, consistent_pair):
        out_path, hess_path, coords, freqs, _modes = consistent_pair
        data = parse_orca_hess_file(hess_path)
        assert data.symbols == ELEMENTS
        assert data.n_atoms == len(ELEMENTS)
        assert data.dimension == 3 * len(ELEMENTS)
        assert data.masses_amu == pytest.approx(list(MASSES), rel=1e-6)
        recovered = np.asarray(data.coordinates_bohr) * BOHR_TO_ANGSTROM
        assert kabsch_rmsd(recovered, coords) < 1e-4

    def test_missing_hessian_section_rejected(self, tmp_path):
        path = tmp_path / "bad.hess"
        path.write_text("$atoms\n1\nH 1.008\n", encoding="utf-8")
        with pytest.raises(HessFileError, match="hessian"):
            parse_orca_hess_file(path)

    def test_dimension_mismatch_rejected(self, tmp_path):
        path = tmp_path / "bad.hess"
        path.write_text(
            "$atoms\n2\nH 1.0\nH 1.0\n$hessian\n3\n1.0 0 0 0 0 0 0 0 0\n",
            encoding="utf-8",
        )
        with pytest.raises(HessFileError, match="3\\*2"):
            parse_orca_hess_file(path)

    def test_non_finite_rejected(self, tmp_path):
        path = tmp_path / "bad.hess"
        values = " ".join(["1.0"] * 35 + ["nan"] + ["1.0"] * 100)
        path.write_text(
            "$atoms\n2\nH 1.0\nH 1.0\n$hessian\n6\n" + values + "\n",
            encoding="utf-8",
        )
        with pytest.raises(HessFileError):
            parse_orca_hess_file(path)

    def test_missing_file(self, tmp_path):
        with pytest.raises(HessFileError):
            parse_orca_hess_file(tmp_path / "absent.hess")


class TestBundleLoading:
    def test_load_consistent_pair(self, consistent_pair):
        out_path, hess_path, coords, freqs, _modes = consistent_pair
        bundle = load_bundle_from_files(out_path, hess_path)
        assert bundle.n_atoms == len(ELEMENTS)
        assert bundle.elements == ELEMENTS
        assert bundle.charge == 0
        assert bundle.multiplicity == 1
        assert bundle.level.orca_version == "6.0.1"
        assert bundle.level.method == "r2SCAN-3c"
        imaginary = bundle.imaginary_modes()
        assert len(imaginary) == 2
        assert all(mode.has_complete_vectors(bundle.n_atoms) for mode in bundle.modes)
        assert bundle.hessian_sha256
        assert bundle.geo_hash.startswith("geo_")

    def test_missing_hess_file(self, tmp_path, consistent_pair):
        out_path, _h, _c, _f, _m = consistent_pair
        with pytest.raises(TsmodeError) as excinfo:
            load_bundle_from_files(out_path, tmp_path / "absent.hess")
        assert excinfo.value.error_code == HESSIAN_MISSING

    def test_missing_output_file(self, tmp_path, consistent_pair):
        _o, hess_path, _c, _f, _m = consistent_pair
        with pytest.raises(TsmodeError) as excinfo:
            load_bundle_from_files(tmp_path / "absent.out", hess_path)
        assert excinfo.value.error_code == FREQUENCY_SOURCE_INCOMPLETE

    def test_truncated_output_rejected(self, tmp_path, consistent_pair):
        out_path, hess_path, _c, _f, _m = consistent_pair
        truncated = tmp_path / "trunc.out"
        truncated.write_text(
            out_path.read_text(encoding="utf-8").split("NORMAL MODES")[0],
            encoding="utf-8",
        )
        with pytest.raises(TsmodeError) as excinfo:
            load_bundle_from_files(truncated, hess_path)
        assert excinfo.value.error_code == FREQUENCY_SOURCE_INCOMPLETE

    def test_geometry_mismatch_rejected(self, tmp_path, consistent_pair):
        out_path, hess_path, coords, freqs, modes = consistent_pair
        # Hessian for a DIFFERENT geometry, output unchanged → mismatch.
        shifted = build_molecule(seed=99)
        from tests.tsmode_synthetic import build_modes

        hessian, _cart_modes, _ = build_modes(shifted, list(freqs.values()), seed=11)
        bad_hess = tmp_path / "shifted.hess"
        write_hess_file(bad_hess, shifted, hessian)
        with pytest.raises(TsmodeError) as excinfo:
            load_bundle_from_files(out_path, bad_hess)
        assert excinfo.value.error_code == SOURCE_GEOMETRY_MISMATCH

    def test_element_order_mismatch_rejected(self, tmp_path, consistent_pair):
        out_path, hess_path, coords, freqs, modes = consistent_pair
        text = out_path.read_text(encoding="utf-8")
        # Corrupt one element token in the coordinates section.
        lines = text.splitlines()
        for position, line in enumerate(lines):
            if line.strip().startswith("0  C"):
                lines[position] = line.replace("C", "N", 1)
                break
        corrupt = tmp_path / "corrupt.out"
        corrupt.write_text("\n".join(lines), encoding="utf-8")
        with pytest.raises(TsmodeError) as excinfo:
            load_bundle_from_files(corrupt, hess_path)
        assert excinfo.value.error_code == SOURCE_GEOMETRY_MISMATCH

    def test_rigid_transform_tolerated(self, tmp_path, consistent_pair):
        out_path, hess_path, coords, freqs, modes = consistent_pair
        angle = np.deg2rad(37.0)
        rotation = np.array(
            [
                [np.cos(angle), -np.sin(angle), 0.0],
                [np.sin(angle), np.cos(angle), 0.0],
                [0.0, 0.0, 1.0],
            ]
        )
        rotated = coords @ rotation.T + np.array([10.0, -4.0, 2.0])
        rotated_out = tmp_path / "rotated.out"
        from tests.tsmode_synthetic import write_out_file

        write_out_file(rotated_out, rotated, freqs, modes)
        bundle = load_bundle_from_files(rotated_out, hess_path)
        assert bundle.n_atoms == len(ELEMENTS)

    def test_inconsistent_frequency_crosscheck_rejected(self, tmp_path, consistent_pair):
        out_path, hess_path, coords, freqs, modes = consistent_pair
        shifted_freqs = {k: v + 400.0 for k, v in freqs.items()}
        inconsistent_out = tmp_path / "inconsistent.out"
        from tests.tsmode_synthetic import write_out_file

        write_out_file(inconsistent_out, coords, shifted_freqs, modes)
        with pytest.raises(TsmodeError) as excinfo:
            load_bundle_from_files(inconsistent_out, hess_path)
        assert excinfo.value.error_code == SOURCE_GEOMETRY_MISMATCH

    def test_explicit_charge_multiplicity_win(self, consistent_pair):
        out_path, hess_path, _c, _f, _m = consistent_pair
        bundle = load_bundle_from_files(
            out_path, hess_path, charge=2, multiplicity=3
        )
        assert bundle.charge == 2
        assert bundle.multiplicity == 3


class TestHessianModes:
    def test_reconstructs_frequencies(self, consistent_pair):
        _o, hess_path, coords, freqs, _m = consistent_pair
        data = parse_orca_hess_file(hess_path)
        modes = compute_hessian_modes(
            data.hessian, data.masses_amu, np.asarray(data.coordinates_bohr) * BOHR_TO_ANGSTROM
        )
        expected = sorted(freqs.values())
        assert list(modes.frequencies_cm1) == pytest.approx(expected, abs=1.0)
        assert modes.n_zero_modes_removed == 6

    def test_kabsch_zero_for_identical(self):
        coords = build_molecule()
        assert kabsch_rmsd(coords, coords.copy()) == pytest.approx(0.0, abs=1e-12)


class TestSnapshot:
    def test_snapshot_roundtrip_and_hash_verification(self, tmp_path, consistent_pair):
        out_path, hess_path, _c, _f, _m = consistent_pair
        bundle = load_bundle_from_files(out_path, hess_path)
        snapshot_dir = tmp_path / "INPUT" / "tsmode"
        files = snapshot_bundle_files(bundle, snapshot_dir)
        assert files["hessian"].name == "source.hess"
        assert (snapshot_dir / "source.xyz").is_file()
        assert (snapshot_dir / "source_modes.json").is_file()
        assert (snapshot_dir / "source_bundle.json").is_file()
        verify_snapshot_hashes(snapshot_dir, bundle)

        (files["hessian"]).write_text("corrupted", encoding="utf-8")
        with pytest.raises(TsmodeError) as excinfo:
            verify_snapshot_hashes(snapshot_dir, bundle)
        assert excinfo.value.error_code == FREQUENCY_SOURCE_INCOMPLETE
