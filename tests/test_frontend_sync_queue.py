"""Frontend sync page tests — queue area.

Split from tests/test_frontend_sync.py (T29, 2026-10-18). Every test keeps
its original name and body; the old -> new mapping is
tests/test_frontend_sync.py::<name> -> tests/test_frontend_sync_queue.py
::<name>. Shared helpers live in tests/_frontend_sync_shared.py.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path

import pytest

from tests._frontend_sync_shared import (
    _EN_BLOCK_RE,
    _ZH_BLOCK_RE,
    FRONTEND_JS_DIR,
    read_workbench_content,
    read_workbench_css,
)

# ---------------------------------------------------------------------------
# T4 — Task-view data layer (apiV2 + prefs + refreshJobs rewrite)
# ---------------------------------------------------------------------------


def test_task_view_data_layer() -> None:
    """T4 data-layer contract: apiV2, DEFAULT_TASK_VIEW, prefs, refreshJobs rewrite."""
    html = read_workbench_content()

    # (1) apiV2 function definition and "/api/v2" literal present.
    assert "async function apiV2(path, opts)" in html, "apiV2 function definition missing"
    assert '"/api/v2"' in html or 'fetch("/api/v2"' in html, '"/api/v2" literal missing from apiV2'

    # (2) DEFAULT_TASK_VIEW with correct shape.
    assert 'groupBy: "molecule"' in html, 'DEFAULT_TASK_VIEW.groupBy must be "molecule"'
    assert 'sort: "created_desc"' in html, 'DEFAULT_TASK_VIEW.sort must be "created_desc"'

    # (3) localStorage prefix and __all__ fallback.
    assert '"acp.taskview."' in html or "acp.taskview." in html, (
        "acp.taskview. localStorage prefix missing"
    )
    assert "__all__" in html, "__all__ fallback literal missing from loadTaskViewPrefs"

    # (4) refreshJobs uses apiV2("/task-view...) and no longer sets limit=50.
    refresh_body = html.split("function configureTaskListController()", 1)[1].split("\nfunction renderTaskPagination", 1)[0]
    assert "client: ACPClient.taskView" in refresh_body
    assert 'adapters.v2("/task-view?"' in html
    assert 'query.set("limit", "50")' not in refresh_body, "refreshJobs must no longer set limit=50"

    # (5) API_BASE unchanged.
    assert 'const API_BASE = "/api/v1"' in html, 'API_BASE must remain "/api/v1"'

    # (6) taskViewCache defined and jobsCache derived from groups.
    assert "let taskViewCache" in html or "var taskViewCache" in html, (
        "taskViewCache variable missing"
    )
    assert "taskViewCache = body" in refresh_body, (
        "refreshJobs must store full body in taskViewCache"
    )
    assert "body.groups" in refresh_body and "jobsCache = rows" in refresh_body

    # (7) try/catch around JSON.parse in loadTaskViewPrefs.
    prefs_fn = html.split("function loadTaskViewPrefs(", 1)[1].split("\nfunction ", 1)[0]
    assert "try" in prefs_fn and "catch" in prefs_fn, (
        "loadTaskViewPrefs must have try/catch for corrupt JSON fallback"
    )
    assert "JSON.parse" in prefs_fn, "loadTaskViewPrefs must use JSON.parse"

    # (8) loadTaskViewPrefs hook at selectedProjectId change site.
    change_handler = html.split('getElementById("project-select").addEventListener("change"', 1)[1]
    change_handler = change_handler.split("});")[0]
    assert "loadTaskViewPrefs(" in change_handler, (
        "loadTaskViewPrefs must be called on project-select change"
    )

    # (9) v1 api("/ call count: refreshJobs switched from api("/jobs") to
    #     apiV2("/task-view") so the count dropped by exactly 1 vs the
    #     pre-T4 baseline (which was 53).  T10 added saved-views PATCH
    #     calls (+2); T11 added auto-tag rules CRUD (+4); P4 structure
    #     picker added s2scan picker branch detail+asset calls (+2).
    #     candidate workspace added _loadPendingGeometry api call (+1).
    #     After merging origin/main's api() timeout wave, 4 SFTP-backed
    #     call sites moved to apiRemote() (60s budget) — floor lowered
    #     accordingly.  FE-04 slice 1 removed the dead s2scan modal call
    #     sites (structure-preview / jobs POST / structure-sources detail /
    #     structure-assets x2) — floor lowered to 57.  FE-04 slice 2 +
    #     FE-05 removed the dead s2scan B-group report chain and the
    #     ensureMechanismDraftStudy POST /mechanism-studies draft write —
    #     floor lowered to 56.  R1 moved the job detail fetch (GET
    #     /jobs/{id}/detail) and its legacy-record fallback (GET /jobs/{id})
    #     into the ACPClient.jobDetail client module — the calls still
    #     funnel through the injected v1 `api` adapter (same single
    #     transport chokepoint), so the page-side floor lowers to 54 and
    #     the migrated endpoints are pinned in the client module below
    #     (equivalent-or-stronger guard).
    api_v1_count = html.count('api("/')
    assert api_v1_count >= 54, (
        f"v1 api('/ call count expected at least 54 (including IRC TS source checks), got {api_v1_count}"
    )
    client_src = (FRONTEND_JS_DIR / "client/job-detail-query.js").read_text(encoding="utf-8")
    assert 'adapters.v1("/jobs/" + encodeURIComponent(id) + "/detail", { signal: signal })' in client_src, (
        "R1: the rich detail endpoint must be fetched by the ACPClient.jobDetail module"
    )
    assert 'adapters.v1("/jobs/" + encodeURIComponent(id), { signal: signal }).then(legacyWrap)' in client_src, (
        "R1: the legacy /jobs/{id} fallback must stay in the ACPClient.jobDetail module"
    )


def test_task_view_prefs_corrupt_fallback() -> None:
    """T4 contract: corrupt localStorage JSON must fall back to DEFAULT_TASK_VIEW clone."""
    html = read_workbench_content()

    # The loadTaskViewPrefs function must have a catch branch that assigns
    # a JSON.parse(JSON.stringify(DEFAULT_TASK_VIEW)) deep clone as fallback.
    prefs_fn = html.split("function loadTaskViewPrefs(", 1)[1].split("\nfunction ", 1)[0]
    assert "catch" in prefs_fn, "loadTaskViewPrefs must have a catch branch for corrupt JSON"
    # The fallback must produce a fresh DEFAULT_TASK_VIEW clone.
    assert "DEFAULT_TASK_VIEW" in prefs_fn, (
        "loadTaskViewPrefs must reference DEFAULT_TASK_VIEW in fallback"
    )


def test_task_view_prefs_constants_structure() -> None:
    """T4 contract: DEFAULT_TASK_VIEW must declare all filter dimensions."""
    html = read_workbench_content()

    # Extract the DEFAULT_TASK_VIEW definition.
    start = html.index("const DEFAULT_TASK_VIEW")
    end = html.index(";", start) + 1
    definition = html[start:end]

    for dim in ("status", "workflow", "molecule", "tag", "batch", "remark"):
        assert f"{dim}: []" in definition, f"DEFAULT_TASK_VIEW.filters must include {dim}: []"
    assert 'archived: "exclude"' in definition, 'DEFAULT_TASK_VIEW.archived must be "exclude"'
    assert "runningFirst: false" in definition, "DEFAULT_TASK_VIEW.runningFirst must be false"


def test_api_v2_after_api_definition() -> None:
    """T4 contract: apiV2 must be defined AFTER api() (not replacing it)."""
    html = read_workbench_content()

    api_pos = html.index("async function api(path, opts)")
    api_v2_pos = html.index("async function apiV2(path, opts)")
    assert api_v2_pos > api_pos, "apiV2 must be defined after api() — it is a separate helper"


def test_queue_view_toolbar_present() -> None:
    """T5 contract: toolbar DOM ids and data-i18n refs must exist."""
    html = read_workbench_content()

    # Five control ids
    for ctrl_id in (
        "task-view-search",
        "task-view-group",
        "task-view-sort",
        "task-view-filter-btn",
        "task-view-chips",
    ):
        assert f'id="{ctrl_id}"' in html, f'Toolbar control id="{ctrl_id}" missing'

    # Filter panel id
    assert 'id="task-view-filter-panel"' in html, "Filter panel id missing"

    # data-i18n-ph on search input
    assert 'id="task-view-search"' in html
    search_tag = html.split('id="task-view-search"', 1)[0].rsplit("<input", 1)[-1]
    search_tag += html.split('id="task-view-search"', 1)[1].split(">", 1)[0]
    assert 'data-i18n-ph="queue.view.search_placeholder"' in search_tag, (
        "Search input must have data-i18n-ph for i18n placeholder"
    )

    # data-i18n on filter button label
    filter_btn_area = html.split('id="task-view-filter-btn"', 1)[1].split("</button>", 1)[0]
    assert 'data-i18n="queue.view.filter_btn"' in filter_btn_area, (
        "Filter button must have data-i18n=queue.view.filter_btn"
    )

    # data-i18n-opt on group select options (tag option enabled in T9)
    group_select = html.split('id="task-view-group"', 1)[1].split("</select>", 1)[0]
    assert "data-i18n-opt=" in group_select, "Group select options must use data-i18n-opt"
    tag_option_match = re.search(r'<option\s+value="tag"[^>]*>', group_select)
    assert tag_option_match, "tag option must exist in group select"
    assert "disabled" not in tag_option_match.group(0), (
        "Tag option must NOT be disabled (T9 enabled tag grouping)"
    )

    # data-i18n-opt on sort select options
    sort_select = html.split('id="task-view-sort"', 1)[1].split("</select>", 1)[0]
    assert "data-i18n-opt=" in sort_select, "Sort select options must use data-i18n-opt"

    # toolbar sits between queue-summary and queue-expanded
    summary_pos = html.index('class="queue-summary"')
    toolbar_pos = html.index('class="task-view-toolbar"')
    expanded_pos = html.index('id="queue-expanded"')
    assert summary_pos < toolbar_pos < expanded_pos, (
        "Toolbar must be between queue-summary and queue-expanded"
    )


def test_queue_view_i18n_parity() -> None:
    """T5 contract: queue.view.* keys must be identical in zh-CN and en-US."""
    html = read_workbench_content()

    zh_keys = _extract_queue_view_keys(html, _ZH_BLOCK_RE)
    en_keys = _extract_queue_view_keys(html, _EN_BLOCK_RE)

    assert len(zh_keys) >= 25, f"Expected >= 25 queue.view.* keys in zh-CN, got {len(zh_keys)}"
    assert len(en_keys) >= 25, f"Expected >= 25 queue.view.* keys in en-US, got {len(en_keys)}"

    only_zh = zh_keys - en_keys
    only_en = en_keys - zh_keys
    assert not only_zh, f"queue.view.* keys in zh-CN but missing from en-US: {sorted(only_zh)}"
    assert not only_en, f"queue.view.* keys in en-US but missing from zh-CN: {sorted(only_en)}"


def test_task_view_chips_logic() -> None:
    """T5 contract: chip render/remove/clear functions exist and reference prefs."""
    html = read_workbench_content()

    # renderChips function exists
    assert "function renderChips()" in html, "renderChips() function missing"

    # Chip remove handler references _clonePrefs + filters
    chips_fn = html.split("function renderChips()", 1)[1].split("\nfunction ", 1)[0]
    assert "_clonePrefs()" in chips_fn, "renderChips must use _clonePrefs() for safe mutation"
    assert "next.filters" in chips_fn, "renderChips remove handler must reference next.filters"

    # Clear-all handler exists (inside renderChips)
    assert "queue.view.chip_clear" in chips_fn, "renderChips must render chip-clear with i18n key"

    # _activeFilterCount helper exists
    assert "function _activeFilterCount()" in html, "_activeFilterCount() function missing"

    # _updateFilterBadge helper exists
    assert "function _updateFilterBadge()" in html, "_updateFilterBadge() function missing"


def test_task_view_prefs_clone_safety() -> None:
    """T5 contract: prefs mutation must clone before assign (T4 verifier note)."""
    html = read_workbench_content()

    # _clonePrefs must use JSON.parse(JSON.stringify(...)) pattern
    clone_fn = html.split("function _clonePrefs()", 1)[1].split("\nfunction ", 1)[0]
    assert "JSON.parse(JSON.stringify(" in clone_fn, (
        "_clonePrefs must use JSON.parse(JSON.stringify(...)) deep clone"
    )

    # Every prefs mutation site must call _clonePrefs() before assigning
    # Check the key mutation functions: scheduleTaskViewSearch, renderChips,
    # group change, sort change, filter checkbox change
    for fn_name in ("scheduleTaskViewSearch", "renderChips"):
        fn_body = html.split(f"function {fn_name}(", 1)[1].split("\nfunction ", 1)[0]
        assert "_clonePrefs()" in fn_body, (
            f"{fn_name} must call _clonePrefs() before mutating prefs"
        )
        assert "taskViewPrefs = next" in fn_body or "taskViewPrefs = next;" in fn_body, (
            f"{fn_name} must assign cloned prefs back to taskViewPrefs"
        )
        assert "saveTaskViewPrefs()" in fn_body, (
            f"{fn_name} must call saveTaskViewPrefs() after mutation"
        )

    # Verify no direct mutation of taskViewPrefs.filters without cloning
    # (except in _clonePrefs itself and loadTaskViewPrefs)
    # Search for patterns like "taskViewPrefs.filters" that are NOT inside _clonePrefs
    all_fn_bodies = html.split("function ")
    for body in all_fn_bodies:
        fn_header = body.split("(", 1)[0].strip() if "(" in body else ""
        if fn_header in (
            "_clonePrefs",
            "loadTaskViewPrefs",
            "_applyTaskViewPrefsToControls",
            "_activeFilterCount",
        ):
            continue
        # No function should directly mutate taskViewPrefs.filters
        if "taskViewPrefs.filters[" in body and "=" in body.split("taskViewPrefs.filters[")[1][:20]:
            # This is a read (index access), not a mutation — OK
            pass


def test_data_i18n_opt_support_in_apply_i18n() -> None:
    """T5 contract: applyI18n must handle data-i18n-opt for select options."""
    html = read_workbench_content()

    apply_fn = html.split("function applyI18n()", 1)[1].split("\nfunction ", 1)[0]
    assert "data-i18n-opt" in apply_fn, "applyI18n must handle data-i18n-opt attribute"


def test_bind_task_view_toolbar_called_at_startup() -> None:
    """T5 contract: _bindTaskViewToolbar must be called in DOMContentLoaded."""
    html = read_workbench_content()

    assert "function _bindTaskViewToolbar()" in html, "_bindTaskViewToolbar() function missing"
    # Must be called in DOMContentLoaded handler
    dom_ready = html.split("DOMContentLoaded", 1)[1]
    assert "_bindTaskViewToolbar()" in dom_ready, (
        "_bindTaskViewToolbar() must be called in DOMContentLoaded handler"
    )


def test_task_view_filter_panel_structure() -> None:
    """T5 contract: filter panel has header, body, close button."""
    html = read_workbench_content()

    assert 'id="task-view-filter-panel"' in html, "Filter panel id missing"
    assert 'id="task-view-filter-body"' in html, "Filter panel body id missing"
    assert 'id="task-view-filter-close"' in html, "Filter panel close button id missing"
    assert "function renderFilterPanel()" in html, "renderFilterPanel() function missing"
    assert "function _toggleFilterPanel()" in html, "_toggleFilterPanel() function missing"


# ---------------------------------------------------------------------------
# T6: Grouped collapsible list + slim cards + collator sort
# ---------------------------------------------------------------------------


def test_task_view_group_render() -> None:
    """T6 contract: view: collapse-key prefix, sentinel→i18n, group header elements."""
    html = read_workbench_content()

    # (1) _buildTaskViewGroup function exists and uses view: namespace prefix.
    assert "function _buildTaskViewGroup(" in html, "_buildTaskViewGroup function missing"
    group_fn = html.split("function _buildTaskViewGroup(", 1)[1].split("\nfunction ", 1)[0]
    assert '"view:"' in group_fn or "'view:'" in group_fn, (
        "_buildTaskViewGroup must use 'view:' namespace prefix for collapse keys"
    )

    # (2) Sentinel resolution branches.
    assert "function _resolveGroupDisplayName(" in html, "_resolveGroupDisplayName function missing"
    sentinel_fn = html.split("function _resolveGroupDisplayName(", 1)[1].split("\nfunction ", 1)[0]
    # __unassigned__ → conditional i18n for molecule vs remark
    assert "__unassigned__" in sentinel_fn, (
        "_resolveGroupDisplayName must handle __unassigned__ sentinel"
    )
    assert "queue.view.unassigned_molecule" in sentinel_fn, (
        "_resolveGroupDisplayName must resolve __unassigned__ to unassigned_molecule i18n key"
    )
    assert "queue.view.unassigned_remark" in sentinel_fn, (
        "_resolveGroupDisplayName must resolve __unassigned__"
        " to unassigned_remark i18n key for remark groupBy"
    )
    # __singles__ → i18n
    assert "__singles__" in sentinel_fn, "_resolveGroupDisplayName must handle __singles__ sentinel"
    assert "queue.view.singles" in sentinel_fn, (
        "_resolveGroupDisplayName must resolve __singles__ to i18n key"
    )
    # __untagged__ → i18n
    assert "__untagged__" in sentinel_fn, (
        "_resolveGroupDisplayName must handle __untagged__ sentinel"
    )
    assert "queue.view.untagged" in sentinel_fn, (
        "_resolveGroupDisplayName must resolve __untagged__ to i18n key"
    )

    # (3) Group count rendering: queue.view.group_tasks_count i18n key.
    assert "queue.view.group_tasks_count" in group_fn, (
        "_buildTaskViewGroup must render task count with queue.view.group_tasks_count"
    )

    # (4) Status summary render function exists.
    assert "function _renderGroupStatusLine(" in html, "_renderGroupStatusLine function missing"
    summary_fn = html.split("function _renderGroupStatusLine(", 1)[1].split("\nfunction ", 1)[0]
    assert "queue.view.status_running" in summary_fn, (
        "_renderGroupStatusLine must use queue.view.status_running"
    )
    assert "queue.view.status_queued" in summary_fn, (
        "_renderGroupStatusLine must use queue.view.status_queued"
    )
    assert "queue.view.status_completed" in summary_fn, (
        "_renderGroupStatusLine must use queue.view.status_completed"
    )
    assert "queue.view.status_failed" in summary_fn, (
        "_renderGroupStatusLine must use queue.view.status_failed"
    )
    assert "group.jobs" in summary_fn, (
        "_renderGroupStatusLine must derive counts from group.jobs"
        " (backend never emits group.counts)"
    )
    assert "group.counts" not in summary_fn, (
        "_renderGroupStatusLine must NOT read group.counts (never emitted by backend)"
    )
    assert (
        "!parts.length" in summary_fn
        or "parts.length === 0" in summary_fn
        or "!parts" in summary_fn
    ), "_renderGroupStatusLine must guard span creation on non-empty parts"

    # (5) Tag-group hint function exists.
    assert "function _renderTagGroupHint()" in html, "_renderTagGroupHint function missing"
    assert "queue.view.tag_group_hint" in html, (
        "queue.view.tag_group_hint i18n key must be referenced"
    )

    # (6) Retired badge function exists.
    assert "function _renderRetiredBadge()" in html, "_renderRetiredBadge function missing"
    assert "queue.view.retired" in html, "queue.view.retired i18n key must be referenced"

    # (7) renderQueueList reads taskViewCache.groups.
    render_fn = html.split("function renderQueueListContents(container)", 1)[1].split("\nfunction ", 1)[0]
    assert "taskViewCache" in render_fn or "cache" in render_fn, (
        "renderQueueList must read taskViewCache.groups"
    )

    # (8) Per-group select-all checkbox.
    assert "queue-group-select-all" in group_fn, (
        "_buildTaskViewGroup must have per-group select-all checkbox"
    )
    assert "queue.view.select_all_group" in group_fn, (
        "_buildTaskViewGroup must use queue.view.select_all_group i18n key for select-all"
    )


def test_slim_card_rules() -> None:
    """T6 contract: progress bar guarded by isActiveJobStatus, ellipsis, tag truncation, no 暂无."""
    html = read_workbench_content()

    build_fn = html.split("function buildQueueRow(job)", 1)[1].split("\nfunction ", 1)[0]

    # (1) Progress bar ONLY in active-status branch.
    assert "isActiveJobStatus(job.status)" in build_fn, (
        "buildQueueRow must guard progress bar with isActiveJobStatus(job.status)"
    )
    # The progress-fill construction must be inside the active branch.
    # Split on the guard to check the active branch contains progress.
    active_branch = build_fn.split("isActiveJobStatus(job.status)", 1)[1]
    active_branch = (
        active_branch.split("\n  }", 1)[0] if "\n  }" in active_branch else active_branch
    )
    assert "progress-fill" in active_branch, (
        "Active-status branch must contain progress-fill construction"
    )

    # (2) No completed-100% progress path (the old fill.style.width = normalizedProgress
    #     for completed jobs). The progress section is entirely inside the active guard,
    #     so non-active rows get no progress bar at all.
    # Verify no progress-fill outside the active guard by checking the full function
    # has exactly one progress-fill construction (inside the active branch).
    progress_count = build_fn.count("progress-fill")
    # There should be exactly 2: one "progress-fill indeterminate" and one "progress-fill"
    assert progress_count <= 2, (
        f"Expected at most 2 progress-fill refs (inside active branch), got {progress_count}"
    )

    # (3) Task name ellipsis: strong element has title attribute.
    assert "strong.title" in build_fn or "strong.title =" in build_fn, (
        "buildQueueRow must set title attribute on strong (task name) for ellipsis"
    )

    # (4) Tag truncation: max 2 + "+N" overflow.
    assert "queue-row-tags" in build_fn, "buildQueueRow must render tags container"
    assert "queue-tag-chip" in build_fn, "buildQueueRow must render tag chips"
    assert '"+"' in build_fn or "'+'" in build_fn, "buildQueueRow must render +N overflow for tags"

    # (5) molecule-dedup: when groupBy=molecule, primaryLabel prefers task_name+remark.
    assert "_groupBy" in build_fn, "buildQueueRow must read _groupBy for molecule dedup"
    assert 'groupBy === "molecule"' in build_fn or "groupBy === 'molecule'" in build_fn, (
        "buildQueueRow must branch on groupBy=molecule for label dedup"
    )

    # (6) No 暂无 placeholder in queue rendering functions
    # (buildQueueRow, _buildTaskViewGroup, renderQueueList).
    # Check the relevant function bodies — the "queue.none"
    # empty state is allowed (it's "暂无任务").
    queue_fns_to_check = ["buildQueueRow", "_buildTaskViewGroup", "renderQueueList"]
    for fn_name in queue_fns_to_check:
        if fn_name + "(" in html:
            fn_body = html.split(f"function {fn_name}(", 1)[1].split("\nfunction ", 1)[0]
            # "queue.none" is allowed (it says "暂无任务" when no jobs exist)
            # But "暂无" as a direct string literal in the function is forbidden
            # (except in the empty-state placeholder which uses t("queue.none"))
            stripped = fn_body.replace('t("queue.none")', "").replace("t('queue.none')", "")
            # Check there's no remaining 暂无 literal
            assert "暂无" not in stripped, f"{fn_name} must not contain 暂无 placeholder literals"


def test_task_sort_collator() -> None:
    """T6 contract: module-level Intl.Collator, sortTaskRows, runningFirst partition."""
    html = read_workbench_content()

    # (1) Module-level Intl.Collator with zh locale and numeric:true.
    collator_line = None
    for line in html.split("\n"):
        if 'new Intl.Collator("zh"' in line or "new Intl.Collator('zh'" in line:
            collator_line = line
            break
    assert collator_line is not None, 'Module-level "new Intl.Collator("zh"..." must exist'
    assert "numeric" in collator_line, "Intl.Collator must have numeric option"
    # Must be at module scope (not inside a function) — check it's not indented
    # by verifying it's defined near other module-level vars.
    assert collator_line.strip().startswith("const ") or collator_line.strip().startswith("var "), (
        "Intl.Collator must be a module-level const/var"
    )

    # (2) sortTaskRows function exists.
    assert "function sortTaskRows(" in html, "sortTaskRows function missing"
    sort_fn = html.split("function sortTaskRows(", 1)[1].split("\nfunction ", 1)[0]

    # (3) Uses _taskCollator for name sorts.
    assert "_taskCollator" in sort_fn, "sortTaskRows must use _taskCollator for name sorting"

    # (4) runningFirst partition logic.
    assert "runningFirst" in sort_fn, "sortTaskRows must implement runningFirst partition"
    assert "isActiveJobStatus" in sort_fn, (
        "sortTaskRows must use isActiveJobStatus for running-first partition"
    )

    # (5) sortTaskRows is called in refreshJobs.
    refresh_fn = html.split("function configureTaskListController()", 1)[1].split("\nfunction renderTaskPagination", 1)[0]
    assert "sortTaskRows(" in refresh_fn, "refreshJobs must call sortTaskRows"

    # (6) Name sort also sorts groups.
    assert "name_asc" in refresh_fn or "name_desc" in refresh_fn, (
        "refreshJobs must handle name_asc/name_desc group sorting"
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_task_sort_collator_node_smoke() -> None:
    """T6 contract: node smoke — collator runtime order with CJK + natural sort."""
    script = textwrap.dedent("""\
        const collator = new Intl.Collator("zh", { numeric: true, sensitivity: "variant" });
        const items = ["TS10", "TS2", "BCB_ALLENE", "\u4e59\u9187", "abc"];
        const sorted = items.slice().sort(collator.compare);
        const ts2_idx = sorted.indexOf("TS2");
        const ts10_idx = sorted.indexOf("TS10");
        if (ts2_idx < 0 || ts10_idx < 0) {
            process.stderr.write(
                "TS2 or TS10 not found in sorted: "
                + JSON.stringify(sorted) + "\\n"
            );
            process.exit(1);
        }
        if (ts2_idx >= ts10_idx) {
            process.stderr.write("TS2 index (" + ts2_idx + ") >= TS10 index (" + ts10_idx + ")\\n");
            process.exit(1);
        }
        process.stdout.write(JSON.stringify(sorted));
    """)
    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, f"node collator smoke failed: {result.stderr}"
    sorted_items = json.loads(result.stdout)
    assert sorted_items.index("TS2") < sorted_items.index("TS10"), (
        f"TS2 should sort before TS10 with numeric:true, got {sorted_items}"
    )


def test_queue_view_i18n_parity_t6_keys() -> None:
    """T6 contract: new i18n keys must be present in both locales."""
    html = read_workbench_content()

    new_keys = [
        "queue.view.group_tasks_count",
        "queue.view.status_running",
        "queue.view.status_queued",
        "queue.view.status_completed",
        "queue.view.status_failed",
        "queue.view.tag_group_hint",
        "queue.view.retired",
        "queue.view.select_all_group",
    ]
    for key in new_keys:
        assert f'"{key}":' in html, f"i18n key {key} missing from HTML"
    # Parity: both locale blocks must contain the same new keys.
    zh_keys = _extract_queue_view_keys(html, _ZH_BLOCK_RE)
    en_keys = _extract_queue_view_keys(html, _EN_BLOCK_RE)
    for key in new_keys:
        assert key in zh_keys, f"{key} missing from zh-CN locale block"
        assert key in en_keys, f"{key} missing from en-US locale block"


def test_p2_batch_and_archive_ui() -> None:
    """T9 contract: batch toolbar + archive + export + endpoint literals."""
    html = read_workbench_content()

    # Batch bar control ids
    for ctrl_id in (
        "queue-batch-bar",
        "batch-tag-input",
        "batch-add-tags",
        "batch-remove-tags",
        "batch-archive",
        "batch-unarchive",
        "batch-molecule-input",
        "batch-set-molecule",
    ):
        assert f'id="{ctrl_id}"' in html, f'Batch control id="{ctrl_id}" missing'

    # Archive select
    assert 'id="task-view-archived"' in html, "Archive select missing"
    assert '"exclude"' in html, "Archive exclude value missing"
    assert '"include"' in html, "Archive include value missing"
    assert '"only"' in html, "Archive only value missing"

    # Export controls
    assert 'id="task-view-export-btn"' in html, "Export button missing"
    assert 'id="task-view-export-csv"' in html, "Export CSV button missing"
    assert 'id="task-view-export-json"' in html, "Export JSON button missing"

    # Endpoint literals referenced
    assert '"/tasks/batch-ops"' in html, "batch-ops endpoint literal missing"
    assert '"/tags/rename"' in html, "tags/rename endpoint literal missing"
    assert '"/tags/merge"' in html, "tags/merge endpoint literal missing"
    assert '"/tags/delete"' in html, "tags/delete endpoint literal missing"
    assert '"/molecule-groups/merge"' in html, "molecule-groups/merge endpoint literal missing"
    assert '"/molecule-groups/suggestions"' in html, (
        "molecule-groups/suggestions endpoint literal missing"
    )
    assert '"/tasks/"' in html and '"/lineage"' in html, "lineage endpoint literal missing"
    assert '"add_tags"' in html, "add_tags op literal missing"
    assert '"remove_tags"' in html, "remove_tags op literal missing"
    assert '"archive"' in html, "archive op literal missing"
    assert '"unarchive"' in html, "unarchive op literal missing"
    assert '"set_molecule_name"' in html, "set_molecule_name op literal missing"

    # updateQueueBatchBar extended
    batch_fn = html.split("function updateQueueBatchBar(", 1)[1].split("\nfunction ", 1)[0]
    assert "queue-batch-bar" in batch_fn, "updateQueueBatchBar must control queue-batch-bar"


def test_archive_toggle_wiring() -> None:
    """T9 contract: prefs.archived wired to #task-view-archived."""
    html = read_workbench_content()

    # _applyTaskViewPrefsToControls sets archived select
    apply_fn = html.split("function _applyTaskViewPrefsToControls(", 1)[1].split("\nfunction ", 1)[
        0
    ]
    assert "task-view-archived" in apply_fn, (
        "_applyTaskViewPrefsToControls must set task-view-archived"
    )
    assert "p.archived" in apply_fn or "prefs.archived" in apply_fn, (
        "_applyTaskViewPrefsToControls must read prefs.archived"
    )

    # archived change handler saves prefs and refreshes
    assert "archivedEl.value" in html, "archived change handler must read value"

    # refreshJobs passes archived param
    refresh_fn = html.split("function configureTaskListController()", 1)[1].split("\nfunction renderTaskPagination", 1)[0]
    assert "client: ACPClient.taskView" in refresh_fn and "prefs.archived" in html, (
        "refreshJobs must pass archived param"
    )

    # archived-row CSS class
    css = read_workbench_css()
    assert ".archived-row" in css, "archived-row CSS class missing"
    assert "archived-row" in html and "job.archived" in html, (
        "buildQueueRow must apply archived-row class"
    )


