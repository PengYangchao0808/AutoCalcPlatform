"""Frontend sync page tests — batch area.

Split from tests/test_frontend_sync.py (T29, 2026-10-18). Every test keeps
its original name and body; the old -> new mapping is
tests/test_frontend_sync.py::<name> -> tests/test_frontend_sync_batch.py
::<name>. Shared helpers live in tests/_frontend_sync_shared.py.
"""

from __future__ import annotations

from tests._frontend_sync_shared import (
    _EN_BLOCK_RE,
    _ZH_BLOCK_RE,
    FRONTEND_JS_DIR,
    FRONTEND_WORKBENCH_JS_DIR,
    _extract_nodes_keys,
    read_workbench_content,
    read_workbench_css,
)


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
    html = read_workbench_content()

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
    assert (
        'document.getElementById("modal-node-select").addEventListener("change", onNodeSelectChange);'  # noqa: E501
        in html
    )
    open_body = html.split("function openModal()", 1)[1].split("\nfunction ", 1)[0]
    assert "resetNodeSelector();" in open_body
    assert "scheduleNodeMatchingRefresh();" in open_body

    # D12: submission-time target error codes map to localized text.
    # M0-06: 422 envelopes put the fine-grained reason in params.rule with the
    # legacy top-level code as fallback; translateNodeError reads params first.
    assert "function translateNodeError(err)" in html
    assert "function formatSubmitError(err)" in html
    assert 'var rule = (typeof params.rule === "string" && params.rule) || parsed.code;' in html
    assert 'var key = "nodes.error." + rule;' in html
    assert 'translated = t("nodes.error.execution_target_error");' in html
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
    html = read_workbench_content()

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
        "nodes.reason.local_missing_software",
        "nodes.reason.remote_missing_software",
        "nodes.reason.no_remote_nodes",
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
    html = read_workbench_content()

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
    html = read_workbench_content()

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


# ---------------------------------------------------------------------------
# BatchOptimize role-toggle modal redesign (2026-09-15)
# ---------------------------------------------------------------------------


def test_batch_no_preset_identifiers_remain() -> None:
    """All preset/inheritance identifiers must be completely removed."""
    html = read_workbench_content()
    for marker in [
        "BATCH_PRESETS",
        "_batchPresetMatch",
        "_presetSummaryText",
        "_batchCustomCount",
        "mc-adv-header-bar",
        "mc-adv-custom-count",
        "mc-adv-reset-btn",
        "mc-preset-bar",
        "mc-preset-btn",
        "mc-preset-summary",
        "mc-preset-toast",
        "_buildRoleOverrideField",
        "_resolveRoleValue",
        "_linkEnabled",
        "_linkTsMethods",
        "_copyIntToTs",
        "mc-role-columns",
        "mc-role-col",
        "mc-role-section",
        "mc-role-link-row",
        "mc-role-link",
        "mc-role-inherit-switch",
        "mc-role-override-row",
        "mc-role-inherit-value",
        "mc-role-inherit-badge",
        "mc-role-linked-note",
        "_ROLE_COLUMN_FIELDS",
        "_ROLE_FIELD_DEFS",
        "_ROLE_COLUMN_BASE_FIELDS",
        "_buildRoleColumn",
        "_buildRoleGroupCol",
    ]:
        assert marker not in html, f"Preset/inheritance marker {marker!r} must be removed"


def test_batch_toggle_buttons_present() -> None:
    """Role buttons remain editable at zero and counts use selected explicit roles."""
    html = read_workbench_content()
    assert "mc-batch-role-bar" in html, "Role toggle bar CSS class missing"
    assert "mc-batch-role-btn" in html, "Role toggle button CSS class missing"
    assert "mc-batch-role-count" in html, "Role count span CSS class missing"
    assert "mc-batch-role-form" in html, "Role form container CSS class missing"
    # Toggle button creation with counts
    assert r"INT \u666e\u901a\u9a7b\u70b9" in html, "INT button label missing"
    assert r"TS \u8fc7\u6e21\u6001" in html, "TS button label missing"
    assert "btn.disabled = count === 0" not in html, "Zero-count roles must remain editable"
    assert "if (item.include === false) return" in html
    assert 'else if (item.tag === "INT") intCount++' in html
    # Default active logic
    assert 'if (intCount === 0 && tsCount > 0) _batchRoleActive = "ts"' in html


