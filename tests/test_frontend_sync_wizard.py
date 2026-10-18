"""Frontend sync page tests — wizard area.

Split from tests/test_frontend_sync.py (T29, 2026-10-18). Every test keeps
its original name and body; the old -> new mapping is
tests/test_frontend_sync.py::<name> -> tests/test_frontend_sync_wizard.py
::<name>. Shared helpers live in tests/_frontend_sync_shared.py.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from acp.catalog import METHOD_SCHEMAS, WORKFLOW_CATALOG
from tests._frontend_sync_shared import (
    _EN_BLOCK_RE,
    _SP_JS,
    _ZH_BLOCK_RE,
    FRONTEND_CSS_DIR,
    FRONTEND_JS_DIR,
    FRONTEND_WORKBENCH_JS_DIR,
    REPO_ROOT,
    _ProfileRecord,
    read_workbench_content,
    read_workbench_css,
    viewer_creation_site_count,
)


def test_wizard_default_workflow_and_protocol_are_catalog_driven() -> None:
    """Regression guard for the stale-protocol-page bug (2026-09-07).

    The create-task wizard used to hardcode the retired "energy" workflow as
    the default and fall back to the legacy "confsearch" method schema, so the
    first open rendered the pre-refactor protocol page until a workflow was
    re-picked. The default must now come from the catalog at runtime:
    ``resolveDefaultWorkflow`` (Confsearch first) + ``pickDefaultProfile``
    (censo-crest preferred) + self-healing ``ensureWizardWorkflowValid``.
    """
    html = read_workbench_content()

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


def test_structure_upload_exts_constant_defined() -> None:
    """STRUCTURE_UPLOAD_EXTS must be a single constant listing all accepted extensions."""
    html = read_workbench_content()
    assert "STRUCTURE_UPLOAD_EXTS" in html, "STRUCTURE_UPLOAD_EXTS constant missing"
    for ext in _EXPECTED_STRUCTURE_EXTS:
        assert (
            f'"{ext}"' in html.split("STRUCTURE_UPLOAD_EXTS")[1].split("\n")[0]
            or f"'{ext}'" in html.split("STRUCTURE_UPLOAD_EXTS")[1].split("\n")[0]
        ), f"{ext} missing from STRUCTURE_UPLOAD_EXTS definition"


def test_structure_upload_exts_include_sd_log_out() -> None:
    """Regression guard: .sd, .log, .out must be in the accept list (S5)."""
    html = read_workbench_content()
    accept_line = html.split('id="upload-file-input"')[1].split(">")[0]
    for ext in (".sd", ".log", ".out"):
        assert ext in accept_line, f"{ext} missing from file picker accept attribute"


def test_file_picker_accept_matches_constant() -> None:
    """The file picker accept attribute must list the same extensions as STRUCTURE_UPLOAD_EXTS."""
    html = read_workbench_content()
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
    html = read_workbench_content()
    assert "function isAcceptedStructureFile(" in html, "isAcceptedStructureFile function missing"
    # The function body must reference STRUCTURE_UPLOAD_EXTS
    fn_body = html.split("function isAcceptedStructureFile(")[1].split("\nfunction ")[0]
    assert "STRUCTURE_UPLOAD_EXTS" in fn_body, (
        "isAcceptedStructureFile must use STRUCTURE_UPLOAD_EXTS"
    )


def test_reject_unsupported_file_function_exists() -> None:
    """rejectUnsupportedFile(file) must exist and use modal.unsupported_ext."""
    html = read_workbench_content()
    assert "function rejectUnsupportedFile(" in html, "rejectUnsupportedFile function missing"
    fn_body = html.split("function rejectUnsupportedFile(")[1].split("\nfunction ")[0]
    assert "modal.unsupported_ext" in fn_body, (
        "rejectUnsupportedFile must use modal.unsupported_ext i18n key"
    )


def test_change_handler_calls_reject_unsupported() -> None:
    """The file input change handler must call rejectUnsupportedFile for unsupported files."""
    html = read_workbench_content()
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
    html = read_workbench_content()
    # Find the drop handler block
    drop_block = html.split('dropzone.addEventListener("drop"')[1].split("});")[0]
    assert "rejectUnsupportedFile(" in drop_block, "drop handler must call rejectUnsupportedFile"
    assert "isAcceptedStructureFile(" in drop_block, (
        "drop handler must call isAcceptedStructureFile"
    )


def test_reject_unsupported_does_not_clear_wizard_state() -> None:
    """rejectUnsupportedFile must not reset wizardUploadFile or other wizard state."""
    html = read_workbench_content()
    fn_body = html.split("function rejectUnsupportedFile(")[1].split("\nfunction ")[0]
    assert "wizardUploadFile = null" not in fn_body, (
        "rejectUnsupportedFile must NOT clear wizardUploadFile"
    )
    assert "parseStructuresPreview" not in fn_body, (
        "rejectUnsupportedFile must NOT trigger parseStructuresPreview"
    )


def test_unsupported_ext_locale_key_in_both_locales() -> None:
    """modal.unsupported_ext must exist in both zh-CN and en-US locale dictionaries."""
    html = read_workbench_content()
    zh_keys = _extract_all_modal_keys(html, _ZH_BLOCK_RE)
    en_keys = _extract_all_modal_keys(html, _EN_BLOCK_RE)
    assert "modal.unsupported_ext" in zh_keys, "modal.unsupported_ext missing from zh-CN locale"
    assert "modal.unsupported_ext" in en_keys, "modal.unsupported_ext missing from en-US locale"


def test_nmr_bruker_zip_upload_unchanged() -> None:
    """The NMR Bruker zip upload must remain independent (accept=.zip, parse=false)."""
    html = read_workbench_content()
    assert 'id="nmr-bruker-file"' in html, "NMR Bruker file input missing"
    bruker_block = html.split('id="nmr-bruker-file"')[1].split(">")[0]
    assert ".zip" in bruker_block, "NMR Bruker input must accept .zip"
    # Must NOT have the structure extensions
    assert ".xyz" not in bruker_block, "NMR Bruker input must not list .xyz"


def test_is_accepted_structure_file_case_insensitive() -> None:
    """isAcceptedStructureFile must compare extensions case-insensitively."""
    html = read_workbench_content()
    fn_body = html.split("function isAcceptedStructureFile(")[1].split("\nfunction ")[0]
    assert ".toLowerCase()" in fn_body or ".toLowerCase()" in fn_body.replace(" ", ""), (
        "isAcceptedStructureFile must use toLowerCase() for case-insensitive comparison"
    )


def test_wizard_project_section_is_first_step() -> None:
    """Project target must be wizard step 1 — before structure and workflow.

    Regression guard (2026-09 plan §1): the project block used to sit after
    the workflow/protocol cards, so users configured the job before deciding
    where it belongs.
    """
    html = read_workbench_content()
    modal = html.split('id="job-modal"', 1)[1].split('class="modal-overlay"', 1)[0]

    proj_pos = modal.find('id="modal-project-section"')
    wizard_pos = modal.find('class="wizard-input-section"')
    cards_pos = modal.find('class="config-cards-row"')
    assert proj_pos != -1 and wizard_pos != -1 and cards_pos != -1
    assert proj_pos < wizard_pos < cards_pos

    assert 'data-i18n="wizard.target_project"' in modal
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


def test_job_modal_layout_prevents_card_and_toolbar_overlap() -> None:
    """Long labels stay inside balanced cards and preview actions stay horizontal."""
    css = read_workbench_css()

    assert "#job-modal .config-cards-row {" in css
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in css
    assert "#job-modal .config-card {" in css
    assert "grid-template-columns: minmax(0, 1fr) auto;" in css
    assert "#job-modal .config-card-btn {" in css
    assert "white-space: nowrap;" in css
    assert "#job-modal .preview-right-toolbar {" in css
    assert "#job-modal .preview-right-actions {" in css
    assert "min-width: max-content;" in css
    assert ".footer-selected-count {" in css
    assert "flex: 1 1 auto;" in css
    assert "#job-modal .config-cards-row { grid-template-columns: minmax(0, 1fr); }" in css


def test_results_filter_targets_modal_project_not_top_filter() -> None:
    """The embedded picker follows the modal target project directly."""
    html = read_workbench_content()

    assert "function resultsProjectTargetId()" in html
    modal = html.split('id="job-modal"', 1)[1].split('class="modal-overlay"', 1)[0]
    assert 'id="results-project"' not in modal
    assert 'id="results-search"' not in modal

    loader = html.split("async function loadStructureSources(", 1)[1].split("\nfunction ", 1)[0]
    assert "selectedProjectId" not in loader
    assert 'density: "editor"' in loader
    assert "projectId: resultsProjectTargetId()" in loader
    assert 'mode === "target"' in loader
    assert "resultsProjectTargetId()" in loader
    assert "all_projects=true" in loader

    change_block = html.split('modal-project-select").addEventListener("change"', 1)[1]
    change_block = change_block.split("});", 1)[0]
    assert "resultsPicker.setProject(resultsProjectTargetId())" in change_block


def test_results_rows_status_badges_project_labels_and_search_scope() -> None:
    """Status badges, project grouping/labels, and the widened search hay."""
    html = read_workbench_content()

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
    assert '(src.candidate_id || "")' in render_body
    assert '(src.job_status || "")' in render_body

    assert "function updateResultsCrossHint(" in html
    assert 'id="results-cross-hint"' in html


def test_structure_source_ref_records_project_provenance() -> None:
    """source_ref must carry project_id/job_status so cross-project loads stay traceable."""
    html = read_workbench_content()

    ref_body = html.split("source_ref: {", 1)[1].split("}", 1)[0]
    assert "project_id" in ref_body
    assert "job_status" in ref_body
    assert "checksum" in ref_body

    loader = html.split("async function loadStructureSource(", 1)[1].split("\nfunction ", 1)[0]
    assert "body.project_id" in loader
    assert "body.job_status" in loader
    assert "results.cross_project" in loader


def test_electronic_state_module_renderer_dispatch() -> None:
    """Contract: electronic_state type:'module' gets a dedicated renderer.

    The generic buildFieldRow must dispatch to buildElectronicStateFieldRow
    for fields with type 'module' and renderer 'electronic_state' BEFORE the
    text-input fallback, so the structured value never renders as
    '[object Object]'.
    """
    html = read_workbench_content()

    # 1. The dedicated dispatch string exists in buildFieldRow.
    assert 'fieldName].renderer === "electronic_state"' in html

    # 2. buildElectronicStateFieldRow is defined exactly once.
    assert html.count("function buildElectronicStateFieldRow(") == 1

    # 3. The module dispatch sits before the generic text-input fallback.
    module_pos = html.find('fieldName].renderer === "electronic_state"')
    fallback_marker = (
        '} else {\n        var inp = document.createElement("input");\n        inp.type = "text";'
    )
    fallback_pos = html.find(fallback_marker)
    assert module_pos != -1 and fallback_pos != -1
    assert module_pos < fallback_pos, (
        "module/electronic_state dispatch must precede the text-input fallback"
    )

    # 4. Preset labels cover all catalog presets.
    from acp.catalog import FIELD_DEFINITIONS

    es_fd = FIELD_DEFINITIONS.get("electronic_state", {})
    for preset_id in es_fd.get("presets", []):
        assert preset_id in html, (
            f"electronic_state preset {preset_id!r} missing from _ES_PRESET_LABELS"
        )

    # 5. The fallback guard handles object values (no raw [object Object]).
    assert 'typeof curVal === "object"' in html
    assert "JSON.stringify(curVal)" in html


def test_electronic_state_summary_expands_preset() -> None:
    """A chosen preset (e.g. doublet) must not render as 'automatic'."""
    html = read_workbench_content()
    es_body = html.split("function _esSummaryText(curVal)", 1)[1].split("\n    function ", 1)[0]
    assert "curVal.preset_id" in es_body, "summary must expand the stored preset_id"
    assert "_ES_PRESET_DETAILS" in html
    # Old misleading automatic wording is gone.
    assert "由后端自动确定电子态" not in html
    assert r"\u7531\u540e\u7aef\u81ea\u52a8\u786e\u5b9a\u7535\u5b50\u6001" not in html


def test_job_modal_structure_browser_visual_contract() -> None:
    """The source browser uses a two-column workspace (left list + right preview)."""
    html = read_workbench_content()
    modal = html.split('id="job-modal"', 1)[1].split('class="modal-overlay"', 1)[0]
    assert 'id="preview-structure-title"' in modal
    assert 'id="source-coordinate-disclosure"' in modal
    assert 'id="preview-reset-view"' in modal
    assert 'id="task-results-browser"' in modal
    assert 'class="task-info-details"' in modal
    assert modal.count('id="structure-preview-3d"') == 1
    assert 'class="tiw-grid"' in modal, "tiw-grid two-column workspace missing"
    assert 'class="tiw-left"' in modal, "tiw-left column missing"
    assert 'class="tiw-right"' in modal, "tiw-right column missing"
    tiw_css = (REPO_ROOT / "frontend" / "css" / "task_input_workspace.css").read_text()
    assert "grid-template-columns" in tiw_css, "tiw-grid grid-template-columns missing from CSS"
    assert "minmax(0, 3fr) minmax(380px, 2fr)" in tiw_css, "60/40 workspace columns missing"
    assert "@media (max-width: 1100px)" in tiw_css, "responsive breakpoint missing from tiw CSS"
    assert '@media (max-width: 900px)' in read_workbench_css() or '@media (max-width: 900px)' in html
    assert 'class="tiw-source-toolbar"' in modal, "tiw-source-toolbar missing"
    assert 'id="tiw-toolbar-host"' in modal, "tiw-toolbar-host missing"
    assert 'id="tiw-list-header"' in modal, "tiw-list-header column header missing"
    assert 'id="footer-input-count"' in html, "footer-input-count missing"
    assert 'id="input-list-overlay"' in html, "input-list-overlay missing"
    assert 'class="tiw-info-panel"' in modal, "tiw-info-panel missing"
    assert 'id="wizard-input-form"' in modal, "wizard-input-form missing"
    assert 'id="tiw-info-panel"' in modal, "tiw-info-panel id missing"
    left_pane = modal.split('class="preview-left"', 1)[1].split(
        'class="preview-right"', 1
    )[0]
    assert 'id="input-panel-task"' in left_pane
    assert 'id="input-panel-structure"' in left_pane
    assert 'id="input-panel-upload"' in left_pane
    assert 'id="task-results-browser"' in left_pane
    assert 'id="structure-preview-3d"' not in left_pane
    right_pane = modal.split('class="preview-right"', 1)[1]
    assert 'id="preview-expand-view"' in right_pane
    assert 'id="preview-remove-selected"' not in right_pane
    assert "var PREVIEW_ZOOM_FACTOR = 0.82" in html
    assert '{ zoomFactor: PREVIEW_ZOOM_FACTOR }' in html
    assert 'updatePreviewActionState();' in html
    assert 'sourceShell.setAttribute("data-source-mode", mode)' in html
    assert 'browser.style.display = mode === "task" ? "flex" : "none"' in html
    assert "display: contents" not in html
    assert len(re.findall(r'class="input-mode-tab(?: active)?"', modal)) == 3


def test_job_modal_typography_has_a_clear_four_level_hierarchy() -> None:
    css = read_workbench_css()
    assert "--editor-font-detail: 12px" in css
    assert "--editor-font-base: 14px" in css
    assert "--editor-font-feature: 16px" in css
    assert "--editor-font-title: 18px" in css
    assert 'font-family: "Noto Sans SC", Inter, "Segoe UI", sans-serif !important' in css
    assert "--editor-weight-normal: 400" in css
    assert "--editor-weight-medium: 600" in css
    assert "--editor-weight-strong: 700" in css
    assert "#job-modal .modal-header h2 {" in css
    feature_heading_rule = css.split("#job-modal .modal-section-title,", 1)[1].split("}", 1)[0]
    assert "#job-modal #modal-project-section > label" in feature_heading_rule
    assert "#job-modal .config-card-label" in feature_heading_rule
    assert "#job-modal .config-card-body strong" not in feature_heading_rule
    content_title_rule = css.rsplit("#job-modal .edit-banner-source,", 1)[1].split("}", 1)[0]
    assert "#job-modal .config-card-body strong" in content_title_rule
    assert "#job-modal .sp-row-name" in content_title_rule
    assert "#job-modal .sp-row-meta {" in css
    assert "font-size: var(--editor-font-base) !important" not in css


def test_job_modal_numbered_headings_use_aligned_step_layout() -> None:
    """Steps 1/2/3/5 share the left edge; protocol remains the right column."""
    html = read_workbench_content()
    modal = html.split('id="job-modal"', 1)[1].split('class="modal-overlay"', 1)[0]

    # project-step-inline is the compressed single-row variant (task 2026-09-22)
    assert 'class="project-step project-step-inline" id="modal-project-section"' in modal
    assert 'class="config-step" id="workflow-card"' in modal
    assert 'class="config-step" id="method-card"' in modal
    assert '<div class="config-card">' in modal

    # The numbered config headings must keep feature-heading typography and
    # must not be pulled back into the tertiary/detail text rule.
    css = read_workbench_css()
    tertiary_rule = css.split("#job-modal .muted,", 1)[1].split("{", 1)[0]
    assert "#job-modal .config-card-label" not in tertiary_rule
    assert "#job-modal .config-step {" in css
    assert "#job-modal .project-step { display: grid; gap: 8px; }" in css


# ---------------------------------------------------------------------------
# Candidate inspector i18n keys (kept — candidate_details.js still uses these)
# ---------------------------------------------------------------------------


def test_candidate_inspector_i18n_keys_in_both_locales() -> None:
    """candidate.inspector.* i18n keys exist in both zh-CN and en-US blocks."""
    html = read_workbench_content()
    ci_key_re = re.compile(r'"(candidate\.inspector\.[^"]+)":')

    zh_m = _ZH_BLOCK_RE.search(html)
    en_m = _EN_BLOCK_RE.search(html)
    assert zh_m, "zh-CN block not found"
    assert en_m, "en-US block not found"

    zh_keys = set(ci_key_re.findall(zh_m.group(1)))
    en_keys = set(ci_key_re.findall(en_m.group(1)))

    assert zh_keys, "No candidate.inspector.* keys found in zh-CN"
    assert en_keys, "No candidate.inspector.* keys found in en-US"

    only_zh = zh_keys - en_keys
    only_en = en_keys - zh_keys
    assert not only_zh, f"candidate.inspector keys in zh-CN but missing from en-US: {sorted(only_zh)}"
    assert not only_en, (
        f"candidate.inspector keys in en-US but missing from zh-CN: {sorted(only_en)}"
    )

    required = {
        "candidate.inspector.empty",
        "candidate.inspector.current_assessment",
        "candidate.inspector.usage_status",
        "candidate.inspector.edit_btn",
        "candidate.inspector.save_btn",
        "candidate.inspector.save_caption",
        "candidate.inspector.cancel_btn",
        "candidate.inspector.conclusion_label",
        "candidate.inspector.reason_label",
        "candidate.inspector.placeholder_select",
        "candidate.inspector.reason_required",
        "candidate.inspector.conflict_banner",
        "candidate.inspector.geometry_hint",
        "candidate.inspector.discard_confirm",
        "candidate.inspector.conclusion.recommended",
        "candidate.inspector.conclusion.review",
        "candidate.inspector.conclusion.not_recommended",
        "candidate.inspector.reason.geometry_unreasonable",
        "candidate.inspector.reason.wrong_reaction_mode",
        "candidate.inspector.reason.other",
    }
    assert required <= zh_keys, (
        f"Missing zh-CN candidate.inspector keys: {sorted(required - zh_keys)}"
    )


def test_wizard_footer_selected_count_element() -> None:
    """footer-selected-count and footer-input-count elements exist."""
    html = read_workbench_content()
    assert 'id="footer-selected-count"' in html, "footer-selected-count element missing"
    assert 'id="footer-input-count"' in html, "footer-input-count element missing"
    assert 'id="footer-input-badge"' in html, "footer-input-badge element missing"
    assert "updateFooterSelectedCount" in html, "updateFooterSelectedCount function missing"
    assert "_refreshWizardInputForm" in html, "_refreshWizardInputForm function missing"


def test_wizard_inspector_i18n_keys_in_both_locales() -> None:
    """wizard.* i18n keys exist in both zh-CN and en-US blocks."""
    html = read_workbench_content()
    _wizard_key_re = re.compile(r'"(wizard\.[^"]+)":')
    zh_m = _ZH_BLOCK_RE.search(html)
    en_m = _EN_BLOCK_RE.search(html)
    assert zh_m, "zh-CN block not found"
    assert en_m, "en-US block not found"
    zh_keys = set(_wizard_key_re.findall(zh_m.group(1)))
    en_keys = set(_wizard_key_re.findall(en_m.group(1)))
    assert zh_keys, "No wizard.* keys found in zh-CN"
    assert en_keys, "No wizard.* keys found in en-US"
    only_zh = zh_keys - en_keys
    only_en = en_keys - zh_keys
    assert not only_zh, f"wizard keys in zh-CN but missing from en-US: {sorted(only_zh)}"
    assert not only_en, f"wizard keys in en-US but missing from zh-CN: {sorted(only_en)}"
    required = {"wizard.select_hint", "wizard.no_3d_coord", "wizard.selected_count_footer",
                "wizard.disabled_needs_action", "wizard.atom_numbers", "wizard.list_tab", "wizard.detail_tab"}
    assert required <= zh_keys, f"Missing zh-CN wizard keys: {sorted(required - zh_keys)}"


def test_picker_set_source_group_exists() -> None:
    js = _SP_JS.read_text(encoding="utf-8")
    assert "setSourceGroup" in js, "setSourceGroup missing from structure_source_picker.js"
    ret_idx = js.rindex("return {")
    ret_section = js[ret_idx:ret_idx + 500]
    assert "setSourceGroup" in ret_section, \
        "setSourceGroup must be in the picker instance return object"


def test_wizard_create_viewer_count_pinned() -> None:
    """Viewer creation sites pinned at 0 (FE-04 slice 1: legacy S2 modal
    viewers removed from the 8-instance budget; T9: energy preview migrated
    to the bounded scene instance; T8/T12: main-structure + NMR canvases
    migrated; T11: reaction-builder migrated; T10: wizard preview migrated —
    creation lives only in scene_instance.js via the adapter)."""
    html = read_workbench_content()
    count = viewer_creation_site_count()
    assert count == 0, f"Expected exactly 0 viewer creation calls, found {count}"


def test_wizard_no_batch_management_in_step1() -> None:
    html = read_workbench_content()
    assert 'id="candidate-library-modal"' not in html, \
        "standalone candidate-library-modal must be removed"
    assert "批量管理" not in html, \
        "批量管理 batch management text must not appear in the converged workspace"


def test_wizard_atom_toggle_button_in_toolbar() -> None:
    """preview-atom-numbers button exists in preview-right-toolbar."""
    html = read_workbench_content()
    assert 'id="preview-atom-numbers"' in html, "preview-atom-numbers button missing"
    assert "wizardAtomLabelsOn" in html, "wizardAtomLabelsOn state variable missing"


def test_wizard_mobile_toggle_buttons_exist() -> None:
    """wizard-mobile-list-btn and wizard-mobile-detail-btn exist for small-screen fallback."""
    html = read_workbench_content()
    assert 'id="wizard-mobile-list-btn"' in html, "wizard-mobile-list-btn missing"
    assert 'id="wizard-mobile-detail-btn"' in html, "wizard-mobile-detail-btn missing"
    assert "wizard-show-list" in html, "wizard-show-list CSS class missing"
    assert "wizard-show-detail" in html, "wizard-show-detail CSS class missing"


def test_wizard_disabled_badge_in_drawer() -> None:
    """loaded-structure-disabled-badge rendered for disabled/archived structures in drawer."""
    html = read_workbench_content()
    assert "loaded-structure-disabled-badge" in html, "loaded-structure-disabled-badge class missing"
    assert "wizard.disabled_needs_action" in html, "wizard.disabled_needs_action i18n key not used in JS"


def test_wizard_project_section_compressed() -> None:
    """#modal-project-section uses project-step-inline class for compressed layout."""
    html = read_workbench_content()
    assert "project-step-inline" in html, "project-step-inline class missing"
    # The stylesheet CSS must exist
    assert ".project-step-inline" in read_workbench_css(), ".project-step-inline CSS rule missing"


