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
