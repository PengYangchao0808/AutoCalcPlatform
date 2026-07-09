"""Tests for the structure intake parsers and API endpoints."""

from __future__ import annotations

from collections.abc import Generator
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def make_client(tmp_path: Path, max_running: int = 2) -> TestClient:
    os.environ["ACP_RUN_ROOT"] = str(tmp_path)
    from acp.api.server import create_app
    return TestClient(create_app(run_root=tmp_path, max_running=max_running))


@pytest.fixture()
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    with make_client(tmp_path, max_running=2) as test_client:
        yield test_client


WATER_XYZ = "3\nwater\nO 0.0 0.0 0.0\nH 0.7586 0.5043 0.0\nH -0.7586 0.5043 0.0\n"

ETHANOL_GJF = (
    "#n B3LYP/6-31G(d) opt\n\n"
    "ethanol\n\n"
    "0 1\n"
    "C  -0.9254  0.0742  0.0328\n"
    "C   0.5123 -0.4192 -0.0743\n"
    "O   1.3778  0.4494  0.6044\n"
    "H  -1.0225  1.0731 -0.4429\n"
    "H  -1.6044 -0.6393 -0.4830\n"
    "H  -1.2236  0.1472  1.1002\n"
    "H   0.8058 -0.5060 -1.1451\n"
    "H   0.5852 -1.4258  0.3853\n"
    "H   1.4948  1.2463  0.0225\n\n"
)


def test_parse_single_xyz(client: TestClient) -> None:
    response = client.post("/api/v1/structures/parse", json={
        "content": WATER_XYZ,
        "format": "auto",
        "filename": "water.xyz",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert len(body["structures"]) == 1
    s = body["structures"][0]
    assert s["atom_count"] == 3
    assert s["formula"] == "H2O"
    assert s["has_3d"] is True
    assert s["original_format"] == "xyz"


def test_parse_multiframe_xyz(client: TestClient) -> None:
    content = WATER_XYZ + WATER_XYZ
    response = client.post("/api/v1/structures/parse", json={
        "content": content,
        "format": "xyz",
        "filename": "traj.xyz",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert len(body["structures"]) == 2
    assert all(s["atom_count"] == 3 for s in body["structures"])


def test_parse_invalid_xyz(client: TestClient) -> None:
    response = client.post("/api/v1/structures/parse", json={
        "content": "not a valid xyz at all",
        "format": "xyz",
        "filename": "bad.xyz",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert len(body["errors"]) > 0


def test_parse_gjf_with_charge_mult(client: TestClient) -> None:
    response = client.post("/api/v1/structures/parse", json={
        "content": ETHANOL_GJF,
        "format": "auto",
        "filename": "ethanol.gjf",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    s = body["structures"][0]
    assert s["atom_count"] == 9
    assert s["charge"] == 0
    assert s["multiplicity"] == 1
    assert s["formula"] == "C2H6O"


def test_parse_smiles_list(client: TestClient) -> None:
    response = client.post("/api/v1/structures/parse", json={
        "content": "CCO\nCC",
        "format": "smiles",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert len(body["structures"]) == 2
    assert body["structures"][0]["formula"] == "C2H6O"
    assert body["structures"][1]["formula"] == "C2H6"


def test_parse_smiles_invalid(client: TestClient) -> None:
    response = client.post("/api/v1/structures/parse", json={
        "content": "not-a-smiles",
        "format": "smiles",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert any("invalid SMILES" in e for e in body["errors"])


def test_parse_empty_content(client: TestClient) -> None:
    response = client.post("/api/v1/structures/parse", json={
        "content": "",
        "format": "auto",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False


def test_upload_xyz_file(client: TestClient) -> None:
    response = client.post(
        "/api/v1/uploads",
        files={"file": ("water.xyz", WATER_XYZ.encode(), "chemical/xyz")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["filename"] == "water.xyz"
    assert len(body["structures"]) == 1
    assert body["structures"][0]["formula"] == "H2O"
    assert body["upload_id"].startswith("up_")


def test_upload_rejects_path_traversal(client: TestClient) -> None:
    response = client.post(
        "/api/v1/uploads",
        files={"file": ("../../etc/passwd", b"test", "application/octet-stream")},
    )
    assert response.status_code == 400


def test_upload_empty_body(client: TestClient) -> None:
    response = client.post(
        "/api/v1/uploads",
        files={"file": ("empty.xyz", b"", "chemical/xyz")},
    )
    assert response.status_code == 400
