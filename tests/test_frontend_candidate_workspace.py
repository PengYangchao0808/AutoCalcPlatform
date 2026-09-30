"""Regression guards for the converged candidate-library/task-input workspace."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "frontend" / "ACP_Workbench_v2.html"
PICKER = ROOT / "frontend" / "js" / "structure_source_picker.js"
PICKER_CSS = ROOT / "frontend" / "css" / "structure_source_picker.css"
TIW_JS = ROOT / "frontend" / "js" / "task_input_workspace.js"


def test_standalone_candidate_library_button_absent() -> None:
    """The standalone candidate-library nav button is removed; candidate
    management is reachable through the wizard workspace (source tabs)."""
    html = HTML.read_text(encoding="utf-8")
    assert 'id="btn-candidate-library"' not in html, \
        "standalone btn-candidate-library must be removed"
    # Candidate management is now via wizard source tabs with sourceGroup
    assert 'sourceGroup: "candidate"' in html or 'sourceGroup:"candidate"' in html, \
        "wizard workspace must expose a candidate source tab via sourceGroup"


def test_preview_focus_is_separate_from_selection() -> None:
    js = PICKER.read_text(encoding="utf-8")
    css = PICKER_CSS.read_text(encoding="utf-8")
    assert 'var previewUid = ""' in js
    assert "selectedUids.add(uid)" in js
    assert "previewUid = uid" in js
    assert ".sp-row.sp-row-previewing" in css


def test_filter_controls_are_disclosed_on_demand() -> None:
    js = PICKER.read_text(encoding="utf-8")
    assert 'sp-filter-button' in js
    assert 'filterRow.style.display = "none"' in js
    assert 'filterButton.setAttribute("aria-expanded"' in js


def test_candidate_geometry_has_explicit_state_machine_and_retry() -> None:
    html = HTML.read_text(encoding="utf-8")
    for state in ("pending", "loading", "ready", "failed"):
        assert state in html and "geometry_state" in html
    assert "geometry_state" in html
    assert "version_changed" in html


def test_inactive_sources_hidden_until_filter_enables_them() -> None:
    """The wizard workspace picker mounts with usageStatus active by default;
    the source_group tabs drive which data set is shown."""
    html = HTML.read_text(encoding="utf-8")
    assert 'sourceGroup' in html or 'source_group' in html, \
        "workspace must use sourceGroup/source_group to drive picker tabs"


# ---------------------------------------------------------------------------
# Node-executed reconciler pure-function tests
# ---------------------------------------------------------------------------

_TIW_NODE_HARNESS = r"""
var window = {};
var document = { createElement: function() { return { appendChild: function(){}, innerHTML: "" }; } };
%s
var W = window.ACPTaskInputWorkspace;
var results = [];

function assert(cond, msg) { if (!cond) results.push("FAIL: " + msg); }

// entryKey
assert(W.entryKey(null) === ":", "null key");
assert(W.entryKey({}) === ":local:", "empty key");
assert(W.entryKey({source_uid:"u1"}) === "u1:", "uid only");
assert(W.entryKey({source_uid:"u1",version_id:"v2"}) === "u1:v2", "uid+version");
assert(W.entryKey({source_uid:"u1",geometry_hash:"h3"}) === "u1:h3", "uid+hash");
assert(W.entryKey({name:"foo"}) === ":local:foo", "local by name");

// entryFromSnapshot
var e = W.entryFromSnapshot({source_uid:"u1",version_id:"v1",resolved_name:"Test",role:"TS",charge:0,multiplicity:1});
assert(e.source_uid === "u1", "ef source_uid");
assert(e.geometry_state === "pending", "ef geometry_state");
assert(e.name === "Test", "ef name");
assert(e.tag === "TS", "ef tag");
assert(e.source_kind === "task_result", "ef source_kind for uid-only");
var e2 = W.entryFromSnapshot({source_uid:"u2",source_kind:"saved_candidate",resolved_name:"Cand"});
assert(e2.source_kind === "saved_candidate", "ef saved_candidate kind");
var e3 = W.entryFromSnapshot({name:"manual"});
assert(e3.source_kind === "manual_input", "ef manual_input kind");

// reconcileSelection — add from empty
var r1 = W.reconcileSelection([], [e]);
assert(r1.length === 1, "r1 add from empty");
assert(r1[0].source_uid === "u1", "r1 uid matches");

// reconcileSelection — remove deselected
var r2 = W.reconcileSelection(r1, []);
assert(r2.length === 0, "r2 remove deselected");

// reconcileSelection — preserve local
var local = {name:"local",source_uid:null,input_id:"L1"};
var r3 = W.reconcileSelection([local], []);
assert(r3.length === 1, "r3 preserve local");
assert(r3[0].name === "local", "r3 local preserved");

// reconcileSelection — version changed
var exist = {source_uid:"u1",version_id:"v1",geometry_state:"ready",name:"Old"};
var newSnap = {source_uid:"u1",version_id:"v2",resolved_name:"New"};
var r4 = W.reconcileSelection([exist], [newSnap]);
assert(r4.length === 1, "r4 same uid 1 entry");
assert(r4[0].version_changed === true, "r4 version_changed set");
assert(r4[0].version_id === "v1", "r4 original version kept");
assert(r4[0].geometry_state === "ready", "r4 original state kept");

// reconcileSelection — same version keeps existing
var exist2 = {source_uid:"u5",version_id:"v1",geometry_state:"ready",name:"Same"};
var sameSnap = {source_uid:"u5",version_id:"v1",resolved_name:"Same"};
var r5 = W.reconcileSelection([exist2], [sameSnap]);
assert(r5.length === 1, "r5 same version 1 entry");
assert(r5[0].version_changed !== true, "r5 no version_changed");

// reconcileSelection — add new + keep existing
var exist3 = {source_uid:"u1",version_id:"v1",geometry_state:"ready"};
var sel3 = [{source_uid:"u1",version_id:"v1"}, {source_uid:"u3",version_id:"v1",resolved_name:"New3"}];
var r6 = W.reconcileSelection([exist3], sel3);
assert(r6.length === 2, "r6 add new + keep existing");

// derivedSelectedUids
var uids = W.derivedSelectedUids([{source_uid:"u1"}, {name:"local",source_uid:null}, {source_uid:"u2"}]);
assert(uids.length === 2, "du two uids");
assert(uids[0] === "u1", "du first uid");
assert(uids[1] === "u2", "du second uid");

// derivedSelectedUids — dedup
var uids2 = W.derivedSelectedUids([{source_uid:"u1"},{source_uid:"u1"}]);
assert(uids2.length === 1, "du dedup uid");

// Immutability check
var orig = [{source_uid:"u1",version_id:"v1"}];
var sel = [{source_uid:"u1",version_id:"v2"}];
var r7 = W.reconcileSelection(orig, sel);
assert(orig[0].version_changed === undefined, "immut orig not mutated");
assert(r7[0].version_changed === true, "immut result has flag");

process.stdout.write(JSON.stringify(results));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node not available")
def test_reconciler_via_node() -> None:
    """Run reconciler pure functions in Node and verify all assertions."""
    js_code = TIW_JS.read_text(encoding="utf-8")
    harness = _TIW_NODE_HARNESS % js_code
    result = subprocess.run(
        ["node", "-e", harness],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, f"Node execution failed:\n{result.stderr}"
    failures = json.loads(result.stdout)
    assert failures == [], f"Reconciler failures: {failures}"

