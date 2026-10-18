"""Frontend sync page tests — energy area.

Split from tests/test_frontend_sync.py (T29, 2026-10-18). Every test keeps
its original name and body; the old -> new mapping is
tests/test_frontend_sync.py::<name> -> tests/test_frontend_sync_energy.py
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
    _EN_BLOCK_RE,
    _SV_JS_PATH,
    _ZH_BLOCK_RE,
    FRONTEND_CSS_DIR,
    FRONTEND_JS_DIR,
    REPO_ROOT,
    _div_span,
    _extract_energy_keys,
    read_workbench_content,
    read_workbench_css,
)


def test_optimization_geometry_loader_frame_endpoint_first() -> None:
    """Regression guard for the optimization structure viewer (2026-09 report).

    geometry_ref is stored relative to the trajectory file
    (e.g. WORK/03_OPT/optimization_trajectory.json), so the job-root files
    endpoint cannot resolve it and returns 404.  The optimization view must
    therefore call /optimization/frame/{frame_index} FIRST, and every source
    must live in its own try/catch so a failed request never blocks the
    remaining fallbacks (only AbortError is rethrown).
    """
    html = read_workbench_content()

    body = html.split("function energyGraphLoadFrameGeometry(node)", 1)[1]
    body = body.split("\nasync function ", 1)[0]

    frame_pos = body.find('"/optimization/frame/"')
    files_pos = body.find('"/files/"')
    assert frame_pos != -1 and files_pos != -1
    assert frame_pos < files_pos

    # Four sources, each in an independent try/catch that only rethrows
    # AbortError — a 404 in one can never skip the rest.
    assert body.count('if (e && e.name === "AbortError") throw e;') >= 4


def test_energy_chart_axes_cannot_scroll_out_of_viewport() -> None:
    """Regression guard for docs/archive/2026-10-16/history/ACP_Energy_Graph_Axis_Rendering_Issue_Report.md.

    The axis band lives at the bottom of the fixed 1040x520 viewBox.  The
    layout contract must guarantee the SVG always fits the chart viewport so
    the axes stay visible: no vertical scrolling, no min-height forcing
    overflow, and no full-replacement redraw leaving the axes below the fold.
    """
    css = read_workbench_css()
    html = read_workbench_content()

    # Energy chart: viewport must never scroll; the SVG is fully responsive
    # (min-width: 0) so the axes can never be pushed out of the card — the
    # contract is enforced by layout, not by scrollbars.
    assert ".energy-chart-scroll {\n  min-width: 0;\n  min-height: 0;\n  overflow: hidden;" in css
    assert ".energy-chart-scroll { min-height: 0; overflow: auto;" not in css
    svg_rule = (
        ".energy-chart-svg {\n  display: block;\n  flex: 1 1 auto;\n  width: 100%;\n"
        "  min-width: 0;\n  min-height: 0;\n  height: 100%;\n}"
    )
    assert svg_rule in css
    assert (
        ".energy-chart-svg { display: block; width: 100%; min-width: 640px; min-height: 360px; height: 100%; }"
        not in css
    )

    # Optimization chart: same contract — the SVG is measured from the live
    # container (ResizeObserver), so no fixed pixel width can force clipping.
    assert (
        ".optimization-chart-svg { display: block; width: 100%; min-width: 0; min-height: 0; height: 100%; cursor: grab; }"
        in css
    )
    assert "min-width: 620px" not in css
    assert "min-width: 620px; height: 238px;" not in css
    assert ".optimization-chart-scroll { flex: 1 1 0; min-height: 0; overflow: hidden;" in css
    assert ".optimization-chart-card { display: flex; flex-direction: column;" in css

    # Axes are still generated and appended inside the SVG viewBox.
    assert "function energyGraphAxesMarkup(xDom, yDom, geom" in html
    assert "svg += energyGraphAxesMarkup(xDom, yDom, geom" in html


def test_optimization_chart_single_view_switching_contract() -> None:
    """Contract for the reworked optimization viewer (2026-09 report).

    One chart container with four mutually exclusive views, geometry measured
    from the live container, zoom/pan/box/reset interactions wired for the
    optimization branch, and item_id-locked polling for batch trajectories.
    """
    html = read_workbench_content()

    view_ids = (
        'OPTIMIZATION_VIEW_IDS = ["energy", "force", "energy_derivative", "force_derivative"]'
    )
    assert view_ids in html
    assert 'data-optimization-view="' in html
    assert 'data-optimization-scroll="main"' in html
    assert 'data-optimization-scroll="energy"' not in html
    assert 'data-optimization-scroll="gradient"' not in html
    assert "W = 820, H = 238" not in html

    bind = html.split("function optimizationGraphBind(root)", 1)[1].split("\nfunction ", 1)[0]
    assert "ResizeObserver" in bind
    assert "optimizationGraphBindChart(root)" in bind
    chart = html.split("function optimizationGraphBindChart(root)", 1)[1].split("\nfunction ", 1)[0]
    for event in ('"wheel"', '"dblclick"', '"pointerdown"', '"pointermove"', '"pointerup"'):
        assert event in chart

    # Batch jobs share one work dir: polling must lock the item the backend
    # resolved instead of re-picking the newest trajectory every time.
    assert "function optimizationJobItemId(job)" in html
    assert '"?item_id=" + encodeURIComponent(lockedItemId)' in html

    # The force view draws displacement; derivative views use backend series.
    assert '"rms_displacement", "max_displacement"' in html
    assert '"rms_gradient_delta", "max_gradient_delta"' in html


def test_optimization_status_panel_dual_mode_contract() -> None:
    """Dual-mode panel contract (2026-09): explicit user pick → per-cycle
    convergence criteria; otherwise → task status card.

    ``selectedNodeId`` alone cannot carry the "user picked a cycle" semantic
    because the poller auto-writes the latest cycle into it, so the state
    machine lives in ``energyGraphState.selectionOrigin``
    (none | user | follow).  Only ``user`` activates the convergence mode.
    Without an explicit pick the panel must never render the misleading
    four-row "no data" convergence block — it shows the task status instead.
    """
    html = read_workbench_content()

    # 1. State machine: selectionOrigin field + selectJob reset.
    assert 'selectionOrigin: "none",' in html
    select_job = html.split("async function selectJob(jobId)", 1)[1].split("\nasync function ", 1)[
        0
    ]
    assert 'energyGraphState.selectionOrigin = "none";' in select_job

    # 2. Panel entry: one pure model decides the mode; the old last_cycle
    #    reader is gone and the render path never touches metadata.last_cycle.
    assert "function optimizationStatusPanelModel(data, job)" in html
    assert "function optimizationStatusPanelMarkup(data, job)" in html
    status_panel = html.split("function optimizationStatusPanelMarkup(data, job)", 1)[1].split(
        "\nfunction ", 1
    )[0]
    assert "lastSelectedJob" not in status_panel
    assert "optimizationConvergencePanelMarkup" not in html
    model = html.split("function optimizationStatusPanelModel(data, job)", 1)[1].split(
        "\nfunction ", 1
    )[0]
    assert "optimizationExplicitSelectedNode(data)" in model
    assert "buildSelectedCycleConvergenceModel(data, selected)" in model
    assert "buildTaskStatusModel(data, job)" in model
    criteria = html.split("function energyOptCriteriaRows(data, cycleMetadata)", 1)[1].split(
        "\n}\n", 1
    )[0]
    assert "last_cycle" not in criteria
    optimization_workspace = html.split(
        "function optimizationWorkspaceMarkup(data, job)", 1
    )[1].split("\nfunction ", 1)[0]
    assert "lastSelectedJob" not in optimization_workspace
    energy_workspace = html.split("function energyGraphWorkspaceMarkup(data, job)", 1)[1].split(
        "\nfunction ", 1
    )[0]
    assert "lastSelectedJob" not in energy_workspace
    assert (
        "metadata.last_cycle"
        not in html.split("function optimizationWorkspaceMarkup(data, job)", 1)[1]
    )

    # 3. Explicit-pick detection requires selectionOrigin === "user" and must
    #    not fall back to another node (inspector/panel node consistency).
    explicit = html.split("function optimizationExplicitSelectedNode(data)", 1)[1].split(
        "\nfunction ", 1
    )[0]
    assert 'energyGraphState.selectionOrigin !== "user"' in explicit
    assert "nodes[0]" not in explicit

    # 4. Selected-cycle mode reads the picked node's own metadata, so a
    #    historical complete cycle shows its numbers even when the last cycle
    #    is incomplete.
    selected_model = html.split("function buildSelectedCycleConvergenceModel(data, node)", 1)[
        1
    ].split("\nfunction ", 1)[0]
    assert "energyOptCriteriaRows(data, nodeMeta)" in selected_model
    assert 't("energy.conv.cycle_title"' in html

    # 5. Missing semantics are split: absent measurement → value_missing;
    #    absent threshold → threshold_missing.  The unified "missing" key is
    #    retired from both locales.
    assert 't("energy.conv.value_missing")' in criteria
    assert 't("energy.conv.threshold_missing")' in criteria
    assert "energy.conv.missing" not in html

    # 6. Task-status mode: status prefers the job object, falls back to
    #    data.status; running/paused carry the current cycle, completed may
    #    add the converged note, failed/cancelled keep the last trajectory
    #    cycle, and a failed job with an incomplete last cycle adds the
    #    incomplete-cycle note.
    task_model = html.split("function buildTaskStatusModel(data, job)", 1)[1].split(
        "\nfunction ", 1
    )[0]
    assert "(job && job.status) || (data && data.status)" in task_model
    assert (
        'rawStatus === "queued" || rawStatus === "starting" || rawStatus === "pending"'
        in task_model
    )
    assert 'rawStatus === "running" || rawStatus === "partial"' in task_model
    assert 'rawStatus === "paused"' in task_model
    assert 'rawStatus === "cancelling"' in task_model
    assert 'rawStatus === "waiting_review"' in task_model
    assert 'kind === "running" || kind === "paused"' in task_model
    assert 't("energy.task.current_cycle", { n: currentCycle })' in task_model
    assert 'kind === "completed" && data && data.complete' in task_model
    assert 't("energy.task.converged")' in task_model
    assert '(kind === "failed" || kind === "cancelled") && lastCycle != null' in task_model
    assert 't("energy.task.last_cycle", { n: lastCycle })' in task_model
    assert 'kind === "failed" && optimizationLastCycleIncomplete(data)' in task_model
    assert 't("energy.task.incomplete_cycle")' in task_model

    # 7. Transitions: chart click / prev-next / keyboard → user (+follow off);
    #    "back to latest" → follow; refresh keeps a surviving user pick and
    #    falls back to follow when the picked node disappears.
    # The selectionOrigin logic now lives in energyGraphSetSelection (unified writer).
    set_selection = html.split("function energyGraphSetSelection(opts)", 1)[
        1
    ].split("\nfunction ", 1)[0]
    assert (
        'energyGraphState.selectionOrigin = origin === "follow" ? "follow" : "user";'
        in set_selection
    )
    # energyGraphSelectFrame delegates to energyGraphSetSelection
    select_frame = html.split("function energyGraphSelectFrame(frameIndex, origin, nodeId)", 1)[
        1
    ].split("\nfunction ", 1)[0]
    assert "energyGraphSetSelection(" in select_frame
    assert (
        'energyGraphSelectFrame(nodes[nodes.length - 1].frame_index, "follow", nodes[nodes.length - 1].id)'
        in html
    )
    opt_bind = html.split("function optimizationGraphBind(root)", 1)[1].split("\nfunction ", 1)[0]
    assert "energyGraphState.liveFollow = false;" in opt_bind
    nav = html.split("function energyGraphBindInspectorButtons(root)", 1)[1].split(
        "\nfunction ", 1
    )[0]
    assert nav.count("energyGraphState.liveFollow = false;") >= 2
    keydown = html.split('if (e.key === "ArrowLeft")', 1)[1].split("switch (e.key)", 1)[0]
    assert keydown.count("energyGraphState.liveFollow = false;") >= 2
    refresh = html.split('if (String(data.view_type || "") === "optimization")', 1)[1].split(
        "\n    }", 1
    )[0]
    assert 'energyGraphState.selectionOrigin = "follow";' in refresh
    assert "energyGraphState.liveFollow = true;" in refresh

    # 8. Bilingual copy: every new key exists in both locales.
    new_keys = (
        "energy.conv.cycle_title",
        "energy.conv.value_missing",
        "energy.conv.threshold_missing",
        "energy.task.title",
        "energy.task.current_cycle",
        "energy.task.converged",
        "energy.task.last_cycle",
        "energy.task.incomplete_cycle",
        "energy.task.status.queued",
        "energy.task.status.running",
        "energy.task.status.paused",
        "energy.task.status.cancelling",
        "energy.task.status.waiting_review",
        "energy.task.status.completed",
        "energy.task.status.failed",
        "energy.task.status.cancelled",
    )
    zh_keys = _extract_energy_keys(html, _ZH_BLOCK_RE)
    en_keys = _extract_energy_keys(html, _EN_BLOCK_RE)
    for key in new_keys:
        assert key in zh_keys, f"{key} missing from zh-CN"
        assert key in en_keys, f"{key} missing from en-US"


def test_energy_i18n_keys_complete_across_locales() -> None:
    """Dual-locale completeness: every energy.* / tab.energy.* key in zh-CN
    must also exist in en-US and vice-versa.

    A future edit that adds an energy key to only one locale will fail here —
    that is its purpose.
    """
    html = read_workbench_content()

    zh_keys = _extract_energy_keys(html, _ZH_BLOCK_RE)
    en_keys = _extract_energy_keys(html, _EN_BLOCK_RE)

    assert zh_keys, "No energy.* / tab.energy.* keys found in zh-CN block"
    assert en_keys, "No energy.* / tab.energy.* keys found in en-US block"

    only_zh = zh_keys - en_keys
    only_en = en_keys - zh_keys
    assert not only_zh, f"Keys in zh-CN but missing from en-US: {sorted(only_zh)}"
    assert not only_en, f"Keys in en-US but missing from zh-CN: {sorted(only_en)}"


def test_frame_candidate_save_uses_terminal_status_predicate() -> None:
    """Red-first contract: the energy inspector save/role-picker gate must use a
    terminal-status predicate (completed OR failed OR cancelled) instead of a
    completed-only variable.
    """
    html = read_workbench_content()

    assert "const jobTerminal = jobStatus ===" in html, (
        "jobTerminal must be defined checking all three terminal statuses"
    )
    assert "&& jobTerminal" in html, "canEditRole must include jobTerminal gate"


def test_frame_candidate_role_picker_gate_uses_terminal_predicate() -> None:
    """Red-first contract: the role-picker visibility must NOT be gated by the
    completed-only ``jobCompleted`` variable.

    ``rolePickerHtml = (!isPES && jobCompleted)`` only shows the TS/INT
    picker when the job status is exactly "completed".  A terminal-status
    predicate (completed / failed / cancelled) must replace it.
    """
    html = read_workbench_content()

    # The role-picker gate must use a terminal predicate, not the
    # completed-only variable.
    assert "(!isPES && jobCompleted)" not in html, (
        "Role-picker gate must use terminal-status predicate, not completed-only jobCompleted"
    )


def test_frame_candidate_disabled_active_job_branch_retained() -> None:
    """Regression guard: the disabled save-candidate button for active
    (non-terminal) jobs must remain with an explanatory badge.

    This assertion must PASS against the current source — it guards the
    existing disabled branch so a future refactor cannot silently drop it.
    """
    html = read_workbench_content()

    assert "energy.inspector.save_after_complete" in html
    assert (
        'save-candidate" data-energy-frame-id="\' + escapeHtml(frameIdStr) + \'" disabled' in html
    )


def test_frame_candidate_post_body_includes_item_id() -> None:
    """Red-first contract: the frame-candidate POST body must propagate
    ``item_id`` from the graph metadata so the backend can associate the
    saved candidate with the correct batch item.

    The current POST body contains ``view_type``, ``frame_index``,
    ``role``, and ``expected_revision`` — but no ``item_id``.
    """
    html = read_workbench_content()

    # Extract the role-picker click handler that builds the POST body.
    assert "[data-energy-candidate-role]" in html, "sanity: role-picker button selector exists"
    handler = html.split("[data-energy-candidate-role]", 1)[1]
    handler = handler.split("\nfunction ", 1)[0]

    # The POST body construction must include item_id.
    assert "item_id" in handler, (
        "Frame-candidate POST body must include item_id from graph metadata"
    )


def test_energy_workspace_peer_layout_contract() -> None:
    """Structural contracts for the energy viewer after the P0 workspace split.

    The energy workspace is a peer canvas view (sibling of #sv-layout), using a
    70/30 chart/inspector grid with a focus mode and a narrow-screen fallback.
    Candidate band / card / hit-layer contracts from the denoise refactor stay.
    """
    html = read_workbench_content()

    assert "energy-candidate-band" in html, "Band container class missing"
    assert 'data-energy-candidate-jump="' in html, "Chip jump attribute missing"
    assert 'data-energy-candidate-expand="' in html, "Overflow expand attribute missing"
    assert "energy-band-chip-hit" in html, "Chip hit target class missing"

    assert "energy-candidate-card" in html, "Card container class missing"
    assert 'data-energy-card-state="' in html, "Card state attribute missing"
    assert "energy-card-type-btn" in html, "Card type button class missing"
    assert 'data-energy-action="card-cancel"' in html, "Card cancel action missing"

    css = read_workbench_css()
    assert "energy-point-hit" in css or "energy-point-hit" in html, "Energy point hit class missing"
    assert "energy-point-group" in css or "energy-point-group" in html, "Energy point group class missing"
    assert "optimization-point-hit" in css or "optimization-point-hit" in html, "Optimization point hit class missing"

    assert ".energy-marker-max" not in css, ".energy-marker-max CSS still present"
    assert "energy-role-row" not in html, ".energy-role-row still present"
    assert 'type !== "maximum"' in html or "type !== 'maximum'" in html, (
        "Defensive maximum-type filter missing"
    )

    assert "function energyChartBuildSvg(cfg)" in html, "Unified chart core missing"
    assert "function energyChartNiceTicks(" in html, "Nice ticks function missing"

    # Peer layout (UX spec v2.0 P0): the energy workspace is NOT a child of
    # #sv-layout; it renders after the structure workspace closes.
    _, layout_end = _div_span(html, 'id="sv-layout"')
    assert html.index('id="viewer-energy"') > layout_end, (
        "#viewer-energy must appear after the closing #sv-layout tag (peer view)"
    )

    assert "grid-template-columns: minmax(0, 7fr) minmax(0, 3fr)" in css, (
        "Energy workspace 70/30 chart/inspector split missing"
    )
    assert "energy-focus-mode" in css or "energy-focus-mode" in html, "Energy focus-mode class missing"

    energy_media = css.split("@media (max-width: 1100px)", 1)[1].split("}", 1)[0]
    assert ".energy-workspace-grid" in energy_media, (
        "Energy workspace must collapse to one column at <=1100px"
    )

    assert "structureFraming" in html, "Structure framing state missing"
    assert "scheduleViewerFraming(" in html, "Shared framing scheduler missing"
    assert "data-expandable" in html, "Expandable field attribute missing"


def test_energy_viewer_same_frame_guard_allows_relayout_reframe() -> None:
    html = read_workbench_content()
    marker = "function energyGraphLoadFrameGeometry("
    assert html.count(marker) == 1, "Energy geometry loader must be defined once"
    body = html.split(marker, 1)[1].split("\nasync function ", 1)[0].split("\nfunction ", 1)[0]

    same_frame_start = body.find("loadedFrameIndex")
    assert same_frame_start >= 0, "Energy same-frame guard missing"
    same_frame_return = body.find("return;", same_frame_start)
    assert same_frame_return >= 0, "Energy same-frame guard missing early return"
    same_frame_region = body[same_frame_start:same_frame_return]
    assert "lastW" in same_frame_region and "lastH" in same_frame_region, (
        "Energy same-frame guard must compare both container dimensions"
    )
    schedule_position = body.find("scheduleViewerFraming(", same_frame_start)
    assert schedule_position >= 0, "Energy same-frame guard must schedule reframing"
    assert schedule_position < same_frame_return, (
        "Energy same-frame guard must reframe before returning"
    )
    assert "structureFramingPending" not in html, "Legacy framing pending state remains"
    assert "structureFraming" in html, "Energy framing state missing"


def test_energy_structure_viewer_stays_within_visible_panel() -> None:
    """Layout regression: the energy viewer canvas must never exceed its panel.

    aspect-ratio: 16/10 + min-height: 240px used to transfer a 384px
    (240 * 1.6) minimum inline size through the grid item automatic minimum
    into the inspector panel's implicit auto column, overflowing the narrower
    right column; the panel's overflow: hidden then clipped the canvas on the
    right (measured 384px canvas in a 348px panel, +18px apparent center
    shift). Checking only "canvas equals its own container" is insufficient —
    the container itself must be bounded by the visible panel.
    """
    css = read_workbench_css()

    viewer_marker = ".energy-structure-viewer {"
    assert css.count(viewer_marker) == 1, "Viewer base rule must be defined once"
    viewer_rule = css.split(viewer_marker, 1)[1].split("}", 1)[0]
    assert "width: 100%" in viewer_rule, "Viewer must fill its column, not self-size"
    assert "min-width: 0" in viewer_rule, (
        "Viewer must defeat the aspect-ratio-transferred minimum width"
    )
    assert "aspect-ratio" in viewer_rule and "min-height" in viewer_rule, (
        "Viewer ratio and height clamps must remain"
    )

    panel_marker = "\n.energy-inspector-panel {"
    panel_rule_bodies = [segment.split("}", 1)[0] for segment in css.split(panel_marker)[1:]]
    rows_rules = [r for r in panel_rule_bodies if "grid-template-rows" in r]
    assert rows_rules, "Inspector panel rows-grid definition missing"
    assert any("grid-template-columns: minmax(0, 1fr)" in r for r in rows_rules), (
        "Inspector rows-grid must bound its implicit column to the panel width"
    )


# ---------------------------------------------------------------------------
# Todo 18: energy graph -> structure viewer one-way push
# ---------------------------------------------------------------------------


def test_energy_selectframe_pushes_to_structure_viewer() -> None:
    """Contract: energyGraphSelectFrame delegates to energyGraphSetSelection which pushes to structure viewer."""
    html = read_workbench_content()

    select_frame = html.split("function energyGraphSelectFrame(frameIndex, origin, nodeId)", 1)[1]
    select_frame = select_frame.split("\nfunction ", 1)[0]
    assert "energyGraphSetSelection(" in select_frame, (
        "energyGraphSelectFrame must delegate to energyGraphSetSelection"
    )

    set_selection = html.split("function energyGraphSetSelection(opts)", 1)[1]
    set_selection = set_selection.split("\nfunction ", 1)[0]
    assert "onEnergyNodeSelected" in set_selection, (
        "energyGraphSetSelection must push selection to structure viewer"
    )
    assert "energyGraphState.jobId" in set_selection, "Push must pass jobId from energyGraphState"


def test_ensemble_total_g_summary_bar_and_drawer_source_contract() -> None:
    """Source contract: ensemble total G chip + drawer row use the frozen
    payload.ensemble_thermo key behind strict null/finite guards, rendered
    via the 2-arg _t("structure.ensemble_total_g", STR.ENSEMBLE_TOTAL_G) form."""
    js = (FRONTEND_JS_DIR / "structure_viewer.js").read_text(encoding="utf-8")
    html = read_workbench_content()
    css = (FRONTEND_CSS_DIR / "structure_viewer.css").read_text(encoding="utf-8")

    assert js.count('className = "sv-summary-ensemble"') == 1
    assert ".sv-summary-ensemble" in css

    t_call = '_t("structure.ensemble_total_g", STR.ENSEMBLE_TOTAL_G)'
    assert js.count(t_call) == 2
    assert "ENSEMBLE_TOTAL_G:" in js

    assert '"structure.ensemble_total_g": "系综总自由能"' in html
    assert '"structure.ensemble_total_g": "Ensemble total G"' in html

    assert "payload.ensemble_thermo" in js or "payload && payload.ensemble_thermo" in js
    assert 'typeof ensG === "number" && isFinite(ensG)' in js

    render_body = js.split("function renderStructureViewer()", 1)[1]
    render_body = render_body[: render_body.index("function _renderStripItem(")]
    assert render_body.index("sv-summary-energy") < render_body.index("sv-summary-ensemble")

    drawer_body = js.split("function _renderSourceDrawer(", 1)[1].split("\n  function ", 1)[0]
    assert drawer_body.index('STR.WEIGHT') < drawer_body.index(t_call)
    assert "sv-summary-ensemble" not in drawer_body


def test_ensemble_total_g_summary_bar_node_logic() -> None:
    """Node logic: ensemble chip renders formatted total G after the energy
    span when payload.ensemble_thermo is present; absent/invalid -> no chip,
    no crash; drawer row carries kcal/mol + Eh + meta; payload passthrough."""
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
            removeAttribute: function (k) { delete this.attrs[k]; },
            addEventListener: function () {},
            appendChild: function (c) { this.children.push(c); return c; },
            querySelectorAll: function () { return []; },
            querySelector: function () { return null; },
          };
        }
        var _summaryBar = fakeEl("div");
        var _bottomStrip = fakeEl("div");
        var _layout = fakeEl("div");
        var _sourceDrawer = fakeEl("div");
        _layout.addEventListener = function () {};
        var document = {
          createElement: fakeEl,
          getElementById: function (id) {
            if (id === "sv-summary-bar") return _summaryBar;
            if (id === "sv-bottom-strip") return _bottomStrip;
            if (id === "sv-layout") return _layout;
            if (id === "sv-drawer-source") return _sourceDrawer;
            return null;
          },
        };
        require(SV_PATH);
        var ns = window.ACPStructureViewer;

        var LABEL = "\\u7cfb\\u7efc\\u603b\\u81ea\\u7531\\u80fd";
        var ENSEMBLE = {
          total_gibbs_kcal_mol: -97248.3477,
          total_gibbs_hartree: -155.123456,
          rank1_weight: 0.9832,
          t_s_mix_kcal_per_mol: 0.421,
          population_coverage: 0.997,
          temperature_k: 298.15,
          method: "GFN2-xTB",
        };

        function barClasses() {
          return _summaryBar.children.map(function (c) { return c.className; });
        }
        function barTextByClass(cls) {
          for (var i = 0; i < _summaryBar.children.length; i++) {
            if (_summaryBar.children[i].className === cls) return _summaryBar.children[i].textContent;
          }
          return null;
        }
        function renderWith(ensemble, energy) {
          ns.state.payload = {
            entries: [{
              id: "e1", group_id: "g1", label: "conf_0001",
              status: "completed", badges: [],
              energy: energy, source: { kind: "formal_result" },
              geometry: { endpoint: "/geo", format: "xyz" },
            }],
            default_entry_id: "e1",
            groups: [{ id: "g1" }],
            warnings: [],
            ensemble_thermo: ensemble,
          };
          ns.state.selectedEntryId = "e1";
          ns.state.jobId = "job-ens";
          _summaryBar.children.length = 0;
          _sourceDrawer.children.length = 0;
          ns.renderStructureViewer();
        }

        var energy = { value: -154.974579, unit: "hartree" };
        renderWith(ENSEMBLE, energy);
        var classes = barClasses();
        if (classes.indexOf("sv-summary-ensemble") < 0) {
          console.error("FAIL: ensemble chip missing, got: " + classes.join(","));
          process.exit(1);
        }
        if (classes.indexOf("sv-summary-energy") > classes.indexOf("sv-summary-ensemble")) {
          console.error("FAIL: ensemble chip must follow energy span");
          process.exit(1);
        }
        var expectChip = LABEL + " -97248.3477 kcal/mol";
        if (barTextByClass("sv-summary-ensemble") !== expectChip) {
          console.error("FAIL: chip text, got: " + barTextByClass("sv-summary-ensemble"));
          process.exit(1);
        }

        renderWith(null, energy);
        if (barClasses().indexOf("sv-summary-ensemble") >= 0) {
          console.error("FAIL: chip rendered without ensemble_thermo");
          process.exit(1);
        }

        renderWith({ rank1_weight: 0.9 }, energy);
        if (barClasses().indexOf("sv-summary-ensemble") >= 0) {
          console.error("FAIL: chip rendered with missing total_gibbs_kcal_mol");
          process.exit(1);
        }

        renderWith({ total_gibbs_kcal_mol: "99" }, energy);
        if (barClasses().indexOf("sv-summary-ensemble") >= 0) {
          console.error("FAIL: chip rendered with non-numeric total_gibbs_kcal_mol");
          process.exit(1);
        }

        renderWith(ENSEMBLE, null);
        if (barTextByClass("sv-summary-ensemble") !== expectChip) {
          console.error("FAIL: chip must survive entry.energy === null");
          process.exit(1);
        }

        renderWith(ENSEMBLE, energy);
        ns.openDrawer("source", null);
        var body = _sourceDrawer.children[_sourceDrawer.children.length - 1];
        var row = null;
        for (var i = 0; i < body.children.length; i++) {
          var kid = body.children[i];
          if (kid.className === "sv-inspector-section" && kid.children[0] &&
              kid.children[0].textContent === LABEL) {
            row = kid;
            break;
          }
        }
        if (!row) {
          console.error("FAIL: drawer ensemble row missing");
          process.exit(1);
        }
        if (row.children[1].textContent !== "-97248.3477 kcal/mol (-155.123456 Eh)") {
          console.error("FAIL: drawer value, got: " + row.children[1].textContent);
          process.exit(1);
        }
        if (row.children.length < 3 ||
            row.children[2].textContent.indexOf("T\\u00b7S_mix 0.4210 kcal/mol") < 0 ||
            row.children[2].textContent.indexOf("coverage 99.7%") < 0) {
          console.error("FAIL: drawer meta line, got: " +
            (row.children[2] ? row.children[2].textContent : "<none>"));
          process.exit(1);
        }

        var data = {
          availability: "ready", revision: 3,
          entries: [], groups: [], warnings: [],
          ensemble_thermo: ENSEMBLE,
        };
        if (!ns._applyCatalogResponse(ns.state, ns.state.requestToken, data)) {
          console.error("FAIL: _applyCatalogResponse rejected current token");
          process.exit(1);
        }
        if (!ns.state.payload.ensemble_thermo ||
            ns.state.payload.ensemble_thermo.total_gibbs_kcal_mol !== -97248.3477) {
          console.error("FAIL: ensemble_thermo not preserved in state.payload");
          process.exit(1);
        }
        console.log("PASS");
    """).replace("SV_PATH", json.dumps(str(_SV_JS_PATH)))

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"Ensemble total G node test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


