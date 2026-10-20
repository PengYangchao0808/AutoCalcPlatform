/* R1b behavior tests: job detail/recovery controller — the single source for
 * the pure logic that workbench/features/job-detail.js delegates to.
 * D03 (todo 9): also proves rerun-session invalidation with the real backend
 * projections ((6,42) original case, (7,43) advanced case) composed through
 * the real page detail entry point — never fabricated equal identities. */
"use strict";

// Drives live page state exactly like the page does: the taskList onRows
// callback assigns "jobsCache = rows" (features/queue.js), and the tests run
// that identical statement in the page script context. No fake DOM.
const vm = require("vm");

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

  // ── D03 (todo 9): real backend projections × v2 queue row ─────────────────
  // Field names mirror the fixed backend (tests/test_acp_api_v1.py D03,
  // dea043d): the v1 job model projects attempt/revision/name_revision from
  // the record, and the v2 task-view row projects the same execution fields
  // (src/acp/scheduler/task_views.py). Identities are compared with the
  // production jobExecutionIdentity/isDetailStaleFor only.

  const D03_ID = "job-d03";

  function v1DetailJob(attempt, revision, nameRevision, over) {
    return Object.assign({
      id: D03_ID,
      spec: { workflow: "singlepoint", name: D03_ID },
      status: "running",
      work_dir: "/var/lib/acp/runs/" + D03_ID,
      project_id: "proj-1",
      project_name: "Project 1",
      created_at: "2026-10-20T08:00:00Z",
      updated_at: "2026-10-20T08:30:00Z",
      progress: 0.4,
      custom_name: "display-only",
      resolved_name: "display-only",
      default_name: D03_ID,
      name_revision: nameRevision,
      attempt: attempt,
      revision: revision,
    }, over || {});
  }

  function v1Detail(attempt, revision, nameRevision, over) {
    return {
      job: v1DetailJob(attempt, revision, nameRevision, over),
      stages: [],
      artifacts_summary: [],
      error_detail: null,
      disk_state: { work_dir: "/var/lib/acp/runs/" + D03_ID, work_dir_exists: true },
      recovery: {
        can_pause: true, can_unpause: false, can_continue: false, continue_mode: "",
        continue_notes: "", can_rerun: true, can_cancel: true, can_purge: true,
      },
    };
  }

  function v2Row(attempt, revision, nameRevision, over) {
    return Object.assign({
      id: D03_ID,
      name: "display-only",
      task_name: "display-only",
      status: "running",
      project_id: "proj-1",
      project_name: "Project 1",
      spec: { workflow: "singlepoint", task_name: "display-only" },
      name_revision: nameRevision,
      attempt: attempt,
      revision: revision,
    }, over || {});
  }

  function executionOf(jobOrRow) {
    return { attempt: jobOrRow.attempt, revision: jobOrRow.revision };
  }

  await test("D03 real case (6,42): detail.job and the v2 row carry ONE equal execution identity", async () => {
    const sandbox = setup();
    const c = ctrl(sandbox);
    const detail = v1Detail(6, 42, 1);
    const row = v2Row(6, 42, 1);
    const detailIdentity = c.jobExecutionIdentity(detail.job);
    assert(detailIdentity === c.jobExecutionIdentity(row),
      "jobExecutionIdentity(detail.job) must equal jobExecutionIdentity(row) for (6,42)");
    assertEq(detailIdentity, "job-d03\u00006\u000042", "concrete (6,42) identity string");
    assertEq(c.isDetailStaleFor(detail, row), false, "same execution is NOT stale");
  });

  await test("D03 stale case (7,43): an advanced attempt/new revision invalidates the (6,42) detail", async () => {
    const sandbox = setup();
    const c = ctrl(sandbox);
    const detail = v1Detail(6, 42, 1);
    assertEq(c.isDetailStaleFor(detail, v2Row(7, 43, 1)), true, "(7,43) row is stale vs the (6,42) detail");
    assertEq(c.isDetailStaleFor(detail, v2Row(7, 42, 1)), true, "attempt 7 alone is stale");
    assertEq(c.isDetailStaleFor(detail, v2Row(6, 43, 1)), true, "new revision alone is stale");
    assertEq(c.jobExecutionIdentity(v2Row(7, 43, 1)), "job-d03\u00007\u000043", "concrete (7,43) identity string");
    assert(c.jobExecutionIdentity(detail.job) !== c.jobExecutionIdentity(v2Row(7, 43, 1)),
      "(6,42) and (7,43) are different executions");
  });

  await test("D03 name_revision alone never changes the (6,42) execution identity", async () => {
    const sandbox = setup();
    const c = ctrl(sandbox);
    const detail = v1Detail(6, 42, 1);
    const renamedRow = v2Row(6, 42, 99);
    assertEq(c.jobExecutionIdentity(detail.job), c.jobExecutionIdentity(renamedRow),
      "rename (name_revision 1 -> 99) leaves the identity equal");
    assertEq(c.isDetailStaleFor(detail, renamedRow), false, "rename alone must NOT mark stale");
    assertEq(executionOf(detail.job), executionOf(renamedRow), "attempt/revision byte-identical");
  });

  function setupPage(v1Adapter) {
    // Real page composition in ACP_Workbench_v2.html script order: client
    // store/query -> job detail controller -> shared-state -> energy.js
    // (page-global isPlainObject) -> job-detail.js page entry points. Only the
    // v1 transport adapter and the queue renderer (renderQueue, a view
    // dependency not under test) are faked — no DOM is built.
    const sandbox = makeSandbox({
      localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
      Intl: Intl,
      matchMedia: () => ({ matches: false, addListener: () => {}, removeListener: () => {}, addEventListener: () => {}, removeEventListener: () => {} }),
      requestAnimationFrame: () => 1,
      document: { getElementById: () => null },
      renderQueue: () => {},
    });
    loadScript("js/client/resource-store.js", sandbox);
    loadScript("js/client/job-detail-query.js", sandbox);
    loadScript("js/workbench/controllers/job_detail_controller.js", sandbox);
    loadScript("js/workbench/compat/shared-state.js", sandbox);
    loadScript("js/workbench/features/energy.js", sandbox);
    loadScript("js/workbench/features/job-detail.js", sandbox);
    sandbox.ACPClient.jobDetail.init({ v1: v1Adapter });
    return sandbox;
  }

  function setQueueRows(sandbox, rows) {
    // Identical statement to the live page's taskList onRows callback
    // (features/queue.js): "jobsCache = rows;" — run in the page context.
    vm.runInContext("jobsCache = " + JSON.stringify(rows), sandbox);
  }

  function pageValue(sandbox, expr) {
    return vm.runInContext(expr, sandbox);
  }

  function deferred() {
    let resolve, reject;
    const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
  }

  await test("D03 composition: fetchJobDetail accepts the same-execution (6,42) projection with equal identities", async () => {
    const seen = [];
    const sandbox = setupPage((path) => {
      seen.push(path);
      return Promise.resolve(v1Detail(6, 42, 1));
    });
    setQueueRows(sandbox, [v2Row(6, 42, 1)]);
    const accepted = await sandbox.fetchJobDetail(D03_ID);
    assert(accepted && accepted.job, "detail accepted through the real page entry point");
    assertEq(seen, ["/jobs/job-d03/detail"], "exactly one v1 detail fetch through the real client");
    const active = sandbox.getJobDetail(D03_ID);
    assertEq(executionOf(active.job), { attempt: 6, revision: 42 }, "active detail is (6,42)");
    const row = v2Row(6, 42, 1);
    assert(sandbox.jobExecutionIdentity(active.job) === sandbox.jobExecutionIdentity(row),
      "page entry point jobExecutionIdentity(detail.job) === jobExecutionIdentity(row)");
    assertEq(sandbox.jobExecutionIdentity(active.job), "job-d03\u00006\u000042", "concrete identity through the page");
    assertEq(sandbox.jobDetailCacheStaleFor(active, row), false, "page wrapper reports NOT stale");
  });

  await test("D03 composition: advancing the row to (7,43) invalidates and prunes the cached (6,42) detail", async () => {
    const sandbox = setupPage(() => Promise.resolve(v1Detail(6, 42, 1)));
    setQueueRows(sandbox, [v2Row(6, 42, 1)]);
    await sandbox.fetchJobDetail(D03_ID);
    const before = pageValue(sandbox, "jobDetailCacheRevision");
    setQueueRows(sandbox, [v2Row(7, 43, 1)]);
    const cached = sandbox.getJobDetail(D03_ID);
    assertEq(executionOf(cached.job), { attempt: 6, revision: 42 }, "cached detail is the (6,42) execution");
    assertEq(sandbox.jobDetailCacheStaleFor(cached, v2Row(7, 43, 1)), true, "(7,43) row marks the detail stale");
    sandbox.pruneJobDetailCacheAgainstList();
    assertEq(sandbox.getJobDetail(D03_ID), null, "pruned (6,42) entry never stays as the active detail");
    assertEq(pageValue(sandbox, "jobDetailCacheRevision"), before + 1, "prune bumps the cache revision");
  });

  await test("D03 composition: a name_revision-only rename keeps the cached detail fresh and active", async () => {
    const sandbox = setupPage(() => Promise.resolve(v1Detail(6, 42, 1)));
    setQueueRows(sandbox, [v2Row(6, 42, 99)]);
    const accepted = await sandbox.fetchJobDetail(D03_ID);
    assert(accepted && accepted.job, "detail accepted against the renamed row");
    assertEq(executionOf(accepted.job), { attempt: 6, revision: 42 });
    assertEq(sandbox.jobDetailCacheStaleFor(sandbox.getJobDetail(D03_ID), v2Row(6, 42, 99)), false,
      "name_revision 1 vs 99 alone is not stale");
    sandbox.pruneJobDetailCacheAgainstList();
    const active = sandbox.getJobDetail(D03_ID);
    assert(active && active.job, "prune must KEEP the rename-fresh detail");
    assert(sandbox.jobExecutionIdentity(active.job) === sandbox.jobExecutionIdentity(v2Row(6, 42, 99)),
      "identity stays equal after the rename");
    assertEq(sandbox.jobExecutionIdentity(active.job), "job-d03\u00006\u000042");
  });

  await test("D03 A->B->A: a late old-attempt response is rejected and never becomes the active detail", async () => {
    const dA = deferred();
    const dB = deferred();
    const answers = [dA.promise, dB.promise];
    const seen = [];
    const sandbox = setupPage((path) => {
      seen.push(path);
      return answers[seen.length - 1];
    });
    setQueueRows(sandbox, [v2Row(5, 41, 1)]);
    const pendingA = sandbox.fetchJobDetail(D03_ID);
    assertEq(seen, ["/jobs/job-d03/detail"], "request A (old execution) issued");
    sandbox.invalidateJobDetail(D03_ID);
    setQueueRows(sandbox, [v2Row(6, 42, 1)]);
    const pendingB = sandbox.fetchJobDetail(D03_ID);
    assertEq(seen.length, 2, "request B issued after the invalidation");
    dB.resolve(v1Detail(6, 42, 1));
    const acceptedB = await pendingB;
    assertEq(executionOf(acceptedB.job), { attempt: 6, revision: 42 }, "B (6,42) accepted first");
    dA.resolve(v1Detail(5, 41, 1));
    const lateA = await pendingA;
    assertEq(lateA, null, "late A (5,41) is rejected");
    const active = sandbox.getJobDetail(D03_ID);
    assertEq(executionOf(active.job), { attempt: 6, revision: 42 }, "B stays the active detail after late A");
    assertEq(sandbox.jobExecutionIdentity(active.job), "job-d03\u00006\u000042", "active identity is (6,42)");
    assertEq(seen.length, 2, "late A never triggers a third request");
  });

  await test("D03 stale replica: a (5,41) body under the current generation is pruned and never cached", async () => {
    const fresh = deferred();
    const stale = deferred();
    const answers = [fresh.promise, stale.promise];
    const seen = [];
    const sandbox = setupPage((path) => {
      seen.push(path);
      return answers[seen.length - 1];
    });
    setQueueRows(sandbox, [v2Row(6, 42, 1)]);
    const first = sandbox.fetchJobDetail(D03_ID);
    fresh.resolve(v1Detail(6, 42, 1));
    await first;
    const replica = sandbox.fetchJobDetail(D03_ID);
    stale.resolve(v1Detail(5, 41, 1));
    assertEq(await replica, null, "stale (5,41) replica pruned at the page composition");
    const active = sandbox.getJobDetail(D03_ID);
    assertEq(executionOf(active.job), { attempt: 6, revision: 42 }, "active detail stays (6,42)");
    assertEq(sandbox.jobExecutionIdentity(active.job), "job-d03\u00006\u000042", "pruned (5,41) never becomes active");
  });
};
