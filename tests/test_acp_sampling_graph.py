"""Tests for the sampling energy projection builder (Todo 8)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from acp.confsearch.sampling_models import (
    BasinInfo,
    SamplingHistory,
    SamplingSaturation,
    TrajFrame,
    write_sampling_history,
)
from acp.results.energy_graph import (
    build_energy_graph_from_job,
    has_sampling_history,
)
from acp.results.frames import ANNOTATION_TYPES, VIEW_REGISTRY

# ── fixtures ──────────────────────────────────────────────────────────────


def _make_history(
    n_frames: int = 6,
    *,
    protocol: str = "xtb-md",
    energies: list[float | None] | None = None,
    time_ps_start: float = 0.0,
    time_ps_step: float = 0.5,
) -> SamplingHistory:
    """Build a synthetic SamplingHistory for testing."""
    if energies is None:
        # 3 basins: frames 0-1 basin 0, frames 2-3 basin 1, frames 4-5 basin 2
        energies = [-100.0, -99.5, -98.0, -97.8, -100.5, -100.2]
    # Pad energies list to match n_frames (None for missing entries)
    energies = list(energies) + [None] * max(0, n_frames - len(energies))

    frames: list[TrajFrame] = []
    basin_ids: list[int] = []
    is_new_basin: list[bool] = []
    mds_coords: list[tuple[float | None, float | None]] = []
    basins: list[BasinInfo] = []

    seen_basins: set[int] = set()
    # Simple 3-basin assignment
    basin_map = [0, 0, 1, 1, 2, 2][:n_frames]
    if len(basin_map) < n_frames:
        basin_map.extend([2] * (n_frames - len(basin_map)))

    for i in range(n_frames):
        bid = basin_map[i] if i < len(basin_map) else 0
        t = time_ps_start + i * time_ps_step
        frames.append(
            TrajFrame(
                index=i,
                time_ps=t,
                step=i,
                energy_kcal_mol=energies[i] if i < len(energies) else None,
                symbols=["C", "H", "H", "H"],
                coords=__import__("numpy").empty((0, 3)),
            )
        )
        basin_ids.append(bid)
        is_new = bid not in seen_basins
        is_new_basin.append(is_new)
        if is_new:
            seen_basins.add(bid)
            basins.append(
                BasinInfo(
                    basin_id=bid,
                    first_seen_index=i,
                    first_seen_ps=t,
                    visit_count=1,
                    min_energy=energies[i] if i < len(energies) else None,
                    representative_frame=i,
                )
            )
        else:
            # Update visit count
            for b in basins:
                if b.basin_id == bid:
                    b.visit_count += 1
                    if energies[i] is not None and (
                        b.min_energy is None or energies[i] < b.min_energy
                    ):
                        b.min_energy = energies[i]
        mds_coords.append((float(bid), float(i)))

    saturation = SamplingSaturation(
        unique_clusters=3,
        new_clusters_last_20pct=0,
        last_new_basin_ps=frames[-2].time_ps if n_frames >= 2 else None,
        revisit_ratio=0.5,
        energy_window_kcal_mol=2.7,
        level="HIGH",
        cumulative_unique=[
            {"time_ps": frames[i].time_ps, "unique": len(set(basin_ids[: i + 1]))}
            for i in range(n_frames)
        ],
    )

    return SamplingHistory(
        schema_version="sampling_history_v1",
        protocol=protocol,
        source_trajectory="/tmp/traj.xyz",
        n_frames_raw=n_frames + 10,
        n_frames_used=n_frames,
        equilibration_cut=10,
        frames=frames,
        basin_ids=basin_ids,
        is_new_basin=is_new_basin,
        mds_coords=mds_coords,
        basins=basins,
        saturation=saturation,
        computed_at="2026-09-07T00:00:00Z",
        subsampled=False,
        subsample_stride=1,
    )


def _write_sampling_fixture(work_dir: Path, history: SamplingHistory) -> Path:
    """Write sampling_history.json into the expected RESULT/ confsearch dir."""
    return write_sampling_history(work_dir, history)


# ── has_sampling_history ──────────────────────────────────────────────────


def test_has_sampling_history_true(tmp_path):
    history = _make_history()
    _write_sampling_fixture(tmp_path, history)
    assert has_sampling_history(tmp_path) is True


def test_has_sampling_history_false(tmp_path):
    assert has_sampling_history(tmp_path) is False


def test_has_sampling_history_corrupt(tmp_path):
    result_dir = tmp_path / "RESULT" / "confsearch"
    result_dir.mkdir(parents=True)
    (result_dir / "sampling_history.json").write_text("NOT_JSON{{{")
    assert has_sampling_history(tmp_path) is False


# ── build_sampling_energy_graph ───────────────────────────────────────────


def test_sampling_projection_basic(tmp_path):
    """Core projection structure from a 6-frame fixture."""
    history = _make_history()
    _write_sampling_fixture(tmp_path, history)

    from acp.results.sampling_graph import build_sampling_energy_graph

    graph = build_sampling_energy_graph("job-001", tmp_path)

    assert graph is not None
    assert graph["job_id"] == "job-001"
    assert graph["view_type"] == "sampling"
    assert graph["title"] == VIEW_REGISTRY["sampling"].title_zh
    assert graph["status"] == "completed"
    assert graph["complete"] is True
    assert graph["available_views"] == ["sampling"]
    assert graph["x_axis"] == {"label": "模拟时间", "unit": "ps"}
    assert graph["source"] == "RESULT/confsearch/sampling_history.json"


def test_sampling_projection_series(tmp_path):
    """Two series: energy_potential and relative_energy."""
    from acp.results.sampling_graph import build_sampling_energy_graph

    history = _make_history(n_frames=3, energies=[-100.0, -99.5, -98.0])
    _write_sampling_fixture(tmp_path, history)

    graph = build_sampling_energy_graph("job-002", tmp_path)

    series_ids = {s["id"] for s in graph["series"]}
    assert "energy_potential" in series_ids
    assert "relative_energy" in series_ids
    potential = next(s for s in graph["series"] if s["id"] == "energy_potential")
    assert potential["label"] == "势能"
    assert potential["unit"] == "kcal/mol"
    relative = next(s for s in graph["series"] if s["id"] == "relative_energy")
    assert relative["label"] == "相对能量"
    assert relative["unit"] == "kcal/mol"
    # Relative energies: min is -100.0 → [0.0, 0.5, 2.0]
    assert relative["values"][0] == pytest.approx(0.0)
    assert relative["values"][1] == pytest.approx(0.5)
    assert relative["values"][2] == pytest.approx(2.0)


def test_sampling_projection_nodes(tmp_path):
    """Nodes carry time_ps as x, relative energy, basin metadata."""
    from acp.results.sampling_graph import build_sampling_energy_graph

    history = _make_history()
    _write_sampling_fixture(tmp_path, history)

    graph = build_sampling_energy_graph("job-003", tmp_path)

    assert len(graph["nodes"]) == 6
    for node in graph["nodes"]:
        # Node wire shape
        assert set(node.keys()) == {
            "id",
            "label",
            "type",
            "frame_index",
            "x",
            "energy",
            "status",
            "geometry_ref",
            "metadata",
        }
        assert node["type"] == VIEW_REGISTRY["sampling"].node_type
    # First node: time_ps = 0.0, basin_id = 0
    n0 = graph["nodes"][0]
    assert n0["x"] == pytest.approx(0.0)
    assert n0["metadata"]["basin_id"] == 0
    assert n0["metadata"]["step"] == 0


def test_sampling_projection_annotations(tmp_path):
    """3 basins → 3 new_basin annotations + 1 minimum."""
    from acp.results.sampling_graph import build_sampling_energy_graph

    history = _make_history()
    _write_sampling_fixture(tmp_path, history)

    graph = build_sampling_energy_graph("job-004", tmp_path)

    annotations = graph["annotations"]
    new_basin_anns = [a for a in annotations if a["type"] == "new_basin"]
    min_anns = [a for a in annotations if a["type"] == "minimum"]
    assert len(new_basin_anns) == 3
    assert len(min_anns) == 1
    # new_basin labels are "新盆地"
    assert all(a["label"] == "新盆地" for a in new_basin_anns)
    # minimum label is "最低能量"
    assert min_anns[0]["label"] == "最低能量"
    # Minimum energy is -100.5 (frame index 4, time 2.0)
    assert min_anns[0]["frame_index"] == 4
    assert min_anns[0]["x"] == pytest.approx(2.0)


def test_sampling_projection_metadata_saturation(tmp_path):
    """Metadata carries basins, saturation, cumulative_unique, axis_options."""
    from acp.results.sampling_graph import build_sampling_energy_graph

    history = _make_history()
    _write_sampling_fixture(tmp_path, history)

    graph = build_sampling_energy_graph("job-005", tmp_path)
    meta = graph["metadata"]

    assert meta["frame_count"] == 6
    assert meta["n_frames_raw"] == 16  # n_frames=6 + 10 equilibration cut
    assert meta["subsampled"] is False
    assert meta["subsample_stride"] == 1
    assert "saturation" in meta
    assert meta["saturation"]["level"] == "HIGH"
    assert meta["saturation"]["unique_clusters"] == 3
    assert "cumulative_unique" in meta
    assert len(meta["cumulative_unique"]) == 6
    assert meta["axis_options"]["x"] == ["time_ps", "step", "frame"]
    assert meta["axis_options"]["y"] == ["potential", "relative"]
    assert len(meta["basins"]) == 3


def test_sampling_projection_mds_finite(tmp_path):
    """Every node's mds pair contains finite floats."""
    from acp.results.sampling_graph import build_sampling_energy_graph

    history = _make_history()
    _write_sampling_fixture(tmp_path, history)

    graph = build_sampling_energy_graph("job-006", tmp_path)
    for node in graph["nodes"]:
        mds = node["metadata"]["mds"]
        assert len(mds) == 2
        assert all(isinstance(v, float) for v in mds)


