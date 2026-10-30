/*
 * ACP Next Shell — bootstrap (UI-01).
 * Wires i18n → client stack → registry → loader → context → layout →
 * commands, then applies the default (or ?demo=1) recipe.
 *
 * Client init mirrors js/workbench/compat/api.js:25-51 with the SAME
 * parameters (transport budgets, v1/v2 adapter injection, event-stream URL
 * base) — but the Shell owns its own thin api()/apiV2() closures over
 * ACPClient.transport and NEVER starts polling / SSE (UI-01 is read-silent;
 * data flows in via commands + context only).
 *
 * Error boundary: any failure in this start() chain renders the whole-page
 * fatal layer (design §2.2) with 重试初始化 + 返回经典工作台. A single panel
 * failing later never reaches here (layout-manager keeps it local).
 */
window.ACPShell = window.ACPShell || {};
ACPShell.bootstrap = (function () {
  "use strict";

  var API_BASE = "/api/v1";
  var API_TIMEOUT_MS = 8000;
  var API_REMOTE_TIMEOUT_MS = 60000;

  var started = false;

  function api(path, opts) {
    return window.ACPClient.transport.request(API_BASE + path, opts);
  }
  function apiV2(path, opts) {
    return window.ACPClient.transport.request("/api/v2" + path, opts);
  }
  function apiRemote(path, opts) {
    opts = opts || {};
    if (opts.timeoutMs == null) opts.timeoutMs = API_REMOTE_TIMEOUT_MS;
    return api(path, opts);
  }

  function initClient() {
    var C = window.ACPClient;
    if (!C || !C.transport) return;
    C.transport.init({
      defaultTimeoutMs: API_TIMEOUT_MS,
      remoteTimeoutMs: API_REMOTE_TIMEOUT_MS,
      translate: ACPShell.i18n.t
    });
    if (C.events) C.events.init({ baseUrl: API_BASE });
    if (C.taskView) C.taskView.init({ v1: api, v2: apiV2 });
    if (C.research) C.research.init({ v2: apiV2 });
    if (C.eventInvalidation) C.eventInvalidation.init({ v1: api, v2: apiV2 });
    if (C.jobDetail) C.jobDetail.init({ v1: api, v1Remote: apiRemote, onLegacyPin: function () {} });
    if (C.jobCommands) C.jobCommands.init({ v1: api, v2: apiV2 });
    if (C.science) C.science.init({ v1: api, v2: apiV2 });
    if (C.wizardEndpoints) {
      // wizardEndpoints takes { v1, direct, baseUrl } — direct = full-URL
      // transport (literal /api/backends etc.), same as compat/api.js.
      C.wizardEndpoints.init({
        v1: api,
        direct: function (url, opts) { return window.ACPClient.transport.request(url, opts); },
        baseUrl: API_BASE
      });
    }
    if (C.systemEndpoints) {
      C.systemEndpoints.init({
        v1: api,
        direct: function (url, opts) { return window.ACPClient.transport.request(url, opts); },
        baseUrl: API_BASE
      });
    }
    // NOTE: polling-strategy / event-stream are initialized but NOT started.
  }

  function showFatal(message) {
    var main = document.getElementById("acp-shell-main");
    var fatal = document.getElementById("acp-shell-fatal");
    if (!fatal) return;
    if (main) main.hidden = true;
    fatal.hidden = false;
    fatal.setAttribute("role", "alert");
    var existing = fatal.querySelector(".acp-shell-fatal-card");
    if (existing && existing.parentNode) existing.parentNode.removeChild(existing);
    var card = document.createElement("div");
    card.setAttribute("class", "acp-shell-fatal-card");
    var title = document.createElement("p");
    title.setAttribute("class", "acp-state-title");
    title.textContent = ACPShell.i18n.t("shell.fatal.title");
    var msg = document.createElement("p");
    msg.setAttribute("class", "acp-state-msg mono");
    msg.textContent = String(message || "unknown error");
    var actions = document.createElement("div");
    actions.setAttribute("class", "acp-state-actions");
    var retry = document.createElement("button");
    retry.setAttribute("class", "acp-btn acp-btn--primary");
    retry.setAttribute("data-action", "fatal-retry");
    retry.textContent = ACPShell.i18n.t("shell.fatal.retry");
    retry.addEventListener("click", function () {
      fatal.hidden = true;
      if (main) main.hidden = false;
      try { start(); } catch (error) { showFatal(error && error.message ? error.message : error); }
    });
    var back = document.createElement("a");
    back.setAttribute("class", "acp-btn acp-btn--ghost");
    back.setAttribute("href", "/");
    back.textContent = ACPShell.i18n.t("shell.fatal.back_classic");
    actions.appendChild(retry);
    actions.appendChild(back);
    card.appendChild(title);
    card.appendChild(msg);
    card.appendChild(actions);
    fatal.appendChild(card);
  }

  function bindLangToggle() {
    var btn = document.getElementById("acp-lang-toggle");
    if (!btn || !btn.addEventListener) return;
    btn.addEventListener("click", function () {
      ACPShell.i18n.toggle();
    });
  }

  function isDemo() {
    try {
      return /(\?|&)demo=1(&|$)/.test(window.location && window.location.search || "");
    } catch (e) {
      return false;
    }
  }

  /**
   * Start (or restart after fatal). Idempotent for the wiring; the recipe is
   * re-applied on every call.
   */
  function start() {
    ACPShell.i18n.applyI18n(document);
    initClient();
    // Bulk-collect mock / late-registered modules from the static table.
    ACPShell.registry.collectFrom(window.ACPNextModules);
    ACPShell.commands.init({ context: ACPShell.context, layout: ACPShell.layout });
    ACPShell.layout.init({
      context: ACPShell.context,
      loader: ACPShell.loader,
      registry: ACPShell.registry,
      i18n: ACPShell.i18n
    });
    bindLangToggle();
    if (ACPShell.layoutEditor) ACPShell.layoutEditor.init();
    var recipeId = isDemo() ? "demo" : ACPShell.preferences ? ACPShell.preferences.active() : "research";
    var applied = ACPShell.layout.applyRecipe(recipeId);
    if (!applied || applied.ok === false) {
      throw new Error("default recipe '" + recipeId + "' failed: " + (applied && applied.error || "no recipe"));
    }
    var shellRoot = document.getElementById("acp-shell");
    if (shellRoot) shellRoot.setAttribute("data-acp-ready", "true");
    started = true;
    return applied;
  }

  /** Entry: wrap start in the whole-page fatal boundary. */
  function boot() {
    try {
      return start();
    } catch (error) {
      showFatal(error && error.message ? error.message : error);
      return { ok: false, fatal: true };
    }
  }

  return {
    boot: boot,
    start: start,
    showFatal: showFatal,
    isStarted: function () { return started; },
    // test seams
    _api: api,
    _apiV2: apiV2,
    _apiRemote: apiRemote,
    _initClient: initClient
  };
})();
