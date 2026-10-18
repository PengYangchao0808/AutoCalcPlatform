"""Frontend sync page tests — structure_editor area.

Split from tests/test_frontend_sync.py (T29, 2026-10-18). Every test keeps
its original name and body; the old -> new mapping is
tests/test_frontend_sync.py::<name> -> tests/test_frontend_sync_structure_editor.py
::<name>. Shared helpers live in tests/_frontend_sync_shared.py.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import textwrap

import pytest

from tests._frontend_sync_shared import (
    _EDITOR_JS_PATH,
    _SV_JS_PATH,
    _VIB_JS,
    FRONTEND_CSS_DIR,
    FRONTEND_JS_DIR,
    read_workbench_content,
)


def test_structure_editor_js_has_namespace() -> None:
    js = (FRONTEND_JS_DIR / "structure_editor.js").read_text(encoding="utf-8")
    assert "window.ACPStructureEditor" in js


# ---------------------------------------------------------------------------
# Wave 6 / todo 32: adjacency graph + provenance + fragments
# ---------------------------------------------------------------------------


def test_structure_editor_graph_contract() -> None:
    """Contract: covalent-radii table, tolerance constant, pure graph API,
    provenance strings, locked-flag guard, no-structure-mutation markers."""
    js = _EDITOR_JS_PATH.read_text(encoding="utf-8")

    assert "COVALENT_RADII" in js
    for pair in ("H: 0.31", "C: 0.76", "O: 0.66", "Cl: 1.02", "Cu: 1.32"):
        assert pair in js, f"covalent radius missing: {pair}"
    assert "DEFAULT_RADIUS = 1.0" in js
    assert "BOND_TOLERANCE = 1.3" in js

    for name in (
        "buildAdjacency",
        "connectedComponents",
        "fragmentsAfterCut",
        "parseMolBonds",
        "buildGraphFromCurrentEntry",
        "editorState",
    ):
        assert name in js, f"{name} missing from structure_editor.js"

    # Edge source markers + provenance strings (zh STR fallbacks)
    assert '"explicit"' in js and '"inferred"' in js
    assert "\u6587\u4ef6\u663e\u5f0f\u952e" in js  # 文件显式键
    assert "\u5171\u4ef7\u534a\u5f84\u63a8\u65ad" in js  # 共价半径推断

    # Locked guard + read-only contract
    assert "if (locked) return null" in js
    assert "displayedCoords" in js and "displayedSymbols" in js


def test_structure_editor_node_adjacency_logic() -> None:
    """Node logic: explicit-vs-inferred edges, tolerance boundary (<=),
    unknown-element default radius, fragments preserved, coords unmutated,
    connectedComponents + fragmentsAfterCut, parseMolBonds, locked guard."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    script = textwrap.dedent("""\
        var window = { fetch: null };
        require(EDITOR_PATH);
        var ed = window.ACPStructureEditor;

        // (a) explicit bonds win exclusively (H2O geometry, but only one
        // explicit edge is used even though O-H distances would bond)
        var symbols = ["O", "H", "H"];
        var coords = [[0, 0, 0], [0.96, 0, 0], [-0.4, 0.87, 0]];
        var explicit = ed.buildAdjacency(symbols, coords, [{ a: 0, b: 1, order: 2 }]);
        if (explicit.edges.length !== 1 ||
            explicit.edges[0].a !== 0 || explicit.edges[0].b !== 1 ||
            explicit.edges[0].order !== 2 || explicit.edges[0].source !== "explicit") {
            console.error("FAIL: explicit edges " + JSON.stringify(explicit.edges));
            process.exit(1);
        }
        if (explicit.provenance !== "文件显式键") {
            console.error("FAIL: explicit provenance " + explicit.provenance);
            process.exit(1);
        }

        // inferred water: O-H 0.96 <= 1.3*(0.66+0.31)=1.261 bonded x2;
        // H-H ~1.61 > 1.3*(0.31+0.31)=0.806 NOT bonded
        var before = JSON.stringify(coords);
        var inferred = ed.buildAdjacency(symbols, coords, null);
        if (inferred.edges.length !== 2) {
            console.error("FAIL: expected 2 inferred O-H edges, got " +
                JSON.stringify(inferred.edges));
            process.exit(1);
        }
        for (var i = 0; i < inferred.edges.length; i++) {
            if (inferred.edges[i].source !== "inferred" ||
                inferred.edges[i].order !== 1) {
                console.error("FAIL: inferred edge fields");
                process.exit(1);
            }
        }
        if (inferred.provenance !== "共价半径推断 (tolerance 1.3)") {
            console.error("FAIL: inferred provenance " + inferred.provenance);
            process.exit(1);
        }
        if (JSON.stringify(coords) !== before) {
            console.error("FAIL: coords were mutated");
            process.exit(1);
        }

        // (b) tolerance boundary: dist == 1.3*(r_i+r_j) bonds; above does not
        var d = 1.3 * (0.76 + 0.76);
        var atBoundary = ed.buildAdjacency(
            ["C", "C"], [[0, 0, 0], [d, 0, 0]], null);
        if (atBoundary.edges.length !== 1) {
            console.error("FAIL: == boundary must bond");
            process.exit(1);
        }
        var above = ed.buildAdjacency(["C", "C"], [[0, 0, 0], [2.0, 0, 0]], null);
        if (above.edges.length !== 0) {
            console.error("FAIL: above cutoff must not bond");
            process.exit(1);
        }
        // unknown element -> DEFAULT_RADIUS 1.0 (cutoff 2.6)
        var unk = ed.buildAdjacency(["Xx", "Xx"], [[0, 0, 0], [2.0, 0, 0]], null);
        if (unk.edges.length !== 1) {
            console.error("FAIL: unknown element default radius");
            process.exit(1);
        }
        var unkFar = ed.buildAdjacency(["Xx", "Xx"], [[0, 0, 0], [2.7, 0, 0]], null);
        if (unkFar.edges.length !== 0) {
            console.error("FAIL: unknown element above default cutoff");
            process.exit(1);
        }

        // (c) disconnected fragments preserved — no auto-bonding
        var iso = ed.buildAdjacency(
            ["H", "H"], [[0, 0, 0], [8.0, 0, 0]], null);
        if (iso.edges.length !== 0) {
            console.error("FAIL: isolated atoms must stay unbonded");
            process.exit(1);
        }
        var comps = ed.connectedComponents(iso.edges, 2);
        if (JSON.stringify(comps) !== JSON.stringify([[0], [1]])) {
            console.error("FAIL: fragments " + JSON.stringify(comps));
            process.exit(1);
        }

        // (e) linear 3-atom chain: A-B-C
        var chain = [{ a: 0, b: 1 }, { a: 1, b: 2 }];
        var whole = ed.connectedComponents(chain, 3);
        if (JSON.stringify(whole) !== JSON.stringify([[0, 1, 2]])) {
            console.error("FAIL: chain components " + JSON.stringify(whole));
            process.exit(1);
        }
        var cut01 = ed.fragmentsAfterCut(chain, 3, 0, 1);
        if (JSON.stringify(cut01) !== JSON.stringify([[0], [1, 2]])) {
            console.error("FAIL: cut 0-1 -> " + JSON.stringify(cut01));
            process.exit(1);
        }
        var cut12 = ed.fragmentsAfterCut(chain, 3, 2, 1);
        if (JSON.stringify(cut12) !== JSON.stringify([[0, 1], [2]])) {
            console.error("FAIL: cut 1-2 (reversed args) -> " + JSON.stringify(cut12));
            process.exit(1);
        }
        if (JSON.stringify(chain) !== JSON.stringify([{ a: 0, b: 1 }, { a: 1, b: 2 }])) {
            console.error("FAIL: fragmentsAfterCut mutated edges");
            process.exit(1);
        }

        // (f) parseMolBonds minimal V2000 (2 atoms, 1 single bond)
        var mol = [
            "minimal",
            "  ACP",
            "",
            "  2  1  0  0  0  0  0  0  0  0999 V2000",
            "    0.0000    0.0000    0.0000 O   0  0  0  0  0  0  0  0  0  0  0  0",
            "    0.9600    0.0000    0.0000 H   0  0  0  0  0  0  0  0  0  0  0  0",
            "  1  2  1  0  0  0  0"
        ].join("\\n");
        var parsed = ed.parseMolBonds(mol);
        if (JSON.stringify(parsed) !== JSON.stringify([{ a: 0, b: 1, order: 1 }])) {
            console.error("FAIL: parseMolBonds " + JSON.stringify(parsed));
            process.exit(1);
        }
        if (ed.parseMolBonds("") !== null && ed.parseMolBonds("").length !== 0) {
            console.error("FAIL: parseMolBonds empty input");
            process.exit(1);
        }

        // (g) locked -> buildGraphFromCurrentEntry refuses
        window.ACPStructureViewer = {
            state: {
                displayedCoords: coords,
                displayedSymbols: symbols,
                displayedEntryId: "e1"
            }
        };
        ed.setLocked(true);
        if (ed.buildGraphFromCurrentEntry() !== null) {
            console.error("FAIL: locked build must return null");
            process.exit(1);
        }
        ed.setLocked(false);
        var built = ed.buildGraphFromCurrentEntry();
        if (!built || built.edges.length !== 2 ||
            built.provenance !== "共价半径推断 (tolerance 1.3)") {
            console.error("FAIL: buildGraphFromCurrentEntry " + JSON.stringify(built));
            process.exit(1);
        }
        if (ed.editorState.graph !== built || ed.editorState.provenance !== built.provenance) {
            console.error("FAIL: editorState cache not populated");
            process.exit(1);
        }
        ed.setLocked(true);
        if (ed.buildGraphFromCurrentEntry() !== null || !ed.isLocked()) {
            console.error("FAIL: lock state");
            process.exit(1);
        }
        console.log("PASS");
    """).replace("EDITOR_PATH", json.dumps(str(_EDITOR_JS_PATH)))

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"Node adjacency test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


# ---------------------------------------------------------------------------
# Wave 6 / todo 33: bond-length edit + move-side toggle
# ---------------------------------------------------------------------------


def test_structure_editor_bond_edit_contract() -> None:
    """Contract: edit API names, range constants, rejection reasons,
    move-side toggle labels (todo 36 UI consumes the constants)."""
    js = _EDITOR_JS_PATH.read_text(encoding="utf-8")

    for name in (
        "editBondLength",
        "resolveMoveSide",
        "defaultMoveSide",
        "applyBondLengthEdit",
        "pendingEdit",
    ):
        assert name in js, f"{name} missing from structure_editor.js"

    assert "BOND_LENGTH_MIN = 0.4" in js
    assert "BOND_LENGTH_MAX = 5.0" in js
    for reason in ("out_of_range", "no_bond", "ring_bond", "locked"):
        assert f'"{reason}"' in js, f"rejection reason {reason} missing"

    # Sign derivation must be documented (s = L - T root choice)
    assert "s = L - T" in js

    # Move-side toggle labels (zh STR fallbacks for the todo-36 UI)
    assert "\u79fb\u52a8\u5de6\u4fa7" in js  # 移动左侧
    assert "\u79fb\u52a8\u53f3\u4fa7" in js  # 移动右侧

    # Rigid translation only inside editBondLength: no rotation math there
    # (todos 34-35 add rotation in their own functions)
    edit_body = js.split("function editBondLength", 1)[1]
    edit_body = edit_body.split("\n  function ", 1)[0]
    assert "Math.cos" not in edit_body and "Math.sin" not in edit_body


def test_structure_editor_node_bond_length_edit() -> None:
    """Node logic: rigid translation to target, move-side toggle, range
    boundaries, ring/no-bond rejections, rigidity (<=1e-9), non-moved side
    byte-identical, input unmutated, locked rejection."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    script = textwrap.dedent("""\
        var window = { fetch: null };
        require(EDITOR_PATH);
        var ed = window.ACPStructureEditor;

        function dist(a, b) {
            var dx = a[0] - b[0], dy = a[1] - b[1], dz = a[2] - b[2];
            return Math.sqrt(dx * dx + dy * dy + dz * dz);
        }
        function close(a, b, eps) { return Math.abs(a - b) <= (eps || 1e-9); }

        // 4-atom chain, non-collinear coordinates
        var symbols = ["C", "C", "C", "C"];
        var coords = [
            [0.0, 0.0, 0.0],
            [1.5, 0.2, -0.1],
            [3.0, 0.1, 0.3],
            [4.5, -0.2, 0.1]
        ];
        var chain = [{ a: 0, b: 1 }, { a: 1, b: 2 }, { a: 2, b: 3 }];
        var snapshot = JSON.stringify(coords);

        // resolve/default move side semantics
        var frags = ed.fragmentsAfterCut(chain, 4, 1, 2); // [[0,1],[2,3]]
        if (ed.defaultMoveSide(frags, 1, 2) !== "A") {
            console.error("FAIL: tie must default to side A");
            process.exit(1);
        }
        if (ed.resolveMoveSide(frags, 1, 2, "B") !== "B" ||
            ed.resolveMoveSide(frags, 1, 2, null) !== "A" ||
            ed.resolveMoveSide(frags, 1, 2, "X") !== null) {
            console.error("FAIL: resolveMoveSide rules");
            process.exit(1);
        }
        var fragsSmall = ed.fragmentsAfterCut(chain, 3, 0, 1); // [[0],[1,2]]
        if (ed.defaultMoveSide(fragsSmall, 0, 1) !== "A") {
            console.error("FAIL: smaller side is A here");
            process.exit(1);
        }
        if (ed.defaultMoveSide(fragsSmall, 1, 0) !== "B") {
            console.error("FAIL: smaller side is B when viewed from atom 1");
            process.exit(1);
        }

        // (a) default side (tie -> A): atoms 0,1 move by the SAME vector
        var resA = ed.editBondLength(symbols, coords, chain, 1, 2, 1.2, null);
        if (!resA.ok) {
            console.error("FAIL: edit rejected " + resA.reason);
            process.exit(1);
        }
        var out = resA.coords;
        // non-moved side byte-identical
        if (JSON.stringify(out[2]) !== JSON.stringify(coords[2]) ||
            JSON.stringify(out[3]) !== JSON.stringify(coords[3])) {
            console.error("FAIL: non-moved atoms changed");
            process.exit(1);
        }
        // moved atoms share one rigid shift
        var shift = [out[0][0] - coords[0][0], out[0][1] - coords[0][1], out[0][2] - coords[0][2]];
        if (!close(out[1][0] - coords[1][0], shift[0]) ||
            !close(out[1][1] - coords[1][1], shift[1]) ||
            !close(out[1][2] - coords[1][2], shift[2])) {
            console.error("FAIL: moved atoms not shifted identically");
            process.exit(1);
        }
        // new bond length hits target within 1e-4
        if (!close(dist(out[1], out[2]), 1.2, 1e-4)) {
            console.error("FAIL: length " + dist(out[1], out[2]));
            process.exit(1);
        }
        // moved fragment internal distance preserved (0-1 bond)
        if (!close(dist(out[0], out[1]), dist(coords[0], coords[1]))) {
            console.error("FAIL: moved fragment deformed");
            process.exit(1);
        }
        // input never mutated
        if (JSON.stringify(coords) !== snapshot) {
            console.error("FAIL: input coords mutated");
            process.exit(1);
        }

        // (b) explicit moveSide B: atoms 0,1 unchanged; 2,3 rigidly shifted
        var resB = ed.editBondLength(symbols, coords, chain, 1, 2, 1.2, "B");
        var outB = resB.coords;
        if (!resB.ok ||
            JSON.stringify(outB[0]) !== JSON.stringify(coords[0]) ||
            JSON.stringify(outB[1]) !== JSON.stringify(coords[1])) {
            console.error("FAIL: side A must be untouched when moving B");
            process.exit(1);
        }
        if (!close(dist(outB[1], outB[2]), 1.2, 1e-4)) {
            console.error("FAIL: side-B edit length");
            process.exit(1);
        }
        var shiftB = outB[2].map(function (v, k) { return v - coords[2][k]; });
        for (var k2 = 0; k2 < 3; k2++) {
            if (!close(outB[3][k2] - coords[3][k2], shiftB[k2])) {
                console.error("FAIL: side-B rigid shift");
                process.exit(1);
            }
        }

        // (c) range: exclusive outside, inclusive boundaries
        if (ed.editBondLength(symbols, coords, chain, 1, 2, 0.39, null).reason !== "out_of_range" ||
            ed.editBondLength(symbols, coords, chain, 1, 2, 5.01, null).reason !== "out_of_range") {
            console.error("FAIL: out-of-range must reject");
            process.exit(1);
        }
        if (!ed.editBondLength(symbols, coords, chain, 1, 2, 0.4, null).ok ||
            !ed.editBondLength(symbols, coords, chain, 1, 2, 5.0, null).ok) {
            console.error("FAIL: boundary targets must be accepted");
            process.exit(1);
        }

        // (d) ring bond: triangle stays connected after the cut
        var ring = [{ a: 0, b: 1 }, { a: 1, b: 2 }, { a: 0, b: 2 }];
        if (ed.editBondLength(symbols, coords, ring, 0, 1, 1.2, null).reason !== "ring_bond") {
            console.error("FAIL: ring bond must reject");
            process.exit(1);
        }

        // (e) non-bonded pair
        if (ed.editBondLength(symbols, coords, chain, 0, 3, 1.2, null).reason !== "no_bond") {
            console.error("FAIL: non-bonded pair must reject");
            process.exit(1);
        }

        // disconnected extra fragment stays untouched (general fragments)
        var coords2 = coords.concat([[20, 5, 5], [21, 5, 5]]);
        var chain2 = chain.concat([{ a: 4, b: 5 }]);
        var resD = ed.editBondLength(symbols.concat(["H", "H"]), coords2, chain2, 1, 2, 1.2, "B");
        if (!resD.ok ||
            JSON.stringify(resD.coords[4]) !== JSON.stringify(coords2[4]) ||
            JSON.stringify(resD.coords[5]) !== JSON.stringify(coords2[5])) {
            console.error("FAIL: unrelated fragment must not move");
            process.exit(1);
        }

        // (h) locked -> applyBondLengthEdit rejects; unlocked -> stages pendingEdit
        window.ACPStructureViewer = {
            state: {
                displayedCoords: coords,
                displayedSymbols: symbols,
                displayedEntryId: "e1"
            }
        };
        ed.setLocked(true);
        var lockedRes = ed.applyBondLengthEdit(1, 2, 1.2, null);
        if (lockedRes.ok || lockedRes.reason !== "locked") {
            console.error("FAIL: locked edit must reject");
            process.exit(1);
        }
        ed.setLocked(false);
        var applied = ed.applyBondLengthEdit(1, 2, 1.2, "B");
        if (!applied.ok || !ed.editorState.pendingEdit ||
            ed.editorState.pendingEdit.type !== "bond_length" ||
            ed.editorState.pendingEdit.atomA !== 1 ||
            ed.editorState.pendingEdit.atomB !== 2 ||
            ed.editorState.pendingEdit.target !== 1.2 ||
            ed.editorState.pendingEdit.moveSide !== "B" ||
            ed.editorState.pendingEdit.coords !== applied.coords) {
            console.error("FAIL: pendingEdit not staged: " +
                JSON.stringify(ed.editorState.pendingEdit && ed.editorState.pendingEdit.type));
            process.exit(1);
        }
        // todo 36: edits now APPLY through the transaction engine —
        // displayed coords become the edited array and dirty goes live
        if (window.ACPStructureViewer.state.displayedCoords !== applied.coords) {
            console.error("FAIL: applied edit must update displayed coords");
            process.exit(1);
        }
        if (window.ACPStructureViewer.state.dirty !== true) {
            console.error("FAIL: dirty flag must be live after edit");
            process.exit(1);
        }
        console.log("PASS");
    """).replace("EDITOR_PATH", json.dumps(str(_EDITOR_JS_PATH)))

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"Node bond-edit test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


# ---------------------------------------------------------------------------
# Wave 6 / todo 34: bond-angle edit + collinear fallback
# ---------------------------------------------------------------------------


def test_structure_editor_angle_edit_contract() -> None:
    """Contract: angle-edit API names, range constants, collinear warning
    constant, Rodrigues formula documented, moveSide normalization."""
    js = _EDITOR_JS_PATH.read_text(encoding="utf-8")

    for name in (
        "editBondAngle",
        "applyBondAngleEdit",
        "angleDeg",
        "_principalAxis",
        "_stableOrthogonalAxis",
        "_rotateRodrigues",
    ):
        assert name in js, f"{name} missing from structure_editor.js"

    assert "ANGLE_MIN = 1" in js
    assert "ANGLE_MAX = 179" in js
    assert "COLLINEAR_EPS = 1e-6" in js

    # Collinear fallback warning constant (surfaced via result.warning)
    assert "\u5171\u7ebf\u89d2\u5ea6\u8f93\u5165" in js  # 共线角度输入
    # second half of the warning: 旋转轴取最稳定正交轴
    assert "\u65cb\u8f6c\u8f74\u53d6\u6700\u7a33\u5b9a\u6b63\u4ea4\u8f74" in js

    # Rodrigues formula documented in the code
    assert "Rodrigues" in js
    assert "(k . v)(1 - cos(theta))" in js

    # Angle-edit moveSide normalization: only "C" is rotatable
    assert 'normalized to "C"' in js


def test_structure_editor_node_bond_angle_edit() -> None:
    """Node logic: rotation hits target (1e-3 deg), A-side untouched,
    C-side rigid (1e-9), range boundaries, no_bond/ring rejections,
    collinear fallback axis choice + warning, staging, locked."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    script = textwrap.dedent("""\
        var window = { fetch: null };
        require(EDITOR_PATH);
        var ed = window.ACPStructureEditor;

        function dist(a, b) {
            var dx = a[0] - b[0], dy = a[1] - b[1], dz = a[2] - b[2];
            return Math.sqrt(dx * dx + dy * dy + dz * dz);
        }
        function close(a, b, eps) { return Math.abs(a - b) <= (eps || 1e-9); }

        // angleDeg sanity
        if (!close(ed.angleDeg([1, 0, 0], [0, 0, 0], [0, 1, 0]), 90)) {
            console.error("FAIL: angleDeg 90");
            process.exit(1);
        }
        if (ed.angleDeg([1, 0, 0], [1, 0, 0], [0, 1, 0]) !== 0) {
            console.error("FAIL: degenerate arm must be 0");
            process.exit(1);
        }

        // fixture: A0-B1-C2 with 130 deg at B, D3 on the A side, E4 on the C side
        var rad = Math.PI / 180;
        var symbols = ["C", "C", "C", "H", "H"];
        var coords = [
            [1.0, 0.0, 0.0],
            [0.0, 0.0, 0.0],
            [1.5 * Math.cos(130 * rad), 1.5 * Math.sin(130 * rad), 0.0],
            [1.5, 0.5, 0.3],
            [2.6 * Math.cos(130 * rad), 2.6 * Math.sin(130 * rad), 0.0]
        ];
        var edges = [{ a: 0, b: 1 }, { a: 1, b: 2 }, { a: 0, b: 3 }, { a: 2, b: 4 }];
        var snapshot = JSON.stringify(coords);

        if (!close(ed.angleDeg(coords[0], coords[1], coords[2]), 130, 1e-9)) {
            console.error("FAIL: fixture angle " + ed.angleDeg(coords[0], coords[1], coords[2]));
            process.exit(1);
        }

        // (a) close 130 -> 120: A side (0,1,3) byte-identical
        var res = ed.editBondAngle(symbols, coords, edges, 0, 1, 2, 120, null);
        if (!res.ok) {
            console.error("FAIL: rejected " + res.reason);
            process.exit(1);
        }
        var out = res.coords;
        for (var fixedIdx = 0; fixedIdx <= 3; fixedIdx++) {
            if (fixedIdx === 2) continue;
            if (JSON.stringify(out[fixedIdx]) !== JSON.stringify(coords[fixedIdx])) {
                console.error("FAIL: A-side atom " + fixedIdx + " moved");
                process.exit(1);
            }
        }
        if (!close(ed.angleDeg(out[0], out[1], out[2]), 120, 1e-3)) {
            console.error("FAIL: angle " + ed.angleDeg(out[0], out[1], out[2]));
            process.exit(1);
        }
        if (!close(dist(out[2], out[4]), dist(coords[2], coords[4]))) {
            console.error("FAIL: C-side deformed");
            process.exit(1);
        }
        if (!close(dist(out[1], out[2]), dist(coords[1], coords[2]))) {
            console.error("FAIL: B-C bond length changed");
            process.exit(1);
        }
        if (JSON.stringify(coords) !== snapshot) {
            console.error("FAIL: input mutated");
            process.exit(1);
        }

        // (b) open 130 -> 150
        var open = ed.editBondAngle(symbols, coords, edges, 0, 1, 2, 150, null);
        var openAngle = ed.angleDeg(open.coords[0], open.coords[1], open.coords[2]);
        if (!open.ok || !close(openAngle, 150, 1e-3)) {
            console.error("FAIL: opening sign convention");
            process.exit(1);
        }

        // moveSide "A"/"B" normalize to "C" (identical output); invalid rejects
        var normA = ed.editBondAngle(symbols, coords, edges, 0, 1, 2, 120, "A");
        if (!normA.ok || JSON.stringify(normA.coords) !== JSON.stringify(res.coords)) {
            console.error("FAIL: moveSide A must normalize to C");
            process.exit(1);
        }
        if (ed.editBondAngle(symbols, coords, edges, 0, 1, 2, 120, "X").reason !==
            "invalid_move_side") {
            console.error("FAIL: invalid moveSide");
            process.exit(1);
        }

        // (c) range boundaries
        var tooSmall = ed.editBondAngle(symbols, coords, edges, 0, 1, 2, 0.9, null);
        var tooBig = ed.editBondAngle(symbols, coords, edges, 0, 1, 2, 179.5, null);
        if (tooSmall.reason !== "out_of_range" || tooBig.reason !== "out_of_range") {
            console.error("FAIL: out-of-range angles must reject");
            process.exit(1);
        }
        if (!ed.editBondAngle(symbols, coords, edges, 0, 1, 2, 1, null).ok ||
            !ed.editBondAngle(symbols, coords, edges, 0, 1, 2, 179, null).ok) {
            console.error("FAIL: boundary angles must be accepted");
            process.exit(1);
        }

        // (d) missing A-B edge -> no_bond
        var noAB = [{ a: 1, b: 2 }, { a: 2, b: 4 }];
        if (ed.editBondAngle(symbols, coords, noAB, 0, 1, 2, 120, null).reason !== "no_bond") {
            console.error("FAIL: missing A-B edge");
            process.exit(1);
        }

        // (e) ring containing B-C -> ring_bond
        var ring = [{ a: 0, b: 1 }, { a: 1, b: 2 }, { a: 0, b: 2 }];
        if (ed.editBondAngle(symbols, coords, ring, 0, 1, 2, 120, null).reason !== "ring_bond") {
            console.error("FAIL: ring must reject");
            process.exit(1);
        }

        // (f) collinear fallback: A-B-C on +x, C-side elongated along y
        var linCoords = [
            [-1.0, 0.0, 0.0],
            [0.0, 0.0, 0.0],
            [2.0, 0.0, 0.0],
            [2.1, 1.0, 0.0],
            [2.1, -1.0, 0.0]
        ];
        var linEdges = [{ a: 0, b: 1 }, { a: 1, b: 2 }, { a: 2, b: 3 }, { a: 2, b: 4 }];
        var p = ed._principalAxis([linCoords[2], linCoords[3], linCoords[4]]);
        if (Math.abs(p[1]) < 0.99) {
            console.error("FAIL: fixture principal axis should be y, got " + JSON.stringify(p));
            process.exit(1);
        }
        var col = ed.editBondAngle(symbols, linCoords, linEdges, 0, 1, 2, 90, null);
        if (!col.ok || col.warning !== "共线角度输入，旋转轴取最稳定正交轴") {
            console.error("FAIL: collinear warning missing");
            process.exit(1);
        }
        var cNew = col.coords[2];
        // axis avoided y (the fragment long axis) -> rotation plane is xz-free:
        // C sweeps in the plane orthogonal to z => C' lands on the y axis
        if (Math.abs(cNew[0]) > 1e-9 || Math.abs(cNew[2]) > 1e-9 ||
            !close(Math.abs(cNew[1]), 2.0, 1e-9)) {
            console.error("FAIL: collinear axis choice " + JSON.stringify(cNew));
            process.exit(1);
        }
        if (!close(ed.angleDeg(col.coords[0], col.coords[1], cNew), 90, 1e-3)) {
            console.error("FAIL: collinear target angle");
            process.exit(1);
        }
        if (!close(dist(col.coords[2], col.coords[3]), dist(linCoords[2], linCoords[3]))) {
            console.error("FAIL: collinear C-side deformed");
            process.exit(1);
        }

        // orchestration: locked -> locked; unlocked -> stages pendingEdit
        window.ACPStructureViewer = {
            state: { displayedCoords: coords, displayedSymbols: symbols, displayedEntryId: "e1" }
        };
        ed.setLocked(true);
        var lockedRes = ed.applyBondAngleEdit(0, 1, 2, 120, null);
        if (lockedRes.ok || lockedRes.reason !== "locked") {
            console.error("FAIL: locked angle edit");
            process.exit(1);
        }
        ed.setLocked(false);
        var staged = ed.applyBondAngleEdit(0, 1, 2, 120, "A");
        if (!staged.ok || !ed.editorState.pendingEdit ||
            ed.editorState.pendingEdit.type !== "bond_angle" ||
            ed.editorState.pendingEdit.target !== 120 ||
            ed.editorState.pendingEdit.moveSide !== "C") {
            console.error("FAIL: pendingEdit staging");
            process.exit(1);
        }
        // todo 36: edits apply live — displayed coords are the edited array
        if (window.ACPStructureViewer.state.displayedCoords !== staged.coords) {
            console.error("FAIL: applied edit must update displayed coords");
            process.exit(1);
        }
        if (window.ACPStructureViewer.state.dirty !== true) {
            console.error("FAIL: dirty flag must be live after edit");
            process.exit(1);
        }
        console.log("PASS");
    """).replace("EDITOR_PATH", json.dumps(str(_EDITOR_JS_PATH)))

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"Node angle-edit test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


# ---------------------------------------------------------------------------
# Wave 6 / todo 35: dihedral edit + shortest rotation + ring refusal
# ---------------------------------------------------------------------------


def test_structure_editor_dihedral_edit_contract() -> None:
    """Contract: dihedral API names, normalization interval, shortest-
    rotation comment, moveSide normalization, topology-preservation note."""
    js = _EDITOR_JS_PATH.read_text(encoding="utf-8")

    for name in (
        "editDihedral",
        "applyDihedralEdit",
        "dihedralDeg",
        "normalizeDihedral",
    ):
        assert name in js, f"{name} missing from structure_editor.js"

    assert "(-180, 180]" in js
    assert "SHORTEST rotation" in js
    assert 'normalized to "C"' in js
    assert "praxeolitic" in js


def test_structure_editor_node_dihedral_edit() -> None:
    """Node logic: dihedral hits target (1e-3 deg), shortest physical
    rotation across the +-180 wrap (marker displacement), rigidity 1e-9,
    A-side untouched, ring/no-bond rejections, normalization, staging."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    script = textwrap.dedent("""\
        var window = { fetch: null };
        require(EDITOR_PATH);
        var ed = window.ACPStructureEditor;

        function dist(a, b) {
            var dx = a[0] - b[0], dy = a[1] - b[1], dz = a[2] - b[2];
            return Math.sqrt(dx * dx + dy * dy + dz * dz);
        }
        function close(a, b, eps) { return Math.abs(a - b) <= (eps || 1e-9); }

        // (c) normalizeDihedral
        var cases = { 180: 180, "-180": 180, 190: -170, "-190": 170, 360: 0, 0: 0, 540: 180 };
        for (var input in cases) {
            if (ed.normalizeDihedral(parseFloat(input)) !== cases[input]) {
                console.error("FAIL: normalize(" + input + ") = " +
                    ed.normalizeDihedral(parseFloat(input)));
                process.exit(1);
            }
        }

        // dihedralDeg sanity: eclipsed 0, anti 180, +90, -90 (B->C along +x)
        var B = [0, 0, 0], C = [1.5, 0, 0];
        var A0 = [0, 1, 0];
        if (!close(ed.dihedralDeg(A0, B, C, [1.5, 1, 0]), 0, 1e-9) ||
            !close(ed.dihedralDeg(A0, B, C, [1.5, -1, 0]), 180, 1e-9) ||
            !close(ed.dihedralDeg(A0, B, C, [1.5, 0, 1]), 90, 1e-9) ||
            !close(ed.dihedralDeg(A0, B, C, [1.5, 0, -1]), -90, 1e-9)) {
            console.error("FAIL: dihedralDeg convention");
            process.exit(1);
        }

        // (a) butane-like chain A0-B1-C2-D3 + C-side marker E4
        var symbols = ["C", "C", "C", "C", "H"];
        var coords = [
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0],
            [1.5, 0.0, 0.0],
            [1.5, 1.0, 0.0],
            [2.4, 1.2, 0.5]
        ];
        var edges = [{ a: 0, b: 1 }, { a: 1, b: 2 }, { a: 2, b: 3 }, { a: 3, b: 4 }];
        var snapshot = JSON.stringify(coords);

        var res = ed.editDihedral(symbols, coords, edges, 0, 1, 2, 3, 60, null);
        if (!res.ok) {
            console.error("FAIL: rejected " + res.reason);
            process.exit(1);
        }
        var out = res.coords;
        var got = ed.dihedralDeg(out[0], out[1], out[2], out[3]);
        if (!close(got, 60, 1e-3)) {
            console.error("FAIL: dihedral " + got);
            process.exit(1);
        }
        if (out[0] !== coords[0] || out[1] !== coords[1]) {
            console.error("FAIL: A-side rows must keep identity");
            process.exit(1);
        }
        if (out.length !== coords.length) {
            console.error("FAIL: atom count/order changed");
            process.exit(1);
        }
        if (!close(dist(out[2], out[3]), dist(coords[2], coords[3])) ||
            !close(dist(out[3], out[4]), dist(coords[3], coords[4])) ||
            !close(dist(out[2], out[4]), dist(coords[2], coords[4]))) {
            console.error("FAIL: C-side deformed");
            process.exit(1);
        }
        if (!close(dist(out[1], out[2]), dist(coords[1], coords[2]))) {
            console.error("FAIL: B-C length changed");
            process.exit(1);
        }
        if (JSON.stringify(coords) !== snapshot) {
            console.error("FAIL: input mutated");
            process.exit(1);
        }

        // (b) shortest path across the +-180 wrap: current +170 -> target -170
        var Bb = [0, 0, 0], Cb = [1.0, 0, 0], Ab = [0, 1, 0];
        var rad = Math.PI / 180;
        var Db = [1.0, Math.cos(170 * rad), Math.sin(170 * rad)];
        var marker = [1.0, Math.cos(170 * rad) * 2, Math.sin(170 * rad) * 2];
        var wrapCoords = [Ab, Bb, Cb, Db, marker];
        var wrapEdges = [{ a: 0, b: 1 }, { a: 1, b: 2 }, { a: 2, b: 3 }, { a: 2, b: 4 }];
        var wrapRes = ed.editDihedral(symbols, wrapCoords, wrapEdges, 0, 1, 2, 3, -170, null);
        if (!wrapRes.ok) {
            console.error("FAIL: wrap rejected " + wrapRes.reason);
            process.exit(1);
        }
        var wOut = wrapRes.coords;
        var finalDihedral = ed.dihedralDeg(wOut[0], wOut[1], wOut[2], wOut[3]);
        if (!close(finalDihedral, -170, 1e-3)) {
            console.error("FAIL: wrap final " + finalDihedral);
            process.exit(1);
        }
        // PHYSICAL shortest-path check: the marker sits at radius 2 from
        // the axis; a 20-degree sweep moves it 2*2*sin(10 deg) = 0.6946,
        // whereas a 340-degree sweep would move it ~3.985.
        var disp = dist(wOut[4], marker);
        if (!close(disp, 4 * Math.sin(10 * rad), 1e-6)) {
            console.error("FAIL: marker displacement " + disp +
                " is not the 20-degree chord");
            process.exit(1);
        }

        // (d) ring 4-cycle -> ring_bond
        var ring = [{ a: 0, b: 1 }, { a: 1, b: 2 }, { a: 2, b: 3 }, { a: 3, b: 0 }];
        if (ed.editDihedral(symbols, coords, ring, 0, 1, 2, 3, 60, null).reason !== "ring_bond") {
            console.error("FAIL: ring dihedral must reject");
            process.exit(1);
        }

        // (e) missing each of the three edges -> no_bond
        var missAB = [{ a: 1, b: 2 }, { a: 2, b: 3 }];
        var missBC = [{ a: 0, b: 1 }, { a: 2, b: 3 }];
        var missCD = [{ a: 0, b: 1 }, { a: 1, b: 2 }];
        if (ed.editDihedral(symbols, coords, missAB, 0, 1, 2, 3, 60, null).reason !== "no_bond" ||
            ed.editDihedral(symbols, coords, missBC, 0, 1, 2, 3, 60, null).reason !== "no_bond" ||
            ed.editDihedral(symbols, coords, missCD, 0, 1, 2, 3, 60, null).reason !== "no_bond") {
            console.error("FAIL: missing edges must reject no_bond");
            process.exit(1);
        }

        // moveSide normalization + invalid
        var norm = ed.editDihedral(symbols, coords, edges, 0, 1, 2, 3, 60, "A");
        if (!norm.ok || JSON.stringify(norm.coords) !== JSON.stringify(res.coords)) {
            console.error("FAIL: moveSide A must normalize to C");
            process.exit(1);
        }
        if (ed.editDihedral(symbols, coords, edges, 0, 1, 2, 3, 60, "X").reason !==
            "invalid_move_side") {
            console.error("FAIL: invalid moveSide");
            process.exit(1);
        }

        // staging: locked -> locked; unlocked (pre-seeded graph) -> pendingEdit
        window.ACPStructureViewer = {
            state: { displayedCoords: coords, displayedSymbols: symbols, displayedEntryId: "e1" }
        };
        ed.editorState.graph = { edges: edges, provenance: "fixture" };
        ed.setLocked(true);
        var lockedRes = ed.applyDihedralEdit(0, 1, 2, 3, 190, null);
        if (lockedRes.ok || lockedRes.reason !== "locked") {
            console.error("FAIL: locked dihedral edit");
            process.exit(1);
        }
        ed.setLocked(false);
        var staged = ed.applyDihedralEdit(0, 1, 2, 3, 190, null);
        if (!staged.ok || !ed.editorState.pendingEdit ||
            ed.editorState.pendingEdit.type !== "dihedral" ||
            ed.editorState.pendingEdit.target !== -170 ||
            ed.editorState.pendingEdit.moveSide !== "C") {
            console.error("FAIL: pendingEdit staging (target must normalize 190 -> -170)");
            process.exit(1);
        }
        console.log("PASS");
    """).replace("EDITOR_PATH", json.dumps(str(_EDITOR_JS_PATH)))

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"Node dihedral test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


# ---------------------------------------------------------------------------
# Wave 6 / todo 36: validation + collision warnings + transactions
# ---------------------------------------------------------------------------


def test_structure_editor_transaction_contract() -> None:
    """Contract: transaction API names, COLLISION_FACTOR, dirty sync,
    entry scoping, i18n keys both locales, coords-only edits."""
    ed_js = _EDITOR_JS_PATH.read_text(encoding="utf-8")
    sv_js = _SV_JS_PATH.read_text(encoding="utf-8")
    css = (FRONTEND_CSS_DIR / "structure_viewer.css").read_text(encoding="utf-8")
    html = read_workbench_content()

    for name in (
        "checkCollisions",
        "bindEntry",
        "confirmSwitch",
        "undoEdit",
        "redoEdit",
        "resetEdits",
        "isDirty",
        "syncDirtyFlag",
        "applyMeasuredEdit",
        "_commitEdit",
        "collision_warnings",
        "moved_atom_ids",
        "originalCoords",
        "pendingSwitch",
        "saveRequested",
    ):
        assert name in ed_js, f"{name} missing from structure_editor.js"

    assert "COLLISION_FACTOR = 0.55" in ed_js
    assert "entry_mismatch" in ed_js and "dirty_switch" in ed_js
    # Dirty flag syncs into the todo-15 store field
    assert "state.dirty = isDirty()" in ed_js

    # Coords-only edits: no atom/bond mutation machinery anywhere
    assert "addAtom" not in ed_js and "removeAtom" not in ed_js
    assert "addHydrogen" not in ed_js and "addHydrogens" not in ed_js

    # structure_viewer integration: bindEntry on geometry load + edit panel
    assert "bindEntry" in sv_js
    assert 'id = "structure-inspector-edit"' in sv_js
    assert "undoEdit" in sv_js and "resetEdits" in sv_js
    assert "confirmSwitch" in sv_js
    assert "sv-edit-panel" in sv_js and "sv-badge-dirty" in sv_js

    # CSS: edit panel + dirty badge (amber) + collision (red) + prompt
    for cls in (
        ".sv-edit-panel",
        ".sv-badge-dirty",
        ".sv-edit-collision",
        ".sv-edit-prompt",
        ".sv-edit-btn",
    ):
        assert cls in css, f"{cls} missing from structure_viewer.css"

    # i18n keys in BOTH locales
    for zh, en in (
        ('"structure.edit.title": "几何编辑"', '"structure.edit.title": "Geometry Editing"'),
        ('"structure.edit.bond_source": "成键来源"', '"structure.edit.bond_source": "Bond Source"'),
        ('"structure.edit.dirty": "已修改，未保存"', '"structure.edit.dirty": "Modified, unsaved"'),
        ('"structure.edit.undo": "撤销"', '"structure.edit.undo": "Undo"'),
        ('"structure.edit.redo": "重做"', '"structure.edit.redo": "Redo"'),
        ('"structure.edit.reset": "重置"', '"structure.edit.reset": "Reset"'),
        ('"structure.edit.discard": "放弃"', '"structure.edit.discard": "Discard"'),
        ('"structure.edit.save_as": "另存为"', '"structure.edit.save_as": "Save As"'),
        ('"structure.edit.cancel": "取消"', '"structure.edit.cancel": "Cancel"'),
        (
            '"structure.edit.collision": "严重碰撞"',
            '"structure.edit.collision": "Severe collision"',
        ),
    ):
        assert zh in html, f"zh edit key missing: {zh}"
        assert en in html, f"en edit key missing: {en}"


def test_structure_editor_node_transactions() -> None:
    """Node logic: validation in apply* path, collisions (pure + in
    transaction), undo/redo/reset byte-exact, moved ids, dirty sync,
    pendingSwitch flow, entry mismatch, coords-only."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    script = textwrap.dedent("""\
        var window = { fetch: null };
        require(EDITOR_PATH);
        var ed = window.ACPStructureEditor;

        function dist(a, b) {
            var dx = a[0] - b[0], dy = a[1] - b[1], dz = a[2] - b[2];
            return Math.sqrt(dx * dx + dy * dy + dz * dz);
        }

        // checkCollisions pure: boundary strictly below factor
        var syms2 = ["C", "C"];
        var close = ed.checkCollisions(syms2, [[0, 0, 0], [0.83, 0, 0]], []);
        var atLimit = ed.checkCollisions(syms2, [[0, 0, 0], [0.837, 0, 0]], []);
        if (close.length !== 1 || close[0].severity !== "severe" ||
            close[0].a !== 0 || close[0].b !== 1) {
            console.error("FAIL: severe collision expected at 0.83 A");
            process.exit(1);
        }
        if (atLimit.length !== 0) {
            console.error("FAIL: exactly-at-factor must NOT warn (strictly <)");
            process.exit(1);
        }
        // bonded pairs never collide
        var bonded = ed.checkCollisions(syms2, [[0, 0, 0], [0.5, 0, 0]], [{ a: 0, b: 1 }]);
        if (bonded.length !== 0) {
            console.error("FAIL: bonded pair must not warn");
            process.exit(1);
        }

        // U-shaped chain: angle edit folds atom 2 onto atom 0 -> collision
        var symbols = ["C", "C", "C", "C"];
        var coords = [
            [0.0, 0.0, 0.0],
            [1.5, 0.0, 0.0],
            [1.5, 1.5, 0.0],
            [0.0, 1.5, 0.0]
        ];
        var edges = [{ a: 0, b: 1 }, { a: 1, b: 2 }, { a: 2, b: 3 }];
        window.ACPStructureViewer = {
            state: {
                displayedCoords: coords,
                displayedSymbols: symbols,
                displayedEntryId: "e1"
            }
        };
        ed.editorState.graph = { edges: edges, provenance: "fixture" };

        // validation runs in the apply* path too
        var badRange = ed.applyBondLengthEdit(0, 1, 0.3, null);
        if (badRange.ok || badRange.reason !== "out_of_range" || ed.isDirty()) {
            console.error("FAIL: apply-path validation");
            process.exit(1);
        }

        var fold = ed.applyBondAngleEdit(0, 1, 2, 1, null);
        if (!fold.ok) {
            console.error("FAIL: fold rejected " + fold.reason);
            process.exit(1);
        }
        var txn = ed.editorState.transactions[0];
        if (txn.type !== "bond_angle" ||
            JSON.stringify(txn.atom_ids) !== JSON.stringify([0, 1, 2]) ||
            JSON.stringify(txn.moved_atom_ids) !== JSON.stringify([2, 3])) {
            console.error("FAIL: transaction shape " + JSON.stringify(txn).slice(0, 120));
            process.exit(1);
        }
        if (!txn.collision_warnings.length ||
            txn.collision_warnings[0].a !== 0 || txn.collision_warnings[0].b !== 2) {
            console.error("FAIL: fold must produce collision 0-2");
            process.exit(1);
        }
        if (window.ACPStructureViewer.state.dirty !== true || !ed.isDirty()) {
            console.error("FAIL: dirty after edit");
            process.exit(1);
        }
        if (window.ACPStructureViewer.state.displayedSymbols !== symbols) {
            console.error("FAIL: symbols must never be touched");
            process.exit(1);
        }

        var beforeSnapshot = JSON.stringify(txn.before);

        // undo restores before EXACTLY (byte-identical) and clears dirty
        var undoRes = ed.undoEdit();
        if (!undoRes.ok ||
            JSON.stringify(window.ACPStructureViewer.state.displayedCoords) !== beforeSnapshot ||
            window.ACPStructureViewer.state.displayedCoords !== txn.before) {
            console.error("FAIL: undo must restore before exactly");
            process.exit(1);
        }
        if (ed.isDirty() || window.ACPStructureViewer.state.dirty !== false) {
            console.error("FAIL: dirty cleared after undo");
            process.exit(1);
        }
        // redo reapplies
        var redoRes = ed.redoEdit();
        if (!redoRes.ok || window.ACPStructureViewer.state.displayedCoords !== txn.after) {
            console.error("FAIL: redo must reapply after");
            process.exit(1);
        }
        // second edit then reset restores the ORIGINAL entry coords
        var orig = ed.editorState.originalCoords;
        var second = ed.applyBondLengthEdit(1, 2, 2.0, null);
        if (!second.ok || ed.editorState.transactions.length !== 2) {
            console.error("FAIL: second edit");
            process.exit(1);
        }
        var resetRes = ed.resetEdits();
        if (!resetRes.ok ||
            window.ACPStructureViewer.state.displayedCoords !== orig ||
            ed.isDirty() || ed.editorState.transactions.length !== 0) {
            console.error("FAIL: reset must restore originalCoords");
            process.exit(1);
        }

        // pendingSwitch: dirty bind to a new entry defers, does not discard
        ed.applyBondLengthEdit(1, 2, 2.0, null); // dirty again
        var sw = ed.bindEntry("e2", symbols, coords.map(function (r) { return r.slice(); }));
        if (sw.ok || sw.reason !== "dirty_switch" || !ed.editorState.pendingSwitch ||
            ed.editorState.pendingSwitch.targetEntryId !== "e2") {
            console.error("FAIL: dirty switch must defer with pendingSwitch");
            process.exit(1);
        }
        if (ed.editorState.transactions.length !== 1) {
            console.error("FAIL: transactions must survive the deferral");
            process.exit(1);
        }
        // apply while the viewer shows the OTHER entry -> entry_mismatch
        window.ACPStructureViewer.state.displayedEntryId = "e2";
        var mm = ed.applyBondLengthEdit(1, 2, 2.0, null);
        if (mm.ok || mm.reason !== "entry_mismatch") {
            console.error("FAIL: cross-entry edit must reject entry_mismatch");
            process.exit(1);
        }
        // cancel: stay, keep edits
        var cancelRes = ed.confirmSwitch("cancel");
        if (!cancelRes || cancelRes.switched !== false || ed.editorState.pendingSwitch ||
            ed.editorState.transactions.length !== 1) {
            console.error("FAIL: cancel keeps edits");
            process.exit(1);
        }
        // discard: rebind + clear
        ed.bindEntry("e2", symbols, coords);
        var disc = ed.confirmSwitch("discard");
        if (!disc || disc.switched !== true || ed.editorState.entryId !== "e2" ||
            ed.editorState.transactions.length !== 0 || ed.isDirty()) {
            console.error("FAIL: discard rebinds and clears");
            process.exit(1);
        }
        // save: flag + hook + rebind (re-seed graph — rebind cleared it and
        // the U fixture would infer a closing square = ring)
        var hookCalls = 0;
        window.ACPStructureViewer.requestSaveEditedAsset = function () { hookCalls += 1; };
        window.ACPStructureViewer.state.displayedEntryId = "e2";
        ed.editorState.graph = { edges: edges, provenance: "fixture" };
        ed.applyBondLengthEdit(1, 2, 2.0, null); // dirty on e2
        ed.bindEntry("e3", symbols, coords);
        var saveRes = ed.confirmSwitch("save");
        if (!saveRes || saveRes.switched !== true || !ed.editorState.saveRequested ||
            hookCalls !== 1 || ed.editorState.entryId !== "e3" ||
            ed.editorState.transactions.length !== 0) {
            console.error("FAIL: save flow (flag + hook + rebind)");
            process.exit(1);
        }

        // applyMeasuredEdit dispatch
        window.ACPStructureViewer.state.displayedEntryId = "e3";
        ed.editorState.graph = { edges: edges, provenance: "fixture" };
        var m1 = ed.applyMeasuredEdit("bond_length", [1, 2], 1.8, null);
        if (!m1.ok || ed.editorState.transactions[0].type !== "bond_length") {
            console.error("FAIL: applyMeasuredEdit bond_length");
            process.exit(1);
        }
        var m2 = ed.applyMeasuredEdit("dihedral", [0, 1, 2], 60);
        if (m2.ok || m2.reason !== "no_bond") {
            console.error("FAIL: applyMeasuredEdit arity guard");
            process.exit(1);
        }
        console.log("PASS");
    """).replace("EDITOR_PATH", json.dumps(str(_EDITOR_JS_PATH)))

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"Node transaction test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


# ---------------------------------------------------------------------------
# Wave 6 / todo 37: asset save + export + new-calculation handoff
# ---------------------------------------------------------------------------


def test_structure_editor_asset_contract() -> None:
    """Contract: export/save/prefill API names, POST body fields, wizard
    hook WITHOUT submit, no RESULT/WORK writes, i18n keys both locales."""
    ed_js = _EDITOR_JS_PATH.read_text(encoding="utf-8")
    sv_js = _SV_JS_PATH.read_text(encoding="utf-8")
    html = read_workbench_content()

    for name in (
        "buildEditedXyz",
        "provenanceComment",
        "exportEditedXyz",
        "saveEditedAsset",
        "requestSaveEditedAsset",
        "prefillNewCalculation",
        "savedAssetId",
        "saveError",
    ):
        assert name in ed_js, f"{name} missing from structure_editor.js"

    for field in ("parent_job_id", "parent_entry_id", "edit_operations", "provenance"):
        assert field in ed_js, f"POST field {field} missing"
    assert "/api/v1/structure-assets" in ed_js

    # Wizard bridge exists; prefill NEVER submits (anti-pattern #29)
    assert "window._svPrefillNewCalculation" in html
    bridge = html.split("window._svPrefillNewCalculation = function", 1)[1]
    bridge = bridge.split("\n};", 1)[0]
    assert "submit" not in bridge.lower()
    assert "submitJob" not in ed_js, "editor must never submit jobs"

    # In-memory + POST only: no task-dir writes anywhere in the editor
    assert "RESULT/" not in ed_js and "WORK/" not in ed_js

    # structure_viewer panel wiring
    for name in (
        "exportEditedXyz",
        "saveEditedAsset",
        "prefillNewCalculation",
        "structure.edit.export_xyz",
        "structure.edit.save_asset",
        "structure.edit.new_calc",
    ):
        assert name in sv_js, f"{name} missing from structure_viewer.js"

    # i18n keys in BOTH locales
    for zh, en in (
        ('"structure.edit.export_xyz": "导出 XYZ"', '"structure.edit.export_xyz": "Export XYZ"'),
        (
            '"structure.edit.save_asset": "另存为结构资产"',
            '"structure.edit.save_asset": "Save As Structure Asset"',
        ),
        (
            '"structure.edit.new_calc": "以此结构新建计算"',
            '"structure.edit.new_calc": "New Calculation From This Structure"',
        ),
        ('"structure.edit.saved": "已保存"', '"structure.edit.saved": "Saved"'),
        ('"structure.edit.save_error": "保存失败"', '"structure.edit.save_error": "Save failed"'),
    ):
        assert zh in html, f"zh key missing: {zh}"
        assert en in html, f"en key missing: {en}"


def test_structure_editor_node_asset_save() -> None:
    """Node logic: buildEditedXyz exact format, export returns text in Node,
    saveEditedAsset POST round-trip (all four fields), success/failure
    states, requestSaveEditedAsset completes the pending switch, prefill
    calls the wizard bridge."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    script = textwrap.dedent("""\
        var window = { fetch: null };
        require(EDITOR_PATH);
        var ed = window.ACPStructureEditor;

        var symbols = ["O", "H", "H"];
        var coords = [[0, 0, 0], [0.96, 0, 0], [-0.4, 0.87, 0]];
        var txns = [
            { type: "bond_length", atom_ids: [0, 1], before: [], after: [],
              moved_atom_ids: [1], collision_warnings: [] },
            { type: "bond_length", atom_ids: [0, 2], before: [], after: [],
              moved_atom_ids: [2], collision_warnings: [] },
            { type: "dihedral", atom_ids: [0, 1, 2, 3], before: [], after: [],
              moved_atom_ids: [2, 3], collision_warnings: [] }
        ];

        // buildEditedXyz: exact format incl. provenance summary
        var xyz = ed.buildEditedXyz(symbols, coords, "orig", {
            jobId: "job1", entryId: "e1", transactions: txns
        });
        var lines = xyz.trim().split("\\n");
        if (lines[0] !== "3") { console.error("FAIL: count line"); process.exit(1); }
        if (lines[1] !== "ACP edit: parent=job1 entry=e1 ops=3 (bond_length:2, dihedral:1)") {
            console.error("FAIL: provenance line " + lines[1]);
            process.exit(1);
        }
        if (lines[2].split(/\\s+/)[0] !== "O" ||
            lines[3].split(/\\s+/)[1] !== "0.960000") {
            console.error("FAIL: coordinate lines");
            process.exit(1);
        }
        // original comment fallback when no provenance info
        var plain = ed.buildEditedXyz(symbols, coords, "original comment", null);
        if (plain.split("\\n")[1] !== "original comment") {
            console.error("FAIL: original comment fallback");
            process.exit(1);
        }

        window.ACPStructureViewer = {
            state: {
                jobId: "job1",
                displayedCoords: coords,
                displayedSymbols: symbols,
                displayedEntryId: "e1"
            }
        };
        ed.editorState.entryId = "e1";
        ed.editorState.transactions = txns;

        // exportEditedXyz in Node: returns text, no DOM crash
        var exported = ed.exportEditedXyz();
        if (exported == null || exported.split("\\n")[1] !== lines[1]) {
            console.error("FAIL: exportEditedXyz node path");
            process.exit(1);
        }

        // saveEditedAsset: fake fetch round-trip
        var calls = [];
        ed._fetchImpl = function (url, opts) {
            calls.push({ url: url, body: JSON.parse(opts.body) });
            return Promise.resolve({
                ok: true, status: 201, statusText: "Created",
                json: function () { return Promise.resolve({ asset_id: "up_abc", ok: true }); }
            });
        };
        ed.saveEditedAsset().then(function (res) {
            if (!res.ok || res.assetId !== "up_abc" ||
                ed.editorState.savedAssetId !== "up_abc" || ed.editorState.saveError !== null) {
                console.error("FAIL: save success state");
                process.exit(1);
            }
            var body = calls[0].body;
            if (calls[0].url !== "/api/v1/structure-assets") {
                console.error("FAIL: POST url");
                process.exit(1);
            }
            if (body.parent_job_id !== "job1" || body.parent_entry_id !== "e1") {
                console.error("FAIL: parent ids in body");
                process.exit(1);
            }
            if (body.edit_operations.length !== 3 ||
                body.edit_operations[2].type !== "dihedral") {
                console.error("FAIL: edit_operations list");
                process.exit(1);
            }
            if (!body.provenance || body.provenance.op_count !== 3 ||
                body.provenance.comment !== lines[1]) {
                console.error("FAIL: provenance dict");
                process.exit(1);
            }
            if (body.xyz_text.split("\\n")[1] !== lines[1]) {
                console.error("FAIL: xyz_text carries provenance");
                process.exit(1);
            }
            if (body.name.indexOf("\\u5df2\\u7f16\\u8f91") < 0) {
                console.error("FAIL: default name uses edited suffix");
                process.exit(1);
            }

            // failure path: keeps edits + error state
            ed._fetchImpl = function () {
                return Promise.resolve({
                    ok: false, status: 500, statusText: "Server Error",
                    json: function () { return Promise.resolve({}); }
                });
            };
            return ed.saveEditedAsset();
        }).then(function (failRes) {
            if (failRes.ok || !ed.editorState.saveError ||
                ed.editorState.transactions.length !== 3) {
                console.error("FAIL: failure keeps edits + error");
                process.exit(1);
            }

            // requestSaveEditedAsset: success completes the pending switch
            ed.editorState.pendingSwitch = {
                targetEntryId: "e2",
                targetSymbols: symbols,
                targetCoords: coords
            };
            ed._fetchImpl = function (url, opts) {
                return Promise.resolve({
                    ok: true, status: 201, statusText: "Created",
                    json: function () { return Promise.resolve({ asset_id: "up_def", ok: true }); }
                });
            };
            return ed.requestSaveEditedAsset();
        }).then(function (res) {
            if (!res.ok || res.assetId !== "up_def") {
                console.error("FAIL: requestSave success");
                process.exit(1);
            }
            if (ed.editorState.entryId !== "e2" ||
                ed.editorState.transactions.length !== 0 ||
                ed.editorState.pendingSwitch !== null) {
                console.error("FAIL: pending switch completed after save");
                process.exit(1);
            }

            // prefill: bridge receives the xyz; NEVER submits
            var prefilled = null;
            window._svPrefillNewCalculation = function (text) { prefilled = text; };
            ed.editorState.entryId = "e9";
            var pf = ed.prefillNewCalculation();
            if (!pf.ok || prefilled == null ||
                prefilled.split("\\n")[0] !== "3") {
                console.error("FAIL: prefill bridge call");
                process.exit(1);
            }
            delete window._svPrefillNewCalculation;
            var noBridge = ed.prefillNewCalculation();
            if (noBridge.ok || noBridge.xyz == null) {
                console.error("FAIL: prefill without bridge");
                process.exit(1);
            }
            console.log("PASS");
        }).catch(function (e) {
            console.error("FAIL: unexpected", e);
            process.exit(1);
        });
    """).replace("EDITOR_PATH", json.dumps(str(_EDITOR_JS_PATH)))

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"Node asset-save test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


# ── Chemistry-correctness suite (plan todo 44) ──
# doc §11 chemistry rows: internal-coordinate targets (1e-4 A / 1e-3 deg),
# never-moved rigidity (<=1e-6 A RMSD), +/-180 shortest path, disconnected
# rejection, mode/geometry binding, cross-language TS gate consistency.


def test_chemistry_editor_reference_scenario() -> None:
    """Reference-fixture end-to-end editor sequence (ethanol-like C-C-O-H
    chain): bond 1.432->1.25 A (<=1e-4), angle ->120 deg (<=1e-3), dihedral
    ->-60 deg (<=1e-3); never-moved atoms rigid across the WHOLE sequence
    (RMSD <=1e-6 A, reference-identical rows); atom ordering unchanged."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    script = textwrap.dedent("""\
        var window = { fetch: null };
        require(EDITOR_PATH);
        var ed = window.ACPStructureEditor;

        function dist(a, b) {
          var dx = a[0]-b[0], dy = a[1]-b[1], dz = a[2]-b[2];
          return Math.sqrt(dx*dx + dy*dy + dz*dz);
        }
        function rmsd(rowsA, rowsB) {
          var s = 0;
          for (var i = 0; i < rowsA.length; i++) {
            var d = dist(rowsA[i], rowsB[i]);
            s += d * d;
          }
          return Math.sqrt(s / rowsA.length);
        }

        // ethanol-like chain: C0-C1-O2-H3 with H4/H5 on C0
        // (substituents placed beyond covalent cutoffs of foreign centers)
        var symbols = ["C", "C", "O", "H", "H", "H"];
        var coords = [
          [0.00, 0.00, 0.00],
          [1.52, 0.00, 0.00],
          [2.92, 0.30, 0.00],
          [3.22, 1.21, 0.00],
          [-0.38, 1.02, 0.00],
          [-0.38, -1.02, 0.00]
        ];
        var graph = ed.buildAdjacency(symbols, coords);
        if (!graph || !graph.edges || graph.edges.length !== 5) {
          console.error("FAIL: inferred graph edges = " +
            (graph && graph.edges ? graph.edges.length : "none"));
          process.exit(1);
        }
        var edges = graph.edges;
        var original = coords.map(function (r) { return r; });
        var byRef = coords.slice();

        // step 1: bond C1-O2 1.432 -> 1.25, move side B (O2+H3)
        var r1 = ed.editBondLength(symbols, coords, edges, 1, 2, 1.25, "B");
        if (!r1.ok) { console.error("FAIL: bond reject " + r1.reason); process.exit(1); }
        if (Math.abs(dist(r1.coords[1], r1.coords[2]) - 1.25) > 1e-4) {
          console.error("FAIL: bond target"); process.exit(1);
        }
        // step 2: angle C1-O2-H3 -> 120 deg (C side = H3 rotates about O2)
        var angleBefore = ed.angleDeg(r1.coords[1], r1.coords[2], r1.coords[3]);
        var r2 = ed.editBondAngle(symbols, r1.coords, edges, 1, 2, 3, 120.0, null);
        if (!r2.ok) { console.error("FAIL: angle reject " + r2.reason); process.exit(1); }
        if (Math.abs(ed.angleDeg(r2.coords[1], r2.coords[2], r2.coords[3]) - 120.0) > 1e-3) {
          console.error("FAIL: angle target (was " + angleBefore.toFixed(3) + ")");
          process.exit(1);
        }
        // step 3: dihedral H4-C0-C1-O2 -> -60 deg (C side {1,2,3} about C0-C1)
        var r3 = ed.editDihedral(symbols, r2.coords, edges, 4, 0, 1, 2, -60.0, null);
        if (!r3.ok) { console.error("FAIL: dihedral reject " + r3.reason); process.exit(1); }
        var dAfter = ed.dihedralDeg(r3.coords[4], r3.coords[0], r3.coords[1], r3.coords[2]);
        if (Math.abs(dAfter - (-60.0)) > 1e-3) {
          console.error("FAIL: dihedral target " + dAfter); process.exit(1);
        }

        // cumulative rigidity: atoms 4,5 were NEVER in any moved set
        // (bond moved {2,3}; angle moved {3}; dihedral moved {1,2,3})
        if (rmsd([original[4], original[5]], [r3.coords[4], r3.coords[5]]) > 1e-6) {
          console.error("FAIL: never-moved rigidity"); process.exit(1);
        }
        if (r3.coords[4] !== byRef[4] || r3.coords[5] !== byRef[5]) {
          console.error("FAIL: never-moved rows must be reference-identical");
          process.exit(1);
        }
        // atom ordering unchanged end-to-end
        if (r3.coords.length !== symbols.length) {
          console.error("FAIL: atom count"); process.exit(1);
        }
        if (r3.symbols && r3.symbols.join(",") !== symbols.join(",")) {
          console.error("FAIL: symbol order"); process.exit(1);
        }
        console.log("PASS");
    """).replace("EDITOR_PATH", json.dumps(str(_EDITOR_JS_PATH)))

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"Node chemistry scenario failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_chemistry_dihedral_shortest_path_rerun() -> None:
    """+-180 wrap takes the SHORTEST rotation (todo-35 physics, re-asserted
    for the consolidated suite): 170 -> -170 rotates 20 deg, so a marker at
    radius r from the axis moves the 20-deg chord 2r*sin(10 deg), not the
    340-deg chord 2r*sin(170 deg)."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    script = textwrap.dedent("""\
        var window = { fetch: null };
        require(EDITOR_PATH);
        var ed = window.ACPStructureEditor;
        function dist(a, b) {
          var dx = a[0]-b[0], dy = a[1]-b[1], dz = a[2]-b[2];
          return Math.sqrt(dx*dx + dy*dy + dz*dz);
        }

        // axis B->C along +x (todo-35 convention); w-arm at +170 deg from +y
        var rad = Math.PI / 180;
        var theta = 170 * rad;
        var arm = [0, Math.cos(theta), Math.sin(theta)];
        var symbols = ["C", "C", "C", "C", "H"];
        var coords = [
          [0.0, 1.0, 0.0],                              // A (v = +y)
          [0.0, 0.0, 0.0],                              // B
          [1.5, 0.0, 0.0],                              // C (axis +x)
          [1.5 + arm[0], arm[1], arm[2]],               // D at 170 deg
          [1.5 + arm[0] * 1.6, arm[1] * 1.6, arm[2] * 1.6]  // marker, r=1.6
        ];
        var edges = [{ a: 0, b: 1 }, { a: 1, b: 2 }, { a: 2, b: 3 }, { a: 3, b: 4 }];
        var before = coords.map(function (r) { return r.slice(); });
        var d0 = ed.dihedralDeg(coords[0], coords[1], coords[2], coords[3]);
        if (Math.abs(d0 - 170) > 1e-9) {
          console.error("FAIL: fixture dihedral " + d0); process.exit(1);
        }

        var res = ed.editDihedral(symbols, coords, edges, 0, 1, 2, 3, -170, null);
        if (!res.ok) { console.error("FAIL: reject " + res.reason); process.exit(1); }
        var d = ed.dihedralDeg(res.coords[0], res.coords[1], res.coords[2], res.coords[3]);
        if (Math.abs(d - (-170)) > 1e-3) {
          console.error("FAIL: target " + d); process.exit(1);
        }
        // radius of the marker from the rotation axis (y-z cylinder about +x)
        var r = Math.sqrt(before[4][1] * before[4][1] + before[4][2] * before[4][2]);
        if (Math.abs(r - 1.6) > 1e-9) {
          console.error("FAIL: marker radius " + r); process.exit(1);
        }
        var chord = dist(before[4], res.coords[4]);
        var shortChord = 2 * r * Math.sin(10 * Math.PI / 180);
        if (Math.abs(chord - shortChord) > 1e-6) {
          console.error("FAIL: not shortest path chord=" + chord +
                        " expected=" + shortChord);
          process.exit(1);
        }
        // chord alone cannot encode rotation direction (a 340-deg sweep has
        // the same start->end chord), so assert the SIGNED angular step of
        // the moving arm directly: exactly 20 deg, not 340 deg
        function armAngle(row) { return Math.atan2(row[2], row[1]) * 180 / Math.PI; }
        var delta = armAngle(res.coords[4]) - armAngle(before[4]);
        delta = ((delta % 360) + 360) % 360;
        if (delta > 180) delta = 360 - delta;
        if (Math.abs(delta - 20.0) > 1e-6) {
          console.error("FAIL: rotation step " + delta + " deg (want 20, not 340)");
          process.exit(1);
        }
        console.log("PASS");
    """).replace("EDITOR_PATH", json.dumps(str(_EDITOR_JS_PATH)))

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"Node shortest-path rerun failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_chemistry_disconnected_sequence_rejection() -> None:
    """Disconnected internal-coordinate sequences are rejected: angle edit
    with NO A-B edge and dihedral edit with a disconnected D both return
    reason 'no_bond' (ring rejection is already covered by todos 33-35)."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    script = textwrap.dedent("""\
        var window = { fetch: null };
        require(EDITOR_PATH);
        var ed = window.ACPStructureEditor;

        var symbols = ["H", "O", "H", "C", "C"];
        var coords = [
          [0.96, 0.00, 0.00],
          [0.00, 0.00, 0.00],
          [-0.24, 0.93, 0.00],
          [4.00, 0.00, 0.00],
          [5.52, 0.00, 0.00]
        ];
        // explicit edges: O-H x2 and C-C only — H0...C3 fully disconnected
        var edges = [
          { a: 0, b: 1 }, { a: 1, b: 2 }, { a: 3, b: 4 }
        ];

        var ra = ed.editBondAngle(symbols, coords, edges, 0, 1, 3, 120.0, null);
        if (ra.ok || ra.reason !== "no_bond") {
          console.error("FAIL: angle disconnected: " + JSON.stringify(ra));
          process.exit(1);
        }
        var rd = ed.editDihedral(symbols, coords, edges, 0, 1, 2, 3, 60.0, null);
        if (rd.ok || rd.reason !== "no_bond") {
          console.error("FAIL: dihedral disconnected D: " + JSON.stringify(rd));
          process.exit(1);
        }
        var rb = ed.editBondLength(symbols, coords, edges, 0, 3, 1.30, null);
        if (rb.ok || rb.reason !== "no_bond") {
          console.error("FAIL: bond disconnected: " + JSON.stringify(rb));
          process.exit(1);
        }
        console.log("PASS");
    """).replace("EDITOR_PATH", json.dumps(str(_EDITOR_JS_PATH)))

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"Node disconnected-rejection failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_chemistry_mode_vectors_never_mixed_node() -> None:
    """Mode vectors never mix across geometries: the todo-31
    geometryMismatch gate flags a cross-product pair (entry A x modes B)
    and passes the same-product pair; null product ids stay allowed
    (historical projection)."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    script = textwrap.dedent("""\
        var window = { fetch: null };
        require(VIB_PATH);
        var vib = window.ACPVibrationViewer;

        var entryA = { source: { product_id: "batch_item_001" } };
        var entryB = { source: { product_id: "batch_item_002" } };
        var entryNull = { source: {} };

        var modesA = {
          available: true,
          geometry_product_id: "batch_item_001",
          modes: [{ mode_index: 6, frequency_cm1: -800.0, imaginary: true,
                    vectors: [[0.11, 0.0, 0.0]] }]
        };
        var modesB = {
          available: true,
          geometry_product_id: "batch_item_002",
          modes: [{ mode_index: 6, frequency_cm1: -650.0, imaginary: true,
                    vectors: [[0.07, 0.0, 0.0]] }]
        };

        if (vib.geometryMismatch(entryA, modesA) !== false) {
          console.error("FAIL: same-product pair must pass"); process.exit(1);
        }
        if (vib.geometryMismatch(entryA, modesB) !== true) {
          console.error("FAIL: cross-product pair must be flagged"); process.exit(1);
        }
        if (vib.geometryMismatch(entryB, modesB) !== false) {
          console.error("FAIL: B/B pair must pass"); process.exit(1);
        }
        if (vib.geometryMismatch(entryNull, modesA) !== false) {
          console.error("FAIL: null entry pid must stay allowed"); process.exit(1);
        }
        if (vib.geometryMismatch(entryA, { geometry_product_id: null }) !== false) {
          console.error("FAIL: null data pid must stay allowed"); process.exit(1);
        }
        console.log("PASS");
    """).replace("VIB_PATH", json.dumps(str(_VIB_JS)))

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"Node mode-binding test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout
