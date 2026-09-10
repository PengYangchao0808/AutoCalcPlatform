"""Tests for acp.results.frame_candidate_annotations — projection service."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from acp.results.frame_candidate_annotations import build_frame_candidate_annotations
from acp.results.frame_candidates import save_frame_candidate

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SAMPLE_XYZ = (
    "3\nsample molecule\nC  0.000  0.000  0.000\nH  0.000  0.000  1.089\nH  0.000  0.943 -0.363\n"
)


def _write_scan_task(root: Path) -> None:
    result_traj = root / "RESULT" / "trajectories"
    result_traj.mkdir(parents=True, exist_ok=True)
    for idx in (0, 1):
        (result_traj / f"scan_frame_{idx:03d}.xyz").write_text(_SAMPLE_XYZ, encoding="utf-8")
    trajectory = {
        "workflow": "scan",
        "frame_count": 2,
        "frames": [
            {"index": 0, "path": "trajectories/scan_frame_000.xyz", "energy_hartree": -100.0},
            {"index": 1, "path": "trajectories/scan_frame_001.xyz", "energy_hartree": -99.5},
        ],
    }
    (result_traj / "scan_trajectory.json").write_text(
        json.dumps(trajectory, indent=2), encoding="utf-8"
    )


def _write_opt_task(root: Path) -> None:
    opt_dir = root / "WORK" / "03_OPT"
    opt_dir.mkdir(parents=True, exist_ok=True)
    for idx in range(3):
        (opt_dir / f"cycle_{idx:03d}.xyz").write_text(_SAMPLE_XYZ, encoding="utf-8")
    trajectory = {
        "status": "converged",
        "source": "ORCA output",
        "cycles": [
            {
                "scf_energy_hartree": -100.0 + idx * 0.01,
                "rms_gradient": 1e-3,
                "max_gradient": 5e-3,
                "geometry_ref": f"cycle_{idx:03d}.xyz",
            }
            for idx in range(3)
        ],
    }
    (opt_dir / "optimization_trajectory.json").write_text(
        json.dumps(trajectory, indent=2), encoding="utf-8"
    )


@pytest.fixture()
def scan_task(tmp_path: Path) -> Path:
    _write_scan_task(tmp_path)
    return tmp_path


@pytest.fixture()
def opt_task(tmp_path: Path) -> Path:
    _write_opt_task(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# Projection shape tests
# ---------------------------------------------------------------------------


class TestBuildFrameCandidateAnnotations:
    """Projection emits TrajectoryAnnotation-shaped dicts."""

    def test_ts_annotation_type(self, scan_task: Path) -> None:
        save_frame_candidate(
            scan_task, job_id="j", workflow="scan", view_type="scan",
            frame_index=0, role="TS",
        )
        annotations = build_frame_candidate_annotations(scan_task, view_type="scan")
        assert len(annotations) == 1
        ann = annotations[0]
        assert ann["type"] == "ts"
        assert ann["label"] == "TS1"
        assert ann["id"] == "candidate:scan_frame_0000"
        assert ann["frame_index"] == 0
        assert ann["candidate_id"] == "scan_frame_0000"
        assert ann["saved"] is True
        assert ann["selection_source"] == "manual_frame"

    def test_int_annotation_type(self, scan_task: Path) -> None:
        save_frame_candidate(
            scan_task, job_id="j", workflow="scan", view_type="scan",
            frame_index=0, role="INT",
        )
        annotations = build_frame_candidate_annotations(scan_task, view_type="scan")
        assert len(annotations) == 1
        assert annotations[0]["type"] == "intermediate"
        assert annotations[0]["label"] == "INT1"

    def test_view_type_filtering(self, scan_task: Path) -> None:
        save_frame_candidate(
            scan_task, job_id="j", workflow="scan", view_type="scan",
            frame_index=0, role="TS",
        )
        assert build_frame_candidate_annotations(scan_task, view_type="optimization") == []
        assert len(build_frame_candidate_annotations(scan_task, view_type="scan")) == 1

    def test_missing_authority_returns_empty(self, tmp_path: Path) -> None:
        assert build_frame_candidate_annotations(tmp_path, view_type="scan") == []

    def test_multiple_annotations_order(self, scan_task: Path) -> None:
        save_frame_candidate(
            scan_task, job_id="j", workflow="scan", view_type="scan",
            frame_index=0, role="TS",
        )
        save_frame_candidate(
            scan_task, job_id="j", workflow="scan", view_type="scan",
            frame_index=1, role="INT",
        )
        annotations = build_frame_candidate_annotations(scan_task, view_type="scan")
        assert len(annotations) == 2
        assert annotations[0]["type"] == "ts"
        assert annotations[1]["type"] == "intermediate"

    def test_metadata_contains_role_and_role_index(self, scan_task: Path) -> None:
        save_frame_candidate(
            scan_task, job_id="j", workflow="scan", view_type="scan",
            frame_index=0, role="TS",
        )
        annotations = build_frame_candidate_annotations(scan_task, view_type="scan")
        ann = annotations[0]
        assert ann["metadata"]["role"] == "TS"
        assert ann["metadata"]["role_index"] == 1
        assert ann["metadata"]["item_id"] is None

    def test_item_id_scoping(self, opt_task: Path) -> None:
        save_frame_candidate(
            opt_task, job_id="j", workflow="BatchOptimize",
            view_type="optimization", frame_index=0, role="TS", item_id="item_A",
        )
        save_frame_candidate(
            opt_task, job_id="j", workflow="BatchOptimize",
            view_type="optimization", frame_index=1, role="TS", item_id="item_B",
        )
        # With item_id filter
        ann_a = build_frame_candidate_annotations(
            opt_task, view_type="optimization", item_id="item_A",
        )
        assert len(ann_a) == 1
        assert ann_a[0]["metadata"]["item_id"] == "item_A"
        # Without item_id filter → all
        ann_all = build_frame_candidate_annotations(opt_task, view_type="optimization")
        assert len(ann_all) == 2


# ---------------------------------------------------------------------------
# Energy graph integration
# ---------------------------------------------------------------------------


class TestEnergyGraphIntegration:
    """Saved candidates appear as annotations in energy graph projections."""

    def test_scan_energy_graph_includes_saved_candidates(self, scan_task: Path) -> None:
        from acp.results.energy_graph import build_energy_graph_from_job

        save_frame_candidate(
            scan_task, job_id="j", workflow="scan", view_type="scan",
            frame_index=0, role="TS",
        )
        graph = build_energy_graph_from_job(
            "j", workflow="scan", method=None, work_dir=scan_task,
        )
        candidate_anns = [
            a for a in graph["annotations"]
            if a.get("id", "").startswith("candidate:")
        ]
        assert len(candidate_anns) == 1
        assert candidate_anns[0]["type"] == "ts"
        assert candidate_anns[0]["candidate_id"] == "scan_frame_0000"
        assert candidate_anns[0]["saved"] is True

    def test_optimization_energy_graph_includes_saved_candidates(self, opt_task: Path) -> None:
        from acp.results.energy_graph import build_energy_graph_from_job

        save_frame_candidate(
            opt_task, job_id="j", workflow="optimize", view_type="optimization",
            frame_index=0, role="INT",
        )
        graph = build_energy_graph_from_job(
            "j", workflow="optimize", method=None, work_dir=opt_task,
        )
        candidate_anns = [
            a for a in graph["annotations"]
            if a.get("id", "").startswith("candidate:")
        ]
        assert len(candidate_anns) == 1
        assert candidate_anns[0]["type"] == "intermediate"

    def test_existing_annotations_win_on_collision(self, scan_task: Path) -> None:
        """Algorithm-recommended annotations are not overwritten by candidates."""
        from acp.results.energy_graph import build_energy_graph_from_job

        save_frame_candidate(
            scan_task, job_id="j", workflow="scan", view_type="scan",
            frame_index=0, role="TS",
        )
        graph = build_energy_graph_from_job(
            "j", workflow="scan", method=None, work_dir=scan_task,
        )
        candidate_anns = [
            a for a in graph["annotations"]
            if a.get("id") == "candidate:scan_frame_0000"
        ]
        assert len(candidate_anns) == 1

    def test_unsupported_view_no_candidates(self, tmp_path: Path) -> None:
        from acp.results.energy_graph import _merge_frame_candidate_annotations, build_unavailable_energy_graph

        projection = build_unavailable_energy_graph("j", workflow="unknown", reason="test")
        result = _merge_frame_candidate_annotations(projection, tmp_path)
        assert result["annotations"] == []
