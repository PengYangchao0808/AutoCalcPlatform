"""Round-9 M2: PES publishes addressable scan-frame products in its manifest.

The external agent could see ``pes_ts_frame_005`` only inside a JSON profile, and
BatchOptimize's ``from_artifact`` had no structure product to read.  These tests
pin the round-9 contract:

* every scan frame that exists on disk becomes a ``structure`` product
  (``RESULT/structures/frame_<NNN>.xyz``) carrying ``frame_index`` /
  ``candidate_id`` / energy metadata;
* the published geometry is the scan frame's own bytes (a hard link, not a
  second copy);
* a frame whose geometry is gone is skipped, never invented;
* publication is capped at ``PES_MAX_PUBLISHED_FRAMES``, keeping both extremes
  and every candidate neighbourhood, with the counters recorded in the
  manifest;
* the manual-only gate is unchanged: raw scan frames are not Batch inputs until
  a review publishes a selection — and then ``from_artifact`` works end to end.

No real QC runs: the scan frames are synthetic XYZ files.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from acp.calculations.batch.loaders import load_batch_request, load_items_from_result_manifest
from acp.calculations.pes.outputs import (
    PES_FRAME_STRUCTURES_SUBDIR,
    PES_MAX_PUBLISHED_FRAMES,
    persist_pes_outputs,
)
from acp.calculations.pes.review import save_pes_review
from acp.storage.manifest import MANIFEST_FILENAME, ProductKind, ResultManifest

_SCAN_DIR_REL = "WORK/07_PATH/pes_scan_001"


def _xyz(index: int) -> str:
    return (
        "3\n"
        f"scan frame {index}\n"
        "O 0.0000000000 0.0000000000 0.0000000000\n"
        f"H 0.0000000000 0.0000000000 {0.96 + index * 0.01:.10f}\n"
        "H 0.0000000000 0.0000000000 -0.9600000000\n"
    )


def _scan_result(
    root: Path,
    *,
    count: int = 3,
    missing: tuple[int, ...] = (),
    candidates: tuple[int, ...] = (),
) -> dict[str, object]:
    """Materialise *count* frames on disk and return the scan-result mapping."""
    scan_dir = root / _SCAN_DIR_REL
    frames_dir = scan_dir / "scan_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    frames: list[dict[str, object]] = []
    for index in range(count):
        if index not in missing:
            (frames_dir / f"frame_{index:03d}.xyz").write_text(_xyz(index), encoding="utf-8")
        frame: dict[str, object] = {
            "index": index,
            "target_coordinate": 1.2 + index * 0.1,
            "actual_coordinate": 1.2 + index * 0.1,
            "coordinate_unit": "angstrom",
            "geometry_path": f"scan_frames/frame_{index:03d}.xyz",
            "scan_energy_hartree": -76.0 - index * 0.01,
            "single_point_energy_hartree": None,
            "single_point_status": "skipped",
            "optimization_converged": True,
            "frame_role": "constrained_optimization",
            "constraint_residual_ok": True,
            "max_constraint_residual": 0.002,
        }
        frames.append(frame)
    return {
        "mode": "bond_length_scan",
        "frames": frames,
        "quality": {"scan_complete": True},
        "profile": {},
        "scan_dir": str(scan_dir),
        "scan_dir_rel": _SCAN_DIR_REL,
        "coordinate": {
            "kind": "distance",
            "atoms": [0, 1],
            "unit": "angstrom",
            "start": 1.2,
            "end": 1.2 + max(count - 1, 0) * 0.1,
            "n_points": count,
        },
        "ts_recommendations": [
            {"frame_index": index, "candidate_id": f"ts_guess_{index}"} for index in candidates
        ],
        "int_recommendations": [],
    }


def _manifest(root: Path) -> ResultManifest:
    return ResultManifest.read(root / "RESULT")


def _products(root: Path) -> dict[str, object]:
    return {product.id: product for product in _manifest(root).products}


def _frame_products(root: Path) -> list:
    return [
        product for product in _manifest(root).products if product.kind is ProductKind.STRUCTURE
    ]


# ── addressable frame products ──────────────────────────────────────────


def test_every_scan_frame_is_an_addressable_structure_product(tmp_path: Path) -> None:
    persist_pes_outputs(tmp_path, scan_result=_scan_result(tmp_path, count=3), task_id="t1")

    products = _products(tmp_path)
    # The two pre-existing products stay; each real frame is added.
    assert {"pes_profile", "pes_recommendations"} <= set(products)
    frames = _frame_products(tmp_path)
    assert [product.id for product in frames] == ["frame_000", "frame_001", "frame_002"]
    for product in frames:
        relative = f"{PES_FRAME_STRUCTURES_SUBDIR}/{product.id}.xyz"
        assert product.path == relative
        published = tmp_path / "RESULT" / relative
        assert published.is_file()
        # A complete single geometry, not a trajectory.
        assert published.read_text(encoding="utf-8").count("\n") == 5


def test_frame_metadata_carries_the_curve_facts(tmp_path: Path) -> None:
    persist_pes_outputs(
        tmp_path,
        scan_result=_scan_result(tmp_path, count=3, candidates=(1,)),
        task_id="t1",
    )

    metadata = _products(tmp_path)["frame_001"].metadata
    assert metadata["frame_index"] == 1
    assert metadata["role"] == "scan_frame"
    assert metadata["selection_source"] == "scan"
    # The stable identity an agent publishes a selection with.
    assert metadata["candidate_id"] == "frame_001"
    assert metadata["energy_hartree"] == pytest.approx(-76.01)
    assert metadata["energy_source"] == "scan"
    assert metadata["scan_coordinate"] == {
        "kind": "distance",
        "atoms": [0, 1],
        "unit": "angstrom",
        "target": pytest.approx(1.3),
        "actual": pytest.approx(1.3),
    }
    assert metadata["residual"] == pytest.approx(0.002)
    assert metadata["accepted"] is True
    assert metadata["direction"] == "increasing"

    # A single-point energy wins over the scan energy when the profile has one.
    scan_result = _scan_result(tmp_path / "second", count=1)
    (scan_result["frames"][0])["single_point_energy_hartree"] = -76.5  # type: ignore[index]
    persist_pes_outputs(tmp_path / "second", scan_result=scan_result, task_id="t2")
    metadata = _products(tmp_path / "second")["frame_000"].metadata
    assert metadata["energy_hartree"] == pytest.approx(-76.5)
    assert metadata["energy_source"] == "single_point"


def test_published_geometry_is_not_a_second_copy(tmp_path: Path) -> None:
    persist_pes_outputs(tmp_path, scan_result=_scan_result(tmp_path, count=2), task_id="t1")

    source = tmp_path / _SCAN_DIR_REL / "scan_frames" / "frame_001.xyz"
    published = tmp_path / "RESULT" / PES_FRAME_STRUCTURES_SUBDIR / "frame_001.xyz"
    # Same inode: the product path is addressable, the bytes are not duplicated.
    assert source.stat().st_ino == published.stat().st_ino
    assert published.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")


def test_missing_frame_geometry_is_skipped_not_invented(tmp_path: Path) -> None:
    persist_pes_outputs(
        tmp_path,
        scan_result=_scan_result(tmp_path, count=3, missing=(1,)),
        task_id="t1",
    )

    ids = [product.id for product in _frame_products(tmp_path)]
    assert ids == ["frame_000", "frame_002"]
    assert not (tmp_path / "RESULT" / PES_FRAME_STRUCTURES_SUBDIR / "frame_001.xyz").exists()
    counters = _products(tmp_path)["pes_profile"].metadata
    assert counters["frames_total"] == 3
    assert counters["frames_selected"] == 3
    assert counters["frames_published"] == 2
    assert counters["frames_missing"] == 1
    assert counters["truncated"] is False
    assert counters["max_published_frames"] == PES_MAX_PUBLISHED_FRAMES


def test_publication_is_capped_and_keeps_extremes_and_candidates(tmp_path: Path) -> None:
    count = PES_MAX_PUBLISHED_FRAMES + 50
    persist_pes_outputs(
        tmp_path,
        scan_result=_scan_result(tmp_path, count=count, candidates=(120,)),
        task_id="t1",
    )

    frames = _frame_products(tmp_path)
    assert len(frames) == PES_MAX_PUBLISHED_FRAMES
    ids = [product.id for product in frames]
    # Both extremes survive the cap...
    assert ids[0] == "frame_000"
    assert ids[-1] == f"frame_{count - 1:03d}"
    # ...and the candidate neighbourhood is always published.
    for index in (119, 120, 121):
        assert f"frame_{index:03d}" in ids
    # Every published path is real and parseable.
    for product in frames:
        assert (tmp_path / "RESULT" / product.path).is_file()
    counters = _products(tmp_path)["pes_profile"].metadata
    assert counters["frames_total"] == count
    assert counters["frames_selected"] == PES_MAX_PUBLISHED_FRAMES
    assert counters["truncated"] is True
    profile = json.loads(
        (tmp_path / "RESULT" / "pes_search" / "pes_profile.json").read_text(encoding="utf-8")
    )
    assert profile["frame_products"]["truncated"] is True
    assert profile["frame_products"]["frames_published"] == PES_MAX_PUBLISHED_FRAMES


# ── the Batch edge: manual gate first, then from_artifact ───────────────


def test_raw_scan_frames_stay_behind_the_manual_confirmation_gate(tmp_path: Path) -> None:
    persist_pes_outputs(tmp_path, scan_result=_scan_result(tmp_path, count=3), task_id="t1")

    # Addressable frames are audit evidence, not confirmed picks: the
    # manual-only policy still refuses an implicit Batch input.
    with pytest.raises(ValueError, match="no pes_review.json"):
        load_items_from_result_manifest(tmp_path)


def test_confirmed_selection_makes_from_artifact_work(tmp_path: Path) -> None:
    persist_pes_outputs(tmp_path, scan_result=_scan_result(tmp_path, count=3), task_id="t1")
    save_pes_review(
        tmp_path,
        job_id="job_001",
        candidates=[
            {"frame_index": 1, "role": "TS", "candidate_id": "pes_ts_frame_001"},
        ],
    )

    request = {
        "schema_version": "batch_structures_v1",
        "items": [
            {
                "source_type": "result_manifest",
                "from_artifact": str(tmp_path / "RESULT" / MANIFEST_FILENAME),
            }
        ],
    }
    items = load_batch_request(request)

    assert [item.candidate_id for item in items] == ["pes_ts_frame_001"]
    assert [item.tag for item in items] == ["TS"]
    assert "pes_ts_frame_001" in items[0].xyz.splitlines()[1]

    # The review-owned structure products are the only loadable picks; the raw
    # scan-frame products are still excluded.
    loaded = load_items_from_result_manifest(tmp_path)
    assert [item.candidate_id for item in loaded] == ["pes_ts_frame_001"]
