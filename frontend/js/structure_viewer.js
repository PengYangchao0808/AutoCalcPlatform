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
 *   - virtualizeEntries/sampleFrames (perf thresholds, todo 41)
 *   - saveViewState/restoreViewState/clearViewState (todo 42)
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

  var VERSION = "0.14.0";

  /* ---- performance thresholds (todo 41; the ONLY degradation knobs) ---- */
  var LIST_VIRTUALIZE_THRESHOLD = 100;   /* entry-list windowing above this */
  var TRAJECTORY_SAMPLE_THRESHOLD = 500; /* frame-list sampling above this */
  var TRAJECTORY_SAMPLE_TARGET = 200;    /* down-sample target count */
  var LARGE_SYSTEM_ATOM_THRESHOLD = 200; /* per-load wireframe default above */
  var _LIST_WINDOW = 50;                 /* head+tail window size */

  /* ---- user-visible strings (zh fallback; primary source is I18N dict via _t()) ---- */
  var STR = {
    NEWER_AVAILABLE: "\u6709\u65b0\u7ed3\u6784\u53ef\u7528",       // 有新结构可用
    PENDING_FETCH: "\u7b49\u5f85\u8fdc\u7a0b\u7ed3\u679c",         // 等待远程结果
    PENDING_RETRY: "\u8fdc\u7a0b\u6587\u4ef6\u5c1a\u672a\u540c\u6b65\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5", // 远程文件尚未同步，请稍后重试
    NO_ENTRY: "\u672a\u9009\u62e9\u7ed3\u6784",                     // 未选择结构
    NO_ENTRY_HINT: "\u8bf7\u4ece\u5de6\u4fa7\u5217\u8868\u9009\u62e9\u4e00\u4e2a\u7ed3\u6784", // 请从左侧列表选择一个结构
    REFRESH: "\u5237\u65b0",                                         // 刷新
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
    PERF_DEGRADE_NOTICE: "\u5927\u4f53\u7cfb\u6a21\u5f0f\uff1a\u5df2\u5207\u6362\u7ebf\u6846\u6837\u5f0f\u5e76\u5173\u95ed\u6807\u7b7e (>200 \u539f\u5b50)", // 大体系模式：已切换线框样式并关闭标签 (>200 原子)
    PERF_PARTIAL_LIST: "\u2026\u663e\u793a\u90e8\u5206",                   // …显示部分
    PERF_SAMPLED: "\u5df2\u62bd\u6837\u663e\u793a",                         // 已抽样显示
    VIEW_RESTORED_MEASUREMENTS: "\u6062\u590d\u7684\u6d4b\u91cf",           // 恢复的测量
    SWITCHER_CONFORMER: "\u6784\u8c61",                                       // 构象
    SWITCHER_CANDIDATE: "\u5019\u9009",                                       // 候选
    SWITCHER_BATCH_ITEM: "\u9879\u76ee",                                      // 项目
    SWITCHER_UNCONFIRMED: "\u672a\u786e\u8ba4",                              // 未确认
    SWITCHER_FILTER_PLACEHOLDER: "\u7b5b\u9009\u2026",                        // 筛选…
    SWITCHER_HAS_IMAG: "\u6709\u865a\u9891",                                  // 有虚频
    TOGGLE_RESULT: "\u7ed3\u679c",                                             // 结果
    TOGGLE_INPUT: "\u521d\u59cb",                                               // 初始
    OVERLAY_COMPARE: "\u53e0\u5408\u6bd4\u8f83",                              // 叠合比较
    STRIP_TOGGLE_SHOW: "\u6784\u8c61\u6761",                                  // 构象条
    SUMMARY_BAR: "\u7ed3\u679c\u6458\u8981",                                  // 结果摘要
    INPUT_RESULT_TOGGLE: "\u8f93\u5165/\u7ed3\u679c\u5207\u6362",             // 输入/结果切换
    STRIP_LABEL: "\u6784\u8c61\u5217\u8868",                                  // 构象列表
    SWITCHER_LABEL: "\u5207\u6362\u6761\u76ee",                               // 切换条目
    DRAWER_CLOSE: "\u5173\u95ed",                                              // 关闭
    DRAWER_MORE: "\u66f4\u591a\u64cd\u4f5c",                                  // 更多操作
    VIB_BTN: "\u632f\u52a8\u6a21\u5f0f",                                       // 振动模式
    VIB_IMAG_BTN: "\u865a\u9891 {count}",                                      // 虚频 {count}
    MEASURE_BTN: "\u6d4b\u91cf",                                               // 测量
    MEASURE_TYPE_DISTANCE: "\u8ddd\u79bb",                                     // 距离
    MEASURE_TYPE_ANGLE: "\u89d2\u5ea6",                                         // 角度
    MEASURE_TYPE_DIHEDRAL: "\u4e8c\u9762\u89d2",                               // 二面角
    MEASURE_APPLY: "\u5e94\u7528\u4fee\u6539",                                 // 应用修改
    MEASURE_APPLIED: "\u5df2\u5e94\u7528",                                     // 已应用
    MEASURE_RANGE_WARN: "\u503c\u8d85\u51fa\u8303\u56f4",                       // 值超出范围
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
    restoredMeasurements: null,
    _abortController: null,
  };

  var _activeSwitcherDropdown = null;
  var _activeSwitcherOverlay = null;
  var _stripExpanded = false;
  var _activeDrawerId = null;
  var _drawerTrigger = null;
  var _switcherActiveIdx = -1;
  var _switcherEntries = [];
  var _switcherAnchor = null;
  var _drawerContentCache = {};
  var _svMeasureAppliedIds = {};

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
    structureViewerState.restoredMeasurements = null;

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
    saveViewState(); /* persist the outgoing entry's view before switching */
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
    _syncStripActive();
    stopIrcPlayback();
    clearOverlay();
    closeAllDrawers();
    loadSelectedGeometry();
    var savedDrawer = _getDrawerPersist(structureViewerState.jobId, entryId);
    if (savedDrawer) {
      _defer(function () { openDrawer(savedDrawer); });
    }
    if (typeof window !== "undefined" && typeof window._energyGraphSyncFromStructure === "function") {
      try { window._energyGraphSyncFromStructure(entryId); } catch (_) { /* sync is best-effort */ }
    }
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
    /* true once the user explicitly picks a preset — large-system degrade
       then never overrides their choice (todo 41) */
    userPresetChosen: false,
    /* true when the LAST shared load exceeded LARGE_SYSTEM_ATOM_THRESHOLD
       and was degraded to wireframe (drives the visible notice) */
    lastLoadDegraded: false,
  };

  /* tracks the main-canvas degrade state so a following SMALL load restores
     the store preset exactly once (never touched for small-only sessions) */
  var _mainStyleDegraded = false;

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

    var parsed = _parseXyzFirstFrame(xyzText);
    var atomCount = parsed ? parsed.coords.length : 0;
    /* large-system default (todo 41): per-load wireframe override — never
       persisted as the user's global choice, never applied when the user
       explicitly picked a preset */
    var degraded = atomCount > LARGE_SYSTEM_ATOM_THRESHOLD && !geometryStore.userPresetChosen;
    geometryStore.lastLoadDegraded = degraded;

    var styleSpec = _resolveStyleSpec(degraded ? "wireframe" : geometryStore.stylePreset);
    var loaded = false;
    try {
      loaded = !!_canvasLoaders[canvasId](xyzText, styleSpec);
    } catch (_) {
      loaded = false;
    }

    if (canvasId === "main") {
      structureViewerState.displayedCoords = parsed ? parsed.coords : null;
      structureViewerState.displayedSymbols = parsed ? parsed.symbols : null;
      structureViewerState.displayedEntryId = structureViewerState.selectedEntryId;
      /* the main bridge applies molDoc.style internally — re-apply the
         per-load preset AFTER the load; restore exactly once when a degrade
         is followed by a small system (small-only sessions untouched) */
      if (!geometryStore.userPresetChosen) {
        if (degraded) {
          _applyPresetSafe("wireframe");
          _mainStyleDegraded = true;
        } else if (_mainStyleDegraded) {
          _applyPresetSafe(geometryStore.stylePreset);
          _mainStyleDegraded = false;
        }
      }
    }
    if (typeof document !== "undefined") _renderPlaybackBar();
    return { loaded: loaded, parsed: parsed };
  }

  function _applyPresetSafe(name) {
    if (typeof applyStylePreset === "function" && _canvasViewer("main")) {
      try { applyStylePreset(name); } catch (_) { /* empty viewer */ }
    }
  }

  /**
   * Window a long entry list for rendering (todo 41).
   *
   * v1 simplification: fixed head+tail windows (no scroll-position math).
   * Selected/default entries are force-included even outside the windows.
   *
   * @param {Array} entries
   * @param {number} threshold
   * @param {Array<string>} [forceIds] - entry ids to force-include
   * @returns {{ visible: Array, total: number, windowed: boolean }}
   */
  function virtualizeEntries(entries, threshold, forceIds) {
    var list = entries || [];
    var total = list.length;
    if (total <= threshold) {
      return { visible: list.slice(), total: total, windowed: false };
    }
    var head = list.slice(0, _LIST_WINDOW);
    var tail = list.slice(Math.max(_LIST_WINDOW, total - _LIST_WINDOW));
    var forced = [];
    var ids = forceIds || [];
    for (var f = 0; f < ids.length; f++) {
      var wantId = ids[f];
      if (wantId == null) continue;
      var covered = false;
      for (var h = 0; h < head.length; h++) { if (head[h].id === wantId) { covered = true; break; } }
      if (!covered) {
        for (var t = 0; t < tail.length; t++) { if (tail[t].id === wantId) { covered = true; break; } }
      }
      if (covered) continue;
      for (var e = 0; e < list.length; e++) {
        if (list[e].id === wantId) { forced.push(list[e]); break; }
      }
    }
    return { visible: head.concat(forced, tail), total: total, windowed: true };
  }

  /**
   * Evenly down-sample a long frame list (todo 41).  Above the threshold,
   * keep every stride-th frame (stride = ceil(len/target)) with the FIRST
   * and LAST frames always kept; the tail slot is replaced by the true last
   * frame so the count never exceeds the target.  At or below the threshold
   * the input array is returned UNCHANGED (same reference).
   *
   * @param {Array} frames
   * @param {number} threshold
   * @param {number} target
   * @returns {Array}
   */
  function sampleFrames(frames, threshold, target) {
    var list = frames || [];
    if (list.length <= threshold) return list;
    var stride = Math.ceil(list.length / target);
    var out = [];
    for (var i = 0; i < list.length; i += stride) out.push(list[i]);
    if (out.length && out[out.length - 1] !== list[list.length - 1]) {
      out[out.length - 1] = list[list.length - 1];
    }
    return out;
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
    /* explicit user choice — large-system degrade never overrides it */
    geometryStore.userPresetChosen = true;
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
    sampledTotal: 0,
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
    if (typeof activeViewerTab !== "undefined" && activeViewerTab !== "structure") {
      stopIrcPlayback();
      return;
    }
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
    /* trajectory sampling (todo 41): huge IRC paths play the sampled set —
       stepping 500+ DOM frames defeats the purpose of the threshold */
    var totalEntries = entries.length;
    if (entries.length > TRAJECTORY_SAMPLE_THRESHOLD) {
      entries = sampleFrames(entries, TRAJECTORY_SAMPLE_THRESHOLD, TRAJECTORY_SAMPLE_TARGET);
    }
    ircPlayback.sampledTotal = entries.length < totalEntries ? totalEntries : 0;
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
    ircPlayback.sampledTotal = 0;
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
    btn.setAttribute("aria-label", _t(key, fallback));
    btn.className = "sv-edit-btn";
    btn.textContent = _t(key, fallback);
    btn.addEventListener("click", handler);
    return btn;
  }

  function _renderPlaybackBar() {
    if (typeof document === "undefined") return;
    var bar = document.getElementById("structure-playback-bar");
    if (!bar) return;
    var degraded = !!geometryStore.lastLoadDegraded;
    if (!degraded) { bar.style.display = "none"; bar.textContent = ""; return; }
    bar.style.display = "";
    bar.textContent = "";
    var degradeNotice = document.createElement("span");
    degradeNotice.className = "sv-notice sv-notice-degrade";
    degradeNotice.setAttribute("aria-live", "polite");
    degradeNotice.textContent = _t("structure.perf.degrade_notice", STR.PERF_DEGRADE_NOTICE);
    bar.appendChild(degradeNotice);
    _renderIrcFrameController();
  }

  function _renderIrcFrameController() {
    if (typeof document === "undefined") return;
    var controller = document.getElementById("frame-controller");
    if (!controller) return;
    var fwdEntries = _ircEntries("forward");
    var revEntries = _ircEntries("reverse");
    var hasIrc = fwdEntries.length > 0 || revEntries.length > 0;
    if (!hasIrc) return;

    controller.style.display = "flex";
    controller.innerHTML = "";

    var title = document.createElement("span");
    title.className = "sv-playback-title";
    title.style.fontSize = "11px";
    title.style.color = "var(--sv-text-tertiary)";
    title.textContent = _t("structure.irc.title", STR.IRC_TITLE);
    controller.appendChild(title);

    if (ircPlayback.playing) {
      var status = document.createElement("span");
      status.className = "frame-info";
      status.textContent = _t("structure.irc.playing", STR.IRC_PLAYING);
      controller.appendChild(status);
    }

    var prevBtn = document.createElement("button");
    prevBtn.className = "frame-btn";
    prevBtn.innerHTML = '<svg viewBox="0 0 20 20" width="14" height="14" stroke="currentColor" fill="none" stroke-width="2"><path d="M12 4L6 10L12 16"/></svg>';
    prevBtn.addEventListener("click", function () {
      if (ircPlayback.playing) {
        stopIrcPlayback();
      }
    });
    controller.appendChild(prevBtn);

    var info = document.createElement("span");
    info.className = "frame-info";
    if (ircPlayback.playing && !ircPlayback.loading) {
      info.textContent = Math.min(ircPlayback.frameIndex + 1, ircPlayback.frames.length) + " / " + ircPlayback.frames.length;
    } else if (ircPlayback.loading) {
      info.textContent = _t("structure.irc.loading", STR.IRC_LOADING);
    } else {
      var totalIrcFrames = fwdEntries.length + revEntries.length;
      info.textContent = "0 / " + totalIrcFrames;
    }
    controller.appendChild(info);

    var nextBtn = document.createElement("button");
    nextBtn.className = "frame-btn";
    nextBtn.innerHTML = '<svg viewBox="0 0 20 20" width="14" height="14" stroke="currentColor" fill="none" stroke-width="2"><path d="M8 4L14 10L8 16"/></svg>';
    nextBtn.addEventListener("click", function () {
      if (ircPlayback.playing) {
        stopIrcPlayback();
      }
    });
    controller.appendChild(nextBtn);

    if (ircPlayback.playing) {
      var stopBtn = document.createElement("button");
      stopBtn.className = "frame-btn";
      stopBtn.textContent = _t("structure.irc.stop", STR.IRC_STOP);
      stopBtn.addEventListener("click", function () { stopIrcPlayback(); });
      controller.appendChild(stopBtn);
    } else {
      if (fwdEntries.length > 0) {
        var fwdBtn = document.createElement("button");
        fwdBtn.className = "frame-btn";
        fwdBtn.textContent = _t("structure.irc.play_forward", STR.IRC_PLAY_FORWARD);
        fwdBtn.addEventListener("click", function () { playIrcPath("forward"); });
        controller.appendChild(fwdBtn);
      }
      if (revEntries.length > 0) {
        var revBtn = document.createElement("button");
        revBtn.className = "frame-btn";
        revBtn.textContent = _t("structure.irc.play_reverse", STR.IRC_PLAY_REVERSE);
        revBtn.addEventListener("click", function () { playIrcPath("reverse"); });
        controller.appendChild(revBtn);
      }
    }

    var slider = document.createElement("input");
    slider.type = "range";
    slider.className = "frame-slider";
    slider.min = "0";
    slider.max = String(Math.max(ircPlayback.frames.length - 1, 0));
    slider.value = String(Math.max(ircPlayback.frameIndex - 1, 0));
    slider.disabled = !ircPlayback.playing || ircPlayback.loading;
    controller.appendChild(slider);
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
    _ariaGroup(div, _t("structure.overlay.title", STR.OVERLAY_TITLE));
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
        restoreViewState(state.jobId, entry.id);
        if (structureViewerState.restoredMeasurements && typeof document !== "undefined") {
          renderInspector();
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
    saveViewState();
    stopIrcPlayback();
    clearOverlay();
    closeAllDrawers();
    if (typeof window !== "undefined" && window.ACPVibrationViewer &&
        typeof window.ACPVibrationViewer.handleTeardown === "function") {
      window.ACPVibrationViewer.handleTeardown();
    }
    _stripExpanded = _getStripExpanded(jobId);
    return loadStructureViewer(jobId, opts)
      .then(function () {
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

  /* ---- view-state persistence (todo 42) ---- */

  /**
   * localStorage namespace for per-job+entry VIEW state only (camera, style
   * preset, measurements).  NEVER results/manifests — the result tree stays
   * the single authority for data; this store holds presentation state.
   */
  var viewStateStore = {
    NS: "acp.sv.view.",
    VERSION: 1,
  };

  function _getStorage() {
    if (typeof window !== "undefined" && window.ACPStructureViewer &&
        window.ACPStructureViewer._storageImpl) {
      return window.ACPStructureViewer._storageImpl;
    }
    try {
      return (typeof window !== "undefined" && window.localStorage) || null;
    } catch (_) {
      return null;
    }
  }

  function _viewKey(jobId, entryId) {
    return viewStateStore.NS + jobId + ":" + entryId;
  }

  function _storageGet(key) {
    var storage = _getStorage();
    if (!storage || typeof storage.getItem !== "function") return null;
    try { return storage.getItem(key); } catch (_) { return null; }
  }

  function _storageSet(key, value) {
    var storage = _getStorage();
    if (!storage || typeof storage.setItem !== "function") return false;
    try { storage.setItem(key, value); return true; } catch (_) { return false; }
  }

  function _storageRemove(key) {
    var storage = _getStorage();
    if (!storage || typeof storage.removeItem !== "function") return false;
    try { storage.removeItem(key); return true; } catch (_) { return false; }
  }

  function _defer(fn) {
    var impl = (typeof window !== "undefined" && window.ACPStructureViewer &&
      typeof window.ACPStructureViewer._setTimeoutImpl === "function")
      ? window.ACPStructureViewer._setTimeoutImpl : null;
    if (impl) { impl(fn, 180); return; }
    if (typeof setTimeout === "function") setTimeout(fn, 180);
  }

  /**
   * Persist the current view state (camera, style preset, measurement atom
   * indices, atom count) for jobId+selectedEntryId.  Called on entry switch
   * and job-switch teardown.  Corrupt/unavailable storage is silent.
   *
   * @returns {Object|null} the saved payload (null when nothing to save)
   */
  function saveViewState() {
    var state = structureViewerState;
    if (!state.jobId || !state.selectedEntryId) return null;
    var camera = null;
    var viewer = _canvasViewer("main");
    if (viewer && typeof viewer.getView === "function") {
      try { camera = viewer.getView(); } catch (_) { camera = null; }
    }
    var measures = null;
    try {
      /* molDoc.measures is the app's global-lexical measurement state
         ({type, atoms: [acpId...], value} entries) */
      if (typeof molDoc !== "undefined" && Array.isArray(molDoc.measures) && molDoc.measures.length) {
        measures = molDoc.measures.map(function (m) {
          return {
            type: m.type,
            atoms: Array.isArray(m.atoms) ? m.atoms.slice() : [],
            value: typeof m.value === "number" ? m.value : null,
          };
        });
      }
    } catch (_) { measures = null; }
    var payload = {
      version: viewStateStore.VERSION,
      camera: camera,
      stylePreset: geometryStore.stylePreset,
      measurements: measures,
      atomCount: state.displayedCoords ? state.displayedCoords.length : null,
      savedAt: new Date().toISOString(),
    };
    _storageSet(_viewKey(state.jobId, state.selectedEntryId), JSON.stringify(payload));
    return payload;
  }

  /**
   * Restore a saved view state for jobId+entryId (called after the entry's
   * geometry load).  Camera restores via setView ONLY when a saved state
   * exists AND its atomCount matches the displayed geometry — else skipped.
   * The setView is deferred one framing tick because the main-canvas load
   * path schedules an async zoomTo reframe.  Corrupted JSON / absent keys /
   * unknown version -> silent defaults.  Measurements are replayed through
   * the app's measurement mechanism when reachable and ALWAYS surfaced as a
   * 恢复的测量 record in the inspector.
   *
   * @returns {Object|null} the restored payload
   */
  function restoreViewState(jobId, entryId) {
    if (!jobId || !entryId) return null;
    var raw = _storageGet(_viewKey(jobId, entryId));
    if (!raw) return null;
    var saved = null;
    try { saved = JSON.parse(raw); } catch (_) { return null; }
    if (!saved || typeof saved !== "object" || saved.version !== viewStateStore.VERSION) {
      return null;
    }
    if (saved.stylePreset) {
      try { setStylePreset(saved.stylePreset); } catch (_) { /* preset is cosmetic */ }
    }
    var token = structureViewerState.selectionToken;
    var atomCount = structureViewerState.displayedCoords
      ? structureViewerState.displayedCoords.length : null;
    if (saved.camera && Array.isArray(saved.camera) &&
        saved.atomCount != null && saved.atomCount === atomCount) {
      _defer(function () {
        if (token !== structureViewerState.selectionToken) return;
        var viewer = _canvasViewer("main");
        if (viewer && typeof viewer.setView === "function") {
          try {
            viewer.setView(saved.camera);
            if (typeof viewer.render === "function") viewer.render();
          } catch (_) { /* camera restore is best-effort */ }
        }
      });
    }
    structureViewerState.restoredMeasurements =
      (saved.measurements && saved.measurements.length) ? saved.measurements : null;
    if (structureViewerState.restoredMeasurements) {
      try {
        if (typeof molDoc !== "undefined" && Array.isArray(molDoc.measures)) {
          molDoc.measures = structureViewerState.restoredMeasurements.map(function (m) {
            return {
              type: m.type,
              atoms: Array.isArray(m.atoms) ? m.atoms.slice() : [],
              value: m.value,
            };
          });
          if (typeof renderMeasurements === "function") renderMeasurements();
        }
      } catch (_) { /* programmatic replay is best-effort; record still shows */ }
    }
    return saved;
  }

  /**
   * Delete the saved view state for jobId+entryId.
   *
   * @returns {boolean}
   */
  function clearViewState(jobId, entryId) {
    if (!jobId || !entryId) return false;
    return _storageRemove(_viewKey(jobId, entryId));
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

  /* ---- render: structure viewer (summary bar + bottom strip) ---- */

  function _detectSwitcherKind(payload) {
    if (!payload) return null;
    var wf = String(payload.workflow || "").toLowerCase();
    if (wf.indexOf("confsearch") >= 0 || wf.indexOf("conformer") >= 0 || wf.indexOf("ensemble") >= 0 || wf.indexOf("energy") >= 0 || wf.indexOf("xtbmd_censo") >= 0) return "conformer";
    if (wf.indexOf("pessearch") >= 0 || wf.indexOf("pes") >= 0 || wf.indexOf("mechanism") >= 0) return "candidate";
    if (wf.indexOf("batch") >= 0 || wf.indexOf("lowconfirm") >= 0 || wf.indexOf("highconfirm") >= 0) return "batch";
    var entries = (payload && payload.entries) || [];
    if (entries.length > 1) {
      var hasBoltz = false, hasTsBadge = false, hasBatchSrc = false;
      for (var i = 0; i < entries.length; i++) {
        if (entries[i].boltzmann_weight != null) hasBoltz = true;
        var badges = entries[i].badges || [];
        for (var b = 0; b < badges.length; b++) {
          if (badges[b] === "TS" || badges[b] === "INT") hasTsBadge = true;
        }
        if (entries[i].source && entries[i].source.kind === "formal_result" && entries[i].group_id && entries[i].group_id.indexOf("batch") >= 0) hasBatchSrc = true;
      }
      if (hasBoltz) return "conformer";
      if (hasTsBadge && !hasBatchSrc) return "candidate";
      if (hasBatchSrc) return "batch";
    }
    return null;
  }

  function _switcherLabel(kind, index, total) {
    if (kind === "conformer") return _t("structure.switcher.conformer", STR.SWITCHER_CONFORMER) + " " + (index + 1) + "/" + total;
    if (kind === "candidate") return _t("structure.switcher.candidate", STR.SWITCHER_CANDIDATE) + " " + (index + 1) + "/" + total;
    if (kind === "batch") return _t("structure.switcher.batch_item", STR.SWITCHER_BATCH_ITEM) + " " + (index + 1) + "/" + total;
    return (index + 1) + "/" + total;
  }

  function _closeSwitcherDropdown() {
    if (_activeSwitcherDropdown) {
      _activeSwitcherDropdown.remove();
      _activeSwitcherDropdown = null;
    }
    if (_activeSwitcherOverlay) {
      _activeSwitcherOverlay.remove();
      _activeSwitcherOverlay = null;
    }
    if (_switcherAnchor) {
      _switcherAnchor.setAttribute("aria-expanded", "false");
      _switcherAnchor.focus();
      _switcherAnchor = null;
    }
    _switcherActiveIdx = -1;
    _switcherEntries = [];
  }

  function _openSwitcherDropdown(anchorEl, entries, kind) {
    _closeSwitcherDropdown();
    _switcherEntries = entries;
    _switcherActiveIdx = -1;
    _switcherAnchor = anchorEl;
    anchorEl.setAttribute("aria-expanded", "true");
    var dd = document.createElement("div");
    dd.className = "sv-switcher-dropdown";
    dd.setAttribute("role", "listbox");
    dd.setAttribute("aria-label", _t("structure.switcher.label", STR.SWITCHER_LABEL));

    var filterInput = document.createElement("input");
    filterInput.className = "sv-switcher-filter";
    filterInput.setAttribute("type", "text");
    filterInput.setAttribute("placeholder", _t("structure.switcher.filter_placeholder", STR.SWITCHER_FILTER_PLACEHOLDER));
    filterInput.setAttribute("aria-label", _t("structure.switcher.filter_placeholder", STR.SWITCHER_FILTER_PLACEHOLDER));
    dd.appendChild(filterInput);

    var list = document.createElement("div");
    list.className = "sv-switcher-list";
    dd.appendChild(list);

    function _visibleItems() {
      return list.querySelectorAll(".sv-switcher-item");
    }

    function _syncSwitcherFocus() {
      var items = _visibleItems();
      for (var si = 0; si < items.length; si++) {
        items[si].classList.toggle("sv-switcher-focus", si === _switcherActiveIdx);
        items[si].setAttribute("aria-selected", si === _switcherActiveIdx ? "true" : "false");
      }
      if (_switcherActiveIdx >= 0 && _switcherActiveIdx < items.length) {
        if (typeof items[_switcherActiveIdx].scrollIntoView === "function") {
          items[_switcherActiveIdx].scrollIntoView({ block: "nearest" });
        }
      }
    }

    function renderItems(filter) {
      list.innerHTML = "";
      var lf = (filter || "").toLowerCase();
      for (var i = 0; i < entries.length; i++) {
        var e = entries[i];
        var label = e.label || e.id;
        if (lf && label.toLowerCase().indexOf(lf) < 0 && String(i + 1).indexOf(lf) < 0) continue;
        var item = document.createElement("div");
        item.className = "sv-switcher-item";
        item.setAttribute("role", "option");
        item.setAttribute("aria-selected", "false");
        if (e.id === structureViewerState.selectedEntryId) {
          item.className += " active";
          item.setAttribute("aria-selected", "true");
        }

        var rank = document.createElement("span");
        rank.className = "sv-switcher-item-rank";
        rank.textContent = String(i + 1);
        item.appendChild(rank);

        var lbl = document.createElement("span");
        lbl.className = "sv-switcher-item-label";
        var badges = e.badges || [];
        var badgePrefix = "";
        for (var b = 0; b < badges.length; b++) {
          if (badges[b] === "TS" || badges[b] === "INT") badgePrefix = "[" + badges[b] + "] ";
        }
        if (kind === "candidate" && badges.indexOf("\u672a\u786e\u8ba4") >= 0) {
          badgePrefix += "[" + _t("structure.switcher.unconfirmed", STR.SWITCHER_UNCONFIRMED) + "] ";
        }
        if (kind === "batch" && e.status === "failed") {
          badgePrefix += "[" + _t("structure.failed", STR.FAILED) + "] ";
        }
        if (kind === "batch" && e.vibrations && e.vibrations.imaginary_count > 0) {
          badgePrefix += "[" + _t("structure.switcher.has_imag", STR.SWITCHER_HAS_IMAG) + "] ";
        }
        lbl.textContent = badgePrefix + label;
        item.appendChild(lbl);

        if (e.relative_energy_kcal != null) {
          var energy = document.createElement("span");
          energy.className = "sv-switcher-item-energy";
          energy.textContent = (e.relative_energy_kcal > 0 ? "+" : "") + e.relative_energy_kcal.toFixed(1) + " kcal/mol";
          item.appendChild(energy);
        }

        if (e.boltzmann_weight != null) {
          var bar = document.createElement("div");
          bar.className = "sv-switcher-item-boltz";
          var fill = document.createElement("div");
          fill.className = "sv-switcher-item-boltz-fill";
          fill.style.width = Math.round(e.boltzmann_weight * 100) + "%";
          bar.appendChild(fill);
          item.appendChild(bar);
        }

        (function (entryId) {
          item.addEventListener("click", function () {
            _closeSwitcherDropdown();
            selectEntry(entryId, "switcher");
          });
        })(e.id);
        list.appendChild(item);
      }
      _switcherActiveIdx = -1;
    }

    renderItems("");
    filterInput.addEventListener("input", function () {
      renderItems(filterInput.value);
    });

    filterInput.addEventListener("keydown", function (ev) {
      var items = _visibleItems();
      if (ev.key === "ArrowDown") {
        ev.preventDefault();
        _switcherActiveIdx = Math.min(_switcherActiveIdx + 1, items.length - 1);
        _syncSwitcherFocus();
      } else if (ev.key === "ArrowUp") {
        ev.preventDefault();
        _switcherActiveIdx = Math.max(_switcherActiveIdx - 1, 0);
        _syncSwitcherFocus();
      } else if (ev.key === "Enter") {
        ev.preventDefault();
        if (_switcherActiveIdx >= 0 && _switcherActiveIdx < items.length) {
          var activeItem = items[_switcherActiveIdx];
          _closeSwitcherDropdown();
          var clickEvt = new MouseEvent("click", { bubbles: true });
          activeItem.dispatchEvent(clickEvt);
        }
      } else if (ev.key === "Escape") {
        ev.preventDefault();
        _closeSwitcherDropdown();
      } else if (ev.key === "Tab") {
        _closeSwitcherDropdown();
      }
    });

    list.addEventListener("keydown", function (ev) {
      var items = _visibleItems();
      if (ev.key === "ArrowDown") {
        ev.preventDefault();
        _switcherActiveIdx = Math.min(_switcherActiveIdx + 1, items.length - 1);
        _syncSwitcherFocus();
      } else if (ev.key === "ArrowUp") {
        ev.preventDefault();
        _switcherActiveIdx = Math.max(_switcherActiveIdx - 1, 0);
        _syncSwitcherFocus();
      } else if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        if (_switcherActiveIdx >= 0 && _switcherActiveIdx < items.length) {
          var activeItem = items[_switcherActiveIdx];
          _closeSwitcherDropdown();
          var clickEvt = new MouseEvent("click", { bubbles: true });
          activeItem.dispatchEvent(clickEvt);
        }
      } else if (ev.key === "Escape") {
        ev.preventDefault();
        _closeSwitcherDropdown();
      } else if (ev.key === "Home") {
        ev.preventDefault();
        _switcherActiveIdx = 0;
        _syncSwitcherFocus();
      } else if (ev.key === "End") {
        ev.preventDefault();
        _switcherActiveIdx = items.length - 1;
        _syncSwitcherFocus();
      }
    });

    var rect = anchorEl.getBoundingClientRect();
    dd.style.left = Math.max(0, rect.left - 8) + "px";
    document.body.appendChild(dd);

    var ddRect = dd.getBoundingClientRect();
    if (ddRect.right > window.innerWidth) {
      dd.style.left = Math.max(0, window.innerWidth - ddRect.width - 8) + "px";
    }

    var overlay = document.createElement("div");
    overlay.style.cssText = "position:fixed;inset:0;z-index:119;";
    overlay.addEventListener("mousedown", function () { _closeSwitcherDropdown(); });
    overlay.setAttribute("tabindex", "-1");
    document.body.appendChild(overlay);
    _activeSwitcherOverlay = overlay;

    _activeSwitcherDropdown = dd;
    filterInput.focus();
  }

  function _findCalculationInputEntry(payload) {
    var entries = (payload && payload.entries) || [];
    for (var i = 0; i < entries.length; i++) {
      if (entries[i].source && entries[i].source.kind === "calculation_input") return entries[i];
    }
    return null;
  }

  function _inputResultKey(jobId) {
    return viewStateStore.NS + "inputResult." + (jobId || "");
  }

  function _getInputResultChoice(jobId) {
    var raw = _storageGet(_inputResultKey(jobId));
    return raw === "input" ? "input" : "result";
  }

  function _setInputResultChoice(jobId, choice) {
    _storageSet(_inputResultKey(jobId), choice);
  }

  function _drawerPersistKey(jobId, entryId) {
    return viewStateStore.NS + "drawer." + (jobId || "") + ":" + (entryId || "");
  }

  function _getDrawerPersist(jobId, entryId) {
    return _storageGet(_drawerPersistKey(jobId, entryId));
  }

  function _setDrawerPersist(jobId, entryId, drawerId) {
    if (drawerId) {
      _storageSet(_drawerPersistKey(jobId, entryId), drawerId);
    } else {
      _storageRemove(_drawerPersistKey(jobId, entryId));
    }
  }

  function _stripExpandedKey(jobId) {
    return viewStateStore.NS + "stripExpanded." + (jobId || "");
  }

  function _getStripExpanded(jobId) {
    return _storageGet(_stripExpandedKey(jobId)) === "true";
  }

  function _setStripExpanded(jobId, expanded) {
    _storageSet(_stripExpandedKey(jobId), expanded ? "true" : "false");
  }

  /**
   * Render the structure viewer from state.payload.
   * Populates the summary bar with selected entry info and the bottom strip
   * with compact entry items for multi-entry payloads.
   */
  function renderStructureViewer() {
    if (typeof document === "undefined") return;
    var summaryBar = document.getElementById("sv-summary-bar");
    var bottomStrip = document.getElementById("sv-bottom-strip");
    var layout = document.getElementById("sv-layout");
    if (!summaryBar || !layout) return;

    if (!layout._svEscapeInit) {
      layout._svEscapeInit = true;
      layout.addEventListener("keydown", function (ev) {
        if (ev.key === "Escape" && _activeDrawerId) {
          ev.stopPropagation();
          closeDrawer(_activeDrawerId);
        }
      });
    }

    var payload = structureViewerState.payload;
    var entries = (payload && payload.entries) || [];
    var groups = (payload && payload.groups) || [];
    var selectedId = structureViewerState.selectedEntryId;

    /* find selected entry */
    var entry = null;
    for (var i = 0; i < entries.length; i++) {
      if (entries[i].id === selectedId) { entry = entries[i]; break; }
    }

    /* --- summary bar --- */
    summaryBar.innerHTML = "";
    summaryBar.setAttribute("role", "toolbar");
    summaryBar.setAttribute("aria-label", _t("structure.summary_bar", STR.SUMMARY_BAR));
    if (entry) {
      var switcherKind = _detectSwitcherKind(payload);
      var showSwitcher = switcherKind && entries.length > 1;
      if (switcherKind === "batch" && entries.length <= 1) showSwitcher = false;

      if (showSwitcher) {
        var switcherLabel = document.createElement("span");
        switcherLabel.className = "sv-summary-switcher";
        switcherLabel.textContent = _switcherLabel(switcherKind, _entryIndex(entries, selectedId), entries.length) + " \u25bc";
        switcherLabel.setAttribute("role", "button");
        switcherLabel.setAttribute("tabindex", "0");
        switcherLabel.setAttribute("aria-expanded", "false");
        switcherLabel.setAttribute("aria-haspopup", "listbox");
        switcherLabel.addEventListener("click", function (ev) {
          if (_activeSwitcherDropdown) { _closeSwitcherDropdown(); return; }
          _openSwitcherDropdown(switcherLabel, entries, switcherKind);
        });
        switcherLabel.addEventListener("keydown", function (ev) {
          if (ev.key === "Enter" || ev.key === " ") {
            ev.preventDefault();
            if (_activeSwitcherDropdown) { _closeSwitcherDropdown(); } else { _openSwitcherDropdown(switcherLabel, entries, switcherKind); }
          }
        });
        summaryBar.appendChild(switcherLabel);
      }

      var title = document.createElement("span");
      title.className = "sv-summary-title";
      title.textContent = _esc(entry.label || entry.id);
      summaryBar.appendChild(title);

      var badges = entry.badges || [];
      for (var bi = 0; bi < badges.length; bi++) {
        summaryBar.appendChild(_renderBadgeChip(badges[bi]));
      }

      if (entry.energy && entry.energy.value != null) {
        var energySpan = document.createElement("span");
        energySpan.className = "sv-summary-energy";
        energySpan.textContent = _formatEnergy(entry.energy);
        summaryBar.appendChild(energySpan);
      }

      if (entry.status) {
        var statusBadge = document.createElement("span");
        statusBadge.className = "sv-summary-badge";
        statusBadge.style.background = entry.status === "completed" ? "rgba(56,193,114,0.12)" : "rgba(229,83,83,0.12)";
        statusBadge.style.color = entry.status === "completed" ? "var(--sv-green)" : "var(--sv-red)";
        statusBadge.textContent = entry.status === "completed" ? _t("structure.completed", STR.COMPLETED) : _t("structure.failed", STR.FAILED);
        summaryBar.appendChild(statusBadge);
      }

      var inputEntry = _findCalculationInputEntry(payload);
      if (inputEntry) {
        var choice = _getInputResultChoice(structureViewerState.jobId);
        var toggle = document.createElement("div");
        toggle.className = "sv-input-result-toggle";
        toggle.setAttribute("role", "radiogroup");
        toggle.setAttribute("aria-label", _t("structure.input_result_toggle", STR.INPUT_RESULT_TOGGLE));
        var btnResult = document.createElement("button");
        btnResult.className = "sv-toggle-btn" + (choice === "result" ? " active" : "");
        btnResult.setAttribute("role", "radio");
        btnResult.setAttribute("aria-checked", choice === "result" ? "true" : "false");
        btnResult.textContent = _t("structure.toggle_result", STR.TOGGLE_RESULT);
        btnResult.addEventListener("click", function () {
          _setInputResultChoice(structureViewerState.jobId, "result");
          renderStructureViewer();
        });
        var btnInput = document.createElement("button");
        btnInput.className = "sv-toggle-btn" + (choice === "input" ? " active" : "");
        btnInput.setAttribute("role", "radio");
        btnInput.setAttribute("aria-checked", choice === "input" ? "true" : "false");
        btnInput.textContent = _t("structure.toggle_input", STR.TOGGLE_INPUT);
        btnInput.addEventListener("click", function () {
          _setInputResultChoice(structureViewerState.jobId, "input");
          selectEntry(inputEntry.id, "input_toggle");
        });
        toggle.appendChild(btnResult);
        toggle.appendChild(btnInput);
        summaryBar.appendChild(toggle);

        if (choice === "result" && entry.id !== inputEntry.id) {
          var compareBtn = document.createElement("button");
          compareBtn.className = "sv-overlay-compare-btn";
          compareBtn.textContent = _t("structure.overlay_compare", STR.OVERLAY_COMPARE);
          compareBtn.addEventListener("click", function () {
            loadOverlay(entry.id, inputEntry.id);
          });
          summaryBar.appendChild(compareBtn);
        }
      }

      if (entries.length > 1) {
        var stripToggle = document.createElement("button");
        stripToggle.className = "sv-strip-toggle";
        stripToggle.setAttribute("aria-expanded", _stripExpanded ? "true" : "false");
        stripToggle.setAttribute("aria-controls", "sv-bottom-strip");
        stripToggle.textContent = _t("structure.strip_toggle_show", STR.STRIP_TOGGLE_SHOW);
        stripToggle.addEventListener("click", function () {
          var strip = document.getElementById("sv-bottom-strip");
          if (!strip) return;
          _stripExpanded = !_stripExpanded;
          strip.classList.toggle("expanded", _stripExpanded);
          stripToggle.setAttribute("aria-expanded", _stripExpanded ? "true" : "false");
          _setStripExpanded(structureViewerState.jobId, _stripExpanded);
        });
        summaryBar.appendChild(stripToggle);
      }

      var detailsBtn = document.createElement("button");
      detailsBtn.className = "sv-edit-btn";
      detailsBtn.textContent = "\u8be6\u60c5";
      detailsBtn.setAttribute("aria-expanded", _activeDrawerId === "source" ? "true" : "false");
      detailsBtn.addEventListener("click", function () { openDrawer("source", detailsBtn); });
      summaryBar.appendChild(detailsBtn);

      /* Vibration button: visible when vibration data is likely available */
      if (entry.vibrations && entry.vibrations.available !== false) {
        var vibBtn = document.createElement("button");
        vibBtn.className = "sv-edit-btn";
        var imagCount = entry.vibrations.imaginary_count;
        if (imagCount != null && imagCount > 0) {
          vibBtn.textContent = _t("structure.vib.imaginary_btn", STR.VIB_IMAG_BTN).replace("{count}", String(imagCount));
        } else {
          vibBtn.textContent = _t("structure.vib.btn", STR.VIB_BTN);
        }
        vibBtn.setAttribute("aria-expanded", _activeDrawerId === "vibration" ? "true" : "false");
        vibBtn.addEventListener("click", function () { openDrawer("vibration", vibBtn); });
        summaryBar.appendChild(vibBtn);
      }

      /* Measure button: always visible for geometry editing */
      var measBtn = document.createElement("button");
      measBtn.className = "sv-edit-btn";
      measBtn.textContent = _t("structure.measure.btn", STR.MEASURE_BTN);
      measBtn.setAttribute("aria-expanded", _activeDrawerId === "measure" ? "true" : "false");
      measBtn.addEventListener("click", function () { openDrawer("measure", measBtn); });
      summaryBar.appendChild(measBtn);
    }

    /* availability / error notices */
    if (structureViewerState.availability === "pending_fetch") {
      var notice = document.createElement("span");
      notice.className = "sv-notice sv-notice-pending";
      notice.style.fontSize = "11px";
      notice.textContent = _t("structure.pending_fetch", STR.PENDING_FETCH) + "\u2014" + _t("structure.pending_retry", STR.PENDING_RETRY);
      summaryBar.appendChild(notice);
    }
    if (structureViewerState.error) {
      var errNotice = document.createElement("span");
      errNotice.className = "sv-notice sv-notice-error";
      errNotice.style.fontSize = "11px";
      errNotice.textContent = _esc(structureViewerState.error);
      summaryBar.appendChild(errNotice);
    }

    /* --- bottom strip --- */
    if (!bottomStrip) return;
    bottomStrip.innerHTML = "";

    var showStrip = entries.length > 1;
    bottomStrip.classList.toggle("expanded", showStrip && _stripExpanded);
    if (!showStrip) { _renderPlaybackBar(); return; }

    _initListbox(bottomStrip);

    var viz = virtualizeEntries(entries, LIST_VIRTUALIZE_THRESHOLD, [
      structureViewerState.selectedEntryId,
      (payload && payload.default_entry_id) || null,
    ]);
    var visibleSet = viz.windowed ? {} : null;
    if (visibleSet) {
      for (var vi = 0; vi < viz.visible.length; vi++) visibleSet[viz.visible[vi].id] = true;
    }

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

      if (group && group.label) {
        var header = document.createElement("span");
        header.className = "sv-group-header";
        header.style.padding = "0 4px";
        header.style.fontSize = "10px";
        header.textContent = _esc(group.label);
        bottomStrip.appendChild(header);
      }

      if (_isPerFrameGroup(groupEntries)) {
        var sampled = sampleFrames(groupEntries, TRAJECTORY_SAMPLE_THRESHOLD, TRAJECTORY_SAMPLE_TARGET);
        if (sampled.length < groupEntries.length) {
          var sampleMarker = document.createElement("span");
          sampleMarker.className = "sv-list-marker";
          sampleMarker.style.borderTop = "none";
          sampleMarker.style.borderBottom = "none";
          sampleMarker.textContent = _t("structure.perf.sampled", STR.PERF_SAMPLED) + " " + sampled.length + "/" + groupEntries.length;
          bottomStrip.appendChild(sampleMarker);
        }
        groupEntries = sampled;
      }

      for (var ej = 0; ej < groupEntries.length; ej++) {
        var entryItem = groupEntries[ej];
        if (visibleSet && !visibleSet[entryItem.id]) continue;
        var item = _renderStripItem(entryItem);
        bottomStrip.appendChild(item);
        _renderedEntryIds.push(entryItem.id);
      }
    }

    if (viz.windowed) {
      var marker = document.createElement("span");
      marker.className = "sv-list-marker";
      marker.style.borderTop = "none";
      marker.style.borderBottom = "none";
      marker.textContent = _t("structure.perf.partial_list", STR.PERF_PARTIAL_LIST) + " " + viz.visible.length + "/" + viz.total;
      bottomStrip.appendChild(marker);
    }

    _syncListboxActive(bottomStrip);
    _renderPlaybackBar();
  }

  function _entryIndex(entries, id) {
    for (var i = 0; i < entries.length; i++) {
      if (entries[i].id === id) return i;
    }
    return 0;
  }

  function _renderStripItem(entry) {
    var item = document.createElement("div");
    item.className = "sv-strip-item";
    var isSelected = entry.id === structureViewerState.selectedEntryId;
    if (isSelected) {
      item.className += " active";
    }
    item.setAttribute("data-entry-id", _esc(entry.id));
    item.setAttribute("role", "option");
    item.setAttribute("aria-selected", isSelected ? "true" : "false");
    item.setAttribute("id", "sv-opt-" + _esc(entry.id));

    var label = document.createElement("span");
    label.textContent = _esc(entry.label || entry.id);
    item.appendChild(label);

    if (entry.relative_energy_kcal != null) {
      var delta = document.createElement("span");
      delta.style.fontSize = "10px";
      delta.style.color = "var(--sv-green)";
      delta.textContent = (entry.relative_energy_kcal > 0 ? "+" : "") + entry.relative_energy_kcal.toFixed(1);
      item.appendChild(delta);
    }

    if (entry.boltzmann_weight != null) {
      var bar = document.createElement("div");
      bar.className = "sv-strip-boltz";
      bar.style.width = Math.round(entry.boltzmann_weight * 100) + "%";
      bar.style.maxWidth = "40px";
      item.appendChild(bar);
    }

    item.addEventListener("click", function () {
      selectEntry(entry.id, "list");
    });

    return item;
  }

  /* ---- listbox a11y (todo 42): WAI-ARIA listbox pattern on the entry
     list — arrows move the active option, Enter/Space selects it.  The
     handler lives on the list container only, so other tabs' keyboard
     handling is untouched. ---- */
  var _renderedEntryIds = [];
  var _listActiveIdx = -1;

  function _initListbox(listBody) {
    listBody.setAttribute("role", "listbox");
    listBody.setAttribute("tabindex", "0");
    listBody.setAttribute("aria-label", _t("structure.strip_label", STR.STRIP_LABEL));
    if (!listBody._svListboxInit) {
      listBody._svListboxInit = true;
      listBody.addEventListener("keydown", function (ev) {
        _onListboxKeydown(ev, listBody);
      });
    }
    _renderedEntryIds = [];
    _listActiveIdx = -1;
  }

  function _onListboxKeydown(ev, listBody) {
    if (!ev || !_renderedEntryIds.length) return;
    var key = ev.key;
    if (key !== "ArrowDown" && key !== "ArrowUp" && key !== "ArrowLeft" &&
        key !== "ArrowRight" && key !== "Enter" && key !== " " &&
        key !== "Home" && key !== "End") {
      return;
    }
    ev.preventDefault();
    if (key === "Enter" || key === " ") {
      if (_listActiveIdx >= 0 && _listActiveIdx < _renderedEntryIds.length) {
        selectEntry(_renderedEntryIds[_listActiveIdx], "list");
      }
      return;
    }
    if (key === "Home") {
      _listActiveIdx = 0;
    } else if (key === "End") {
      _listActiveIdx = _renderedEntryIds.length - 1;
    } else if (key === "ArrowDown" || key === "ArrowRight") {
      _listActiveIdx = Math.min(_listActiveIdx + 1, _renderedEntryIds.length - 1);
    } else {
      _listActiveIdx = Math.max(_listActiveIdx - 1, 0);
    }
    _syncListboxActive(listBody);
    _scrollToActiveItem(listBody);
  }

  function _syncListboxActive(listBody) {
    if (_listActiveIdx < 0 || _listActiveIdx >= _renderedEntryIds.length) {
      _listActiveIdx = _renderedEntryIds.length ? 0 : -1;
    }
    if (_listActiveIdx < 0) {
      listBody.removeAttribute("aria-activedescendant");
      return;
    }
    var activeId = _renderedEntryIds[_listActiveIdx];
    listBody.setAttribute("aria-activedescendant", "sv-opt-" + activeId);
    var rows = listBody.children;
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      if (typeof row.classList === "undefined") continue;
      var isActive = row.getAttribute && row.getAttribute("data-entry-id") === activeId;
      row.classList.toggle("sv-option-focus", isActive);
    }
  }

  function _scrollToActiveItem(listBody) {
    if (_listActiveIdx < 0 || _listActiveIdx >= _renderedEntryIds.length) return;
    var activeId = _renderedEntryIds[_listActiveIdx];
    var rows = listBody.children;
    for (var i = 0; i < rows.length; i++) {
      if (rows[i].getAttribute && rows[i].getAttribute("data-entry-id") === activeId) {
        if (typeof rows[i].scrollIntoView === "function") {
          rows[i].scrollIntoView({ block: "nearest", inline: "nearest" });
        }
        break;
      }
    }
  }

  function _isPerFrameGroup(groupEntries) {
    if (!groupEntries || !groupEntries.length) return false;
    for (var i = 0; i < groupEntries.length; i++) {
      var src = groupEntries[i] && groupEntries[i].source;
      if (!src || src.frame_index == null) return false;
    }
    return true;
  }

  function _listMarker(label, shown, total) {
    var marker = document.createElement("div");
    marker.className = "sv-list-marker";
    marker.textContent = label + " " + shown + "/" + total;
    return marker;
  }

  function _renderEntryRow(entry) {
    var row = document.createElement("div");
    row.className = "sv-entry-row";
    if (entry.id === structureViewerState.selectedEntryId) {
      row.className += " sv-active";
    }
    row.setAttribute("data-entry-id", _esc(entry.id));
    row.setAttribute("role", "option");
    row.setAttribute("id", "sv-opt-" + _esc(entry.id));
    row.setAttribute("aria-selected", entry.id === structureViewerState.selectedEntryId
      ? "true" : "false");

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

  /* ---- render: inspector (drawer-based) ---- */

  /**
   * Refresh any open drawer content for the currently selected entry.
   * Also loads vibrations for the selected entry (contract: always fetch
   * when available=true, regardless of drawer visibility).
   */
  function renderInspector() {
    if (typeof document === "undefined") return;

    var payload = structureViewerState.payload;
    var entries = (payload && payload.entries) || [];
    var entry = null;
    for (var i = 0; i < entries.length; i++) {
      if (entries[i].id === structureViewerState.selectedEntryId) { entry = entries[i]; break; }
    }

    if (entry && entry.vibrations && entry.vibrations.available !== false) {
      if (typeof window !== "undefined" && window.ACPVibrationViewer &&
          typeof window.ACPVibrationViewer.loadVibrations === "function") {
        var vibOpts = {};
        if (entry.vibrations.endpoint) vibOpts.endpoint = entry.vibrations.endpoint;
        window.ACPVibrationViewer.loadVibrations(structureViewerState.jobId, entry.id, vibOpts);
      }
    }

    var ids = ["source", "vibration", "measure", "more"];
    for (var j = 0; j < ids.length; j++) {
      var drawer = document.getElementById("sv-drawer-" + ids[j]);
      if (drawer && drawer.style.display !== "none") {
        _renderDrawerContent(ids[j]);
      }
    }
    _renderPlaybackBar();
  }

  function _ariaGroup(el, label) {
    el.setAttribute("role", "group");
    el.setAttribute("aria-label", _esc(label));
  }

  function _inspectorSection(label, value) {
    var div = document.createElement("div");
    div.className = "sv-inspector-section";
    _ariaGroup(div, label);
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
    _ariaGroup(div, _t("structure.edit.title", STR.EDIT_TITLE));
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
    btn.setAttribute("aria-label", _t(key, fallback));
    btn.className = "sv-edit-btn";
    btn.textContent = _t(key, fallback);
    btn.addEventListener("click", handler);
    return btn;
  }

  /* ---- drawer toggles ---- */

  function _syncStripActive() {
    if (typeof document === "undefined") return;
    var strip = document.getElementById("sv-bottom-strip");
    if (!strip) return;
    var items = strip.querySelectorAll(".sv-strip-item");
    for (var i = 0; i < items.length; i++) {
      var id = items[i].getAttribute("data-entry-id");
      var isActive = id === structureViewerState.selectedEntryId;
      items[i].classList.toggle("active", isActive);
      items[i].setAttribute("aria-selected", isActive ? "true" : "false");
    }
  }

  var _DRAWER_ARIA_LABELS = {
    measure: function () { return _t("structure.measurements", STR.MEASUREMENTS); },
    vibration: function () { return _t("structure.vibrations", STR.VIBRATIONS); },
    source: function () { return _t("structure.inspector_title", STR.INSPECTOR_TITLE); },
    more: function () { return _t("structure.drawer.more", STR.DRAWER_MORE); },
  };

  function openDrawer(id, triggerElement) {
    if (typeof document === "undefined") return;
    closeAllDrawers();
    _activeDrawerId = id;
    _drawerTrigger = triggerElement || null;
    var drawer = document.getElementById("sv-drawer-" + id);
    if (!drawer) return;
    drawer.style.display = "block";
    drawer.setAttribute("role", "dialog");
    drawer.setAttribute("aria-modal", "false");
    var labelFn = _DRAWER_ARIA_LABELS[id];
    drawer.setAttribute("aria-label", labelFn ? labelFn() : id);
    _renderDrawerContent(id);
    _setDrawerPersist(structureViewerState.jobId, structureViewerState.selectedEntryId, id);
    /* Focus management: move focus to drawer's first focusable element */
    _defer(function () {
      var focusable = drawer.querySelector(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
      );
      if (focusable && typeof focusable.focus === "function") {
        focusable.focus();
      }
    });
  }

  function closeDrawer(id) {
    if (typeof document === "undefined") return;
    var drawer = document.getElementById("sv-drawer-" + id);
    if (drawer) {
      drawer.style.display = "none";
      drawer.removeAttribute("role");
      drawer.removeAttribute("aria-modal");
      drawer.removeAttribute("aria-label");
    }
    if (_activeDrawerId === id) {
      _activeDrawerId = null;
      _setDrawerPersist(structureViewerState.jobId, structureViewerState.selectedEntryId, null);
      /* Focus return: restore focus to the trigger element */
      if (_drawerTrigger && typeof _drawerTrigger.focus === "function") {
        _defer(function () { _drawerTrigger.focus(); });
      }
      _drawerTrigger = null;
    }
  }

  function closeAllDrawers() {
    if (typeof document === "undefined") return;
    var ids = ["measure", "vibration", "source", "more"];
    for (var i = 0; i < ids.length; i++) {
      var drawer = document.getElementById("sv-drawer-" + ids[i]);
      if (drawer) {
        drawer.style.display = "none";
        drawer.removeAttribute("role");
        drawer.removeAttribute("aria-modal");
        drawer.removeAttribute("aria-label");
      }
    }
    if (_activeDrawerId) {
      _setDrawerPersist(structureViewerState.jobId, structureViewerState.selectedEntryId, null);
      _activeDrawerId = null;
    }
    _drawerContentCache = {};
  }

  function _renderDrawerContent(id) {
    var drawer = document.getElementById("sv-drawer-" + id);
    if (!drawer) return;
    drawer.innerHTML = "";

    var header = document.createElement("div");
    header.className = "sv-drawer-header";
    var title = document.createElement("span");
    var titles = { measure: _t("structure.measurements", STR.MEASUREMENTS), vibration: _t("structure.vibrations", STR.VIBRATIONS), source: _t("structure.inspector_title", STR.INSPECTOR_TITLE), more: "\u66f4\u591a" };
    title.textContent = titles[id] || id;
    header.appendChild(title);
    var closeBtn = document.createElement("button");
    closeBtn.className = "sv-drawer-close";
    closeBtn.setAttribute("aria-label", _t("structure.drawer.close", STR.DRAWER_CLOSE));
    closeBtn.textContent = "\u00d7";
    closeBtn.addEventListener("click", function () { closeDrawer(id); });
    header.appendChild(closeBtn);
    drawer.appendChild(header);

    drawer.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") {
        ev.stopPropagation();
        closeDrawer(id);
      }
    });

    var body = document.createElement("div");
    body.className = "sv-drawer-body";
    drawer.appendChild(body);

    if (id === "source") {
      _renderSourceDrawer(body);
    } else if (id === "vibration") {
      _renderVibrationDrawer(body);
    } else if (id === "measure") {
      _renderMeasureDrawer(body);
    } else if (id === "more") {
      _renderMoreDrawer(body);
    }
  }

  function _renderSourceDrawer(body) {
    var payload = structureViewerState.payload;
    var entries = (payload && payload.entries) || [];
    var entry = null;
    for (var i = 0; i < entries.length; i++) {
      if (entries[i].id === structureViewerState.selectedEntryId) { entry = entries[i]; break; }
    }
    if (!entry) {
      body.textContent = _t("structure.no_entry", STR.NO_ENTRY);
      return;
    }
    body.appendChild(_inspectorSection(_t("structure.status", STR.STATUS),
      entry.status === "completed" ? _t("structure.completed", STR.COMPLETED) : _t("structure.failed", STR.FAILED)));
    var sourceKind = (entry.source && entry.source.kind) || "";
    var sourceFallback = STR.SOURCE_KINDS[sourceKind] || _esc(sourceKind);
    var sourceLabel = sourceKind ? _t("structure.source_kind." + sourceKind, sourceFallback) : sourceFallback;
    body.appendChild(_inspectorSection(_t("structure.source", STR.SOURCE), sourceLabel));
    if (entry.energy && entry.energy.value != null) {
      var energyDiv = document.createElement("div");
      energyDiv.className = "sv-inspector-section";
      var lbl = document.createElement("div");
      lbl.className = "sv-inspector-label";
      lbl.textContent = _t("structure.energy", STR.ENERGY);
      energyDiv.appendChild(lbl);
      var valSpan = document.createElement("div");
      valSpan.className = "sv-inspector-value";
      valSpan.textContent = entry.energy.value.toFixed(6) + " " + _esc(entry.energy.unit || "hartree");
      energyDiv.appendChild(valSpan);
      body.appendChild(energyDiv);
    }
    if (entry.relative_energy_kcal != null) {
      body.appendChild(_inspectorSection(_t("structure.delta_e", STR.DELTA_E),
        entry.relative_energy_kcal.toFixed(2) + " kcal/mol"));
    }
    if (entry.boltzmann_weight != null) {
      body.appendChild(_inspectorSection(_t("structure.weight", STR.WEIGHT),
        (entry.boltzmann_weight * 100).toFixed(1) + "%"));
    }
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
      body.appendChild(newerNotice);
    }
    _renderOverlaySection(body);
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
      body.appendChild(warnDiv);
    }
  }

  function _renderVibrationDrawer(body) {
    var payload = structureViewerState.payload;
    var entries = (payload && payload.entries) || [];
    var entry = null;
    for (var i = 0; i < entries.length; i++) {
      if (entries[i].id === structureViewerState.selectedEntryId) { entry = entries[i]; break; }
    }
    var vibSection = document.createElement("div");
    vibSection.className = "sv-inspector-section";
    _ariaGroup(vibSection, _t("structure.vibrations", STR.VIBRATIONS));
    var vibLbl = document.createElement("div");
    vibLbl.className = "sv-inspector-label";
    vibLbl.textContent = _t("structure.vibrations", STR.VIBRATIONS);
    vibSection.appendChild(vibLbl);
    var vibContainer = document.createElement("div");
    vibContainer.id = "structure-inspector-vibrations";
    vibSection.appendChild(vibContainer);
    body.appendChild(vibSection);
    if (entry && entry.vibrations && entry.vibrations.available !== false) {
      if (typeof window !== "undefined" && window.ACPVibrationViewer &&
          typeof window.ACPVibrationViewer.loadVibrations === "function") {
        var vibOpts = {};
        if (entry.vibrations.endpoint) vibOpts.endpoint = entry.vibrations.endpoint;
        window.ACPVibrationViewer.loadVibrations(structureViewerState.jobId, entry.id, vibOpts);
      }
    } else {
      var vibNone = document.createElement("div");
      vibNone.className = "sv-inspector-value sv-muted";
      vibNone.textContent = _t("structure.vib.none", STR.VIB_NONE);
      vibContainer.appendChild(vibNone);
    }
  }

  function _renderMeasureDrawer(body) {
    var measDiv = document.createElement("div");
    measDiv.className = "sv-inspector-section";
    if (overlayMeasurementsBlocked()) {
      measDiv.className += " sv-measurements-blocked";
    }
    _ariaGroup(measDiv, _t("structure.measurements", STR.MEASUREMENTS));
    var measLbl = document.createElement("div");
    measLbl.className = "sv-inspector-label";
    measLbl.textContent = _t("structure.measurements", STR.MEASUREMENTS);
    measDiv.appendChild(measLbl);

    var hasMeasures = false;
    try {
      if (typeof molDoc !== "undefined" && Array.isArray(molDoc.measures) && molDoc.measures.length) {
        hasMeasures = true;
      }
    } catch (_) { /* molDoc guard */ }

    if (overlayMeasurementsBlocked()) {
      var blockedVal = document.createElement("div");
      blockedVal.className = "sv-inspector-value sv-muted";
      blockedVal.textContent = _t("structure.overlay.unproven", STR.OVERLAY_UNPROVEN);
      measDiv.appendChild(blockedVal);
    } else if (!hasMeasures) {
      var measVal = document.createElement("div");
      measVal.className = "sv-inspector-value sv-muted";
      measVal.textContent = _t("structure.measurements_placeholder", STR.MEASUREMENTS_PLACEHOLDER);
      measDiv.appendChild(measVal);
    } else {
      var _measureTypeLabels = {
        distance: function () { return _t("structure.measure.type_distance", STR.MEASURE_TYPE_DISTANCE); },
        angle: function () { return _t("structure.measure.type_angle", STR.MEASURE_TYPE_ANGLE); },
        dihedral: function () { return _t("structure.measure.type_dihedral", STR.MEASURE_TYPE_DIHEDRAL); },
      };
      var _measureKindMap = { distance: "bond_length", angle: "bond_angle", dihedral: "dihedral" };
      var _measureRange = {
        distance: [0.4, 5.0],
        angle: [1, 179],
        dihedral: [-180, 180],
      };
      for (var mi = 0; mi < molDoc.measures.length; mi++) {
        (function (idx) {
          var m = molDoc.measures[idx];
          var mRow = document.createElement("div");
          mRow.className = "sv-inspector-value";

          var typeLabelFn = _measureTypeLabels[m.type];
          var tLabel = typeLabelFn ? typeLabelFn() : m.type;
          var atoms = Array.isArray(m.atoms) ? m.atoms : [];
          var atomStr = atoms.map(function (a) { return String(a); }).join("-");
          var valStr = m.value != null ? (m.type === "distance" ? m.value.toFixed(3) + " \u00c5" : m.value.toFixed(1) + "\u00b0") : "";

          var applied = !!m._applied;
          if (applied) {
            var appliedLabel = _t("structure.measure.applied", STR.MEASURE_APPLIED);
            var recomputedVal = m.value != null ? (m.type === "distance" ? m.value.toFixed(3) + " \u00c5" : m.value.toFixed(1) + "\u00b0") : "";
            mRow.textContent = tLabel + " \u00b7 " + atomStr + " \u00b7 " + recomputedVal + " \u00b7 " + appliedLabel;
            mRow.style.color = "var(--sv-green)";
          } else {
            mRow.textContent = tLabel + " \u00b7 " + atomStr + " \u00b7 " + valStr;
          }
          measDiv.appendChild(mRow);

          if (!applied) {
            var editRow = document.createElement("div");
            editRow.className = "sv-edit-row";
            var targetInput = document.createElement("input");
            targetInput.type = "number";
            targetInput.className = "sv-edit-btn";
            targetInput.style.width = "80px";
            targetInput.style.padding = "2px 4px";
            targetInput.style.fontSize = "11px";
            if (m.value != null) targetInput.value = m.type === "distance" ? m.value.toFixed(3) : m.value.toFixed(1);
            var range = _measureRange[m.type] || [0, 999];
            targetInput.min = String(range[0]);
            targetInput.max = String(range[1]);
            targetInput.step = m.type === "distance" ? "0.01" : "1";
            editRow.appendChild(targetInput);

            var applyBtn = document.createElement("button");
            applyBtn.className = "sv-edit-btn";
            applyBtn.textContent = _t("structure.measure.apply", STR.MEASURE_APPLY);
            applyBtn.addEventListener("click", function () {
              var targetNum = parseFloat(targetInput.value);
              if (isNaN(targetNum)) return;
              var kind = _measureKindMap[m.type];
              if (!kind) return;
              var r = _measureRange[m.type] || [0, 999];
              if (targetNum < r[0] || targetNum > r[1]) {
                var warn = document.createElement("div");
                warn.className = "sv-edit-collision";
                warn.textContent = _t("structure.measure.range_warn", STR.MEASURE_RANGE_WARN);
                editRow.appendChild(warn);
                return;
              }
              if (typeof window === "undefined" || !window.ACPStructureEditor ||
                  typeof window.ACPStructureEditor.applyMeasuredEdit !== "function") return;
              var capturedType = m.type;
              var capturedAtoms = atoms.slice();
              var result = window.ACPStructureEditor.applyMeasuredEdit(kind, capturedAtoms, targetNum);
              if (result && result.ok) {
                _svMeasureAppliedIds = {};
                try {
                  if (typeof molDoc !== "undefined" && Array.isArray(molDoc.measures) &&
                      typeof measurementValue === "function") {
                    var recomputed = measurementValue(capturedType, capturedAtoms);
                    molDoc.measures.push({
                      type: capturedType,
                      atoms: capturedAtoms,
                      value: recomputed,
                      _applied: true,
                    });
                  }
                } catch (_) { /* re-seed is best-effort */ }
                _renderDrawerContent("measure");
              } else if (result && result.reason) {
                var err = document.createElement("div");
                err.className = "sv-edit-collision";
                err.textContent = result.reason;
                editRow.appendChild(err);
              }
            });
            editRow.appendChild(applyBtn);
            measDiv.appendChild(editRow);
          }
        })(mi);
      }
    }
    body.appendChild(measDiv);

    if (structureViewerState.restoredMeasurements && structureViewerState.restoredMeasurements.length) {
      var restoredDiv = document.createElement("div");
      restoredDiv.className = "sv-inspector-section sv-view-restored";
      _ariaGroup(restoredDiv, _t("structure.view.restored_measurements", STR.VIEW_RESTORED_MEASUREMENTS));
      var rLbl = document.createElement("div");
      rLbl.className = "sv-inspector-label";
      rLbl.textContent = _t("structure.view.restored_measurements", STR.VIEW_RESTORED_MEASUREMENTS);
      restoredDiv.appendChild(rLbl);
      for (var ri = 0; ri < structureViewerState.restoredMeasurements.length; ri++) {
        var rec = structureViewerState.restoredMeasurements[ri];
        var rLine = document.createElement("div");
        rLine.className = "sv-inspector-value sv-muted";
        rLine.textContent = _esc(rec.type) + " \u00b7 " + (rec.atoms || []).join("-") + (rec.value != null ? " \u00b7 " + rec.value.toFixed(3) : "");
        restoredDiv.appendChild(rLine);
      }
      body.appendChild(restoredDiv);
    }

    body.appendChild(_renderEditPanel());
  }

  function _renderMoreDrawer(body) {
    var payload = structureViewerState.payload;
    var entries = (payload && payload.entries) || [];
    var entry = null;
    for (var i = 0; i < entries.length; i++) {
      if (entries[i].id === structureViewerState.selectedEntryId) { entry = entries[i]; break; }
    }
    if (!entry) { body.textContent = _t("structure.no_entry", STR.NO_ENTRY); return; }
  }

  function toggleListDrawer() { /* legacy stub — no-op */ }
  function toggleInspectorDrawer() { /* legacy stub — no-op */ }

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
    LIST_VIRTUALIZE_THRESHOLD: LIST_VIRTUALIZE_THRESHOLD,
    TRAJECTORY_SAMPLE_THRESHOLD: TRAJECTORY_SAMPLE_THRESHOLD,
    TRAJECTORY_SAMPLE_TARGET: TRAJECTORY_SAMPLE_TARGET,
    LARGE_SYSTEM_ATOM_THRESHOLD: LARGE_SYSTEM_ATOM_THRESHOLD,
    virtualizeEntries: virtualizeEntries,
    sampleFrames: sampleFrames,
    viewStateStore: viewStateStore,
    saveViewState: saveViewState,
    restoreViewState: restoreViewState,
    clearViewState: clearViewState,
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
    openDrawer: openDrawer,
    closeDrawer: closeDrawer,
    _closeSwitcherDropdown: _closeSwitcherDropdown,
    _renderIrcFrameController: _renderIrcFrameController,
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