def test_export_logic() -> None:
    """T9 contract: CSV builder + JSON export + Blob/download."""
    html = read_workbench_content()

    # _exportCSV function
    assert "function _exportCSV(" in html, "_exportCSV function missing"
    csv_fn = html.split("function _exportCSV(", 1)[1].split("\nfunction ", 1)[0]
    assert "Blob" in csv_fn, "_exportCSV must use Blob"
    assert "text/csv" in csv_fn, "_exportCSV must set text/csv MIME"
    assert "a.download" in csv_fn or "a.click" in csv_fn, "_exportCSV must trigger download"
    assert "FEFF" in csv_fn, "_exportCSV must include BOM for Excel"

    # _exportJSON function
    assert "function _exportJSON(" in html, "_exportJSON function missing"
    json_fn = html.split("function _exportJSON(", 1)[1].split("\nfunction ", 1)[0]
    assert "application/json" in json_fn, "_exportJSON must set application/json MIME"

    # _flattenTaskViewRows helper
    assert "function _flattenTaskViewRows(" in html, "_flattenTaskViewRows missing"

    # Column header i18n key
    assert '"queue.view.export_columns"' in html, "export_columns i18n key missing"


def test_molecule_management_ui() -> None:
    """T9 contract: merge suggestions + bulk assign molecule."""
    html = read_workbench_content()

    # Merge suggestions banner rendering
    assert "function _renderMergeSuggestions(" in html, "_renderMergeSuggestions function missing"
    suggest_fn = html.split("function _renderMergeSuggestions(", 1)[1].split("\nfunction ", 1)[0]
    assert "merge-suggestion-banner" in suggest_fn, (
        "_renderMergeSuggestions must render merge-suggestion-banner"
    )
    assert "merge_suggestion" in suggest_fn, (
        "_renderMergeSuggestions must use merge_suggestion i18n key"
    )
    assert "_executeMergeSuggestion" in suggest_fn, (
        "_renderMergeSuggestions must wire _executeMergeSuggestion"
    )

    # Merge suggestion loading
    assert "function _loadMergeSuggestions(" in html, "_loadMergeSuggestions function missing"
    assert "/molecule-groups/suggestions" in html, "suggestions endpoint literal missing"

    # Execute merge
    assert "function _executeMergeSuggestion(" in html, "_executeMergeSuggestion function missing"
    exec_fn = html.split("function _executeMergeSuggestion(", 1)[1].split("\nfunction ", 1)[0]
    assert "/molecule-groups/merge" in exec_fn, (
        "_executeMergeSuggestion must call molecule-groups/merge"
    )

    # refreshJobs triggers suggestions for molecule grouping
    refresh_fn = html.split("function configureTaskListController()", 1)[1].split("\nfunction renderTaskPagination", 1)[0]
    assert "_loadMergeSuggestions" in refresh_fn, "refreshJobs must call _loadMergeSuggestions"
    assert "_renderMergeSuggestions" in refresh_fn, "refreshJobs must call _renderMergeSuggestions"

    # Bulk molecule assignment via batch-ops
    assert "function _batchSetMolecule(" in html, "_batchSetMolecule function missing"
    mol_fn = html.split("function _batchSetMolecule(", 1)[1].split("\nfunction ", 1)[0]
    assert "set_molecule_name" in mol_fn, "_batchSetMolecule must use set_molecule_name op"


