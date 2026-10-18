"""Frontend sync page tests — vibration area.

Split from tests/test_frontend_sync.py (T29, 2026-10-18). Every test keeps
its original name and body; the old -> new mapping is
tests/test_frontend_sync.py::<name> -> tests/test_frontend_sync_vibration.py
::<name>. Shared helpers live in tests/_frontend_sync_shared.py.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import textwrap

import pytest

from tests._frontend_sync_shared import (
    _EDITOR_JS_PATH,
    _EN_BLOCK_RE,
    _SV_JS_PATH,
    _VIB_JS,
    _ZH_BLOCK_RE,
    FRONTEND_CSS_DIR,
    FRONTEND_JS_DIR,
    read_workbench_content,
)


def test_vibration_viewer_js_has_namespace() -> None:
    js = (FRONTEND_JS_DIR / "vibration_viewer.js").read_text(encoding="utf-8")
    assert "window.ACPVibrationViewer" in js


def test_vibration_viewer_frequency_inspector_contract() -> None:
    """Contract: state fields, API names, endpoint URL, reason mapping,
    negatives-first sort, and the no-mode-render-when-unavailable branch."""
    vib = _VIB_JS.read_text(encoding="utf-8")
    sv = (FRONTEND_JS_DIR / "structure_viewer.js").read_text(encoding="utf-8")

    for field in ("jobId", "entryId", "data", "selectedModeIndex", "loading", "error"):
        assert field in vib, f"vibrationState field {field!r} missing"

    for name in (
        "loadVibrations",
        "renderFrequencyInspector",
        "sortModesNegativesFirst",
        "defaultModeIndex",
        "reasonText",
        "_fetchImpl",
    ):
        assert name in vib, f"{name} missing from vibration_viewer.js"

    # Canonical endpoint URL construction
    assert "/structure-viewer/entries/" in vib
    assert '"/vibrations"' in vib or "/vibrations" in vib

    # Reason i18n keys are built dynamically in JS ("structure.vib.reason." + reason);
    # locale-side existence of all four keys is asserted by the i18n completeness test.
    assert '"structure.vib.reason." + reason' in vib
    for reason in (
        "no_normal_modes",
        "geometry_mismatch",
        "pending_fetch",
        "historical_unavailable",
    ):
        assert reason in vib, f"reason code {reason!r} unmapped"

    # Display units + imaginary chip
    assert "cm\u207b\u00b9" in vib  # cm⁻¹
    assert "km\u00b7mol\u207b\u00b9" in vib
    assert "\u865a\u9891" in vib  # 虚频

    # Unavailable branch: reason ONLY, never mode rows (must not fabricate)
    assert "data.available === false" in vib

    # Inspector hook: container id + delegation + no-fetch local branch
    assert 'id = "structure-inspector-vibrations"' in sv
    assert "ACPVibrationViewer.loadVibrations" in sv
    assert "structure.vib.none" in sv


def test_vibration_viewer_i18n_keys_complete_across_locales() -> None:
    """Every structure.vib.* key must exist in BOTH zh-CN and en-US, and the
    required Wave-5 subset (loading/none/chip/summary/4 reasons) is present."""
    html = read_workbench_content()

    zh_keys = _extract_vib_keys(html, _ZH_BLOCK_RE)
    en_keys = _extract_vib_keys(html, _EN_BLOCK_RE)

    required = {
        "structure.vib.loading",
        "structure.vib.none",
        "structure.vib.imaginary_chip",
        "structure.vib.imaginary_summary",
        "structure.vib.reason.no_normal_modes",
        "structure.vib.reason.geometry_mismatch",
        "structure.vib.reason.pending_fetch",
        "structure.vib.reason.historical_unavailable",
    }

    assert required <= zh_keys, f"Missing zh-CN vib keys: {sorted(required - zh_keys)}"
    assert required <= en_keys, f"Missing en-US vib keys: {sorted(required - en_keys)}"
    assert zh_keys == en_keys, (
        f"structure.vib.* locale mismatch: only-zh={sorted(zh_keys - en_keys)} "
        f"only-en={sorted(en_keys - zh_keys)}"
    )

    # Summary template carries the required placeholders in both locales
    assert '"structure.vib.imaginary_summary": "虚频 {count} 个"' in html
    assert '"structure.vib.imaginary_summary": "{count} imaginary mode(s)"' in html


def test_vibration_viewer_node_sort_negatives_first() -> None:
    """Node logic: sortModesNegativesFirst on [-797.72, -100, 0, 1411.55]
    yields negatives first (most-negative first), then ascending."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    script = textwrap.dedent("""\
        var window = { fetch: null };
        require(JS_PATH);
        var ns = window.ACPVibrationViewer;

        var modes = [
            { mode_index: 5, frequency_cm1: 1411.55, imaginary: false },
            { mode_index: 6, frequency_cm1: 0.0, imaginary: false },
            { mode_index: 7, frequency_cm1: -100.0, imaginary: true },
            { mode_index: 8, frequency_cm1: -797.72, imaginary: true }
        ];
        var sorted = ns.sortModesNegativesFirst(modes);
        var freqs = sorted.map(function (m) { return m.frequency_cm1; });
        var expected = [-797.72, -100.0, 0.0, 1411.55];
        if (JSON.stringify(freqs) !== JSON.stringify(expected)) {
            console.error("FAIL: order " + JSON.stringify(freqs));
            process.exit(1);
        }
        // Input array untouched
        if (modes[0].frequency_cm1 !== 1411.55) {
            console.error("FAIL: input mutated");
            process.exit(1);
        }
        // Empty / null safe
        if (ns.sortModesNegativesFirst([]).length !== 0) {
            console.error("FAIL: empty input");
            process.exit(1);
        }
        if (ns.sortModesNegativesFirst(null).length !== 0) {
            console.error("FAIL: null input");
            process.exit(1);
        }
        console.log("PASS");
    """).replace("JS_PATH", json.dumps(str(_VIB_JS)))

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"Node sort test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_vibration_viewer_node_default_mode_and_reasons() -> None:
    """Node logic: defaultModeIndex picks the most-negative imaginary mode
    (falling back to the first mode), and reasonText maps all 4 reasons."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    script = textwrap.dedent("""\
        var window = { fetch: null };
        require(JS_PATH);
        var ns = window.ACPVibrationViewer;

        var tsModes = [
            { mode_index: 5, frequency_cm1: 0.0, imaginary: false },
            { mode_index: 6, frequency_cm1: -797.72, imaginary: true },
            { mode_index: 7, frequency_cm1: -100.0, imaginary: true }
        ];
        if (ns.defaultModeIndex(tsModes) !== 6) {
            console.error("FAIL: expected 6, got " + ns.defaultModeIndex(tsModes));
            process.exit(1);
        }

        var plainModes = [
            { mode_index: 3, frequency_cm1: 12.34, imaginary: false },
            { mode_index: 4, frequency_cm1: 56.78, imaginary: false }
        ];
        if (ns.defaultModeIndex(plainModes) !== 3) {
            console.error("FAIL: no-imaginary default should be first mode");
            process.exit(1);
        }
        if (ns.defaultModeIndex([]) !== null || ns.defaultModeIndex(null) !== null) {
            console.error("FAIL: empty/null default should be null");
            process.exit(1);
        }

        var expected = {
            no_normal_modes: "无振动模式数据",
            geometry_mismatch: "模式与当前几何不匹配",
            pending_fetch: "等待远程结果拉取",
            historical_unavailable: "历史任务数据不可用"
        };
        for (var key in expected) {
            if (ns.reasonText(key) !== expected[key]) {
                console.error("FAIL: reason " + key + " -> " + ns.reasonText(key));
                process.exit(1);
            }
        }
        if (ns.reasonText(null) !== null) {
            console.error("FAIL: null reason");
            process.exit(1);
        }
        if (ns.reasonText("something_odd") !== "something_odd") {
            console.error("FAIL: unknown reason must be verbatim");
            process.exit(1);
        }
        console.log("PASS");
    """).replace("JS_PATH", json.dumps(str(_VIB_JS)))

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"Node default/reason test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_vibration_viewer_node_load_stale_guard_and_default_selection() -> None:
    """Node logic: stale response for a superseded entry is discarded; the
    winning entry's most-negative imaginary mode is auto-selected; re-calls
    for the same entry short-circuit without refetching."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    script = textwrap.dedent("""\
        var window = { fetch: null };
        require(JS_PATH);
        var ns = window.ACPVibrationViewer;
        var state = ns.state;

        var resolveE1, resolveE2, fetchCount = 0;
        ns._fetchImpl = function (url) {
            fetchCount++;
            if (url.indexOf("entry1") >= 0) {
                return new Promise(function (r) { resolveE1 = r; });
            }
            return new Promise(function (r) { resolveE2 = r; });
        };

        var modes = [
            { mode_index: 5, frequency_cm1: 0.0, imaginary: false },
            { mode_index: 6, frequency_cm1: -797.72, imaginary: true },
            { mode_index: 7, frequency_cm1: 1411.55, imaginary: false }
        ];

        ns.loadVibrations("job1", "entry1");
        ns.loadVibrations("job1", "entry2");

        resolveE2({ ok: true, status: 200, statusText: "OK", json: function () {
            return Promise.resolve({
                available: true, reason: null, threshold_cm1: -50.0,
                threshold_source: "default", modes: modes, atom_count: 3
            });
        }});
        resolveE1({ ok: true, status: 200, statusText: "OK", json: function () {
            return Promise.resolve({
                available: true, reason: null, modes: [
                    { mode_index: 0, frequency_cm1: 99.99, imaginary: false }
                ], atom_count: 1
            });
        }});

        setTimeout(function () {
            if (state.entryId !== "entry2") {
                console.error("FAIL: entryId should be entry2, got " + state.entryId);
                process.exit(1);
            }
            if (!state.data || state.data.atom_count !== 3) {
                console.error("FAIL: stale entry1 response was applied");
                process.exit(1);
            }
            if (state.selectedModeIndex !== 6) {
                console.error("FAIL: default mode " + state.selectedModeIndex);
                process.exit(1);
            }
            // Same entry again -> short-circuit, no refetch
            var before = fetchCount;
            ns.loadVibrations("job1", "entry2");
            if (fetchCount !== before) {
                console.error("FAIL: same-entry reload must not refetch");
                process.exit(1);
            }
            console.log("PASS");
        }, 50);
    """).replace("JS_PATH", json.dumps(str(_VIB_JS)))

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"Node stale-guard test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_vibration_viewer_node_render_unavailable_reason_only() -> None:
    """Node logic: available=false renders ONLY the reason text — no mode
    rows, no summary, no fabricated frequencies."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    script = textwrap.dedent(
        """\
        var window = { fetch: null };
        require(JS_PATH);
        var ns = window.ACPVibrationViewer;
"""
        + _VIB_DOM_STUB
        + """
        ns.state.loading = false;
        ns.state.error = null;
        ns.state.data = { available: false, reason: "geometry_mismatch", modes: [] };

        var container = fakeEl("div");
        ns.renderFrequencyInspector(container);

        if (container.children.length !== 1) {
            console.error("FAIL: expected exactly 1 child, got " + container.children.length);
            process.exit(1);
        }
        if (container.children[0].textContent !== "模式与当前几何不匹配") {
            console.error("FAIL: reason text mismatch: " + container.children[0].textContent);
            process.exit(1);
        }
        var rendered = JSON.stringify(container);
        if (rendered.indexOf("sv-vib-row") >= 0 || rendered.indexOf("sv-vib-summary") >= 0) {
            console.error("FAIL: mode rows rendered while available=false");
            process.exit(1);
        }
        console.log("PASS");
    """
    ).replace("JS_PATH", json.dumps(str(_VIB_JS)))

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"Node unavailable-render test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_vibration_viewer_node_render_modes_header_and_rows() -> None:
    """Node logic: available=true renders the 虚频 summary and
    negatives-first rows with chip + IR intensity + selected highlight."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    script = textwrap.dedent(
        """\
        var window = { fetch: null };
        require(JS_PATH);
        var ns = window.ACPVibrationViewer;
