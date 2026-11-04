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

  var API_TIMEOUT_MS = 8000;
  var API_REMOTE_TIMEOUT_MS = 60000;

  var started = false;

  // Host seam (js/shell/host-env.js). Without it the shell keeps the literals
  // it always used, so the standalone Workbench is byte-for-byte unchanged.
  function env() {
    return window.ACPShell && window.ACPShell.env ? window.ACPShell.env : null;
  }
  function doc() {
    return env() ? env().doc() : window.document;
  }
  function apiBase() {
    return env() ? env().apiBase() : "/api/v1";
  }
  function apiV2Base() {
    return env() ? env().apiV2Base() : "/api/v2";
  }

  function api(path, opts) {
    return observed(window.ACPClient.transport.request(apiBase() + path, opts));
  }
  function apiV2(path, opts) {
    return observed(window.ACPClient.transport.request(apiV2Base() + path, opts));
  }
  function observed(promise) {
    return Promise.resolve(promise).then(function (value) {
      if (ACPShell.connection) ACPShell.connection.noteSuccess();
      return value;
    }, function (error) {
      if (ACPShell.connection) ACPShell.connection.noteFailure(error);
      throw error;
    });
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
    if (C.events) C.events.init({ baseUrl: apiBase() });
    if (C.taskView) C.taskView.init({ v1: api, v2: apiV2 });
    if (C.projectFiles) C.projectFiles.init({ v2: apiV2 });
    if (C.research) C.research.init({ v2: apiV2 });
    if (C.researchCommands) C.researchCommands.init({ v1: api, v2: apiV2 });
    if (C.eventInvalidation) C.eventInvalidation.init({ v1: api, v2: apiV2 });
    if (C.jobDetail) C.jobDetail.init({ v1: api, v1Remote: apiRemote, onLegacyPin: function () {} });
    if (C.jobCommands) C.jobCommands.init({ v1: api, v2: apiV2 });
    if (C.frameCandidates) C.frameCandidates.init({ v1: api });
    if (C.science) C.science.init({ v1: api, v2: apiV2 });
    if (C.wizardEndpoints) {
      // wizardEndpoints takes { v1, direct, baseUrl } — direct = full-URL
      // transport (literal /api/backends etc.), same as compat/api.js.
      C.wizardEndpoints.init({
        v1: api,
        direct: function (url, opts) { return window.ACPClient.transport.request(url, opts); },
        baseUrl: apiBase()
      });
    }
    if (C.systemEndpoints) {
      C.systemEndpoints.init({
        v1: api,
        direct: function (url, opts) { return window.ACPClient.transport.request(url, opts); },
        baseUrl: apiBase()
      });
    }
    // NOTE: polling-strategy / event-stream are initialized but NOT started.
  }

  function showFatal(message) {
    var main = doc().getElementById("acp-shell-main");
    var fatal = doc().getElementById("acp-shell-fatal");
    if (!fatal) return;
    if (main) main.hidden = true;
    fatal.hidden = false;
    // A-63: the fatal layer is a labelled alert dialog and takes focus, so a
    // keyboard user lands on the recovery action instead of a silent page.
    fatal.setAttribute("role", "alertdialog");
    fatal.setAttribute("aria-labelledby", "acp-shell-fatal-title");
    fatal.setAttribute("aria-describedby", "acp-shell-fatal-msg");
    fatal.setAttribute("data-acp-dialog", "fatal");
    var existing = fatal.querySelector(".acp-shell-fatal-card");
    if (existing && existing.parentNode) existing.parentNode.removeChild(existing);
    var card = doc().createElement("div");
    card.setAttribute("class", "acp-shell-fatal-card");
    var title = doc().createElement("p");
    title.setAttribute("class", "acp-state-title");
    title.setAttribute("id", "acp-shell-fatal-title");
    title.textContent = ACPShell.i18n.t("shell.fatal.title");
    var msg = doc().createElement("p");
    msg.setAttribute("class", "acp-state-msg mono");
    msg.setAttribute("id", "acp-shell-fatal-msg");
    msg.textContent = String(message || "unknown error");
    var actions = doc().createElement("div");
    actions.setAttribute("class", "acp-state-actions");
    var retry = doc().createElement("button");
    retry.setAttribute("class", "acp-btn acp-btn--primary");
    retry.setAttribute("data-action", "fatal-retry");
    retry.textContent = ACPShell.i18n.t("shell.fatal.retry");
    retry.addEventListener("click", function () {
      fatal.hidden = true;
      if (main) main.hidden = false;
      try { start(); } catch (error) { showFatal(error && error.message ? error.message : error); }
    });
    var back = doc().createElement("a");
    back.setAttribute("class", "acp-btn acp-btn--ghost");
    back.setAttribute("href", env() ? env().homeHref() : "/");
    back.textContent = ACPShell.i18n.t("shell.fatal.back_classic");
    actions.appendChild(retry);
    actions.appendChild(back);
    card.appendChild(title);
    card.appendChild(msg);
    card.appendChild(actions);
    fatal.appendChild(card);
    if (typeof retry.focus === "function") {
      try { retry.focus(); } catch (error) { /* focus unavailable in this environment */ }
    }
  }

  function bindLangToggle() {
    var btn = doc().getElementById("acp-lang-toggle");
    if (!btn || !btn.addEventListener) return;
    btn.addEventListener("click", function () {
      ACPShell.i18n.toggle();
    });
  }

  function isDemo() {
    try {
      var loc = env() ? env().location() : window.location;
      return /(\?|&)demo=1(&|$)/.test(loc && loc.search || "");
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
    if (ACPShell.connection) ACPShell.connection.init({ request: api });
    // Bulk-collect mock / late-registered modules from the static table.
    ACPShell.registry.collectFrom(window.ACPNextModules);
    ACPShell.commands.init({ context: ACPShell.context, layout: ACPShell.layout });
    ACPShell.layout.init({
      context: ACPShell.context,
      loader: ACPShell.loader,
      registry: ACPShell.registry,
      i18n: ACPShell.i18n,
      request: api
    });
    if (ACPShell.globalRegions) ACPShell.globalRegions.init({ context: ACPShell.context, loader: ACPShell.loader, request: api });
    if (ACPShell.platformPanels) ACPShell.platformPanels.init();
    if (ACPShell.projectSwitcher) ACPShell.projectSwitcher.init({ context: ACPShell.context, request: api, i18n: ACPShell.i18n });
    if (ACPShell.topbar) ACPShell.topbar.init({ layout: ACPShell.layout, i18n: ACPShell.i18n });
    bindLangToggle();
    if (ACPShell.layoutEditor) ACPShell.layoutEditor.init();
    var recipeId = isDemo() ? "demo" : ACPShell.preferences ? ACPShell.preferences.active() : "research";
    var applied = ACPShell.layout.applyRecipe(recipeId);
    if (!applied || applied.ok === false) {
      throw new Error("default recipe '" + recipeId + "' failed: " + (applied && applied.error || "no recipe"));
    }
    var shellRoot = doc().getElementById("acp-shell");
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
