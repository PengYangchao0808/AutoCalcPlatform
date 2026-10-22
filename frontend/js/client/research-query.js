/*
 * ACP Client — research read query (proposal / record / round / approval /
 * evidence references).
 *
 * Owner: frontend/js/client/** (OpenCode). Fetches go through the injected
 * request adapters (`v1(url, opts)` / `v2(path, opts)`, same seam as
 * task-view-query.js); this module owns no fetch and no URL prefix. Every
 * shared snapshot is written only through ACPClient.store.query, so a late
 * response from an older record revision is superseded (StaleDiscard) rather
 * than overwriting a newer one.
 * Version: v20261022.1
 *
 * Read-only by construction: there is no approve / decide / submit / edit
 * method here. Research decisions belong to the owning service; this module
 * only reads the persisted Proposal and ResearchRecord.
 *
 * Store key shape (every segment URI-encoded):
 *   research:<project>:<permission>:<kind>:<identity>:<revision>
 * The group omits the revision, so a newer revision retires an in-flight
 * older one even though the two cache keys differ.
 *
 * Endpoints consumed (relative paths; the adapter owns the prefix):
 *   GET /research/proposals/{proposal_id}?project_id=...
 *   GET /research/records/{research_id}?project_id=...
 */
window.ACPClient = window.ACPClient || {};
ACPClient.research = (function () {
  "use strict";

  var store = ACPClient.store;
  var adapters = { v1: null, v2: null }; // injected: v1(url, opts), v2(path, opts)
  var GROUP = "research";

  function init(deps) {
    deps = deps || {};
    if (typeof deps.v1 === "function") adapters.v1 = deps.v1;
    if (typeof deps.v2 === "function") adapters.v2 = deps.v2;
  }

  function contract() {
    return (window.ACPWorkbench && ACPWorkbench.contracts &&
      ACPWorkbench.contracts.experience) || null;
  }

  function normalizeScope(scope) {
    scope = scope || {};
    var projectId = scope.projectId != null ? scope.projectId : scope.project_id;
    var permission = scope.permission != null ? scope.permission
      : (scope.permissionScope != null ? scope.permissionScope : "read");
    if (typeof projectId !== "string" || !projectId) {
      throw new Error("research-query: scope.projectId required");
    }
    if (typeof permission !== "string" || !permission) {
      throw new Error("research-query: scope.permission required");
    }
    return { projectId: projectId, permission: permission };
  }

  function segment(value) {
    return encodeURIComponent(String(value));
  }

  /** Public for tests/consumers: the exact cache key of one resource revision. */
  function keyFor(scope, kind, identity, revision) {
    var s = normalizeScope(scope);
    return [GROUP, s.projectId, s.permission, kind, identity,
      revision == null ? "0" : String(revision)].map(segment).join(":");
  }

  function groupFor(scope, kind, identity) {
    var s = normalizeScope(scope);
    return [GROUP, s.projectId, s.permission, kind, identity].map(segment).join(":");
  }

  function normalizeRef(ref, idKey, label) {
    if (typeof ref === "string" && ref) return { id: ref, revision: null };
    if (ref && typeof ref === "object") {
      var id = ref.id != null ? ref.id : ref[idKey];
      if (typeof id !== "string" || !id) {
        throw new Error("research-query: " + label + " id required");
      }
      var revision = ref.revision != null ? ref.revision
        : (ref.generation != null ? ref.generation : null);
      return { id: id, revision: revision };
    }
    throw new Error("research-query: " + label + " reference required");
  }

  function revisionOf(opts, ref) {
    opts = opts || {};
    var value = opts.revision != null ? opts.revision : opts.generation;
    if (value == null && ref) value = ref.revision;
    return value == null ? "0" : String(value);
  }

  /** Reuses ACPClient.transport semantics: the store's signal owns
   * cancellation and the caller's timeoutMs/fetchImpl pass through untouched. */
  function requestOptions(opts, signal) {
    opts = opts || {};
    var out = { signal: signal };
    if (opts.timeoutMs != null) out.timeoutMs = opts.timeoutMs;
    if (typeof opts.fetchImpl === "function") out.fetchImpl = opts.fetchImpl;
    return out;
  }

  function validateOrThrow(name, body) {
    var contracts = contract();
    if (!contracts || typeof contracts[name] !== "function") {
      throw new Error("research-query: contract " + name + " unavailable");
    }
    var check = contracts[name](body);
    if (!check.ok) {
      throw new Error("research-query: " + name + " rejected: " + check.problems.join("; "));
    }
    return body;
  }

  function assertProject(body, expected, label) {
    if (!body || typeof body !== "object" || body.project_id !== expected) {
      throw new Error("research-query: " + label + " project_id does not match scope");
    }
  }

  function copyProposal(proposal) {
    return Object.assign({}, proposal, {
      science: Object.assign({}, proposal.science),
      budget: Object.assign({}, proposal.budget),
      allowed_change_scope: Object.assign({}, proposal.allowed_change_scope),
      human_edits: Array.isArray(proposal.human_edits)
        ? proposal.human_edits.map(function (item) { return Object.assign({}, item); })
        : proposal.human_edits,
    });
  }

  function copyRecord(record) {
    return Object.assign({}, record, {
      step_identity_refs: (record.step_identity_refs || []).map(function (item) {
        var copy = Object.assign({}, item);
        if (Array.isArray(item.upstream_digests)) copy.upstream_digests = item.upstream_digests.slice();
        return copy;
      }),
      task_refs: (record.task_refs || []).map(function (item) { return Object.assign({}, item); }),
      evidence_refs: (record.evidence_refs || []).map(function (item) {
        return Object.assign({}, item);
      }),
      missing_validation: (record.missing_validation || []).map(function (item) {
        return Object.assign({}, item);
      }),
      human_edits: (record.human_edits || []).map(function (item) {
        return Object.assign({}, item);
      }),
    });
  }

  function proposalPath(ref, projectId) {
    return "/research/proposals/" + encodeURIComponent(ref.id) +
      "?project_id=" + encodeURIComponent(projectId);
  }

  function recordPath(ref, projectId) {
    return "/research/records/" + encodeURIComponent(ref.id) +
      "?project_id=" + encodeURIComponent(projectId);
  }

  /** Read one Proposal (hypothesis + frozen plan definition + approval ref). */
  function fetchProposal(ref, scope, opts) {
    opts = opts || {};
    var s = normalizeScope(scope);
    var target = normalizeRef(ref, "proposalId", "proposal");
    if (!adapters.v2) return Promise.reject(
      new Error("ACPClient.research.init({v2}) not configured")
    );
    var key = keyFor(s, "proposal", target.id, revisionOf(opts, target));
    return store.query(key, {
      group: groupFor(s, "proposal", target.id),
      signal: opts.signal || null,
      loader: function (signal) {
        return adapters.v2(proposalPath(target, s.projectId), requestOptions(opts, signal))
          .then(function (body) {
            var proposal = validateOrThrow("validateProposal", body);
            assertProject(proposal, s.projectId, "proposal");
            return copyProposal(proposal);
          });
      },
    }).then(copyProposal);
  }

  /** Read one persisted ResearchRecord (the only evidence document). */
  function fetchRecord(ref, scope, opts) {
    opts = opts || {};
    var s = normalizeScope(scope);
    var target = normalizeRef(ref, "researchId", "research");
    if (!adapters.v2) return Promise.reject(
      new Error("ACPClient.research.init({v2}) not configured")
    );
    var key = keyFor(s, "record", target.id, revisionOf(opts, target));
    return store.query(key, {
      group: groupFor(s, "record", target.id),
      signal: opts.signal || null,
      loader: function (signal) {
        return adapters.v2(recordPath(target, s.projectId), requestOptions(opts, signal))
          .then(function (body) {
            var record = validateOrThrow("validateResearchRecord", body);
            assertProject(record, s.projectId, "record");
            return copyRecord(record);
          });
      },
    }).then(copyRecord);
  }

  /** Pure projection: round + decision + approval ref + evidence refs only.
   * It never promotes a decision or invents an approval. */
  function summarizeRecord(record) {
    record = record || {};
    return {
      research_id: record.research_id,
      round: record.round,
      decision: record.decision,
      approval_ref: record.approval_ref == null ? null : record.approval_ref,
      evidence_refs: (record.evidence_refs || []).map(function (item) {
        return {
          evidence_id: item.evidence_id,
          kind: item.kind,
          sha256: item.sha256,
          relative_path: item.relative_path == null ? null : item.relative_path,
        };
      }),
    };
  }

  function summarizeProposal(proposal) {
    proposal = proposal || {};
    return {
      proposal_id: proposal.proposal_id,
      round: proposal.round,
      decision: proposal.decision,
      approval_ref: proposal.approval_ref == null ? null : proposal.approval_ref,
      plan_definition_digest: proposal.plan_definition_digest,
    };
  }

  /** Round/decision/approval/evidence view derived from one record read. */
  function readRound(ref, scope, opts) {
    return fetchRecord(ref, scope, opts).then(summarizeRecord);
  }

  function invalidateAll() {
    store.invalidate(GROUP + ":*");
  }

  function invalidateProposal(ref, scope) {
    var s = normalizeScope(scope);
    var target = normalizeRef(ref, "proposalId", "proposal");
    store.invalidate(groupFor(s, "proposal", target.id) + "*");
  }

  function invalidateRecord(ref, scope) {
    var s = normalizeScope(scope);
    var target = normalizeRef(ref, "researchId", "research");
    store.invalidate(groupFor(s, "record", target.id) + "*");
  }

  return {
    readOnly: true,
    init: init,
    keyFor: keyFor,
    groupFor: groupFor,
    fetchProposal: fetchProposal,
    fetchRecord: fetchRecord,
    readRound: readRound,
    summarizeRecord: summarizeRecord,
    summarizeProposal: summarizeProposal,
    invalidateAll: invalidateAll,
    invalidateProposal: invalidateProposal,
    invalidateRecord: invalidateRecord,
  };
})();
