/*
 * ACP Client — event consumption as invalidation (T-16 / C10b).
 *
 * Owner: frontend/js/client/** (OpenCode). This module consumes job/experience
 * events as *invalidation signals only*:
 *
 *   - an event never carries task authority and never becomes a data source;
 *     the payload's `status`/`data` fields are ignored by design;
 *   - `after_seq` (event-stream.js:14-25,59-90) is a resume hint, NOT durable
 *     replay: after any gap or reconnect the watcher performs a full
 *     authoritative read;
 *   - every authoritative read goes through `ACPClient.store.query`, so a late
 *     response for an older revision/generation can never overwrite a newer
 *     snapshot (StaleDiscard) and the watcher also compares the event's
 *     revision/generation before it even invalidates;
 *   - artifacts expose exactly the four contract states
 *     `unprepared | preparing | readable | failed`; the display mapping is
 *     frozen and `read` is offered only for `readable`;
 *   - the loader is GET-only: this module has no prepare/transfer/submit path
 *     and rejects a non-read path before it reaches an adapter.
 *
 * Adapters are injected (`init({ v1, v2 })`, same seam as science-query.js).
 * This module owns no fetch and no URL prefix. Version: v20261022.1
 *
 * Store key shape (each segment URI-encoded):
 *   events:<project>:<permission>:view:<task/job/attempt>
 */