def test_batch_serializer_emits_batch_roles() -> None:
    """Serializer emits batch_roles with exact key set and preserves null/0."""
    html = read_workbench_content()
    fn_body = html.split("function applyBatchOptimizeMethodFields(methodPayload)", 1)[1]
    fn_body = fn_body.split("\nfunction ", 1)[0]
    assert "methodPayload.batch_roles = {}" in fn_body, "batch_roles initialization missing"
    assert '"int", "ts"].forEach' in fn_body, "Role iteration missing"
    # Key contract
    for key in [
        "method",
        "basis",
        "sp_method",
        "sp_basis",
        "opt_max_iter",
        "opt_convergence",
        "opt_trust_radius",
        "opt_initial_hessian",
        "opt_recalc_hess",
        "scf_max_iter",
        "scf_convergence",
        "scf_strategy",
        "scf_orbital_inherit",
        "scf_damp",
        "scf_damp_fac",
        "scf_shift",
        "scf_shift_fac",
        "opt_rescue_policy",
        "opt_max_rescue",
        "temperature",
        "pressure",
        "scale_factor",
    ]:
        assert f'"{key}"' in fn_body, f"ROLE_CONFIG key {key!r} missing from serializer"
    # Null preservation
    assert "dest[k] = (src[k] !== undefined) ? src[k] : null" in fn_body, (
        "Null preservation logic missing"
    )
    # No minimum_/transition_state_ prefixed keys in the serializer
    assert "minimum_opt_" not in fn_body, "Old minimum_ prefixed keys must be gone from serializer"
    assert "transition_state_opt_" not in fn_body, (
        "Old transition_state_ prefixed keys must be gone from serializer"
    )


def test_batch_role_defaults_defined() -> None:
    """_BATCH_ROLE_DEFAULTS defines complete defaults for both roles."""
    html = read_workbench_content()
    assert "_BATCH_ROLE_DEFAULTS" in html, "_BATCH_ROLE_DEFAULTS constant missing"
    assert "_BATCH_ROLE_DEFAULTS.int" in html or '_BATCH_ROLE_DEFAULTS["int"]' in html
    assert "_BATCH_ROLE_DEFAULTS.ts" in html or '_BATCH_ROLE_DEFAULTS["ts"]' in html
    # INT defaults
    assert "opt_trust_radius: null" in html, "INT trust_radius should be null (engine default)"
    assert 'opt_initial_hessian: "auto"' in html, "INT initial_hessian should be auto"
    assert 'opt_recalc_hess: "auto"' in html, "INT recalc_hess should be auto"
    # TS defaults
    assert "opt_trust_radius: 0.3" in html, "TS trust_radius should be 0.3"
    assert 'opt_initial_hessian: "calculate"' in html, "TS initial_hessian should be calculate"
    assert "opt_recalc_hess: 5" in html, "TS recalc_hess should be 5"


def test_batch_hessian_label_not_computation_count() -> None:
    """Plan section: 'Hessian 计算次数' is forbidden; '每 N 个优化循环重算' is correct."""
    html = read_workbench_content()
    assert r"Hessian \u8ba1\u7b97\u6b21\u6570" not in html, "'Hessian 计算次数' is forbidden"
    assert r"\u6bcf N \u4e2a\u4f18\u5316\u5faa\u73af\u91cd\u7b97" in html


def test_batch_trust_radius_label() -> None:
    """opt_trust_radius label must be '信赖半径' (NOT '步长')."""
    html = read_workbench_content()
    assert r"\u4fe1\u8d56\u534a\u5f84" in html, "'信赖半径' label missing for opt_trust_radius"
    # Must NOT be renamed to 步长
    assert r"\u6b65\u957f" not in html, "'步长' must not replace '信赖半径'"