"""
        + _VIB_DOM_STUB
        + """
        var modes = [
            { mode_index: 5, frequency_cm1: 0.0, imaginary: false, ir_intensity: null },
            { mode_index: 6, frequency_cm1: -797.72, imaginary: true, ir_intensity: 24.8 },
            { mode_index: 7, frequency_cm1: 1411.55, imaginary: false, ir_intensity: 3.25 },
            { mode_index: 8, frequency_cm1: -100.0, imaginary: true, ir_intensity: null }
        ];
        ns.state.loading = false;
        ns.state.error = null;
        ns.state.data = { available: true, reason: null, modes: modes, atom_count: 3 };
        ns.state.selectedModeIndex = ns.defaultModeIndex(modes);

        var container = fakeEl("div");
        ns.renderFrequencyInspector(container);

        // summary + TS evidence block (todo 31) + list + arrow controls
        if (container.children.length !== 4) {
            console.error("FAIL: expected 4 sections, got " + container.children.length);
            process.exit(1);
        }
        var summary = container.children[0];
        var tsBlock = container.children[1];
        if (summary.textContent !== "虚频 2 个") {
            console.error("FAIL: summary mismatch: " + summary.textContent);
            process.exit(1);
        }

        var tsHintDiv = tsBlock.children[0];
        if (tsHintDiv.textContent !== "高阶鞍点或未充分优化") {
            console.error("FAIL: TS hint text " + tsHintDiv.textContent);
            process.exit(1);
        }
        var tsSuffixDiv = tsBlock.children[1];
        if (tsSuffixDiv.textContent !== "仍需检查振动方向及 IRC") {
            console.error("FAIL: TS suffix " + tsSuffixDiv.textContent);
            process.exit(1);
        }
        if (tsBlock.children.length !== 2) {
            console.error("FAIL: unexpected TS evidence lines " + tsBlock.children.length);
            process.exit(1);
        }

        var rows = container.children[2].children;        if (rows.length !== 4) {
            console.error("FAIL: expected 4 rows, got " + rows.length);
            process.exit(1);
        }
        // Negatives first, most-negative first
        if (rows[0].children[1].textContent !== "-797.72 cm⁻¹") {
            console.error("FAIL: first row freq " + rows[0].children[1].textContent);
            process.exit(1);
        }
        if (rows[1].children[1].textContent !== "-100.00 cm⁻¹") {
            console.error("FAIL: second row freq " + rows[1].children[1].textContent);
            process.exit(1);
        }
        // Default-selected most-negative imaginary row is highlighted
        if (rows[0].className.indexOf("sv-active") < 0 || rows[0]["attr_data-mode-index"] !== "6") {
            console.error("FAIL: mode 6 row not highlighted");
            process.exit(1);
        }
        // Imaginary chip text
        if (rows[0].children[2].textContent !== "虚频") {
            console.error("FAIL: chip text " + rows[0].children[2].textContent);
            process.exit(1);
        }
        // IR intensity 1dp only when present
        if (rows[0].children[3].textContent !== "24.8 km·mol⁻¹") {
            console.error("FAIL: ir text " + rows[0].children[3].textContent);
            process.exit(1);
        }
        if (rows[1].children.length !== 3) {
            console.error("FAIL: null ir_intensity must render no IR span");
            process.exit(1);
        }
        console.log("PASS");
    """
    ).replace("JS_PATH", json.dumps(str(_VIB_JS)))

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"Node modes-render test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


# ---------------------------------------------------------------------------
# Vibration dock regression tests (UX optimization plan)
# ---------------------------------------------------------------------------


def test_vibration_dock_host_element_exists() -> None:
    """Dock host element exists in HTML with display:none default."""
    html = read_workbench_content()
    dock_match = re.search(r'<div[^>]*id="sv-vibration-dock"[^>]*>', html)
    assert dock_match is not None, "sv-vibration-dock div missing from HTML"
    assert "display:none" in dock_match.group(0).replace(" ", ""), (
        "sv-vibration-dock must default to display:none"
    )


def test_vibration_dock_css_exists() -> None:
    """CSS has dock layout styles with font tokens and height constants."""
    css = (FRONTEND_CSS_DIR / "structure_viewer.css").read_text(encoding="utf-8")
    assert ".sv-vibration-dock" in css
    assert ".sv-vib-dock-header" in css
    assert ".sv-vib-dock-body" in css
    assert ".sv-vib-dock-col-ts" in css
    assert ".sv-vib-dock-col-modes" in css
    assert ".sv-vib-dock-col-controls" in css
    assert "--font-mono" in css
    assert "--font-ui" in css
    assert ".sv-vib-filter-tabs" in css
    assert ".sv-vib-filter-tab" in css
    assert ".sv-vib-mode-track" in css
    assert ".sv-vib-mode-item" in css


def test_vibration_dock_height_constants() -> None:
    """Dock height constants: default 236, min 180, max 340, max ratio 42%."""
    sv = (FRONTEND_JS_DIR / "structure_viewer.js").read_text(encoding="utf-8")
    assert "DOCK_DEFAULT_HEIGHT = 236" in sv
    assert "DOCK_MIN_HEIGHT = 180" in sv
    assert "DOCK_MAX_HEIGHT = 340" in sv
    assert "DOCK_MAX_RATIO = 0.42" in sv


def test_vibration_dock_state_persistence() -> None:
    """Dock state persisted under acp.sv.view.* namespace."""
    sv = (FRONTEND_JS_DIR / "structure_viewer.js").read_text(encoding="utf-8")
    assert "vibDock" in sv
    assert "_getDockPersist" in sv
    assert "_setDockPersist" in sv


def test_vibration_dock_viewer_resize_called() -> None:
    """viewer.resize() called on dock toggle (no zoomTo)."""
    sv = (FRONTEND_JS_DIR / "structure_viewer.js").read_text(encoding="utf-8")
    assert "_triggerViewerResize" in sv
    assert "viewer.resize()" in sv
    assert "zoomTo" not in sv.split("_triggerViewerResize")[1].split("function ")[0]


def test_vibration_dock_summary_entries() -> None:
    """Summary bar has vibration summary entries (imaginary + modes)."""
    sv = (FRONTEND_JS_DIR / "structure_viewer.js").read_text(encoding="utf-8")
    assert "sv-vib-summary-imag" in sv
    assert "sv-vib-summary-modes" in sv
    assert "toggleVibrationDock" in sv


def test_vibration_dock_no_positive_freq_labeled_imaginary() -> None:
    """Positive frequency modes must show positive label, not imaginary."""
    vib = _VIB_JS.read_text(encoding="utf-8")
    assert "POSITIVE_LABEL" in vib
    assert "IMAGINARY_LABEL" in vib
    assert "\\u6b63\\u9891" in vib


def test_vibration_dock_filter_tabs() -> None:
    """Filter tabs: 虚频/有效模式/全部 with mode categorization."""
    vib = _VIB_JS.read_text(encoding="utf-8")
    assert "_categorizeModes" in vib
    assert "FILTER_IMAGINARY" in vib
    assert "FILTER_VALID" in vib
    assert "FILTER_ALL" in vib
    assert "sv-vib-filter-tab" in vib
    assert "_filterTab" in vib


def test_vibration_dock_zero_mode_collapse() -> None:
    """Zero modes collapsed by default with expand button."""
    vib = _VIB_JS.read_text(encoding="utf-8")
    assert "ZERO_MODES" in vib
    assert "sv-vib-zero-collapse" in vib
    assert "\\u5e73\\u79fb/\\u8f6c\\u52a8\\u96f6\\u6a21" in vib


def test_vibration_dock_keyboard_navigation() -> None:
    """Keyboard left/right mode switching in the dock."""
    vib = _VIB_JS.read_text(encoding="utf-8")
    assert "_navigateMode" in vib
    assert "ArrowRight" in vib
    assert "ArrowLeft" in vib


def test_vibration_dock_default_mode_selection_order() -> None:
    """Default mode selection: most-negative frequency → first positive."""
    vib = _VIB_JS.read_text(encoding="utf-8")
    assert "bestNeg" in vib
    assert "firstPos" in vib


def test_vibration_dock_i18n_keys_new() -> None:
    """New dock i18n keys exist in both locales."""
    html = read_workbench_content()
    zh_keys = _extract_vib_keys(html, _ZH_BLOCK_RE)
    en_keys = _extract_vib_keys(html, _EN_BLOCK_RE)
    new_keys = {
        "structure.vib.current_positive",
        "structure.vib.current_imaginary",
        "structure.vib.dock_title",
        "structure.vib.filter_imaginary",
        "structure.vib.filter_valid",
        "structure.vib.filter_all",
        "structure.vib.zero_modes",
        "structure.vib.positive_label",
        "structure.vib.imaginary_label",
        "structure.vib.imag_summary",
        "structure.vib.modes_summary",
    }
    assert new_keys <= zh_keys, f"Missing zh-CN new dock keys: {sorted(new_keys - zh_keys)}"
    assert new_keys <= en_keys, f"Missing en-US new dock keys: {sorted(new_keys - en_keys)}"


def test_vibration_viewer_version_bump() -> None:
    """ACPVibrationViewer bumped to 0.9.0."""
    vib = _VIB_JS.read_text(encoding="utf-8")
    assert 'var VERSION = "0.9.0"' in vib


def test_vibration_dock_ir_unit_updated() -> None:
    """IR unit updated from km/mol to km·mol⁻¹."""
    vib = _VIB_JS.read_text(encoding="utf-8")
    assert "km\u00b7mol\u207b\u00b9" in vib
    assert "IR_UNIT" in vib


def test_vibration_old_drawer_removed_from_html() -> None:
    """Old sv-drawer-vibration div removed from HTML."""
    html = read_workbench_content()
    assert 'id="sv-drawer-vibration"' not in html


def test_vibration_viewer_node_default_mode_order() -> None:
    """Node logic: defaultModeIndex picks the most-negative mode first."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    script = textwrap.dedent("""\
        var window = { fetch: null };
        require(JS_PATH);
        var ns = window.ACPVibrationViewer;

        // Case 1: multiple negative modes, choose the most negative.
        var tsModes = [
            { mode_index: 5, frequency_cm1: 42.44, imaginary: false },
            { mode_index: 6, frequency_cm1: -419.46, imaginary: true },
            { mode_index: 7, frequency_cm1: -30.0, imaginary: true }
        ];
        if (ns.defaultModeIndex(tsModes) !== 6) {
            console.error("FAIL: expected 6 (most negative), got " + ns.defaultModeIndex(tsModes));
            process.exit(1);
        }

        // Case 2: a weak negative mode is still selected.
        var negModes = [
            { mode_index: 3, frequency_cm1: -30.0, imaginary: true },
            { mode_index: 4, frequency_cm1: 100.0, imaginary: false }
        ];
        if (ns.defaultModeIndex(negModes) !== 3) {
            console.error("FAIL: expected 3 (negative), got " + ns.defaultModeIndex(negModes));
            process.exit(1);
        }

        // Case 3: no negatives at all
        var posModes = [
            { mode_index: 1, frequency_cm1: 50.0, imaginary: false },
            { mode_index: 2, frequency_cm1: 100.0, imaginary: false }
        ];
        if (ns.defaultModeIndex(posModes) !== 1) {
            console.error("FAIL: expected 1 (first pos), got " + ns.defaultModeIndex(posModes));
            process.exit(1);
        }

        console.log("PASS");
    """).replace("JS_PATH", json.dumps(str(_VIB_JS)))

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"Node default mode order test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_vibration_viewer_node_categorize_modes() -> None:
    """Node logic: _categorizeModes splits into imaginary/valid/all/zeros."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    script = textwrap.dedent("""\
        var window = { fetch: null };
        require(JS_PATH);
        var ns = window.ACPVibrationViewer;

        var modes = [
            { mode_index: 1, frequency_cm1: 0.0, imaginary: false },
            { mode_index: 2, frequency_cm1: 0.1, imaginary: false },
            { mode_index: 3, frequency_cm1: -419.46, imaginary: true },
            { mode_index: 4, frequency_cm1: -30.0, imaginary: true },
            { mode_index: 5, frequency_cm1: 100.0, imaginary: false },
            { mode_index: 6, frequency_cm1: 200.0, imaginary: false },
            { mode_index: 7, frequency_cm1: -0.01, imaginary: true }
        ];
        var cats = ns._categorizeModes(modes);
        if (cats.imaginary.length !== 3) {
            console.error("FAIL: imaginary count " + cats.imaginary.length);
            process.exit(1);
        }
        if (cats.valid.length !== 2) {
            console.error("FAIL: valid count " + cats.valid.length);
            process.exit(1);
        }
        if (cats.zeros.length !== 2) {
            console.error("FAIL: zeros count " + cats.zeros.length);
            process.exit(1);
        }
        if (cats.all.length !== 7) {
            console.error("FAIL: all count " + cats.all.length);
            process.exit(1);
        }
        if (cats.imaginaryCount !== 3) {
            console.error("FAIL: imaginary count " + cats.imaginaryCount);
            process.exit(1);
        }
        console.log("PASS");
    """).replace("JS_PATH", json.dumps(str(_VIB_JS)))

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"Node categorize modes test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_vibration_dock_drag_handle_exists() -> None:
    """Dock grip element rendered in JS with correct CSS class."""
    vib = _VIB_JS.read_text(encoding="utf-8")
    sv = (FRONTEND_JS_DIR / "structure_viewer.js").read_text(encoding="utf-8")
    css = (FRONTEND_CSS_DIR / "structure_viewer.css").read_text(encoding="utf-8")
    assert "sv-vib-dock-grip" in vib, "grip element class missing from vibration_viewer.js"
    assert "sv-vib-dock-grip" in css, "grip CSS class missing"
    assert "_onDockGripMouseDown" in sv, "drag mousedown handler missing"
    assert "_onDockDragMove" in sv, "drag mousemove handler missing"
    assert "_onDockDragEnd" in sv, "drag mouseup handler missing"
    assert "_attachDockGripListener" in sv, "grip listener attachment missing"


def test_vibration_dock_drag_clamp_constants() -> None:
    """Drag logic uses the correct clamp constants (180/340/0.42)."""
    sv = (FRONTEND_JS_DIR / "structure_viewer.js").read_text(encoding="utf-8")
    assert "_clampDockHeight" in sv, "clamp function missing"
    assert "DOCK_MIN_HEIGHT" in sv
    assert "DOCK_MAX_HEIGHT" in sv
    assert "DOCK_MAX_RATIO" in sv
    clamp_fn = sv.split("function _clampDockHeight(", 1)[1].split("}", 1)[0]
    assert "DOCK_MIN_HEIGHT" in clamp_fn, "clamp must reference DOCK_MIN_HEIGHT"
    assert "DOCK_MAX_HEIGHT" in clamp_fn, "clamp must reference DOCK_MAX_HEIGHT"
    assert "DOCK_MAX_RATIO" in clamp_fn, "clamp must reference DOCK_MAX_RATIO"


def test_vibration_dock_drag_persistence_on_mouseup() -> None:
    """Drag end persists height and calls viewer resize."""
    sv = (FRONTEND_JS_DIR / "structure_viewer.js").read_text(encoding="utf-8")
    drag_end = sv.split("function _onDockDragEnd(", 1)[1].split("\n  function ", 1)[0]
    assert "_setDockPersist" in drag_end, "drag end must persist height"
    assert "_triggerViewerResize" in drag_end, "drag end must trigger resize"
    assert "removeEventListener" in drag_end, "drag end must clean up listeners"


def test_vibration_dock_drag_no_leak() -> None:
    """Drag cleanup removes both mousemove and mouseup listeners."""
    sv = (FRONTEND_JS_DIR / "structure_viewer.js").read_text(encoding="utf-8")
    close_fn = sv.split("function closeVibrationDock(", 1)[1].split("\n  function ", 1)[0]
    assert "removeEventListener" in close_fn, "closeVibrationDock must clean up drag listeners"
    assert "_dockDragState" in close_fn, "closeVibrationDock must reset drag state"


def test_vibration_summary_loading_class_used() -> None:
    """sv-vib-summary-loading class used in summary bar during fetch."""
    sv = (FRONTEND_JS_DIR / "structure_viewer.js").read_text(encoding="utf-8")
    assert "sv-vib-summary-loading" in sv, "loading spinner class missing"
    render_fn = sv.split("function renderStructureViewer()", 1)[1]
    render_fn = render_fn[: render_fn.index("function _renderStripItem(")]
    assert "sv-vib-summary-loading" in render_fn, (
        "loading class must be used in renderStructureViewer"
    )
    assert "vibLoading" in render_fn, "loading state check missing from summary bar"


def test_vibration_summary_loading_no_height_change() -> None:
    """Loading indicator is inline (no block-level height change)."""
    css = (FRONTEND_CSS_DIR / "structure_viewer.css").read_text(encoding="utf-8")
    loading_block = css.split(".sv-vib-summary-loading {", 1)[1].split("}", 1)[0]
    assert (
        "display: inline-block" in loading_block
        or "display:inline-block" in loading_block.replace(" ", "")
    ), "loading indicator must be inline-block to avoid height change"
    assert "12px" in loading_block or "10px" in loading_block, (
        "loading indicator must be small (10-12px)"
    )


def test_vibration_grip_css_cursor_and_position() -> None:
    """Grip element has ns-resize cursor and is positioned at dock top."""
    css = (FRONTEND_CSS_DIR / "structure_viewer.css").read_text(encoding="utf-8")
    grip_block = css.split(".sv-vib-dock-grip {", 1)[1].split("}", 1)[0]
    assert "cursor: ns-resize" in grip_block or "cursor:ns-resize" in grip_block.replace(" ", ""), (
        "grip must have ns-resize cursor"
    )
    assert "position: absolute" in grip_block or "position:absolute" in grip_block.replace(
        " ", ""
    ), "grip must be absolutely positioned"


def test_vibration_viewer_arrow_contract() -> None:
    """Contract: addArrow/removeAllShapes usage, clamp bounds, H-skip
    threshold constant, slider attrs, viewer resolution, displayed-coords
    source, and the three toggle labels in both locales."""
    vib = _VIB_JS.read_text(encoding="utf-8")
    sv = _SV_JS_PATH.read_text(encoding="utf-8")
    html = read_workbench_content()

    # 3Dmol shape API usage + arrow spec
    assert ".addArrow(" in vib, "viewer.addArrow usage missing"
    assert "removeAllShapes" in vib, "clearArrows must use removeAllShapes"
    assert "ARROW_RADIUS = 0.06" in vib
    assert "radius: ARROW_RADIUS" in vib

    # Amplitude clamp [0.05, 0.6], default 0.25
    assert "AMP_MIN = 0.05" in vib
    assert "AMP_MAX = 0.6" in vib
    assert "AMP_DEFAULT = 0.25" in vib
    assert "clampAmplitude" in vib

    # Zero-vector guard + named H-skip threshold
    assert "VECTOR_EPS" in vib
    assert "H_SKIP_ATOM_THRESHOLD = 50" in vib
    assert "hydrogenSkipMask" in vib

    # Arrow API surface
    for name in (
        "computeArrowEndpoints",
        "applyArrows",
        "clearArrows",
        "refreshArrows",
        "setDisplayMode",
        "setAmplitude",
        "arrowState",
        "_viewerImpl",
    ):
        assert name in vib, f"{name} missing from vibration_viewer.js"

    # Slider bounds/step + toggle state machine values
    assert '"0.01"' in vib
    assert '"arrows"' in vib and '"animation"' in vib and '"combo"' in vib

    # Displayed-coordinates source + geometry-load arrow refresh hook
    for name in ("displayedCoords", "displayedSymbols", "displayedEntryId", "_parseXyzFirstFrame"):
        assert name in sv, f"{name} missing from structure_viewer.js"
    assert "ACPVibrationViewer.refreshArrows" in sv

    # Toggle labels in BOTH locales (animation_soon stub key removed in todo 29)
    for zh, en in (
        (
            '"structure.vib.arrow.display_arrows": "箭头"',
            '"structure.vib.arrow.display_arrows": "Arrows"',
        ),
        (
            '"structure.vib.arrow.display_animation": "动画"',
            '"structure.vib.arrow.display_animation": "Animation"',
        ),
        (
            '"structure.vib.arrow.display_combo": "箭头+动画"',
            '"structure.vib.arrow.display_combo": "Arrows+Animation"',
        ),
    ):
        assert zh in html, f"zh toggle label missing: {zh}"
        assert en in html, f"en toggle label missing: {en}"


def test_vibration_viewer_node_arrow_math() -> None:
    """Node logic: endpoint math within 1e-9, zero/NaN guards, no vector
    mutation, clamp bounds, H-skip at 50/51 atoms, applyArrows/clearArrows
    against a fake viewer, and the three-state toggle machine."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    script = textwrap.dedent("""\
        var window = { fetch: null };
        require(JS_PATH);
        var ns = window.ACPVibrationViewer;

        function close(a, b) { return Math.abs(a - b) <= 1e-9; }

        // 3-atom fixture: unit-ish vectors, amp 0.25
        var coords = [[0, 0, 0], [1, 1, 1], [2, 2, 2]];
        var vectors = [[1, 0, 0], [0, 2, 0], [0, 0, -1]];
        var before = JSON.stringify(vectors);
        var rows = ns.computeArrowEndpoints(coords, vectors, 0.25);
        if (!rows || rows.length !== 3) {
            console.error("FAIL: expected 3 rows");
            process.exit(1);
        }
        var expectedEnds = [[0.25, 0, 0], [1, 1.25, 1], [2, 2, 1.75]];
        for (var i = 0; i < 3; i++) {
            for (var k = 0; k < 3; k++) {
                if (!close(rows[i].start[k], coords[i][k]) ||
                    !close(rows[i].end[k], expectedEnds[i][k])) {
                    console.error("FAIL: atom " + i + " axis " + k);
                    process.exit(1);
                }
            }
        }
        if (JSON.stringify(vectors) !== before) {
            console.error("FAIL: stored vectors were mutated");
            process.exit(1);
        }

        // zero-length and NaN vectors -> null rows (never NaN)
        var rows2 = ns.computeArrowEndpoints(
            [[0, 0, 0], [1, 1, 1], [2, 2, 2]],
            [[0, 0, 0], [NaN, 0, 0], [1, 1, 1]],
            0.25
        );
        if (rows2[0] !== null || rows2[1] !== null || rows2[2] === null) {
            console.error("FAIL: zero/NaN guards");
            process.exit(1);
        }
        if (ns.computeArrowEndpoints(null, vectors, 0.25) !== null) {
            console.error("FAIL: null coords should return null");
            process.exit(1);
        }

        // clamp bounds
        var clamps = { "-1": 0.05, "0.25": 0.25, "5": 0.6, "0.03": 0.05 };
        for (var input in clamps) {
            if (ns.clampAmplitude(parseFloat(input)) !== clamps[input]) {
                console.error("FAIL: clamp(" + input + ")");
                process.exit(1);
            }
        }
        if (ns.clampAmplitude(undefined) !== 0.25) {
            console.error("FAIL: clamp default");
            process.exit(1);
        }

        // H skip at threshold boundary: 50 -> no skip, 51 -> H masked
        var syms50 = [], syms51 = [];
        for (var s = 0; s < 50; s++) syms50.push(s % 2 ? "H" : "C");
        for (var s2 = 0; s2 < 51; s2++) syms51.push(s2 % 2 ? "h" : "C");
        var mask50 = ns.hydrogenSkipMask(syms50, 50);
        var mask51 = ns.hydrogenSkipMask(syms51, 51);
        for (var m = 0; m < 50; m++) {
            if (mask50[m]) { console.error("FAIL: skip at 50 atoms"); process.exit(1); }
        }
        for (var m2 = 0; m2 < 51; m2++) {
            if (mask51[m2] !== (m2 % 2 === 1)) {
                console.error("FAIL: H mask at 51 atoms, idx " + m2);
                process.exit(1);
            }
        }

        // applyArrows / clearArrows against a fake viewer
        var added = [], cleared = 0;
        var fakeViewer = {
            addArrow: function (spec) { added.push(spec); },
            removeAllShapes: function () { cleared += 1; },
            render: function () {}
        };
        var count = ns.applyArrows(fakeViewer, rows, ["C", "C", "C"], { color: "#ff0000" });
        if (count !== 3 || added.length !== 3) {
            console.error("FAIL: applyArrows count " + count);
            process.exit(1);
        }
        if (added[0].radius !== 0.06 || added[0].color !== "#ff0000") {
            console.error("FAIL: arrow spec radius/color");
            process.exit(1);
        }
        if (added[1].start.x !== 1 || !close(added[1].end.y, 1.25)) {
            console.error("FAIL: arrow endpoints");
            process.exit(1);
        }
        // null endpoints skipped; H skipped above threshold
        var count2 = ns.applyArrows(fakeViewer, rows2, ["C", "C", "C"], {});
        if (count2 !== 1) {
            console.error("FAIL: null rows must be skipped, got " + count2);
            process.exit(1);
        }
        var bigRows = [], bigSyms = [];
        for (var b = 0; b < 51; b++) {
            bigRows.push({ start: [b, 0, 0], end: [b, 0.25, 0] });
            bigSyms.push(b % 2 ? "H" : "C");
        }
        var count3 = ns.applyArrows(fakeViewer, bigRows, bigSyms, {});
        if (count3 !== 26) {
            console.error("FAIL: expected 26 non-H arrows, got " + count3);
            process.exit(1);
        }
        ns.clearArrows(fakeViewer);
        if (cleared !== 1) {
            console.error("FAIL: clearArrows must call removeAllShapes");
            process.exit(1);
        }
        if (ns.applyArrows(null, rows, null, {}) !== 0) {
            console.error("FAIL: null viewer must be a no-op");
            process.exit(1);
        }

        // three-state toggle machine
        ns.setDisplayMode("animation");
        if (ns.arrowState.displayMode !== "animation") {
            console.error("FAIL: displayMode animation");
            process.exit(1);
        }
        ns.setDisplayMode("combo");
        ns.setDisplayMode("bogus");
        if (ns.arrowState.displayMode !== "combo") {
            console.error("FAIL: invalid displayMode must be ignored");
            process.exit(1);
        }
        ns.arrowState.enabled = true;
        ns.arrowState.displayMode = "arrows";
        if (!ns._arrowsVisible()) { console.error("FAIL: arrows visible"); process.exit(1); }
        ns.arrowState.displayMode = "animation";
        if (ns._arrowsVisible()) { console.error("FAIL: animation hides arrows"); process.exit(1); }
        ns.arrowState.displayMode = "combo";
        if (!ns._arrowsVisible()) { console.error("FAIL: combo shows arrows"); process.exit(1); }

        // clamped amplitude setter
        ns.setAmplitude(5);
        if (ns.arrowState.amplitude !== 0.6) {
            console.error("FAIL: setAmplitude clamp");
            process.exit(1);
        }
        console.log("PASS");
    """).replace("JS_PATH", json.dumps(str(_VIB_JS)))

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"Node arrow-math test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


# ---------------------------------------------------------------------------
# Wave 5 / todo 29: instance playback + controls + camera preservation
# ---------------------------------------------------------------------------


def test_vibration_viewer_animation_contract() -> None:
    """Contract: instance-owned playback interval (one interval per
    instance, no second animation), getView/setView camera save/restore,
    removeAllModels+addModel rebuild, NO viewer.animate(), throttle
    constant, speed clamp, injectables, style reuse, control labels."""
    vib = _VIB_JS.read_text(encoding="utf-8")
    html = read_workbench_content()

    # T13: playback runs on the scene instance interval — vibration owns
    # NO timer of its own (the rAF loop is retired).
    assert "requestAnimationFrame" not in vib, "no second animation outside the instance"
    assert "setInterval" not in vib, "the playback interval is owned by the scene instance"
    for name in (
        "_nowImpl", "playSeq", "pausePlayback", "resumePlayback",
        "setHidden", "stopPlayback",
    ):
        assert name in vib, f"{name} missing"
    assert ".play(" in vib, "playback must start via the instance play() entry"

    # Camera preservation + per-frame rebuild recipe
    assert "getView" in vib
    assert "setView" in vib
    assert "removeAllModels" in vib
    assert "addModel(" in vib

    # Built-in viewer.animate() is FORBIDDEN (no variable speed/phase)
    assert ".animate(" not in vib, "viewer.animate() must not be used"

    # Throttle + rate + speed clamp constants
    assert "FRAME_MIN_MS = 33" in vib
    assert "CYCLE_RATE_HZ = 0.6" in vib
    assert "SPEED_MIN = 0.25" in vib
    assert "SPEED_MAX = 2" in vib
    assert "SPEED_OPTIONS = [0.25, 0.5, 1, 2]" in vib

    # Pure helpers + control functions
    for name in (
        "computeDisplacedCoords",
        "buildDisplacedXyz",
        "clampSpeed",
        "playAnimation",
        "pauseAnimation",
        "togglePlay",
        "stopAnimation",
        "setSpeed",
        "toggleInvertPhase",
        "animationState",
    ):
        assert name in vib, f"{name} missing from vibration_viewer.js"

    # Per-frame style reuses the app's style path
    assert "applyStylePreset(molDoc.style)" in vib
    assert "getCurrentSphereScale" in vib

    # Stub hint fully removed (JS + dictionaries)
    assert "animation_soon" not in vib
    assert "animation_soon" not in html

    # Playback control labels in BOTH locales
    for zh, en in (
        ('"structure.vib.anim.play": "播放"', '"structure.vib.anim.play": "Play"'),
        ('"structure.vib.anim.pause": "暂停"', '"structure.vib.anim.pause": "Pause"'),
        ('"structure.vib.anim.speed": "速度"', '"structure.vib.anim.speed": "Speed"'),
        ('"structure.vib.anim.invert": "相位反转"', '"structure.vib.anim.invert": "Invert Phase"'),
    ):
        assert zh in html, f"zh label missing: {zh}"
        assert en in html, f"en label missing: {en}"


def test_vibration_viewer_node_displacement_math() -> None:
    """Node logic: r = r0 + A*sin(phase)*normalize(v); invert mirrors;
    zero/NaN stay at equilibrium; skipMask freezes atoms; vectors not
    mutated; buildDisplacedXyz preserves atom ordering."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    script = textwrap.dedent("""\
        var window = { fetch: null };
        require(JS_PATH);
        var ns = window.ACPVibrationViewer;
        function close(a, b) { return Math.abs(a - b) <= 1e-9; }

        var eq = [[0, 0, 0]];
        var v = [[1, 0, 0]];
        var before = JSON.stringify(v);

        function rowAt(phase, opts) {
            return ns.computeDisplacedCoords(eq, v, 0.25, phase, opts)[0];
        }
        var atPeak = rowAt(Math.PI / 2);
        if (!close(atPeak[0], 0.25) || !close(atPeak[1], 0) || !close(atPeak[2], 0)) {
            console.error("FAIL: phase pi/2 -> " + JSON.stringify(atPeak));
            process.exit(1);
        }
        var atZero = rowAt(0);
        if (!close(atZero[0], 0)) { console.error("FAIL: phase 0"); process.exit(1); }
        var atTrough = rowAt(Math.PI * 1.5);
        if (!close(atTrough[0], -0.25)) { console.error("FAIL: trough phase"); process.exit(1); }
        var inverted = rowAt(Math.PI / 2, { invert: true });
        if (!close(inverted[0], -0.25)) { console.error("FAIL: invert mirror"); process.exit(1); }

        // zero / NaN vectors stay at equilibrium
        var mixed = ns.computeDisplacedCoords(
            [[1, 1, 1], [2, 2, 2]],
            [[0, 0, 0], [NaN, 1, 0]],
            0.25, Math.PI / 2, {}
        );
        if (!close(mixed[0][0], 1) || !close(mixed[1][0], 2)) {
            console.error("FAIL: zero/NaN must stay at equilibrium");
            process.exit(1);
        }
        // skipMask freezes the masked atom
        var masked = ns.computeDisplacedCoords(
            [[0, 0, 0], [5, 5, 5]],
            [[1, 0, 0], [1, 0, 0]],
            0.25, Math.PI / 2, { skipMask: [false, true] }
        );
        if (!close(masked[0][0], 0.25) || !close(masked[1][0], 5)) {
            console.error("FAIL: skipMask must freeze atom");
            process.exit(1);
        }
        // normalization display-only
        if (JSON.stringify(v) !== before) {
            console.error("FAIL: vectors mutated");
            process.exit(1);
        }
        if (ns.computeDisplacedCoords(null, v, 0.25, 0) !== null) {
            console.error("FAIL: null input");
            process.exit(1);
        }

        // buildDisplacedXyz: atom ordering + header preserved
        var xyz = ns.buildDisplacedXyz(
            ["C", "H", "O"],
            [[0, 0, 0], [1.1, 0, 0], [0, 0, 2.2]],
            "comment"
        );
        var lines = xyz.trim().split("\\n");
        if (lines[0] !== "3" || lines[1] !== "comment") {
            console.error("FAIL: xyz header");
            process.exit(1);
        }
        var syms = [];
        for (var li = 2; li <= 4; li++) syms.push(lines[li].split(/\\s+/)[0]);
        if (JSON.stringify(syms) !== JSON.stringify(["C", "H", "O"])) {
            console.error("FAIL: atom order changed: " + JSON.stringify(syms));
            process.exit(1);
        }
        if (ns.clampSpeed(10) !== 2 || ns.clampSpeed(0.01) !== 0.25 || ns.clampSpeed(1) !== 1) {
            console.error("FAIL: clampSpeed bounds");
            process.exit(1);
        }
        console.log("PASS");
    """).replace("JS_PATH", json.dumps(str(_VIB_JS)))

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"Node displacement-math test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_vibration_viewer_node_animation_loop() -> None:
    """Node logic with a recording instance stub + fake clock: camera
    captured before the rebuild and restored with the SAME view object,
    ~30fps throttle, phase advance, equilibrium restore on stop, instance
    stopPlayback routing, idempotency."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    script = textwrap.dedent("""\
        var window = { fetch: null };
        require(JS_PATH);
        var ns = window.ACPVibrationViewer;

        var calls = [];
        var viewSeq = 0, lastView = null, lastSetView = null;
        var models = [], arrows = 0, renders = 0;
        var playCalls = 0, pauseCalls = 0, resumeCalls = 0, stopCalls = 0;
        var tickFn = null, playMs = null, playSeq = 0, hiddenLog = [];
        var inst = {
            play: function (onTick, ms) {
                playCalls += 1; tickFn = onTick; playMs = ms; playSeq += 1;
                return playSeq;
            },
            pausePlayback: function () { pauseCalls += 1; },
            resumePlayback: function () { resumeCalls += 1; },
            stopPlayback: function () { stopCalls += 1; tickFn = null; },
            setHidden: function (v) { hiddenLog.push(!!v); },
            getView: function () {
                viewSeq += 1;
                lastView = { seq: viewSeq };
                calls.push("getView");
                return lastView;
            },
            setView: function (v) {
                lastSetView = v;
                calls.push("setView");
            },
            removeAllModels: function () { calls.push("removeAllModels"); },
            addModel: function (xyz, fmt) {
                calls.push("addModel");
                models.push({ xyz: xyz, fmt: fmt });
                return {};
            },
            addStyle: function () {},
            removeAllShapes: function () { calls.push("removeAllShapes"); },
            addArrow: function () { arrows += 1; },
            render: function () { renders += 1; }
        };
        ns._viewerImpl = function () { return inst; };

        var nowMs = 0;
        ns._nowImpl = function () { return nowMs; };
        function flush(ts) {
            nowMs = ts;
            if (tickFn) tickFn(ts);
        }

        var coords = [[0, 0, 0], [1, 1, 1], [2, 2, 2]];
        var vectors = [[1, 0, 0], [0, 2, 0], [0, 0, 2]];
        ns.state.jobId = "job1";
        ns.state.entryId = "e1";
        ns.state.data = {
            available: true, reason: null,
            modes: [{ mode_index: 6, frequency_cm1: -797.72, imaginary: true,
                      ir_intensity: null, vectors: vectors }]
        };
        ns.state.selectedModeIndex = 6;
        window.ACPStructureViewer = {
            state: {
                displayedCoords: coords,
                displayedSymbols: ["C", "O", "C"],
                displayedEntryId: "e1"
            }
        };
        ns.arrowState.displayMode = "combo";
        ns.arrowState.enabled = true;

        ns.playAnimation();
        if (!ns.animationState.playing || playCalls !== 1 || playMs !== 33) {
            console.error("FAIL: playAnimation must schedule exactly one instance interval");
            process.exit(1);
        }
        ns.playAnimation();
        if (playCalls !== 1) {
            console.error("FAIL: replay while playing must not open a second interval");
            process.exit(1);
        }

        calls.length = 0;
        flush(1000);
        // frame 1 sequence: getView < removeAllModels < addModel < removeShapes < setView < render
        function idx(tag) {
            for (var i = 0; i < calls.length; i++) if (calls[i] === tag) return i;
            return -1;
        }
        var ig = idx("getView"), irm = idx("removeAllModels"), iam = idx("addModel");
        var irs = idx("removeAllShapes"), isv = idx("setView");
        if (ig < 0 || irm < 0 || iam < 0 || irs < 0 || isv < 0 ||
            !(ig < irm && irm < iam && iam < irs && irs < isv)) {
            console.error("FAIL: frame call order " + JSON.stringify(calls));
            process.exit(1);
        }
        if (lastSetView === null || lastSetView.seq !== viewSeq) {
            console.error("FAIL: setView must receive the SAME view object as getView");
            process.exit(1);
        }
        if (models.length !== 1) {
            console.error("FAIL: expected 1 model after first frame");
            process.exit(1);
        }
        if (arrows !== 3) {
            console.error("FAIL: combo mode must draw 3 arrows per frame");
            process.exit(1);
        }

        // throttle: +10ms -> no new frame; +40ms -> advances
        flush(1010);
        if (models.length !== 1) {
            console.error("FAIL: 10ms gap must be throttled");
            process.exit(1);
        }
        flush(1040);
        if (models.length !== 2) {
            console.error("FAIL: 40ms gap must render a frame");
            process.exit(1);
        }
        if (ns.animationState.phase <= 0) {
            console.error("FAIL: phase must advance");
            process.exit(1);
        }
        if (models[1].xyz === models[0].xyz) {
            console.error("FAIL: displaced xyz must differ from equilibrium");
            process.exit(1);
        }
        if (models[1].fmt !== "xyz") {
            console.error("FAIL: addModel format must be xyz");
            process.exit(1);
        }

        // stop: instance stopPlayback + exact equilibrium restore + idempotent
        var modelCountBeforeStop = models.length;
        ns.stopAnimation();
        if (stopCalls !== 1) {
            console.error("FAIL: stop must cancel the instance interval (stopPlayback)");
            process.exit(1);
        }
        if (ns.animationState.playSeq !== null) {
            console.error("FAIL: stop must clear the session seq");
            process.exit(1);
        }
        if (ns.animationState.playing) {
            console.error("FAIL: stop must clear playing");
            process.exit(1);
        }
        var equilibriumXyz = ns.buildDisplacedXyz(["C", "O", "C"], coords, "");
        if (models[models.length - 1].xyz !== equilibriumXyz) {
            console.error("FAIL: stop must rebuild the exact equilibrium model");
            process.exit(1);
        }
        ns.stopAnimation();
        if (models.length !== modelCountBeforeStop + 1 || stopCalls !== 1) {
            console.error("FAIL: stopAnimation must be idempotent");
            process.exit(1);
        }
        if (ns.animationState.playing) {
            console.error("FAIL: playing after repeated stop");
            process.exit(1);
        }
        console.log("PASS");
    """).replace("JS_PATH", json.dumps(str(_VIB_JS)))

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"Node animation-loop test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_vibration_viewer_teardown_contract() -> None:
    """Contract: teardown hooks at all three sites (tab switch / job switch /
    viewer destroy), entry-switch stops first, editor lock API, locked hint,
    and the idempotent-stop cancel guard."""
    vib = _VIB_JS.read_text(encoding="utf-8")
    sv = _SV_JS_PATH.read_text(encoding="utf-8")
    editor = _EDITOR_JS_PATH.read_text(encoding="utf-8")
    html = read_workbench_content()

    for name in (
        "stopAnimationAndRestore",
        "isAnimationActive",
        "handleTeardown",
        "_setEditorLocked",
    ):
        assert name in vib, f"{name} missing from vibration_viewer.js"
    assert "stopAnimationAndRestore" in vib
    # Idempotent stop: the session-clear branch is guarded by a null check
    assert "animationState.playSeq != null" in vib

    # Editor lock contract exists today (Wave 6 consumes isLocked)
    assert "setLocked" in editor and "isLocked" in editor
    assert "setLocked(true)" in vib.replace("_setEditorLocked(true)", "setLocked(true)")

    # Locked hint: STR fallback + class + i18n key in BOTH locales
    assert "sv-vib-locked-hint" in vib
    assert "structure.vib.locked_hint" in vib
    assert '"structure.vib.locked_hint": "动画播放中，编辑已暂停"' in html
    assert '"structure.vib.locked_hint": "Editing is paused while the animation plays"' in html

    # Site 1: tab switch (leaving the structure tab) tears down
    set_tab = _fn_body(html, "async function setViewerTab(tab) {")
    assert "handleTeardown" in set_tab
    assert 'tab !== "structure"' in set_tab

    # Site 2: job switch tears down FIRST (before loadStructureViewer)
    on_job = _fn_body(sv, "function onJobSelected(jobId, opts) {")
    teardown_idx = on_job.find("handleTeardown")
    load_idx = on_job.find("loadStructureViewer(jobId")
    assert teardown_idx != -1 and load_idx != -1 and teardown_idx < load_idx

    # Site 3: viewer destroy paths tear down
    clear_body = _fn_body(html, "function clearViewer() {")
    destroy_body = _fn_body(html, "function energyGraphDestroyViewer() {")
    assert "handleTeardown" in clear_body
    assert "handleTeardown" in destroy_body

    # Entry switch: animation stops BEFORE the new geometry loads
    select_body = _fn_body(sv, "function selectEntry(entryId, origin) {")
    stop_idx = select_body.find("stopAnimationAndRestore")
    load_geom_idx = select_body.find("loadSelectedGeometry()")
    assert stop_idx != -1 and load_geom_idx != -1 and stop_idx < load_geom_idx


def test_vibration_viewer_node_mutual_exclusion_and_teardown() -> None:
    """Node logic with a recording instance stub + fake clock + real editor
    skeleton: (i) play locks editing, stop unlocks + exact equilibrium +
    camera + idempotent; (ii) handleTeardown mid-play stops + restores +
    resets; (iii) entry switch mid-play stops BEFORE the new geometry loads."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    script = (
        textwrap.dedent("""\
        var window = { fetch: null };
        var AbortController = class { constructor() { this.signal = null; } abort() {} };
        require(EDITOR_PATH);
        require(SV_PATH);
        require(VIB_PATH);
        var ns = window.ACPVibrationViewer;
        var ns2 = window.ACPStructureViewer;
        var ed = window.ACPStructureEditor;

        var events = [];
        var viewSeq = 0, lastView = null, lastSetView = null;
        var addModelCount = 0, stopCalls = 0, removeShapesCount = 0;
        var tickFn = null, playSeq = 0;
        var inst = {
            play: function (onTick, ms) {
                tickFn = onTick; playSeq += 1; return playSeq;
            },
            pausePlayback: function () {},
            resumePlayback: function () {},
            stopPlayback: function () {
                stopCalls += 1; events.push("stopPlayback"); tickFn = null;
            },
            setHidden: function () {},
            getView: function () { viewSeq += 1; lastView = { seq: viewSeq }; return lastView; },
            setView: function (v) { lastSetView = v; events.push("setView"); },
            removeAllModels: function () { events.push("removeAllModels"); },
            addModel: function (xyz) {
                addModelCount += 1;
                this.lastModelXyz = xyz;
                events.push("addModel");
                return {};
            },
            addStyle: function () {},
            removeAllShapes: function () {
                removeShapesCount += 1;
                events.push("removeAllShapes");
            },
            addArrow: function () {},
            render: function () {}
        };
        ns._viewerImpl = function () { return inst; };

        var nowMs = 0;
        ns._nowImpl = function () { return nowMs; };
        function flush(ts) {
            nowMs = ts;
            if (tickFn) tickFn(ts);
        }

        var coords = [[0, 0, 0], [1, 1, 1], [2, 2, 2]];
        var vectors = [[1, 0, 0], [0, 2, 0], [0, 0, 2]];
        ns.state.jobId = "job1";
        ns.state.entryId = "e1";
        ns.state.data = {
            available: true, reason: null,
            modes: [{ mode_index: 6, frequency_cm1: -797.72, imaginary: true,
                      ir_intensity: null, vectors: vectors }]
        };
        ns.state.selectedModeIndex = 6;
        ns2.state.displayedCoords = coords;
        ns2.state.displayedSymbols = ["C", "O", "C"];
        ns2.state.displayedEntryId = "e1";
        ns.arrowState.displayMode = "arrows";
        ns.arrowState.enabled = true;

        // (i) play -> locked; stop -> unlock + equilibrium + camera + idempotent
        ns.playAnimation();
        if (!ns.isAnimationActive() || !ed.isLocked()) {
            console.error("FAIL: playing must lock the editor");
            process.exit(1);
        }
        flush(1000);
        flush(1040);
        if (addModelCount !== 2) {
            console.error("FAIL: expected 2 frames, got " + addModelCount);
            process.exit(1);
        }
        var viewBeforeStop = lastView;
        ns.stopAnimationAndRestore();
        if (ns.isAnimationActive() || ed.isLocked()) {
            console.error("FAIL: stop must unlock + deactivate");
            process.exit(1);
        }
        if (ns.animationState.phase !== 0) {
            console.error("FAIL: stop must reset phase");
            process.exit(1);
        }
        var eqXyz = ns.buildDisplacedXyz(["C", "O", "C"], coords, "");
        var lastModelXyz = inst.lastModelXyz;
        if (lastModelXyz !== eqXyz) {
            console.error("FAIL: equilibrium not restored exactly");
            process.exit(1);
        }
        if (!lastSetView || lastSetView.seq !== viewBeforeStop.seq) {
            console.error("FAIL: stop must setView the saved view");
            process.exit(1);
        }
        var counts = [addModelCount, stopCalls, removeShapesCount];
        ns.stopAnimationAndRestore();
        if (addModelCount !== counts[0] || stopCalls !== counts[1] ||
            removeShapesCount !== counts[2]) {
            console.error("FAIL: second stop must be a full no-op");
            process.exit(1);
        }

        // (ii) teardown mid-play: cancel + equilibrium + reset + unlock
        ns.playAnimation();
        flush(2000);
        if (!ns.isAnimationActive()) { console.error("FAIL: replay"); process.exit(1); }
        ns.handleTeardown();
        if (ns.isAnimationActive()) { console.error("FAIL: teardown stops loop"); process.exit(1); }
        if (events.indexOf("stopPlayback") < 0) {
            console.error("FAIL: teardown must stop the instance interval");
            process.exit(1);
        }
        if (inst.lastModelXyz !== eqXyz) {
            console.error("FAIL: teardown restores equilibrium");
            process.exit(1);
        }
        if (ed.isLocked()) { console.error("FAIL: teardown unlocks editor"); process.exit(1); }
        if (ns.state.data !== null || ns.state.selectedModeIndex !== null) {
            console.error("FAIL: teardown resets vibration state");
            process.exit(1);
        }

        // (iii) entry switch mid-play: stop BEFORE new geometry load
        ns.state.jobId = "job1";
        ns.state.entryId = "e1";
        ns.state.data = {
            available: true, reason: null,
            modes: [{ mode_index: 6, frequency_cm1: -797.72, imaginary: true,
                      ir_intensity: null, vectors: vectors }]
        };
        ns.state.selectedModeIndex = 6;
        ns2.state.displayedCoords = coords;
        ns2.state.displayedSymbols = ["C", "O", "C"];
        ns2.state.displayedEntryId = "e1";
        ns2.state.payload = {
            entries: [
                { id: "e1", geometry: { endpoint: "/g/e1" }, vibrations: { available: false } },
                { id: "e2", geometry: { endpoint: "/g/e2" }, vibrations: { available: false } }
            ]
        };
        ns2.state.selectedEntryId = "e1";
        ns2._fetchImpl = function () {
            return Promise.resolve({
                ok: true, status: 200, statusText: "OK",
                text: function () {
                    return Promise.resolve("2\\nsecond\\nC 0 0 0\\nO 1 1 1\\n");
                }
            });
        };
        window._svLoadXyzToViewer = function () { events.push("loadxyz"); };

        ns.playAnimation();
        flush(3000);
        events.length = 0;
        ns2.selectEntry("e2", "user");
        setTimeout(function () {
            var loadAt = events.indexOf("loadxyz");
            if (loadAt < 0) {
                console.error("FAIL: new entry geometry never loaded: " +
                    JSON.stringify(events));
                process.exit(1);
            }
            var lastAdd = events.lastIndexOf("addModel");
            var stopAt = events.indexOf("stopPlayback");
            if (lastAdd < 0 || !(lastAdd < loadAt)) {
                console.error("FAIL: stop must rebuild equilibrium BEFORE new load: " +
                    JSON.stringify(events));
                process.exit(1);
            }
            if (stopAt < 0 || !(stopAt < loadAt)) {
                console.error("FAIL: interval stop must precede new load");
                process.exit(1);
            }
            console.log("PASS");
        }, 30);
    """)
        .replace("VIB_PATH", json.dumps(str(_VIB_JS)))
        .replace("SV_PATH", json.dumps(str(_SV_JS_PATH)))
        .replace("EDITOR_PATH", json.dumps(str(_EDITOR_JS_PATH)))
    )

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"Node teardown test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


