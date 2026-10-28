"""Frontend sync page tests — shell area.

Split from tests/test_frontend_sync.py (T29, 2026-10-18). Every test keeps
its original name and body; the old -> new mapping is
tests/test_frontend_sync.py::<name> -> tests/test_frontend_sync_shell.py
::<name>. Shared helpers live in tests/_frontend_sync_shared.py.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import textwrap

import pytest

from tests._frontend_sync_shared import (
    _EN_BLOCK_RE,
    _SV_JS_PATH,
    _ZH_BLOCK_RE,
    FRONTEND,
    FRONTEND_FILES,
    FRONTEND_JS_DIR,
    REPO_ROOT,
    SERVER,
    _extract_structure_keys,
    read_workbench_content,
)


def test_default_workbench_keeps_original_v2_frontend_and_v1_contract() -> None:
    html = read_workbench_content()
    server = SERVER.read_text(encoding="utf-8")

    assert 'html_path = _FRONTEND_DIR / "ACP_Workbench_v2.html"' in server
    assert "ACP_Workbench_minimal.html" not in server
    assert 'const API_BASE = "/api/v1"' in html
    for feature in ("workflow-catalog", "method-catalog", "/uploads", "/structures/parse"):
        assert feature in html
    for action in ("pause", "unpause", "continue", "rerun", "purge"):
        assert f'"/jobs/" + encodeURIComponent(jobId) + "/{action}"' in html or action in html
    assert "/jobs/" in html and "/detail" in html
    assert "updateSrPickButtons" not in html, (
        "T25: the retired SR pick hook (empty stub + its setMode caller) must stay removed"
    )
    assert 'serviceStatus === "ok"' in html
    assert 'if (!response.ok)' in html
    assert 'await api("/method-catalog")' in html
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
    assert ">结构查看器</button>" in html
    assert '"tab.structure": "结构查看器"' in html
    assert '"tab.structure": "Structure Viewer"' in html
    assert '"tab.conformers"' not in html, "tab.conformers i18n key must be removed"
    assert '"tab.3d"' not in html, "tab.3d i18n key must be removed"
    # Other tabs must remain
    assert 'data-tab="path"' in html
    assert 'data-tab="energy"' in html
    assert 'data-tab="wavefunction"' in html
    # Compat mapping: stale "3d"/"conformers" -> "structure"
    assert (
        'tab === "3d" || tab === "conformers"' in html
        or 'tab === "conformers" || tab === "3d"' in html
    )

    # Renamed tab: HTML button + i18n zh-CN + i18n en-US
    assert ">能量与轨迹</button>" in html
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
    assert (
        'save-candidate" data-energy-frame-id="\' + escapeHtml(frameIdStr) + \'" disabled' in html
    )

    # Sampling hooks
    assert 'data-sampling-view="' in html
    assert "samplingState" in html

    # Convergence panel hooks
    assert "data-optimization-convergence" in html
    assert "function energyOptCriteriaRows(data, cycleMetadata)" in html

    # Unified geometry loader
    assert "function energyGraphLoadFrameGeometry(node)" in html


def test_minimal_frontend_is_not_the_default_page() -> None:
    server = SERVER.read_text(encoding="utf-8")

    assert "ACP_Workbench_minimal.html" not in server


def test_frontend_script_has_no_syntax_errors() -> None:
    """Check each actual classic script, without hiding cross-file syntax errors."""
    if not shutil.which("node"):
        import pytest

        pytest.skip("node not available")

    html = FRONTEND.read_text(encoding="utf-8")
    sources = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html)
    assert sources, "Default page must load its scripts"
    for source in sources:
        script = FRONTEND.parent / source.split("?")[0]
        assert script.is_file(), f"Missing script: {source}"
        result = subprocess.run(
            ["node", "--check", str(script)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"node --check failed for {source}:\n{result.stderr}"


def test_frontend_files_exist_and_readable() -> None:
    for path in FRONTEND_FILES:
        assert path.exists(), f"Frontend file missing: {path}"
        content = path.read_text(encoding="utf-8")
        assert len(content) > 0, f"Frontend file is empty: {path}"


def test_v2_html_loads_structure_viewer_modules() -> None:
    html = read_workbench_content()
    assert '<link rel="stylesheet" href="css/structure_viewer.css">' in html
    assert '<script src="js/geometry_store.js"></script>' in html
    assert '<script src="js/structure_viewer.js"></script>' in html
    assert '<script src="js/structure_editor.js"></script>' in html
    assert '<script src="js/vibration_viewer.js"></script>' in html
    # The store defines window.ACPGeometryStore before any consumer module
    # evaluates (structure_viewer.js reads it at call time, but the contract
    # is a strict load order: store first).
    assert html.index('<script src="js/geometry_store.js"></script>') < html.index(
        '<script src="js/structure_viewer.js"></script>'
    ), "geometry_store.js must load BEFORE structure_viewer.js"


def test_v2_html_dropped_legacy_pick_and_measure_state() -> None:
    """The HTML uses native 3Dmol atom.index + ACPGeometryStore; the removed
    legacy ways of tracking picks/measurements must not come back."""
    html = FRONTEND.read_text(encoding="utf-8")

    assert "atom.acpId" not in html, "legacy acpId atom property must not return"
    assert "measureBuffer" not in html, "legacy measureBuffer must not return"
    assert "molDoc.selection" not in html, "legacy molDoc.selection must not return"
    assert "molDoc.measures" not in html, "legacy molDoc.measures must not return"


@pytest.mark.parametrize(
    "js_file",
    [
        "geometry_store.js",
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
    assert result.returncode == 0, f"node --check {js_file} failed:\n{result.stderr}"


def test_i18n_str_fallback_sweep() -> None:
    """Todo-42 mechanical sweep: every STR fallback across the three viewer
    modules maps to a dictionary key present in BOTH locales — via a _t /
    _editBtn / _playbackBtn call-site link or the declared map above."""
    html = read_workbench_content()
    zh_keys = _extract_structure_keys(html, _ZH_BLOCK_RE)
    en_keys = _extract_structure_keys(html, _EN_BLOCK_RE)

    for module_name, declared in _STR_DECLARED_KEY_MAP.items():
        src = (FRONTEND_JS_DIR / module_name).read_text(encoding="utf-8")
        str_block = src.split("var STR = {", 1)[1].split("\n  };", 1)[0]
        str_names = set(re.findall(r"\b([A-Z][A-Z0-9_]+):", str_block))
        str_names -= _DYNAMIC_STR_NAMES
        assert str_names, f"{module_name}: STR table not parsed"

        linked = {
            name: key for key, name in re.findall(r'_t\("([^"]+)",\s*STR\.([A-Z][A-Z0-9_]+)\)', src)
        }
        for helper in ("_editBtn", "_playbackBtn"):
            for key, name in re.findall(rf'{helper}\("([^"]+)",\s*STR\.([A-Z][A-Z0-9_]+)\)', src):
                linked[name] = key

        orphans = str_names - set(linked) - set(declared)
        assert not orphans, (
            f"{module_name}: STR fallbacks with no dictionary link: {sorted(orphans)}"
        )
        for str_name in str_names:
            key = linked.get(str_name) or declared.get(str_name)
            assert key is not None, str_name
            assert key in zh_keys, f"{module_name} {str_name}: {key} missing zh-CN"
            assert key in en_keys, f"{module_name} {str_name}: {key} missing en-US"


def test_str_keys_linked_via_t_calls() -> None:
    """All new STR keys are linked via _t() calls in structure_viewer.js."""
    sv = _SV_JS_PATH.read_text(encoding="utf-8")

    for key in (
        "VIB_BTN",
        "VIB_IMAG_BTN",
        "MEASURE_BTN",
        "MEASURE_TYPE_DISTANCE",
        "MEASURE_TYPE_ANGLE",
        "MEASURE_TYPE_DIHEDRAL",
        "MEASURE_APPLY",
        "MEASURE_APPLIED",
        "MEASURE_RANGE_WARN",
    ):
        assert f"STR.{key}" in sv, f"STR.{key} not referenced via _t() in structure_viewer.js"


def test_workbench_api_timeout_no_raw_abort_exception() -> None:
    """api() timeouts surface a localized message, never the raw DOMException.

    2026-09-28 regression: the blanket 8s AbortController timeout aborted slow
    requests without a reason, so SFTP-backed remote endpoints (routinely >8s
    on a Raspberry Pi) rendered "加载失败: signal is aborted without reason".
    Timeouts now throw the localized ``api.timeout`` error, and remote-capable
    endpoints opt into the ``apiRemote()`` budget instead.
    """
    html = read_workbench_content()

    assert "var API_TIMEOUT_MS = 8000;" in html
    assert "var API_REMOTE_TIMEOUT_MS = 60000;" in html
    assert "function apiRemote(path, opts)" in html
    assert "var timedOut = false;" in html
    assert 'settings.translate("api.timeout", { seconds: seconds })' in html
    assert "translate: t" in html
    assert "return ACPClient.transport.request(url, opts)" in html
    # Caller-provided aborts keep the AbortError name (silent-cancel filters).
    assert "if (timedOut) {" in html
    assert "throw e;" in html
    assert '"api.timeout": "请求超时' in html
    assert '"api.timeout": "Request timed out' in html

    for fragment in (
        'apiRemote("/jobs/" + encodeURIComponent(jobId) + "/remote-files")',
        'apiRemote("/jobs/" + encodeURIComponent(jobId) + "/pes/review").then(',
        'apiRemote("/jobs/" + encodeURIComponent(jobId) + "/pes/review/restore", {',
        # FE-04 slice 1 removed the s2scanLoadAssets legacy fallback that held
        # the paren-terminated structure-sources remote call; wizard.js:3318
        # keeps a comma-form call site covered by the transport contract.
        # FE-04 slice 2 removed the dead s2scanLoadStep5Data remote calls
        # (s2/profile + s2/candidates) together with the B-group report chain.
        "await apiRemote(url)",
        "jobRemoteNodeName(job) ? await apiRemote(url) : await api(url)",
        "apiRemote(s2Url, ac ? { signal: ac.signal } : {})",
    ):
        assert fragment in html, f"missing remote-budget call site: {fragment}"
    assert html.count("selectedJobIsRemote ? await apiRemote(url) : await api(url)") == 2

    for stale in (
        'api("/jobs/" + encodeURIComponent(jobId) + "/remote-files")',
        'api("/jobs/" + encodeURIComponent(jobId) + "/pes/review")',
        'api("/jobs/" + encodeURIComponent(s2scanState.jobId) + "/s2/profile")',
        'api("/jobs/" + encodeURIComponent(s2scanState.jobId) + "/s2/candidates")',
    ):
        assert stale not in html, f"remote endpoint regressed to 8s default: {stale}"


def test_workbench_api_timeout_behavior() -> None:
    """Node behavior: own timeout → localized error; caller abort → AbortError."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    html = read_workbench_content()
    match = re.search(
        r"(var API_TIMEOUT_MS = 8000;[\s\S]*?async function apiV2\(path, opts\) \{[\s\S]*?\n\})",
        html,
    )
    assert match, "api()/apiRemote() block not found in workbench HTML"
    api_source = match.group(1)
    modules = "\n".join((FRONTEND_JS_DIR / relative).read_text(encoding="utf-8")
                        for relative in ("client/transport.js", "client/resource-store.js",
                                         "workbench/contracts/task_view_contract.js", "client/task-view-query.js"))

    script = (
        "const API_BASE = '/api/v1';\n"
        "function t(key, vars) { return key + (vars ? ':' + JSON.stringify(vars) : ''); }\n"
        "function fail(msg) { console.error('FAIL: ' + msg); process.exit(1); }\n"
        "class FakeAbortError extends Error { constructor() { super('signal is aborted without reason'); this.name = 'AbortError'; } }\n"
        + "global.window = global;\n" + modules + "\n" + api_source
        + "\n"
        + textwrap.dedent(
            """
            (async function main() {
              // Own timeout: localized error, never the raw AbortError text.
              global.fetch = function (url, opts) {
                return new Promise(function (_resolve, reject) {
                  if (opts && opts.signal) {
                    opts.signal.addEventListener('abort', function () { reject(new FakeAbortError()); });
                  }
                });
              };
              try {
                await api('/slow', { timeoutMs: 30 });
                fail('timeout did not reject');
              } catch (e) {
                if (e.name === 'AbortError') fail('timeout leaked AbortError');
                if (String(e.message).indexOf('aborted') >= 0)
                    fail('timeout leaked raw abort text: ' + e.message);
                if (String(e.message).indexOf('api.timeout') !== 0) fail('timeout message missing key: ' + e.message);
              }

              // Caller abort keeps the AbortError name for silent-cancel filters.
              var ac = new AbortController();
              var pending = api('/cancel', { signal: ac.signal });
              setTimeout(function () { ac.abort(); }, 10);
              try {
                await pending;
                fail('caller abort did not reject');
              } catch (e) {
                if (e.name !== 'AbortError') fail('caller abort lost AbortError name: ' + e.name);
              }

              // Happy path still resolves the JSON body.
              global.fetch = function () {
                return Promise.resolve({
                  ok: true,
                  headers: { get: function () { return 'application/json'; } },
                  json: function () { return Promise.resolve({ ok: 1 }); }
                });
              };
              var body = await api('/fast', { timeoutMs: 10000 });
              if (!body || body.ok !== 1) fail('happy path body mismatch');

              // v2 shares readable timeouts, including the response body.
              global.fetch = function (url, opts) {
                return Promise.resolve({
                  ok: true,
                  headers: { get: function () { return 'application/json'; } },
                  json: function () {
                    return new Promise(function (_resolve, reject) {
                      opts.signal.addEventListener('abort', function () { reject(new FakeAbortError()); });
                    });
                  }
                });
              };
              var reusable = { timeoutMs: 30 };
              try {
                await apiV2('/slow-body', reusable);
                fail('response body escaped the timeout');
              } catch (e) {
                if (e.name === 'AbortError' || !e.message.startsWith('api.timeout')) fail('v2 timeout not readable');
              }
              if (reusable.signal) fail('transport mutated caller options');
              var caller = new AbortController();
              try {
                await apiV2('/slow-body', { timeoutMs: 30, signal: caller.signal });
                fail('caller signal disabled the timeout');
              } catch (e) {
                if (e.name === 'AbortError' || !e.message.startsWith('api.timeout')) fail('caller signal timeout not readable');
              }
              console.log('PASS');
            })().catch(function (e) { console.error('FAIL: unexpected', e); process.exit(1); });
            """
        )
    )
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"node failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    assert "PASS" in result.stdout


