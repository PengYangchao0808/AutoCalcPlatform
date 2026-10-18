"""Frontend sync page tests — viewer_framing area.

Split from tests/test_frontend_sync.py (T29, 2026-10-18). Every test keeps
its original name and body; the old -> new mapping is
tests/test_frontend_sync.py::<name> -> tests/test_frontend_sync_viewer_framing.py
::<name>. Shared helpers live in tests/_frontend_sync_shared.py.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests._frontend_sync_shared import (
    FRONTEND_JS_DIR,
    FRONTEND_WORKBENCH_JS_DIR,
    read_workbench_content,
)


def test_viewer_framing_shared_helper_call_order() -> None:
    html = (FRONTEND_JS_DIR / "visualization" / "framing.js").read_text(encoding="utf-8")
    page = read_workbench_content()
    assert "ACPVisualization.framing." in page

    marker = "function frame3DViewer("
    assert html.count(marker) == 1, "Shared framing helper must be defined once"
    body = html.split(marker, 1)[1].split("\nfunction ", 1)[0]
    for token in (".resize()", ".center(", ".zoomTo(", ".render()"):
        assert token in body, f"Shared framing helper missing {token}"
    assert (
        body.find(".resize()")
        < body.find(".center(")
        < body.find(".zoomTo(")
        < body.find(".render()")
    ), "Shared framing helper call order changed"
    assert "preserveView" in body, "Shared framing helper must preserve user view"


def test_viewer_framing_container_stability_wait() -> None:
    html = (FRONTEND_JS_DIR / "visualization" / "framing.js").read_text(encoding="utf-8")
    page = read_workbench_content()
    assert "ACPVisualization.framing." in page

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
    html = read_workbench_content()
    # T18: the preview 3D rebuild moved to the S2 slice module, so its load site
    # is asserted against that file (the wizard.js name is a thin delegate).
    s2_module = (FRONTEND_WORKBENCH_JS_DIR / "features" / "wizard" / "preview-validate.js").read_text(encoding="utf-8")
    load_sites = (
        ("renderMolDoc", html),
        ("energyGraphLoadFrameGeometry", html),
        ("renderPreviewStructure3D", s2_module),
        ("renderReactionChanges3D", html),
    )

    for name, source in load_sites:
        marker = f"function {name}("
        assert source.count(marker) == 1, f"{name} must be defined once"
        body = source.split(marker, 1)[1]
        body = body.split("\nasync function ", 1)[0].split("\nfunction ", 1)[0]
        assert "scheduleViewerFraming(" in body, f"{name} bypasses shared framing"
        assert ".zoomTo();" not in body, f"{name} contains a bare zoomTo call"


def test_viewer_framing_disposed_in_cleanup_paths() -> None:
    html = read_workbench_content()
    cleanup_paths = (
        "energyGraphDestroyViewer",
        "clearViewer",
        "s2scanResetState",
    )

    for name in cleanup_paths:
        marker = f"function {name}("
        assert html.count(marker) == 1, f"{name} must be defined once"
        body = html.split(marker, 1)[1].split("\nfunction ", 1)[0]
        assert "disposeViewerFraming(" in body, f"{name} omits framing cleanup"


def test_dollar3dmol_single_station_pin() -> None:
    """T15 single-station pin (FE-09): the vendored $3Dmol library is
    executable-mediated by exactly one module — js/visualization/mol3d_adapter.js.
    Every other occurrence must be a comment on one of the two known
    GAP-2-a/b renderers (energy_chart_renderer.js, candidate_review_controller.js)."""
    adapter = Path("visualization") / "mol3d_adapter.js"
    allowed_comment_only = {
        Path("visualization") / "energy_chart_renderer.js",
        Path("workbench") / "controllers" / "candidate_review_controller.js",
    }
    seen_files: set[Path] = set()
    violations: list[str] = []
    for js in FRONTEND_JS_DIR.rglob("*.js"):
        if "vendor" in js.parts or "module_tests" in js.parts:
            continue
        rel = js.relative_to(FRONTEND_JS_DIR)
        for lineno, line in enumerate(js.read_text(encoding="utf-8").splitlines(), 1):
            if "$3Dmol" not in line:
                continue
            seen_files.add(rel)
            if rel == adapter:
                continue
            is_comment = line.lstrip().startswith(("//", "*", "/*"))
            if rel not in allowed_comment_only or not is_comment:
                violations.append(f"{rel}:{lineno}")
    assert seen_files == {adapter} | allowed_comment_only, (
        f"unexpected $3Dmol station set: {sorted(str(p) for p in seen_files)}"
    )
    assert adapter in seen_files, "mol3d_adapter.js must remain the executable $3Dmol station"
    assert violations == [], f"$3Dmol executable/unknown references: {violations}"


def test_add_model_result_restricted_methods_pin() -> None:
    """T15: a value captured from addModel() is an opaque scene-instance token.
    Call sites may only use the token whitelist (setStyle/addStyle/setClickable/
    setHoverable/selectedAtoms); vendor-native-only GLModel methods must never be
    called on the captured result. Guards the T8-T14 migrations."""
    token_methods = {"setStyle", "addStyle", "setClickable", "setHoverable", "selectedAtoms"}
    assign = re.compile(r"(?:var|let|const)?\s*([A-Za-z_$][\w$]*)\s*=\s*[\w$.]*\.addModel\(")
    violations: list[str] = []
    for js in FRONTEND_JS_DIR.rglob("*.js"):
        if "vendor" in js.parts or "module_tests" in js.parts:
            continue
        src = js.read_text(encoding="utf-8")
        for name in sorted({m.group(1) for m in assign.finditer(src)}):
            for m in re.finditer(rf"\b{re.escape(name)}\.([A-Za-z_$][\w$]*)\(", src):
                if m.group(1) not in token_methods:
                    violations.append(f"{js.relative_to(FRONTEND_JS_DIR)}: {name}.{m.group(1)}()")
    assert violations == [], (
        "addModel() results are restricted tokens; native-only calls: " + "; ".join(violations)
    )


def test_scan_s2_dead_viewer_fields_pin() -> None:
    """T25 closure of the T15 pin: the dead s2scanState.viewer /
    s2scanState.resultViewer fields (F-⑥-1, declared and nulled but never
    created and never consumed) were removed together with their single
    guarded clear. The fields must not be reintroduced without a creation
    site, a real consumer, and a deliberate update of this pin."""
    src = (FRONTEND_WORKBENCH_JS_DIR / "features" / "scan-s2.js").read_text(encoding="utf-8")
    assert "resultViewer" not in src, "removed dead field resultViewer must not reappear"
    assert "viewer: null" not in src, "removed dead field viewer must not reappear"
    assert "s2scanState.viewer" not in src, "no s2scanState.viewer read/write may reappear"
    assert "s2scanState.viewer.clear()" not in src, "the dead guarded clear must stay deleted"
    for name in ("s2scanDist", "s2scanAngle", "s2scanDihedral", "s2scanResetState"):
        assert f"function {name}(" in src, f"{name} must stay defined in scan-s2.js"


def test_no_standalone_s2scan_modal() -> None:
    """FE-04 slice 1: the legacy S2 static modal must not exist in the HTML.

    S2 result review now lives on the energy viewer; selection flows through
    the active pes/scan selection panels. The modal DOM (and its dedicated
    listeners/styles) were removed — this guards against reintroduction.
    """
    html = read_workbench_content()
    assert 'id="s2scan-modal"' not in html, (
        "legacy s2scan-modal must not be reintroduced into the workbench HTML"
    )


def test_s2scan_geometry_helpers_live_anchors() -> None:
    """FE-04 slice 1: shared geometry helpers remain single-definition and
    stay wired to their live caller in wizard.js.

    s2scanDist/s2scanAngle/s2scanDihedral are the active coordinate math used
    by the pes/scan selection flow; each must be defined exactly once in
    scan-s2.js and wizard.js must keep calling s2scanDist.
    """
    scan_s2 = (FRONTEND_WORKBENCH_JS_DIR / "features" / "scan-s2.js").read_text(encoding="utf-8")
    for name in ("s2scanDist", "s2scanAngle", "s2scanDihedral"):
        n = scan_s2.count(f"function {name}(")
        assert n == 1, f"{name} must be defined exactly once in scan-s2.js, found {n}"
    wizard = (FRONTEND_WORKBENCH_JS_DIR / "features" / "wizard.js").read_text(encoding="utf-8")
    assert "s2scanDist(" in wizard, "wizard.js must keep its live s2scanDist call site"


def test_no_retired_mechanism_write_interactions() -> None:
    """FE-05: retired mechanism write interactions must not be reintroduced.

    Mechanism write endpoints answer 410 Gone (backend route tests pin the
    interface layer); the frontend must not re-add the dead write path — no
    accept/lock button, no manual-mapping button, no allow-zero escape hatch,
    no confirm-handler stub, and no draft-study POST client. Read-only
    mechanism rendering stays allowed.
    """
    html = read_workbench_content()
    for gone in (
        "mech-accept-lock",
        "mech-manual-mapping",
        "mech-allow-zero-changes",
        "_retiredConfirmReaction",
        "ensureMechanismDraftStudy",
    ):
        assert gone not in html, (
            f"retired mechanism write interaction must not be reintroduced: {gone}"
        )
