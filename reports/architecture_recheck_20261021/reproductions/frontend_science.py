"""Read-only reviewer probes; run from repository root with PYTHONPATH=src:. ."""
import json
from dataclasses import replace

from cccp.analysis.bounded import BoundedProduct, chunk_curve_product
from tests.test_cccp_bounded_products import _curve_product


def probe_bounded():
    source = _curve_product(6)
    first = chunk_curve_product(source, chunk_size=3)
    second = chunk_curve_product(
        replace(source, identity=replace(source.identity, algorithm_version="other")),
        chunk_size=3,
    )
    mixed = second.to_dict()
    mixed["blocks"][0]["product"] = first.to_dict()["blocks"][0]["product"]
    accepted = BoundedProduct.from_dict(mixed)
    return {
        "old_source_digest": first.spec.source_identity_digest,
        "new_source_digest": second.spec.source_identity_digest,
        "spliced_old_identity": accepted.blocks[0].product.identity == first.blocks[0].product.identity,
        "differs_expected_new_identity": accepted.blocks[0].product.identity != second.blocks[0].product.identity,
        "is_cache_adoptable": accepted.is_cache_adoptable(),
    }


def probe_nmr():
    from playwright.sync_api import sync_playwright
    from tests.test_acp_frontend_browser import _Harness, PAGE_URL, load_viewer_stack

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context(viewport={"width": 1000, "height": 700})
        page = context.new_page()
        harness = _Harness(page)
        harness.attach()
        page.goto(PAGE_URL, wait_until="load")
        load_viewer_stack(harness)
        result = page.evaluate("""async () => {
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
            const before = { canvasConnected: canvas.isConnected, disposed: info.instance.isDisposed(), atoms: info.atoms.length };
            pending.old(null);
            await oldRequest;
            const after = { canvasConnected: canvas.isConnected, disposed: info.instance.isDisposed(), registryContainsDetachedViewer: nmrPanelState.viewers[0] === info, body: body.textContent };
            nmrPanelDispose();
            return {before, after};
        }""")
        context.close()
        browser.close()
        return result


if __name__ == "__main__":
    print(json.dumps({"bounded": probe_bounded(), "nmr": probe_nmr()}, indent=2))