# ---------------------------------------------------------------------------
# Task Input Workspace — new-IA contracts
# ---------------------------------------------------------------------------

class TestTaskInputWorkspace:

    @staticmethod
    def _html() -> str:
        return read_workbench_content()

    def test_unified_structure_library_filters(self) -> None:
        html = self._html()
        tabs = html.split('class="input-mode-tabs"', 1)[1].split("</div>", 1)[0]
        assert 'data-i18n="modal.mode_library"' in tabs
        assert 'data-input-mode="candidate"' not in tabs
        for group in ("candidate", "task_result"):
            assert f'data-source-group="{group}"' in html, f"library filter {group!r} missing"

    def test_tiw_grid_layout_and_details_host(self) -> None:
        html = self._html()
        assert 'class="tiw-grid"' in html, "tiw-grid layout missing"
        assert 'class="tiw-details-host"' in html, "tiw-details-host missing"
        assert 'id="candidate-details-host"' in html, "candidate-details-host missing"

    def test_trash_row_menu_and_undo(self) -> None:
        html = self._html()
        assert "trash.row_menu" in html, "trash.row_menu i18n keys missing"
        assert "wizard-open-trash" in html, "wizard-open-trash button missing"
        assert "wizard-trash-header" in html, "wizard-trash-header area missing"

    def test_details_panel_wired_in_on_preview_item(self) -> None:
        html = self._html()
        assert "candidateDetailsPanel.setEntry" in html, \
            "candidateDetailsPanel.setEntry wiring missing"

    def test_draft_retention_and_close_keep_draft(self) -> None:
        html = self._html()
        assert "wizard.close_keep_draft" in html, "close_keep_draft i18n key missing"
        assert "wizard-clear-draft" in html, "wizard-clear-draft button missing"

    def test_i18n_parity_for_new_prefixes(self) -> None:
        html = self._html()
        zh_m = _ZH_BLOCK_RE.search(html)
        en_m = _EN_BLOCK_RE.search(html)
        assert zh_m and en_m, "locale blocks missing"
        for prefix in ("wizard.", "trash.", "cd.", "picker."):
            key_re = re.compile(r'"(' + re.escape(prefix) + r'[^"]+)":')
            zh_keys = set(key_re.findall(zh_m.group(1)))
            en_keys = set(key_re.findall(en_m.group(1)))
            only_zh = zh_keys - en_keys
            only_en = en_keys - zh_keys
            assert not only_zh, f"{prefix} keys in zh-CN but not en-US: {sorted(only_zh)}"
            assert not only_en, f"{prefix} keys in en-US but not zh-CN: {sorted(only_en)}"

    def test_create_viewer_count_pinned(self) -> None:
        html = self._html()
        count = viewer_creation_site_count()
        assert count == 0, f"Expected exactly 0 viewer creation calls, found {count}"

    def test_candidate_api_preserved_and_used(self) -> None:
        # FE-07b: candidateApi is a transport delegation shim; the .status
        # attachment moved to js/client/transport.js (behavior pinned by
        # module test wizard_candidate_api.test.js err.status/err.body).
        html = self._html()
        assert "async function candidateApi" in html, "candidateApi function missing"
        assert "err.status = response.status" in html, \
            "transport must attach .status to thrown errors"

    def test_no_standalone_candidate_library_modal(self) -> None:
        html = self._html()
        assert 'id="candidate-library-modal"' not in html, \
            "standalone candidate-library-modal must be removed"
        assert 'id="btn-candidate-library"' not in html, \
            "standalone btn-candidate-library must be removed"

    def test_no_data_i18n_element_wraps_form_controls(self) -> None:
        html = self._html()
        pattern = re.compile(
            r"<(label|div|span)\b[^>]*data-i18n=[^>]*>(?:(?!</\1>).)*?"
            r"<(select|input|textarea)\b",
            re.DOTALL,
        )
        offenders = pattern.findall(html)
        assert not offenders, (
            f"data-i18n elements wrapping form controls found: {offenders[:5]} — "
            "the i18n textContent pass would gut these controls at startup"
        )


