/*
 * ACP adapter layer — "选择态上行" (selection uplink) client half.
 *
 * Publishes the current selection state to the DSH host through the ACP
 * transport (`transport(url, opts)`-like request function).
 *
 * Body contract (references only — no molecule/structure payloads):
 *   { session, generation, revision, snapshot }
 *   snapshot = exactly { projectId, researchRef, taskRef, structureRef,
 *                        networkNodeId, revision, source } when present.
 *
 * publish() NEVER rejects and never throws synchronously:
 *   {ok:true, sent:true}                         sent and accepted
 *   {ok:true, sent:false, reason:"no_session"}   no session wired
 *   {ok:true, sent:false, reason:"stale_generation"}
 *   {ok:true, sent:false, reason:"not_monotonic"}
 *   {ok:true, sent:false, reason:"invalid_state"} body could not be built
 *   {ok:true, sent:false, reason:"rejected"}     host answered falsy / {ok:false}
 *   {ok:true, sent:false, coalesced:true, reason:"superseded"}
 *
 * Coalescing: at most one publish is in flight; while it is, only the LATEST
 * queued state survives (an older queued one resolves "superseded") and it is
 * sent when the in-flight one settles. No automatic retry.
 */
window.ACPAdapters = window.ACPAdapters || {};
ACPAdapters.dsh = ACPAdapters.dsh || {};

