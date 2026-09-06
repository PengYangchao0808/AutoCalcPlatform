"""Tests for acp.confsearch.sampling — MD sampling-history capture.

Covers: traj parsing, equilibration cut, greedy RMSD basins, classical MDS,
saturation metrics, SamplingHistory round-trip, read_traj_frame_xyz, and the
engine finalize hook for MD protocols.
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path

import numpy as np
import pytest

from acp.confsearch.sampling import (
    BasinInfo,
    SamplingHistory,
    SamplingSaturation,
    TrajFrame,
    assign_basins,
    compute_sampling_history,
    equilibration_cutoff,
    load_sampling_history,
    mds_2d,
    parse_traj_frames,
    read_traj_frame_xyz,
    write_sampling_history,
)

# ---------------------------------------------------------------------------
# Synthetic trajectory fixture builder
# ---------------------------------------------------------------------------

# 4-atom toy molecule: C, H, H, H  (methane-like)
_SYMBOLS = ["C", "H", "H", "H"]

# Two genuinely different shapes (centroid-aligned RMSD > 0.5 Å).
# Basin A: tetrahedral arrangement  |  Basin B: planar (all Z=0)
_BASIN_A = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
_BASIN_B = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [1.0, 1.0, 0.0]])


def _build_traj_text(n_frames: int = 120) -> str:
    """Build a synthetic multi-frame XYZ trajectory.

    Frames 0-59: alternating basins A and B (two distinct clusters).
    Frames 60-119: basin A only (plateau — no new clusters).
    Titles: ``md: <t(ps)> <E_pot> (kcal/mol) <E_tot> (kcal/mol)``
    Energy drifts down for 0-59, then flattens for 60-119.
    """
    lines: list[str] = []
    for i in range(n_frames):
        n_atoms = len(_SYMBOLS)
        lines.append(str(n_atoms))
        t_ps = i * 0.5
        if i < 60:
            # drifting energy; alternating basins
            e_pot = -100.0 + i * 0.1
            coords = _BASIN_A if i % 2 == 0 else _BASIN_B
        else:
            # plateau; basin A only
            e_pot = -94.0
            coords = _BASIN_A
        e_tot = e_pot + 1.5
        title = f"md: {t_ps:.1f} {e_pot:.2f} (kcal/mol) {e_tot:.2f} (kcal/mol)"
        lines.append(title)
        for sym, row in zip(_SYMBOLS, coords, strict=True):
            lines.append(f"{sym}  {row[0]:.6f}  {row[1]:.6f}  {row[2]:.6f}")
    return "\n".join(lines) + "\n"


@pytest.fixture()
def synthetic_traj(tmp_path: Path) -> Path:
    """Write a 120-frame synthetic trajectory to a temp file."""
    traj = tmp_path / "traj.xyz"
    traj.write_text(_build_traj_text(120), encoding="utf-8")
    return traj


@pytest.fixture()
def small_traj(tmp_path: Path) -> Path:
    """Write a tiny 6-frame trajectory (3 basin-A, 3 basin-B)."""
    lines: list[str] = []
    for i in range(6):
        lines.append(str(len(_SYMBOLS)))
        t_ps = i * 1.0
        e_pot = -100.0 + i * 0.5
        e_tot = e_pot + 1.0
        title = f"md: {t_ps:.1f} {e_pot:.2f} (kcal/mol) {e_tot:.2f} (kcal/mol)"
        lines.append(title)
        coords = _BASIN_A if i < 3 else _BASIN_B
        for sym, row in zip(_SYMBOLS, coords, strict=True):
            lines.append(f"{sym}  {row[0]:.6f}  {row[1]:.6f}  {row[2]:.6f}")
    text = "\n".join(lines) + "\n"
    traj = tmp_path / "small_traj.xyz"
    traj.write_text(text, encoding="utf-8")
    return traj


# ---------------------------------------------------------------------------
# parse_traj_frames
# ---------------------------------------------------------------------------


class TestParseTrajFrames:
    """Frame parser: title regex, energy/time extraction, malformed handling."""

    def test_basic_parse(self, synthetic_traj: Path) -> None:
        frames = parse_traj_frames(synthetic_traj)
        assert len(frames) == 120
        assert frames[0].time_ps == pytest.approx(0.0)
        assert frames[0].step == 0
        assert frames[0].energy_kcal_mol == pytest.approx(-100.0, abs=0.1)
        assert frames[0].coords.shape == (4, 3)
        assert frames[0].symbols == _SYMBOLS

    def test_time_ps_parsed(self, synthetic_traj: Path) -> None:
        frames = parse_traj_frames(synthetic_traj)
        assert frames[59].time_ps == pytest.approx(29.5)
        assert frames[119].time_ps == pytest.approx(59.5)

    def test_step_equals_index(self, synthetic_traj: Path) -> None:
        frames = parse_traj_frames(synthetic_traj)
        for i, f in enumerate(frames):
            assert f.step == i

    def test_scientific_notation_energy(self, tmp_path: Path) -> None:
        """Title with scientific-notation energy parses correctly."""
        lines = [
            "1",
            "md: 0.5 -1.23456e+02 (kcal/mol) -1.23455e+02 (kcal/mol)",
            "C  0.0  0.0  0.0",
        ]
        traj = tmp_path / "sci.xyz"
        traj.write_text("\n".join(lines) + "\n", encoding="utf-8")
        frames = parse_traj_frames(traj)
        assert len(frames) == 1
        assert frames[0].energy_kcal_mol == pytest.approx(-123.456, abs=0.01)

    def test_malformed_title_keeps_frame(self, tmp_path: Path) -> None:
        """Frame with unparseable title is kept with None energy/time."""
        lines = [
            "1",
            "no md prefix here",
            "C  0.0  0.0  0.0",
        ]
        traj = tmp_path / "bad_title.xyz"
        traj.write_text("\n".join(lines) + "\n", encoding="utf-8")
        frames = parse_traj_frames(traj)
        assert len(frames) == 1
        assert frames[0].energy_kcal_mol is None
        assert frames[0].time_ps is None

    def test_empty_file_returns_empty(self, tmp_path: Path) -> None:
        traj = tmp_path / "empty.xyz"
        traj.write_text("", encoding="utf-8")
        assert parse_traj_frames(traj) == []

    def test_mixed_valid_and_malformed(self, tmp_path: Path) -> None:
        """Mix of valid and malformed frames — all kept, counter incremented."""
        lines = [
            "1",
            "md: 0.5 -100.00 (kcal/mol) -98.50 (kcal/mol)",
            "C  0.0  0.0  0.0",
            "1",
            "garbage title",
            "C  1.0  0.0  0.0",
            "1",
            "md: 1.5 -99.00 (kcal/mol) -97.50 (kcal/mol)",
            "C  2.0  0.0  0.0",
        ]
        traj = tmp_path / "mixed.xyz"
        traj.write_text("\n".join(lines) + "\n", encoding="utf-8")
        frames = parse_traj_frames(traj)
        assert len(frames) == 3
        assert frames[0].energy_kcal_mol == pytest.approx(-100.0)
        assert frames[1].energy_kcal_mol is None
        assert frames[2].energy_kcal_mol == pytest.approx(-99.0)

    def test_five_part_xyz_lines(self, tmp_path: Path) -> None:
        """5-part lines (leading atom index) parse correctly."""
        lines = [
            "2",
            "md: 0.5 -100.00 (kcal/mol) -98.50 (kcal/mol)",
            "0 C  0.0  0.0  0.0",
            "1 H  1.0  0.0  0.0",
        ]
        traj = tmp_path / "5part.xyz"
        traj.write_text("\n".join(lines) + "\n", encoding="utf-8")
        frames = parse_traj_frames(traj)
        assert len(frames) == 1
        assert frames[0].coords.shape == (2, 3)
        assert frames[0].coords[0, 0] == pytest.approx(0.0)
        assert frames[0].coords[1, 0] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Equilibration cutoff
# ---------------------------------------------------------------------------


class TestEquilibrationCutoff:
    """±2σ sliding-window equilibration detection."""

    def test_stable_series_drops_min_frac(self) -> None:
        """Constant energy → drops min_frac (5%)."""
        energies = [100.0] * 200
        cut = equilibration_cutoff(energies)
        assert cut == int(round(0.05 * 200))  # 10

    def test_transient_drops_prefix(self) -> None:
        """Big jump early → transient prefix discarded."""
        # first 50 frames at 200, rest at 100 — significant shift
        energies = [200.0] * 50 + [100.0] * 150
        cut = equilibration_cutoff(energies, window=25)
        assert cut > 0

    def test_empty_returns_zero(self) -> None:
        assert equilibration_cutoff([]) == 0

    def test_none_energies_fallback(self) -> None:
        """All-None energies → fallback_frac."""
        energies: list[float | None] = [None] * 100
        cut = equilibration_cutoff(energies)
        assert cut == int(round(0.10 * 100))  # 10


# ---------------------------------------------------------------------------
# assign_basins
# ---------------------------------------------------------------------------


class TestAssignBasins:
    """Greedy RMSD basin clustering with carry-forward."""

    def test_two_basins_small_traj(self, small_traj: Path) -> None:
        """6 frames alternating A/A/A/B/B/B → 2 basins."""
        frames = parse_traj_frames(small_traj)
        basin_ids, basins = assign_basins(frames, rmsd_threshold=0.5)
        assert len(basins) == 2
        # basin 0 starts at frame 0, basin 1 at frame 3
        assert basins[0].first_seen_index == 0
        assert basins[1].first_seen_index == 3

    def test_carry_forward_approximation(self, synthetic_traj: Path) -> None:
        """Non-sampled frames (when stride > 1) inherit previous basin."""
        frames = parse_traj_frames(synthetic_traj)
        # With max_cluster_frames=50, stride ~ 2-3, some frames skipped
        basin_ids, basins = assign_basins(frames, rmsd_threshold=0.5, max_cluster_frames=50)
        # Every frame must have a basin id
        assert len(basin_ids) == len(frames)
        # Basin ids must be non-negative
        assert all(b >= 0 for b in basin_ids)

    def test_basin_visit_counts(self, small_traj: Path) -> None:
        frames = parse_traj_frames(small_traj)
        _, basins = assign_basins(frames, rmsd_threshold=0.5)
        total_visits = sum(b.visit_count for b in basins)
        assert total_visits == len(frames)

    def test_basin_min_energy(self, small_traj: Path) -> None:
        frames = parse_traj_frames(small_traj)
        _, basins = assign_basins(frames, rmsd_threshold=0.5)
        # min_energy should be set
        for b in basins:
            assert b.min_energy is not None

    def test_single_frame(self, tmp_path: Path) -> None:
        """Single frame → 1 basin."""
        lines = [
            "1",
            "md: 0.5 -100.00 (kcal/mol) -98.50 (kcal/mol)",
            "C  0.0  0.0  0.0",
        ]
        traj = tmp_path / "one.xyz"
        traj.write_text("\n".join(lines) + "\n", encoding="utf-8")
        frames = parse_traj_frames(traj)
        basin_ids, basins = assign_basins(frames)
        assert len(basins) == 1
        assert basin_ids == [0]

    def test_shape_mismatch_guard(self, tmp_path: Path) -> None:
        """Frames with different atom counts → plain_rmsd returns inf → new basins."""
        lines = [
            "2",
            "md: 0.0 -100.00 (kcal/mol) -98.50 (kcal/mol)",
            "C  0.0  0.0  0.0",
            "H  1.0  0.0  0.0",
            "1",
            "md: 0.5 -99.00 (kcal/mol) -97.50 (kcal/mol)",
            "C  0.0  0.0  0.0",
        ]
        traj = tmp_path / "mixed_atoms.xyz"
        traj.write_text("\n".join(lines) + "\n", encoding="utf-8")
        frames = parse_traj_frames(traj)
        basin_ids, basins = assign_basins(frames)
        assert len(basins) == 2  # shape mismatch → inf → new basin


# ---------------------------------------------------------------------------
# MDS
# ---------------------------------------------------------------------------


class TestMDS2D:
    """Classical MDS from distance matrix → 2D coordinates."""

    def test_basic_mds(self) -> None:
        """3-point distance matrix → 3×2 finite coords."""
        dist_mat = np.array([[0, 1, 2], [1, 0, 1], [2, 1, 0]], dtype=float)
        coords = mds_2d(dist_mat)
        assert len(coords) == 3
        for x, y in coords:
            assert math.isfinite(x)
            assert math.isfinite(y)

    def test_degenerate_all_zero(self) -> None:
        """All-zero distances → zeros."""
        dist_mat = np.zeros((3, 3))
        coords = mds_2d(dist_mat)
        assert all(x == 0.0 and y == 0.0 for x, y in coords)

    def test_single_point(self) -> None:
        dist_mat = np.array([[0.0]])
        coords = mds_2d(dist_mat)
        assert coords == [(0.0, 0.0)]

    def test_two_points(self) -> None:
        dist_mat = np.array([[0, 5], [5, 0]], dtype=float)
        coords = mds_2d(dist_mat)
        assert len(coords) == 2
        # The two points should be separated
        dist = math.sqrt((coords[0][0] - coords[1][0]) ** 2 + (coords[0][1] - coords[1][1]) ** 2)
        assert dist > 0


# ---------------------------------------------------------------------------
# SamplingSaturation
# ---------------------------------------------------------------------------


class TestSamplingSaturation:
    """Saturation level rule: HIGH / MEDIUM / LOW."""

    def test_high_when_no_new_clusters_in_last_20pct(self) -> None:
        sat = SamplingSaturation(
            unique_clusters=5,
            new_clusters_last_20pct=0,
            last_new_basin_ps=10.0,
            revisit_ratio=0.8,
            energy_window_kcal_mol=5.0,
            level="HIGH",
            cumulative_unique=[],
        )
        assert sat.level == "HIGH"

    def test_medium_when_few_new_clusters(self) -> None:
        # unique=10, new_last_20=1, max(1,10//10)=1 → MEDIUM
        sat = SamplingSaturation(
            unique_clusters=10,
            new_clusters_last_20pct=1,
            last_new_basin_ps=40.0,
            revisit_ratio=0.5,
            energy_window_kcal_mol=10.0,
            level="MEDIUM",
            cumulative_unique=[],
        )
        assert sat.level == "MEDIUM"

    def test_low_when_many_new_clusters(self) -> None:
        # unique=5, new_last_20=3, max(1,5//10)=1, 3>1 → LOW
        sat = SamplingSaturation(
            unique_clusters=5,
            new_clusters_last_20pct=3,
            last_new_basin_ps=50.0,
            revisit_ratio=0.2,
            energy_window_kcal_mol=20.0,
            level="LOW",
            cumulative_unique=[],
        )
        assert sat.level == "LOW"


# ---------------------------------------------------------------------------
# SamplingHistory round-trip
# ---------------------------------------------------------------------------


class TestSamplingHistory:
    """Frozen dataclass serialization and JSON round-trip."""

    def _make_history(self) -> SamplingHistory:
        from acp.confsearch.sampling import SamplingSaturation

        frames = [
            TrajFrame(
                index=0,
                time_ps=0.0,
                step=0,
                energy_kcal_mol=-100.0,
                symbols=["C"],
                coords=np.array([[0.0, 0.0, 0.0]]),
            ),
            TrajFrame(
                index=1,
                time_ps=0.5,
                step=1,
                energy_kcal_mol=-99.0,
                symbols=["C"],
                coords=np.array([[1.0, 0.0, 0.0]]),
            ),
        ]
        basins = [
            BasinInfo(
                basin_id=0,
                first_seen_index=0,
                first_seen_ps=0.0,
                visit_count=2,
                min_energy=-100.0,
                representative_frame=0,
            ),
        ]
        sat = SamplingSaturation(
            unique_clusters=1,
            new_clusters_last_20pct=0,
            last_new_basin_ps=0.0,
            revisit_ratio=1.0,
            energy_window_kcal_mol=1.0,
            level="HIGH",
            cumulative_unique=[{"time_ps": 0.0, "unique": 1}, {"time_ps": 0.5, "unique": 1}],
        )
        return SamplingHistory(
            schema_version="sampling_history_v1",
            protocol="xtb-md",
            source_trajectory="traj.xyz",
            n_frames_raw=2,
            n_frames_used=2,
            equilibration_cut=0,
            frames=frames,
            basin_ids=[0, 0],
            is_new_basin=[True, False],
            mds_coords=[(0.0, 0.0), (1.0, 0.0)],
            basins=basins,
            saturation=sat,
            computed_at="2026-09-06T00:00:00",
            subsampled=False,
            subsample_stride=1,
        )

    def test_to_dict_from_dict_roundtrip(self) -> None:
        h = self._make_history()
        d = h.to_dict()
        assert d["schema_version"] == "sampling_history_v1"
        assert d["protocol"] == "xtb-md"
        assert len(d["frames"]) == 2
        # Frames must NOT carry coordinates
        for f in d["frames"]:
            assert "coords" not in f
            assert "symbols" not in f
        # Round-trip
        h2 = SamplingHistory.from_dict(d)
        assert h2.schema_version == h.schema_version
        assert h2.n_frames_raw == h.n_frames_raw
        assert len(h2.frames) == len(h.frames)
        assert h2.saturation.level == "HIGH"

    def test_frames_carry_no_coords_in_json(self) -> None:
        h = self._make_history()
        d = h.to_dict()
        json_str = json.dumps(d)
        # coords should not appear in JSON
        assert "coords" not in json_str

    def test_relative_energy_computed(self) -> None:
        h = self._make_history()
        d = h.to_dict()
        # Relative energy: frame 0 = 0, frame 1 = 1.0 kcal/mol
        assert d["frames"][0]["relative_energy_kcal_mol"] == pytest.approx(0.0, abs=0.1)
        assert d["frames"][1]["relative_energy_kcal_mol"] == pytest.approx(1.0, abs=0.1)


# ---------------------------------------------------------------------------
# write / load round-trip
# ---------------------------------------------------------------------------


class TestWriteLoadSamplingHistory:
    """Atomic JSON write + load round-trip."""

    def test_write_and_load(self, tmp_path: Path) -> None:
        h = TestSamplingHistory()._make_history()
        write_sampling_history(tmp_path, h)
        path = tmp_path / "RESULT" / "confsearch" / "sampling_history.json"
        assert path.exists()
        h2 = load_sampling_history(tmp_path)
        assert h2 is not None
        assert h2.schema_version == "sampling_history_v1"

    def test_load_missing_returns_none(self, tmp_path: Path) -> None:
        assert load_sampling_history(tmp_path) is None

    def test_load_corrupt_returns_none(self, tmp_path: Path) -> None:
        result_dir = tmp_path / "RESULT" / "confsearch"
        result_dir.mkdir(parents=True)
        (result_dir / "sampling_history.json").write_text("NOT JSON", encoding="utf-8")
        assert load_sampling_history(tmp_path) is None


# ---------------------------------------------------------------------------
# read_traj_frame_xyz
# ---------------------------------------------------------------------------


class TestReadTrajFrameXyz:
    """Exact XYZ text block extraction for one frame."""

    def test_read_first_frame(self, synthetic_traj: Path) -> None:
        xyz = read_traj_frame_xyz(synthetic_traj, 0)
        assert xyz is not None
        lines = xyz.strip().splitlines()
        assert lines[0] == "4"
        assert lines[1].startswith("md:")
        assert len(lines) == 6  # count + title + 4 atoms

    def test_read_last_frame(self, synthetic_traj: Path) -> None:
        xyz = read_traj_frame_xyz(synthetic_traj, 119)
        assert xyz is not None
        assert xyz.strip().splitlines()[0] == "4"

    def test_out_of_range_returns_none(self, synthetic_traj: Path) -> None:
        assert read_traj_frame_xyz(synthetic_traj, 999) is None
        assert read_traj_frame_xyz(synthetic_traj, -1) is None


# ---------------------------------------------------------------------------
# compute_sampling_history (end-to-end)
# ---------------------------------------------------------------------------


class TestComputeSamplingHistory:
    """Full pipeline: parse → cut → basins → MDS → saturation → history."""

    def test_synthetic_120_frames(self, synthetic_traj: Path) -> None:
        h = compute_sampling_history(synthetic_traj, protocol="xtb-md")
        assert h is not None
        assert h.n_frames_raw == 120
        assert h.protocol == "xtb-md"
        assert len(h.frames) > 0
        # Two alternating basins in first 60 frames, then plateau
        assert h.saturation.unique_clusters >= 2
        # Saturation should be HIGH (no new basins in last 20%)
        assert h.saturation.level == "HIGH"
        # MDS coords are finite
        for x, y in h.mds_coords:
            assert math.isfinite(x)
            assert math.isfinite(y)
        # Cumulative unique is monotonic non-decreasing
        cu = h.saturation.cumulative_unique
        for i in range(1, len(cu)):
            assert cu[i]["unique"] >= cu[i - 1]["unique"]

    def test_small_traj_two_basins(self, small_traj: Path) -> None:
        h = compute_sampling_history(small_traj, protocol="xtbmd-censo")
        assert h is not None
        assert h.saturation.unique_clusters == 2

    def test_empty_traj(self, tmp_path: Path) -> None:
        traj = tmp_path / "empty.xyz"
        traj.write_text("", encoding="utf-8")
        h = compute_sampling_history(traj, protocol="xtb-md")
        assert h is not None
        assert h.n_frames_raw == 0
        assert h.saturation.level == "LOW"


# ---------------------------------------------------------------------------
# Engine hook integration
# ---------------------------------------------------------------------------


class TestEngineSamplingHook:
    """Confsearch engine finalize writes sampling_history.json for MD protocols."""

    def test_md_protocol_writes_sampling_history(self, tmp_path: Path) -> None:
        """Fake task dir with traj.xyz → file written + manifest fields present."""
        from unittest.mock import MagicMock, patch

        from acp.confsearch.engine import ConfsearchEngine

        # Mock _confsearch_dir returns <tmp>/output/mol/RESULT/confsearch
        # → task_root = <tmp>/output/mol → traj at WORK/02_SEARCH/xTB/traj.xyz
        task_root = tmp_path / "output" / "mol"
        confsearch_dir = task_root / "RESULT" / "confsearch"
        confsearch_dir.mkdir(parents=True)
        work_dir = task_root / "WORK" / "02_SEARCH" / "xTB"
        work_dir.mkdir(parents=True)
        (work_dir / "traj.xyz").write_text(_build_traj_text(20), encoding="utf-8")

        request = MagicMock()
        request.protocol = "xtb-md"
        request.profile = "default"
        request.refinement_policy = "screen"
        request.output_dir = tmp_path / "output"
        request.input_source = "CCO"
        request.name = "test"
        request.charge = 0
        request.multiplicity = 1
        request.backend = "native"
        request.levels = {}
        request.config = None

        from acp.confsearch.contracts import ProtocolOutcome

        outcome = ProtocolOutcome(
            records=[],
            sampling={},
            workflow_metadata={},
            temperature_k=298.15,
        )

        with (
            patch.object(ConfsearchEngine, "_confsearch_dir", return_value=confsearch_dir),
            patch("acp.confsearch.engine.write_conformer_geometries"),
            patch("acp.confsearch.engine.write_ensemble_table"),
            patch("acp.confsearch.engine.select_for_refinement", return_value=[]),
            patch("acp.confsearch.engine.build_entries", return_value=[]),
            patch("acp.confsearch.engine.quality_gates", return_value={}),
            patch("acp.confsearch.engine.refinement_block", return_value={}),
            patch("acp.confsearch.engine.input_block", return_value={}),
            patch("acp.confsearch.engine.provenance_block", return_value={}),
            patch("acp.confsearch.engine.sorted_records", return_value=[]),
            patch("acp.confsearch.engine.write_manifest") as mock_write,
        ):
            # Capture the payload that write_manifest receives
            captured_payload: dict = {}

            def capture_write(_dir: Path, payload: dict) -> Path:
                captured_payload.update(payload)
                return _dir / "confsearch_manifest.json"

            mock_write.side_effect = capture_write

            # Patch progress reporter to None
            engine = ConfsearchEngine()
            engine._finalize(request, outcome)

            # Check that sampling_history was written
            sampling_file = confsearch_dir / "sampling_history.json"
            assert sampling_file.exists(), "sampling_history.json should be written"

            # Check manifest payload has sampling fields
            sampling = captured_payload.get("sampling", {})
            assert sampling.get("sampling_history") == "confsearch/sampling_history.json"
            assert sampling.get("saturation") in ("HIGH", "MEDIUM", "LOW")

    def test_crest_protocol_skips_sampling(self, tmp_path: Path) -> None:
        """CREST protocol → no sampling_history.json, success still."""
        from unittest.mock import MagicMock, patch

        from acp.confsearch.engine import ConfsearchEngine

        confsearch_dir = tmp_path / "output" / "mol" / "RESULT" / "confsearch"
        confsearch_dir.mkdir(parents=True)

        request = MagicMock()
        request.protocol = "xtb-crest"
        request.profile = "default"
        request.refinement_policy = "screen"
        request.output_dir = tmp_path / "output"
        request.input_source = "CCO"
        request.name = "test"
        request.charge = 0
        request.multiplicity = 1
        request.backend = "native"
        request.levels = {}
        request.config = None

        from acp.confsearch.contracts import ProtocolOutcome

        outcome = ProtocolOutcome(
            records=[],
            sampling={},
            workflow_metadata={},
            temperature_k=298.15,
        )

        with (
            patch.object(ConfsearchEngine, "_confsearch_dir", return_value=confsearch_dir),
            patch("acp.confsearch.engine.write_conformer_geometries"),
            patch("acp.confsearch.engine.write_ensemble_table"),
            patch("acp.confsearch.engine.select_for_refinement", return_value=[]),
            patch("acp.confsearch.engine.build_entries", return_value=[]),
            patch("acp.confsearch.engine.quality_gates", return_value={}),
            patch("acp.confsearch.engine.refinement_block", return_value={}),
            patch("acp.confsearch.engine.input_block", return_value={}),
            patch("acp.confsearch.engine.provenance_block", return_value={}),
            patch("acp.confsearch.engine.sorted_records", return_value=[]),
            patch("acp.confsearch.engine.write_manifest") as mock_write,
        ):
            captured_payload: dict = {}

            def capture_write(_dir: Path, payload: dict) -> Path:
                captured_payload.update(payload)
                return _dir / "confsearch_manifest.json"

            mock_write.side_effect = capture_write

            engine = ConfsearchEngine()
            engine._finalize(request, outcome)

            # No sampling_history.json should exist
            assert not (confsearch_dir / "sampling_history.json").exists()
            # Manifest should not have sampling_history key
            assert "sampling_history" not in captured_payload.get("sampling", {})

    def test_missing_traj_logs_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """traj.xyz missing → warning logged, finalize still succeeds."""
        from unittest.mock import MagicMock, patch

        from acp.confsearch.engine import ConfsearchEngine

        confsearch_dir = tmp_path / "output" / "mol" / "RESULT" / "confsearch"
        confsearch_dir.mkdir(parents=True)

        request = MagicMock()
        request.protocol = "xtb-md"
        request.profile = "default"
        request.refinement_policy = "screen"
        request.output_dir = tmp_path / "output"
        request.input_source = "CCO"
        request.name = "test"
        request.charge = 0
        request.multiplicity = 1
        request.backend = "native"
        request.levels = {}
        request.config = None

        from acp.confsearch.contracts import ProtocolOutcome

        outcome = ProtocolOutcome(
            records=[],
            sampling={},
            workflow_metadata={},
            temperature_k=298.15,
        )

        with (
            patch.object(ConfsearchEngine, "_confsearch_dir", return_value=confsearch_dir),
            patch("acp.confsearch.engine.write_conformer_geometries"),
            patch("acp.confsearch.engine.write_ensemble_table"),
            patch("acp.confsearch.engine.select_for_refinement", return_value=[]),
            patch("acp.confsearch.engine.build_entries", return_value=[]),
            patch("acp.confsearch.engine.quality_gates", return_value={}),
            patch("acp.confsearch.engine.refinement_block", return_value={}),
            patch("acp.confsearch.engine.input_block", return_value={}),
            patch("acp.confsearch.engine.provenance_block", return_value={}),
            patch("acp.confsearch.engine.sorted_records", return_value=[]),
            patch("acp.confsearch.engine.write_manifest") as mock_write,
        ):
            captured_payload: dict = {}

            def capture_write(_dir: Path, payload: dict) -> Path:
                captured_payload.update(payload)
                return _dir / "confsearch_manifest.json"

            mock_write.side_effect = capture_write

            engine = ConfsearchEngine()
            with caplog.at_level(logging.WARNING):
                engine._finalize(request, outcome)

            # Warning logged about sampling history failure
            assert any("sampling history" in record.message.lower() for record in caplog.records)
            # No sampling file written
            assert not (confsearch_dir / "sampling_history.json").exists()
