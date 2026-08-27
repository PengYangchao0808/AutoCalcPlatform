"""Editable S2 candidate marking (s2_candidate_v1) tests.

Covers the TS/INT candidate lifecycle: review-model legacy compatibility,
any-frame add / reclassify / cancel through the API, XYZ materialization
under RESULT/structures/s2_candidates/, the s2_candidate_manifest.json and
result_manifest.json products, downstream batch default-all inclusion, explicit select
subsets, downstream handoff, and the energy-graph user-state projection.
"""

# pyright: reportAny=false, reportExplicitAny=false, reportArgumentType=false, reportAttributeAccessIssue=false, reportFunctionMemberAccess=false, reportMissingParameterType=false, reportPossiblyUnboundVariable=false, reportPrivateLocalImportUsage=false, reportPrivateUsage=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnusedCallResult=false, reportUnusedFunction=false, reportUnusedParameter=false, reportUnannotatedClassAttribute=false
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from acp.mechanism import bond_scan as bond_scan_module
from acp.mechanism.scan_manifest import (
    S2_CANDIDATE_MANIFEST_NAME,
    materialize_s2_candidates,
    read_s2_candidate_manifest,
    read_s2_review,
)
from acp.mechanism.scan_models import ReviewCandidate, ScanReview
from acp.scheduler.jobs import JobSpec
from acp.storage.manifest import ResultManifest
from tests.test_acp_s2_bond_length_scan import (
    _api_manager,
    _fake_scan,
    _fake_sp,
    _run_scan,
    _scan_request,
    _StubRunner,
    make_client,
)


