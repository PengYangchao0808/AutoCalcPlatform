/*
 * ACP Workbench contracts — ACP 2.0 experience read models (pure, no DOM/network).
 *
 * Owner: frontend/js/workbench/contracts/** (OpenCode). Consumers:
 * ACPClient.science / ACPClient.research (client slice) and the Workbench
 * feature panels.
 * Version: v20261022.1
 *
 * The validators mirror the frozen C contracts under
 * docs/reference/next-change/acp2-contracts/experience/:
 *   view-model.schema.json        -> validateViewModel
 *   research-record.schema.json   -> validateResearchRecord
 *   proposal.schema.json          -> validateProposal
 * plus the read-only capability/preflight/status projections from
 * tool-semantics.md.
 *
 * They are intentionally structural: a body that violates the frozen enum
 * (e.g. a fifth artifact availability state, a task-state field on a Proposal,
 * a DSH transcript posing as a ResearchRecord) is reported as a problem and
 * never silently accepted. Unknown OPTIONAL extra fields are ignored, matching
 * the S0 compatibility rule in task_view_contract.js.
 */
window.ACPWorkbench = window.ACPWorkbench || {};
ACPWorkbench.contracts = ACPWorkbench.contracts || {};
ACPWorkbench.contracts.experience = (function () {
  "use strict";

  var AVAILABILITY_STATES = ["unprepared", "preparing", "readable", "failed"];
  var CLAIM_STATES = ["supported", "refuted", "inconclusive", "not_evaluated"];
  var EVIDENCE_STATUSES = ["complete", "partial", "incomplete", "failed"];
  var PROPOSAL_DECISIONS = ["proposed", "approved", "rejected", "superseded", "deferred"];
  var RECORD_DECISIONS = ["accept", "revise", "reject", "defer", "escalate"];
  var ACP_TOOLS = [
    "acp_capabilities", "acp_preflight", "acp_submit", "acp_status",
    "acp_cancel", "acp_prepare_artifact", "acp_read_artifact", "acp_analyze",
    "acp_get_guidance", "acp_list_jobs",
  ];
  var TASK_STATE_KEYS = ["status", "state", "lifecycle", "job_id", "attempt",
    "queue_position", "running", "queued"];
  var SESSION_KEYS = ["session_id", "chat_id", "messages", "transcript",
    "conversation", "model_role"];

  var DIGEST_RE = /^sha256:[0-9a-f]{64}$/;
  var PRODUCER_RE = /^(?:[0-9a-f]{40}|sha256:[0-9a-f]{64})$/;
  var PROPOSAL_ID_RE = /^prop-[A-Za-z0-9][A-Za-z0-9._:-]*$/;
  var RESEARCH_ID_RE = /^res-[A-Za-z0-9][A-Za-z0-9._:-]*$/;
  var SHA256_RE = /^[0-9a-f]{64}$/;

  function isObject(value) {
    return !!value && typeof value === "object" && !Array.isArray(value);
  }
  function isText(value) {
    return typeof value === "string" && value.length > 0;
  }
  function isInteger(value, minimum) {
    return typeof value === "number" && isFinite(value) &&
      Math.floor(value) === value && value >= minimum;
  }
  function hasOwn(object, key) {
    return Object.prototype.hasOwnProperty.call(object, key);
  }
  function requireText(problems, object, key, prefix) {
    if (!isText(object[key])) problems.push(prefix + key + ": expected non-empty string");
  }
  function requireInteger(problems, object, key, minimum, prefix) {
    if (!isInteger(object[key], minimum)) {
      problems.push(prefix + key + ": expected integer >= " + minimum);
    }
  }
  function requireArray(problems, object, key, prefix) {
    if (!Array.isArray(object[key])) problems.push(prefix + key + ": expected array");
    return Array.isArray(object[key]) ? object[key] : [];
  }
  function requireObject(problems, object, key, prefix) {
    if (!isObject(object[key])) problems.push(prefix + key + ": expected object");
    return isObject(object[key]) ? object[key] : null;
  }
  function forbiddenKeys(problems, body, keys, label) {
    keys.forEach(function (key) {
      if (hasOwn(body, key)) problems.push(key + ": forbidden on a " + label);
    });
  }
  function result(problems) {
    return { ok: problems.length === 0, problems: problems };
  }

  function taskRefProblems(taskRef, prefix) {
    var problems = [];
    if (!isObject(taskRef)) {
      problems.push(prefix + ": expected object");
      return problems;
    }
    requireText(problems, taskRef, "job_id", prefix + ".");
    requireInteger(problems, taskRef, "attempt", 1, prefix + ".");
    if (hasOwn(taskRef, "task_id") && taskRef.task_id !== null && !isText(taskRef.task_id)) {
      problems.push(prefix + ".task_id: expected string or null");
    }
    return problems;
  }

  /** view-model.schema.json structural check. Four availability states and the
   * read-only constants are the machine-readable safety statements. */
  function validateViewModel(body) {
    var problems = [];
    if (!isObject(body)) return result(["body is not an object"]);
    requireInteger(problems, body, "snapshot_revision", 0, "");
    requireText(problems, body, "project_id", "");
    problems = problems.concat(taskRefProblems(body.task_ref, "task_ref"));
    requireInteger(problems, body, "manifest_generation", 0, "");

    var artifacts = requireArray(problems, body, "artifacts", "");
    artifacts.forEach(function (item, index) {
      var prefix = "artifacts[" + index + "].";
      if (!isObject(item)) { problems.push(prefix + ": not an object"); return; }
      requireText(problems, item, "artifact_id", prefix);
      requireText(problems, item, "role", prefix);
      requireInteger(problems, item, "generation", 0, prefix);
      if (AVAILABILITY_STATES.indexOf(item.availability) === -1) {
        problems.push(prefix + "availability: not one of " + AVAILABILITY_STATES.join("|"));
      }
      if (hasOwn(item, "sha256") && item.sha256 !== null && !SHA256_RE.test(item.sha256)) {
        problems.push(prefix + "sha256: expected 64 lowercase hex characters or null");
      }
      if (hasOwn(item, "size_bytes") && item.size_bytes !== null && !isInteger(item.size_bytes, 0)) {
        problems.push(prefix + "size_bytes: expected integer >= 0 or null");
      }
    });

    var claims = requireArray(problems, body, "claims", "");
    claims.forEach(function (item, index) {
      var prefix = "claims[" + index + "].";
      if (!isObject(item)) { problems.push(prefix + ": not an object"); return; }
      requireText(problems, item, "claim_id", prefix);
      requireText(problems, item, "statement", prefix);
      if (CLAIM_STATES.indexOf(item.status) === -1) {
        problems.push(prefix + "status: not one of " + CLAIM_STATES.join("|"));
      }
      var refs = requireArray(problems, item, "evidence_refs", prefix);
      refs.forEach(function (ref) {
        if (!isText(ref)) problems.push(prefix + "evidence_refs: expected non-empty strings");
      });
    });

    requireArray(problems, body, "clarifications", "");
    var provenance = requireObject(problems, body, "provenance", "");
    if (provenance) {
      requireText(problems, provenance, "source", "provenance.");
      if (!PRODUCER_RE.test(String(provenance.producer_revision || ""))) {
        problems.push("provenance.producer_revision: expected 40-hex or sha256:<64 hex>");
      }
      requireText(problems, provenance, "produced_at", "provenance.");
      if (!DIGEST_RE.test(String(provenance.digest || ""))) {
        problems.push("provenance.digest: expected sha256:<64 hex>");
      }
    }
    if (body.read_only !== true) problems.push("read_only: expected true");
    if (body.not_authoritative_for_science !== true) {
      problems.push("not_authoritative_for_science: expected true");
    }
    return result(problems);
  }

  /** acp_capabilities projection: protocol version plus the closed raw tool
   * names. A model-visible `mcp__acp__` prefix is a bridge mistake, not a tool. */
  function validateCapabilities(body) {
    var problems = [];
    if (!isObject(body)) return result(["body is not an object"]);
    requireText(problems, body, "protocol_version", "");
    if (!Array.isArray(body.tools) || body.tools.length === 0) {
      problems.push("tools: expected non-empty array");
    } else {
      body.tools.forEach(function (tool) {
        if (!isText(tool)) problems.push("tools: expected non-empty strings");
        else if (tool.indexOf("mcp__acp__") === 0) {
          problems.push("tools: raw server names must not carry the mcp__acp__ prefix");
        } else if (ACP_TOOLS.indexOf(tool) === -1) {
          problems.push("tools: not in the frozen allowlist: " + tool);
        }
      });
    }
    if (hasOwn(body, "capabilities") && !isObject(body.capabilities)) {
      problems.push("capabilities: expected object");
    }
    return result(problems);
  }

  /** acp_preflight projection must prove zero side effects. */
  function validatePreflight(body) {
    var problems = [];
    if (!isObject(body)) return result(["body is not an object"]);
    if (body.executed !== false) problems.push("executed: preflight must be executed=false");
    if (body.metering_consumed !== false) {
      problems.push("metering_consumed: preflight must not consume metering");
    }
    if (body.transfers_attempted !== false) {
      problems.push("transfers_attempted: preflight must not transfer");
    }
    requireObject(problems, body, "plan", "");
    requireObject(problems, body, "estimate", "");
    return result(problems);
  }

  /** acp_status projection scoped to one (job, attempt). */
  function validateTaskStatus(body) {
    var problems = [];
    if (!isObject(body)) return result(["body is not an object"]);
    problems = problems.concat(taskRefProblems(body.task_ref, "task_ref"));
    requireText(problems, body, "status", "");
    if (hasOwn(body, "revision") && !isInteger(body.revision, 0)) {
      problems.push("revision: expected integer >= 0");
    }
    return result(problems);
  }

  /** proposal.schema.json structural check (task state is never a Proposal). */
  function validateProposal(body) {
    var problems = [];
    if (!isObject(body)) return result(["body is not an object"]);
    var required = ["proposal_id", "project_id", "question_ref", "question_revision",
      "reaction_ref", "reaction_revision", "hypothesis", "recipe_digest",
      "plan_definition_digest", "science", "budget", "approval_ref",
      "allowed_change_scope", "round", "decision", "created_at", "human_edits"];
    required.forEach(function (key) {
      if (!hasOwn(body, key)) problems.push(key + ": required");
    });
    if (hasOwn(body, "proposal_id") && !PROPOSAL_ID_RE.test(String(body.proposal_id || ""))) {
      problems.push("proposal_id: expected prop-<id>");
    }
    requireText(problems, body, "project_id", "");
    requireText(problems, body, "hypothesis", "");
    if (hasOwn(body, "recipe_digest") && !DIGEST_RE.test(String(body.recipe_digest || ""))) {
      problems.push("recipe_digest: expected sha256:<64 hex>");
    }
    if (hasOwn(body, "plan_definition_digest") &&
        !DIGEST_RE.test(String(body.plan_definition_digest || ""))) {
      problems.push("plan_definition_digest: expected sha256:<64 hex>");
    }
    if (hasOwn(body, "round") && !isInteger(body.round, 0)) {
      problems.push("round: expected integer >= 0");
    }
    if (hasOwn(body, "decision") && PROPOSAL_DECISIONS.indexOf(body.decision) === -1) {
      problems.push("decision: not one of " + PROPOSAL_DECISIONS.join("|"));
    }
    if (hasOwn(body, "approval_ref") && body.approval_ref !== null && !isText(body.approval_ref)) {
      problems.push("approval_ref: expected string or null");
    }
    if (hasOwn(body, "science") && !isObject(body.science)) {
      problems.push("science: expected object");
    }
    if (hasOwn(body, "budget") && !isObject(body.budget)) {
      problems.push("budget: expected object");
    }
    if (hasOwn(body, "allowed_change_scope") && !isObject(body.allowed_change_scope)) {
      problems.push("allowed_change_scope: expected object");
    }
    if (hasOwn(body, "human_edits") && !Array.isArray(body.human_edits)) {
      problems.push("human_edits: expected array");
    }
    forbiddenKeys(problems, body, TASK_STATE_KEYS, "Proposal");
    return result(problems);
  }

  /** research-record.schema.json structural check. Every TaskRef must carry the
   * containing record's project and a release_id; a chat session is not a record. */
  function validateResearchRecord(body) {
    var problems = [];
    if (!isObject(body)) return result(["body is not an object"]);
    if (hasOwn(body, "research_id") && !RESEARCH_ID_RE.test(String(body.research_id || ""))) {
      problems.push("research_id: expected res-<id>");
    }
    requireText(problems, body, "project_id", "");
    if (hasOwn(body, "round") && !isInteger(body.round, 0)) {
      problems.push("round: expected integer >= 0");
    }
    if (hasOwn(body, "decision") && RECORD_DECISIONS.indexOf(body.decision) === -1) {
      problems.push("decision: not one of " + RECORD_DECISIONS.join("|"));
    }
    if (hasOwn(body, "plan_definition_digest") &&
        !DIGEST_RE.test(String(body.plan_definition_digest || ""))) {
      problems.push("plan_definition_digest: expected sha256:<64 hex>");
    }
    if (hasOwn(body, "approval_ref") && body.approval_ref !== null && !isText(body.approval_ref)) {
      problems.push("approval_ref: expected string or null");
    }
    if (hasOwn(body, "record_revision") && !isInteger(body.record_revision, 1)) {
      problems.push("record_revision: expected integer >= 1");
    }

    var taskRefs = requireArray(problems, body, "task_refs", "");
    taskRefs.forEach(function (item, index) {
      var prefix = "task_refs[" + index + "].";
      if (!isObject(item)) { problems.push(prefix + ": not an object"); return; }
      requireText(problems, item, "project_id", prefix);
      requireText(problems, item, "job_id", prefix);
      requireInteger(problems, item, "attempt", 1, prefix);
      requireText(problems, item, "release_id", prefix);
      if (isText(item.project_id) && isText(body.project_id) &&
          item.project_id !== body.project_id) {
        problems.push(prefix + "project_id: cross-project TaskRef");
      }
    });

    var evidenceRefs = requireArray(problems, body, "evidence_refs", "");
    evidenceRefs.forEach(function (item, index) {
      var prefix = "evidence_refs[" + index + "].";
      if (!isObject(item)) { problems.push(prefix + ": not an object"); return; }
      requireText(problems, item, "evidence_id", prefix);
      requireText(problems, item, "kind", prefix);
      if (!DIGEST_RE.test(String(item.sha256 || ""))) {
        problems.push(prefix + "sha256: expected sha256:<64 hex>");
      }
    });
    requireArray(problems, body, "missing_validation", "");
    requireArray(problems, body, "human_edits", "");
    requireArray(problems, body, "step_identity_refs", "");
    forbiddenKeys(problems, body, SESSION_KEYS, "ResearchRecord");
    return result(problems);
  }

  /** `acp2.runtime.evidence-projection.v1` structural check (M1-06).
   *
   * The platform projection is the only evidence document a consumer may read:
   * execution status and claim status are separate fields, a decision is never
   * merged into a task status, and every cited evidence ref must be digest
   * bound. This validator mirrors the frozen payload; it never re-derives a
   * claim status and never recomputes a digest (the server owns both).
   */
  function validateEvidenceView(body) {
    var problems = [];
    if (!isObject(body)) return result(["body is not an object"]);
    if (body.schema !== "acp2.runtime.evidence-projection.v1") {
      problems.push("schema: expected acp2.runtime.evidence-projection.v1");
    }
    var research = requireObject(problems, body, "research", "");
    if (research !== null) {
      requireText(problems, research, "principal_id", "research.");
      requireText(problems, research, "project_id", "research.");
      if (hasOwn(research, "research_id") &&
          !RESEARCH_ID_RE.test(String(research.research_id || ""))) {
        problems.push("research.research_id: expected res-<id>");
      }
      requireText(problems, research, "session_id", "research.");
      requireInteger(problems, research, "round", 0, "research.");
    }
    var taskRef = requireObject(problems, body, "task_ref", "");
    if (taskRef !== null) {
      requireText(problems, taskRef, "project_id", "task_ref.");
      requireText(problems, taskRef, "task_id", "task_ref.");
      requireInteger(problems, taskRef, "attempt", 1, "task_ref.");
      var release = requireObject(problems, taskRef, "release", "task_ref.");
      if (release !== null) {
        requireText(problems, release, "release_id", "task_ref.release.");
        if (!DIGEST_RE.test(String(release.release_digest || ""))) {
          problems.push("task_ref.release.release_digest: expected sha256:<64 hex>");
        }
      }
    }
    var report = requireObject(problems, body, "report", "");
    if (report !== null) {
      requireText(problems, report, "report_id", "report.");
      if (!DIGEST_RE.test(String(report.digest || ""))) {
        problems.push("report.digest: expected sha256:<64 hex>");
      }
      requireInteger(problems, report, "schema_version", 1, "report.");
      requireText(problems, report, "subject_id", "report.");
    }
    requireText(problems, body, "execution_terminal_state", "");
    requireText(problems, body, "qualification", "");
    if (hasOwn(body, "evidence_status") && EVIDENCE_STATUSES.indexOf(body.evidence_status) === -1) {
      problems.push("evidence_status: not one of " + EVIDENCE_STATUSES.join("|"));
    }
    if (hasOwn(body, "overall_claim_status") &&
        CLAIM_STATES.indexOf(body.overall_claim_status) === -1) {
      problems.push("overall_claim_status: not one of " + CLAIM_STATES.join("|"));
    }
    var claims = requireArray(problems, body, "claims", "");
    claims.forEach(function (item, index) {
      var prefix = "claims[" + index + "].";
      if (!isObject(item)) { problems.push(prefix + ": not an object"); return; }
      requireText(problems, item, "claim_id", prefix);
      if (CLAIM_STATES.indexOf(item.status) === -1) {
        problems.push(prefix + "status: not one of " + CLAIM_STATES.join("|"));
      }
    });
    var refs = requireArray(problems, body, "evidence_refs", "");
    refs.forEach(function (item, index) {
      var prefix = "evidence_refs[" + index + "].";
      if (!isObject(item)) { problems.push(prefix + ": not an object"); return; }
      requireText(problems, item, "evidence_id", prefix);
      requireText(problems, item, "kind", prefix);
      if (!DIGEST_RE.test(String(item.sha256 || ""))) {
        problems.push(prefix + "sha256: expected sha256:<64 hex>");
      }
    });
    requireArray(problems, body, "blocking_missing_checks", "");
    requireArray(problems, body, "limitations", "");
    if (!DIGEST_RE.test(String(body.projection_digest || ""))) {
      problems.push("projection_digest: expected sha256:<64 hex>");
    }
    if (hasOwn(body, "normal_reuse") && typeof body.normal_reuse !== "boolean") {
      problems.push("normal_reuse: expected boolean");
    }
    return result(problems);
  }

  /** Display mapping only; the schema above rejects any value outside the four
   * states. Unknown values are surfaced as `failed`, never as a fifth state. */
  function normalizeAvailability(value) {
    return AVAILABILITY_STATES.indexOf(value) === -1 ? "failed" : value;
  }

  function isClaimStatus(value) {
    return CLAIM_STATES.indexOf(value) !== -1;
  }

  return {
    AVAILABILITY_STATES: AVAILABILITY_STATES,
    CLAIM_STATES: CLAIM_STATES,
    PROPOSAL_DECISIONS: PROPOSAL_DECISIONS,
    RECORD_DECISIONS: RECORD_DECISIONS,
    ACP_TOOLS: ACP_TOOLS,
    validateViewModel: validateViewModel,
    validateCapabilities: validateCapabilities,
    validatePreflight: validatePreflight,
    validateTaskStatus: validateTaskStatus,
    validateProposal: validateProposal,
    validateResearchRecord: validateResearchRecord,
    validateEvidenceView: validateEvidenceView,
    EVIDENCE_STATUSES: EVIDENCE_STATUSES,
    normalizeAvailability: normalizeAvailability,
    isClaimStatus: isClaimStatus,
  };
})();