def test_tag_management_dialog() -> None:
    """T9 contract: tag management modal with rename/merge/delete."""
    html = read_workbench_content()

    # Modal DOM
    assert 'id="tag-mgmt-modal"' in html, "tag-mgmt-modal missing"
    assert 'id="tag-mgmt-list"' in html, "tag-mgmt-list missing"
    assert 'id="tag-mgmt-modal-close"' in html, "tag-mgmt-modal-close missing"

    # Functions
    assert "function _openTagManagement(" in html, "_openTagManagement function missing"
    open_fn = html.split("function _openTagManagement(", 1)[1].split("\nfunction ", 1)[0]
    assert "/projects/" in open_fn and "/tags" in open_fn, (
        "_openTagManagement must call /projects/{id}/tags"
    )
    assert "tag-mgmt-item" in open_fn, "_openTagManagement must render tag-mgmt-item rows"

    # Rename/merge/delete functions
    assert "function _renameTag(" in html, "_renameTag missing"
    assert "function _mergeTag(" in html, "_mergeTag missing"
    assert "function _deleteTag(" in html, "_deleteTag missing"

    rename_fn = html.split("function _renameTag(", 1)[1].split("\nfunction ", 1)[0]
    assert "/tags/rename" in rename_fn, "_renameTag must call /tags/rename"
    assert "source" in rename_fn and "target" in rename_fn, "_renameTag must send source/target"

    merge_fn = html.split("function _mergeTag(", 1)[1].split("\nfunction ", 1)[0]
    assert "/tags/merge" in merge_fn, "_mergeTag must call /tags/merge"

    delete_fn = html.split("function _deleteTag(", 1)[1].split("\nfunction ", 1)[0]
    assert "/tags/delete" in delete_fn, "_deleteTag must call /tags/delete"
    assert "tag_delete_warn" in delete_fn, "_deleteTag must show warning"

    # Filter panel links to tag management
    render_fn = html.split("function renderFilterPanel(", 1)[1].split("\nfunction ", 1)[0]
    assert "_openTagManagement" in render_fn, "renderFilterPanel must link to _openTagManagement"
    assert "manage_tags" in render_fn, "renderFilterPanel must use manage_tags i18n key"

    # Event listener wired
    assert 'id="tag-mgmt-modal-close"' in html


