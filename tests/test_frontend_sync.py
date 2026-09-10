from __future__ import annotations

import re
from pathlib import Path

from acp.catalog import METHOD_SCHEMAS, WORKFLOW_CATALOG

REPO_ROOT = Path(__file__).parents[1]
FRONTEND = REPO_ROOT / "frontend" / "ACP_Workbench_v2.html"
SERVER = REPO_ROOT / "src" / "acp" / "api" / "server.py"

_I18N_KEY_RE = re.compile(r'"((?:energy|tab\.energy)\.[^"]+)":')
_NODES_I18N_KEY_RE = re.compile(r'"(nodes\.[^"]+)":')
_ZH_BLOCK_RE = re.compile(r'"zh-CN":\s*\{(.*?)\n\s*"en-US":', re.DOTALL)
_EN_BLOCK_RE = re.compile(r'"en-US":\s*\{(.*?)(?:\n\s*\};)', re.DOTALL)


def _extract_energy_keys(html: str, block_re: re.Pattern[str]) -> set[str]:  # type: ignore[type-arg]
    """Extract energy.* / tab.energy.* i18n keys from a single locale block."""
    m = block_re.search(html)
    if not m:
        return set()
    return set(_I18N_KEY_RE.findall(m.group(1)))


def _extract_nodes_keys(html: str, block_re: re.Pattern[str]) -> set[str]:  # type: ignore[type-arg]
    """Extract nodes.* i18n keys from a single locale block."""
    m = block_re.search(html)
    if not m:
        return set()
    return set(_NODES_I18N_KEY_RE.findall(m.group(1)))


def test_default_workbench_keeps_original_v2_frontend_and_v1_contract() -> None:
    html = FRONTEND.read_text(encoding="utf-8")
    server = SERVER.read_text(encoding="utf-8")

    assert 'html_path = _FRONTEND_DIR / "ACP_Workbench_v2.html"' in server
    assert 'ACP_Workbench_minimal.html' not in server
    assert 'const API_BASE = "/api/v1"' in html
    for feature in ("workflow-catalog", "method-catalog", "/uploads", "/structures/parse"):
        assert feature in html
    for action in ("pause", "unpause", "continue", "rerun", "purge"):
        assert f'"/jobs/" + encodeURIComponent(jobId) + "/{action}"' in html or action in html
    assert "/jobs/" in html and "/detail" in html
    assert html.count("function updateSrPickButtons()") == 1
    assert 'serviceStatus === "ok"' in html
    assert 'if (!resp.ok) throw new Error("HTTP " + resp.status + " " + resp.statusText);' in html
    assert 'typeof $3Dmol === "undefined"' in html
    assert "</html>\n;\n</script>" not in html
    assert 'id="mc-profile-select"' in html
    assert 'id="batch-optimize-profile"' not in html
    assert 'id="batch-optimize-profile-summary"' in html
    assert "methodPayload.optimization_method" in html
    assert "methodPayload.single_point_method" in html
    assert "window.wizardStructures" not in html
    assert "function applyBatchOptimizeMethodFields(methodPayload)" in html
    assert "applyBatchOptimizeMethodFields(methodPayload);" in html
    assert "mc-number-with-unit" in html
    assert "mc-number-unit" in html
    # PES manual selections are persisted independently; BatchOptimize is
    # intentionally started from the new-task flow rather than from the
    # energy viewer.
    assert 'data-energy-action="toggle-selection-lock"' in html
    assert "function energyGraphToggleSelectionLock()" in html
    assert "选点已锁定" in html
    assert "选点可编辑" in html
    assert "energyGraphConfirmAndBatch" not in html
    assert 'data-energy-action="to-batch"' not in html
    # BatchOptimize keeps one scheduler task per parsed structure.  The
    # frontend may group those tasks with batch_id, but must not submit one
    # batch_structures payload as a single scheduler job.
    assert "for (var i = 0; i < wizardStructures.length; i++)" in html
    assert "batch_id: batchId" in html
    assert "await submitJobBatch(batchBodies);" in html
    assert "var batchBody = {" not in html

    # Renamed tab: HTML button + i18n zh-CN + i18n en-US
    assert '>能量与轨迹</button>' in html
    assert '"tab.energy": "能量与轨迹"' in html
    assert '"tab.energy": "Energy & Trajectory"' in html
    assert "能量图" not in html

    # Generic frame actions: one primary action (save-as-candidate) with an
    # explicit TS/INT picker; export lives in the overflow menu; the
    # misleading localStorage "lock" was removed (2026-09 review).
    assert 'data-energy-action="save-candidate"' in html
    assert 'data-energy-action="change-role"' in html
    assert 'data-energy-action="remove-candidate"' in html
    assert 'data-energy-action="more-menu"' in html
    assert 'data-energy-action="export-frame"' in html
    assert 'data-energy-candidate-role="TS"' in html
    assert 'data-energy-candidate-role="INT"' in html
    assert "acp-frame-lock" not in html
    assert 'data-energy-action="lock-frame"' not in html
    assert "isFrameLocked" not in html
    # Saved-candidate state is server-authoritative: restored from
    # GET /frame-candidates and guarded by expected_revision on mutations.
    assert '"/jobs/" + encodeURIComponent(jobId) + "/frame-candidates"' in html
    assert "expected_revision" in html
    # The role prompt() free-text flow is gone — roles come only from the
    # TS/INT picker buttons.
    assert "energy.inspector.role_none" not in html
    # Running jobs disable the save action with an explicit reason instead of
    # offering a button the backend will reject with 409.
    assert "energy.inspector.save_after_complete" in html
    assert "save-candidate\" data-energy-frame-id=\"' + escapeHtml(frameIdStr) + '\" disabled" in html

    # Sampling hooks
    assert 'data-sampling-view="' in html
    assert "samplingState" in html

    # Convergence panel hooks
    assert 'data-optimization-convergence' in html
    assert "function energyOptCriteriaRows(data, cycleMetadata)" in html

    # Unified geometry loader
    assert "function energyGraphLoadFrameGeometry(node)" in html


