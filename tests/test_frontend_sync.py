from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path

import pytest
from pydantic import TypeAdapter
from typing_extensions import TypedDict

from acp.catalog import METHOD_SCHEMAS, WORKFLOW_CATALOG

REPO_ROOT = Path(__file__).parents[1]
FRONTEND = REPO_ROOT / "frontend" / "ACP_Workbench_v2.html"
SERVER = REPO_ROOT / "src" / "acp" / "api" / "server.py"

# Structure-viewer extracted modules (todo 13)
FRONTEND_JS_DIR = REPO_ROOT / "frontend" / "js"
FRONTEND_CSS_DIR = REPO_ROOT / "frontend" / "css"
FRONTEND_FILES: list[Path] = [
    FRONTEND,
    FRONTEND_JS_DIR / "structure_viewer.js",
    FRONTEND_JS_DIR / "structure_editor.js",
    FRONTEND_JS_DIR / "vibration_viewer.js",
    FRONTEND_CSS_DIR / "structure_viewer.css",
]

_I18N_KEY_RE = re.compile(r'"((?:energy|tab\.energy)\.[^"]+)":')
_NODES_I18N_KEY_RE = re.compile(r'"(nodes\.[^"]+)":')
_ZH_BLOCK_RE = re.compile(r'"zh-CN":\s*\{(.*?)\n\s*"en-US":', re.DOTALL)
_EN_BLOCK_RE = re.compile(r'"en-US":\s*\{(.*?)(?:\n\s*\};)', re.DOTALL)


