/*
 * ACP Workbench — persistent research approval card (T-27 / C19a / M3-05b).
 *
 * Owner: frontend/js/workbench/features/**. One card is the approval surface
 * for one frozen research Proposal. It renders the proposal/recipe digest, the
 * scientific limits, the execution plan summary, the resource caps, the cost
 * basis and the approval expiry, and it binds an approval EXACTLY to the
 * platform record (science digest + execution plan digest + project + budget).
 *
 * M3-05b — the platform is the only approval authority:
 *   * a decision/revoke/submit goes through the injected
 *     `ACPClient.researchCommands` adapter (v1/v2 transport). This module owns
 *     no network call of its own; without commands every action throws
 *     `platform_transport_required` and the card states that visibly;
 *   * the pre-M3-05 local lifecycle (locally invented approval ids, dispatch
 *     refs and the in-module pending→approved/revoked transitions) is GONE:
 *     status, revision, approver and task_ref always come from the server
 *     record;
 *   * a 409 (stale revision) never replays the decision: the card refreshes and
 *     asks the human to re-review the new record;
 *   * a submit is reported as submitted only when the Controller returned a
 *     real job_id + attempt + TaskRef; a network failure stays "unconfirmed".
 *
 * Contract rules that still hold (docs/reference/next-change/acp2-contracts/
 * experience/plan-identity.md §3):
 *   * changing method / mapping / initial input / budget invalidates the
 *     approval because they change the frozen science digest;
 *   * appending TaskRef/evidence refs, or changing display name / timestamps,
 *     never invalidates it — they sit outside the digest projection;
 *   * DSH tool approval and platform research approval are separate mappings;
 *     a DSH tool approval can never change platform approval state;
 *   * a model claim of "user agreed" is never an approval, and no identity
 *     field is ever put into a platform payload.
 *
 * Version: v20261027.2
 */