def test_candidate_details_js_exists() -> None:
    """candidate_details.js exists."""
    assert _CD_JS.is_file(), "frontend/js/candidate_details.js missing"


def test_candidate_details_js_passes_node_check() -> None:
    """candidate_details.js is valid JS (node --check)."""
    if not shutil.which("node"):
        pytest.skip("node not available")
    result = subprocess.run(
        ["node", "--check", str(_CD_JS)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"node --check failed:\n{result.stderr}"


def test_candidate_details_namespace_shape() -> None:
    """candidate_details.js exports VERSION, mount, VALID_CONCLUSIONS, destroy."""
    js = _CD_JS.read_text(encoding="utf-8")
    assert "VERSION" in js, "VERSION missing"
    assert "VALID_CONCLUSIONS" in js, "VALID_CONCLUSIONS missing"
    assert "window.ACPCandidateDetails" in js, "namespace export missing"
    assert "mount:" in js, "mount method missing"
    assert "destroy:" in js, "destroy method missing"


def test_candidate_details_four_state_enum() -> None:
    """VALID_CONCLUSIONS contains exactly the four backend enum values."""
    js = _CD_JS.read_text(encoding="utf-8")
    for conclusion in ("unreviewed", "recommended", "review", "not_recommended"):
        assert f'"{conclusion}"' in js, f"conclusion {conclusion!r} missing from VALID_CONCLUSIONS"
    assert '"已确认"' not in js, "must never label anything as 已确认"


def test_candidate_details_revision_fields() -> None:
    """metadata_revision and status_revision are both referenced."""
    js = _CD_JS.read_text(encoding="utf-8")
    assert "metadata_revision" in js, "metadata_revision field missing"
    assert "version_id" in js, "version_id field missing (assessment binding)"


def test_candidate_details_not_provided_fallback() -> None:
    """未提供 / Not provided fallback for missing data fields."""
    js = _CD_JS.read_text(encoding="utf-8")
    assert "not_provided" in js, "not_provided i18n key missing"
    assert "\u672a\u63d0\u4f9b" in js, "未提供 Chinese fallback missing"


def test_candidate_details_host_div_exists() -> None:
    """#candidate-details-host div exists in the wizard right column."""
    html = read_workbench_content()
    assert 'id="candidate-details-host"' in html, "candidate-details-host div missing"
    assert 'class="tiw-details-host"' in html, "tiw-details-host class missing"


def test_candidate_details_wired_in_on_preview_item() -> None:
    """candidateDetailsPanel.setEntry is called from both wizard onPreviewItem callbacks."""
    html = read_workbench_content()
    count = html.count("candidateDetailsPanel.setEntry(item)")
    assert count >= 2, \
        f"candidateDetailsPanel.setEntry(item) should appear in both onPreviewItem callbacks, found {count}"


def test_candidate_details_mount_called_in_open_modal() -> None:
    """ACPCandidateDetails.mount is called in openModal."""
    html = read_workbench_content()
    open_modal_start = html.index("function openModal()")
    next_fn = html.find("\n  function ", open_modal_start + 1)
    open_modal_section = html[open_modal_start:next_fn if next_fn != -1 else len(html)]
    assert "ACPCandidateDetails.mount" in open_modal_section, \
        "ACPCandidateDetails.mount must be called inside openModal"


def test_candidate_details_cleanup_in_close_modal() -> None:
    """candidateDetailsPanel.destroy() is called in closeModal."""
    html = read_workbench_content()
    close_start = html.index("function closeModal()")
    close_section = html[close_start:close_start + 300]
    assert "candidateDetailsPanel" in close_section, \
        "candidateDetailsPanel cleanup missing from closeModal"


def test_wizard_upload_parse_slice_owner_and_h3_guard() -> None:
    """T17 S1: upload/parse slice is owned by upload-parse.js; the parse token is
    invalidated on close and tab switch (H-3 late-parse guard) and the old global
    names stay as thin delegates in wizard.js."""
    module_path = FRONTEND_WORKBENCH_JS_DIR / "features" / "wizard" / "upload-parse.js"
    module_src = module_path.read_text(encoding="utf-8")
    assert "function invalidatePendingParse()" in module_src
    assert "parseSeqToken++" in module_src
    html = read_workbench_content()
    close_fn = html.split("function closeModal()", 1)[1].split("\nfunction ", 1)[0]
    assert "invalidatePendingParse()" in close_fn, \
        "H-3: closeModal must invalidate the pending parse"
    mode_fn = html.split("function setWizardInputMode(mode)", 1)[1].split("\nfunction ", 1)[0]
    assert "invalidatePendingParse()" in mode_fn, \
        "tab switch must invalidate the pending parse"
    wizard_src = (FRONTEND_WORKBENCH_JS_DIR / "features" / "wizard.js").read_text(encoding="utf-8")
    assert "\nvar parseSeqToken" not in wizard_src, "S1 state must live in upload-parse.js only"
    assert "function scheduleParseStructures() {" in wizard_src, "thin delegate missing"
    assert "window.ACPWizardUploadParse" in wizard_src, "delegates must route to the S1 module"


def test_wizard_preview_validate_slice_owner_and_h4_guard() -> None:
    """T18 S2: preview/validate state is owned by preview-validate.js; the batch
    preview is aborted on method-config open and on cancel/close (H-4 late-preview
    guard) and the old global names stay as thin delegates in wizard.js."""
    module_path = FRONTEND_WORKBENCH_JS_DIR / "features" / "wizard" / "preview-validate.js"
    module_src = module_path.read_text(encoding="utf-8")
    assert "function renderStructurePreview(" in module_src
    assert "function renderPreviewStructure3D(" in module_src
    assert "function createValidation(" in module_src
    assert "function createHessianPreview(" in module_src
    assert "function createBatchConfigPreview(" in module_src
    assert "function abortBatchPreviews()" in module_src
    assert "hessianPreviewToken" in module_src, "hessian preview state must live in the module"
    assert "_previewGeneration" in module_src, "batch preview generation must live in the module"
    html = read_workbench_content()
    wizard_src = (FRONTEND_WORKBENCH_JS_DIR / "features" / "wizard.js").read_text(encoding="utf-8")
    # S2 state lease: no closure-local validate/hessian/batch preview state left.
    assert "\n  var validateTimer" not in wizard_src, "validation state must live in the S2 module"
    assert "_lastValidationResult =" not in wizard_src, "validation state must live in the S2 module"
    assert "var hessianPreviewTimer" not in wizard_src, "hessian preview state must live in the S2 module"
    assert "var _previewGeneration" not in wizard_src, "batch preview state must live in the S2 module"
    # Old globals remain callable as thin delegates.
    for name in ("renderStructurePreview", "renderLoadedStructuresList",
                 "_refreshWizardInputForm", "renderPreviewStructure3D"):
        assert f"function {name}() {{\n  return window.ACPWizardPreviewValidate." in wizard_src, \
            f"thin delegate missing for {name}"
    # H-4: open + cancel/close abort any in-flight batch preview.
    open_fn = html.split("async function openMethodConfig()", 1)[1].split("\n  var title", 1)[0]
    assert "abortBatchPreviews()" in open_fn, "H-4: openMethodConfig must abort stale batch previews"
    revert_fn = html.split("function revertMethodDraft()", 1)[1].split("\n  }", 1)[0]
    assert "abortBatchPreviews()" in revert_fn, "H-4: revertMethodDraft must abort stale batch previews"


def test_wizard_draft_form_slice_owner_and_h2_guard() -> None:
    """T19 S3: draft/form state is owned by draft-form.js; explicit 0/false/null
    and the legacy draft reader are preserved; batch_role_defaults.js stays the
    only batch-role default source; H-2 clears the autosave timer + bumps the
    draft epoch on close and re-checks epoch/visibility inside the timer."""
    module_path = FRONTEND_WORKBENCH_JS_DIR / "features" / "wizard" / "draft-form.js"
    module_src = module_path.read_text(encoding="utf-8")
    # Single S3 state owner.
    for decl in ("const wizardState = {", "var wizardDraft = null;",
                 "var wizardDraftMeta = null;", "var wizardDraftSaveQueue =",
                 "var wizardDraftAutosaveTimer = null;", "var wizardDraftEpoch = 0;",
                 "var createWizardStep = 1;", "var ircSourcePreviewSequence = 0;"):
        assert decl in module_src, f"S3 state must live in draft-form.js: {decl!r}"
    # Legacy draft reader + explicit falsy handling.
    assert "function _captureWizardDraft()" in module_src
    assert "function _restoreWizardDraft(draft)" in module_src
    assert 'hasOwnProperty.call(saved, "checked")' in module_src, \
        "explicit false must be restored, never collapsed to a default"
    assert 'hasOwnProperty.call(saved, "value")' in module_src, \
        "explicit 0/null must be restored, never collapsed to a default"
    assert "legacy grid migration hint (T23f)" in module_src, "legacy draft migration hint preserved"
    # batch_role_defaults.js remains the only default-value source (no literals).
    assert "var _BATCH_ROLE_DEFAULTS = batchRoleStaticDefaults();" in module_src
    # H-2: close ends the autosave session; the timer re-checks epoch + visibility.
    close_fn = module_src.split("function closeModal()", 1)[1].split("\nfunction ", 1)[0]
    assert "wizardDraftEpoch++" in close_fn, "H-2: closeModal must bump the draft epoch"
    assert "clearTimeout(wizardDraftAutosaveTimer)" in close_fn, \
        "H-2: closeModal must clear the pending autosave timer"
    sched_fn = module_src.split("function _scheduleWizardDraftAutosave()", 1)[1].split(
        "\nfunction ", 1)[0]
    assert "scheduledEpoch !== wizardDraftEpoch" in sched_fn, \
        "H-2: the timer callback must re-check the draft epoch"
    assert 'modal.style.display === "none"' in sched_fn, \
        "H-2: the timer callback must re-check modal visibility"
    # Thin same-name delegates in wizard.js; no duplicated S3 state.
    wizard_src = (FRONTEND_WORKBENCH_JS_DIR / "features" / "wizard.js").read_text(encoding="utf-8")
    for name in ("openModal", "closeModal", "setWizardInputMode", "updateConfigCards",
                 "openMethodConfig", "loadDefaultMethodProfile", "_scheduleWizardDraftAutosave"):
        assert f"function {name}() {{\n  return window.ACPWizardDraftForm." in wizard_src, \
            f"thin delegate missing for {name}"
    for dup in ("\nconst wizardState = {", "\nvar wizardDraft = null;",
                "\nvar wizardDraftEpoch = 0;", "\nvar createWizardStep = 1;",
                "\nvar nmrExperimentMode =", "\nvar ircSourcePreviewSequence ="):
        assert dup not in wizard_src, f"S3 state must not be duplicated in wizard.js: {dup.strip()!r}"


def test_wizard_submit_edit_slice_owner_and_interception_guard() -> None:
    """T20 S4: the submit/edit-recalc slice is owned by submit-edit.js; the
    single submit entry, the per-branch payloads and the edit-mode POST /jobs
    interception (compat/api.js) are preserved; wizard.js keeps thin delegates
    and no duplicated submit body."""
    module_path = FRONTEND_WORKBENCH_JS_DIR / "features" / "wizard" / "submit-edit.js"
    module_src = module_path.read_text(encoding="utf-8")
    for name in ("submitStageWorkflowJob", "jobFormValidationFailed", "submitJobBatch",
                 "buildPESProtocolFromMethod", "submitPESsearchTask", "handleModalSubmit",
                 "submitJobModal", "initializeMethodDefaults"):
        assert f"function {name}(" in module_src, f"S4 body missing from submit-edit.js: {name}"
    assert "ACPJobEditor.checkAndSubmit()" in module_src, "handleModalSubmit editor branch missing"
    assert module_src.count('api("/jobs"') >= 4, "a submit branch stopped POSTing /jobs"
    assert module_src.count("_discardSavedWizardDraft()") >= 3, \
        "submit branches must still discard the saved wizard draft"
    assert "window.confirm(_confirmMsg)" in module_src, "PES submit confirmation removed"
    api_src = (FRONTEND_WORKBENCH_JS_DIR / "compat" / "api.js").read_text(encoding="utf-8")
    assert 'path === "/jobs" && opts && opts.method === "POST"' in api_src, \
        "edit POST /jobs interception condition changed"
    assert "ACPJobEditor.interceptJobCreate(_intercepted)" in api_src, \
        "edit POST /jobs interception target changed"
    wizard_src = (FRONTEND_WORKBENCH_JS_DIR / "features" / "wizard.js").read_text(encoding="utf-8")
    for name in ("submitStageWorkflowJob", "jobFormValidationFailed", "submitJobBatch",
                 "buildPESProtocolFromMethod", "submitPESsearchTask", "handleModalSubmit",
                 "submitJobModal", "initializeMethodDefaults"):
        assert f"function {name}() {{\n  return window.ACPWizardSubmitEdit." in wizard_src, \
            f"thin delegate missing for {name}"
    assert "if (createWizardStep < 3)" not in wizard_src, \
        "handleModalSubmit body must not be duplicated in wizard.js"
    assert "await submitJobBatch(batchBodies);" not in wizard_src, \
        "the batch submit loop must live in the S4 module only"


def test_wizard_new_and_edit_share_defaults_and_validation() -> None:
    """T21: create wizard and edit-recalculate editor share one defaults source
    and one submit/validation path.

    Defaults: both consumers call the contracts module's
    ``batchRoleStaticDefaults()`` factory (no role-default literal duplicated
    in either consumer). Validation/payload: the editor collects by running
    the page's own ``submitJobModal()``, so edit submissions use the same
    collectors, the same ``jobFormValidationFailed`` branch and the same
    ``POST /jobs`` chokepoint as a new task.
    """
    contracts = FRONTEND_WORKBENCH_JS_DIR / "contracts" / "batch_role_defaults.js"
    defaults_js = contracts.read_text(encoding="utf-8")
    s3_src = (
        FRONTEND_WORKBENCH_JS_DIR / "features" / "wizard" / "draft-form.js"
    ).read_text(encoding="utf-8")
    editor_src = (FRONTEND_JS_DIR / "job_editor.js").read_text(encoding="utf-8")
    for literal in ('opt_trust_radius: 0.3', 'opt_initial_hessian: "calculate"',
                    "opt_recalc_hess: 5", 'opt_convergence: "tight"'):
        assert literal in defaults_js, f"shared defaults factory lost {literal!r}"
        assert literal not in s3_src, (
            f"role-default literal duplicated in draft-form.js: {literal!r}"
        )
        assert literal not in editor_src, (
            f"role-default literal duplicated in job_editor.js: {literal!r}"
        )
    assert "var _BATCH_ROLE_DEFAULTS = batchRoleStaticDefaults();" in s3_src
    assert (
        "var batchRoleStaticDefaults = window.ACPWorkbench.contracts.batchRoleStaticDefaults;"
        in editor_src
    )
    collect_fn = editor_src.split("function collectViaPageCollectors(ctx)", 1)[1].split(
        "\n  function ", 1)[0]
    assert "submitJobModal()" in collect_fn, \
        "edit mode must collect through the page's own submitJobModal()"
    s4_src = (
        FRONTEND_WORKBENCH_JS_DIR / "features" / "wizard" / "submit-edit.js"
    ).read_text(encoding="utf-8")
    assert "function jobFormValidationFailed(message)" in s4_src
    assert "if (window.ACPJobEditor && ACPJobEditor.isCollecting()) throw new Error(message);" \
        in s4_src, "shared collecting-vs-alert validation branch missing"
    assert "return submitJobModal();" in s4_src, "single create submit entry missing"


def test_wizard_active_workflow_form_coverage() -> None:
    """T21: all 15 active workflows keep a catalog-driven wizard form.

    The four-slice extraction must not narrow the wizard to a subset: every
    active+visible catalog entry resolves to a method schema with at least one
    ``method_level`` (the rows ``openMethodConfig`` renders), the workflow list
    is filtered only by the catalog's ``visible`` flag, and the schema is read
    from ``methodCatalogCache`` by ``method_schema_id``. A hardcoded workflow
    whitelist (or a schema with no levels) would silently drop a form.
    """
    active = [
        wf for wf in WORKFLOW_CATALOG
        if wf.get("status") == "active" and wf.get("visible") is not False
    ]
    assert sorted(wf["id"] for wf in active) == [
        "BatchOptimize", "Confsearch", "OrcaGradient", "PESsearch",
        "XtbConstrainedOpt", "XtbPathSearch", "casscf", "frequency", "irc",
        "nmr", "optimize", "scan", "singlepoint", "tsmode", "xtb_optimize",
    ], "the 15 active workflows must all remain visible to the wizard form"
    for wf in active:
        schema = METHOD_SCHEMAS[wf["method_schema_id"]]
        assert schema.get("method_levels"), (
            f"active workflow {wf['id']!r} has no method_levels "
            f"(schema {wf['method_schema_id']!r})"
        )

    s3_src = (
        FRONTEND_WORKBENCH_JS_DIR / "features" / "wizard" / "draft-form.js"
    ).read_text(encoding="utf-8")
    for name in ("renderWorkflowDetail", "renderMethodDetail", "updateConfigCards",
                 "loadDefaultMethodProfile", "openMethodConfig"):
        assert f"function {name}(" in s3_src, f"S3 form path missing {name}"
    assert "workflowCatalogCache.filter(function(w) { return w.visible !== false; })" in s3_src, \
        "workflow list must stay catalog-visible driven"
    assert "var schema = (methodCatalogCache.method_schemas || {})[schemaId];" in s3_src, \
        "form fields must be read from the catalog schema, not a workflow whitelist"
    assert "w.id === id && w.status === \"active\"" in s3_src, \
        "workflow resolution must accept any active catalog entry"

    wizard_src = (FRONTEND_WORKBENCH_JS_DIR / "features" / "wizard.js").read_text(encoding="utf-8")
    for name in ("renderWorkflowDetail", "renderMethodDetail", "updateConfigCards",
                 "loadDefaultMethodProfile", "openMethodConfig"):
        assert f"function {name}() {{\n  return window.ACPWizardDraftForm." in wizard_src, \
            f"thin delegate missing for {name}"


def test_candidate_details_i18n_keys_in_both_locales() -> None:
    """cd.* i18n keys exist in both zh-CN and en-US blocks."""
    html = read_workbench_content()
    _cd_key_re = re.compile(r'"(cd\.[^"]+)":')
    zh_m = _ZH_BLOCK_RE.search(html)
    en_m = _EN_BLOCK_RE.search(html)
    assert zh_m, "zh-CN block not found"
    assert en_m, "en-US block not found"
    zh_keys = set(_cd_key_re.findall(zh_m.group(1)))
    en_keys = set(_cd_key_re.findall(en_m.group(1)))
    assert zh_keys, "No cd.* keys found in zh-CN"
    assert en_keys, "No cd.* keys found in en-US"
    only_zh = zh_keys - en_keys
    only_en = en_keys - zh_keys
    assert not only_zh, f"cd.* keys only in zh-CN: {only_zh}"
    assert not only_en, f"cd.* keys only in en-US: {only_en}"


def test_candidate_details_css_file_has_panel_styles() -> None:
    """task_input_workspace.css contains candidate details panel styles."""
    css = (FRONTEND_CSS_DIR / "task_input_workspace.css").read_text(encoding="utf-8")
    assert "cd-row" in css, "cd-row CSS class missing"
    assert "cd-save-btn" in css, "cd-save-btn CSS class missing"
    assert "cd-conflict" in css, "cd-conflict CSS class missing"


def test_candidate_details_set_entry_and_destroy() -> None:
    """setEntry and destroy are instance methods on the mounted component."""
    js = _CD_JS.read_text(encoding="utf-8")
    assert "setEntry:" in js or "setEntry :" in js, "setEntry instance method missing"
    assert "destroy:" in js or "destroy :" in js, "destroy instance method missing"
    assert "refresh:" in js or "refresh :" in js, "refresh instance method missing"


def test_candidate_details_assessment_post_carries_version_id() -> None:
    """Assessment POST payload includes version_id."""
    js = _CD_JS.read_text(encoding="utf-8")
    assert "version_id" in js, "version_id missing from assessment payload"


def test_candidate_details_patch_expected_revision() -> None:
    """Metadata PATCH payload includes expected_revision."""
    js = _CD_JS.read_text(encoding="utf-8")
    assert "expected_revision" in js, "expected_revision missing from metadata PATCH payload"


def test_candidate_details_409_conflict_handling() -> None:
    """409 responses are handled with conflict banner (not auto-overwrite)."""
    js = _CD_JS.read_text(encoding="utf-8")
    assert "409" in js or "status === 409" in js, "409 status handling missing"
    assert "conflict" in js.lower(), "conflict handling missing"


def test_wizard_i18n_parity_across_locales() -> None:
    """Every wizard.*/cd.*/trash.*/modal.mode_* key exists in both zh-CN and en-US."""
    html = read_workbench_content()
    zh_m = _ZH_BLOCK_RE.search(html)
    en_m = _EN_BLOCK_RE.search(html)
    assert zh_m, "zh-CN block not found"
    assert en_m, "en-US block not found"
    zh_block, en_block = zh_m.group(1), en_m.group(1)
    for prefix in _WIZARD_I18N_PREFIXES:
        pat = re.compile(r'"(' + re.escape(prefix) + r'[^"]+)":')
        zh_keys = set(pat.findall(zh_block))
        en_keys = set(pat.findall(en_block))
        only_zh = zh_keys - en_keys
        only_en = en_keys - zh_keys
        assert not only_zh, f"{prefix}* keys only in zh-CN: {only_zh}"
        assert not only_en, f"{prefix}* keys only in en-US: {only_en}"


def test_wizard_step2_step3_inside_tiw_grid() -> None:
    """config-cards-row, task-info-details, create-review are children of .tiw-left."""
    html = read_workbench_content()
    grid_start = html.index('class="tiw-grid"')
    grid_end = html.index("<!-- /tiw-grid -->", grid_start)
    grid_html = html[grid_start:grid_end]
    left_start = grid_html.index('class="tiw-left"')
    left_end = grid_html.index("<!-- /tiw-left -->", left_start)
    left_html = grid_html[left_start:left_end]
    assert 'class="config-cards-row"' in left_html, "config-cards-row not inside tiw-left"
    assert 'class="task-info-details"' in left_html, "task-info-details not inside tiw-left"
    assert 'id="create-review"' in left_html, "create-review not inside tiw-left"


def test_wizard_step_summary_in_tiw_right() -> None:
    """wizard-step-summary div exists inside tiw-right for steps 2/3 compact view."""
    html = read_workbench_content()
    grid_start = html.index('class="tiw-grid"')
    grid_end = html.index("<!-- /tiw-grid -->", grid_start)
    grid_html = html[grid_start:grid_end]
    right_start = grid_html.index('class="tiw-right"')
    right_end = grid_html.index("<!-- /tiw-right -->", right_start)
    right_html = grid_html[right_start:right_end]
    assert 'id="wizard-step-summary"' in right_html, "wizard-step-summary not inside tiw-right"


def test_wizard_step_css_rules_target_tiw_children() -> None:
    """Step visibility CSS rules use .tiw-left > and .tiw-right > child selectors."""
    css = read_workbench_css()
    assert '.tiw-left > .config-cards-row' in css, "step CSS missing .tiw-left > .config-cards-row"
    assert '.tiw-right > .wizard-step-summary' in css, "step CSS missing .tiw-right > .wizard-step-summary"
    assert 'data-create-step="2"] .tiw-left > .source-section-header' in css, \
        "step-2 CSS missing source-section-header hide rule"
    assert 'data-create-step="3"] .tiw-left > .create-review' in css, \
        "step-3 CSS missing create-review show rule"


def test_wizard_no_standalone_config_cards_outside_grid() -> None:
    """config-cards-row does NOT appear as a direct child of .modal-body."""
    html = read_workbench_content()
    body_start = html.index('<div class="modal-body">')
    body_end = html.index("</div><!-- /modal-body -->", body_start) if "<!-- /modal-body -->" in html[body_start:body_start+5000] else -1
    if body_end < 0:
        body_end = html.index('<div class="modal-footer">', body_start)
    body_html = html[body_start:body_end]
    grid_start = body_html.index('class="tiw-grid"')
    after_grid = body_html[grid_start:]
    grid_end_marker = after_grid.index("<!-- /tiw-grid -->")
    after_grid_only = after_grid[grid_end_marker:]
    assert 'class="config-cards-row"' not in after_grid_only, \
        "config-cards-row found outside tiw-grid in modal-body"


def test_editor_source_tab_mapping_preserved() -> None:
    """Legacy candidate drafts normalize to the unified task/library mode."""
    html = read_workbench_content()
    assert 'if (mode === "candidate") mode = "task"' in html, \
        "legacy candidate draft normalization missing"
    js = _SP_JS.read_text(encoding="utf-8") if _SP_JS.exists() else ""
    je = (FRONTEND_JS_DIR / "job_editor.js").read_text(encoding="utf-8")
    assert "SOURCE_TAB_KINDS" in je, "SOURCE_TAB_KINDS missing from job_editor.js"
    assert "{ task: 1, structure: 1, upload: 1 }" in je, \
        "SOURCE_TAB_KINDS mapping changed"


def test_editor_check_and_submit_interception() -> None:
    """handleModalSubmit delegates to ACPJobEditor.checkAndSubmit when editor is active."""
    html = read_workbench_content()
    submit_fn_start = html.index("function handleModalSubmit()")
    submit_fn = html[submit_fn_start:submit_fn_start + 300]
    assert "ACPJobEditor.checkAndSubmit()" in submit_fn, \
        "handleModalSubmit missing checkAndSubmit delegation"


def test_editor_cancel_routes_through_guarded_close() -> None:
    """closeJobModalGuarded routes through ACPJobEditor.cancelEditor(false)."""
    html = read_workbench_content()
    guard_start = html.index("function closeJobModalGuarded()")
    guard_fn = html[guard_start:guard_start + 400]
    assert "ACPJobEditor.cancelEditor(false)" in guard_fn, \
        "closeJobModalGuarded missing cancelEditor routing"


def test_wizard_submit_button_uses_i18n() -> None:
    """Submit button text uses t() calls, not hard-coded strings."""
    html = read_workbench_content()
    step_fn_start = html.index("function setCreateWizardStep(step)")
    step_fn = html[step_fn_start:step_fn_start + 800]
    assert 't("wizard.submit_task")' in step_fn, "submit button not using i18n"
    assert 't("wizard.next_step")' in step_fn, "next button not using i18n"


def test_wizard_review_uses_i18n() -> None:
    """renderCreateReview uses t() calls for all labels, not hard-coded Chinese."""
    html = read_workbench_content()
    review_fn_start = html.index("function renderCreateReview()")
    review_fn_end = html.index("function renderStepCompactSummary()", review_fn_start)
    review_fn = html[review_fn_start:review_fn_end]
    assert 't("wizard.review_project")' in review_fn, "review_project i18n missing"
    assert 't("wizard.review_input")' in review_fn, "review_input i18n missing"
    assert 't("wizard.review_workflow")' in review_fn, "review_workflow i18n missing"
    assert 't("wizard.review_risk")' in review_fn, "review_risk i18n missing"


# ---------------------------------------------------------------------------
# T24 — method-validation warnings are rendered end to end
# ---------------------------------------------------------------------------


def test_validate_method_warnings_rendered_in_modal_and_summary() -> None:
    html = read_workbench_content()

    # Modal carries a dedicated (amber) warnings block beside the errors block.
    assert 'id="mc-validation-warnings"' in html
    assert "mc-validation-warnings" in html  # CSS class exists

    # doValidate persists the server warnings on the wizard method state so
    # the summary card can render them after the modal closes.
    assert "wizardState.method.warnings = methodWarnings" in html

    # refreshMethodValidationState renders the warnings list from the last
    # /validate-method response (escapeHtml'd, ⚠-prefixed), hiding when empty.
    assert 'document.getElementById("mc-validation-warnings")' in html
    assert "(vr && vr.warnings) || []" in html
    assert '"<div>\\u26a0 " + escapeHtml(w)' in html

    # The wizard summary (renderMethodDetail) appends the same warnings.
    assert "(wizardState.method && wizardState.method.warnings) || []" in html
    assert 'warnItem.className = "method-detail-warning"' in html


# ── Executed scan submission: real wizard body -> real v1 -> runner argv ─
# (todo 13 / GAP-8 acceptance).  Reuses T9's node captured-body helper and
# fake-scheduler seam from tests/test_scan_workflow.py; production code is
# never touched and no live scan process can spawn.


def test_executed_wizard_body_reaches_v1_validation_and_runner_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run the page's REAL builder output through real v1 + fake scheduler.

    The node harness executes ``buildScanWizardBodyParts`` from the shipped
    HTML (1-based fields -> 0-based wire, distinct values so the chain is
    proven to carry data, not constants); the resulting ``/api/v1/jobs``
    body is posted through the real FastAPI route (real
    ``_validate_scan_submission``), the scheduler seam captures the JobSpec
    instead of queueing, and the real ``JobRunner._build_cmd`` must emit
    ``--coordinate <a,b,start,end>`` and ``--scan-points <n>``.
    """
    import os

    from fastapi.testclient import TestClient

    from acp.scheduler.runner import JobRunner
    from tests.test_scan_workflow import (
        XYZ_TRIATOMIC,
        _fake_scheduler,
        _run_wizard_builder_in_node,
    )

    built = _run_wizard_builder_in_node(
        [
            {
                "name": "accepted",
                "fields": {
                    "atomA": "2",
                    "atomB": "3",
                    "start": "1.5",
                    "end": "2.5",
                    "points": "7",
                },
                "structure": {"xyz": XYZ_TRIATOMIC},
            }
        ]
    )[0]["result"]
    assert built["ok"] is True, built
    # Captured builder output: 1-based display fields became the 0-based wire form.
    assert built["input"]["scan_coordinates"] == ["1,2,1.5,2.5"]
    assert built["method"]["scan_points"] == 7

    body = {
        "workflow": "scan",
        "molecule_name": "wizard_scan",
        "input": {
            "source_type": "xyz_text",
            "source": XYZ_TRIATOMIC,
            "charge": 0,
            "multiplicity": 1,
            "scan_coordinates": built["input"]["scan_coordinates"],
        },
        "method": {
            "schema_id": "dft_scan",
            "profile_id": "default",
            "scan_points": built["method"]["scan_points"],
            "levels": {"scan": dict(built["mirror"], engine="orca", functional="r2SCAN-3c")},
        },
        "resources": {"nproc": 4, "mem": "8GB"},
        "execution_mode": "local",
    }

    os.environ["ACP_RUN_ROOT"] = str(tmp_path)
    from acp.api.server import create_app

    captured: list = []
    with TestClient(create_app(run_root=tmp_path, max_running=1)) as client:
        _fake_scheduler(monkeypatch, captured)
        response = client.post("/api/v1/jobs", json=body)
        assert response.status_code == 201, response.text

    # Fake scheduler: exactly one JobSpec captured, nothing queued for real work.
    assert len(captured) == 1
    spec = captured[0]
    assert spec.workflow == "scan"
    assert spec.input["scan_coordinates"] == ["1,2,1.5,2.5"]
    assert spec.method["scan_points"] == 7

    # Real runner argv builder (pure — spawns nothing).
    cmd = JobRunner(python_executable="python")._build_cmd(spec, tmp_path, input_path="input.xyz")
    coordinate_at = cmd.index("--coordinate")
    assert cmd[coordinate_at + 1] == "1,2,1.5,2.5"
    points_at = cmd.index("--scan-points")
    assert cmd[points_at + 1] == "7"


# ---------------------------------------------------------------------------
# S5 — Structure upload validation (STRUCTURE_UPLOAD_EXTS + reject helpers)
# ---------------------------------------------------------------------------

_EXPECTED_STRUCTURE_EXTS = [
    ".xyz",
    ".sdf",
    ".sd",
    ".mol",
    ".gjf",
    ".com",
    ".inp",
    ".log",
    ".out",
]


def _extract_all_modal_keys(html: str, block_re: re.Pattern[str]) -> set[str]:  # type: ignore[type-arg]
    """Extract all modal.* i18n keys from a single locale block."""
    modal_key_re = re.compile(r'"(modal\.[^"]+)":')
    m = block_re.search(html)
    if not m:
        return set()
    return set(modal_key_re.findall(m.group(1)))


# ── Candidate Details panel (§5.4) ──────────────────────────────────────
_CD_JS = FRONTEND_JS_DIR / "candidate_details.js"


# ── §8: step-2/3 frame + wizard i18n parity + editor mapping ──────────


_WIZARD_I18N_KEY_RE = re.compile(r'"(wizard\.[^"]+)":')


_WIZARD_I18N_PREFIXES = ("wizard.", "cd.", "trash.", "modal.mode_")