# --- Energy graph unified selection + annotation resolution contracts ---

def test_energy_graph_set_selection_exists() -> None:
    """energyGraphSetSelection is the unified selection writer."""
    html = read_workbench_content()
    assert "function energyGraphSetSelection(opts)" in html, "Unified selection writer missing"
    body = html.split("function energyGraphSetSelection(opts)", 1)[1].split("\nfunction ", 1)[0]
    assert "energyGraphState.selection =" in body, "Must store selection object"
    assert "energyGraphState.selectedNodeId" in body, "Must mirror to legacy selectedNodeId"
    assert "energyGraphState.selectedAnnotationId" in body, "Must mirror to legacy selectedAnnotationId"


def test_energy_graph_node_for_annotation_exists() -> None:
    """energyGraphNodeForAnnotation resolves node_id then frame_index."""
    html = read_workbench_content()
    assert "function energyGraphNodeForAnnotation(data, annotation)" in html, "Annotation resolver missing"
    body = html.split("function energyGraphNodeForAnnotation(data, annotation)", 1)[1].split("\nfunction ", 1)[0]
    assert "annotation.node_id" in body, "Must check node_id first"
    assert "annotation.frame_index" in body, "Must fallback to frame_index"


def test_energy_band_frame_i18n_key_exists() -> None:
    """energy.band.frame key exists in both locales."""
    html = read_workbench_content()
    zh_keys = _extract_energy_keys(html, _ZH_BLOCK_RE)
    en_keys = _extract_energy_keys(html, _EN_BLOCK_RE)
    assert "energy.band.frame" in zh_keys, "energy.band.frame missing from zh-CN"
    assert "energy.band.frame" in en_keys, "energy.band.frame missing from en-US"


