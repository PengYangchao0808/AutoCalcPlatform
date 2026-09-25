"""Mode-mapping tests: the three-ID model and the launch gate (plan §6)."""

from __future__ import annotations

import numpy as np
import pytest

from acp.calculations.tsmode import contracts as tsmode_contracts
from acp.calculations.tsmode.contracts import (
    MODE_MAPPING_AMBIGUOUS,
    MODE_MAPPING_UNSUPPORTED,
    TARGET_MODE_INVALID,
    TsmodeError,
)
from acp.calculations.tsmode.mode_mapping import (
    MAPPING_VERSION,
    TS_MODE_MAPPING_VERIFIED_VERSIONS,
    enforce_launch_gate,
    resolve_target_mode,
)
from acp.calculations.tsmode.source import load_bundle_from_files
from tests.tsmode_synthetic import (
    eigenvalue_ascending_order,
    make_consistent_pair,
    write_out_file,
)

DEFAULT_FREQS = [-520.4, -180.2, 210.5, 480.2, 950.0, 1250.3, 1700.5, 3100.7, 3550.1]


@pytest.fixture()
def bundle(tmp_path):
    out_path, hess_path, _c, _f, _m = make_consistent_pair(tmp_path)
    return load_bundle_from_files(out_path, hess_path)


def _imaginary_index(freqs_desc: list[float], target: float) -> int:
    return freqs_desc.index(target)


class TestResolveTarget:
    def test_resolved_maps_by_ascending_eigenvalue_rank(self, bundle):
        rank = eigenvalue_ascending_order(sorted(DEFAULT_FREQS, reverse=True))
        imaginary = bundle.imaginary_modes()
        assert len(imaginary) == 2
        for mode in imaginary:
            resolution = resolve_target_mode(bundle, mode.source_mode_index)
            assert resolution.status == "resolved"
            assert resolution.optimizer_mode_index == rank[mode.source_mode_index]
            assert resolution.evidence["best_overlap"] > 0.999
            assert resolution.evidence["verified_against_orca"] is None
            assert resolution.mapping_version == MAPPING_VERSION

    def test_second_imaginary_not_zero(self, bundle):
        modes = bundle.imaginary_modes()
        second = max(modes, key=lambda m: m.frequency_cm1)  # -180.2
        resolution = resolve_target_mode(bundle, second.source_mode_index)
        assert resolution.optimizer_mode_index == 1
        assert resolution.status == "resolved"

    def test_target_mode_id_binds_files_and_vector(self, bundle):
        mode = bundle.imaginary_modes()[0]
        first = resolve_target_mode(bundle, mode.source_mode_index)
        second = resolve_target_mode(bundle, mode.source_mode_index)
        assert first.target_mode_id == second.target_mode_id
        assert first.target_mode_id.startswith("tm_")

    def test_sign_flip_same_target(self, bundle):
        mode = bundle.mode_by_index(bundle.imaginary_modes()[0].source_mode_index)
        original = resolve_target_mode(bundle, mode.source_mode_index)
        flipped_id = tsmode_contracts.compute_target_mode_id(
            bundle.hessian_sha256,
            mode.source_mode_index,
            [[-v for v in row] for row in mode.vectors],
        )
        assert flipped_id == original.target_mode_id

    def test_positive_mode_rejected(self, bundle):
        positive = [m for m in bundle.modes if not m.is_imaginary][0]
        with pytest.raises(TsmodeError) as excinfo:
            resolve_target_mode(bundle, positive.source_mode_index)
        assert excinfo.value.error_code == TARGET_MODE_INVALID

    def test_bool_rejected(self, bundle):
        with pytest.raises(TsmodeError) as excinfo:
            resolve_target_mode(bundle, True)
        assert excinfo.value.error_code == TARGET_MODE_INVALID

    def test_negative_rejected(self, bundle):
        with pytest.raises(TsmodeError):
            resolve_target_mode(bundle, -1)

    def test_out_of_bounds_rejected(self, bundle):
        with pytest.raises(TsmodeError) as excinfo:
            resolve_target_mode(bundle, 999)
        assert excinfo.value.error_code == TARGET_MODE_INVALID

    def test_near_degenerate_ambiguous(self, tmp_path):
        freqs = sorted(
            [-180.0, -180.0, 300.0, 500, 900, 1400, 2200, 3000, 3400],
            reverse=True,
        )
        out_path, hess_path, coords, freq_map, mode_map = make_consistent_pair(
            tmp_path, frequencies_cm1=freqs, seed=13, mode_seed=21
        )
        bundle = load_bundle_from_files(out_path, hess_path)
        target = bundle.imaginary_modes()[0]
        resolution = resolve_target_mode(bundle, target.source_mode_index)
        assert resolution.status == "ambiguous"
        assert resolution.optimizer_mode_index is None

    def test_mismatch_when_vector_unrelated(self, tmp_path):
        out_path, hess_path, coords, freq_map, mode_map = make_consistent_pair(tmp_path)
        rng = np.random.default_rng(5)
        modes = np.asarray(list(mode_map.values()))
        # Equal random mix of many modes: no single eigenvector and no
        # two-mode degenerate subspace can explain the printed vector.
        mixed = rng.normal(size=modes.shape[1:])
        corrupted = {
            k: (mixed if k == max(freq_map) else np.asarray(v))
            for k, v in mode_map.items()
        }
        corrupt_out = tmp_path / "corrupt.out"
        write_out_file(corrupt_out, coords, freq_map, corrupted)
        bundle = load_bundle_from_files(corrupt_out, hess_path)
        corrupted_index = max(freq_map)
        resolution = resolve_target_mode(bundle, corrupted_index)
        assert resolution.status in {"mismatch", "ambiguous"}
        if resolution.status == "mismatch":
            assert resolution.evidence["best_overlap"] < 0.95


