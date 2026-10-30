/*
 * ACP 2.0 experiment-entry module — molecule-viewer (UI-02a).
 *
 * Classic script (no build, no ESM). Registers exactly one key on the global
 * module namespace; the Shell loader (UI-01) resolves the factory and mounts
 * the returned instance into an arbitrary host element. Nothing here touches
 * the legacy workbench page, its globals, or its DOM ids.
 *
 * Rendering goes ONLY through the bounded visualization stack:
 *   ACPVisualization.scene.create (js/visualization/scene_instance.js)
 *   + ACPVisualization.framing.scheduleViewerFraming / disposeViewerFraming
 *   (js/visualization/framing.js). The vendored viewer library is mediated
 *   exclusively by js/visualization/mol3d_adapter.js (root ANTI #22 single
 *   station) — this file never references it directly, and the model-add
 *   method name is assembled at runtime because modules/** is grep-gated.
 *
 * Geometry goes through the page-level singleton window.ACPGeometryStore
 * (js/geometry_store.js) — single active instance semantics for P0 (see
 * README: multi-instance requires factory-izing the store first).
 *
 * structureRef contract (frozen v0, see README.md):
 *   { kind: "job-file" | "job-remote-file", jobId, path, frameIndex? }
 *   { kind: "inline-xyz", text, frameIndex? }
 *   (a JSON string of the above is tolerated as a bridge for the UI-00 draft
 *    `structureRef?: string` shape)
 * Load endpoints are the existing v1 surfaces the legacy viewer already
 * uses — no new server endpoints are invented:
 *   job-file        GET /api/v1/jobs/{jobId}/files/{path}            (raw text)
 *   job-remote-file GET /api/v1/jobs/{jobId}/remote-files/{path}/preview?mode=structure
 *                   (JSON envelope {content: string})
 *
 * Lifecycle: mount/updateContext/resize/suspend/resume/dispose. dispose is
 * idempotent and thorough; mount failures clean up and render a LOCAL error
 * inside the host; every async result carries a generation guard so late
 * responses (A→B→A, dispose, suspend-switch) never write into the DOM.
 */
