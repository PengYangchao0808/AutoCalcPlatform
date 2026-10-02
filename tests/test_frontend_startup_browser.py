"""Browser regressions for independent startup, request failure and paging."""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Generator

import pytest

pytest.importorskip("playwright")
from playwright.sync_api import Page, expect, sync_playwright

pytestmark = pytest.mark.slow


def _find_free_port() -> int:
    """Return an OS-assigned ephemeral port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def server_url() -> Generator[str, None, None]:
    """Boot the real FastAPI app on an ephemeral port for the test session.

    Mirrors the sibling browser suites: the server runs in a daemon thread
    against a temporary ``run_root`` so no persistent state is written.
    """
    import os
    import tempfile
    from pathlib import Path

    import uvicorn

    tmp_root = Path(tempfile.mkdtemp(prefix="acp_browser_test_"))

    # Set ACP_RUN_ROOT BEFORE importing the server module, because server.py
    # has a module-level ``app = create_app()`` that reads this env var.
    os.environ["ACP_RUN_ROOT"] = str(tmp_root)

    from acp.api.server import create_app

    app = create_app(run_root=tmp_root)
    port = _find_free_port()
    host = "127.0.0.1"
    url = f"http://{host}:{port}"

    config = uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                break
        except OSError:
            time.sleep(0.1)
    else:
        pytest.fail(f"Server did not start within 15 s on {url}")

    yield url

    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture()
def page() -> Generator[Page, None, None]:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        yield context.new_page()
        context.close()
        browser.close()


def _view(offset: int = 0, total: int = 0) -> dict:
    jobs = [
        {
            "id": f"test-{i}",
            "name": f"Task {i}",
            "status": "completed",
            "spec": {"workflow": "singlepoint", "task_name": f"Task {i}"},
            "task_name": f"Task {i}",
            "project_id": "test",
            "project_name": "Test",
        }
        for i in range(offset, min(total, offset + 200))
    ]
    return {
        "groups": [{"key": "test", "display_name": "Test", "count": len(jobs), "jobs": jobs}],
        "facets": {},
        "total": total,
        "counts": {"completed": total},
        "offset": offset,
        "limit": 200,
        "next_offset": offset + 200 if offset + 200 < total else None,
    }


@pytest.mark.parametrize("viewer_stalled", [False, True])
def test_stalled_catalogs_and_missing_viewer_do_not_block_shell(
    page: Page, server_url: str, viewer_stalled: bool
) -> None:
    pending = []
    page.route("**/api/v1/*-catalog", lambda route: pending.append(route))
    pending_viewers = []
    page.route(
        "**/js/vendor/3Dmol-*",
        lambda route: pending_viewers.append(route) if viewer_stalled else route.abort(),
    )
    page.route("**/api/v2/task-view?**", lambda route: route.fulfill(json=_view()))
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(server_url, wait_until="domcontentloaded")
    expect(page.locator("#queue-counts")).to_contain_text("完成 0", timeout=2000)
    page.locator("#btn-new-project").click()
    expect(page.locator("#project-modal")).to_be_visible()
    assert len(pending) == 2
    assert not errors


def test_failed_task_list_has_retry_and_recovers(page: Page, server_url: str) -> None:
    requests = []

    def serve(route):
        requests.append(route.request.url)
        if len(requests) == 1:
            route.fulfill(
                status=503, content_type="application/json", body=json.dumps({"detail": "busy"})
            )
        else:
            route.fulfill(json=_view())

    page.route("**/api/v2/task-view?**", serve)
    page.goto(server_url, wait_until="domcontentloaded")
    expect(page.locator("#queue-counts")).to_have_text("任务列表加载失败")
    page.locator("#workbench-load-errors").get_by_role("button", name="重试").click()
    expect(page.locator("#queue-counts")).to_contain_text("完成 0")
    expect(page.locator("#workbench-load-errors")).not_to_contain_text("busy")


def test_status_failure_does_not_block_task_list(page: Page, server_url: str) -> None:
    page.route(
        "**/api/v1/status", lambda route: route.fulfill(status=503, json={"detail": "offline"})
    )
    page.route("**/api/v2/task-view?**", lambda route: route.fulfill(json=_view()))
    page.goto(server_url, wait_until="domcontentloaded")
    expect(page.locator("#status-pill")).to_have_text("离线")
    expect(page.locator("#queue-counts")).to_contain_text("完成 0")


def test_paging_keeps_counts_and_reuses_unchanged_rows(page: Page, server_url: str) -> None:
    from urllib.parse import parse_qs, urlparse

    requests = []

    def serve(route):
        query = parse_qs(urlparse(route.request.url).query)
        requests.append(query)
        route.fulfill(json=_view(int(query["offset"][0]), total=201))

    page.route("**/api/v2/task-view?**", serve)
    page.goto(server_url, wait_until="domcontentloaded")
    expect(page.locator("#queue-pagination")).to_contain_text("共 201 个任务")
    expect(page.locator("#queue-list .queue-row")).to_have_count(200)
    page.evaluate("window._testRow = document.querySelector('#queue-list .queue-row')")
    page.evaluate("refreshJobs()")
    assert page.evaluate("window._testRow === document.querySelector('#queue-list .queue-row')")
    assert requests[-1]["include_facets"] == ["false"]
    page.locator("#queue-pagination").get_by_role("button", name="下一页").click()
    expect(page.locator("#queue-list .queue-row")).to_have_count(1)
    expect(page.locator("#queue-pagination")).to_contain_text("第 2 页")
    expect(page.locator("#queue-counts")).to_contain_text("完成 201")
    page.locator("#queue-pagination").get_by_role("button", name="上一页").click()
    expect(page.locator("#queue-list .queue-row")).to_have_count(200)
