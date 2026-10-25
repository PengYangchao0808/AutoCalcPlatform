"""Frontend sync page tests — jobs area.

Split from tests/test_frontend_sync.py (T29, 2026-10-18). Every test keeps
its original name and body; the old -> new mapping is
tests/test_frontend_sync.py::<name> -> tests/test_frontend_sync_jobs.py
::<name>. Shared helpers live in tests/_frontend_sync_shared.py.
"""
from __future__ import annotations

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
)

# ── P2c: job detail effective config for BatchOptimize ────────────────


def test_p2c_job_detail_effective_config_function_exists() -> None:
    """P2c contract: buildJobDetailEffectiveConfig is defined exactly once."""
    html = read_workbench_content()
    assert html.count("function buildJobDetailEffectiveConfig(") == 1, (
        "buildJobDetailEffectiveConfig must be defined exactly once"
    )


def test_p2c_effective_config_schema_guard() -> None:
    """P2c contract: render only when schema === batch_optimize_effective_v1."""
    html = read_workbench_content()
    assert (
        'effectiveConfig.schema !== "batch_optimize_effective_v1"' in html
        or "effectiveConfig.schema !== 'batch_optimize_effective_v1'" in html
    ), "Schema guard string 'batch_optimize_effective_v1' missing"


def test_p2c_effective_config_orca_summary_chips() -> None:
    """P2c contract: orca_summary arrays drive chip rendering per role."""
    html = read_workbench_content()
    fn_body = html.split("function buildJobDetailEffectiveConfig(", 1)[1]
    fn_body = fn_body.split("\nfunction ", 1)[0]
    assert "orca_summary" in fn_body, "orca_summary key reference missing in detail renderer"
    assert "mc-eff-chip" in fn_body, "mc-eff-chip class missing in detail renderer"
    assert "mc-eff-role-label" in fn_body, "mc-eff-role-label class missing in detail renderer"


def test_p2c_effective_config_null_safety() -> None:
    """P2c contract: non-batch or absent effective_config renders nothing."""
    html = read_workbench_content()
    fn_body = html.split("function buildJobDetailEffectiveConfig(", 1)[1]
    fn_body = fn_body.split("\nfunction ", 1)[0]
    assert "!effectiveConfig" in fn_body, "Null guard for effectiveConfig missing"
    assert "!isPlainObject(effectiveConfig)" in fn_body, "Type guard for effectiveConfig missing"
    call_marker = "buildJobDetailEffectiveConfig(body, detail.effective_config)"
    call_site = html.split(call_marker, 1)[0].rsplit("\n", 1)[-1]
    assert "if (detail)" in call_site, "Call site must guard against null detail"


def test_p2c_effective_config_zh_labels() -> None:
    """P2c contract: Chinese label '\u751f\u6548\u914d\u7f6e' (生效配置) via \\uXXXX."""
    html = read_workbench_content()
    assert r"\u751f\u6548\u914d\u7f6e" in html, (
        "Chinese label '生效配置' (\\u751f\\u6548\\u914d\\u7f6e) missing"
    )


def test_p2c_effective_config_rescue_not_in_api() -> None:
    """P2c contract: rescue/provenance data is NOT exposed in V1JobDetailResponse.

    _read_effective_config reads effective_config.json only (not batch_provenance.json).
    The V1JobDetailResponse has no provenance/rescue field.
    If the backend later adds rescue data to the detail payload, the frontend
    renderer should be updated to display it (TODO: batch_provenance surfacing).
    """
    from acp.api.v1_schemas import V1JobDetailResponse

    fields = V1JobDetailResponse.model_fields
    assert "effective_config" in fields, "effective_config field missing from V1JobDetailResponse"
    assert "provenance" not in fields, (
        "provenance unexpectedly added — update frontend renderer to display rescue history"
    )


