"""Tests for the structure intake parsers and API endpoints."""

from __future__ import annotations

import os
from collections.abc import Generator
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


XYZ_NO_COMMENT = (
    "3\n"
    "O    0.000000    0.000000    0.117000\n"
    "H    0.757000    0.000000   -0.469000\n"
    "H   -0.757000    0.000000   -0.469000\n"
)

XYZ_WITH_CHARGE_COMMENT = (
    "3\n"
    "charge=0 mult=1\n"
    "O    0.000000    0.000000    0.117000\n"
    "H    0.757000    0.000000   -0.469000\n"
    "H   -0.757000    0.000000   -0.469000\n"
)

GAUSSIAN_INPUT = (
    "#p opt freq b3lyp/6-31g*\n"
    "\n"
    "title\n"
    "\n"
    "0 1\n"
    "C    0.0    0.0    0.0\n"
    "H    1.0    0.0    0.0\n"
    "\n"
)


def test_parse_xyz_no_comment_line() -> None:
    from acp.intake.parsers import parse_xyz_text

    result = parse_xyz_text(XYZ_NO_COMMENT)
    assert result.ok is True
    assert len(result.structures) == 1
    s = result.structures[0]
    assert s.atom_count == 3
    assert s.formula == "H2O"


def test_parse_xyz_no_comment_line_via_api(client: TestClient) -> None:
    response = client.post("/api/v1/structures/parse", json={
        "content": XYZ_NO_COMMENT,
        "format": "auto",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert len(body["structures"]) == 1
    assert body["structures"][0]["atom_count"] == 3
    assert body["structures"][0]["formula"] == "H2O"


def test_parse_xyz_charge_comment_via_api(client: TestClient) -> None:
    response = client.post("/api/v1/structures/parse", json={
        "content": XYZ_WITH_CHARGE_COMMENT,
        "format": "auto",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    s = body["structures"][0]
    assert s["charge"] == 0
    assert s["multiplicity"] == 1


def test_detect_format_gaussian_is_gjf_not_xyz() -> None:
    from acp.intake.parsers import detect_format

    fmt = detect_format("", GAUSSIAN_INPUT)
    assert fmt == "gjf", f"expected 'gjf', got '{fmt}'"


def test_detect_format_gaussian_is_gjf_via_api(client: TestClient) -> None:
    response = client.post("/api/v1/structures/parse", json={
        "content": GAUSSIAN_INPUT,
        "format": "auto",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    s = body["structures"][0]
    assert s["original_format"] == "gjf"


BARE_ATOM_XYZ = (
    "C -1.27754075530705 0.24959642600 -0.42791993623\n"
    "H -1.20726432454 1.32587325300 -0.26976639393\n"
    "H -0.50366608974 -0.11407399100 -1.09416730905\n"
    "H -2.24810610987 -0.05192132600 -0.82248080310\n"
    "O -1.13544847498 -0.00903059400 0.92359802403\n"
)


def test_parse_bare_atom_coordinates() -> None:
    from acp.intake.parsers import detect_format, parse_xyz_text

    fmt = detect_format("", BARE_ATOM_XYZ)
    assert fmt == "xyz", f"expected 'xyz', got '{fmt}'"

    result = parse_xyz_text(BARE_ATOM_XYZ)
    assert result.ok is True
    assert len(result.structures) == 1
    s = result.structures[0]
    assert s.atom_count == 5
    assert s.formula == "CH3O"


def test_parse_bare_atom_coordinates_via_api(client: TestClient) -> None:
    response = client.post("/api/v1/structures/parse", json={
        "content": BARE_ATOM_XYZ,
        "format": "auto",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert len(body["structures"]) == 1
    s = body["structures"][0]
    assert s["atom_count"] == 5
    assert s["formula"] == "CH3O"
    assert s["original_format"] == "xyz"


def test_detect_format_non_atom_not_xyz() -> None:
    from acp.intake.parsers import detect_format

    fmt = detect_format("", "hello world\nfoo bar baz qux\ntest line here\n")
    assert fmt != "xyz"
