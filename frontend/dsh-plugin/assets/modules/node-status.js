/* file: js/modules/node-status/module.js */
/*
 * ACP Next — node-status panel (A-30 / A-31, system & resources surface).
 *
 * Classic script (no build, no ESM). Registers exactly one key on the global
 * module namespace; the Shell loader resolves the factory and mounts the
 * returned instance into an arbitrary host element. No page globals are
 * written besides `window.ACPNextModules`.
 *
 * Endpoints (existing v1 contract; nothing invented, no new writes):
 *   GET  {API_BASE}/nodes                          -> { nodes: [...], auto_select }
 *   GET  {API_BASE}/nodes/{name}/status            -> NodeStatusModel
 *   POST {API_BASE}/nodes/{name}/ping              -> { reachable, node, status, error }
 *   POST {API_BASE}/nodes/{name}/bootstrap         -> NodeBootstrapResponse
 *   GET  {API_BASE}/software/discovery             -> { software: [...] }
 *   GET  {API_BASE}/backends                       -> { backends: [...] }
 *
 * A-30 = node list/status + per-node ping/bootstrap with busy/error states and
 *   clear failure text. A-31 = discovered software/backends and availability;
 *   a field that was never probed is rendered as 未知/未发现 — this module
 *   never guesses a version or a path.
 *
 * Data access is strictly injected:
 *   1. `deps.fetch`            fetch-like `(url, init) -> Response` (or a
 *                              request-like callable returning a parsed body);
 *   2. `deps.request`          request-like `(path, opts) -> parsed body`;
 *   3. `deps.client.transport.request`  the Shell/page transport chokepoint;
 *   4. `window.ACPClient.transport.request` page-level fallback.
 * When none exists the panel renders a LOCAL error state and performs no
 * network call at all (never `window.fetch`).
 *
 * Four states live on the module root: `data-acp-module-state` =
 * empty | loading | ready | error. Every async result carries a generation +
 * token guard, so dispose / reload / a newer refresh can never write into the
 * DOM. dispose() is idempotent, removes the DOM it created, and clears every
 * timer it owns (the bootstrap elapsed-time ticker).
 *
 * Boundary: read-only apart from the two existing node commands (ping and
 * bootstrap). There is no task submission and no other node mutation here.
 */