def test_workbench_links_workbench_stylesheets() -> None:
    """H1 contract: ACP_Workbench_v2.html links external workbench CSS before other styles."""
    html = FRONTEND.read_text(encoding="utf-8")
    assert '<link rel="stylesheet" href="css/workbench/' in html
    legacy_or_base = (
        'href="css/workbench/legacy-base.css"' in html
        or 'href="css/workbench/base.css"' in html
    )
    assert legacy_or_base, "Workbench stylesheet link missing from HTML head"
    wb_idx = html.find('href="css/workbench/')
    sv_idx = html.find('href="css/structure_viewer.css"')
    assert wb_idx < sv_idx, "Workbench base stylesheet must load before structure_viewer.css"


def test_workbench_links_modular_scripts() -> None:
    """H3 contract: ACP_Workbench_v2.html links all 14 modular workbench scripts in dependency order."""
    html = FRONTEND.read_text(encoding="utf-8")
    expected_scripts = [
        "js/workbench/contracts/batch_role_defaults.js",
        "js/workbench/compat/shared-state.js",
        "js/workbench/i18n.js",
        "js/workbench/compat/api.js",
        "js/workbench/features/molecule-view.js",
        "js/workbench/features/energy.js",
        "js/workbench/features/queue.js",
        "js/workbench/features/job-detail.js",
        "js/workbench/features/files-logs.js",
        "js/workbench/features/reaction-builder.js",
        "js/workbench/features/nmr.js",
        "js/workbench/features/wizard.js",
        "js/workbench/features/scan-s2.js",
        "js/workbench/bootstrap.js",
    ]
    assert "js/workbench/compat/workbench-inline.js" not in html, "intermediate workbench-inline.js must not be referenced"
    last_idx = -1
    for s in expected_scripts:
        tag = f'<script src="{s}"></script>'
        assert tag in html, f"Missing script tag: {tag}"
        idx = html.index(tag)
        assert idx > last_idx, f"Script {s} out of order in HTML"
        last_idx = idx
        script_file = REPO_ROOT / "frontend" / s
        assert script_file.is_file(), f"Script file missing: {script_file}"