def test_energy_annotations_consume_node_id() -> None:
    """renderTrajectoryAnnotations uses energyGraphNodeForAnnotation for resolution."""
    html = read_workbench_content()
    body = html.split("function renderTrajectoryAnnotations(opts)", 1)[1].split("\nfunction ", 1)[0]
    assert "energyGraphNodeForAnnotation" in body, "Must use energyGraphNodeForAnnotation for resolution"


# ---------------------------------------------------------------------------
# Multi-frame playback bar (#frame-controller) visibility gate
# ---------------------------------------------------------------------------


def test_frame_controller_visibility_gate_uses_live_tab_id() -> None:
    """updateFrameController() must gate #frame-controller on a LIVE tab id.

    The 2026-09-11 canvas-tab rename (3d -> structure) missed the gate
    literal inside updateFrameController(), so the condition was always
    false and any frame change (play tick / prev / next / slider) hid the
    playback bar. Locked two ways:

    (a) STATIC: the exact stale pattern getAttribute("data-tab") === "3d"
        occurs 0 times in the page, and the single gate literal inside the
        extracted function names a tab id that actually renders
        (data-tab="..." occurrences), so future renames cannot strand it.
    (b) BEHAVIORAL (Node, DOM stubs): the extracted function shows the bar
        ("flex") only on the structure tab with >1 frames; "none" on the
        energy/path/wavefunction tabs, for a single-frame document, or when
        no active canvas tab exists.
    """
    html = read_workbench_content()

    # --- STATIC half ---------------------------------------------------
    # Exact pre-rename literal (was at the updateFrameController gate).
    assert html.count('getAttribute("data-tab") === "3d"') == 0, (
        'stale tab id "3d" must not be used in a getAttribute("data-tab") gate'
    )

    assert html.count("function updateFrameController()") == 1, (
        "updateFrameController must be defined exactly once"
    )
    fn_body = html.split("function updateFrameController()", 1)[1]
    fn_body = fn_body.split("\nfunction ", 1)[0]
    gate_literals = re.findall(r'getAttribute\("data-tab"\) === "([^"]+)"', fn_body)
    assert len(gate_literals) == 1, (
        "updateFrameController must contain exactly one data-tab gate literal, "
        f"found {gate_literals!r}"
    )
    # The gated id must be one of the ids that actually render on the page
    # (parsed live from data-tab="..." — not hardcoded, so tab evolution
    # keeps this test honest without edits here).
    rendered_tab_ids = set(re.findall(r'data-tab="([^"]+)"', html))
    assert rendered_tab_ids, "page must render at least one data-tab button"
    assert gate_literals[0] in rendered_tab_ids, (
        f"updateFrameController gates on tab id {gate_literals[0]!r} which is "
        f"not a rendered data-tab value (rendered: {sorted(rendered_tab_ids)})"
    )

    # --- BEHAVIORAL half -------------------------------------------------
    if not shutil.which("node"):
        pytest.skip("node not available")

    fn_source = "function updateFrameController()" + fn_body
    script = (
        # DOM stubs: element registry + active canvas-tab id per case.
        "var elements = {};\n"
        "var activeTabId = null;\n"
        "var document = {\n"
        "  getElementById: function (id) {\n"
        "    if (!elements[id]) elements[id] = "
        "{ style: {}, textContent: '', max: '', value: '' };\n"
        "    return elements[id];\n"
        "  },\n"
        "  querySelector: function (sel) {\n"
        "    if (sel === '.canvas-tab.active') {\n"
        "      return activeTabId === null ? null : { getAttribute: function () { return activeTabId; } };\n"
        "    }\n"
        "    return null;\n"
        "  }\n"
        "};\n"
        "var molDoc = { frames: [], currentFrame: 0 };\n"
        "var currentFrame = function () { return { energy: null }; };\n"
        + fn_source
        + "\n"
        "function runCase(tabId, frameCount) {\n"
        "  elements = {};\n"
        "  activeTabId = tabId;\n"
        "  molDoc = { frames: [], currentFrame: 0 };\n"
        "  for (var i = 0; i < frameCount; i++) molDoc.frames.push({ energy: null });\n"
        "  updateFrameController();\n"
        "  return elements['frame-controller'].style.display;\n"
        "}\n"
        "var cases = [\n"
        "  ['structure', 2, 'flex'],\n"  # <-- regression: was 'none' pre-fix
        "  ['energy', 2, 'none'],\n"
        "  ['path', 2, 'none'],\n"
        "  ['wavefunction', 2, 'none'],\n"
        "  ['structure', 1, 'none'],\n"
        "  [null, 2, 'none'],\n"
        "];\n"
        "for (var c = 0; c < cases.length; c++) {\n"
        "  var got = runCase(cases[c][0], cases[c][1]);\n"
        "  if (got !== cases[c][2]) {\n"
        "    console.error('FAIL: tabId=' + cases[c][0] + ' frames=' + cases[c][1]\n"
        "      + ' expected display=' + cases[c][2] + ' got ' + got);\n"
        "    process.exit(1);\n"
        "  }\n"
        "}\n"
        "console.log('PASS');\n"
    )

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"Node updateFrameController visibility test failed:\nstdout={result.stdout}"
        f"\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


