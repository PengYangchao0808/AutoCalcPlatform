"""Tests for detect_format priority rework + detect_and_parse (P0, §4.1/§4.2)."""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from acp.intake import detect_and_parse, detect_format


def make_client(tmp_path: Path) -> TestClient:
    os.environ["ACP_RUN_ROOT"] = str(tmp_path)
    from acp.api.server import create_app

    return TestClient(create_app(run_root=tmp_path, max_running=2))


@pytest.fixture()
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    with make_client(tmp_path) as test_client:
        yield test_client


WATER_XYZ = "3\nwater\nO 0.0 0.0 0.0\nH 0.7586 0.5043 0.0\nH -0.7586 0.5043 0.0\n"

WATER_MOL = (
    "water\n"
    "  ACP     3D\n"
    "\n"
    "  3  2  0  0  0  0  0  0  0  0999 V2000\n"
    "    0.0000    0.0000    0.1170 O   0  0  0  0  0  0  0  0  0  0  0  0\n"
    "    0.7570    0.0000   -0.4690 H   0  0  0  0  0  0  0  0  0  0  0  0\n"
    "   -0.7570    0.0000   -0.4690 H   0  0  0  0  0  0  0  0  0  0  0  0\n"
    "  1  2  1  0\n"
    "  1  3  1  0\n"
    "M  END\n"
)

WATER_SDF = WATER_MOL + "$$$$\n"

GJF_WITH_LINK0 = (
    "%chk=water.chk\n"
    "%mem=4GB\n"
    "%nprocshared=8\n"
    "#n B3LYP/6-31G(d) opt\n"
    "\n"
    "water\n"
    "\n"
    "0 1\n"
    "O    0.000000    0.000000    0.117000\n"
    "H    0.757000    0.000000   -0.469000\n"
    "H   -0.757000    0.000000   -0.469000\n"
    "\n"
)

ORCA_PAL = (
    "%pal nprocs 8 end\n"
    "! wB97X-D4 def2-TZVPPD Opt\n"
    "* xyz 0 1\n"
    "O    0.000000    0.000000    0.117000\n"
    "H    0.757000    0.000000   -0.469000\n"
    "H   -0.757000    0.000000   -0.469000\n"
    "*\n"
)

ORCA_BANG_ONLY = "! RKS B3LYP def2-SVP TightSCF\n* xyz 0 1\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74\n*\n"

# ORCA input carrying a `#` comment line + 2 blank-line separators: the
# single-pass heuristic reads it as GJF; detect+verify must fall back to inp.
ORCA_WITH_HASH_COMMENT = (
    "%pal nprocs 4 end\n"
    "! B3LYP def2-SVP\n"
    "# reminder: job for review\n"
    "\n"
    "* xyz 0 1\n"
    "H 0.0 0.0 0.0\n"
    "H 0.0 0.0 0.74\n"
    "*\n"
    "\n"
)


def test_gjf_with_link0_header_detected_as_gjf() -> None:
    assert detect_format("", GJF_WITH_LINK0) == "gjf"


def test_gjf_with_link0_header_detect_and_parse() -> None:
    fmt, result = detect_and_parse(GJF_WITH_LINK0)
    assert fmt == "gjf"
    assert result.ok is True
    assert result.structures[0].atom_count == 3
    assert result.structures[0].original_format == "gjf"


def test_orca_inp_pal_block_detected_as_inp() -> None:
    assert detect_format("", ORCA_PAL) == "inp"
    fmt, result = detect_and_parse(ORCA_PAL)
    assert fmt == "inp"
    assert result.ok is True
    assert result.structures[0].atom_count == 3


def test_orca_inp_bang_flags_only_detected_as_inp() -> None:
    assert detect_format("", ORCA_BANG_ONLY) == "inp"
    fmt, result = detect_and_parse(ORCA_BANG_ONLY)
    assert fmt == "inp"
    assert result.ok is True


def test_plain_xyz_detected() -> None:
    assert detect_format("", WATER_XYZ) == "xyz"


def test_plain_sdf_detected() -> None:
    assert detect_format("", WATER_SDF) == "sdf"


def test_plain_mol_detected() -> None:
    assert detect_format("", WATER_MOL) == "mol"


def test_plain_smiles_detected() -> None:
    assert detect_format("", "CCO") == "smiles"


def test_txt_extension_falls_through_to_content_detection() -> None:
    assert detect_format("coords.txt", WATER_XYZ) == "xyz"
    fmt, result = detect_and_parse(WATER_XYZ, filename="coords.txt")
    assert fmt == "xyz"
    assert result.ok is True


def test_percent_ambiguity_resolved_by_detect_and_verify_fallback() -> None:
    assert detect_format("", ORCA_WITH_HASH_COMMENT) == "gjf"
    fmt, result = detect_and_parse(ORCA_WITH_HASH_COMMENT)
    assert fmt == "inp"
    assert result.ok is True
    assert result.structures[0].atom_count == 2
    assert result.structures[0].formula == "H2"


def test_detect_and_parse_returns_format_and_ok_result() -> None:
    fmt, result = detect_and_parse("CCO")
    assert fmt == "smiles"
    assert result.ok is True
    assert result.structures[0].smiles == "CCO"


def test_detect_and_parse_unparseable_content_reports_failure() -> None:
    fmt, result = detect_and_parse("definitely not a molecule !!##%%\n" * 5)
    assert result.ok is False
    assert fmt in ("xyz", "sdf", "mol", "gjf", "inp", "smiles")


def test_parse_endpoint_reports_detected_format_auto(client: TestClient) -> None:
    response = client.post(
        "/api/v1/structures/parse",
        json={
            "content": GJF_WITH_LINK0,
            "format": "auto",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["detected_format"] == "gjf"


def test_parse_endpoint_reports_forced_format(client: TestClient) -> None:
    response = client.post(
        "/api/v1/structures/parse",
        json={
            "content": WATER_XYZ,
            "format": "xyz",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["detected_format"] == "xyz"


def test_upload_endpoint_reports_detected_format(client: TestClient) -> None:
    response = client.post(
        "/api/v1/uploads",
        files={"file": ("coords.txt", WATER_XYZ.encode(), "text/plain")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["detected_format"] == "xyz"