(function () {
  "use strict";

  window.ACPNextModules = window.ACPNextModules || {};

  var MODULE_ID = "node-status";
  var DEFAULT_API_BASE = "/api/v1";

  var DESCRIPTOR = {
    id: MODULE_ID,
    interfaceVersion: 1,
    version: "0.1.0",
    provides: ["system.read"],
    capabilities: { requires: [], provides: ["system.read"] },
    hosts: ["acp-web"],
    supportedInstancing: "single",
    configSchemaVersion: 0,
    error: { titleKey: "next.nodes.errorTitle", messageKey: "next.nodes.errorMessage" }
  };

  /*
   * Local zh dictionary (module copy). The Shell dictionary is consulted
   * first, but its `t()` echoes the key when a translation is missing — the
   * inline text below is therefore the real fallback until `next.nodes.*`
   * lands in js/shell/i18n.js (see README integration steps).
   */
  var ZH = {
    "next.nodes.title": "节点与软件",
    "next.nodes.subtitle": "远程节点状态、软件发现与后端可用性",
    "next.nodes.refresh": "刷新",
    "next.nodes.refreshing": "刷新中…",
    "next.nodes.loading": "正在读取系统信息…",
    "next.nodes.empty": "没有可显示的系统信息。",
    "next.nodes.empty_hint": "节点、后端与软件发现均为空；远程执行可能尚未配置。",
    "next.nodes.error": "系统信息加载失败：{message}",
    "next.nodes.retry": "重试",
    "next.nodes.errorTitle": "节点与软件不可用",
    "next.nodes.errorMessage": "node-status 模块加载失败，请查看控制台。",
    "next.nodes.no_adapter": "未注入 fetch 或 client.transport.request，无法读取系统信息。",
    "next.nodes.section.nodes": "远程节点",
    "next.nodes.section.backends": "计算后端",
    "next.nodes.section.software": "软件发现",
    "next.nodes.section_error": "{section}加载失败：{message}",
    "next.nodes.auto_select_on": "自动选择已启用",
    "next.nodes.auto_select_off": "自动选择未启用",
    "next.nodes.nodes_empty": "未配置远程节点",
    "next.nodes.backends_empty": "没有后端条目",
    "next.nodes.software_empty": "没有软件发现结果",
    "next.nodes.backend_summary": "可用 {available} / {total} 个后端",
    "next.nodes.software_summary": "发现 {found} / {total} 个软件",
    "next.nodes.not_configured": "未配置远程节点（服务端未启用远程执行）",
    "next.nodes.not_configured_hint": "本机未启用远程执行时，节点列表为空是正常状态。",
    "next.nodes.unnamed": "未命名节点",
    "next.nodes.unknown": "未知",
    "next.nodes.version": "版本",
    "next.nodes.path": "路径",
    "next.nodes.source": "来源",
    "next.nodes.meta_jobs": "作业",
    "next.nodes.meta_disk": "磁盘",
    "next.nodes.meta_queue": "队列",
    "next.nodes.meta_checked": "上次检查",
    "next.nodes.meta_capability": "能力",
    "next.nodes.status.online": "在线",
    "next.nodes.status.degraded": "高负载",
    "next.nodes.status.offline": "离线",
    "next.nodes.status.unknown": "状态未知",
    "next.nodes.capability.declared": "已声明",
    "next.nodes.capability.probe_inferred": "探测推断",
    "next.nodes.capability.unknown": "能力未知",
    "next.nodes.declared_ok": "声明一致",
    "next.nodes.declared_mismatch": "声明与实际不一致",
    "next.nodes.mismatch_list": "不一致项",
    "next.nodes.probe_note": "探测说明：{note}",
    "next.nodes.node_error": "节点错误：{message}",
    "next.nodes.node_software": "已探测软件（{count}）",
    "next.nodes.ping": "测试连通",
    "next.nodes.pinging": "测试中…",
    "next.nodes.ping_ok": "可达",
    "next.nodes.ping_unreachable": "不可达：{message}",
    "next.nodes.ping_error": "连通测试失败：{message}",
    "next.nodes.bootstrap": "引导安装",
    "next.nodes.bootstrapping": "引导中… {seconds}s",
    "next.nodes.bootstrap_ok": "引导完成（exit {code}）",
    "next.nodes.bootstrap_failed": "引导失败：{message}",
    "next.nodes.bootstrap_exit": "引导失败：退出码 {code}",
    "next.nodes.bootstrap_unreachable": "节点不可达，引导未执行",
    "next.nodes.stderr_tail": "stderr 尾部",
    "next.nodes.stdout_tail": "stdout 尾部",
    "next.nodes.status_refresh_failed": "状态刷新失败：{message}",
    "next.nodes.version_unknown": "未知",
    "next.nodes.path_missing": "未发现可执行文件",
    "next.nodes.source_unknown": "来源未知",
    "next.nodes.found": "已发现",
    "next.nodes.not_found": "未发现",
    "next.nodes.candidates": "候选安装（{count}）",
    "next.nodes.candidate_active": "当前生效",
    "next.nodes.backend.available": "可用",
    "next.nodes.backend.unavailable": "不可用",
    "next.nodes.backend.remote": "远程：{nodes}",
    "next.nodes.backend.remote_detail": "远程 {path} · 版本 {version}",
    "next.nodes.backend.capabilities": "能力",
    "next.nodes.backend.cap_yes": "支持",
    "next.nodes.backend.cap_no": "不支持"
  };

  /* ── translations / small helpers ───────────────────────────────────── */

  function interpolate(value, vars) {
    if (!vars) return value;
    Object.keys(vars).forEach(function (name) {
      value = value.split("{" + name + "}").join(String(vars[name]));
    });
    return value;
  }

  /** Shell dictionary first (real translations only), local zh fallback. */
  function t(key, vars) {
    var shell = typeof window !== "undefined" && window.ACPShell ? window.ACPShell.i18n : null;
    if (shell && typeof shell.t === "function") {
      var translated = shell.t(key, vars);
      if (translated && translated !== key) return translated;
    }
    var raw = Object.prototype.hasOwnProperty.call(ZH, key) ? ZH[key] : key;
    return interpolate(raw, vars);
  }

  function el(doc, tag, cls, text) {
    var node = doc.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function clearChildren(node) {
    if (!node) return;
    if (typeof node.replaceChildren === "function") {
      node.replaceChildren();
      return;
    }
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function textOf(value) {
    if (value == null) return "";
    return String(value).trim();
  }

  function toInt(value) {
    var number = Number(value);
    if (!isFinite(number)) return 0;
    return Math.max(0, Math.round(number));
  }

  function detailOf(body) {
    if (body == null) return "";
    if (typeof body === "string") return body.trim();
    if (typeof body !== "object") return "";
    var detail = body.detail;
    if (typeof detail === "string") return detail.trim();
    if (Array.isArray(detail) && detail.length) {
      var first = detail[0];
      if (first && typeof first === "object" && first.msg) return String(first.msg);
      if (first != null) return String(first);
    }
    if (detail && typeof detail === "object") {
      if (detail.message) return String(detail.message);
      if (detail.code) return String(detail.code);
    }
    if (body.message) return String(body.message);
    return "";
  }

  function describeError(error) {
    if (error == null) return t("next.nodes.unknown");
    if (typeof error === "string") return error || t("next.nodes.unknown");
    var fromBody = detailOf(error.body);
    if (fromBody) return fromBody;
    if (error.message) return String(error.message);
    return String(error);
  }

  /** The v1 node routes answer 503 "Remote execution is not configured ...". */
  function isNotConfigured(error) {
    var text = describeError(error).toLowerCase();
    if (text.indexOf("not configured") !== -1) return true;
    return Number(error && error.status) === 503 && text.indexOf("remote execution") !== -1;
  }

  function normalizeStatus(value) {
    var status = textOf(value).toLowerCase();
    if (status === "online" || status === "degraded" || status === "offline") return status;
    return "unknown";
  }

  function statusLabel(status) {
    return t("next.nodes.status." + (status || "unknown"));
  }

  function normalizeCapability(value) {
    var state = textOf(value).toLowerCase();
    if (state === "declared" || state === "probe-inferred") return state;
    return "unknown";
  }

  function capabilityLabel(state) {
    if (state === "declared") return t("next.nodes.capability.declared");
    if (state === "probe-inferred") return t("next.nodes.capability.probe_inferred");
    return t("next.nodes.capability.unknown");
  }

  function versionText(value) {
    var version = textOf(value);
    return version ? version : t("next.nodes.version_unknown");
  }

  /* ── payload normalization (contract-shaped, no guessing) ───────────── */

  function normalizeNode(raw) {
    raw = raw || {};
    var software = raw.software;
    if (!software || typeof software !== "object" || Array.isArray(software)) software = {};
    return {
      name: textOf(raw.name),
      host: textOf(raw.host),
      status: normalizeStatus(raw.status),
      runningJobs: toInt(raw.running_jobs),
      maxJobs: toInt(raw.max_jobs),
      diskPct: toInt(raw.disk_usage_pct),
      lastCheck: textOf(raw.last_check),
      error: textOf(raw.error) || null,
      queue: textOf(raw.queue) || null,
      software: software,
      capabilityState: normalizeCapability(raw.capability_state),
      declaredOk: raw.declared_ok === true ? true : (raw.declared_ok === false ? false : null),
      mismatch: Array.isArray(raw.mismatch) ? raw.mismatch.map(function (item) { return String(item); }) : [],
      probeNote: textOf(raw.probe_note) || null
    };
  }

  function normalizeNodes(payload) {
    var list = payload && Array.isArray(payload.nodes) ? payload.nodes : (Array.isArray(payload) ? payload : []);
    return list.map(normalizeNode);
  }

  function normalizeBackends(payload) {
    var list = payload && Array.isArray(payload.backends) ? payload.backends : (Array.isArray(payload) ? payload : []);
    return list.map(function (raw) {
      raw = raw || {};
      var caps = Array.isArray(raw.capabilities) ? raw.capabilities.map(function (cap) {
        cap = cap || {};
        return { name: textOf(cap.name), available: cap.available === true };
      }).filter(function (cap) { return !!cap.name; }) : [];
      return {
        name: textOf(raw.name),
        available: raw.available === true,
        path: textOf(raw.path),
        version: textOf(raw.version),
        capabilities: caps,
        remoteAvailable: raw.remote_available === true,
        remoteNodes: Array.isArray(raw.remote_nodes) ? raw.remote_nodes.map(function (node) { return String(node); }) : [],
        remotePath: textOf(raw.remote_path),
        remoteVersion: textOf(raw.remote_version)
      };
    }).filter(function (entry) { return !!entry.name; });
  }

  function normalizeSoftware(payload) {
    var list = payload && Array.isArray(payload.software) ? payload.software : (Array.isArray(payload) ? payload : []);
    return list.map(function (raw) {
      raw = raw || {};
      var candidates = Array.isArray(raw.candidates) ? raw.candidates.map(function (cand) {
        cand = cand || {};
        return {
          path: textOf(cand.path),
          version: textOf(cand.version),
          source: textOf(cand.source)
        };
      }) : [];
      return {
        name: textOf(raw.name),
        resolved: textOf(raw.resolved),
        version: textOf(raw.version),
        source: textOf(raw.source),
        multiple: raw.multiple === true || candidates.length > 1,
        candidates: candidates
      };
    }).filter(function (entry) { return !!entry.name; });
  }

  /* ── transport (injected only; never window.fetch) ──────────────────── */

  function readTextBody(response) {
    if (!response || typeof response.text !== "function") return Promise.resolve(null);
    try {
      return Promise.resolve(response.text()).then(function (text) {
        if (typeof text !== "string" || !text.trim()) return null;
        try { return JSON.parse(text); } catch (e) { return text; }
      }, function () { return null; });
    } catch (e) {
      return Promise.resolve(null);
    }
  }

  function readBody(response) {
    if (!response || typeof response.json !== "function") return readTextBody(response);
    try {
      return Promise.resolve(response.json()).then(function (value) {
        return value;
      }, function () {
        return readTextBody(response);
      });
    } catch (e) {
      return Promise.resolve(null);
    }
  }

  function httpError(response, body) {
    var status = Number(response && response.status);
    var message = detailOf(body);
    if (!message) message = "HTTP " + (isFinite(status) ? String(status) : "?");
    var error = new Error(message);
    error.status = isFinite(status) ? status : null;
    error.body = body;
    return error;
  }

  /** fetch Response -> parsed JSON body (non-2xx rejected with detail). */
  function parseFetchResult(response) {
    if (!response || typeof response !== "object") return response;
    if (typeof response.json !== "function" && typeof response.text !== "function") return response;
    return readBody(response).then(function (body) {
      if (response.ok === false) throw httpError(response, body);
      return body;
    });
  }

  function normalizeBase(value) {
    var base = textOf(value) || DEFAULT_API_BASE;
    if (base.length > 1 && base.charAt(base.length - 1) === "/") base = base.slice(0, -1);
    return base;
  }

  /**
   * Build the single request adapter. Precedence is explicit injection first
   * and the page transport last; with no adapter at all `call()` rejects
   * locally and the panel renders an error state instead of touching fetch.
   */
  function createTransport(deps) {
    deps = deps || {};
    var baseUrl = normalizeBase(deps.apiBase);
    var fetchImpl = typeof deps.fetch === "function" ? deps.fetch : null;
    var requestImpl = null;

    if (typeof deps.request === "function") {
      requestImpl = function (path, opts) { return deps.request(path, opts); };
    }
    if (!requestImpl && deps.client && deps.client.transport &&
        typeof deps.client.transport.request === "function") {
      var clientTransport = deps.client.transport;
      requestImpl = function (path, opts) { return clientTransport.request(path, opts); };
    }
    if (!requestImpl && typeof window !== "undefined" && window.ACPClient &&
        window.ACPClient.transport && typeof window.ACPClient.transport.request === "function") {
      var pageTransport = window.ACPClient.transport;
      requestImpl = function (path, opts) { return pageTransport.request(path, opts); };
    }

    function call(path, opts) {
      opts = opts || {};
      var method = opts.method || "GET";
      var url = baseUrl + path;
      if (fetchImpl) {
        var init = { method: method, headers: { Accept: "application/json" } };
        try {
          return Promise.resolve(fetchImpl(url, init)).then(parseFetchResult);
        } catch (error) {
          return Promise.reject(error);
        }
      }
      if (requestImpl) {
        var requestOpts = { method: method };
        // The page transport owns an 8 s default; a slow bootstrap command
        // must be allowed to run at least as long as the route's own 600 s.
        if (opts.timeoutMs != null) requestOpts.timeoutMs = opts.timeoutMs;
        try {
          return Promise.resolve(requestImpl(url, requestOpts));
        } catch (error) {
          return Promise.reject(error);
        }
      }
      return Promise.reject(new Error(t("next.nodes.no_adapter")));
    }

    return {
      baseUrl: baseUrl,
      hasAdapter: !!(fetchImpl || requestImpl),
      mode: fetchImpl ? "fetch" : (requestImpl ? "request" : "none"),
      call: call
    };
  }

  /* ── module instance ────────────────────────────────────────────────── */

  function createNodeStatus(deps) {
    deps = deps || {};
    var doc = deps.doc || (typeof document !== "undefined" ? document : null);
    var transport = createTransport(deps);
    // The bootstrap route defaults to timeout=600 (server-side pip install);
    // the client budget must not cut that short (page transport default = 8 s).
    var bootstrapTimeoutMs = (function () {
      var value = Number(deps.bootstrapTimeoutMs);
      if (isFinite(value) && value >= 1000 && value <= 1800000) return value;
      return 600000;
    })();

    var state = {
      generation: 0,
      loadToken: 0,
      mounted: false,
      disposed: false,
      suspended: false,
      loading: false,
      loaded: false,
      host: null,
      autoSelect: true,
      nodes: [],
      nodeError: null,
      nodeNotConfigured: false,
      backends: [],
      backendError: null,
      software: [],
      softwareError: null,
      criticalError: null,
      actions: Object.create(null),
      timer: null,
      ui: {}
    };

    var cleanup = [];

    function current(generation) {
      return !state.disposed && state.mounted && state.generation === generation;
    }

    /** Listener whose removal is part of dispose (mount-scoped nodes only). */
    function on(node, type, handler) {
      if (!node || typeof node.addEventListener !== "function") return;
      node.addEventListener(type, handler);
      cleanup.push(function () {
        if (typeof node.removeEventListener === "function") node.removeEventListener(type, handler);
      });
    }

    /** Row-scoped listener: the row is rebuilt on the next full render. */
    function listen(node, type, handler) {
      if (!node || typeof node.addEventListener !== "function") return;
      node.addEventListener(type, handler);
    }

    /* ---------------------------- busy ticker ------------------------- */

    function elapsedSeconds(action) {
      if (!action || action.startedAt == null) return 0;
      var elapsed = Date.now() - action.startedAt;
      if (!isFinite(elapsed) || elapsed < 0) return 0;
      return Math.floor(elapsed / 1000);
    }

    function anyBootstrapBusy() {
      var names = Object.keys(state.actions);
      for (var i = 0; i < names.length; i += 1) {
        var action = state.actions[names[i]];
        if (action && action.busy && action.kind === "bootstrap") return true;
      }
      return false;
    }

    function stopTicker() {
      if (state.timer == null) return;
      clearInterval(state.timer);
      state.timer = null;
    }

    function onTick() {
      if (state.disposed || !state.mounted) {
        stopTicker();
        return;
      }
      var names = Object.keys(state.actions);
      names.forEach(function (name) {
        var action = state.actions[name];
        if (action && action.busy && action.kind === "bootstrap") renderNodeAction(name);
      });
      if (!anyBootstrapBusy()) stopTicker();
    }

    /** Own the single elapsed-time interval; cleared by dispose/suspend/idle. */
    function syncTicker() {
      var needed = anyBootstrapBusy() && !state.suspended && !state.disposed && state.mounted;
      if (needed && state.timer == null) {
        state.timer = setInterval(onTick, 1000);
      } else if (!needed) {
        stopTicker();
      }
    }

    /* ------------------------------- DOM ----------------------------- */

    function metaItem(labelKey, labelText, valueText, mono) {
      var item = el(doc, "span", "acp-nodes-meta-item");
      var label = el(doc, "span", "acp-nodes-meta-label", labelText);
      label.setAttribute("data-i18n", labelKey);
      item.appendChild(label);
      item.appendChild(el(doc, "span", "acp-nodes-meta-value" + (mono ? " acp-nodes-mono" : ""), valueText));
      return item;
    }

    function buildSection(titleKey, titleText) {
      var section = el(doc, "section", "acp-nodes-section");
      var head = el(doc, "div", "acp-nodes-section-head");
      var title = el(doc, "h4", "acp-nodes-section-title", titleText);
      title.setAttribute("data-i18n", titleKey);
      head.appendChild(title);
      section.appendChild(head);
      var note = el(doc, "p", "acp-nodes-note");
      note.hidden = true;
      note.setAttribute("role", "status");
      section.appendChild(note);
      var list = el(doc, "div", "acp-nodes-list");
      section.appendChild(list);
      return { section: section, note: note, list: list };
    }

    function buildDom() {
      var root = el(doc, "div", "acp-nodes");
      root.setAttribute("data-acp-module-state", "empty");
      root.setAttribute("data-module", MODULE_ID);

      var header = el(doc, "header", "acp-nodes-head");
      var titleRow = el(doc, "div", "acp-nodes-title-row");
      var title = el(doc, "h3", "acp-nodes-title", t("next.nodes.title"));
      title.setAttribute("data-i18n", "next.nodes.title");
      titleRow.appendChild(title);
      var refreshBtn = el(doc, "button", "acp-nodes-btn acp-nodes-refresh", t("next.nodes.refresh"));
      refreshBtn.type = "button";
      refreshBtn.setAttribute("data-action", "refresh");
      titleRow.appendChild(refreshBtn);
      header.appendChild(titleRow);
      var sub = el(doc, "p", "acp-nodes-sub", t("next.nodes.subtitle"));
      sub.setAttribute("data-i18n", "next.nodes.subtitle");
      header.appendChild(sub);
      root.appendChild(header);

      var stateLine = el(doc, "p", "acp-nodes-state");
      stateLine.hidden = true;
      stateLine.setAttribute("role", "status");
      stateLine.setAttribute("aria-live", "polite");
      var stateText = el(doc, "span", "acp-nodes-state-text");
      stateLine.appendChild(stateText);
      var retryBtn = el(doc, "button", "acp-nodes-btn acp-nodes-retry", t("next.nodes.retry"));
      retryBtn.type = "button";
      retryBtn.setAttribute("data-action", "retry");
      retryBtn.hidden = true;
      stateLine.appendChild(retryBtn);
      root.appendChild(stateLine);

      var nodesSection = buildSection("next.nodes.section.nodes", t("next.nodes.section.nodes"));
      var backendsSection = buildSection("next.nodes.section.backends", t("next.nodes.section.backends"));
      var softwareSection = buildSection("next.nodes.section.software", t("next.nodes.section.software"));
      root.appendChild(nodesSection.section);
      root.appendChild(backendsSection.section);
      root.appendChild(softwareSection.section);

      state.host.replaceChildren(root);
      state.ui = {
        root: root,
        title: title,
        sub: sub,
        refreshBtn: refreshBtn,
        stateLine: stateLine,
        stateText: stateText,
        retryBtn: retryBtn,
        nodesSection: nodesSection.section,
        nodesNote: nodesSection.note,
        nodesList: nodesSection.list,
        backendsSection: backendsSection.section,
        backendsNote: backendsSection.note,
        backendsList: backendsSection.list,
        softwareSection: softwareSection.section,
        softwareNote: softwareSection.note,
        softwareList: softwareSection.list,
        nodeRowEls: Object.create(null)
      };

      on(refreshBtn, "click", function (event) {
        if (event && event.preventDefault) event.preventDefault();
        loadAll();
      });
      on(retryBtn, "click", function (event) {
        if (event && event.preventDefault) event.preventDefault();
        loadAll();
      });
    }

    /* ----------------------------- render ---------------------------- */

    function setNote(note, text, tone) {
      if (!note) return;
      if (!text) {
        note.hidden = true;
        note.textContent = "";
        note.removeAttribute("data-tone");
        return;
      }
      note.hidden = false;
      note.textContent = text;
      note.setAttribute("data-tone", tone || "muted");
    }

    function moduleState() {
      var hasData = state.nodes.length > 0 || state.backends.length > 0 || state.software.length > 0;
      if (state.criticalError && !hasData) return "error";
      if (state.loading && !state.loaded) return "loading";
      if (!hasData) return "empty";
      return "ready";
    }

    function renderHeader() {
      var ui = state.ui;
      if (!ui.sub) return;
      ui.sub.textContent = t("next.nodes.subtitle");
      if (ui.refreshBtn) {
        ui.refreshBtn.disabled = state.loading;
        ui.refreshBtn.textContent = state.loading ? t("next.nodes.refreshing") : t("next.nodes.refresh");
      }
    }

    function renderStateLine(name) {
      var ui = state.ui;
      if (!ui.stateLine) return;
      var text = "";
      var showRetry = false;
      if (name === "loading") {
        text = t("next.nodes.loading");
      } else if (name === "empty") {
        text = [t("next.nodes.empty"), state.nodeNotConfigured
          ? t("next.nodes.not_configured_hint")
          : t("next.nodes.empty_hint")].join(" ");
      } else if (name === "error") {
        if (!transport.hasAdapter) {
          text = t("next.nodes.no_adapter");
        } else {
          text = t("next.nodes.error", { message: describeError(state.criticalError) });
          showRetry = true;
        }
      }
      ui.stateLine.hidden = !text;
      ui.stateText.textContent = text;
      ui.retryBtn.hidden = !showRetry;
    }

    function renderNodeAction(name) {
      var refs = state.ui.nodeRowEls ? state.ui.nodeRowEls[name] : null;
      if (!refs) return;
      var action = state.actions[name] || null;
      var busy = !!(action && action.busy);
      refs.row.setAttribute("data-node-busy", busy ? action.kind : "");
      refs.row.setAttribute("data-node-result", !action || busy ? "" : action.kind + "-" +
        (action.tone === "ok" ? "ok" : "error"));
      refs.pingBtn.disabled = busy;
      refs.bootstrapBtn.disabled = busy;
      refs.pingBtn.textContent = busy && action.kind === "ping"
        ? t("next.nodes.pinging") : t("next.nodes.ping");
      refs.bootstrapBtn.textContent = busy && action.kind === "bootstrap"
        ? t("next.nodes.bootstrapping", { seconds: elapsedSeconds(action) })
        : t("next.nodes.bootstrap");
      if (!action) {
        refs.statusEl.hidden = true;
        refs.statusEl.textContent = "";
        refs.statusEl.className = "acp-nodes-node-action";
        refs.details.hidden = true;
        return;
      }
      refs.statusEl.hidden = false;
      refs.statusEl.textContent = action.text;
      refs.statusEl.className = "acp-nodes-node-action is-" + action.tone;
      var tail = action.stderr || action.stdout || "";
      var showTail = !busy && action.kind === "bootstrap" && !!tail;
      refs.details.hidden = !showTail;
      if (showTail) {
        refs.summary.textContent = action.stderr ? t("next.nodes.stderr_tail") : t("next.nodes.stdout_tail");
        refs.pre.textContent = tail;
      }
    }

    function buildNodeRow(node) {
      var row = el(doc, "div", "acp-nodes-row acp-nodes-node");
      row.setAttribute("data-node", node.name);
      row.setAttribute("data-node-status", node.status);
      row.setAttribute("data-node-busy", "");
      row.setAttribute("data-node-result", "");

      var dot = el(doc, "span", "acp-nodes-dot is-" + node.status);
      dot.setAttribute("aria-hidden", "true");
      row.appendChild(dot);

      var main = el(doc, "div", "acp-nodes-main");
      var head = el(doc, "div", "acp-nodes-row-head");
      head.appendChild(el(doc, "strong", "acp-nodes-name", node.name || t("next.nodes.unnamed")));
      if (node.host) head.appendChild(el(doc, "span", "acp-nodes-host", node.host));
      head.appendChild(el(doc, "span", "acp-nodes-badge is-" + node.status, statusLabel(node.status)));
      main.appendChild(head);

      var meta = el(doc, "div", "acp-nodes-meta");
      meta.appendChild(metaItem("next.nodes.meta_jobs", t("next.nodes.meta_jobs"),
        String(node.runningJobs) + "/" + String(node.maxJobs)));
      meta.appendChild(metaItem("next.nodes.meta_disk", t("next.nodes.meta_disk"),
        String(node.diskPct) + "%"));
      if (node.queue) meta.appendChild(metaItem("next.nodes.meta_queue", t("next.nodes.meta_queue"), node.queue));
      if (node.lastCheck) {
        meta.appendChild(metaItem("next.nodes.meta_checked", t("next.nodes.meta_checked"), node.lastCheck, true));
      }
      meta.appendChild(metaItem("next.nodes.meta_capability", t("next.nodes.meta_capability"),
        capabilityLabel(node.capabilityState)));
      main.appendChild(meta);

      if (node.declaredOk === false) {
        var mismatch = el(doc, "p", "acp-nodes-warn", t("next.nodes.declared_mismatch"));
        if (node.mismatch.length) mismatch.setAttribute("title", node.mismatch.join(", "));
        main.appendChild(mismatch);
      } else if (node.declaredOk === true) {
        main.appendChild(el(doc, "p", "acp-nodes-ok", t("next.nodes.declared_ok")));
      }
      if (node.mismatch.length) {
        var mismatchDetails = el(doc, "details", "acp-nodes-details");
        mismatchDetails.appendChild(el(doc, "summary", "", t("next.nodes.mismatch_list")));
        mismatchDetails.appendChild(el(doc, "pre", "acp-nodes-pre", node.mismatch.join("\n")));
        main.appendChild(mismatchDetails);
      }

      var softwareNames = Object.keys(node.software || {});
      if (softwareNames.length) {
        var softwareDetails = el(doc, "details", "acp-nodes-details");
        softwareDetails.setAttribute("data-node-software", String(softwareNames.length));
        softwareDetails.appendChild(el(doc, "summary", "",
          t("next.nodes.node_software", { count: softwareNames.length })));
        var softwareBody = el(doc, "div", "acp-nodes-sub-list");
        softwareNames.forEach(function (name) {
          var info = node.software[name] && typeof node.software[name] === "object" ? node.software[name] : {};
          var line = el(doc, "div", "acp-nodes-sub-row");
          line.setAttribute("data-software-name", name);
          line.appendChild(el(doc, "span", "acp-nodes-name", name));
          line.appendChild(el(doc, "span", "acp-nodes-mono", textOf(info.resolved) || t("next.nodes.path_missing")));
          line.appendChild(el(doc, "span", "acp-nodes-muted", versionText(info.version)));
          softwareBody.appendChild(line);
        });
        softwareDetails.appendChild(softwareBody);
        main.appendChild(softwareDetails);
      }

      if (node.error) {
        var errorLine = el(doc, "p", "acp-nodes-node-error", t("next.nodes.node_error", { message: node.error }));
        errorLine.setAttribute("role", "status");
        main.appendChild(errorLine);
      }
      if (node.probeNote) {
        main.appendChild(el(doc, "p", "acp-nodes-muted", t("next.nodes.probe_note", { note: node.probeNote })));
      }

      var statusEl = el(doc, "p", "acp-nodes-node-action");
      statusEl.hidden = true;
      statusEl.setAttribute("role", "status");
      statusEl.setAttribute("aria-live", "polite");
      main.appendChild(statusEl);

      var details = el(doc, "details", "acp-nodes-details acp-nodes-action-details");
      details.hidden = true;
      var summary = el(doc, "summary", "", t("next.nodes.stderr_tail"));
      var pre = el(doc, "pre", "acp-nodes-pre");
      details.appendChild(summary);
      details.appendChild(pre);
      main.appendChild(details);
      row.appendChild(main);

      var pingBtn = el(doc, "button", "acp-nodes-btn", t("next.nodes.ping"));
      pingBtn.type = "button";
      pingBtn.setAttribute("data-action", "ping");
      pingBtn.setAttribute("data-node", node.name);
      var bootstrapBtn = el(doc, "button", "acp-nodes-btn", t("next.nodes.bootstrap"));
      bootstrapBtn.type = "button";
      bootstrapBtn.setAttribute("data-action", "bootstrap");
      bootstrapBtn.setAttribute("data-node", node.name);
      if (node.name) {
        listen(pingBtn, "click", function (event) {
          if (event && event.preventDefault) event.preventDefault();
          runAction(node.name, "ping");
        });
        listen(bootstrapBtn, "click", function (event) {
          if (event && event.preventDefault) event.preventDefault();
          runAction(node.name, "bootstrap");
        });
      } else {
        // A nameless node cannot be addressed by the command routes: render
        // the fact, never a button that would 404.
        pingBtn.disabled = true;
        bootstrapBtn.disabled = true;
      }
      var actions = el(doc, "div", "acp-nodes-actions");
      actions.appendChild(pingBtn);
      actions.appendChild(bootstrapBtn);
      row.appendChild(actions);

      if (node.name) {
        state.ui.nodeRowEls[node.name] = {
          row: row,
          statusEl: statusEl,
          details: details,
          summary: summary,
          pre: pre,
          pingBtn: pingBtn,
          bootstrapBtn: bootstrapBtn
        };
      }
      return row;
    }

    function renderNodes() {
      var ui = state.ui;
      if (!ui.nodesList) return;
      clearChildren(ui.nodesList);
      ui.nodeRowEls = Object.create(null);
      state.nodes.forEach(function (node) {
        ui.nodesList.appendChild(buildNodeRow(node));
      });
      state.nodes.forEach(function (node) {
        if (node.name) renderNodeAction(node.name);
      });
      var text = "";
      var tone = "muted";
      if (state.nodeError) {
        text = t("next.nodes.section_error", {
          section: t("next.nodes.section.nodes"),
          message: describeError(state.nodeError)
        });
        tone = "bad";
      } else if (state.nodeNotConfigured) {
        text = t("next.nodes.not_configured") + " " + t("next.nodes.not_configured_hint");
      } else if (!state.nodes.length) {
        text = t("next.nodes.nodes_empty");
      } else {
        text = state.autoSelect ? t("next.nodes.auto_select_on") : t("next.nodes.auto_select_off");
      }
      setNote(ui.nodesNote, text, tone);
    }

    function buildSoftwareRow(entry) {
      var row = el(doc, "div", "acp-nodes-row acp-nodes-software");
      row.setAttribute("data-software", entry.name);
      row.setAttribute("data-acp-availability", entry.resolved ? "resolved" : "unknown");
      var main = el(doc, "div", "acp-nodes-main");
      var head = el(doc, "div", "acp-nodes-row-head");
      head.appendChild(el(doc, "strong", "acp-nodes-name", entry.name));
      head.appendChild(el(doc, "span", "acp-nodes-badge is-" + (entry.resolved ? "online" : "unknown"),
        entry.resolved ? t("next.nodes.found") : t("next.nodes.not_found")));
      main.appendChild(head);

      var meta = el(doc, "div", "acp-nodes-meta");
      meta.appendChild(metaItem("next.nodes.version", t("next.nodes.version"), versionText(entry.version)));
      meta.appendChild(metaItem("next.nodes.path", t("next.nodes.path"),
        entry.resolved || t("next.nodes.path_missing"), true));
      meta.appendChild(metaItem("next.nodes.source", t("next.nodes.source"),
        entry.source || t("next.nodes.source_unknown")));
      main.appendChild(meta);

      if (entry.multiple && entry.candidates.length) {
        var details = el(doc, "details", "acp-nodes-details");
        details.setAttribute("data-software-candidates", String(entry.candidates.length));
        details.appendChild(el(doc, "summary", "",
          t("next.nodes.candidates", { count: entry.candidates.length })));
        var body = el(doc, "div", "acp-nodes-sub-list");
        entry.candidates.forEach(function (candidate) {
          var active = !!entry.resolved && candidate.path === entry.resolved;
          var line = el(doc, "div", "acp-nodes-sub-row");
          line.setAttribute("data-candidate-active", active ? "true" : "false");
          line.appendChild(el(doc, "span", "acp-nodes-mono", candidate.path || t("next.nodes.path_missing")));
          line.appendChild(el(doc, "span", "acp-nodes-muted", versionText(candidate.version)));
          if (candidate.source) {
            line.appendChild(el(doc, "span", "acp-nodes-muted",
              t("next.nodes.source") + " " + candidate.source));
          }
          if (active) line.appendChild(el(doc, "span", "acp-nodes-badge is-online", t("next.nodes.candidate_active")));
          body.appendChild(line);
        });
        details.appendChild(body);
        main.appendChild(details);
      }
      row.appendChild(main);
      return row;
    }

    function renderSoftware() {
      var ui = state.ui;
      if (!ui.softwareList) return;
      clearChildren(ui.softwareList);
      state.software.forEach(function (entry) {
        ui.softwareList.appendChild(buildSoftwareRow(entry));
      });
      var text = "";
      var tone = "muted";
      if (state.softwareError) {
        text = t("next.nodes.section_error", {
          section: t("next.nodes.section.software"),
          message: describeError(state.softwareError)
        });
        tone = "bad";
      } else if (!state.software.length) {
        text = t("next.nodes.software_empty");
      } else {
        var found = state.software.filter(function (entry) { return !!entry.resolved; }).length;
        text = t("next.nodes.software_summary", { found: found, total: state.software.length });
      }
      setNote(ui.softwareNote, text, tone);
    }

    function buildBackendRow(entry) {
      var row = el(doc, "div", "acp-nodes-row acp-nodes-backend");
      row.setAttribute("data-backend", entry.name);
      row.setAttribute("data-acp-availability", entry.available ? "available" : "unavailable");
      var main = el(doc, "div", "acp-nodes-main");
      var head = el(doc, "div", "acp-nodes-row-head");
      head.appendChild(el(doc, "strong", "acp-nodes-name", entry.name));
      head.appendChild(el(doc, "span", "acp-nodes-badge is-" + (entry.available ? "online" : "offline"),
        entry.available ? t("next.nodes.backend.available") : t("next.nodes.backend.unavailable")));
      if (entry.remoteAvailable) {
        head.appendChild(el(doc, "span", "acp-nodes-badge is-remote",
          t("next.nodes.backend.remote", { nodes: entry.remoteNodes.join(", ") })));
      }
      main.appendChild(head);

      var meta = el(doc, "div", "acp-nodes-meta");
      meta.appendChild(metaItem("next.nodes.version", t("next.nodes.version"), versionText(entry.version)));
      meta.appendChild(metaItem("next.nodes.path", t("next.nodes.path"),
        entry.path || t("next.nodes.path_missing"), true));
      main.appendChild(meta);
      if (entry.remoteAvailable && (entry.remotePath || entry.remoteVersion)) {
        main.appendChild(el(doc, "p", "acp-nodes-muted", t("next.nodes.backend.remote_detail", {
          path: entry.remotePath || t("next.nodes.path_missing"),
          version: versionText(entry.remoteVersion)
        })));
      }

      if (entry.capabilities.length) {
        var caps = el(doc, "div", "acp-nodes-caps");
        caps.setAttribute("data-capabilities", String(entry.capabilities.length));
        caps.appendChild(el(doc, "span", "acp-nodes-meta-label", t("next.nodes.backend.capabilities")));
        entry.capabilities.forEach(function (cap) {
          var chip = el(doc, "span", "acp-nodes-chip " + (cap.available ? "is-on" : "is-off"), cap.name);
          chip.setAttribute("data-capability", cap.name);
          chip.setAttribute("data-available", cap.available ? "true" : "false");
          chip.appendChild(el(doc, "span", "acp-nodes-chip-state",
            cap.available ? t("next.nodes.backend.cap_yes") : t("next.nodes.backend.cap_no")));
          caps.appendChild(chip);
        });
        main.appendChild(caps);
      }
      row.appendChild(main);
      return row;
    }

    function renderBackends() {
      var ui = state.ui;
      if (!ui.backendsList) return;
      clearChildren(ui.backendsList);
      state.backends.forEach(function (entry) {
        ui.backendsList.appendChild(buildBackendRow(entry));
      });
      var text = "";
      var tone = "muted";
      if (state.backendError) {
        text = t("next.nodes.section_error", {
          section: t("next.nodes.section.backends"),
          message: describeError(state.backendError)
        });
        tone = "bad";
      } else if (!state.backends.length) {
        text = t("next.nodes.backends_empty");
      } else {
        var available = state.backends.filter(function (entry) { return entry.available; }).length;
        text = t("next.nodes.backend_summary", { available: available, total: state.backends.length });
      }
      setNote(ui.backendsNote, text, tone);
    }

    function render() {
      if (state.disposed || !state.mounted || !state.ui.root) return;
      var name = moduleState();
      state.ui.root.setAttribute("data-acp-module-state", name);
      state.ui.root.setAttribute("data-nodes-count", String(state.nodes.length));
      state.ui.root.setAttribute("data-backends-count", String(state.backends.length));
      state.ui.root.setAttribute("data-software-count", String(state.software.length));
      renderHeader();
      renderStateLine(name);
      renderNodes();
      renderBackends();
      renderSoftware();
    }

    /* ------------------------------ loading -------------------------- */

    function resultOf(request) {
      return request.then(function (payload) {
        return { ok: true, data: payload };
      }, function (error) {
        return { ok: false, error: error };
      });
    }

    function applyNodes(result) {
      state.nodes = [];
      state.nodeError = null;
      state.nodeNotConfigured = false;
      state.autoSelect = true;
      if (result.ok) {
        state.nodes = normalizeNodes(result.data);
        state.autoSelect = !(result.data && result.data.auto_select === false);
        return;
      }
      if (isNotConfigured(result.error)) {
        state.nodeNotConfigured = true;
        return;
      }
      state.nodeError = result.error;
    }

    function applyBackends(result) {
      state.backends = [];
      state.backendError = null;
      if (result.ok) {
        state.backends = normalizeBackends(result.data);
        return;
      }
      state.backendError = result.error;
    }

    function applySoftware(result) {
      state.software = [];
      state.softwareError = null;
      if (result.ok) {
        state.software = normalizeSoftware(result.data);
        return;
      }
      state.softwareError = result.error;
    }

    /**
     * Reload all three read endpoints. The newest call owns the result: an
     * older refresh that resolves late is dropped by the load token.
     */
    function loadAll() {
      if (state.disposed || !state.mounted) return Promise.resolve();
      var generation = state.generation;
      state.loadToken += 1;
      var token = state.loadToken;

      if (!transport.hasAdapter) {
        state.loading = false;
        state.loaded = true;
        state.nodes = [];
        state.backends = [];
        state.software = [];
        state.nodeError = null;
        state.nodeNotConfigured = false;
        state.backendError = null;
        state.softwareError = null;
        state.criticalError = new Error(t("next.nodes.no_adapter"));
        render();
        return Promise.resolve();
      }

      state.loading = true;
      state.criticalError = null;
      render();

      var nodesRequest = resultOf(transport.call("/nodes", { method: "GET" }));
      var backendsRequest = resultOf(transport.call("/backends", { method: "GET" }));
      var softwareRequest = resultOf(transport.call("/software/discovery", { method: "GET" }));

      return Promise.all([nodesRequest, backendsRequest, softwareRequest]).then(function (results) {
        if (!current(generation) || token !== state.loadToken) return;
        state.loaded = true;
        state.loading = false;
        applyNodes(results[0]);
        applyBackends(results[1]);
        applySoftware(results[2]);
        state.criticalError = state.nodeError || state.backendError || state.softwareError || null;
        render();
        syncTicker();
      });
    }

    /* ------------------------------ actions -------------------------- */

    function findNode(name) {
      for (var i = 0; i < state.nodes.length; i += 1) {
        if (state.nodes[i].name === name) return state.nodes[i];
      }
      return null;
    }

    function replaceNode(next) {
      for (var i = 0; i < state.nodes.length; i += 1) {
        if (state.nodes[i].name === next.name) {
          state.nodes[i] = next;
          return;
        }
      }
      state.nodes.push(next);
    }

    function setAction(name, patch) {
      var current = state.actions[name] || { kind: "ping", busy: false, tone: "info", text: "", stderr: "", stdout: "", startedAt: null, token: 0 };
      state.actions[name] = {
        kind: patch.kind != null ? patch.kind : current.kind,
        busy: patch.busy != null ? patch.busy : current.busy,
        tone: patch.tone != null ? patch.tone : current.tone,
        text: patch.text != null ? patch.text : current.text,
        stderr: patch.stderr != null ? patch.stderr : current.stderr,
        stdout: patch.stdout != null ? patch.stdout : current.stdout,
        startedAt: patch.startedAt != null ? patch.startedAt : current.startedAt,
        token: patch.token != null ? patch.token : current.token
      };
      return state.actions[name];
    }

    function actionStillCurrent(name, token) {
      var action = state.actions[name];
      return !!action && action.token === token;
    }

    /** Best-effort single-node status refresh (GET .../status). */
    function refreshNodeStatus(name, token) {
      var generation = state.generation;
      return transport.call("/nodes/" + encodeURIComponent(name) + "/status", { method: "GET" }).then(function (payload) {
        if (!current(generation) || !actionStillCurrent(name, token)) return;
        var node = normalizeNode(payload);
        if (!node.name) node.name = name;
        replaceNode(node);
        render();
      }, function (error) {
        if (!current(generation) || !actionStillCurrent(name, token)) return;
        var action = state.actions[name];
        if (!action || action.busy) return;
        action.text = action.text + " · " +
          t("next.nodes.status_refresh_failed", { message: describeError(error) });
        renderNodeAction(name);
      });
    }

    function applyPingResult(name, payload, token) {
      var reachable = payload && payload.reachable === true;
      var action = state.actions[name];
      if (!action || action.token !== token) return;
      action.busy = false;
      action.tone = reachable ? "ok" : "bad";
      if (reachable) {
        action.text = t("next.nodes.ping_ok") +
          (payload && payload.status ? " · " + statusLabel(normalizeStatus(payload.status)) : "");
      } else {
        action.text = t("next.nodes.ping_unreachable", {
          message: payload && payload.error ? String(payload.error) : t("next.nodes.unknown")
        });
      }
      var node = findNode(name);
      if (node) {
        if (payload && payload.status) node.status = normalizeStatus(payload.status);
        if (payload && payload.error) node.error = String(payload.error);
        else if (reachable) node.error = null;
      }
      syncTicker();
      renderNodes();
      refreshNodeStatus(name, token);
    }

    function applyBootstrapResult(name, payload, token) {
      var action = state.actions[name];
      if (!action || action.token !== token) return;
      payload = payload || {};
      action.busy = false;
      action.stderr = textOf(payload.stderr_tail);
      action.stdout = textOf(payload.stdout_tail);
      if (payload.reachable === false) {
        action.tone = "bad";
        action.text = t("next.nodes.bootstrap_unreachable");
      } else if (payload.ok === true) {
        action.tone = "ok";
        action.text = t("next.nodes.bootstrap_ok", {
          code: payload.exit_code == null ? "0" : String(payload.exit_code)
        });
      } else if (payload.error) {
        action.tone = "bad";
        action.text = t("next.nodes.bootstrap_failed", { message: String(payload.error) });
      } else {
        action.tone = "bad";
        action.text = t("next.nodes.bootstrap_exit", {
          code: payload.exit_code == null ? "?" : String(payload.exit_code)
        });
      }
      syncTicker();
      renderNodes();
      if (payload.reachable !== false) refreshNodeStatus(name, token);
    }

    function runAction(name, kind) {
      if (state.disposed || !state.mounted) return;
      if (kind !== "ping" && kind !== "bootstrap") return;
      if (!findNode(name)) return;
      var existing = state.actions[name];
      if (existing && existing.busy) return; // one command per node at a time
      if (!transport.hasAdapter) {
        setAction(name, { kind: kind, busy: false, tone: "bad", text: t("next.nodes.no_adapter"), token: (existing ? existing.token : 0) + 1 });
        renderNodeAction(name);
        return;
      }
      var generation = state.generation;
      var token = (existing ? existing.token : 0) + 1;
      setAction(name, {
        kind: kind,
        busy: true,
        tone: "info",
        text: kind === "ping" ? t("next.nodes.pinging") : t("next.nodes.bootstrapping", { seconds: 0 }),
        stderr: "",
        stdout: "",
        startedAt: Date.now(),
        token: token
      });
      renderNodeAction(name);
      syncTicker();

      var path = "/nodes/" + encodeURIComponent(name) + (kind === "ping" ? "/ping" : "/bootstrap");
      var opts = { method: "POST" };
      if (kind === "bootstrap") opts.timeoutMs = bootstrapTimeoutMs;
      transport.call(path, opts).then(function (payload) {
        if (!current(generation) || !actionStillCurrent(name, token)) return;
        if (kind === "ping") applyPingResult(name, payload, token);
        else applyBootstrapResult(name, payload, token);
      }, function (error) {
        if (!current(generation) || !actionStillCurrent(name, token)) return;
        var action = state.actions[name];
        if (!action) return;
        action.busy = false;
        action.tone = "bad";
        action.text = kind === "ping"
          ? t("next.nodes.ping_error", { message: describeError(error) })
          : t("next.nodes.bootstrap_failed", { message: describeError(error) });
        syncTicker();
        renderNodeAction(name);
      });
    }

    /* ---------------------------- lifecycle ------------------------- */

    function mount(hostEl, api) {
      api = api || {};
      hostEl = hostEl || (api.panel && api.panel.bodyEl);
      if (!hostEl || typeof hostEl.appendChild !== "function") {
        return Promise.reject(new TypeError("node-status mount: host 必须是 DOM 元素"));
      }
      if (!doc || typeof doc.createElement !== "function") {
        return Promise.reject(new TypeError("node-status: document 不可用"));
      }
      if (state.mounted) {
        return Promise.reject(new Error("node-status: already mounted (dispose() first)"));
      }
      state.host = hostEl;
      state.generation += 1;
      state.mounted = true;
      state.disposed = false;
      state.suspended = false;
      state.loaded = false;
      state.loading = false;
      state.actions = Object.create(null);
      buildDom();
      if (doc && typeof doc.addEventListener === "function") {
        var langHandler = function () {
          if (state.disposed) return;
          render();
        };
        doc.addEventListener("acp:langchange", langHandler);
        cleanup.push(function () { doc.removeEventListener("acp:langchange", langHandler); });
      }
      render();
      loadAll();
      return Promise.resolve();
    }

    /**
     * The panel is project/task independent, so context changes do not
     * trigger a reload; the method exists for ModuleInstance symmetry.
     */
    function updateContext() {
      return undefined;
    }

    function suspend() {
      state.suspended = true;
      stopTicker();
    }

    function resume() {
      state.suspended = false;
      syncTicker();
    }

    function dispose() {
      if (state.disposed) return;
      state.disposed = true;
      state.mounted = false;
      state.generation += 1;
      state.loadToken += 1;
      stopTicker();
      cleanup.splice(0).forEach(function (fn) {
        try { fn(); } catch (error) { /* listener already gone */ }
      });
      if (state.host && typeof state.host.replaceChildren === "function") {
        try { state.host.replaceChildren(); } catch (error) { /* host gone */ }
      }
      state.host = null;
      state.ui = {};
      state.nodes = [];
      state.backends = [];
      state.software = [];
      state.actions = Object.create(null);
      state.nodeError = null;
      state.nodeNotConfigured = false;
      state.backendError = null;
      state.softwareError = null;
      state.criticalError = null;
    }

    return {
      mount: mount,
      updateContext: updateContext,
      refresh: loadAll,
      suspend: suspend,
      resume: resume,
      dispose: dispose
    };
  }

  window.ACPNextModules[MODULE_ID] = {
    descriptor: DESCRIPTOR,
    factory: createNodeStatus
  };
})();