def test_batch_preview_endpoint_url_present() -> None:
    """config-preview endpoint URL string present."""
    html = read_workbench_content()
    assert "/api/v1/batch-optimize/config-preview" in html, "Preview endpoint URL missing"


def test_batch_preview_debounce_and_abort() -> None:
    """Debounce timer and AbortController markers present."""
    html = read_workbench_content()
    assert "_previewDebounceTimer" in html, "Debounce timer variable missing"
    assert "_previewAbortCtrl" in html, "Abort controller variable missing"
    assert "_PREVIEW_DEBOUNCE_MS" in html, "Debounce constant missing"
    assert "AbortController" in html, "AbortController reference missing"


def test_batch_preview_schema_validation() -> None:
    """Preview response validates schema string."""
    html = read_workbench_content()
    assert "batch_optimize_preview_v1" in html, "Preview schema version string missing"


def test_batch_preview_failure_explicit() -> None:
    """Preview failure must be labeled unverified."""
    html = read_workbench_content()
    assert "function _renderPreviewFailure(" in html, "_renderPreviewFailure missing"
    assert "mc-preview-failure" in html, "Failure CSS class missing"
    assert "mc-preview-unverified" in html, "Unverified CSS class missing"
    # T18: the catch path now lives in the S2 slice module (wizard.js keeps the
    # same-name delegate). catch path routes to the failure renderer.
    s2_module = (
        FRONTEND_WORKBENCH_JS_DIR / "features" / "wizard" / "preview-validate.js"
    ).read_text(encoding="utf-8")
    catch_body = s2_module.split("function fetch()", 1)[1]
    catch_body = catch_body.split("function renderServer", 1)[0]
    assert "renderFailure(" in catch_body


def test_batch_server_preview_rendering() -> None:
    """_renderServerPreview renders per-role effective view."""
    html = read_workbench_content()
    assert "function _renderServerPreview(data)" in html, "_renderServerPreview missing"
    assert "orca_summary" in html, "orca_summary reference missing"
    assert "_previewGeneration" in html, "Generation token missing"


def test_batch_step_bar_removed_from_batch_modal() -> None:
    """The flow selector is sufficient; BatchOptimize must not render a duplicate step bar."""
    html = read_workbench_content()
    assert 'stepBar = document.createElement("div")' not in html
    assert "mc-batch-flat" in html


def test_batch_modal_has_title_without_explanatory_subtitle() -> None:
    """BatchOptimize keeps the title and suppresses the redundant subtitle."""
    html = read_workbench_content()
    assert r"\u6279\u91cf\u4f18\u5316\u8bbe\u7f6e" in html, "批量优化设置 title missing"
    batch_branch = html.split('if (wizardState.workflow.id === "BatchOptimize")', 1)[1]
    batch_branch = batch_branch.split("} else {", 1)[0]
    assert 'subtitle.style.display = "none"' in batch_branch


def test_batch_purpose_specific_disclosures_replace_more_options() -> None:
    """Advanced controls are grouped by purpose instead of one mixed bucket."""
    html = read_workbench_content()
    assert r"\u66f4\u591a\u8ba1\u7b97\u9009\u9879" not in html
    for marker in ["SCF 详细设置", "热化学", "失败重试", "参数摘要"]:
        assert marker in html


def test_batch_single_point_role_fields_are_primary_and_catalog_backed() -> None:
    """SP profiles expose role-specific method/basis fields outside More Options."""
    html = read_workbench_content()
    assert 'if (batchHasStep("singlepoint"))' in html
    assert '_buildBatchRoleMethodField(methodFields, "sp_method"' in html
    assert '_buildBatchRoleMethodField(methodFields, "sp_basis"' in html
    assert 'label_zh: "单点能"' in html