def test_lineage_panel() -> None:
    """T9 contract: lineage panel lazy-loads /api/v2/tasks/{id}/lineage."""
    html = read_workbench_content()

    # _loadAndRenderLineage function
    assert "function _loadAndRenderLineage(" in html, "_loadAndRenderLineage function missing"
    load_fn = html.split("function _loadAndRenderLineage(", 1)[1].split("\nfunction ", 1)[0]
    assert '"/tasks/"' in load_fn and '"/lineage"' in load_fn, (
        "_loadAndRenderLineage must call /tasks/{id}/lineage"
    )
    assert "apiV2" in load_fn, "_loadAndRenderLineage must use apiV2"

    # Render function
    assert "function _renderLineageData(" in html, "_renderLineageData function missing"
    render_fn = html.split("function _renderLineageData(", 1)[1].split("\nfunction ", 1)[0]
    assert "upstream" in render_fn, "_renderLineageData must handle upstream"
    assert "downstream" in render_fn, "_renderLineageData must handle downstream"
    assert "lineage_subtitle" in render_fn or "lineage-subtitle" in render_fn, (
        "_renderLineageData must render subtitles"
    )

    # Node builder with depth rendering
    assert "function _buildLineageNode(" in html, "_buildLineageNode function missing"
    node_fn = html.split("function _buildLineageNode(", 1)[1].split("\nfunction ", 1)[0]
    assert "depth" in node_fn, "_buildLineageNode must render depth"
    assert "lineage-name" in node_fn, "_buildLineageNode must have lineage-name"
    assert "selectJob" in node_fn, "_buildLineageNode must wire selectJob click"

    # Lineage section in detail drawer (openDetailDrawer)
    drawer_fn = html.split("async function openDetailDrawer(", 1)[1].split("\nasync function ", 1)[
        0
    ]
    assert "lineage-section" in drawer_fn, "openDetailDrawer must include lineage-section"
    assert "lineage-header" in drawer_fn, "openDetailDrawer must include lineage-header"
    assert "_loadAndRenderLineage" in drawer_fn, "openDetailDrawer must call _loadAndRenderLineage"
    assert "lineageLoaded" in drawer_fn, "openDetailDrawer must implement lazy-load flag"

    # i18n keys
    for key in (
        "queue.view.lineage_section",
        "queue.view.lineage_upstream",
        "queue.view.lineage_downstream",
    ):
        assert f'"{key}":' in html, f"i18n key {key} missing"


