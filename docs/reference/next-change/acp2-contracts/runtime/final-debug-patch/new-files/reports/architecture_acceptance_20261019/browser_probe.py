"""Run the actual NMR loader with real Chromium DOM and bundled 3Dmol.

Geometry and translation/outer-app glue are injected. This is an isolated
component/browser probe, not an end-to-end scientific or production test.
"""

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1000, "height": 700})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.set_content(
            '<div id="nmr-report-body">'
            '<div data-nmr-structure-viewer="0" style="width:400px;height:300px"></div>'
            "</div>"
        )
        page.evaluate("""() => {
            window.t = key => key;
            window._loaders = {};
            window.ACPStructureViewer = {
                registerCanvasLoader: (id, fn) => { window._loaders[id] = fn; },
                sharedLoadGeometry: (xyz, options) => ({
                    loaded: !!window._loaders[options.canvasId](xyz, {stick:{}})
                })
            };
        }""")
        for script in [
            "js/vendor/3Dmol-2.5.5.min.js",
            "js/visualization/framing.js",
            "js/visualization/mol3d_adapter.js",
            "js/visualization/scene_instance.js",
            "js/workbench/features/nmr.js",
        ]:
            page.add_script_tag(path=str(ROOT / "frontend" / script))
        result = page.evaluate("""async () => {
            window.scheduleViewerFraming = window.ACPVisualization.framing.scheduleViewerFraming;
            window.disposeViewerFraming = window.ACPVisualization.framing.disposeViewerFraming;
            window.nmrFetchCandidateGeometry = async () =>
                '3\\nwater\\nO 0 0 0\\nH 0 0 0.96\\nH 0 0.93 -0.24\\n';
            const container = document.querySelector('[data-nmr-structure-viewer="0"]');
            const info = nmrEnsureViewer(0, container);
            const before = container.querySelectorAll('canvas').length;
            await nmrLoadCandidateGeometry({id:'audit'}, {index:0}, nmrPanelState.token);
            return {
                vendor: 'bundled 3Dmol 2.5.5',
                canvas_before_success: before,
                canvas_after_success: container.querySelectorAll('canvas').length,
                atoms_loaded: nmrPanelState.viewers[0].atoms.length,
                instance_still_registered: !!nmrPanelState.viewers[0].instance,
                container_class: container.className
            };
        }""")
        result["page_errors"] = errors
        print(json.dumps(result, ensure_ascii=False, indent=2))
        browser.close()


if __name__ == "__main__":
    main()