def test_optimization_geometry_loader_frame_endpoint_first() -> None:
    """Regression guard for the optimization structure viewer (2026-09 report).

    geometry_ref is stored relative to the trajectory file
    (e.g. WORK/03_OPT/optimization_trajectory.json), so the job-root files
    endpoint cannot resolve it and returns 404.  The optimization view must
    therefore call /optimization/frame/{frame_index} FIRST, and every source
    must live in its own try/catch so a failed request never blocks the
    remaining fallbacks (only AbortError is rethrown).
    """
    html = FRONTEND.read_text(encoding="utf-8")

    body = html.split("function energyGraphLoadFrameGeometry(node)", 1)[1]
    body = body.split("\nasync function ", 1)[0]

    frame_pos = body.find('"/optimization/frame/"')
    files_pos = body.find('"/files/"')
    assert frame_pos != -1 and files_pos != -1
    assert frame_pos < files_pos

    # Four sources, each in an independent try/catch that only rethrows
    # AbortError — a 404 in one can never skip the rest.
    assert body.count('if (e && e.name === "AbortError") throw e;') >= 4


def test_minimal_frontend_is_not_the_default_page() -> None:
    server = SERVER.read_text(encoding="utf-8")

    assert "ACP_Workbench_minimal.html" not in server


def test_wizard_default_workflow_and_protocol_are_catalog_driven() -> None:
    """Regression guard for the stale-protocol-page bug (2026-09-07).

    The create-task wizard used to hardcode the retired "energy" workflow as
    the default and fall back to the legacy "confsearch" method schema, so the
    first open rendered the pre-refactor protocol page until a workflow was
    re-picked. The default must now come from the catalog at runtime:
    ``resolveDefaultWorkflow`` (Confsearch first) + ``pickDefaultProfile``
    (censo-crest preferred) + self-healing ``ensureWizardWorkflowValid``.
    """
    html = FRONTEND.read_text(encoding="utf-8")

    # 1. Catalog integrity: every visible workflow must resolve to a schema
    #    the method catalog actually defines, otherwise the wizard cannot
    #    derive a default profile from it.
    for wf in WORKFLOW_CATALOG:
        if wf.get("visible") is False:
            continue
        assert wf["method_schema_id"] in METHOD_SCHEMAS, (
            f"workflow {wf['id']!r} -> schema {wf['method_schema_id']!r} "
            "missing from METHOD_SCHEMAS"
        )
    assert METHOD_SCHEMAS["confsearch_unified"]["profiles"], (
        "confsearch_unified must define profiles (xtb-crest/xtb-md/"
        "censo-crest/xtbmd-censo) for the wizard default"
    )
    censo_ids = {p["profile_id"] for p in METHOD_SCHEMAS["confsearch_unified"]["profiles"]}
    assert "censo-crest" in censo_ids

    # 2. No hardcoded retired workflow id may serve as the wizard default.
    retired_ids = [w["id"] for w in WORKFLOW_CATALOG if w.get("status") != "active"]
    for rid in retired_ids:
        assert f'wizardState.workflow.id || "{rid}"' not in html, (
            f"retired workflow {rid!r} must not be a hardcoded wizard default"
        )
    assert 'workflow: { id: "", label: "" }' in html
    assert 'id: "energy", label: "Conformer Energy"' not in html

    # 3. No hardcoded schema-id fallback (legacy "confsearch" literal) may
    #    bypass the catalog-derived method_schema_id.
    assert '|| "confsearch"' not in html

    # 4. Catalog-driven helpers exist and are wired into the consumers.
    assert "function resolveDefaultWorkflow(preferredId)" in html
    assert 'w.id === "Confsearch" && w.status === "active"' in html
    assert "function ensureWizardWorkflowValid()" in html
    assert "function pickDefaultProfile(schema)" in html
    assert 'p.profile_id === "censo-crest"' in html
    for consumer in ("updateConfigCards", "openMethodConfig", "submitJobModal"):
        fn_body = html.split(f"function {consumer}(", 1)[1].split("\nfunction ", 1)[0]
        assert "ensureWizardWorkflowValid()" in fn_body, (
            f"{consumer} must self-heal a stale wizard workflow"
        )
    assert "resolveDefaultWorkflow(wizardState.workflow.id)" in html  # init block
    assert "resolveDefaultWorkflow(pending.workflow)" in html  # applyPendingNewTask