def test_job_editor_module_loaded_before_inline_script() -> None:
    """job_editor.js ships, loads in v2, and exports the editor surface."""
    assert JOB_EDITOR_JS.is_file(), "frontend/js/job_editor.js missing"
    html = read_workbench_content()
    tag = '<script src="js/job_editor.js"></script>'
    assert tag in html, "v2 HTML must load job_editor.js"
    wb_first = html.find('<script src="js/workbench/compat/shared-state.js">')
    assert html.index(tag) < wb_first, "job_editor.js loads before workbench bundle scripts"
    js = JOB_EDITOR_JS.read_text(encoding="utf-8")
    for export in (
        "openJobEditor",
        "openRerunMenu",
        "interceptJobCreate",
        "cancelEditor",
        "isActive",
    ):
        assert export + ":" in js, f"ACPJobEditor export missing: {export}"


def test_job_editor_js_has_no_syntax_errors() -> None:
    if not shutil.which("node"):
        pytest.skip("node not available")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".js", delete=False, encoding="utf-8"
    ) as f:
        f.write(JOB_EDITOR_JS.read_text(encoding="utf-8"))
        path = f.name
    proc = subprocess.run(["node", "--check", path], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"node --check failed: {proc.stderr}"


def test_rerun_button_routes_through_action_menu() -> None:
    """↻ opens the 3-action menu (直接重跑/修改参数后重算/复制为新任务),
    both in queue rows and the detail drawer (plan §4.1)."""
    html = read_workbench_content()
    assert "function openRerunActionMenu(" in html
    assert "ACPJobEditor.openRerunMenu(job, btn)" in html
    # Detail drawer routes through the same wrapper (queue + drawer parity).
    assert "openRerunActionMenu(job, dRerunBtn)" in html
    # Queue inline rerun no longer calls rerunJob directly.
    queue_section = html.split("function appendQueueInlineActions(")[1].split("\n}") [0]
    assert "rerunJob(jobId, btn)" not in queue_section, (
        "queue ↻ must open the action menu, not fire a direct rerun"
    )
    js = JOB_EDITOR_JS.read_text(encoding="utf-8")
    for key in ("edit.menu.rerun_direct", "edit.menu.edit_recalculate", "edit.menu.new_from_job"):
        assert key in js, f"menu item key missing: {key}"
    # Menu must not trigger card selection (plan §4.1).
    assert "stopPropagation" in js


def test_edit_banner_container_and_close_guard() -> None:
    """Edit banner container exists; modal close routes through the editor
    guard so a dirty draft asks before vanishing (plan §4.2)."""
    html = read_workbench_content()
    assert 'id="edit-recalc-banner"' in html
    assert "function closeJobModalGuarded(" in html
    assert 'getElementById("modal-cancel").addEventListener("click", closeJobModalGuarded)' in html
    assert 'getElementById("modal-close").addEventListener("click", closeJobModalGuarded)' in html
    assert "ACPJobEditor.cancelEditor(false)" in html


def test_api_create_intercepted_in_edit_mode() -> None:
    """All POST /jobs submits funnel through ACPJobEditor while editing —
    the single chokepoint reuses every existing payload builder (plan §8)."""
    html = read_workbench_content()
    api_body = html.split("async function api(path, opts) {", 1)[1].split(
        "async function apiV2(", 1
    )[0]
    assert 'path === "/jobs"' in api_body
    assert "ACPJobEditor.interceptJobCreate" in api_body


def test_edit_flow_uses_draft_preview_submit_endpoints() -> None:
    js = JOB_EDITOR_JS.read_text(encoding="utf-8")
    assert "/edit-draft" in js
    assert "/edit-recalculate/preview" in js
    assert '"/jobs/" + encodeURIComponent(ctx.sourceJobId) + "/edit-recalculate"' in js
    # Draft hydration reuses the existing pending-new-task channel and the
    # shared config cards instead of a second defaults system.
    assert "applyPendingNewTask" in js
    assert "updateConfigCards" in js


def test_edit_i18n_keys_complete_across_locales() -> None:
    zh = _i18n_block_keys(_ZH_BLOCK_RE, _EDIT_I18N_KEY_RE)
    en = _i18n_block_keys(_EN_BLOCK_RE, _EDIT_I18N_KEY_RE)
    assert zh, "no edit.* keys found in zh-CN"
    assert zh == en, f"edit.* i18n mismatch: zh-only={sorted(zh - en)} en-only={sorted(en - zh)}"
    for required in (
        "edit.menu.edit_recalculate",
        "edit.check_and_submit",
        "edit.summary_cleanup_warning",
        "edit.confirm_in_place",
        "edit.confirm_new_job",
        "edit.discard_confirm",
        "edit.copy_to_structure",
        "edit.original_items_summary",
        "edit.batch_panel_hint",
        "edit.method_backfilled",
        "edit.effective_snapshot",
        "edit.effective_recomputed",
    ):
        assert required in zh, f"required edit key missing: {required}"


# ---------------------------------------------------------------------------
# Edit-and-recalculate unified-state fixes (single draft / single source
# state / single submit entry; BatchOptimize full hydrate + patch serialize).
# ---------------------------------------------------------------------------


def test_single_submit_entry_dispatches_to_editor() -> None:
    """The footer #modal-submit is the ONLY submit button: it dispatches to
    ACPJobEditor.checkAndSubmit while a draft is open, otherwise the normal
    create flow. The banner submit button is gone."""
    html = read_workbench_content()
    js = JOB_EDITOR_JS.read_text(encoding="utf-8")
    assert "function handleModalSubmit()" in html
    assert "ACPJobEditor.checkAndSubmit()" in html
    assert "return submitJobModal();" in html
    assert (
        'document.getElementById("modal-submit").addEventListener("click", handleModalSubmit)'
        in html
    )
    # The duplicate banner submit entry must not exist anywhere.
    assert 'id="edit-recalc-submit"' not in html
    assert "edit-recalc-submit" not in js
    # The editor owns the footer button label/disabled state in edit mode and
    # restores the standard 提交 label on close.
    assert "checkAndSubmit: checkAndSubmit" in js
    assert 'btn.setAttribute("data-i18n", "modal.submit")' in js


def test_profile_summary_resolved_from_catalog() -> None:
    """Profile titles resolve from profile_id against the catalog on every
    render — never from the mutable wizardState.method.profile_label (the
    stale "仅优化" bug); language switches re-resolve via applyI18n."""
    html = read_workbench_content()
    assert "function resolveCurrentProfile()" in html
    assert "function resolveCurrentProfileLabel()" in html
    cards = html.split("function updateConfigCards()", 1)[1].split("\nfunction ", 1)[0]
    assert "resolveCurrentProfileLabel()" in cards
    assert "profile_label" not in cards, "config card must not read stale profile_label"
    summary = html.split("function syncBatchOptimizeProfileSummary()", 1)[1].split(
        "\nfunction ", 1
    )[0]
    assert "resolveCurrentProfileLabel()" in summary
    # batch_roles INT/TS line must win over the engine-less "(default)" line.
    batch_pos = cards.find('lv.level_id === "batch" && stages.batch_roles')
    engine_pos = cards.find("st._disabled === true")
    assert batch_pos != -1 and engine_pos != -1 and batch_pos < engine_pos
    # applyI18n refreshes open-modal summaries so titles follow the language.
    i18n_body = html.split("function applyI18n()", 1)[1].split("\nfunction ", 1)[0]
    assert "updateConfigCards()" in i18n_body
    assert "ACPJobEditor.refreshUi()" in i18n_body


def test_editor_single_source_area() -> None:
    """The modal has one three-entry source browser and one preview state."""
    html = read_workbench_content()
    js = JOB_EDITOR_JS.read_text(encoding="utf-8")
    tabs = html.split('class="input-mode-tabs"', 1)[1].split("</div>", 1)[0]
    assert 'data-input-mode="task"' in tabs
    assert 'data-input-mode="structure"' in tabs
    assert 'data-input-mode="upload"' in tabs
    assert 'data-input-mode="original"' not in tabs
    assert 'data-input-mode="last_structure"' not in tabs
    assert 'id="input-panel-original"' not in html
    assert 'id="edit-recalc-footer"' in html
    # Single source-state authority in the editor.
    assert "sourceSelection" in js
    assert 'name="edit-input-mode"' not in js, "banner input radios must be gone"
    mode_fn = html.split("function setWizardInputMode(mode)", 1)[1].split("\nfunction ", 1)[0]
    assert "ACPJobEditor.onSourceTabChange(" in mode_fn
    assert 'mode === "candidate"' in mode_fn, "candidate→task editor mapping missing"
    assert 'input-panel-task' in mode_fn
    assert 'task-results-browser' in mode_fn
    assert "hydrateTaskStructures(ctx, draft)" in js
    assert 'kind: "original_input"' in js


def test_job_editor_source_tabs_do_not_apply_unparsed_sources() -> None:
    """Changing tabs is navigation; only a successful parse changes the source."""
    js = JOB_EDITOR_JS.read_text(encoding="utf-8")
    tab_fn = js.split("function onSourceTabChange(kind)", 1)[1].split(
        "function onParsedSourceApplied", 1
    )[0]
    parsed_fn = js.split("function onParsedSourceApplied", 1)[1].split(
        "function ", 1
    )[0]
    assert 'kind: "manual_input"' not in tab_fn
    assert 'kind: "upload"' not in tab_fn
    assert "batchPreviewItems = []" not in tab_fn
    assert 'kind === "structure" ? "manual_input" : "upload"' in parsed_fn


def test_job_editor_workflow_adapters_registered() -> None:
    """Every active edit workflow has an adapter with the full hydrate/
    serialize surface; the patch contract is presence-based (no ||)."""
    js = JOB_EDITOR_JS.read_text(encoding="utf-8")
    adapters_block = js.split("var workflowAdapters = {", 1)[1].split("};", 1)[0]
    for key in ("BatchOptimize", "Confsearch", "PESsearch", "nmr", "scan", "default"):
        assert key + ":" in adapters_block, f"workflow adapter missing: {key}"
    assert "function has(obj, key)" in js, "presence check helper required"
    assert "hasOwnProperty.call" in js
    # BatchOptimize hydrate must restore batch_roles + profile, serialize must
    # patch (never rebuild) — serialize(hydrate(original)) ≡ original.
    assert "mergeBatchRoles" in js
    assert "buildBatchMethodFromPatch" in js
    assert "methodBaseline" in js
    assert "baselineFormState" in js
    # Baseline dirty state: compare current form vs post-hydrate baseline,
    # not current-default form vs original JSON.
    count_fn = js.split("function countLocalChanges()", 1)[1].split("\nfunction ", 1)[0]
    assert "baselineFormState" in count_fn


def test_job_editor_batch_roundtrip_semantics() -> None:
    """Node runtime harness over the editor's pure internals: BatchOptimize
    hydrate/serialize round-trips, single-field patches, presence semantics
    for null/false/0/"", and legacy flat-field reconstruction."""
    if not shutil.which("node"):
        pytest.skip("node not available")
    js = JOB_EDITOR_JS.read_text(encoding="utf-8")
    contracts_js = (
        FRONTEND_JS_DIR / "workbench" / "contracts" / "batch_role_defaults.js"
    ).read_text(encoding="utf-8")
    harness = (
        '"use strict";\n'
        "global.window = {};\n"
        "global.document = { removeEventListener: function() {}, "
        "getElementById: function() { return null; } };\n"
        + contracts_js
        + "\n"
        + js
        + textwrap.dedent(
            """
            var I = window.ACPJobEditorInternals;
            function expect(cond, msg) {
              if (!cond) { console.error("FAIL: " + msg); process.exit(1); }
            }
            var original = {
              schema_id: "batch_optimize",
              profile: "opt_freq_sp_thermo",
              profile_id: "opt_freq_sp_thermo",
              optimization_method: "wB97X-D4",
              optimization_basis: "def2-TZVP",
              single_point_method: "DLPNO-CCSD(T)",
              single_point_basis: "def2-TZVP",
              temperature: 298.15,
              batch_roles: {
                int: { method: "wB97X-D4", basis: "def2-TZVP",
                       opt_convergence: "tight", opt_max_iter: null,
                       scf_orbital_inherit: true, scf_damp: false,
                       opt_max_rescue: 2, sp_method: null, aux_j_basis: "",
                       temperature: 298.15 },
                ts: { method: "wB97X-D4", opt_trust_radius: 0.1,
                      opt_initial_hessian: "calculate", opt_recalc_hess: 5,
                      opt_max_iter: 0, basis: "", scf_shift: false }
              }
            };
            var defaults = I.batchRoleStaticDefaults();
            // hydrate: static defaults <- legacy flat <- original batch_roles
            var hydrated = I.mergeBatchRoles(original, defaults);
            // presence semantics survive the merge
            expect(hydrated.int.opt_max_iter === null, "null preserved");
            expect(hydrated.int.scf_damp === false, "false preserved");
            expect(hydrated.ts.opt_max_iter === 0, "zero preserved");
            expect(hydrated.ts.basis === "", "empty string preserved");
            // serialize(hydrate(original)) with no user edits === original
            var out = I.buildBatchMethodFromPatch(
              original, hydrated, I.deepCopy(hydrated),
              "opt_freq_sp_thermo", "opt_freq_sp_thermo", defaults);
            expect(I.deepEqual(out, original), "unmodified roundtrip equals original");
            // single TS trust-radius edit: patch touches only that field
            var cur = I.deepCopy(hydrated);
            cur.ts.opt_trust_radius = 0.15;
            out = I.buildBatchMethodFromPatch(
              original, hydrated, cur, "opt_freq_sp_thermo", "opt_freq_sp_thermo", defaults);
            var expected = I.deepCopy(original);
            expected.batch_roles.ts.opt_trust_radius = 0.15;
            expect(I.deepEqual(out, expected), "single-field patch touches only that field");
            // profile switch patches profile_id/profile only
            out = I.buildBatchMethodFromPatch(
              original, hydrated, I.deepCopy(hydrated),
              "opt_freq_sp_thermo", "opt_freq", defaults);
            expect(out.profile_id === "opt_freq" && out.profile === "opt_freq",
                   "profile patch applied");
            expect(out.batch_roles.ts.opt_trust_radius === 0.1,
                   "profile patch leaves roles untouched");
            // INT method edit syncs the legacy flat mirror for consumers
            cur = I.deepCopy(hydrated);
            cur.int.method = "wB97M-V";
            out = I.buildBatchMethodFromPatch(
              original, hydrated, cur, "opt_freq_sp_thermo", "opt_freq_sp_thermo", defaults);
            expect(out.batch_roles.int.method === "wB97M-V", "int method patched");
            expect(out.optimization_method === "wB97M-V", "flat mirror synced");
            // legacy flat reconstruction (no batch_roles at all)
            var legacy = { optimization_method: "r2SCAN-3c", optimization_basis: "def2-SVP",
                           transition_state_opt_trust_radius: 0.3, temperature: 310.0 };
            var roles = I.mergeBatchRoles(legacy, defaults);
            expect(roles.int.method === "r2SCAN-3c" && roles.ts.method === "r2SCAN-3c",
                   "flat common method lands on both roles");
            expect(roles.ts.opt_trust_radius === 0.3 && roles.int.opt_trust_radius === null,
                   "role-prefixed flat honored on its role only");
            expect(roles.int.temperature === 310.0 && roles.ts.temperature === 310.0,
                   "flat temperature lands on both roles");
            // batch_roles keys override flat only where present
            var mixed = I.deepCopy(legacy);
            mixed.batch_roles = { ts: { method: "wB97M-V" } };
            roles = I.mergeBatchRoles(mixed, defaults);
            expect(roles.ts.method === "wB97M-V" && roles.int.method === "r2SCAN-3c",
                   "batch_roles override flat only for present keys");
            console.log("OK");
            """
        )
    )
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "editor_roundtrip.js"
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
# Edit-and-recalculate chain (docs/archive/2026-10-16/history/ACP_Edit_And_Recalculate_Plan.md §8/§14)
# ---------------------------------------------------------------------------

JOB_EDITOR_JS = FRONTEND_JS_DIR / "job_editor.js"


_EDIT_I18N_KEY_RE = re.compile(r'"(edit\.[^"]+)":')


def _i18n_block_keys(block_re: re.Pattern[str], key_re: re.Pattern[str]) -> set[str]:
    html = read_workbench_content()
    block = block_re.search(html)
    assert block is not None
    return set(key_re.findall(block.group(1)))
