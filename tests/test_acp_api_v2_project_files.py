"""Route-level tests for the project-level read-only file API.

Pins the two v2 endpoints, the v2 project/entry authorization gate, the stable
error schema, bounded listing/read budgets, GET zero side effects and the
absence of absolute server paths in responses.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

_TREE = "/api/v2/projects/{project_id}/tree"
_FILE = "/api/v2/projects/{project_id}/file"


def _set_environment(monkeypatch: pytest.MonkeyPatch, run_root: Path) -> None:
    monkeypatch.setenv("ACP_PUBLIC_ORIGIN", "")
    monkeypatch.setenv("ACP_RUN_ROOT", str(run_root))
    monkeypatch.setenv("ACP_DEPLOYMENT_TIER", "local-single")
    monkeypatch.setenv("ACP_MAX_RUNNING", "1")


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    _set_environment(monkeypatch, tmp_path)
    from acp.api.server import create_app

    app = create_app(run_root=tmp_path, max_running=1)
    with TestClient(app) as test_client:
        yield test_client


def _create_project(client: TestClient, name: str) -> tuple[dict[str, Any], Path]:
    manager = client.app.state.job_manager
    project = manager.projects.create_project(name)
    return project, Path(project["run_root"])


def _symlink_or_skip(link: Path, target: Path, *, directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - platform gate
        pytest.skip(f"symbolic links unavailable on this filesystem: {exc}")


def _get(client: TestClient, url: str, **kwargs: Any) -> Any:
    manager = client.app.state.job_manager
    before = manager.store.counts()
    response = client.get(url, **kwargs)
    after = manager.store.counts()
    assert after == before, f"GET {url} changed store counts: {before} -> {after}"
    return response


def _seed_project(client: TestClient) -> tuple[dict[str, Any], Path]:
    project, root = _create_project(client, "Project Files")
    (root / "notes.txt").write_bytes(b"hello\nworld\n")
    (root / "bom.txt").write_bytes(b"\xef\xbb\xbfabc")
    (root / "large.txt").write_bytes(b"x" * 5000)
    (root / "lines.txt").write_bytes(b"a\nb\nc\n")
    (root / "binary.bin").write_bytes(b"abc\x00def")
    (root / "bad.txt").write_bytes(b"\xff\xfe")
    (root / "sub").mkdir()
    (root / "sub" / "nested.txt").write_bytes(b"nested\n")
    (root / ".hidden").write_bytes(b"hidden")
    (root / ".env").write_bytes(b"SECRET=1\n")
    (root / "credentials.json").write_bytes(b"{}")
    (root / "cache.sqlite").write_bytes(b"db")
    _symlink_or_skip(root / "link.txt", root / "notes.txt")
    return project, root


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_tree_lists_one_level_without_absolute_root(client: TestClient) -> None:
    project, root = _seed_project(client)
    project_id = str(project["project_id"])

    response = _get(client, _TREE.format(project_id=project_id))
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    body = response.json()
    assert body["schema_version"] == "acp.project-files.v1"
    assert body["project_id"] == project_id
    assert body["path"] == ""
    assert body["limit"] == 200
    assert body["truncated"] is False
    assert body["truncation_reason"] == ""

    paths = [entry["path"] for entry in body["entries"]]
    assert "sub" in paths
    assert "notes.txt" in paths
    assert not any(path.startswith(".") for path in paths)
    assert "credentials.json" not in paths
    assert "cache.sqlite" not in paths
    assert "link.txt" not in paths
    assert str(root) not in response.text
    assert str(root.parent) not in response.text
    for entry in body["entries"]:
        assert entry["type"] in {"file", "directory"}
        assert set(entry) == {"path", "name", "type", "size", "mtime"}


def test_tree_lists_subdirectory_relative_path(client: TestClient) -> None:
    project, _root = _seed_project(client)
    project_id = str(project["project_id"])

    response = _get(client, _TREE.format(project_id=project_id), params={"path": "sub"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["path"] == "sub"
    assert [entry["path"] for entry in body["entries"]] == ["sub/nested.txt"]


def test_file_read_returns_bounded_utf8_and_scope(client: TestClient) -> None:
    project, root = _seed_project(client)
    project_id = str(project["project_id"])

    response = _get(
        client,
        _FILE.format(project_id=project_id),
        params={"path": "notes.txt"},
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    body = response.json()
    assert body["schema_version"] == "acp.project-files.v1"
    assert body["project_id"] == project_id
    assert body["path"] == "notes.txt"
    assert body["content"] == "hello\nworld\n"
    assert body["encoding"] == "utf-8"
    assert body["size"] == (root / "notes.txt").stat().st_size
    assert body["returned_bytes"] == len(b"hello\nworld\n")
    assert body["returned_lines"] == 2
    assert body["truncated"] is False
    assert body["sha256_scope"] == "returned_bytes"
    assert body["sha256"] == hashlib.sha256(body["content"].encode("utf-8")).hexdigest()
    assert str(root) not in response.text


def test_file_read_strips_bom_and_reports_returned_bytes(client: TestClient) -> None:
    project, _root = _seed_project(client)
    project_id = str(project["project_id"])

    response = _get(
        client,
        _FILE.format(project_id=project_id),
        params={"path": "bom.txt"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["content"] == "abc"
    assert body["returned_bytes"] == 3
    assert body["size"] == 6
    assert body["sha256"] == hashlib.sha256(b"abc").hexdigest()


def test_file_read_truncates_bytes_without_unbounded_read(client: TestClient) -> None:
    project, _root = _seed_project(client)
    project_id = str(project["project_id"])

    response = _get(
        client,
        _FILE.format(project_id=project_id),
        params={"path": "large.txt", "max_bytes": 64},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["truncated"] is True
    assert body["truncation_reason"] == "max_bytes"
    assert body["returned_bytes"] == 64
    assert len(body["content"].encode("utf-8")) == 64
    assert body["size"] == 5000


def test_file_read_truncates_lines(client: TestClient) -> None:
    project, _root = _seed_project(client)
    project_id = str(project["project_id"])

    response = _get(
        client,
        _FILE.format(project_id=project_id),
        params={"path": "lines.txt", "max_lines": 2},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["content"] == "a\nb\n"
    assert body["returned_lines"] == 2
    assert body["truncated"] is True
    assert body["truncation_reason"] == "max_lines"


# ---------------------------------------------------------------------------
# Authorization and project scope
# ---------------------------------------------------------------------------


def test_unknown_project_is_404_with_stable_detail(client: TestClient) -> None:
    response = _get(client, _TREE.format(project_id="missing"))
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "not_found"

    response = _get(
        client,
        _FILE.format(project_id="missing"),
        params={"path": "notes.txt"},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "not_found"


def test_verified_project_mismatch_has_stable_404_shape(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, _root = _create_project(client, "Scoped")
    project_id = str(project["project_id"])
    monkeypatch.setattr(
        client.app.state,
        "server_identity",
        type("_Identity", (), {"verified_project": "another-project"})(),
        raising=False,
    )

    response = _get(client, _TREE.format(project_id=project_id))
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "not_found"


def test_cross_project_file_is_not_reachable_through_another_project(
    client: TestClient,
) -> None:
    alpha, alpha_root = _create_project(client, "Alpha")
    beta, beta_root = _create_project(client, "Beta")
    (alpha_root / "alpha.txt").write_bytes(b"alpha")
    (beta_root / "beta-only.txt").write_bytes(b"beta")
    alpha_id = str(alpha["project_id"])

    tree = _get(client, _TREE.format(project_id=alpha_id))
    assert tree.status_code == 200
    assert "beta-only.txt" not in [entry["path"] for entry in tree.json()["entries"]]

    response = _get(
        client,
        _FILE.format(project_id=alpha_id),
        params={"path": "beta-only.txt"},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "not_found"


def test_entry_access_denial_maps_to_read_not_allowed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, _root = _seed_project(client)
    project_id = str(project["project_id"])

    from acp_services.workspace.entry_authz import EntryDecision

    class _DenyAll:
        def authorize(self, entry_class: str, **kwargs: Any) -> EntryDecision:
            return EntryDecision(
                allowed=False,
                entry_class=entry_class,
                action=str(kwargs.get("action") or "read"),
                reason_code="no_grant",
                principal_id="test",
                scope_id="test",
                http_status=403,
                detail="denied",
            )

    monkeypatch.setattr(client.app.state, "entry_authorizer", _DenyAll(), raising=False)
    for url in (
        _TREE.format(project_id=project_id),
        _FILE.format(project_id=project_id) + "?path=notes.txt",
    ):
        response = _get(client, url)
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "read_not_allowed"


def test_project_root_outside_platform_root_is_503(client: TestClient) -> None:
    project, _root = _create_project(client, "Broken Root")
    project_id = str(project["project_id"])
    manager = client.app.state.job_manager
    with sqlite3.connect(str(manager.store.db_path)) as conn:
        conn.execute(
            "UPDATE projects SET run_root=? WHERE project_id=?",
            (str(manager.run_root), project_id),
        )
        conn.commit()

    response = _get(client, _TREE.format(project_id=project_id))
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "project_root_unavailable"


# ---------------------------------------------------------------------------
# Path and parameter refusals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "../outside",
        "/etc/passwd",
        "C:\\Windows\\win.ini",
        "a\\b",
        "a:b",
        ".hidden",
        ".env",
        "credentials.json",
        "id_rsa",
        "cache.sqlite",
        "CON",
        "a//b",
        "a/../b",
    ],
)
def test_invalid_paths_are_400(client: TestClient, path: str) -> None:
    project, _root = _seed_project(client)
    project_id = str(project["project_id"])

    tree = _get(client, _TREE.format(project_id=project_id), params={"path": path})
    assert tree.status_code == 400
    assert tree.json()["detail"]["code"] == "invalid_relative_path"

    file_response = _get(
        client,
        _FILE.format(project_id=project_id),
        params={"path": path},
    )
    assert file_response.status_code == 400
    assert file_response.json()["detail"]["code"] == "invalid_relative_path"


def test_path_verification_budget_is_413(client: TestClient) -> None:
    project, _root = _seed_project(client)
    project_id = str(project["project_id"])

    response = _get(
        client,
        _TREE.format(project_id=project_id),
        params={"path": "a" * 5000},
    )
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "verification_budget_exceeded"


@pytest.mark.parametrize(
    ("url_suffix", "params"),
    [
        ("/tree", {"limit": 0}),
        ("/tree", {"limit": 501}),
        ("/file", {"path": "notes.txt", "max_bytes": 0}),
        ("/file", {"path": "notes.txt", "max_bytes": 262145}),
        ("/file", {"path": "notes.txt", "max_lines": 0}),
        ("/file", {"path": "notes.txt", "max_lines": 5001}),
    ],
)
def test_out_of_range_parameters_are_422(
    client: TestClient,
    url_suffix: str,
    params: dict[str, int | str],
) -> None:
    project, _root = _seed_project(client)
    project_id = str(project["project_id"])
    response = _get(
        client,
        f"/api/v2/projects/{project_id}{url_suffix}",
        params=params,
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_parameter"
    assert isinstance(detail["message"], str) and detail["message"]


# ---------------------------------------------------------------------------
# Content refusals and GET side effects
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("binary.bin", "binary"),
        ("bad.txt", "unsupported_encoding"),
        ("sub", "not_regular_file"),
        ("link.txt", "not_found"),
        ("missing.txt", "not_found"),
    ],
)
def test_file_refusals_use_stable_codes(client: TestClient, path: str, code: str) -> None:
    project, _root = _seed_project(client)
    project_id = str(project["project_id"])

    response = _get(
        client,
        _FILE.format(project_id=project_id),
        params={"path": path},
    )
    expected_status = 415 if code in {"binary", "unsupported_encoding", "not_regular_file"} else 404
    assert response.status_code == expected_status
    assert response.json()["detail"]["code"] == code


def test_get_file_has_no_side_effects(client: TestClient) -> None:
    project, root = _seed_project(client)
    project_id = str(project["project_id"])
    target = root / "notes.txt"
    before_bytes = target.read_bytes()
    before_mtime = target.stat().st_mtime_ns
    before_entries = sorted(path.name for path in root.iterdir())
    manager = client.app.state.job_manager
    before_counts = manager.store.counts()

    for _ in range(2):
        response = _get(
            client,
            _FILE.format(project_id=project_id),
            params={"path": "notes.txt"},
        )
        assert response.status_code == 200

    assert target.read_bytes() == before_bytes
    assert target.stat().st_mtime_ns == before_mtime
    assert sorted(path.name for path in root.iterdir()) == before_entries
    assert manager.store.counts() == before_counts


def test_error_detail_is_json_object_code_message(client: TestClient) -> None:
    project, _root = _seed_project(client)
    project_id = str(project["project_id"])

    response = _get(
        client,
        _FILE.format(project_id=project_id),
        params={"path": "binary.bin"},
    )
    assert response.status_code == 415
    payload = response.json()
    assert set(payload) == {"detail"}
    assert set(payload["detail"]) == {"code", "message"}
    assert json.dumps(payload)  # serialisable, no raw OSError repr
    assert str(Path(client.app.state.run_root)) not in response.text