ACPAdapters.dsh.focusBridge = (function () {
  "use strict";

  var REFERENCE_KEYS = ["projectId", "researchRef", "taskRef", "structureRef", "networkNodeId", "revision", "source"];

  function fail(code, message) {
    var err = new Error(message);
    err.code = code;
    return err;
  }

  function isInteger(value) {
    return typeof value === "number" && isFinite(value) && value === Math.floor(value);
  }

  function isPositiveInteger(value) {
    return isInteger(value) && value > 0;
  }

  function isPlainObject(value) {
    return !!value && typeof value === "object" && !Array.isArray(value);
  }

  function buildBody(state) {
    if (!isPlainObject(state)) throw fail("E_FOCUS_BODY", "state must be an object");
    if (typeof state.sessionId !== "string" || state.sessionId === "") {
      throw fail("E_FOCUS_BODY", "sessionId is required");
    }
    if (!isInteger(state.revision) || state.revision < 0) {
      throw fail("E_FOCUS_BODY", "revision must be a non-negative integer");
    }
    if (!isPlainObject(state.snapshot)) {
      throw fail("E_FOCUS_BODY", "snapshot must be an object");
    }
    var snapshot = {};
    REFERENCE_KEYS.forEach(function (key) {
      if (Object.prototype.hasOwnProperty.call(state.snapshot, key) && state.snapshot[key] !== undefined) {
        snapshot[key] = state.snapshot[key];
      }
    });
    return {
      session: state.sessionId,
      generation: (state.generation == null) ? null : state.generation,
      revision: state.revision,
      snapshot: snapshot
    };
  }

  function create(options) {
    options = options || {};
    var endpoint = options.endpoint;
    var sessionId = (typeof options.sessionId === "string") ? options.sessionId : "";
    var generation = options.generation;
    var transport = options.transport;

    var lastSentRevision = null;
    var inFlight = false;
    var queued = null;
    var subscription = null;
    var counts = { sent: 0, dropped: 0, rejected: 0 };

    function accepted(result) {
      if (!result) return false;
      if (typeof result === "object") {
        if (Object.prototype.hasOwnProperty.call(result, "ok")) return result.ok === true;
        return true;
      }
      return true;
    }

    function evaluate(state) {
      if (!sessionId) return { send: false, reason: "no_session" };
      var stateGeneration = state && state.generation;
      if (!isPositiveInteger(stateGeneration) || !isPositiveInteger(generation) || stateGeneration !== generation) {
        return { send: false, reason: "stale_generation" };
      }
      var revision = state && state.revision;
      if (!isInteger(revision) || revision < 0) return { send: false, reason: "not_monotonic" };
      if (lastSentRevision !== null && revision <= lastSentRevision) {
        return { send: false, reason: "not_monotonic" };
      }
      return { send: true };
    }

    function settleRefusal(resolve, reason, coalesced) {
      counts.dropped += 1;
      resolve({ ok: true, sent: false, coalesced: coalesced === true, reason: reason });
    }

    function sendEntry(entry) {
      var verdict = evaluate(entry.state);
      if (!verdict.send) {
        inFlight = false;
        settleRefusal(entry.resolve, verdict.reason, entry.coalesced);
        return pump();
      }
      inFlight = true;
      var body;
      try {
        body = buildBody(entry.state);
      } catch (e) {
        inFlight = false;
        settleRefusal(entry.resolve, "invalid_state", entry.coalesced);
        return pump();
      }
      var call;
      try {
        call = transport(endpoint, {
          method: "POST",
          body: JSON.stringify(body),
          headers: { "content-type": "application/json" }
        });
      } catch (e) {
        call = null;
      }
      var promise = (call && typeof call.then === "function") ? call : Promise.resolve(call);
      return promise.then(
        function (value) { return { settled: true, value: value }; },
        function (err) { return { settled: false, value: err }; }
      ).then(function (outcome) {
        inFlight = false;
        var ok = outcome.settled && accepted(outcome.value);
        if (ok) {
          counts.sent += 1;
          if (lastSentRevision === null || body.revision > lastSentRevision) lastSentRevision = body.revision;
        } else {
          counts.rejected += 1;
        }
        entry.resolve({
          ok: true,
          sent: ok,
          coalesced: entry.coalesced === true,
          reason: ok ? undefined : "rejected"
        });
        return pump();
      });
    }

    function pump() {
      if (!queued) { inFlight = false; return Promise.resolve(); }
      var next = queued;
      queued = null;
      return sendEntry(next);
    }

    function settle(state, resolve) {
      var verdict = evaluate(state);
      if (!verdict.send) { settleRefusal(resolve, verdict.reason, false); return; }
      var entry = { state: state, resolve: resolve, coalesced: false };
      if (inFlight) {
        if (queued) {
          counts.dropped += 1;
          queued.resolve({ ok: true, sent: false, coalesced: true, reason: "superseded" });
        }
        entry.coalesced = true;
        queued = entry;
        return;
      }
      sendEntry(entry);
    }

    function publish(state) {
      return new Promise(function (resolve) {
        try {
          if (!transport || typeof transport !== "function") {
            settleRefusal(resolve, "no_transport", false);
            return;
          }
          settle(state, resolve);
        } catch (e) {
          // Absolute last resort: publish never rejects.
          try { resolve({ ok: true, sent: false, reason: "error" }); } catch (ignored) {}
        }
      });
    }

    function toState(change) {
      if (!isPlainObject(change)) return null;
      if (isPlainObject(change.snapshot)) return change;
      if (isInteger(change.revision)) {
        return {
          sessionId: sessionId,
          generation: generation,
          revision: change.revision,
          snapshot: change
        };
      }
      return null;
    }

    function detach() {
      var token = subscription;
      subscription = null;
      if (!token || token.active !== true) return;
      // The token goes inert FIRST so a context that keeps calling the released
      // listener (or that returns no unsubscribe function) cannot publish.
      token.active = false;
      if (typeof token.unsubscribe === "function") {
        try { token.unsubscribe(); } catch (e) { /* already gone */ }
      }
    }

    function attach(context) {
      detach();
      if (!context || typeof context.subscribe !== "function") return detach;
      var token = { active: true, unsubscribe: null };
      subscription = token;
      var result = context.subscribe(function (change) {
        if (token.active !== true) return;
        var state = toState(change);
        if (!state) return;
        publish(state);
      });
      token.unsubscribe = (typeof result === "function") ? result : null;
      return detach;
    }

    function pending() {
      return queued ? 1 : 0;
    }

    function stats() {
      return {
        sent: counts.sent,
        dropped: counts.dropped,
        rejected: counts.rejected,
        lastRevision: lastSentRevision
      };
    }

    return {
      publish: publish,
      attach: attach,
      detach: detach,
      pending: pending,
      stats: stats
    };
  }

  return {
    REFERENCE_KEYS: REFERENCE_KEYS,
    buildBody: buildBody,
    create: create
  };
})();
