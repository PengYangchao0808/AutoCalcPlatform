/*
 * ACP adapter layer — standalone (acp-web) environment descriptor.
 *
 * The standalone Workbench page must keep working with or without ACP_ENV, so
 * this producer only fills the gaps: install() never overwrites an existing
 * descriptor. No DOM / fetch at load time.
 */
window.ACPAdapters = window.ACPAdapters || {};
ACPAdapters.acpWeb = ACPAdapters.acpWeb || {};

ACPAdapters.acpWeb.env = (function () {
  "use strict";

  var KEY_DEFAULTS = {
    host: "acp-web",
    assetBase: "",
    apiBase: "/api/v1",
    apiV2Base: "/api/v2",
    manageDocument: true,
    storagePrefix: "",
    sessionId: null,
    focusBridge: null,
    degraded: false
  };

  function build(options) {
    var env = {};
    Object.keys(KEY_DEFAULTS).forEach(function (key) { env[key] = KEY_DEFAULTS[key]; });
    options = options || {};
    /* Unknown keys are dropped: the descriptor shape is fixed. */
    Object.keys(KEY_DEFAULTS).forEach(function (key) {
      if (options[key] !== undefined) env[key] = options[key];
    });
    env.host = "acp-web";
    return env;
  }

  function install(win, options) {
    var target = win || window;
    if (target.ACP_ENV != null) return { ok: true, installed: false };
    target.ACP_ENV = build(options);
    return { ok: true, installed: true };
  }

  return { KEY_DEFAULTS: KEY_DEFAULTS, build: build, install: install };
})();
