"""Recheck R11 using real Chromium/3Dmol and the real NMR report entry point.

Run from the repository root:
  PYTHONPATH=src:. /opt/acp/venv/bin/python /tmp/acp_review_r2_nmr_late_response.py

Only the report response timing and geometry network boundary are controlled;
the report DOM, canvas, viewer library and NMR lifecycle are the real code.
"""
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright
from tests.test_acp_frontend_browser import PAGE_URL, _Harness, load_viewer_stack


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
    context = browser.new_context(viewport={"width": 1000, "height": 700})
    page = context.new_page()
    harness = _Harness(page)
    harness.attach()
    page.goto(PAGE_URL, wait_until="load")
    load_viewer_stack(harness)
    facts = page.evaluate("""async () => {
        const body = document.getElementById('nmr-report-body');
        const panel = document.createElement('div');
        panel.id = 'nmr-report-panel';
        body.parentNode.insertBefore(panel, body);
        panel.appendChild(body);
        const pending = {};
        window.fetchNmrReportJson = (job) => new Promise(resolve => { pending[job.id] = resolve; });
        const oldRequest = refreshNmrReport({id: 'old', spec: {workflow: 'nmr'}});
        const newRequest = refreshNmrReport({id: 'new', spec: {workflow: 'nmr'}});
        pending.new({schema_version: 2, summary: {}, candidates: [{index: 0, label: 'new'}]});
        await newRequest;
        const until = Date.now() + 3000;
        while (!nmrPanelState.viewers[0] && Date.now() < until) await new Promise(r => setTimeout(r, 10));
        const info = nmrPanelState.viewers[0];
        if (!info) throw new Error('new request did not create a viewer: '+body.textContent);
        const canvas = info.container.querySelector('canvas');
        const before = {canvasConnected: canvas.isConnected, disposed: info.instance.isDisposed(), atoms: info.atoms.length};
        pending.old(null);
        await oldRequest;
        const after = {
            canvasConnected: canvas.isConnected,
            disposed: info.instance.isDisposed(),
            sameRegisteredViewer: nmrPanelState.viewers[0] === info,
            body: body.textContent
        };
        nmrPanelDispose();
        return {before, after};
    }""")
    metadata = {
        "recorded_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.executable,
        "chromium": browser.version,
        "nmr_js_sha256": hashlib.sha256(Path("frontend/js/workbench/features/nmr.js").read_bytes()).hexdigest(),
        "facts": facts,
    }
    print(json.dumps(metadata, indent=2))
    context.close()
    browser.close()
    assert facts["before"]["canvasConnected"] is True
    assert facts["before"]["atoms"] == 3
    assert facts["after"]["canvasConnected"] is True
    assert facts["after"]["disposed"] is False
    assert facts["after"]["sameRegisteredViewer"] is True
    assert facts["after"]["body"] != "nmr.no_report"
