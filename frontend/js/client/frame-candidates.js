/*
 * ACP Client — frame-candidate CRUD (A-40/A-41).
 *
 * Mirrors the existing v1 endpoints exactly; the transport is injected by the
 * Shell bootstrap so timeout shaping and 409/422 error objects stay with
 * ACPClient.transport. Candidate writes are NOT task submission and never
 * touch the scheduler state.
 *
 *   GET    /api/v1/jobs/{job_id}/frame-candidates
 *   POST   /api/v1/jobs/{job_id}/frame-candidate
 *   DELETE /api/v1/jobs/{job_id}/frame-candidate/{candidate_id}?expected_revision=
 */
window.ACPClient = window.ACPClient || {};
ACPClient.frameCandidates = (function () {
  "use strict";

  var adapters = { v1: null };

  function init(deps) {
    deps = deps || {};
    if (typeof deps.v1 === "function") adapters.v1 = deps.v1;
  }

  function api(path, opts) {
    if (!adapters.v1) throw new Error("frameCandidates: transport not initialized");
    return adapters.v1(path, opts);
  }

  function encode(value) {
    return encodeURIComponent(String(value));
  }

  function list(jobId) {
    if (!jobId) return Promise.reject(new Error("frameCandidates.list: jobId required"));
    return api("/jobs/" + encode(jobId) + "/frame-candidates");
  }

  function save(jobId, payload) {
    if (!jobId) return Promise.reject(new Error("frameCandidates.save: jobId required"));
    if (!payload || typeof payload !== "object") {
      return Promise.reject(new Error("frameCandidates.save: payload required"));
    }
    return api("/jobs/" + encode(jobId) + "/frame-candidate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
  }

  function remove(jobId, candidateId, expectedRevision) {
    if (!jobId || !candidateId) {
      return Promise.reject(new Error("frameCandidates.remove: jobId and candidateId required"));
    }
    var url = "/jobs/" + encode(jobId) + "/frame-candidate/" + encode(candidateId);
    if (expectedRevision != null) url += "?expected_revision=" + encode(expectedRevision);
    return api(url, { method: "DELETE" });
  }

  return { init: init, list: list, save: save, remove: remove };
})();