window.ACPResearchApproval = (function () {
  "use strict";

  // ---------------------------------------------------------------------
  // constants (plan-identity.md §1.1 / §3; approval record states)
  // ---------------------------------------------------------------------

  var SCIENCE_FIELDS = [
    "capability_steps", "initial_inputs", "method", "mapping",
    "charge", "multiplicity", "allowed_substitutions",
  ];
  var BUDGET_FIELDS = [
    "model_requests", "input_tokens", "output_tokens", "task_refs",
    "recipe_steps", "qc_invocations", "wall_seconds", "queue_seconds",
  ];

  /** The two approval mappings stay separate: the DSH tool decision is a
   * session-local record and mutates nothing on the platform side. */
  var APPROVAL_MAPPINGS = {
    platform_research: {
      id: "platform_research",
      authority: "platform",
      mutates: ["approval_record"],
      operations: ["approval.request", "approval.query", "approval.approve",
        "approval.reject", "approval.revoke", "research.submit"],
    },
    dsh_tool: {
      id: "dsh_tool",
      authority: "dsh_session",
      mutates: [],
      operations: ["dsh.tool_decision"],
    },
  };

  /** Every state the platform approval record can report (APPROVAL_STATES).
   * Anything else is NOT approved: the gate below fails closed. */
  var STATUS = {
    DRAFT: "draft",
    PENDING: "pending",
    APPROVED: "approved",
    REJECTED: "rejected",
    REVOKED: "revoked",
    EXPIRED: "expired",
  };

  var KNOWN_STATUSES = [
    STATUS.DRAFT, STATUS.PENDING, STATUS.APPROVED,
    STATUS.REJECTED, STATUS.REVOKED, STATUS.EXPIRED,
  ];

  var PLATFORM_TRANSPORT_REQUIRED = "platform_transport_required";

  // ---------------------------------------------------------------------
  // module state (the server record is the only authority)
  // ---------------------------------------------------------------------

  var deps = {
    research: null,
    commands: null,
    taskView: null,
    now: null,
    buildSubmitBody: null,
    onTaskRef: null,
  };
  var domDoc = null;
  var rootEl = null;
  var eventsBound = false;
  var state = {
    mounted: false,
    disposed: false,
    projectId: "",
    proposalRef: "",
    approvalRef: "",
    proposal: null,
    plan: null,
    record: null,
    card: null,
    submitted: false,
    lastSubmit: null,
    reviewRequired: false,
    notice: null,
    lastError: null,
    busy: false,
  };

  // ---------------------------------------------------------------------
  // small helpers
  // ---------------------------------------------------------------------

  function isObject(value) {
    return !!value && typeof value === "object" && !Array.isArray(value);
  }

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function text(value, fallback) {
    if (value == null || value === "") return fallback == null ? "—" : fallback;
    return String(value);
  }

  function tr(key, fallback, vars) {
    if (typeof t === "function") {
      var value = t(key, vars);
      if (value && value !== key) return value;
    }
    if (vars) {
      Object.keys(vars).forEach(function (name) {
        fallback = fallback.split("{" + name + "}").join(String(vars[name]));
      });
    }
    return fallback;
  }

  function shortDigest(digest) {
    if (typeof digest === "string" && digest) return digest.slice(0, 19) + "…";
    return "—";
  }

  /** Typed failure: `code` is the machine branch, the message keeps the
   * `code: detail` shape used by the workbench error plumbing. */
  function fail(code, detail) {
    var error = new Error(code + ": " + detail);
    error.code = code;
    throw error;
  }

  /** A typed failure value (for the two places that reject instead of throw). */
  function commandFailure(code, detail) {
    var error = new Error(code + ": " + detail);
    error.code = code;
    return error;
  }

  /** Run one command body so every validation refusal is a rejected promise
   * (the public command API never throws synchronously). */
  function guarded(fn) {
    return Promise.resolve().then(fn);
  }

  function nowMs() {
    if (typeof deps.now === "function") return deps.now();
    return Date.now();
  }

  function pageProjectId() {
    try {
      if (typeof selectedProjectId === "string") return selectedProjectId;
    } catch (error) { /* not in the page scope (vm tests) */ }
    return "";
  }

  function compactJson(value) {
    if (value == null) return "—";
    try {
      return JSON.stringify(value);
    } catch (error) {
      return String(value);
    }
  }

  // ---------------------------------------------------------------------
  // sha256 (pure JS; zero dependency, vm-realm safe)
  // ---------------------------------------------------------------------

  var SHA_K = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
    0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
    0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
    0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
    0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
    0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
    0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
    0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
  ];

  function utf8Bytes(str) {
    var out = [];
    for (var i = 0; i < str.length; i += 1) {
      var code = str.charCodeAt(i);
      if (code < 0x80) {
        out.push(code);
      } else if (code < 0x800) {
        out.push(0xc0 | (code >> 6), 0x80 | (code & 0x3f));
      } else if (code >= 0xd800 && code <= 0xdbff && i + 1 < str.length) {
        var next = str.charCodeAt(i + 1);
        if (next >= 0xdc00 && next <= 0xdfff) {
          var point = 0x10000 + ((code - 0xd800) << 10) + (next - 0xdc00);
          out.push(
            0xf0 | (point >> 18), 0x80 | ((point >> 12) & 0x3f),
            0x80 | ((point >> 6) & 0x3f), 0x80 | (point & 0x3f)
          );
          i += 1;
        } else {
          out.push(0xe0 | (code >> 12), 0x80 | ((code >> 6) & 0x3f), 0x80 | (code & 0x3f));
        }
      } else {
        out.push(0xe0 | (code >> 12), 0x80 | ((code >> 6) & 0x3f), 0x80 | (code & 0x3f));
      }
    }
    return out;
  }

  function rotr(value, bits) {
    return ((value >>> bits) | (value << (32 - bits))) | 0;
  }

  function sha256Hex(bytes) {
    var h = [
      0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
      0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
    ];
    var length = bytes.length;
    var padded = bytes.slice();
    padded.push(0x80);
    while (padded.length % 64 !== 56) padded.push(0);
    var bitLength = length * 8;
    var low = bitLength % 0x100000000;
    var high = (bitLength - low) / 0x100000000;
    padded.push(
      (high >>> 24) & 0xff, (high >>> 16) & 0xff, (high >>> 8) & 0xff, high & 0xff,
      (low >>> 24) & 0xff, (low >>> 16) & 0xff, (low >>> 8) & 0xff, low & 0xff
    );
    var w = new Array(64);
    for (var offset = 0; offset < padded.length; offset += 64) {
      var i;
      for (i = 0; i < 16; i += 1) {
        var base = offset + i * 4;
        w[i] = ((padded[base] << 24) | (padded[base + 1] << 16) |
          (padded[base + 2] << 8) | padded[base + 3]) | 0;
      }
      for (i = 16; i < 64; i += 1) {
        var s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
        var s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
        w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0;
      }
      var a = h[0], b = h[1], c = h[2], d = h[3];
      var e = h[4], f = h[5], g = h[6], hh = h[7];
      for (i = 0; i < 64; i += 1) {
        var bigS1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
        var ch = (e & f) ^ (~e & g);
        var t1 = (hh + bigS1 + ch + SHA_K[i] + w[i]) | 0;
        var bigS0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
        var maj = (a & b) ^ (a & c) ^ (b & c);
        var t2 = (bigS0 + maj) | 0;
        hh = g; g = f; f = e; e = (d + t1) | 0;
        d = c; c = b; b = a; a = (t1 + t2) | 0;
      }
      h[0] = (h[0] + a) | 0; h[1] = (h[1] + b) | 0;
      h[2] = (h[2] + c) | 0; h[3] = (h[3] + d) | 0;
      h[4] = (h[4] + e) | 0; h[5] = (h[5] + f) | 0;
      h[6] = (h[6] + g) | 0; h[7] = (h[7] + hh) | 0;
    }
    var hex = "";
    for (var index = 0; index < 8; index += 1) {
      var part = h[index] >>> 0;
      hex += ("00000000" + part.toString(16)).slice(-8);
    }
    return hex;
  }

  // ---------------------------------------------------------------------
  // plan_definition_digest (plan-identity.md §1 — projection, not a second
  // runtime implementation; byte-for-byte mirror of the frozen algorithm)
  // ---------------------------------------------------------------------

  function dropNulls(value) {
    if (Array.isArray(value)) return value.map(dropNulls);
    if (isObject(value)) {
      var out = {};
      Object.keys(value).forEach(function (key) {
        var child = value[key];
        if (child === null || child === undefined) return;
        out[key] = dropNulls(child);
      });
      return out;
    }
    return value;
  }

  function compareCodePoints(left, right) {
    var a = Array.from(left);
    var b = Array.from(right);
    var length = Math.min(a.length, b.length);
    for (var index = 0; index < length; index += 1) {
      var leftPoint = a[index].codePointAt(0);
      var rightPoint = b[index].codePointAt(0);
      if (leftPoint !== rightPoint) return leftPoint - rightPoint;
    }
    return a.length - b.length;
  }

  function normalizeDigestValue(value, path) {
    if (value === null) fail("null_in_digest_input", "null at " + path);
    if (typeof value === "boolean") return value;
    if (typeof value === "number") {
      if (!isFinite(value) || Math.floor(value) !== value) {
        fail("non_canonical_number", "non-integer number at " + path);
      }
      return value;
    }
    if (typeof value === "string") return value.normalize("NFC");
    if (Array.isArray(value)) {
      return value.map(function (child, index) {
        return normalizeDigestValue(child, path + "[" + index + "]");
      });
    }
    if (isObject(value)) {
      var out = {};
      Object.keys(value).sort(compareCodePoints).forEach(function (key) {
        out[key] = normalizeDigestValue(value[key], path + "." + key);
      });
      return out;
    }
    fail("unsupported_value", "unsupported value at " + path);
    return value;
  }

  function canonicalJson(value) {
    if (typeof value === "string") return JSON.stringify(value.normalize("NFC"));
    if (typeof value === "number") {
      if (Math.floor(value) !== value) fail("non_canonical_number", "floats are not canonical");
      return String(value);
    }
    if (typeof value === "boolean") return value ? "true" : "false";
    if (Array.isArray(value)) {
      return "[" + value.map(canonicalJson).join(",") + "]";
    }
    if (isObject(value)) {
      return "{" + Object.keys(value).sort(compareCodePoints).map(function (key) {
        return JSON.stringify(key) + ":" + canonicalJson(value[key]);
      }).join(",") + "}";
    }
    fail("unsupported_value", "unsupported value in canonical JSON");
    return "";
  }

  /** The exact frozen projection: recipe_digest + 7 science fields + 8 budget
   * counters. Everything else (approval state, timestamps, display names,
   * appended TaskRef/evidence refs) is outside it. */
  function planDefinitionProjection(proposal) {
    if (!isObject(proposal)) fail("root_not_object", "proposal must be an object");
    var science = proposal.science;
    var budget = proposal.budget;
    if (!isObject(science) || !isObject(budget)) {
      fail("root_not_object", "proposal.science and proposal.budget must be objects");
    }
    var projectedScience = {};
    SCIENCE_FIELDS.forEach(function (field) {
      projectedScience[field] = science[field];
    });
    var projectedBudget = {};
    BUDGET_FIELDS.forEach(function (field) {
      projectedBudget[field] = budget[field];
    });
    return {
      recipe_digest: proposal.recipe_digest,
      science: projectedScience,
      budget: projectedBudget,
    };
  }

  function computePlanDefinitionDigest(proposal) {
    var projection = planDefinitionProjection(proposal);
    var normalized = normalizeDigestValue(dropNulls(projection), "$");
    var bytes = utf8Bytes(canonicalJson(normalized));
    return "sha256:" + sha256Hex(bytes);
  }

  // ---------------------------------------------------------------------
  // server record -> card (the only card the UI renders)
  // ---------------------------------------------------------------------

  /** Map one platform approval record onto the card model. The binding is the
   * frozen science digest the platform signed plus the execution plan digest;
   * the launch budget is shown separately (the server enforces it on submit).
   * FAIL CLOSED: an unknown/missing state or a malformed binding is a typed
   * error, never a silently "approved" card. */
  function cardFromRecord(record) {
    if (!isObject(record)) {
      fail("approval_card_invalid", "an approval record is required");
    }
    var ref = record.approval_ref;
    if (typeof ref !== "string" || !ref) {
      fail("approval_card_invalid", "the approval record has no approval_ref");
    }
    var status = typeof record.status === "string" ? record.status : record.state;
    if (KNOWN_STATUSES.indexOf(status) === -1) {
      fail("approval_status_unknown", "unknown approval status " + JSON.stringify(String(status)));
    }
    var scienceDigest = typeof record.science_digest === "string" ? record.science_digest : "";
    return {
      source: "server",
      approval_ref: ref,
      status: status,
      revision: typeof record.revision === "number" ? record.revision : null,
      project_id: typeof record.project_id === "string" ? record.project_id : null,
      proposal_ref: typeof record.proposal_ref === "string" ? record.proposal_ref : null,
      proposal_digest: typeof record.proposal_digest === "string" ? record.proposal_digest : null,
      requested_by: record.requester_ref == null ? null : record.requester_ref,
      approver_ref: record.approver_ref == null ? null : record.approver_ref,
      requested_at: record.requested_at == null ? null : record.requested_at,
      expires_at: record.expires_at == null ? null : record.expires_at,
      decided_at: record.decided_at == null ? null : record.decided_at,
      decided_by: record.approver_ref == null ? null : record.approver_ref,
      decision: record.status === STATUS.APPROVED ? "approve"
        : (record.status === STATUS.REJECTED ? "reject"
          : (record.status === STATUS.REVOKED ? "revoke" : null)),
      reason: record.reason == null ? null : record.reason,
      task_ref: isObject(record.task_ref) ? JSON.parse(JSON.stringify(record.task_ref)) : null,
      binding_status: scienceDigest ? "full" : "execution-only",
      binding: {
        plan_definition_digest: scienceDigest,
        execution_plan_digest: typeof record.plan_digest === "string" ? record.plan_digest : "",
        project_id: record.project_id == null ? null : record.project_id,
      },
      plan_definition: isObject(record.plan_definition)
        ? JSON.parse(JSON.stringify(record.plan_definition)) : {},
      budget: isObject(record.budget) ? JSON.parse(JSON.stringify(record.budget)) : null,
    };
  }

  // ---------------------------------------------------------------------
  // validity against the CURRENT proposal (fail closed on every branch)
  // ---------------------------------------------------------------------

  /** Validity of one card against the CURRENT proposal fields. The digest is
   * recomputed from the fields, never trusted from a stored claim. FAIL
   * CLOSED: a null/malformed card, an unknown status, a missing or unparseable
   * expiry, an unbound science/execution digest or a changed science projection
   * is never valid. */
  function approvalValidity(card, proposal) {
    if (!isObject(card)) {
      return { valid: false, reason: "not_requested", current_digest: null, bound_digest: null };
    }
    var bound = isObject(card.binding) ? card.binding.plan_definition_digest : null;
    if (!isObject(card.binding) || typeof card.status !== "string" || !card.status) {
      return {
        valid: false,
        reason: "approval_card_invalid",
        current_digest: null,
        bound_digest: bound || null,
      };
    }
    var current = null;
    try {
      current = isObject(proposal) ? computePlanDefinitionDigest(proposal) : null;
    } catch (error) {
      return {
        valid: false,
        reason: "approval_binding_mismatch",
        current_digest: null,
        bound_digest: bound || null,
      };
    }
    if (KNOWN_STATUSES.indexOf(card.status) === -1) {
      return {
        valid: false,
        reason: "approval_status_unknown",
        current_digest: current,
        bound_digest: bound || null,
      };
    }
    if (card.status === STATUS.DRAFT) {
      return {
        valid: false,
        reason: "approval_draft",
        current_digest: current,
        bound_digest: bound || null,
      };
    }
    if (card.status === STATUS.REVOKED) {
      return { valid: false, reason: "approval_revoked", current_digest: current, bound_digest: bound || null };
    }
    if (card.status === STATUS.REJECTED) {
      return { valid: false, reason: "approval_rejected", current_digest: current, bound_digest: bound || null };
    }
    // The platform approval may legitimately carry no expiry (None = no
    // expiry). Only an unparseable non-empty value is invalid; a missing one is
    // never silently "expired".
    if (card.expires_at != null && card.expires_at !== "") {
      if (isNaN(Date.parse(card.expires_at))) {
        return {
          valid: false,
          reason: "approval_expiry_invalid",
          current_digest: current,
          bound_digest: bound || null,
        };
      }
      if (isExpired(card)) {
        return {
          valid: false,
          reason: "approval_expired",
          current_digest: current,
          bound_digest: bound || null,
        };
      }
    }
    if (card.status === STATUS.PENDING) {
      return { valid: false, reason: "approval_not_granted", current_digest: current, bound_digest: bound || null };
    }
    if (!isObject(proposal)) {
      return {
        valid: false,
        reason: "proposal_required",
        current_digest: null,
        bound_digest: bound || null,
      };
    }
    if (!bound) {
      // An execution-only approval carries no science digest; it can never be
      // shown as a full scientific approval.
      return {
        valid: false,
        reason: "approval_science_unbound",
        current_digest: current,
        bound_digest: null,
      };
    }
    if (!card.binding.execution_plan_digest) {
      return {
        valid: false,
        reason: "approval_execution_unbound",
        current_digest: current,
        bound_digest: bound,
      };
    }
    if (current !== bound) {
      return {
        valid: false,
        reason: "approval_binding_mismatch",
        current_digest: current,
        bound_digest: bound,
      };
    }
    if (card.binding.project_id && proposal.project_id !== card.binding.project_id) {
      return {
        valid: false,
        reason: "approval_binding_mismatch",
        current_digest: current,
        bound_digest: bound,
      };
    }
    return { valid: true, reason: "ok", current_digest: current, bound_digest: bound };
  }

  function isExpired(card) {
    if (!isObject(card) || !card.expires_at) return false;
    var expiry = Date.parse(card.expires_at);
    if (isNaN(expiry)) return false;
    return expiry <= nowMs();
  }

  // ---------------------------------------------------------------------
  // principals: a model claim is never a user approval
  // ---------------------------------------------------------------------

  function principalKind(principal) {
    if (!isObject(principal)) return "unknown";
    if (principal.kind === "model" || principal.source === "model" || principal.model_role) {
      return "model";
    }
    if (principal.kind === "user" && typeof principal.user_id === "string" && principal.user_id) {
      return "user";
    }
    return "unknown";
  }

  /** Only a verified human principal may decide or submit. A chat/session
   * claim that "the user agreed" carries no authority at all. */
  function isUserApprovalAuthority(principal) {
    return principalKind(principal) === "user";
  }

  /** A chat/session claim such as "the user agreed" is never an approval:
   * whatever it asserts, it carries no authority on this card. */
  function assertNotModelApprovalClaim(claim) {
    if (isObject(claim) && (claim.user_agreed === true || claim.user_approved === true ||
        claim.kind === "model" || claim.source === "model" || claim.model_role)) {
      fail("model_claim_not_authority",
        "a model claim of user agreement is never an approval");
    }
    return claim;
  }

  function assertUserPrincipal(principal) {
    if (!isUserApprovalAuthority(principal)) {
      fail("model_claim_not_authority",
        "a model or session claim is never user approval authority");
    }
    return principal;
  }

  // ---------------------------------------------------------------------
  // platform commands (injected; no local approval authority)
  // ---------------------------------------------------------------------

  function requireCommands() {
    if (deps.commands && typeof deps.commands.query === "function" &&
        typeof deps.commands.decide === "function") {
      return deps.commands;
    }
    fail(PLATFORM_TRANSPORT_REQUIRED,
      "research approvals require ACPClient.researchCommands " +
      "(init({commands}) or the page researchCommands.init({v1, v2}))");
  }

  function recordNotice(code, error) {
    state.notice = code || null;
    state.lastError = error ? {
      code: error.code || code || null,
      httpStatus: error.httpStatus == null ? null : error.httpStatus,
      rule: error.rule || null,
    } : null;
  }

  function invalidateResearchCache() {
    var research = deps.research || (window.ACPClient && ACPClient.research) || null;
    if (research && typeof research.invalidateAll === "function") research.invalidateAll();
  }

  function adoptRecord(record) {
    if (!isObject(record)) return null;
    state.record = JSON.parse(JSON.stringify(record));
    state.card = cardFromRecord(state.record);
    state.approvalRef = String(state.record.approval_ref || "");
    return state.card;
  }

  /** Read the authoritative record (query by approval_ref / proposal_ref, or
   * the newest record of the project when neither is known). */
  function refresh(options) {
    options = options || {};
    var scope = { projectId: options.projectId != null ? options.projectId : (state.projectId || pageProjectId()) };
    var approvalRef = options.approvalRef || state.approvalRef
      || (state.record && state.record.approval_ref) || "";
    var proposalRef = options.proposalRef || state.proposalRef || "";
    var queryOpts = {};
    if (approvalRef) queryOpts.approvalRef = approvalRef;
    else if (proposalRef) queryOpts.proposalRef = proposalRef;
    if (options.status) queryOpts.status = options.status;
    state.busy = options.silent !== true ? true : state.busy;
    return guarded(function () { return requireCommands().query(scope, queryOpts); })
      .then(function (body) {
        var list = (body && Array.isArray(body.approvals)) ? body.approvals : [];
        if (!list.length) {
          state.record = null;
          state.card = null;
          // An empty project-wide query is the normal "nothing yet" state; only
          // an explicitly addressed record that is missing is a notice.
          recordNotice(approvalRef ? "approval_not_found" : null, null);
          if (options.silent !== true) render();
          return null;
        }
        adoptRecord(list[0]);
        recordNotice(null, null);
        if (options.silent !== true) render();
        return state.record;
      }, function (error) {
        recordNotice((error && error.code) || "request_failed", error);
        if (options.silent !== true) render();
        throw error;
      })
      .finally(function () {
        if (options.silent !== true) state.busy = false;
      });
  }

  function refreshSilently(options) {
    return refresh(Object.assign({}, options || {}, { silent: true }))
      .catch(function () { return null; });
  }

  /** Request the approval of the frozen science plan (server-derived digests;
   * the client sends the C plan definition, never a computed digest). */
  function requestApproval(input) {
    return guarded(function () {
      input = input || {};
      var commands = requireCommands();
      var proposal = isObject(input.proposal) ? input.proposal : state.proposal;
      if (!isObject(proposal)) {
        fail("proposal_required", "requestApproval needs a proposal");
      }
      var plan = isObject(input.plan) ? input.plan : state.plan;
      var payload = {
        proposalRef: proposal.proposal_id,
        proposal: input.platformProposal,
        proposalDigest: input.proposalDigest,
        scienceProjection: proposal,
        plan: plan,
        planDigest: input.planDigest,
        budget: input.budget,
        allowedChangeRange: input.allowedChangeRange || proposal.allowed_change_scope,
        expiresAt: input.expiresAt,
        idempotencyKey: input.idempotencyKey,
        draft: input.draft === true,
        scope: { projectId: proposal.project_id || state.projectId || pageProjectId() },
      };
      return commands.request(payload, input.options || input);
    }).then(function (result) {
      adoptRecord(result.approval);
      recordNotice(null, null);
      state.reviewRequired = false;
      invalidateResearchCache();
      render();
      return state.card;
    }, function (error) {
      return handleActionError(error, input);
    });
  }

  function expectedRevisionOf(options) {
    if (options && typeof options.expectedRevision === "number") return options.expectedRevision;
    if (state.card && typeof state.card.revision === "number") return state.card.revision;
    return null;
  }

  /** Approve or reject the record the server currently holds. The decision is
   * sent with the observed revision; the server is the authority. */
  function decide(decision, options) {
    return guarded(function () {
      options = options || {};
      var commands = requireCommands();
      var card = state.card;
      if (!card) fail("not_requested", "no approval record");
      if (card.status !== STATUS.PENDING) {
        fail("already_decided", "approval status is " + card.status);
      }
      if (decision !== "approve" && decision !== "reject") {
        fail("decision_mismatch", "decision must be approve or reject");
      }
      assertNotModelApprovalClaim(options.approvalClaim);
      var revision = expectedRevisionOf(options);
      if (typeof revision !== "number") {
        fail("revision_required", "the observed approval revision is required");
      }
      return commands.decide(
        { projectId: card.project_id || state.projectId || pageProjectId() },
        card.approval_ref,
        {
          decision: decision,
          expectedRevision: revision,
          reason: options.reason,
          idempotencyKey: options.idempotencyKey,
        },
        options
      );
    }).then(function (result) {
      adoptRecord(result.approval);
      recordNotice(null, null);
      state.reviewRequired = false;
      invalidateResearchCache();
      render();
      return state.card;
    }, function (error) {
      return handleActionError(error, options);
    });
  }

  /** Revoke an approved record so it can never authorize a submit. */
  function revoke(options) {
    return guarded(function () {
      options = options || {};
      var commands = requireCommands();
      var card = state.card;
      if (!card) fail("not_requested", "no approval record");
      if (card.status !== STATUS.APPROVED) {
        fail("not_approved", "approval status is " + card.status);
      }
      var revision = expectedRevisionOf(options);
      if (typeof revision !== "number") {
        fail("revision_required", "the observed approval revision is required");
      }
      return commands.revoke(
        { projectId: card.project_id || state.projectId || pageProjectId() },
        card.approval_ref,
        { expectedRevision: revision, reason: options.reason, idempotencyKey: options.idempotencyKey },
        options
      );
    }).then(function (result) {
      adoptRecord(result.approval);
      recordNotice(null, null);
      state.reviewRequired = false;
      state.submitted = false;
      invalidateResearchCache();
      render();
      return state.card;
    }, function (error) {
      return handleActionError(error, options);
    });
  }

  function canSubmitPlan(card, proposal) {
    return approvalValidity(card, proposal).valid === true;
  }

  function submitBodyOf(options) {
    if (isObject(options && options.body)) return options.body;
    if (typeof deps.buildSubmitBody === "function") return deps.buildSubmitBody();
    var questionModule = window.ACPScienceQuestion;
    if (questionModule && typeof questionModule.researchSubmitBody === "function") {
      return questionModule.researchSubmitBody();
    }
    return null;
  }

  function taskViewOf() {
    return deps.taskView || (window.ACPClient && ACPClient.taskView) || null;
  }

  /** Refresh the approval record and the task view after a real submit. */
  function refreshAfterSubmit(taskRef) {
    var jobId = taskRef && (taskRef.task_id || taskRef.job_id);
    var view = taskViewOf();
    var viewRefreshed = Promise.resolve();
    if (view && typeof view.invalidateAll === "function") view.invalidateAll();
    if (view && typeof view.fetchSummary === "function" && jobId) {
      viewRefreshed = Promise.resolve(view.fetchSummary(jobId, {})).catch(function () { return null; });
    }
    var approvalRefreshed = refreshSilently({ approvalRef: state.approvalRef });
    if (typeof deps.onTaskRef === "function") {
      Promise.resolve().then(function () {
        return deps.onTaskRef(taskRef, state.lastSubmit);
      }).catch(function () { return null; });
    }
    return Promise.all([approvalRefreshed, viewRefreshed]).then(function () {
      return state.lastSubmit;
    });
  }

  /** Submit the approved plan. The body is the ONE normalized submission body
   * the caller already preflighted; only the approval binding is attached by
   * the commands layer. A response without a real job_id/attempt/TaskRef is
   * "unconfirmed", never success. */
  function submit(options) {
    return guarded(function () {
      options = options || {};
      var commands = requireCommands();
      var card = state.card;
      var check = approvalValidity(card, state.proposal);
      if (!check.valid) {
        fail(check.reason === "ok" ? "approval_binding_mismatch" : check.reason,
          "submit requires the bound digest of an approved, unexpired plan");
      }
      assertNotModelApprovalClaim(options.approvalClaim);
      if (!state.record || state.record.approval_ref !== card.approval_ref) {
        fail("approval_record_required",
          "submit needs the approval record read from the platform");
      }
      var body = submitBodyOf(options);
      if (!isObject(body)) {
        fail("submit_body_required",
          "submit needs the normalized submission body (one payload builder)");
      }
      state.submitted = false;
      state.lastSubmit = null;
      return commands.submit(
        { projectId: card.project_id || state.projectId || pageProjectId() },
        body,
        {
          approval: state.record,
          idempotencyKey: options.idempotencyKey,
          signal: options.signal,
          timeoutMs: options.timeoutMs,
        }
      );
    })
      .then(function (result) {
        var taskRef = result && isObject(result.task_ref) ? result.task_ref : null;
        var taskId = taskRef && (taskRef.task_id || taskRef.job_id);
        if (!result || result.submitted !== true || !result.job_id ||
            !result.attempt || !taskId) {
          // Defence in depth: even a permissive transport cannot make this a
          // success without a real TaskRef.
          recordNotice("submit_unconfirmed", null);
          state.submitted = false;
          fail("submit_unconfirmed",
            "the Controller did not return a job_id, attempt and task_ref");
        }
        state.submitted = true;
        state.reviewRequired = false;
        state.lastSubmit = {
          job_id: result.job_id,
          attempt: result.attempt,
          task_ref: taskRef,
          approval_ref: state.card ? state.card.approval_ref
            : (state.record ? state.record.approval_ref : null),
          plan_digest: result.plan_digest ||
            (state.card && state.card.binding ? state.card.binding.execution_plan_digest : null),
          idempotency_key: result.idempotency_key == null ? null : result.idempotency_key,
        };
        recordNotice(null, null);
        render();
        return refreshAfterSubmit(taskRef);
      }, function (error) {
        // A typed refusal (binding, approval state, server code) keeps its own
        // code; only an unknown outcome degrades to `submit_unconfirmed`.
        var code = (error && error.code) || "submit_unconfirmed";
        recordNotice(code, error);
        state.submitted = false;
        render();
        if (error && error.code) throw error;
        fail("submit_unconfirmed", String((error && error.message) || "submit failed"));
      });
  }

  /** One shared error path for the mutating actions. A 409 (stale revision or
   * any state conflict) refreshes the record and demands a human re-review —
   * the decision is NEVER replayed automatically. */
  function handleActionError(error, options) {
    var code = (error && error.code) || "request_failed";
    recordNotice(code, error);
    if (error && error.httpStatus === 409) {
      state.reviewRequired = true;
      return refreshSilently(options).then(function () {
        throw error;
      }, function () {
        throw error;
      });
    }
    state.reviewRequired = false;
    render();
    return Promise.reject(error);
  }

  /** Single dispatch point for the rendered buttons (and for tests). */
  function performAction(action, options) {
    options = options || {};
    if (state.busy) {
      return Promise.reject(commandFailure("action_in_progress", "another approval action is running"));
    }
    var run;
    if (action === "approve" || action === "reject") {
      run = function () { return decide(action, options); };
    } else if (action === "revoke") {
      run = function () { return revoke(options); };
    } else if (action === "submit") {
      run = function () { return submit(options); };
    } else if (action === "request") {
      run = function () { return requestApproval(options); };
    } else if (action === "refresh") {
      run = function () { return refresh(options); };
    } else {
      return Promise.reject(commandFailure("unknown_action", String(action)));
    }
    state.busy = true;
    render();
    return Promise.resolve()
      .then(run)
      .then(function (result) {
        state.busy = false;
        render();
        return result;
      }, function (error) {
        state.busy = false;
        render();
        throw error;
      });
  }

  // ---------------------------------------------------------------------
  // DSH tool approval — a separate mapping that never touches the platform
  // ---------------------------------------------------------------------

  function recordDshToolApproval(session, action) {
    session = session || {};
    action = action || {};
    var entry = {
      mapping: APPROVAL_MAPPINGS.dsh_tool.id,
      tool_call_id: typeof action.toolCallId === "string" ? action.toolCallId : null,
      decision: action.decision === "deny" ? "deny" : "allow",
      actor: isObject(action.principal) && action.principal.user_id
        ? action.principal.user_id : String(action.principal || "session"),
      at: (typeof deps.now === "function" ? new Date(deps.now()).toISOString() : new Date().toISOString()),
      platform_effect: "none",
    };
    return {
      platform: session.platform === undefined ? null : session.platform,
      dsh: (Array.isArray(session.dsh) ? session.dsh : []).concat([entry]),
    };
  }

  /** Dispatch one approval action through exactly one mapping. */
  function applyApprovalMapping(mappingId, session, action) {
    var mapping = APPROVAL_MAPPINGS[mappingId];
    if (!isObject(mapping)) fail("unknown_approval_mapping", String(mappingId));
    action = action || {};
    if (mappingId === APPROVAL_MAPPINGS.dsh_tool.id) {
      return recordDshToolApproval(session, action);
    }
    // The platform mapping is the injected command layer, not a local mutation.
    var dispatched = performAction(action.type, action);
    dispatched.catch(function () { /* surfaced through getState().notice */ });
    return {
      platform: session ? session.platform : null,
      dsh: session && Array.isArray(session.dsh) ? session.dsh : [],
      dispatched: dispatched,
    };
  }

  // ---------------------------------------------------------------------
  // rendering — digests, science/execution plans, budgets, expiry, actions
  // ---------------------------------------------------------------------

  var LABEL_KEYS = {
    plan_definition_digest: ["research.approval.label.plan_definition_digest", "Plan definition digest"],
    recipe_digest: ["research.approval.label.recipe_digest", "Recipe digest"],
    bound_digest: ["research.approval.label.bound_digest", "Bound plan definition digest"],
    execution_plan_digest: ["research.approval.label.execution_plan_digest", "Execution plan digest"],
    project_id: ["research.approval.label.project_id", "Project"],
    approval_ref: ["research.approval.label.approval_ref", "Approval reference"],
    revision: ["research.approval.label.revision", "Revision"],
    requester_ref: ["research.approval.label.requester_ref", "Requested by"],
    approver_ref: ["research.approval.label.approver_ref", "Approver"],
    task_ref: ["research.approval.label.task_ref", "Task reference"],
    binding_status: ["research.approval.label.binding_status", "Binding"],
    method: ["research.approval.label.method", "Method"],
    mapping: ["research.approval.label.mapping", "Reaction mapping"],
    charge: ["research.approval.label.charge", "Charge"],
    multiplicity: ["research.approval.label.multiplicity", "Spin multiplicity"],
    allowed_substitutions: ["research.approval.label.allowed_substitutions", "Allowed substitutions"],
    "budget.model_requests": ["research.approval.label.budget.model_requests", "Budget: model requests"],
    "budget.input_tokens": ["research.approval.label.budget.input_tokens", "Budget: input tokens"],
    "budget.output_tokens": ["research.approval.label.budget.output_tokens", "Budget: output tokens"],
    "budget.task_refs": ["research.approval.label.budget.task_refs", "Budget: task refs"],
    "budget.recipe_steps": ["research.approval.label.budget.recipe_steps", "Budget: recipe steps"],
    "budget.qc_invocations": ["research.approval.label.budget.qc_invocations", "Budget: QC invocations"],
    "budget.wall_seconds": ["research.approval.label.budget.wall_seconds", "Budget: wall seconds"],
    "budget.queue_seconds": ["research.approval.label.budget.queue_seconds", "Budget: queue seconds"],
    unit_basis: ["research.approval.label.unit_basis", "Cost basis"],
    budget_notes: ["research.approval.label.budget_notes", "Budget notes"],
    expiry: ["research.approval.label.expiry", "Approval expiry"],
    "execution.workflow": ["research.approval.label.execution.workflow", "Workflow"],
    "execution.input": ["research.approval.label.execution.input", "Input"],
    "execution.method": ["research.approval.label.execution.method", "Method"],
    "execution.resources": ["research.approval.label.execution.resources", "Resources"],
  };

  var STATUS_FALLBACKS = {
    not_requested: "Not requested",
    draft: "Draft",
    pending: "Pending",
    approved: "Approved",
    rejected: "Rejected",
    revoked: "Revoked",
    expired: "Expired",
    invalidated: "Invalidated",
  };

  var VALIDITY_FALLBACKS = {
    ok: "Approval valid for the current plan",
    not_requested: "No approval requested yet",
    approval_not_granted: "Approval not granted yet",
    approval_pending: "Approval is still pending",
    approval_expired: "Approval expired",
    approval_revoked: "Approval was revoked",
    approval_rejected: "Approval was rejected",
    approval_draft: "Approval is only a draft — it was never requested",
    approval_binding_mismatch: "Plan changed after approval — approval invalidated",
    approval_status_unknown: "Unknown approval status — treated as not approved",
    approval_card_invalid: "Invalid approval record",
    approval_expiry_invalid: "Invalid approval expiry",
    approval_science_unbound: "Approval binds no scientific plan — treated as not approved",
    approval_execution_unbound: "Approval binds no execution plan — treated as not approved",
    proposal_required: "Proposal required to verify the approval",
    bound_digest_required: "Submit requires the bound plan digest",
    model_claim_not_authority: "A model claim is never an approval",
    platform_transport_required: "Platform research commands are not configured — no local approval is possible",
    approval_not_found: "The approval record was not found",
    approval_record_required: "The platform approval record must be read before submitting",
    submit_body_required: "Submit needs the normalized submission body",
    submit_unconfirmed: "Submit outcome unconfirmed — refresh and verify before retrying",
    revision_conflict: "The approval changed — refreshed; re-review before deciding again",
    approval_required: "An approval is required",
    access_denied: "This principal may not perform that approval action",
    not_found: "The approval record was not found",
    input_invalid: "The approval request was rejected as invalid",
    transport_unavailable: "The Controller is unreachable — the outcome is unknown",
    approved_plan_digest_mismatch: "The submitted plan differs from the approved plan",
    approved_proposal_mismatch: "The submitted proposal differs from the approved proposal",
    approved_proposal_digest_mismatch: "The proposal changed after approval",
    approved_budget_mismatch: "The requested budget changed after approval",
    revision_required: "The observed approval revision is required",
    already_decided: "The approval was already decided",
    not_approved: "Only an approved record can be revoked",
    decision_mismatch: "Decision must be approve or reject",
    action_in_progress: "Another approval action is still running",
  };

  var SCOPE_MODE_LABELS = {
    none: ["research.approval.scope.none", "No substitutions"],
    finite_list: ["research.approval.scope.finite_list", "Finite substitution list only"],
    any: ["research.approval.scope.any", "Any substitution"],
  };

  var UNIT_BASIS_LABELS = {
    per_scenario: ["research.approval.unit_basis.per_scenario", "per scenario"],
    per_task: ["research.approval.unit_basis.per_task", "per task"],
    per_request: ["research.approval.unit_basis.per_request", "per request"],
    per_hour: ["research.approval.unit_basis.per_hour", "per hour"],
  };

  var BINDING_LABELS = {
    full: ["research.approval.binding.full", "science + execution"],
    "execution-only": ["research.approval.binding.execution_only", "execution only"],
  };

  function enumLabel(table, value, fallback) {
    var key = String(value == null ? "" : value);
    var entry = table[key];
    if (entry) return tr(entry[0], entry[1]);
    if (!key) return fallback == null ? "—" : fallback;
    return key.replace(/_/g, " ");
  }

  function scopeModeLabel(mode) {
    return enumLabel(SCOPE_MODE_LABELS, mode, "—");
  }

  function unitBasisLabel(value) {
    return enumLabel(UNIT_BASIS_LABELS, value, "—");
  }

  function bindingLabel(value) {
    return enumLabel(BINDING_LABELS, value, "—");
  }

  function labelFor(field) {
    var entry = LABEL_KEYS[field];
    if (entry) return tr(entry[0], entry[1]);
    return String(field).replace(/^budget\./, "").replace(/_/g, " ");
  }

  function statusLabel(status) {
    return tr("research.approval.status." + status,
      STATUS_FALLBACKS[status] || String(status).replace(/_/g, " "));
  }

  function validityLabel(reason) {
    return tr("research.approval.validity." + reason,
      VALIDITY_FALLBACKS[reason] || String(reason).replace(/_/g, " "));
  }

  /** Effective chip state: an approved card whose binding no longer verifies
   * shows `invalidated` (red), never a green `approved`. */
  function effectiveStatus(card, validity) {
    if (!isObject(card)) return "not_requested";
    if (validity && validity.valid) return card.status;
    if (card.status === STATUS.APPROVED) return "invalidated";
    return card.status;
  }

  function row(field, label, value, attrs) {
    return '<div class="ra-row" data-ra-field="' + esc(field) + '"' +
      (attrs || "") + "><dt title=\"" + esc(field) + "\">" + esc(label) + "</dt><dd>" + value + "</dd></div>";
  }

  function digestRow(field, label, digest) {
    var full = typeof digest === "string" ? digest : "";
    return row(field, label,
      '<code class="ra-digest" title="' + esc(full) + '" data-ra-digest="' + esc(full) + '">' +
      esc(shortDigest(full)) + "</code>");
  }

  function actionButton(action, key, fallback, enabled, titleKey) {
    return '<button type="button" class="btn ra-action" data-ra-action="' + esc(action) + '"' +
      (enabled ? "" : " disabled") +
      (titleKey ? ' title="' + esc(tr(titleKey[0], titleKey[1])) + '"' : "") +
      ">" + esc(tr(key, fallback)) + "</button>";
  }

  function renderActions(card, validity) {
    var status = card ? card.status : "not_requested";
    // No commands means no platform action: the buttons stay visible but
    // disabled (the notice explains why). A local approval is never offered.
    var ready = !!deps.commands && state.busy !== true;
    var canRequest = !card || status === STATUS.DRAFT || status === STATUS.EXPIRED ||
      status === STATUS.REJECTED || status === STATUS.REVOKED;
    var canDecide = status === STATUS.PENDING;
    var canRevoke = status === STATUS.APPROVED;
    var canSubmit = !!(validity && validity.valid) && !!submitBodyOfSafe();
    var buttons = [];
    buttons.push(actionButton("refresh",
      "research.approval.action.refresh", "Refresh", ready));
    if (canRequest) {
      buttons.push(actionButton("request",
        "research.approval.action.request", "Request approval",
        ready && isObject(state.proposal)));
    }
    if (canDecide) {
      buttons.push(actionButton("approve",
        "research.approval.action.approve", "Approve", ready));
      buttons.push(actionButton("reject",
        "research.approval.action.reject", "Reject", ready));
    }
    if (canRevoke) {
      buttons.push(actionButton("revoke",
        "research.approval.action.revoke", "Revoke", ready));
    }
    buttons.push(actionButton("submit",
      "research.approval.action.submit", "Submit approved plan", canSubmit && ready,
      ["research.approval.action.submit_hint",
        "Submit uses the single normalized submission body of the current plan"]));
    return '<div class="ra-actions" data-ra-busy="' + (state.busy ? "true" : "false") + '">' +
      buttons.join("") + "</div>";
  }

  function submitBodyOfSafe() {
    try {
      return submitBodyOf({});
    } catch (error) {
      return null;
    }
  }

  function renderNotice() {
    var html = "";
    if (state.reviewRequired) {
      html += '<div class="ra-review" data-ra-review-required="true" role="alert">' +
        esc(tr("research.approval.notice.review_required",
          "The approval changed on the platform — re-review the current record before deciding again")) +
        "</div>";
    }
    if (state.notice) {
      html += '<div class="ra-notice" data-ra-notice="' + esc(state.notice) + '" role="alert">' +
        esc(validityLabel(state.notice)) + "</div>";
    }
    return html;
  }

  function renderSubmitted(card) {
    var taskRef = state.lastSubmit ? state.lastSubmit.task_ref : (card ? card.task_ref : null);
    if (!taskRef) return "";
    var jobId = taskRef.task_id || taskRef.job_id || (state.lastSubmit && state.lastSubmit.job_id) || "";
    var attempt = taskRef.attempt != null ? taskRef.attempt
      : (state.lastSubmit ? state.lastSubmit.attempt : null);
    var proof = jobId && attempt != null;
    return '<div class="ra-submitted" data-ra-submitted="' + (proof ? "true" : "false") + '"' +
      ' data-ra-task-ref="' + esc(String(jobId) + "#" + String(attempt)) + '">' +
      '<span class="ra-submitted-label">' + esc(tr("research.approval.submitted", "Submitted")) + "</span>" +
      row("task_ref", labelFor("task_ref"),
        esc(text(String(jobId) + "#" + String(attempt)))) +
      "</div>";
  }

  function renderExecutionPlan(plan) {
    plan = isObject(plan) ? plan : {};
    var rows = [
      row("execution.workflow", labelFor("execution.workflow"), esc(text(plan.workflow))),
      row("execution.input", labelFor("execution.input"),
        '<code class="ra-json">' + esc(compactJson(plan.input)) + "</code>"),
      row("execution.method", labelFor("execution.method"),
        '<code class="ra-json">' + esc(compactJson(plan.method)) + "</code>"),
      row("execution.resources", labelFor("execution.resources"),
        '<code class="ra-json">' + esc(compactJson(plan.resources)) + "</code>"),
    ].join("");
    return '<dl class="ra-execution-plan" data-ra-plan="execution">' + rows + "</dl>";
  }

  function renderApprovedBudget(budget) {
    budget = isObject(budget) ? budget : {};
    var keys = Object.keys(budget);
    var rows = keys.map(function (key) {
      return row("approved_budget." + key, String(key).replace(/_/g, " "),
        esc(compactJson(budget[key])));
    }).join("");
    return '<dl class="ra-budget ra-budget-approved" data-ra-budget="approved">' + rows + "</dl>";
  }

  function renderCurrentBudget(budget) {
    budget = isObject(budget) ? budget : {};
    var rows = BUDGET_FIELDS.map(function (field) {
      return row("budget." + field, labelFor("budget." + field), esc(text(budget[field])));
    }).join("");
    return '<dl class="ra-budget ra-budget-current" data-ra-budget="current">' + rows + "</dl>";
  }

  function renderApprovalCard(card, proposal) {
    card = isObject(card) ? card : state.card;
    proposal = isObject(proposal) ? proposal : state.proposal;
    var binding = card && card.binding ? card.binding : null;
    var science = proposal && isObject(proposal.science) ? proposal.science : {};
    var budget = proposal && isObject(proposal.budget) ? proposal.budget : {};
    var scope = proposal && isObject(proposal.allowed_change_scope)
      ? proposal.allowed_change_scope
      : null;
    var currentDigest = null;
    if (proposal) {
      try {
        currentDigest = computePlanDefinitionDigest(proposal);
      } catch (error) {
        currentDigest = null;
      }
    }
    var validity = card ? approvalValidity(card, proposal) : { valid: false, reason: "not_requested" };
    var expired = card ? isExpired(card) : false;
    var chip = effectiveStatus(card, validity);
    var rawStatus = card ? card.status : "not_requested";
    var executionPlan = card && isObject(card.plan_definition) ? card.plan_definition : {};

    var html = '<div class="ra-card" data-ra-status="' + esc(rawStatus) +
      '" data-ra-effective-status="' + esc(chip) +
      '" data-ra-ref="' + esc(card ? card.approval_ref : "") +
      '" data-ra-validity="' + esc(validity.reason) +
      '" data-ra-source="' + esc(card ? "server" : "none") + '">';

    html += '<div class="ra-card-sticky">';
    html += '<div class="ra-card-head"><span class="ra-title">' +
      esc(tr("research.approval.title", "Research approval")) + "</span>" +
      '<span class="ra-status ra-status-' + esc(chip) +
      '" data-ra-status-chip="' + esc(chip) + '">' + esc(statusLabel(chip)) + "</span></div>";
    html += '<div class="ra-validity" data-ra-validity="' + esc(validity.reason) +
      '" data-ra-validity-text="1" role="status">' +
      esc(validityLabel(validity.reason)) + "</div>";
    html += renderNotice();
    html += "</div>";

    html += '<dl class="ra-digests">';
    html += digestRow("plan_definition_digest", labelFor("plan_definition_digest"),
      currentDigest || (proposal && proposal.plan_definition_digest));
    html += digestRow("recipe_digest", labelFor("recipe_digest"),
      proposal ? proposal.recipe_digest : null);
    html += digestRow("bound_digest", labelFor("bound_digest"),
      binding ? binding.plan_definition_digest : null);
    html += digestRow("execution_plan_digest", labelFor("execution_plan_digest"),
      binding ? binding.execution_plan_digest : null);
    html += row("binding_status", labelFor("binding_status"),
      esc(bindingLabel(card ? card.binding_status : null)));
    html += row("project_id", labelFor("project_id"), esc(text(
      proposal ? proposal.project_id : (binding ? binding.project_id : null))));
    html += row("approval_ref", labelFor("approval_ref"),
      esc(text(card ? card.approval_ref : null)));
    html += row("revision", labelFor("revision"), esc(text(card ? card.revision : null)));
    html += row("requester_ref", labelFor("requester_ref"),
      esc(text(card ? card.requested_by : null)));
    html += row("approver_ref", labelFor("approver_ref"),
      esc(text(card ? card.approver_ref : null)));
    html += "</dl>";

    html += '<div class="ra-plans" data-ra-plan-compare="1">';
    html += '<div class="ra-plan ra-plan-science" data-ra-plan="science">' +
      '<dl class="ra-science">' +
      row("method", labelFor("method"), esc(text(science.method))) +
      row("mapping", labelFor("mapping"), esc(text(science.mapping))) +
      row("charge", labelFor("charge"), esc(text(science.charge))) +
      row("multiplicity", labelFor("multiplicity"), esc(text(science.multiplicity))) +
      row("allowed_substitutions", labelFor("allowed_substitutions"),
        esc(scopeModeLabel(scope ? scope.mode : null))) +
      "</dl></div>";
    html += renderExecutionPlan(executionPlan);
    html += "</div>";

    html += '<div class="ra-budget-compare" data-ra-budget-compare="1">';
    html += renderApprovedBudget(card ? card.budget : null);
    html += renderCurrentBudget(budget);
    html += "</div>";

    html += '<dl class="ra-cost-basis">' +
      row("unit_basis", labelFor("unit_basis"), esc(unitBasisLabel(budget.unit_basis))) +
      row("budget_notes", labelFor("budget_notes"), esc(text(budget.notes))) +
      "</dl>";

    html += '<div class="ra-expiry">' +
      row("expiry", labelFor("expiry"),
        esc(text(card ? card.expires_at : null)) +
        ' <span class="ra-expired" data-ra-expired="' + (expired ? "true" : "false") +
        '">' + esc(expired ? tr("research.approval.expired", "expired")
          : tr("research.approval.expiry_valid", "valid")) + "</span>") +
      "</div>";

    html += renderSubmitted(card);
    html += renderActions(card, validity);
    html += "</div>";
    return html;
  }

  // ---------------------------------------------------------------------
  // mount / render / state
  // ---------------------------------------------------------------------

  function setCard(record) {
    if (isObject(record)) {
      var mapped = record;
      if (typeof record.status === "string" && typeof record.approval_ref === "string") {
        mapped = cardFromRecord(record);
        state.record = JSON.parse(JSON.stringify(record));
      }
      state.card = mapped;
      state.approvalRef = String(mapped.approval_ref || "");
    }
    return getState();
  }

  function setProposal(proposal) {
    state.proposal = isObject(proposal) ? proposal : null;
    return getState();
  }

  function setPlan(plan) {
    state.plan = isObject(plan) ? plan : null;
    return getState();
  }

  function currentCard() {
    return state.card;
  }

  function render() {
    if (state.disposed || !rootEl) return;
    rootEl.innerHTML = renderApprovalCard(currentCard(), state.proposal);
  }

  function init(next) {
    next = next || {};
    if (isObject(next.research)) deps.research = next.research;
    if (isObject(next.commands)) deps.commands = next.commands;
    if (isObject(next.taskView)) deps.taskView = next.taskView;
    if (typeof next.now === "function") deps.now = next.now;
    if (typeof next.buildSubmitBody === "function") deps.buildSubmitBody = next.buildSubmitBody;
    if (typeof next.onTaskRef === "function") deps.onTaskRef = next.onTaskRef;
    return getState();
  }

  function bindRootEvents() {
    if (!rootEl || typeof rootEl.addEventListener !== "function" || eventsBound) return;
    rootEl.addEventListener("click", onClick);
    eventsBound = true;
  }

  function onClick(event) {
    var target = event && event.target;
    var element = target && typeof target.closest === "function"
      ? target.closest("[data-ra-action]") : null;
    if (!element) return;
    var action = element.getAttribute && element.getAttribute("data-ra-action");
    if (!action) return;
    if (typeof event.preventDefault === "function") event.preventDefault();
    performAction(action, {}).catch(function () { /* notice is already rendered */ });
  }

  function mount(doc, options) {
    options = options || {};
    domDoc = doc || (typeof document !== "undefined" ? document : null);
    if (!domDoc) return getState();
    init(options);
    if (window.ACPClient && window.ACPClient.research) {
      deps.research = window.ACPClient.research;
    }
    if (window.ACPClient && window.ACPClient.researchCommands) {
      deps.commands = window.ACPClient.researchCommands;
    }
    rootEl = typeof options.container === "object" && options.container
      ? options.container
      : domDoc.getElementById("research-approval-body");
    state.mounted = !!rootEl;
    state.disposed = false;
    eventsBound = false;
    if (typeof options.projectId === "string" && options.projectId) {
      state.projectId = options.projectId;
    }
    if (typeof options.proposalRef === "string" && options.proposalRef) {
      state.proposalRef = options.proposalRef;
    }
    if (typeof options.approvalRef === "string" && options.approvalRef) {
      state.approvalRef = options.approvalRef;
    }
    if (isObject(options.plan)) state.plan = options.plan;
    if (isObject(options.proposal)) state.proposal = options.proposal;
    if (isObject(options.record)) adoptRecord(options.record);
    if (!deps.commands) {
      recordNotice(PLATFORM_TRANSPORT_REQUIRED, null);
    }
    if (!rootEl) return getState();
    bindRootEvents();
    render();
    if (options.autoDiscover !== false) {
      if (deps.commands) {
        refresh({ silent: true }).catch(function () { render(); });
      }
      if (options.proposalRef && deps.research &&
          typeof deps.research.fetchProposal === "function") {
        Promise.resolve()
          .then(function () {
            return deps.research.fetchProposal(options.proposalRef, {
              projectId: options.projectId, permission: "read",
            }, {});
          })
          .then(function (proposal) {
            if (state.disposed) return;
            setProposal(proposal);
            render();
          })
          .catch(function (error) {
            if (state.disposed) return;
            recordNotice((error && error.code) || "request_failed", error);
            render();
          });
      }
    }
    return getState();
  }

  function dispose() {
    state.disposed = true;
    state.mounted = false;
    state.proposal = null;
    state.record = null;
    state.card = null;
    state.reviewRequired = false;
    state.notice = null;
    state.submitted = false;
    state.lastSubmit = null;
    rootEl = null;
    domDoc = null;
    eventsBound = false;
    return getState();
  }

  function getState() {
    var card = currentCard();
    var validity = card && state.proposal
      ? approvalValidity(card, state.proposal)
      : null;
    return {
      mounted: state.mounted,
      disposed: state.disposed,
      projectId: state.projectId || pageProjectId() || null,
      hasRecord: !!state.record,
      status: card ? card.status : null,
      revision: card ? card.revision : null,
      approvalRef: card ? card.approval_ref : null,
      requesterRef: card ? card.requested_by : null,
      approverRef: card ? card.approver_ref : null,
      taskRef: card && card.task_ref ? card.task_ref : null,
      bindingStatus: card ? card.binding_status : null,
      boundDigest: card && card.binding ? card.binding.plan_definition_digest : null,
      executionPlanDigest: card && card.binding ? card.binding.execution_plan_digest : null,
      currentDigest: validity ? validity.current_digest : null,
      validity: validity ? validity.reason : null,
      valid: validity ? validity.valid : null,
      expiresAt: card ? card.expires_at : null,
      expired: card ? isExpired(card) : false,
      submitted: state.submitted === true,
      submittedJobId: state.lastSubmit ? state.lastSubmit.job_id : null,
      submittedAttempt: state.lastSubmit ? state.lastSubmit.attempt : null,
      reviewRequired: state.reviewRequired === true,
      notice: state.notice,
      lastError: state.lastError,
      busy: state.busy === true,
      transportRequired: !deps.commands,
    };
  }

  if (typeof document !== "undefined" && typeof document.addEventListener === "function") {
    document.addEventListener("DOMContentLoaded", function () {
      try {
        if (!document.getElementById || !document.getElementById("research-approval-body")) return;
        mount(document);
      } catch (error) {
        if (typeof console !== "undefined" && console.warn) {
          console.warn("research-approval mount skipped:", error);
        }
      }
    });
  }

  return {
    APPROVAL_MAPPINGS: APPROVAL_MAPPINGS,
    STATUS: STATUS,
    SCIENCE_FIELDS: SCIENCE_FIELDS,
    BUDGET_FIELDS: BUDGET_FIELDS,
    PLATFORM_TRANSPORT_REQUIRED: PLATFORM_TRANSPORT_REQUIRED,
    planDefinitionProjection: planDefinitionProjection,
    computePlanDefinitionDigest: computePlanDefinitionDigest,
    approvalValidity: approvalValidity,
    cardFromRecord: cardFromRecord,
    canSubmitPlan: canSubmitPlan,
    requestApproval: requestApproval,
    decide: decide,
    revoke: revoke,
    submit: submit,
    refresh: refresh,
    performAction: performAction,
    recordDshToolApproval: recordDshToolApproval,
    applyApprovalMapping: applyApprovalMapping,
    isUserApprovalAuthority: isUserApprovalAuthority,
    assertNotModelApprovalClaim: assertNotModelApprovalClaim,
    effectiveStatus: effectiveStatus,
    statusLabel: statusLabel,
    validityLabel: validityLabel,
    scopeModeLabel: scopeModeLabel,
    unitBasisLabel: unitBasisLabel,
    bindingLabel: bindingLabel,
    labelFor: labelFor,
    renderApprovalCard: renderApprovalCard,
    setCard: setCard,
    setProposal: setProposal,
    setPlan: setPlan,
    init: init,
    mount: mount,
    render: render,
    dispose: dispose,
    getState: getState,
  };
})();

window.ACPWorkbench = window.ACPWorkbench || {};
ACPWorkbench.features = ACPWorkbench.features || {};
ACPWorkbench.features.researchApproval = window.ACPResearchApproval;
