"""Tests for the ``/next/`` frontend experimental entry (B-req-19).

Mirrors the TestClient + ``ACP_RUN_ROOT``→tmp_path style of
``test_acp_api.py``.  Covers: the two explicit HTML routes, identity-exempt
parity with ``/``, ``_RevalidatingStaticFiles`` revalidation for mounted
``/next`` assets, and graceful degradation (no mount, 404) when
``frontend/next/`` is absent.
"""

# FastAPI's TestClient stubs expose untyped JSON payloads in these integration tests.
# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnusedParameter=false

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.routing import Mount


@pytest.fixture()
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    import os

    os.environ["ACP_RUN_ROOT"] = str(tmp_path)
    from acp.api.server import create_app

    app = create_app(run_root=tmp_path)
    with TestClient(app) as c:
        yield c


def test_next_index_served(client: TestClient) -> None:
    """GET /next/ and /next/index.html both serve the experimental entry HTML."""
    for path in ("/next/", "/next/index.html"):
        r = client.get(path)
        assert r.status_code == 200, f"{path} -> {r.status_code}"
        assert "text/html" in r.headers["content-type"]
    body = client.get("/next/").text
    assert "ACP Next" in body
    assert 'id="acp-next-root"' in body


def test_next_identity_exempt_parity_with_root(client: TestClient) -> None:
    """/next/ is exempt from identity exactly like the / entry path.

    The default local-single mode never enters the identity branch, so the
    unit-level assertion on ``_identity_exempt`` is the real gate; the client
    GET asserts the no-credential 200 parity with ``/``.
    """
    from acp.api.server import _identity_exempt

    assert _identity_exempt("/") is True
    assert _identity_exempt("/next/") is True
    assert _identity_exempt("/next/index.html") is True
    # Asset paths under the mount are entry-surface too.
    assert _identity_exempt("/next/assets/app.js") is True
    # No credential supplied — same as GET /.
    assert client.get("/").status_code == 200
    assert client.get("/next/").status_code == 200


def test_next_static_mount_revalidates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mounted /next assets get Cache-Control: no-cache via _RevalidatingStaticFiles.

    Also pins the route/mount split: the explicit HTML routes win for /next/
    and /next/index.html; everything else (e.g. /next/app.js) falls through to
    the /next static mount.
    """
    frontend = tmp_path / "frontend"
    (frontend / "next").mkdir(parents=True)
    (frontend / "next" / "index.html").write_text(
        '<!doctype html><title>ACP Next</title><div id="acp-next-root"></div>',
        encoding="utf-8",
    )
    (frontend / "next" / "app.js").write_text("// next bundle\n", encoding="utf-8")
    monkeypatch.setattr("acp.api.server._FRONTEND_DIR", frontend)

    run_root = tmp_path / "run_root"
    monkeypatch.setenv("ACP_RUN_ROOT", str(run_root))
    from acp.api.server import create_app

    app = create_app(run_root=run_root)
    with TestClient(app) as c:
        js = c.get("/next/app.js")
        assert js.status_code == 200
        assert "javascript" in js.headers["content-type"]
        assert "no-cache" in js.headers["cache-control"]
        # The explicit routes still serve the entry HTML over the mount.
        page = c.get("/next/")
        assert page.status_code == 200
        assert "ACP Next" in page.text


def test_next_directory_missing_skips_mount_and_serves_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """frontend/next absent → no /next mount, routes 404, server keeps booting."""
    frontend = tmp_path / "frontend_no_next"
    frontend.mkdir()
    (frontend / "ACP_Workbench_v2.html").write_text("<h1>ACP Molecular Workbench</h1>", "utf-8")
    monkeypatch.setattr("acp.api.server._FRONTEND_DIR", frontend)

    run_root = tmp_path / "run_root"
    monkeypatch.setenv("ACP_RUN_ROOT", str(run_root))
    from acp.api.server import create_app

    app = create_app(run_root=run_root)
    mount_paths = [r.path for r in app.routes if isinstance(r, Mount)]
    assert "/next" not in mount_paths
    with TestClient(app) as c:
        assert c.get("/next/").status_code == 404
        assert c.get("/next/index.html").status_code == 404
        # Existing surfaces are unaffected (both routes fall back to 200 HTML).
        root = c.get("/")
        assert root.status_code == 200
        assert "ACP Molecular Workbench" in root.text
        assert c.get("/legacy/").status_code == 200
