/* R1b behavior tests: job detail/recovery controller — the single source for
 * the pure logic that workbench/features/job-detail.js delegates to. */
"use strict";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  function setup(overrides) {
    const sandbox = makeSandbox(overrides);
    loadScript("js/workbench/controllers/job_detail_controller.js", sandbox);
    return sandbox;
  }

  function ctrl(sandbox) {
    return sandbox.window.ACPWorkbench.controllers.jobDetail;
  }

  // ── resolveJobRecovery: server-wins per-field merge ──────────────────────

  await test("server recovery wins per field; unspecified fields keep the status fallback", async () => {
    const sandbox = setup();
    const c = ctrl(sandbox);
    // failed + mechanism → fallback: can_continue true (checkpoint), can_rerun true
    const job = { id: "j1", status: "failed", spec: { workflow: "mechanism" } };
    const merged = c.resolveJobRecovery(job, {
      job: { id: "j1" },
      recovery: { can_pause: true, can_rerun: false, continue_mode: "resume", continue_notes: "server note" },
    });
    assertEq(merged, {
      can_pause: true,      // server override (fallback would be false)
      can_unpause: false,   // fallback
      can_continue: true,   // fallback (mechanism + failed)
      can_rerun: false,     // server override (fallback would be true)
      can_cancel: false,    // fallback (failed is not active)
      can_purge: true,      // fallback
      continue_mode: "resume", // server override
      continue_notes: "server note", // server override
    });
  });

  await test("non-boolean boolean-fields and non-string string-fields fall back per field", async () => {
    const sandbox = setup();
    const c = ctrl(sandbox);
    const job = { id: "j2", status: "failed", spec: { workflow: "xtbmd_censo_energy" } };
    const merged = c.resolveJobRecovery(job, {
      job: { id: "j2" },
      recovery: {
        can_pause: "yes",     // not a boolean → fallback (false)
        can_rerun: 1,         // not a boolean → fallback (true: failed is terminal)
        can_continue: null,   // not a boolean → fallback (true: continuable workflow)
        can_cancel: 0,        // not a boolean → fallback (false)
        continue_mode: 42,    // not a string → fallback ("checkpoint")
        continue_notes: null, // not a string → fallback ("")
      },
    });
    assertEq(merged.can_pause, false, "string can_pause falls back");
    assertEq(merged.can_rerun, true, "numeric can_rerun falls back");
    assertEq(merged.can_continue, true, "null can_continue falls back");
    assertEq(merged.can_cancel, false, "numeric can_cancel falls back");
    assertEq(merged.continue_mode, "checkpoint", "numeric continue_mode falls back");
    assertEq(merged.continue_notes, "", "null continue_notes falls back");
    assertEq(merged.can_unpause, false);
    assertEq(merged.can_purge, true);
  });

  await test("missing detail / missing recovery / non-object recovery → pure status default", async () => {
    const sandbox = setup();
    const c = ctrl(sandbox);
    const job = { id: "j3", status: "running" };
    const expected = {
      can_pause: true, can_unpause: false, can_continue: false,
      continue_mode: "", continue_notes: "",
      can_rerun: false, can_cancel: true, can_purge: true,
    };
    assertEq(c.resolveJobRecovery(job, null), expected, "null detail");
    assertEq(c.resolveJobRecovery(job, { job: { id: "j3" } }), expected, "detail without recovery");
    assertEq(c.resolveJobRecovery(job, { recovery: "bogus" }), expected, "non-object recovery");
    assertEq(c.resolveJobRecovery(job, { recovery: ["can_pause"] }), expected, "array recovery is not plain-object");
  });

  // ── jobExecutionIdentity: (id, attempt, revision) triple ─────────────────

  await test("identity is (id, attempt, revision); name_revision NEVER participates", async () => {
    const sandbox = setup();
    const c = ctrl(sandbox);
    const base = { id: "job-4", attempt: 1, revision: 2 };
    assertEq(c.jobExecutionIdentity(base), "job-4\u00001\u00002");
    assertEq(
      c.jobExecutionIdentity({ id: "job-4", attempt: 1, revision: 2, name_revision: 99 }),
      c.jobExecutionIdentity(base),
      "name_revision must not change the identity"
    );
    assertEq(
      c.jobExecutionIdentity({ id: "job-4", attempt: 1, revision: 3, name_revision: base ? 99 : 0 }),
      "job-4\u00001\u00003",
      "revision bump does change the identity"
    );
  });

  await test("identity edge cases: job_id fallback, attempt 0, non-numeric fields, null job", async () => {
    const sandbox = setup();
    const c = ctrl(sandbox);
    assertEq(c.jobExecutionIdentity(null), "");
    assertEq(c.jobExecutionIdentity({}), "\u0000\u0000", "all-empty triple");
    assertEq(c.jobExecutionIdentity({ job_id: "g" }), "g\u0000\u0000", "job_id fallback, no attempt/revision");
    assertEq(
      c.jobExecutionIdentity({ id: "z", attempt: 0, revision: 0 }),
      "z\u00000\u00000",
      "numeric 0 is a real attempt/revision (typeof check, not truthiness)"
    );
    assertEq(
      c.jobExecutionIdentity({ id: "z", attempt: "1", revision: null }),
      "z\u0000\u0000",
      "non-numeric attempt/revision degrade to empty"
    );
    assertEq(
      c.jobExecutionIdentity({ id: "z", attempt: Infinity }),
      "z\u0000\u0000",
      "non-finite attempt degrades to empty"
    );
  });

  // ── isDetailStaleFor: status change OR attempt/revision identity change ──

  await test("staleness triggers on status change and on attempt/revision identity change only", async () => {
    const sandbox = setup();
    const c = ctrl(sandbox);
    const detail = { job: { id: "j", status: "running", attempt: 1, revision: 1 } };
    const row = (over) => Object.assign({ id: "j", status: "running", attempt: 1, revision: 1 }, over);
    assertEq(c.isDetailStaleFor(detail, row()), false, "identical row is fresh");
    assertEq(c.isDetailStaleFor(detail, row({ status: "RUNNING" })), false, "status compare is case-insensitive");
    assertEq(c.isDetailStaleFor(detail, row({ status: "completed" })), true, "status change is stale");
    assertEq(c.isDetailStaleFor(detail, row({ attempt: 2 })), true, "attempt change is stale (rerun)");
    assertEq(c.isDetailStaleFor(detail, row({ revision: 5 })), true, "revision change is stale");
    assertEq(
      c.isDetailStaleFor(detail, row({ name_revision: 77 })),
      false,
      "name_revision alone must NOT mark stale (rename is not an execution change)"
    );
    assertEq(c.isDetailStaleFor(null, row()), false, "no detail → not stale");
    assertEq(c.isDetailStaleFor({}, row()), false, "detail without job object → not stale");
    assertEq(c.isDetailStaleFor(detail, null), false, "no row → not stale");
  });

  // ── pruneAgainstList: drop only present-but-stale; absent rows are KEPT ──

  await test("prune drops present-in-list stale entries and KEEPS rows absent from the list", async () => {
    const sandbox = setup();
    const c = ctrl(sandbox);
    // Live parity pin: the live page wrapper leaves filtered-out rows alone
    // ("they cannot be compared") — the controller must do the same.
    const cache = {
      "status-stale": { job: { id: "status-stale", status: "running", attempt: 1, revision: 1 } },
      "attempt-stale": { job: { id: "attempt-stale", status: "running", attempt: 1, revision: 1 } },
      "revision-stale": { job: { id: "revision-stale", status: "running", attempt: 1, revision: 1 } },
      "fresh": { job: { id: "fresh", status: "running", attempt: 1, revision: 1 } },
      "unlisted": { job: { id: "unlisted", status: "failed", attempt: 3, revision: 9 } },
    };
    const rows = [
      { id: "status-stale", status: "completed", attempt: 1, revision: 1 },
      { id: "attempt-stale", status: "running", attempt: 2, revision: 1 },
      { id: "revision-stale", status: "running", attempt: 1, revision: 2 },
      { id: "fresh", status: "running", attempt: 1, revision: 1 },
      // "unlisted" is intentionally absent from rows
    ];
    const changed = c.pruneAgainstList(cache, rows);
    assertEq(changed, true, "prune reports a change");
    assert(!("status-stale" in cache), "status-stale dropped");
    assert(!("attempt-stale" in cache), "attempt-stale dropped");
    assert(!("revision-stale" in cache), "revision-stale dropped");
    assert("fresh" in cache, "fresh entry kept");
    assert("unlisted" in cache, "absent-from-list entry KEPT (live behavior)");
  });

  await test("prune with an all-fresh cache is a no-op returning false", async () => {
    const sandbox = setup();
    const c = ctrl(sandbox);
    const cache = {
      "a": { job: { id: "a", status: "paused", attempt: 1, revision: 1 } },
      "b": { job: { id: "b", status: "queued", attempt: 0, revision: 0 } },
    };
    const rows = [
      { id: "a", status: "paused", attempt: 1, revision: 1 },
      { id: "b", status: "queued", attempt: 0, revision: 0 },
    ];
    assertEq(c.pruneAgainstList(cache, rows), false);
    assert("a" in cache && "b" in cache, "nothing dropped");
    assertEq(c.pruneAgainstList(cache, []), false, "empty list keeps everything (nothing comparable)");
    assertEq(c.pruneAgainstList(null, rows), false, "null cache is safe");
  });

  // ── computeDefaultRecovery: status × workflow matrix (live parity) ───────

  await test("default recovery matrix across statuses", async () => {
    const sandbox = setup();
    const c = ctrl(sandbox);
    const def = (status, workflow) => c.computeDefaultRecovery({ id: "m", status, spec: workflow ? { workflow } : {} });
    assertEq(def("running"), {
      can_pause: true, can_unpause: false, can_continue: false, continue_mode: "",
      continue_notes: "", can_rerun: false, can_cancel: true, can_purge: true,
    });
    assertEq(def("paused"), {
      can_pause: false, can_unpause: true, can_continue: false, continue_mode: "",
      continue_notes: "", can_rerun: false, can_cancel: true, can_purge: true,
    }, "paused is still active → can_cancel");
    assertEq(def("queued"), {
      can_pause: false, can_unpause: false, can_continue: false, continue_mode: "",
      continue_notes: "", can_rerun: false, can_cancel: true, can_purge: true,
    });
    assertEq(def("waiting_review"), {
      can_pause: false, can_unpause: false, can_continue: false, continue_mode: "",
      continue_notes: "", can_rerun: false, can_cancel: true, can_purge: true,
    });
    assertEq(def("completed"), {
      can_pause: false, can_unpause: false, can_continue: false, continue_mode: "",
      continue_notes: "", can_rerun: true, can_cancel: false, can_purge: true,
    });
    assertEq(def("cancelled"), {
      can_pause: false, can_unpause: false, can_continue: false, continue_mode: "",
      continue_notes: "", can_rerun: true, can_cancel: false, can_purge: true,
    }, "cancelled without a continuable workflow: rerun only");
    assertEq(def("failed"), {
      can_pause: false, can_unpause: false, can_continue: false, continue_mode: "",
      continue_notes: "", can_rerun: true, can_cancel: false, can_purge: true,
    });
    assertEq(def("bogus_status"), {
      can_pause: false, can_unpause: false, can_continue: false, continue_mode: "",
      continue_notes: "", can_rerun: false, can_cancel: false, can_purge: true,
    }, "unknown status: purge only");
    assertEq(def("RUNNING"), def("running"), "status compare is case-insensitive");
    assertEq(def("running", "mechanism").can_continue, false, "active jobs are never continuable");
  });

  await test("continuable workflow rules ported from live: mechanism / xtbmd_censo_energy on failed|cancelled", async () => {
    const sandbox = setup();
    const c = ctrl(sandbox);
    const def = (status, workflow) => c.computeDefaultRecovery({ id: "m", status, spec: { workflow } });
    for (const workflow of ["mechanism", "xtbmd_censo_energy"]) {
      for (const status of ["failed", "cancelled"]) {
        const d = def(status, workflow);
        assertEq(d.can_continue, true, workflow + " + " + status + " is continuable");
        assertEq(d.continue_mode, "checkpoint", "continuable default mode is checkpoint");
        assertEq(d.continue_notes, "");
        assertEq(d.can_rerun, true, status + " is terminal → rerun too");
      }
    }
    // Negative cells of the continuable matrix.
    assertEq(def("failed", "Confsearch").can_continue, false, "other workflows are not continuable");
    assertEq(def("failed", "BatchOptimize").can_continue, false);
    assertEq(def("completed", "mechanism").can_continue, false, "terminal-but-not-failed/cancelled is not continuable");
    assertEq(def("completed", "mechanism").continue_mode, "");
    assertEq(def("running", "xtbmd_censo_energy").can_continue, false);
    assertEq(def("failed", "Mechanism").can_continue, false, "workflow match is exact-case (live parity)");
    // Missing spec entirely.
    const bare = c.computeDefaultRecovery({ id: "m", status: "failed" });
    assertEq(bare.can_continue, false, "no spec → not continuable");
  });

  // ── status classification helpers ────────────────────────────────────────

  await test("terminal/active classification matches the recovery matrix inputs", async () => {
    const sandbox = setup();
    const c = ctrl(sandbox);
    for (const s of ["completed", "failed", "cancelled", "COMPLETED"]) {
      assertEq(c.isTerminalJobStatus(s), true, s + " is terminal");
    }
    assertEq(c.isTerminalJobStatus("running"), false);
    for (const s of ["queued", "starting", "running", "cancelling", "pending", "waiting_review", "paused"]) {
      assertEq(c.isActiveJobStatus(s), true, s + " is active");
    }
    for (const s of ["completed", "failed", "cancelled", "nonsense"]) {
      assertEq(c.isActiveJobStatus(s), false, s + " is not active");
    }
  });

  // ── source purity ────────────────────────────────────────────────────────

  await test("controller purity: no DOM/network/raw-library access in module source", () => {
    const fs = require("fs"), path = require("path");
    const src = fs.readFileSync(path.resolve(__dirname, "../workbench/controllers/job_detail_controller.js"), "utf8");
    const code = src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
    for (const bad of ["document.", "fetch(", "$3Dmol", "EventSource"]) {
      assert(code.indexOf(bad) === -1, "controller must not use " + bad);
    }
  });

  await test("page delegation: job-detail.js entry points forward to this controller", () => {
    const fs = require("fs"), path = require("path");
    const raw = fs.readFileSync(path.resolve(__dirname, "../workbench/features/job-detail.js"), "utf8");
    const src = raw.replace(/\r\n/g, "\n");
    const code = src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
    // Same-name, same-signature page entry points that delegate.
    assert(/function jobExecutionIdentity\(job\) \{\n\s*return ACPWorkbench\.controllers\.jobDetail\.jobExecutionIdentity\(job\);/.test(code),
      "jobExecutionIdentity delegates");
    assert(/\bfunction jobDetailCacheStaleFor\(detail, row\) \{\n\s*return ACPWorkbench\.controllers\.jobDetail\.isDetailStaleFor\(detail, row\);/.test(code),
      "jobDetailCacheStaleFor delegates to isDetailStaleFor");
    assert(/\bfunction computeDefaultRecovery\(job\) \{\n\s*return ACPWorkbench\.controllers\.jobDetail\.computeDefaultRecovery\(job\);/.test(code),
      "computeDefaultRecovery delegates");
    assert(/var changed = ACPWorkbench\.controllers\.jobDetail\.pruneAgainstList\(jobDetailCache, jobsCache\);\n\s*if \(changed\) jobDetailCacheRevision \+= 1;/.test(code),
      "prune wrapper delegates and keeps the revision bump side effect");
    const rr = code.match(/\bfunction resolveJobRecovery\(job\) \{[\s\S]*?\n\}/);
    assert(rr, "resolveJobRecovery present");
    assert(rr[0].indexOf("ACPWorkbench.controllers.jobDetail.resolveJobRecovery(job, detail)") !== -1,
      "resolveJobRecovery resolves the detail snapshot and delegates");
    assert(rr[0].indexOf("getJobDetail(") !== -1, "resolveJobRecovery reads the page projection");
  });
};