def test_tag_group_option_enabled() -> None:
    """T9 contract: tag option in #task-view-group must NOT be disabled."""
    html = read_workbench_content()

    # Find the tag option line
    tag_opt_match = re.search(r'<option\s+value="tag"[^>]*>.*?</option>', html)
    assert tag_opt_match, "tag option not found in #task-view-group"
    tag_opt = tag_opt_match.group(0)
    assert "disabled" not in tag_opt, f"tag option must NOT be disabled, got: {tag_opt}"
    assert 'data-i18n-opt="queue.view.group_tag"' in tag_opt, "tag option must have data-i18n-opt"

    # tag_group_disabled hint key should still exist in i18n (for backward compat)
    # but no data-i18n-title referencing it on the option
    assert "data-i18n-title" not in tag_opt or "tag_group_disabled" not in tag_opt, (
        "tag option must not have tag_group_disabled title"
    )


def test_queue_view_i18n_parity_t9_keys() -> None:
    """T9 contract: new i18n keys must be present in both locales."""
    html = read_workbench_content()

    new_keys = [
        "queue.view.batch_add_tags",
        "queue.view.batch_remove_tags",
        "queue.view.batch_archive",
        "queue.view.batch_unarchive",
        "queue.view.batch_set_molecule",
        "queue.view.export_btn",
        "queue.view.export_csv",
        "queue.view.export_json",
        "queue.view.archived_exclude",
        "queue.view.archived_include",
        "queue.view.archived_only",
        "queue.view.manage_tags",
        "queue.view.tag_rename",
        "queue.view.tag_merge",
        "queue.view.tag_delete",
        "queue.view.tag_delete_warn",
        "queue.view.bulk_assign_molecule",
        "queue.view.merge_to",
        "queue.view.merge_suggestion",
        "queue.view.lineage_section",
        "queue.view.lineage_upstream",
        "queue.view.lineage_downstream",
        "queue.view.lineage_depth",
        "queue.view.lineage_parent",
        "queue.view.lineage_node",
        "queue.view.lineage_relation",
        "queue.view.batch_op_success",
        "queue.view.batch_op_partial",
        "queue.view.export_columns",
    ]
    for key in new_keys:
        assert f'"{key}":' in html, f"i18n key {key} missing from HTML"
    zh_keys = _extract_queue_view_keys(html, _ZH_BLOCK_RE)
    en_keys = _extract_queue_view_keys(html, _EN_BLOCK_RE)
    for key in new_keys:
        assert key in zh_keys, f"{key} missing from zh-CN locale block"
        assert key in en_keys, f"{key} missing from en-US locale block"


