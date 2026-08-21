"""Tests for the software-discovery endpoint and backend version surfacing."""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import cccp.software as software
from cccp.software import SoftwareCandidate, SoftwareDiscovery


def _make_executable(directory: Path, name: str = "orca") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture()
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    os.environ["ACP_RUN_ROOT"] = str(tmp_path)
    from acp.api.server import create_app

    with TestClient(create_app(run_root=tmp_path, max_running=2)) as test_client:
        yield test_client


def test_backends_include_version_field(client: TestClient) -> None:
    software._VERSION_CACHE.clear()
    with patch("cccp.software.version_cached", return_value=""):
        response = client.get("/api/backends")
        proxy = client.get("/api/v1/backends")
    assert response.status_code == 200
    backends = response.json()["backends"]
    assert backends
    assert all("version" in backend for backend in backends)

    assert proxy.status_code == 200
    assert all("version" in backend for backend in proxy.json()["backends"])


def test_backends_version_populated_from_probe(client: TestClient, tmp_path: Path) -> None:
    binary = _make_executable(tmp_path)
    software._VERSION_CACHE.clear()
    with (
        patch("acp.api.routes._resolve_backend_path", return_value=str(binary)),
        patch.object(software, "detect_version", return_value="Program Version 6.1.1"),
    ):
        response = client.get("/api/backends")
    assert response.status_code == 200
    by_name = {b["name"]: b for b in response.json()["backends"]}
    assert by_name["orca"]["path"] == str(binary)
    assert by_name["orca"]["version"] == "6.1.1"


def test_software_discovery_endpoint_shape(client: TestClient) -> None:
    software._VERSION_CACHE.clear()
    with patch("cccp.software.version_cached", return_value=""):
        response = client.get("/api/v1/software/discovery")
    assert response.status_code == 200
    body = response.json()
    entries = body["software"]
    assert {entry["name"] for entry in entries} == set(software.EXECUTABLES)
    for entry in entries:
        assert set(entry) == {"name", "resolved", "version", "source", "multiple", "candidates"}
        assert entry["multiple"] == (len(entry["candidates"]) > 1)
        for candidate in entry["candidates"]:
            assert set(candidate) == {"path", "version", "source"}


def test_software_discovery_multi_install(client: TestClient, tmp_path: Path) -> None:
    env_binary = _make_executable(tmp_path / "orca_6_1_1")
    path_binary = _make_executable(tmp_path / "bin")
    discovery = SoftwareDiscovery(
        name="orca",
        resolved=env_binary.resolve(),
        source="env",
        candidates=(
            SoftwareCandidate(path=env_binary.resolve(), source="env"),
            SoftwareCandidate(path=path_binary.resolve(), source="path"),
        ),
    )
    versions = {
        str(env_binary.resolve()): "6.1.1",
        str(path_binary.resolve()): "5.0.4",
    }
    software._VERSION_CACHE.clear()

    def _fake_version(name: str, path: Path | None) -> str:
        return versions.get(str(path), "") if path else ""

    with (
        patch(
            "cccp.software.discover_all_detailed",
            return_value={"orca": discovery},
        ),
        patch("cccp.software.version_cached", side_effect=_fake_version),
    ):
        response = client.get("/api/v1/software/discovery")

    assert response.status_code == 200
    (entry,) = response.json()["software"]
    assert entry["name"] == "orca"
    assert entry["resolved"] == str(env_binary.resolve())
    assert entry["source"] == "env"
    assert entry["version"] == "6.1.1"
    assert entry["multiple"] is True
    assert entry["candidates"] == [
        {"path": str(env_binary.resolve()), "version": "6.1.1", "source": "env"},
        {"path": str(path_binary.resolve()), "version": "5.0.4", "source": "path"},
    ]


def test_software_discovery_empty_install(client: TestClient) -> None:
    discovery = SoftwareDiscovery(name="orca", resolved=None, source=None, candidates=())
    software._VERSION_CACHE.clear()
    with patch("cccp.software.discover_all_detailed", return_value={"orca": discovery}):
        response = client.get("/api/v1/software/discovery")

    assert response.status_code == 200
    (entry,) = response.json()["software"]
    assert entry["resolved"] is None
    assert entry["source"] is None
    assert entry["version"] == ""
    assert entry["multiple"] is False
    assert entry["candidates"] == []
