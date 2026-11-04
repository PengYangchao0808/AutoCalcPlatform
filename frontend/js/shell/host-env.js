/*
 * ACP Next Shell — host environment seam (P1).
 *
 * One place where the shell asks "where am I running?". The standalone
 * Workbench page installs nothing and therefore keeps today's values
 * (asset base "", /api/v1 + /api/v2, window.document, no storage namespace).
 * An embedding host (e.g. the DeepSeek Harness panel served under /acp) sets
 * window.ACP_ENV before the shell scripts run and gets its own asset prefix,
 * API bases, storage namespace and doc/win roots.
 *
 * window.ACP_ENV is read LAZILY on every accessor call, so a host may install
 * it after this file has loaded. Every accessor falls back to the standalone
 * behaviour when window.ACP_ENV is absent, empty or malformed — loading this
 * file without ACP_ENV is a byte-for-byte no-op for the shell.
 */
window.ACPShell = window.ACPShell || {};
ACPShell.env = (function () {
  "use strict";

  var DEFAULTS = Object.freeze({
    host: "acp-web",
    assetBase: "",
    apiBase: "/api/v1",
    apiV2Base: "/api/v2",
    manageDocument: true,
    storagePrefix: "",
    doc: null,
    win: null,
    location: null,
    homeHref: "/",
    sessionId: null,
    focusBridge: null,
    degraded: false
  });

  function trim(value) {
    return String(value).replace(/^[\s]+/, "").replace(/[\s]+$/, "");
  }

  /** Non-empty string, else the default ("" and "   " fall back). */
  function text(value, fallback) {
    if (typeof value !== "string") return fallback;
    var trimmed = trim(value);
    return trimmed ? trimmed : fallback;
  }

  /** API base: non-empty string, no trailing slash (paths are "/x" joined). */
  function baseText(value, fallback) {
    var base = text(value, "").replace(/\/+$/, "");
    return base ? base : fallback;
  }

  /** Normalised config; a fresh frozen copy on every call. */
  function raw() {
    var source = window.ACP_ENV;
    if (!source || typeof source !== "object") source = {};
    var config = {
      host: text(source.host, DEFAULTS.host),
      assetBase: typeof source.assetBase === "string" ? trim(source.assetBase) : DEFAULTS.assetBase,
      apiBase: baseText(source.apiBase, DEFAULTS.apiBase),
      apiV2Base: baseText(source.apiV2Base, DEFAULTS.apiV2Base),
      manageDocument: source.manageDocument !== false,
      storagePrefix: typeof source.storagePrefix === "string" ? trim(source.storagePrefix) : DEFAULTS.storagePrefix,
      doc: source.doc || DEFAULTS.doc,
      win: source.win || DEFAULTS.win,
      location: source.location || DEFAULTS.location,
      homeHref: text(source.homeHref, DEFAULTS.homeHref),
      sessionId: typeof source.sessionId === "string" && source.sessionId ? source.sessionId : DEFAULTS.sessionId,
      focusBridge: source.focusBridge && typeof source.focusBridge === "object" ? source.focusBridge : DEFAULTS.focusBridge,
      degraded: source.degraded == null ? DEFAULTS.degraded : Boolean(source.degraded)
    };
    return Object.freeze(config);
  }

  function host() { return raw().host; }
  function sessionId() { return raw().sessionId; }
  function degraded() { return raw().degraded; }
  function manageDocument() { return raw().manageDocument; }
  function focusBridge() { return raw().focusBridge; }
  function homeHref() { return raw().homeHref; }

  function doc() { return raw().doc || window.document; }
  function win() { return raw().win || window; }
  function location() { return raw().location || window.location; }

  /** Exactly one slash between the asset base and the path. */
  function assetUrl(path) {
    if (typeof path !== "string") return path;
    var base = raw().assetBase.replace(/\/+$/, "");
    if (!base) return path;
    var rest = path.replace(/^\/+/, "");
    if (!rest) return base + "/";
    return base + "/" + rest;
  }

  function apiBase() { return raw().apiBase; }
  function apiV2Base() { return raw().apiV2Base; }

  function storageKey(key) {
    if (typeof key !== "string") return key;
    return raw().storagePrefix + key;
  }

  // Storage is namespaced through storageKey() and never throws: a sandboxed /
  // privacy-mode localStorage degrades to "nothing persisted", exactly like
  // the shell's own try/catch wrappers.
  var storage = {
    get: function (key) {
      try {
        var store = win().localStorage;
        return store ? store.getItem(storageKey(key)) : null;
      } catch (e) { return null; }
    },
    set: function (key, value) {
      try {
        var store = win().localStorage;
        if (store) store.setItem(storageKey(key), String(value));
      } catch (e) { /* storage unavailable — session-only value */ }
    },
    remove: function (key) {
      try {
        var store = win().localStorage;
        if (store) store.removeItem(storageKey(key));
      } catch (e) { /* storage unavailable — nothing to remove */ }
    }
  };

  /** False only when the embedding host owns the page title / <html lang>. */
  function isDomControlled() { return manageDocument() !== false; }

  return {
    // contract metadata, for hosts and tests
    defaults: DEFAULTS,
    raw: raw,
    host: host,
    sessionId: sessionId,
    degraded: degraded,
    manageDocument: manageDocument,
    focusBridge: focusBridge,
    homeHref: homeHref,
    doc: doc,
    win: win,
    location: location,
    assetUrl: assetUrl,
    apiBase: apiBase,
    apiV2Base: apiV2Base,
    storageKey: storageKey,
    storage: storage,
    isDomControlled: isDomControlled
  };
})();
