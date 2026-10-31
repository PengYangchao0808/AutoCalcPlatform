/*
 * ACP Client — research commands (approval request/query/decide/revoke and the
 * submit of an already approved plan).
 *
 * Owner: frontend/js/client/** (OpenCode). M3-05b. Mirrors the seam of
 * research-query.js: every call goes through an injected request adapter
 * (`v1(url, opts)` from the page api(), `v2(path, opts)` from apiV2()); this
 * module owns no fetch and no API prefix. research-query.js stays read-only —
 * decisions live here and only here.
 *
 * The platform is the approval authority, so this module never *decides*
 * anything locally:
 *   - the authenticated principal comes from the Controller channel; no
 *     identity field is ever put into a payload (a payload approver is refused
 *     by the server with `approver_identity_from_payload`);
 *   - an approval record is adopted verbatim from the server response after a
 *     narrow structural check (ref + known state + positive revision); an
 *     unknown shape is a typed failure, never a silently "approved" card;
 *   - a submit is only reported as submitted when the Controller returned a
 *     real `job_id` + `attempt` + `task_ref`; a network failure is
 *     `submit_unconfirmed` (unknown, never success).
 *
 * Endpoints (relative paths; the adapter owns the prefix):
 *   POST /api/v2/research/approvals                      request/modify (draft)
 *   GET  /api/v2/research/approvals                      bounded query
 *   GET  /api/v2/research/approvals/{approval_ref}       read one record
 *   POST /api/v2/research/approvals/{ref}/approve        decision
 *   POST /api/v2/research/approvals/{ref}/reject         decision
 *   POST /api/v2/research/approvals/{ref}/revoke         revoke an approval
 *   POST /api/v1/jobs                                    submit the approved plan
 *
 * Version: v20261027.1
 */