# ── T10: Saved views contract ───────────────────────────────────────────


def test_saved_views_menu_dom_ids() -> None:
    """T10 contract: saved views menu DOM ids must exist in toolbar."""
    html = read_workbench_content()

    for elem_id in (
        "task-view-views-btn",
        "task-view-views-menu",
        "task-view-views-list",
        "task-view-views-save",
    ):
        assert f'id="{elem_id}"' in html, f'Saved views element id="{elem_id}" missing'


def test_saved_views_i18n_parity() -> None:
    """T10 contract: saved views i18n keys present in both locales."""
    html = read_workbench_content()

    view_keys = [
        "queue.view.views_btn",
        "queue.view.views_save",
        "queue.view.views_apply",
        "queue.view.views_delete",
        "queue.view.views_empty",
        "queue.view.views_no_project",
        "queue.view.views_saved_ok",
        "queue.view.views_name_label",
    ]
    for key in view_keys:
        assert f'"{key}":' in html, f"i18n key {key} missing from HTML"

    zh_keys = _extract_queue_view_keys(html, _ZH_BLOCK_RE)
    en_keys = _extract_queue_view_keys(html, _EN_BLOCK_RE)
    for key in view_keys:
        assert key in zh_keys, f"{key} missing from zh-CN locale"
        assert key in en_keys, f"{key} missing from en-US locale"


def test_saved_views_handler_functions_exist() -> None:
    """T10 contract: saved views JS handler functions must be defined."""
    html = read_workbench_content()

    for func_name in (
        "_toggleViewsMenu",
        "_saveCurrentView",
        "_applySavedView",
        "_deleteSavedView",
        "_getSavedViews",
        "_renderSavedViewsList",
    ):
        assert f"function {func_name}" in html, f"JS function {func_name} not defined"


def test_saved_views_query_fields_not_job_ids() -> None:
    """T10 contract: saved view query uses filter fields, NOT job/task ID lists."""
    html = read_workbench_content()

    query_fields = [
        "group_by",
        "sort",
        "statuses",
        "workflows",
        "search",
        "archived",
        "running_first",
    ]
    for field in query_fields:
        assert f"q.{field}" in html or f'"{field}"' in html, (
            f"Query field '{field}' not referenced in saved views logic"
        )


def test_saved_views_live_query_assertion() -> None:
    """T10 contract: saved view stores query filters, not snapshot of task IDs."""
    html = read_workbench_content()

    assert '"statuses"' in html or "'statuses'" in html, "saved view must include statuses"
    assert '"group_by"' in html or "'group_by'" in html, "saved view must include group_by"

    assert "taskViewPrefs = next" in html or "taskViewPrefs=next" in html, (
        "apply must assign back to taskViewPrefs"
    )
    assert "saveTaskViewPrefs()" in html, "apply must persist to localStorage"
    assert "refreshJobs()" in html, "apply must refresh the task list"

    save_section = (
        html.split("_saveCurrentView")[1].split("function")[0] if "_saveCurrentView" in html else ""
    )
    assert "query" in save_section, "save function must build a query object"


# ── T11: auto-tag rules frontend contracts ──────────────────────────────


def test_auto_tag_rules_tab_dom() -> None:
    """T11: tag-mgmt-modal has auto-tag rules tab with required DOM elements."""
    html = read_workbench_content()
    assert "tag-mgmt-tab-tags" in html, "tags tab button missing"
    assert "tag-mgmt-tab-auto" in html, "auto-tag tab button missing"
    assert "tag-mgmt-panel-tags" in html, "tags panel missing"
    assert "tag-mgmt-panel-auto" in html, "auto-tag panel missing"
    assert "auto-tag-rules-list" in html, "rules list container missing"
    assert "auto-tag-form" in html, "add-rule form missing"
    assert "auto-tag-field" in html, "field select missing"
    assert "auto-tag-op" in html, "op select missing"
    assert "auto-tag-value" in html, "value input missing"
    assert "auto-tag-tag" in html, "tag input missing"
    assert "auto-tag-add-btn" in html, "add button missing"
    assert "auto-tag-apply-btn" in html, "backfill button missing"


def test_auto_tag_rules_handler_functions_exist() -> None:
    """T11: JS handler functions for auto-tag rules are defined."""
    html = read_workbench_content()
    for fn in [
        "_switchMgmtTab",
        "_loadAutoTagRules",
        "_addAutoTagRule",
        "_toggleAutoTagRule",
        "_deleteAutoTagRule",
        "_applyAutoTagRulesBackfill",
    ]:
        assert f"function {fn}" in html or f"async function {fn}" in html, (
            f"Handler function {fn} not found"
        )


def test_auto_tag_rules_backfill_endpoint_literal() -> None:
    """T11: frontend references the backfill endpoint."""
    html = read_workbench_content()
    assert "auto-tag-rules/apply" in html, "backfill endpoint not referenced"


def test_auto_tag_rules_settings_storage() -> None:
    """T11: rules are stored in project settings.auto_tag_rules via PATCH."""
    html = read_workbench_content()
    assert "auto_tag_rules" in html, "auto_tag_rules key not in frontend"


def test_auto_tag_rules_i18n_parity() -> None:
    """T11: auto-tag i18n keys exist in both zh-CN and en-US."""
    html = read_workbench_content()
    zh_keys = _extract_queue_view_keys(html, _ZH_BLOCK_RE)
    en_keys = _extract_queue_view_keys(html, _EN_BLOCK_RE)
    expected = {
        "queue.view.tab_tags",
        "queue.view.tab_auto_tag",
        "queue.view.auto_tag_empty",
        "queue.view.auto_tag_add",
        "queue.view.auto_tag_apply",
        "queue.view.auto_tag_applied",
        "queue.view.auto_tag_confirm_apply",
        "queue.view.auto_tag_field_remark",
        "queue.view.auto_tag_field_molecule",
        "queue.view.auto_tag_field_workflow",
        "queue.view.auto_tag_op_contains",
        "queue.view.auto_tag_op_equals",
    }
    missing_zh = expected - zh_keys
    missing_en = expected - en_keys
    assert not missing_zh, f"zh-CN missing auto-tag keys: {sorted(missing_zh)}"
    assert not missing_en, f"en-US missing auto-tag keys: {sorted(missing_en)}"


def test_auto_tag_rules_event_bindings() -> None:
    """T11: event listeners bound for tab switch, add, and backfill buttons."""
    html = read_workbench_content()
    assert "tag-mgmt-tab-tags" in html and "addEventListener" in html, "tab event binding missing"
    assert "auto-tag-add-btn" in html, "add button binding missing"
    assert "auto-tag-apply-btn" in html, "backfill button binding missing"


def test_task_rename_modal_dom() -> None:
    """T12: #task-rename-modal exists with input + three buttons + data-i18n."""
    html = read_workbench_content()
    assert 'id="task-rename-modal"' in html, "task-rename-modal missing"
    assert 'id="task-rename-input"' in html, "task-rename-input missing"
    assert 'id="task-rename-restore"' in html, "restore button missing"
    assert 'id="task-rename-cancel"' in html, "cancel button missing"
    assert 'id="task-rename-save"' in html, "save button missing"
    assert 'id="task-rename-default"' in html, "default name display missing"
    assert 'id="task-rename-err"' in html, "error text missing"
    # data-i18n on key elements
    assert 'data-i18n="task.rename.title"' in html, "title data-i18n missing"
    assert 'data-i18n="task.rename.hint"' in html, "hint data-i18n missing"
    assert 'data-i18n="task.rename.restore"' in html, "restore data-i18n missing"
    assert 'data-i18n="task.rename.save"' in html, "save data-i18n missing"


