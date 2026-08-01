"""Tests for the xtbmd_censo_energy workflow (Phase 3 + Phase 4).

Phase 3 covers ``_batch_opt_frames``: frame splitting, adaptive equilibration
discard (±2σ sliding-window test, fallback clamping), max_frames uniform
subsampling, per-frame working-directory concurrency (nproc=1 per frame),
per-frame timeouts, energy sidecar, success-rate fail-fast, and the two
sampling-convergence diagnostics (geometric pre-check + ISOSTAT-based formal
check with population-weighted novelty).

Phase 4 covers the ``run_xtbmd_censo_energy`` orchestration: stage order,
MD/batch-opt/isostat parameter passthrough, energy-window filter, the three
CENSO presets × dual DFT modes, ensemble total-Gibbs numerics, per-stage
resume fingerprints, empty-ensemble fail-fast and solvent consistency.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from acp.backends.base import QCResult
from acp.backends.censo_backend import CensoConformerRecord, CensoRunResult
from acp.core.models import HARTREE_TO_KCAL
from acp.workflows.energy_shared import xtb_passthrough_result
from acp.workflows.ensemble_thermo import (
    ensemble_total_gibbs_from_values,
)
from acp.workflows.xtbmd_censo_energy import (
    BatchOptResult,
    _batch_opt_frames,
    _equilibration_cutoff,
    _filter_energy_window,
    _read_trajectory,
    _uniform_subsample_indices,
    run_xtbmd_censo_energy,
)
from cccp.utils.file_io import read_xyz_multiframe

_H2 = [
    "H 0.0000000000 0.0000000000 0.0000000000",
    "H 0.0000000000 0.0000000000 0.7400000000",
]


def _write_blocks(path: Path, blocks: list[list[str]]) -> Path:
    path.write_text("\n".join(line for block in blocks for line in block), encoding="utf-8")
    return path


def _frame_block(bond: float, energy: float, time: float) -> list[str]:
    return [
        "2",
        f"md: {time:.8f} {energy:.6f} (kcal/mol) {energy:.6f} (kcal/mol)",
        "H 0.0000000000 0.0000000000 0.0000000000",
        f"H 0.0000000000 0.0000000000 {bond:.10f}",
        "",
    ]


def _write_traj(
    path: Path,
    n_frames: int,
    bond: float = 0.74,
    energy: float | None = -10.0,
) -> Path:
    blocks: list[str] = []
    for i in range(n_frames):
        if energy is None:
            blocks.extend(
                [
                    "2",
                    f"Frame {i}",
                    "H 0.0000000000 0.0000000000 0.0000000000",
                    f"H 0.0000000000 0.0000000000 {bond:.10f}",
                    "",
                ]
            )
        else:
            blocks.extend(_frame_block(bond, energy, float(i)))
    path.write_text("\n".join(blocks), encoding="utf-8")
    return path


def _ok_result(bond: float, energy: float) -> QCResult:
    return QCResult(
        success=True,
        converged=True,
        coordinates=np.asarray(
            [
                [0.0, 0.0, 0.0],
                [0.0, 0.0, bond],
            ],
            dtype=np.float64,
        ),
        energy=energy,
    )


class _BackendHarness:
    """Records constructor kwargs and lets tests script per-frame results."""

    def __init__(self, results: dict[int, QCResult] | None = None) -> None:
        self.xtb_factory_kwargs: list[dict[str, Any]] = []
        self.xtb_calls: list[dict[str, Any]] = []
        self.isostat_kwargs: list[dict[str, Any]] = []
        self.isostat_fail: bool = False
        self.results = results or {}
        self.raise_frames: set[int] = set()

    def _xtb_factory(self, config: dict[str, object], **kwargs: object) -> MagicMock:
        self.xtb_factory_kwargs.append(dict(kwargs))
        backend = MagicMock()
        backend.optimize.side_effect = self._optimize
        return backend

    def _optimize(
        self,
        coordinates: np.ndarray,
        symbols: list[str],
        *,
        output_dir: Path,
        **kwargs: object,
    ) -> QCResult:
        self.xtb_calls.append(
            {"coordinates": coordinates, "output_dir": Path(output_dir), **kwargs}
        )
        frame_index = int(Path(output_dir).name.split("_")[1])
        if frame_index in self.raise_frames:
            raise RuntimeError(f"frame {frame_index} backend explosion")
        if frame_index in self.results:
            return self.results[frame_index]
        bond = float(coordinates[1, 2])
        return _ok_result(bond, -100.0)

    def _isostat_factory(self, config: dict[str, object], **kwargs: object) -> MagicMock:
        backend = MagicMock()
        backend.cluster.side_effect = self._cluster
        return backend

    def _cluster(
        self,
        ensemble_xyz: Path,
        output_dir: Path | None = None,
        **kwargs: object,
    ) -> QCResult:
        self.isostat_kwargs.append({"input": ensemble_xyz, "output_dir": output_dir, **kwargs})
        target = Path(output_dir or ensemble_xyz.parent)
        target.mkdir(parents=True, exist_ok=True)
        if self.isostat_fail:
            return QCResult(success=False, error_message="isostat boom")
        # Representative set = first and last frame of the input half.
        _, _, frames = _read_trajectory(ensemble_xyz)
        block = []
        for frame in (frames[0], frames[-1]):
            bond = float(frame[1, 2])
            block.extend(
                [
                    "2",
                    f"rep {bond}",
                    "H 0.0000000000 0.0000000000 0.0000000000",
                    f"H 0.0000000000 0.0000000000 {bond:.10f}",
                    "",
                ]
            )
        (target / "cluster.xyz").write_text("\n".join(block), encoding="utf-8")
        return QCResult(success=True, converged=True, output_file=target / "cluster.xyz")

    def factory(self, name: str) -> Any:
        if name == "xtb":
            return self._xtb_factory
        if name == "isostat":
            return self._isostat_factory
        raise KeyError(name)


def _run(
    traj: Path,
    tmp_path: Path,
    harness: _BackendHarness,
    **kwargs: object,
) -> BatchOptResult:
    with patch("acp.workflows.xtbmd_censo_energy.get_backend", side_effect=harness.factory):
        return _batch_opt_frames(traj, work_dir=tmp_path / "work", cfg={}, **kwargs)


# ---------------------------------------------------------------------------
# Frame splitting / equilibration / subsampling
# ---------------------------------------------------------------------------


def test_parses_trajectory_and_reports_frame_counts(tmp_path: Path) -> None:
    traj = _write_traj(tmp_path / "traj.xyz", 20)
    harness = _BackendHarness()
    result = _run(traj, tmp_path, harness)

    assert result.n_frames_raw == 20
    assert result.n_discarded_equilibration == 2  # fallback 10% (20 < 2 windows)
    assert result.n_frames == 18
    assert result.n_ok == 18
    assert result.n_failed == 0
    assert result.n_timeout == 0

    coords, symbols = read_xyz_multiframe(result.isomers_xyz)
    assert symbols == ["H", "H"]
    assert coords.shape[0] // 2 == 18


def test_equilibration_statistical_test_drops_drifting_prefix(tmp_path: Path) -> None:
    traj = tmp_path / "traj.xyz"
    blocks = [_frame_block(0.74, -10.0, float(i)) for i in range(100)]
    blocks += [_frame_block(0.74, -8.0, float(100 + i)) for i in range(100)]
    _write_blocks(traj, blocks)

    energies = [-10.0 if i < 100 else -8.0 for i in range(200)]
    assert _equilibration_cutoff(energies) == 40  # 0.20 clamp of the drift window

    harness = _BackendHarness()
    result = _run(traj, tmp_path, harness)
    assert result.n_discarded_equilibration == 40
    assert result.n_frames == 160


def test_equilibration_stable_trajectory_drops_minimum(tmp_path: Path) -> None:
    traj = _write_traj(tmp_path / "traj.xyz", 200)
    harness = _BackendHarness()
    result = _run(traj, tmp_path, harness)

    assert result.n_discarded_equilibration == 10  # min_frac 5%
    assert result.n_frames == 190


def test_equilibration_fallback_without_energies(tmp_path: Path) -> None:
    traj = _write_traj(tmp_path / "traj.xyz", 20, energy=None)
    harness = _BackendHarness()
    result = _run(traj, tmp_path, harness)

    assert result.n_discarded_equilibration == 2  # fallback 10%


def test_per_replica_equilibration(tmp_path: Path) -> None:
    traj = tmp_path / "traj.xyz"
    blocks = [_frame_block(0.74, -10.0, float(i)) for i in range(100)]
    blocks += [_frame_block(0.74, -8.0, float(100 + i)) for i in range(100)]
    blocks += [_frame_block(0.74, -10.0, float(200 + i)) for i in range(20)]
    _write_blocks(traj, blocks)

    harness = _BackendHarness()
    result = _run(traj, tmp_path, harness, replica_frames=[200, 20])

    assert result.n_frames_raw == 220
    assert result.n_discarded_equilibration == 42  # 40 (replica 0) + 2 (replica 1)
    assert result.n_frames == 178
    assert result.n_ok == 178


def test_max_frames_uniform_subsampling(tmp_path: Path) -> None:
    traj = _write_traj(tmp_path / "traj.xyz", 40)
    harness = _BackendHarness()
    result = _run(traj, tmp_path, harness, max_frames=10)

    assert result.n_frames == 10
    assert result.n_ok == 10
    coords, _ = read_xyz_multiframe(result.isomers_xyz)
    assert coords.shape[0] // 2 == 10

    sidecar = json.loads(result.isomers_energies_json.read_text(encoding="utf-8"))
    source_frames = [entry["source_frame"] for entry in sidecar["frames"]]
    assert source_frames[0] == 4  # 40 frames → 10% fallback equilibration drop
    assert source_frames == sorted(source_frames)


def test_max_frames_zero_is_unlimited(tmp_path: Path) -> None:
    traj = _write_traj(tmp_path / "traj.xyz", 20)
    harness = _BackendHarness()
    result = _run(traj, tmp_path, harness, max_frames=0)

    assert result.n_frames == 18  # only equilibration discard
    assert result.n_ok == 18


def test_uniform_subsample_indices_preserves_ends() -> None:
    indices = _uniform_subsample_indices(100, 10)
    assert len(indices) == 10
    assert indices[0] == 0
    assert indices[-1] == 99
    assert list(indices) == sorted(indices)

    assert list(_uniform_subsample_indices(5, 0)) == [0, 1, 2, 3, 4]
    assert list(_uniform_subsample_indices(5, 10)) == [0, 1, 2, 3, 4]


# ---------------------------------------------------------------------------
# Concurrency contract / per-frame handling
# ---------------------------------------------------------------------------


def test_per_frame_working_dirs_and_single_process(tmp_path: Path) -> None:
    traj = _write_traj(tmp_path / "traj.xyz", 20)
    harness = _BackendHarness()
    result = _run(traj, tmp_path, harness, nproc=4, solvent="water", solvent_model="alpb")

    assert len(harness.xtb_calls) == 18
    dirs = [call["output_dir"] for call in harness.xtb_calls]
    assert len({str(d) for d in dirs}) == 18, "per-frame working dirs must be unique"
    for directory in dirs:
        assert directory.parent == result.isomers_xyz.parent
        assert re.fullmatch(r"frame_\d{4}", directory.name)
    assert not any(d.exists() for d in dirs), "frame dirs cleaned up by default"

    for kwargs in harness.xtb_factory_kwargs:
        assert kwargs["nproc"] == 1
        assert kwargs["gfn_level"] == 1
        assert kwargs["solvent"] == "water"
        assert kwargs["solvent_model"] == "alpb"


def test_keep_frames_retains_working_dirs(tmp_path: Path) -> None:
    traj = _write_traj(tmp_path / "traj.xyz", 12)
    harness = _BackendHarness()
    _run(traj, tmp_path, harness, keep_frames=True, max_frames=4)

    dirs = [call["output_dir"] for call in harness.xtb_calls]
    assert all(d.exists() for d in dirs)


def test_charge_mult_opt_level_timeout_passthrough(tmp_path: Path) -> None:
    traj = _write_traj(tmp_path / "traj.xyz", 12)
    harness = _BackendHarness()
    _run(
        traj,
        tmp_path,
        harness,
        charge=2,
        multiplicity=3,
        opt_level="tight",
        opt_timeout=120,
    )

    call = harness.xtb_calls[0]
    assert call["charge"] == 2
    assert call["multiplicity"] == 3
    assert call["opt_level"] == "tight"
    assert call["timeout"] == 120


def test_opt_timeout_zero_passes_none(tmp_path: Path) -> None:
    traj = _write_traj(tmp_path / "traj.xyz", 12)
    harness = _BackendHarness()
    _run(traj, tmp_path, harness, opt_timeout=0)

    assert harness.xtb_calls[0]["timeout"] is None


def test_failed_frames_skipped_and_counted(tmp_path: Path) -> None:
    traj = _write_traj(tmp_path / "traj.xyz", 30)
    harness = _BackendHarness()
    harness.results = {
        5: QCResult(success=False, error_message="boom"),
        7: QCResult(success=False, error_message="nope"),
    }
    result = _run(traj, tmp_path, harness)

    assert result.n_frames == 27  # 30 − 3 fallback equilibration
    assert result.n_ok == 25
    assert result.n_failed == 2
    assert result.n_timeout == 0

    sidecar = json.loads(result.isomers_energies_json.read_text(encoding="utf-8"))
    statuses = {entry["frame"]: entry["status"] for entry in sidecar["frames"]}
    assert statuses[5] == "failed"
    assert statuses[7] == "failed"
    assert sum(1 for s in statuses.values() if s == "ok") == 25

    coords, _ = read_xyz_multiframe(result.isomers_xyz)
    assert coords.shape[0] // 2 == 25


def test_timeout_frames_counted_without_blocking(tmp_path: Path) -> None:
    traj = _write_traj(tmp_path / "traj.xyz", 30)
    harness = _BackendHarness()
    harness.results = {4: QCResult(success=False, error_message="xtb frame timed out after 300 s")}
    result = _run(traj, tmp_path, harness)

    assert result.n_failed == 1
    assert result.n_timeout == 1
    assert result.n_ok == 26
    sidecar = json.loads(result.isomers_energies_json.read_text(encoding="utf-8"))
    assert sidecar["frames"][4]["status"] == "timeout"
    assert sidecar["frames"][4]["energy"] is None


def test_success_without_energy_counts_failed(tmp_path: Path) -> None:
    traj = _write_traj(tmp_path / "traj.xyz", 12)
    harness = _BackendHarness()
    harness.results = {
        0: QCResult(success=True, converged=True, coordinates=np.zeros((2, 3)), energy=None)
    }
    result = _run(traj, tmp_path, harness)

    assert result.n_failed == 1
    assert result.n_ok == 10
    sidecar = json.loads(result.isomers_energies_json.read_text(encoding="utf-8"))
    assert sidecar["frames"][0]["status"] == "failed"


def test_success_rate_below_threshold_fails_fast(tmp_path: Path) -> None:
    traj = _write_traj(tmp_path / "traj.xyz", 10)
    harness = _BackendHarness()
    harness.results = {i: QCResult(success=False, error_message="boom") for i in range(9)}

    with pytest.raises(RuntimeError, match="fail-fast"):
        _run(traj, tmp_path, harness)

    # Frame directories are kept on fail-fast for debugging.
    assert any((tmp_path / "work").glob("frame_*"))


def test_sidecar_energies_match_isomers_titles(tmp_path: Path) -> None:
    traj = _write_traj(tmp_path / "traj.xyz", 20)
    harness = _BackendHarness()
    result = _run(traj, tmp_path, harness)

    sidecar = json.loads(result.isomers_energies_json.read_text(encoding="utf-8"))
    ok_energies = [e["energy"] for e in sidecar["frames"] if e["status"] == "ok"]

    title_energies = [
        float(match.group(1))
        for match in re.finditer(
            r"Energy: (-?\d+\.\d+)",
            result.isomers_xyz.read_text(encoding="utf-8"),
        )
    ]
    assert len(title_energies) == len(ok_energies)
    for title_energy, sidecar_energy in zip(title_energies, ok_energies):
        assert title_energy == pytest.approx(sidecar_energy, abs=1e-9)


# ---------------------------------------------------------------------------
# Sampling-convergence diagnostics
# ---------------------------------------------------------------------------


def test_geometric_precheck_warns_on_diverse_halves(tmp_path: Path) -> None:
    traj = tmp_path / "traj.xyz"
    blocks = [_frame_block(0.74, -10.0, float(i)) for i in range(10)]
    blocks += [_frame_block(2.5, -10.0, float(10 + i)) for i in range(10)]
    _write_blocks(traj, blocks)

    harness = _BackendHarness()
    result = _run(traj, tmp_path, harness)

    assert any("pre-check" in warning for warning in result.precheck_warnings)


def test_geometric_precheck_silent_on_homogeneous_trajectory(tmp_path: Path) -> None:
    traj = _write_traj(tmp_path / "traj.xyz", 20)
    harness = _BackendHarness()
    result = _run(traj, tmp_path, harness)

    assert result.precheck_warnings == []


def test_conv_check_novel_second_half_fails(tmp_path: Path) -> None:
    traj = tmp_path / "traj.xyz"
    blocks = [_frame_block(0.74, -10.0, float(i)) for i in range(14)]
    blocks += [_frame_block(2.5, -10.0, float(14 + i)) for i in range(6)]
    _write_blocks(traj, blocks)

    harness = _BackendHarness()
    result = _run(traj, tmp_path, harness)

    assert result.conv_passed is False
    assert result.conv_novelty_rate == pytest.approx(6 / 9, abs=0.01)
    assert any("novel" in note for note in result.conv_notes)

    # ISOSTAT got the production thresholds + per-frame threads.
    assert harness.isostat_kwargs[0]["edis"] == 0.5
    assert harness.isostat_kwargs[0]["gdis"] == 0.25
    assert harness.isostat_kwargs[0]["nthreads"] == 1
    assert harness.isostat_kwargs[0]["temperature"] == 298.15


def test_conv_check_matching_halves_passes(tmp_path: Path) -> None:
    traj = _write_traj(tmp_path / "traj.xyz", 20)
    harness = _BackendHarness()
    result = _run(traj, tmp_path, harness)

    assert result.conv_passed is True
    assert result.conv_novelty_rate == pytest.approx(0.0)


def test_conv_check_uses_conv_rmsd_not_gdis(tmp_path: Path) -> None:
    traj = tmp_path / "traj.xyz"
    blocks = [_frame_block(0.74, -10.0, float(i)) for i in range(14)]
    blocks += [_frame_block(2.5, -10.0, float(14 + i)) for i in range(6)]
    _write_blocks(traj, blocks)

    harness = _BackendHarness()
    result = _run(traj, tmp_path, harness, conv_rmsd=2.0)

    assert result.conv_passed is True
    assert result.conv_novelty_rate == pytest.approx(0.0)


def test_conv_check_disabled_skips_diagnostics(tmp_path: Path) -> None:
    traj = tmp_path / "traj.xyz"
    blocks = [_frame_block(0.74, -10.0, float(i)) for i in range(10)]
    blocks += [_frame_block(2.5, -10.0, float(10 + i)) for i in range(10)]
    _write_blocks(traj, blocks)

    harness = _BackendHarness()
    result = _run(traj, tmp_path, harness, conv_check=False)

    assert result.conv_passed is None
    assert result.conv_novelty_rate is None
    assert result.precheck_warnings == []
    assert harness.isostat_kwargs == []


def test_conv_check_isostat_failure_degrades_gracefully(tmp_path: Path) -> None:
    traj = _write_traj(tmp_path / "traj.xyz", 20)
    harness = _BackendHarness()
    harness.isostat_fail = True
    result = _run(traj, tmp_path, harness)

    assert result.conv_passed is None
    assert result.conv_novelty_rate is None
    assert any("could not run" in note for note in result.conv_notes)


def test_conv_check_per_replica_grouping(tmp_path: Path) -> None:
    traj = tmp_path / "traj.xyz"
    blocks = [_frame_block(0.74, -10.0, float(i)) for i in range(60)]
    blocks += [_frame_block(2.5, -10.0, float(60 + i)) for i in range(20)]
    _write_blocks(traj, blocks)

    # Per-replica halves: replica 1's 2.5 Å conformer is seen in its own first
    # half, so the second-half group adds nothing novel.
    harness = _BackendHarness()
    result = _run(traj, tmp_path, harness, replica_frames=[60, 20])
    assert result.conv_passed is True
    assert result.conv_novelty_rate == pytest.approx(0.0)

    # Without replica boundaries the same trajectory splits globally: the
    # 2.5 Å conformer (frames 60–79) lands entirely in the second half → novel.
    harness2 = _BackendHarness()
    result2 = _run(traj, tmp_path / "out2", harness2)
    assert result2.conv_passed is False
    assert result2.conv_novelty_rate == pytest.approx(20 / 36, abs=0.01)


# ---------------------------------------------------------------------------
# Trajectory parsing robustness
# ---------------------------------------------------------------------------


def test_trajectory_parsing_errors(tmp_path: Path) -> None:
    truncated = tmp_path / "truncated.xyz"
    truncated.write_text(
        "2\nmd: 0.0 -10.0 (kcal/mol) -10.0 (kcal/mol)\nH 0 0 0\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="Truncated"):
        _read_trajectory(truncated)

    mismatch = tmp_path / "mismatch.xyz"
    mismatch.write_text(
        "2\nFrame 0\nH 0 0 0\nH 0 0 0.7\n3\nFrame 1\nO 0 0 0\nH 0 0 0.9\nH 0 0 -0.9\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Atom count inconsistency"):
        _read_trajectory(mismatch)

    empty = tmp_path / "empty.xyz"
    empty.write_text("0\n\n", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        _read_trajectory(empty)

    garbage = tmp_path / "garbage.xyz"
    garbage.write_text("GARBAGE\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no frames"):
        _read_trajectory(garbage)


def test_batch_opt_rejects_missing_trajectory(tmp_path: Path) -> None:
    harness = _BackendHarness()
    with pytest.raises(FileNotFoundError):
        _run(tmp_path / "nope.xyz", tmp_path, harness)


# ---------------------------------------------------------------------------
# Audit fixes (v1.4)
# ---------------------------------------------------------------------------


def test_backend_exception_isolated_per_frame(tmp_path: Path) -> None:
    traj = _write_traj(tmp_path / "traj.xyz", 30)
    harness = _BackendHarness()
    harness.raise_frames = {3, 11}
    result = _run(traj, tmp_path, harness)

    assert result.n_failed == 2
    assert result.n_ok == 25
    sidecar = json.loads(result.isomers_energies_json.read_text(encoding="utf-8"))
    statuses = {entry["frame"]: entry["status"] for entry in sidecar["frames"]}
    assert statuses[3] == "failed"
    assert statuses[11] == "failed"
    assert all(entry["energy"] is None for entry in sidecar["frames"] if entry["status"] != "ok")


def test_batch_opt_rejects_invalid_opt_level_and_gfn(tmp_path: Path) -> None:
    traj = _write_traj(tmp_path / "traj.xyz", 12)
    harness = _BackendHarness()
    with pytest.raises(ValueError, match="opt_level"):
        _run(traj, tmp_path, harness, opt_level="loose")
    with pytest.raises(ValueError, match="gfn_level"):
        _run(traj, tmp_path, harness, gfn_level=5)


def test_precheck_rmsd_sample_capped(tmp_path: Path) -> None:
    from acp.workflows import xtbmd_censo_energy as module

    frames = []
    for i in range(2000):
        bond = 0.74 if i < 1000 else 2.5
        frames.append(np.asarray([[0.0, 0.0, 0.0], [0.0, 0.0, bond]], dtype=np.float64))
    counted = {"n": 0}
    original = module._aligned_rmsd

    def counting(a, b):
        counted["n"] += 1
        return original(a, b)

    with patch.object(module, "_aligned_rmsd", side_effect=counting):
        module._geometric_precheck(frames, ["H", "H"], conv_rmsd=0.5, novelty_max=0.10)

    # 2000 frames → 1000 per half → 100 samples per half before the cap; the
    # cap keeps pairwise work at 100×100 = 10 000 (not 100×100... uncapped
    # would be 100×100 anyway at step 10 — the cap binds for longer runs).
    assert counted["n"] == 100 * 100


def test_precheck_uncapped_exceeds_cap_budget(tmp_path: Path) -> None:
    from acp.workflows import xtbmd_censo_energy as module

    # 4000 frames → 200 samples per half without the cap (2× the budget);
    # the cap must bring the pairwise work back to 100×100.
    frames = []
    for i in range(4000):
        bond = 0.74 if i < 2000 else 2.5
        frames.append(np.asarray([[0.0, 0.0, 0.0], [0.0, 0.0, bond]], dtype=np.float64))
    counted = {"n": 0}
    original = module._aligned_rmsd

    def counting(a, b):
        counted["n"] += 1
        return original(a, b)

    with patch.object(module, "_aligned_rmsd", side_effect=counting):
        module._geometric_precheck(frames, ["H", "H"], conv_rmsd=0.5, novelty_max=0.10)

    assert counted["n"] == 100 * 100


def test_conv_check_invalid_temperature_skips(tmp_path: Path) -> None:
    traj = _write_traj(tmp_path / "traj.xyz", 20)
    harness = _BackendHarness()
    result = _run(traj, tmp_path, harness, temperature_k=0.0)

    assert result.conv_passed is None
    assert result.conv_novelty_rate is None
    assert any("invalid temperature_k" in note for note in result.conv_notes)


# ---------------------------------------------------------------------------
# Phase 4: run_xtbmd_censo_energy orchestration
# ---------------------------------------------------------------------------


@pytest.fixture
def _wf_sample_config() -> dict[str, Any]:
    return {
        "executables": {
            "censo": {"path": "censo"},
            "orca": {"path": "orca"},
            "xtb": {"path": "xtb"},
            "isostat": {"path": "isostat"},
            "shermo": {"path": "Shermo"},
        },
        "resources": {"nproc": 4},
        "censo": {"preset": "censo-light", "temperature": 298.15},
    }


class _WorkflowHarness:
    """Mocks the sampling pipeline; produces real intermediate files."""

    def __init__(
        self,
        n_frames: int = 20,
        energies: list[float] | None = None,
        *,
        gtot_values: list[float] | None = None,
        gibbs_map: dict[str, float] | None = None,
        cluster_mode: str = "all",
    ) -> None:
        self.calls: dict[str, int] = {"md": 0, "batch": 0, "isostat": 0, "censo": 0, "handoff": 0}
        self.md_kwargs: dict[str, Any] = {}
        self.batch_kwargs: dict[str, Any] = {}
        self.isostat_kwargs: dict[str, Any] = {}
        self.censo_kwargs: dict[str, Any] = {}
        self.handoff_calls: list[dict[str, Any]] = []
        self.n_frames = n_frames
        self.energies = energies or [-100.0] * n_frames
        self.gtot_values = gtot_values or [-5.0, -4.99984]
        self.gibbs_map = gibbs_map or {"CONF1": -5.0, "CONF2": -4.99984}
        self.cluster_mode = cluster_mode

    # -- MD ---------------------------------------------------------------

    def fake_md_replicas(self, input_source: str, primary_xyz: Path, **kwargs: Any) -> QCResult:
        self.calls["md"] += 1
        self.md_kwargs = kwargs
        out = Path(kwargs["output_dir"]) / "xtbmd"
        out.mkdir(parents=True, exist_ok=True)
        traj = out / "traj.xyz"
        blocks = [_frame_block(0.74, e, float(i)) for i, e in enumerate(self.energies)]
        _write_blocks(traj, blocks)
        return QCResult(
            success=True,
            converged=True,
            output_file=traj,
            metadata={
                "trajectory_file": str(traj),
                "n_frames": len(self.energies),
                "md_seed": kwargs.get("md_seed", 42),
                "md_seeds": kwargs.get("md_seeds", 1),
                "replica_frames": [len(self.energies)],
                "start_conf_index": [0],
            },
        )

    # -- batch_opt --------------------------------------------------------

    def fake_batch_opt(self, traj_xyz: Path, **kwargs: Any) -> BatchOptResult:
        self.calls["batch"] += 1
        self.batch_kwargs = kwargs
        work = Path(kwargs["work_dir"])
        work.mkdir(parents=True, exist_ok=True)
        _, _, frames = _read_trajectory(traj_xyz)
        n = len(frames)
        block = []
        for i, frame in enumerate(frames):
            bond = float(frame[1, 2])
            block.extend(
                [
                    "2",
                    f"Energy: {self.energies[i]:.8f}",
                    "H 0.0000000000 0.0000000000 0.0000000000",
                    f"H 0.0000000000 0.0000000000 {bond:.10f}",
                    "",
                ]
            )
        isomers_xyz = work / "isomers.xyz"
        isomers_xyz.write_text("\n".join(block), encoding="utf-8")
        sidecar = work / "isomers_energies.json"
        sidecar.write_text(
            json.dumps(
                {
                    "gfn_level": 1,
                    "units": "hartree",
                    "frames": [
                        {
                            "frame": i,
                            "source_frame": i,
                            "status": "ok",
                            "energy": self.energies[i],
                        }
                        for i in range(n)
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return BatchOptResult(
            isomers_xyz=isomers_xyz,
            isomers_energies_json=sidecar,
            n_frames_raw=n,
            n_frames=n,
            n_ok=n,
            n_failed=0,
            n_timeout=0,
            n_discarded_equilibration=0,
            discarded_equilibration_fraction=0.0,
            conv_passed=True,
            conv_novelty_rate=0.0,
        )

    # -- isostat ----------------------------------------------------------

    def fake_isostat_factory(self, config: dict[str, object], **kwargs: object) -> MagicMock:
        backend = MagicMock()
        backend.cluster.side_effect = self._cluster
        return backend

    def _cluster(
        self,
        ensemble_xyz: Path,
        output_dir: Path | None = None,
        **kwargs: object,
    ) -> QCResult:
        self.calls["isostat"] += 1
        self.isostat_kwargs = {"input": ensemble_xyz, "output_dir": output_dir, **kwargs}
        target = Path(output_dir or ensemble_xyz.parent)
        target.mkdir(parents=True, exist_ok=True)
        cluster = target / "cluster.xyz"
        if self.cluster_mode == "empty":
            cluster.write_text("", encoding="utf-8")
            return QCResult(success=True, converged=True, output_file=cluster)
        _, _, frames = _read_trajectory(ensemble_xyz)
        block = []
        for frame in frames:
            bond = float(frame[1, 2])
            block.extend(
                [
                    "2",
                    f"Cluster {bond}",
                    "H 0.0000000000 0.0000000000 0.0000000000",
                    f"H 0.0000000000 0.0000000000 {bond:.10f}",
                    "",
                ]
            )
        cluster.write_text("\n".join(block), encoding="utf-8")
        return QCResult(success=True, converged=True, output_file=cluster)

    # -- CENSO ------------------------------------------------------------

    def fake_censo_factory(self, config: dict[str, object], **kwargs: object) -> MagicMock:
        backend = MagicMock()
        backend.refine_ensemble.side_effect = self._refine_ensemble
        return backend

    def _refine_ensemble(
        self,
        ensemble_xyz: Path,
        output_dir: Path,
        **kwargs: Any,
    ) -> CensoRunResult:
        self.calls["censo"] += 1
        self.censo_kwargs = {
            "ensemble_xyz": ensemble_xyz,
            "output_dir": output_dir,
            **kwargs,
        }
        _, _, frames = _read_trajectory(ensemble_xyz)
        records = []
        for i, gtot in enumerate(self.gtot_values[: len(frames)]):
            records.append(
                CensoConformerRecord(
                    conf_id=f"CONF{i + 1}",
                    frame_index=i,
                    energy=gtot + 0.08,
                    gsolv=-0.004,
                    grrho=-0.076,
                    gtot=gtot,
                    coordinates=frames[i],
                    symbols=["H", "H"],
                )
            )
        result = CensoRunResult(
            preset=str(kwargs.get("preset", "censo-light")),
            records=records,
            final_part="screening",
            work_dir=Path(output_dir),
            temperature=kwargs.get("temperature", 298.15),
        )
        result.sort_by_gtot()
        return result

    # -- handoff ----------------------------------------------------------

    def fake_handoff(
        self,
        cfg: dict[str, Any],
        coordinates: Any,
        symbols: list[str],
        charge: int,
        multiplicity: int,
        work_dir: Path,
        resolved: dict[str, Any],
        solvent: str | None,
        solvent_model: str,
        index: int = 0,
        source: str = "rank1",
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.calls["handoff"] += 1
        self.handoff_calls.append({"index": index, "source": source, **kwargs})
        gibbs = self.gibbs_map.get(source)
        if gibbs is None:
            gibbs = self.gibbs_map.get(f"CONF{index + 1}", -5.0)
        return {
            "index": index,
            "coordinates": np.asarray(coordinates),
            "symbols": list(symbols),
            "energy": -100.0,
            "gibbs": gibbs,
            "gibbs_correction": 0.0,
            "h_correction": None,
            "u_correction": None,
            "s_total": None,
            "g_conc": None,
            "source": source,
        }

    def factory(self, name: str) -> Any:
        if name == "isostat":
            return self.fake_isostat_factory
        raise KeyError(name)


def _run_workflow(
    harness: _WorkflowHarness,
    tmp_path: Path,
    config: dict[str, Any],
    **kwargs: Any,
) -> Any:
    with (
        patch(
            "acp.workflows.xtbmd_censo_energy.run_md_replicas",
            side_effect=harness.fake_md_replicas,
        ),
        patch(
            "acp.workflows.xtbmd_censo_energy._batch_opt_frames",
            side_effect=harness.fake_batch_opt,
        ),
        patch(
            "acp.workflows.xtbmd_censo_energy.get_backend",
            side_effect=harness.factory,
        ),
        patch(
            "acp.workflows.xtbmd_censo_energy.CensoBackend",
            side_effect=harness.fake_censo_factory,
        ),
        patch(
            "acp.workflows.xtbmd_censo_energy.run_rank1_handoff",
            side_effect=harness.fake_handoff,
        ),
    ):
        return run_xtbmd_censo_energy(
            input_source="CCO",
            output_dir=str(tmp_path / "out"),
            config=config,
            **kwargs,
        )


def test_workflow_stage_order_and_md_params(
    tmp_path: Path, _wf_sample_config: dict[str, Any]
) -> None:
    harness = _WorkflowHarness()
    result = _run_workflow(harness, tmp_path, _wf_sample_config, nproc=4)

    assert result.status == "completed"
    assert result.stages_completed == [
        "embed",
        "xtbmd",
        "batch_opt",
        "isostat",
        "energy_filter",
        "censo",
        "dft_handoff",
        "finalize",
    ]
    assert harness.calls == {"md": 1, "batch": 1, "isostat": 1, "censo": 1, "handoff": 2}

    # run_md_replicas received the workflow MD parameters (400/100/42 default)
    md = harness.md_kwargs
    assert md["temperature"] == pytest.approx(400.0)
    assert md["time_ps"] == pytest.approx(100.0)
    assert md["md_seed"] == 42
    assert md["md_method"] == "gfnff"
    assert md["md_seeds"] == 1
    # 100 ps → ~1 min/ps heuristic timeout
    assert md["timeout"] == 6000

    # batch_opt received the trajectory + workflow opt parameters
    batch = harness.batch_kwargs
    assert batch["gfn_level"] == 1
    assert batch["opt_level"] == "normal"
    assert batch["max_frames"] == 500
    assert batch["opt_timeout"] == 300
    assert batch["nproc"] == 4
    assert batch["conv_rmsd"] == 0.5
    assert batch["conv_novelty_max"] == 0.10

    # CENSO input is the energy-filtered ensemble
    assert harness.censo_kwargs["ensemble_xyz"] == Path(result.metadata["ensemble_xyz"])

    # finalDFT products + metadata
    mol_dir = Path(result.metadata["ensemble_thermo_json"]).parent
    assert (mol_dir / "all_conformers.xyz").exists()
    assert result.metadata["n_after_isostat"] == 20
    assert result.metadata["n_after_filter"] == 20
    assert result.metadata["n_ok"] == 20
    assert result.metadata["conv_passed"] is True


def test_workflow_parameter_passthrough(tmp_path: Path, _wf_sample_config: dict[str, Any]) -> None:
    harness = _WorkflowHarness()
    _run_workflow(
        harness,
        tmp_path,
        _wf_sample_config,
        md_temperature=500.0,
        md_time_ps=50.0,
        md_seed=7,
        md_seeds=3,
        md_method="gfn1",
        md_nvt=False,
        max_frames=10,
        opt_timeout=60,
        conv_check=True,
        conv_rmsd=1.0,
        conv_novelty_max=0.20,
        edis=0.7,
        gdis=0.4,
        ewin=8.0,
    )

    md = harness.md_kwargs
    assert md["temperature"] == pytest.approx(500.0)
    assert md["time_ps"] == pytest.approx(50.0)
    assert md["md_seeds"] == 3
    assert md["md_method"] == "gfn1"
    assert md["nvt"] is False
    assert md["timeout"] == 3600  # 50 ps heuristic, 1 h floor

    batch = harness.batch_kwargs
    assert batch["max_frames"] == 10
    assert batch["opt_timeout"] == 60
    assert batch["conv_rmsd"] == 1.0
    assert batch["conv_novelty_max"] == 0.20
    assert batch["edis"] == 0.7
    assert batch["gdis"] == 0.4

    iso = harness.isostat_kwargs
    assert iso["edis"] == 0.7
    assert iso["gdis"] == 0.4


def test_workflow_solvent_consistency(tmp_path: Path, _wf_sample_config: dict[str, Any]) -> None:
    harness = _WorkflowHarness()
    _run_workflow(harness, tmp_path, _wf_sample_config, solvent="water")

    assert harness.md_kwargs["solvent"] == "water"
    assert harness.md_kwargs["solvent_model"] == "smd"
    assert harness.batch_kwargs["solvent"] == "water"
    assert harness.batch_kwargs["solvent_model"] == "smd"
    assert harness.censo_kwargs["solvent"] == "water"
    assert harness.censo_kwargs["solvent_model"] == "smd"


def test_workflow_mode1_full_ensemble_total_gibbs(
    tmp_path: Path, _wf_sample_config: dict[str, Any]
) -> None:
    harness = _WorkflowHarness()
    result = _run_workflow(harness, tmp_path, _wf_sample_config)

    assert result.status == "completed"
    assert result.metadata["n_conformers"] == 2

    # Mode 1: full-mix formula over the fine DFT Gibbs table
    expected = ensemble_total_gibbs_from_values([-5.0, -4.99984], 298.15) * HARTREE_TO_KCAL
    assert result.metadata["total_gibbs_kcal_mol"] == pytest.approx(expected, abs=1e-6)

    thermo_json = Path(result.metadata["ensemble_thermo_json"])
    summary = json.loads(thermo_json.read_text(encoding="utf-8"))
    assert summary["method"] == "dft_table"
    assert summary["temperature_k"] == pytest.approx(298.15)
    assert summary["total_gibbs_kcal_mol"] == pytest.approx(expected, abs=1e-6)

    csv_lines = Path(result.metadata["thermo_csv"]).read_text(encoding="utf-8").strip().splitlines()
    assert csv_lines[-1].startswith("TOTAL,")

    # no mode-2 artefacts
    assert "boltzmann_table_json" not in result.metadata


def test_workflow_mode2_rank1_only(tmp_path: Path, _wf_sample_config: dict[str, Any]) -> None:
    harness = _WorkflowHarness()
    result = _run_workflow(harness, tmp_path, _wf_sample_config, rank1_only=True)

    assert result.status == "completed"
    assert harness.calls["handoff"] == 1
    assert harness.handoff_calls[0]["source"] == "CONF1"
    assert result.metadata["n_conformers"] == 1
    assert result.metadata["rank1_only"] is True

    # G_total = G1(fine) + kT·ln p1(CENSO) with the full screening table
    kt = 3.166811563e-6 * 298.15
    p1 = 1.0 / (1.0 + math.exp(-(0.00016) / kt))
    expected = (-5.0 + kt * math.log(p1)) * HARTREE_TO_KCAL
    assert result.metadata["total_gibbs_kcal_mol"] == pytest.approx(expected, abs=1e-4)

    boltzmann_table = Path(result.metadata["boltzmann_table_json"])
    assert boltzmann_table.exists()
    table = json.loads(boltzmann_table.read_text(encoding="utf-8"))
    assert table["source"] == "censo"
    assert set(table["weights"]) == {"CONF1", "CONF2"}

    summary = json.loads(Path(result.metadata["ensemble_thermo_json"]).read_text(encoding="utf-8"))
    assert summary["method"] == "censo_table_rank1"
    assert "total_gibbs_censo_hartree" in result.metadata


def test_workflow_preset_light_no_opt(tmp_path: Path, _wf_sample_config: dict[str, Any]) -> None:
    harness = _WorkflowHarness()
    result = _run_workflow(harness, tmp_path, _wf_sample_config, preset="censo-light", no_opt=True)

    assert result.status == "completed"
    # cheap path: CENSO refinement is final, no ACP handoff
    assert harness.calls["handoff"] == 0
    kwargs = harness.censo_kwargs
    assert kwargs["preset"] == "censo-light"
    assert kwargs["include_refinement"] is True
    assert kwargs.get("nconf") is None
    assert result.metadata["n_conformers"] == 2
    assert result.metadata["opt_enabled"] is False


def test_workflow_preset_zero_no_opt_preselects_frames(
    tmp_path: Path, _wf_sample_config: dict[str, Any]
) -> None:
    harness = _WorkflowHarness()
    result = _run_workflow(harness, tmp_path, _wf_sample_config, preset="censo-zero", no_opt=True)

    assert result.status == "completed"
    assert harness.calls["handoff"] == 0
    kwargs = harness.censo_kwargs
    assert kwargs["preset"] == "censo-zero"
    assert kwargs["include_refinement"] is False
    # xTB preselection: all 20 frames share the same energy → cumulative
    # Boltzmann reaches 99% only with the full set → -n 20
    assert kwargs["nconf"] == 20


def test_workflow_preset_default_skip_opt_sp(
    tmp_path: Path, _wf_sample_config: dict[str, Any]
) -> None:
    harness = _WorkflowHarness()
    result = _run_workflow(harness, tmp_path, _wf_sample_config, preset="censo-default")

    assert result.status == "completed"
    kwargs = harness.censo_kwargs
    assert kwargs["preset"] == "censo-default"
    assert kwargs.get("include_refinement", False) is False
    # full funnel → same-level freq + Shermo (skip opt/SP), precomputed energy
    assert harness.calls["handoff"] == 2
    assert harness.handoff_calls[0]["skip_opt_sp"] is True
    assert harness.handoff_calls[0]["sp_energy_precomputed"] == pytest.approx(-4.92)


def test_workflow_preset_zero_opt_on_passthrough(
    tmp_path: Path, _wf_sample_config: dict[str, Any]
) -> None:
    harness = _WorkflowHarness()
    result = _run_workflow(harness, tmp_path, _wf_sample_config, preset="censo-zero")

    assert result.status == "completed"
    # censo-zero + opt: no CENSO CLI — xTB passthrough → ACP handoff for
    # every filtered frame (all 20 share the same GFN1 energy → all within
    # the 99% cumulative-Boltzmann set)
    assert harness.calls["censo"] == 0
    assert harness.calls["handoff"] == 20
    assert result.metadata["n_conformers"] == 20


def test_workflow_resume_skips_sampling_stages(
    tmp_path: Path, _wf_sample_config: dict[str, Any]
) -> None:
    harness = _WorkflowHarness()
    first = _run_workflow(harness, tmp_path, _wf_sample_config)
    assert first.status == "completed"
    assert harness.calls["md"] == 1

    # second run with --resume: sampling stages skipped, censo/handoff rerun
    harness2 = _WorkflowHarness()
    second = _run_workflow(harness2, tmp_path, _wf_sample_config, resume=True)
    assert second.status == "completed"
    assert harness2.calls == {"md": 0, "batch": 0, "isostat": 0, "censo": 1, "handoff": 2}
    # metadata restored from the checkpoints
    assert second.metadata["n_after_isostat"] == 20
    assert second.metadata["n_ok"] == 20


def test_workflow_resume_fingerprint_stage_specific(
    tmp_path: Path, _wf_sample_config: dict[str, Any]
) -> None:
    harness = _WorkflowHarness()
    assert _run_workflow(harness, tmp_path, _wf_sample_config).status == "completed"

    # changing md_temperature invalidates only the xtbmd checkpoint
    harness2 = _WorkflowHarness()
    result = _run_workflow(harness2, tmp_path, _wf_sample_config, resume=True, md_temperature=500.0)
    assert result.status == "failed"
    assert "xtbmd" in (result.error or "")

    # changing opt_gfn invalidates only the batch_opt checkpoint
    harness3 = _WorkflowHarness()
    result = _run_workflow(harness3, tmp_path, _wf_sample_config, resume=True, opt_gfn_level=2)
    assert result.status == "failed"
    assert "batch_opt" in (result.error or "")

    # changing edis invalidates only the isostat checkpoint
    harness4 = _WorkflowHarness()
    result = _run_workflow(harness4, tmp_path, _wf_sample_config, resume=True, edis=0.3)
    assert result.status == "failed"
    assert "isostat" in (result.error or "")


def test_workflow_empty_isostat_ensemble_fails_fast(
    tmp_path: Path, _wf_sample_config: dict[str, Any]
) -> None:
    harness = _WorkflowHarness(cluster_mode="empty")
    result = _run_workflow(harness, tmp_path, _wf_sample_config)

    assert result.status == "failed"
    assert "edis" in (result.error or "") or "gdis" in (result.error or "")


def test_workflow_unknown_preset(tmp_path: Path, _wf_sample_config: dict[str, Any]) -> None:
    harness = _WorkflowHarness()
    result = _run_workflow(harness, tmp_path, _wf_sample_config, preset="bogus")

    assert result.status == "failed"
    assert "Unknown preset" in (result.error or "")
    assert harness.calls["md"] == 0


# ---------------------------------------------------------------------------
# Phase 4: energy-window filter (doc §8.3)
# ---------------------------------------------------------------------------


def _write_isomers_xyz(
    path: Path,
    energies: list[float],
    titles: list[str] | None = None,
) -> Path:
    block: list[str] = []
    for i, energy in enumerate(energies):
        title = titles[i] if titles else f"Energy: {energy:.8f}"
        block.extend(
            [
                "2",
                title,
                "H 0.0000000000 0.0000000000 0.0000000000",
                f"H 0.0000000000 0.0000000000 {0.74 + i * 0.01:.10f}",
                "",
            ]
        )
    path.write_text("\n".join(block), encoding="utf-8")
    return path


def _write_sidecar(path: Path, energies: list[float], ok: bool = True) -> Path:
    path.write_text(
        json.dumps(
            {
                "frames": [
                    {"frame": i, "status": "ok" if ok else "failed", "energy": energy}
                    for i, energy in enumerate(energies)
                ]
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def test_energy_filter_sidecar_primary_channel(tmp_path: Path) -> None:
    """Cluster frames without title energies map geometrically to the sidecar."""
    energies = [-100.0, -100.01]  # 0.01 Ha = 6.28 kcal/mol > 6 → frame 2 filtered
    isomers = _write_isomers_xyz(tmp_path / "isomers.xyz", energies, titles=["Frame 0", "Frame 1"])
    sidecar = _write_sidecar(tmp_path / "isomers_energies.json", energies)
    cluster = _write_isomers_xyz(
        tmp_path / "cluster.xyz",
        energies,
        titles=["Cluster 1", "Cluster 2"],
    )

    result = _filter_energy_window(
        cluster,
        isomers,
        sidecar,
        ewin=6.0,
        work_dir=tmp_path / "filter",
    )

    assert result.n_total == 2
    assert result.n_after_filter == 1

    # rewritten titles carry the GFN1 Hartree energy → censo-zero passthrough
    passthrough = xtb_passthrough_result(result.ensemble_xyz, 298.15)
    assert len(passthrough.records) == 1
    assert passthrough.records[0].gtot == pytest.approx(-100.01, abs=1e-9)

    side = json.loads(result.ensemble_energies_json.read_text(encoding="utf-8"))
    assert side["frames"][0]["source_frame"] == 1
    assert side["frames"][0]["energy"] == pytest.approx(-100.01)


def test_energy_filter_title_compat_channel(tmp_path: Path) -> None:
    """Without a sidecar, the rewritten-title float is the fallback channel."""
    energies = [-100.0, -99.9]
    isomers = _write_isomers_xyz(tmp_path / "isomers.xyz", energies)
    sidecar = tmp_path / "missing_sidecar.json"
    sidecar.write_text("not json", encoding="utf-8")
    cluster = _write_isomers_xyz(
        tmp_path / "cluster.xyz", energies, titles=["E: -100.0", "E: -99.9"]
    )

    result = _filter_energy_window(
        cluster,
        isomers,
        sidecar,
        ewin=6.0,
        work_dir=tmp_path / "filter",
    )
    assert result.n_after_filter == 1
    passthrough = xtb_passthrough_result(result.ensemble_xyz, 298.15)
    assert passthrough.records[0].gtot == pytest.approx(-100.0, abs=1e-9)


def test_energy_filter_no_energy_fails_fast(tmp_path: Path) -> None:
    energies = [-100.0, -99.9]
    isomers = _write_isomers_xyz(tmp_path / "isomers.xyz", energies, titles=["A", "B"])
    sidecar = tmp_path / "sidecar.json"
    sidecar.write_text(json.dumps({"frames": []}), encoding="utf-8")
    cluster = _write_isomers_xyz(tmp_path / "cluster.xyz", energies, titles=["X", "Y"])

    with pytest.raises(RuntimeError, match="no GFN1 energy recoverable"):
        _filter_energy_window(
            cluster,
            isomers,
            sidecar,
            ewin=6.0,
            work_dir=tmp_path / "filter",
        )


def test_energy_filter_empty_cluster_fails_fast(tmp_path: Path) -> None:
    cluster = tmp_path / "cluster.xyz"
    cluster.write_text("", encoding="utf-8")
    isomers = _write_isomers_xyz(tmp_path / "isomers.xyz", [-100.0])
    sidecar = _write_sidecar(tmp_path / "sidecar.json", [-100.0])

    with pytest.raises(RuntimeError, match="edis|gdis"):
        _filter_energy_window(
            cluster,
            isomers,
            sidecar,
            ewin=6.0,
            work_dir=tmp_path / "filter",
        )


# ---------------------------------------------------------------------------
# Phase 4 audit fixes (2026-08-01 review)
# ---------------------------------------------------------------------------


def test_workflow_batch_nproc_falls_back_to_config(
    tmp_path: Path, _wf_sample_config: dict[str, Any]
) -> None:
    """Without --nproc the batch concurrency defaults to resources.nproc."""
    harness = _WorkflowHarness()
    result = _run_workflow(harness, tmp_path, _wf_sample_config)
    assert result.status == "completed"
    assert harness.batch_kwargs["nproc"] == 4  # _wf_sample_config resources.nproc


def test_workflow_resume_conv_params_invalidate_batch_opt(
    tmp_path: Path, _wf_sample_config: dict[str, Any]
) -> None:
    """conv-check controls are part of the batch_opt fingerprint."""
    harness = _WorkflowHarness()
    assert _run_workflow(harness, tmp_path, _wf_sample_config).status == "completed"

    harness2 = _WorkflowHarness()
    result = _run_workflow(harness2, tmp_path, _wf_sample_config, resume=True, conv_rmsd=1.0)
    assert result.status == "failed"
    assert "batch_opt" in (result.error or "")

    harness3 = _WorkflowHarness()
    result = _run_workflow(harness3, tmp_path, _wf_sample_config, resume=True, conv_check=False)
    assert result.status == "failed"
    assert "batch_opt" in (result.error or "")


def test_energy_filter_aligned_fallback_matches_rotated_copy(tmp_path: Path) -> None:
    """A symmetry-rotated cluster frame still maps to its isomers source."""
    energies = [-100.0, -99.9]
    isomers = _write_isomers_xyz(tmp_path / "isomers.xyz", energies)
    sidecar = _write_sidecar(tmp_path / "isomers_energies.json", energies)
    # Cluster frame 1 is the reversed (rotated 180°) copy of isomers frame 1:
    # plain RMSD ≈ 0.74 Å > tolerance → Kabsch fallback must resolve it.
    cluster = tmp_path / "cluster.xyz"
    cluster.write_text(
        "\n".join(
            [
                "2",
                "Cluster 0",
                "H 0.0000000000 0.0000000000 0.0000000000",
                "H 0.0000000000 0.0000000000 0.7400000000",
                "2",
                "Cluster 1",
                "H 0.0000000000 0.0000000000 0.7500000000",
                "H 0.0000000000 0.0000000000 0.0000000000",
            ]
        ),
        encoding="utf-8",
    )

    result = _filter_energy_window(
        cluster,
        isomers,
        sidecar,
        ewin=6.0,
        work_dir=tmp_path / "filter",
    )
    assert result.n_after_filter == 1
    side = json.loads(result.ensemble_energies_json.read_text(encoding="utf-8"))
    assert side["frames"][0]["source_frame"] == 0
    assert side["frames"][0]["energy"] == pytest.approx(-100.0, abs=1e-9)
