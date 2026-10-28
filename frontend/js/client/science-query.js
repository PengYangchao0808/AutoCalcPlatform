/*
 * ACP Client — science read query (capabilities, preflight, task/attempt
 * status, view artifacts and claims).
 *
 * Owner: frontend/js/client/** (OpenCode). Fetches go through the injected
 * request adapters (`v1(url, opts)` / `v2(path, opts)`, same seam as
 * task-view-query.js); this module owns no fetch and no URL prefix. Every
 * shared snapshot is written only through ACPClient.store.query, so late
 * responses are superseded by generation instead of overwriting newer data.
 * Version: v20261022.1
 *
 * Read-only by construction: there is no submit/prepare/cancel/analyze call
 * here. A caller that wants to mutate science must go through the command
 * surface, never a GET.
 *
 * Store key shape (every segment URI-encoded):
 *   science:<project>:<permission>:<kind>:<identity>:<generation>
 * The group omits the generation, so a newer generation retires an in-flight
 * older one even though the two cache keys differ.
 *
 * Endpoints consumed (relative paths; the adapter owns the prefix):
 *   GET  /science/capabilities?project_id=...
 *   POST /science/preflight?project_id=...
 *   GET  /science/tasks/{job_id}/attempts/{attempt}/status?project_id=...
 *   GET  /science/tasks/{job_id}/attempts/{attempt}/view?project_id=...
 */
