/*
 * ACP Workbench — persistent research approval card (T-27 / C19a).
 *
 * Owner: frontend/js/workbench/features/**. One card is the approval surface
 * for one frozen research Proposal. It renders the proposal/recipe digest, the
 * scientific limits, the resource caps, the cost basis and the approval
 * expiry, and it binds an approval EXACTLY to
 * plan_definition_digest + project + budget + allowed_change_scope
 * (docs/reference/next-change/acp2-contracts/experience/plan-identity.md §3).
 *
 * Contract rules enforced here (fixture mode; the Runtime approval endpoint
 * B16 is not delivered in this run):
 *   * changing method / mapping / initial input / budget invalidates the
 *     approval because they change plan_definition_digest;
 *   * appending TaskRef/evidence refs, or changing display name / timestamps,
 *     never invalidates it — they sit outside the digest projection;
 *   * DSH tool approval and platform research approval are separate mappings;
 *     a DSH tool approval can never change platform approval state;
 *   * a submit is allowed only with the bound digest;
 *   * a model claim of "user agreed" is never an approval, and an approve by
 *     any principal other than the bound approver is rejected.
 *
 * Platform reads go through the injected ACPClient.research adapter; approval
 * transitions are pure functions over the card record. This module owns no
 * network call of its own.
 * Version: v20261023.1
 */