# ---------------------------------------------------------------------------
# Wave 5 / todo 31: TS judgment hints + phase-B contract tests (phase-B gate)
# ---------------------------------------------------------------------------


def test_vibration_viewer_ts_evidence_contract() -> None:
    """Phase-B contract: three hint strings + suffix +
    mismatch-disable branch in both locales; pure-helper names; evidence-only
    (no batch/irc validation calls)."""
    vib = _VIB_JS.read_text(encoding="utf-8")
    html = read_workbench_content()

    for name in (
        "tsJudgment",
        "tsHintText",
        "tsSuffixText",
        "geometryMismatch",
        "_catalogEntry",
    ):
        assert name in vib, f"{name} missing from vibration_viewer.js"

    # Negative-frequency rule matches the backend TS gate.
    assert "f < 0" in vib
    assert "thresholdText" not in vib

    # Mismatch-disable branch: controls suppressed, reason shown
    assert "geometryMismatch(_catalogEntry(), data)" in vib
    assert "structure.vib.ts.mismatch_reason" in vib

    # Selector contract hooks
    for cls in ("sv-vib-ts-hint", "sv-vib-ts-line", "sv-vib-ts-suffix"):
        assert cls in vib, f"{cls} class missing"

    # All phase-B labels in BOTH locales
    for zh, en in (
        (
            '"structure.vib.ts.first_order": "频率数量符合一阶鞍点"',
            '"structure.vib.ts.first_order": "Frequency count is consistent '
            'with a first-order saddle point"',
        ),
        (
            '"structure.vib.ts.no_evidence": "不是一阶鞍点证据"',
            '"structure.vib.ts.no_evidence": "Not evidence of a first-order saddle point"',
        ),
        (
            '"structure.vib.ts.higher_order": "高阶鞍点或未充分优化"',
            '"structure.vib.ts.higher_order": "Higher-order saddle or insufficiently optimized"',
        ),
        (
            '"structure.vib.ts.suffix": "仍需检查振动方向及 IRC"',
            '"structure.vib.ts.suffix": "Still verify the vibration direction and IRC"',
        ),
        (
            '"structure.vib.ts.mismatch_reason": "模式与当前几何不匹配"',
            '"structure.vib.ts.mismatch_reason": "The modes do not match the displayed geometry"',
        ),
    ):
        assert zh in html, f"zh TS label missing: {zh}"
        assert en in html, f"en TS label missing: {en}"

    # Evidence only: the hint block must never call batch/irc validation
    assert "batch" not in vib, "vibration_viewer.js must not call batch validation"
    assert "/irc" not in vib and "irc/" not in vib, "no IRC validation calls"
    assert "validate" not in vib, "no validation endpoints in the viewer"


