"""API-level tests for the /api/v1/structure-sources endpoints."""

from __future__ import annotations

import fnmatch
import json
import os
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus

_XYZ = """\
2
charge=0 mult=1
C 0.000000 0.000000 0.000000
O 1.200000 0.000000 0.000000
"""


def _make_client(tmp_path: Path) -> TestClient:
    os.environ["ACP_RUN_ROOT"] = str(tmp_path)
    from acp.api.server import create_app

    return TestClient(create_app(run_root=tmp_path, max_running=2))


@pytest.fixture()
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    with _make_client(tmp_path) as test_client:
        yield test_client


class FakeFetcher:
    """In-memory duck-typed stand-in for RemoteResultFetcher."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files

    def walk_remote_files(self, record, include=None, exclude=None):
        patterns = include or ["*"]
        for rel in sorted(self.files):
            if any(fnmatch.fnmatch(rel, pat) for pat in patterns):
                yield rel, None

    def read_file(self, record, filename: str) -> bytes:
        if filename not in self.files:
            raise FileNotFoundError(filename)
        return self.files[filename]

    def file_exists(self, record, filename: str) -> bool:
        return filename in self.files


def _seed_job(
    client: TestClient,
    tmp_path: Path,
    job_id: str,
    *,
    workflow: str = "energy",
    project_id: str | None = "uncategorized",
    completed_at: str = "2026-08-22T10:00:00+00:00",
    status: JobStatus = JobStatus.COMPLETED,
    products: list[dict[str, Any]] | None = None,
    files: dict[str, str] | None = None,
    result: dict[str, Any] | None = None,
    remote_job_id: str | None = None,
    molecule_name: str = "",
) -> Path:
    """Insert a job record directly into the store and write its work dir."""
    work_dir = tmp_path / (project_id or "uncategorized") / job_id
    mol_dir = work_dir / "mol"
    mol_dir.mkdir(parents=True, exist_ok=True)
    if products is not None:
        (mol_dir / "result_summary.json").write_text(
            json.dumps({"version": 1, "workflow": workflow, "products": products}),
            encoding="utf-8",
        )
    for rel, content in (files or {}).items():
        target = work_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    manager = client.app.state.job_manager
    manager.store.create(
        JobRecord(
            id=job_id,
            spec=JobSpec(
                workflow=workflow,
                name=f"name-{job_id}",
                project_id=project_id,
                molecule_name=molecule_name,
            ),
            status=status,
            work_dir=str(work_dir),
            created_at="2026-08-22T09:00:00+00:00",
            updated_at="2026-08-22T09:00:00+00:00",
            completed_at=completed_at,
            project_id=project_id,
            remote_job_id=remote_job_id,
            result=result,
        )
    )
    return work_dir


def _global_min_product() -> list[dict[str, Any]]:
    return [
        {
            "label": "Global minimum structure",
            "path": "mol_global_min.xyz",
            "kind": "xyz",
            "role": "final_stable_structure",
        }
    ]


def test_recent_lists_completed_sources(client: TestClient, tmp_path: Path) -> None:
    _seed_job(
        client,
        tmp_path,
        "job1",
        products=_global_min_product(),
        files={"mol/mol_global_min.xyz": _XYZ},
    )
    response = client.get("/api/v1/structure-sources/recent")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["sources"]) == 1
    source = payload["sources"][0]
    assert source["source_id"] == "job_job1:mol/mol_global_min.xyz"
    assert source["job_id"] == "job1"
    assert source["workflow"] == "energy"
    assert source["formula"] == "CO"
    assert source["atom_count"] == 2
    assert source["remote"] is False


def test_recent_excludes_non_completed_and_excluded_workflows(
    client: TestClient, tmp_path: Path
) -> None:
    _seed_job(
        client,
        tmp_path,
        "running1",
        status=JobStatus.RUNNING,
        products=_global_min_product(),
        files={"mol/mol_global_min.xyz": _XYZ},
    )
    _seed_job(
        client,
        tmp_path,
        "sp1",
        workflow="singlepoint",
        products=_global_min_product(),
        files={"mol/mol_global_min.xyz": _XYZ},
    )
    response = client.get("/api/v1/structure-sources/recent")
    assert response.status_code == 200
    assert response.json()["sources"] == []


def test_recent_default_project_and_all_projects(client: TestClient, tmp_path: Path) -> None:
    _seed_job(
        client,
        tmp_path,
        "default1",
        products=_global_min_product(),
        files={"mol/mol_global_min.xyz": _XYZ},
    )
    _seed_job(
        client,
        tmp_path,
        "alpha1",
        project_id="alpha",
        completed_at="2026-08-22T11:00:00+00:00",
        products=_global_min_product(),
        files={"mol/mol_global_min.xyz": _XYZ},
    )

    response = client.get("/api/v1/structure-sources/recent")
    assert [s["job_id"] for s in response.json()["sources"]] == ["default1"]

    response = client.get("/api/v1/structure-sources/recent", params={"all_projects": True})
    assert [s["job_id"] for s in response.json()["sources"]] == ["alpha1", "default1"]

    response = client.get("/api/v1/structure-sources/recent", params={"project_id": "alpha"})
    assert [s["job_id"] for s in response.json()["sources"]] == ["alpha1"]


def test_recent_workflow_filter_and_limit(client: TestClient, tmp_path: Path) -> None:
    for idx in range(3):
        _seed_job(
            client,
            tmp_path,
            f"e{idx}",
            completed_at=f"2026-08-22T1{idx}:00:00+00:00",
            products=_global_min_product(),
            files={"mol/mol_global_min.xyz": _XYZ},
        )
    _seed_job(
        client,
        tmp_path,
        "nmr1",
        workflow="nmr",
        completed_at="2026-08-22T19:00:00+00:00",
        products=_global_min_product(),
        files={"mol/mol_global_min.xyz": _XYZ},
    )

    response = client.get("/api/v1/structure-sources/recent", params={"workflow": "nmr"})
    assert [s["job_id"] for s in response.json()["sources"]] == ["nmr1"]

    response = client.get("/api/v1/structure-sources/recent", params={"limit": 2})
    assert len(response.json()["sources"]) == 2

    response = client.get("/api/v1/structure-sources/recent", params={"workflow": "singlepoint"})
    assert response.json()["sources"] == []


def test_get_source_ok(client: TestClient, tmp_path: Path) -> None:
    _seed_job(
        client,
        tmp_path,
        "job1",
        products=_global_min_product(),
        files={"mol/mol_global_min.xyz": _XYZ},
    )
    response = client.get("/api/v1/structure-sources/job_job1:mol/mol_global_min.xyz")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source_id"] == "job_job1:mol/mol_global_min.xyz"
    assert payload["checksum"].startswith("sha256:")
    structure = payload["structure"]
    assert structure["formula"] == "CO"
    assert structure["atom_count"] == 2
    assert structure["original_format"] == "xyz"
    assert structure["source_type"] == "job_artifact"
    assert structure["xyz"].splitlines()[0] == "2"


def test_source_exposes_only_canonical_molecule_name_for_import(
    client: TestClient, tmp_path: Path
) -> None:
    _seed_job(
        client,
        tmp_path,
        "job1",
        molecule_name="INT_P_energy_mt5g72i5",
        products=_global_min_product(),
        files={"mol/mol_global_min.xyz": _XYZ},
    )

    listing = client.get("/api/v1/structure-sources/recent").json()["sources"]
    assert listing[0]["molecule_name"] == "INT_P_energy_mt5g72i5"
    # The artifact/file label remains separate from the inherited identity.
    detail = client.get("/api/v1/structure-sources/job_job1:mol/mol_global_min.xyz")
    assert detail.json()["structure"]["name"] == "mol_global_min.xyz"
    assert detail.json()["structure"]["molecule_name"] == "INT_P_energy_mt5g72i5"


def test_candidate_detail_preserves_role_id_and_unique_inherited_name(
    client: TestClient, tmp_path: Path
) -> None:
    ts_xyz = """\