def test_batch_compact_grids_cover_primary_and_more_fields() -> None:
    """Wide screens use two-column compact fields and narrow screens collapse."""
    css = read_workbench_css()
    assert ".mc-batch-more-fields" in css
    assert "repeat(2, minmax(0, 1fr))" in css
    assert "@media (max-width: 680px)" in css


def test_batch_parameter_summary_collapsible() -> None:
    """A normalised parameter summary is not mislabeled as a full input preview."""
    html = read_workbench_content()
    assert '_makeBatchDisclosure("参数摘要", "Parameter Summary"' in html
    assert '"输入预览" : "Input Preview"' not in html


def test_batch_copy_other_role_method() -> None:
    """The role-toolbar copy button copies methods but not optimizer/SCF fields."""
    html = read_workbench_content()
    assert "roleToolbar.appendChild(copyRoleBtn)" in html
    assert "roleToolbar.appendChild(roleBar)" in html
    copy_body = html.split('copyRoleBtn.addEventListener("click"', 1)[1]
    copy_body = copy_body.split("});", 1)[0]
    for key in ["method", "basis", "sp_method", "sp_basis"]:
        assert f'"{key}"' in copy_body, f"Copy must include key {key!r}"
    # Must NOT copy optimizer/Hessian/SCF params
    for key in ["opt_max_iter", "opt_convergence", "scf_max_iter", "opt_recalc_hess"]:
        assert f'"{key}"' not in copy_body, f"Copy must NOT include key {key!r}"


def test_batch_hessian_recalc_writes_values() -> None:
    """Hessian recalc UI: 每N循环 writes N, 不重算 writes 0."""
    html = read_workbench_content()
    body = html.split("function _buildHessianRecalcField(dest)", 1)[1]
    body = body.split("\n        }", 1)[0]
    # "auto" mode writes "auto"
    assert 'rd.opt_recalc_hess = "auto"' in body, "Auto mode must write 'auto'"
    # "off" mode writes 0
    assert "rd.opt_recalc_hess = 0" in body, "Off mode must write 0"
    # "custom" mode writes the numeric value
    assert "rd.opt_recalc_hess = customVal" in body, "Custom mode must write customVal"
    # "custom" mode with N=1 should work (customVal defaults to 10, user can change)
    assert "customVal = v" in body, "Custom value must be updated from input"


def test_batch_restore_default_affects_active_role_only() -> None:
    """Restore-default button resets only the active role's draft."""
    html = read_workbench_content()
    restore_body = html.split('restoreBtn.addEventListener("click"', 1)
    assert len(restore_body) > 1, "Restore button handler missing"
    handler = restore_body[1].split("});", 1)[0]
    assert "activeRole" in handler, "Must reference active role"
    assert "_BATCH_ROLE_DEFAULTS" in handler, "Must use _BATCH_ROLE_DEFAULTS"
    # Must not reset both roles at once
    assert 'br["int"]' not in handler and "br.int" not in handler, (
        "Must not reset INT directly — only active role"
    )


def test_batch_cancel_reverts_whole_draft() -> None:
    """Cancel/close on the batch config window must fully revert changes."""
    html = read_workbench_content()
    open_body = html.split("async function openMethodConfig()", 1)[1]
    open_body = open_body.split("\nasync function ", 1)[0]
    assert "var _methodDraft = JSON.parse(JSON.stringify(wizardState.method));" in open_body
    assert "function revertMethodDraft()" in open_body
    assert "window._revertMethodDraft = revertMethodDraft;" in open_body
    for btn_id in ("mt-config-cancel", "mt-config-close"):
        marker = (
            f'document.getElementById("{btn_id}").addEventListener("click", function() {{\n'
            '    if (typeof window._revertMethodDraft === "function") '
            "window._revertMethodDraft();"
        )
        assert marker in html, f"{btn_id} must route through revertMethodDraft()"


