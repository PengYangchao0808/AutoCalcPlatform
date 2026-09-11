/**
 * ACP Structure Viewer — state store + catalog fetch + stale-response guard
 * @version 0.12.0
 *
 * Namespace: window.ACPStructureViewer
 *
 * Exposes:
 *   - state                     (the live structureViewerState object)
 *   - geometryStore             (shared geometry/style/camera store, phase D)
 *   - sharedLoadGeometry(xyzText, {canvasId, source})  (ONE load path)
 *   - registerCanvasLoader(canvasId, fn)  (per-canvas load adapters)
 *   - saveCamera(canvasId) / restoreCamera(canvasId)
 *   - setStylePreset(name) / getStylePreset()
 *   - loadOverlay(a, b)          (server-mapped overlay + RMSD, todo 40)
 *   - clearOverlay()              (remove the overlay second model)
 *   - overlayMeasurementsBlocked() (unproven-mapping clearing rule)
 *   - playIrcPath(direction)     (IRC forward/reverse frame playback, todo 39)
 *   - stopIrcPlayback()          (idempotent playback teardown)
 *   - isIrcPlaying()             (true while playback active/loading)
 *   - loadStructureViewer(jobId, opts)
 *   - onJobSelected(jobId, opts) (called from selectJob; loads catalog + default geometry)
 *   - onEnergyNodeSelected(jobId, entryMeta) (energy-graph one-way push; phase A)
 *   - selectEntry(entryId, origin)
 *   - refreshIfChanged()
 *   - loadSelectedGeometry()    (fetches geometry for selected entry)
 *   - injectManualEntry(relpath, label, xyzText?) (manual file injection)
 *   - renderStructureViewer()   (renders list panel from state.payload)
 *   - renderInspector()         (renders inspector for selected entry)
 *   - toggleListDrawer()        (narrow-screen drawer toggle)
 *   - toggleInspectorDrawer()   (narrow-screen drawer toggle)
 *
 * Internal (test-overridable via namespace property):
 *   - _fetchImpl                (default: window.fetch; tests inject a fake)
 *   - _styleSpecImpl            (default: app STYLE_PRESETS table -> module default)
 *   - _mainViewerImpl           (default: app's global lexical `viewer`)
 *   - _canvasViewerImpl         (per-canvasId viewer resolution, non-main)
 *   - _applyCatalogResponse     (pure: applies a server response to state)
 *   - _esc                      (XSS-safe text insertion)
 *   - _sha256hex(str)            (sync SHA-256 → hex string)
 *
 * i18n: structure.* keys migrated to I18N dictionaries (zh-CN + en-US);
 *       _t() helper reads from app's t() with STR-table fallback for Node.js.
 */