def test_energy_chart_axes_cannot_scroll_out_of_viewport() -> None:
    """Regression guard for docs/ACP_Energy_Graph_Axis_Rendering_Issue_Report.md.

    The axis band lives at the bottom of the fixed 1040x520 viewBox.  The
    layout contract must guarantee the SVG always fits the chart viewport so
    the axes stay visible: no vertical scrolling, no min-height forcing
    overflow, and no full-replacement redraw leaving the axes below the fold.
    """
    html = FRONTEND.read_text(encoding="utf-8")

    # Energy chart: viewport must never scroll; the SVG is fully responsive
    # (min-width: 0) so the axes can never be pushed out of the card — the
    # contract is enforced by layout, not by scrollbars.
    assert ".energy-chart-scroll {\n  min-width: 0;\n  min-height: 0;\n  overflow: hidden;" in html
    assert ".energy-chart-scroll { min-height: 0; overflow: auto;" not in html
    svg_rule = (
        ".energy-chart-svg {\n  display: block;\n  flex: 1 1 auto;\n  width: 100%;\n"
        "  min-width: 0;\n  min-height: 0;\n  height: 100%;\n}"
    )
    assert svg_rule in html
    assert ".energy-chart-svg { display: block; width: 100%; min-width: 640px; min-height: 360px; height: 100%; }" not in html

    # Optimization chart: same contract — the SVG is measured from the live
    # container (ResizeObserver), so no fixed pixel width can force clipping.
    assert ".optimization-chart-svg { display: block; width: 100%; min-width: 0; min-height: 0; height: 100%; cursor: grab; }" in html
    assert "min-width: 620px" not in html
    assert "min-width: 620px; height: 238px;" not in html
    assert ".optimization-chart-scroll { flex: 1 1 0; min-height: 0; overflow: hidden;" in html
    assert ".optimization-chart-card { display: flex; flex-direction: column;" in html

    # Axes are still generated and appended inside the SVG viewBox.
    assert "function energyGraphAxesMarkup(xDom, yDom, geom)" in html
    assert "svg += energyGraphAxesMarkup(xDom, yDom, geom);" in html


