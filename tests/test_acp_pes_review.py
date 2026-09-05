"""Unit tests for the PES manual-review service (calculations/pes/review.py)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from acp.calculations.batch.loaders import load_items_from_result_manifest
from acp.calculations.pes.outputs import persist_pes_outputs
from acp.calculations.pes.review import (
    PES_REVIEW_RELATIVE_PATH,
    PesReviewError,
    RevisionConflictError,
    candidate_id_for,
    load_pes_review,
    load_pes_review_backups,
    normalize_role,
    restore_pes_review,
    save_pes_review,
)
from acp.storage.manifest import ResultManifest

FIXED_NOW = datetime(2026, 9, 3, 12, 0, 0)


def _xyz(text_comment: str = "frame") -> str:
    return "3\n" + text_comment + "\nO 0.0 0.0 0.0\nH 0.0 0.0 0.96\nH 0.0 0.0 -0.96\n"


@pytest.fixture()
def pes_task(tmp_path: Path) -> Path:
    """A minimal completed PESsearch task tree with a 3-frame profile."""
    root = tmp_path
    scan_dir = root / "WORK" / "07_PATH" / "pes_scan_001" / "scan_frames"
    scan_dir.mkdir(parents=True)
    for index in range(3):
        (scan_dir / f"frame_{index:03d}.xyz").write_text(_xyz(), encoding="utf-8")
    frames = [
        {
            "index": index,
            "target_coordinate": 1.0 + index * 0.1,
            "actual_coordinate": 1.0 + index * 0.1,
            "geometry_path": f"scan_frames/frame_{index:03d}.xyz",
            "scan_energy_hartree": -76.0 - index * 0.01,
        }
        for index in range(3)
    ]
    profile = {
        "schema_version": "pes_profile_v2",
        "workflow": "PESsearch",
        "mode": "bond_length_scan",
        "status": "completed",
        "scan_dir": "WORK/07_PATH/pes_scan_001",
        "frames": frames,
        "frames_count": 3,
        "ts_candidates": [],
        "int_candidates": [],
    }
    pes_dir = root / "RESULT" / "pes_search"
    pes_dir.mkdir(parents=True)
    (pes_dir / "pes_profile.json").write_text(json.dumps(profile), encoding="utf-8")
    return root


class TestSavePesReview:
    def test_happy_path_writes_review_structures_and_manifest(self, pes_task: Path) -> None:
        payload = save_pes_review(
            pes_task,
            job_id="job_001",
            candidates=[
                {"frame_index": 1, "role": "TS", "name": "frame_001__TAG_TS"},
                {"frame_index": 2, "role": "INT"},
            ],
            note="Stepwise",
            now=FIXED_NOW,
        )

        review = load_pes_review(pes_task)
        assert review is not None
        assert review["schema_version"] == "pes_review_v1"
        assert review["status"] == "confirmed"
        assert review["job_id"] == "job_001"
        assert review["revision"] == 1
        assert review["confirmed_at"] == "2026-09-03T12:00:00+08:00" or review[
            "confirmed_at"
        ].startswith("2026-09-03T12:00:00")
        assert review["note"] == "Stepwise"
        assert payload["selected"] == review["selected"]

        assert [entry["candidate_id"] for entry in review["selected"]] == [
            "pes_ts_frame_001",
            "pes_int_frame_002",
        ]
        assert [entry["role"] for entry in review["selected"]] == ["TS", "INT"]
        assert all(
            entry["structure_path"].startswith("structures/") for entry in review["selected"]
        )
        assert all(entry["selection_source"] == "manual" for entry in review["selected"])

        ts_xyz = pes_task / "RESULT" / "structures" / "pes_ts_frame_001.xyz"
        int_xyz = pes_task / "RESULT" / "structures" / "pes_int_frame_002.xyz"
        assert ts_xyz.is_file() and int_xyz.is_file()
        ts_comment = ts_xyz.read_text(encoding="utf-8").splitlines()[1]
        assert ts_comment == (
            "TAG: TS | candidate_id=pes_ts_frame_001 | source=PESsearch"
            " | frame=001 | selection_source=manual"
        )
        assert ts_xyz.read_text(encoding="utf-8").count("\n") == 5

        manifest = ResultManifest.read(pes_task / "RESULT")
        structures = [p for p in manifest.products if p.kind.value == "structure"]
        assert [p.id for p in structures] == [
            "pes_candidate_pes_ts_frame_001",
            "pes_candidate_pes_int_frame_002",
        ]
        assert structures[0].metadata == {
            "candidate_id": "pes_ts_frame_001",
            "role": "TS",
            "frame_index": 1,
            "source": "PESsearch",
            "selection_source": "manual",
        }

    def test_resave_is_idempotent(self, pes_task: Path) -> None:
        selection = [{"frame_index": 1, "role": "TS"}]
        first = save_pes_review(pes_task, job_id="job_001", candidates=selection, now=FIXED_NOW)
        second = save_pes_review(pes_task, job_id="job_001", candidates=selection, now=FIXED_NOW)

        assert second["revision"] == first["revision"] + 1
        assert second["selected"] == first["selected"]
        manifest = ResultManifest.read(pes_task / "RESULT")
        structures = [p for p in manifest.products if p.kind.value == "structure"]
        assert len(structures) == 1

    def test_expected_revision_conflict(self, pes_task: Path) -> None:
        save_pes_review(pes_task, job_id="job_001", candidates=[], now=FIXED_NOW)
        with pytest.raises(RevisionConflictError):
            save_pes_review(
                pes_task,
                job_id="job_001",
                candidates=[],
                expected_revision=7,
                now=FIXED_NOW,
            )
        # Matching revision passes.
        save_pes_review(
            pes_task,
            job_id="job_001",
            candidates=[],
            expected_revision=1,
            now=FIXED_NOW,
        )

    def test_frame_out_of_range_writes_nothing(self, pes_task: Path) -> None:
        with pytest.raises(PesReviewError, match="out of range"):
            save_pes_review(
                pes_task,
                job_id="job_001",
                candidates=[{"frame_index": 99, "role": "TS"}],
                now=FIXED_NOW,
            )
        assert load_pes_review(pes_task) is None
        assert not (pes_task / "RESULT" / "structures" / "pes_ts_frame_099.xyz").exists()

    def test_invalid_role_rejected(self, pes_task: Path) -> None:
        with pytest.raises(PesReviewError, match="invalid candidate role"):
            save_pes_review(
                pes_task,
                job_id="job_001",
                candidates=[{"frame_index": 0, "role": "TSX"}],
                now=FIXED_NOW,
            )

    def test_role_aliases_accepted(self) -> None:
        assert normalize_role("ts") == "TS"
        assert normalize_role("intermediate") == "INT"
        assert normalize_role("transition_state") == "TS"
        assert normalize_role("minimum") == "INT"

    def test_escape_path_rejected(self, tmp_path: Path) -> None:
        root = tmp_path / "task"
        pes_dir = root / "RESULT" / "pes_search"
        pes_dir.mkdir(parents=True)
        profile = {
            "schema_version": "pes_profile_v2",
            "scan_dir": "../../outside",
            "frames": [{"index": 0, "geometry_path": "frame_000.xyz"}],
        }
        (pes_dir / "pes_profile.json").write_text(json.dumps(profile), encoding="utf-8")
        with pytest.raises(PesReviewError, match="escapes the task directory"):
            save_pes_review(
                root,
                job_id="job_001",
                candidates=[{"frame_index": 0, "role": "TS"}],
                now=FIXED_NOW,
            )

    def test_missing_frame_geometry_rejected(self, pes_task: Path) -> None:
        (pes_task / "WORK" / "07_PATH" / "pes_scan_001" / "scan_frames" / "frame_001.xyz").unlink()
        with pytest.raises(PesReviewError, match="missing on disk"):
            save_pes_review(
                pes_task,
                job_id="job_001",
                candidates=[{"frame_index": 1, "role": "TS"}],
                now=FIXED_NOW,
            )

    def test_duplicate_frame_and_duplicate_custom_id_rejected(self, pes_task: Path) -> None:
        with pytest.raises(PesReviewError, match="more than once"):
            save_pes_review(
                pes_task,
                job_id="job_001",
                candidates=[
                    {"frame_index": 0, "role": "TS"},
                    {"frame_index": 0, "role": "INT"},
                ],
                now=FIXED_NOW,
            )
        with pytest.raises(PesReviewError, match="duplicate candidate_id"):
            save_pes_review(
                pes_task,
                job_id="job_001",
                candidates=[
                    {"frame_index": 0, "role": "TS", "candidate_id": "dup"},
                    {"frame_index": 1, "role": "INT", "candidate_id": "dup"},
                ],
                now=FIXED_NOW,
            )

    def test_custom_candidate_id_and_name_honoured(self, pes_task: Path) -> None:
        payload = save_pes_review(
            pes_task,
            job_id="job_001",
            candidates=[
                {"frame_index": 2, "role": "TS", "candidate_id": "my_ts", "name": "显示名"}
            ],
            now=FIXED_NOW,
        )
        assert payload["selected"][0]["candidate_id"] == "my_ts"
        assert payload["selected"][0]["name"] == "显示名"
        assert (pes_task / "RESULT" / "structures" / "my_ts.xyz").is_file()

    def test_resave_replaces_previous_selection(self, pes_task: Path) -> None:
        save_pes_review(
            pes_task,
            job_id="job_001",
            candidates=[{"frame_index": 0, "role": "TS"}],
            now=FIXED_NOW,
        )
        save_pes_review(
            pes_task,
            job_id="job_001",
            candidates=[{"frame_index": 2, "role": "INT"}],
            now=FIXED_NOW,
        )
        manifest = ResultManifest.read(pes_task / "RESULT")
        structures = [p for p in manifest.products if p.kind.value == "structure"]
        assert [p.metadata["candidate_id"] for p in structures] == ["pes_int_frame_002"]
        # Old candidate file is kept on disk; only the manifest reference moves.
        assert (pes_task / "RESULT" / "structures" / "pes_ts_frame_000.xyz").is_file()

    def test_empty_selection_clears_manifest_entries(self, pes_task: Path) -> None:
        save_pes_review(
            pes_task,
            job_id="job_001",
            candidates=[{"frame_index": 0, "role": "TS"}],
            now=FIXED_NOW,
        )
        payload = save_pes_review(pes_task, job_id="job_001", candidates=[], now=FIXED_NOW)
        assert payload["selected"] == []
        manifest = ResultManifest.read(pes_task / "RESULT")
        assert [p for p in manifest.products if p.kind.value == "structure"] == []


class TestHelpers:
    def test_candidate_id_for(self) -> None:
        assert candidate_id_for("TS", 27) == "pes_ts_frame_027"
        assert candidate_id_for("INT", 36) == "pes_int_frame_036"

    def test_load_missing_review_returns_none(self, tmp_path: Path) -> None:
        assert load_pes_review(tmp_path) is None

    def test_load_corrupt_review_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / PES_REVIEW_RELATIVE_PATH
        path.parent.mkdir(parents=True)
        path.write_text("{not json", encoding="utf-8")
        assert load_pes_review(tmp_path) is None

    def test_missing_profile_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(PesReviewError, match="PES profile not found"):
            save_pes_review(tmp_path, job_id="j", candidates=[], now=FIXED_NOW)


class TestBatchLoaderIntegration:
    def test_manifest_loader_reads_confirmed_candidates(self, pes_task: Path) -> None:
        save_pes_review(
            pes_task,
            job_id="job_001",
            candidates=[
                {"frame_index": 1, "role": "TS"},
                {"frame_index": 2, "role": "INT"},
            ],
            now=FIXED_NOW,
        )
        items = load_items_from_result_manifest(pes_task)
        assert [item.candidate_id for item in items] == ["pes_ts_frame_001", "pes_int_frame_002"]
        assert [item.tag for item in items] == ["TS", "INT"]
        assert items[0].source_type == "result_manifest"
        assert "candidate_id=pes_ts_frame_001" in items[0].xyz.splitlines()[1]


class TestProductMetadataRoundtrip:
    def test_metadata_roundtrip(self, tmp_path: Path) -> None:
        result_dir = tmp_path / "RESULT"
        manifest = ResultManifest()
        manifest.add_product(
            "pes_candidate_x",
            "PESsearch TS candidate x (manual)",
            "structures/x.xyz",
            "structure",
            metadata={"candidate_id": "x", "role": "TS", "frame_index": 3},
        )
        manifest.write(result_dir)
        loaded = ResultManifest.read(result_dir)
        assert loaded.products[0].metadata == {
            "candidate_id": "x",
            "role": "TS",
            "frame_index": 3,
        }

    def test_metadata_omitted_when_empty(self, tmp_path: Path) -> None:
        result_dir = tmp_path / "RESULT"
        manifest = ResultManifest()
        manifest.add_product("plain", "Plain", "structures/plain.xyz", "structure")
        payload = manifest.write(result_dir)
        document = json.loads(payload.read_text(encoding="utf-8"))
        assert "metadata" not in document["products"][0]


class TestBackupRotation:
    def test_first_save_creates_no_backup(self, pes_task: Path) -> None:
        save_pes_review(
            pes_task,
            job_id="job_001",
            candidates=[{"frame_index": 0, "role": "TS"}],
            now=FIXED_NOW,
        )
        assert load_pes_review_backups(pes_task) == []
        review = load_pes_review(pes_task)
        assert review["revision"] == 1
        assert review["attempt"] == 1

    def test_resave_rotates_previous_state(self, pes_task: Path) -> None:
        save_pes_review(
            pes_task,
            job_id="job_001",
            candidates=[{"frame_index": 0, "role": "TS"}],
            note="round1",
            now=FIXED_NOW,
        )
        save_pes_review(
            pes_task,
            job_id="job_001",
            candidates=[{"frame_index": 1, "role": "INT"}],
            note="round2",
            now=FIXED_NOW,
        )
        backups = load_pes_review_backups(pes_task)
        assert len(backups) == 1
        assert backups[0]["n"] == 1
        assert backups[0]["note"] == "round1"
        assert backups[0]["selected_count"] == 1
        assert backups[0]["selected"][0]["candidate_id"] == "pes_ts_frame_000"
        review = load_pes_review(pes_task)
        assert review["revision"] == 2
        assert review["attempt"] == 2

    def test_backup_count_grows_with_attempts(self, pes_task: Path) -> None:
        for index in range(3):
            save_pes_review(
                pes_task,
                job_id="job_001",
                candidates=[{"frame_index": index, "role": "TS"}],
                now=FIXED_NOW,
            )
        backups = load_pes_review_backups(pes_task)
        assert [item["n"] for item in backups] == [1, 2]

    def test_restore_switches_manifest_and_rotates(self, pes_task: Path) -> None:
        save_pes_review(
            pes_task,
            job_id="job_001",
            candidates=[{"frame_index": 0, "role": "TS"}],
            note="round1",
            now=FIXED_NOW,
        )
        save_pes_review(
            pes_task,
            job_id="job_001",
            candidates=[{"frame_index": 2, "role": "INT"}],
            note="round2",
            now=FIXED_NOW,
        )
        payload = restore_pes_review(pes_task, 1, now=FIXED_NOW)

        assert payload["restored_from"] == 1
        assert payload["revision"] == 3
        assert payload["selected"][0]["candidate_id"] == "pes_ts_frame_000"
        review = load_pes_review(pes_task)
        assert review["restored_from"] == 1
        assert review["note"] == "round1"
        manifest = ResultManifest.read(pes_task / "RESULT")
        structures = [p for p in manifest.products if p.kind.value == "structure"]
        assert [p.metadata["candidate_id"] for p in structures] == ["pes_ts_frame_000"]
        # The pre-restore state (round 2) itself became a backup.
        assert [item["n"] for item in load_pes_review_backups(pes_task)] == [1, 2]
        # Restored selection drives the batch loader again.
        items = load_items_from_result_manifest(pes_task)
        assert [item.candidate_id for item in items] == ["pes_ts_frame_000"]

    def test_restore_unknown_backup_raises(self, pes_task: Path) -> None:
        save_pes_review(
            pes_task,
            job_id="job_001",
            candidates=[{"frame_index": 0, "role": "TS"}],
            now=FIXED_NOW,
        )
        with pytest.raises(PesReviewError, match="not found"):
            restore_pes_review(pes_task, 99, now=FIXED_NOW)


class TestRecommendationIsolation:
    def test_persist_registers_no_structure_products(self, tmp_path: Path) -> None:
        scan_result = {
            "mode": "bond_length_scan",
            "status": "completed",
            "scan_dir_rel": "WORK/07_PATH/pes_scan_001",
            "frames": [{"index": 0, "geometry_path": "scan_frames/frame_000.xyz"}],
            "ts_recommendations": [
                {
                    "candidate_id": "ts_guess_001",
                    "kind": "ts",
                    "frame_index": 0,
                    "geometry_path": "scan_frames/frame_000.xyz",
                    "score": 0.8,
                    "confidence": "high",
                    "reason": "peak",
                }
            ],
            "int_recommendations": [],
            "profile": {},
            "quality": {},
        }
        profile_path, manifest_path = persist_pes_outputs(
            tmp_path, scan_result=scan_result, status="completed"
        )
        assert profile_path.is_file() and manifest_path.is_file()

        manifest = ResultManifest.read(tmp_path / "RESULT")
        kinds = {p.kind.value for p in manifest.products}
        assert kinds == {"pes_profile", "report"}
        assert not [p for p in manifest.products if p.kind.value == "structure"]

        recommendations = json.loads(
            (tmp_path / "RESULT" / "pes_search" / "pes_recommendations.json").read_text(
                encoding="utf-8"
            )
        )
        assert recommendations["schema_version"] == "pes_recommendations_v1"
        assert recommendations["ts"][0]["candidate_id"] == "ts_guess_001"
        assert not (tmp_path / "RESULT" / "structures" / "ts_guess_001.xyz").exists()


class TestUnconfirmedPesGuard:
    def test_unconfirmed_pes_task_raises_actionable_error(self, pes_task: Path) -> None:
        # pes_task fixture has a profile but no pes_review.json yet.
        with pytest.raises(ValueError, match="no pes_review.json"):
            load_items_from_result_manifest(pes_task)

    def test_confirmed_pes_task_loads_normally(self, pes_task: Path) -> None:
        save_pes_review(
            pes_task,
            job_id="job_001",
            candidates=[{"frame_index": 1, "role": "TS"}],
            now=FIXED_NOW,
        )
        items = load_items_from_result_manifest(pes_task)
        assert [item.candidate_id for item in items] == ["pes_ts_frame_001"]

    def test_non_pes_task_without_structures_keeps_warning(self, tmp_path: Path) -> None:
        result_dir = tmp_path / "RESULT"
        result_dir.mkdir(parents=True)
        (result_dir / "result_manifest.json").write_text(
            json.dumps({"version": 2, "products": []}), encoding="utf-8"
        )
        assert load_items_from_result_manifest(tmp_path) == []