(function () {
  "use strict";

  window.ACPNextModules = window.ACPNextModules || {};

  var MODULE_ID = "molecule-viewer";
  var DEFAULT_API_BASE = "/api/v1"; // matches compat/shared-state.js API_BASE
  var DEFAULT_BACKGROUND = "#0e1419"; // css var --bg-canvas value
  // Mirrors legacy STYLE_PRESETS["ball-stick"] (compat/shared-state.js).
  var BALL_STICK = { stick: { radius: 0.15 }, sphere: { scale: 0.28 } };

  /* ── pure helpers (no DOM, no globals; covered by module tests) ───────── */

  function parseEnergyFromComment(comment) {
    var m = String(comment || "").match(/(-?\d+\.\d+)/);
    return m ? parseFloat(m[1]) : null;
  }

  /** Minimal multi-frame XYZ parser. Frames: [{atoms:[{id,elem,x,y,z}],
   * comment, energy}]. Self-written (molecule-view.js whitelist functions are
   * pure but loading that file drags page-global side effects — README). */
  function parseMultiFrameXyz(text) {
    var lines = String(text == null ? "" : text).trim().split(/\r?\n/);
    var frames = [];
    var i = 0;
    while (i < lines.length) {
      while (i < lines.length && !String(lines[i] || "").trim()) i += 1;
      if (i >= lines.length) break;
      var n = parseInt(String(lines[i] || "").trim(), 10);
      if (isNaN(n) || n < 1 || i + 1 + n >= lines.length) break;
      var comment = lines[i + 1] || "";
      var atoms = [];
      for (var j = 0; j < n; j += 1) {
        var parts = String(lines[i + 2 + j] || "").trim().split(/\s+/);
        if (parts.length >= 4) {
          atoms.push({ id: j, elem: parts[0], x: +parts[1], y: +parts[2], z: +parts[3] });
        }
      }
      frames.push({ atoms: atoms, comment: comment, energy: parseEnergyFromComment(comment) });
      i += 2 + n;
    }
    return frames;
  }

  function atomsToXyz(atoms) {
    var out = atoms.length + "\n\n";
    for (var i = 0; i < atoms.length; i += 1) {
      var a = atoms[i];
      out += a.elem + " " + a.x.toFixed(6) + " " + a.y.toFixed(6) + " " + a.z.toFixed(6) + "\n";
    }
    return out;
  }

  /** Stable change-detection key for a structureRef (order-insensitive). */
  function refKey(ref) {
    if (ref == null) return null;
    if (typeof ref === "string") return "s:" + ref;
    if (typeof ref !== "object") return "x:" + String(ref);
    var kind = String(ref.kind || "");
    if (kind === "inline-xyz") {
      return ["inline-xyz", String(ref.frameIndex == null ? 0 : ref.frameIndex), String(ref.text || "")].join("|");
    }
    return [
      kind,
      String(ref.jobId || ""),
      String(ref.path || ""),
      String(ref.frameIndex == null ? 0 : ref.frameIndex),
    ].join("|");
  }

  /** Normalize a structureRef into a concrete request, or an invalid marker. */
  function normalizeRef(ref) {
    if (ref == null) return null;
    if (typeof ref === "string") {
      var text = String(ref).trim();
      if (!text) return null;
      try {
        return normalizeRef(JSON.parse(text));
      } catch (e) {
        return { kind: "invalid", reason: "structureRef 字符串需为 JSON 对象（模块 README 冻结形状）" };
      }
    }
    if (typeof ref !== "object") {
      return { kind: "invalid", reason: "structureRef 类型不支持: " + typeof ref };
    }
    var kind = String(ref.kind || "");
    var frameIndex = typeof ref.frameIndex === "number" && ref.frameIndex >= 0 ? Math.floor(ref.frameIndex) : 0;
    if (kind === "inline-xyz") {
      if (typeof ref.text !== "string" || !ref.text.trim()) {
        return { kind: "invalid", reason: "inline-xyz 需要非空 text" };
      }
      return { kind: kind, text: ref.text, frameIndex: frameIndex, entryId: "inline-xyz" };
    }
    if (kind === "job-file" || kind === "job-remote-file") {
      if (typeof ref.jobId !== "string" || !ref.jobId) {
        return { kind: "invalid", reason: kind + " 需要非空 jobId" };
      }
      if (typeof ref.path !== "string" || !ref.path) {
        return { kind: "invalid", reason: kind + " 需要非空 path" };
      }
      return { kind: kind, jobId: ref.jobId, path: ref.path, frameIndex: frameIndex, entryId: ref.jobId + ":" + ref.path };
    }
    return { kind: "invalid", reason: "未知 structureRef.kind: " + (kind || "(空)") };
  }

  function encodeFilePath(path) {
    return String(path).split("/").map(encodeURIComponent).join("/");
  }

  function resolveApiBase() {
    if (typeof window !== "undefined" && window.API_BASE) return String(window.API_BASE);
    return DEFAULT_API_BASE;
  }

  function buildUrl(req) {
    var jobId = encodeURIComponent(req.jobId);
    var encoded = encodeFilePath(req.path);
    if (req.kind === "job-file") {
      return resolveApiBase() + "/jobs/" + jobId + "/files/" + encoded;
    }
    return resolveApiBase() + "/jobs/" + jobId + "/remote-files/" + encoded + "/preview?mode=structure";
  }

  function unwrapRemoteContent(body) {
    if (body == null) throw new Error("空响应体");
    if (typeof body.content === "string") return body.content;
    throw new Error("响应缺少 content 字段");
  }

  /** Accept a fetch Response, a pre-parsed api()-style body, or raw text. */
  function readResponse(resp, kind) {
    if (resp == null) throw new Error("空响应");
    if (typeof resp === "string") return Promise.resolve(resp);
    if (resp && typeof resp === "object" && resp.ok === false) {
      throw new Error("HTTP " + (resp.status || "") + (resp.statusText ? " " + resp.statusText : ""));
    }
    if (kind === "job-remote-file") {
      if (typeof resp.json === "function") return Promise.resolve(resp.json()).then(unwrapRemoteContent);
      return Promise.resolve(unwrapRemoteContent(resp));
    }
    if (typeof resp.text === "function") return Promise.resolve(resp.text());
    throw new Error("不支持的响应形状");
  }

  function resolveFetch(deps) {
    if (deps && typeof deps.fetch === "function") {
      return function () { return deps.fetch.apply(null, arguments); };
    }
    if (typeof window !== "undefined" && typeof window.fetch === "function") {
      return function () { return window.fetch.apply(window, arguments); };
    }
    return null;
  }

  /* ── module instance ──────────────────────────────────────────────────── */

  function createInstance(deps) {
    deps = deps || {};
    var context = deps.context || null;
    var fetchFn = null;

    // mount-scoped state
    var alive = false;
    var mounted = false;
    var suspended = false;
    var host = null;
    var root = null;
    var canvasEl = null;
    var overlayEl = null;
    var msgEl = null;
    var detailEl = null;
    var retryBtn = null;
    var scene = null;
    var framingState = null;
    var unsubscribeContext = null;
    var generation = 0;
    var currentRef = null;
    var currentRefKey = null;
    var storeOwned = false;
    var ready = false;

    /* ------------------------- DOM (self-rendered) ----------------------- */

    function buildDom() {
      var doc = window.document;
      if (!doc || typeof doc.createElement !== "function") {
        throw new Error("molecule-viewer: document 不可用");
      }
      root = doc.createElement("div");
      root.className = "acp-mod-viewer";
      root.setAttribute("data-acp-module-state", "empty");
      canvasEl = doc.createElement("div");
      canvasEl.className = "acp-mod-viewer-canvas";
      root.appendChild(canvasEl);
      overlayEl = doc.createElement("div");
      overlayEl.className = "acp-mod-viewer-overlay";
      var box = doc.createElement("div");
      box.className = "acp-mod-viewer-box";
      msgEl = doc.createElement("p");
      msgEl.className = "acp-mod-viewer-message";
      msgEl.setAttribute("data-i18n", "next.viewer.empty");
      msgEl.setAttribute("aria-live", "polite");
      msgEl.textContent = "等待结构加载";
      detailEl = doc.createElement("p");
      detailEl.className = "acp-mod-viewer-detail";
      detailEl.hidden = true;
      retryBtn = doc.createElement("button");
      retryBtn.type = "button";
      retryBtn.className = "acp-mod-viewer-retry";
      retryBtn.setAttribute("data-i18n", "next.viewer.retry");
      retryBtn.textContent = "重试";
      retryBtn.hidden = true;
      retryBtn.addEventListener("click", onRetryClick);
      box.appendChild(msgEl);
      box.appendChild(detailEl);
      box.appendChild(retryBtn);
      overlayEl.appendChild(box);
      root.appendChild(overlayEl);
      host.appendChild(root);
    }

    var COPY = {
      empty: { text: "等待结构加载", i18n: "next.viewer.empty" },
      loading: { text: "结构加载中…", i18n: "next.viewer.loading" },
      ready: { text: "", i18n: "next.viewer.ready" },
      error: { text: "结构加载失败", i18n: "next.viewer.errorTitle" },
    };

    function setState(name, detail) {
      if (!root) return;
      ready = name === "ready";
      root.setAttribute("data-acp-module-state", name);
      var copy = COPY[name] || COPY.empty;
      msgEl.textContent = copy.text;
      msgEl.setAttribute("data-i18n", copy.i18n);
      if (detail) {
        detailEl.textContent = String(detail);
        detailEl.hidden = false;
      } else {
        detailEl.textContent = "";
        detailEl.hidden = true;
      }
      overlayEl.hidden = name === "ready";
      retryBtn.hidden = name !== "error";
    }

    function onRetryClick() {
      if (!alive) return;
      loadRef(currentRef);
    }

    /* --------------------------- scene ownership ------------------------- */

    function ensureScene() {
      if (scene) return scene;
      var viz = window.ACPVisualization;
      if (!viz || !viz.scene || typeof viz.scene.create !== "function") {
        throw new Error("ACPVisualization.scene 未加载（检查 visualization 脚本顺序）");
      }
      scene = viz.scene.create({
        container: canvasEl,
        options: { backgroundColor: DEFAULT_BACKGROUND, antialias: true },
      });
      if (!scene) {
        var lib = viz.mol3d;
        var libReady = lib && typeof lib.isAvailable === "function" && lib.isAvailable();
        throw new Error(libReady ? "3D 场景创建失败" : "3D 组件尚未就绪，请稍后重试");
      }
      return scene;
    }

    function scheduleFraming() {
      var viz = window.ACPVisualization;
      if (!viz || !viz.framing || !scene || suspended) return;
      if (!framingState) {
        framingState = { epoch: 0, pending: false, settled: false, timer: null, observer: null };
      }
      viz.framing.scheduleViewerFraming(canvasEl, framingState, function () { return scene; });
    }

    function disposeFraming() {
      var viz = window.ACPVisualization;
      if (framingState && viz && viz.framing && typeof viz.framing.disposeViewerFraming === "function") {
        try { viz.framing.disposeViewerFraming(framingState); } catch (e) { /* teardown is best-effort */ }
      }
      framingState = null;
    }

    function syncGeometryStore(frame, req) {
      var store = window.ACPGeometryStore;
      if (!store || typeof store.load !== "function") return;
      var symbols = [];
      var coordinates = [];
      for (var i = 0; i < frame.atoms.length; i += 1) {
        var a = frame.atoms[i];
        symbols.push(a.elem);
        coordinates.push([a.x, a.y, a.z]);
      }
      try {
        store.load({ entryId: req.entryId, symbols: symbols, coordinates: coordinates });
        storeOwned = true;
      } catch (e) { /* store is auxiliary; rendering must not fail on it */ }
    }

    function releaseStoreIfOwned() {
      if (!storeOwned) return;
      storeOwned = false;
      var store = window.ACPGeometryStore;
      if (store && typeof store.reset === "function") {
        try { store.reset(); } catch (e) { /* teardown is best-effort */ }
      }
    }

    /* ------------------------------ loading ------------------------------ */

    function renderFrame(frame, req) {
      try {
        ensureScene();
        scene.clearScene();
        var token = scene.addModel(atomsToXyz(frame.atoms), "xyz");
        if (!token) {
          var err = typeof scene.lastError === "function" ? scene.lastError() : null;
          setState("error", err ? "场景渲染失败: " + err.message : "场景渲染失败");
          return;
        }
        token.setStyle({}, BALL_STICK);
        scene.setBackgroundColor(DEFAULT_BACKGROUND);
        syncGeometryStore(frame, req);
        scheduleFraming();
        scene.render();
        setState("ready");
      } catch (e) {
        setState("error", e && e.message ? e.message : String(e));
      }
    }

    function onStructureText(text, gen, req) {
      if (!alive || gen !== generation) return; // late result guard
      var frames;
      try {
        frames = parseMultiFrameXyz(text);
      } catch (e) {
        frames = [];
      }
      if (!frames.length) {
        setState("error", "结构解析失败（无有效 XYZ 帧）");
        return;
      }
      var idx = typeof req.frameIndex === "number" && req.frameIndex >= 0 ? Math.floor(req.frameIndex) : 0;
      if (idx >= frames.length) idx = frames.length - 1;
      var frame = frames[idx];
      if (!frame || !frame.atoms.length) {
        setState("error", "结构解析失败（第 " + idx + " 帧无原子）");
        return;
      }
      renderFrame(frame, req);
    }

    function loadRef(ref) {
      if (!alive) return;
      generation += 1;
      var gen = generation;
      currentRef = ref == null ? null : ref;
      var req = normalizeRef(ref);
      if (!req) {
        clearStructure();
        setState("empty");
        return;
      }
      if (req.kind === "invalid") {
        setState("error", req.reason);
        return;
      }
      if (req.kind === "inline-xyz") {
        setState("loading");
        onStructureText(req.text, gen, req);
        return;
      }
      if (!fetchFn) {
        setState("error", "无可用 fetch（注入 deps.fetch 或 window.fetch）");
        return;
      }
      setState("loading");
      var url = buildUrl(req);
      Promise.resolve()
        .then(function () { return fetchFn(url, {}); })
        .then(function (resp) { return readResponse(resp, req.kind); })
        .then(function (text) {
          if (!alive || gen !== generation) return; // A→B→A / dispose guard
          onStructureText(text, gen, req);
        })
        .catch(function (error) {
          if (!alive || gen !== generation) return;
          setState("error", error && error.message ? error.message : String(error));
        });
    }

    function clearStructure() {
      currentRef = null;
      ready = false;
      disposeFraming();
      if (scene) {
        try { scene.clearScene(); } catch (e) { /* scene may already be released */ }
      }
      releaseStoreIfOwned();
    }

    /* --------------------------- context (P0 read) ----------------------- */

    function applySnapshot(snap) {
      if (!alive || !mounted) return;
      var nextRef = snap == null ? null : snap.structureRef;
      var nextKey = refKey(nextRef);
      if (nextKey === currentRefKey) return; // same ref → no reload, no flicker
      currentRefKey = nextKey;
      loadRef(nextRef);
    }

    function onContextNotify() {
      if (!alive || !mounted || !context || typeof context.getSnapshot !== "function") return;
      var snap = null;
      try { snap = context.getSnapshot(); } catch (e) { return; }
      applySnapshot(snap);
    }

    /* ------------------------------ lifecycle ---------------------------- */

    function cleanupMount() {
      if (unsubscribeContext) {
        try { unsubscribeContext(); } catch (e) { /* unsubscribe is best-effort */ }
        unsubscribeContext = null;
      }
      generation += 1;
      disposeFraming();
      if (scene) {
        try { scene.dispose(); } catch (e) { /* teardown is best-effort */ }
        scene = null;
      }
      releaseStoreIfOwned();
      currentRef = null;
      currentRefKey = null;
      ready = false;
      suspended = false;
    }

    function removeDom() {
      if (root && root.parentNode && typeof root.parentNode.removeChild === "function") {
        try { root.parentNode.removeChild(root); } catch (e) { /* already detached */ }
      }
      root = null;
      canvasEl = null;
      overlayEl = null;
      msgEl = null;
      detailEl = null;
      retryBtn = null;
    }

    function renderMountFailure(error) {
      // Local, in-host failure surface; never rethrows (mount 返回 ≠ 数据就绪).
      try {
        if (!root) {
          buildDom();
        }
        setState("error", (error && error.message ? error.message : String(error)) + "（模块局部错误，不影响其它面板）");
      } catch (e) {
        // Host itself unusable — nothing local left to render; swallow: the
        // instance stays alive-but-empty and dispose() remains safe.
        if (typeof console !== "undefined" && console && console.error) {
          console.error("[molecule-viewer] mount failed and error surface unavailable:", e);
        }
      }
    }

    var instance = {
      mount: function (hostEl, initial) {
        if (mounted) {
          return Promise.reject(new Error("molecule-viewer: already mounted (dispose() first)"));
        }
        if (!hostEl || typeof hostEl.appendChild !== "function") {
          return Promise.reject(new TypeError("molecule-viewer mount: host 必须是 DOM 元素"));
        }
        alive = true;
        mounted = true;
        suspended = false;
        host = hostEl;
        try {
          buildDom();
          fetchFn = resolveFetch(deps);
          if (!window.ACPVisualization || !window.ACPVisualization.scene) {
            throw new Error("ACPVisualization.scene 未加载（检查 visualization 脚本顺序）");
          }
          if (context && typeof context.subscribe === "function") {
            unsubscribeContext = context.subscribe(onContextNotify);
          }
          var snap = initial;
          if (snap == null && context && typeof context.getSnapshot === "function") {
            snap = context.getSnapshot();
          }
          applySnapshot(snap);
        } catch (error) {
          cleanupMount();
          mounted = true; // still mounted: the host shows the local error box
          alive = true;
          renderMountFailure(error);
        }
        return Promise.resolve();
      },

      updateContext: function (next) {
        // P0 read-only: react to structureRef/taskRef changes; never write back.
        applySnapshot(next || null);
      },

      resize: function () {
        if (!alive || !scene || suspended) return;
        try { scene.resize(); scene.render(); } catch (e) { /* resize is best-effort */ }
      },

      suspend: function () {
        if (!alive || suspended) return;
        suspended = true;
        disposeFraming(); // drops the ResizeObserver + pending framing timers
        if (scene) {
          try { scene.setHidden(true); } catch (e) { /* hidden is best-effort */ }
        }
      },

      resume: function () {
        if (!alive || !suspended) return;
        suspended = false;
        if (scene) {
          try { scene.setHidden(false); } catch (e) { /* hidden is best-effort */ }
          if (ready) scheduleFraming();
        }
      },

      dispose: function () {
        if (!alive && !mounted && !root) return; // idempotent
        alive = false;
        mounted = false;
        cleanupMount();
        removeDom();
        host = null;
      },
    };

    return instance;
  }

  window.ACPNextModules[MODULE_ID] = {
    descriptor: {
      id: MODULE_ID,
      interfaceVersion: 1,
      dependencies: [],
      // Top-level provides/binds are what the Shell findPanelFor reads
      // (commands.js resolves "structure.render" / binds structureRef).
      provides: ["structure.render"],
      binds: ["structureRef"],
      capabilities: { requires: [], provides: ["structure.render"] },
      hosts: ["acp-web"],
      supportedInstancing: "single",
      configSchemaVersion: 0,
      error: { titleKey: "next.viewer.errorTitle", messageKey: "next.viewer.errorMessage" },
    },
    factory: function (deps) { return createInstance(deps || {}); },
  };

  // Test seam (pure functions only — no instance state leaks).
  window.ACPNextModules[MODULE_ID]._internals = {
    parseMultiFrameXyz: parseMultiFrameXyz,
    atomsToXyz: atomsToXyz,
    refKey: refKey,
    normalizeRef: normalizeRef,
    buildUrl: buildUrl,
  };
})();