window.ACPClient = window.ACPClient || {};
ACPClient.researchCommands = (function () {
  "use strict";

  //: The states the platform approval record can report (APPROVAL_STATES in
  //: acp_services/runtime/approvals.py). Anything else is refused.
  var APPROVAL_STATES = ["draft", "pending", "approved", "rejected", "expired", "revoked"];
  var DEFAULT_LIMIT = 50;
  var MAX_LIMIT = 200;

  var adapters = { v1: null, v2: null }; // injected: v1(url, opts), v2(path, opts)

  function init(deps) {
    deps = deps || {};
    if (typeof deps.v1 === "function") adapters.v1 = deps.v1;
    if (typeof deps.v2 === "function") adapters.v2 = deps.v2;
  }

  function isObject(value) {
    return !!value && typeof value === "object" && !Array.isArray(value);
  }

  function isText(value) {
    return typeof value === "string" && value.length > 0;
  }

  /** Typed command failure: `code` is the machine branch, the message keeps the
   * `code: detail` shape used by the workbench error plumbing. */
  function failure(code, message, extra) {
    extra = extra || {};
    var err = new Error(code + ": " + String(message == null ? "" : message));
    err.code = code;
    err.rule = typeof extra.rule === "string" ? extra.rule : null;
    err.httpStatus = extra.status == null ? null : extra.status;
    err.retryable = extra.retryable === true;
    err.detail = extra.detail == null ? null : extra.detail;
    if (extra.cause) err.cause = extra.cause;
    return err;
  }

  function requireAdapter(kind) {
    if (!adapters[kind]) {
      throw failure("platform_transport_required",
        "research commands require the injected " + kind + " transport " +
        "(ACPClient.researchCommands.init({v1, v2}))");
    }
    return adapters[kind];
  }

  function copy(value) {
    return isObject(value) ? JSON.parse(JSON.stringify(value)) : value;
  }

  //: HTTP status -> the platform error code used when the body carries none.
  var STATUS_CODES = {
    400: "input_invalid",
    403: "access_denied",
    404: "not_found",
    409: "revision_conflict",
    422: "input_invalid",
    429: "rate_limited",
  };

  /** Convert a transport/adapter error into a typed command failure. The
   * transport already carries `err.status`/`err.body` (FE-07 contract). */
  function failFromTransport(err, options) {
    options = options || {};
    if (err && typeof err.code === "string" && err.code) return err; // already typed
    var status = err && typeof err.status === "number" ? err.status : null;
    var detail = err && isObject(err.body) && isObject(err.body.detail) ? err.body.detail : null;
    var code = detail && isText(detail.code) ? detail.code
      : (status ? (STATUS_CODES[status] || "request_failed") : null);
    var rule = detail && isObject(detail.params) && isText(detail.params.rule)
      ? detail.params.rule : null;
    var message = (detail && isText(detail.message) ? detail.message : null)
      || (err && err.message) || "request failed";
    if (!status) {
      // No HTTP status: the outcome is unknown. A submit is never "success" and
      // never "failed" — it is unconfirmed until a read proves otherwise.
      return failure(options.transportCode || "transport_unavailable", message, {
        retryable: true,
        cause: err,
        detail: detail || undefined,
      });
    }
    return failure(code || options.fallbackCode || "request_failed", message, {
        status: status,
        rule: rule,
        retryable: !!(detail && detail.retryable),
        detail: detail || undefined,
        cause: err,
      });
  }

  function requestOptions(opts, body) {
    opts = opts || {};
    var out = { signal: opts.signal || null };
    if (opts.timeoutMs != null) out.timeoutMs = opts.timeoutMs;
    if (typeof opts.fetchImpl === "function") out.fetchImpl = opts.fetchImpl;
    if (body !== undefined) {
      out.method = opts.method || "POST";
      out.headers = Object.assign({ "Content-Type": "application/json" },
        opts.headers || {});
      out.body = JSON.stringify(body);
    }
    return out;
  }

  function segment(value) {
    return encodeURIComponent(String(value));
  }

  // ------------------------------------------------------------------
  // approval record: adopted from the server, structurally fail-closed
  // ------------------------------------------------------------------

  function normalizeRecord(raw, label) {
    if (!isObject(raw)) {
      throw failure("invalid_approval_record",
        (label || "approval") + " is not an object");
    }
    var ref = raw.approval_ref;
    if (!isText(ref)) {
      throw failure("invalid_approval_record", (label || "approval") + " has no approval_ref");
    }
    var status = isText(raw.status) ? raw.status : (isText(raw.state) ? raw.state : "");
    if (APPROVAL_STATES.indexOf(status) === -1) {
      throw failure("invalid_approval_record",
        "unknown approval status " + JSON.stringify(String(status)));
    }
    var revision = raw.revision;
    if (typeof revision !== "number" || !isFinite(revision) || Math.floor(revision) !== revision ||
        revision < 1) {
      throw failure("invalid_approval_record", "approval revision must be an integer >= 1");
    }
    var scienceDigest = isText(raw.science_digest) ? raw.science_digest : "";
    var declaredBinding = isText(raw.binding_status) ? raw.binding_status : "";
    if (declaredBinding === "full" && !scienceDigest) {
      throw failure("invalid_approval_record",
        "a full binding must carry a science digest");
    }
    return {
      schema_version: raw.schema_version == null ? 1 : raw.schema_version,
      approval_ref: ref,
      status: status,
      state: status,
      revision: revision,
      project_id: isText(raw.project_id) ? raw.project_id : "",
      proposal_ref: isText(raw.proposal_ref) ? raw.proposal_ref : "",
      proposal_digest: isText(raw.proposal_digest) ? raw.proposal_digest : "",
      plan_digest: isText(raw.plan_digest) ? raw.plan_digest : "",
      science_digest: scienceDigest,
      science_projection: copy(isObject(raw.science_projection) ? raw.science_projection : {}),
      // Derived from the digest, never trusted from the label alone.
      binding_status: scienceDigest ? "full" : "execution-only",
      plan_definition: copy(isObject(raw.plan_definition) ? raw.plan_definition : {}),
      budget: raw.budget == null ? null : copy(raw.budget),
      budget_digest: isText(raw.budget_digest) ? raw.budget_digest : "",
      allowed_change_range: copy(isObject(raw.allowed_change_range)
        ? raw.allowed_change_range : {}),
      requester_ref: isText(raw.requester_ref) ? raw.requester_ref : null,
      approver_ref: isText(raw.approver_ref) ? raw.approver_ref : null,
      expires_at: isText(raw.expires_at) ? raw.expires_at : null,
      requested_at: isText(raw.requested_at) ? raw.requested_at
        : (isText(raw.created_at) ? raw.created_at : null),
      decided_at: isText(raw.decided_at) ? raw.decided_at : null,
      reason: isText(raw.reason) ? raw.reason : null,
      task_ref: isObject(raw.task_ref) ? copy(raw.task_ref) : null,
      replayed: raw.replayed === true,
    };
  }

  function normalizeScope(scope) {
    scope = scope || {};
    var projectId = scope.projectId != null ? scope.projectId : scope.project_id;
    if (projectId != null && typeof projectId !== "string") {
      throw failure("input_invalid", "scope.projectId must be a string when present");
    }
    return { projectId: projectId || "" };
  }

  function approvalPath(ref) {
    return "/research/approvals/" + segment(ref);
  }

  // ------------------------------------------------------------------
  // commands
  // ------------------------------------------------------------------

  /** Request (or replay) the approval of one frozen plan.
   *
   * The client never computes a digest: either the platform Proposal body is
   * supplied (the server derives `proposal_digest`), or the caller supplies the
   * digest it read from the platform (`proposalDigest`). `scienceProjection` is
   * the C plan definition whose frozen digest the approval also binds. */
  function request(input, opts) {
    var v2 = requireAdapter("v2");
    input = input || {};
    var proposalRef = input.proposalRef != null ? input.proposalRef : input.proposal_ref;
    if (!isText(proposalRef)) {
      throw failure("input_invalid", "proposalRef is required");
    }
    var proposal = isObject(input.proposal) ? copy(input.proposal) : null;
    var proposalDigest = input.proposalDigest != null ? input.proposalDigest : input.proposal_digest;
    if (!proposal && !isText(proposalDigest)) {
      throw failure("input_invalid",
        "either the platform proposal body or its proposalDigest is required");
    }
    var plan = isObject(input.plan) ? copy(input.plan)
      : (isObject(input.planDefinition) ? copy(input.planDefinition) : null);
    var planDigest = input.planDigest != null ? input.planDigest : input.plan_digest;
    if (!plan && !isText(planDigest)) {
      throw failure("input_invalid", "either the execution plan body or its planDigest is required");
    }
    var scienceProjection = isObject(input.scienceProjection) ? copy(input.scienceProjection)
      : (isObject(input.science_projection) ? copy(input.science_projection) : null);
    var scope = normalizeScope(input.scope || input);
    var body = {
      proposal_ref: proposalRef,
      plan: plan,
      plan_digest: isText(planDigest) ? planDigest : null,
      science_projection: scienceProjection,
      science_digest: isText(input.scienceDigest) ? input.scienceDigest
        : (isText(input.science_digest) ? input.science_digest : null),
      budget: isObject(input.budget) ? copy(input.budget) : null,
      allowed_change_range: isObject(input.allowedChangeRange) ? copy(input.allowedChangeRange)
        : (isObject(input.allowed_change_range) ? copy(input.allowed_change_range) : null),
      expires_at: isText(input.expiresAt) ? input.expiresAt
        : (isText(input.expires_at) ? input.expires_at : null),
      idempotency_key: isText(input.idempotencyKey) ? input.idempotencyKey
        : (isText(input.idempotency_key) ? input.idempotency_key : null),
      draft: input.draft === true,
    };
    if (proposal) body.proposal = proposal;
    if (isText(proposalDigest)) body.proposal_digest = proposalDigest;
    if (scope.projectId) body.project_id = scope.projectId;
    return Promise.resolve()
      .then(function () { return v2("/research/approvals", requestOptions(opts, body)); })
      .then(function (raw) {
        return { approval: normalizeRecord(raw, "approval request") };
      }, function (err) {
        throw failFromTransport(err);
      });
  }

  /** Bounded query of the authoritative approval records of one project. */
  function query(scope, opts) {
    var v2 = requireAdapter("v2");
    var s = normalizeScope(scope || {});
    var limit = (opts && opts.limit != null) ? Math.max(1, Math.min(MAX_LIMIT, opts.limit))
      : DEFAULT_LIMIT;
    var params = ["limit=" + limit];
    if (s.projectId) params.push("project_id=" + segment(s.projectId));
    if (opts && isText(opts.approvalRef)) params.push("approval_ref=" + segment(opts.approvalRef));
    if (opts && isText(opts.proposalRef)) params.push("proposal_ref=" + segment(opts.proposalRef));
    if (opts && isText(opts.status)) params.push("status=" + segment(opts.status));
    return Promise.resolve()
      .then(function () { return v2("/research/approvals?" + params.join("&"), requestOptions(opts)); })
      .then(function (body) {
        if (!isObject(body) || !Array.isArray(body.approvals)) {
          throw failure("invalid_approval_record", "approval query response has no approvals list");
        }
        return {
          approvals: body.approvals.map(function (item) { return normalizeRecord(item, "approval"); }),
          snapshot_revision: typeof body.snapshot_revision === "number" ? body.snapshot_revision : 0,
        };
      }, function (err) {
        throw failFromTransport(err);
      });
  }

  /** Read exactly one approval record (single source of truth). */
  function fetchApproval(scope, approvalRef, opts) {
    var v2 = requireAdapter("v2");
    if (!isText(approvalRef)) throw failure("input_invalid", "approvalRef is required");
    var s = normalizeScope(scope || {});
    var suffix = s.projectId ? "?project_id=" + segment(s.projectId) : "";
    return Promise.resolve()
      .then(function () { return v2(approvalPath(approvalRef) + suffix, requestOptions(opts)); })
      .then(function (raw) { return { approval: normalizeRecord(raw, "approval") }; },
        function (err) { throw failFromTransport(err); });
  }

  function decisionBody(action) {
    action = action || {};
    if (typeof action.expectedRevision !== "number" ||
        Math.floor(action.expectedRevision) !== action.expectedRevision ||
        action.expectedRevision < 1) {
      throw failure("input_invalid", "expectedRevision (integer >= 1) is required");
    }
    var body = { expected_revision: action.expectedRevision };
    if (isText(action.reason)) body.reason = action.reason;
    if (isText(action.idempotencyKey)) body.idempotency_key = action.idempotencyKey;
    return body;
  }

  /** Approve or reject one pending approval (server decides, never the UI). */
  function decide(scope, approvalRef, action, opts) {
    var v2 = requireAdapter("v2");
    if (!isText(approvalRef)) throw failure("input_invalid", "approvalRef is required");
    action = action || {};
    var decision = action.decision;
    if (decision !== "approve" && decision !== "reject") {
      throw failure("decision_mismatch", "decision must be approve or reject");
    }
    var body = decisionBody(action);
    return Promise.resolve()
      .then(function () {
        return v2(approvalPath(approvalRef) + "/" + decision, requestOptions(opts, body));
      })
      .then(function (raw) { return { approval: normalizeRecord(raw, "approval decision") }; },
        function (err) { throw failFromTransport(err); });
  }

  /** Revoke an approval so it can never authorize a submit. */
  function revoke(scope, approvalRef, action, opts) {
    var v2 = requireAdapter("v2");
    if (!isText(approvalRef)) throw failure("input_invalid", "approvalRef is required");
    var body = decisionBody(action || {});
    return Promise.resolve()
      .then(function () {
        return v2(approvalPath(approvalRef) + "/revoke", requestOptions(opts, body));
      })
      .then(function (raw) { return { approval: normalizeRecord(raw, "approval revoke") }; },
        function (err) { throw failFromTransport(err); });
  }

  function submitError(code, message, extra) {
    return failure(code, message, extra || {});
  }

  /** Merge the approved binding into the ONE normalized submission body.
   *
   * This is not a second payload builder: the caller keeps supplying the
   * server-normalized request (the same body it preflighted). Only the three
   * approval binding fields are attached, and a local disagreement with the
   * approved record fails closed instead of being silently rewritten. */
  function bindSubmission(body, approval) {
    if (!isObject(body)) throw submitError("input_invalid", "a normalized submission body is required");
    if (!isObject(approval)) {
      throw submitError("approval_required", "an approved record is required to submit");
    }
    if (approval.status !== "approved") {
      throw submitError("approval_not_granted", "approval status is " + approval.status);
    }
    if (!isText(approval.plan_digest) || !isText(approval.proposal_ref)) {
      throw submitError("approval_card_invalid", "the approved record is missing its binding");
    }
    var payload = copy(body);
    if (isText(payload.plan_digest) && payload.plan_digest !== approval.plan_digest) {
      throw submitError("approved_plan_digest_mismatch",
        "the submitted plan digest differs from the approved plan");
    }
    payload.plan_digest = approval.plan_digest;
    if (isText(payload.proposal_ref) && payload.proposal_ref !== approval.proposal_ref) {
      throw submitError("approved_proposal_mismatch",
        "the submitted proposal differs from the approved proposal");
    }
    payload.proposal_ref = approval.proposal_ref;
    if (isText(payload.proposal_digest) && isText(approval.proposal_digest) &&
        payload.proposal_digest !== approval.proposal_digest) {
      throw submitError("approved_proposal_digest_mismatch",
        "the proposal changed after approval");
    }
    if (isText(approval.proposal_digest)) payload.proposal_digest = approval.proposal_digest;
    if (isObject(payload.budget) && isObject(approval.budget) &&
        JSON.stringify(payload.budget) !== JSON.stringify(approval.budget)) {
      throw submitError("approved_budget_mismatch", "the requested budget changed after approval");
    }
    if (!isObject(payload.budget) && isObject(approval.budget)) {
      payload.budget = copy(approval.budget);
    }
    return payload;
  }

  /** Submit the approved plan. Only a real TaskRef is reported as submitted. */
  function submit(scope, body, opts) {
    var v1 = requireAdapter("v1");
    opts = opts || {};
    var payload = bindSubmission(body, opts.approval);
    if (isText(opts.idempotencyKey) && !isText(payload.idempotency_key)) {
      payload.idempotency_key = opts.idempotencyKey;
    }
    return Promise.resolve()
      .then(function () { return v1("/jobs", requestOptions(opts, payload)); })
      .then(function (raw) {
        var result = {
          job_id: isObject(raw) && isText(raw.job_id) ? raw.job_id : null,
          attempt: isObject(raw) && typeof raw.attempt === "number" && raw.attempt >= 1
            ? raw.attempt : null,
          task_ref: isObject(raw) && isObject(raw.task_ref) ? copy(raw.task_ref) : null,
          status: isObject(raw) && isText(raw.status) ? raw.status : null,
          workflow: isObject(raw) && isText(raw.workflow) ? raw.workflow : null,
          project_id: isObject(raw) && isText(raw.project_id) ? raw.project_id : null,
          plan_digest: isObject(raw) && isText(raw.plan_digest) ? raw.plan_digest : null,
          proposal_digest: isObject(raw) && isText(raw.proposal_digest) ? raw.proposal_digest : null,
          approval_ref: isObject(raw) && isText(raw.approval_ref) ? raw.approval_ref : null,
          submit_class: isObject(raw) && isText(raw.submit_class) ? raw.submit_class : null,
          idempotency_key: isObject(raw) && isText(raw.idempotency_key)
            ? raw.idempotency_key : null,
          idempotent_replay: isObject(raw) && raw.idempotent_replay === true,
          replayed: isObject(raw) && raw.idempotent_replay === true,
          submitted: true,
        };
        var taskId = result.task_ref && (result.task_ref.task_id || result.task_ref.job_id);
        if (!result.job_id || !result.attempt || !isText(taskId)) {
          throw submitError("submit_unconfirmed",
            "the Controller did not return a job_id, attempt and task_ref");
        }
        if (result.task_ref.attempt == null) result.task_ref.attempt = result.attempt;
        return result;
      }, function (err) {
        throw failFromTransport(err, { transportCode: "submit_unconfirmed" });
      });
  }

  return {
    write: true,
    APPROVAL_STATES: APPROVAL_STATES,
    init: init,
    request: request,
    query: query,
    fetchApproval: fetchApproval,
    decide: decide,
    revoke: revoke,
    submit: submit,
    bindSubmission: bindSubmission,
    normalizeRecord: normalizeRecord,
  };
})();
