/*
 * ACP Workbench controller — job detail and recovery matrix controller.
 *
 * Owner: frontend/js/workbench/controllers/** (OpenCode).
 * Pure business logic and recovery rules; interacts with views and clients via injection.
 *
 * v20261017.1 (R1b): this module is the SINGLE SOURCE for the job-detail pure
 * logic — execution identity, default recovery matrix, server-wins recovery
 * merge, detail staleness, and detail-cache pruning. workbench/features/
 * job-detail.js delegates here through same-name page entry points and keeps
 * only the page-side side effects (jobDetailCacheRevision bump, detail cache
 * projection). This file must stay DOM/network/library-free — pinned by
 * module_tests/job_detail_controller.test.js.
 */
window.ACPWorkbench = window.ACPWorkbench || {};
ACPWorkbench.controllers = ACPWorkbench.controllers || {};
ACPWorkbench.controllers.jobDetail = (function () {
  "use strict";

  function isPlainObject(o) {
    return o != null && typeof o === "object" && !Array.isArray(o);
  }

  function isTerminalJobStatus(status) {
    var s = String(status || "").toLowerCase();
    return s === "completed" || s === "failed" || s === "cancelled";
  }

  function isActiveJobStatus(status) {
    var s = String(status || "").toLowerCase();
    return (
      s === "queued" ||
      s === "starting" ||
      s === "running" ||
      s === "cancelling" ||
      s === "pending" ||
      s === "waiting_review" ||
      s === "paused"
    );
  }

  // GAP-9: execution-version identity from task 5 — jobs-authoritative
  // (job_id, attempt, revision).
  // name_revision is organization naming only and must NEVER participate here.
  function jobExecutionIdentity(job) {
    if (!job) return "";
    var id = String(job.id || job.job_id || "");
    var attempt = typeof job.attempt === "number" && isFinite(job.attempt) ? String(job.attempt) : "";
    var revision = typeof job.revision === "number" && isFinite(job.revision) ? String(job.revision) : "";
    return id + "\u0000" + attempt + "\u0000" + revision;
  }

  function computeDefaultRecovery(job) {
    var status = String((job && job.status) || "").toLowerCase();
    var workflow = job && job.spec && job.spec.workflow ? String(job.spec.workflow) : "";
    var terminal = isTerminalJobStatus(status);
    var continuable =
      (workflow === "mechanism" || workflow === "xtbmd_censo_energy") &&
      (status === "failed" || status === "cancelled");
    return {
      can_pause: status === "running",
      can_unpause: status === "paused",
      can_continue: continuable,
      continue_mode: continuable ? "checkpoint" : "",
      continue_notes: "",
      can_rerun: terminal,
      can_cancel: isActiveJobStatus(status),
      can_purge: true,
    };
  }

  function resolveJobRecovery(job, detail) {
    var server = detail && isPlainObject(detail.recovery) ? detail.recovery : null;
    var fallback = computeDefaultRecovery(job);
    if (!server) return fallback;
    var merged = {};
    ["can_pause", "can_unpause", "can_continue", "can_rerun", "can_cancel", "can_purge"].forEach(function (key) {
      merged[key] = typeof server[key] === "boolean" ? server[key] : fallback[key];
    });
    merged.continue_mode = typeof server.continue_mode === "string" ? server.continue_mode : fallback.continue_mode;
    merged.continue_notes = typeof server.continue_notes === "string" ? server.continue_notes : fallback.continue_notes;
    return merged;
  }

  function isDetailStaleFor(detail, row) {
    if (!detail || !isPlainObject(detail.job) || !row) return false;
    if (String(detail.job.status || "").toLowerCase() !== String(row.status || "").toLowerCase()) return true;
    return jobExecutionIdentity(detail.job) !== jobExecutionIdentity(row);
  }

  function pruneAgainstList(cache, rows) {
    if (!cache || !rows) return false;
    var changed = false;
    var rowMap = Object.create(null);
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i];
      var key = String(r.id || r.job_id || "");
      // First row wins on a duplicated key — exact parity with the live page
      // scan (findJobRowInCache returns the first list match).
      if (key && !rowMap[key]) rowMap[key] = r;
    }
    Object.keys(cache).forEach(function (key) {
      var row = rowMap[key];
      if (row && isDetailStaleFor(cache[key], row)) {
        delete cache[key];
        changed = true;
      }
    });
    return changed;
  }

  return {
    isTerminalJobStatus: isTerminalJobStatus,
    isActiveJobStatus: isActiveJobStatus,
    jobExecutionIdentity: jobExecutionIdentity,
    computeDefaultRecovery: computeDefaultRecovery,
    resolveJobRecovery: resolveJobRecovery,
    isDetailStaleFor: isDetailStaleFor,
    pruneAgainstList: pruneAgainstList,
  };
})();