class TestLaunchGate:
    def test_launch_gate_ambiguous_degenerate(self, tmp_path):
        freqs = sorted(
            [-180.0, -180.0, 300.0, 500, 900, 1400, 2200, 3000, 3400],
            reverse=True,
        )
        out_path, hess_path, _c, _f, _m = make_consistent_pair(
            tmp_path, frequencies_cm1=freqs, seed=13, mode_seed=21
        )
        bundle = load_bundle_from_files(out_path, hess_path)
        resolution = resolve_target_mode(
            bundle, bundle.imaginary_modes()[0].source_mode_index
        )
        with pytest.raises(TsmodeError) as excinfo:
            enforce_launch_gate(resolution, require_verified=True, orca_version="6.0.1")
        assert excinfo.value.error_code == MODE_MAPPING_AMBIGUOUS

    def test_unverified_version_blocked_by_default(self, bundle):
        resolution = resolve_target_mode(
            bundle, bundle.imaginary_modes()[0].source_mode_index
        )
        assert TS_MODE_MAPPING_VERIFIED_VERSIONS == frozenset()
        with pytest.raises(TsmodeError) as excinfo:
            enforce_launch_gate(resolution, require_verified=True, orca_version="6.0.1")
        assert excinfo.value.error_code == MODE_MAPPING_UNSUPPORTED
        assert "P0" in excinfo.value.detail or "verified" in excinfo.value.detail

    def test_optout_allows_unverified(self, bundle):
        resolution = resolve_target_mode(
            bundle, bundle.imaginary_modes()[0].source_mode_index
        )
        enforce_launch_gate(resolution, require_verified=False, orca_version=None)

    def test_verified_version_matrix_allows(self, bundle, monkeypatch):
        monkeypatch.setattr(
            "acp.calculations.tsmode.mode_mapping.TS_MODE_MAPPING_VERIFIED_VERSIONS",
            frozenset({"6.0"}),
        )
        resolution = resolve_target_mode(
            bundle,
            bundle.imaginary_modes()[0].source_mode_index,
            orca_version="6.0.1",
        )
        assert resolution.evidence["verified_against_orca"] == "6.0"
        enforce_launch_gate(resolution, require_verified=True, orca_version="6.0.1")
