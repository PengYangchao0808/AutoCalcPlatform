"""Browser fault injection for the API response audit, without a live server.

Chromium loads the real workbench and local assets. HTTP responses are routed
inside Playwright; tests never contact production jobs or a remote node.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect, sync_playwright

pytestmark = pytest.mark.slow

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE: dict = {}
JOB = {
    "id": "audit",
    "name": "audit",
    "status": "running",
    "updated_at": "2026-10-12T00:00:00Z",
    "progress": 0.2,
    "current_stage": "optimize",
    "spec": {"workflow": "optimize"},
}


@pytest.fixture(scope="session", autouse=True)
def save_evidence():
    yield
    (Path(__file__).parent / "frontend_evidence.json").write_text(
        json.dumps(EVIDENCE, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        EVIDENCE["browser_version"] = browser.version
        yield browser
        browser.close()


@pytest.fixture()
def page(browser):
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    page = context.new_page()
    requests = []

    def serve(route):
        path = urlparse(route.request.url).path
        requests.append(path)
        if path == "/":
            route.fulfill(
                path=ROOT / "frontend" / "ACP_Workbench_v2.html", content_type="text/html"
            )
        elif path.startswith(("/js/", "/css/")):
            asset = ROOT / "frontend" / path.lstrip("/")
            route.fulfill(path=asset) if asset.is_file() else route.fulfill(status=404)
        elif path == "/api/v2/task-view":
            route.fulfill(
                json={
                    "groups": [],
                    "facets": {},
                    "total": 0,
                    "counts": {"completed": 0},
                    "offset": 0,
                    "limit": 200,
                }
            )
        elif path == "/api/v1/status":
            route.fulfill(
                json={"status": "ok", "queue": {"completed": 0}, "execution_mode": "local"}
            )
        elif path == "/api/v1/projects":
            route.fulfill(json={"projects": []})
        elif path == "/api/v1/workflow-catalog":
            route.fulfill(json={"workflows": []})
        elif path == "/api/v1/method-catalog":
            route.fulfill(json={"method_schemas": {}})
        elif path == "/api/v1/jobs/audit":
            route.fulfill(json=JOB)
        elif "/jobs/" in path or "/tasks/" in path or "/drafts" in path:
            route.fulfill(status=503, json={"detail": "audit injected failure"})
        else:
            route.fulfill(json={})

    page.route("**/*", serve)
    page.goto("http://acp.audit/", wait_until="load")
    expect(page.locator("#queue-counts")).to_contain_text("完成 0")
    page.evaluate("""() => {
      jobsPollGeneration++;
      ['_jobsPollTimer','_midPollTimer','_slowPollTimer','_logPollTimer']
        .forEach(k => clearTimeout(window[k]));
    }""")
    EVIDENCE.setdefault(
        "startup",
        {
            "request_paths": requests.copy(),
            "localized_elements": page.locator("[data-i18n]").count(),
        },
    )
    yield page
    context.close()


def test_main_rename_displays_error(page):
    page.evaluate(
        """async job => {
      openTaskRenameModal(job);
      await submitTaskRenameNow('new name');
    }""",
        JOB,
    )
    error = page.locator("#task-rename-err")
    expect(error).to_be_visible()
    expect(error).to_contain_text("audit injected failure")
    EVIDENCE["main_rename_error"] = error.inner_text()


def test_source_row_rename_has_undefined_toast(page):
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.evaluate("_handleRowMenuAction('rename_task', {job_id:'uncached-audit'})")
    page.wait_for_timeout(200)
    EVIDENCE["source_row_rename_error"] = errors
    assert any("showToast is not defined" in error for error in errors)
    expect(page.locator("#task-rename-modal")).not_to_be_visible()


def test_detail_error_silently_falls_back(page):
    detail = page.evaluate("fetchJobDetail('audit')")
    EVIDENCE["detail_fallback"] = {
        "legacy": detail.get("legacy"),
        "stages": detail.get("stages"),
        "recovery": detail.get("recovery"),
        "visible_load_error": page.locator("#workbench-load-errors").inner_text(),
    }
    assert detail["legacy"] is True and detail["stages"] is None
    assert "audit injected failure" not in EVIDENCE["detail_fallback"]["visible_load_error"]


def test_summary_and_log_error_keep_old_ui(page):
    result = page.evaluate(
        """async job => {
      selectedJobId = job.id; updateInfoPanel(job);
      const panel = document.getElementById('info-panel'); const child = panel.firstChild;
      await refreshSelectedJobSummary();
      const oldAppender = appendLogLine; const messages = [];
      appendLogLine = (...args) => messages.push(args);
      await pollLogTail(job.id); appendLogLine = oldAppender;
      return {same_child: child === panel.firstChild,
              progress: lastSelectedJob.progress, log_messages: messages};
    }""",
        JOB,
    )
    EVIDENCE["summary_log_failure"] = result
    assert result["same_child"] and result["progress"] == 0.2 and not result["log_messages"]


def test_folder_failure_becomes_empty_directory(page):
    result = page.evaluate("""async () => {
      selectedJobId = 'audit'; selectedJobIsRemote = true;
      folderCache = {}; folderState = {}; lastFileListing = [{path:'RESULT', is_dir:true}];
      await fetchAndExpand('audit', 'RESULT');
      return {files: folderCache.RESULT, expanded: folderState.RESULT};
    }""")
    EVIDENCE["folder_error"] = result
    assert result == {"files": [], "expanded": True}


def test_nmr_server_errors_render_missing_report(page):
    page.evaluate("refreshNmrReport({id:'audit', spec:{workflow:'nmr'}, status:'completed'})")
    text = page.locator("#nmr-report-body").inner_text()
    EVIDENCE["nmr_error_as_absence"] = text
    assert "nmr_report.json" in text
    assert "audit injected failure" not in text


def test_pes_history_failure_has_feedback(page):
    page.evaluate("""() => { energyGraphState.jobId = 'audit'; energyGraphOpenSaveDialog(); }""")
    box = page.locator("[data-energy-review-history]")
    expect(box).to_contain_text("历史备份加载失败")
    EVIDENCE["pes_history_error"] = box.inner_text()


def test_disk_indicator_hides_after_error(page):
    page.route(
        "**/api/v1/maintenance/disk-usage",
        lambda route: route.fulfill(status=503, json={"detail": "offline"}),
    )
    page.evaluate("""async () => {
      document.getElementById('disk-pill').style.display = '';
      document.getElementById('disk-pill').textContent = 'DISK 20%';
      await loadDiskUsage();
    }""")
    expect(page.locator("#disk-pill")).not_to_be_visible()
    EVIDENCE["disk_error_hides_pill"] = True


def test_shared_timeout_is_localized(page):
    page.route("**/api/v1/hang", lambda route: None)
    result = page.evaluate("""async () => {
      const budgets = [API_TIMEOUT_MS, API_REMOTE_TIMEOUT_MS];
      const start = performance.now();
      try { await api('/hang', {timeoutMs:40}); }
      catch (e) {
        return {budgets, name:e.name, message:e.message, elapsed_ms:performance.now()-start};
      }
    }""")
    EVIDENCE["shared_timeout"] = result
    assert result["budgets"] == [8000, 60000]
    assert result["name"] == "Error" and "AbortError" not in result["message"]


@pytest.mark.parametrize(
    "view,expected",
    [
        ("irc", [8000]),
        ("optimization", [8000, 8000]),
        ("sampling", [8000, 8000]),
        ("scan", [8000, 60000]),
        ("conformer", [8000]),
    ],
)
def test_frame_fallbacks_are_view_specific(page, view, expected):
    calls = page.evaluate(
        """async view => {
      const container = document.createElement('div');
      container.id = 'energy-structure-viewer'; document.body.appendChild(container);
      const local = api, remote = apiRemote, calls = [];
      api = async path => {calls.push({path, budget_ms:8000}); throw new Error('injected');};
      apiRemote = async path => {calls.push({path, budget_ms:60000}); throw new Error('injected');};
      energyGraphState.jobId = 'audit'; energyGraphState.data = {view_type:view};
      energyGraphState.loadedFrameIndex = -1;
      await energyGraphLoadFrameGeometry({id:'f0', frame_index:0,
          geometry_ref:'missing.xyz', metadata:{direction:'forward'}});
      api = local; apiRemote = remote; container.remove(); return calls;
    }""",
        view,
    )
    EVIDENCE.setdefault("frame_fallbacks", {})[view] = calls
    assert [call["budget_ms"] for call in calls] == expected


def test_info_panel_only_refreshes_on_change_but_replaces_both_copies(page):
    result = page.evaluate(
        """async job => {
      selectedJobId = job.id; updateInfoPanel(job);
      const desktop = document.getElementById('info-panel');
      const mobile = document.getElementById('mobile-info-panel');
      const d = desktop.firstChild, m = mobile.firstChild, original = api;
      api = async () => JSON.parse(JSON.stringify(job));
      await refreshSelectedJobSummary();
      const stable = d === desktop.firstChild && m === mobile.firstChild;
      api = async () => Object.assign({}, job, {updated_at:'2026-10-12T00:00:01Z'});
      await refreshSelectedJobSummary(); api = original;
      return {unchanged_summary_reuses_dom:stable,
              changed_summary_replaces_desktop:d !== desktop.firstChild,
              changed_summary_replaces_mobile:m !== mobile.firstChild};
    }""",
        JOB,
    )
    EVIDENCE["info_dom_refresh"] = result
    assert all(result.values())


def test_queue_reuses_unaffected_rows_after_one_change(page):
    result = page.evaluate("""async () => {
      const jobs = [0,1].map(i => ({id:'audit-'+i, name:'Task '+i,
          status:'running', progress:0.2, spec:{workflow:'optimize'}}));
      let data = {groups:[{key:'audit', display_name:'audit', count:2, jobs}],
          facets:{},total:2, counts:{running:2},offset:0,limit:200};
      const original = apiV2;
      apiV2 = async path => path.startsWith('/task-view')
          ? JSON.parse(JSON.stringify(data)) : {suggestions:[]};
      await refreshJobs();
      const d = Array.from(document.querySelectorAll('#queue-list .queue-row'));
      const m = Array.from(document.querySelectorAll('#mobile-queue-list .queue-row'));
      await refreshJobs();
      const reused = d.every((el,i) =>
          el === document.querySelectorAll('#queue-list .queue-row')[i]);
      data.groups[0].jobs[0].progress = 0.3;
      await refreshJobs(); apiV2 = original;
      const nextDesktop = document.querySelectorAll('#queue-list .queue-row');
      const nextMobile = document.querySelectorAll('#mobile-queue-list .queue-row');
      return {unchanged_reused:reused,
              row_count:d.length,
              affected_desktop_replaced:d[0] !== nextDesktop[0],
              unaffected_desktop_reused:d[1] === nextDesktop[1],
              unaffected_mobile_reused:m[1] === nextMobile[1]};
    }""")
    EVIDENCE["queue_dom_refresh"] = result
    assert result["row_count"] == 2 and all(result.values())


def test_structure_catalog_and_candidate_requests_have_no_deadline(page):
    result = page.evaluate("""async () => {
      const originalFetch = window.fetch, originalSv = ACPStructureViewer._fetchImpl;
      const originalTimer = window.setTimeout, deadlines = [], calls = [];
      window.setTimeout = (fn, ms, ...args) => {
        deadlines.push(ms); return originalTimer(fn,ms,...args);
      };
      const pending = (url, opts) => {
        calls.push({url, signal:!!(opts && opts.signal)}); return new Promise(() => {});
      };
      window.fetch = pending; ACPStructureViewer._fetchImpl = pending;
      let catalogDone = false, candidateDone = false;
      ACPStructureViewer.onJobSelected('unreachable-audit').then(() => { catalogDone = true; });
      candidateApi('/structure-sources/audit/candidate').then(() => { candidateDone = true; });
      window.setTimeout = originalTimer; window.fetch = originalFetch;
      ACPStructureViewer._fetchImpl = originalSv;
      return {deadlines, calls, catalog_settled:catalogDone, candidate_settled:candidateDone};
    }""")
    EVIDENCE["requests_without_deadlines"] = result
    assert result["deadlines"] == []
    assert len(result["calls"]) == 2
    assert result["calls"][0]["signal"] is True  # cancellation on selection change only
    assert result["calls"][1]["signal"] is False


def test_unreachable_event_not_registered_by_sse_client(page):
    result = page.evaluate("""() => {
      const Original = window.EventSource, events = [];
      window.EventSource = function() {
        this.close=()=>{}; this.addEventListener=(type,fn)=>events.push(type);
      };
      selectedJobId = 'audit'; openSSE('audit'); activeSSE.close(); activeSSE = null;
      window.EventSource = Original; return events;
    }""")
    EVIDENCE["sse_registered_events"] = result
    assert "done" in result and "remote.poll_unreachable" not in result


def test_startup_handlers_remain_bound_with_stalled_catalogs(page):
    pending = []
    page.route("**/api/v1/*-catalog", lambda route: pending.append(route))
    page.reload(wait_until="domcontentloaded")
    EVIDENCE["startup_layout"] = page.evaluate("""() => {
      const result=[];
      for (let el=document.getElementById('btn-new-project');el;el=el.parentElement) {
        const style=getComputedStyle(el),rect=el.getBoundingClientRect();
        result.push({tag:el.tagName,id:el.id,display:style.display,visibility:style.visibility,width:rect.width,height:rect.height});
      }
      return result;
    }""")
    try:
        page.locator("#btn-new-project").dispatch_event("click")
        expect(page.locator("#project-modal")).to_be_visible()
        EVIDENCE["stalled_catalog_handlers_work"] = True
        assert len(pending) == 2
    finally:
        for route in pending:
            route.abort()


def test_pointer_stability_control(browser):
    from playwright.sync_api import TimeoutError

    context = browser.new_context(viewport={"width": 1280, "height": 800})
    control = context.new_page()
    control.goto("about:blank")
    control.evaluate("document.body.innerHTML = '<button id=control>control</button>'")
    try:
        control.locator("#control").click(timeout=1500)
        EVIDENCE["pointer_stability_control"] = {"normal_click": True}
    except TimeoutError as error:
        EVIDENCE["pointer_stability_control"] = {"normal_click": False, "error": str(error)}
    finally:
        context.close()