def test_vibration_viewer_node_ts_judgment_logic() -> None:
    """Node logic: tsJudgment counts every negative frequency,
    tsHintText mapping, geometryMismatch cases,
    and the mismatch-disable render branch (no controls, animation refused)."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    script = textwrap.dedent("""\
        var window = { fetch: null };
        require(VIB_PATH);
        var ns = window.ACPVibrationViewer;

        function modesOf(freqs) {
            return freqs.map(function (f, i) {
                return { mode_index: i, frequency_cm1: f, imaginary: f < 0 };
            });
        }

        var one = ns.tsJudgment(modesOf([-34.22]));
        if (one.imaginaryCount !== 1 || one.hint !== "first_order") {
            console.error("FAIL: 1 imaginary -> first_order");
            process.exit(1);
        }
        var none = ns.tsJudgment(modesOf([0.0, 1411.55, 3896.58]));
        if (none.imaginaryCount !== 0 || none.hint !== "no_evidence") {
            console.error("FAIL: 0 -> no_evidence");
            process.exit(1);
        }
        var two = ns.tsJudgment(modesOf([-797.72, -0.01]));
        if (two.imaginaryCount !== 2 || two.hint !== "higher_order") {
            console.error("FAIL: 2 -> higher_order");
            process.exit(1);
        }
        // A weak negative frequency still counts.
        var weak = ns.tsJudgment(modesOf([-0.01]));
        if (weak.imaginaryCount !== 1) {
            console.error("FAIL: weak negative must count");
            process.exit(1);
        }

        // hint text mapping (zh STR fallbacks in Node)
        if (ns.tsHintText("first_order") !== "频率数量符合一阶鞍点" ||
            ns.tsHintText("no_evidence") !== "不是一阶鞍点证据" ||
            ns.tsHintText("higher_order") !== "高阶鞍点或未充分优化") {
            console.error("FAIL: tsHintText mapping");
            process.exit(1);
        }
        if (ns.tsHintText("bogus") !== "" || ns.tsSuffixText() !== "仍需检查振动方向及 IRC") {
            console.error("FAIL: unknown hint / suffix");
            process.exit(1);
        }

        // geometryMismatch cases
        var entryMatch = { id: "e1", source: { product_id: "batch_item_001" } };
        var entryDiff = { id: "e1", source: { product_id: "batch_item_002" } };
        var entryNone = { id: "e1", source: {} };
        var vibMatch = { geometry_product_id: "batch_item_001" };
        var vibNone = { geometry_product_id: null };
        if (ns.geometryMismatch(entryMatch, vibMatch) !== false ||
            ns.geometryMismatch(entryDiff, vibMatch) !== true ||
            ns.geometryMismatch(entryNone, vibMatch) !== false ||
            ns.geometryMismatch(entryMatch, vibNone) !== false ||
            ns.geometryMismatch(null, vibMatch) !== false ||
            ns.geometryMismatch(entryMatch, null) !== false) {
            console.error("FAIL: geometryMismatch rule");
            process.exit(1);
        }
        console.log("PASS");
    """).replace("VIB_PATH", json.dumps(str(_VIB_JS)))

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"Node TS-judgment test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_vibration_viewer_node_mismatch_disables_controls() -> None:
    """Node logic (DOM stub): geometry mismatch -> reason rendered, no arrow
    controls, playAnimation refused."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    script = textwrap.dedent("""\
        var window = { fetch: null };
        require(VIB_PATH);
        var ns = window.ACPVibrationViewer;
        function fakeEl(tag) {
            return {
                tagName: tag, children: [], className: "", textContent: "", style: {},
                setAttribute: function (k, v) { this["attr_" + k] = v; },
                addEventListener: function () {},
                appendChild: function (c) { this.children.push(c); return c; }
            };
        }
        var document = { createElement: fakeEl, getElementById: function () { return null; } };

        var modes = [
            { mode_index: 6, frequency_cm1: -797.72, imaginary: true,
              ir_intensity: null, vectors: [[1, 0, 0], [0, 1, 0]] }
        ];
        ns.state.jobId = "job1";
        ns.state.entryId = "e1";
        ns.state.data = {
            available: true, reason: null, threshold_cm1: -50.0,
            threshold_source: "default", modes: modes, atom_count: 2,
            geometry_product_id: "batch_item_002"
        };
        ns.state.selectedModeIndex = 6;
        window.ACPStructureViewer = {
            state: {
                payload: {
                    entries: [{ id: "e1", source: { product_id: "batch_item_001" } }]
                },
                displayedCoords: [[0, 0, 0], [1, 1, 1]],
                displayedSymbols: ["C", "O"],
                displayedEntryId: "e1"
            }
        };

        var container = fakeEl("div");
        ns.renderFrequencyInspector(container);
        var rendered = JSON.stringify(container);
        if (rendered.indexOf("sv-vib-controls") >= 0) {
            console.error("FAIL: controls must be suppressed on mismatch");
            process.exit(1);
        }
        var lastChild = container.children[container.children.length - 1];
        if (lastChild.textContent !== "模式与当前几何不匹配") {
            console.error("FAIL: mismatch reason missing: " + lastChild.textContent);
            process.exit(1);
        }
        // TS evidence still rendered (frequency info remains viewable)
        if (rendered.indexOf("sv-vib-ts-hint") < 0) {
            console.error("FAIL: TS evidence block missing");
            process.exit(1);
        }
        // animation refused
        ns._viewerImpl = function () { return { getView: function () { return {}; } }; };
        ns.playAnimation();
        if (ns.isAnimationActive()) {
            console.error("FAIL: animation must be disabled on mismatch");
            process.exit(1);
        }
        console.log("PASS");
    """).replace("VIB_PATH", json.dumps(str(_VIB_JS)))

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"Node mismatch-disable test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


# ---------------------------------------------------------------------------
# F2 post-review fix: catalog vibrations wiring reaches the frontend gate
# ---------------------------------------------------------------------------


def test_f2_vibrations_fetch_gate_contract() -> None:
    """F2 MAJOR-1: the frontend fetch-gate contract is
    `available !== false -> fetch` (backend authoritative). Once the catalog
    emits available=true, the gate must actually call loadVibrations; the
    genuinely-absent case keeps the local 无振动数据 text with NO fetch."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    sv_js = _SV_JS_PATH.read_text(encoding="utf-8")
    assert "entry.vibrations && entry.vibrations.available !== false" in sv_js

    script = textwrap.dedent("""\
        var window = { fetch: null };
        var AbortController = class { constructor() { this.signal = null; } abort() {} };

        // minimal DOM stub (fakeEl pattern from the Wave-5 node tests)
        function fakeEl(tag) {
          return {
            tag: tag, children: [], className: "", textContent: "",
            attrs: {}, style: {},
            setAttribute: function (k, v) { this.attrs[k] = v; },
            getAttribute: function (k) { return this.attrs[k]; },
            addEventListener: function () {},
            appendChild: function (c) { this.children.push(c); return c; },
          };
        }
        var document = {
          createElement: fakeEl,
          getElementById: function (id) {
            if (id === "sv-inspector-body") return fakeEl("div");
            if (id === "sv-inspector-header") return fakeEl("div");
            return null;  // playback bar etc. -> guarded early-returns
          },
        };

        var vibCalls = [];
        window.ACPVibrationViewer = {
          loadVibrations: function (jobId, entryId, opts) {
            vibCalls.push([jobId, entryId, opts]);
          },
        };

        require(SV_PATH);
        var ns = window.ACPStructureViewer;
        ns.state.jobId = "job-f2";
        ns.state.selectedEntryId = "batch_item_001";

        function entryWith(available, endpoint) {
          return {
            id: "batch_item_001", group_id: "batch_items", label: "item",
            role: "ts", status: "completed",
            geometry: { endpoint: "/geo", format: "xyz" },
            energy: null, badges: [],
            source: { kind: "formal_result", geometry_ref: "x" },
            vibrations: { available: available, endpoint: endpoint || null },
          };
        }

        ns.state.payload = {
          entries: [entryWith(true, "/vib/1")], default_entry_id: "batch_item_001",
          groups: [], warnings: [],
        };
        ns.renderInspector();
        if (vibCalls.length !== 1 || vibCalls[0][1] !== "batch_item_001" ||
            vibCalls[0][2].endpoint !== "/vib/1") {
          console.error("FAIL: available=true must fetch through the gate");
          process.exit(1);
        }

        vibCalls.length = 0;
        ns.state.payload.entries = [entryWith(false, null)];
        ns.renderInspector();
        if (vibCalls.length !== 0) {
          console.error("FAIL: available=false must NOT fetch");
          process.exit(1);
        }
        console.log("PASS");
    """).replace("SV_PATH", json.dumps(str(_SV_JS_PATH)))

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"Node vibrations-gate test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


