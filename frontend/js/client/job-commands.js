/*
 * ACP Client — job commands (pause, unpause, continue, rerun, cancel, purge,
 * batch ops, rename, move, clone).
 *
 * Owner: frontend/js/client/** (OpenCode).
 *
 * R3 slice (a): unified task commands (plan §42.3 R3 row + §42.4 red lines).
 *   - Adapters are injected via init({ v1, v2, onInvalidate }); the page binds
 *     its api()/apiV2() transports, so timeout handling, error shaping
 *     (err.status/err.body preserved verbatim for 409/422) and the apiV2
 *     non-GET task-view metadata invalidation stay with the transport.
 *   - onInvalidate fires only after a successful receipt — never on failure
 *     or abort.
 *   - No automatic retry on timeouts for any command (§42.4).
 *   - rerun deduplicates concurrent submissions via an in-flight set: the
 *     second call rejects without hitting the adapter. The page pre-checks
 *     isRerunInFlight to keep its silent early-return UX.
 *   - Endpoints/methods/bodies mirror the page's previous api()/apiV2()
 *     calls exactly; no extra fields are added.
 */
window.ACPClient = window.ACPClient || {};
ACPClient.jobCommands = (function () {
  "use strict";

  var adapters = { v1: null, v2: null };
  var rerunInFlight = new Set();
  var onInvalidateHook = null;

  function init(deps) {
    deps = deps || {};
    if (typeof deps.v1 === "function") adapters.v1 = deps.v1;
    if (typeof deps.v2 === "function") adapters.v2 = deps.v2;
    if (typeof deps.onInvalidate === "function") onInvalidateHook = deps.onInvalidate;
  }

  function triggerInvalidate(jobId) {
    if (onInvalidateHook) {
      try {
        onInvalidateHook(jobId);
      } catch (e) {
        console.error("Invalidate hook failed", e);
      }
    }
  }

  // The injected adapters ARE the page's api()/apiV2() transports; the
  // commands below call them under those names so the transport call sites
  // moved out of queue.js/molecule-view.js stay visible to the api("/
  // call-site drift guard in the frontend sync tests.
  function api(path, opts) { return adapters.v1(path, opts); }
  function apiV2(path, opts) { return adapters.v2(path, opts); }

  async function cmdPause(jobId) {
    if (!jobId) throw new Error("pause: jobId required");
    var result = await api("/jobs/" + encodeURIComponent(jobId) + "/pause", { method: "POST" });
    triggerInvalidate(jobId);
    return result;
  }

  async function cmdUnpause(jobId) {
    if (!jobId) throw new Error("unpause: jobId required");
    var result = await api("/jobs/" + encodeURIComponent(jobId) + "/unpause", { method: "POST" });
    triggerInvalidate(jobId);
    return result;
  }

  async function cmdContinue(jobId, targetNode) {
    if (!jobId) throw new Error("continue: jobId required");
    var opts = { method: "POST" };
    if (targetNode) {
      opts.headers = { "Content-Type": "application/json" };
      opts.body = JSON.stringify({ target_node: targetNode });
    }
    var result = await api("/jobs/" + encodeURIComponent(jobId) + "/continue", opts);
    triggerInvalidate(jobId);
    return result;
  }

  async function cmdRerun(jobId) {
    if (!jobId) throw new Error("rerun: jobId required");
    var key = String(jobId);
    if (rerunInFlight.has(key)) {
      throw new Error("rerun already in flight for job " + key);
    }
    rerunInFlight.add(key);
    try {
      var result = await api("/jobs/" + encodeURIComponent(key) + "/rerun", { method: "POST" });
      triggerInvalidate(key);
      return result;
    } finally {
      rerunInFlight.delete(key);
    }
  }

  async function cmdCancel(jobId) {
    if (!jobId) throw new Error("cancel: jobId required");
    var result = await api("/jobs/" + encodeURIComponent(jobId) + "/cancel", { method: "POST" });
    triggerInvalidate(jobId);
    return result;
  }

  // Canonical form: purge({ jobIds, deleteData }). A bare array is still
  // accepted (delete_data false) so earlier page call sites keep working.
  async function cmdPurge(request) {
    var jobIds = Array.isArray(request) ? request : (request && request.jobIds);
    var deleteData = Array.isArray(request) ? false : !!(request && request.deleteData);
    if (!jobIds || !jobIds.length) return null;
    var result = await api("/jobs/purge", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_ids: jobIds, delete_data: deleteData === true }),
    });
    jobIds.forEach(function (id) { triggerInvalidate(id); });
    return result;
  }

  async function cmdBatchOp(request) {
    var taskIds = request && request.taskIds;
    var op = request && request.op;
    if (!taskIds || !taskIds.length || !op) return null;
    var result = await apiV2("/tasks/batch-ops", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task_ids: taskIds, op: op, payload: (request.payload || {}) }),
    });
    taskIds.forEach(function (id) { triggerInvalidate(id); });
    return result;
  }

  async function cmdRename(jobId, customName, expectedRevision) {
    if (!jobId) throw new Error("rename: jobId required");
    var result = await apiV2("/tasks/" + encodeURIComponent(jobId), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        custom_name: customName === undefined ? null : customName,
        expected_name_revision: expectedRevision,
      }),
    });
    triggerInvalidate(jobId);
    return result;
  }

  async function cmdMove(jobId, projectId) {
    if (!jobId) throw new Error("move: jobId required");
    var result = await api("/jobs/" + encodeURIComponent(jobId) + "/move", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_id: projectId }),
    });
    triggerInvalidate(jobId);
    return result;
  }

  async function cmdClone(jobId, projectId) {
    if (!jobId) throw new Error("clone: jobId required");
    var result = await api("/jobs/" + encodeURIComponent(jobId) + "/clone", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_id: projectId }),
    });
    triggerInvalidate(jobId);
    return result;
  }

  return {
    init: init,
    pause: cmdPause,
    unpause: cmdUnpause,
    continueJob: cmdContinue,
    rerun: cmdRerun,
    cancel: cmdCancel,
    purge: cmdPurge,
    batchOp: cmdBatchOp,
    rename: cmdRename,
    move: cmdMove,
    clone: cmdClone,
    isRerunInFlight: function (jobId) {
      return rerunInFlight.has(String(jobId));
    },
  };
})();