# ---------------------------------------------------------------------------
# Conformer energy-inspector key alignment (confsearch weight provenance)
# ---------------------------------------------------------------------------


def test_conformer_inspector_spec_matches_energy_graph_metadata_keys() -> None:
    """Conformer inspector spec keys must match _build_conformer_graph metadata.

    The energy-graph conformer node metadata is emitted via
    ``TrajectoryFrame.to_node()`` with exactly the keys listed in
    ``ENERGY_INSPECTOR_FIELD_SPECS.conformer`` (rank / gibbs_energy /
    relative_energy_kcal / boltzmann_weight). The legacy aliases ``weight``/
    ``gibbs_hartree`` must never reach node metadata, and must be listed in
    ``_INSPECTOR_COMMON_KEYS`` so stale payloads cannot render raw duplicate
    rows next to the localized "Boltzmann 权重" row.
    """
    html = read_workbench_content()
    source = (REPO_ROOT / "src" / "acp" / "results" / "energy_graph.py").read_text(encoding="utf-8")

    specs_block = html.split("var ENERGY_INSPECTOR_FIELD_SPECS = {", 1)[1].split("\n};", 1)[0]
    conformer_block = specs_block.split("conformer: [", 1)[1].split("],", 1)[0]
    spec_keys = set(re.findall(r'key: "([^"]+)"', conformer_block))
    assert spec_keys == {"rank", "gibbs_energy", "relative_energy_kcal", "boltzmann_weight"}

    builder_block = source.split("def _build_conformer_graph(", 1)[1].split("\ndef ", 1)[0]
    metadata_block = builder_block.split("metadata={", 1)[1].split("}", 1)[0]
    emitted_keys = set(re.findall(r'"([a-z_]+)":', metadata_block))
    assert emitted_keys == spec_keys, (
        "conformer node metadata keys must exactly match ENERGY_INSPECTOR_FIELD_SPECS.conformer"
    )
    assert "weight" not in emitted_keys and "gibbs_hartree" not in emitted_keys

    common_block = html.split("var _INSPECTOR_COMMON_KEYS = {", 1)[1].split("\n};", 1)[0]
    assert re.search(r"\bweight: 1\b", common_block), (
        "legacy alias 'weight' must be suppressed from raw metadata rows"
    )
    assert re.search(r"\bgibbs_hartree: 1\b", common_block), (
        "legacy alias 'gibbs_hartree' must be suppressed from raw metadata rows"
    )
    assert "_INSPECTOR_KEY_ALIASES" in html


