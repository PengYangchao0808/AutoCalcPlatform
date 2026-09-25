"""Tests for the ACP Structure Source Picker module (P4).

Covers:
- Module file existence and namespace declaration
- JS syntax validation (node --check)
- Integration site references in ACP_Workbench_v2.html
- Selection API markers
- Batch endpoint literal
- PATCH literal
- Legacy fallback marker
- localStorage pref key
- i18n parity for every new key in BOTH dicts
- Availability badge keys
- CSS file existence and HTML link
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[1]
FRONTEND = REPO_ROOT / "frontend" / "ACP_Workbench_v2.html"
FRONTEND_JS_DIR = REPO_ROOT / "frontend" / "js"
FRONTEND_CSS_DIR = REPO_ROOT / "frontend" / "css"
PICKER_JS = FRONTEND_JS_DIR / "structure_source_picker.js"
PICKER_CSS = FRONTEND_CSS_DIR / "structure_source_picker.css"

# i18n key extraction patterns (same approach as test_frontend_sync.py)
_PICKER_I18N_KEY_RE = re.compile(r'"((?:picker)\.[^"]+)":')
_ZH_BLOCK_RE = re.compile(r'"zh-CN":\s*\{(.*?)\n\s*"en-US":', re.DOTALL)
_EN_BLOCK_RE = re.compile(r'"en-US":\s*\{(.*?)(?:\n\s*\};)', re.DOTALL)


def _extract_picker_keys(html: str, block_re: re.Pattern[str]) -> set[str]:  # type: ignore[type-arg]
    """Extract picker.* i18n keys from a single locale block."""
    m = block_re.search(html)
    if not m:
        return set()
    return set(_PICKER_I18N_KEY_RE.findall(m.group(1)))


# ---------------------------------------------------------------------------
# Module existence & syntax
# ---------------------------------------------------------------------------


def test_picker_js_file_exists() -> None:
    """Module file exists and is non-empty."""
    assert PICKER_JS.exists(), "structure_source_picker.js missing"
    content = PICKER_JS.read_text(encoding="utf-8")
    assert len(content) > 0, "structure_source_picker.js is empty"


def test_picker_js_passes_node_check() -> None:
    """Module passes node --check (no syntax errors)."""
    if not shutil.which("node"):
        pytest.skip("node not available")
    result = subprocess.run(
        ["node", "--check", str(PICKER_JS)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"node --check failed:\n{result.stderr}"


def test_picker_js_has_namespace() -> None:
    """Module declares window.ACPSourcePicker namespace."""
    js = PICKER_JS.read_text(encoding="utf-8")
    assert "window.ACPSourcePicker" in js, "ACPSourcePicker namespace not declared"


def test_picker_js_has_mount() -> None:
    """Module exposes mount() method."""
    js = PICKER_JS.read_text(encoding="utf-8")
    assert "mount:" in js or "mount =" in js, "mount method not found"


def test_picker_js_has_instance_methods() -> None:
    """Module exposes instance methods: getSelection, clearSelection, refresh, destroy."""
    js = PICKER_JS.read_text(encoding="utf-8")
    for method in ("getSelection", "clearSelection", "refresh", "destroy"):
        assert method in js, f"Instance method {method} not found"


def test_picker_js_has_set_dedupe() -> None:
    """Selection model uses Set for deduplication."""
    js = PICKER_JS.read_text(encoding="utf-8")
    assert "new Set()" in js, "Set-based selection model not found"


# ---------------------------------------------------------------------------
# CSS file
# ---------------------------------------------------------------------------


def test_picker_css_file_exists() -> None:
    """CSS file exists and is non-empty."""
    assert PICKER_CSS.exists(), "structure_source_picker.css missing"
    content = PICKER_CSS.read_text(encoding="utf-8")
    assert len(content) > 0, "structure_source_picker.css is empty"


def test_picker_css_linked_in_html() -> None:
    """CSS file is linked in ACP_Workbench_v2.html."""
    html = FRONTEND.read_text(encoding="utf-8")
    assert '<link rel="stylesheet" href="css/structure_source_picker.css">' in html, (
        "structure_source_picker.css not linked in HTML"
    )


def test_editor_density_uses_compact_inline_controls() -> None:
    """The editor picker keeps toolbar, list, and pagination in stable rows."""
    js = PICKER_JS.read_text(encoding="utf-8")
    css = PICKER_CSS.read_text(encoding="utf-8")
    assert 'if (density === "editor") {' in js
    assert "filterRow.appendChild(sortSelect)" in js
    assert 'sortSelect.setAttribute("aria-label", _t("picker.filter.sort"))' in js
    assert ".sp-density-editor .sp-search-row" in css
    assert "grid-template-rows: 40px minmax(288px, 1fr) 36px" in css
    assert ".sp-density-editor .sp-toolbar" in css
    assert ".sp-density-editor .sp-project-select { width: 100px; }" in css
    assert ".sp-density-editor .sp-role-select { width: 76px; }" in css
    assert ".sp-density-editor .sp-sort-select { width: 104px; }" in css
    assert "min-height: 58px;" in css
    assert ".sp-density-editor .sp-row-copy" in css
    assert 'class="sp-row-cb"' in js
    assert "grid-template-columns: 22px minmax(0, 1fr) auto auto" in css
    assert 'refreshButton.setAttribute("aria-label", _t("picker.refresh"))' in js
    assert '<span class="sp-page-current"' in js
    assert 'if (density !== "editor") {' in js
    assert "_renderEditorRow" in js


def test_picker_js_loaded_in_html() -> None:
    """JS file is loaded via script tag in ACP_Workbench_v2.html."""
    html = FRONTEND.read_text(encoding="utf-8")
    assert '<script src="js/structure_source_picker.js"></script>' in html, (
        "structure_source_picker.js not loaded in HTML"
    )


# ---------------------------------------------------------------------------
# Integration sites
# ---------------------------------------------------------------------------


def test_results_tab_references_picker() -> None:
    """Results tab integration: loadStructureSources references ACPSourcePicker."""
    html = FRONTEND.read_text(encoding="utf-8")
    # The loadStructureSources function should reference ACPSourcePicker
    section = html.split("async function loadStructureSources")[1].split("function ")[0]
    assert "ACPSourcePicker" in section, "loadStructureSources does not reference ACPSourcePicker"


def test_batch_picker_references_picker() -> None:
    """BatchOptimize picker integration: stageBatchFromResults references ACPSourcePicker."""
    html = FRONTEND.read_text(encoding="utf-8")
    section = html.split("async function stageBatchFromResults")[1].split("function ")[0]
    assert "ACPSourcePicker" in section, "stageBatchFromResults does not reference ACPSourcePicker"


def test_s2scan_references_picker() -> None:
    """S2 scan picker integration: s2scanLoadAssets references ACPSourcePicker."""
    html = FRONTEND.read_text(encoding="utf-8")
    section = html.split("async function s2scanLoadAssets")[1].split("function ")[0]
    assert "ACPSourcePicker" in section, "s2scanLoadAssets does not reference ACPSourcePicker"


def test_results_picker_variable_declared() -> None:
    """resultsPicker variable declared for results tab picker instance."""
    html = FRONTEND.read_text(encoding="utf-8")
    assert "var resultsPicker" in html, "resultsPicker variable not declared"


def test_results_picker_cleanup_in_reset() -> None:
    """resetResultsPanel destroys picker instance."""
    html = FRONTEND.read_text(encoding="utf-8")
    section = html.split("function resetResultsPanel")[1].split("function ")[0]
    assert "resultsPicker" in section, "resetResultsPanel does not reference resultsPicker"
    assert "destroy" in section, "resetResultsPanel does not call destroy()"


# ---------------------------------------------------------------------------
# API endpoints & literals
# ---------------------------------------------------------------------------


def test_batch_metadata_endpoint_literal() -> None:
    """Batch metadata endpoint literal present in JS."""
    js = PICKER_JS.read_text(encoding="utf-8")
    assert "/structure-sources/batch-metadata" in js, "batch-metadata endpoint literal missing"


def test_patch_metadata_endpoint_literal() -> None:
    """PATCH metadata endpoint literal present in JS."""
    js = PICKER_JS.read_text(encoding="utf-8")
    assert "/structure-sources/" in js, "structure-sources endpoint literal missing"
    assert '"PATCH"' in js or "'PATCH'" in js, "PATCH method literal missing"


def test_legacy_fallback_marker() -> None:
    """Legacy fallback: structure-sources/recent endpoint literal present."""
    js = PICKER_JS.read_text(encoding="utf-8")
    assert "structure-sources/recent" in js, "Legacy fallback endpoint missing"


def test_localstorage_pref_key() -> None:
    """localStorage pref key 'acp.picker.view.' present."""
    js = PICKER_JS.read_text(encoding="utf-8")
    assert "acp.picker.view." in js, "localStorage pref key missing"


# ---------------------------------------------------------------------------
# i18n parity
# ---------------------------------------------------------------------------

# All picker.* keys that must exist in BOTH zh-CN and en-US dicts
EXPECTED_PICKER_KEYS = {
    "picker.search_ph",
    "picker.refresh",
    "picker.filter.project",
    "picker.filter.all_projects",
    "picker.filter.target_project",
    "picker.filter.role",
    "picker.filter.tags",
    "picker.filter.sort",
    "picker.filter.group",
    "picker.filter.tag_match_any",
    "picker.filter.tag_match_all",
    "picker.filter.tag_match_toggle",
    "picker.role.all",
    "picker.role.ts",
    "picker.role.int",
    "picker.role.unlabeled",
    "picker.sort.produced_desc",
    "picker.sort.produced_asc",
    "picker.sort.name_asc",
    "picker.sort.name_desc",
    "picker.sort.organized_desc",
    "picker.group.none",
    "picker.group.job",
    "picker.group.tag",
    "picker.group.role",
    "picker.empty",
    "picker.loading",
    "picker.error",
    "picker.load",
    "picker.load_disabled_reason",
    "picker.source_label",
    "picker.remote",
    "picker.retry",
    "picker.indexing",
    "picker.total_count",
    "picker.prev_page",
    "picker.next_page",
    "picker.select_page",
    "picker.select_item",
    "picker.selected_count",
    "picker.hidden_selected",
    "picker.selection_max",
    "picker.availability.pending_sync",
    "picker.availability.pending_fetch",
    "picker.availability.unavailable",
    "picker.rename.title",
    "picker.rename.short",
    "picker.rename.default_label",
    "picker.rename.hint",
    "picker.rename.restore",
    "picker.rename.cancel",
    "picker.rename.save",
    "picker.rename.conflict",
    "picker.rename.invalid",
    "picker.rename.failed",
    "picker.tags.title",
    "picker.tags.short",
    "picker.tags.input_ph",
    "picker.tags.add",
    "picker.tags.remove",
    "picker.tags.close",
    "picker.tags.hint",
    "picker.tags.none",
    "picker.tags.too_long",
    "picker.tags.max_count",
    "picker.tags.failed",
    "picker.batch.add_tags",
    "picker.batch.remove_tags",
    "picker.batch.clear",
    "picker.batch.load",
    "picker.batch.apply",
    "picker.batch.tag_hint",
    "picker.batch.max_notice",
    "picker.batch.partial_fail",
    "picker.batch.all_failed",
    "picker.legacy_mode",
}


def test_picker_i18n_parity() -> None:
    """All picker.* i18n keys present in both zh-CN and en-US dicts."""
    html = FRONTEND.read_text(encoding="utf-8")

    # Verify keys exist at all
    for key in EXPECTED_PICKER_KEYS:
        assert f'"{key}":' in html, f"i18n key {key} missing from HTML"

    # Verify parity per locale block
    zh_keys = _extract_picker_keys(html, _ZH_BLOCK_RE)
    en_keys = _extract_picker_keys(html, _EN_BLOCK_RE)
    missing_zh = EXPECTED_PICKER_KEYS - zh_keys
    missing_en = EXPECTED_PICKER_KEYS - en_keys
    assert not missing_zh, f"zh-CN missing picker keys: {sorted(missing_zh)}"
    assert not missing_en, f"en-US missing picker keys: {sorted(missing_en)}"


def test_availability_badge_keys_present() -> None:
    """Availability badge i18n keys present in both dicts."""
    html = FRONTEND.read_text(encoding="utf-8")
    for key in (
        "picker.availability.pending_sync",
        "picker.availability.pending_fetch",
        "picker.availability.unavailable",
    ):
        assert f'"{key}":' in html, f"Availability badge key {key} missing"


def test_picker_keys_used_in_js() -> None:
    """Key picker i18n keys are referenced in the JS module via _t()."""
    js = PICKER_JS.read_text(encoding="utf-8")
    # Spot-check that the JS references these keys
    for key in (
        "picker.search_ph",
        "picker.empty",
        "picker.loading",
        "picker.error",
        "picker.load",
        "picker.rename.title",
        "picker.tags.title",
        "picker.batch.add_tags",
        "picker.batch.clear",
        "picker.batch.load",
        "picker.legacy_mode",
        "picker.indexing",
        "picker.selected_count",
        "picker.hidden_selected",
        "picker.select_page",
    ):
        assert f'"{key}"' in js, f"i18n key {key} not referenced in JS module"


# ---------------------------------------------------------------------------
# Cleanup in integration sites
# ---------------------------------------------------------------------------


def test_s2scan_close_destroys_picker() -> None:
    """s2scanClose destroys picker instance."""
    html = FRONTEND.read_text(encoding="utf-8")
    section = html.split("function s2scanClose()")[1].split("function ")[0]
    assert "_acpPicker" in section, "s2scanClose does not clean up picker"
    assert "destroy" in section, "s2scanClose does not call destroy()"


# ---------------------------------------------------------------------------
# Existing test_frontend_sync.py compatibility
# ---------------------------------------------------------------------------


def test_existing_frontier_files_still_exist() -> None:
    """Existing frontend files not removed by picker addition."""
    for name in (
        "geometry_store.js",
        "structure_viewer.js",
        "structure_editor.js",
        "vibration_viewer.js",
    ):
        path = FRONTEND_JS_DIR / name
        assert path.exists(), f"Existing frontend file missing: {name}"


def test_structure_viewer_css_still_linked() -> None:
    """structure_viewer.css still linked in HTML."""
    html = FRONTEND.read_text(encoding="utf-8")
    assert '<link rel="stylesheet" href="css/structure_viewer.css">' in html


def test_picker_css_loads_after_structure_viewer_css() -> None:
    """picker CSS loads after structure_viewer.css (correct cascade order)."""
    html = FRONTEND.read_text(encoding="utf-8")
    sv_pos = html.index('<link rel="stylesheet" href="css/structure_viewer.css">')
    sp_pos = html.index('<link rel="stylesheet" href="css/structure_source_picker.css">')
    assert sp_pos > sv_pos, "picker CSS must load after structure_viewer.css"


def test_picker_js_loads_after_vibration_viewer() -> None:
    """picker JS loads after vibration_viewer.js (correct load order)."""
    html = FRONTEND.read_text(encoding="utf-8")
    vib_pos = html.index('<script src="js/vibration_viewer.js"></script>')
    sp_pos = html.index('<script src="js/structure_source_picker.js"></script>')
    assert sp_pos > vib_pos, "picker JS must load after vibration_viewer.js"


# ---------------------------------------------------------------------------
# New picker API: setSelection, getSelectedUids, onRowMenu
# ---------------------------------------------------------------------------


def test_picker_js_has_setSelection() -> None:
    """setSelection method present in returned instance object."""
    js = PICKER_JS.read_text(encoding="utf-8")
    assert "setSelection: setSelection" in js, "setSelection not in returned instance"


def test_picker_js_has_getSelectedUids() -> None:
    """getSelectedUids method present in returned instance object."""
    js = PICKER_JS.read_text(encoding="utf-8")
    assert "getSelectedUids: getSelectedUids" in js, "getSelectedUids not in returned instance"


def test_picker_js_has_onRowMenu_option() -> None:
    """onRowMenu option recognized in the option table."""
    js = PICKER_JS.read_text(encoding="utf-8")
    assert "opts.onRowMenu" in js, "onRowMenu option not recognized"
    assert "var onRowMenu" in js, "onRowMenu variable not declared"


def test_picker_js_onRowMenu_row_button_branch() -> None:
    """onRowMenu branch present in row-button click handler."""
    js = PICKER_JS.read_text(encoding="utf-8")
    assert "if (onRowMenu)" in js, "onRowMenu branch missing in row-button handler"
    assert "onRowMenu(_snapshotItem(item), btn)" in js, "onRowMenu call signature incorrect"


def test_picker_js_onRowMenu_falls_back_to_onTrashItem() -> None:
    """When onRowMenu absent, onTrashItem still invoked (backward compat)."""
    js = PICKER_JS.read_text(encoding="utf-8")
    assert "else if (onTrashItem)" in js, "onTrashItem fallback missing"


def test_picker_js_onRowMenu_button_rendered_when_either_present() -> None:
    """Row button renders when onRowMenu OR onTrashItem is provided."""
    js = PICKER_JS.read_text(encoding="utf-8")
    assert "onRowMenu || onTrashItem" in js, "Button render guard must check onRowMenu || onTrashItem"


def test_setSelection_function_defined() -> None:
    """setSelection function body exists in the picker module."""
    js = PICKER_JS.read_text(encoding="utf-8")
    assert "function setSelection(items, opts)" in js, "setSelection function not defined"


def test_getSelectedUids_function_defined() -> None:
    """getSelectedUids function body exists in the picker module."""
    js = PICKER_JS.read_text(encoding="utf-8")
    assert "function getSelectedUids()" in js, "getSelectedUids function not defined"


def test_setSelection_silent_default() -> None:
    """setSelection defaults to silent mode (does not call onChanged)."""
    js = PICKER_JS.read_text(encoding="utf-8")
    assert "opts.silent !== false" in js, "setSelection silent default check missing"


# ---------------------------------------------------------------------------
# New scaffold files: task_input_workspace.js, candidate_details.js, CSS
# ---------------------------------------------------------------------------


TASK_INPUT_WORKSPACE_JS = FRONTEND_JS_DIR / "task_input_workspace.js"
CANDIDATE_DETAILS_JS = FRONTEND_JS_DIR / "candidate_details.js"
TASK_INPUT_WORKSPACE_CSS = FRONTEND_CSS_DIR / "task_input_workspace.css"


def test_task_input_workspace_js_exists() -> None:
    """task_input_workspace.js scaffold file exists."""
    assert TASK_INPUT_WORKSPACE_JS.exists(), "task_input_workspace.js missing"


def test_candidate_details_js_exists() -> None:
    """candidate_details.js scaffold file exists."""
    assert CANDIDATE_DETAILS_JS.exists(), "candidate_details.js missing"


def test_task_input_workspace_js_passes_node_check() -> None:
    """task_input_workspace.js passes node --check."""
    if not shutil.which("node"):
        pytest.skip("node not available")
    result = subprocess.run(
        ["node", "--check", str(TASK_INPUT_WORKSPACE_JS)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"node --check failed:\n{result.stderr}"


def test_candidate_details_js_passes_node_check() -> None:
    """candidate_details.js passes node --check."""
    if not shutil.which("node"):
        pytest.skip("node not available")
    result = subprocess.run(
        ["node", "--check", str(CANDIDATE_DETAILS_JS)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"node --check failed:\n{result.stderr}"


def test_task_input_workspace_js_has_namespace() -> None:
    """task_input_workspace.js declares window.ACPTaskInputWorkspace."""
    js = TASK_INPUT_WORKSPACE_JS.read_text(encoding="utf-8")
    assert "window.ACPTaskInputWorkspace" in js, "ACPTaskInputWorkspace namespace not declared"


def test_task_input_workspace_js_has_mount() -> None:
    """task_input_workspace.js exposes mount() method."""
    js = TASK_INPUT_WORKSPACE_JS.read_text(encoding="utf-8")
    assert "mount:" in js or "mount =" in js, "mount method not found"


def test_task_input_workspace_js_version() -> None:
    """task_input_workspace.js declares VERSION 0.1.0."""
    js = TASK_INPUT_WORKSPACE_JS.read_text(encoding="utf-8")
    assert '"0.2.0"' in js, "VERSION 0.2.0 not declared"


def test_candidate_details_js_has_namespace() -> None:
    """candidate_details.js declares window.ACPCandidateDetails."""
    js = CANDIDATE_DETAILS_JS.read_text(encoding="utf-8")
    assert "window.ACPCandidateDetails" in js, "ACPCandidateDetails namespace not declared"


def test_candidate_details_js_has_setEntry() -> None:
    """candidate_details.js exposes setEntry() method on instance."""
    js = CANDIDATE_DETAILS_JS.read_text(encoding="utf-8")
    assert "setEntry:" in js or "setEntry =" in js, "setEntry method not found"


def test_task_input_workspace_css_exists() -> None:
    """task_input_workspace.css exists and is non-empty."""
    assert TASK_INPUT_WORKSPACE_CSS.exists(), "task_input_workspace.css missing"
    content = TASK_INPUT_WORKSPACE_CSS.read_text(encoding="utf-8")
    assert len(content) > 0, "task_input_workspace.css is empty"


def test_task_input_workspace_css_has_tiw_scope() -> None:
    """CSS custom properties scoped to .tiw root."""
    css = TASK_INPUT_WORKSPACE_CSS.read_text(encoding="utf-8")
    assert ".tiw" in css, ".tiw scope missing"


def test_task_input_workspace_css_has_design_tokens() -> None:
    """CSS contains the §4.3 design tokens."""
    css = TASK_INPUT_WORKSPACE_CSS.read_text(encoding="utf-8")
    for token in (
        "--tiw-bg-page: #0D141C",
        "--tiw-bg-surface: #121C28",
        "--tiw-bg-elevated: #182434",
        "--tiw-border: #283649",
        "--tiw-text-primary: #E5ECF4",
        "--tiw-text-secondary: #A5B3C4",
        "--tiw-accent: #6EA8FE",
        "--tiw-spacing-sm: 8px",
        "--tiw-spacing-md: 16px",
        "--tiw-spacing-lg: 24px",
        "--tiw-font-size-body: 14px",
        "--tiw-btn-height: 36px",
    ):
        assert token in css, f"Design token {token} missing"


def test_task_input_workspace_css_linked_after_picker_css() -> None:
    """task_input_workspace.css linked AFTER structure_source_picker.css."""
    html = FRONTEND.read_text(encoding="utf-8")
    sp_pos = html.index('<link rel="stylesheet" href="css/structure_source_picker.css">')
    tiw_pos = html.index('<link rel="stylesheet" href="css/task_input_workspace.css?v=20260924-layout">')
    assert tiw_pos > sp_pos, "task_input_workspace.css must load after structure_source_picker.css"


def test_task_input_workspace_js_loaded_in_html() -> None:
    """task_input_workspace.js loaded via script tag."""
    html = FRONTEND.read_text(encoding="utf-8")
    assert '<script src="js/task_input_workspace.js"></script>' in html, (
        "task_input_workspace.js not loaded in HTML"
    )


def test_candidate_details_js_loaded_in_html() -> None:
    """candidate_details.js loaded via script tag."""
    html = FRONTEND.read_text(encoding="utf-8")
    assert '<script src="js/candidate_details.js"></script>' in html, (
        "candidate_details.js not loaded in HTML"
    )


def test_scaffold_scripts_load_after_job_editor() -> None:
    """New script tags load after job_editor.js (correct load order)."""
    html = FRONTEND.read_text(encoding="utf-8")
    je_pos = html.index('<script src="js/job_editor.js"></script>')
    tiw_pos = html.index('<script src="js/task_input_workspace.js"></script>')
    cd_pos = html.index('<script src="js/candidate_details.js"></script>')
    assert tiw_pos > je_pos, "task_input_workspace.js must load after job_editor.js"
    assert cd_pos > tiw_pos, "candidate_details.js must load after task_input_workspace.js"


def test_picker_existing_instance_methods_preserved() -> None:
    """Existing instance methods still present (refresh, setProject, getSelection, clearSelection, setVirtualItems, setUsageStatus, render, destroy)."""
    js = PICKER_JS.read_text(encoding="utf-8")
    for method in (
        "refresh: refresh",
        "setProject: setProject",
        "getSelection: getSelection",
        "clearSelection: clearSelection",
        "setVirtualItems: setVirtualItems",
        "setUsageStatus: setUsageStatus",
        "render: _renderList",
        "destroy: destroy",
    ):
        assert method in js, f"Existing instance method {method} missing"


# ---------------------------------------------------------------------------
# sourceGroup option + setSourceGroup
# ---------------------------------------------------------------------------


def test_picker_js_has_source_group_option() -> None:
    """sourceGroup option recognized in the option table."""
    js = PICKER_JS.read_text(encoding="utf-8")
    assert "opts.sourceGroup" in js, "sourceGroup option not recognized"
    assert 'var sourceGroup' in js, "sourceGroup variable not declared"


def test_picker_js_has_setSourceGroup() -> None:
    """setSourceGroup method present in returned instance object."""
    js = PICKER_JS.read_text(encoding="utf-8")
    assert "setSourceGroup: setSourceGroup" in js, "setSourceGroup not in returned instance"


def test_picker_js_setSourceGroup_function_defined() -> None:
    """setSourceGroup function body exists."""
    js = PICKER_JS.read_text(encoding="utf-8")
    assert "function setSourceGroup(value)" in js, "setSourceGroup function not defined"


def test_picker_js_fetchV2_sends_source_group() -> None:
    """_fetchV2 sends source_group param when set."""
    js = PICKER_JS.read_text(encoding="utf-8")
    assert 'params.set("source_group", sourceGroup)' in js, "source_group param not sent in _fetchV2"


def test_picker_js_facets_sends_source_group() -> None:
    """_loadFacets sends source_group param when set."""
    js = PICKER_JS.read_text(encoding="utf-8")
    # The facets function should also send source_group
    facets_section = js.split("async function _loadFacets")[1].split("function ")[0]
    assert 'params.set("source_group", sourceGroup)' in facets_section, \
        "source_group param not sent in _loadFacets"


def test_picker_js_source_group_validates_values() -> None:
    """setSourceGroup only accepts candidate/task_result."""
    js = PICKER_JS.read_text(encoding="utf-8")
    assert 'value === "candidate" || value === "task_result"' in js, \
        "setSourceGroup value validation missing"


def test_picker_js_source_group_null_by_default() -> None:
    """sourceGroup defaults to null (backward-compatible)."""
    js = PICKER_JS.read_text(encoding="utf-8")
    assert "? opts.sourceGroup" in js or 'opts.sourceGroup === "candidate"' in js, \
        "sourceGroup null-default handling missing"


# ---------------------------------------------------------------------------
# Integration: candidate workspace tabs in HTML
# ---------------------------------------------------------------------------


def test_html_has_unified_structure_library_tab() -> None:
    """Candidate and task-result sources share one structure-library tab."""
    html = FRONTEND.read_text(encoding="utf-8")
    assert 'data-i18n="modal.mode_library"' in html, "structure-library tab missing"
    assert 'data-input-mode="candidate"' not in html, "duplicate candidate tab remains"


def test_html_has_single_library_browser_host() -> None:
    """The unified library uses one browser instead of duplicate hosts."""
    html = FRONTEND.read_text(encoding="utf-8")
    assert 'id="task-results-browser"' in html, "structure-library browser missing"
    assert 'id="candidate-browser"' not in html, "duplicate candidate browser remains"
    assert 'data-source-group="candidate"' in html
    assert 'data-source-group="task_result"' in html


def test_html_candidate_i18n_keys_in_both_locales() -> None:
    """modal.mode_candidate i18n key exists in both zh-CN and en-US."""
    html = FRONTEND.read_text(encoding="utf-8")
    assert '"modal.mode_candidate":' in html, "modal.mode_candidate key missing"
    zh_block = html.split('"zh-CN":')[1].split('"en-US":')[0]
    en_block = html.split('"en-US":')[1]
    assert '"modal.mode_candidate"' in zh_block, "modal.mode_candidate missing from zh-CN"
    assert '"modal.mode_candidate"' in en_block, "modal.mode_candidate missing from en-US"


def test_html_has_candidate_picker_variable() -> None:
    """candidatePicker variable declared in HTML."""
    html = FRONTEND.read_text(encoding="utf-8")
    assert "var candidatePicker" in html, "candidatePicker variable missing"


def test_html_has_load_candidate_sources_function() -> None:
    """loadCandidateSources function defined in HTML."""
    html = FRONTEND.read_text(encoding="utf-8")
    assert "async function loadCandidateSources(" in html, "loadCandidateSources missing"


def test_html_candidate_picker_mounts_with_source_group() -> None:
    """Candidate picker mount includes sourceGroup: 'candidate'."""
    html = FRONTEND.read_text(encoding="utf-8")
    cand_section = html.split("async function loadCandidateSources")[1].split("function ")[0]
    assert 'sourceGroup: "candidate"' in cand_section, \
        "Candidate picker not mounted with sourceGroup: 'candidate'"


def test_html_library_picker_mounts_without_forced_source_group() -> None:
    """The library starts with all sources and applies its local quick filter."""
    html = FRONTEND.read_text(encoding="utf-8")
    task_section = html.split("async function loadStructureSources")[1].split("function ")[0]
    assert "sourceGroup: null" in task_section, "library picker should initially show all sources"
    assert "function setLibrarySourceGroup(group)" in html


def test_html_has_sync_picker_selection_function() -> None:
    """_syncPickerSelection function defined in HTML."""
    html = FRONTEND.read_text(encoding="utf-8")
    assert "function _syncPickerSelection()" in html, "_syncPickerSelection missing"


def test_html_has_input_reconciling_guard() -> None:
    """_inputReconciling re-entrancy guard declared."""
    html = FRONTEND.read_text(encoding="utf-8")
    assert "var _inputReconciling" in html, "_inputReconciling guard missing"


def test_html_submit_gating_for_geometry_state() -> None:
    """submitJobModal blocks on pending/loading/failed geometry_state."""
    html = FRONTEND.read_text(encoding="utf-8")
    submit_section = html.split("async function submitJobModal")[1].split("async function ")[0]
    assert "geometry_state" in submit_section, "geometry_state gating missing from submitJobModal"
    assert '"pending"' in submit_section, "pending state check missing"
    assert '"loading"' in submit_section, "loading state check missing"
    assert '"failed"' in submit_section, "failed state check missing"


def test_html_submit_gating_for_version_changed() -> None:
    """submitJobModal blocks on version_changed."""
    html = FRONTEND.read_text(encoding="utf-8")
    submit_section = html.split("async function submitJobModal")[1].split("async function ")[0]
    assert "version_changed" in submit_section, "version_changed gating missing from submitJobModal"


# ---------------------------------------------------------------------------
# task_input_workspace.js: reconciler pure functions
# ---------------------------------------------------------------------------


TASK_INPUT_WORKSPACE_JS = FRONTEND_JS_DIR / "task_input_workspace.js"


def test_tiw_has_entryKey() -> None:
    """entryKey function exported on namespace."""
    js = TASK_INPUT_WORKSPACE_JS.read_text(encoding="utf-8")
    assert "entryKey: entryKey" in js, "entryKey not exported"


def test_tiw_has_entryFromSnapshot() -> None:
    """entryFromSnapshot function exported on namespace."""
    js = TASK_INPUT_WORKSPACE_JS.read_text(encoding="utf-8")
    assert "entryFromSnapshot: entryFromSnapshot" in js, "entryFromSnapshot not exported"


def test_tiw_has_reconcileSelection() -> None:
    """reconcileSelection function exported on namespace."""
    js = TASK_INPUT_WORKSPACE_JS.read_text(encoding="utf-8")
    assert "reconcileSelection: reconcileSelection" in js, "reconcileSelection not exported"


def test_tiw_has_derivedSelectedUids() -> None:
    """derivedSelectedUids function exported on namespace."""
    js = TASK_INPUT_WORKSPACE_JS.read_text(encoding="utf-8")
    assert "derivedSelectedUids: derivedSelectedUids" in js, "derivedSelectedUids not exported"


def test_tiw_entryKey_uses_source_uid() -> None:
    """entryKey builds key from source_uid + version_id/geometry_hash."""
    js = TASK_INPUT_WORKSPACE_JS.read_text(encoding="utf-8")
    assert "source_uid" in js.split("function entryKey")[1].split("function ")[0], \
        "entryKey does not reference source_uid"


def test_tiw_reconcile_preserves_local_entries() -> None:
    """reconcileSelection preserves entries with source_uid == null."""
    js = TASK_INPUT_WORKSPACE_JS.read_text(encoding="utf-8")
    reconcile_section = js.split("function reconcileSelection")[1].split("function ")[0]
    assert "Local entry" in reconcile_section or "source_uid" in reconcile_section, \
        "reconcileSelection local-entry preservation not found"


def test_tiw_reconcile_sets_version_changed() -> None:
    """reconcileSelection sets version_changed when versions differ."""
    js = TASK_INPUT_WORKSPACE_JS.read_text(encoding="utf-8")
    assert "version_changed = true" in js, "version_changed flag not set in reconciler"
