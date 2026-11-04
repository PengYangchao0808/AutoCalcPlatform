/*
 * ACP adapter layer — DSH host environment descriptor PRODUCER.
 *
 * The shell reads `window.ACP_ENV` lazily (js/shell/host-env.js). This module
 * builds and installs that object for a DSH-hosted Workbench; it is the only
 * place that decides the DSH URL layout.
 *
 * Contract (normalised env):
 *   { host:"dsh", apiPrefix, apiBase, apiV2Base, assetBase, sessionId,
 *     generation, focusEndpoint, storagePrefix, manageDocument }
 *   apiBase   = apiPrefix + "/v1"
 *   apiV2Base = apiPrefix + "/v2"
 *   focusEndpoint defaults to apiPrefix + "/ui/focus"
 *
 * No DOM / fetch / storage access at load time; nothing here touches the
 * document at all.
 */
window.ACPAdapters = window.ACPAdapters || {};
ACPAdapters.dsh = ACPAdapters.dsh || {};

ACPAdapters.dsh.env = (function () {
  "use strict";

  var DEFAULTS = {
    apiPrefix: "/acp-api",
    assetBase: "/acp",
    storagePrefix: "dsh:",
    manageDocument: false
  };

  function fail(code, message) {
    var err = new Error(message);
    err.code = code;
    return err;
  }

  /* Collapse duplicate slashes and drop a trailing slash (keep a bare "/"). */
  function normalisePath(value) {
    var out = String(value).replace(/\/{2,}/g, "/");
    if (out.length > 1 && out.charAt(out.length - 1) === "/") out = out.slice(0, -1);
    return out;
  }

  function joinBase(prefix, suffix) {
    if (prefix === "/") return suffix;
    return prefix + suffix;
  }

  function build(options) {
    options = options || {};

    var apiPrefix = (options.apiPrefix == null) ? DEFAULTS.apiPrefix : options.apiPrefix;
    if (typeof apiPrefix !== "string" || apiPrefix === "") {
      throw fail("E_ENV_INPUT", "apiPrefix must be a non-empty string");
    }
    apiPrefix = normalisePath(apiPrefix);
    if (apiPrefix.charAt(0) !== "/") {
      throw fail("E_ENV_INPUT", "apiPrefix must start with '/'");
    }

    var assetBase = (typeof options.assetBase === "string")
      ? normalisePath(options.assetBase)
      : DEFAULTS.assetBase;

    var focusEndpoint = (typeof options.focusEndpoint === "string" && options.focusEndpoint !== "")
      ? options.focusEndpoint
      : joinBase(apiPrefix, "/ui/focus");

    var storagePrefix = (typeof options.storagePrefix === "string")
      ? options.storagePrefix
      : DEFAULTS.storagePrefix;

    var sessionId = (typeof options.sessionId === "string" && options.sessionId !== "")
      ? options.sessionId
      : null;

    return {
      host: "dsh",
      apiPrefix: apiPrefix,
      apiBase: joinBase(apiPrefix, "/v1"),
      apiV2Base: joinBase(apiPrefix, "/v2"),
      assetBase: assetBase,
      sessionId: sessionId,
      generation: (options.generation == null) ? null : options.generation,
      focusEndpoint: focusEndpoint,
      storagePrefix: storagePrefix,
      manageDocument: options.manageDocument === true
    };
  }

  /* Install onto ACP_ENV. Refuses an existing value unless options.force. */
  function install(win, options) {
    options = options || {};
    var target = win || window;
    if (target.ACP_ENV != null && options.force !== true) {
      throw fail("E_ENV_INSTALLED", "ACP_ENV is already installed");
    }
    var env = build(options);
    target.ACP_ENV = env;
    return env;
  }

  return { DEFAULTS: DEFAULTS, build: build, install: install };
})();
