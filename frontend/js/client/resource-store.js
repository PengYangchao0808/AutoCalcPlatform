/* Shared resource cache. Callers own cancellation; only supersession,
 * invalidation, or the last departing consumer cancels the underlying request. */
window.ACPClient = window.ACPClient || {};
ACPClient.store = (function () {
  "use strict";
  var entries = Object.create(null);
  var serial = 0;

  function entryFor(key) {
    if (!entries[key]) entries[key] = { key: key, status: undefined, data: undefined,
      error: undefined, at: 0, serial: 0, inflight: null, controller: null,
      subs: new Set(), consumers: new Set(), group: null };
    return entries[key];
  }
  function snapshot(key) {
    var e = entries[key];
    return Object.freeze(e ? { status: e.status, data: e.data, error: e.error, at: e.at }
      : { status: undefined, data: undefined, error: undefined, at: 0 });
  }
  function notify(key) {
    var e = entries[key];
    if (!e) return;
    var snap = snapshot(key);
    e.subs.forEach(function (fn) {
      try { fn(snap); } catch (error) { console.error("Resource subscriber failed", error); }
    });
  }
  function subscribe(key, listener) {
    var e = entryFor(key);
    e.subs.add(listener);
    return function () { e.subs.delete(listener); };
  }
  function namedError(name, message) {
    var error = new Error(message);
    error.name = name;
    return error;
  }
  function retire(e) {
    e.serial = ++serial;
    var controller = e.controller;
    e.inflight = null;
    e.controller = null;
    e.consumers = new Set();
    e.status = e.data === undefined ? undefined : "fresh";
    if (controller) controller.abort();
  }
  function consume(e, promise, signal) {
    if (signal && signal.aborted) return Promise.reject(namedError("AbortError", "Caller cancelled"));
    var consumers = e.consumers;
    var token = {};
    consumers.add(token);
    return new Promise(function (resolve, reject) {
      var settled = false;
      function finish(fn, value) {
        if (settled) return;
        settled = true;
        if (signal) signal.removeEventListener("abort", abort);
        consumers.delete(token);
        fn(value);
      }
      function abort() {
        finish(reject, namedError("AbortError", "Caller cancelled"));
        if (e.consumers === consumers && !consumers.size && e.inflight) {
          retire(e);
          notify(e.key);
        }
      }
      if (signal) signal.addEventListener("abort", abort, { once: true });
      promise.then(function (data) { finish(resolve, data); }, function (error) { finish(reject, error); });
    });
  }
  function query(key, opts) {
    opts = opts || {};
    if (typeof opts.loader !== "function") return Promise.reject(new Error("store.query: loader required"));
    if (opts.signal && opts.signal.aborted) return Promise.reject(namedError("AbortError", "Caller cancelled"));
    var e = entryFor(key);
    if (e.inflight) return consume(e, e.inflight, opts.signal);
    e.group = opts.group || null;
    if (e.group) Object.keys(entries).forEach(function (otherKey) {
      var other = entries[otherKey];
      if (other !== e && other.group === e.group && other.inflight) retire(other);
    });
    var generation = e.serial = ++serial;
    var previousStatus = e.status;
    e.status = "inflight";
    e.error = undefined;
    e.controller = new AbortController();
    e.consumers = new Set();
    var controller = e.controller;
    var promise = new Promise(function (resolve) { resolve(opts.loader(controller.signal)); }).then(
      function (data) {
        if (entries[key] !== e || e.serial !== generation) throw namedError("StaleDiscard", "Resource superseded");
        var unchanged = opts.isEqual && previousStatus === "fresh" && opts.isEqual(e.data, data);
        e.status = "fresh";
        if (!unchanged) e.data = data;
        e.error = undefined;
        e.at = Date.now();
        notify(key);
        return data;
      },
      function (error) {
        if (entries[key] !== e || e.serial !== generation) throw namedError("StaleDiscard", "Resource superseded");
        if (error && error.name === "AbortError") {
          e.status = e.data === undefined ? undefined : "fresh";
          throw error;
        }
        e.status = "error";
        e.error = error;
        e.at = Date.now();
        notify(key);
        throw error;
      }
    ).finally(function () {
      // A late request must never clear a newer generation's in-flight slot.
      if (e.serial === generation) { e.inflight = null; e.controller = null; }
    });
    e.inflight = promise;
    notify(key);
    return consume(e, promise, opts.signal);
  }
  function invalidate(matcher) {
    var dropped = [];
    Object.keys(entries).forEach(function (key) {
      var hit = typeof matcher === "function" ? matcher(key)
        : typeof matcher === "string" && matcher.slice(-1) === "*"
          ? key.indexOf(matcher.slice(0, -1)) === 0 : key === matcher;
      if (!hit) return;
      var e = entries[key];
      retire(e);
      e.status = undefined; e.data = undefined; e.error = undefined; e.at = 0;
      dropped.push(key);
      notify(key);
    });
    return dropped;
  }
  function reset() {
    Object.keys(entries).forEach(function (key) { retire(entries[key]); });
    entries = Object.create(null);
  }
  return { subscribe: subscribe, snapshot: snapshot, query: query, invalidate: invalidate, reset: reset };
})();