# ---------------------------------------------------------------------------
# Wire fix: summary bar vibration + measure buttons, measure drawer live data
# ---------------------------------------------------------------------------


def test_summary_bar_vibration_and_measure_buttons() -> None:
    """Summary bar has vibration dock entries + measure drawer button."""
    sv = _SV_JS_PATH.read_text(encoding="utf-8")

    assert 'openDrawer("measure"' in sv, "measure drawer openDrawer call missing"
    assert "imaginary_count" in sv, "imaginary_count reference missing"
    assert "VIB_IMAG_BTN" in sv or "VIB_IMAG_SUMMARY" in sv, "imaginary label key missing"
    assert "VIB_BTN" in sv, "vibration button label key missing"
    assert "MEASURE_BTN" in sv, "measure button label key missing"
    assert "toggleVibrationDock" in sv, "dock toggle missing"

    summary_fn = sv.split("function renderStructureViewer()", 1)[1]
    summary_fn = summary_fn[: summary_fn.index("function _renderStripItem(")]
    assert "toggleVibrationDock" in summary_fn, "vibration dock toggle not in summary bar"
    assert 'openDrawer("measure"' in summary_fn, "measure button not in summary bar"
    assert "imaginary_count" in summary_fn, "imaginary_count not in summary bar"
    assert "entry.vibrations.available !== false" in summary_fn, (
        "vibration availability gate missing from summary bar"
    )