def test_weight_provenance_i18n_keys_and_js_refs() -> None:
    """Contract: weight-source labels exist in BOTH locale dicts and
    structure_viewer.js renders via them.

    The inspector weight row must show ``xx.x%（<SOURCE>, <T> K）`` where
    <SOURCE> comes from ``structure.weight_source.*`` i18n keys (zh ``计算值``
    vs en ``computed``) and <T> from ``entry.energy.temperature_k``.  This
    asserts the key set in both locales plus the JS references; removing a
    key from one locale turns ``test_phase_a_i18n_structure_keys_complete_
    across_locales`` red as well.
    """
    html = read_workbench_content()
    js = (FRONTEND_JS_DIR / "structure_viewer.js").read_text(encoding="utf-8")

    zh = _locale_weight_source_values(html, _ZH_BLOCK_RE)
    en = _locale_weight_source_values(html, _EN_BLOCK_RE)

    for key in _WEIGHT_SOURCE_I18N_KEYS:
        assert key in zh, f"{key} missing from zh-CN dict"
        assert key in en, f"{key} missing from en-US dict"

    assert zh["structure.weight_source.censo"] == "CENSO"
    assert zh["structure.weight_source.xtb"] == "xTB"
    assert zh["structure.weight_source.dft"] == "DFT"
    assert zh["structure.weight_source.computed"] == "\u8ba1\u7b97\u503c"  # 计算值
    assert en["structure.weight_source.censo"] == "CENSO"
    assert en["structure.weight_source.xtb"] == "xTB"
    assert en["structure.weight_source.dft"] == "DFT"
    assert en["structure.weight_source.computed"] == "computed"

    # JS must look labels up through the i18n keys and read the payload fields.
    assert '"structure.weight_source."' in js, (
        "structure_viewer.js must resolve labels via structure.weight_source.* keys"
    )
    assert "weight_source" in js
    assert "temperature_k" in js
    assert "WEIGHT_SOURCES" in js, "zh STR fallback table for weight sources missing"