2
TAG: TS | candidate_id=ts_guess_017 | source=PESsearch
C 0.000000 0.000000 0.000000
O 1.200000 0.000000 0.000000
"""
    work_dir = _seed_job(
        client,
        tmp_path,
        "pes1",
        workflow="PESsearch",
        molecule_name="INT_P_energy_mt5g72",
        products=None,
        files={"RESULT/structures/ts_guess_017.xyz": ts_xyz},
    )
    (work_dir / "RESULT" / "result_manifest.json").write_text(
        json.dumps(
            {
                "version": 2,
                "workflow": "PESsearch",
                "status": "completed",
                "products": [
                    {
                        "id": "s2_candidate_ts_guess_017",
                        "label": "S2 candidate ts_guess_017 (TS)",
                        "path": "structures/ts_guess_017.xyz",
                        "kind": "structure",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    listing = client.get("/api/v1/structure-sources/recent").json()["sources"]
    assert listing[0]["tag"] == "TS"
    assert listing[0]["candidate_id"] == "ts_guess_017"
    assert listing[0]["molecule_name"] == "INT_P_energy_mt5g72__ts_guess_017"

    detail = client.get(
        "/api/v1/structure-sources/job_pes1:RESULT/structures/ts_guess_017.xyz"
    ).json()["structure"]
    assert detail["tag"] == "TS"
    assert detail["candidate_id"] == "ts_guess_017"
    assert detail["molecule_name"] == "INT_P_energy_mt5g72__ts_guess_017"


def test_get_source_404_branches(client: TestClient, tmp_path: Path) -> None:
    # malformed source_id
    response = client.get("/api/v1/structure-sources/nonsense")
    assert response.status_code == 404
    assert "Invalid source_id" in response.json()["detail"]

    # unknown job
    response = client.get("/api/v1/structure-sources/job_ghost:mol/x.xyz")
    assert response.status_code == 404
    assert "Job not found" in response.json()["detail"]

    # file cleaned from disk
    _seed_job(
        client,
        tmp_path,
        "job1",
        products=_global_min_product(),
        files={"mol/mol_global_min.xyz": _XYZ},
    )
    response = client.get("/api/v1/structure-sources/job_job1:mol/deleted.xyz")
    assert response.status_code == 404
    assert "Source file not found" in response.json()["detail"]

    # not completed
    _seed_job(client, tmp_path, "job2", status=JobStatus.FAILED)
    response = client.get("/api/v1/structure-sources/job_job2:mol/mol_global_min.xyz")
    assert response.status_code == 404
    assert "not completed" in response.json()["detail"]


_REMOTE_RESULT = {
    "node": "node1",
    "remote_dir": "/remote/root/rjob1",
    "lsf_job_id": "9911",
}


def _remote_files() -> dict[str, bytes]:
    summary = json.dumps(
        {"version": 1, "workflow": "energy", "products": _global_min_product()}
    ).encode()
    return {
        "mol/result_summary.json": summary,
        "mol/mol_global_min.xyz": _XYZ.encode(),
    }


def test_remote_source_with_fetcher(client: TestClient, tmp_path: Path) -> None:
    _seed_job(client, tmp_path, "rjob1", result=dict(_REMOTE_RESULT))
    manager = client.app.state.job_manager
    manager._remote_fetcher = FakeFetcher(_remote_files())

    response = client.get("/api/v1/structure-sources/recent")
    assert response.status_code == 200
    sources = response.json()["sources"]
    assert len(sources) == 1
    assert sources[0]["remote"] is True
    assert sources[0]["needs_fetch"] is False
    assert sources[0]["source_id"] == "job_rjob1:mol/mol_global_min.xyz"

    response = client.get("/api/v1/structure-sources/job_rjob1:mol/mol_global_min.xyz")
    assert response.status_code == 200
    payload = response.json()
    assert payload["checksum"].startswith("sha256:")
    assert payload["structure"]["formula"] == "CO"


def test_remote_source_without_fetcher(client: TestClient, tmp_path: Path) -> None:
    _seed_job(client, tmp_path, "rjob1", result=dict(_REMOTE_RESULT))

    response = client.get("/api/v1/structure-sources/recent")
    assert response.status_code == 200
    sources = response.json()["sources"]
    assert len(sources) == 1
    assert sources[0]["remote"] is True
    assert sources[0]["needs_fetch"] is True

    response = client.get("/api/v1/structure-sources/job_rjob1:mol/mol_global_min.xyz")
    assert response.status_code == 404
    assert "fetcher" in response.json()["detail"]