class _ProfileRecord(TypedDict, total=False):
    profile_id: str


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

    # Structure viewer tab: conformers removed, 3d renamed to structure
    assert 'data-tab="conformers"' not in html, "conformers tab must be removed"
    assert 'data-tab="structure"' in html, "structure tab must exist"
    assert '>结构查看器</button>' in html
    assert '"tab.structure": "结构查看器"' in html
    assert '"tab.structure": "Structure Viewer"' in html
    assert '"tab.conformers"' not in html, "tab.conformers i18n key must be removed"
    assert '"tab.3d"' not in html, "tab.3d i18n key must be removed"
    # Other tabs must remain
    assert 'data-tab="path"' in html
    assert 'data-tab="energy"' in html
    assert 'data-tab="wavefunction"' in html
    # Compat mapping: stale "3d"/"conformers" -> "structure"
    assert 'tab === "3d" || tab === "conformers"' in html or 'tab === "conformers" || tab === "3d"' in html

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
    profiles = TypeAdapter(list[_ProfileRecord]).validate_python(
        METHOD_SCHEMAS["confsearch_unified"]["profiles"]
    )
    censo_ids: set[str] = set()
    for profile in profiles:
        profile_id = profile.get("profile_id")
        if profile_id is not None:
            censo_ids.add(profile_id)
    assert "censo-crest" in censo_ids

    # 2. No hardcoded retired workflow id may serve as the wizard default.
    retired_ids: list[str] = []
    for workflow in WORKFLOW_CATALOG:
        if workflow.get("status") == "active":
            continue
        workflow_id = workflow.get("id")
        if isinstance(workflow_id, str):
            retired_ids.append(workflow_id)
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
    assert "function energyGraphAxesMarkup(xDom, yDom, geom" in html
    assert "svg += energyGraphAxesMarkup(xDom, yDom, geom" in html


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
    """
    html = FRONTEND.read_text(encoding="utf-8")

    assert "const jobTerminal = jobStatus ===" in html, (
        "jobTerminal must be defined checking all three terminal statuses"
    )
    assert "&& jobTerminal" in html, (
        "canEditRole must include jobTerminal gate"
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
# PES manual review — candidate card mode vs editability (2026-09 regression)
# ---------------------------------------------------------------------------

def _candidate_card_source(html: str) -> str:
    """Source of energyCandidateCardMarkup() up to the next top-level function."""
    return html.split("function energyCandidateCardMarkup(", 1)[1].split("\nfunction ", 1)[0]


def test_pes_candidate_card_mode_is_workflow_view_driven() -> None:
    """Regression guard (2026-09): the PES card flavour must not depend on editability.

    ``energyCandidateCardMarkup()`` used to compute ``isScanPES = canEditRole``.
    Because ``canEditRole`` also encodes ``!selectionLocked``, a *locked* PES
    selection degraded to the generic single-frame candidate card whose save
    button POSTs /frame-candidate — an endpoint the backend rejects for
    PESsearch jobs with a pointer to /pes/review.  The card flavour must be
    decided by workflow + view_type alone; ``canEditRole`` may only disable
    the TS/INT/none role buttons inside the PES card.
    """
    html = FRONTEND.read_text(encoding="utf-8")
    card = _candidate_card_source(html)

    # The bug form is banned: editability must never select the card mode.
    assert "var isScanPES = canEditRole;" not in card, (
        "isScanPES must not be derived from canEditRole — a locked PES "
        "selection would render the generic /frame-candidate card"
    )
    # Mode is workflow (PESsearch) + scan view.
    assert 'var isScanPES = isPES && String(data.view_type || "") === "scan";' in card, (
        "isScanPES must be decided by PESsearch workflow + scan view_type"
    )

    # canEditRole only controls button disabling inside the PES card —
    # one occurrence per role button (ts / intermediate / none).
    assert card.count('(canEditRole ? "" : " disabled")') == 3, (
        "the three PES role buttons must be disabled via canEditRole"
    )
    assert "(selectionLocked ? \" disabled\" : '')" not in card, (
        "role-button disabling must use canEditRole, not the raw lock flag"
    )


def test_pes_locked_selection_never_renders_generic_save_button() -> None:
    """Regression guard (2026-09): the PES card must never offer /frame-candidate.

    With ``selectionLocked=true`` the PESsearch energy viewer used to fall
    back to the generic "保存为候选" card bound to
    ``data-energy-action="save-candidate"`` → POST /jobs/{id}/frame-candidate,
    which the backend rejects (400) for PESsearch.  The PES card branch must
    not bind that action at all — locking only disables the role buttons and
    saving goes through the toolbar lock button → /pes/review.
    """
    html = FRONTEND.read_text(encoding="utf-8")
    card = _candidate_card_source(html)

    assert "if (isScanPES) {" in card, "sanity: PES card branch exists"
    pes_branch = card.split("if (isScanPES) {", 1)[1].split("/* Default: idle state", 1)[0]

    assert 'data-energy-action="save-candidate"' not in pes_branch, (
        "the PES card branch must never bind the generic save-candidate action"
    )
    assert "data-energy-role" in pes_branch, (
        "the PES card branch must expose the TS/INT/none role buttons"
    )


def test_pes_review_confirm_accepts_single_candidate() -> None:
    """Regression guard (2026-09): a one-frame PES selection must be confirmable.

    The backend accepts one-element (and even empty) candidate lists, so the
    confirm dialog must POST the *entire* working candidate set to
    /pes/review without any minimum-size gate.
    """
    html = FRONTEND.read_text(encoding="utf-8")

    payload = html.split("function energyGraphReviewPayload()", 1)[1]
    payload = payload.split("\nfunction ", 1)[0]
    assert "s2scanState.candidates.map(" in payload, (
        "the review payload must carry the whole working candidate set"
    )
    assert re.search(r"candidates\.length\s*[<>]=?\s*2", payload) is None, (
        "the frontend must not impose a >=2 candidate gate — a single TS "
        "frame is a valid selection"
    )

    dialog = html.split("function energyGraphOpenSaveDialog()", 1)[1]
    dialog = dialog.split("\nfunction energyGraphRenderCurrent", 1)[0]
    assert '"/jobs/" + encodeURIComponent(jobId) + "/pes/review"' in dialog, (
        "confirming the selection must POST to /pes/review, not /frame-candidate"
    )
    assert "/frame-candidate" not in dialog, (
        "the PES confirm dialog must never call /frame-candidate"
    )


def test_pes_none_role_labeled_cancel_candidate_with_effect_hint() -> None:
    """Regression guard (2026-09): the PES "none" role must read as 取消候选.

    The third role button cancels the frame's candidate by removing it from
    the working set; the removal only takes effect once the user saves and
    locks the selection via /pes/review.  The label must therefore say
    取消候选 (not the ambiguous 无标记) and carry a "保存并锁定后生效"
    hint — as a button tooltip plus an edit-mode note line.
    """
    html = FRONTEND.read_text(encoding="utf-8")

    # Labels in both locales.
    assert '"energy.card.type_none": "取消候选"' in html, (
        "zh label must read 取消候选, not 无标记"
    )
    assert '"energy.card.type_none": "Remove Candidate"' in html
    # Effect hint shipped in both locales (2 i18n definitions + 1 t() call site).
    assert html.count('"energy.card.type_none_hint":') == 2
    assert 't("energy.card.type_none_hint")' in html
    assert "保存并锁定后生效" in html

    card = _candidate_card_source(html)
    # The none button exposes the hint as a tooltip.
    none_btn = card.split('data-energy-role="none"', 1)[1].split("</button>", 1)[0]
    assert "noneHint" in none_btn, (
        "取消候选 button must carry the effect hint as its title"
    )
    # The hint note renders only while the role buttons are editable.
    assert (
        "(canEditRole ? '<div class=\"energy-card-disabled-note\">' + noneHint + '</div>' : '')"
        in card
    ), "the effect-hint note must be gated on canEditRole (edit mode only)"


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


def test_wizard_project_section_is_first_step() -> None:
    """Project target must be wizard step 1 — before structure and workflow.

    Regression guard (2026-09 plan §1): the project block used to sit after
    the workflow/protocol cards, so users configured the job before deciding
    where it belongs.
    """
    html = FRONTEND.read_text(encoding="utf-8")
    modal = html.split('id="job-modal"', 1)[1].split('class="modal-overlay"', 1)[0]

    proj_pos = modal.find('id="modal-project-section"')
    wizard_pos = modal.find('class="wizard-input-section"')
    cards_pos = modal.find('class="config-cards-row"')
    assert proj_pos != -1 and wizard_pos != -1 and cards_pos != -1
    assert proj_pos < wizard_pos < cards_pos

    assert 'data-i18n="modal.step1"' in modal
    assert 'data-i18n="modal.step2"' in modal
    assert 'data-i18n="modal.step3">工作流' in modal
    assert 'data-i18n="modal.step4">计算协议' in modal
    assert 'data-i18n="modal.step5"' in modal
    assert 'data-i18n="modal.step2">工作流' not in modal

    for key in (
        '"modal.step1": "1. 选择项目"',
        '"modal.step2": "2. 选择结构来源"',
        '"modal.step3": "3. 选择工作流"',
        '"modal.step4": "4. 计算协议"',
        '"modal.step5": "5. 资源设置并提交"',
        '"modal.step1": "1. Select Project"',
        '"modal.step2": "2. Choose Structure Source"',
        '"modal.step3": "3. Select Workflow"',
        '"modal.step4": "4. Calculation Protocol"',
        '"modal.step5": "5. Resources & Submit"',
    ):
        assert key in html, f"missing i18n entry: {key}"


def test_results_filter_targets_modal_project_not_top_filter() -> None:
    """任务结果 filter must follow the modal target project (plan §2).

    The old static "当前项目" option bound to the top selectedProjectId,
    so a user preparing submission to project A queried results of the
    top-filtered project B.
    """
    html = FRONTEND.read_text(encoding="utf-8")

    toolbar = html.split('id="results-project"', 1)[1].split("</select>", 1)[0]
    assert 'value="current"' not in toolbar
    assert "<option" not in toolbar

    assert "function resultsProjectTargetId()" in html
    assert "function renderResultsProjectOptions()" in html

    loader = html.split("async function loadStructureSources(", 1)[1].split("\nfunction ", 1)[0]
    assert "selectedProjectId" not in loader
    assert 'mode === "target"' in loader
    assert "resultsProjectTargetId()" in loader
    assert "all_projects=true" in loader

    change_block = html.split('modal-project-select").addEventListener("change"', 1)[1]
    change_block = change_block.split("});", 1)[0]
    assert "renderResultsProjectOptions()" in change_block
    assert 'projEl.value === "target"' in change_block
    assert "loadStructureSources(true)" in change_block


def test_results_rows_status_badges_project_labels_and_search_scope() -> None:
    """Status badges, project grouping/labels, and the widened search hay."""
    html = FRONTEND.read_text(encoding="utf-8")

    for key in (
        '"results.status_completed": "已完成"',
        '"results.status_failed_saved": "失败任务 · 已保存结构"',
        '"results.status_cancelled_saved": "已取消任务 · 已保存结构"',
        '"results.cross_project": "来源：项目 {source} → 新任务：项目 {target}"',
        '"results.status_completed": "Completed"',
        '"results.status_failed_saved": "Failed · saved structures"',
        '"results.status_cancelled_saved": "Cancelled · saved structures"',
        '"results.cross_project": "Source: project {source} → new job: project {target}"',
    ):
        assert key in html, f"missing i18n entry: {key}"

    assert "function resultStatusBadge(src)" in html
    assert 'if (!status) return "";' in html
    assert "badge failed" in html
    assert "badge cancelled" in html

    assert "results-group-label" in html
    assert "function resultSourceRowMarkup(src, showProject)" in html

    render_body = html.split("function renderResultsList()", 1)[1].split("\nfunction ", 1)[0]
    assert 'mode === "all"' in render_body
    assert "projectNameOf(src.project_id)" in render_body
    assert "(src.candidate_id || \"\")" in render_body
    assert "(src.job_status || \"\")" in render_body

    assert "function updateResultsCrossHint(" in html
    assert 'id="results-cross-hint"' in html


def test_structure_source_ref_records_project_provenance() -> None:
    """source_ref must carry project_id/job_status so cross-project loads stay traceable."""
    html = FRONTEND.read_text(encoding="utf-8")

    ref_body = html.split("source_ref: {", 1)[1].split("}", 1)[0]
    assert "project_id" in ref_body
    assert "job_status" in ref_body
    assert "checksum" in ref_body

    loader = html.split("async function loadStructureSource(", 1)[1].split("\nfunction ", 1)[0]
    assert "body.project_id" in loader
    assert "body.job_status" in loader
    assert "results.cross_project" in loader


def test_energy_viewer_refactor_dom_contracts() -> None:
    """Structural contracts for the energy-viewer-denoise-candidate-card refactor.

    These assertions guard the new DOM structure introduced across Todos 1-10:
    candidate band, candidate card, hit layers, removed elements, nice ticks,
    unified chart core, and responsive layout.
    """
    html = FRONTEND.read_text(encoding="utf-8")

    assert "energy-candidate-band" in html, "Band container class missing"
    assert 'data-energy-candidate-jump="' in html, "Chip jump attribute missing"
    assert 'data-energy-candidate-expand="' in html, "Overflow expand attribute missing"
    assert "energy-band-chip-hit" in html, "Chip hit target class missing"

    assert "energy-candidate-card" in html, "Card container class missing"
    assert 'data-energy-card-state="' in html, "Card state attribute missing"
    assert "energy-card-type-btn" in html, "Card type button class missing"
    assert 'data-energy-action="card-cancel"' in html, "Card cancel action missing"

    assert "energy-point-hit" in html, "Energy point hit class missing"
    assert "energy-point-group" in html, "Energy point group class missing"
    assert "optimization-point-hit" in html, "Optimization point hit class missing"

    assert ".energy-marker-max" not in html, ".energy-marker-max CSS still present"
    assert "energy-role-row" not in html, ".energy-role-row still present"
    assert 'type !== "maximum"' in html or "type !== 'maximum'" in html, (
        "Defensive maximum-type filter missing"
    )

    assert "function energyChartBuildSvg(cfg)" in html, "Unified chart core missing"
    assert "function energyChartNiceTicks(" in html, "Nice ticks function missing"

    assert "clamp(340px, 24vw, 380px)" in html, "Right column clamp width missing"
    assert "structureFraming" in html, "Structure framing state missing"
    assert "scheduleViewerFraming(" in html, "Shared framing scheduler missing"
    assert "data-expandable" in html, "Expandable field attribute missing"


def test_viewer_framing_shared_helper_call_order() -> None:
    html = FRONTEND.read_text(encoding="utf-8")

    marker = "function frame3DViewer("
    assert html.count(marker) == 1, "Shared framing helper must be defined once"
    body = html.split(marker, 1)[1].split("\nfunction ", 1)[0]
    for token in (".resize()", ".center(", ".zoomTo(", ".render()"):
        assert token in body, f"Shared framing helper missing {token}"
    assert body.find(".resize()") < body.find(".center(") < body.find(".zoomTo(") < body.find(
        ".render()"
    ), "Shared framing helper call order changed"
    assert "preserveView" in body, "Shared framing helper must preserve user view"


def test_viewer_framing_container_stability_wait() -> None:
    html = FRONTEND.read_text(encoding="utf-8")

    wait_marker = "function waitForContainerStable("
    schedule_marker = "function scheduleViewerFraming("
    assert html.count(wait_marker) == 1, "Container stability helper must be defined once"
    assert html.count(schedule_marker) == 1, "Framing scheduler must be defined once"

    wait_body = html.split(wait_marker, 1)[1].split("\nfunction ", 1)[0]
    for token in ("clientWidth", "clientHeight", "requestAnimationFrame"):
        assert token in wait_body, f"Container stability helper missing {token}"

    schedule_body = html.split(schedule_marker, 1)[1].split("\nfunction ", 1)[0]
    for token in (
        "waitForContainerStable(",
        "frame3DViewer(",
        "ResizeObserver",
        "lastW",
        "lastH",
        "pending",
        "settled",
        ".resize()",
        ".render()",
    ):
        assert token in schedule_body, f"Framing scheduler missing {token}"


def test_all_viewer_load_sites_use_shared_framing() -> None:
    html = FRONTEND.read_text(encoding="utf-8")
    load_sites = (
        "renderMolDoc",
        "energyGraphLoadFrameGeometry",
        "renderPreviewStructure3D",
        "renderReactionChanges3D",
        "s2scanLoadPreview",
        "s2scanInitCoordinateViewer",
        "s2scanLoadResultFrame",
    )

    for name in load_sites:
        marker = f"function {name}("
        assert html.count(marker) == 1, f"{name} must be defined once"
        body = html.split(marker, 1)[1]
        body = body.split("\nasync function ", 1)[0].split("\nfunction ", 1)[0]
        assert "scheduleViewerFraming(" in body, f"{name} bypasses shared framing"
        assert ".zoomTo();" not in body, f"{name} contains a bare zoomTo call"


def test_energy_viewer_same_frame_guard_allows_relayout_reframe() -> None:
    html = FRONTEND.read_text(encoding="utf-8")
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


def test_viewer_framing_disposed_in_cleanup_paths() -> None:
    html = FRONTEND.read_text(encoding="utf-8")
    cleanup_paths = (
        "energyGraphDestroyViewer",
        "clearViewer",
        "s2scanClose",
        "s2scanResetState",
    )

    for name in cleanup_paths:
        marker = f"function {name}("
        assert html.count(marker) == 1, f"{name} must be defined once"
        body = html.split(marker, 1)[1].split("\nfunction ", 1)[0]
        assert "disposeViewerFraming(" in body, f"{name} omits framing cleanup"


def test_frontend_script_has_no_syntax_errors() -> None:
    """Regression guard: the main <script> block must pass node --check."""
    if not shutil.which("node"):
        import pytest
        pytest.skip("node not available")

    html = FRONTEND.read_text(encoding="utf-8")
    script_start = html.find("<script>")
    script_end = html.rfind("</script>")
    assert script_start != -1 and script_end != -1, "Could not find <script> block"

    js_start = html.index("\n", script_start) + 1
    js_content = html[js_start:script_end]

    with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False) as f:
        _ = f.write(js_content)
        _ = f.flush()
        result = subprocess.run(
            ["node", "--check", f.name],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, (
            f"node --check failed:\n{result.stderr}"
        )


def test_frontend_files_exist_and_readable() -> None:
    for path in FRONTEND_FILES:
        assert path.exists(), f"Frontend file missing: {path}"
        content = path.read_text(encoding="utf-8")
        assert len(content) > 0, f"Frontend file is empty: {path}"


def test_structure_viewer_js_has_namespace() -> None:
    js = (FRONTEND_JS_DIR / "structure_viewer.js").read_text(encoding="utf-8")
    assert "window.ACPStructureViewer" in js
    assert "loadStructureViewer" in js
    assert "selectEntry" in js
    assert "refreshIfChanged" in js
    assert "loadSelectedGeometry" in js
    assert "_applyCatalogResponse" in js


def test_structure_editor_js_has_namespace() -> None:
    js = (FRONTEND_JS_DIR / "structure_editor.js").read_text(encoding="utf-8")
    assert "window.ACPStructureEditor" in js


def test_vibration_viewer_js_has_namespace() -> None:
    js = (FRONTEND_JS_DIR / "vibration_viewer.js").read_text(encoding="utf-8")
    assert "window.ACPVibrationViewer" in js


def test_v2_html_loads_structure_viewer_modules() -> None:
    html = FRONTEND.read_text(encoding="utf-8")
    assert '<link rel="stylesheet" href="css/structure_viewer.css">' in html
    assert '<script src="js/structure_viewer.js"></script>' in html
    assert '<script src="js/structure_editor.js"></script>' in html
    assert '<script src="js/vibration_viewer.js"></script>' in html


@pytest.mark.parametrize(
    "js_file",
    [
        "structure_viewer.js",
        "structure_editor.js",
        "vibration_viewer.js",
    ],
)
def test_extracted_js_passes_node_check(js_file: str) -> None:
    if not shutil.which("node"):
        import pytest as _pytest
        _pytest.skip("node not available")

    path = FRONTEND_JS_DIR / js_file
    result = subprocess.run(
        ["node", "--check", str(path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"node --check {js_file} failed:\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# Todo 15: structureViewerState store + payload fetch + revision/token
# ---------------------------------------------------------------------------

def test_structure_viewer_state_store_contract() -> None:
    """Contract: state fields, AbortController, requestToken, _fetchImpl exist."""
    js = (FRONTEND_JS_DIR / "structure_viewer.js").read_text(encoding="utf-8")

    for field in (
        "jobId", "payload", "revision", "selectedEntryId",
        "selectionOrigin", "selectionToken", "dirty", "editState",
        "requestToken", "availability", "newerAvailable",
    ):
        assert field in js, f"state field {field!r} missing from structure_viewer.js"

    assert "AbortController" in js, "AbortController usage missing"
    assert "requestToken" in js, "requestToken guard missing"
    assert "_fetchImpl" in js, "_fetchImpl injectable missing"
    assert "_applyCatalogResponse" in js, "_applyCatalogResponse pure helper missing"
    assert "_catalogUrl" in js, "_catalogUrl helper missing"
    assert "/structure-viewer" in js, "catalog endpoint path missing"
    assert "selectionToken" in js, "selectionToken guard missing"


def test_structure_viewer_node_logic_stale_response_discarded() -> None:
    """Node logic: job A slow resolves after job B -> B wins."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    js_path = FRONTEND_JS_DIR / "structure_viewer.js"
    script = textwrap.dedent("""\
        var window = { fetch: null };
        var AbortController = class { constructor() { this.signal = null; } abort() {} };
        // Load the module (IIFE reads window, attaches namespace)
        require(JS_PATH);

        var ns = window.ACPStructureViewer;
        var state = ns.state;

        // Inject a fake fetch that returns controlled promises.
        var resolveA, resolveB;
        ns._fetchImpl = function(url) {
            if (url.indexOf("jobA") >= 0) {
                return new Promise(function(r) { resolveA = r; });
            }
            return new Promise(function(r) { resolveB = r; });
        };

        // Start load A, then load B (B supersedes A).
        ns.loadStructureViewer("jobA");
        var tokenAfterA = state.requestToken;
        ns.loadStructureViewer("jobB");
        var tokenAfterB = state.requestToken;

        // B's token is newer.
        if (tokenAfterB <= tokenAfterA) {
            console.error("FAIL: requestToken must increase");
            process.exit(1);
        }

        // Resolve B first (as it should in real usage).
        resolveB({ ok: true, status: 200, statusText: "OK", json: function() {
            return Promise.resolve({
                schema_version: "structure_viewer_v1",
                revision: "revB",
                availability: "ready",
                default_entry_id: "e2",
                groups: [], entries: [], warnings: []
            });
        }});

        // Now resolve A (stale — should be discarded).
        resolveA({ ok: true, status: 200, statusText: "OK", json: function() {
            return Promise.resolve({
                schema_version: "structure_viewer_v1",
                revision: "revA",
                availability: "ready",
                default_entry_id: "e1",
                groups: [], entries: [], warnings: []
            });
        }});

        // Give promises time to settle.
        setTimeout(function() {
            if (state.revision !== "revB") {
                console.error("FAIL: expected revB, got " + state.revision);
                process.exit(1);
            }
            if (state.selectedEntryId !== "e2") {
                console.error("FAIL: expected e2, got " + state.selectedEntryId);
                process.exit(1);
            }
            console.log("PASS");
        }, 50);
    """).replace("JS_PATH", json.dumps(str(js_path)))

    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, (
        f"Node logic test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_structure_viewer_node_logic_dirty_guard() -> None:
    """Node logic: dirty=true -> refreshIfChanged sets newerAvailable, not payload."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    js_path = FRONTEND_JS_DIR / "structure_viewer.js"
    script = textwrap.dedent("""\
        var window = { fetch: null };
        var AbortController = class { constructor() { this.signal = null; } abort() {} };
        require(JS_PATH);

        var ns = window.ACPStructureViewer;
        var state = ns.state;

        // Seed state with a loaded payload.
        ns._fetchImpl = function(url) {
            return Promise.resolve({ ok: true, status: 200, statusText: "OK", json: function() {
                return Promise.resolve({
                    schema_version: "structure_viewer_v1",
                    revision: "rev1",
                    availability: "ready",
                    default_entry_id: "e1",
                    groups: [], entries: [{id:"e1", group_id:"g1"}], warnings: []
                });
            }});
        };

        ns.loadStructureViewer("jobX").then(function() {
            if (state.revision !== "rev1") {
                console.error("FAIL: initial load revision");
                process.exit(1);
            }
            // Mark dirty (simulating Wave 6 edits).
            state.dirty = true;

            // Now simulate a server-side revision change.
            ns._fetchImpl = function(url) {
                return Promise.resolve({ ok: true, status: 200, statusText: "OK", json: function() {
                    return Promise.resolve({
                        schema_version: "structure_viewer_v1",
                        revision: "rev2",
                        availability: "ready",
                        default_entry_id: "e2",
                        groups: [], entries: [{id:"e2", group_id:"g1"}], warnings: []
                    });
                }});
            };

            return ns.refreshIfChanged();
        }).then(function() {
            if (!state.newerAvailable) {
                console.error("FAIL: newerAvailable should be true");
                process.exit(1);
            }
            if (state.payload && state.payload.revision === "rev2") {
                console.error("FAIL: payload should NOT be replaced when dirty");
                process.exit(1);
            }
            console.log("PASS");
        }).catch(function(e) {
            console.error("FAIL: unexpected error", e);
            process.exit(1);
        });
    """).replace("JS_PATH", json.dumps(str(js_path)))

    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, (
        f"Node dirty-guard test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_structure_viewer_node_logic_select_entry_token() -> None:
    """Node logic: selectEntry increments selectionToken each call."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    js_path = FRONTEND_JS_DIR / "structure_viewer.js"
    script = textwrap.dedent("""\
        var window = { fetch: null };
        var AbortController = class { constructor() { this.signal = null; } abort() {} };
        require(JS_PATH);

        var ns = window.ACPStructureViewer;
        var state = ns.state;

        var t0 = state.selectionToken;
        var t1 = ns.selectEntry("e1", "user");
        var t2 = ns.selectEntry("e2", "energy_graph");

        if (t1 !== t0 + 1) {
            console.error("FAIL: first selectEntry token should be " + (t0+1) + ", got " + t1);
            process.exit(1);
        }
        if (t2 !== t1 + 1) {
            console.error("FAIL: second selectEntry token should be " + (t1+1) + ", got " + t2);
            process.exit(1);
        }
        if (state.selectedEntryId !== "e2") {
            console.error("FAIL: selectedEntryId should be e2");
            process.exit(1);
        }
        if (state.selectionOrigin !== "energy_graph") {
            console.error("FAIL: selectionOrigin should be energy_graph");
            process.exit(1);
        }
        console.log("PASS");
    """).replace("JS_PATH", json.dumps(str(js_path)))

    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, (
        f"Node selectEntry token test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


# ---------------------------------------------------------------------------
# Todo 16: structure list + inspector + narrow-screen drawers
# ---------------------------------------------------------------------------

def test_structure_list_inspector_html_contract() -> None:
    """HTML contract: list/inspector/playback container ids present inside structure tab."""
    html = FRONTEND.read_text(encoding="utf-8")

    assert 'id="structure-list-panel"' in html
    assert 'id="structure-inspector-panel"' in html
    assert 'id="structure-playback-bar"' in html
    assert 'id="sv-layout"' in html
    assert 'id="sv-list-header"' in html
    assert 'id="sv-list-body"' in html
    assert 'id="sv-inspector-header"' in html
    assert 'id="sv-inspector-body"' in html

    # viewer-3d must still exist exactly once (no new canvas)
    assert html.count('id="viewer-3d"') == 1


def test_structure_viewer_render_functions_exist() -> None:
    """JS contract: renderStructureViewer, renderInspector, _esc, STR exist."""
    js = FRONTEND_JS_DIR / "structure_viewer.js"
    content = js.read_text(encoding="utf-8")

    assert "renderStructureViewer" in content
    assert "renderInspector" in content
    assert "_esc" in content
    assert "STR" in content
    assert "toggleListDrawer" in content
    assert "toggleInspectorDrawer" in content
    assert "closeAllDrawers" in content


def test_structure_viewer_badge_chips_include_unconfirmed_legacy() -> None:
    """JS contract: badge rendering includes 未确认/兼容模式 classes."""
    js = FRONTEND_JS_DIR / "structure_viewer.js"
    content = js.read_text(encoding="utf-8")

    assert "sv-badge-unconfirmed" in content
    assert "sv-badge-legacy" in content
    assert "sv-badge-failed-frame" in content
    assert "sv-badge-role" in content
    assert "sv-badge-rank" in content


def test_structure_viewer_group_hide_logic() -> None:
    """JS contract: single-entry payloads hide the list panel."""
    js = FRONTEND_JS_DIR / "structure_viewer.js"
    content = js.read_text(encoding="utf-8")

    assert "sv-list-hidden" in content
    assert "entries.length > 1" in content or "entries.length>1" in content


def test_structure_viewer_availability_pending_notice() -> None:
    """JS contract: availability pending_fetch shows notice string."""
    js = FRONTEND_JS_DIR / "structure_viewer.js"
    content = js.read_text(encoding="utf-8")

    assert "pending_fetch" in content
    assert "\u7b49\u5f85\u8fdc\u7a0b\u7ed3\u679c" in content  # 等待远程结果


def test_structure_viewer_source_kind_labels() -> None:
    """JS contract: source.kind localized labels map."""
    js = FRONTEND_JS_DIR / "structure_viewer.js"
    content = js.read_text(encoding="utf-8")

    assert "\u6b63\u5f0f\u7ed3\u679c" in content      # 正式结果
    assert "\u81ea\u52a8\u63a8\u8350" in content        # 自动推荐
    assert "\u4eba\u5de5\u786e\u8ba4" in content        # 人工确认
    assert "\u6700\u540e\u6709\u6548\u5468\u671f" in content  # 最后有效周期
    assert "\u8ba1\u7b97\u8f93\u5165" in content        # 计算输入
    assert "\u624b\u52a8\u6587\u4ef6" in content        # 手动文件


def test_structure_viewer_css_drawer_media_queries() -> None:
    """CSS contract: drawer media queries present."""
    css = FRONTEND_CSS_DIR / "structure_viewer.css"
    content = css.read_text(encoding="utf-8")

    assert "@media" in content
    assert "sv-drawer-open" in content
    assert "sv-drawer-overlay" in content
    assert "1100px" in content
    assert "860px" in content


def test_structure_viewer_css_three_column_grid() -> None:
    """CSS contract: three-column grid layout for sv-layout."""
    css = FRONTEND_CSS_DIR / "structure_viewer.css"
    content = css.read_text(encoding="utf-8")

    assert "grid-template-columns" in content
    assert "sv-layout" in content
    assert "sv-list-panel" in content
    assert "sv-inspector-panel" in content
    assert "sv-canvas-col" in content


# ---------------------------------------------------------------------------
# Todo 17: auto-load on job select + manual_file injection + dirty guard
# ---------------------------------------------------------------------------

def test_selectjob_calls_onjobselected() -> None:
    """Contract: selectJob must call ACPStructureViewer.onJobSelected."""
    html = FRONTEND.read_text(encoding="utf-8")

    select_job = html.split("async function selectJob(jobId)", 1)[1]
    select_job = select_job.split("\nasync function ", 1)[0]

    assert "ACPStructureViewer.onJobSelected" in select_job, (
        "selectJob must call ACPStructureViewer.onJobSelected"
    )
    assert 'window.ACPStructureViewer && window.ACPStructureViewer.onJobSelected' in select_job, (
        "onJobSelected call must be guarded by existence check"
    )


def test_polling_calls_refreshifchanged() -> None:
    """Contract: summaryPoll must call ACPStructureViewer.refreshIfChanged."""
    html = FRONTEND.read_text(encoding="utf-8")

    summary_poll = html.split("async function summaryPoll()", 1)[1]
    summary_poll = summary_poll.split("\n    } catch", 1)[0]

    assert "ACPStructureViewer.refreshIfChanged" in summary_poll, (
        "summaryPoll must call ACPStructureViewer.refreshIfChanged"
    )


def test_file_tree_xyz_click_calls_injectmanualentry() -> None:
    """Contract: file-tree .xyz click must call injectManualEntry."""
    html = FRONTEND.read_text(encoding="utf-8")

    xyz_block = html.split('node.path.endsWith(".xyz")', 1)[1]
    xyz_block = xyz_block.split("return;", 1)[0]

    assert "ACPStructureViewer.injectManualEntry" in xyz_block, (
        "File-tree .xyz click must call ACPStructureViewer.injectManualEntry"
    )


def test_structure_viewer_js_has_todo17_functions() -> None:
    """JS contract: onJobSelected, injectManualEntry, loadSelectedGeometry exist."""
    js = FRONTEND_JS_DIR / "structure_viewer.js"
    content = js.read_text(encoding="utf-8")

    assert "onJobSelected" in content
    assert "injectManualEntry" in content
    assert "loadSelectedGeometry" in content
    assert "_sha256hex" in content
    assert "_manualEntryId" in content
    assert "pendingGeometryRetry" in content
    assert "geometryLoadedFor" in content


def test_structure_viewer_node_manual_entry_id_matches_python() -> None:
    """Node logic: manual_entry_id matches Python sha256[:12] for sample paths."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    js_path = FRONTEND_JS_DIR / "structure_viewer.js"
    script = textwrap.dedent("""\
        var window = { fetch: null };
        require(JS_PATH);
        var ns = window.ACPStructureViewer;

        var cases = [
          ["RESULT/confsearch/conformers/0001.xyz", "5072cff2b42b"],
          ["WORK/03_OPT/batch/opt_item_001/optimize/cycles/cycle_0001.xyz", "092f7b2db5a3"],
        ];
        for (var i = 0; i < cases.length; i++) {
          var path = cases[i][0];
          var expected = cases[i][1];
          var got = ns._manualEntryId(path);
          if (got !== "manual_" + expected) {
            console.error("FAIL: _manualEntryId(" + path + ") = " + got + ", expected manual_" + expected);
            process.exit(1);
          }
        }
        console.log("PASS");
    """).replace("JS_PATH", json.dumps(str(js_path)))

    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, (
        f"Node manual_entry_id test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_structure_viewer_node_sha256_vectors() -> None:
    """Node logic: _sha256hex produces correct digests for known inputs."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    js_path = FRONTEND_JS_DIR / "structure_viewer.js"

    # Pre-computed expected digests
    import hashlib
    test_vectors = [
        ("", hashlib.sha256(b"").hexdigest()),
        ("abc", hashlib.sha256(b"abc").hexdigest()),
        ("hello world", hashlib.sha256(b"hello world").hexdigest()),
    ]

    vectors_js = json.dumps(test_vectors)
    script = textwrap.dedent("""\
        var window = { fetch: null };
        require(JS_PATH);
        var ns = window.ACPStructureViewer;
        var vectors = VECTORS;
        for (var i = 0; i < vectors.length; i++) {
          var input = vectors[i][0];
          var expected = vectors[i][1];
          var got = ns._sha256hex(input);
          if (got !== expected) {
            console.error("FAIL: _sha256hex(" + JSON.stringify(input) + ") = " + got + ", expected " + expected);
            process.exit(1);
          }
        }
        console.log("PASS");
    """).replace("JS_PATH", json.dumps(str(js_path))).replace("VECTORS", vectors_js)

    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, (
        f"Node sha256 test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_structure_viewer_node_dirty_guard_no_replace() -> None:
    """Node logic: dirty=true + poll -> coordinates untouched, newerAvailable set."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    js_path = FRONTEND_JS_DIR / "structure_viewer.js"
    script = textwrap.dedent("""\
        var window = { fetch: null };
        require(JS_PATH);
        var ns = window.ACPStructureViewer;
        var state = ns.state;

        // Simulate a loaded payload
        state.jobId = "test-job";
        state.revision = "rev1";
        state.dirty = true;
        state.payload = { revision: "rev1", entries: [], groups: [] };

        // Mock fetch to return a different revision
        var callCount = 0;
        ns._fetchImpl = function(url, opts) {
          callCount++;
          return Promise.resolve({
            ok: true,
            json: function() {
              return Promise.resolve({
                schema_version: "structure_viewer_v1",
                revision: "rev2",
                availability: "ready",
                default_entry_id: null,
                groups: [],
                entries: [],
                warnings: []
              });
            }
          });
        };

        ns.refreshIfChanged().then(function() {
          if (!state.newerAvailable) {
            console.error("FAIL: newerAvailable should be true when dirty");
            process.exit(1);
          }
          if (state.payload.revision !== "rev1") {
            console.error("FAIL: payload revision should not change when dirty");
            process.exit(1);
          }
          console.log("PASS");
        }).catch(function(e) {
          console.error("FAIL: unexpected error", e);
          process.exit(1);
        });
    """).replace("JS_PATH", json.dumps(str(js_path)))

    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, (
        f"Node dirty-guard test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_structure_viewer_node_geometry_409_retry() -> None:
    """Node logic: geometry 409 -> pendingGeometryRetry flag set."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    js_path = FRONTEND_JS_DIR / "structure_viewer.js"
    script = textwrap.dedent("""\
        var window = { fetch: null };
        require(JS_PATH);
        var ns = window.ACPStructureViewer;
        var state = ns.state;

        // Set up state with a selected entry
        state.jobId = "test-job";
        state.selectedEntryId = "entry1";
        state.selectionToken = 1;
        state.payload = {
          entries: [{
            id: "entry1",
            geometry: { endpoint: "/api/v1/jobs/test/structure-viewer/entries/entry1/geometry", format: "xyz" }
          }]
        };

        var fetchCalls = 0;
        ns._fetchImpl = function(url, opts) {
          fetchCalls++;
          if (fetchCalls === 1) {
            // First call: 409
            return Promise.resolve({ status: 409, ok: false });
          }
          // Retry call with fetch=1: also 409
          return Promise.resolve({ status: 409, ok: false });
        };

        ns.loadSelectedGeometry().then(function() {
          if (!state.pendingGeometryRetry) {
            console.error("FAIL: pendingGeometryRetry should be true after 409");
            process.exit(1);
          }
          if (fetchCalls !== 2) {
            console.error("FAIL: expected 2 fetch calls (initial + retry), got " + fetchCalls);
            process.exit(1);
          }
          console.log("PASS");
        }).catch(function(e) {
          console.error("FAIL: unexpected error", e);
          process.exit(1);
        });
    """).replace("JS_PATH", json.dumps(str(js_path)))

    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, (
        f"Node geometry 409 test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_structure_viewer_svload_xyz_bridge_exists() -> None:
    """Contract: _svLoadXyzToViewer bridge function exists in HTML."""
    html = FRONTEND.read_text(encoding="utf-8")

    assert "window._svLoadXyzToViewer" in html, (
        "Bridge function _svLoadXyzToViewer must exist in HTML"
    )
    assert "parseMultiFrameXYZ" in html, (
        "_svLoadXyzToViewer must use parseMultiFrameXYZ"
    )


# ---------------------------------------------------------------------------
# Todo 18: energy graph -> structure viewer one-way push
# ---------------------------------------------------------------------------

def test_energy_selectframe_pushes_to_structure_viewer() -> None:
    """Contract: energyGraphSelectFrame calls onEnergyNodeSelected."""
    html = FRONTEND.read_text(encoding="utf-8")

    select_frame = html.split("function energyGraphSelectFrame(frameIndex, origin)", 1)[1]
    select_frame = select_frame.split("\nfunction ", 1)[0]

    assert "onEnergyNodeSelected" in select_frame, (
        "energyGraphSelectFrame must push selection to structure viewer"
    )
    assert 'energyGraphState.jobId' in select_frame, (
        "Push must pass jobId from energyGraphState"
    )


def test_structure_viewer_js_has_energy_push_api() -> None:
    """Contract: onEnergyNodeSelected + _entryIdFromEnergyNode exist."""
    js = FRONTEND_JS_DIR / "structure_viewer.js"
    content = js.read_text(encoding="utf-8")

    assert "onEnergyNodeSelected" in content
    assert "_entryIdFromEnergyNode" in content
    assert '"energy_graph"' in content, (
        'Must use "energy_graph" origin for energy-graph push'
    )


def test_structure_viewer_energy_push_forbidden_ids_still_absent() -> None:
    """Contract: forbidden identifiers from anti-pattern #29 must stay absent."""
    html = FRONTEND.read_text(encoding="utf-8")

    assert "energyGraphConfirmAndBatch" not in html, (
        "energyGraphConfirmAndBatch must stay absent (anti-pattern #29)"
    )
    assert 'data-energy-action="to-batch"' not in html, (
        "to-batch action must stay absent (anti-pattern #29)"
    )


def test_structure_viewer_node_energy_stale_job_guard() -> None:
    """Node logic: onEnergyNodeSelected ignores mismatched jobId."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    js_path = FRONTEND_JS_DIR / "structure_viewer.js"
    script = textwrap.dedent("""\
        var window = { fetch: null };
        require(JS_PATH);
        var ns = window.ACPStructureViewer;
        var state = ns.state;

        state.jobId = "job-A";
        state.payload = { entries: [{ id: "conf_1" }], groups: [] };
        state.selectedEntryId = "conf_1";

        var result = ns.onEnergyNodeSelected("job-B", { kind: "conformer", key: "1" });
        if (result !== null) {
            console.error("FAIL: stale job should be ignored, got " + result);
            process.exit(1);
        }
        if (state.selectedEntryId !== "conf_1") {
            console.error("FAIL: selectedEntryId should not change");
            process.exit(1);
        }
        console.log("PASS");
    """).replace("JS_PATH", json.dumps(str(js_path)))

    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, (
        f"Stale-job guard test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_structure_viewer_node_energy_entry_id_mapping() -> None:
    """Node logic: _entryIdFromEnergyNode maps kind/key to correct ids."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    js_path = FRONTEND_JS_DIR / "structure_viewer.js"
    script = textwrap.dedent("""\
        var window = { fetch: null };
        require(JS_PATH);
        var ns = window.ACPStructureViewer;
        var map = ns._entryIdFromEnergyNode;

        var cases = [
          [{ entryId: "conf_0001" }, "conf_0001"],
          [{ kind: "conformer", key: "0001" }, "conf_0001"],
          [{ kind: "batch", key: "opt_item_001" }, "batch_opt_item_001"],
          [{ kind: "scan", frameIndex: 5 }, "scan_frame_5"],
          [{ kind: "pes", candidateId: "ts_frame_005" }, "pes_ts_frame_005"],
          [{ kind: "simple", stepKind: "optimize" }, "simple_optimize"],
          [{ kind: "irc", endpoint: "forward", frameIndex: 0 }, "irc_forward_0"],
          [{ kind: "optimization", frameIndex: 12 }, null],
        ];
        for (var i = 0; i < cases.length; i++) {
          var input = cases[i][0];
          var expected = cases[i][1];
          var got = map(input);
          if (got !== expected) {
            console.error("FAIL: _entryIdFromEnergyNode(" + JSON.stringify(input) + ") = " + got + ", expected " + expected);
            process.exit(1);
          }
        }
        console.log("PASS");
    """).replace("JS_PATH", json.dumps(str(js_path)))

    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, (
        f"Entry-id mapping test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_structure_viewer_node_energy_token_guard() -> None:
    """Node logic: rapid selections — older token's effects are superseded."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    js_path = FRONTEND_JS_DIR / "structure_viewer.js"
    script = textwrap.dedent("""\
        var window = { fetch: null };
        require(JS_PATH);
        var ns = window.ACPStructureViewer;
        var state = ns.state;

        state.jobId = "job-1";
        state.payload = {
          entries: [
            { id: "conf_1", group_id: "", label: "C1", role: "minimum", status: "completed",
              geometry: { endpoint: "/e1", format: "xyz" }, source: { kind: "formal_result" },
              badges: [], vibrations: { available: false } },
            { id: "conf_2", group_id: "", label: "C2", role: "minimum", status: "completed",
              geometry: { endpoint: "/e2", format: "xyz" }, source: { kind: "formal_result" },
              badges: [], vibrations: { available: false } },
          ],
          groups: []
        };

        // First push
        var tok1 = ns.onEnergyNodeSelected("job-1", { kind: "conformer", key: "1" });
        // Second push immediately after
        var tok2 = ns.onEnergyNodeSelected("job-1", { kind: "conformer", key: "2" });

        if (tok2 !== tok1 + 1) {
          console.error("FAIL: token should increment, got tok1=" + tok1 + " tok2=" + tok2);
          process.exit(1);
        }
        if (state.selectedEntryId !== "conf_2") {
          console.error("FAIL: selectedEntryId should be conf_2, got " + state.selectedEntryId);
          process.exit(1);
        }
        if (state.selectionOrigin !== "energy_graph") {
          console.error("FAIL: selectionOrigin should be energy_graph, got " + state.selectionOrigin);
          process.exit(1);
        }
        console.log("PASS");
    """).replace("JS_PATH", json.dumps(str(js_path)))

    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, (
        f"Token guard test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_structure_viewer_node_energy_transient_entry() -> None:
    """Node logic: unknown optimization frame creates a transient entry."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    js_path = FRONTEND_JS_DIR / "structure_viewer.js"
    script = textwrap.dedent("""\
        var window = { fetch: null };
        require(JS_PATH);
        var ns = window.ACPStructureViewer;
        var state = ns.state;

        state.jobId = "job-1";
        state.payload = {
          entries: [],
          groups: []
        };

        var tok = ns.onEnergyNodeSelected("job-1", {
          kind: "optimization",
          frameIndex: 12,
          geometryRef: "WORK/03_OPT/optimization_trajectory.json",
          label: "Cycle 12"
        });

        if (!tok) {
          console.error("FAIL: should return a token");
          process.exit(1);
        }
        if (state.selectedEntryId !== "transient_opt_12") {
          console.error("FAIL: selectedEntryId should be transient_opt_12, got " + state.selectedEntryId);
          process.exit(1);
        }
        // Verify transient entry was injected
        var entries = state.payload.entries;
        var found = false;
        for (var i = 0; i < entries.length; i++) {
          if (entries[i].id === "transient_opt_12") { found = true; break; }
        }
        if (!found) {
          console.error("FAIL: transient entry not injected into payload");
          process.exit(1);
        }
        console.log("PASS");
    """).replace("JS_PATH", json.dumps(str(js_path)))

    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, (
        f"Transient entry test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


# ---------------------------------------------------------------------------
# Todo 19: Phase-A contract tests + i18n completeness
# ---------------------------------------------------------------------------
# This section consolidates all phase-A structural locks for the structure
# viewer.  Every assertion here guards a contract established in todos 13-18.
# Removing or weakening any assertion turns the corresponding test red.

_STRUCTURE_I18N_KEY_RE = re.compile(r'"(structure\.[^"]+)":')


def _extract_structure_keys(html: str, block_re: re.Pattern[str]) -> set[str]:  # type: ignore[type-arg]
    """Extract structure.* i18n keys from a single locale block."""
    m = block_re.search(html)
    if not m:
        return set()
    return set(_STRUCTURE_I18N_KEY_RE.findall(m.group(1)))


def test_phase_a_structure_tab_contract() -> None:
    """Phase-A lock: single structure tab, conformers/3d absent (todo 14)."""
    html = FRONTEND.read_text(encoding="utf-8")

    assert 'data-tab="conformers"' not in html, "conformers tab must be removed"
    assert 'data-tab="3d"' not in html, "3d tab must be renamed to structure"
    assert 'data-tab="structure"' in html, "structure tab must exist"
    assert '>结构查看器</button>' in html
    assert '"tab.structure": "结构查看器"' in html
    assert '"tab.structure": "Structure Viewer"' in html
    assert '"tab.conformers"' not in html
    assert '"tab.3d"' not in html
    # Other tabs must remain
    assert 'data-tab="path"' in html
    assert 'data-tab="energy"' in html
    assert 'data-tab="wavefunction"' in html
    # Compat mapping
    assert 'tab === "3d" || tab === "conformers"' in html or 'tab === "conformers" || tab === "3d"' in html


def test_phase_a_store_api_names() -> None:
    """Phase-A lock: structure_viewer.js exposes all required API names (todos 15-18)."""
    js = FRONTEND_JS_DIR / "structure_viewer.js"
    content = js.read_text(encoding="utf-8")

    required = [
        "structureViewerState",
        "loadStructureViewer",
        "selectEntry",
        "refreshIfChanged",
        "onJobSelected",
        "injectManualEntry",
        "onEnergyNodeSelected",
        "loadSelectedGeometry",
    ]
    for name in required:
        assert name in content, f"{name} missing from structure_viewer.js namespace"


def test_phase_a_selectjob_autoload_call() -> None:
    """Phase-A lock: selectJob calls onJobSelected which triggers loadStructureViewer (todo 17)."""
    html = FRONTEND.read_text(encoding="utf-8")

    select_job = html.split("async function selectJob(jobId)", 1)[1].split("\nasync function ", 1)[0]
    assert "onJobSelected" in select_job, (
        "selectJob must call ACPStructureViewer.onJobSelected"
    )


def test_phase_a_manual_file_injection_and_sha256_parity() -> None:
    """Phase-A lock: manual_file injection path + SHA-256 parity (todo 17)."""
    js = FRONTEND_JS_DIR / "structure_viewer.js"
    content = js.read_text(encoding="utf-8")

    assert "injectManualEntry" in content
    assert "_manualEntryId" in content
    assert '_sha256hex(relpath).slice(0, 12)' in content, (
        "manual entry id must use sha256(relpath)[:12]"
    )


def test_phase_a_energy_push_token_guard() -> None:
    """Phase-A lock: energy push uses selectionToken guard (todo 18)."""
    js = FRONTEND_JS_DIR / "structure_viewer.js"
    content = js.read_text(encoding="utf-8")

    assert "onEnergyNodeSelected" in content
    assert '"energy_graph"' in content, (
        'Must use "energy_graph" origin for energy-graph push'
    )


def test_phase_a_dirty_guard_branch() -> None:
    """Phase-A lock: dirty guard prevents coordinate replacement (todo 15)."""
    js = FRONTEND_JS_DIR / "structure_viewer.js"
    content = js.read_text(encoding="utf-8")

    assert "newerAvailable" in content
    assert "structure.newer_available" in content or "NEWER_AVAILABLE" in content


def test_phase_a_forbidden_identifiers_absent() -> None:
    """Phase-A lock: forbidden identifiers must stay absent (anti-pattern #29)."""
    html = FRONTEND.read_text(encoding="utf-8")

    assert "energyGraphConfirmAndBatch" not in html
    assert 'data-energy-action="to-batch"' not in html
    assert 'data-energy-action="lock-frame"' not in html
    assert "isFrameLocked" not in html
    assert "acp-frame-lock" not in html


def test_phase_a_i18n_structure_keys_complete_across_locales() -> None:
    """Phase-A lock: every structure.* key in zh-CN must also exist in en-US
    and vice-versa.

    A future edit that adds a structure key to only one locale will fail here.
    """
    html = FRONTEND.read_text(encoding="utf-8")

    zh_keys = _extract_structure_keys(html, _ZH_BLOCK_RE)
    en_keys = _extract_structure_keys(html, _EN_BLOCK_RE)

    assert zh_keys, "No structure.* keys found in zh-CN block"
    assert en_keys, "No structure.* keys found in en-US block"

    only_zh = zh_keys - en_keys
    only_en = en_keys - zh_keys
    assert not only_zh, f"Keys in zh-CN but missing from en-US: {sorted(only_zh)}"
    assert not only_en, f"Keys in en-US but missing from zh-CN: {sorted(only_en)}"


def test_phase_a_i18n_structure_keys_used_in_js_exist_in_both_locales() -> None:
    """Phase-A lock: every structure.* key referenced by _t() in
    structure_viewer.js must exist in both locale dictionaries."""
    html = FRONTEND.read_text(encoding="utf-8")
    js = FRONTEND_JS_DIR / "structure_viewer.js"
    js_content = js.read_text(encoding="utf-8")

    # Extract keys used in _t("structure.xxx", ...) calls
    # Filter out dynamic keys like "structure.source_kind." (empty suffix)
    t_call_re = re.compile(r'_t\("(structure\.[^"]+)"')
    used_keys = {k for k in t_call_re.findall(js_content) if not k.endswith(".")}

    zh_keys = _extract_structure_keys(html, _ZH_BLOCK_RE)
    en_keys = _extract_structure_keys(html, _EN_BLOCK_RE)

    missing_zh = used_keys - zh_keys
    missing_en = used_keys - en_keys
    assert not missing_zh, (
        f"structure.* keys used in JS but missing from zh-CN: {sorted(missing_zh)}"
    )
    assert not missing_en, (
        f"structure.* keys used in JS but missing from en-US: {sorted(missing_en)}"
    )


def test_phase_a_i18n_structure_keys_in_js_have_str_fallback() -> None:
    """Phase-A lock: every _t() call in structure_viewer.js must have a
    STR-table fallback (second argument) for Node.js environments."""
    js = FRONTEND_JS_DIR / "structure_viewer.js"
    content = js.read_text(encoding="utf-8")

    # Match _t("structure.xxx", STR.YYY) — must have two arguments
    t_calls = re.findall(r'_t\("structure\.[^"]+",\s*STR\.\w+\)', content)
    assert len(t_calls) >= 20, (
        f"Expected >=20 _t() calls with STR fallback, found {len(t_calls)}"
    )