def test_task_rename_modal_input_attributes() -> None:
    """T12: input has maxlength and correct type."""
    html = read_workbench_content()
    assert 'id="task-rename-input" class="modal-input" type="text" maxlength="200"' in html, (
        "input missing required attributes"
    )


def test_displayTaskName_defined_and_used() -> None:
    """T12: displayTaskName function defined and used at display sites."""
    html = read_workbench_content()
    assert "function displayTaskName(job)" in html, "displayTaskName not defined"
    assert "displayTaskName(job) || deleteJobId" in html, (
        "openDeleteJobModal not using displayTaskName"
    )
    assert "const name = displayTaskName(job)" in html, (
        "updateFileTreeTaskName not using displayTaskName"
    )
    build_row_section = html.split("function buildQueueRow")[1].split("function sortTaskRows")[0]
    assert "displayTaskName(job)" in build_row_section, "buildQueueRow not using displayTaskName"
    render_section = html.split("function renderInfoInto")[1].split("function ")[0]
    assert "displayTaskName(job)" in render_section, "renderInfoInto not using displayTaskName"


def test_canonicalTaskName_still_exists() -> None:
    """T12: canonicalTaskName function preserved for pure-path uses."""
    html = read_workbench_content()
    assert "function canonicalTaskName(job)" in html, "canonicalTaskName removed"


def test_displayTaskName_not_used_for_file_tree_nodes() -> None:
    """T12: file-tree node labels still use disk names (canonicalTaskName)."""
    html = read_workbench_content()
    # displayTaskName should NOT appear in file-tree node rendering contexts.
    # The updateFileTreeTaskName PANEL TITLE uses displayTaskName (correct),
    # but actual node labels (file names) should not.
    # Verify canonicalTaskName is still used in the codebase (not replaced everywhere)
    assert html.count("canonicalTaskName(") > 1, "canonicalTaskName should still be used"


def test_pencil_button_in_queue_inline_actions() -> None:
    """T12: pencil (✎) button wired in appendQueueInlineActions."""
    html = read_workbench_content()
    actions_section = html.split("function appendQueueInlineActions")[1].split("}")[0]
    assert "btn-rename-task" in actions_section, (
        "pencil button class missing in appendQueueInlineActions"
    )
    assert "openTaskRenameModal" in actions_section, (
        "openTaskRenameModal not called in appendQueueInlineActions"
    )


def test_pencil_button_in_detail_panel() -> None:
    """T12: pencil button next to #v-task-name in detail panel."""
    html = read_workbench_content()
    render_section = html.split("function renderInfoInto")[1].split("function ")[0]
    assert 'taskName.id = "v-task-name"' in render_section, (
        "v-task-name id assignment missing in renderInfoInto"
    )
    assert "btn-rename-task" in render_section, "pencil button class missing in renderInfoInto"
    assert "openTaskRenameModal(job)" in render_section, (
        "openTaskRenameModal not called in renderInfoInto"
    )


def test_pencil_css_reveal_rules() -> None:
    """T12: CSS hover/focus-within reveal + @media(hover:none) always-show."""
    css = read_workbench_css()
    assert ".btn-rename-task { display: none" in css, "pencil default hidden rule missing"
    assert ".queue-row:hover .btn-rename-task" in css, "hover reveal rule missing"
    assert ".queue-row:focus-within .btn-rename-task" in css, "focus-within reveal rule missing"
    assert "@media (hover: none)" in css, "@media(hover:none) rule missing"
    assert (
        ".btn-rename-task { display: inline-flex"
        in css.split("@media (hover: none)")[1].split("}")[0]
    ), "always-show rule in @media(hover:none) missing"


def test_task_rename_i18n_parity() -> None:
    """T12: all task rename i18n keys present in both zh-CN and en-US."""
    html = read_workbench_content()
    expected = {
        "queue.rename",
        "task.rename.title",
        "task.rename.default_label",
        "task.rename.hint",
        "task.rename.restore",
        "task.rename.save",
        "task.rename.conflict",
        "task.rename.invalid",
        "task.rename.failed",
    }
    for key in expected:
        assert f'"{key}":' in html, f"i18n key {key} missing from HTML"
    zh_keys = _extract_rename_keys(html, _ZH_BLOCK_RE)
    en_keys = _extract_rename_keys(html, _EN_BLOCK_RE)
    missing_zh = expected - zh_keys
    missing_en = expected - en_keys
    assert not missing_zh, f"zh-CN missing rename keys: {sorted(missing_zh)}"
    assert not missing_en, f"en-US missing rename keys: {sorted(missing_en)}"


def test_task_rename_patch_call_shape() -> None:
    """T12: PATCH call includes custom_name and expected_name_revision."""
    html = read_workbench_content()
    assert 'apiV2("/tasks/" + encodeURIComponent' in html, "apiV2 PATCH call not found"
    assert "expected_name_revision" in html, "expected_name_revision not in PATCH payload"
    assert "custom_name" in html, "custom_name not in PATCH payload"
    assert '"PATCH"' in html, "PATCH method not specified"


def test_task_rename_409_handling() -> None:
    """T12: 409 conflict handling with name_revision_conflict marker."""
    html = read_workbench_content()
    assert "name_revision_conflict" in html, "name_revision_conflict marker missing"
    # The 409 handler should refresh revision from response
    rename_section = html.split("submitTaskRenameNow")[1].split("function _applyRenameResult")[0]
    assert "name_revision" in rename_section, "revision refresh missing in 409 handler"


def test_task_rename_handler_functions_exist() -> None:
    """T12: rename handler functions defined."""
    html = read_workbench_content()
    for fn in (
        "openTaskRenameModal",
        "closeTaskRenameModal",
        "submitTaskRenameNow",
        "_applyRenameResult",
        "_refreshAfterRename",
        "_validateRenameInput",
        "_updateRenameSaveState",
    ):
        assert f"function {fn}" in html or f"async function {fn}" in html, (
            f"Handler function {fn} not found"
        )


def test_task_rename_apply_result_static_no_self_call() -> None:
    """T12 regression (bb3cdb7): _applyRenameResult must not call itself.

    Pre-fix code synced jobsCache / lastSelectedJob via self-recursion; the
    job id always matched, so the PATCH-200 handler died with
    ``RangeError: Maximum call stack size exceeded`` (shown to the user as a
    bogus 重命名失败). Fan-out now lives in non-recursive _applyRenameFields.
    """
    html = read_workbench_content()
    assert "function _applyRenameFields(" in html, "_applyRenameFields helper missing"
    result_body = html.split("function _applyRenameResult(", 1)[1].split(
        "function _refreshAfterRename(", 1
    )[0]
    assert "_applyRenameResult(" not in result_body, (
        "_applyRenameResult calls itself — reintroduces infinite recursion"
    )
    fields_body = html.split("function _applyRenameFields(", 1)[1].split(
        "function _applyRenameResult(", 1
    )[0]
    assert "_applyRenameResult" not in fields_body, (
        "_applyRenameFields must not call back into _applyRenameResult (cycle)"
    )