window.ACPClient = window.ACPClient || {};
ACPClient.science = (function () {
  "use strict";

  var store = ACPClient.store;
  var adapters = { v1: null, v2: null }; // injected: v1(url, opts), v2(path, opts)
  var GROUP = "science";
  var FALLBACK_AVAILABILITY = ["unprepared", "preparing", "readable", "failed"];

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
      throw new Error("science-query: scope.projectId required");
    }
    if (typeof permission !== "string" || !permission) {
      throw new Error("science-query: scope.permission required");
    }
    return { projectId: projectId, permission: permission };
  }

  function segment(value) {
    return encodeURIComponent(String(value));
  }

  /** Public for tests/consumers: the exact cache key of one resource version. */
  function keyFor(scope, kind, identity, version) {
    var s = normalizeScope(scope);
    return [GROUP, s.projectId, s.permission, kind, identity,
      version == null ? "0" : String(version)].map(segment).join(":");
  }

  function groupFor(scope, kind, identity) {
    var s = normalizeScope(scope);
    return [GROUP, s.projectId, s.permission, kind, identity].map(segment).join(":");
  }

  function versionOf(opts, ref) {
    opts = opts || {};
    var value = opts.generation != null ? opts.generation : opts.revision;
    if (value == null && ref) {
      value = ref.generation != null ? ref.generation : ref.revision;
    }
    return value == null ? "0" : String(value);
  }

  // LOCAL CACHE KEY ONLY — never a security value and never sent to the
  // server. WebCrypto is async and absent from the vm test sandbox, so this is
  // a synchronous FNV-1a 32-bit digest over canonical JSON (sorted keys) of
  // the normalized request, computable before any response exists.
  function canonicalize(value) {
    if (Array.isArray(value)) return value.map(canonicalize);
    if (value && typeof value === "object") {
      var out = {};
      Object.keys(value).sort().forEach(function (key) {
        if (value[key] !== undefined) out[key] = canonicalize(value[key]);
      });
      return out;
    }
    return value;
  }

  function clientRequestDigest(plan) {
    var text = JSON.stringify(canonicalize(plan || {}));
    var hash = 0x811c9dc5;
    for (var i = 0; i < text.length; i += 1) {
      hash ^= text.charCodeAt(i);
      hash = Math.imul(hash, 0x01000193) >>> 0;
    }
    return ("00000000" + hash.toString(16)).slice(-8);
  }

  /** Exact cache key of one preflight run (identity + inputRevision + local
   * request digest + generation), public for tests/consumers. */
  function preflightKeyFor(scope, plan, opts) {
    opts = opts || {};
    var s = normalizeScope(scope);
    var identity = opts.identity ||
      (plan && plan.plan_definition_digest) || "plan";
    var inputRevision = opts.inputRevision != null ? opts.inputRevision
      : (opts.generation != null ? opts.generation : "0");
    var requestDigest = opts.clientRequestDigest || clientRequestDigest(plan);
    var keyIdentity = identity + ":" + inputRevision + ":" + requestDigest;
    return keyFor(s, "preflight", keyIdentity, versionOf(opts));
  }

  /** Reuses ACPClient.transport semantics: the store's signal owns
   * cancellation and the caller's timeoutMs/fetchImpl pass through untouched. */
  function requestOptions(opts, signal, extra) {
    opts = opts || {};
    var out = { signal: signal };
    if (opts.timeoutMs != null) out.timeoutMs = opts.timeoutMs;
    if (typeof opts.fetchImpl === "function") out.fetchImpl = opts.fetchImpl;
    if (extra) Object.assign(out, extra);
    return out;
  }

  function validateOrThrow(name, body) {
    var contracts = contract();
    if (!contracts || typeof contracts[name] !== "function") {
      throw new Error("science-query: contract " + name + " unavailable");
    }
    var check = contracts[name](body);
    if (!check.ok) {
      throw new Error("science-query: " + name + " rejected: " + check.problems.join("; "));
    }
    return body;
  }

  function normalizeTaskRef(ref) {
    ref = ref || {};
    var jobId = ref.jobId != null ? ref.jobId : ref.job_id;
    var attempt = ref.attempt;
    if (typeof jobId !== "string" || !jobId) {
      throw new Error("science-query: taskRef.jobId required");
    }
    if (typeof attempt !== "number" || !isFinite(attempt) ||
        Math.floor(attempt) !== attempt || attempt < 1) {
      throw new Error("science-query: taskRef.attempt must be an integer >= 1");
    }
    return { jobId: jobId, attempt: attempt };
  }

  function taskIdentity(task) {
    return "task/" + task.jobId + "/attempt/" + task.attempt;
  }

  function assertTaskMatch(taskRef, task, label) {
    var jobId = taskRef && (taskRef.job_id != null ? taskRef.job_id : taskRef.jobId);
    var attempt = taskRef && taskRef.attempt;
    if (String(jobId) !== task.jobId || Number(attempt) !== task.attempt) {
      throw new Error("science-query: " + label +
        " task_ref does not match the requested (job, attempt)");
    }
  }

  function copyTaskRef(taskRef) {
    return taskRef ? Object.assign({}, taskRef) : taskRef;
  }

  function copyArtifact(item) {
    return Object.assign({}, item);
  }

  function copyClaim(item) {
    var copy = Object.assign({}, item);
    if (Array.isArray(item.evidence_refs)) copy.evidence_refs = item.evidence_refs.slice();
    return copy;
  }

  function copyView(view) {
    return Object.assign({}, view, {
      task_ref: copyTaskRef(view.task_ref),
      artifacts: (view.artifacts || []).map(copyArtifact),
      claims: (view.claims || []).map(copyClaim),
      clarifications: (view.clarifications || []).map(function (item) {
        return Object.assign({}, item);
      }),
      provenance: Object.assign({}, view.provenance),
    });
  }

  function copyCapabilities(body) {
    return Object.assign({}, body, {
      tools: Array.isArray(body.tools) ? body.tools.slice() : body.tools,
      capabilities: body.capabilities ? Object.assign({}, body.capabilities) : body.capabilities,
    });
  }

  function copyStatus(body) {
    return Object.assign({}, body, { task_ref: copyTaskRef(body.task_ref) });
  }

  function capabilityPath(projectId) {
    return "/science/capabilities?project_id=" + encodeURIComponent(projectId);
  }

  function capabilityFormsPath(projectId) {
    return "/science/capability-forms?project_id=" + encodeURIComponent(projectId);
  }

  function preflightPath(projectId) {
    return "/science/preflight?project_id=" + encodeURIComponent(projectId);
  }

  function attemptPath(task, tail, projectId) {
    return "/science/tasks/" + encodeURIComponent(task.jobId) +
      "/attempts/" + task.attempt + "/" + tail +
      "?project_id=" + encodeURIComponent(projectId);
  }

  /** Capability discovery. `opts.generation` versions the cached snapshot. */
  function fetchCapabilities(scope, opts) {
    opts = opts || {};
    var s = normalizeScope(scope);
    if (!adapters.v2) return Promise.reject(
      new Error("ACPClient.science.init({v2}) not configured")
    );
    var identity = "catalog";
    var key = keyFor(s, "capabilities", identity, versionOf(opts));
    return store.query(key, {
      group: groupFor(s, "capabilities", identity),
      signal: opts.signal || null,
      loader: function (signal) {
        return adapters.v2(capabilityPath(s.projectId), requestOptions(opts, signal))
          .then(function (body) {
            return copyCapabilities(validateOrThrow("validateCapabilities", body));
          });
      },
    }).then(copyCapabilities);
  }

  /** Frozen schema-driven capability forms (M3-06).
   *
   * The client refuses a document that speaks a different subset: the served
   * `subset` table must equal the local `ACPWorkbench.contracts.uiSubset`
   * tables, and every served form is validated against that whitelist before
   * it is cached. `ui_subset_drift`/`ui_*` typed errors are the only outcomes
   * for an unknown schema — never a silently ignored field.
   */
  function validateCapabilityForms(body) {
    if (!body || typeof body !== "object") {
      throw new Error("science-query: capability-forms body is not an object");
    }
    var subset = (window.ACPWorkbench && ACPWorkbench.contracts &&
      ACPWorkbench.contracts.uiSubset) || null;
    if (!subset) {
      throw new Error("science-query: uiSubset contract unavailable");
    }
    if (body.schema !== subset.FORMS_SCHEMA) {
      throw new Error("science-query: ui_form_malformed: schema " +
        JSON.stringify(String(body.schema)));
    }
    if (body.subset_version !== subset.SUBSET_VERSION) {
      throw new Error("science-query: ui_subset_unknown_major: " +
        JSON.stringify(String(body.subset_version)));
    }
    if (!subset.tablesMatch(body.subset)) {
      throw new Error("science-query: ui_subset_drift: the served subset tables " +
        "differ from this UI build");
    }
    if (!Array.isArray(body.forms) || !Array.isArray(body.read_only)) {
      throw new Error("science-query: ui_form_malformed: forms/read_only must be arrays");
    }
    body.forms.forEach(function (entry) {
      if (!entry || typeof entry !== "object" || typeof entry.capability_id !== "string") {
        throw new Error("science-query: ui_form_malformed: form entry has no capability_id");
      }
      if (entry.state !== "available") return;
      var check = subset.validateFormDocument(entry.form);
      if (!check.ok) {
        throw new Error("science-query: " + check.code + ": " + entry.capability_id);
      }
      if (typeof entry.form_digest !== "string" || entry.form_digest !== entry.form.form_digest) {
        throw new Error("science-query: ui_form_digest_mismatch: " + entry.capability_id);
      }
    });
    return body;
  }

  function copyCapabilityForms(body) {
    var copy = JSON.parse(JSON.stringify(body));
    copy.forms = body.forms.map(function (entry) {
      return Object.assign({}, entry, {
        form: entry.form ? JSON.parse(JSON.stringify(entry.form)) : entry.form,
        error: entry.error ? Object.assign({}, entry.error) : entry.error,
      });
    });
    copy.read_only = body.read_only.map(function (entry) { return Object.assign({}, entry); });
    return copy;
  }

  /** Capability-form discovery. `opts.generation` versions the cached snapshot. */
  function fetchCapabilityForms(scope, opts) {
    opts = opts || {};
    var s = normalizeScope(scope);
    if (!adapters.v2) return Promise.reject(
      new Error("ACPClient.science.init({v2}) not configured")
    );
    var identity = "forms";
    var key = keyFor(s, "capability-forms", identity, versionOf(opts));
    return store.query(key, {
      group: groupFor(s, "capability-forms", identity),
      signal: opts.signal || null,
      loader: function (signal) {
        return adapters.v2(capabilityFormsPath(s.projectId), requestOptions(opts, signal))
          .then(function (body) {
            return copyCapabilityForms(validateCapabilityForms(body));
          });
      },
    }).then(copyCapabilityForms);
  }

  /** Preflight/estimate. Never a submit: the validated projection must prove
   * executed=false / metering_consumed=false / transfers_attempted=false.
   * The cache key carries the input revision and the local request digest so
   * two in-flight requests for different content never share a slot, while the
   * group (identity only) still lets a newer run retire an older one. */
  function preflight(plan, scope, opts) {
    opts = opts || {};
    var s = normalizeScope(scope);
    if (!adapters.v2) return Promise.reject(
      new Error("ACPClient.science.init({v2}) not configured")
    );
    var identity = opts.identity ||
      (plan && plan.plan_definition_digest) || "plan";
    var inputRevision = opts.inputRevision != null ? opts.inputRevision
      : (opts.generation != null ? opts.generation : "0");
    var requestDigest = opts.clientRequestDigest || clientRequestDigest(plan);
    var keyIdentity = identity + ":" + inputRevision + ":" + requestDigest;
    var key = keyFor(s, "preflight", keyIdentity, versionOf(opts));
    var headers = Object.assign({ "content-type": "application/json" }, opts.headers || {});
    return store.query(key, {
      group: groupFor(s, "preflight", identity),
      signal: opts.signal || null,
      loader: function (signal) {
        return adapters.v2(preflightPath(s.projectId), requestOptions(opts, signal, {
          method: "POST",
          headers: headers,
          body: JSON.stringify(plan || {}),
        })).then(function (body) {
          return validateOrThrow("validatePreflight", body);
        });
      },
    });
  }

  /** Lightweight task/attempt status projection (v1 read adapter). */
  function fetchTaskStatus(ref, scope, opts) {
    opts = opts || {};
    var s = normalizeScope(scope);
    var task = normalizeTaskRef(ref);
    if (!adapters.v1) return Promise.reject(
      new Error("ACPClient.science.init({v1}) not configured")
    );
    var identity = taskIdentity(task);
    var key = keyFor(s, "status", identity, versionOf(opts, ref));
    return store.query(key, {
      group: groupFor(s, "status", identity),
      signal: opts.signal || null,
      loader: function (signal) {
        return adapters.v1(attemptPath(task, "status", s.projectId), requestOptions(opts, signal))
          .then(function (body) {
            var status = validateOrThrow("validateTaskStatus", body);
            assertTaskMatch(status.task_ref, task, "status");
            return copyStatus(status);
          });
      },
    }).then(copyStatus);
  }

  /** Authoritative read-only view snapshot (artifacts + claims + clarifications).
   * The response project must equal the caller's scope; a cross-project body is
   * rejected rather than cached. */
  function fetchView(ref, scope, opts) {
    opts = opts || {};
    var s = normalizeScope(scope);
    var task = normalizeTaskRef(ref);
    if (!adapters.v2) return Promise.reject(
      new Error("ACPClient.science.init({v2}) not configured")
    );
    var identity = taskIdentity(task);
    var key = keyFor(s, "view", identity, versionOf(opts, ref));
    return store.query(key, {
      group: groupFor(s, "view", identity),
      signal: opts.signal || null,
      loader: function (signal) {
        return adapters.v2(attemptPath(task, "view", s.projectId), requestOptions(opts, signal))
          .then(function (body) {
            var view = validateOrThrow("validateViewModel", body);
            assertTaskMatch(view.task_ref, task, "view");
            if (view.project_id !== s.projectId) {
              throw new Error("science-query: view project_id does not match scope");
            }
            return copyView(view);
          });
      },
    }).then(copyView);
  }

  /** Artifact availability projection. Exactly the four contract states are
   * surfaced; the validator already refused a fifth one. */
  function fetchArtifacts(ref, scope, opts) {
    return fetchView(ref, scope, opts).then(function (view) {
      var contracts = contract();
      var normalize = contracts && typeof contracts.normalizeAvailability === "function"
        ? contracts.normalizeAvailability
        : function (value) {
          return FALLBACK_AVAILABILITY.indexOf(value) === -1 ? "failed" : value;
        };
      return view.artifacts.map(function (item) {
        return Object.assign({}, item, { availability: normalize(item.availability) });
      });
    });
  }

  /** Claim states are projected verbatim; the UI never upgrades them. */
  function fetchClaims(ref, scope, opts) {
    return fetchView(ref, scope, opts).then(function (view) {
      return view.claims.map(copyClaim);
    });
  }

  function invalidateAll() {
    store.invalidate(GROUP + ":*");
  }

  function invalidateTask(ref, scope) {
    var s = normalizeScope(scope);
    var task = normalizeTaskRef(ref);
    var identity = taskIdentity(task);
    store.invalidate(groupFor(s, "status", identity) + "*");
    store.invalidate(groupFor(s, "view", identity) + "*");
  }

  function snapshotView(ref, scope, opts) {
    opts = opts || {};
    var s = normalizeScope(scope);
    var task = normalizeTaskRef(ref);
    return store.snapshot(keyFor(s, "view", taskIdentity(task), versionOf(opts, ref)));
  }

  function subscribeView(ref, scope, opts, listener) {
    opts = opts || {};
    var s = normalizeScope(scope);
    var task = normalizeTaskRef(ref);
    return store.subscribe(
      keyFor(s, "view", taskIdentity(task), versionOf(opts, ref)),
      listener
    );
  }

  return {
    init: init,
    keyFor: keyFor,
    groupFor: groupFor,
    clientRequestDigest: clientRequestDigest,
    preflightKeyFor: preflightKeyFor,
    fetchCapabilities: fetchCapabilities,
    fetchCapabilityForms: fetchCapabilityForms,
    validateCapabilityForms: validateCapabilityForms,
    preflight: preflight,
    fetchTaskStatus: fetchTaskStatus,
    fetchView: fetchView,
    fetchArtifacts: fetchArtifacts,
    fetchClaims: fetchClaims,
    invalidateAll: invalidateAll,
    invalidateTask: invalidateTask,
    snapshotView: snapshotView,
    subscribeView: subscribeView,
  };
})();
