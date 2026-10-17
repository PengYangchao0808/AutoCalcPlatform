/*
 * ACP Workbench contracts — task list/detail display contract (pure, no DOM/network).
 *
 * OC-0 deliverable: the minimal pure contract + fixture samples for the
 * "task list / detail + one result view" first slice, derived from the real
 * S0 endpoint GET /api/v2/task-view (shape captured 2026-10-16 against the
 * running service; keys only — no user data is copied into this repo).
 *
 * Owner: OpenCode (frontend/js/workbench/contracts/**). Consumers:
 * ACPClient.taskView (client slice) and task list/result controllers.
 * Version: v20261016.1
 *
 * S0 response body (GET /api/v2/task-view):
 *   groups: [{ key, display_name, unassigned, retired, count, truncated,
 *              min_created_at, jobs: JobRow[] }]
 *   facets: { statuses: {<status>: int}, workflows: [{key,count}],
 *             molecules: [{key,name,count}], tags: [{tag,count}],
 *             batches: [{batch_id,min_created_at,count}] }   (include_facets=true)
 *   total: int, truncated: bool, counts: {<JobStatus>: int},
 *   query: { project_id, group_by, sort, statuses[], workflows[], molecule_keys[],
 *            tags[], batch_ids[], remarks[], search, archived, running_first },
 *   offset: int, limit: int, next_offset: int|null
 *
 * JobRow (V1JobRecordModel-compatible projection used by the queue list):
 *   id, status, group_id, project_id, project_name, created_at, updated_at,
 *   started_at, completed_at, last_activity_at, current_stage, progress,
 *   molecule_name, task_name, remark, display_name, task_dir_name, workflow,
 *   tags[], archived, batch_id, spec, custom_name, resolved_name, default_name,
 *   name_revision, name_updated_at, attempt, revision, stage_index, stage_total,
 *   stage_detail, progress_state, live_status, display_method
 *
 * Version semantics (client guards): `revision` = job row projection revision
 * (monotonic per job), `name_revision` = custom-name optimistic-lock revision
 * (PATCH /api/v2/tasks/{id} expected_name_revision), `attempt` = execution
 * attempt ordinal. S0 has NO cursor/persistent events — refresh is polling +
 * per-task SSE (/jobs/{id}/events), and the client must NOT invent revisions.
 */
window.ACPWorkbench = window.ACPWorkbench || {};
ACPWorkbench.contracts = ACPWorkbench.contracts || {};
ACPWorkbench.contracts.taskView = (function () {
  "use strict";

  var TERMINAL_STATUSES = ["completed", "failed", "cancelled"];
  var ACTIVE_STATUSES = ["queued", "starting", "pending", "running", "paused",
    "cancelling", "waiting_review"];

  function isTerminalStatus(status) {
    return TERMINAL_STATUSES.indexOf(String(status || "").toLowerCase()) !== -1;
  }

  function isActiveStatus(status) {
    return ACTIVE_STATUSES.indexOf(String(status || "").toLowerCase()) !== -1;
  }

  /** Structural validation of a task-view body against the S0 contract.
   * Returns { ok: bool, problems: string[] }. Unknown OPTIONAL extra fields are
   * ignored (S0 compatibility rule); missing REQUIRED fields are reported —
   * a failing body must surface as an error, never as an empty-success. */
  function validateBody(body) {
    var problems = [];
    if (!body || typeof body !== "object") return { ok: false, problems: ["body is not an object"] };
    if (!Array.isArray(body.groups)) problems.push("groups: expected array");
    if (typeof body.total !== "number") problems.push("total: expected number");
    if (typeof body.offset !== "number") problems.push("offset: expected number");
    if (body.counts && typeof body.counts !== "object") problems.push("counts: expected object");
    (Array.isArray(body.groups) ? body.groups : []).forEach(function (g, gi) {
      if (!g || typeof g !== "object") { problems.push("groups[" + gi + "]: not an object"); return; }
      if (!Array.isArray(g.jobs)) problems.push("groups[" + gi + "].jobs: expected array");
    });
    return { ok: problems.length === 0, problems: problems };
  }

  /** Display-model derivation, pure: flatten groups (group order preserved)
   * into the V1JobRecordModel-compatible row list the queue consumers use.
   * Mirrors the legacy refreshJobs flattening exactly (no re-sorting here). */
  function flattenRows(body) {
    var flat = [];
    if (body && Array.isArray(body.groups)) {
      body.groups.forEach(function (g) {
        if (g && Array.isArray(g.jobs)) g.jobs.forEach(function (row) { flat.push(row); });
      });
    }
    return flat;
  }

  /** Version tuple used for stale-detail pruning (GAP-9 in legacy
   * refreshJobs): a cached detail is inconsistent with the fresh list row
   * when status/attempt/revision disagree. name_revision is compared
   * separately by the rename flow (different optimistic lock). */
  function rowVersion(row) {
    return {
      status: row && row.status != null ? String(row.status) : null,
      attempt: row && row.attempt != null ? Number(row.attempt) : null,
      revision: row && row.revision != null ? Number(row.revision) : null,
    };
  }

  function sameVersion(a, b) {
    var va = rowVersion(a), vb = rowVersion(b);
    return va.status === vb.status && va.attempt === vb.attempt && va.revision === vb.revision;
  }

  function isOlderRow(candidate, known) {
    if (!candidate || !known) return false;
    var candidateId = candidate.id || candidate.job_id;
    var knownId = known.id || known.job_id;
    if (candidateId && knownId && String(candidateId) !== String(knownId)) return false;
    var a = rowVersion(candidate), b = rowVersion(known);
    if (a.attempt !== null && b.attempt !== null && a.attempt !== b.attempt) return a.attempt < b.attempt;
    if (a.revision !== null && b.revision !== null) {
      if (a.revision !== b.revision) return a.revision < b.revision;
      return isTerminalStatus(b.status) && isActiveStatus(a.status);
    }
    // Older servers may omit versions; never manufacture a revision.
    return false;
  }

  /** Capability note: S0 provides no event cursor / persistent events. Any
   * refresh policy must degrade to polling; per-task SSE stays per-task. */
  var capabilities = {
    revisions: true,          // job row revision + name_revision present
    facets: true,             // include_facets=true supported
    eventCursor: false,       // NOT in S0 — do not fabricate cursors
    globalEventStream: false, // NOT in S0 — per-task SSE only
    prepareAvailability: false, // NOT in S0 (AR-4 later)
  };

  return {
    TERMINAL_STATUSES: TERMINAL_STATUSES,
    ACTIVE_STATUSES: ACTIVE_STATUSES,
    isTerminalStatus: isTerminalStatus,
    isActiveStatus: isActiveStatus,
    validateBody: validateBody,
    flattenRows: flattenRows,
    rowVersion: rowVersion,
    sameVersion: sameVersion,
    isOlderRow: isOlderRow,
    capabilities: capabilities,
  };
})();