def test_vibration_summary_bar_node_logic() -> None:
    """Node logic: vibration button shows imaginary badge when count > 0."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    script = textwrap.dedent("""\
        var window = { fetch: null };
        var AbortController = class { constructor() { this.signal = null; } abort() {} };
        function fakeEl(tag) {
          return {
            tag: tag, children: [], className: "", textContent: "",
            attrs: {}, style: {},
            classList: {
              _classes: [],
              toggle: function (cls, on) {
                var idx = this._classes.indexOf(cls);
                if (on && idx < 0) this._classes.push(cls);
                if (!on && idx >= 0) this._classes.splice(idx, 1);
              },
              contains: function (cls) { return this._classes.indexOf(cls) >= 0; },
            },
            setAttribute: function (k, v) { this.attrs[k] = v; },
            getAttribute: function (k) { return this.attrs[k]; },
            addEventListener: function () {},
            appendChild: function (c) { this.children.push(c); return c; },
            querySelectorAll: function () { return []; },
          };
        }
        var _summaryBar = fakeEl("div");
        var _bottomStrip = fakeEl("div");
        var _layout = fakeEl("div");
        _layout.addEventListener = function () {};
        var document = {
          createElement: fakeEl,
          getElementById: function (id) {
            if (id === "sv-summary-bar") return _summaryBar;
            if (id === "sv-bottom-strip") return _bottomStrip;
            if (id === "sv-layout") return _layout;
            return null;
          },
        };
        require(SV_PATH);
        var ns = window.ACPStructureViewer;

        ns.state.payload = {
          entries: [{
            id: "e1", group_id: "g1", label: "TS1",
            status: "completed", badges: [],
            energy: null, source: { kind: "formal_result" },
            vibrations: { available: true, imaginary_count: 2, endpoint: "/vib" },
            geometry: { endpoint: "/geo", format: "xyz" },
          }],
          default_entry_id: "e1",
          groups: [{ id: "g1" }],
          warnings: [],
        };
        ns.state.selectedEntryId = "e1";
        ns.state.jobId = "job-vib";
        ns.renderStructureViewer();

        var bar = _summaryBar;
        var btnTexts = bar.children.map(function(c) { return c.textContent; }).join(" | ");
        if (btnTexts.indexOf("\\u865a\\u9891") < 0) {
          console.error("FAIL: imaginary badge missing, got: " + btnTexts);
          process.exit(1);
        }
        if (btnTexts.indexOf("\\u6d4b\\u91cf") < 0) {
          console.error("FAIL: measure button missing, got: " + btnTexts);
          process.exit(1);
        }

        ns.state.payload.entries[0].vibrations.imaginary_count = 0;
        _summaryBar.children.length = 0;
        ns.renderStructureViewer();
        var bar2 = _summaryBar;
        var btnTexts2 = bar2.children.map(function(c) { return c.textContent; }).join(" | ");
        if (btnTexts2.indexOf("\\u632f\\u52a8\\u6a21\\u5f0f") < 0) {
          console.error("FAIL: plain vib label missing when count=0, got: " + btnTexts2);
          process.exit(1);
        }
        console.log("PASS");
    """).replace("SV_PATH", json.dumps(str(_SV_JS_PATH)))

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"Vibration summary bar test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_vibration_viewer_contains_tsmode_editor_open_call() -> None:
    """vibration_viewer.js references ACPTsmodeEditor.open for quick-create."""
    vib = _VIB_JS.read_text(encoding="utf-8")
    assert "ACPTsmodeEditor" in vib, (
        "vibration_viewer.js must reference ACPTsmodeEditor for quick-create"
    )
    assert "ACPTsmodeEditor.open" in vib, (
        "vibration_viewer.js must call ACPTsmodeEditor.open"
    )


def test_vibration_viewer_has_ts_mode_create_string() -> None:
    """vibration_viewer.js contains the TS mode create button text."""
    vib = _VIB_JS.read_text(encoding="utf-8")
    assert "TS_MODE_CREATE" in vib, "TS_MODE_CREATE string constant missing"


_VIB_I18N_KEY_RE = re.compile(r'"(structure\.vib\.[^"]+)":')


def _extract_vib_keys(html: str, block_re: re.Pattern[str]) -> set[str]:  # type: ignore[type-arg]
    """Extract structure.vib.* i18n keys from a single locale block."""
    m = block_re.search(html)
    if not m:
        return set()
    return set(_VIB_I18N_KEY_RE.findall(m.group(1)))


_VIB_DOM_STUB = """\
    function fakeEl(tag) {
        return {
            tagName: tag, children: [], className: "", textContent: "", style: {},
            setAttribute: function (k, v) { this["attr_" + k] = v; },
            addEventListener: function () {},
            appendChild: function (c) { this.children.push(c); return c; }
        };
    }
    var document = { createElement: fakeEl, getElementById: function () { return null; } };
"""


def _fn_body(source: str, header: str) -> str:
    """Return the body of a function whose definition starts with `header`."""
    assert header in source, f"function not found: {header}"
    return source.split(header, 1)[1].split("\nfunction ", 1)[0].split("\nasync function ", 1)[0]
