/* One connection state for the shell. Real evidence only: an explicit /status
 * probe, successful API responses, or a failed request. No polling. */
window.ACPShell = window.ACPShell || {};
ACPShell.connection = (function () {
  "use strict";
  var STATE = "unknown", request = null, bound = false, probing = null, listeners = [];
  function doc() { return ACPShell.env ? ACPShell.env.doc() : document; }
  function t(key, fallback) {
    try { var out = ACPShell.i18n && ACPShell.i18n.t ? ACPShell.i18n.t(key) : key; return out === key ? fallback : out; }
    catch (e) { return fallback; }
  }
  function dotEls() { return [doc().getElementById("acp-conn-pill"), doc().getElementById("acp-conn-menu-dot")].filter(Boolean); }
  function paint() {
    var d = doc();
    var label = STATE === "online" ? t("shell.conn.online", "已连接") : STATE === "offline" ? t("shell.conn.offline", "异常") : t("shell.conn.unknown", "未知");
    dotEls().forEach(function (dot) { dot.setAttribute("data-conn", STATE); dot.title = label; });
    var button = d.getElementById("acp-conn-status");
    if (button) {
      button.setAttribute("data-conn", STATE);
      button.setAttribute("aria-label", label + "，" + t("shell.conn.retry", "重试连接"));
      button.title = label + "，" + t("shell.conn.retry", "重试连接");
    }
    var topLabel = d.getElementById("acp-conn-label");
    if (topLabel) topLabel.textContent = label;
    var menuText = d.getElementById("acp-conn-text");
    if (menuText) menuText.textContent = label;
    listeners.slice().forEach(function (fn) { try { fn(STATE); } catch (e) { /* observers are best effort */ } });
  }
  function set(state) {
    var next = state === "online" || state === "offline" ? state : "unknown";
    if (next === STATE) return STATE;
    STATE = next; paint(); return STATE;
  }
  function noteSuccess() { if (STATE !== "online") set("online"); }
  function noteFailure(error) {
    var code = Number(error && (error.status || error.statusCode));
    if (code === 503 || code === 0) set("offline");
    else if (STATE === "unknown") set("unknown");
  }
  function probe() {
    if (probing) return probing;
    if (!request) { set("unknown"); return Promise.resolve(STATE); }
    probing = Promise.resolve(request("/status", { method: "GET" })).then(function () {
      set("online"); return STATE;
    }, function (error) {
      noteFailure(error); return STATE;
    }).then(function (state) { probing = null; return state; });
    return probing;
  }
  function refreshCurrentView() {
    var d = doc();
    try { d.dispatchEvent(new CustomEvent("acp:connection-retry", { detail: { state: STATE } })); } catch (e) { /* older browsers */ }
    if (ACPShell.layout && typeof ACPShell.layout.reloadCurrentWorkspace === "function") {
      try { ACPShell.layout.reloadCurrentWorkspace(); } catch (e) { /* panel reload is best effort */ }
    }
    var taskRefresh = d.querySelector ? d.querySelector("#acp-next-task-host .acp-mod-tasks-refresh") : null;
    if (taskRefresh && taskRefresh.click) { try { taskRefresh.click(); } catch (e) { /* task retry is visible */ } }
  }
  function retry() {
    var state = probe();
    Promise.resolve(state).then(function () { refreshCurrentView(); });
    return state;
  }
  function bind() {
    if (bound) return; bound = true;
    var button = doc().getElementById("acp-conn-status");
    if (button && button.addEventListener) button.addEventListener("click", retry);
    var d = doc();
    if (d && d.addEventListener) d.addEventListener("acp:langchange", paint);
    var win = typeof window !== "undefined" ? window : null;
    if (win && win.addEventListener) {
      win.addEventListener("offline", function () { set("offline"); });
      win.addEventListener("online", function () { set("unknown"); });
    }
  }
  function init(options) {
    options = options || {};
    if (typeof options.request === "function") request = options.request;
    bind(); paint();
    if (options.probe !== false) probe();
  }
  return { init: init, probe: probe, retry: retry, noteSuccess: noteSuccess, noteFailure: noteFailure, state: function () { return STATE; }, subscribe: function (fn) { if (typeof fn === "function") listeners.push(fn); return function () { var i = listeners.indexOf(fn); if (i >= 0) listeners.splice(i, 1); }; } };
})();