def test_batch_cancel_blocks_late_validation_writeback() -> None:
    """doValidate must not write normalized values after cancel closed the modal."""
    html = read_workbench_content()
    modal_guard = (
        'var modalHidden = document.getElementById("method-config-modal").style.display === "none";'
    )
    assert modal_guard in html
    assert "if (body.normalized_levels && !modalHidden)" in html


def test_batch_restore_default_button_in_footer() -> None:
    """'恢复当前页默认' button added to modal footer for BatchOptimize."""
    html = read_workbench_content()
    assert "mt-config-restore-default" in html, "Restore-default button id missing"
    assert r"\u6062\u590d\u5f53\u524d\u9875\u9ed8\u8ba4" in html, "恢复当前页默认 label missing"


def test_batch_update_config_cards_reads_batch_roles() -> None:
    """updateConfigCards reads INT/TS methods from batch_roles."""
    html = read_workbench_content()
    update_body = html.split("function updateConfigCards()", 1)[1]
    update_body = update_body.split("\nfunction ", 1)[0]
    assert "batch_roles" in update_body, "updateConfigCards must reference batch_roles"
    assert "br.int" in update_body or "br['int']" in update_body, "Must read INT role"
    assert "br.ts" in update_body or "br['ts']" in update_body, "Must read TS role"


def test_batch_hessian_combined_row_still_exists() -> None:
    """Hessian has initial select + recalc segmented in one row."""
    html = read_workbench_content()
    assert "mc-hessian-row" in html, "Hessian combined row class missing"
    assert "mc-hessian-seg" in html, "Hessian segmented control class missing"
    assert "mc-hessian-seg-btn" in html, "Hessian segmented button class missing"


def test_batch_basis_readonly_display() -> None:
    """Single-option locked basis shows read-only display."""
    html = read_workbench_content()
    assert "mc-basis-readonly" in html, "Basis read-only display class missing"


def test_batch_info_tooltip() -> None:
    """Help text uses info tooltip."""
    html = read_workbench_content()
    assert "mc-info-tip" in html, "Info tooltip class missing"


def test_batch_convergence_canonicalization() -> None:
    """Payload builder uses lowercase enum values."""
    html = read_workbench_content()
    fn_body = html.split("function applyBatchOptimizeMethodFields(methodPayload)", 1)[1]
    fn_body = fn_body.split("\nfunction ", 1)[0]
    # Serializer reads from batch_roles which stores user-selected values
    assert "batch_roles" in fn_body, "Serializer must use batch_roles"


def test_batch_preview_local_summary_for_active_role() -> None:
    """Local summary renders for the active role only."""
    html = read_workbench_content()
    # T18: the renderer body moved to the S2 slice module; wizard.js supplies the
    # active-role / roles accessors to the factory, so pin the wiring there.
    assert "getActiveRole: function() { return _batchRoleActive; }" in html, (
        "Must use active role for local summary"
    )
    assert "getRoles: function() { return _batchRoles; }" in html, "Must read from _batchRoles"
    s2_module = (
        FRONTEND_WORKBENCH_JS_DIR / "features" / "wizard" / "preview-validate.js"
    ).read_text(encoding="utf-8")
    body = s2_module.split("function renderLocal()", 1)[1].split("\n    }", 1)[0]
    assert "getActiveRole()" in body, "Local summary must resolve the active role"
    assert "getRoles()" in body, "Local summary must resolve the role drafts"