# ── missing / corrupt fallback ────────────────────────────────────────────


def test_sampling_graph_none_on_missing(tmp_path):
    """build_sampling_energy_graph returns None when file is absent."""
    from acp.results.sampling_graph import build_sampling_energy_graph

    assert build_sampling_energy_graph("job-miss", tmp_path) is None


def test_sampling_graph_none_on_corrupt(tmp_path):
    """build_sampling_energy_graph returns None when file is corrupt."""
    from acp.results.sampling_graph import build_sampling_energy_graph

    result_dir = tmp_path / "RESULT" / "confsearch"
    result_dir.mkdir(parents=True)
    (result_dir / "sampling_history.json").write_text("{bad json")
    assert build_sampling_energy_graph("job-corrupt", tmp_path) is None


# ── energy_graph dispatch integration ─────────────────────────────────────


def test_dispatch_confsearch_with_sampling_view(tmp_path):
    """view='sampling' returns sampling projection when history exists."""
    history = _make_history()
    _write_sampling_fixture(tmp_path, history)
    # Write a minimal confsearch manifest so conformer fallback also works
    result_confsearch = tmp_path / "RESULT" / "confsearch"
    result_confsearch.mkdir(parents=True, exist_ok=True)
    (result_confsearch / "confsearch_manifest.json").write_text(
        json.dumps(
            {
                "conformers": [
                    {"conf_id": "C1", "free_energy_hartree": -100.0, "rank": 1},
                ]
            }
        )
    )

    graph = build_energy_graph_from_job(
        "job-d001",
        workflow="Confsearch",
        method=None,
        work_dir=tmp_path,
        view="sampling",
    )

    assert graph["view_type"] == "sampling"
    assert graph["title"] == VIEW_REGISTRY["sampling"].title_zh