window.ACPResearchApproval = (function () {
  "use strict";

  // ---------------------------------------------------------------------
  // constants (plan-identity.md §1.1 / §3; proposal.schema.json names)
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
      mutates: ["approval_card"],
      operations: ["approval.request", "approval.query", "approval.approve",
        "approval.reject", "approval.revoke"],
    },
    dsh_tool: {
      id: "dsh_tool",
      authority: "dsh_session",
      mutates: [],
      operations: ["dsh.tool_decision"],
    },
  };

  var STATUS = {
    PENDING: "pending",
    APPROVED: "approved",
    REJECTED: "rejected",
    REVOKED: "revoked",
  };

  var DEFAULT_TTL_SECONDS = 3600;
  var seq = 0;

  // ---------------------------------------------------------------------
  // module state (card registry is persistent across dispose/remount)
  // ---------------------------------------------------------------------

  var deps = { research: null, now: null, persistence: null };
  var domDoc = null;
  var rootEl = null;
  var cards = {};
  var state = {
    mounted: false,
    disposed: false,
    cardRef: null,
    proposal: null,
    notice: null,
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

  function fail(code, detail) {
    throw new Error(code + ": " + detail);
  }

  function nowMs() {
    if (typeof deps.now === "function") return deps.now();
    return Date.now();
  }

  function nowIso() {
    try {
      return new Date(nowMs()).toISOString();
    } catch (e) {
      return "";
    }
  }

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
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
  // approval binding (plan-identity.md §3)
  // ---------------------------------------------------------------------

  function budgetProjection(budget) {
    var out = {};
    BUDGET_FIELDS.forEach(function (field) {
      out[field] = budget[field];
    });
    return out;
  }

  function scopeCanonical(scope) {
    return canonicalJson(normalizeDigestValue(dropNulls(scope), "$.allowed_change_scope"));
  }

  function approvalBindingDigest(binding) {
    var payload = normalizeDigestValue(dropNulls({
      plan_definition_digest: binding.plan_definition_digest,
      project_id: binding.project_id,
      budget: budgetProjection(binding.budget),
      allowed_change_scope: binding.allowed_change_scope,
    }), "$");
    return "sha256:" + sha256Hex(utf8Bytes(canonicalJson(payload)));
  }

  /** Binding tuple equality: plan_definition_digest + project + budget +
   * allowed_change_scope — exactly the four, never more, never fewer. */
  function bindingMatches(card, proposal) {
    if (!isObject(card) || !isObject(card.binding) || !isObject(proposal)) return false;
    var binding = card.binding;
    if (computePlanDefinitionDigest(proposal) !== binding.plan_definition_digest) return false;
    if (proposal.project_id !== binding.project_id) return false;
    if (canonicalJson(budgetProjection(proposal.budget)) !==
        canonicalJson(budgetProjection(binding.budget))) return false;
    return scopeCanonical(proposal.allowed_change_scope) ===
      scopeCanonical(binding.allowed_change_scope);
  }

  /** Validity of one card against the CURRENT proposal fields. The digest is
   * recomputed from the fields, never trusted from a stored claim. */
  function approvalValidity(card, proposal) {
    if (!isObject(card)) {
      return { valid: false, reason: "not_requested", current_digest: null, bound_digest: null };
    }
    var bound = card.binding ? card.binding.plan_definition_digest : null;
    var current = null;
    try {
      current = isObject(proposal) ? computePlanDefinitionDigest(proposal) : null;
    } catch (error) {
      return {
        valid: false,
        reason: "approval_binding_mismatch",
        current_digest: null,
        bound_digest: bound,
      };
    }
    if (card.status === STATUS.REVOKED) {
      return { valid: false, reason: "approval_revoked", current_digest: current, bound_digest: bound };
    }
    if (card.status === STATUS.REJECTED) {
      return { valid: false, reason: "approval_rejected", current_digest: current, bound_digest: bound };
    }
    if (isExpired(card)) {
      return { valid: false, reason: "approval_expired", current_digest: current, bound_digest: bound };
    }
    if (card.status === STATUS.PENDING) {
      return { valid: false, reason: "approval_not_granted", current_digest: current, bound_digest: bound };
    }
    if (isObject(proposal) && !bindingMatches(card, proposal)) {
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
  // platform research approval lifecycle (fixture record; B16 not delivered)
  // ---------------------------------------------------------------------

  function cardKey(card) {
    return String(card.project_id) + ":" + String(card.approval_ref);
  }

  function persist(card) {
    if (card) cards[cardKey(card)] = card;
    var adapter = deps.persistence;
    if (isObject(adapter) && typeof adapter.write === "function") {
      adapter.write(cardKey(card), clone(card));
    }
    return card;
  }

  function loadCard(projectId, approvalRef) {
    var key = String(projectId) + ":" + String(approvalRef);
    if (cards[key]) return cards[key];
    var adapter = deps.persistence;
    if (isObject(adapter) && typeof adapter.read === "function") {
      var stored = adapter.read(key);
      if (isObject(stored)) {
        cards[key] = stored;
        return stored;
      }
    }
    return null;
  }

  /** Open a platform approval request bound to the exact plan definition. */
  function requestApproval(input) {
    input = input || {};
    var proposal = input.proposal;
    if (!isObject(proposal)) fail("invalid_proposal", "requestApproval needs a proposal");
    var requester = assertUserPrincipal(input.requestedBy);
    if (typeof input.approverRef !== "string" || !input.approverRef) {
      fail("invalid_approver", "requestApproval needs the bound human approver ref");
    }
    if (typeof input.projectId === "string" && input.projectId &&
        input.projectId !== proposal.project_id) {
      fail("project_mismatch", "request project does not match the proposal project");
    }
    var digest = computePlanDefinitionDigest(proposal);
    var binding = {
      plan_definition_digest: digest,
      project_id: proposal.project_id,
      budget: budgetProjection(proposal.budget),
      allowed_change_scope: clone(proposal.allowed_change_scope),
    };
    seq += 1;
    var requestedAt = typeof input.requestedAt === "string" ? input.requestedAt : nowIso();
    var expiresAt = typeof input.expiresAt === "string" ? input.expiresAt : null;
    if (!expiresAt) {
      var ttl = typeof input.ttlSeconds === "number" && input.ttlSeconds > 0
        ? input.ttlSeconds : DEFAULT_TTL_SECONDS;
      expiresAt = new Date(Date.parse(requestedAt) + ttl * 1000).toISOString();
    }
    var card = {
      approval_ref: "apr-" + seq,
      status: STATUS.PENDING,
      revision: 1,
      project_id: proposal.project_id,
      proposal_ref: proposal.proposal_id,
      requested_by: requester.user_id,
      approver_ref: input.approverRef,
      requested_at: requestedAt,
      expires_at: expiresAt,
      decided_at: null,
      decided_by: null,
      decision: null,
      reason: null,
      binding: binding,
      binding_digest: approvalBindingDigest(binding),
    };
    return persist(card);
  }

  function requireRevision(card, action) {
    if (isObject(action) && action.expectedRevision != null &&
        action.expectedRevision !== card.revision) {
      fail("revision_conflict",
        "expected revision " + action.expectedRevision + " but card is " + card.revision);
    }
  }

  /** Approve or reject exactly the frozen plan digest, by the bound approver. */
  function decideApproval(card, action) {
    if (!isObject(card)) fail("not_requested", "no approval card to decide");
    action = action || {};
    if (card.status !== STATUS.PENDING) fail("already_decided", "card status is " + card.status);
    requireRevision(card, action);
    var principal = assertUserPrincipal(action.principal);
    if (principal.user_id !== card.approver_ref) {
      fail("approver_mismatch",
        "approval is bound to " + card.approver_ref + ", not " + principal.user_id);
    }
    if (action.decision !== "approve" && action.decision !== "reject") {
      fail("decision_mismatch", "decision must be approve or reject");
    }
    if (action.decision === "approve" && typeof action.boundDigest === "string" &&
        action.boundDigest !== card.binding.plan_definition_digest) {
      fail("approval_binding_mismatch", "approve digest does not match the bound digest");
    }
    var next = clone(card);
    next.status = action.decision === "approve" ? STATUS.APPROVED : STATUS.REJECTED;
    next.decision = action.decision;
    next.decided_at = nowIso();
    next.decided_by = principal.user_id;
    next.reason = typeof action.reason === "string" ? action.reason : null;
    next.revision = card.revision + 1;
    return persist(next);
  }

  /** Revoke a previous approval so it can never authorize a submit. */
  function revokeApproval(card, action) {
    if (!isObject(card)) fail("not_requested", "no approval card to revoke");
    action = action || {};
    if (card.status !== STATUS.APPROVED) fail("not_approved", "card status is " + card.status);
    requireRevision(card, action);
    var principal = assertUserPrincipal(action.principal);
    if (principal.user_id !== card.approver_ref) {
      fail("approver_mismatch", "revoke is bound to " + card.approver_ref);
    }
    var next = clone(card);
    next.status = STATUS.REVOKED;
    next.decision = "revoke";
    next.decided_at = nowIso();
    next.decided_by = principal.user_id;
    next.reason = typeof action.reason === "string" ? action.reason : null;
    next.revision = card.revision + 1;
    return persist(next);
  }

  function canSubmitPlan(card, proposal) {
    return approvalValidity(card, proposal).valid === true;
  }

  /** Submit only the already-bound plan. The submit never changes approval
   * state and never accepts a model claim as the approval itself. */
  function submitPlan(card, proposal, action) {
    action = action || {};
    assertNotModelApprovalClaim(action.approvalClaim);
    var principal = assertUserPrincipal(action.principal);
    if (typeof action.boundDigest === "string" && isObject(card) &&
        action.boundDigest !== card.binding.plan_definition_digest) {
      fail("approval_binding_mismatch", "submit digest is not the bound digest");
    }
    var check = approvalValidity(card, proposal);
    if (!check.valid) {
      fail(check.reason === "ok" ? "approval_binding_mismatch" : check.reason,
        "submit requires the bound digest of an approved, unexpired plan");
    }
    seq += 1;
    return {
      submitted: true,
      dispatch_ref: "dsp-" + seq,
      approval_ref: card.approval_ref,
      plan_definition_digest: card.binding.plan_definition_digest,
      submitted_by: principal.user_id,
      submitted_at: nowIso(),
    };
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
      at: nowIso(),
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
    var current = session ? session.platform : null;
    var nextCard = current;
    if (action.type === "request") nextCard = requestApproval(action);
    else if (action.type === "decide") nextCard = decideApproval(current, action);
    else if (action.type === "revoke") nextCard = revokeApproval(current, action);
    else if (action.type === "submit") {
      submitPlan(current, action.proposal, action);
      nextCard = current;
    } else {
      fail("unknown_approval_action", String(action.type));
    }
    return {
      platform: nextCard,
      dsh: session && Array.isArray(session.dsh) ? session.dsh : [],
    };
  }

  // ---------------------------------------------------------------------
  // rendering — digest, scientific limits, resource caps, cost basis, expiry
  // ---------------------------------------------------------------------

  function row(field, label, value, attrs) {
    return '<div class="ra-row" data-ra-field="' + esc(field) + '"' +
      (attrs || "") + "><dt>" + esc(label) + "</dt><dd>" + value + "</dd></div>";
  }

  function digestRow(field, label, digest) {
    var full = typeof digest === "string" ? digest : "";
    return row(field, label,
      '<code class="ra-digest" title="' + esc(full) + '" data-ra-digest="' + esc(full) + '">' +
      esc(shortDigest(full)) + "</code>");
  }

  function renderApprovalCard(card, proposal) {
    card = isObject(card) ? card : null;
    proposal = isObject(proposal) ? proposal : state.proposal;
    var binding = card && card.binding ? card.binding : null;
    var science = proposal && isObject(proposal.science) ? proposal.science : {};
    var budget = proposal && isObject(proposal.budget) ? proposal.budget : {};
    var scope = proposal && isObject(proposal.allowed_change_scope)
      ? proposal.allowed_change_scope
      : (binding ? binding.allowed_change_scope : null);
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

    var html = '<div class="ra-card" data-ra-status="' + esc(card ? card.status : "not_requested") +
      '" data-ra-ref="' + esc(card ? card.approval_ref : "") +
      '" data-ra-validity="' + esc(validity.reason) + '">';

    html += '<div class="ra-card-head"><span class="ra-title">' +
      esc(tr("research.approval.title", "Research approval")) + "</span>" +
      '<span class="ra-status ra-status-' + esc(card ? card.status : "not_requested") +
      '">' + esc(card ? card.status : "not_requested") + "</span></div>";

    html += '<dl class="ra-digests">';
    html += digestRow("plan_definition_digest", "plan_definition_digest",
      currentDigest || (proposal && proposal.plan_definition_digest));
    html += digestRow("recipe_digest", "recipe_digest",
      proposal ? proposal.recipe_digest : (binding ? "" : ""));
    html += digestRow("bound_digest", "bound_plan_definition_digest",
      binding ? binding.plan_definition_digest : null);
    html += row("project_id", "project_id", esc(text(
      proposal ? proposal.project_id : (binding ? binding.project_id : null))));
    html += row("approval_ref", "approval_ref",
      esc(text(card ? card.approval_ref : null)));
    html += "</dl>";

    html += '<dl class="ra-science">' +
      row("method", "science.method", esc(text(science.method))) +
      row("mapping", "science.mapping", esc(text(science.mapping))) +
      row("charge", "science.charge", esc(text(science.charge))) +
      row("multiplicity", "science.multiplicity", esc(text(science.multiplicity))) +
      row("allowed_substitutions", "science.allowed_substitutions",
        esc(text(scope ? scope.mode : null, "none"))) +
      "</dl>";

    html += '<dl class="ra-budget">';
    BUDGET_FIELDS.forEach(function (field) {
      html += row("budget." + field, "budget." + field, esc(text(budget[field])));
    });
    html += "</dl>";

    html += '<dl class="ra-cost-basis">' +
      row("unit_basis", "budget.unit_basis", esc(text(budget.unit_basis))) +
      row("budget_notes", "budget.notes", esc(text(budget.notes))) +
      "</dl>";

    html += '<div class="ra-expiry">' +
      row("expiry", "approval.expires_at",
        esc(text(card ? card.expires_at : null)) +
        ' <span class="ra-expired" data-ra-expired="' + (expired ? "true" : "false") +
        '">' + esc(expired ? tr("research.approval.expired", "expired")
          : tr("research.approval.expiry_valid", "expiry ok")) + "</span>") +
      "</div>";

    html += '<div class="ra-validity" data-ra-validity="' + esc(validity.reason) + '">' +
      esc(tr("research.approval.validity_" + validity.reason, validity.reason)) + "</div>";
    html += "</div>";
    return html;
  }

  // ---------------------------------------------------------------------
  // mount / render / state
  // ---------------------------------------------------------------------

  function setCard(card) {
    if (isObject(card)) {
      persist(card);
      state.cardRef = cardKey(card);
    }
    return getState();
  }

  function setProposal(proposal) {
    state.proposal = isObject(proposal) ? proposal : null;
    return getState();
  }

  function currentCard() {
    return state.cardRef && cards[state.cardRef] ? cards[state.cardRef] : null;
  }

  function render() {
    if (state.disposed || !rootEl) return;
    rootEl.innerHTML = renderApprovalCard(currentCard(), state.proposal);
  }

  function init(next) {
    next = next || {};
    if (isObject(next.research)) deps.research = next.research;
    if (typeof next.now === "function") deps.now = next.now;
    if (isObject(next.persistence)) deps.persistence = next.persistence;
    return getState();
  }

  function mount(doc, options) {
    options = options || {};
    domDoc = doc || (typeof document !== "undefined" ? document : null);
    if (!domDoc) return getState();
    init(options);
    if (!deps.research && window.ACPClient && window.ACPClient.research) {
      deps.research = window.ACPClient.research;
    }
    rootEl = typeof options.container === "object" && options.container
      ? options.container
      : domDoc.getElementById("research-approval-body");
    state.mounted = !!rootEl;
    state.disposed = false;
    if (isObject(options.proposal)) state.proposal = options.proposal;
    if (isObject(options.card)) setCard(options.card);
    if (typeof options.cardRef === "string" && cards[options.cardRef]) {
      state.cardRef = options.cardRef;
    }
    if (state.cardRef && cards[state.cardRef] === undefined) {
      var stored = loadCard.apply(null, state.cardRef.split(":"));
      if (stored) state.cardRef = cardKey(stored);
    }
    if (!rootEl) return getState();
    render();
    if (options.autoDiscover !== false && options.proposalRef && deps.research &&
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
          state.notice = "research-approval load failed: " + (error && error.message ? error.message : error);
          render();
        });
    }
    return getState();
  }

  function dispose() {
    state.disposed = true;
    state.mounted = false;
    state.proposal = null;
    state.cardRef = null;
    rootEl = null;
    domDoc = null;
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
      cardRef: state.cardRef,
      status: card ? card.status : null,
      revision: card ? card.revision : null,
      approvalRef: card ? card.approval_ref : null,
      boundDigest: card && card.binding ? card.binding.plan_definition_digest : null,
      currentDigest: validity ? validity.current_digest : null,
      validity: validity ? validity.reason : null,
      valid: validity ? validity.valid : null,
      expiresAt: card ? card.expires_at : null,
      expired: card ? isExpired(card) : false,
      cardCount: Object.keys(cards).length,
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
    planDefinitionProjection: planDefinitionProjection,
    computePlanDefinitionDigest: computePlanDefinitionDigest,
    approvalBindingDigest: approvalBindingDigest,
    bindingMatches: bindingMatches,
    approvalValidity: approvalValidity,
    requestApproval: requestApproval,
    decideApproval: decideApproval,
    revokeApproval: revokeApproval,
    canSubmitPlan: canSubmitPlan,
    submitPlan: submitPlan,
    recordDshToolApproval: recordDshToolApproval,
    applyApprovalMapping: applyApprovalMapping,
    isUserApprovalAuthority: isUserApprovalAuthority,
    assertNotModelApprovalClaim: assertNotModelApprovalClaim,
    renderApprovalCard: renderApprovalCard,
    setCard: setCard,
    setProposal: setProposal,
    loadCard: loadCard,
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
