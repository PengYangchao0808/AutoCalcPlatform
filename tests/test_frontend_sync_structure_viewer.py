"""Frontend sync page tests — structure_viewer area.

Split from tests/test_frontend_sync.py (T29, 2026-10-18). Every test keeps
its original name and body; the old -> new mapping is
tests/test_frontend_sync.py::<name> -> tests/test_frontend_sync_structure_viewer.py
::<name>. Shared helpers live in tests/_frontend_sync_shared.py.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import textwrap

import pytest

from tests._frontend_sync_shared import (
    _EN_BLOCK_RE,
    _SV_JS_PATH,
    _ZH_BLOCK_RE,
    FRONTEND_CSS_DIR,
    FRONTEND_JS_DIR,
    REPO_ROOT,
    _div_span,
    _extract_structure_keys,
    read_workbench_content,
)


def test_structure_viewer_js_has_namespace() -> None:
    js = (FRONTEND_JS_DIR / "structure_viewer.js").read_text(encoding="utf-8")
    assert "window.ACPStructureViewer" in js
    assert "loadStructureViewer" in js
    assert "selectEntry" in js
    assert "refreshIfChanged" in js
    assert "loadSelectedGeometry" in js
    assert "_applyCatalogResponse" in js


# ---------------------------------------------------------------------------
# Todo 15: structureViewerState store + payload fetch + revision/token
# ---------------------------------------------------------------------------


def test_structure_viewer_state_store_contract() -> None:
    """Contract: state fields, AbortController, requestToken, _fetchImpl exist."""
    js = (FRONTEND_JS_DIR / "structure_viewer.js").read_text(encoding="utf-8")

    for field in (
        "jobId", "payload", "revision", "selectedEntryId",
        "selectionOrigin", "selectionToken", "dirty", "editState",
        "requestToken", "availability", "newerAvailable",
        "pendingFetchInFlight", "pendingFetchNextRetryAt",
    ):
        assert field in js, f"state field {field!r} missing from structure_viewer.js"

    assert "AbortController" in js, "AbortController usage missing"
    assert "requestToken" in js, "requestToken guard missing"
    assert "_fetchImpl" in js, "_fetchImpl injectable missing"
    assert "_applyCatalogResponse" in js, "_applyCatalogResponse pure helper missing"
    assert "_catalogUrl" in js, "_catalogUrl helper missing"
    assert "/structure-viewer" in js, "catalog endpoint path missing"
    assert "selectionToken" in js, "selectionToken guard missing"


def test_structure_viewer_node_logic_stale_response_discarded() -> None:
    """Node logic: job A slow resolves after job B -> B wins."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    js_path = FRONTEND_JS_DIR / "structure_viewer.js"
    script = textwrap.dedent("""\
        var window = { fetch: null };
        var AbortController = class { constructor() { this.signal = null; } abort() {} };
        // Load the module (IIFE reads window, attaches namespace)
        require(JS_PATH);

        var ns = window.ACPStructureViewer;
        var state = ns.state;

        // Inject a fake fetch that returns controlled promises.
        var resolveA, resolveB;
        ns._fetchImpl = function(url) {
            if (url.indexOf("jobA") >= 0) {
                return new Promise(function(r) { resolveA = r; });
            }
            return new Promise(function(r) { resolveB = r; });
        };

        // Start load A, then load B (B supersedes A).
        ns.loadStructureViewer("jobA");
        var tokenAfterA = state.requestToken;
        ns.loadStructureViewer("jobB");
        var tokenAfterB = state.requestToken;

        // B's token is newer.
        if (tokenAfterB <= tokenAfterA) {
            console.error("FAIL: requestToken must increase");
            process.exit(1);
        }

        // Resolve B first (as it should in real usage).
        resolveB({ ok: true, status: 200, statusText: "OK", json: function() {
            return Promise.resolve({
                schema_version: "structure_viewer_v1",
                revision: "revB",
                availability: "ready",
                default_entry_id: "e2",
                groups: [], entries: [], warnings: []
            });
        }});

        // Now resolve A (stale — should be discarded).
        resolveA({ ok: true, status: 200, statusText: "OK", json: function() {
            return Promise.resolve({
                schema_version: "structure_viewer_v1",
                revision: "revA",
                availability: "ready",
                default_entry_id: "e1",
                groups: [], entries: [], warnings: []
            });
        }});

        // Give promises time to settle.
        setTimeout(function() {
            if (state.revision !== "revB") {
                console.error("FAIL: expected revB, got " + state.revision);
                process.exit(1);
            }
            if (state.selectedEntryId !== "e2") {
                console.error("FAIL: expected e2, got " + state.selectedEntryId);
                process.exit(1);
            }
            console.log("PASS");
        }, 50);
    """).replace("JS_PATH", json.dumps(str(js_path)))

    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, (
        f"Node logic test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_structure_viewer_remote_catalog_auto_fetch_retry() -> None:
    """Pure-remote pending catalogs are retried once with ``fetch=1``."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    js_path = FRONTEND_JS_DIR / "structure_viewer.js"
    script = textwrap.dedent("""\
        var window = { fetch: null };
        var AbortController = class { constructor() { this.signal = null; } abort() {} };
        require(JS_PATH);

        var ns = window.ACPStructureViewer;
        var urls = [];
        ns._fetchImpl = function(url) {
            urls.push(url);
            var pending = url.indexOf("fetch=1") < 0;
            return Promise.resolve({
                ok: true, status: 200, statusText: "OK",
                json: function() {
                    return Promise.resolve({
                        schema_version: "structure_viewer_v1",
                        revision: pending ? "empty" : "remote-rev",
                        availability: pending ? "pending_fetch" : "ready",
                        job_status: "completed",
                        default_entry_id: pending ? null : "conf_0001",
                        groups: [],
                        entries: pending ? [] : [{ id: "conf_0001" }],
                        warnings: pending ? ["pending_fetch"] : []
                    });
                }
            });
        };

        ns.loadStructureViewer("remote-job", { itemId: "item 1" }).then(function() {
            if (urls.length !== 2) {
                console.error("FAIL: expected 2 catalog calls, got " + urls.length);
                process.exit(1);
            }
            if (urls[1].indexOf("item_id=item%201&fetch=1") < 0) {
                console.error("FAIL: retry URL missing item_id/fetch=1: " + urls[1]);
                process.exit(1);
            }
            if (ns.state.availability !== "ready" || ns.state.revision !== "remote-rev") {
                console.error("FAIL: fetched catalog was not applied");
                process.exit(1);
            }
            console.log("PASS");
        }).catch(function(e) {
            console.error("FAIL: unexpected error", e);
            process.exit(1);
        });
    """).replace("JS_PATH", json.dumps(str(js_path)))

    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, (
        f"Node remote-catalog retry failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_structure_viewer_pending_retry_source_contract() -> None:
    """Contract: retry-ownership symbols exist, are exported, and the backoff
    clear sits after the _applyCatalogResponse token early-return."""
    js = (FRONTEND_JS_DIR / "structure_viewer.js").read_text(encoding="utf-8")

    assert "function _retryPendingFetch(jobId)" in js
    assert "function refreshPending(jobId, opts)" in js
    assert "refreshPending: refreshPending," in js
    assert "_retryPendingFetch: _retryPendingFetch," in js
    assert "pendingFetchInFlight" in js
    assert "pendingFetchNextRetryAt" in js

    early_idx = js.index("if (capturedToken !== state.requestToken)")
    clear_idx = js.index("state.pendingFetchNextRetryAt = 0;")
    payload_idx = js.index("state.payload = data;")
    assert early_idx < clear_idx < payload_idx, (
        "_applyCatalogResponse must clear the retry backoff only AFTER the "
        "token early-return (stale responses must never clear it)"
    )


def test_structure_viewer_refresh_pending_guards_and_retry() -> None:
    """(i) refreshPending: pending+terminal same job retries once via fetch=1;
    ready / different job / non-terminal -> zero calls."""
    _run_sv_node(
        """\
        var urls = [];
        ns._fetchImpl = function (url) {
            urls.push(url);
            var fetchRemote = url.indexOf("fetch=1") >= 0;
            return Promise.resolve(okResp(catalogData({
                revision: fetchRemote ? "remote-rev" : "empty",
                availability: fetchRemote ? "ready" : "pending_fetch",
                job_status: "completed",
                default_entry_id: fetchRemote ? "conf_0001" : null,
                entries: fetchRemote ? [{ id: "conf_0001" }] : [],
                warnings: fetchRemote ? [] : ["pending_fetch"]
            })));
        };

        async function main() {
            state.jobId = "jobP";
            ns._applyCatalogResponse(state, state.requestToken, catalogData({
                availability: "pending_fetch", job_status: "completed",
                revision: "seed", warnings: ["pending_fetch"]
            }));
            urls.length = 0;

            await ns.refreshPending("jobP");
            if (urls.length !== 2) fail("expected 2 catalog calls, got " + urls.length);
            if (urls[1].indexOf("fetch=1") < 0) fail("retry URL missing fetch=1: " + urls[1]);
            if (state.availability !== "ready") fail("expected ready, got " + state.availability);

            urls.length = 0;
            await ns.refreshPending("jobP");
            if (urls.length !== 0) fail("ready state must not retry, got " + urls.length);

            ns._applyCatalogResponse(state, state.requestToken, catalogData({
                availability: "pending_fetch", job_status: "completed",
                revision: "seed2", warnings: ["pending_fetch"]
            }));
            await ns.refreshPending("otherJob");
            if (urls.length !== 0) fail("different jobId must not retry, got " + urls.length);

            ns._applyCatalogResponse(state, state.requestToken, catalogData({
                availability: "pending_fetch", job_status: "running",
                revision: "seed3", warnings: ["pending_fetch"]
            }));
            await ns.refreshPending("jobP");
            if (urls.length !== 0) fail("non-terminal job must not retry, got " + urls.length);
            console.log("PASS");
        }
        main().catch(function (e) { console.error("FAIL: unexpected error", e); process.exit(1); });
        """
    )


def test_structure_viewer_pending_retry_state_ownership() -> None:
    """(ii) pendingFetchInFlight is set synchronously (same-job reload must NOT
    clear it) and blocks duplicate retries in the same window."""
    _run_sv_node(
        """\
        var urls = [];
        var resolvers = [];
        ns._fetchImpl = function (url) {
            urls.push(url);
            return new Promise(function (resolve) {
                resolvers.push(function () {
                    var fetchRemote = url.indexOf("fetch=1") >= 0;
                    resolve(okResp(catalogData({
                        revision: fetchRemote ? "remote-rev" : "empty",
                        availability: fetchRemote ? "ready" : "pending_fetch",
                        job_status: "completed",
                        default_entry_id: fetchRemote ? "c1" : null,
                        entries: fetchRemote ? [{ id: "c1" }] : [],
                        warnings: fetchRemote ? [] : ["pending_fetch"]
                    })));
                });
            });
        };

        async function main() {
            state.jobId = "jobS";
            ns._applyCatalogResponse(state, state.requestToken, catalogData({
                availability: "pending_fetch", job_status: "completed",
                revision: "seed", warnings: ["pending_fetch"]
            }));
            urls.length = 0;

            var p1 = ns.refreshPending("jobS");
            if (state.pendingFetchInFlight !== true) {
                fail("pendingFetchInFlight must be true synchronously after refreshPending");
            }
            var p2 = ns.refreshPending("jobS");
            if (urls.length !== 1) fail("in-flight window must not add calls, got " + urls.length);

            while (resolvers.length) {
                resolvers.shift()();
                await immediate();
            }
            await p1;
            await p2;
            if (urls.length !== 2) fail("expected exactly 2 catalog calls, got " + urls.length);
            if (state.pendingFetchInFlight !== false) fail("settled retry must clear in-flight");
            if (state.availability !== "ready") fail("expected ready, got " + state.availability);
            console.log("PASS");
        }
        main().catch(function (e) { console.error("FAIL: unexpected error", e); process.exit(1); });
        """
    )


def test_structure_viewer_refresh_if_changed_terminal_backoff() -> None:
    """(iii) refreshIfChanged owns retries: first pending+terminal delegates
    (fetch=1); a poll within 30 s adds none (backoff survives the reload);
    a later ready response zeroes pendingFetchNextRetryAt."""
    _run_sv_node(
        """\
        var fetch1Count = 0;
        var fetch1Pending = true;
        var pollRevision = "r-0";
        var pollAvailability = "pending_fetch";
        ns._fetchImpl = function (url) {
            var fetchRemote = url.indexOf("fetch=1") >= 0;
            if (fetchRemote) {
                fetch1Count++;
                return Promise.resolve(okResp(catalogData({
                    revision: "r-auto",
                    availability: fetch1Pending ? "pending_fetch" : "ready",
                    job_status: "completed",
                    warnings: fetch1Pending ? ["pending_fetch"] : []
                })));
            }
            return Promise.resolve(okResp(catalogData({
                revision: pollRevision,
                availability: pollAvailability,
                job_status: "completed",
                warnings: pollAvailability === "ready" ? [] : ["pending_fetch"]
            })));
        };

        async function main() {
            state.jobId = "jobB";
            ns._applyCatalogResponse(state, state.requestToken, catalogData({
                availability: "pending_fetch", job_status: "completed",
                revision: "seed", warnings: ["pending_fetch"]
            }));

            await ns.refreshIfChanged();
            if (fetch1Count !== 1) fail("first terminal poll must delegate once, got " + fetch1Count);
            if (!(state.pendingFetchNextRetryAt > Date.now())) {
                fail("backoff must survive the retry reload");
            }

            pollRevision = "r-1";
            await ns.refreshIfChanged();
            if (fetch1Count !== 1) fail("poll within 30s must not add fetch=1, got " + fetch1Count);

            pollAvailability = "ready";
            pollRevision = "r-final";
            await ns.refreshIfChanged();
            if (state.pendingFetchNextRetryAt !== 0) fail("ready response must clear the backoff");
            if (state.availability !== "ready") fail("expected ready, got " + state.availability);
            console.log("PASS");
        }
        main().catch(function (e) { console.error("FAIL: unexpected error", e); process.exit(1); });
        """
    )


def test_structure_viewer_pending_retry_hard_failure_no_deadlock() -> None:
    """(iv) A rejected auto-fetch leaves availability empty; the NEXT
    refreshIfChanged with a fresh pending+terminal response still retries
    (no availability deadlock).  Backoff is reset explicitly to avoid 30s waits."""
    _run_sv_node(
        """\
        var fetch1Count = 0;
        var fetch1Reject = true;
        var pollRevision = "r-0";
        ns._fetchImpl = function (url) {
            var fetchRemote = url.indexOf("fetch=1") >= 0;
            if (fetchRemote) {
                fetch1Count++;
                if (fetch1Reject) return Promise.reject(new Error("network down"));
                return Promise.resolve(okResp(catalogData({
                    revision: "r-auto", availability: "ready", job_status: "completed"
                })));
            }
            return Promise.resolve(okResp(catalogData({
                revision: pollRevision, availability: "pending_fetch",
                job_status: "completed", warnings: ["pending_fetch"]
            })));
        };

        async function main() {
            state.jobId = "jobH";
            ns._applyCatalogResponse(state, state.requestToken, catalogData({
                availability: "pending_fetch", job_status: "completed",
                revision: "seed", warnings: ["pending_fetch"]
            }));

            await ns.refreshIfChanged();
            if (fetch1Count !== 1) fail("first retry must attempt fetch=1 once, got " + fetch1Count);
            if (state.availability !== "") {
                fail("hard failure must leave availability empty, got " + JSON.stringify(state.availability));
            }

            state.pendingFetchNextRetryAt = 0;
            fetch1Reject = false;
            pollRevision = "r-1";
            await ns.refreshIfChanged();
            if (fetch1Count !== 2) fail("hard failure must not deadlock retries, got " + fetch1Count);
            if (state.availability !== "ready") fail("retry must recover to ready, got " + state.availability);
            console.log("PASS");
        }
        main().catch(function (e) { console.error("FAIL: unexpected error", e); process.exit(1); });
        """
    )


def test_structure_viewer_refresh_pending_force_channel() -> None:
    """(v) SSE force channel: stale payload job_status=running blocks the
    unforced refreshPending but NOT the forced one."""
    _run_sv_node(
        """\
        var urls = [];
        ns._fetchImpl = function (url) {
            urls.push(url);
            var fetchRemote = url.indexOf("fetch=1") >= 0;
            return Promise.resolve(okResp(catalogData({
                revision: fetchRemote ? "remote-rev" : "empty",
                availability: fetchRemote ? "ready" : "pending_fetch",
                job_status: "completed",
                default_entry_id: fetchRemote ? "c1" : null,
                entries: fetchRemote ? [{ id: "c1" }] : [],
                warnings: fetchRemote ? [] : ["pending_fetch"]
            })));
        };

        async function main() {
            state.jobId = "jobF";
            ns._applyCatalogResponse(state, state.requestToken, catalogData({
                availability: "pending_fetch", job_status: "running",
                revision: "seed", warnings: ["pending_fetch"]
            }));
            urls.length = 0;

            await ns.refreshPending("jobF");
            if (urls.length !== 0) fail("unforced refresh must respect stale status guard, got " + urls.length);

            await ns.refreshPending("jobF", { force: true });
            if (urls.length !== 2) fail("force must trigger reload, got " + urls.length);
            if (urls[1].indexOf("fetch=1") < 0) fail("forced retry URL missing fetch=1: " + urls[1]);
            if (state.availability !== "ready") fail("forced retry must end ready, got " + state.availability);
            console.log("PASS");
        }
        main().catch(function (e) { console.error("FAIL: unexpected error", e); process.exit(1); });
        """
    )


def test_structure_viewer_refresh_if_changed_dirty_terminal_guard() -> None:
    """(vi) wasDirty: the terminal branch sets newerAvailable and does NOT
    delegate (fetch=1 count unchanged)."""
    _run_sv_node(
        """\
        var urls = [];
        ns._fetchImpl = function (url) {
            urls.push(url);
            return Promise.resolve(okResp(catalogData({
                revision: "r-next", availability: "pending_fetch",
                job_status: "completed", warnings: ["pending_fetch"]
            })));
        };

        async function main() {
            state.jobId = "jobD";
            ns._applyCatalogResponse(state, state.requestToken, catalogData({
                availability: "ready", job_status: "running", revision: "rev1"
            }));
            state.dirty = true;
            urls.length = 0;

            await ns.refreshIfChanged();
            for (var i = 0; i < urls.length; i++) {
                if (urls[i].indexOf("fetch=1") >= 0) fail("dirty guard must not delegate a fetch=1 retry");
            }
            if (state.newerAvailable !== true) fail("dirty guard must set newerAvailable");
            console.log("PASS");
        }
        main().catch(function (e) { console.error("FAIL: unexpected error", e); process.exit(1); });
        """
    )


def test_structure_viewer_pending_retry_job_switch_guard() -> None:
    """(vii) A stale job's finally must NOT clear the new job's
    pendingFetchInFlight."""
    _run_sv_node(
        """\
        var resolvers = [];
        ns._fetchImpl = function (url) {
            return new Promise(function (resolve) {
                resolvers.push({ url: url, resolve: resolve });
            });
        };

        async function main() {
            state.jobId = "oldJob";
            ns._applyCatalogResponse(state, state.requestToken, catalogData({
                availability: "pending_fetch", job_status: "completed",
                revision: "seed", warnings: ["pending_fetch"]
            }));

            var pOld = ns._retryPendingFetch("oldJob");
            if (state.pendingFetchInFlight !== true) fail("old retry must set in-flight synchronously");

            var pNewLoad = ns.loadStructureViewer("newJob");
            if (String(state.jobId) !== "newJob") fail("expected job switch to newJob");

            var pNew = ns.refreshPending("newJob", { force: true });
            if (state.pendingFetchInFlight !== true) fail("new retry must set in-flight");

            resolvers.shift().resolve(okResp(catalogData({
                availability: "pending_fetch", job_status: "completed",
                revision: "old", warnings: ["pending_fetch"]
            })));
            await pOld;
            if (state.pendingFetchInFlight !== true) {
                fail("old finally must not clear the new job's pendingFetchInFlight");
            }

            while (resolvers.length) {
                resolvers.shift().resolve(okResp(catalogData({
                    availability: "ready", job_status: "completed", revision: "x"
                })));
                await immediate();
            }
            await pNewLoad;
            await pNew;
            if (state.pendingFetchInFlight !== false) fail("settled retry must clear in-flight");
            console.log("PASS");
        }
        main().catch(function (e) { console.error("FAIL: unexpected error", e); process.exit(1); });
        """
    )


def test_structure_viewer_auto_fetch_unavailable_terminal() -> None:
    """(viii) availability=unavailable + terminal also auto-fetches with
    fetch=1 (the real endpoint only emits ready/pending_fetch — stubbed)."""
    _run_sv_node(
        """\
        var urls = [];
        ns._fetchImpl = function (url) {
            urls.push(url);
            var fetchRemote = url.indexOf("fetch=1") >= 0;
            return Promise.resolve(okResp(catalogData({
                revision: fetchRemote ? "remote-rev" : "empty",
                availability: fetchRemote ? "ready" : "unavailable",
                job_status: "completed",
                default_entry_id: fetchRemote ? "c1" : null,
                entries: fetchRemote ? [{ id: "c1" }] : [],
                warnings: fetchRemote ? [] : ["remote_missing"]
            })));
        };

        async function main() {
            await ns.loadStructureViewer("uJob");
            if (urls.length !== 2) fail("expected 2 catalog calls, got " + urls.length);
            if (urls[1].indexOf("fetch=1") < 0) fail("second URL must contain fetch=1: " + urls[1]);
            if (state.availability !== "ready") fail("expected ready, got " + state.availability);
            console.log("PASS");
        }
        main().catch(function (e) { console.error("FAIL: unexpected error", e); process.exit(1); });
        """
    )


def test_structure_viewer_stale_token_response_discarded_on_refresh() -> None:
    """(ix) A stale refreshIfChanged response is discarded entirely, and a
    stale ready response must never clear pendingFetchNextRetryAt."""
    _run_sv_node(
        """\
        var held = null;
        var mode = "immediate";
        var nextData = null;
        ns._fetchImpl = function (url) {
            if (mode === "hold") {
                mode = "immediate";
                return new Promise(function (resolve) { held = resolve; });
            }
            return Promise.resolve(okResp(nextData));
        };

        async function main() {
            nextData = catalogData({ availability: "ready", job_status: "completed", revision: "rev1" });
            await ns.loadStructureViewer("jobT");

            mode = "hold";
            var pStale = ns.refreshIfChanged();

            nextData = catalogData({ availability: "ready", job_status: "completed", revision: "rev2" });
            await ns.loadStructureViewer("jobT");
            if (state.revision !== "rev2") fail("expected rev2 applied, got " + state.revision);

            held(okResp(catalogData({ availability: "ready", job_status: "completed", revision: "rev9" })));
            await pStale;
            if (state.revision !== "rev2") fail("stale refreshIfChanged response must be discarded, got " + state.revision);
            if (state.newerAvailable !== false) fail("stale response must not set newerAvailable");

            state.pendingFetchNextRetryAt = 424242;
            var applied = ns._applyCatalogResponse(state, state.requestToken + 1,
                catalogData({ availability: "ready", job_status: "completed", revision: "stale" }));
            if (applied !== false) fail("stale _applyCatalogResponse must return false");
            if (state.pendingFetchNextRetryAt !== 424242) {
                fail("stale ready response must not clear the backoff");
            }
            ns._applyCatalogResponse(state, state.requestToken,
                catalogData({ availability: "ready", job_status: "completed", revision: "fresh" }));
            if (state.pendingFetchNextRetryAt !== 0) fail("fresh ready response must clear the backoff");
            console.log("PASS");
        }
        main().catch(function (e) { console.error("FAIL: unexpected error", e); process.exit(1); });
        """
    )


def test_structure_viewer_same_tick_terminal_race_regression() -> None:
    """(x) refreshIfChanged + SSE terminal event in one tick -> ends ready."""
    _run_sv_node(
        """\
        ns._fetchImpl = function (url) {
            var fetchRemote = url.indexOf("fetch=1") >= 0;
            return Promise.resolve(okResp(catalogData({
                revision: fetchRemote ? "remote-rev" : "empty",
                availability: fetchRemote ? "ready" : "pending_fetch",
                job_status: "completed",
                default_entry_id: fetchRemote ? "c1" : null,
                entries: fetchRemote ? [{ id: "c1" }] : [],
                warnings: fetchRemote ? [] : ["pending_fetch"]
            })));
        };

        async function main() {
            state.jobId = "raceJob";
            ns._applyCatalogResponse(state, state.requestToken, catalogData({
                availability: "pending_fetch", job_status: "completed",
                revision: "seed", warnings: ["pending_fetch"]
            }));

            var pPoll = ns.refreshIfChanged();
            var pSse = ns.refreshPending("raceJob", { force: true });
            await pPoll;
            await pSse;
            if (state.availability !== "ready") fail("same-tick race must end ready, got " + state.availability);
            console.log("PASS");
        }
        main().catch(function (e) { console.error("FAIL: unexpected error", e); process.exit(1); });
        """
    )


# Todo 5: v2 HTML wiring — SSE terminal instant refresh (force) + job.cancelled
# ---------------------------------------------------------------------------

def test_workbench_sse_terminal_refresh_wiring() -> None:
    """Contract: openSSE subscribes to job.cancelled, its terminal condition
    matches completed/failed/cancelled/done, and the terminal branch (selected
    job only) calls refreshPending with force — the SSE terminal event is the
    authoritative signal and must bypass the stale job_status guard."""
    html = read_workbench_content()

    sse = html.split("function openSSE(jobId)", 1)[1]

    # SSE types subscription list includes job.cancelled
    types_block = sse.split("var types = [", 1)[1].split("];", 1)[0]
    for evt in ('"job.completed"', '"job.failed"', '"job.cancelled"', '"done"'):
        assert evt in types_block, f"openSSE types must include {evt}"

    # Terminal condition matches completed/failed/cancelled/done
    terminal = sse.split("// Terminal events:", 1)[1].split(
        'updateSSEState("connected");', 1
    )[0]
    assert (
        'if (type === "job.completed" || type === "job.failed" '
        '|| type === "job.cancelled" || type === "done") {'
    ) in terminal, "terminal condition must match completed/failed/cancelled/done"

    # Forced refreshPending call inside the selected-job branch
    assert "window.ACPStructureViewer && window.ACPStructureViewer.refreshPending" in terminal
    assert "refreshPending(jid, { force: true })" in terminal
    sel_idx = terminal.index('String(selectedJobId || "") === jid')
    force_idx = terminal.index("refreshPending(jid, { force: true })")
    assert sel_idx < force_idx, (
        "refreshPending must run only for the selected job (inside the jid guard)"
    )

    # job.cancelled appears exactly at the two wiring sites (types + condition)
    assert html.count('"job.cancelled"') == 2


def test_summary_refresh_has_no_retry_hook() -> None:
    """Negative lock: refreshSelectedJobSummary must NOT call refreshPending —
    terminal retries are owned solely by refreshIfChanged (same-tick race)."""
    html = read_workbench_content()

    summary_body = html.split("async function refreshSelectedJobSummary()", 1)[1]
    summary_body = summary_body.split("\nfunction ", 1)[0]

    assert "refreshPending" not in summary_body, (
        "refreshSelectedJobSummary must not call refreshPending "
        "(terminal retry ownership belongs to refreshIfChanged alone)"
    )


def test_summary_poll_refreshifchanged_outside_drawer_gate() -> None:
    """Structural lock (R2 owner: client/polling-strategy.js lane 2). The
    selected-job summary fetch keeps its drawer-skip, while the periodic
    (every-2nd-tick) structure-viewer refresh sits OUTSIDE the drawer
    condition — proving the pending-fetch retry still covers the drawer-open
    path (and is not dragged behind the drawer condition)."""
    poll_src = (
        REPO_ROOT / "frontend" / "js" / "client" / "polling-strategy.js"
    ).read_text(encoding="utf-8")

    lane2 = poll_src.split("async function pollSummaryLane()", 1)[1]
    lane2 = lane2.split("async function pollLogLane", 1)[0]

    drawer_skip = "selectedId && !drawerOpen && handlers.pollSelectedSummary"
    gate = "tick % 2 === 0"
    assert drawer_skip in lane2, "lane 2 must keep the drawer-skip for the summary fetch"
    assert gate in lane2, "lane 2 must keep the every-2nd-tick (5s) gate"

    # The periodic structure-viewer dispatch lives inside the tick gate ...
    tick_branch = lane2.split(gate, 1)[1].split("} catch (e) {", 1)[0]
    assert "if (handlers.pollStructureViewer)" in tick_branch, (
        "pollStructureViewer dispatch must run inside the `tick % 2 === 0` branch"
    )
    # ... and the drawer state must NOT gate it (drawer only guards the summary fetch).
    assert "drawerOpen" not in tick_branch, (
        "the periodic structure-viewer refresh must stay outside the drawer condition"
    )
    assert lane2.index(drawer_skip) < lane2.index(gate), (
        "drawer-skip summary line must sit before the 5s gate"
    )


def test_structure_viewer_node_logic_dirty_guard() -> None:
    """Node logic: dirty=true -> refreshIfChanged sets newerAvailable, not payload."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    js_path = FRONTEND_JS_DIR / "structure_viewer.js"
    script = textwrap.dedent("""\
        var window = { fetch: null };
        var AbortController = class { constructor() { this.signal = null; } abort() {} };
        require(JS_PATH);

        var ns = window.ACPStructureViewer;
        var state = ns.state;

        // Seed state with a loaded payload.
        ns._fetchImpl = function(url) {
            return Promise.resolve({ ok: true, status: 200, statusText: "OK", json: function() {
                return Promise.resolve({
                    schema_version: "structure_viewer_v1",
                    revision: "rev1",
                    availability: "ready",
                    default_entry_id: "e1",
                    groups: [], entries: [{id:"e1", group_id:"g1"}], warnings: []
                });
            }});
        };

        ns.loadStructureViewer("jobX").then(function() {
            if (state.revision !== "rev1") {
                console.error("FAIL: initial load revision");
                process.exit(1);
            }
            // Mark dirty (simulating Wave 6 edits).
            state.dirty = true;

            // Now simulate a server-side revision change.
            ns._fetchImpl = function(url) {
                return Promise.resolve({ ok: true, status: 200, statusText: "OK", json: function() {
                    return Promise.resolve({
                        schema_version: "structure_viewer_v1",
                        revision: "rev2",
                        availability: "ready",
                        default_entry_id: "e2",
                        groups: [], entries: [{id:"e2", group_id:"g1"}], warnings: []
                    });
                }});
            };

            return ns.refreshIfChanged();
        }).then(function() {
            if (!state.newerAvailable) {
                console.error("FAIL: newerAvailable should be true");
                process.exit(1);
            }
            if (state.payload && state.payload.revision === "rev2") {
                console.error("FAIL: payload should NOT be replaced when dirty");
                process.exit(1);
            }
            console.log("PASS");
        }).catch(function(e) {
            console.error("FAIL: unexpected error", e);
            process.exit(1);
        });
    """).replace("JS_PATH", json.dumps(str(js_path)))

    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, (
        f"Node dirty-guard test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_structure_viewer_node_logic_select_entry_token() -> None:
    """Node logic: selectEntry increments selectionToken each call."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    js_path = FRONTEND_JS_DIR / "structure_viewer.js"
    script = textwrap.dedent("""\
        var window = { fetch: null };
        var AbortController = class { constructor() { this.signal = null; } abort() {} };
        require(JS_PATH);

        var ns = window.ACPStructureViewer;
        var state = ns.state;

        var t0 = state.selectionToken;
        var t1 = ns.selectEntry("e1", "user");
        var t2 = ns.selectEntry("e2", "energy_graph");

        if (t1 !== t0 + 1) {
            console.error("FAIL: first selectEntry token should be " + (t0+1) + ", got " + t1);
            process.exit(1);
        }
        if (t2 !== t1 + 1) {
            console.error("FAIL: second selectEntry token should be " + (t1+1) + ", got " + t2);
            process.exit(1);
        }
        if (state.selectedEntryId !== "e2") {
            console.error("FAIL: selectedEntryId should be e2");
            process.exit(1);
        }
        if (state.selectionOrigin !== "energy_graph") {
            console.error("FAIL: selectionOrigin should be energy_graph");
            process.exit(1);
        }
        console.log("PASS");
    """).replace("JS_PATH", json.dumps(str(js_path)))

    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, (
        f"Node selectEntry token test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


# ---------------------------------------------------------------------------
# Todo 16: structure list + inspector + narrow-screen drawers
# ---------------------------------------------------------------------------


def test_structure_workspace_html_contract() -> None:
    """HTML contract: single-column workspace (summary bar + strip + drawers).

    The legacy list/inspector panels were removed in the UX spec v2.0 P0
    restructure; their ids must never come back into the static markup.
    """
    html = read_workbench_content()

    assert 'id="structure-playback-bar"' in html
    assert 'id="sv-layout"' in html
    assert 'id="sv-summary-bar"' in html
    assert 'id="sv-bottom-strip"' in html
    assert 'id="sv-drawer-measure"' in html
    assert 'id="sv-vibration-dock"' in html
    assert 'id="sv-drawer-source"' in html
    assert 'id="sv-drawer-more"' in html

    assert 'id="sv-list-header"' not in html
    assert 'id="sv-list-body"' not in html
    assert 'id="sv-inspector-header"' not in html
    assert 'id="sv-inspector-body"' not in html
    assert 'id="structure-list-panel"' not in html
    assert 'id="structure-inspector-panel"' not in html

    # viewer-3d must still exist exactly once (no new canvas)
    assert html.count('id="viewer-3d"') == 1


def test_structure_viewer_render_functions_exist() -> None:
    """JS contract: renderStructureViewer, renderInspector, _esc, STR exist."""
    js = FRONTEND_JS_DIR / "structure_viewer.js"
    content = js.read_text(encoding="utf-8")

    assert "renderStructureViewer" in content
    assert "renderInspector" in content
    assert "_esc" in content
    assert "STR" in content
    assert "toggleListDrawer" in content
    assert "toggleInspectorDrawer" in content
    assert "closeAllDrawers" in content


def test_structure_viewer_badge_chips_include_unconfirmed_legacy() -> None:
    """JS contract: badge rendering includes 未确认/兼容模式 classes."""
    js = FRONTEND_JS_DIR / "structure_viewer.js"
    content = js.read_text(encoding="utf-8")

    assert "sv-badge-unconfirmed" in content
    assert "sv-badge-legacy" in content
    assert "sv-badge-failed-frame" in content
    assert "sv-badge-role" in content
    assert "sv-badge-rank" in content


def test_structure_viewer_summary_strip_rendering() -> None:
    """JS contract: renderStructureViewer drives the summary bar + bottom strip."""
    js = FRONTEND_JS_DIR / "structure_viewer.js"
    content = js.read_text(encoding="utf-8")

    assert "sv-list-hidden" not in content

    marker = "function renderStructureViewer()"
    assert content.count(marker) == 1
    render_body = content.split(marker, 1)[1]
    render_body = render_body[: render_body.index("function _renderStripItem(")]

    assert "sv-summary-bar" in render_body
    assert "sv-bottom-strip" in render_body


def test_structure_viewer_availability_pending_notice() -> None:
    """JS contract: availability pending_fetch shows notice string."""
    js = FRONTEND_JS_DIR / "structure_viewer.js"
    content = js.read_text(encoding="utf-8")

    assert "pending_fetch" in content
    assert "\u7b49\u5f85\u8fdc\u7a0b\u7ed3\u679c" in content  # 等待远程结果


def test_structure_viewer_source_kind_labels() -> None:
    """JS contract: source.kind localized labels map."""
    js = FRONTEND_JS_DIR / "structure_viewer.js"
    content = js.read_text(encoding="utf-8")

    assert "\u6b63\u5f0f\u7ed3\u679c" in content  # 正式结果
    assert "\u81ea\u52a8\u63a8\u8350" in content  # 自动推荐
    assert "\u4eba\u5de5\u786e\u8ba4" in content  # 人工确认
    assert "\u6700\u540e\u6709\u6548\u5468\u671f" in content  # 最后有效周期
    assert "\u8ba1\u7b97\u8f93\u5165" in content  # 计算输入
    assert "\u624b\u52a8\u6587\u4ef6" in content  # 手动文件


def test_structure_viewer_css_drawer_responsive() -> None:
    """CSS contract: overlay drawers shrink at the 1100px breakpoint."""
    css = FRONTEND_CSS_DIR / "structure_viewer.css"
    content = css.read_text(encoding="utf-8")

    assert "sv-drawer-open" in content
    assert "sv-drawer-overlay" in content
    assert "@media (max-width: 1100px)" in content

    media_block = content.split("@media (max-width: 1100px)", 1)[1].split("@media", 1)[0]
    assert ".sv-drawer" in media_block
    assert "width" in media_block


def test_structure_viewer_css_single_column_layout() -> None:
    """CSS contract: single-column flex workspace; legacy list/inspector panels gone."""
    css = FRONTEND_CSS_DIR / "structure_viewer.css"
    content = css.read_text(encoding="utf-8")

    layout_block = content.split(".sv-layout {", 1)[1].split("}", 1)[0]
    assert "display: flex" in layout_block
    assert "flex-direction: column" in layout_block
    assert "display: grid" not in layout_block

    # Regression guard (2026-09-11): .sv-layout switched from the three-column
    # grid to a flex column, but .sv-canvas-col kept only content height (no
    # flex-grow) -> the viewer container collapsed to 0px and nothing rendered.
    canvas_col_block = content.split(".sv-canvas-col {", 1)[1].split("}", 1)[0]
    assert re.search(r"\bflex(-grow)?:\s*1", canvas_col_block), (
        ".sv-canvas-col must declare flex: 1 to fill the workspace height"
    )

    viewer_container_block = content.split(".sv-canvas-col .viewer-container {", 1)[1].split(
        "}", 1
    )[0]
    assert "flex: 1" in viewer_container_block
    assert "min-height: 0" in viewer_container_block

    assert ".sv-summary-bar" in content
    assert ".sv-bottom-strip" in content
    assert ".sv-drawer" in content
    assert ".sv-canvas-col" in content

    assert "grid-template-columns: 220px" not in content
    assert ".sv-list-panel" not in content
    assert ".sv-inspector-panel" not in content


def test_structure_viewer_css_ultrawide_canvas_col_width() -> None:
    """CSS contract: >=1920px canvas column fills width up to 1600px, centered.

    Regression guard (2026-09-11): `margin: 0 auto` disables flex cross-axis
    stretch, so the ultrawide rule must set an explicit width or the canvas
    column shrinks to fit-content (measured ~175px at a 2560px viewport).
    """
    css = FRONTEND_CSS_DIR / "structure_viewer.css"
    content = css.read_text(encoding="utf-8")

    assert "@media (min-width: 1920px)" in content
    media_block = content.split("@media (min-width: 1920px)", 1)[1].split("@media", 1)[0]
    canvas_block = media_block.split(".sv-canvas-col {", 1)[1].split("}", 1)[0]
    assert "width: 100%" in canvas_block
    assert "max-width: 1600px" in canvas_block
    assert "margin: 0 auto" in canvas_block


def test_tab_independence_energy_not_in_sv_layout() -> None:
    """#viewer-energy is a peer canvas view, not a descendant of #sv-layout."""
    html = read_workbench_content()

    assert html.count('id="sv-layout"') == 1
    assert html.count('id="viewer-energy"') == 1

    layout_start, layout_end = _div_span(html, 'id="sv-layout"')
    assert html.index('id="viewer-energy"') > layout_end, (
        "#viewer-energy must be rendered after the closing </div> of #sv-layout"
    )
    layout_block = html[layout_start:layout_end]
    assert 'id="viewer-energy"' not in layout_block
    assert 'id="structure-playback-bar"' not in layout_block


def test_drawers_default_closed() -> None:
    """All three overlay drawers start hidden; vibration dock also hidden."""
    html = read_workbench_content()

    for drawer_id in ("measure", "source", "more"):
        pattern = '<div[^>]*id="sv-drawer-' + drawer_id + '"[^>]*>'
        match = re.search(pattern, html)
        assert match is not None, f"drawer div missing: sv-drawer-{drawer_id}"
        assert "display:none" in match.group(0).replace(" ", ""), (
            f"sv-drawer-{drawer_id} must default to display:none"
        )

    dock_match = re.search(r'<div[^>]*id="sv-vibration-dock"[^>]*>', html)
    assert dock_match is not None, "sv-vibration-dock div missing"
    assert "display:none" in dock_match.group(0).replace(" ", ""), (
        "sv-vibration-dock must default to display:none"
    )


def test_summary_bar_exists_in_html() -> None:
    """Summary bar id exists and has dedicated CSS rules."""
    html = read_workbench_content()
    css = (FRONTEND_CSS_DIR / "structure_viewer.css").read_text(encoding="utf-8")

    assert 'id="sv-summary-bar"' in html
    assert ".sv-summary-bar" in css
    summary_block = css.split(".sv-summary-bar {", 1)[1].split("}", 1)[0]
    assert "display: flex" in summary_block


def test_bottom_strip_defaults_hidden() -> None:
    """Bottom strip exists in HTML but starts hidden for single-entry payloads."""
    html = read_workbench_content()

    match = re.search(r'<div[^>]*id="sv-bottom-strip"[^>]*>', html)
    assert match is not None, "sv-bottom-strip div missing"
    assert "display:none" in match.group(0).replace(" ", ""), (
        "sv-bottom-strip must default to display:none"
    )


def test_no_empty_inspector_sections() -> None:
    """renderInspector refreshes open drawers instead of fabricating empty sections."""
    js = (FRONTEND_JS_DIR / "structure_viewer.js").read_text(encoding="utf-8")

    inspector_body = js.split("function renderInspector()", 1)[1].split("\n  function ", 1)[0]
    assert "sv-inspector-section" not in inspector_body

    for fn in (
        "_renderSourceDrawer",
        "_renderVibrationDrawer",
        "_renderMeasureDrawer",
        "_renderMoreDrawer",
    ):
        assert f"function {fn}(" in js, f"drawer content renderer missing: {fn}"


# ---------------------------------------------------------------------------
# Todo 17: auto-load on job select + manual_file injection + dirty guard
# ---------------------------------------------------------------------------


def test_selectjob_calls_onjobselected() -> None:
    """Contract: selectJob must call ACPStructureViewer.onJobSelected."""
    html = read_workbench_content()

    select_job = html.split("async function selectJob(jobId)", 1)[1]
    select_job = select_job.split("\nasync function ", 1)[0]

    assert "ACPStructureViewer.onJobSelected" in select_job, (
        "selectJob must call ACPStructureViewer.onJobSelected"
    )
    assert "window.ACPStructureViewer && window.ACPStructureViewer.onJobSelected" in select_job, (
        "onJobSelected call must be guarded by existence check"
    )


def test_polling_calls_refreshifchanged() -> None:
    """Contract (R2): the lane-2 pollStructureViewer handler wired in
    compat/api.js must call ACPStructureViewer.refreshIfChanged."""
    html = read_workbench_content()

    wiring = html.split("ACPClient.polling.init({", 1)[1].split("ACPClient.polling.start(", 1)[0]
    handler = wiring.split("pollStructureViewer: function", 1)[1].split("},", 1)[0]

    assert "ACPStructureViewer.refreshIfChanged" in handler, (
        "pollStructureViewer handler must call ACPStructureViewer.refreshIfChanged"
    )


def test_file_tree_xyz_click_calls_injectmanualentry() -> None:
    """Contract: file-tree .xyz click must call injectManualEntry."""
    html = read_workbench_content()

    xyz_block = html.split('node.path.endsWith(".xyz")', 1)[1]
    xyz_block = xyz_block.split("return;", 1)[0]

    assert "ACPStructureViewer.injectManualEntry" in xyz_block, (
        "File-tree .xyz click must call ACPStructureViewer.injectManualEntry"
    )


def test_structure_viewer_js_has_todo17_functions() -> None:
    """JS contract: onJobSelected, injectManualEntry, loadSelectedGeometry exist."""
    js = FRONTEND_JS_DIR / "structure_viewer.js"
    content = js.read_text(encoding="utf-8")

    assert "onJobSelected" in content
    assert "injectManualEntry" in content
    assert "loadSelectedGeometry" in content
    assert "_sha256hex" in content
    assert "_manualEntryId" in content
    assert "pendingGeometryRetry" in content
    assert "geometryLoadedFor" in content


def test_structure_viewer_node_manual_entry_id_matches_python() -> None:
    """Node logic: manual_entry_id matches Python sha256[:12] for sample paths."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    js_path = FRONTEND_JS_DIR / "structure_viewer.js"
    script = textwrap.dedent("""\
        var window = { fetch: null };
        require(JS_PATH);
        var ns = window.ACPStructureViewer;

        var cases = [
          ["RESULT/confsearch/conformers/0001.xyz", "5072cff2b42b"],
          ["WORK/03_OPT/batch/opt_item_001/optimize/cycles/cycle_0001.xyz", "092f7b2db5a3"],
        ];
        for (var i = 0; i < cases.length; i++) {
          var path = cases[i][0];
          var expected = cases[i][1];
          var got = ns._manualEntryId(path);
          if (got !== "manual_" + expected) {
            console.error("FAIL: _manualEntryId(" + path + ") = " + got + ", expected manual_" + expected);
            process.exit(1);
          }
        }
        console.log("PASS");
    """).replace("JS_PATH", json.dumps(str(js_path)))

    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, (
        f"Node manual_entry_id test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_structure_viewer_node_sha256_vectors() -> None:
    """Node logic: _sha256hex produces correct digests for known inputs."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    js_path = FRONTEND_JS_DIR / "structure_viewer.js"

    # Pre-computed expected digests
    import hashlib

    test_vectors = [
        ("", hashlib.sha256(b"").hexdigest()),
        ("abc", hashlib.sha256(b"abc").hexdigest()),
        ("hello world", hashlib.sha256(b"hello world").hexdigest()),
    ]

    vectors_js = json.dumps(test_vectors)
    script = (
        textwrap.dedent("""\
        var window = { fetch: null };
        require(JS_PATH);
        var ns = window.ACPStructureViewer;
        var vectors = VECTORS;
        for (var i = 0; i < vectors.length; i++) {
          var input = vectors[i][0];
          var expected = vectors[i][1];
          var got = ns._sha256hex(input);
          if (got !== expected) {
            console.error("FAIL: _sha256hex(" + JSON.stringify(input) + ") = " + got + ", expected " + expected);
            process.exit(1);
          }
        }
        console.log("PASS");
    """)
        .replace("JS_PATH", json.dumps(str(js_path)))
        .replace("VECTORS", vectors_js)
    )

    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, (
        f"Node sha256 test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_structure_viewer_node_dirty_guard_no_replace() -> None:
    """Node logic: dirty=true + poll -> coordinates untouched, newerAvailable set."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    js_path = FRONTEND_JS_DIR / "structure_viewer.js"
    script = textwrap.dedent("""\
        var window = { fetch: null };
        require(JS_PATH);
        var ns = window.ACPStructureViewer;
        var state = ns.state;

        // Simulate a loaded payload
        state.jobId = "test-job";
        state.revision = "rev1";
        state.dirty = true;
        state.payload = { revision: "rev1", entries: [], groups: [] };

        // Mock fetch to return a different revision
        var callCount = 0;
        ns._fetchImpl = function(url, opts) {
          callCount++;
          return Promise.resolve({
            ok: true,
            json: function() {
              return Promise.resolve({
                schema_version: "structure_viewer_v1",
                revision: "rev2",
                availability: "ready",
                default_entry_id: null,
                groups: [],
                entries: [],
                warnings: []
              });
            }
          });
        };

        ns.refreshIfChanged().then(function() {
          if (!state.newerAvailable) {
            console.error("FAIL: newerAvailable should be true when dirty");
            process.exit(1);
          }
          if (state.payload.revision !== "rev1") {
            console.error("FAIL: payload revision should not change when dirty");
            process.exit(1);
          }
          console.log("PASS");
        }).catch(function(e) {
          console.error("FAIL: unexpected error", e);
          process.exit(1);
        });
    """).replace("JS_PATH", json.dumps(str(js_path)))

    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, (
        f"Node dirty-guard test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_structure_viewer_node_geometry_409_retry() -> None:
    """Node logic: geometry 409 -> pendingGeometryRetry flag set."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    js_path = FRONTEND_JS_DIR / "structure_viewer.js"
    script = textwrap.dedent("""\
        var window = { fetch: null };
        require(JS_PATH);
        var ns = window.ACPStructureViewer;
        var state = ns.state;

        // Set up state with a selected entry
        state.jobId = "test-job";
        state.selectedEntryId = "entry1";
        state.selectionToken = 1;
        state.payload = {
          entries: [{
            id: "entry1",
            geometry: { endpoint: "/api/v1/jobs/test/structure-viewer/entries/entry1/geometry", format: "xyz" }
          }]
        };

        var fetchCalls = 0;
        ns._fetchImpl = function(url, opts) {
          fetchCalls++;
          if (fetchCalls === 1) {
            // First call: 409
            return Promise.resolve({ status: 409, ok: false });
          }
          // Retry call with fetch=1: also 409
          return Promise.resolve({ status: 409, ok: false });
        };

        ns.loadSelectedGeometry().then(function() {
          if (!state.pendingGeometryRetry) {
            console.error("FAIL: pendingGeometryRetry should be true after 409");
            process.exit(1);
          }
          if (fetchCalls !== 2) {
            console.error("FAIL: expected 2 fetch calls (initial + retry), got " + fetchCalls);
            process.exit(1);
          }
          console.log("PASS");
        }).catch(function(e) {
          console.error("FAIL: unexpected error", e);
          process.exit(1);
        });
    """).replace("JS_PATH", json.dumps(str(js_path)))

    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, (
        f"Node geometry 409 test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_structure_viewer_svload_xyz_bridge_exists() -> None:
    """Contract: _svLoadXyzToViewer bridge function exists in HTML."""
    html = read_workbench_content()

    assert "window._svLoadXyzToViewer" in html, (
        "Bridge function _svLoadXyzToViewer must exist in HTML"
    )
    assert "parseMultiFrameXYZ" in html, "_svLoadXyzToViewer must use parseMultiFrameXYZ"


def test_structure_viewer_js_has_energy_push_api() -> None:
    """Contract: onEnergyNodeSelected + _entryIdFromEnergyNode exist."""
    js = FRONTEND_JS_DIR / "structure_viewer.js"
    content = js.read_text(encoding="utf-8")

    assert "onEnergyNodeSelected" in content
    assert "_entryIdFromEnergyNode" in content
    assert '"energy_graph"' in content, 'Must use "energy_graph" origin for energy-graph push'


def test_structure_viewer_energy_push_forbidden_ids_still_absent() -> None:
    """Contract: forbidden identifiers from anti-pattern #29 must stay absent."""
    html = read_workbench_content()

    assert "energyGraphConfirmAndBatch" not in html, (
        "energyGraphConfirmAndBatch must stay absent (anti-pattern #29)"
    )
    assert 'data-energy-action="to-batch"' not in html, (
        "to-batch action must stay absent (anti-pattern #29)"
    )


def test_structure_viewer_node_energy_stale_job_guard() -> None:
    """Node logic: onEnergyNodeSelected ignores mismatched jobId."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    js_path = FRONTEND_JS_DIR / "structure_viewer.js"
    script = textwrap.dedent("""\
        var window = { fetch: null };
        require(JS_PATH);
        var ns = window.ACPStructureViewer;
        var state = ns.state;

        state.jobId = "job-A";
        state.payload = { entries: [{ id: "conf_1" }], groups: [] };
        state.selectedEntryId = "conf_1";

        var result = ns.onEnergyNodeSelected("job-B", { kind: "conformer", key: "1" });
        if (result !== null) {
            console.error("FAIL: stale job should be ignored, got " + result);
            process.exit(1);
        }
        if (state.selectedEntryId !== "conf_1") {
            console.error("FAIL: selectedEntryId should not change");
            process.exit(1);
        }
        console.log("PASS");
    """).replace("JS_PATH", json.dumps(str(js_path)))

    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, (
        f"Stale-job guard test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_structure_viewer_node_energy_entry_id_mapping() -> None:
    """Node logic: _entryIdFromEnergyNode maps kind/key to correct ids."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    js_path = FRONTEND_JS_DIR / "structure_viewer.js"
    script = textwrap.dedent("""\
        var window = { fetch: null };
        require(JS_PATH);
        var ns = window.ACPStructureViewer;
        var map = ns._entryIdFromEnergyNode;

        var cases = [
          [{ entryId: "conf_0001" }, "conf_0001"],
          [{ kind: "conformer", key: "0001" }, "conf_0001"],
          [{ kind: "batch", key: "opt_item_001" }, "batch_opt_item_001"],
          [{ kind: "scan", frameIndex: 5 }, "scan_frame_5"],
          [{ kind: "pes", candidateId: "ts_frame_005" }, "pes_ts_frame_005"],
          [{ kind: "simple", stepKind: "optimize" }, "simple_optimize"],
          [{ kind: "irc", endpoint: "forward", frameIndex: 0 }, "irc_forward_0"],
          [{ kind: "optimization", frameIndex: 12 }, null],
        ];
        for (var i = 0; i < cases.length; i++) {
          var input = cases[i][0];
          var expected = cases[i][1];
          var got = map(input);
          if (got !== expected) {
            console.error("FAIL: _entryIdFromEnergyNode(" + JSON.stringify(input) + ") = " + got + ", expected " + expected);
            process.exit(1);
          }
        }
        console.log("PASS");
    """).replace("JS_PATH", json.dumps(str(js_path)))

    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, (
        f"Entry-id mapping test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_structure_viewer_node_energy_token_guard() -> None:
    """Node logic: rapid selections — older token's effects are superseded."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    js_path = FRONTEND_JS_DIR / "structure_viewer.js"
    script = textwrap.dedent("""\
        var window = { fetch: null };
        require(JS_PATH);
        var ns = window.ACPStructureViewer;
        var state = ns.state;

        state.jobId = "job-1";
        state.payload = {
          entries: [
            { id: "conf_1", group_id: "", label: "C1", role: "minimum", status: "completed",
              geometry: { endpoint: "/e1", format: "xyz" }, source: { kind: "formal_result" },
              badges: [], vibrations: { available: false } },
            { id: "conf_2", group_id: "", label: "C2", role: "minimum", status: "completed",
              geometry: { endpoint: "/e2", format: "xyz" }, source: { kind: "formal_result" },
              badges: [], vibrations: { available: false } },
          ],
          groups: []
        };

        // First push
        var tok1 = ns.onEnergyNodeSelected("job-1", { kind: "conformer", key: "1" });
        // Second push immediately after
        var tok2 = ns.onEnergyNodeSelected("job-1", { kind: "conformer", key: "2" });

        if (tok2 !== tok1 + 1) {
          console.error("FAIL: token should increment, got tok1=" + tok1 + " tok2=" + tok2);
          process.exit(1);
        }
        if (state.selectedEntryId !== "conf_2") {
          console.error("FAIL: selectedEntryId should be conf_2, got " + state.selectedEntryId);
          process.exit(1);
        }
        if (state.selectionOrigin !== "energy_graph") {
          console.error("FAIL: selectionOrigin should be energy_graph, got " + state.selectionOrigin);
          process.exit(1);
        }
        console.log("PASS");
    """).replace("JS_PATH", json.dumps(str(js_path)))

    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, (
        f"Token guard test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_structure_viewer_node_energy_transient_entry() -> None:
    """Node logic: unknown optimization frame creates a transient entry."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    js_path = FRONTEND_JS_DIR / "structure_viewer.js"
    script = textwrap.dedent("""\
        var window = { fetch: null };
        require(JS_PATH);
        var ns = window.ACPStructureViewer;
        var state = ns.state;

        state.jobId = "job-1";
        state.payload = {
          entries: [],
          groups: []
        };

        var tok = ns.onEnergyNodeSelected("job-1", {
          kind: "optimization",
          frameIndex: 12,
          geometryRef: "WORK/03_OPT/optimization_trajectory.json",
          label: "Cycle 12"
        });

        if (!tok) {
          console.error("FAIL: should return a token");
          process.exit(1);
        }
        if (state.selectedEntryId !== "transient_opt_12") {
          console.error("FAIL: selectedEntryId should be transient_opt_12, got " + state.selectedEntryId);
          process.exit(1);
        }
        // Verify transient entry was injected
        var entries = state.payload.entries;
        var found = false;
        for (var i = 0; i < entries.length; i++) {
          if (entries[i].id === "transient_opt_12") { found = true; break; }
        }
        if (!found) {
          console.error("FAIL: transient entry not injected into payload");
          process.exit(1);
        }
        console.log("PASS");
    """).replace("JS_PATH", json.dumps(str(js_path)))

    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, (
        f"Transient entry test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_phase_a_structure_tab_contract() -> None:
    """Phase-A lock: single structure tab, conformers/3d absent (todo 14)."""
    html = read_workbench_content()

    assert 'data-tab="conformers"' not in html, "conformers tab must be removed"
    assert 'data-tab="3d"' not in html, "3d tab must be renamed to structure"
    assert 'data-tab="structure"' in html, "structure tab must exist"
    assert ">结构查看器</button>" in html
    assert '"tab.structure": "结构查看器"' in html
    assert '"tab.structure": "Structure Viewer"' in html
    assert '"tab.conformers"' not in html
    assert '"tab.3d"' not in html
    # Other tabs must remain
    assert 'data-tab="path"' in html
    assert 'data-tab="energy"' in html
    assert 'data-tab="wavefunction"' in html
    # Compat mapping
    assert (
        'tab === "3d" || tab === "conformers"' in html
        or 'tab === "conformers" || tab === "3d"' in html
    )


def test_phase_a_store_api_names() -> None:
    """Phase-A lock: structure_viewer.js exposes all required API names (todos 15-18)."""
    js = FRONTEND_JS_DIR / "structure_viewer.js"
    content = js.read_text(encoding="utf-8")

    required = [
        "structureViewerState",
        "loadStructureViewer",
        "selectEntry",
        "refreshIfChanged",
        "onJobSelected",
        "injectManualEntry",
        "onEnergyNodeSelected",
        "loadSelectedGeometry",
    ]
    for name in required:
        assert name in content, f"{name} missing from structure_viewer.js namespace"


def test_phase_a_selectjob_autoload_call() -> None:
    """Phase-A lock: selectJob calls onJobSelected which triggers loadStructureViewer (todo 17)."""
    html = read_workbench_content()

    select_job = html.split("async function selectJob(jobId)", 1)[1].split("\nasync function ", 1)[
        0
    ]
    assert "onJobSelected" in select_job, "selectJob must call ACPStructureViewer.onJobSelected"


def test_phase_a_manual_file_injection_and_sha256_parity() -> None:
    """Phase-A lock: manual_file injection path + SHA-256 parity (todo 17)."""
    js = FRONTEND_JS_DIR / "structure_viewer.js"
    content = js.read_text(encoding="utf-8")

    assert "injectManualEntry" in content
    assert "_manualEntryId" in content
    assert "_sha256hex(relpath).slice(0, 12)" in content, (
        "manual entry id must use sha256(relpath)[:12]"
    )


def test_phase_a_energy_push_token_guard() -> None:
    """Phase-A lock: energy push uses selectionToken guard (todo 18)."""
    js = FRONTEND_JS_DIR / "structure_viewer.js"
    content = js.read_text(encoding="utf-8")

    assert "onEnergyNodeSelected" in content
    assert '"energy_graph"' in content, 'Must use "energy_graph" origin for energy-graph push'


def test_phase_a_dirty_guard_branch() -> None:
    """Phase-A lock: dirty guard prevents coordinate replacement (todo 15)."""
    js = FRONTEND_JS_DIR / "structure_viewer.js"
    content = js.read_text(encoding="utf-8")

    assert "newerAvailable" in content
    assert "structure.newer_available" in content or "NEWER_AVAILABLE" in content


def test_phase_a_forbidden_identifiers_absent() -> None:
    """Phase-A lock: forbidden identifiers must stay absent (anti-pattern #29)."""
    html = read_workbench_content()

    assert "energyGraphConfirmAndBatch" not in html
    assert 'data-energy-action="to-batch"' not in html
    assert 'data-energy-action="lock-frame"' not in html
    assert "isFrameLocked" not in html
    assert "acp-frame-lock" not in html


def test_phase_a_i18n_structure_keys_complete_across_locales() -> None:
    """Phase-A lock: every structure.* key in zh-CN must also exist in en-US
    and vice-versa.

    A future edit that adds a structure key to only one locale will fail here.
    """
    html = read_workbench_content()

    zh_keys = _extract_structure_keys(html, _ZH_BLOCK_RE)
    en_keys = _extract_structure_keys(html, _EN_BLOCK_RE)

    assert zh_keys, "No structure.* keys found in zh-CN block"
    assert en_keys, "No structure.* keys found in en-US block"

    only_zh = zh_keys - en_keys
    only_en = en_keys - zh_keys
    assert not only_zh, f"Keys in zh-CN but missing from en-US: {sorted(only_zh)}"
    assert not only_en, f"Keys in en-US but missing from zh-CN: {sorted(only_en)}"


def test_phase_a_i18n_structure_keys_used_in_js_exist_in_both_locales() -> None:
    """Phase-A lock: every structure.* key referenced by _t() in
    structure_viewer.js must exist in both locale dictionaries."""
    html = read_workbench_content()
    js = FRONTEND_JS_DIR / "structure_viewer.js"
    js_content = js.read_text(encoding="utf-8")

    # Extract keys used in _t("structure.xxx", ...) calls
    # Filter out dynamic keys like "structure.source_kind." (empty suffix)
    t_call_re = re.compile(r'_t\("(structure\.[^"]+)"')
    used_keys = {k for k in t_call_re.findall(js_content) if not k.endswith(".")}

    zh_keys = _extract_structure_keys(html, _ZH_BLOCK_RE)
    en_keys = _extract_structure_keys(html, _EN_BLOCK_RE)

    missing_zh = used_keys - zh_keys
    missing_en = used_keys - en_keys
    assert not missing_zh, (
        f"structure.* keys used in JS but missing from zh-CN: {sorted(missing_zh)}"
    )
    assert not missing_en, (
        f"structure.* keys used in JS but missing from en-US: {sorted(missing_en)}"
    )


def test_phase_a_i18n_structure_keys_in_js_have_str_fallback() -> None:
    """Phase-A lock: every _t() call in structure_viewer.js must have a
    STR-table fallback (second argument) for Node.js environments."""
    js = FRONTEND_JS_DIR / "structure_viewer.js"
    content = js.read_text(encoding="utf-8")

    # Match _t("structure.xxx", STR.YYY) — must have two arguments
    t_calls = re.findall(r'_t\("structure\.[^"]+",\s*STR\.\w+\)', content)
    assert len(t_calls) >= 20, f"Expected >=20 _t() calls with STR fallback, found {len(t_calls)}"


def test_structure_viewer_node_parse_xyz_first_frame() -> None:
    """Node logic: _parseXyzFirstFrame feeds displayedCoords for arrows."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    script = textwrap.dedent("""\
        var window = { fetch: null };
        var AbortController = class { constructor() { this.signal = null; } abort() {} };
        require(JS_PATH);
        var ns = window.ACPStructureViewer;

        var xyz = "3\\ncomment line\\nC 0.0 0.0 0.0\\nH 1.1 0.0 0.0\\nO 0.0 1.2 0.0\\n";
        var parsed = ns._parseXyzFirstFrame(xyz);
        if (!parsed || parsed.symbols.length !== 3 || parsed.coords.length !== 3) {
            console.error("FAIL: basic parse");
            process.exit(1);
        }
        if (parsed.symbols[0] !== "C" || parsed.symbols[2] !== "O") {
            console.error("FAIL: symbols");
            process.exit(1);
        }
        if (parsed.coords[1][0] !== 1.1 || parsed.coords[2][1] !== 1.2) {
            console.error("FAIL: coords");
            process.exit(1);
        }
        if (ns._parseXyzFirstFrame("garbage") !== null ||
            ns._parseXyzFirstFrame("") !== null ||
            ns._parseXyzFirstFrame(null) !== null) {
            console.error("FAIL: invalid inputs must return null");
            process.exit(1);
        }
        // truncated frame (claims 5 atoms, has 2)
        if (ns._parseXyzFirstFrame("5\\nc\\nC 0 0 0\\nH 1 0 0\\n") !== null) {
            console.error("FAIL: truncated frame must return null");
            process.exit(1);
        }
        console.log("PASS");
    """).replace("JS_PATH", json.dumps(str(_SV_JS_PATH)))

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"Node xyz-parser test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


# ---------------------------------------------------------------------------
# Todo 4: terminal-aware catalog refresh — retry state ownership (D4)
# ---------------------------------------------------------------------------

def _run_sv_node(body: str) -> None:
    """Run a structure_viewer.js Node logic snippet under the standard harness.

    Binds ``ns``/``state`` plus ``fail()``/``okResp()``/``catalogData()``
    helpers, then evaluates ``body``, which must print ``PASS`` or fail().
    Skips when node is unavailable.
    """
    if not shutil.which("node"):
        pytest.skip("node not available")

    js_path = FRONTEND_JS_DIR / "structure_viewer.js"
    script = (
        textwrap.dedent(
            """\
            var window = { fetch: null };
            var AbortController = class { constructor() { this.signal = null; } abort() {} };
            require(JS_PATH);
            var ns = window.ACPStructureViewer;
            var state = ns.state;
            function fail(msg) { console.error("FAIL: " + msg); process.exit(1); }
            function immediate() { return new Promise(function (r) { setImmediate(r); }); }
            function okResp(data) {
                return { ok: true, status: 200, statusText: "OK",
                         json: function () { return Promise.resolve(data); } };
            }
            function catalogData(opts) {
                opts = opts || {};
                return {
                    schema_version: "structure_viewer_v1",
                    revision: opts.revision || "rev",
                    availability: opts.availability || "ready",
                    job_status: opts.job_status || "completed",
                    default_entry_id: opts.default_entry_id || null,
                    groups: [],
                    entries: opts.entries || [],
                    warnings: opts.warnings || []
                };
            }
            """
        ).replace("JS_PATH", json.dumps(str(js_path)))
        + textwrap.dedent(body)
    )
    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, (
        f"Node logic failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout
