"""Tests for /api/v2/structure-sources endpoints."""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from acp.scheduler.structure_source_store import StructureSourceStore, source_uid_for

_XYZ_TS = """\
3
TAG: TS | candidate_id=ts_guess_001
C 0.000000 0.000000 0.000000
O 1.200000 0.000000 0.000000
H 0.000000 1.200000 0.000000
"""

_XYZ_INT = """\
3
TAG: INT | candidate_id=int_guess_001
C 0.000000 0.000000 0.000000
O 1.200000 0.000000 0.000000
H 0.000000 1.200000 0.000000
"""

_XYZ_PLAIN = """\
3
water
O 0.000000 0.000000 0.000000
H 0.950000 0.000000 0.000000
H -0.950000 0.000000 0.000000
"""


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _seed_index_entry(
    source_store: StructureSourceStore,
    job_id: str,
    *,
    rel_path: str = "RESULT/optimized.xyz",
    project_id: str | None = "uncategorized",
    workflow: str = "Confsearch",
    role: str = "",
    role_evidence: str = "",
    label: str = "Rank-1 conformer",
) -> str:
    uid = source_uid_for(job_id, rel_path)
    source_store.upsert_index_entries(
        [
            {
                "job_id": job_id,
                "relative_path": rel_path,
                "source_id": f"job_{job_id}:{rel_path}",
                "project_id": project_id,
                "job_name": f"Task {job_id}",
                "molecule_name": "ethanol",
                "workflow": workflow,
                "job_status": "completed",
                "source_kind": "final",
                "label": label,
                "candidate_id": "",
                "role": role,
                "role_evidence": role_evidence,
                "formula": "C2H6O",
                "atom_count": 9,
                "charge": 0,
                "multiplicity": 1,
                "has_3d": True,
                "remote": False,
                "availability": "available",
                "produced_at": "2026-09-01T10:00:00+00:00",
            }
        ],
        discovery_version=1,
    )
    return uid


@pytest.fixture()
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    os.environ["ACP_RUN_ROOT"] = str(tmp_path)
    from acp.api.server import create_app

    app = create_app(run_root=tmp_path, max_running=2)
    with TestClient(app) as test_client:
        yield test_client


def _get_source_store(client: TestClient) -> StructureSourceStore:
    db_path = client.app.state.db_path
    return StructureSourceStore(db_path)


# ------------------------------------------------------------------ #
# GET /structure-sources
# ------------------------------------------------------------------ #


