"""Tests for method-catalog API serialization shape (Phase 5.6).

Verifies that the API returns both basis_catalog (array, v1.0 compat) and
basis_catalog_v2 (object, v1.1) fields, and that ETag caching works.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Env must precede import: server.py's module-level create_app() resolves
    # run_root at import time and would collide with a live server's lock.
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    from acp.api.server import create_app

    app = create_app(run_root=tmp_path)
    return TestClient(app)


def test_method_catalog_has_both_basis_catalog_versions(client) -> None:
    """v1.1 dual-field coexistence: basis_catalog (array) + basis_catalog_v2 (object)."""
    resp = client.get("/api/v1/method-catalog")
    assert resp.status_code == 200
    payload = resp.json()
    # v1.0 field preserved
    assert isinstance(payload["basis_catalog"], list)
    assert "def2-TZVPP" in payload["basis_catalog"]
    # v1.1 new field
    assert isinstance(payload["basis_catalog_v2"], dict)
    v2 = payload["basis_catalog_v2"]
    assert v2["def2-TZVPP"]["aux_j"] == "def2/J"
    assert v2["def2-TZVPP"]["aux_c"] == "def2-TZVPP/C"
    assert v2["cc-pVTZ"]["aux_j"] is None
    # v1.1 revision: def2-SV(P)/C does not exist
    assert v2["def2-SV(P)"]["aux_c"] is None


def test_method_catalog_etag_stable(client) -> None:
    """ETag should hit when catalog content is unchanged."""
    r1 = client.get("/api/v1/method-catalog")
    assert r1.status_code == 200
    etag = r1.headers.get("ETag")
    assert etag is not None
    r2 = client.get("/api/v1/method-catalog", headers={"If-None-Match": etag})
    assert r2.status_code == 304


def test_method_catalog_v2_contains_all_basis_sets(client) -> None:
    """basis_catalog_v2 should contain all basis sets from BASIS_CATALOG."""
    from acp.catalog import BASIS_CATALOG

    resp = client.get("/api/v1/method-catalog")
    payload = resp.json()
    v2 = payload["basis_catalog_v2"]
    for basis in BASIS_CATALOG:
        assert basis in v2, f"{basis} missing from basis_catalog_v2"
        assert "aux_j" in v2[basis]
        assert "aux_c" in v2[basis]
