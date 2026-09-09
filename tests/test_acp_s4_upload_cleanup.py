# pyright: reportMissingTypeArgument=false, reportAny=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportFunctionMemberAccess=false, reportAttributeAccessIssue=false, reportArgumentType=false, reportUnusedParameter=false, reportImplicitStringConcatenation=false, reportIndexIssue=false, reportOperatorIssue=false
from __future__ import annotations

import logging
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

WATER_XYZ = "3\nwater\nO 0.0 0.0 0.0\nH 0.7586 0.5043 0.0\nH -0.7586 0.5043 0.0\n"


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    from acp.api.server import create_app

    with TestClient(create_app(run_root=tmp_path, max_running=2)) as test_client:
        yield test_client


def _upload_dir(client: TestClient, project_id: str, upload_id: str) -> Path:
    return Path(client.app.state.run_root) / project_id / "_uploads" / upload_id


def test_parse_failure_removes_orphan_upload(client: TestClient) -> None:
    response = client.post(
        "/api/v1/uploads",
        params={"project_id": "failed-project"},
        files={"file": ("broken.xyz", b"not a valid xyz at all", "chemical/xyz")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["structures"] == []
    assert body["ok"] is False
    assert not _upload_dir(client, "failed-project", body["upload_id"]).exists()


def test_parse_false_keeps_store_only_upload(client: TestClient) -> None:
    payload = b"raw binary payload"
    response = client.post(
        "/api/v1/uploads",
        params={"project_id": "raw-project", "parse": "false"},
        files={"file": ("data.zip", payload, "application/zip")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["structures"] == []
    upload_dir = _upload_dir(client, "raw-project", body["upload_id"])
    assert (upload_dir / "original" / "data.zip").read_bytes() == payload


def test_partial_success_keeps_upload_and_normalized_asset(client: TestClient) -> None:
    content = WATER_XYZ + "not a valid xyz frame\n"
    response = client.post(
        "/api/v1/uploads",
        params={"project_id": "partial-project"},
        files={"file": ("mixed.xyz", content.encode(), "chemical/xyz")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert len(body["structures"]) == 1
    upload_dir = _upload_dir(client, "partial-project", body["upload_id"])
    assert upload_dir.is_dir()
    assert (upload_dir / "normalized").is_dir()


def test_unhandled_exception_returns_json_500_and_is_logged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    from acp.api.server import create_app

    app = create_app(run_root=tmp_path, max_running=2)

    def explode() -> None:
        raise RuntimeError("boom")

    app.add_api_route("/s4-unhandled", explode)
    with TestClient(app, raise_server_exceptions=False) as client:
        with caplog.at_level(logging.ERROR, logger="acp.api.server"):
            response = client.get("/s4-unhandled")

    assert response.status_code == 500
    assert response.json() == {"detail": "RuntimeError: boom"}
    assert any(record.exc_info and record.exc_info[0] is RuntimeError for record in caplog.records)