def test_weight_provenance_rendering_exact_strings() -> None:
    """Node logic: exact rendered weight strings incl. edge cases.

    Cases (weight 0.5 => ``50.0%``):
      both      -> ``50.0%（CENSO, 298.15 K）``
      source only -> ``50.0%（CENSO）``
      temp only -> ``50.0%（298.15 K）``
      neither   -> bare ``50.0%`` (must equal today's format exactly)
      zh computed -> ``50.0%（计算值）`` vs en computed -> ``50.0%（computed）``
      unknown source -> raw value shown; non-numeric temperature omitted.
    """
    if not shutil.which("node"):
        pytest.skip("node not available")

    js_path = FRONTEND_JS_DIR / "structure_viewer.js"
    script = textwrap.dedent("""\
        var window = { fetch: null };
        var AbortController = class { constructor() { this.signal = null; } abort() {} };
        require(JS_PATH);

        var ns = window.ACPStructureViewer;
        if (typeof ns._formatWeightValue !== "function") {
            console.error("FAIL: _formatWeightValue not exposed on namespace");
            process.exit(1);
        }

        // en locale labels via the app's t() shape (missing key => key itself)
        var EN = {
            "structure.weight_source.censo": "CENSO",
            "structure.weight_source.xtb": "xTB",
            "structure.weight_source.dft": "DFT",
            "structure.weight_source.computed": "computed",
        };

        function setLang(lang) {
            if (lang === "en") {
                global.t = function(key) { return EN[key] || key; };
            } else {
                delete global.t;  // zh STR fallback path
            }
        }

        var cases = [
            // [lang, entry, expected]
            ["en", { boltzmann_weight: 0.5, weight_source: "censo",
                     energy: { temperature_k: 298.15 } },
                "50.0%\uff08CENSO, 298.15 K\uff09"],
            ["en", { boltzmann_weight: 0.5, weight_source: "censo" },
                "50.0%\uff08CENSO\uff09"],
            ["en", { boltzmann_weight: 0.5,
                     energy: { temperature_k: 298.15 } },
                "50.0%\uff08298.15 K\uff09"],
            ["en", { boltzmann_weight: 0.5 },
                "50.0%"],
            ["zh", { boltzmann_weight: 0.5, weight_source: "computed" },
                "50.0%\uff08\u8ba1\u7b97\u503c\uff09"],
            ["en", { boltzmann_weight: 0.5, weight_source: "computed" },
                "50.0%\uff08computed\uff09"],
            ["en", { boltzmann_weight: 0.5, weight_source: "mystery" },
                "50.0%\uff08mystery\uff09"],
            ["en", { boltzmann_weight: 0.5, weight_source: "xtb",
                     energy: { temperature_k: 298.1 } },
                "50.0%\uff08xTB, 298.1 K\uff09"],
            ["en", { boltzmann_weight: 0.5,
                     energy: { temperature_k: 300 } },
                "50.0%\uff08300 K\uff09"],
            ["en", { boltzmann_weight: 0.5,
                     energy: { temperature_k: 298.156 } },
                "50.0%\uff08298.16 K\uff09"],
            ["en", { boltzmann_weight: 0.5, weight_source: "dft",
                     energy: { temperature_k: "not-a-number" } },
                "50.0%\uff08DFT\uff09"],
            ["en", { boltzmann_weight: 0.005, weight_source: "censo" },
                "0.5%\uff08CENSO\uff09"],
        ];

        for (var i = 0; i < cases.length; i++) {
            setLang(cases[i][0]);
            var got = ns._formatWeightValue(cases[i][1]);
            if (got !== cases[i][2]) {
                console.error("FAIL: lang=" + cases[i][0]
                    + " entry=" + JSON.stringify(cases[i][1])
                    + " expected=" + JSON.stringify(cases[i][2])
                    + " got=" + JSON.stringify(got));
                process.exit(1);
            }
        }

        // Tooltip helper: empty without provenance, full string with it.
        setLang("en");
        if (ns._weightBarTitle({ boltzmann_weight: 0.5 }) !== "") {
            console.error("FAIL: _weightBarTitle must be empty without provenance");
            process.exit(1);
        }
        var tip = ns._weightBarTitle({
            boltzmann_weight: 0.5, weight_source: "censo",
            energy: { temperature_k: 298.15 }
        });
        if (tip !== "50.0%\uff08CENSO, 298.15 K\uff09") {
            console.error(
                "FAIL: _weightBarTitle expected provenance string, got " + JSON.stringify(tip)
            );
            process.exit(1);
        }
        console.log("PASS");
    """).replace("JS_PATH", json.dumps(str(js_path)))

    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, (
        f"Weight provenance rendering test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_weight_provenance_boltzmann_bar_tooltips() -> None:
    """JS contract: all three Boltzmann bars carry the provenance tooltip
    when source/temperature present, and nothing when absent."""
    js = (FRONTEND_JS_DIR / "structure_viewer.js").read_text(encoding="utf-8")

    for marker in (
        'bar.className = "sv-switcher-item-boltz";',
        'bar.className = "sv-strip-boltz";',
        'bar.className = "sv-boltzmann-bar";',
    ):
        assert marker in js, f"bar site {marker!r} missing"
        block = js.split(marker, 1)[1][:300]
        assert "_weightBarTitle(" in block, (
            f"{marker} block must set the provenance tooltip via _weightBarTitle"
        )
        assert 'setAttribute("title"' in block or ".title =" in block, (
            f"{marker} block must expose the tooltip as a title attribute"
        )

    # _weightBarTitle must gate on provenance (empty => no title => unchanged)
    helper = js.split("function _weightBarTitle(", 1)[1].split("\n  }", 1)[0]
    assert "_weightProvenanceSuffix(" in helper

    # Inspector weight row goes through the shared formatter.
    inspector_tail = js.split('_t("structure.weight", STR.WEIGHT)', 1)[1][:200]
    assert "_formatWeightValue(" in inspector_tail, (
        "inspector weight row must render via _formatWeightValue"
    )


# ---------------------------------------------------------------------------
# Weight provenance display (confsearch weight provenance, todo 9)
# ---------------------------------------------------------------------------

_WEIGHT_SOURCE_I18N_KEYS = (
    "structure.weight_source.censo",
    "structure.weight_source.xtb",
    "structure.weight_source.dft",
    "structure.weight_source.computed",
)


def _locale_weight_source_values(html: str, block_re: re.Pattern[str]) -> dict[str, str]:  # type: ignore[type-arg]
    """Extract structure.weight_source.* key/value pairs from a locale block."""
    m = block_re.search(html)
    if not m:
        return {}
    block = m.group(1)
    out: dict[str, str] = {}
    for key in _WEIGHT_SOURCE_I18N_KEYS:
        vm = re.search(rf'"{re.escape(key)}":\s*"([^"]*)"', block)
        if vm:
            out[key] = vm.group(1)
    return out