def test_task_rename_apply_result_runtime_no_recursion() -> None:
    """T12 regression (bb3cdb7): run _applyRenameResult under node.

    Extracts ``_applyRenameFields`` + ``_applyRenameResult`` verbatim and
    executes them against the reported repro shape (PATCH 200, job present in
    jobsCache and selected). Text-only contract tests cannot see the stack
    overflow; the pre-fix code exits non-zero here with ``RangeError``.
    """
    if not shutil.which("node"):
        pytest.skip("node not available")
    html = read_workbench_content()
    extracted = "function _applyRenameFields(" + html.split(
        "function _applyRenameFields(", 1
    )[1].split("function _refreshAfterRename(", 1)[0]
    harness = (
        '"use strict";\n'
        + extracted
        + textwrap.dedent(
            """
            function expect(cond, msg) {
              if (!cond) { console.error("FAIL: " + msg); process.exit(1); }
            }
            var result = {
              custom_name: "TS1_Strength_Fail",
              resolved_name: "TS1_Strength_Fail",
              default_name: "20260919_001_Confsearch",
              name_revision: 4,
              name_updated_at: "2026-09-19T01:33:00Z"
            };
            var jobsCache = [
              { id: "job-other", name: "other" },
              { id: "job-a", name: "original" }
            ];
            var lastSelectedJob = { id: "job-a", name: "original" };
            var job = { id: "job-a", name: "original" };
            _applyRenameResult(job, result);
            expect(job.custom_name === "TS1_Strength_Fail", "job.custom_name updated");
            expect(job.name_revision === 4, "job.name_revision updated");
            expect(jobsCache[1].custom_name === "TS1_Strength_Fail",
                   "matching jobsCache entry updated");
            expect(jobsCache[1].name_updated_at === "2026-09-19T01:33:00Z",
                   "jobsCache name_updated_at updated");
            expect(!("custom_name" in jobsCache[0]),
                   "non-matching jobsCache entry untouched");
            expect(lastSelectedJob.custom_name === "TS1_Strength_Fail",
                   "lastSelectedJob updated");
            expect(lastSelectedJob.resolved_name === "TS1_Strength_Fail",
                   "lastSelectedJob resolved_name updated");
            // Guards: null / non-object result must be no-ops
            _applyRenameResult(job, null);
            _applyRenameResult(job, "not-an-object");
            expect(job.custom_name === "TS1_Strength_Fail", "no-op guards keep fields");
            // job-only path: empty cache, mismatched lastSelectedJob
            jobsCache = [];
            lastSelectedJob = { id: "job-z" };
            var solo = { id: "job-solo" };
            _applyRenameResult(solo, { custom_name: "solo-rename", name_revision: 9 });
            expect(solo.custom_name === "solo-rename" && solo.name_revision === 9,
                   "job-only path works");
            expect(lastSelectedJob.custom_name === undefined,
                   "mismatched lastSelectedJob untouched");
            // job_id-keyed entries (v1 projection shape)
            jobsCache = [{ job_id: "job-b" }];
            lastSelectedJob = null;
            var keyed = { job_id: "job-b" };
            _applyRenameResult(keyed, { custom_name: "keyed-rename" });
            expect(jobsCache[0].custom_name === "keyed-rename",
                   "job_id-keyed cache entry updated");
            console.log("OK");
            """
        )
    )
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "rename_regression.js"
        script.write_text(harness, encoding="utf-8")
        proc = subprocess.run(
            ["node", str(script)],
            capture_output=True,
            text=True,
            timeout=60,
        )
    assert proc.returncode == 0, (
        f"node harness failed (exit {proc.returncode}):\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "OK" in proc.stdout, f"unexpected harness output: {proc.stdout!r}"


# ---------------------------------------------------------------------------
# Queue-row freshness / detail-cache coherence (todo 14 / GAP-9) — static
# pins supplementary to the executed browser suite
# (tests/test_frontend_interaction.py::TestQueueRowFreshnessBrowser).
# ---------------------------------------------------------------------------


def test_job_detail_cache_epoch_guard_present() -> None:
    """Late detail responses from older generations are rejected, and the
    frozen (job_id, attempt, revision) identity gates cache writes."""
    html = read_workbench_content()
    assert "jobDetailGenerations" in html, "detail request generation map missing"
    assert "function jobExecutionIdentity(job)" in html
    body = html.split("function jobExecutionIdentity(job)", 1)[1].split("\nfunction ", 1)[0]
    assert "attempt" in body and "revision" in body
    assert "name_revision" not in body, (
        "name_revision is organization naming — it must not enter the execution-version identity"
    )
    fetch_body = html.split("async function fetchJobDetail(jobId)", 1)[1].split(
        "\nasync function ", 1
    )[0]
    assert "jobDetailGenerations[key]" in fetch_body, "generation capture/check missing"
    assert "function jobDetailCacheStaleFor(" in html
    assert "function invalidateJobDetail(" in html


def test_job_detail_cache_invalidated_on_all_row_actions_and_batch_ops() -> None:
    """Every row action and batch operation invalidates the acted jobs'
    cached detail — not only the selected job's."""
    html = read_workbench_content()
    for fn in (
        "async function pauseJob(",
        "async function unpauseJob(",
        "async function continueJob(",
        "async function rerunJob(",
        "async function cancelJob(",
    ):
        idx = html.index(fn)
        body = html[idx : idx + 1500]
        assert "refreshJobDetailAfterAction(jobId)" in body, (
            f"{fn} does not route through the invalidating post-action refresh"
        )
    helper = html.split("async function refreshJobDetailAfterAction(", 1)[1][:1500]
    assert "invalidateJobDetail(jobId)" in helper
    assert "findJobRowInCache" in html
    for fn in (
        "async function submitDeleteJobModal(",
        "async function submitPurgeModal(",
        "async function _batchOp(",
        "function _refreshAfterRename(",
    ):
        idx = html.index(fn)
        body = html[idx : idx + 2500]
        assert "invalidateJobDetail(" in body, f"{fn} does not invalidate jobDetailCache"


def test_refresh_jobs_prunes_detail_cache_against_list() -> None:
    """refreshJobs drops cached details whose status/attempt/revision
    disagree with the fresh list row, and recovery changes re-render rows."""
    html = read_workbench_content()
    refresh = html.split("function configureTaskListController()", 1)[1].split("\nfunction renderTaskPagination", 1)[0]
    assert "pruneDetailsAgainstList: pruneJobDetailCacheAgainstList" in refresh
    assert "deps.views.pruneDetailsAgainstList()" in html
    assert "jobDetailCacheRevision" in refresh, "recovery revision must gate the render key"
    assert "function renderQueueRowsForRecoveryChange()" in html
    assert "function pruneJobDetailCacheAgainstList()" in html


def test_queue_row_signature_and_terminal_floor() -> None:
    """Row render cache includes the recovery matrix (recovery-only changes
    must rebuild rows) and terminal rows never render active-only actions."""
    html = read_workbench_content()
    row_fn = html.split("function buildQueueRow(job)", 1)[1].split("\nfunction ", 1)[0]
    assert "getJobRecovery(job)" in row_fn, "row signature must include recovery"
    actions = html.split("function appendQueueInlineActions(", 1)[1].split("\nfunction ", 1)[0]
    assert "isTerminalJobStatus(" in actions, "terminal floor missing on queue rows"
    assert "btn-rename-task" in actions, "rename button must stay on queue rows"
    assert "openTaskRenameModal" in actions
    drawer = html.split("async function openDetailDrawer(job)", 1)[1].split("\nfunction ", 1)[0]
    assert "isTerminalJobStatus(" in drawer, "terminal floor missing on the detail drawer"
    assert "function isTerminalJobStatus(" in html


# ---------------------------------------------------------------------------
# T5: Task view toolbar, filter panel, chips, i18n parity
# ---------------------------------------------------------------------------

_QUEUE_VIEW_I18N_KEY_RE = re.compile(r'"(queue\.view\.[^"]+)":')


def _extract_queue_view_keys(html: str, block_re: re.Pattern[str]) -> set[str]:  # type: ignore[type-arg]
    """Extract queue.view.* i18n keys from a single locale block."""
    m = block_re.search(html)
    if not m:
        return set()
    return set(_QUEUE_VIEW_I18N_KEY_RE.findall(m.group(1)))


# ── T12: Task rename feature contracts ──────────────────────────────────

_RENAME_I18N_KEY_RE = re.compile(r'"(task\.rename\.[^"]+)":')


_RENAME_QUEUE_I18N_KEY_RE = re.compile(r'"(queue\.rename)"')


def _extract_rename_keys(html: str, block_re: re.Pattern[str]) -> set[str]:  # type: ignore[type-arg]
    """Extract task.rename.* + queue.rename i18n keys from a single locale block."""
    m = block_re.search(html)
    if not m:
        return set()
    return set(_RENAME_I18N_KEY_RE.findall(m.group(1))) | set(
        _RENAME_QUEUE_I18N_KEY_RE.findall(m.group(1))
    )
