"""Frontend sync page tests — pes_scan area.

Split from tests/test_frontend_sync.py (T29, 2026-10-18). Every test keeps
its original name and body; the old -> new mapping is
tests/test_frontend_sync.py::<name> -> tests/test_frontend_sync_pes_scan.py
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

from acp.catalog import METHOD_SCHEMAS
from tests._frontend_sync_shared import (
    _EN_BLOCK_RE,
    _ZH_BLOCK_RE,
    FRONTEND_CSS_DIR,
    FRONTEND_JS_DIR,
    read_workbench_content,
)


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
    html = read_workbench_content()
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
    html = read_workbench_content()
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
    html = read_workbench_content()

    payload = html.split("function energyGraphReviewPayload()", 1)[1]
    payload = payload.split("\nfunction ", 1)[0]
    # R4: the working candidate set is owned by the candidateReview controller;
    # the payload maps the controller snapshot (single owner), still as a whole.
    assert "candidateReviewState()" in payload, (
        "the review payload must read the working candidate set from the "
        "candidateReview controller (single owner)"
    )
    assert "candidates.map(" in payload, (
        "the review payload must carry the whole working candidate set"
    )
    assert "s2scanState" not in payload, (
        "the review payload must not read the deprecated s2scanState aliases"
    )
    assert "s2scanState" not in payload, (
        "the review payload must not read the deprecated s2scanState aliases"
    )
    assert re.search(r"candidates\.length\s*[<>]=?\s*2", payload) is None, (
        "the frontend must not impose a >=2 candidate gate — a single TS frame is a valid selection"
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
    html = read_workbench_content()

    # Labels in both locales.
    assert '"energy.card.type_none": "取消候选"' in html, "zh label must read 取消候选, not 无标记"
    assert '"energy.card.type_none": "Remove Candidate"' in html
    # Effect hint shipped in both locales (2 i18n definitions + 1 t() call site).
    assert html.count('"energy.card.type_none_hint":') == 2
    assert 't("energy.card.type_none_hint")' in html
    assert "保存并锁定后生效" in html

    card = _candidate_card_source(html)
    # The none button exposes the hint as a tooltip.
    none_btn = card.split('data-energy-role="none"', 1)[1].split("</button>", 1)[0]
    assert "noneHint" in none_btn, "取消候选 button must carry the effect hint as its title"
    # The hint note renders only while the role buttons are editable.
    assert (
        "(canEditRole ? '<div class=\"energy-card-disabled-note\">' + noneHint + '</div>' : '')"
        in card
    ), "the effect-hint note must be gated on canEditRole (edit mode only)"


def test_pes_interrupted_scan_review_contract() -> None:
    """Failed-task partial scans: interrupted banner (never 计算中), view-only
    geometry gate, and unconverged manual-judgement badge (2026-09).
    """
    html = read_workbench_content()

    # Interrupted scans must never render as 计算中 — both the workspace
    # status badge and the footer completeness label say 扫描中断、数据不完整.
    assert 'status === "interrupted" ? "● 扫描中断、数据不完整"' in html
    assert '"扫描中断、数据不完整" : data.complete' in html
    assert html.count('"energy.header.status.interrupted":') == 2, (
        "interrupted header status must exist in zh + en locales"
    )

    # Frames without usable geometry are view-only: role buttons stay disabled.
    assert "const nodeGeometryOk = metadata.selectable !== false" in html
    assert "&& jobTerminal && nodeGeometryOk" in html, (
        "canEditRole must gate on per-frame geometry availability"
    )
    assert "该帧缺少有效几何" in html, "view-only frames need an explicit note"

    # Unconverged frames are annotatable but flagged as manual judgement.
    assert "未收敛 · 待后续验证" in html
    assert "未收敛帧 · 标注为人工判断" in html


def test_pes_live_status_transition_rebuilds_review_toolbar() -> None:
    """A failed scan can gain review controls without gaining another frame."""
    html = read_workbench_content()
    revision_source = "function energyGraphDataRevision(data) {" + html.split(
        "function energyGraphDataRevision(data) {", 1
    )[1].split("\nfunction optimizationJobItemId", 1)[0]
    script = revision_source + textwrap.dedent(
        """
        const running = {revision: 'same-frames', status: 'running',
          metadata: {job_status: 'running'}};
        const failed = {revision: 'same-frames', status: 'interrupted',
          metadata: {job_status: 'failed', scan_interrupted: true,
            review: {status: 'pending', editable: true}}};
        const saved = {revision: 'same-frames', status: 'interrupted',
          metadata: {job_status: 'failed', scan_interrupted: true,
            review: {status: 'confirmed', revision: 1, editable: true}}};
        if (energyGraphDataRevision(running) === energyGraphDataRevision(failed)) process.exit(1);
        if (energyGraphDataRevision(failed) === energyGraphDataRevision(saved)) process.exit(2);
        """
    )
    if shutil.which("node"):
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    assert 'data-energy-reviewable="\' + (hasReview ? "1" : "0")' in html
    assert 'root.getAttribute("data-energy-reviewable") !== (hasReview ? "1" : "0")' in html


def test_scan_optimizer_method_option_groups_consumed() -> None:
    """(a) scan_optimizer_method option_groups: frontend renders <optgroup> when present.

    The field definition for scan_optimizer_method carries option_groups
    (xtb / composite_dft / conventional_dft).  The frontend buildFieldRow
    must detect option_groups on the field definition and render <optgroup>
    elements with localized labels.
    """
    from acp.catalog import FIELD_DEFINITIONS

    html = read_workbench_content()

    # Verify the backend actually defines option_groups for scan_optimizer_method
    fd = FIELD_DEFINITIONS.get("scan_optimizer_method", {})
    assert "option_groups" in fd, "scan_optimizer_method must have option_groups in FIELD_DEFINITIONS"
    assert len(fd["option_groups"]) >= 2, "need at least 2 option groups"

    # Verify the frontend code handles option_groups generically
    assert "option_groups" in html, "frontend must reference option_groups"
    assert "optgroup" in html, "frontend must render <optgroup> elements"
    assert 'grpLblKey = currentLang === "zh-CN" ? "label_zh" : "label"' in html or \
           'label_zh' in html, "optgroup labels must be localized"


def test_scan_optimizer_level_method_linkage_registration() -> None:
    """(b) scan_optimizer level method linkage: scan_optimizer_method is the linkage source.

    The frontend buildFieldRow must resolve method-context for the scan_optimizer
    level using scan_optimizer_method (not "functional") as the method field.
    Basis/dispersion/RI must key off scan_optimizer_method for METHOD_META lookup.
    """
    html = read_workbench_content()

    # Verify the level-to-method-field mapping exists
    assert '"scan_optimizer": "scan_optimizer_method"' in html, (
        "scan_optimizer must map to scan_optimizer_method as linkage source"
    )
    # Verify basis/dispersion/RI field mappings exist
    assert '"scan_optimizer_basis": "basis"' in html or \
           '"scan_optimizer_basis"' in html, (
        "scan_optimizer_basis must be mapped for funcFilter lookup"
    )
    assert '"scan_optimizer_dispersion"' in html, (
        "scan_optimizer_dispersion must be handled in linkage"
    )
    assert '"scan_optimizer_ri_approximation"' in html, (
        "scan_optimizer_ri_approximation must be handled in RI lock"
    )
    # Verify scan_optimizer_method change handler exists
    assert 'fieldName === "scan_optimizer_method"' in html, (
        "scan_optimizer_method must have its own change handler for re-default cascade"
    )


def test_copy_scan_level_button_exists() -> None:
    """(c) Copy scan level button exists on single_point card for pes_scan schema.

    The button copies scan_optimizer level fields into single_point, then
    rebuilds the card.  It must be disabled when scan_optimizer has no state.
    """
    html = read_workbench_content()

    assert "modal.copy_scan_level" in html, "copy_scan_level i18n key missing"
    assert "mc-copy-scan-level" in html, "copy button class missing"
    assert 'lvDef.level_id === "single_point"' in html, (
        "copy button must be conditional on single_point level"
    )
    assert 'schema_id === "pes_scan"' in html or 'wizardState.workflow.schema_id' in html, (
        "copy button must be conditional on pes_scan schema"
    )
    assert "rebuildLevelCard(\"single_point\")" in html or \
           "rebuildLevelCard('single_point')" in html, (
        "copy button must rebuild single_point card after copying"
    )


def test_submit_summary_contains_three_lines() -> None:
    """(d) Submit confirmation summary shows three lines before POSTing.

    The summary must include: geometry scan (method + solvent + convergence +
    max iterations + n points), single point energy (method/basis or disabled),
    recovery strategy (per-point retry or native note).
    """
    html = read_workbench_content()

    # Verify i18n keys exist in both locales
    for key in (
        "modal.scan_summary_title",
        "modal.scan_summary_geometry",
        "modal.scan_summary_sp",
        "modal.scan_summary_recovery",
        "modal.scan_summary_sp_disabled",
        "modal.scan_summary_native_no_retry",
        "modal.scan_summary_per_point_retry",
    ):
        assert key in html, f"i18n key {key} missing from frontend"

    # Verify the summary is built in submitPESsearchTask
    submit_fn = html.split("async function submitPESsearchTask()", 1)[1]
    submit_fn = submit_fn.split("\n// Single submit entry", 1)[0]
    assert "scan_summary_geometry" in submit_fn, "summary must include geometry line"
    assert "scan_summary_sp" in submit_fn, "summary must include single point line"
    assert "scan_summary_recovery" in submit_fn, "summary must include recovery line"
    assert "window.confirm" in submit_fn, "summary must use confirm dialog"


def test_recovery_differentiation_native_disables_per_point_retry() -> None:
    """(e) Recovery differentiation: native single-coordinate scan disables per-point retry.

    When the scan has exactly ONE coordinate (native ORCA scan), the
    retry_count and retry_strategy fields in scan_optimizer must be disabled
    with an explanatory note.  Multiple coordinates (double_bond_scan) keep
    them enabled.
    """
    html = read_workbench_content()

    # Verify native single-coordinate detection
    assert "_native_single_coord" in html, "native single-coordinate flag missing"
    assert 'selectionKind !== "double_bond_scan"' in html or \
           'selectionKind === "double_bond_scan"' in html, (
        "coordinate count detection missing"
    )
    # Verify retry fields are disabled for native scan
    assert 'scan_optimizer_retries' in html, "retry field name missing"
    assert 'scan_optimizer_retry_strategy' in html, "retry strategy field name missing"
    assert "_native_single_coord" in html, "native flag must be checked for disable"
    # Verify the i18n key for the note
    assert "modal.native_scan_no_per_point_retry" in html, (
        "native scan no-retry i18n key missing"
    )


def test_pes_scan_profiles_catalog_driven() -> None:
    """(f) pes_scan profiles are read dynamically from catalog (anti-pattern #27).

    The frontend must not hardcode a fixed list of pes_scan profile ids.
    Profiles must be read from METHOD_SCHEMAS["pes_scan"]["profiles"] at
    runtime, so future profile additions require zero frontend changes.
    """
    schema = _get_pes_scan_schema()
    profiles = schema.get("profiles", [])
    assert len(profiles) >= 4, (
        f"pes_scan must have at least 4 profiles (default/economy-dft/standard-dft/hybrid-dft), "
        f"got {len(profiles)}"
    )
    profile_ids = {p["profile_id"] for p in profiles}
    assert "default" in profile_ids, "default profile missing"
    assert "economy-dft" in profile_ids, "economy-dft profile missing"
    assert "standard-dft" in profile_ids, "standard-dft profile missing"
    assert "hybrid-dft" in profile_ids, "hybrid-dft profile missing"

    # Verify no hardcoded profile id list in the frontend for pes_scan
    html = read_workbench_content()
    # The profile selector is built dynamically from schema.profiles
    assert "profiles = schema.profiles || []" in html or \
           "var profiles = schema.profiles" in html, (
        "profiles must be read from schema dynamically"
    )


def test_catalogutils_family_gate_is_payload_driven_not_method_list() -> None:
    """T15: family gating keys on catalog payload signals, never on a
    hardcoded method list (AGENTS anti-pattern #27).
    """
    html = read_workbench_content()
    src = _catalogutils_source(html)

    assert "function isFieldFamilyLocked(" in src
    assert "isFieldFamilyLocked: isFieldFamilyLocked" in src
    assert "function fieldBaseName(" in src
    assert "fieldBaseName: fieldBaseName" in src
    assert "function applyMethodFamilyState(" in src
    assert "applyMethodFamilyState: applyMethodFamilyState" in src

    assert "functional_options_map" in src, "gate must read functional_options_map"
    assert "solvent_models" in src, "gate must read METHOD_META.solvent_models"
    for literal in (
        '"GFN2-xTB"',
        '"GFN1-xTB"',
        '"GFN0-xTB"',
        '"GFN-FF"',
        '"B97-3c"',
        "'gfn'",
        '"gfn"',
    ):
        assert literal not in src, f"hardcoded method literal {literal} in CatalogUtils"


def test_buildfieldrow_family_locked_render_hidden_or_locked() -> None:
    """T15: buildFieldRow renders family-unavailable fields hidden or locked.

    basis -> locked read-only built-in display, dispersion -> built-in badge,
    grid/SCF -> hidden with the stale value cleared.
    """
    html = read_workbench_content()

    assert "family-unavailable fields (T15)" in html
    gate = html.index("if (isFieldFamilyLocked(fieldName, functional)) {")
    assert gate < html.index("var funcOptsMap ="), (
        "family gate must precede generic option resolution"
    )

    gate_block = html[gate : html.index("var funcOptsMap =", gate)]
    assert 'famBase === "basis"' in gate_block
    assert "mc-basis-readonly" in gate_block, "family-locked basis must render read-only"
    assert 'famBase === "dispersion"' in gate_block
    assert "mc-builtin-badge" in gate_block
    assert "hidden entirely for this family" in gate_block
    assert 'famState[fieldName] = "";' in gate_block, (
        "hidden family fields must clear their stored value"
    )
    assert "dest.appendChild(fDiv)" not in gate_block.split("hidden entirely for this family", 1)[1], (
        "hidden family fields must not append their row"
    )


def test_select_empty_default_option_wired() -> None:
    """T15: selects whose catalog default is '' render an explicit empty
    option (single_point grid: DefGrid1/2/3 with default '').
    """
    from acp.catalog import FIELD_DEFINITIONS, METHOD_SCHEMAS

    grid_fd = FIELD_DEFINITIONS["grid"]
    assert grid_fd["options"] == ["DefGrid1", "DefGrid2", "DefGrid3"]
    assert grid_fd["default"] == {"*": ""}
    assert set(grid_fd["option_labels_zh"]) == {"DefGrid1", "DefGrid2", "DefGrid3"}, (
        "zh labels must stay keyed by option value (DefGrid*)"
    )

    sp_level = next(
        lv for lv in METHOD_SCHEMAS["pes_scan"]["method_levels"]
        if lv["level_id"] == "single_point"
    )
    assert "grid" in sp_level["fields"], "single_point level must expose the grid field"

    html = read_workbench_content()
    assert "explicit empty default (T15)" in html
    assert 'defaultEmptyOpt.value = "";' in html
    assert 'curVal === "" && opts.indexOf("") < 0' in html, (
        "empty option must only render when the stored/default value is empty"
    )


def test_method_change_handlers_apply_family_state_sweep() -> None:
    """T15: every method-change handler sweeps the level field list through
    applyMethodFamilyState (clears family-locked fields, re-defaults values
    the new family no longer admits).
    """
    html = read_workbench_content()

    for handler in (
        'if (fieldName === "functional") {',
        'if (fieldName === "single_point_method") {',
        'if (fieldName === "scan_optimizer_method") {',
    ):
        assert handler in html, f"missing change handler: {handler}"
        span = html.split(handler, 1)[1].split("\n        if (fieldName ===", 1)[0]
        assert "applyMethodFamilyState(st, lvDef.fields" in span, (
            f"{handler} must run the family-state sweep"
        )

    assert html.count("applyMethodFamilyState(st, lvDef.fields") == 3


def test_catalogutils_family_gating_behavior_node() -> None:
    """T15 behavioral contract: evaluate the real CatalogUtils IIFE against the
    real catalog payload and check GFN hides/locks vs B97-3c restores.
    """
    if not shutil.which("node"):
        pytest.skip("node not available")

    from acp.catalog import get_method_catalog

    catalog = get_method_catalog()
    html = read_workbench_content()
    cu_src = _catalogutils_source(html)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".js", delete=False, encoding="utf-8"
    ) as f:
        _ = f.write(cu_src)
        cu_path = f.name
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        _ = f.write(json.dumps(catalog))
        cat_path = f.name

    script = textwrap.dedent(
        r"""
        const fs = require("fs");
        const cuSrc = fs.readFileSync(CU_PATH, "utf8");
        const catalog = JSON.parse(fs.readFileSync(CAT_PATH, "utf8"));
        const CatalogUtils = eval(
            "(" + cuSrc.replace(/^var\s+CatalogUtils\s*=\s*/, "").replace(/;\s*$/, "") + ")"
        );

        function eq(name, actual, expected) {
            const a = JSON.stringify(actual), e = JSON.stringify(expected);
            if (a !== e) {
                console.error("FAIL " + name + ": got " + a + " want " + e);
                process.exit(1);
            }
        }

        // (1) family-scoped solvent models (T12/T15): GFN -> ["none","ALPB"].
        eq("gfn solvent model options",
            CatalogUtils.getFieldOptions(catalog, "scan_optimizer_solvent_model", "orca", "GFN2-xTB"),
            ["none", "ALPB"]);
        eq("dft solvent model options unchanged",
            CatalogUtils.getFieldOptions(catalog, "scan_optimizer_solvent_model", "orca", "B97-3c"),
            ["none", "CPCM", "SMD"]);

        // (2) family-locked fields resolve to the empty offer (T11/T15).
        eq("gfn basis locked",
            CatalogUtils.getFieldOptions(catalog, "scan_optimizer_basis", "orca", "GFN2-xTB"), []);
        eq("gfn grid locked",
            CatalogUtils.getFieldOptions(catalog, "grid", "orca", "GFN2-xTB"), []);
        eq("gfn scf select locked",
            CatalogUtils.getFieldOptions(catalog, "scan_optimizer_scf_convergence", "orca", "GFN2-xTB"), []);
        eq("gfn scf scalar passes through",
            CatalogUtils.getFieldOptions(
                catalog, "scan_optimizer_scf_max_iterations", "orca", "GFN2-xTB"), null);
        eq("dft grid stays open",
            CatalogUtils.getFieldOptions(catalog, "grid", "orca", "B3LYP"),
            ["DefGrid1", "DefGrid2", "DefGrid3"]);
        eq("3c basis scoped to the family offer",
            CatalogUtils.getFieldOptions(catalog, "scan_optimizer_basis", "orca", "B97-3c"),
            ["mTZVP"]);

        // (3) gate booleans.
        eq("gfn basis locked?",
            CatalogUtils.isFieldFamilyLocked(catalog, "scan_optimizer_basis", "GFN2-xTB"), true);
        eq("gfn grid locked?", CatalogUtils.isFieldFamilyLocked(catalog, "scan_optimizer_grid", "GFN2-xTB"), true);
        eq("gfn solvent_model unlocked?",
            CatalogUtils.isFieldFamilyLocked(
                catalog, "scan_optimizer_solvent_model", "GFN2-xTB"), false);
        eq("gfn scf scalar unlocked?",
            CatalogUtils.isFieldFamilyLocked(
                catalog, "scan_optimizer_scf_max_iterations", "GFN2-xTB"), false);
        eq("3c basis unlocked?",
            CatalogUtils.isFieldFamilyLocked(catalog, "scan_optimizer_basis", "B97-3c"), false);
        eq("3c grid unlocked?",
            CatalogUtils.isFieldFamilyLocked(catalog, "scan_optimizer_grid", "B97-3c"), false);

        // (4) single_point grid default ''.
        eq("grid default", CatalogUtils.getFieldDefault(catalog, "grid", "orca", "B3LYP"), "");
        eq("gfn basis default",
            CatalogUtils.getFieldDefault(catalog, "scan_optimizer_basis", "orca", "GFN2-xTB"), "");
        eq("3c basis default from METHOD_META",
            CatalogUtils.getFieldDefault(
                catalog, "scan_optimizer_basis", "orca", "B97-3c"), "mTZVP");

        // (5) method-change sweep: GFN clears/locks inapplicable fields.
        const scanLevel = catalog.method_schemas.pes_scan.method_levels.find(
            (l) => l.level_id === "scan_optimizer"
        );
        const st = {
            scan_optimizer_method: "GFN2-xTB",
            scan_optimizer_basis: "def2-TZVPP",
            scan_optimizer_dispersion: "D4",
            scan_optimizer_grid: "DefGrid2",
            scan_optimizer_scf_convergence: "tight",
            scan_optimizer_solvent_model: "CPCM",
            scan_optimizer_scf_max_iterations: 200
        };
        CatalogUtils.applyMethodFamilyState(catalog, st, scanLevel.fields, "orca", "GFN2-xTB");
        eq("sweep clears basis", st.scan_optimizer_basis, "");
        eq("sweep clears dispersion to none", st.scan_optimizer_dispersion, "none");
        eq("sweep hides grid value", st.scan_optimizer_grid, "");
        eq("sweep hides scf value", st.scan_optimizer_scf_convergence, "");
        eq("sweep resets out-of-family solvent model", st.scan_optimizer_solvent_model, "none");
        eq("sweep keeps scalar caps", st.scan_optimizer_scf_max_iterations, 200);

        // (6) switching back to B97-3c restores visibility and keeps values
        // the family admits.
        eq("restored grid unlocked",
            CatalogUtils.isFieldFamilyLocked(catalog, "scan_optimizer_grid", "B97-3c"), false);
        eq("restored scf unlocked",
            CatalogUtils.isFieldFamilyLocked(
                catalog, "scan_optimizer_scf_convergence", "B97-3c"), false);
        const st2 = {
            scan_optimizer_basis: "mTZVP",
            scan_optimizer_grid: "DefGrid2",
            scan_optimizer_scf_convergence: "tight",
            scan_optimizer_solvent_model: "SMD"
        };
        CatalogUtils.applyMethodFamilyState(catalog, st2, scanLevel.fields, "orca", "B97-3c");
        eq("restore keeps basis", st2.scan_optimizer_basis, "mTZVP");
        eq("restore keeps grid", st2.scan_optimizer_grid, "DefGrid2");
        eq("restore keeps scf", st2.scan_optimizer_scf_convergence, "tight");
        eq("restore keeps solvent model", st2.scan_optimizer_solvent_model, "SMD");

        console.log("PASS");
        """
    )
    script = script.replace("CU_PATH", json.dumps(cu_path)).replace(
        "CAT_PATH", json.dumps(cat_path)
    )

    try:
        result = subprocess.run(
            ["node", "-e", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"CatalogUtils family-gating node test failed:\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
        assert "PASS" in result.stdout
    finally:
        Path(cu_path).unlink(missing_ok=True)
        Path(cat_path).unlink(missing_ok=True)


def test_copy_scan_level_reconciles_against_copied_method_family() -> None:
    """T16: copy-scan-level reconciles ALL destination fields against the
    COPIED method's family — never the pre-copy target state.

    The method travels with the copy (scan_optimizer_method → functional),
    so the family sweep must read the destination's POST-copy functional.
    """
    html = read_workbench_content()
    src = _catalogutils_source(html)

    # The copy procedure lives in CatalogUtils (single testable authority).
    assert "function copyScanLevelFields(catalog, srcState, dstState, fieldNames, engine)" in src
    assert "copyScanLevelFields: copyScanLevelFields" in src

    fn = src.split("function copyScanLevelFields(", 1)[1]

    # (T23g) The copy projection is DERIVED from the catalog level field
    # lists — the inline copyMap literal is retired. The method travels as
    # the destination's "functional"; the RI family is deliberately not
    # copied (T16 contract). Behavior equivalence with the historical
    # 7-pair map is locked by test_t23_derived_copy_projection_behavior_node.
    assert "var copyMap" not in fn, "inline copy map literal must stay retired"
    assert 'sk.indexOf("scan_optimizer_") !== 0' in fn
    assert 'base === "method" ? "functional" : base' in fn
    assert '"ri_approximation": true' in fn
    assert '"aux_j_basis": true' in fn and '"aux_c_basis": true' in fn
    assert "dstFieldSet" in fn

    # Reconciliation runs AFTER the copy loop and keys on the COPIED method
    # (the destination's post-copy functional) — not the pre-copy target.
    loop_at = fn.index("Object.keys(srcState).forEach")
    sweep_at = fn.index("applyMethodFamilyState(catalog, dstState,")
    assert loop_at < sweep_at, "family sweep must run after the copy loop"
    assert 'var copiedMethod = dstState.functional' in fn
    assert "applyMethodFamilyState(catalog, dstState, fieldNames, engine, copiedMethod)" in fn

    # The DOM handler delegates to the tested helper instead of an inline map.
    handler = html.split('copyBtn.addEventListener("click"', 1)[1].split(
        "hdr.appendChild(copyBtn)", 1
    )[0]
    assert "CatalogUtils.copyScanLevelFields(methodCatalogCache, _src, _dst, lvDef.fields" in handler
    assert "_fieldMap" not in handler, "inline copy map must live in CatalogUtils"

    # The family gate covers the RI/aux trio through the ri_support payload
    # signal (never a hardcoded method list).
    gate = src.split("function isFieldFamilyLocked(", 1)[1].split(
        "function applyMethodFamilyState(", 1
    )[0]
    assert 'base === "ri_approximation"' in gate
    assert '(riMeta.ri_support || "user") !== "user"' in gate

    # Locked RI clears to the "none" spelling (dispersion-like), aux to "".
    sweep = src.split("function applyMethodFamilyState(", 1)[1].split(
        "function copyScanLevelFields(", 1
    )[0]
    assert 'lockedBase === "ri_approximation"' in sweep


def test_copy_scan_level_serialized_payload_node() -> None:
    """T16 behavioral contract: copy a GFN scan level into the single_point
    level and assert the FINAL SERIALIZED payload (the captured
    buildPESProtocolFromMethod JSON), not just UI state.

    Copying a GFN scan level over a stale DFT single_point configuration
    must yield a GFN single-point level whose basis/dispersion/RI/aux/grid/
    SCF are cleared; a DFT copy must still transfer the valid shared fields.
    """
    if not shutil.which("node"):
        pytest.skip("node not available")

    from acp.catalog import get_method_catalog

    catalog = get_method_catalog()
    html = read_workbench_content()
    cu_src = _catalogutils_source(html)
    pes_src = _pes_protocol_builder_source(html)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".js", delete=False, encoding="utf-8"
    ) as f:
        _ = f.write(cu_src)
        cu_path = f.name
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".js", delete=False, encoding="utf-8"
    ) as f:
        _ = f.write(pes_src)
        pes_path = f.name
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        _ = f.write(json.dumps(catalog))
        cat_path = f.name

    script = textwrap.dedent(
        r"""
        const fs = require("fs");
        const cuSrc = fs.readFileSync(CU_PATH, "utf8");
        const pesSrc = fs.readFileSync(PES_PATH, "utf8");
        const catalog = JSON.parse(fs.readFileSync(CAT_PATH, "utf8"));
        const CatalogUtils = eval(
            "(" + cuSrc.replace(/^var\s+CatalogUtils\s*=\s*/, "").replace(/;\s*$/, "") + ")"
        );

        // Stubs consumed by buildPESProtocolFromMethod (real function source).
        var methodCatalogCache = catalog;
        var lookupCI = CatalogUtils.lookupCI;
        var wizardState = { method: { stages: {} } };
        const buildPESProtocolFromMethod = eval("(" + pesSrc + ")");

        function eq(name, actual, expected) {
          const a = JSON.stringify(actual), e = JSON.stringify(expected);
          if (a !== e) {
            console.error("FAIL " + name + ": got " + a + " want " + e);
            process.exit(1);
          }
        }
        function ok(name, cond, detail) {
          if (!cond) {
            console.error("FAIL " + name + ": " + detail);
            process.exit(1);
          }
        }

        const spFields = catalog.method_schemas.pes_scan.method_levels.find(
          (l) => l.level_id === "single_point"
        ).fields;

        function runCopy(srcLevel, dstLevel) {
          wizardState.method.stages = {
            scan_driver: {},
            scan_optimizer: srcLevel,
            single_point: dstLevel,
          };
          CatalogUtils.copyScanLevelFields(
            catalog,
            wizardState.method.stages.scan_optimizer,
            wizardState.method.stages.single_point,
            spFields,
            "orca"
          );
          return buildPESProtocolFromMethod(0, 1);
        }

        // A stale DFT single_point configuration the copy must reconcile.
        function staleDftTarget() {
          return {
            engine: "orca",
            functional: "B3LYP",
            basis: "def2-TZVP",
            dispersion: "D4",
            ri_approximation: "RIJCOSX",
            aux_j_basis: "def2/J",
            aux_c_basis: "AutoAux",
            solvent_model: "CPCM",
            solvent: "chloroform",
            grid: "DefGrid2",
            scf_convergence: "Tight",
            single_point_resume: true,
          };
        }

        // ── (A) GFN copy: stale DFT target must be reconciled away ──
        const gfnPayload = runCopy({
          scan_optimizer_method: "GFN2-xTB",
          scan_optimizer_basis: "",
          scan_optimizer_dispersion: "none",
          scan_optimizer_solvent_model: "ALPB",
          scan_optimizer_solvent: "water",
          scan_optimizer_grid: "",
          scan_optimizer_scf_convergence: "",
        }, staleDftTarget());
        const captured = JSON.stringify(gfnPayload);
        const sp = gfnPayload.single_point;
        eq("gfn copy: method is the copied one", sp.method, "GFN2-xTB");
        eq("gfn copy: basis cleared", sp.basis, null);
        ok("gfn copy: dispersion cleared",
           sp.dispersion === "none" || sp.dispersion === null, captured);
        ok("gfn copy: ri cleared",
           sp.ri_approximation === "none" || sp.ri_approximation === null, captured);
        eq("gfn copy: aux_j cleared", sp.aux_j_basis, null);
        eq("gfn copy: aux_c cleared", sp.aux_c_basis, null);
        eq("gfn copy: grid cleared", sp.grid, null);
        eq("gfn copy: scf_convergence cleared", sp.scf_convergence, null);
        // Valid shared fields still travel with the copy.
        eq("gfn copy: solvent model copied", sp.solvent_model, "ALPB");
        eq("gfn copy: solvent copied", sp.solvent, "water");
        eq("gfn copy: single_point enabled", gfnPayload.single_point.enabled, true);
        // No stale family-inapplicable value may reach the captured payload.
        for (const stale of ["def2-TZVP", "RIJCOSX", "def2/J", "AutoAux",
                             "DefGrid2", "CPCM", "chloroform", "D4"]) {
          ok("captured payload free of stale '" + stale + "'",
             captured.indexOf(stale) < 0, captured);
        }

        // ── (B) DFT copy: valid shared fields still copy ──
        const dftPayload = runCopy({
          scan_optimizer_method: "B3LYP",
          scan_optimizer_basis: "def2-TZVP",
          scan_optimizer_dispersion: "D4",
          scan_optimizer_solvent_model: "SMD",
          scan_optimizer_solvent: "toluene",
          scan_optimizer_grid: "DefGrid2",
          scan_optimizer_scf_convergence: "Tight",
        }, {
          engine: "orca",
          functional: "r2SCAN-3c",
          basis: "mTZVP",
          dispersion: "none",
          ri_approximation: "none",
          aux_j_basis: "",
          aux_c_basis: "",
          solvent_model: "none",
          solvent: "",
          grid: "",
          scf_convergence: "Tight",
          single_point_resume: true,
        });
        const sp2 = dftPayload.single_point;
        eq("dft copy: method copied", sp2.method, "B3LYP");
        eq("dft copy: basis copied", sp2.basis, "def2-TZVP");
        eq("dft copy: dispersion copied", sp2.dispersion, "D4");
        eq("dft copy: solvent model copied", sp2.solvent_model, "SMD");
        eq("dft copy: solvent copied", sp2.solvent, "toluene");
        eq("dft copy: grid copied", sp2.grid, "DefGrid2");
        eq("dft copy: scf_convergence copied", sp2.scf_convergence, "Tight");
        eq("dft copy: ri left at target spelling", sp2.ri_approximation, "none");

        console.log("PASS");
        """
    )
    script = (
        script.replace("CU_PATH", json.dumps(cu_path))
        .replace("PES_PATH", json.dumps(pes_path))
        .replace("CAT_PATH", json.dumps(cat_path))
    )

    try:
        result = subprocess.run(
            ["node", "-e", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"copy-scan-level serialized-payload node test failed:\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
        assert "PASS" in result.stdout
    finally:
        Path(cu_path).unlink(missing_ok=True)
        Path(pes_path).unlink(missing_ok=True)
        Path(cat_path).unlink(missing_ok=True)


def test_t23_serializer_family_lock_source_contract() -> None:
    """(a,g) buildPESProtocolFromMethod carries the T23a serialization
    defense (payload family gate, both levels) and no method literals."""
    html = read_workbench_content()
    src = _pes_protocol_builder_source(html)

    assert "serialization defense (T23a)" in src
    for gate in (
        'CatalogUtils.isFieldFamilyLocked(methodCatalogCache, "basis", scanOptimizerMethod)',
        'CatalogUtils.isFieldFamilyLocked(methodCatalogCache, "dispersion", scanOptimizerMethod)',
        "CatalogUtils.isFieldFamilyLocked("
        'methodCatalogCache, "ri_approximation", scanOptimizerMethod)',
        "scanOptimizerFamilyLocked",
        "singlePointFamilyLocked",
        "singlePointRiLocked",
    ):
        assert gate in src, f"missing serialization gate: {gate}"
    assert (
        'CatalogUtils.getFieldDefault(methodCatalogCache, "scan_optimizer_method"'
        in src
    ), "scan_optimizer method default must resolve from the catalog"
    assert '"GFN2-xTB"' not in src, "serializer must carry no method literal"


def test_t23_gfn_serialization_defense_node() -> None:
    """(a) leaked family-locked overrides never reach the serialized payload;
    user-RI DFT values still serialize; composite behavior is preserved."""
    script = (
        textwrap.dedent(_T23_NODE_PRELUDE)
        + textwrap.dedent(_T23_NODE_PES_STUBS)
        + textwrap.dedent(
            r"""
        // (A) stale GFN state (bypassing the T15/T16 UI sweep) must not leak.
        wizardState.method.stages = {
          scan_driver: {},
          scan_optimizer: {
            scan_optimizer_method: "GFN2-xTB",
            scan_optimizer_basis: "def2-TZVPP",
            scan_optimizer_dispersion: "D4",
            scan_optimizer_ri_approximation: "RIJCOSX",
            scan_optimizer_solvent_model: "ALPB",
            scan_optimizer_grid: "DefGrid2",
          },
          single_point: {
            engine: "orca",
            functional: "GFN2-xTB",
            basis: "def2-TZVP",
            dispersion: "D4",
            ri_approximation: "RIJCOSX",
            aux_j_basis: "def2/J",
            aux_c_basis: "AutoAux",
            solvent_model: "ALPB",
            solvent: "water",
            grid: "DefGrid2",
            single_point_resume: true,
          },
        };
        const gfn = buildPESProtocolFromMethod(0, 1);
        const captured = JSON.stringify(gfn);
        eq("gfn scan_optimizer basis null", gfn.scan_optimizer.basis, null);
        eq("gfn scan_optimizer dispersion null", gfn.scan_optimizer.dispersion, null);
        ok("gfn scan_optimizer ri cleared",
           gfn.scan_optimizer.ri_approximation === "none"
           || gfn.scan_optimizer.ri_approximation === null,
           captured);
        eq("gfn single_point basis null", gfn.single_point.basis, null);
        eq("gfn single_point dispersion null", gfn.single_point.dispersion, null);
        ok("gfn single_point ri cleared",
           gfn.single_point.ri_approximation === "none"
           || gfn.single_point.ri_approximation === null,
           captured);
        eq("gfn single_point aux_j null", gfn.single_point.aux_j_basis, null);
        eq("gfn single_point aux_c null", gfn.single_point.aux_c_basis, null);
        for (const stale of ["def2-TZVPP", "def2-TZVP", "RIJCOSX", "def2/J", "AutoAux", "D4"]) {
          ok("payload free of stale '" + stale + "'", captured.indexOf(stale) < 0, captured);
        }
        eq("gfn solvent model survives", gfn.single_point.solvent_model, "ALPB");
        eq("gfn solvent survives", gfn.single_point.solvent, "water");

        // (B) user-RI DFT values must still serialize (no over-masking).
        wizardState.method.stages = {
          scan_driver: {},
          scan_optimizer: {
            scan_optimizer_method: "B3LYP",
            scan_optimizer_basis: "def2-TZVP",
            scan_optimizer_dispersion: "D4",
            scan_optimizer_ri_approximation: "RIJCOSX",
            scan_optimizer_solvent_model: "SMD",
          },
          single_point: {
            engine: "orca",
            functional: "B3LYP",
            basis: "def2-TZVP",
            dispersion: "D4",
            ri_approximation: "RIJCOSX",
            aux_j_basis: "def2/J",
            single_point_resume: true,
          },
        };
        const dft = buildPESProtocolFromMethod(0, 1);
        eq("dft scan_optimizer basis kept", dft.scan_optimizer.basis, "def2-TZVP");
        eq("dft scan_optimizer dispersion kept", dft.scan_optimizer.dispersion, "D4");
        eq("dft scan_optimizer ri kept", dft.scan_optimizer.ri_approximation, "RIJCOSX");
        eq("dft single_point basis kept", dft.single_point.basis, "def2-TZVP");
        eq("dft single_point dispersion kept", dft.single_point.dispersion, "D4");
        eq("dft single_point ri kept", dft.single_point.ri_approximation, "RIJCOSX");
        eq("dft single_point aux_j kept", dft.single_point.aux_j_basis, "def2/J");

        // (C) composite behavior preserved (T11 basis nulling) + catalog default.
        wizardState.method.stages = {
          scan_driver: {},
          scan_optimizer: {
            scan_optimizer_method: "B97-3c",
            scan_optimizer_basis: "mTZVP",
            scan_optimizer_dispersion: "D3BJ",
          },
          single_point: {},
        };
        const c3 = buildPESProtocolFromMethod(0, 1);
        eq("composite scan_optimizer basis still null", c3.scan_optimizer.basis, null);
        eq("composite dispersion unchanged", c3.scan_optimizer.dispersion, "D3BJ");
        wizardState.method.stages = { scan_driver: {}, scan_optimizer: {}, single_point: {} };
        eq("scan_optimizer default method is catalog-driven",
           buildPESProtocolFromMethod(0, 1).scan_optimizer.method,
           catalog.field_definitions.scan_optimizer_method.default["*"]);

        console.log("PASS");
        """
        )
    )
    _t23_node(script, with_pes=True)


def test_t23_banner_source_contract() -> None:
    """(d) the ORCA-availability banner keys on the catalog family gate;
    the hardcoded _xtbMethods list stays retired."""
    html = read_workbench_content()

    assert "var _xtbMethods" not in html
    assert "_xtbMethods = [" not in html
    assert "catalog family gate (T23d)" in html
    banner = html.split("catalog family gate (T23d)", 1)[1].split(
        "if (_isDftMethod)", 1
    )[0]
    assert 'isFieldFamilyLocked("basis", _soMethod)' in banner
    assert "modal.orca_unavailable_dft_scan" in html


def test_t23_banner_family_gate_behavior_node() -> None:
    """(d) banner predicate from the payload: xTB-family suppresses the ORCA
    warning, DFT/composite methods keep it (GFN0 delta documented — the old
    hardcoded list wrongly warned for GFN0-xTB)."""
    script = textwrap.dedent(_T23_NODE_PRELUDE) + textwrap.dedent(
        r"""
        for (const m of ["GFN2-xTB", "GFN1-xTB", "GFN-FF", "GFN0-xTB"]) {
          eq("xTB-family suppresses ORCA banner: " + m,
             CatalogUtils.isFieldFamilyLocked(catalog, "basis", m), true);
        }
        for (const m of ["B3LYP", "B97-3c", "r2SCAN-3c", "DLPNO-CCSD(T)"]) {
          eq("DFT/composite keeps ORCA banner: " + m,
             CatalogUtils.isFieldFamilyLocked(catalog, "basis", m), false);
        }
        console.log("PASS");
        """
    )
    _t23_node(script)


def test_t23_hydration_grid_migration_hint_source_contract() -> None:
    """(f) draft hydration migrates legacy grid aliases and shows the hint;
    (g) the alias mirror is a marked historical-compat branch."""
    html = read_workbench_content()

    assert "legacy grid migration hint (T23f)" in html
    fn = html.split("function _restoreWizardDraft(draft) {", 1)[1].split(
        "\nasync function _revalidateDraftSources()", 1
    )[0]
    assert "CatalogUtils.migrateLegacyGridInStates(" in fn
    assert 't("draft.grid_migration_hint"' in fn
    assert (
        html.count('"draft.grid_migration_hint":') == 2
    ), "hint key must exist in zh-CN and en-US locales"

    cu = _catalogutils_source(html)
    assert "HISTORICAL-COMPAT (T23g)" in cu
    assert "HISTORICAL_COMPAT_GRID_ALIASES" in cu
    assert "migrateLegacyGridInStates: migrateLegacyGridInStates" in cu
    assert "migrateLegacyGridValue: migrateLegacyGridValue" in cu


def test_t23_legacy_grid_alias_parity_and_behavior_node() -> None:
    """(f,g) the frontend alias mirror is parity-locked to
    cccp.qc.keyword_registry (single authority) and hydration migration maps
    UltraFine→DefGrid3 with a per-field hint."""
    from cccp.qc.keyword_registry import _ENUM_TABLES, _LEGACY_ALIAS_KEYS

    expected = {
        alias: _ENUM_TABLES["grid"][alias][0] for alias in _LEGACY_ALIAS_KEYS["grid"]
    }
    assert expected == {
        "sg1": "DefGrid1",
        "fine": "DefGrid2",
        "ultrafine": "DefGrid3",
        "superfine": "DefGrid3",
    }, "registry legacy grid aliases changed — update the frontend mirror + this lock"

    script = textwrap.dedent(_T23_NODE_PRELUDE) + textwrap.dedent(
        r"""
        const expectedAliases = __EXPECTED_GRID_ALIASES__;
        const mirror = CatalogUtils.HISTORICAL_COMPAT_GRID_ALIASES;
        eq("alias mirror keys",
           Object.keys(mirror).sort().join(","), Object.keys(expectedAliases).sort().join(","));
        for (const k of Object.keys(expectedAliases)) {
          eq("alias mirror [" + k + "]", mirror[k], expectedAliases[k]);
        }
        eq("UltraFine -> DefGrid3", CatalogUtils.migrateLegacyGridValue(catalog, "UltraFine"),
           {from: "UltraFine", to: "DefGrid3"});
        eq("ULTRA FINE (registry-normalized) -> DefGrid3",
           CatalogUtils.migrateLegacyGridValue(catalog, "ULTRA FINE"),
           {from: "ULTRA FINE", to: "DefGrid3"});
        eq("SG1 -> DefGrid1", CatalogUtils.migrateLegacyGridValue(catalog, "SG1"),
           {from: "SG1", to: "DefGrid1"});
        eq("Fine -> DefGrid2", CatalogUtils.migrateLegacyGridValue(catalog, "Fine"),
           {from: "Fine", to: "DefGrid2"});
        eq("SuperFine -> DefGrid3", CatalogUtils.migrateLegacyGridValue(catalog, "SuperFine"),
           {from: "SuperFine", to: "DefGrid3"});
        eq("canonical value untouched",
           CatalogUtils.migrateLegacyGridValue(catalog, "DefGrid3"), null);
        eq("blank untouched", CatalogUtils.migrateLegacyGridValue(catalog, ""), null);
        eq("unknown value never invents a target",
           CatalogUtils.migrateLegacyGridValue(catalog, "Grid5"), null);

        const stages = {
          scan_optimizer: { scan_optimizer_method: "GFN2-xTB", scan_optimizer_grid: "UltraFine" },
          single_point: { grid: "DefGrid3", basis: "x" },
          batch: { grid: "SG1", scf_convergence: "Tight" },
        };
        const hints = CatalogUtils.migrateLegacyGridInStates(catalog, stages);
        eq("two migration hints", hints.length, 2);
        eq("scan_optimizer hint", hints[0],
           {level: "scan_optimizer", field: "scan_optimizer_grid",
            from: "UltraFine", to: "DefGrid3"});
        eq("batch hint", hints[1],
           {level: "batch", field: "grid", from: "SG1", to: "DefGrid1"});
        eq("state migrated in place", stages.scan_optimizer.scan_optimizer_grid, "DefGrid3");
        eq("batch state migrated", stages.batch.grid, "DefGrid1");
        eq("canonical state untouched", stages.single_point.grid, "DefGrid3");
        console.log("PASS");
        """
    ).replace("__EXPECTED_GRID_ALIASES__", json.dumps(expected))
    _t23_node(script)


def test_t23_derived_copy_projection_behavior_node() -> None:
    """(g) the catalog-derived copy projection equals the historical 7-pair
    map: shared fields travel as their base names, method→functional, and
    the RI family / non-shared fields never copy."""
    script = textwrap.dedent(_T23_NODE_PRELUDE) + textwrap.dedent(
        r"""
        const spFields = catalog.method_schemas.pes_scan.method_levels.find(
          (l) => l.level_id === "single_point").fields;
        const src = {
          scan_optimizer_method: "B3LYP",
          scan_optimizer_basis: "def2-TZVP",
          scan_optimizer_dispersion: "D4",
          scan_optimizer_solvent_model: "SMD",
          scan_optimizer_solvent: "toluene",
          scan_optimizer_grid: "DefGrid2",
          scan_optimizer_scf_convergence: "Tight",
          scan_optimizer_max_iterations: 321,
          scan_optimizer_convergence: "tight",
          scan_optimizer_scf_max_iterations: 150,
          scan_optimizer_ri_approximation: "RIJCOSX",
          scan_optimizer_retries: 7,
          scan_optimizer_retry_strategy: "previous_geometry",
        };
        const dst = {
          engine: "orca", functional: "r2SCAN-3c", basis: "mTZVP", dispersion: "none",
          ri_approximation: "none", aux_j_basis: "", aux_c_basis: "",
          solvent_model: "none", solvent: "", grid: "", scf_convergence: "Tight",
          single_point_resume: true,
        };
        CatalogUtils.copyScanLevelFields(catalog, src, dst, spFields, "orca");
        eq("method travels as functional", dst.functional, "B3LYP");
        eq("basis copied", dst.basis, "def2-TZVP");
        eq("dispersion copied", dst.dispersion, "D4");
        eq("solvent_model copied", dst.solvent_model, "SMD");
        eq("solvent copied", dst.solvent, "toluene");
        eq("grid copied", dst.grid, "DefGrid2");
        eq("scf_convergence copied", dst.scf_convergence, "Tight");
        ok("RI deliberately not copied", dst.ri_approximation !== "RIJCOSX", JSON.stringify(dst));
        eq("RI stays at target spelling", dst.ri_approximation, "none");
        eq("aux_j not copied", dst.aux_j_basis, "");
        eq("aux_c not copied", dst.aux_c_basis, "");
        ok("non-shared fields never appear",
           dst.max_iterations === undefined && dst.convergence === undefined
           && dst.scf_max_iterations === undefined && dst.retries === undefined
           && dst.retry_strategy === undefined && dst.scan_optimizer_method === undefined,
           JSON.stringify(dst));
        console.log("PASS");
        """
    )
    _t23_node(script)


def test_t23_hardcode_audit_sentinels() -> None:
    """(g) retired hardcode declarations stay retired and remaining method
    literals live only in marked historical-compat branches."""
    html = read_workbench_content()
    cu = _catalogutils_source(html)

    assert "var _scopedFieldBase" not in html
    assert "_xtbMethods = [" not in html
    assert "var copyMap" not in cu
    assert (
        '"basis", "single_point_basis", "scan_optimizer_basis", "aux_j_basis", "aux_c_basis"'
        not in html
    ), "hardcoded supports_custom allowlist must stay retired"
    assert (
        "fieldFd.supports_custom && !isOptionalOverride && !isBasisSingleLocked" in html
    )
    assert "var filterFieldName = fieldBaseName(fieldName);" in html

    lines = html.splitlines()
    for i, line in enumerate(lines):
        for literal in ('"GFN2-xTB"', '"B97-3c"'):
            if literal in line and "FIELD_DEFINITIONS" not in line:
                window = "\n".join(lines[max(0, i - 3) : i + 1])
                assert "HISTORICAL-COMPAT" in window or "T23g" in window, (
                    f"line {i + 1} carries {literal} outside a historical-compat "
                    f"branch: {line.strip()}"
                )


def test_tsmode_editor_js_exists_and_has_namespace() -> None:
    """tsmode_editor.js defines window.ACPTsmodeEditor with open/submit/close."""
    assert _TSMODE_JS.is_file(), "frontend/js/tsmode_editor.js missing"
    js = _TSMODE_JS.read_text(encoding="utf-8")
    assert "window.ACPTsmodeEditor" in js
    assert "open:" in js or "open =" in js
    assert "submit:" in js or "submit =" in js
    assert "close:" in js or "close =" in js


def test_tsmode_editor_css_exists() -> None:
    """tsmode_editor.css exists and contains tsme-root."""
    assert _TSMODE_CSS.is_file(), "frontend/css/tsmode_editor.css missing"
    css = _TSMODE_CSS.read_text(encoding="utf-8")
    assert ".tsme-root" in css


def test_v2_html_loads_tsmode_editor_modules() -> None:
    """ACP_Workbench_v2.html includes tsmode_editor.js + tsmode_editor.css."""
    html = read_workbench_content()
    assert '<script src="js/tsmode_editor.js"></script>' in html
    assert '<link rel="stylesheet" href="css/tsmode_editor.css">' in html
    assert html.index('<script src="js/structure_source_picker.js"></script>') < \
           html.index('<script src="js/tsmode_editor.js"></script>'), \
        "tsmode_editor.js must load after structure_source_picker.js"


def test_tsmode_editor_js_passes_node_check() -> None:
    """tsmode_editor.js has no syntax errors."""
    if not shutil.which("node"):
        pytest.skip("node not available")
    result = subprocess.run(
        ["node", "--check", str(_TSMODE_JS)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"node --check tsmode_editor.js failed:\n{result.stderr}"


def test_submit_tsmode_task_branch_exists() -> None:
    """submitJobModal() has a tsmode branch that opens ACPTsmodeEditor."""
    html = read_workbench_content()
    assert 'stageWf === "tsmode"' in html, (
        "submitJobModal must have a tsmode workflow branch"
    )
    assert "ACPTsmodeEditor.open" in html, (
        "tsmode branch must call ACPTsmodeEditor.open"
    )


def test_tsmode_i18n_keys_in_both_locales() -> None:
    """tsmode.* i18n keys exist in both zh-CN and en-US blocks."""
    html = read_workbench_content()
    tsmode_key_re = re.compile(r'"(tsmode\.[^"]+)":')

    zh_m = _ZH_BLOCK_RE.search(html)
    en_m = _EN_BLOCK_RE.search(html)
    assert zh_m, "zh-CN block not found"
    assert en_m, "en-US block not found"

    zh_keys = set(tsmode_key_re.findall(zh_m.group(1)))
    en_keys = set(tsmode_key_re.findall(en_m.group(1)))

    assert zh_keys, "No tsmode.* keys found in zh-CN"
    assert en_keys, "No tsmode.* keys found in en-US"

    only_zh = zh_keys - en_keys
    only_en = en_keys - zh_keys
    assert not only_zh, f"tsmode keys in zh-CN but missing from en-US: {sorted(only_zh)}"
    assert not only_en, f"tsmode keys in en-US but missing from zh-CN: {sorted(only_en)}"

    required = {
        "tsmode.modal_title",
        "tsmode.panel_source",
        "tsmode.panel_mode",
        "tsmode.panel_settings",
        "tsmode.select_job",
        "tsmode.hess_available",
        "tsmode.hess_missing",
        "tsmode.no_imaginary",
        "tsmode.set_target",
        "tsmode.target_confirmed",
        "tsmode.preview_differs",
        "tsmode.inherited_level",
        "tsmode.allow_unverified",
        "tsmode.allow_unverified_warn",
        "tsmode.submit",
        "tsmode.cancel",
        "tsmode.submit_failed",
        "tsmode.source_required",
        "tsmode.target_required",
    }
    assert required <= zh_keys, f"Missing zh-CN tsmode keys: {sorted(required - zh_keys)}"


def test_tsmode_editor_has_request_token_guard() -> None:
    """tsmode_editor.js uses requestToken for stale-response guarding."""
    js = _TSMODE_JS.read_text(encoding="utf-8")
    assert "requestToken" in js, "requestToken stale-response guard missing"
    assert "capturedToken" in js, "capturedToken pattern missing in tsmode_editor.js"


def test_tsmode_editor_has_i18n_helper() -> None:
    """tsmode_editor.js defines _t() i18n helper with STR-table fallback."""
    js = _TSMODE_JS.read_text(encoding="utf-8")
    assert "function _t(" in js, "_t i18n helper missing"
    assert "STR" in js, "STR fallback table missing"


def test_scan_selection_panel_dom_and_i18n_lock() -> None:
    """DOM + i18n lock for the PES-style scan selection panel (right pane).

    The scan workflow's atom picking mirrors PESsearch's 通用原子选择 panel:
    a #scan-selection-panel inside #tiw-right (after #pes-selection-panel,
    before #tiw-info-panel) shown at wizard step 2, with click-only kind
    buttons (距离/键角/二面角 + 清除), selection summary, task badge and
    status.  The old step-1 left panel (#scan-wizard-panel with #scan-wiz-*
    manual inputs) is deleted — range/points moved to the shared
    method-protocol dialog.  Every user-visible label/message exists in
    BOTH locale blocks.  Removing the panel, renaming an id, resurrecting a
    dead id, shipping an untranslated key, or sneaking the PES-only
    double-bond kind into this panel turns this red.
    """
    html = read_workbench_content()
    for dom_id in (
        "scan-selection-panel",
        "scan-clear-selection",
        "scan-selection-summary",
        "scan-selection-error",
        "scan-task-badge",
        "scan-selection-status",
        "scan-pick-hint",
    ):
        assert f'id="{dom_id}"' in html, dom_id
    # Exactly three click-only kind buttons (mirrors .pes-function-btn);
    # scan has NO double-bond kind — that PES-only mode must not leak in.
    kind_buttons = re.findall(r"<button[^>]*scan-function-btn[^>]*>", html)
    assert len(kind_buttons) == 3, f"expected 3 scan-function-btn buttons, got {len(kind_buttons)}"
    for kind in ("distance", "angle", "dihedral"):
        assert any(f'data-scan-kind="{kind}"' in btn for btn in kind_buttons), kind
    scan_section = html.split('id="scan-selection-panel"', 1)[1].split("</section>", 1)[0]
    assert 'data-selection-kind="double_bond_scan"' not in scan_section
    # Right-pane placement: inside #tiw-right, after the PES panel, before
    # the info panel (step 2 shows picking, not the old left column).
    assert html.index('id="tiw-right"') < html.index('id="scan-selection-panel"') < html.index(
        'id="tiw-info-panel"'
    )
    assert html.index('id="pes-selection-panel"') < html.index('id="scan-selection-panel"')
    # Old step-1 left panel and its manual inputs are gone for good.
    for dom_id in (
        "scan-wizard-panel",
        "scan-wiz-atom-1",
        "scan-wiz-atom-2",
        "scan-wiz-atom-3",
        "scan-wiz-atom-4",
        "scan-wiz-start",
        "scan-wiz-end",
        "scan-wiz-points",
        "scan-wiz-status",
        "scan-wiz-pick-hint",
    ):
        assert f'id="{dom_id}"' not in html, dom_id
    i18n_keys = [
        # New right-pane panel copy (both locales required).
        "scan.selection.title",
        "scan.selection.hint",
        "scan.function.distance",
        "scan.function.angle",
        "scan.function.dihedral",
        "scan.action.clear",
        "scan.task.title",
        "scan.task.incomplete",
        # Pick hints + validation errors still shared with the builder.
        "modal.scan_pick_hint_distance",
        "modal.scan_pick_hint_angle",
        "modal.scan_pick_hint_dihedral",
        "modal.scan_invalid",
        "modal.scan_err_fields_required",
        "modal.scan_err_atom_not_integer",
        "modal.scan_err_atom_not_positive",
        "modal.scan_err_atoms_not_distinct",
        "modal.scan_err_atom_out_of_range",
        "modal.scan_err_kind_invalid",
        "modal.scan_err_range_not_finite",
        "modal.scan_err_range_not_positive",
        "modal.scan_err_range_angle_invalid",
        "modal.scan_err_range_dihedral_invalid",
        "modal.scan_err_range_equal",
        "modal.scan_err_points_invalid",
        "modal.scan_err_no_confirmed_structure",
    ]
    for key in i18n_keys:
        assert html.count(f'"{key}":') == 2, f"{key} must exist in zh-CN and en-US"
    # Old-panel-only labels die with the panel (each verified twice today).
    removed_keys = [
        "modal.scan_coord",
        "modal.scan_atom_1",
        "modal.scan_atom_2",
        "modal.scan_atom_3",
        "modal.scan_atom_4",
        "modal.scan_atom_ph",
        "modal.scan_start",
        "modal.scan_end",
        "modal.scan_points",
        "modal.scan_hint",
        "modal.scan_kind_distance",
        "modal.scan_kind_angle",
        "modal.scan_kind_dihedral",
    ]
    for key in removed_keys:
        assert html.count(f'"{key}":') == 0, f"{key} must be gone with the old panel"


def test_scan_selection_submission_path_lock() -> None:
    """The wizard submit path uses the pure builder; conversion is once.

    ``submitJobModal`` must route every scan body through
    ``buildScanWizardBodyParts`` fed by the click-only field snapshot
    (validated per final submitted structure) and the 1-based -> 0-based
    conversion must live in exactly one place.
    """
    html = read_workbench_content()
    assert "function buildScanWizardBodyParts(fields, structureEntry)" in html
    assert "function scanWizardCountAtoms(xyzText)" in html
    # Exactly-once conversion: the single marked loop inside the builder is
    # the only parseInt(raw(name), 10) - 1 site in the whole page; the old
    # per-atom atomA - 1 / atomB - 1 sites are gone for good.
    builder = html.split("function buildScanWizardBodyParts", 1)[1].split(
        "function scanWizardErrorMessage", 1
    )[0]
    assert builder.count("scan-index-base-convert") == 1
    assert html.count("scan-index-base-convert") == 1
    assert builder.count("parseInt(raw(") == 1
    assert html.count("parseInt(raw(") == 1
    assert "atomA - 1" not in html
    assert "atomB - 1" not in html
    # submitJobModal consumes the builder result from the selection-state
    # snapshot; it never re-derives coordinates or points itself, and the
    # snapshot never reads the deleted #scan-wiz-* inputs.
    assert "scanParts = buildScanWizardBodyParts(scanSelectionFieldValues(), s);" in html
    assert "function scanSelectionFieldValues()" in html
    field_values = html.split("function scanSelectionFieldValues()", 1)[1].split(
        "\nfunction ", 1
    )[0]
    assert "scanSelectionState.selectedAtoms" in field_values
    assert "wizardState.method.stages.scan" in field_values
    assert 'getElementById("scan-wiz-' not in field_values
    assert "inputPayload.scan_coordinates = scanParts.input.scan_coordinates;" in html
    assert "methodPayload.scan_points = scanParts.method.scan_points;" in html
    assert (
        "methodPayload.levels.scan = Object.assign({}, methodPayload.levels.scan"
        " || {}, scanParts.mirror);" in html
    )
    # Scan bodies are bound to the final submitted structure's geometry:
    # the scan branch submits explicit xyz_text, never a smiles/asset guess.
    scan_branch = html.split("if (scanParts) {", 1)[1].split("} else if", 1)[0]
    assert (
        'inputPayload = { source_type: "xyz_text", '
        'source: String(s.xyz || s.xyz_text || "") };' in scan_branch
    )
    # Batch semantics: a failed per-structure validation routes through the
    # selection panel's error sink and aborts the submit with a clear
    # result instead of queueing an unverifiable job.
    assert "setScanSelectionError(" in html
    assert "jobFormValidationFailed(scanMsg);" in html
    validator = html.split("function jobFormValidationFailed", 1)[1].split(
        "\nasync function submitJobBatch", 1
    )[0]
    assert "ACPJobEditor.isCollecting()" in validator
    assert "throw new Error(message)" in validator
    assert "window.alert(message)" in validator


def test_scan_selection_hydration_and_switch_away_lock() -> None:
    """Step-2 visibility, hydrate/reset, kind switch, and switch-away wiring.

    The right-pane selection panel shows only at wizard step 2
    (``createWizardStep === 2`` plus the ``scan-selection-active`` marker),
    hydration flows through ``syncScanSelectionFromWizard``, pending-task
    hydrate starts from ``resetScanSelection``, and a kind switch mirrors
    the coordinate kind/start/end while never touching the click-picked
    atoms (the click handler stays their single writer).  The deleted left
    panel leaves no ``scan-wizard-panel`` block behind.
    """
    html = read_workbench_content()
    assert "function updateScanSelectionVisibility()" in html
    visibility = html.split("function updateScanSelectionVisibility()", 1)[1].split(
        "\nfunction ", 1
    )[0]
    assert "createWizardStep === 2" in visibility
    assert "scan-selection-active" in visibility
    # Hydrate/restore: the method-stage mirror flows back into the selection
    # state without resetting the ranges.
    assert "function syncScanSelectionFromWizard(" in html
    sync = html.split("function syncScanSelectionFromWizard(", 1)[1].split(
        "\nfunction ", 1
    )[0]
    assert "scan_coordinate_kind" in sync
    assert "scan_coordinate_atoms" in sync
    # Fresh context on pending-task/editor hydrate.
    assert "function resetScanSelection()" in html
    pending = html.split("function applyPendingNewTask()", 1)[1].split(
        "function configureStageSourcePanel", 1
    )[0]
    assert "resetScanSelection();" in pending
    # Kind switch (distance/angle/dihedral): mirror the kind, swap start/end
    # only while they still equal the previous kind's defaults, and leave
    # the picked atoms to the click handler (single writer).
    assert "function setScanSelectionKind(kind)" in html
    kind_switch = html.split("function setScanSelectionKind(kind)", 1)[1].split(
        "\nfunction ", 1
    )[0]
    assert "wizardState.method.stages.scan.scan_coordinate_kind = kind;" in kind_switch
    assert re.search(r"scan_coordinate_start\s*=(?!=)", kind_switch), (
        "setScanSelectionKind must write stages.scan.scan_coordinate_start"
    )
    assert re.search(r"scan_coordinate_end\s*=(?!=)", kind_switch), (
        "setScanSelectionKind must write stages.scan.scan_coordinate_end"
    )
    assert "scanWizardKindDefaults" in kind_switch
    assert "scan_coordinate_atoms" not in kind_switch
    assert "function handleScanPreviewAtomClick(atom)" in html
    handler = html.split("function handleScanPreviewAtomClick(atom)", 1)[1].split(
        "\nfunction ", 1
    )[0]
    assert re.search(r"scan_coordinate_atoms\s*=(?!=)", handler), (
        "handleScanPreviewAtomClick must be the single writer of scan_coordinate_atoms"
    )
    # Switch-away: updateInputModeVisibility no longer carries the old
    # left-panel block, and its dead helpers vanished page-wide.
    mode_visibility = html.split("function updateInputModeVisibility()", 1)[1].split(
        "function updateTaskNamePlaceholder", 1
    )[0]
    assert "scan-wizard-panel" not in mode_visibility
    for dead in (
        "scanWizardEnsureListeners",
        "scanWizardUserDirty",
        "scanSelectionRebuildFromFields",
    ):
        assert dead not in html, dead


def test_scan_atom_pick_dispatch_wiring() -> None:
    """WS3: click-to-pick binds through the PES preview dispatcher only.

    The scan picker hangs off attachPESPreviewSelection's single dispatch
    point (PES behavior stays byte-identical), its marker renderer mirrors
    the PES renderer's shape recipe, every preview re-render drops stale
    scan shapes without rebuilding from deleted fields, and the visibility
    pass drives the step-2 right-pane panel's picking state instead of
    blanket-disabling the model.
    """
    html = read_workbench_content()
    assert "let scanSelectionState = {" in html
    for name in (
        "canPickScanAtoms",
        "handleScanPreviewAtomClick",
        "renderScanPreviewSelection",
        "clearScanPreviewSelectionShapes",
    ):
        assert f"function {name}(" in html, name
    attach = html.split("function attachPESPreviewSelection()", 1)[1].split(
        "function canPickPESAtoms", 1
    )[0]
    assert "var canPick = canPickPESAtoms() || canPickScanAtoms();" in attach
    assert (
        "canPickPESAtoms() ? handlePESPreviewAtomClick(atom) : handleScanPreviewAtomClick(atom);"
        in attach
    )
    assert "if (canPickScanAtoms()) renderScanPreviewSelection();" in attach
    assert "renderPESPreviewSelection();" in attach
    assert "function canPickScanAtoms()" in html
    can_pick = html.split("function canPickScanAtoms()", 1)[1].split("\nfunction ", 1)[0]
    assert "createWizardStep === 2" in can_pick, "scan picking is a step-2 right-pane capability"
    assert "createWizardStep === 1" not in can_pick
    preview = html.split("function renderPreviewStructure3D(structure)", 1)[1].split(
        "function isPESsearchTask", 1
    )[0]
    assert "clearPESPreviewSelectionShapes();" in preview
    assert "clearScanPreviewSelectionShapes();" in preview
    # Re-render must NOT rebuild the selection from the deleted atom fields
    # — the click selection itself is the authority and must survive a
    # preview re-render instead of being wiped by a fields-based rebuild.
    assert "scanSelectionRebuildFromFields" not in preview
    assert "scheduleViewerFraming(" in preview
    assert ".zoomTo();" not in preview
    handler = html.split("function handleScanPreviewAtomClick(atom)", 1)[1].split(
        "\nfunction ", 1
    )[0]
    assert "renderScanPreviewSelection();" in handler
    assert "updateScanSelectionSummary" in handler
    assert "dispatchEvent" not in handler, "click-only picking never fakes input events"
    visibility = html.split("function updatePESSelectionVisibility()", 1)[1].split(
        "function setPESSelectionMode", 1
    )[0]
    assert "updateScanSelectionVisibility()" in visibility
    assert "function updateScanSelectionVisibility()" in html
    scan_vis = html.split("function updateScanSelectionVisibility()", 1)[1].split(
        "\nfunction ", 1
    )[0]
    scan_gate_lines = [
        ln
        for ln in (visibility + "\n" + scan_vis).splitlines()
        if 'toggle("picking-disabled"' in ln and "canPickPESAtoms" not in ln
    ]
    assert scan_gate_lines, "scan panel must toggle .picking-disabled from its pick gate"


# ---------------------------------------------------------------------------
# PES manual review — candidate card mode vs editability (2026-09 regression)
# ---------------------------------------------------------------------------


def _candidate_card_source(html: str) -> str:
    """Source of energyCandidateCardMarkup() up to the next top-level function."""
    return html.split("function energyCandidateCardMarkup(", 1)[1].split("\nfunction ", 1)[0]


# ---------------------------------------------------------------------------
# PES DFT-scan extension — frontend contract tests (2026-09)
# ---------------------------------------------------------------------------


def _get_pes_scan_schema():
    """Return the pes_scan METHOD_SCHEMA, dynamically from the catalog."""
    schema = METHOD_SCHEMAS.get("pes_scan")
    assert schema is not None, "pes_scan schema missing from METHOD_SCHEMAS"
    return schema


# ---------------------------------------------------------------------------
# T15 — method-family-driven field gating in the wizard + single_point grid
# ---------------------------------------------------------------------------


def _catalogutils_source(html: str) -> str:
    """Extract the standalone CatalogUtils IIFE from the workbench HTML."""
    start = html.index("var CatalogUtils = (function() {")
    end = html.index("})();", start) + len("})();")
    return html[start:end]


# ---------------------------------------------------------------------------
# T16 — copy-scan-level must not propagate family-inapplicable fields
# ---------------------------------------------------------------------------


def _pes_protocol_builder_source(html: str) -> str:
    """Extract buildPESProtocolFromMethod(charge, multiplicity) from the HTML."""
    start = html.index("function buildPESProtocolFromMethod(charge, multiplicity) {")
    end = html.index("\nasync function submitPESsearchTask()", start)
    return html[start:end]


# ---------------------------------------------------------------------------
# T23 — serialization defense, data-driven banner, hydration hint, hardcode audit
# ---------------------------------------------------------------------------


_T23_NODE_PRELUDE = r"""
const fs = require("fs");
const cuSrc = fs.readFileSync(CU_PATH, "utf8");
const catalog = JSON.parse(fs.readFileSync(CAT_PATH, "utf8"));
const CatalogUtils = eval(
  "(" + cuSrc.replace(/^var\s+CatalogUtils\s*=\s*/, "").replace(/;\s*$/, "") + ")"
);
function eq(name, actual, expected) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a !== e) { console.error("FAIL " + name + ": got " + a + " want " + e); process.exit(1); }
}
function ok(name, cond, detail) {
  if (!cond) { console.error("FAIL " + name + ": " + detail); process.exit(1); }
}
"""


_T23_NODE_PES_STUBS = r"""
const pesSrc = fs.readFileSync(PES_PATH, "utf8");
var methodCatalogCache = catalog;
var lookupCI = CatalogUtils.lookupCI;
var wizardState = { method: { stages: {} } };
const buildPESProtocolFromMethod = eval("(" + pesSrc + ")");
"""


def _t23_node(script: str, *, with_pes: bool = False) -> None:
    """Run a node contract script against the real CatalogUtils IIFE + catalog.

    CU_PATH / CAT_PATH (and PES_PATH when ``with_pes``) tokens in the script
    are substituted with temp-file paths; the script must end in a PASS
    sentinel (same harness pattern as the T15/T16 node contracts).
    """
    if not shutil.which("node"):
        pytest.skip("node not available")

    from acp.catalog import get_method_catalog

    catalog = get_method_catalog()
    html = read_workbench_content()

    paths: dict[str, str] = {}
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".js", delete=False, encoding="utf-8"
    ) as f:
        f.write(_catalogutils_source(html))
        paths["CU_PATH"] = f.name
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        f.write(json.dumps(catalog))
        paths["CAT_PATH"] = f.name
    if with_pes:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".js", delete=False, encoding="utf-8"
        ) as f:
            f.write(_pes_protocol_builder_source(html))
            paths["PES_PATH"] = f.name

    for token, path in paths.items():
        script = script.replace(token, json.dumps(path))
    try:
        result = subprocess.run(
            ["node", "-e", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"T23 node contract failed:\nstdout={result.stdout}\nstderr={result.stderr}"
        )
        assert "PASS" in result.stdout
    finally:
        for path in paths.values():
            Path(path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# TS Mode Editor — module integration + vibration viewer quick-create
# ---------------------------------------------------------------------------

_TSMODE_JS = FRONTEND_JS_DIR / "tsmode_editor.js"


_TSMODE_CSS = FRONTEND_CSS_DIR / "tsmode_editor.css"