(function () {
  "use strict";

  var VERSION = "0.12.0";

  /* ---- user-visible strings (zh fallback; primary source is I18N dict via _t()) ---- */
  var STR = {
    NEWER_AVAILABLE: "\u6709\u65b0\u7ed3\u6784\u53ef\u7528",       // 有新结构可用
    PENDING_FETCH: "\u7b49\u5f85\u8fdc\u7a0b\u7ed3\u679c",         // 等待远程结果
    PENDING_RETRY: "\u8fdc\u7a0b\u6587\u4ef6\u5c1a\u672a\u540c\u6b65\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5", // 远程文件尚未同步，请稍后重试
    NO_ENTRY: "\u672a\u9009\u62e9\u7ed3\u6784",                     // 未选择结构
    NO_ENTRY_HINT: "\u8bf7\u4ece\u5de6\u4fa7\u5217\u8868\u9009\u62e9\u4e00\u4e2a\u7ed3\u6784", // 请从左侧列表选择一个结构
    REFRESH: "\u5237\u65b0",                                         // 刷新
    LIST_TITLE: "\u7ed3\u6784\u5217\u8868",                         // 结构列表
    INSPECTOR_TITLE: "\u68c0\u67e5\u5668",                           // 检查器
    SOURCE: "\u6765\u6e90",                                           // 来源
    STATUS: "\u72b6\u6001",                                           // 状态
    ENERGY: "\u80fd\u91cf",                                           // 能量
    DELTA_E: "\u0394E",                                               // ΔE
    WEIGHT: "Boltzmann \u6743\u91cd",                                // Boltzmann 权重
    VIBRATIONS: "\u632f\u52a8\u6a21\u5f0f",                         // 振动模式
    MEASUREMENTS: "\u6d4b\u91cf",                                     // 测量
    WARNINGS: "\u8b66\u544a",                                         // 警告
    COMPLETED: "\u5df2\u5b8c\u6210",                                 // 已完成
    FAILED: "\u5931\u8d25",                                           // 失败
    SOURCE_KINDS: {
      formal_result: "\u6b63\u5f0f\u7ed3\u679c",                   // 正式结果
      algorithm_recommendation: "\u81ea\u52a8\u63a8\u8350",         // 自动推荐
      manual_review: "\u4eba\u5de5\u786e\u8ba4",                   // 人工确认
      last_valid_cycle: "\u6700\u540e\u6709\u6548\u5468\u671f",     // 最后有效周期
      calculation_input: "\u8ba1\u7b97\u8f93\u5165",               // 计算输入
      manual_file: "\u624b\u52a8\u6587\u4ef6",                     // 手动文件
    },
    VIBRATIONS_NA: "\u6682\u4e0d\u53ef\u7528",                     // 暂不可用
    VIB_NONE: "\u65e0\u632f\u52a8\u6570\u636e",                   // 无振动数据 (Wave 5: catalog says available=false)
    EDIT_TITLE: "\u51e0\u4f55\u7f16\u8f91",                             // 几何编辑
    BOND_SOURCE: "\u6210\u952e\u6765\u6e90",                           // 成键来源
    EDIT_DIRTY: "\u5df2\u4fee\u6539\uff0c\u672a\u4fdd\u5b58",           // 已修改，未保存
    EDIT_UNDO: "\u64a4\u9500",                                           // 撤销
    EDIT_REDO: "\u91cd\u505a",                                           // 重做
    EDIT_RESET: "\u91cd\u7f6e",                                           // 重置
    EDIT_DISCARD: "\u653e\u5f03",                                         // 放弃
    EDIT_SAVE_AS: "\u53e6\u5b58\u4e3a",                                 // 另存为
    EDIT_CANCEL: "\u53d6\u6d88",                                         // 取消
    EDIT_COLLISION: "\u4e25\u91cd\u78b0\u649e",                         // 严重碰撞
    EDIT_EXPORT: "\u5bfc\u51fa XYZ",                                     // 导出 XYZ
    EDIT_SAVE_ASSET: "\u53e6\u5b58\u4e3a\u7ed3\u6784\u8d44\u4ea7",       // 另存为结构资产
    EDIT_NEW_CALC: "\u4ee5\u6b64\u7ed3\u6784\u65b0\u5efa\u8ba1\u7b97",   // 以此结构新建计算
    EDIT_SAVED: "\u5df2\u4fdd\u5b58",                                   // 已保存
    EDIT_SAVE_ERROR: "\u4fdd\u5b58\u5931\u8d25",                         // 保存失败
    MEASUREMENTS_PLACEHOLDER: "\u9009\u62e9\u539f\u5b50\u540e\u663e\u793a\u6d4b\u91cf\u7ed3\u679c", // 选择原子后显示测量结果
    EDIT_PLACEHOLDER: "\u7f16\u8f91\u529f\u80fd\u5c06\u5728\u540e\u7eed\u7248\u672c\u5f00\u653e", // 编辑功能将在后续版本开放
    IRC_TITLE: "IRC \u8def\u5f84\u52a8\u753b",                             // IRC 路径动画
    IRC_PLAY_FORWARD: "\u64ad\u653e\u6b63\u5411",                         // 播放正向
    IRC_PLAY_REVERSE: "\u64ad\u653e\u53cd\u5411",                         // 播放反向
    IRC_STOP: "\u505c\u6b62",                                             // 停止
    IRC_PLAYING: "\u64ad\u653e\u4e2d",                                   // 播放中
    IRC_LOADING: "\u52a0\u8f7d\u5e27\u2026",                               // 加载帧…
    OVERLAY_TITLE: "\u53e0\u5408\u5bf9\u6bd4",                             // 叠合对比
    OVERLAY_RMSD: "RMSD",
    OVERLAY_MAPPED_COUNT: "\u5df2\u6620\u5c04\u539f\u5b50",               // 已映射原子
    OVERLAY_SOURCE_IDENTITY: "\u540c\u5e8f",                               // 同序
    OVERLAY_SOURCE_MCS: "\u6700\u5927\u516c\u5171\u5b50\u7ed3\u6784",     // 最大公共子结构
    OVERLAY_UNPROVEN: "\u65e0\u6cd5\u5efa\u7acb\u539f\u5b50\u6620\u5c04\uff0c\u6d4b\u91cf\u5df2\u6e05\u9664", // 无法建立原子映射，测量已清除
    OVERLAY_CLEAR: "\u6e05\u9664\u53e0\u5408",                             // 清除叠合
    OVERLAY_MAX_ATOM: "\u6700\u5927\u4f4d\u79fb\u539f\u5b50",             // 最大位移原子
  };

  /**
   * i18n lookup with STR-table fallback.
   * Tries the app's t(key) first (browser); falls back to STR[fallbackKey]
   * for Node.js test environments where the app's t() is unavailable.
   *
   * @param {string} key        - i18n key (e.g. "structure.newer_available")
   * @param {string} fallback   - fallback value (STR table constant)
   * @returns {string}
   */
  function _t(key, fallback) {
    if (typeof t === "function") {
      var val = t(key);
      if (val && val !== key) return val;
    }
    return fallback;
  }

  /** @returns {Function|null} */
  function _getFetchImpl() {
    return (typeof window !== "undefined" && window.ACPStructureViewer && window.ACPStructureViewer._fetchImpl) || null;
  }

  /**
   * XSS-safe text insertion.  Converts a value to a safe string for use
   * in textContent (never innerHTML with server strings).
   *
   * @param {*} val
   * @returns {string}
   */
  function _esc(val) {
    if (val == null) return "";
    return String(val);
  }

  /* ---- SHA-256 (pure JS, sync, self-contained) ---- */

  /**
   * Compute SHA-256 hex digest of a UTF-8 string.
   * Uses the Web Crypto API when available (browser), falls back to a
   * minimal pure-JS implementation for Node.js test environments.
   *
   * @param {string} str
   * @returns {string} lowercase hex digest
   */
  function _sha256hex(str) {
    /* Try Web Crypto (browser) */
    if (typeof crypto !== "undefined" && crypto.subtle && typeof TextEncoder !== "undefined") {
      /* Web Crypto is async; for sync compat we use the pure-JS fallback.
         The pure-JS path is ~40 lines and correct for short strings. */
    }
    /* Pure-JS SHA-256 (FIPS 180-4 compliant for messages < 2^64 bits) */
    var K = [
      0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
      0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
      0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
      0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
      0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
      0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
      0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
      0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
    ];
    function _rotr(x, n) { return (x >>> n) | (x << (32 - n)); }
    function _ch(x, y, z) { return (x & y) ^ (~x & z); }
    function _maj(x, y, z) { return (x & y) ^ (x & z) ^ (y & z); }
    function _sigma0(x) { return _rotr(x, 2) ^ _rotr(x, 13) ^ _rotr(x, 22); }
    function _sigma1(x) { return _rotr(x, 6) ^ _rotr(x, 11) ^ _rotr(x, 25); }
    function _gamma0(x) { return _rotr(x, 7) ^ _rotr(x, 18) ^ (x >>> 3); }
    function _gamma1(x) { return _rotr(x, 17) ^ _rotr(x, 19) ^ (x >>> 10); }

    /* Encode string as UTF-8 bytes */
    var bytes = [];
    for (var i = 0; i < str.length; i++) {
      var c = str.charCodeAt(i);
      if (c < 0x80) {
        bytes.push(c);
      } else if (c < 0x800) {
        bytes.push(0xc0 | (c >> 6), 0x80 | (c & 0x3f));
      } else if (c < 0xd800 || c >= 0xe000) {
        bytes.push(0xe0 | (c >> 12), 0x80 | ((c >> 6) & 0x3f), 0x80 | (c & 0x3f));
      } else {
        /* surrogate pair */
        i++;
        c = 0x10000 + (((c & 0x3ff) << 10) | (str.charCodeAt(i) & 0x3ff));
        bytes.push(0xf0 | (c >> 18), 0x80 | ((c >> 12) & 0x3f), 0x80 | ((c >> 6) & 0x3f), 0x80 | (c & 0x3f));
      }
    }
    var msgLen = bytes.length;

    /* Padding */
    bytes.push(0x80);
    while (bytes.length % 64 !== 56) { bytes.push(0); }
    var bitLen = msgLen * 8;
    /* Append length as 64-bit big-endian (high 32 bits always 0 for our use) */
    bytes.push(0, 0, 0, 0, (bitLen >> 24) & 0xff, (bitLen >> 16) & 0xff, (bitLen >> 8) & 0xff, bitLen & 0xff);

    /* Initial hash values */
    var H = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];

    /* Process each 512-bit block */
    for (var off = 0; off < bytes.length; off += 64) {
      var W = new Array(64);
      for (var t = 0; t < 16; t++) {
        W[t] = (bytes[off + t * 4] << 24) | (bytes[off + t * 4 + 1] << 16) | (bytes[off + t * 4 + 2] << 8) | bytes[off + t * 4 + 3];
      }
      for (var t2 = 16; t2 < 64; t2++) {
        W[t2] = (_gamma1(W[t2 - 2]) + W[t2 - 7] + _gamma0(W[t2 - 15]) + W[t2 - 16]) | 0;
      }
      var a = H[0], b = H[1], c = H[2], d = H[3], e = H[4], f = H[5], g = H[6], h = H[7];
      for (var t3 = 0; t3 < 64; t3++) {
        var T1 = (h + _sigma1(e) + _ch(e, f, g) + K[t3] + W[t3]) | 0;
        var T2 = (_sigma0(a) + _maj(a, b, c)) | 0;
        h = g; g = f; f = e; e = (d + T1) | 0;
        d = c; c = b; b = a; a = (T1 + T2) | 0;
      }
      H[0] = (H[0] + a) | 0; H[1] = (H[1] + b) | 0; H[2] = (H[2] + c) | 0; H[3] = (H[3] + d) | 0;
      H[4] = (H[4] + e) | 0; H[5] = (H[5] + f) | 0; H[6] = (H[6] + g) | 0; H[7] = (H[7] + h) | 0;
    }

    /* Format as hex */
    var hex = "";
    for (var hi = 0; hi < 8; hi++) {
      var v = H[hi];
      hex += ((v >>> 28) & 0xf).toString(16) + ((v >>> 24) & 0xf).toString(16) +
             ((v >>> 20) & 0xf).toString(16) + ((v >>> 16) & 0xf).toString(16) +
             ((v >>> 12) & 0xf).toString(16) + ((v >>> 8) & 0xf).toString(16) +
             ((v >>> 4) & 0xf).toString(16) + (v & 0xf).toString(16);
    }
    return hex;
  }

  /**
   * Build a manual-file entry id matching the Python backend scheme:
   * ``manual_<sha256(relpath)[:12]>``
   *
   * @param {string} relpath
   * @returns {string}
   */
  function _manualEntryId(relpath) {
    return "manual_" + _sha256hex(relpath).slice(0, 12);
  }

  /**
   * @typedef {Object} StructureViewerState
   * @property {string|null} jobId
   * @property {Object|null} payload
   * @property {string|null} revision
   * @property {string|null} selectedEntryId
   * @property {string|null} selectionOrigin
   * @property {number} selectionToken
   * @property {boolean} dirty
   * @property {Object|null} editState
   * @property {number} requestToken
   * @property {string} availability
   * @property {boolean} newerAvailable
   * @property {string|null} error
   * @property {AbortController|null} _abortController
   */
  var structureViewerState = {
    jobId: null,
    payload: null,
    revision: null,
    selectedEntryId: null,
    selectionOrigin: null,
    selectionToken: 0,
    dirty: false,
    editState: null,
    requestToken: 0,
    availability: "",
    newerAvailable: false,
    error: null,
    geometryLoadedFor: null,
    pendingGeometryRetry: false,
    displayedCoords: null,
    displayedSymbols: null,
    displayedEntryId: null,
    _abortController: null,
  };

  /**
   * Apply a catalog response to the state object.  Pure-ish: mutates the
   * supplied state but performs no I/O and is deterministic for a given input.
   *
   * @param {StructureViewerState} state
   * @param {number} capturedToken - the requestToken at fetch-initiation time
   * @param {Object} data          - parsed JSON from the server
   * @returns {boolean} true if applied, false if discarded (stale)
   */
  function _applyCatalogResponse(state, capturedToken, data) {
    if (capturedToken !== state.requestToken) {
      return false;
    }
    state.payload = data;
    state.revision = data && data.revision ? data.revision : null;
    state.availability = data && data.availability ? data.availability : "";
    state.error = null;
    state.newerAvailable = false;

    if (data && data.default_entry_id && !state.selectedEntryId) {
      state.selectedEntryId = data.default_entry_id;
      state.selectionOrigin = "auto";
    }
    return true;
  }

  /**
   * @param {string} jobId
   * @param {string|null} itemId
   * @returns {string}
   */
  function _catalogUrl(jobId, itemId) {
    var base = "/api/v1/jobs/" + encodeURIComponent(jobId) + "/structure-viewer";
    if (itemId) {
      base += "?item_id=" + encodeURIComponent(itemId);
    }
    return base;
  }

  /**
   * Load (or reload) the structure-viewer catalog for a job.
   *
   * @param {string} jobId
   * @param {{ itemId?: string }} [opts]
   * @returns {Promise<void>}
   */
  function loadStructureViewer(jobId, opts) {
    opts = opts || {};
    var itemId = opts.itemId || null;

    if (structureViewerState._abortController) {
      try { structureViewerState._abortController.abort(); } catch (_) { /* ignore */ }
    }

    var controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    structureViewerState._abortController = controller;

    structureViewerState.requestToken += 1;
    var capturedToken = structureViewerState.requestToken;

    structureViewerState.jobId = jobId;
    structureViewerState.payload = null;
    structureViewerState.revision = null;
    structureViewerState.availability = "";
    structureViewerState.error = null;
    structureViewerState.newerAvailable = false;
    structureViewerState.selectedEntryId = null;
    structureViewerState.selectionOrigin = null;
    structureViewerState.displayedCoords = null;
    structureViewerState.displayedSymbols = null;
    structureViewerState.displayedEntryId = null;

    var fetchFn = _getFetchImpl();
    if (!fetchFn) {
      structureViewerState.error = "fetch not available";
      return Promise.resolve();
    }

    var url = _catalogUrl(jobId, itemId);
    var fetchOpts = { headers: { "Accept": "application/json" } };
    if (controller) {
      fetchOpts.signal = controller.signal;
    }

    return fetchFn(url, fetchOpts)
      .then(function (resp) {
        if (!resp.ok) {
          throw new Error("HTTP " + resp.status + " " + resp.statusText);
        }
        return resp.json();
      })
      .then(function (data) {
        _applyCatalogResponse(structureViewerState, capturedToken, data);
        renderStructureViewer();
        renderInspector();
      })
      .catch(function (err) {
        if (err && err.name === "AbortError") { return; }
        if (capturedToken !== structureViewerState.requestToken) { return; }
        structureViewerState.error = err && err.message ? err.message : String(err);
        renderStructureViewer();
        renderInspector();
      });
  }

  /**
   * Select an entry in the structure list.
   *
   * @param {string} entryId
   * @param {string} [origin]
   * @returns {number} The new selectionToken
   */
  function selectEntry(entryId, origin) {
    /* entry switch mid-play: stop the mode animation + restore equilibrium
       BEFORE the new entry's geometry swaps in (todo 30 ordering contract) */
    if (typeof window !== "undefined" && window.ACPVibrationViewer &&
        typeof window.ACPVibrationViewer.stopAnimationAndRestore === "function") {
      window.ACPVibrationViewer.stopAnimationAndRestore();
    }
    structureViewerState.selectionToken += 1;
    structureViewerState.selectedEntryId = entryId;
    /* Shared selection ownership (todo 38): every selection — list click,
       energy-graph push, manual/auto — flows through HERE and shares the
       single selectionToken above. The energy viewer sets origin
       "energy_graph"; list clicks set "list" so a future todo can
       distinguish user intent. Deliberate scope: NO reverse feedback —
       selecting in the structure list does NOT auto-highlight energy
       nodes (no bidirectional loop in this todo). */
    structureViewerState.selectionOrigin = origin || "user";
    renderStructureViewer();
    renderInspector();
    stopIrcPlayback(); /* entry switch ends playback (todo-30 ordering spirit) */
    clearOverlay(); /* entry switch clears the overlay second model */
    loadSelectedGeometry();
    return structureViewerState.selectionToken;
  }

  /**
   * Refresh the catalog if the server revision changed since last load.
   *
   * @returns {Promise<void>}
   */
  function refreshIfChanged() {
    if (!structureViewerState.jobId) {
      return Promise.resolve();
    }

    var oldRevision = structureViewerState.revision;
    var wasDirty = structureViewerState.dirty;
    var prevSelected = structureViewerState.selectedEntryId;

    var fetchFn = _getFetchImpl();
    if (!fetchFn) {
      return Promise.resolve();
    }

    var url = _catalogUrl(structureViewerState.jobId, null);
    var controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    var fetchOpts = { headers: { "Accept": "application/json" } };
    if (controller) {
      fetchOpts.signal = controller.signal;
    }

    return fetchFn(url, fetchOpts)
      .then(function (resp) {
        if (!resp.ok) {
          throw new Error("HTTP " + resp.status + " " + resp.statusText);
        }
        return resp.json();
      })
      .then(function (data) {
        var newRevision = data && data.revision ? data.revision : null;
        if (newRevision === oldRevision) { return; }
        if (wasDirty) {
          structureViewerState.newerAvailable = true;
          renderInspector();
          return;
        }
        _applyCatalogResponse(structureViewerState, structureViewerState.requestToken, data);
        if (prevSelected && structureViewerState.payload) {
          var entries = structureViewerState.payload.entries || [];
          var found = false;
          for (var i = 0; i < entries.length; i++) {
            if (entries[i].id === prevSelected) { found = true; break; }
          }
          if (found) {
            structureViewerState.selectedEntryId = prevSelected;
          }
        }
        renderStructureViewer();
        renderInspector();
      })
      .catch(function (err) {
        if (err && err.name === "AbortError") { return; }
      });
  }

  /**
   * Parse the first XYZ frame into element symbols + coordinates.
   * Pure; feeds vibration arrows (todo 28) via state.displayedCoords.
   *
   * @param {string} xyzText
   * @returns {{symbols: string[], coords: Array<[number,number,number]>}|null}
   */
  function _parseXyzFirstFrame(xyzText) {
    if (!xyzText) return null;
    var lines = String(xyzText).trim().split(/\r?\n/);
    if (lines.length < 2) return null;
    var n = parseInt(lines[0], 10);
    if (isNaN(n) || n <= 0 || lines.length < 2 + n) return null;
    var symbols = [];
    var coords = [];
    for (var j = 0; j < n; j++) {
      var parts = (lines[2 + j] || "").trim().split(/\s+/);
      if (parts.length < 4) continue;
      var x = +parts[1], y = +parts[2], z = +parts[3];
      if (!isFinite(x) || !isFinite(y) || !isFinite(z)) continue;
      symbols.push(parts[0]);
      coords.push([x, y, z]);
    }
    if (!coords.length) return null;
    return { symbols: symbols, coords: coords };
  }

  /* ---- shared geometry loader + style/camera store (phase D, todo 38) ---- */

  /**
   * Fallback canvas style spec (the energy mini viewer's original look).
   * Used only when the app's STYLE_PRESETS table is not reachable (Node
   * test environments); in the browser the shared preset always wins.
   */
  var _DEFAULT_CANVAS_STYLE = { stick: { radius: 0.14 }, sphere: { scale: 0.26 } };

  /**
   * Shared geometry/style/camera store. ONE owner for what is displayed on
   * every structure canvas (main viewer, energy mini viewer, future canvases).
   *
   * @typedef {Object} GeometryStore
   * @property {string|null} currentXyz   - last XYZ text loaded via the shared loader
   * @property {string} stylePreset      - shared style preset name for ALL canvases
   * @property {Object} cameras          - canvasId -> saved view array (viewer.getView())
   * @property {number} loaderVersion    - bumped on every sharedLoadGeometry call
   */
  var geometryStore = {
    currentXyz: null,
    stylePreset: "ball-stick",
    cameras: {},
    loaderVersion: 0,
  };

  /**
   * Per-canvas load adapters. Each adapter loads into the canvas's EXISTING
   * viewer instance via the app's existing loading paths — the shared loader
   * never creates a viewer. The main canvas goes through the app bridge
   * (window._svLoadXyzToViewer); other canvases register their own adapter
   * via registerCanvasLoader().
   *
   * @type {Object<string, Function>}
   */
  var _canvasLoaders = {
    main: function (xyzText) {
      if (typeof window === "undefined") return false;
      if (typeof window._svLoadXyzToViewer === "function") {
        window._svLoadXyzToViewer(xyzText, structureViewerState.selectedEntryId);
        return true;
      }
      return false;
    },
  };

  /**
   * Register (or replace) the load adapter for a canvas id.
   *
   * @param {string} canvasId
   * @param {Function} fn - (xyzText, styleSpec) => boolean
   */
  function registerCanvasLoader(canvasId, fn) {
    if (!canvasId || typeof fn !== "function") return false;
    _canvasLoaders[canvasId] = fn;
    return true;
  }

  /**
   * Resolve the style spec object for a preset name.
   * Injectable via namespace property `_styleSpecImpl` (tests); then the
   * app's global STYLE_PRESETS table; then the module default.
   *
   * @param {string} presetName
   * @returns {Object} style spec (never null)
   */
  function _resolveStyleSpec(presetName) {
    var impl = (typeof window !== "undefined" && window.ACPStructureViewer &&
      typeof window.ACPStructureViewer._styleSpecImpl === "function")
      ? window.ACPStructureViewer._styleSpecImpl : null;
    if (impl) {
      var injected = impl(presetName);
      if (injected) return injected;
    }
    /* STYLE_PRESETS is a top-level const in the app's inline script —
       reachable here as a global lexical binding at call time. */
    try {
      if (typeof STYLE_PRESETS !== "undefined" && STYLE_PRESETS[presetName]) {
        return STYLE_PRESETS[presetName].style;
      }
    } catch (_) { /* not loaded — fall through */ }
    return _DEFAULT_CANVAS_STYLE;
  }

  /**
   * Resolve the LIVE viewer instance for a canvas id (never creates one).
   * Main canvas: injectable `_mainViewerImpl`, else the app's global lexical
   * `viewer`. Other canvases: injectable `_canvasViewerImpl(canvasId)`.
   *
   * @param {string} canvasId
   * @returns {Object|null}
   */
  function _canvasViewer(canvasId) {
    if (canvasId === "main") {
      if (typeof window !== "undefined" && window.ACPStructureViewer &&
          typeof window.ACPStructureViewer._mainViewerImpl === "function") {
        try { return window.ACPStructureViewer._mainViewerImpl(); } catch (_) { return null; }
      }
      try {
        if (typeof viewer !== "undefined" && viewer) return viewer;
      } catch (_) { /* not loaded — stay null */ }
      return null;
    }
    var impl = (typeof window !== "undefined" && window.ACPStructureViewer &&
      typeof window.ACPStructureViewer._canvasViewerImpl === "function")
      ? window.ACPStructureViewer._canvasViewerImpl : null;
    if (impl) {
      try { return impl(canvasId); } catch (_) { return null; }
    }
    return null;
  }

  /**
   * THE shared geometry loader (phase D). Parses the XYZ (reusing
   * _parseXyzFirstFrame), applies the current shared style preset, and loads
   * into the target canvas's EXISTING viewer instance via its registered
   * adapter. Main-canvas loads also update state.displayedCoords/Symbols.
   *
   * @param {string} xyzText
   * @param {{ canvasId?: string, source?: string }} [opts]
   * @returns {{ loaded: boolean, parsed: Object|null }|null} null when no
   *   loader is registered for the canvas or xyzText is empty
   */
  function sharedLoadGeometry(xyzText, opts) {
    opts = opts || {};
    var canvasId = opts.canvasId || "main";
    if (!xyzText || typeof _canvasLoaders[canvasId] !== "function") {
      return null;
    }
    geometryStore.loaderVersion += 1;
    geometryStore.currentXyz = xyzText;

    var styleSpec = _resolveStyleSpec(geometryStore.stylePreset);
    var loaded = false;
    try {
      loaded = !!_canvasLoaders[canvasId](xyzText, styleSpec);
    } catch (_) {
      loaded = false;
    }

    var parsed = null;
    if (canvasId === "main") {
      parsed = _parseXyzFirstFrame(xyzText);
      structureViewerState.displayedCoords = parsed ? parsed.coords : null;
      structureViewerState.displayedSymbols = parsed ? parsed.symbols : null;
      structureViewerState.displayedEntryId = structureViewerState.selectedEntryId;
    }
    return { loaded: loaded, parsed: parsed };
  }

  /**
   * Save the current camera of a canvas into the shared store.
   *
   * @param {string} canvasId
   * @returns {boolean}
   */
  function saveCamera(canvasId) {
    var v = _canvasViewer(canvasId);
    if (!v || typeof v.getView !== "function") return false;
    try {
      geometryStore.cameras[canvasId] = v.getView();
      return true;
    } catch (_) {
      return false;
    }
  }

  /**
   * Restore a previously saved camera onto a canvas.
   *
   * @param {string} canvasId
   * @returns {boolean}
   */
  function restoreCamera(canvasId) {
    var v = _canvasViewer(canvasId);
    var view = geometryStore.cameras[canvasId];
    if (!v || !view || typeof v.setView !== "function") return false;
    try {
      v.setView(view);
      if (typeof v.render === "function") v.render();
      return true;
    } catch (_) {
      return false;
    }
  }

  /**
   * Set the shared style preset for ALL canvases. Persists in the store;
   * applies immediately to the main canvas through the app's existing
   * applyStylePreset path when a viewer is live; other canvases pick the
   * preset up on their next sharedLoadGeometry call.
   *
   * @param {string} name
   * @returns {string} the stored preset name
   */
  function setStylePreset(name) {
    if (!name) return geometryStore.stylePreset;
    geometryStore.stylePreset = String(name);
    if (_canvasViewer("main") && typeof applyStylePreset === "function") {
      try { applyStylePreset(geometryStore.stylePreset); } catch (_) { /* empty viewer */ }
    }
    return geometryStore.stylePreset;
  }

  /**
   * @returns {string} the current shared style preset name
   */
  function getStylePreset() {
    return geometryStore.stylePreset;
  }

  /* ---- IRC path playback (todo 39) ---- */

  var IRC_FRAME_MS = 250; /* ~4 frames/s */

  var ircPlayback = {
    direction: null,
    playing: false,
    loading: false,
    frameIndex: 0,
    frames: [],
    timerHandle: null,
    selectionToken: 0,
  };

  /* IRC playback adapter: rebuilds the model on the MAIN viewer with the
     todo-29 per-frame recipe (getView → removeAllModels → addModel → style
     → setView → render) so the camera NEVER jumps between frames. */
  registerCanvasLoader("main-irc", function (xyzText, styleSpec) {
    var v = _canvasViewer("main");
    if (!v || typeof v.addModel !== "function") return false;
    var view = null;
    try { if (typeof v.getView === "function") view = v.getView(); } catch (_) { view = null; }
    try { if (typeof v.removeAllModels === "function") v.removeAllModels(); } catch (_) { /* empty */ }
    v.addModel(xyzText, "xyz");
    if (styleSpec && typeof v.setStyle === "function") v.setStyle({}, styleSpec);
    if (view && typeof v.setView === "function") { try { v.setView(view); } catch (_) { /* keep */ } }
    try { if (typeof v.render === "function") v.render(); } catch (_) { /* keep */ }
    return true;
  });

  function _ircSetInterval(fn, ms) {
    var impl = (typeof window !== "undefined" && window.ACPStructureViewer &&
      typeof window.ACPStructureViewer._setIntervalImpl === "function")
      ? window.ACPStructureViewer._setIntervalImpl : null;
    if (impl) return impl(fn, ms);
    if (typeof setInterval === "function") return setInterval(fn, ms);
    return null;
  }

  function _ircClearInterval(handle) {
    var impl = (typeof window !== "undefined" && window.ACPStructureViewer &&
      typeof window.ACPStructureViewer._clearIntervalImpl === "function")
      ? window.ACPStructureViewer._clearIntervalImpl : null;
    if (impl) { impl(handle); return; }
    if (typeof clearInterval === "function") clearInterval(handle);
  }

  function _ircEntries(direction) {
    var payload = structureViewerState.payload;
    var entries = (payload && payload.entries) || [];
    var out = [];
    for (var i = 0; i < entries.length; i++) {
      var e = entries[i];
      if (e && e.group_id === "irc_" + direction && e.geometry && e.geometry.endpoint) out.push(e);
    }
    out.sort(function (a, b) {
      var fa = (a.source && a.source.frame_index) || 0;
      var fb = (b.source && b.source.frame_index) || 0;
      return fa - fb;
    });
    return out;
  }

  function _ircStep() {
    if (!ircPlayback.playing) return;
    if (ircPlayback.selectionToken !== structureViewerState.selectionToken) {
      stopIrcPlayback();
      return;
    }
    /* self-stop when the structure tab is hidden (tab-switch teardown) */
    var layout = (typeof document !== "undefined") ? document.getElementById("sv-layout") : null;
    if (layout && layout.offsetWidth === 0 && layout.offsetHeight === 0) {
      stopIrcPlayback();
      return;
    }
    if (ircPlayback.frameIndex >= ircPlayback.frames.length) {
      stopIrcPlayback();
      return;
    }
    sharedLoadGeometry(ircPlayback.frames[ircPlayback.frameIndex], {
      canvasId: "main-irc",
      source: "irc_playback",
    });
    ircPlayback.frameIndex += 1;
    _renderPlaybackBar();
  }

  /**
   * Play the IRC path for one direction, frame by frame in FILE ORDER
   * (~4 frames/s), through the shared loader onto the main canvas with a
   * stable camera.  Prefetches frame XYZ via each entry's geometry endpoint;
   * stops on tab/job/entry switch (see stopIrcPlayback callers).
   *
   * @param {string} direction - "forward" | "reverse"
   * @returns {boolean} false when the direction has no playable frames
   */
  function playIrcPath(direction) {
    if (direction !== "forward" && direction !== "reverse") return false;
    stopIrcPlayback();
    clearOverlay();
    var entries = _ircEntries(direction);
    if (!entries.length) return false;
    var fetchFn = _getFetchImpl();
    if (!fetchFn) return false;
    /* both animations own the main viewer — never run them interleaved */
    if (typeof window !== "undefined" && window.ACPVibrationViewer &&
        typeof window.ACPVibrationViewer.isAnimationActive === "function" &&
        window.ACPVibrationViewer.isAnimationActive() &&
        typeof window.ACPVibrationViewer.stopAnimationAndRestore === "function") {
      window.ACPVibrationViewer.stopAnimationAndRestore();
    }

    ircPlayback.direction = direction;
    ircPlayback.playing = true;
    ircPlayback.loading = true;
    ircPlayback.frameIndex = 0;
    ircPlayback.frames = [];
    ircPlayback.selectionToken = structureViewerState.selectionToken;
    var capturedToken = structureViewerState.selectionToken;

    var fetches = entries.map(function (e) {
      return fetchFn(e.geometry.endpoint, { headers: { "Accept": "text/plain" } })
        .then(function (resp) { return resp.ok ? resp.text() : null; })
        .catch(function () { return null; });
    });
    Promise.all(fetches).then(function (texts) {
      if (capturedToken !== structureViewerState.selectionToken) { stopIrcPlayback(); return; }
      var frames = [];
      for (var i = 0; i < texts.length; i++) { if (texts[i]) frames.push(texts[i]); }
      if (!frames.length) { stopIrcPlayback(); return; }
      ircPlayback.frames = frames;
      ircPlayback.loading = false;
      _renderPlaybackBar();
      _ircStep();
      ircPlayback.timerHandle = _ircSetInterval(_ircStep, IRC_FRAME_MS);
    });
    _renderPlaybackBar();
    return true;
  }

  /**
   * Stop IRC playback.  Idempotent; safe before any playback started.
   * Wired into the job-switch (onJobSelected) and entry-switch (selectEntry)
   * teardown paths plus the per-step tab-visibility self-stop.
   */
  function stopIrcPlayback() {
    var wasActive = ircPlayback.playing || ircPlayback.loading;
    if (ircPlayback.timerHandle !== null) {
      _ircClearInterval(ircPlayback.timerHandle);
      ircPlayback.timerHandle = null;
    }
    ircPlayback.playing = false;
    ircPlayback.loading = false;
    ircPlayback.direction = null;
    ircPlayback.frames = [];
    ircPlayback.frameIndex = 0;
    if (wasActive && typeof document !== "undefined") _renderPlaybackBar();
  }

  /**
   * @returns {boolean} true while IRC playback is active or loading frames
   */
  function isIrcPlaying() {
    return !!(ircPlayback.playing || ircPlayback.loading);
  }

  function _playbackBtn(key, fallback, handler) {
    var btn = document.createElement("button");
    btn.setAttribute("type", "button");
    btn.className = "sv-edit-btn";
    btn.textContent = _t(key, fallback);
    btn.addEventListener("click", handler);
    return btn;
  }

  function _renderPlaybackBar() {
    if (typeof document === "undefined") return;
    var bar = document.getElementById("structure-playback-bar");
    if (!bar) return;
    var hasIrc = _ircEntries("forward").length > 0 || _ircEntries("reverse").length > 0;
    if (!hasIrc) { bar.style.display = "none"; bar.textContent = ""; return; }
    bar.style.display = "";
    bar.textContent = "";
    var title = document.createElement("span");
    title.className = "sv-playback-title";
    title.textContent = _t("structure.irc.title", STR.IRC_TITLE);
    bar.appendChild(title);
    if (ircPlayback.playing) {
      var status = document.createElement("span");
      status.className = "sv-playback-status";
      var dirLabel = ircPlayback.direction === "forward"
        ? _t("structure.irc.play_forward", STR.IRC_PLAY_FORWARD)
        : _t("structure.irc.play_reverse", STR.IRC_PLAY_REVERSE);
      var progress = ircPlayback.loading
        ? _t("structure.irc.loading", STR.IRC_LOADING)
        : " " + Math.min(ircPlayback.frameIndex + 1, ircPlayback.frames.length) + "/" + ircPlayback.frames.length;
      status.textContent = _t("structure.irc.playing", STR.IRC_PLAYING) + " · " + dirLabel + progress;
      bar.appendChild(status);
      bar.appendChild(_playbackBtn("structure.irc.stop", STR.IRC_STOP, function () {
        stopIrcPlayback();
      }));
    } else {
      bar.appendChild(_playbackBtn("structure.irc.play_forward", STR.IRC_PLAY_FORWARD, function () {
        playIrcPath("forward");
      }));
      bar.appendChild(_playbackBtn("structure.irc.play_reverse", STR.IRC_PLAY_REVERSE, function () {
        playIrcPath("reverse");
      }));
    }
  }

  /* ---- structure overlay + RMSD (todo 40) ---- */

  /* Distinct style for the overlaid SECOND model on the existing main
     viewer — cyan carbons + translucency separate it from model A. */
  var OVERLAY_STYLE_B = {
    stick: { radius: 0.14, colorscheme: "cyanCarbon", opacity: 0.85 },
    sphere: { scale: 0.24, colorscheme: "cyanCarbon", opacity: 0.55 },
  };

  var overlayState = {
    active: false,
    entryA: null,
    entryB: null,
    data: null,
    xyzB: null,
    loading: false,
    error: null,
  };

  /**
   * True while an ACTIVE overlay could not prove an atom mapping — the
   * inspector must show the clearing note and disable measurement UI
   * (cross-structure measurements are never kept on an unproven mapping).
   */
  function overlayMeasurementsBlocked() {
    return !!(overlayState.active && overlayState.data &&
      overlayState.data.reason === "unproven");
  }

  function _overlayUrl(entryA, entryB) {
    return "/api/v1/jobs/" + encodeURIComponent(structureViewerState.jobId || "") +
      "/structure-viewer/overlay?entry_a=" + encodeURIComponent(entryA) +
      "&entry_b=" + encodeURIComponent(entryB);
  }

  function _removeOverlayModels() {
    var v = _canvasViewer("main");
    if (!v) return;
    try {
      while (typeof v.getModelCount === "function" && v.getModelCount() > 1) {
        v.removeModel(v.getModel(1));
      }
      if (typeof v.render === "function") v.render();
    } catch (_) { /* viewer may be empty */ }
  }

  /**
   * Add geometry B as a SECOND model (cyan/transparent) on the existing
   * main viewer, with the max-displacement atom highlighted on model A.
   * Never creates a viewer instance; model A stays untouched.
   */
  function renderOverlay() {
    if (!overlayState.active || !overlayState.xyzB) return;
    var v = _canvasViewer("main");
    if (!v || typeof v.addModel !== "function") return;
    _removeOverlayModels();
    var modelB = null;
    try { modelB = v.addModel(overlayState.xyzB, "xyz"); } catch (_) { return; }
    if (modelB && typeof modelB.setStyle === "function") {
      try { modelB.setStyle({}, OVERLAY_STYLE_B); } catch (_) { /* keep default */ }
    }
    var md = overlayState.data && overlayState.data.max_displacement;
    if (md && typeof md.i === "number") {
      var baseScale = 0.25;
      try { if (typeof getCurrentSphereScale === "function") baseScale = getCurrentSphereScale(); } catch (_) { /* app helper absent */ }
      try {
        v.addStyle({ index: md.i, model: 0 }, {
          sphere: { scale: baseScale + 0.14, color: "yellow", opacity: 0.6 },
        });
      } catch (_) { /* highlight is cosmetic */ }
    }
    try { v.render(); } catch (_) { /* render is best-effort */ }
  }

  /**
   * Fetch the server-side overlay (mapping + Kabsch RMSD — the mapping is
   * NEVER guessed client-side) plus geometry B, then render the overlay.
   *
   * @param {string} entryIdA - base entry (the selected one)
   * @param {string} entryIdB - entry to overlay on top
   * @returns {Promise<boolean>} true when the overlay request succeeded
   */
  function loadOverlay(entryIdA, entryIdB) {
    if (!entryIdA || !entryIdB || entryIdA === entryIdB) {
      return Promise.resolve(false);
    }
    clearOverlay();
    var fetchFn = _getFetchImpl();
    if (!fetchFn) {
      overlayState.active = true;
      overlayState.error = "fetch not available";
      renderInspector();
      return Promise.resolve(false);
    }
    overlayState.active = true;
    overlayState.loading = true;
    overlayState.entryA = entryIdA;
    overlayState.entryB = entryIdB;
    overlayState.data = null;
    overlayState.xyzB = null;
    overlayState.error = null;
    renderInspector();
    var capturedToken = structureViewerState.selectionToken;

    var entryB = null;
    var entries = (structureViewerState.payload && structureViewerState.payload.entries) || [];
    for (var i = 0; i < entries.length; i++) {
      if (entries[i].id === entryIdB) { entryB = entries[i]; break; }
    }

    return fetchFn(_overlayUrl(entryIdA, entryIdB), { headers: { "Accept": "application/json" } })
      .then(function (resp) {
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.json();
      })
      .then(function (data) {
        if (capturedToken !== structureViewerState.selectionToken) return false;
        overlayState.data = data;
        if (!entryB || !entryB.geometry || !entryB.geometry.endpoint) return true;
        return fetchFn(entryB.geometry.endpoint, { headers: { "Accept": "text/plain" } })
          .then(function (resp) { return resp.ok ? resp.text() : null; })
          .then(function (xyzB) {
            if (capturedToken !== structureViewerState.selectionToken) return false;
            overlayState.xyzB = xyzB;
            return true;
          })
          .catch(function () { return true; });
      })
      .then(function (okFetch) {
        overlayState.loading = false;
        if (capturedToken !== structureViewerState.selectionToken) {
          clearOverlay();
          return false;
        }
        if (!okFetch) return false;
        renderOverlay();
        renderInspector();
        return true;
      })
      .catch(function (err) {
        overlayState.loading = false;
        overlayState.error = (err && err.message) ? err.message : String(err);
        renderInspector();
        return false;
      });
  }

  /**
   * Clear the active overlay: removes the second model from the main
   * viewer and resets the state.  Idempotent.  Wired into the entry/job
   * switch teardown paths and IRC playback start.
   */
  function clearOverlay() {
    var wasActive = overlayState.active;
    overlayState.active = false;
    overlayState.entryA = null;
    overlayState.entryB = null;
    overlayState.data = null;
    overlayState.xyzB = null;
    overlayState.loading = false;
    overlayState.error = null;
    if (wasActive) {
      _removeOverlayModels();
      if (typeof document !== "undefined") renderInspector();
    }
  }

  function _overlaySourceText(reason) {
    if (reason === "identity") return _t("structure.overlay.source_identity", STR.OVERLAY_SOURCE_IDENTITY);
    if (reason === "mcs") return _t("structure.overlay.source_mcs", STR.OVERLAY_SOURCE_MCS);
    return reason || "";
  }

  function _renderOverlaySection(inspBody) {
    if (!overlayState.active) return;
    var div = document.createElement("div");
    div.className = "sv-inspector-section sv-overlay-panel";
    var lbl = document.createElement("div");
    lbl.className = "sv-inspector-label";
    lbl.textContent = _t("structure.overlay.title", STR.OVERLAY_TITLE);
    div.appendChild(lbl);

    if (overlayState.loading) {
      var loading = document.createElement("div");
      loading.className = "sv-inspector-value sv-muted";
      loading.textContent = _t("structure.irc.loading", STR.IRC_LOADING);
      div.appendChild(loading);
    } else if (overlayState.error) {
      var errLine = document.createElement("div");
      errLine.className = "sv-edit-collision";
      errLine.textContent = overlayState.error;
      div.appendChild(errLine);
    } else if (overlayState.data) {
      var data = overlayState.data;
      if (data.reason === "unproven") {
        var note = document.createElement("div");
        note.className = "sv-edit-collision";
        note.textContent = _t("structure.overlay.unproven", STR.OVERLAY_UNPROVEN);
        div.appendChild(note);
      } else {
        var rmsdLine = document.createElement("div");
        rmsdLine.className = "sv-inspector-value";
        rmsdLine.textContent = _t("structure.overlay.rmsd", STR.OVERLAY_RMSD) + " " +
          (data.rmsd != null ? data.rmsd.toFixed(3) : "--") + " \u00c5";
        div.appendChild(rmsdLine);
        var mappedLine = document.createElement("div");
        mappedLine.className = "sv-inspector-value sv-muted";
        mappedLine.textContent = _t("structure.overlay.mapped_count", STR.OVERLAY_MAPPED_COUNT) +
          ": " + (data.n_mapped || 0) + " \u00b7 " + _overlaySourceText(data.reason);
        div.appendChild(mappedLine);
        var md = data.max_displacement;
        if (md && typeof md.distance === "number") {
          var maxLine = document.createElement("div");
          maxLine.className = "sv-inspector-value sv-muted";
          maxLine.textContent = _t("structure.overlay.max_atom", STR.OVERLAY_MAX_ATOM) +
            ": A#" + md.i + " \u2194 B#" + md.j + " (" + md.distance.toFixed(3) + " \u00c5)";
          div.appendChild(maxLine);
        }
      }
    }
    var row = document.createElement("div");
    row.className = "sv-edit-row";
    row.appendChild(_editBtn("structure.overlay.clear", STR.OVERLAY_CLEAR, function () {
      clearOverlay();
    }));
    div.appendChild(row);
    inspBody.appendChild(div);
  }

  /**
   * Load geometry for the currently selected entry.
   * Fetches geometry.endpoint, handles 409 pending_fetch with auto-retry,
   * then hands XYZ text to the app's existing model-loading path.
   *
   * @returns {Promise<void>}
   */
  function loadSelectedGeometry() {
    var state = structureViewerState;
    if (!state.payload || !state.selectedEntryId) { return Promise.resolve(); }

    var entries = state.payload.entries || [];
    var entry = null;
    for (var i = 0; i < entries.length; i++) {
      if (entries[i].id === state.selectedEntryId) { entry = entries[i]; break; }
    }
    if (!entry || !entry.geometry || !entry.geometry.endpoint) { return Promise.resolve(); }

    var capturedToken = state.selectionToken;
    var endpoint = entry.geometry.endpoint;

    var fetchFn = _getFetchImpl();
    if (!fetchFn) { return Promise.resolve(); }

    return fetchFn(endpoint, { headers: { "Accept": "text/plain" } })
      .then(function (resp) {
        if (resp.status === 409) {
          /* pending_fetch — auto-retry once with ?fetch=1 */
          var retryUrl = endpoint + (endpoint.indexOf("?") >= 0 ? "&" : "?") + "fetch=1";
          return fetchFn(retryUrl, { headers: { "Accept": "text/plain" } })
            .then(function (retryResp) {
              if (!retryResp.ok) {
                state.pendingGeometryRetry = true;
                renderInspector();
                return null;
              }
              return retryResp.text();
            });
        }
        if (!resp.ok) { return null; }
        return resp.text();
      })
      .then(function (xyzText) {
        if (!xyzText) { return; }
        if (capturedToken !== state.selectionToken) { return; }
        state.geometryLoadedFor = state.selectedEntryId;
        state.pendingGeometryRetry = false;
        /* Phase D (todo 38): ONE load path — parse + style + model load all
           live in sharedLoadGeometry (main canvas -> _svLoadXyzToViewer). */
        var result = sharedLoadGeometry(xyzText, { canvasId: "main", source: "structure_list" });
        var parsed = result ? result.parsed : null;
        if (typeof window !== "undefined" && window.ACPStructureEditor &&
            typeof window.ACPStructureEditor.bindEntry === "function" && parsed) {
          /* dirty switches raise editorState.pendingSwitch (todo 36 prompt) */
          try { window.ACPStructureEditor.bindEntry(entry.id, parsed.symbols, parsed.coords); } catch (_) { /* editor is optional */ }
        }
        if (typeof window !== "undefined" && window.ACPVibrationViewer &&
            typeof window.ACPVibrationViewer.refreshArrows === "function") {
          window.ACPVibrationViewer.refreshArrows();
        }
      })
      .catch(function (err) {
        if (err && err.name === "AbortError") { return; }
      });
  }

  /**
   * Called from the app's selectJob(). Loads the structure catalog
   * and auto-loads the default entry geometry into the main viewer.
   *
   * @param {string} jobId
   * @param {{ itemId?: string }} [opts]
   * @returns {Promise<void>}
   */
  function onJobSelected(jobId, opts) {
    opts = opts || {};
    /* job switch: stop IRC playback first (todo-30 teardown call site),
       then full vibration teardown — no running loop or stale arrows may
       survive into the new job */
    stopIrcPlayback();
    clearOverlay();
    if (typeof window !== "undefined" && window.ACPVibrationViewer &&
        typeof window.ACPVibrationViewer.handleTeardown === "function") {
      window.ACPVibrationViewer.handleTeardown();
    }
    return loadStructureViewer(jobId, opts)
      .then(function () {
        /* Auto-select default entry if payload loaded successfully */
        var state = structureViewerState;
        if (state.payload && state.payload.default_entry_id && !state.selectedEntryId) {
          selectEntry(state.payload.default_entry_id, "auto");
        }
        return loadSelectedGeometry();
      });
  }

  /**
   * Inject a manual_file entry into the current payload and select it.
   * Mirrors the Python backend's manual_entry_id scheme exactly.
   *
   * @param {string} relpath - Relative path to the geometry file
   * @param {string} label   - Human-readable label
   * @param {string} [xyzText] - XYZ content (if already loaded)
   * @returns {string} The entry id
   */
  function injectManualEntry(relpath, label, xyzText) {
    var state = structureViewerState;
    var entryId = _manualEntryId(relpath);

    var entry = {
      id: entryId,
      group_id: "",
      label: label || relpath,
      role: "minimum",
      status: "completed",
      geometry: {
        endpoint: "/api/v1/jobs/" + encodeURIComponent(state.jobId || "") + "/structure-viewer/entries/" + encodeURIComponent(entryId) + "/geometry",
        format: "xyz",
      },
      source: { kind: "manual_file", geometry_ref: relpath },
      badges: [],
      vibrations: { available: false },
    };

    if (!state.payload) {
      state.payload = {
        schema_version: "structure_viewer_v1",
        job_id: state.jobId || "",
        workflow: "",
        job_status: "",
        availability: "ready",
        revision: null,
        default_entry_id: entryId,
        groups: [],
        entries: [entry],
        warnings: [],
      };
    } else {
      /* Check for duplicate */
      var entries = state.payload.entries || [];
      var found = false;
      for (var i = 0; i < entries.length; i++) {
        if (entries[i].id === entryId) { found = true; break; }
      }
      if (!found) {
        entries.push(entry);
        state.payload.entries = entries;
      }
    }

    selectEntry(entryId, "manual_file");

    /* If xyzText is provided, load it directly through the shared loader */
    if (xyzText) {
      sharedLoadGeometry(xyzText, { canvasId: "main", source: "manual_file" });
    }

    return entryId;
  }

  /* ---- badge rendering helpers ---- */

  function _badgeClass(badge) {
    if (badge === "\u672a\u786e\u8ba4") return "sv-badge-unconfirmed";       // 未确认
    if (badge === "\u517c\u5bb9\u6a21\u5f0f") return "sv-badge-legacy";     // 兼容模式
    if (badge === "failed-last-frame") return "sv-badge-failed-frame";
    if (badge === "TS" || badge === "INT") return "sv-badge-role";
    if (badge.indexOf("rank-") === 0) return "sv-badge-rank";
    return "sv-badge-role";
  }

  function _renderBadgeChip(badge) {
    var span = document.createElement("span");
    span.className = "sv-badge " + _badgeClass(badge);
    span.textContent = _esc(badge);
    return span;
  }

  /* ---- render: structure list ---- */

  /**
   * Render the structure list panel from state.payload.
   * Groups section rendered only when >1 entry or a named group exists.
   * Single-entry payloads hide the list panel entirely.
   */
  function renderStructureViewer() {
    if (typeof document === "undefined") return;
    var listHeader = document.getElementById("sv-list-header");
    var listBody = document.getElementById("sv-list-body");
    var layout = document.getElementById("sv-layout");
    if (!listHeader || !listBody || !layout) return;

    listBody.innerHTML = "";

    var payload = structureViewerState.payload;
    var entries = (payload && payload.entries) || [];
    var groups = (payload && payload.groups) || [];

    /* availability / error notices */
    if (structureViewerState.availability === "pending_fetch") {
      var notice = document.createElement("div");
      notice.className = "sv-notice sv-notice-pending";
      notice.textContent = _t("structure.pending_fetch", STR.PENDING_FETCH) + "\u2014" + _t("structure.pending_retry", STR.PENDING_RETRY);
      listBody.appendChild(notice);
    }
    if (structureViewerState.error) {
      var errNotice = document.createElement("div");
      errNotice.className = "sv-notice sv-notice-error";
      errNotice.textContent = _esc(structureViewerState.error);
      listBody.appendChild(errNotice);
    }

    /* hide list for single-entry payloads (no named groups) */
    var hasNamedGroup = false;
    for (var g = 0; g < groups.length; g++) {
      if (groups[g].label && groups[g].label !== groups[g].id) {
        hasNamedGroup = true;
        break;
      }
    }
    var showList = entries.length > 1 || hasNamedGroup;
    layout.classList.toggle("sv-list-hidden", !showList);

    if (!showList) {
      listHeader.textContent = "";
      return;
    }

    listHeader.textContent = _t("structure.list_title", STR.LIST_TITLE);

    /* group entries by group_id */
    var groupMap = {};
    for (var gi = 0; gi < groups.length; gi++) {
      groupMap[groups[gi].id] = groups[gi];
    }

    var entriesByGroup = {};
    for (var ei = 0; ei < entries.length; ei++) {
      var e = entries[ei];
      var gid = e.group_id || "__ungrouped";
      if (!entriesByGroup[gid]) entriesByGroup[gid] = [];
      entriesByGroup[gid].push(e);
    }

    var groupIds = Object.keys(entriesByGroup);
    for (var gk = 0; gk < groupIds.length; gk++) {
      var groupId = groupIds[gk];
      var group = groupMap[groupId];
      var groupEntries = entriesByGroup[groupId];

      /* group header (only if named group exists) */
      if (group && group.label) {
        var header = document.createElement("div");
        header.className = "sv-group-header";
        header.textContent = _esc(group.label);
        listBody.appendChild(header);
      }

      for (var ej = 0; ej < groupEntries.length; ej++) {
        var entry = groupEntries[ej];
        var row = _renderEntryRow(entry);
        listBody.appendChild(row);
      }
    }

    _renderPlaybackBar();
  }

  function _renderEntryRow(entry) {
    var row = document.createElement("div");
    row.className = "sv-entry-row";
    if (entry.id === structureViewerState.selectedEntryId) {
      row.className += " sv-active";
    }
    row.setAttribute("data-entry-id", _esc(entry.id));

    /* status dot */
    var dot = document.createElement("span");
    dot.className = "sv-status-dot sv-status-" + _esc(entry.status || "completed");
    row.appendChild(dot);

    /* label */
    var label = document.createElement("span");
    label.className = "sv-entry-label";
    label.textContent = _esc(entry.label || entry.id);
    row.appendChild(label);

    /* badges */
    var badges = entry.badges || [];
    for (var bi = 0; bi < badges.length; bi++) {
      row.appendChild(_renderBadgeChip(badges[bi]));
    }

    /* energy */
    if (entry.energy && entry.energy.value != null) {
      var energySpan = document.createElement("span");
      energySpan.className = "sv-entry-energy";
      energySpan.textContent = _formatEnergy(entry.energy);
      row.appendChild(energySpan);
    }

    /* boltzmann bar */
    if (entry.boltzmann_weight != null) {
      var bar = document.createElement("span");
      bar.className = "sv-boltzmann-bar";
      var fill = document.createElement("span");
      fill.className = "sv-boltzmann-bar-fill";
      fill.style.width = Math.round(entry.boltzmann_weight * 100) + "%";
      bar.appendChild(fill);
      row.appendChild(bar);
    }

    /* overlay action (todo 40): compare this entry against the SELECTED
       one — a click here must NOT change the selection */
    if (entry.id !== structureViewerState.selectedEntryId) {
      var overlayBtn = document.createElement("button");
      overlayBtn.setAttribute("type", "button");
      overlayBtn.className = "sv-edit-btn sv-overlay-btn";
      overlayBtn.textContent = _t("structure.overlay.title", STR.OVERLAY_TITLE);
      overlayBtn.addEventListener("click", function (ev) {
        if (ev && typeof ev.stopPropagation === "function") ev.stopPropagation();
        loadOverlay(structureViewerState.selectedEntryId, entry.id);
      });
      row.appendChild(overlayBtn);
    }

    /* click handler */
    row.addEventListener("click", function () {
      selectEntry(entry.id, "list");
    });

    return row;
  }

  function _formatEnergy(energy) {
    if (!energy || energy.value == null) return "";
    var val = energy.value;
    var unit = energy.unit || "hartree";
    if (unit === "hartree") {
      return val.toFixed(6) + " Eh";
    }
    return val.toFixed(4) + " " + _esc(unit);
  }

  /* ---- render: inspector ---- */

  /**
   * Render the inspector panel for the currently selected entry.
   */
  function renderInspector() {
    if (typeof document === "undefined") return;
    var inspHeader = document.getElementById("sv-inspector-header");
    var inspBody = document.getElementById("sv-inspector-body");
    if (!inspHeader || !inspBody) return;

    inspBody.innerHTML = "";
    inspHeader.textContent = _t("structure.inspector_title", STR.INSPECTOR_TITLE);

    var payload = structureViewerState.payload;
    var entries = (payload && payload.entries) || [];
    var selectedId = structureViewerState.selectedEntryId;

    /* newer-available notice */
    if (structureViewerState.newerAvailable) {
      var newerNotice = document.createElement("div");
      newerNotice.className = "sv-notice sv-notice-newer";
      newerNotice.textContent = _t("structure.newer_available", STR.NEWER_AVAILABLE);
      var refreshBtn = document.createElement("button");
      refreshBtn.className = "sv-refresh-btn";
      refreshBtn.textContent = _t("structure.refresh", STR.REFRESH);
      refreshBtn.addEventListener("click", function () {
        structureViewerState.newerAvailable = false;
        refreshIfChanged();
      });
      newerNotice.appendChild(document.createElement("br"));
      newerNotice.appendChild(refreshBtn);
      inspBody.appendChild(newerNotice);
    }

    /* pending geometry retry notice */
    if (structureViewerState.pendingGeometryRetry) {
      var pendingNotice = document.createElement("div");
      pendingNotice.className = "sv-notice sv-notice-pending";
      pendingNotice.textContent = _t("structure.pending_fetch", STR.PENDING_FETCH);
      inspBody.appendChild(pendingNotice);
    }

    /* find selected entry */
    var entry = null;
    for (var i = 0; i < entries.length; i++) {
      if (entries[i].id === selectedId) { entry = entries[i]; break; }
    }

    if (!entry) {
      var noEntry = document.createElement("div");
      noEntry.className = "sv-inspector-value sv-muted";
      noEntry.textContent = _t("structure.no_entry", STR.NO_ENTRY);
      inspBody.appendChild(noEntry);
      return;
    }

    /* status section */
    inspBody.appendChild(_inspectorSection(_t("structure.status", STR.STATUS),
      entry.status === "completed" ? _t("structure.completed", STR.COMPLETED) : _t("structure.failed", STR.FAILED)));

    /* source section */
    var sourceKind = (entry.source && entry.source.kind) || "";
    var sourceFallback = STR.SOURCE_KINDS[sourceKind] || _esc(sourceKind);
    var sourceLabel = sourceKind ? _t("structure.source_kind." + sourceKind, sourceFallback) : sourceFallback;
    inspBody.appendChild(_inspectorSection(_t("structure.source", STR.SOURCE), sourceLabel));

    /* energy section */
    if (entry.energy && entry.energy.value != null) {
      var energyDiv = document.createElement("div");
      energyDiv.className = "sv-inspector-section";

      var lbl = document.createElement("div");
      lbl.className = "sv-inspector-label";
      lbl.textContent = _t("structure.energy", STR.ENERGY);
      energyDiv.appendChild(lbl);

      var valRow = document.createElement("div");
      valRow.className = "sv-inspector-energy";
      var valSpan = document.createElement("span");
      valSpan.className = "sv-inspector-energy-value";
      valSpan.textContent = entry.energy.value.toFixed(6);
      valRow.appendChild(valSpan);
      var unitSpan = document.createElement("span");
      unitSpan.className = "sv-inspector-energy-unit";
      unitSpan.textContent = _esc(entry.energy.unit || "hartree");
      valRow.appendChild(unitSpan);
      energyDiv.appendChild(valRow);

      if (entry.energy.kind) {
        var kindSpan = document.createElement("div");
        kindSpan.className = "sv-inspector-value sv-muted";
        kindSpan.textContent = _esc(entry.energy.kind);
        energyDiv.appendChild(kindSpan);
      }

      if (entry.energy.temperature_k != null) {
        var tempSpan = document.createElement("div");
        tempSpan.className = "sv-inspector-value sv-muted";
        tempSpan.textContent = "T = " + entry.energy.temperature_k + " K";
        energyDiv.appendChild(tempSpan);
      }

      inspBody.appendChild(energyDiv);
    }

    /* delta E */
    if (entry.relative_energy_kcal != null) {
      var deltaDiv = document.createElement("div");
      deltaDiv.className = "sv-inspector-section";
      var deltaLbl = document.createElement("div");
      deltaLbl.className = "sv-inspector-label";
      deltaLbl.textContent = _t("structure.delta_e", STR.DELTA_E);
      deltaDiv.appendChild(deltaLbl);
      var deltaVal = document.createElement("div");
      deltaVal.className = "sv-inspector-delta";
      deltaVal.textContent = entry.relative_energy_kcal.toFixed(2) + " kcal/mol";
      deltaDiv.appendChild(deltaVal);
      inspBody.appendChild(deltaDiv);
    }

    /* boltzmann weight */
    if (entry.boltzmann_weight != null) {
      var weightDiv = document.createElement("div");
      weightDiv.className = "sv-inspector-section";
      var weightLbl = document.createElement("div");
      weightLbl.className = "sv-inspector-label";
      weightLbl.textContent = _t("structure.weight", STR.WEIGHT);
      weightDiv.appendChild(weightLbl);
      var weightVal = document.createElement("div");
      weightVal.className = "sv-inspector-value";
      weightVal.textContent = (entry.boltzmann_weight * 100).toFixed(1) + "%";
      weightDiv.appendChild(weightVal);
      var weightBar = document.createElement("div");
      weightBar.className = "sv-inspector-weight-bar";
      var weightFill = document.createElement("div");
      weightFill.className = "sv-inspector-weight-bar-fill";
      weightFill.style.width = Math.round(entry.boltzmann_weight * 100) + "%";
      weightBar.appendChild(weightFill);
      weightDiv.appendChild(weightBar);
      inspBody.appendChild(weightDiv);
    }

    /* vibrations — delegated to ACPVibrationViewer (Wave 5, todo 27) */
    var vibDiv = document.createElement("div");
    vibDiv.className = "sv-inspector-section";
    var vibLbl = document.createElement("div");
    vibLbl.className = "sv-inspector-label";
    vibLbl.textContent = _t("structure.vibrations", STR.VIBRATIONS);
    vibDiv.appendChild(vibLbl);
    var vibContainer = document.createElement("div");
    vibContainer.id = "structure-inspector-vibrations";
    vibDiv.appendChild(vibContainer);
    inspBody.appendChild(vibDiv);

    if (entry.vibrations && entry.vibrations.available !== false) {
      /* contract: fetch the authoritative answer; never fabricate locally */
      if (typeof window !== "undefined" && window.ACPVibrationViewer &&
          typeof window.ACPVibrationViewer.loadVibrations === "function") {
        var vibOpts = {};
        if (entry.vibrations.endpoint) {
          vibOpts.endpoint = entry.vibrations.endpoint;
        }
        window.ACPVibrationViewer.loadVibrations(structureViewerState.jobId, entry.id, vibOpts);
      }
    } else {
      /* contract: available=false -> local text only, NO fetch */
      var vibNone = document.createElement("div");
      vibNone.className = "sv-inspector-value sv-muted";
      vibNone.textContent = _t("structure.vib.none", STR.VIB_NONE);
      vibContainer.appendChild(vibNone);
    }

    /* measurements placeholder — disabled with a note while an overlay is
       active but its atom mapping is unproven (todo 40 clearing rule) */
    var measDiv = document.createElement("div");
    measDiv.className = "sv-inspector-section";
    if (overlayMeasurementsBlocked()) {
      measDiv.className += " sv-measurements-blocked";
    }
    var measLbl = document.createElement("div");
    measLbl.className = "sv-inspector-label";
    measLbl.textContent = _t("structure.measurements", STR.MEASUREMENTS);
    measDiv.appendChild(measLbl);
    var measVal = document.createElement("div");
    measVal.className = "sv-inspector-value sv-muted";
    measVal.textContent = overlayMeasurementsBlocked()
      ? _t("structure.overlay.unproven", STR.OVERLAY_UNPROVEN)
      : _t("structure.measurements_placeholder", STR.MEASUREMENTS_PLACEHOLDER);
    measDiv.appendChild(measVal);
    inspBody.appendChild(measDiv);

    _renderOverlaySection(inspBody);

    /* geometry edit panel (todo 36): provenance + dirty badge +
       transaction controls + dirty-switch prompt + collision warnings */
    inspBody.appendChild(_renderEditPanel());

    /* warnings */
    var warnings = (payload && payload.warnings) || [];
    if (warnings.length > 0) {
      var warnDiv = document.createElement("div");
      warnDiv.className = "sv-inspector-section";
      var warnLbl = document.createElement("div");
      warnLbl.className = "sv-inspector-label";
      warnLbl.textContent = _t("structure.warnings", STR.WARNINGS);
      warnDiv.appendChild(warnLbl);
      var warnList = document.createElement("ul");
      warnList.className = "sv-warning-list";
      for (var wi = 0; wi < warnings.length; wi++) {
        var warnItem = document.createElement("li");
        warnItem.className = "sv-warning-item";
        warnItem.textContent = _esc(warnings[wi]);
        warnList.appendChild(warnItem);
      }
      warnDiv.appendChild(warnList);
      inspBody.appendChild(warnDiv);
    }

    _renderPlaybackBar();
  }

  function _inspectorSection(label, value) {
    var div = document.createElement("div");
    div.className = "sv-inspector-section";
    var lbl = document.createElement("div");
    lbl.className = "sv-inspector-label";
    lbl.textContent = _esc(label);
    div.appendChild(lbl);
    var val = document.createElement("div");
    val.className = "sv-inspector-value";
    val.textContent = _esc(value);
    div.appendChild(val);
    return div;
  }

  /**
   * Geometry edit panel (todo 36): bond provenance, dirty badge,
   * undo/redo/reset transaction controls, collision warnings from the
   * last transaction, and the dirty-switch prompt.  Degrades to the
   * placeholder text when the editor module is absent.
   */
  function _renderEditPanel() {
    var div = document.createElement("div");
    div.className = "sv-inspector-section sv-edit-panel";
    var lbl = document.createElement("div");
    lbl.className = "sv-inspector-label";
    lbl.textContent = _t("structure.edit.title", STR.EDIT_TITLE);
    div.appendChild(lbl);

    var ed = (typeof window !== "undefined" && window.ACPStructureEditor)
      ? window.ACPStructureEditor
      : null;
    if (!ed || typeof ed.isDirty !== "function") {
      var na = document.createElement("div");
      na.className = "sv-inspector-value sv-muted";
      na.textContent = _t("structure.edit_placeholder", STR.EDIT_PLACEHOLDER);
      div.appendChild(na);
      return div;
    }

    var body = document.createElement("div");
    body.id = "structure-inspector-edit";

    var prov = ed.editorState && ed.editorState.provenance;
    if (!prov && typeof ed.buildGraphFromCurrentEntry === "function" && !ed.isLocked()) {
      try { ed.buildGraphFromCurrentEntry(); } catch (_) { /* graph build is best-effort */ }
      prov = ed.editorState.provenance;
    }
    if (prov) {
      var provLine = document.createElement("div");
      provLine.className = "sv-inspector-value sv-edit-provenance";
      provLine.textContent = _t("structure.edit.bond_source", STR.BOND_SOURCE) + ": " + prov;
      body.appendChild(provLine);
    }

    var row = document.createElement("div");
    row.className = "sv-edit-row";
    if (ed.isDirty()) {
      var badge = document.createElement("span");
      badge.className = "sv-badge sv-badge-dirty";
      badge.textContent = _t("structure.edit.dirty", STR.EDIT_DIRTY);
      row.appendChild(badge);
    }
    row.appendChild(_editBtn("structure.edit.undo", STR.EDIT_UNDO, function () { ed.undoEdit(); }));
    row.appendChild(_editBtn("structure.edit.redo", STR.EDIT_REDO, function () { ed.redoEdit(); }));
    row.appendChild(_editBtn("structure.edit.reset", STR.EDIT_RESET, function () { ed.resetEdits(); }));
    body.appendChild(row);

    /* export / save / new-calculation handoff (todo 37) */
    var actionsRow = document.createElement("div");
    actionsRow.className = "sv-edit-row";
    actionsRow.appendChild(_editBtn("structure.edit.export_xyz", STR.EDIT_EXPORT, function () {
      if (typeof ed.exportEditedXyz === "function") { ed.exportEditedXyz(); }
    }));
    actionsRow.appendChild(_editBtn("structure.edit.save_asset", STR.EDIT_SAVE_ASSET, function () {
      if (typeof ed.saveEditedAsset === "function") { ed.saveEditedAsset(); }
    }));
    actionsRow.appendChild(_editBtn("structure.edit.new_calc", STR.EDIT_NEW_CALC, function () {
      if (typeof ed.prefillNewCalculation === "function") { ed.prefillNewCalculation(); }
    }));
    body.appendChild(actionsRow);
    if (ed.editorState && ed.editorState.savedAssetId) {
      var savedLine = document.createElement("div");
      savedLine.className = "sv-inspector-value sv-muted";
      savedLine.textContent = _t("structure.edit.saved", STR.EDIT_SAVED);
      body.appendChild(savedLine);
    }
    if (ed.editorState && ed.editorState.saveError) {
      var errLine = document.createElement("div");
      errLine.className = "sv-edit-collision";
      errLine.textContent = _t("structure.edit.save_error", STR.EDIT_SAVE_ERROR) +
        ": " + ed.editorState.saveError;
      body.appendChild(errLine);
    }

    var txns = (ed.editorState && ed.editorState.transactions) || [];
    var last = txns.length ? txns[txns.length - 1] : null;
    if (last && last.collision_warnings && last.collision_warnings.length) {
      for (var ci = 0; ci < last.collision_warnings.length; ci++) {
        var cw = last.collision_warnings[ci];
        var line = document.createElement("div");
        line.className = "sv-edit-collision";
        line.textContent = _t("structure.edit.collision", STR.EDIT_COLLISION) +
          ": atoms " + _esc(cw.a) + "-" + _esc(cw.b) +
          " @ " + cw.distance.toFixed(2) + " \u00c5";
        body.appendChild(line);
      }
    }

    if (ed.editorState && ed.editorState.pendingSwitch) {
      var prompt = document.createElement("div");
      prompt.className = "sv-edit-prompt";
      var ptxt = document.createElement("div");
      ptxt.className = "sv-inspector-value";
      ptxt.textContent = _t("structure.edit.dirty", STR.EDIT_DIRTY);
      prompt.appendChild(ptxt);
      var prow = document.createElement("div");
      prow.className = "sv-edit-row";
      prow.appendChild(_editBtn("structure.edit.discard", STR.EDIT_DISCARD,
        function () { ed.confirmSwitch("discard"); }));
      prow.appendChild(_editBtn("structure.edit.save_as", STR.EDIT_SAVE_AS,
        function () { ed.confirmSwitch("save"); }));
      prow.appendChild(_editBtn("structure.edit.cancel", STR.EDIT_CANCEL,
        function () { ed.confirmSwitch("cancel"); }));
      prompt.appendChild(prow);
      body.appendChild(prompt);
    }

    div.appendChild(body);
    return div;
  }

  function _editBtn(key, fallback, handler) {
    var btn = document.createElement("button");
    btn.setAttribute("type", "button");
    btn.className = "sv-edit-btn";
    btn.textContent = _t(key, fallback);
    btn.addEventListener("click", handler);
    return btn;
  }

  /* ---- drawer toggles ---- */

  function toggleListDrawer() {
    if (typeof document === "undefined") return;
    var panel = document.getElementById("structure-list-panel");
    var overlay = document.getElementById("sv-drawer-overlay");
    if (!panel) return;
    var isOpen = panel.classList.contains("sv-drawer-open");
    panel.classList.toggle("sv-drawer-open", !isOpen);
    if (overlay) overlay.classList.toggle("sv-drawer-open", !isOpen);
  }

  function toggleInspectorDrawer() {
    if (typeof document === "undefined") return;
    var panel = document.getElementById("structure-inspector-panel");
    var overlay = document.getElementById("sv-drawer-overlay");
    if (!panel) return;
    var isOpen = panel.classList.contains("sv-drawer-open");
    panel.classList.toggle("sv-drawer-open", !isOpen);
    if (overlay) overlay.classList.toggle("sv-drawer-open", !isOpen);
  }

  function closeAllDrawers() {
    if (typeof document === "undefined") return;
    var list = document.getElementById("structure-list-panel");
    var insp = document.getElementById("structure-inspector-panel");
    var overlay = document.getElementById("sv-drawer-overlay");
    if (list) list.classList.remove("sv-drawer-open");
    if (insp) insp.classList.remove("sv-drawer-open");
    if (overlay) overlay.classList.remove("sv-drawer-open");
  }

  /* ---- energy-graph → structure-viewer push (one-way, phase A) ---- */

  /**
   * Map an energy-graph node descriptor to a structure-viewer entry id.
   *
   * Accepts either:
   *   - { entryId: "conf_0001" }        (ready-made id)
   *   - { kind: "conformer", key: "0001" }  → "conf_0001"
   *   - { kind: "batch", key: "opt_item_001" }  → "batch_opt_item_001"
   *   - { kind: "scan", frameIndex: 5 } → "scan_frame_5"
   *   - { kind: "pes", candidateId: "ts_frame_005" } → "pes_ts_frame_005"
   *   - { kind: "optimization", frameIndex: 12 } → null (transient)
   *   - { kind: "simple", stepKind: "optimize" } → "simple_optimize"
   *
   * @param {Object} meta
   * @returns {string|null} entry id or null for transient
   */
  function _entryIdFromEnergyNode(meta) {
    if (!meta) return null;
    if (meta.entryId) return meta.entryId;

    var kind = meta.kind || "";
    var key = meta.key || "";

    if (kind === "conformer") {
      return "conf_" + (key || String(meta.frameIndex || ""));
    }
    if (kind === "batch") {
      return "batch_" + key;
    }
    if (kind === "scan") {
      return "scan_frame_" + String(meta.frameIndex != null ? meta.frameIndex : key);
    }
    if (kind === "pes") {
      return "pes_" + (meta.candidateId || key);
    }
    if (kind === "simple") {
      return "simple_" + (meta.stepKind || key);
    }
    if (kind === "irc") {
      return "irc_" + (meta.endpoint || "") + "_" + String(meta.frameIndex != null ? meta.frameIndex : 0);
    }
    /* optimization frames are transient — no stable entry id */
    if (kind === "optimization") {
      return null;
    }
    /* fallback: treat key as raw entry id */
    return key || null;
  }

  /**
   * Called from the energy/trajectory viewer when a node is selected.
   * Shared selection ownership (phase D, todo 38): the push goes through
   * selectEntry, so energy-graph picks and structure-list picks share the
   * single selectionToken. Still one-way visually — see selectEntry.
   *
   * @param {string} jobId  - current job id (stale-job guard)
   * @param {Object} entryMeta - node descriptor from energy viewer
   *   (see _entryIdFromEnergyNode for accepted shapes)
   * @returns {number|null} selectionToken or null if ignored
   */
  function onEnergyNodeSelected(jobId, entryMeta) {
    var state = structureViewerState;

    /* Stale-job guard: ignore if the energy viewer is reporting for a
       different job than the structure viewer currently shows. */
    if (!state.jobId || state.jobId !== jobId) {
      return null;
    }

    var entryId = _entryIdFromEnergyNode(entryMeta);
    var capturedToken = state.selectionToken;

    if (entryId) {
      /* Try to find the entry in the current payload */
      var entries = (state.payload && state.payload.entries) || [];
      var found = false;
      for (var i = 0; i < entries.length; i++) {
        if (entries[i].id === entryId) { found = true; break; }
      }

      if (found) {
        /* Entry exists in catalog — select it and load geometry */
        return selectEntry(entryId, "energy_graph");
      }
    }

    /* Entry not in catalog (e.g. optimization trajectory frame not yet
       in the structure catalog).  Create a transient entry so the
       structure viewer can still highlight and load geometry. */
    if (!entryId && entryMeta) {
      /* For optimization frames, synthesize a transient id */
      if (entryMeta.kind === "optimization" && entryMeta.frameIndex != null) {
        entryId = "transient_opt_" + String(entryMeta.frameIndex);
      } else if (entryMeta.frameIndex != null) {
        entryId = "transient_frame_" + String(entryMeta.frameIndex);
      }
    }

    if (!entryId) return null;

    /* Build a transient entry */
    var transient = {
      id: entryId,
      group_id: "__transient",
      label: entryMeta.label || entryId,
      role: "minimum",
      status: "completed",
      geometry: entryMeta.geometryEndpoint ? {
        endpoint: entryMeta.geometryEndpoint,
        format: "xyz",
      } : null,
      source: { kind: "last_valid_cycle", geometry_ref: entryMeta.geometryRef || "" },
      badges: [],
      vibrations: { available: false },
    };

    /* Inject into payload if not already present */
    if (!state.payload) {
      state.payload = {
        schema_version: "structure_viewer_v1",
        job_id: jobId,
        workflow: "",
        job_status: "",
        availability: "ready",
        revision: null,
        default_entry_id: entryId,
        groups: [],
        entries: [transient],
        warnings: [],
      };
    } else {
      var existingEntries = state.payload.entries || [];
      var dup = false;
      for (var j = 0; j < existingEntries.length; j++) {
        if (existingEntries[j].id === entryId) { dup = true; break; }
      }
      if (!dup) {
        existingEntries.push(transient);
        state.payload.entries = existingEntries;
      }
    }

    return selectEntry(entryId, "energy_graph");
  }

  /* ---- public namespace ---- */
  window.ACPStructureViewer = {
    version: VERSION,
    state: structureViewerState,
    STR: STR,
    geometryStore: geometryStore,
    sharedLoadGeometry: sharedLoadGeometry,
    registerCanvasLoader: registerCanvasLoader,
    saveCamera: saveCamera,
    restoreCamera: restoreCamera,
    setStylePreset: setStylePreset,
    getStylePreset: getStylePreset,
    playIrcPath: playIrcPath,
    stopIrcPlayback: stopIrcPlayback,
    isIrcPlaying: isIrcPlaying,
    overlayState: overlayState,
    loadOverlay: loadOverlay,
    renderOverlay: renderOverlay,
    clearOverlay: clearOverlay,
    overlayMeasurementsBlocked: overlayMeasurementsBlocked,
    loadStructureViewer: loadStructureViewer,
    onJobSelected: onJobSelected,
    onEnergyNodeSelected: onEnergyNodeSelected,
    selectEntry: selectEntry,
    refreshIfChanged: refreshIfChanged,
    loadSelectedGeometry: loadSelectedGeometry,
    injectManualEntry: injectManualEntry,
    renderStructureViewer: renderStructureViewer,
    renderInspector: renderInspector,
    toggleListDrawer: toggleListDrawer,
    toggleInspectorDrawer: toggleInspectorDrawer,
    closeAllDrawers: closeAllDrawers,
    _applyCatalogResponse: _applyCatalogResponse,
    _esc: _esc,
    _t: _t,
    _sha256hex: _sha256hex,
    _manualEntryId: _manualEntryId,
    _entryIdFromEnergyNode: _entryIdFromEnergyNode,
    _parseXyzFirstFrame: _parseXyzFirstFrame,
    _resolveStyleSpec: _resolveStyleSpec,
    _fetchImpl: (typeof window !== "undefined" && window.fetch) ? window.fetch.bind(window) : null,
  };
})();