def test_dispatch_confsearch_without_sampling_falls_back(tmp_path):
    """view='sampling' with no history file falls back to conformer."""
    # Write a minimal confsearch manifest
    result_confsearch = tmp_path / "RESULT" / "confsearch"
    result_confsearch.mkdir(parents=True, exist_ok=True)
    (result_confsearch / "confsearch_manifest.json").write_text(
        json.dumps(
            {
                "conformers": [
                    {"conf_id": "C1", "free_energy_hartree": -100.0, "rank": 1},
                ]
            }
        )
    )

    graph = build_energy_graph_from_job(
        "job-d002",
        workflow="Confsearch",
        method=None,
        work_dir=tmp_path,
        view="sampling",
    )

    # Falls back to conformer
    assert graph["view_type"] == "conformer"


def test_dispatch_confsearch_available_views_with_sampling(tmp_path):
    """Conformer projection includes 'sampling' in available_views when present."""
    history = _make_history()
    _write_sampling_fixture(tmp_path, history)
    result_confsearch = tmp_path / "RESULT" / "confsearch"
    result_confsearch.mkdir(parents=True, exist_ok=True)
    (result_confsearch / "confsearch_manifest.json").write_text(
        json.dumps(
            {
                "conformers": [
                    {"conf_id": "C1", "free_energy_hartree": -100.0, "rank": 1},
                ]
            }
        )
    )

    graph = build_energy_graph_from_job(
        "job-d003",
        workflow="Confsearch",
        method=None,
        work_dir=tmp_path,
        view=None,
    )

    assert "conformer" in graph["available_views"]
    assert "sampling" in graph["available_views"]