def test_optimization_chart_single_view_switching_contract() -> None:
    """Contract for the reworked optimization viewer (2026-09 report).

    One chart container with four mutually exclusive views, geometry measured
    from the live container, zoom/pan/box/reset interactions wired for the
    optimization branch, and item_id-locked polling for batch trajectories.
    """
    html = FRONTEND.read_text(encoding="utf-8")

    view_ids = (
        'OPTIMIZATION_VIEW_IDS = ["energy", "force", '
        '"energy_derivative", "force_derivative"]'
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
    html = FRONTEND.read_text(encoding="utf-8")

    # 1. State machine: selectionOrigin field + selectJob reset.
    assert 'selectionOrigin: "none",' in html
    select_job = html.split("async function selectJob(jobId)", 1)[1].split("\nasync function ", 1)[0]
    assert 'energyGraphState.selectionOrigin = "none";' in select_job

    # 2. Panel entry: one pure model decides the mode; the old last_cycle
    #    reader is gone and the render path never touches metadata.last_cycle.
    assert "function optimizationStatusPanelModel(data, job)" in html
    assert "function optimizationStatusPanelMarkup(data)" in html
    assert "optimizationConvergencePanelMarkup" not in html
    model = html.split("function optimizationStatusPanelModel(data, job)", 1)[1].split("\nfunction ", 1)[0]
    assert "optimizationExplicitSelectedNode(data)" in model
    assert "buildSelectedCycleConvergenceModel(data, selected)" in model
    assert "buildTaskStatusModel(data, job)" in model
    criteria = html.split("function energyOptCriteriaRows(data, cycleMetadata)", 1)[1].split("\n}\n", 1)[0]
    assert "last_cycle" not in criteria
    assert "metadata.last_cycle" not in html.split("function optimizationWorkspaceMarkup(data)", 1)[1]

    # 3. Explicit-pick detection requires selectionOrigin === "user" and must
    #    not fall back to another node (inspector/panel node consistency).
    explicit = html.split("function optimizationExplicitSelectedNode(data)", 1)[1].split("\nfunction ", 1)[0]
    assert 'energyGraphState.selectionOrigin !== "user"' in explicit
    assert "nodes[0]" not in explicit

    # 4. Selected-cycle mode reads the picked node's own metadata, so a
    #    historical complete cycle shows its numbers even when the last cycle
    #    is incomplete.
    selected_model = html.split("function buildSelectedCycleConvergenceModel(data, node)", 1)[1].split("\nfunction ", 1)[0]
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
    task_model = html.split("function buildTaskStatusModel(data, job)", 1)[1].split("\nfunction ", 1)[0]
    assert "(job && job.status) || (data && data.status)" in task_model
    assert 'rawStatus === "queued" || rawStatus === "starting" || rawStatus === "pending"' in task_model
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
    select_frame = html.split("function energyGraphSelectFrame(frameIndex, origin)", 1)[1].split("\nfunction ", 1)[0]
    assert 'energyGraphState.selectionOrigin = origin === "follow" ? "follow" : "user";' in select_frame
    assert 'energyGraphSelectFrame(nodes[nodes.length - 1].frame_index, "follow")' in html
    opt_bind = html.split("function optimizationGraphBind(root)", 1)[1].split("\nfunction ", 1)[0]
    assert "energyGraphState.liveFollow = false;" in opt_bind
    nav = html.split("function energyGraphBindInspectorButtons(root)", 1)[1].split("\nfunction ", 1)[0]
    assert nav.count("energyGraphState.liveFollow = false;") >= 2
    keydown = html.split('if (e.key === "ArrowLeft")', 1)[1].split("switch (e.key)", 1)[0]
    assert keydown.count("energyGraphState.liveFollow = false;") >= 2
    refresh = html.split('if (String(data.view_type || "") === "optimization")', 1)[1].split("\n    }", 1)[0]
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
    html = FRONTEND.read_text(encoding="utf-8")

    zh_keys = _extract_energy_keys(html, _ZH_BLOCK_RE)
    en_keys = _extract_energy_keys(html, _EN_BLOCK_RE)

    assert zh_keys, "No energy.* / tab.energy.* keys found in zh-CN block"
    assert en_keys, "No energy.* / tab.energy.* keys found in en-US block"

    only_zh = zh_keys - en_keys
    only_en = en_keys - zh_keys
    assert not only_zh, f"Keys in zh-CN but missing from en-US: {sorted(only_zh)}"
    assert not only_en, f"Keys in en-US but missing from zh-CN: {sorted(only_en)}"


def test_node_selector_structure_and_disabled_logic_lock() -> None:
    """Structural lock for the submit-wizard compute-node selector (T11).

    The selector is a three-state dropdown (auto / local / one option per
    remote node) rendered purely from ``POST /api/v1/nodes/matching``.  These
    assertions exist so that removing the capability-based option disabling
    turns this test red, so that re-introducing the removed E4 local-mode
    guard (which disabled every per-node option while 本地 was selected and
    deadlocked the dropdown — users could never switch back from 本地 to a
    node) also turns it red, and so T12 can rely on the stable ids/hooks
    named here.
    """
    html = FRONTEND.read_text(encoding="utf-8")

    # Stable DOM hooks (T12 payload wiring + manual QA target these ids).
    assert 'id="modal-node-select"' in html
    assert 'id="modal-node-auto-hint"' in html
    assert 'id="modal-node-detail"' in html
    assert 'id="modal-node-tags"' in html
    assert 'id="modal-node-note"' in html
    assert "data-node-name" in html
    assert "data-node-satisfies" in html
    assert "data-node-tag" in html

    # The dropdown is data-driven: no static <option> may be hardcoded into
    # the select element itself (options come from the matching response).
    select_markup = html.split('id="modal-node-select"', 1)[1].split("</select>", 1)[0]
    assert "<option" not in select_markup

    # Data flow: debounced (~300 ms) refresh → matching endpoint; the
    # frontend never re-derives capability requirements (no derivation
    # tables in the frontend — only response consumption).
    assert '"/nodes/matching"' in html
    assert "function scheduleNodeMatchingRefresh()" in html
    assert "}, 300);" in html
    assert "function buildNodeMatchingRequest()" in html
    assert "node_tags: nodeMatchingState.selectedTags.slice()" in html
    assert "function refreshNodeMatching()" in html
    assert "function renderNodeSelector()" in html

    # Disabled logic lock 1: unsatisfying nodes render disabled + reason.
    assert "opt.disabled = true;" in html
    assert "opt.title = nodeDisabledReason(node);" in html
    assert "function nodeDisabledReason(node)" in html

    # Disabled logic lock 2 (deadlock regression): capability/health is the
    # ONLY source of option disabling. The E4 local guard disabled every
    # per-node option while 本地 was selected, which deadlocked the
    # three-state select — once 本地 was chosen no node option could be
    # picked again. It was removed because the single-select value plus the
    # applyNodeSelectionToBody payload mapping are already mutually
    # exclusive; re-introducing any "local selection disables node options"
    # branch must turn this test red.
    assert "applyNodeSelectLocalGuard" not in html
    assert "isLocal" not in html
    render_body = html.split("function renderNodeSelector()", 1)[1].split("\nfunction ", 1)[0]
    assert "opt.disabled = true;" in render_body
    change_body = html.split("function onNodeSelectChange()", 1)[1].split("\nfunction ", 1)[0]
    assert "opt.disabled" not in change_body

    # Wizard change points flow through updateConfigCards → debounced
    # refresh; the select has its own change listener; modal open resets
    # tag selection and schedules a refresh after becoming visible.
    cards_body = html.split("function updateConfigCards()", 1)[1].split("\nfunction ", 1)[0]
    assert "scheduleNodeMatchingRefresh();" in cards_body
    assert 'document.getElementById("modal-node-select").addEventListener("change", onNodeSelectChange);' in html
    open_body = html.split("function openModal()", 1)[1].split("\nfunction ", 1)[0]
    assert "resetNodeSelector();" in open_body
    assert "scheduleNodeMatchingRefresh();" in open_body

    # D12: submission-time target error codes map to localized text.
    assert "function translateNodeError(err)" in html
    assert "function formatSubmitError(err)" in html
    assert 'key = "nodes.error." + parsed.code;' in html
    # m9: EVERY submit-branch alert routes coded 400 bodies through
    # formatSubmitError — definition + stage chain + PESsearch inline scan
    # + NMR + mechanism.  (The general per-structure loop alerts with a
    # different local name: formatSubmitError(error).)
    assert html.count("formatSubmitError(err)") == 5
    submit_failed_prefix = 'window.alert((t("modal.submit_failed") || "提交失败") + ": "'
    assert html.count(submit_failed_prefix + " + formatSubmitError(err));") == 4
    # No submit branch may surface the raw Error (bare JSON) again.
    assert 'window.alert((t("modal.submit_failed") || "提交失败") + ": " + err);' not in html

    # m11: a refresh that disables the already-picked node must NOT
    # silently clear the dropdown to auto — the pick stays selected (a
    # disabled option can remain the current value) and a localized
    # warning renders in the note area.  The submit still carries the
    # pick; the server's coded 400 (localized via formatSubmitError) is
    # the backstop.
    assert 'if (selected && selected.disabled) sel.value = "";' not in html
    hints_body = html.split("function updateNodeSelectorHints()", 1)[1]
    hints_body = hints_body.split("\nfunction ", 1)[0]
    assert "selectedOpt.disabled" in hints_body
    assert 't("nodes.selected_unavailable")' in hints_body
    assert '"nodes.selected_unavailable":' in html

    # Badge/hint rendering for degraded / capability_state / declared_ok.
    assert "nodes.badge.degraded" in html
    assert "nodes.badge.probe_inferred" in html
    assert "nodes.badge.unknown" in html
    assert "nodes.badge.mismatch" in html
    assert "nodes.auto_remote_hint" in html


def test_node_selector_i18n_keys_complete_across_locales() -> None:
    """Dual-locale completeness for nodes.* keys (selector + D12 codes)."""
    html = FRONTEND.read_text(encoding="utf-8")

    zh_keys = _extract_nodes_keys(html, _ZH_BLOCK_RE)
    en_keys = _extract_nodes_keys(html, _EN_BLOCK_RE)

    assert zh_keys, "No nodes.* keys found in zh-CN block"
    assert en_keys, "No nodes.* keys found in en-US block"

    required = {
        "nodes.section",
        "nodes.opt.auto",
        "nodes.opt.local",
        "nodes.continue_source",
        "nodes.continue_to_hint",
        "nodes.source_pin",
        "nodes.auto_remote_hint",
        "nodes.matching_error",
        "nodes.tags_hint",
        "nodes.badge.degraded",
        "nodes.badge.probe_inferred",
        "nodes.badge.unknown",
        "nodes.badge.mismatch",
        "nodes.reason.offline",
        "nodes.reason.missing_software",
        "nodes.reason.missing_tags",
        "nodes.error.no_capable_node",
        "nodes.error.target_node_incapable",
        "nodes.error.unknown_target_node",
        "nodes.error.target_node_disabled",
        "nodes.error.execution_target_error",
        "nodes.selected_unavailable",
    }
    assert required <= zh_keys, f"missing zh-CN nodes.* keys: {sorted(required - zh_keys)}"

    only_zh = zh_keys - en_keys
    only_en = en_keys - zh_keys
    assert not only_zh, f"Keys in zh-CN but missing from en-US: {sorted(only_zh)}"
    assert not only_en, f"Keys in en-US but missing from zh-CN: {sorted(only_en)}"


def test_submit_payload_carries_node_selection() -> None:
    """T12 lock: wizard submit payloads carry target_node / node_tags.

    The selector state must reach the v1 create body on every submit branch:
    a picked node (or "local") becomes ``target_node``, checked tag chips
    become ``node_tags``, and auto ("") omits both so the server default
    applies.  Removing a payload key or a branch's wiring turns this red.
    """
    html = FRONTEND.read_text(encoding="utf-8")

    # Shared helper maps the selector state onto the payload.
    helper = html.split("function applyNodeSelectionToBody(body, opts)", 1)[1]
    helper = helper.split("\nfunction ", 1)[0]
    assert "body.target_node = sel.value;" in helper
    assert "body.node_tags = nodeMatchingState.selectedTags.slice();" in helper
    # Stage chains may only send an explicit user pick (the server inherits
    # the source pin when the field is omitted).
    assert "opts.requireUserChoice" in helper
    assert "nodeMatchingState.userTouched" in helper

    # Every wizard submit branch routes through the helper: NMR single-job,
    # mechanism single-job, the general per-structure batch loop, the
    # PESsearch inline coordinate-scan path, and stage chains.
    assert "applyNodeSelectionToBody(nmrBody);" in html
    assert "applyNodeSelectionToBody(mechBody);" in html
    assert html.count("applyNodeSelectionToBody(body);") >= 2
    stage_body = html.split("async function submitStageWorkflowJob(workflow)", 1)[1]
    stage_body = stage_body.split("\nasync function ", 1)[0]
    assert "applyNodeSelectionToBody(body, { requireUserChoice: true });" in stage_body

    # No hardcoded node names anywhere — options and payloads are data-driven
    # from /nodes/matching responses only.
    for literal in ('"comp-', "'comp-", "gpu-node", "fat-node", "bigmem"):
        assert literal not in html, f"hardcoded node literal {literal!r} in frontend"


def test_continue_node_override_and_stage_preselect_lock() -> None:
    """T12 lock: continue node override, stage preselect, top-level node_id.

    The detail continue action gains an optional "continue to node" select
    whose blank option means back-to-source (no request field); the stage
    wizard mirrors the source job's explicit pin without sending it; and the
    remote-node display reads the T8 top-level node_id with a result.node
    fallback for older responses.
    """
    html = FRONTEND.read_text(encoding="utf-8")

    # Continue override control in the detail action bar.
    assert 'dContinueNodeSel.id = "detail-continue-node-select";' in html
    pop = html.split("function populateContinueNodeOptions(sel)", 1)[1]
    pop = pop.split("\nasync function ", 1)[0]
    assert 't("nodes.continue_source")' in pop
    assert 't("nodes.opt.local")' in pop
    assert 'node.status === "offline"' in pop  # offline nodes are not offered

    # The continue request only carries target_node when the user picked a
    # node; the default path keeps the previous no-body POST (= back to
    # source, T6 contract).
    cont = html.split("async function continueJob(jobId)", 1)[1]
    cont = cont.split("\nasync function ", 1)[0]
    assert "if (targetNode) {" in cont
    assert "opts.body = JSON.stringify({ target_node: targetNode });" in cont

    # Stage preselect: mirror the source job's explicit spec.target_node.
    assert "async function preselectNodeFromSourceJob(sourceJobId)" in html
    pre = html.split("async function preselectNodeFromSourceJob(sourceJobId)", 1)[1]
    pre = pre.split("\nfunction ", 1)[0]
    assert "srcSpec.target_node" in pre
    # m10: userTouched resets ONLY synchronously at the new-source decision
    # point (before the fetch); the fetch-return path records the pin for
    # the source-pin detail line but never resets/applies over a manual
    # pick made while the fetch was in flight.
    reset_pos = pre.find("nodeMatchingState.userTouched = false;")
    fetch_pos = pre.find('await api("/jobs/"')
    assert reset_pos != -1 and fetch_pos != -1
    assert reset_pos < fetch_pos, (
        "userTouched must reset before the source-job fetch (new decision point)"
    )
    assert "nodeMatchingState.userTouched = false;" not in pre[fetch_pos:], (
        "fetch-return must never reset userTouched — that was the m10 race"
    )
    assert "function applyNodePreselect(sel, pinned)" in html
    render_body = html.split("function renderNodeSelector()", 1)[1]
    render_body = render_body.split("\nfunction ", 1)[0]
    assert "applyNodePreselect(sel, nodeMatchingState.preselectNode);" in render_body
    # A programmatic mirror never counts as a user choice — only the change
    # handler may set userTouched.
    change_body = html.split("function onNodeSelectChange()", 1)[1]
    change_body = change_body.split("\nfunction ", 1)[0]
    assert "nodeMatchingState.userTouched = true;" in change_body
    # Source-field listener + both configureStageSourcePanel directions.
    assert "preselectNodeFromSourceJob(this.value);" in html
    cfg = html.split("function configureStageSourcePanel(workflowId)", 1)[1]
    cfg = cfg.split("\nfunction ", 1)[0]
    assert 'preselectNodeFromSourceJob("");' in cfg
    assert 'preselectNodeFromSourceJob(stageSourceInput ? stageSourceInput.value : "");' in cfg

    # Node display: top-level node_id (T8) first, result.node fallback, and
    # "local" is never a remote node.
    helper = html.split("function jobRemoteNodeName(job)", 1)[1]
    helper = helper.split("\nfunction ", 1)[0]
    assert "job.node_id" in helper
    assert "job.result && job.result.node" in helper
    assert 'node !== "local"' in helper
    assert html.count("lastRemoteNode = jobRemoteNodeName(") >= 3
    assert "selectedJobIsRemote = !!(job.result && job.result.node)" not in html


def test_frame_candidate_save_uses_terminal_status_predicate() -> None:
    """Red-first contract: the energy inspector save/role-picker gate must use a
    terminal-status predicate (completed OR failed OR cancelled) instead of a
    completed-only variable.

    The current source defines ``jobCompleted = ... === "completed"`` and
    passes it to both the save-candidate gate and the role-picker visibility
    condition.  A failed or cancelled job is also terminal — the predicate
    must recognise all three.
    """
    html = FRONTEND.read_text(encoding="utf-8")

    # Extract the inspector rendering block that contains the save gate.
    assert "var jobCompleted" in html, "sanity: jobCompleted variable exists"
    inspector = html.split("var jobCompleted", 1)[1]
    inspector = inspector.split("\nfunction ", 1)[0]

    # The block must check for all three terminal statuses, not just
    # "completed".  Currently only ``=== "completed"`` is tested.
    assert '"failed"' in inspector, (
        "Inspector must recognise 'failed' as a terminal status "
        "(currently only checks 'completed')"
    )
    assert '"cancelled"' in inspector, (
        "Inspector must recognise 'cancelled' as a terminal status "
        "(currently only checks 'completed')"
    )


def test_frame_candidate_role_picker_gate_uses_terminal_predicate() -> None:
    """Red-first contract: the role-picker visibility must NOT be gated by the
    completed-only ``jobCompleted`` variable.

    ``rolePickerHtml = (!isPES && jobCompleted)`` only shows the TS/INT
    picker when the job status is exactly "completed".  A terminal-status
    predicate (completed / failed / cancelled) must replace it.
    """
    html = FRONTEND.read_text(encoding="utf-8")

    # The role-picker gate must use a terminal predicate, not the
    # completed-only variable.
    assert "(!isPES && jobCompleted)" not in html, (
        "Role-picker gate must use terminal-status predicate, "
        "not completed-only jobCompleted"
    )


def test_frame_candidate_disabled_active_job_branch_retained() -> None:
    """Regression guard: the disabled save-candidate button for active
    (non-terminal) jobs must remain with an explanatory badge.

    This assertion must PASS against the current source — it guards the
    existing disabled branch so a future refactor cannot silently drop it.
    """
    html = FRONTEND.read_text(encoding="utf-8")

    assert "energy.inspector.save_after_complete" in html
    assert 'save-candidate" data-energy-frame-id="\' + escapeHtml(frameIdStr) + \'" disabled' in html


def test_frame_candidate_post_body_includes_item_id() -> None:
    """Red-first contract: the frame-candidate POST body must propagate
    ``item_id`` from the graph metadata so the backend can associate the
    saved candidate with the correct batch item.

    The current POST body contains ``view_type``, ``frame_index``,
    ``role``, and ``expected_revision`` — but no ``item_id``.
    """
    html = FRONTEND.read_text(encoding="utf-8")

    # Extract the role-picker click handler that builds the POST body.
    assert "[data-energy-candidate-role]" in html, (
        "sanity: role-picker button selector exists"
    )
    handler = html.split("[data-energy-candidate-role]", 1)[1]
    handler = handler.split("\nfunction ", 1)[0]

    # The POST body construction must include item_id.
    assert "item_id" in handler, (
        "Frame-candidate POST body must include item_id from graph metadata"
    )


# ---------------------------------------------------------------------------
# S5 — Structure upload validation (STRUCTURE_UPLOAD_EXTS + reject helpers)
# ---------------------------------------------------------------------------

_EXPECTED_STRUCTURE_EXTS = [
    ".xyz", ".sdf", ".sd", ".mol", ".gjf", ".com", ".inp", ".log", ".out",
]


def test_structure_upload_exts_constant_defined() -> None:
    """STRUCTURE_UPLOAD_EXTS must be a single constant listing all accepted extensions."""
    html = FRONTEND.read_text(encoding="utf-8")
    assert "STRUCTURE_UPLOAD_EXTS" in html, "STRUCTURE_UPLOAD_EXTS constant missing"
    for ext in _EXPECTED_STRUCTURE_EXTS:
        assert f'"{ext}"' in html.split("STRUCTURE_UPLOAD_EXTS")[1].split("\n")[0] or \
               f"'{ext}'" in html.split("STRUCTURE_UPLOAD_EXTS")[1].split("\n")[0], (
            f"{ext} missing from STRUCTURE_UPLOAD_EXTS definition"
        )


def test_structure_upload_exts_include_sd_log_out() -> None:
    """Regression guard: .sd, .log, .out must be in the accept list (S5)."""
    html = FRONTEND.read_text(encoding="utf-8")
    accept_line = html.split('id="upload-file-input"')[1].split(">")[0]
    for ext in (".sd", ".log", ".out"):
        assert ext in accept_line, (
            f"{ext} missing from file picker accept attribute"
        )


def test_file_picker_accept_matches_constant() -> None:
    """The file picker accept attribute must list the same extensions as STRUCTURE_UPLOAD_EXTS."""
    html = FRONTEND.read_text(encoding="utf-8")
    # Extract accept attribute value
    accept_match = re.search(r'id="upload-file-input"[^>]*accept="([^"]*)"', html)
    assert accept_match, "Could not find accept attribute on upload-file-input"
    accept_exts = {e.strip().lower() for e in accept_match.group(1).split(",") if e.strip()}
    expected_exts = {e.lower() for e in _EXPECTED_STRUCTURE_EXTS}
    assert accept_exts == expected_exts, (
        f"accept attribute extensions {sorted(accept_exts)} != expected {sorted(expected_exts)}"
    )


def test_is_accepted_structure_file_function_exists() -> None:
    """isAcceptedStructureFile(file) must exist and use STRUCTURE_UPLOAD_EXTS."""
    html = FRONTEND.read_text(encoding="utf-8")
    assert "function isAcceptedStructureFile(" in html, (
        "isAcceptedStructureFile function missing"
    )
    # The function body must reference STRUCTURE_UPLOAD_EXTS
    fn_body = html.split("function isAcceptedStructureFile(")[1].split("\nfunction ")[0]
    assert "STRUCTURE_UPLOAD_EXTS" in fn_body, (
        "isAcceptedStructureFile must use STRUCTURE_UPLOAD_EXTS"
    )


def test_reject_unsupported_file_function_exists() -> None:
    """rejectUnsupportedFile(file) must exist and use modal.unsupported_ext."""
    html = FRONTEND.read_text(encoding="utf-8")
    assert "function rejectUnsupportedFile(" in html, (
        "rejectUnsupportedFile function missing"
    )
    fn_body = html.split("function rejectUnsupportedFile(")[1].split("\nfunction ")[0]
    assert "modal.unsupported_ext" in fn_body, (
        "rejectUnsupportedFile must use modal.unsupported_ext i18n key"
    )


def test_change_handler_calls_reject_unsupported() -> None:
    """The file input change handler must call rejectUnsupportedFile for unsupported files."""
    html = FRONTEND.read_text(encoding="utf-8")
    # Find the change handler block (fileInput.addEventListener("change", ...
    change_block = html.split('fileInput.addEventListener("change"')[1].split("});")[0]
    assert "rejectUnsupportedFile(" in change_block, (
        "change handler must call rejectUnsupportedFile"
    )
    assert "isAcceptedStructureFile(" in change_block, (
        "change handler must call isAcceptedStructureFile"
    )


def test_drop_handler_calls_reject_unsupported() -> None:
    """The dropzone drop handler must call rejectUnsupportedFile for unsupported files."""
    html = FRONTEND.read_text(encoding="utf-8")
    # Find the drop handler block
    drop_block = html.split('dropzone.addEventListener("drop"')[1].split("});")[0]
    assert "rejectUnsupportedFile(" in drop_block, (
        "drop handler must call rejectUnsupportedFile"
    )
    assert "isAcceptedStructureFile(" in drop_block, (
        "drop handler must call isAcceptedStructureFile"
    )


def test_reject_unsupported_does_not_clear_wizard_state() -> None:
    """rejectUnsupportedFile must not reset wizardUploadFile or other wizard state."""
    html = FRONTEND.read_text(encoding="utf-8")
    fn_body = html.split("function rejectUnsupportedFile(")[1].split("\nfunction ")[0]
    assert "wizardUploadFile = null" not in fn_body, (
        "rejectUnsupportedFile must NOT clear wizardUploadFile"
    )
    assert "parseStructuresPreview" not in fn_body, (
        "rejectUnsupportedFile must NOT trigger parseStructuresPreview"
    )


def test_unsupported_ext_locale_key_in_both_locales() -> None:
    """modal.unsupported_ext must exist in both zh-CN and en-US locale dictionaries."""
    html = FRONTEND.read_text(encoding="utf-8")
    zh_keys = _extract_all_modal_keys(html, _ZH_BLOCK_RE)
    en_keys = _extract_all_modal_keys(html, _EN_BLOCK_RE)
    assert "modal.unsupported_ext" in zh_keys, (
        "modal.unsupported_ext missing from zh-CN locale"
    )
    assert "modal.unsupported_ext" in en_keys, (
        "modal.unsupported_ext missing from en-US locale"
    )


def test_nmr_bruker_zip_upload_unchanged() -> None:
    """The NMR Bruker zip upload must remain independent (accept=.zip, parse=false)."""
    html = FRONTEND.read_text(encoding="utf-8")
    assert 'id="nmr-bruker-file"' in html, "NMR Bruker file input missing"
    bruker_block = html.split('id="nmr-bruker-file"')[1].split(">")[0]
    assert ".zip" in bruker_block, "NMR Bruker input must accept .zip"
    # Must NOT have the structure extensions
    assert ".xyz" not in bruker_block, "NMR Bruker input must not list .xyz"


def test_is_accepted_structure_file_case_insensitive() -> None:
    """isAcceptedStructureFile must compare extensions case-insensitively."""
    html = FRONTEND.read_text(encoding="utf-8")
    fn_body = html.split("function isAcceptedStructureFile(")[1].split("\nfunction ")[0]
    assert ".toLowerCase()" in fn_body or ".toLowerCase()" in fn_body.replace(" ", ""), (
        "isAcceptedStructureFile must use toLowerCase() for case-insensitive comparison"
    )


def _extract_all_modal_keys(html: str, block_re: re.Pattern[str]) -> set[str]:  # type: ignore[type-arg]
    """Extract all modal.* i18n keys from a single locale block."""
    modal_key_re = re.compile(r'"(modal\.[^"]+)":')
    m = block_re.search(html)
    if not m:
        return set()
    return set(modal_key_re.findall(m.group(1)))
