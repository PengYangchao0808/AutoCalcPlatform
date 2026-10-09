"""Frequency-source loading and validation tests (plan §4.3, §7.3)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from acp.calculations.tsmode.contracts import (
    FREQUENCY_SOURCE_INCOMPLETE,
    HESSIAN_MISSING,
    SOURCE_GEOMETRY_MISMATCH,
    TsmodeError,
)
from acp.calculations.tsmode.source import (
    build_external_subspace,
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

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "qc" / "orca61"
# sha256 of tests/fixtures/qc/orca61/ts_opt.hess (README, frozen 2026-10-06).
TS_OPT_HESS_SHA256 = "7dd266577567c38bb2419aa5293a10cfe0e77d99ab8acdf4eaf9510874501334"

# Directly constructed controlled LINEAR HCN (H–C≡N along x, Å) — never
# generated through tests/tsmode_synthetic.py, whose fixtures are built with
# the same build_external_subspace under test (self-masking).
LINEAR_HCN_COORDS = np.array([[0.0, 0.0, 0.0], [1.06, 0.0, 0.0], [2.20, 0.0, 0.0]])
LINEAR_HCN_MASSES = np.array([1.008, 12.011, 14.007])
# Nonlinear control: water (bent, Å).
WATER_COORDS = np.array([[0.0, 0.0, 0.0], [0.7572, 0.5864, 0.0], [-0.7572, 0.5864, 0.0]])
WATER_MASSES = np.array([15.999, 1.008, 1.008])


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
        bundle = load_bundle_from_files(out_path, hess_path, charge=2, multiplicity=3)
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


class TestExternalSubspaceRank:
    def test_linear_hcn_keeps_five_columns(self):
        external = build_external_subspace(LINEAR_HCN_COORDS, np.sqrt(LINEAR_HCN_MASSES))
        assert external.shape == (9, 5)
        gram = external.T @ external
        assert np.allclose(gram, np.eye(5), atol=1e-12)

    def test_water_keeps_six_columns(self):
        external = build_external_subspace(WATER_COORDS, np.sqrt(WATER_MASSES))
        assert external.shape == (9, 6)
        gram = external.T @ external
        assert np.allclose(gram, np.eye(6), atol=1e-12)

    def test_linear_zero_mode_removal_is_five(self):
        # Any SPD Cartesian Hessian: after projection only the projector
        # kernel is near-zero, so the removal count equals the external rank.
        modes = compute_hessian_modes(np.eye(9) * 0.1, LINEAR_HCN_MASSES, LINEAR_HCN_COORDS)
        assert modes.n_zero_modes_removed == 5
        assert len(modes.frequencies_cm1) == 9 - 5

    def test_nonlinear_zero_mode_removal_is_six(self):
        modes = compute_hessian_modes(np.eye(9) * 0.1, WATER_MASSES, WATER_COORDS)
        assert modes.n_zero_modes_removed == 6
        assert len(modes.frequencies_cm1) == 9 - 6


def _perturb_last_output_geometry(text: str, delta_angstrom: float) -> str:
    lines = text.splitlines()
    header = max(
        index for index, line in enumerate(lines) if "CARTESIAN COORDINATES (ANGSTROEM)" in line
    )
    for index in range(header + 1, len(lines)):
        tokens = lines[index].split()
        if len(tokens) < 4:
            continue
        try:
            float(tokens[-1])
            float(tokens[-2])
            float(tokens[-3])
        except ValueError:
            continue
        # Both ORCA row forms ("0 C x y z" and "C x y z") keep y at -2.
        tokens[-2] = f"{float(tokens[-2]) + delta_angstrom:.10f}"
        lines[index] = " ".join(tokens)
        return "\n".join(lines) + "\n"
    raise AssertionError("no cartesian geometry row found after header")


class TestRealTsOptFixture:
    """(c) re-verification against the frozen ORCA 6.1.1 bundle (T02)."""

    def _load(self, tmp_path, *, out_text=None, with_geometry=True):
        out_path = tmp_path / "ts_opt.out"
        if out_text is None:
            out_text = (FIXTURE_DIR / "ts_opt.out").read_text(encoding="utf-8", errors="replace")
        out_path.write_text(out_text, encoding="utf-8")
        return load_bundle_from_files(
            out_path,
            FIXTURE_DIR / "ts_opt.hess",
            geometry_path=FIXTURE_DIR / "ts_opt.xyz" if with_geometry else None,
        )

    def test_bohr_units_atom_order_and_xyz_rmsd(self, tmp_path):
        bundle = self._load(tmp_path)
        # Atom ordering: Hessian $atoms order == output order == xyz order
        # (a mismatch raises during load; assert the resolved order itself).
        assert bundle.elements == ["C", "N", "H"]
        # Unit discrimination stays on the documented Bohr interpretation —
        # no fallback warning, and the Å rendering matches the frozen xyz.
        assert not [w for w in bundle.warnings if "unit" in w]
        coords = np.asarray(bundle.coordinates_angstrom, dtype=np.float64)
        xyz = np.loadtxt(FIXTURE_DIR / "ts_opt.xyz", skiprows=2, usecols=(1, 2, 3))
        # Literal 0.05 Å gate (NOT the module constant): a relaxation of
        # _GEOMETRY_RMSD_TOLERANCE_A must not silently relax this assertion.
        assert kabsch_rmsd(coords, xyz) < 0.05
        # Bohr→Å conversion applied: raw-Bohr numbers interpreted as Å would
        # not match the xyz at the 0.05 Å gate.
        assert coords.max() > 1.0
        assert bundle.masses_amu[0] == pytest.approx(12.0, rel=0.01)
        assert bundle.hessian_sha256 == TS_OPT_HESS_SHA256

    def test_zero_mode_index_rejected(self, tmp_path):
        from acp.calculations.tsmode.contracts import TARGET_MODE_INVALID
        from acp.calculations.tsmode.mode_mapping import resolve_target_mode

        bundle = self._load(tmp_path)
        with pytest.raises(TsmodeError) as excinfo:
            resolve_target_mode(bundle, 0)
        assert excinfo.value.error_code == TARGET_MODE_INVALID

    def test_half_angstrom_perturbation_rejected(self, tmp_path):
        text = (FIXTURE_DIR / "ts_opt.out").read_text(encoding="utf-8", errors="replace")
        perturbed = _perturb_last_output_geometry(text, 0.5)
        with pytest.raises(TsmodeError) as excinfo:
            self._load(tmp_path, out_text=perturbed)
        assert excinfo.value.error_code == SOURCE_GEOMETRY_MISMATCH


class TestSnapshot:
    def test_snapshot_roundtrip_and_hash_verification(self, tmp_path, consistent_pair):
        out_path, hess_path, _c, _f, _m = consistent_pair
        bundle = load_bundle_from_files(out_path, hess_path)
        snapshot_dir = tmp_path / "INPUT" / "tsmode"
        files = snapshot_bundle_files(bundle, snapshot_dir)
        assert files["hessian"].name == "source.hess"
        # Raw-byte staging: snapshot copy is byte-identical to the source.
        assert files["hessian"].read_bytes() == Path(bundle.hessian_file).read_bytes()
        assert (snapshot_dir / "source.xyz").is_file()
        assert (snapshot_dir / "source_modes.json").is_file()
        assert (snapshot_dir / "source_bundle.json").is_file()
        verify_snapshot_hashes(snapshot_dir, bundle)

        (files["hessian"]).write_text("corrupted", encoding="utf-8")
        with pytest.raises(TsmodeError) as excinfo:
            verify_snapshot_hashes(snapshot_dir, bundle)
        assert excinfo.value.error_code == FREQUENCY_SOURCE_INCOMPLETE