def test_dispatch_confsearch_available_views_without_sampling(tmp_path):
    """Conformer projection has only ['conformer'] when no sampling history."""
    result_confsearch = tmp_path / "RESULT" / "confsearch"
    result_confsearch.mkdir(parents=True, exist_ok=True)
    (result_confsearch / "confsearch_manifest.json").write_text(
        json.dumps(
            {
                "conformers": [
                    {"conf_id": "C1", "free_energy_hartree": -100.0, "rank": 1},
                ]
            }
        )
    )

    graph = build_energy_graph_from_job(
        "job-d004",
        workflow="Confsearch",
        method=None,
        work_dir=tmp_path,
        view=None,
    )

    assert graph["available_views"] == ["conformer"]


def test_dispatch_confsearch_bogus_view_falls_back(tmp_path):
    """Bogus view falls back to default conformer."""
    result_confsearch = tmp_path / "RESULT" / "confsearch"
    result_confsearch.mkdir(parents=True, exist_ok=True)
    (result_confsearch / "confsearch_manifest.json").write_text(
        json.dumps(
            {
                "conformers": [
                    {"conf_id": "C1", "free_energy_hartree": -100.0, "rank": 1},
                ]
            }
        )
    )

    graph = build_energy_graph_from_job(
        "job-d005",
        workflow="Confsearch",
        method=None,
        work_dir=tmp_path,
        view="bogus_view",
    )

    # Bogus view falls back to default conformer
    assert graph["view_type"] == "conformer"


def test_dispatch_xtbmd_censo_energy_also_exposes_sampling(tmp_path):
    """xtbmd_censo_energy workflow also exposes sampling when present."""
    history = _make_history()
    _write_sampling_fixture(tmp_path, history)
    result_confsearch = tmp_path / "RESULT" / "confsearch"
    result_confsearch.mkdir(parents=True, exist_ok=True)
    (result_confsearch / "confsearch_manifest.json").write_text(
        json.dumps(
            {
                "conformers": [
                    {"conf_id": "C1", "free_energy_hartree": -100.0, "rank": 1},
                ]
            }
        )
    )

    graph = build_energy_graph_from_job(
        "job-d006",
        workflow="xtbmd_censo_energy",
        method=None,
        work_dir=tmp_path,
        view=None,
    )

    assert "sampling" in graph["available_views"]


# ── sanitization ──────────────────────────────────────────────────────────


def test_sampling_graph_survives_non_finite(tmp_path):
    """NaN energies in sampling history are sanitized to None."""
    from acp.results.sampling_graph import build_sampling_energy_graph

    history = _make_history(energies=[-100.0, float("nan"), -98.0])
    _write_sampling_fixture(tmp_path, history)

    graph = build_sampling_energy_graph("job-san", tmp_path)
    assert graph is not None
    # The NaN energy node should have energy=None
    potential = next(s for s in graph["series"] if s["id"] == "energy_potential")
    assert potential["values"][1] is None


# ── annotation type membership ────────────────────────────────────────────


def test_annotation_types_include_new_basin():
    """new_basin is a registered annotation type."""
    assert "new_basin" in ANNOTATION_TYPES