@pytest.fixture
def fake_orca(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(bond_scan_module.ORCAInterface, "relaxed_scan", _fake_scan(peak=True))
    monkeypatch.setattr(bond_scan_module.ORCAInterface, "single_point", _fake_sp(peak=True))


def _manifest_path(tmp_path: Path) -> Path:
    return tmp_path / "job_work" / "RESULT" / "mechanism" / "s2_path_manifest.json"


def _frame_xyz(tmp_path: Path, frame_index: int) -> Path:
    return (
        tmp_path
        / "job_work"
        / "WORK"
        / "02_SEARCH"
        / "s2_bond_scan_001"
        / "scan_frames"
        / f"frame_{frame_index:03d}.xyz"
    )


# ── review model serialization + legacy compat ──────────────────────────


class TestReviewModels:
    def test_candidates_roundtrip(self) -> None:
        review = ScanReview.from_dict(
            {
                "status": "confirmed",
                "candidates": [
                    {"candidate_id": "ts_guess_001", "frame_index": 6, "role": "ts"},
                    {"candidate_id": "manual_frame_012", "frame_index": 12, "role": "intermediate"},
                ],
            }
        )
        assert review.status == "confirmed"
        assert [c.candidate_id for c in review.candidates] == [
            "ts_guess_001",
            "manual_frame_012",
        ]
        dumped = review.to_dict()
        assert dumped["candidates"][1]["role"] == "intermediate"

    def test_legacy_selected_lists_still_read(self) -> None:
        review = ScanReview.from_dict({"status": "confirmed", "selected_ts": ["ts_guess_008"]})
        assert review.selected_ts == ("ts_guess_008",)
        assert review.candidates == ()

    def test_malformed_candidates_skipped(self) -> None:
        review = ScanReview.from_dict(
            {
                "status": "confirmed",
                "candidates": [{"frame_index": 1, "role": "ts"}, "junk", None],
            }
        )
        assert review.candidates == ()

    def test_role_alias_normalization(self) -> None:
        candidate = ReviewCandidate.from_dict(
            {"candidate_id": "x", "frame_index": 3, "role": "int"}
        )
        assert candidate.role == "intermediate"
        with pytest.raises(ValueError, match="role"):
            ReviewCandidate.from_dict({"candidate_id": "x", "frame_index": 3, "role": "nope"})


# ── materialization ─────────────────────────────────────────────────────


class TestMaterialize:
    def test_materialize_copies_xyz_and_writes_manifests(self, tmp_path: Path, fake_orca) -> None:
        payload = _run_scan(tmp_path)
        manifest_path = _manifest_path(tmp_path)
        ts_rec = payload["recommendations"]["ts"][0]

        summary = materialize_s2_candidates(
            manifest_path,
            payload,
            [
                ReviewCandidate(
                    candidate_id=ts_rec["candidate_id"],
                    frame_index=int(ts_rec["frame_index"]),
                    role="ts",
                ),
                ReviewCandidate(candidate_id="", frame_index=12, role="intermediate"),
            ],
        )

        structures = tmp_path / "job_work" / "RESULT" / "structures" / "s2_candidates"
        assert (structures / f"{ts_rec['candidate_id']}.xyz").is_file()
        assert (structures / "manual_frame_012.xyz").is_file()
        assert (structures / "manual_frame_012.xyz").read_text().startswith("6")
        # Materialized candidates carry the canonical TAG comment (batch plan §4)
        ts_xyz = (structures / f"{ts_rec['candidate_id']}.xyz").read_text(encoding="utf-8")
        ts_comment = ts_xyz.splitlines()[1]
        assert ts_comment.startswith("TAG: TS")
        assert f"candidate_id={ts_rec['candidate_id']}" in ts_comment
        manual_xyz = (structures / "manual_frame_012.xyz").read_text(encoding="utf-8")
        assert manual_xyz.splitlines()[1].startswith("TAG: INT")

        candidate_manifest = read_s2_candidate_manifest(manifest_path)
        assert candidate_manifest is not None
        assert candidate_manifest["schema_version"] == "s2_candidate_v1"
        assert candidate_manifest["source_manifest"] == "RESULT/mechanism/s2_path_manifest.json"
        by_id = {row["candidate_id"]: row for row in candidate_manifest["candidates"]}
        assert by_id[ts_rec["candidate_id"]]["selection_source"] == "algorithm"
        assert by_id[ts_rec["candidate_id"]]["recommended_role"] == "ts"
        assert by_id["manual_frame_012"]["selection_source"] == "manual"
        assert (
            by_id["manual_frame_012"]["geometry"] == "structures/s2_candidates/manual_frame_012.xyz"
        )
        assert all(row["active"] for row in candidate_manifest["candidates"])

        review = read_s2_review(manifest_path)
        assert review is not None and review.status == "confirmed"
        assert {c.candidate_id for c in review.candidates} == {
            ts_rec["candidate_id"],
            "manual_frame_012",
        }
        assert review.selected_intermediates == ("manual_frame_012",)

        embedded = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert embedded["review"]["status"] == "confirmed"

        result_manifest = ResultManifest.read(tmp_path / "job_work" / "RESULT")
        product_ids = {p.id for p in result_manifest.products}
        assert f"s2_candidate_{ts_rec['candidate_id']}" in product_ids
        assert "s2_candidate_manual_frame_012" in product_ids
        structure_products = [p for p in result_manifest.products if p.kind.value == "structure"]
        assert len(structure_products) == 2
        assert summary["structures_dir"] == "RESULT/structures/s2_candidates"

    def test_reclassify_and_cancel_keep_lineage(self, tmp_path: Path, fake_orca) -> None:
        payload = _run_scan(tmp_path)
        manifest_path = _manifest_path(tmp_path)
        ts_rec = payload["recommendations"]["ts"][0]
        ts_id = ts_rec["candidate_id"]
        ts_frame = int(ts_rec["frame_index"])

        materialize_s2_candidates(
            manifest_path,
            payload,
            [ReviewCandidate(candidate_id=ts_id, frame_index=ts_frame, role="intermediate")],
        )
        candidate_manifest = read_s2_candidate_manifest(manifest_path)
        assert candidate_manifest is not None
        rows = {row["candidate_id"]: row for row in candidate_manifest["candidates"]}
        assert rows[ts_id]["role"] == "intermediate"
        assert rows[ts_id]["recommended_role"] == "ts"
        assert rows[ts_id]["active"] is True

        payload2 = json.loads(manifest_path.read_text(encoding="utf-8"))
        materialize_s2_candidates(
            manifest_path,
            payload2,
            [ReviewCandidate(candidate_id="", frame_index=2, role="ts")],
        )
        candidate_manifest2 = read_s2_candidate_manifest(manifest_path)
        assert candidate_manifest2 is not None
        rows2 = {row["candidate_id"]: row for row in candidate_manifest2["candidates"]}
        assert rows2[ts_id]["active"] is False
        assert rows2[ts_id]["role"] == "intermediate"
        assert rows2["manual_frame_002"]["active"] is True

        review = read_s2_review(manifest_path)
        assert review is not None
        assert review.active_candidate_ids() == ("manual_frame_002",)

    def test_duplicate_frame_rejected(self, tmp_path: Path, fake_orca) -> None:
        payload = _run_scan(tmp_path)
        manifest_path = _manifest_path(tmp_path)
        with pytest.raises(ValueError, match="more than once"):
            materialize_s2_candidates(
                manifest_path,
                payload,
                [
                    ReviewCandidate(candidate_id="", frame_index=4, role="ts"),
                    ReviewCandidate(candidate_id="", frame_index=4, role="intermediate"),
                ],
            )

    def test_unknown_frame_rejected(self, tmp_path: Path, fake_orca) -> None:
        payload = _run_scan(tmp_path)
        with pytest.raises(ValueError, match="no resolvable XYZ"):
            materialize_s2_candidates(
                _manifest_path(tmp_path),
                payload,
                [ReviewCandidate(candidate_id="", frame_index=99, role="ts")],
            )


# ── S3 candidate loading (batch_models reading order) ───────────────────


class TestLowConfirmCandidates:
    def test_no_select_includes_all_active(self, tmp_path: Path, fake_orca) -> None:
        from acp.mechanism.batch_models import load_items_from_s2_path_manifest
        from acp.mechanism.stages.low_confirm import _require_confirmed_review, read_s2_manifest

        payload = _run_scan(tmp_path)
        manifest_path = _manifest_path(tmp_path)
        ts_rec = payload["recommendations"]["ts"][0]
        materialize_s2_candidates(
            manifest_path,
            payload,
            [
                ReviewCandidate(
                    candidate_id=ts_rec["candidate_id"],
                    frame_index=int(ts_rec["frame_index"]),
                    role="ts",
                ),
                ReviewCandidate(candidate_id="", frame_index=12, role="intermediate"),
            ],
        )
        loaded = read_s2_manifest(manifest_path)
        _require_confirmed_review(loaded)
        items, _payload = load_items_from_s2_path_manifest(manifest_path, [])
        tags = {item.tag for item in items}
        assert tags == {"TS", "INT"}
        assert {item.candidate_id for item in items} == {
            ts_rec["candidate_id"],
            "manual_frame_012",
        }

    def test_explicit_select_subset(self, tmp_path: Path, fake_orca) -> None:
        from acp.mechanism.batch_models import load_items_from_s2_path_manifest

        payload = _run_scan(tmp_path)
        manifest_path = _manifest_path(tmp_path)
        ts_rec = payload["recommendations"]["ts"][0]
        materialize_s2_candidates(
            manifest_path,
            payload,
            [
                ReviewCandidate(
                    candidate_id=ts_rec["candidate_id"],
                    frame_index=int(ts_rec["frame_index"]),
                    role="ts",
                ),
                ReviewCandidate(candidate_id="", frame_index=12, role="intermediate"),
            ],
        )
        items, _payload = load_items_from_s2_path_manifest(manifest_path, [ts_rec["candidate_id"]])
        assert [item.candidate_id for item in items] == [ts_rec["candidate_id"]]
        with pytest.raises(ValueError, match="Unknown candidate ids"):
            load_items_from_s2_path_manifest(manifest_path, ["ts_guess_999"])

    def test_handoff_copies_candidate_artifacts(self, tmp_path: Path, fake_orca) -> None:
        from acp.mechanism.batch_models import load_items_from_s2_path_manifest
        from acp.mechanism.stages.handoff import copy_handoff_payload

        payload = _run_scan(tmp_path)
        manifest_path = _manifest_path(tmp_path)
        ts_rec = payload["recommendations"]["ts"][0]
        materialize_s2_candidates(
            manifest_path,
            payload,
            [
                ReviewCandidate(
                    candidate_id=ts_rec["candidate_id"],
                    frame_index=int(ts_rec["frame_index"]),
                    role="ts",
                )
            ],
        )
        target = tmp_path / "handoff"
        copied = copy_handoff_payload(manifest_path, target)
        assert copied.is_file()
        assert (target / S2_CANDIDATE_MANIFEST_NAME).is_file()
        assert (target / "structures" / "s2_candidates" / f"{ts_rec['candidate_id']}.xyz").is_file()

        items, _payload = load_items_from_s2_path_manifest(copied, [])
        assert [item.candidate_id for item in items] == [ts_rec["candidate_id"]]

    def test_lowconfirm_snapshot_survives_source_purge(self, tmp_path: Path, fake_orca) -> None:
        from acp.mechanism.batch_models import load_items_from_s2_path_manifest
        from acp.mechanism.stages.low_confirm import _snapshot_s2_candidate_package

        payload = _run_scan(tmp_path)
        manifest_path = _manifest_path(tmp_path)
        ts_rec = payload["recommendations"]["ts"][0]
        materialize_s2_candidates(
            manifest_path,
            payload,
            [
                ReviewCandidate(
                    candidate_id=ts_rec["candidate_id"],
                    frame_index=int(ts_rec["frame_index"]),
                    role="ts",
                )
            ],
        )
        copied = _snapshot_s2_candidate_package(manifest_path, tmp_path / "s3_work")
        shutil.rmtree(tmp_path / "job_work")

        items, _payload = load_items_from_s2_path_manifest(copied, [])
        assert [item.candidate_id for item in items] == [ts_rec["candidate_id"]]
        assert (tmp_path / "s3_work" / "RESULT" / "result_manifest.json").is_file()


# ── API surface ─────────────────────────────────────────────────────────


class TestS2CandidateApi:
    def _completed_job(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> str:
        manager = _api_manager(client)
        setattr(manager, "runner", _StubRunner(manager))
        spec = JobSpec(
            workflow="PESsearch",
            name="s2cand",
            input={"from": str(tmp_path / "placeholder")},
            method={"mode": "bond_length_scan"},
            output_dir=str(tmp_path / "jobs"),
            molecule_name="wd",
            task_name="PESsearch",
        )
        record = manager.submit(spec)
        payload = bond_scan_module.run_bond_length_scan(
            request=_scan_request(),
            output_dir=record.work_dir,
            config={"resources": {"nproc": 1}},
        )
        assert payload["recommendations"]["ts"]
        return record.id

    def _job_recommendations(
        self, client: TestClient, tmp_path: Path
    ) -> tuple[str, dict[str, Any]]:
        job_id = self._completed_job(client, tmp_path)
        response = client.get(f"/api/v1/jobs/{job_id}/s2/candidates")
        assert response.status_code == 200, response.text
        return job_id, response.json()["recommendations"]

    def _job_candidate(self, client: TestClient, tmp_path: Path) -> tuple[str, dict[str, Any]]:
        job_id = self._completed_job(client, tmp_path)
        recommendations = client.get(f"/api/v1/jobs/{job_id}/s2/candidates").json()[
            "recommendations"
        ]
        return job_id, recommendations["ts"][0]

    @staticmethod
    def _review_payload(candidate: dict[str, Any]) -> dict[str, Any]:
        return {
            "candidates": [
                {
                    "candidate_id": candidate["candidate_id"],
                    "frame_index": candidate["frame_index"],
                    "role": "ts",
                    "name": "selected transition state",
                }
            ]
        }

    def test_job_level_review_save_with_candidates(self, tmp_path: Path, fake_orca) -> None:
        with make_client(tmp_path) as client:
            job_id, candidate = self._job_candidate(client, tmp_path)

            response = client.post(
                f"/api/v1/jobs/{job_id}/s2/review",
                json=self._review_payload(candidate),
            )

            assert response.status_code == 200, response.text
            body = response.json()
            assert body["job_id"] == job_id
            assert body["project_id"] is None
            assert body["active_count"] == 1
            assert body["review"]["status"] == "confirmed"
            assert body["candidates"][0]["name"] == "selected transition state"
            assert body["candidate_manifest"] == "RESULT/mechanism/s2_candidate_manifest.json"

    def test_job_level_review_empty_save_is_valid(self, tmp_path: Path, fake_orca) -> None:
        with make_client(tmp_path) as client:
            job_id, _candidate = self._job_candidate(client, tmp_path)

            response = client.post(
                f"/api/v1/jobs/{job_id}/s2/review",
                json={"candidates": []},
            )

            assert response.status_code == 200, response.text
            assert response.json()["active_count"] == 0
            assert response.json()["review"]["status"] == "rejected"

    def test_s3_creation_endpoints_removed(self, tmp_path: Path, fake_orca) -> None:
        with make_client(tmp_path) as client:
            job_id, candidate = self._job_candidate(client, tmp_path)
            saved = client.post(
                f"/api/v1/jobs/{job_id}/s2/review",
                json=self._review_payload(candidate),
            )
            assert saved.status_code == 200, saved.text

            assert (
                client.post(
                    f"/api/v1/jobs/{job_id}/s2/create-lowconfirm",
                    json={"run_irc": False},
                ).status_code
                == 404
            )
            assert (
                client.post(
                    f"/api/v1/jobs/{job_id}/s2/confirm",
                    json={"candidates": [], "action": "create_s3"},
                ).status_code
                == 404
            )

    def test_job_level_review_unknown_frame_returns_422(self, tmp_path: Path, fake_orca) -> None:
        with make_client(tmp_path) as client:
            job_id, _candidate = self._job_candidate(client, tmp_path)

            response = client.post(
                f"/api/v1/jobs/{job_id}/s2/review",
                json={"candidates": [{"frame_index": 999, "role": "ts"}]},
            )

            assert response.status_code == 422
            assert "Unknown frame_index" in response.json()["detail"]

    def test_job_level_review_unknown_job_returns_404(self, tmp_path: Path) -> None:
        with make_client(tmp_path) as client:
            response = client.post(
                "/api/v1/jobs/missing-s2-job/s2/review",
                json={"candidates": []},
            )

            assert response.status_code == 404

    def test_job_level_review_rejects_wrong_workflow(self, tmp_path: Path) -> None:
        with make_client(tmp_path) as client:
            manager = _api_manager(client)
            setattr(manager, "runner", _StubRunner(manager))
            record = manager.submit(
                JobSpec(
                    workflow="fake",
                    name="not-s2",
                    input={"source": "CCO"},
                    method={"protocol": "ext"},
                    output_dir=str(tmp_path / "jobs"),
                    molecule_name="wd",
                    task_name="fake",
                )
            )

            response = client.post(
                f"/api/v1/jobs/{record.id}/s2/review",
                json={"candidates": []},
            )

            assert response.status_code == 409

    def test_full_candidate_lifecycle(self, tmp_path: Path, fake_orca) -> None:
        with make_client(tmp_path) as client:
            manager = _api_manager(client)
            job_id, recommendations = self._job_recommendations(client, tmp_path)
            ts_id = recommendations["ts"][0]["candidate_id"]
            ts_frame = int(recommendations["ts"][0]["frame_index"])

            review = client.post(
                f"/api/v1/jobs/{job_id}/s2/review",
                json={
                    "candidates": [
                        {"candidate_id": ts_id, "frame_index": ts_frame, "role": "intermediate"},
                        {"frame_index": 12, "role": "ts"},
                    ]
                },
            )
            assert review.status_code == 200, review.text
            body = review.json()
            assert body["job_id"] == job_id
            assert body["project_id"] is None
            assert body["active_count"] == 2
            assert body["review"]["status"] == "confirmed"
            rows = {row["candidate_id"]: row for row in body["candidates"]}
            assert rows[ts_id]["role"] == "intermediate"
            assert rows[ts_id]["recommended_role"] == "ts"
            assert rows["manual_frame_012"]["selection_source"] == "manual"
            assert body["structures_dir"] == "RESULT/structures/s2_candidates"

            source_record = manager.get(job_id)
            assert source_record is not None
            work_dir = Path(source_record.work_dir)
            candidates_dir = work_dir / "RESULT" / "structures" / "s2_candidates"
            assert (candidates_dir / "manual_frame_012.xyz").is_file()
            result_manifest = ResultManifest.read(work_dir / "RESULT")
            assert any(
                p.kind.value == "structure" and p.id == "s2_candidate_manual_frame_012"
                for p in result_manifest.products
            )

            graph = client.get(f"/api/v1/jobs/{job_id}/energy-graph").json()
            annotations = {
                a["candidate_id"]: a
                for a in graph["annotations"]
                if a["type"] in {"ts", "intermediate"}
            }
            assert annotations[ts_id]["type"] == "intermediate"
            assert annotations[ts_id]["recommended_type"] == "ts"
            assert annotations[ts_id]["saved"] is True
            assert annotations[ts_id]["active"] is True
            assert annotations["manual_frame_012"]["selection_source"] == "manual"
            assert graph["metadata"]["review"]["status"] == "confirmed"
            saved_ids = {
                key for key, value in annotations.items() if value["saved"] and value["active"]
            }
            assert saved_ids == {ts_id, "manual_frame_012"}

            assert client.post(f"/api/v1/jobs/{job_id}/s3", json={}).status_code == 404

    def test_unsaved_recommendations_projected_as_pending(self, tmp_path: Path, fake_orca) -> None:
        with make_client(tmp_path) as client:
            job_id, _recs = self._job_recommendations(client, tmp_path)
            graph = client.get(f"/api/v1/jobs/{job_id}/energy-graph").json()
            ts_annotations = [
                a for a in graph["annotations"] if a["type"] in {"ts", "intermediate"}
            ]
            assert ts_annotations
            assert all(a["saved"] is False for a in ts_annotations)
            assert all(a["active"] is True for a in ts_annotations)
            assert graph["metadata"]["review"]["status"] == "pending"

    def test_legacy_payload_still_accepted(self, tmp_path: Path, fake_orca) -> None:
        with make_client(tmp_path) as client:
            job_id, recommendations = self._job_recommendations(client, tmp_path)
            ts_id = recommendations["ts"][0]["candidate_id"]
            review = client.post(
                f"/api/v1/jobs/{job_id}/s2/review",
                json={"selected_ts": [ts_id]},
            )
            assert review.status_code == 200, review.text
            assert review.json()["review"]["selected_ts"] == [ts_id]
            assert any(
                row["candidate_id"] == ts_id and row["role"] == "ts"
                for row in review.json()["candidates"]
            )

    def test_cancel_marking_deactivates_candidate(self, tmp_path: Path, fake_orca) -> None:
        with make_client(tmp_path) as client:
            job_id, recommendations = self._job_recommendations(client, tmp_path)
            ts_id = recommendations["ts"][0]["candidate_id"]
            ts_frame = int(recommendations["ts"][0]["frame_index"])
            first = client.post(
                f"/api/v1/jobs/{job_id}/s2/review",
                json={
                    "candidates": [{"candidate_id": ts_id, "frame_index": ts_frame, "role": "ts"}]
                },
            )
            assert first.status_code == 200

            second = client.post(
                f"/api/v1/jobs/{job_id}/s2/review",
                json={"candidates": []},
            )
            assert second.status_code == 200
            rows = {row["candidate_id"]: row for row in second.json()["candidates"]}
            assert rows[ts_id]["active"] is False
            assert second.json()["review"]["status"] == "rejected"

            graph = client.get(f"/api/v1/jobs/{job_id}/energy-graph").json()
            annotation = next(a for a in graph["annotations"] if a.get("candidate_id") == ts_id)
            assert annotation["active"] is False
            assert annotation["recommended_type"] == "ts"

    def test_validation_errors(self, tmp_path: Path, fake_orca) -> None:
        with make_client(tmp_path) as client:
            job_id, _recs = self._job_recommendations(client, tmp_path)
            unknown_frame = client.post(
                f"/api/v1/jobs/{job_id}/s2/review",
                json={"candidates": [{"frame_index": 99, "role": "ts"}]},
            )
            assert unknown_frame.status_code == 422

            dup_frame = client.post(
                f"/api/v1/jobs/{job_id}/s2/review",
                json={
                    "candidates": [
                        {"frame_index": 4, "role": "ts"},
                        {"frame_index": 4, "role": "intermediate"},
                    ]
                },
            )
            assert dup_frame.status_code == 422

            bad_role = client.post(
                f"/api/v1/jobs/{job_id}/s2/review",
                json={"candidates": [{"frame_index": 4, "role": "transition"}]},
            )
            assert bad_role.status_code == 422

            legacy_unknown = client.post(
                f"/api/v1/jobs/{job_id}/s2/review",
                json={"selected_ts": ["ts_guess_999"]},
            )
            assert legacy_unknown.status_code == 422

    def test_s3_creation_endpoint_removed(self, tmp_path: Path, fake_orca) -> None:
        with make_client(tmp_path) as client:
            job_id, _recs = self._job_recommendations(client, tmp_path)
            assert client.post(f"/api/v1/jobs/{job_id}/s3", json={}).status_code == 404


# ── module import guard ─────────────────────────────────────────────────

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