window.ACPClient = window.ACPClient || {};
ACPClient.eventInvalidation = (function () {
  "use strict";

  var store = ACPClient.store;
  var adapters = { v1: null, v2: null }; // injected read adapters; never fetch
  var READ_FORBIDDEN = /(^|\/)(prepare|transfer|submit|compute|dispatch)(\/|\?|$)/i;

  var AVAILABILITY_STATES = ["unprepared", "preparing", "readable", "failed"];
  var AVAILABILITY_DISPLAY = Object.freeze({
    unprepared: Object.freeze({
      state: "unprepared", label: "Not prepared", tone: "muted", action: null, canRead: false,
    }),
    preparing: Object.freeze({
      state: "preparing", label: "Preparing", tone: "progress", action: null, canRead: false,
    }),
    readable: Object.freeze({
      state: "readable", label: "Readable", tone: "ready", action: "read", canRead: true,
    }),
    failed: Object.freeze({
      state: "failed", label: "Failed", tone: "error", action: null, canRead: false,
    }),
  });

  function init(deps) {
    deps = deps || {};
    if (typeof deps.v1 === "function") adapters.v1 = deps.v1;
    if (typeof deps.v2 === "function") adapters.v2 = deps.v2;
    // deps.prepare is deliberately never stored or called: a GET/loader path
    // must not trigger prepare or a transfer.
  }

  function normalizeAvailability(value) {
    return AVAILABILITY_STATES.indexOf(value) === -1 ? "failed" : value;
  }

  /** Stable display mapping: the same frozen object for the same state. */
  function displayFor(value) {
    return AVAILABILITY_DISPLAY[normalizeAvailability(value)];
  }

  /** Exactly one action is ever offered, and only for a readable artifact. */
  function actionsFor(value) {
    return displayFor(value).canRead ? ["read"] : [];
  }

  function normalizeScope(scope) {
    scope = scope || {};
    var projectId = scope.projectId != null ? scope.projectId : scope.project_id;
    var permission = scope.permission != null ? scope.permission
      : (scope.permissionScope != null ? scope.permissionScope : "read");
    if (typeof projectId !== "string" || !projectId) {
      throw new Error("event-invalidation: scope.projectId required");
    }
    if (typeof permission !== "string" || !permission) {
      throw new Error("event-invalidation: scope.permission required");
    }
    return { projectId: projectId, permission: permission };
  }

  function normalizeTask(ref) {
    ref = ref || {};
    var jobId = ref.jobId != null ? ref.jobId : ref.job_id;
    var attempt = ref.attempt;
    if (typeof jobId !== "string" || !jobId) {
      throw new Error("event-invalidation: taskRef.jobId required");
    }
    if (typeof attempt !== "number" || !isFinite(attempt) ||
        Math.floor(attempt) !== attempt || attempt < 1) {
      throw new Error("event-invalidation: taskRef.attempt must be an integer >= 1");
    }
    return { jobId: jobId, attempt: attempt };
  }

  function segment(value) {
    return encodeURIComponent(String(value));
  }

  function taskIdentity(task) {
    return "task/" + task.jobId + "/attempt/" + task.attempt;
  }

  function storeKey(scope, task) {
    return ["events", scope.projectId, scope.permission, "view",
      taskIdentity(task)].map(segment).join(":");
  }

  function viewPath(projectId, task) {
    return "/science/tasks/" + encodeURIComponent(task.jobId) +
      "/attempts/" + task.attempt + "/view?project_id=" +
      encodeURIComponent(projectId);
  }

  function artifactReadPath(projectId, task, artifactId, generation, digest) {
    var path = "/science/tasks/" + encodeURIComponent(task.jobId) +
      "/attempts/" + task.attempt + "/artifacts/" +
      encodeURIComponent(String(artifactId)) + "/read?project_id=" +
      encodeURIComponent(projectId) + "&generation=" + encodeURIComponent(generation);
    if (digest != null) path += "&digest=" + encodeURIComponent(digest);
    return path;
  }

  // Artifact reads are only addressable by content: a view artifact without a
  // bare 64-hex sha256 cannot be read, and no digest-less request is ever sent.
  function artifactDigest(artifact) {
    var raw = artifact && artifact.sha256;
    if (typeof raw !== "string") return null;
    var value = raw.trim().toLowerCase();
    if (value.indexOf("sha256:") === 0) value = value.slice(7);
    if (!/^[0-9a-f]{64}$/.test(value)) return null;
    return "sha256:" + value;
  }

  /** A read path can never become a side-effecting call. */
  function assertReadOnlyPath(path) {
    if (READ_FORBIDDEN.test(path)) {
      throw new Error("event-invalidation: read path must never prepare/transfer (" + path + ")");
    }
    return path;
  }

  function firstNumber(values) {
    for (var i = 0; i < values.length; i += 1) {
      var value = values[i];
      if (typeof value === "number" && isFinite(value)) return value;
    }
    return null;
  }

  function revisionOf(event) {
    event = event || {};
    return firstNumber([
      event.snapshot_revision, event.snapshotRevision,
      event.manifest_generation, event.manifestGeneration,
      event.record_revision, event.recordRevision, event.revision,
    ]);
  }

  function generationOf(event) {
    event = event || {};
    return firstNumber([event.generation, event.snapshot_generation, event.snapshotGeneration]);
  }

  function seqOf(event) {
    event = event || {};
    var seq = firstNumber([event.seq, event.sequence, event.lastEventId, event.last_event_id]);
    return seq == null ? null : seq;
  }

  function projectOf(event) {
    event = event || {};
    return event.project_id != null ? event.project_id
      : (event.projectId != null ? event.projectId : null);
  }

  /** `after_seq` is a resume hint, never durable replay. */
  function resumeHint(afterSeq) {
    var value = Number(afterSeq);
    return Object.freeze({
      after_seq: isFinite(value) && value > 0 ? value : 0,
      mode: "resume_hint",
      durable: false,
      replay: false,
      requiresAuthoritativeRefresh: true,
    });
  }

  function createWatcher(opts) {
    opts = opts || {};
    var scope = normalizeScope(opts.scope);
    var task = normalizeTask(opts.taskRef || opts.task);
    var key = opts.key || storeKey(scope, task);
    var refetch = typeof opts.refetch === "function" ? opts.refetch : null;
    var knownRevision = opts.revision != null ? Number(opts.revision) : null;
    var knownGeneration = opts.generation != null ? Number(opts.generation) : null;
    var cursor = opts.afterSeq != null ? Number(opts.afterSeq) : null;
    // Starting from a non-zero cursor is already a resume: it must never be
    // treated as a replay, so the first event forces a full refresh.
    var needFullRefresh = opts.afterSeq != null;
    var closed = false;

    function noteReconnect() {
      cursor = null;
      needFullRefresh = true;
      return resumeHint(0);
    }

    function refresh(reason) {
      var loader;
      if (refetch) {
        loader = function (signal) {
          return refetch({
            reason: reason,
            scope: scope,
            taskRef: task,
            signal: signal,
            revision: knownRevision,
            generation: knownGeneration,
          });
        };
      } else {
        if (!adapters.v2) {
          return Promise.reject(
            new Error("ACPClient.eventInvalidation.init({v2}) not configured")
          );
        }
        loader = function (signal) {
          var path = assertReadOnlyPath(viewPath(scope.projectId, task));
          return adapters.v2(path, { method: "GET", signal: signal });
        };
      }
      return store.query(key, { loader: loader }).then(function (snapshot) {
        return applySnapshot(snapshot, reason);
      });
    }

    function applySnapshot(snapshot, reason) {
      var revision = revisionOf(snapshot);
      var generation = generationOf(snapshot);
      var downgrade = false;
      if (generation != null && knownGeneration != null && generation < knownGeneration) {
        downgrade = true;
      } else if (generation != null && knownGeneration != null &&
          generation === knownGeneration && revision != null &&
          knownRevision != null && revision < knownRevision) {
        downgrade = true;
      } else if (generation == null && revision != null &&
          knownRevision != null && revision < knownRevision) {
        downgrade = true;
      }
      if (downgrade) {
        return {
          action: "ignored", applied: false, reason: "stale_snapshot",
          refetch_reason: reason, authority: false,
          revision: knownRevision, generation: knownGeneration,
        };
      }
      if (generation != null) knownGeneration = generation;
      if (revision != null && (knownRevision == null || revision > knownRevision)) {
        knownRevision = revision;
      }
      return {
        action: "refreshed", applied: true, reason: "authoritative_refetch",
        refetch_reason: reason, authority: false,
        revision: knownRevision, generation: knownGeneration,
      };
    }

    function advanceCursor(seq) {
      if (seq == null) return;
      if (cursor == null || seq > cursor) cursor = seq;
    }

    function deliver(event) {
      if (closed) return Promise.resolve({ action: "ignored", ignored: true, reason: "closed" });
      event = event || {};
      var project = projectOf(event);
      if (project != null && project !== scope.projectId) {
        return Promise.resolve({
          action: "ignored", ignored: true, reason: "cross_project",
          authority: false,
        });
      }
      var previousCursor = cursor;
      var seq = seqOf(event);

      var eventRevision = revisionOf(event);
      var eventGeneration = generationOf(event);
      var stale = false;
      if (eventGeneration != null && knownGeneration != null) {
        if (eventGeneration < knownGeneration) stale = true;
        else if (eventGeneration === knownGeneration && eventRevision != null &&
            knownRevision != null && eventRevision <= knownRevision) stale = true;
      } else if (eventGeneration == null && eventRevision != null &&
          knownRevision != null && eventRevision < knownRevision) {
        stale = true;
      }
      if (stale) {
        advanceCursor(seq);
        return Promise.resolve({
          action: "ignored", ignored: true, reason: "stale_revision",
          event_revision: eventRevision, known_revision: knownRevision,
          authority: false,
        });
      }

      var gap = false;
      if (seq != null && previousCursor != null && seq > previousCursor + 1) gap = true;
      // A resumed connection has no durable replay: refresh even when the next
      // seq looks contiguous.
      if (needFullRefresh) gap = true;
      needFullRefresh = false;
      advanceCursor(seq);
      var reason = gap ? "gap" : "invalidate";

      // Events only mark versioned data invalid. The payload is never applied.
      store.invalidate(key);
      return refresh(reason).then(function (result) {
        return Object.assign({}, result, {
          gap: gap,
          event_revision: eventRevision,
          refetch_reason: reason,
          authority: false,
        });
      });
    }

    function invalidate() {
      return store.invalidate(key);
    }

    return {
      key: key,
      scope: scope,
      taskRef: task,
      deliver: deliver,
      noteReconnect: noteReconnect,
      resumeHint: resumeHint,
      refresh: refresh,
      invalidate: invalidate,
      snapshot: function () { return store.snapshot(key); },
      getRevision: function () { return knownRevision; },
      getGeneration: function () { return knownGeneration; },
      getCursor: function () { return cursor; },
      close: function () { closed = true; },
    };
  }

  /** GET-only artifact read. Refuses any state other than `readable`; never
   * calls a prepare adapter and never transfers. */
  function readArtifact(opts) {
    opts = opts || {};
    var scope = normalizeScope(opts.scope);
    var task = normalizeTask(opts.taskRef || opts.task);
    var artifact = opts.artifact || {};
    var display = displayFor(artifact.availability);
    if (!display.canRead) {
      var error = new Error("event-invalidation: artifact is " + display.state +
        "; read requires readable");
      error.name = "ArtifactNotReadable";
      return Promise.reject(error);
    }
    if (!adapters.v2) {
      return Promise.reject(
        new Error("ACPClient.eventInvalidation.init({v2}) not configured")
      );
    }
    var digest = artifactDigest(artifact);
    if (!digest) {
      var digestError = new Error("artifact sha256 missing");
      digestError.name = "ArtifactNotReadable";
      return Promise.reject(digestError);
    }
    var generation = opts.generation != null ? opts.generation
      : (artifact.generation != null ? artifact.generation : 0);
    var path = assertReadOnlyPath(
      artifactReadPath(scope.projectId, task,
        artifact.artifact_id != null ? artifact.artifact_id : artifact.artifactId,
        generation, digest)
    );
    var request = { method: "GET", signal: opts.signal || null };
    if (opts.timeoutMs != null) request.timeoutMs = opts.timeoutMs;
    return Promise.resolve(adapters.v2(path, request));
  }

  return {
    init: init,
    AVAILABILITY_STATES: AVAILABILITY_STATES,
    AVAILABILITY_DISPLAY: AVAILABILITY_DISPLAY,
    normalizeAvailability: normalizeAvailability,
    displayFor: displayFor,
    actionsFor: actionsFor,
    keyFor: storeKey,
    resumeHint: resumeHint,
    createWatcher: createWatcher,
    readArtifact: readArtifact,
    assertReadOnlyPath: assertReadOnlyPath,
  };
})();