# ---------------------------------------------------------------------------
# Wave 7 / todo 42: view-state persistence + accessibility + i18n sweep
# ---------------------------------------------------------------------------

# STR fallbacks that are NOT linked through _t()/_editBtn/_playbackBtn call
# sites get their dictionary key DECLARED here (mechanical completeness).
_STR_DECLARED_KEY_MAP: dict[str, dict[str, str]] = {
    "structure_viewer.js": {
        "NO_ENTRY_HINT": "structure.no_entry_hint",
        "VIBRATIONS_NA": "structure.vibrations_na",
        "EDIT_UNDO": "structure.edit.undo",
        "EDIT_REDO": "structure.edit.redo",
        "EDIT_RESET": "structure.edit.reset",
        "EDIT_DISCARD": "structure.edit.discard",
        "EDIT_SAVE_AS": "structure.edit.save_as",
        "EDIT_CANCEL": "structure.edit.cancel",
        "EDIT_EXPORT": "structure.edit.export_xyz",
        "EDIT_SAVE_ASSET": "structure.edit.save_asset",
        "EDIT_NEW_CALC": "structure.edit.new_calc",
        "IRC_STOP": "structure.irc.stop",
        "OVERLAY_CLEAR": "structure.overlay.clear",
    },
    "structure_editor.js": {
        "PROV_EXPLICIT": "structure.edit.prov_explicit",
        "PROV_INFERRED": "structure.edit.prov_inferred",
        "MOVE_LEFT": "structure.edit.move_left",
        "MOVE_RIGHT": "structure.edit.move_right",
        "COLLINEAR_WARNING": "structure.edit.collinear_warning",
        "DIRTY": "structure.edit.dirty",
        "UNDO": "structure.edit.undo",
        "REDO": "structure.edit.redo",
        "RESET": "structure.edit.reset",
        "DISCARD": "structure.edit.discard",
        "SAVE_AS": "structure.edit.save_as",
        "CANCEL": "structure.edit.cancel",
        "COLLISION": "structure.edit.collision",
        "ENTRY_MISMATCH": "structure.edit.entry_mismatch",
        "EXPORT_XYZ": "structure.edit.export_xyz",
        "SAVE_ASSET": "structure.edit.save_asset",
        "NEW_CALC": "structure.edit.new_calc",
        "SAVE_ERROR": "structure.edit.save_error",
        "SAVED_ASSET": "structure.edit.saved",
        "EDITED_SUFFIX": "structure.edit.edited_suffix",
    },
    "vibration_viewer.js": {
        "FREQ_UNIT": "structure.vib.freq_unit",
        "IR_UNIT": "structure.vib.ir_unit",
        "UNIT_ANGSTROM": "structure.vib.unit_angstrom",
        "DISPLAY_ARROWS": "structure.vib.arrow.display_arrows",
        "DISPLAY_ANIMATION": "structure.vib.arrow.display_animation",
        "DISPLAY_COMBO": "structure.vib.arrow.display_combo",
    },
}


# STR names dispatched dynamically (map lookups) — covered by the dedicated
# dynamic-key tests, never linked at a call site.
_DYNAMIC_STR_NAMES = {"SOURCE_KINDS", "REASONS", "TS_HINTS", "WEIGHT_SOURCES"}
