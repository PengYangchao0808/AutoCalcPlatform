"""P0-03: both discovery paths must probe with the deployment config.

The service runs with systemd's minimal ``PATH`` and pins binaries under
``executables.<name>.path``.  A probe that constructs the backend with an
empty config therefore reports a pinned install as missing.  This module
covers the two public discovery surfaces (``/api/backends`` and
``/api/capabilities``) plus the no-config counter-case.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import cccp.software as software


def _make_executable(directory: Path, name: str = "xtb") -> Path:
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


@pytest.fixture()
def pinned_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A config-pinned executable plus an empty PATH, so only config can find it."""
    binary = _make_executable(tmp_path / "bin", "xtb")
    monkeypatch.setattr(software, "_search_path", lambda: "")
    monkeypatch.setattr(software, "_scan_candidates", lambda name: [])
    return binary


def _xtb_available(payload: dict[str, object]) -> bool:
    backends = payload["backends"]
    assert isinstance(backends, list)
    return bool(next(entry["available"] for entry in backends if entry["name"] == "xtb"))


def test_backends_probes_with_pinned_config(client: TestClient, pinned_binary: Path) -> None:
    with patch(
        "acp.api.routes._load_executables",
        return_value={"xtb": {"path": str(pinned_binary)}},
    ):
        payload = client.get("/api/backends").json()
    assert _xtb_available(payload) is True


def test_capabilities_probes_with_pinned_config(
    client: TestClient, pinned_binary: Path
) -> None:
    with patch(
        "acp.api.routes._load_executables",
        return_value={"xtb": {"path": str(pinned_binary)}},
    ):
        payload = client.get("/api/capabilities").json()
    assert _xtb_available(payload) is True


@pytest.mark.parametrize("path", ["/api/backends", "/api/capabilities"])
def test_discovery_without_config_reports_missing(
    client: TestClient, pinned_binary: Path, path: str
) -> None:
    """Counter-case: same empty PATH, no pinned config => missing."""
    with patch("acp.api.routes._load_executables", return_value={}):
        payload = client.get(path).json()
    assert _xtb_available(payload) is False