class TestListStructureSources:
    def test_empty(self, client: TestClient) -> None:
        resp = client.get("/api/v2/structure-sources")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0
        assert "indexing" in data

    def test_with_data(self, client: TestClient, tmp_path: Path) -> None:
        ss = _get_source_store(client)
        _seed_index_entry(ss, "job_001", role="TS", role_evidence="xyz_tag")
        _seed_index_entry(ss, "job_002")
        resp = client.get("/api/v2/structure-sources", params={"all_projects": "true"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 2

    def test_filter_role(self, client: TestClient, tmp_path: Path) -> None:
        ss = _get_source_store(client)
        _seed_index_entry(ss, "job_ts", role="TS", role_evidence="xyz_tag")
        _seed_index_entry(ss, "job_plain")
        resp = client.get(
            "/api/v2/structure-sources",
            params={"role": "TS", "all_projects": "true"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        for item in data["items"]:
            assert item["role"] == "TS"

    def test_group_by_role(self, client: TestClient, tmp_path: Path) -> None:
        ss = _get_source_store(client)
        _seed_index_entry(ss, "job_ts", role="TS", role_evidence="xyz_tag")
        _seed_index_entry(
            ss,
            "job_int",
            role="INT",
            role_evidence="xyz_tag",
            label="TS int candidate",
            rel_path="RESULT/int.xyz",
        )
        _seed_index_entry(ss, "job_plain")
        resp = client.get(
            "/api/v2/structure-sources",
            params={"group_by": "role", "all_projects": "true"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "groups" in data
        role_keys = {g["key"] for g in data["groups"]}
        assert "TS" in role_keys
        assert "INT" in role_keys

    def test_pagination_cursor(self, client: TestClient, tmp_path: Path) -> None:
        ss = _get_source_store(client)
        for i in range(5):
            _seed_index_entry(ss, f"job_{i:03d}", rel_path=f"RESULT/conformer_{i:03d}.xyz")
        resp1 = client.get(
            "/api/v2/structure-sources",
            params={"all_projects": "true", "limit": 2},
        )
        data1 = resp1.json()
        assert len(data1["items"]) <= 2
        if data1["next_cursor"]:
            resp2 = client.get(
                "/api/v2/structure-sources",
                params={"all_projects": "true", "limit": 2, "cursor": data1["next_cursor"]},
            )
            assert resp2.status_code == 200

    def test_bad_cursor_422(self, client: TestClient) -> None:
        resp = client.get(
            "/api/v2/structure-sources",
            params={"cursor": "not_a_valid_cursor!!!"},
        )
        assert resp.status_code == 422

    def test_indexing_in_response(self, client: TestClient) -> None:
        resp = client.get("/api/v2/structure-sources")
        assert resp.status_code == 200
        indexing = resp.json()["indexing"]
        assert "indexed_jobs" in indexing
        assert "indexing_state" in indexing


# ------------------------------------------------------------------ #
# GET /structure-sources/facets
# ------------------------------------------------------------------ #


class TestFacets:
    def test_empty_facets(self, client: TestClient) -> None:
        resp = client.get("/api/v2/structure-sources/facets")
        assert resp.status_code == 200
        data = resp.json()
        assert "roles" in data
        assert "tags" in data
        assert "total_structures" in data
        assert "indexing" in data

    def test_facets_with_data(self, client: TestClient, tmp_path: Path) -> None:
        ss = _get_source_store(client)
        _seed_index_entry(ss, "job_ts", role="TS", role_evidence="xyz_tag")
        _seed_index_entry(ss, "job_plain")
        resp = client.get(
            "/api/v2/structure-sources/facets",
            params={"all_projects": "true"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_structures"] >= 2
        assert data["roles"]["TS"] >= 1


# ------------------------------------------------------------------ #
# GET /structure-sources/{source_uid}
# ------------------------------------------------------------------ #


class TestGetSource:
    def test_not_found(self, client: TestClient) -> None:
        resp = client.get("/api/v2/structure-sources/ss_nonexistent")
        assert resp.status_code == 404

    def test_found(self, client: TestClient, tmp_path: Path) -> None:
        ss = _get_source_store(client)
        uid = _seed_index_entry(ss, "job_get", role="TS", role_evidence="xyz_tag")
        resp = client.get(f"/api/v2/structure-sources/{uid}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["source_uid"] == uid
        assert "source_id" in data


# ------------------------------------------------------------------ #
# PATCH /structure-sources/{source_uid}/metadata
# ------------------------------------------------------------------ #


class TestPatchMetadata:
    def test_set_custom_name(self, client: TestClient, tmp_path: Path) -> None:
        ss = _get_source_store(client)
        uid = _seed_index_entry(ss, "job_rename")
        projection = ss.get(uid)
        rev = projection["metadata_revision"] or 0
        resp = client.patch(
            f"/api/v2/structure-sources/{uid}/metadata",
            json={"custom_name": "My Structure", "expected_revision": rev},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["custom_name"] == "My Structure"
        assert data["resolved_name"] == "My Structure"

    def test_restore_default(self, client: TestClient, tmp_path: Path) -> None:
        ss = _get_source_store(client)
        uid = _seed_index_entry(ss, "job_restore")
        projection = ss.get(uid)
        rev = projection["metadata_revision"] or 0
        client.patch(
            f"/api/v2/structure-sources/{uid}/metadata",
            json={"custom_name": "temp", "expected_revision": rev},
        )
        updated = ss.get(uid)
        resp = client.patch(
            f"/api/v2/structure-sources/{uid}/metadata",
            json={"custom_name": None, "expected_revision": updated["metadata_revision"]},
        )
        assert resp.status_code == 200
        assert resp.json()["custom_name"] is None

    def test_409_conflict(self, client: TestClient, tmp_path: Path) -> None:
        ss = _get_source_store(client)
        uid = _seed_index_entry(ss, "job_conflict")
        resp = client.patch(
            f"/api/v2/structure-sources/{uid}/metadata",
            json={"custom_name": "A", "expected_revision": 999},
        )
        assert resp.status_code == 409

    def test_422_missing_revision(self, client: TestClient, tmp_path: Path) -> None:
        ss = _get_source_store(client)
        uid = _seed_index_entry(ss, "job_norev")
        resp = client.patch(
            f"/api/v2/structure-sources/{uid}/metadata",
            json={"custom_name": "X"},
        )
        assert resp.status_code == 422

    def test_404_unknown(self, client: TestClient) -> None:
        resp = client.patch(
            "/api/v2/structure-sources/ss_nonexistent/metadata",
            json={"custom_name": "X", "expected_revision": 0},
        )
        assert resp.status_code == 404


# ------------------------------------------------------------------ #
# POST /structure-sources/batch-metadata
# ------------------------------------------------------------------ #


class TestBatchMetadata:
    def test_partial_success(self, client: TestClient, tmp_path: Path) -> None:
        ss = _get_source_store(client)
        uid1 = _seed_index_entry(ss, "job_b1")
        body = {
            "items": [
                {"source_uid": uid1, "add_tags": ["test"], "expected_revision": 0},
                {"source_uid": "ss_nonexistent", "add_tags": ["nope"], "expected_revision": 0},
            ]
        }
        resp = client.post("/api/v2/structure-sources/batch-metadata", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["succeeded"]) >= 1
        assert len(data["failed"]) >= 1


# ------------------------------------------------------------------ #
# Project tags
# ------------------------------------------------------------------ #


class TestProjectTags:
    def test_list_tags_empty(self, client: TestClient) -> None:
        resp = client.get("/api/v2/projects/proj_1/structure-tags")
        assert resp.status_code == 200
        assert resp.json()["tags"] == []

    def test_rename_tag(self, client: TestClient, tmp_path: Path) -> None:
        ss = _get_source_store(client)
        uid = _seed_index_entry(ss, "job_tag", project_id="proj_tag")
        ss.add_tags(uid, ["old_tag"], 0)
        resp = client.post(
            "/api/v2/projects/proj_tag/structure-tags/rename",
            json={"source": "old_tag", "target": "new_tag"},
        )
        assert resp.status_code == 200
        assert resp.json()["affected"] >= 1

    def test_remove_tag(self, client: TestClient, tmp_path: Path) -> None:
        ss = _get_source_store(client)
        uid = _seed_index_entry(ss, "job_rmtag", project_id="proj_rmtag")
        ss.add_tags(uid, ["to_remove"], 0)
        resp = client.post(
            "/api/v2/projects/proj_rmtag/structure-tags/remove",
            json={"tag": "to_remove"},
        )
        assert resp.status_code == 200
        assert resp.json()["affected"] >= 1