def test_batch_engine_defaults_match_backend() -> None:
    """batchRoleStaticDefaults() must mirror backend ROLE_PRODUCT_DEFAULTS exactly.

    Imports ROLE_PRODUCT_DEFAULTS at runtime, extracts the JS object literal
    from the single authoritative shared factory
    (frontend/js/workbench/contracts/batch_role_defaults.js, consumed by both
    wizard openMethodConfig and ACPJobEditor), and asserts every key/value
    pair matches for both roles. Static assertions are kept for semantic
    documentation.
    """
    from acp.calculations.batch.options import ROLE_PRODUCT_DEFAULTS

    defaults_js = (
        FRONTEND_JS_DIR / "workbench" / "contracts" / "batch_role_defaults.js"
    ).read_text(encoding="utf-8")

    # Extract the JS literal from the shared defaults factory.
    block = defaults_js.split("function batchRoleStaticDefaults()", 1)[1]
    block = block.split("return {", 1)[1]
    block = block.split("\n  };", 1)[0]
    block += "\n}"  # re-add closing brace for the outer object

    def _py_to_js(val: object) -> str:
        if val is None:
            return "null"
        if val is True:
            return "true"
        if val is False:
            return "false"
        if isinstance(val, str):
            return '"' + val + '"'
        if isinstance(val, int):
            return str(val)
        if isinstance(val, float):
            return str(val) if val != int(val) else str(int(val))
        return str(val)

    for role in ("int", "ts"):
        role_defaults = ROLE_PRODUCT_DEFAULTS[role]
        # Locate the role sub-object in the JS literal
        role_block = block.split(role + ": {", 1)
        assert len(role_block) > 1, f"Role {role!r} not found in _BATCH_ROLE_DEFAULTS"
        role_js = role_block[1].split("}", 1)[0]
        for key, expected_val in role_defaults.items():
            js_pair = key + ": " + _py_to_js(expected_val)
            assert js_pair in role_js, (
                f"Frontend _BATCH_ROLE_DEFAULTS.{role}.{key} mismatch: "
                f"expected {js_pair!r} not found in JS role block"
            )

    # Static semantic assertions (kept for readability)
    # INT: no trust radius override, auto hessian
    assert "opt_trust_radius: null" in defaults_js, "INT trust_radius must be null"
    assert 'opt_initial_hessian: "auto"' in defaults_js, "INT initial_hessian must be auto"
    assert 'opt_recalc_hess: "auto"' in defaults_js, "INT recalc_hess must be auto"
    # TS: trust 0.3, calculate hessian, recalc every 5
    assert "opt_trust_radius: 0.3" in defaults_js, "TS trust_radius must be 0.3"
    assert 'opt_initial_hessian: "calculate"' in defaults_js, "TS initial_hessian must be calculate"
    assert "opt_recalc_hess: 5" in defaults_js, "TS recalc_hess must be 5"
    # Shared defaults
    assert 'opt_convergence: "tight"' in defaults_js, "Shared opt_convergence must be tight"
    assert "scf_max_iter: 300" in defaults_js, "Shared scf_max_iter must be 300"
    assert 'scf_convergence: "tight"' in defaults_js, "Shared scf_convergence must be tight"


def test_batch_no_expert_overrides_group() -> None:
    """expert_overrides group must not appear in FIELD_GROUPS."""
    html = read_workbench_content()
    assert 'id: "expert_overrides"' not in html


def test_batch_temperature_pressure_labels() -> None:
    """Temperature/pressure/scale_factor labels present in batch form."""
    html = read_workbench_content()
    assert r"\u6e29\u5ea6" in html, "Temperature label missing"
    assert r"\u538b\u529b" in html, "Pressure label missing"
    assert r"\u9891\u7387\u7f29\u653e\u56e0\u5b50" in html, "Scale factor label missing"


def test_batch_slowconv_label_zh() -> None:
    """SlowConv zh label (慢收敛) present."""
    html = read_workbench_content()
    assert 'slowconv: "慢收敛"' in html, "SlowConv zh label missing"


def test_batch_hessian_control_writes_canonical_field() -> None:
    """Hessian tri-state control must read/write opt_recalc_hess."""
    html = read_workbench_content()
    body = html.split("function buildHessianFieldRow(dest)", 1)[1]
    body = body.split("\n    function ", 1)[0]
    assert "st.opt_recalc_hess" in body, "control must write the canonical opt_recalc_hess field"
    assert "st.recalc_hess =" not in body, "legacy recalc_hess writes must be gone"
    assert "st.recalc_hess !== undefined" in body, "legacy one-time migration read must remain"
