/* file: js/modules/tsmode-editor/module.js */
/*
 * ACP 3.0 module — tsmode-editor (Track A / A-16 · P05 "TS Mode 编辑器").
 *
 * Classic script, no build, no ESM. Registers exactly one global key
 * (`window.ACPNextModules["tsmode-editor"]`); the Shell loader resolves the
 * factory and the returned instance is mounted into an arbitrary host element.
 *
 * Scope (A-16): load the TS-mode candidates (frequency sources + imaginary
 * modes) of the task focused in the WorkspaceContext, let the user choose the
 * frequency source and the optimization target by its NATIVE ORCA mode index
 * (never by list position), show the mode details (imaginary frequency, atom
 * count, source), preview/validate, and submit when valid.
 *
 * Data sources — EXISTING endpoints only, no new backend:
 *   GET  /api/v1/jobs/{job_id}/frequency-sources
 *        -> {job_id, sources:[{entry_id,item_id,label,output_path,hess_path,
 *            complete,hessian_available,imaginary_count,atom_count,mode_count,
 *            origin}], warnings:[]}
 *   GET  /api/v1/jobs/{job_id}/structure-viewer/entries/{entry_id}/vibrations
 *        -> {available, reason, modes:[{mode_index,frequency_cm1,imaginary,
 *            ir_intensity,vectors}], atom_count, source, imaginary_count}
 *        `mode_index` is ORCA's native printed mode index (the backend keeps
 *        it verbatim, `acp/results/frequencies.py`); it is NEVER the row
 *        position. This module forwards that very number as
 *        `source_mode_index`.
 *   POST /api/v1/preflight   -> zero-side-effect feasibility preview
 *   POST /api/v1/jobs        -> the EXISTING tsmode submission entry (the
 *        shared job-submission route, whose tsmode branch runs
 *        `_prepare_tsmode_input`: source job / entry / Hessian / native mode
 *        resolution / launch gate, then rewrites the spec). There is no
 *        tsmode-specific `/tsmode/...` submit route in `v1_routes.py`, and
 *        this module does not invent one. Submit is only reachable after a
 *        feasible preview of the exact same plan body.
 *
 * Boundary decisions pinned by acp2_tsmode_editor.test.js:
 *   - data only through the injected request seam (`deps.request` →
 *     `deps.client.transport.request` → `window.ACPClient.transport.request`);
 *     the module never touches the global network primitives (browser fetch,
 *     XHR or beacon) and never reads DOM geometry;
 *   - no second viewer: no 3D scene, no canvas, no geometry read. The panel
 *     only renders numbers/text from the two read endpoints above;
 *   - no local frequency computation and no guessed modes: rows come from the
 *     vibrations payload, selection stores the payload's `mode_index`, and an
 *     unavailable/empty/404 mode list disables the target instead of
 *     synthesizing one (see README §8 for the entry-id gap);
 *   - no task mutation beyond the one existing submit command.
 *
 * Four states live on the module root `data-acp-module-state` =
 * empty | loading | ready | error. Mode loading is a SEPARATE sub-state
 * `data-acp-modes-state` = idle | loading | ready | unavailable | error, and
 * command busy state is `data-acp-busy` = none | preview | submit, so a
 * running preview never hides the source/mode form.
 *
 * Guards: `state.generation` (mount/dispose) plus a per-request token for
 * sources / modes / preview / submit; every async result is dropped before it
 * can write the DOM when the generation/token no longer matches.
 * dispose() is idempotent, removes the DOM it created, its context
 * subscription, its `acp:langchange` listener and every owned timer.
 */
(function () {
  "use strict";

  window.ACPNextModules = window.ACPNextModules || {};

  var MODULE_ID = "tsmode-editor";
  var API = "/api/v1";
  var WORKFLOW = "tsmode";

  /* Local zh dictionary (module copy). The Shell dictionary is consulted
   * first; `ACPShell.i18n.t()` echoes the key when no translation exists, so
   * these values are the real fallback until A-61 adds `next.tsmode.*`
   * (README §6 lists the key set). Static chrome also carries data-i18n. */
  var ZH = {
    "next.tsmode.title": "TS 模式优化编辑器",
    "next.tsmode.subtitle": "从频率源选择原生模式序号 · 定向 OptTS",
    "next.tsmode.task_line": "源任务 {jobId}",
    "next.tsmode.task_unknown": "未绑定任务（在任务面板选中一个已完成频率/批量任务）",
    "next.tsmode.empty": "尚无可用频率源",
    "next.tsmode.empty_no_task": "未绑定任务。请先在任务面板选中一个含频率结果的任务。",
    "next.tsmode.empty_no_sources": "该任务没有可发现的频率源（需要 ORCA 频率输出及其 Hessian）。",
    "next.tsmode.loading": "正在读取频率源…",
    "next.tsmode.error_title": "频率源读取失败",
    "next.tsmode.errorTitle": "TS 模式编辑器不可用",
    "next.tsmode.errorMessage": "tsmode-editor 模块加载失败，请查看控制台。",
    "next.tsmode.retry": "重试",
    "next.tsmode.source_title": "1 · 频率来源",
    "next.tsmode.source_hint": "只列出该任务内可发现的 ORCA 频率输出；缺少 Hessian 的来源不能作为优化目标。",
    "next.tsmode.source_empty": "无可用频率源",
    "next.tsmode.source_loading": "正在读取频率源…",
    "next.tsmode.source_error": "频率源读取失败：{message}",
    "next.tsmode.source_row_meta": "原子 {atoms} · 模式 {modes} · 虚频 {imag}",
    "next.tsmode.source_hess_available": "Hessian 可用",
    "next.tsmode.source_hess_missing": "缺少 Hessian",
    "next.tsmode.source_entry": "条目 {entry}",
    "next.tsmode.source_selected": "已选择",
    "next.tsmode.source_select": "选择此频率源",
    "next.tsmode.source_pending_fetch": "远程任务的频率数据尚未取回（pending_fetch）。",
    "next.tsmode.mode_title": "2 · 目标模式（原生模式序号）",
    "next.tsmode.mode_hint_native": "模式序号是 ORCA 输出的原始编号（例如 #6、#9），不是列表位置；提交时原样作为 source_mode_index。",
    "next.tsmode.mode_hint_select_source": "先选择一个频率来源。",
    "next.tsmode.mode_loading": "正在读取模式…",
    "next.tsmode.mode_error": "模式读取失败：{message}",
    "next.tsmode.mode_unavailable": "该来源没有可用模式数据：{reason}",
    "next.tsmode.mode_empty": "该来源没有虚频模式 — 建议使用普通 optimize / frequency。",
    "next.tsmode.mode_count": "共 {modes} 个模式 · {imag} 个虚频",
    "next.tsmode.mode_row_index": "#{index}",
    "next.tsmode.mode_row_frequency": "{frequency} cm⁻¹",
    "next.tsmode.mode_row_ir": "IR {value}",
    "next.tsmode.mode_select": "设为优化目标",
    "next.tsmode.mode_selected": "已确认目标",
    "next.tsmode.mode_detail_title": "模式详情",
    "next.tsmode.mode_detail_index": "原生模式序号",
    "next.tsmode.mode_detail_frequency": "虚频",
    "next.tsmode.mode_detail_atoms": "原子数",
    "next.tsmode.mode_detail_source": "模式来源",
    "next.tsmode.mode_detail_vectors": "位移向量",
    "next.tsmode.mode_detail_ir": "IR 强度",
    "next.tsmode.mode_detail_entry": "频率条目",
    "next.tsmode.mode_source_product": "normal_modes 产品",
    "next.tsmode.mode_source_historical": "历史 ORCA 投影",
    "next.tsmode.mode_source_unknown": "未知来源",
    "next.tsmode.settings_title": "3 · 优化设置",
    "next.tsmode.settings_hint": "方法/基组/电荷/多重度继承自频率源；本面板不覆盖它们。",
    "next.tsmode.allow_unverified": "允许未验证映射（显式）",
    "next.tsmode.allow_unverified_warn": "模式映射尚未针对真实 ORCA 版本验证（P0 待完成）。勾选即表示你确认以未验证映射继续；服务端 launch gate 会据此放行。",
    "next.tsmode.final_frequency": "最终频率验证",
    "next.tsmode.max_iterations": "最大迭代",
    "next.tsmode.settings_default": "默认",
    "next.tsmode.review_title": "4 · 预览与提交",
    "next.tsmode.summary": "配置摘要",
    "next.tsmode.summary_lines": "工作流 {workflow}\n源任务 {jobId} · 频率条目 {entry}\n原生模式序号 {mode} · 虚频 {frequency}\n原子数 {atoms}\n映射验证 {verified}\n设置 {settings}",
    "next.tsmode.summary_no_mode": "—",
    "next.tsmode.preview": "预检",
    "next.tsmode.preflighting": "预检中…",
    "next.tsmode.preview_idle": "尚未预检。预检由服务端零副作用执行（不建任务、不写盘）。",
    "next.tsmode.preview_ok": "预检通过：{digest}",
    "next.tsmode.preview_failed": "预检未通过",
    "next.tsmode.preview_error": "预检请求失败：{message}",
    "next.tsmode.preview_blocked": "当前配置未通过本地校验，未发送预检。",
    "next.tsmode.preview_mapping_note": "预检只校验来源/Hessian 可行性；模式映射与 launch gate 在提交时由服务端判定。",
    "next.tsmode.advisories": "预检提示",
    "next.tsmode.submit": "提交 TS 模式优化",
    "next.tsmode.submitting": "提交中…",
    "next.tsmode.submit_blocked": "预检通过后才能提交。",
    "next.tsmode.submit_already": "该计划已提交，未重复发送。",
    "next.tsmode.submit_error": "提交失败：{message}",
    "next.tsmode.receipt": "已提交",
    "next.tsmode.receipt_body": "任务 {jobId} · 状态 {status} · 第 {attempt} 次尝试",
    "next.tsmode.receipt_digest": "计划摘要 {digest}",
    "next.tsmode.receipt_replay": "幂等重放：返回的是同一任务的既有回执。",
    "next.tsmode.open_task": "在任务面板打开",
    "next.tsmode.note": "提交走既有任务提交端点（workflow=tsmode）；服务端会重新校验来源、Hessian、原生模式映射与 launch gate。本面板不计算频率，也不猜测模式。",
    "next.tsmode.err.task_missing": "未绑定任务。",
    "next.tsmode.err.client_missing": "客户端未注入（ACPClient.transport），无法读取或提交。",
    "next.tsmode.err.source_missing": "请先选择一个频率来源。",
    "next.tsmode.err.source_unknown": "所选频率来源不在当前任务列表中。",
    "next.tsmode.err.hessian_missing": "该频率来源缺少 Hessian，不能用于 TS 模式优化。",
    "next.tsmode.err.mode_missing": "请先选择一个虚频模式作为优化目标。",
    "next.tsmode.err.mode_unknown": "所选模式不在当前模式列表中（模式列表可能已变化）。",
    "next.tsmode.err.mode_not_imaginary": "只有虚频模式可以驱动定向 TS 优化。",
    "next.tsmode.err.mode_index_invalid": "模式序号必须是非负整数。",
    "next.tsmode.err.max_iterations_invalid": "最大迭代必须是 ≥1 的整数。",
    "next.tsmode.err.modes_entry_mismatch": "频率条目 {entry} 没有对应的结构查看器条目（vibrations 404）。本模块不会猜测其它条目 id。",
    "next.tsmode.err.job_not_found": "任务不存在或无权访问。",
    "next.tsmode.err.atom_count_mismatch": "频率源原子数（{source}）与模式表原子数（{modes}）不一致，请先核对来源数据。",
    "next.tsmode.err.mapping_unverified": "当前未允许未验证映射：服务端 launch gate 在缺少已验证 ORCA 版本时会拒绝提交。",
    "next.tsmode.err.mode_mapping_unsupported": "服务端拒绝：模式映射未针对已部署 ORCA 版本验证（mode_mapping_unsupported）。如确认继续，请勾选“允许未验证映射”后重新预检。",
    "next.tsmode.err.mode_mapping_ambiguous": "服务端拒绝：目标模式无法唯一映射到优化器模式（近简并），请换一个模式或改用普通 TS 优化。",
    "next.tsmode.err.target_mode_invalid": "服务端拒绝：目标模式无效（不存在/非虚频/向量不完整）。",
    "next.tsmode.err.frequency_source_incomplete": "服务端拒绝：频率来源不完整（缺源任务/条目/Hessian）。",
    "next.tsmode.err.preflight_stale": "预检已过期（计划指纹不匹配），请重新预检。",
    "next.tsmode.mode_reason.pending_fetch": "远程任务的频率数据尚未取回（pending_fetch）。",
    "next.tsmode.mode_reason.no_normal_modes": "该任务没有 normal_modes 数据（无模式表）。"
  };

  /* ── shared helpers ─────────────────────────────────────────────── */

  function t(key, vars) {
    var shell = typeof window !== "undefined" && window.ACPShell ? window.ACPShell.i18n : null;
    var value = key;
    if (shell && typeof shell.t === "function") {
      var translated = shell.t(key, vars);
      if (translated && translated !== key) return translated;
    }
    value = Object.prototype.hasOwnProperty.call(ZH, key) ? ZH[key] : key;
    if (vars) {
      Object.keys(vars).forEach(function (name) {
        value = value.split("{" + name + "}").join(String(vars[name]));
      });
    }
    return value;
  }

  function el(doc, tag, cls, text) {
    var node = doc.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function i18nEl(doc, tag, cls, key, vars) {
    var node = el(doc, tag, cls, t(key, vars));
    node.setAttribute("data-i18n", key);
    return node;
  }

  function clearChildren(node) {
    if (!node) return;
    if (typeof node.replaceChildren === "function") node.replaceChildren();
    else node.textContent = "";
  }

  function textOf(value) {
    return value == null ? "" : String(value);
  }

  function isFiniteNumber(value) {
    return typeof value === "number" && isFinite(value);
  }

  function isInteger(value) {
    return typeof value === "number" && isFinite(value) && Math.floor(value) === value;
  }

  function encodeSegment(value) {
    return encodeURIComponent(textOf(value));
  }

  function integerText(value) {
    var raw = textOf(value).trim();
    if (!/^[+-]?\d+$/.test(raw)) return null;
    return parseInt(raw, 10);
  }

  /**
   * Human-readable error from the transport Error (which carries `status` and
   * the parsed `body`). Handles the three shapes this API emits:
   *   {detail: {code, message, params}}   application envelope (preflight/jobs)
   *   {detail: {error, detail}}           TsmodeError mapping
   *   {detail: "Job not found: x"}        plain HTTPException
   */
  function describeError(error) {
    if (!error) return "";
    var body = error.body;
    if (body && typeof body === "object") {
      var detail = body.detail;
      if (detail && typeof detail === "object" && !Array.isArray(detail)) {
        var code = detail.error || detail.code;
        var message = detail.detail || detail.message;
        if (code || message) return (code ? String(code) + ": " : "") + String(message || "");
      }
      if (typeof detail === "string" && detail) return detail;
    }
    return error.message ? String(error.message) : String(error);
  }

  function errorCodeOf(error) {
    var body = error && error.body;
    var detail = body && typeof body === "object" ? body.detail : null;
    if (detail && typeof detail === "object" && !Array.isArray(detail)) {
      return textOf(detail.error || detail.code);
    }
    return "";
  }

  function newIdempotencyKey() {
    try {
      if (typeof crypto !== "undefined" && crypto && typeof crypto.randomUUID === "function") {
        return "next-tsmode-" + crypto.randomUUID();
      }
    } catch (e) { /* fall through to the timestamp/random key */ }
    return "next-tsmode-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
  }

  function formatFrequency(value) {
    if (!isFiniteNumber(Number(value))) return "—";
    return Number(value).toFixed(2);
  }

  /* ── payload normalization (pure) ───────────────────────────────── */

  /** Normalize `GET .../frequency-sources`; never fabricates a source. */
  function normalizeSources(payload) {
    var raw = payload && Array.isArray(payload.sources) ? payload.sources : [];
    var sources = [];
    var seen = Object.create(null);
    raw.forEach(function (item) {
      if (!item || typeof item !== "object") return;
      var entryId = textOf(item.entry_id).trim();
      if (!entryId || seen[entryId]) return;
      seen[entryId] = true;
      sources.push({
        entryId: entryId,
        itemId: item.item_id == null ? "" : String(item.item_id),
        label: textOf(item.label) || entryId,
        outputPath: textOf(item.output_path),
        hessPath: item.hess_path == null ? "" : String(item.hess_path),
        hessianAvailable: item.hessian_available === true,
        imaginaryCount: isFiniteNumber(Number(item.imaginary_count)) ? Number(item.imaginary_count) : 0,
        atomCount: isFiniteNumber(Number(item.atom_count)) ? Number(item.atom_count) : 0,
        modeCount: isFiniteNumber(Number(item.mode_count)) ? Number(item.mode_count) : 0
      });
    });
    var warnings = [];
    if (payload && Array.isArray(payload.warnings)) {
      payload.warnings.forEach(function (value) { warnings.push(textOf(value)); });
    }
    return { sources: sources, warnings: warnings };
  }

  /**
   * Normalize the vibrations payload. `modeIndex` is copied VERBATIM from
   * `mode_index` (native ORCA index) — the array position is never used as an
   * identity. A mode without an integer native index is skipped, not guessed.
   */
  function normalizeModes(payload) {
    if (!payload || payload.available === false) {
      return {
        available: false,
        reason: textOf(payload && payload.reason),
        modes: [],
        atomCount: 0,
        source: "",
        imaginaryCount: null
      };
    }
    var raw = Array.isArray(payload.modes) ? payload.modes : [];
    var modes = [];
    var seen = Object.create(null);
    raw.forEach(function (item) {
      if (!item || typeof item !== "object") return;
      var index = Number(item.mode_index);
      if (!isInteger(index) || index < 0) return;
      if (seen[index]) return;
      seen[index] = true;
      modes.push({
        modeIndex: index,
        frequencyCm1: isFiniteNumber(Number(item.frequency_cm1)) ? Number(item.frequency_cm1) : NaN,
        imaginary: item.imaginary === true || (isFiniteNumber(Number(item.frequency_cm1)) && Number(item.frequency_cm1) < 0),
        irIntensity: item.ir_intensity == null || !isFiniteNumber(Number(item.ir_intensity))
          ? null : Number(item.ir_intensity),
        vectorCount: Array.isArray(item.vectors) ? item.vectors.length : 0
      });
    });
    modes.sort(function (a, b) { return a.modeIndex - b.modeIndex; });
    return {
      available: true,
      reason: "",
      modes: modes,
      atomCount: isFiniteNumber(Number(payload.atom_count)) ? Number(payload.atom_count) : 0,
      source: textOf(payload.source),
      imaginaryCount: isFiniteNumber(Number(payload.imaginary_count))
        ? Number(payload.imaginary_count) : null
    };
  }

  /** Imaginary modes only; the native index is preserved (never renumbered). */
  function imaginaryModes(modes) {
    return (Array.isArray(modes) ? modes : []).filter(function (mode) {
      return mode && mode.imaginary === true;
    });
  }

  /**
   * Client-side pre-submit validation. The server stays authoritative; these
   * checks only prevent locally-known-invalid requests from leaving the page.
   *
   * @returns {{ok:boolean, errors:Array<{code,field,vars?}>,
   *            warnings:Array<{code,vars?}>, mode:(object|null)}}
   */
  function validateSelection(input) {
    input = input || {};
    var errors = [];
    var warnings = [];
    var jobId = textOf(input.jobId).trim();
    var source = input.source && typeof input.source === "object" ? input.source : null;
    var modesState = input.modes || {};
    var items = Array.isArray(modesState.modes) ? modesState.modes : [];
    var settings = input.settings || {};
    var modeIndex = input.modeIndex == null ? null : Number(input.modeIndex);

    if (!jobId) errors.push({ code: "task_missing", field: "task" });
    if (!source) {
      errors.push({ code: "source_missing", field: "source" });
    } else {
      if (!source.entryId) errors.push({ code: "source_unknown", field: "source" });
      if (source.hessianAvailable !== true) errors.push({ code: "hessian_missing", field: "source" });
    }
    if (modeIndex == null) {
      errors.push({ code: "mode_missing", field: "mode" });
    } else if (!isInteger(modeIndex) || modeIndex < 0) {
      errors.push({ code: "mode_index_invalid", field: "mode" });
    } else if (modesState.available === true) {
      var match = items.filter(function (mode) { return mode.modeIndex === modeIndex; })[0];
      if (!match) errors.push({ code: "mode_unknown", field: "mode" });
      else if (match.imaginary !== true) errors.push({ code: "mode_not_imaginary", field: "mode" });
    }

    if (source && modesState.available === true && source.atomCount > 0 &&
        isFiniteNumber(modesState.atomCount) && modesState.atomCount > 0 &&
        source.atomCount !== modesState.atomCount) {
      warnings.push({
        code: "atom_count_mismatch",
        vars: { source: source.atomCount, modes: modesState.atomCount }
      });
    }
    if (!input.allowUnverifiedMapping) {
      warnings.push({ code: "mapping_unverified" });
    }

    var maxIterations = textOf(settings.maxIterations).trim();
    if (maxIterations) {
      var maxValue = integerText(maxIterations);
      if (maxValue === null || maxValue < 1) {
        errors.push({ code: "max_iterations_invalid", field: "settings.maxIterations" });
      }
    }

    return {
      ok: errors.length === 0,
      errors: errors,
      warnings: warnings,
      mode: modeIndex == null ? null :
        (items.filter(function (mode) { return mode.modeIndex === modeIndex; })[0] || null)
    };
  }

  /**
   * Frozen tsmode input. Only declared `_TSMODE_INPUT_KEYS` are emitted; no
   * `method` block is ever sent (the workflow contract accepts zero method
   * keys). `source_mode_index` is the native mode index.
   *
   * Only knobs proven end-to-end through the HTTP preview→submit chain are
   * exposed (README §5): `allow_unverified_mapping`, `final_frequency` and the
   * integer `max_iterations`. `trust_radius` is deliberately absent — the
   * canonical plan digest rejects JSON floats, and the workflow adapter
   * (`_optional_float`) ignores decimal strings, so a float knob cannot be
   * previewed and applied at the same time. `recalc_hess` (boolean vs the
   * adapter's int-only reader) and `retry_limit` (`0` coerced by `or 2`) have
   * the same "accepted but silently altered" problem; see README §9.
   */
  function buildInput(input) {
    input = input || {};
    var out = {
      source_job_id: textOf(input.jobId),
      entry_id: textOf(input.entryId),
      source_mode_index: Number(input.modeIndex),
      allow_unverified_mapping: input.allowUnverifiedMapping === true,
      final_frequency: input.finalFrequency !== false
    };
    var maxIterations = integerText(input.maxIterations);
    if (maxIterations !== null) out.max_iterations = maxIterations;
    return out;
  }

  /** Submit/preflight body: workflow + display name + the frozen input. */
  function buildRequest(input) {
    var body = {
      workflow: WORKFLOW,
      input: buildInput(input)
    };
    if (input && input.name) body.name = textOf(input.name);
    return body;
  }

  function defaultJobName(entryId, modeIndex) {
    var entry = textOf(entryId).trim();
    var mode = textOf(modeIndex).trim();
    if (!entry || !mode) return "";
    return "TSMode_" + entry + "_mode" + mode;
  }

  /* Deterministic UI cache key (never the server canonical digest). */
  function planKeyOf(request) {
    if (!request) return "";
    try {
      return JSON.stringify(request);
    } catch (e) {
      return "";
    }
  }

  function sourceLabel(source) {
    if (!source) return "";
    return source.label || source.entryId;
  }

  function modeSourceLabel(value) {
    if (value === "product") return t("next.tsmode.mode_source_product");
    if (value === "historical_projection") return t("next.tsmode.mode_source_historical");
    return value ? String(value) : t("next.tsmode.mode_source_unknown");
  }

  function settingsSummary(settings) {
    var parts = [];
    parts.push((settings.allowUnverifiedMapping ? "允许未验证映射" : "要求已验证映射"));
    parts.push(settings.finalFrequency === false ? "跳过最终频率" : "最终频率验证");
    var maxIterations = textOf(settings.maxIterations).trim();
    if (maxIterations) parts.push("最大迭代 " + maxIterations);
    return parts.join(" · ");
  }

  /* ── module instance ───────────────────────────────────────────── */

  function createInstance(deps) {
    deps = deps || {};
    var doc = deps.doc || (typeof window !== "undefined" ? window.document : null);
    var context = deps.context || null;
    var commands = deps.commands || null;

    function requestFnFor() {
      if (typeof deps.request === "function") return deps.request;
      if (deps.client && deps.client.transport && typeof deps.client.transport.request === "function") {
        return function (path, opts) { return deps.client.transport.request(path, opts); };
      }
      if (typeof window !== "undefined" && window.ACPClient && window.ACPClient.transport &&
          typeof window.ACPClient.transport.request === "function") {
        return function (path, opts) { return window.ACPClient.transport.request(path, opts); };
      }
      return null;
    }

    var requestFn = requestFnFor();

    var state = {
      generation: 0,
      mounted: false,
      disposed: false,
      suspended: false,
      host: null,
      root: null,
      snapshot: null,
      taskJobId: "",
      projectId: "",
      sources: { status: "idle", items: [], warnings: [], error: null, token: 0 },
      selectedEntryId: "",
      modes: {
        status: "idle", token: 0, entryId: "", reason: "",
        items: [], atomCount: 0, source: "", imaginaryCount: null, error: null
      },
      selectedModeIndex: null,
      settings: {
        allowUnverifiedMapping: false,
        finalFrequency: true,
        maxIterations: ""
      },
      configRevision: 0,
      validation: { ok: false, errors: [], warnings: [], mode: null },
      preview: {
        status: "idle", token: 0, digest: null, planKey: null,
        reasons: [], advisories: [], error: null, revision: -1, request: null
      },
      submit: {
        status: "idle", token: 0, receipt: null, receiptKey: "", error: null,
        idempotencyKey: null, keyPlan: null
      },
      busy: "none",
      retry: null,
      ui: {},
      unlisten: [],
      unsubscribeContext: null
    };

    function alive(gen) {
      return !state.disposed && state.mounted && state.generation === gen;
    }

    function listen(node, type, handler) {
      if (!node || typeof node.addEventListener !== "function") return;
      node.addEventListener(type, handler);
      state.unlisten.push(function () {
        if (typeof node.removeEventListener === "function") node.removeEventListener(type, handler);
      });
    }

    /* ── DOM skeleton ─────────────────────────────────────────────── */

    function buildDom() {
      var root = el(doc, "div", "acp-tsmode");
      root.setAttribute("data-acp-module-state", "empty");
      root.setAttribute("data-acp-busy", "none");
      root.setAttribute("data-acp-modes-state", "idle");
      root.setAttribute("data-acp-valid", "false");
      root.setAttribute("data-acp-allow-unverified", "false");
      root.setAttribute("data-acp-task", "");
      root.setAttribute("data-acp-selected-entry", "");
      root.setAttribute("data-acp-selected-mode", "");

      var head = el(doc, "div", "acp-tsmode-head");
      head.appendChild(i18nEl(doc, "h2", "acp-tsmode-title", "next.tsmode.title"));
      head.appendChild(i18nEl(doc, "p", "acp-tsmode-sub", "next.tsmode.subtitle"));
      var taskLine = el(doc, "p", "acp-tsmode-task");
      taskLine.setAttribute("data-role", "task-line");
      taskLine.textContent = t("next.tsmode.task_unknown");
      head.appendChild(taskLine);
      root.appendChild(head);

      var body = el(doc, "div", "acp-tsmode-body");
      root.appendChild(body);

      /* 1 — frequency source */
      var sourceSection = el(doc, "section", "acp-tsmode-section");
      sourceSection.appendChild(i18nEl(doc, "h3", "acp-tsmode-heading", "next.tsmode.source_title"));
      sourceSection.appendChild(i18nEl(doc, "p", "acp-tsmode-hint", "next.tsmode.source_hint"));
      var sourceStatus = el(doc, "p", "acp-tsmode-status");
      sourceStatus.setAttribute("data-role", "source-status");
      sourceStatus.setAttribute("role", "status");
      sourceSection.appendChild(sourceStatus);
      var sourceWarnings = el(doc, "ul", "acp-tsmode-warnings");
      sourceWarnings.setAttribute("data-role", "source-warnings");
      sourceWarnings.hidden = true;
      sourceSection.appendChild(sourceWarnings);
      var sourceList = el(doc, "div", "acp-tsmode-source-list");
      sourceList.setAttribute("data-role", "source-list");
      sourceSection.appendChild(sourceList);
      body.appendChild(sourceSection);

      /* 2 — native mode index target */
      var modeSection = el(doc, "section", "acp-tsmode-section");
      modeSection.appendChild(i18nEl(doc, "h3", "acp-tsmode-heading", "next.tsmode.mode_title"));
      modeSection.appendChild(i18nEl(doc, "p", "acp-tsmode-hint", "next.tsmode.mode_hint_native"));
      var modeStatus = el(doc, "p", "acp-tsmode-status");
      modeStatus.setAttribute("data-role", "mode-status");
      modeStatus.setAttribute("role", "status");
      modeStatus.textContent = t("next.tsmode.mode_hint_select_source");
      modeSection.appendChild(modeStatus);
      var modeMeta = el(doc, "p", "acp-tsmode-meta");
      modeMeta.setAttribute("data-role", "mode-meta");
      modeMeta.hidden = true;
      modeSection.appendChild(modeMeta);
      var modeList = el(doc, "div", "acp-tsmode-mode-list");
      modeList.setAttribute("data-role", "mode-list");
      modeSection.appendChild(modeList);

      var detail = el(doc, "div", "acp-tsmode-detail");
      detail.setAttribute("data-role", "mode-detail");
      detail.hidden = true;
      detail.appendChild(i18nEl(doc, "h4", "acp-tsmode-detail-title", "next.tsmode.mode_detail_title"));
      var detailFields = el(doc, "dl", "acp-tsmode-detail-fields");
      detailFields.setAttribute("data-role", "mode-detail-fields");
      detail.appendChild(detailFields);
      modeSection.appendChild(detail);
      body.appendChild(modeSection);

      /* 3 — settings */
      var settingsSection = el(doc, "section", "acp-tsmode-section");
      settingsSection.appendChild(i18nEl(doc, "h3", "acp-tsmode-heading", "next.tsmode.settings_title"));
      settingsSection.appendChild(i18nEl(doc, "p", "acp-tsmode-hint", "next.tsmode.settings_hint"));

      var checks = el(doc, "div", "acp-tsmode-checks");
      var allowLabel = el(doc, "label", "acp-tsmode-check");
      var allowInput = el(doc, "input");
      allowInput.type = "checkbox";
      allowInput.checked = false;
      allowInput.setAttribute("data-role", "allow-unverified");
      allowLabel.appendChild(allowInput);
      allowLabel.appendChild(i18nEl(doc, "span", "acp-tsmode-check-text", "next.tsmode.allow_unverified"));
      checks.appendChild(allowLabel);
      var finalLabel = el(doc, "label", "acp-tsmode-check");
      var finalInput = el(doc, "input");
      finalInput.type = "checkbox";
      finalInput.checked = true;
      finalInput.setAttribute("data-role", "final-frequency");
      finalLabel.appendChild(finalInput);
      finalLabel.appendChild(i18nEl(doc, "span", "acp-tsmode-check-text", "next.tsmode.final_frequency"));
      checks.appendChild(finalLabel);
      settingsSection.appendChild(checks);

      var warning = i18nEl(doc, "p", "acp-tsmode-warning", "next.tsmode.allow_unverified_warn");
      warning.setAttribute("data-role", "unverified-warning");
      settingsSection.appendChild(warning);
      listen(allowInput, "change", onSettingsChanged);
      listen(finalInput, "change", onSettingsChanged);

      var grid = el(doc, "div", "acp-tsmode-grid");
      state.ui.fields = {};
      [
        { role: "max-iterations", key: "next.tsmode.max_iterations", field: "maxIterations" }
      ].forEach(function (spec) {
        var wrap = el(doc, "div", "acp-tsmode-field");
        var label = i18nEl(doc, "label", "acp-tsmode-label", spec.key);
        var input = el(doc, "input", "acp-tsmode-input");
        input.type = "text";
        input.setAttribute("data-role", spec.role);
        input.setAttribute("inputmode", "decimal");
        input.setAttribute("placeholder", t("next.tsmode.settings_default"));
        label.appendChild(input);
        wrap.appendChild(label);
        grid.appendChild(wrap);
        state.ui.fields[spec.field] = input;
        listen(input, "input", onSettingsChanged);
        listen(input, "change", onSettingsChanged);
      });
      settingsSection.appendChild(grid);
      state.ui.allowUnverified = allowInput;
      state.ui.finalFrequency = finalInput;
      body.appendChild(settingsSection);

      /* 4 — preview / submit */
      var reviewSection = el(doc, "section", "acp-tsmode-section");
      reviewSection.appendChild(i18nEl(doc, "h3", "acp-tsmode-heading", "next.tsmode.review_title"));
      reviewSection.appendChild(i18nEl(doc, "p", "acp-tsmode-subheading", "next.tsmode.summary"));
      var summary = el(doc, "pre", "acp-tsmode-summary");
      summary.setAttribute("data-role", "summary");
      reviewSection.appendChild(summary);

      var errorsList = el(doc, "ul", "acp-tsmode-errors");
      errorsList.setAttribute("data-role", "errors");
      reviewSection.appendChild(errorsList);
      var warningsList = el(doc, "ul", "acp-tsmode-warnings");
      warningsList.setAttribute("data-role", "warnings");
      reviewSection.appendChild(warningsList);
      state.ui.errors = errorsList;
      state.ui.warnings = warningsList;

      var previewStatus = el(doc, "p", "acp-tsmode-status");
      previewStatus.setAttribute("data-role", "preview-status");
      previewStatus.setAttribute("role", "status");
      previewStatus.textContent = t("next.tsmode.preview_idle");
      reviewSection.appendChild(previewStatus);
      var previewReasons = el(doc, "ul", "acp-tsmode-errors");
      previewReasons.setAttribute("data-role", "preview-reasons");
      previewReasons.hidden = true;
      reviewSection.appendChild(previewReasons);
      var advisoriesLabel = i18nEl(doc, "p", "acp-tsmode-subheading", "next.tsmode.advisories");
      advisoriesLabel.setAttribute("data-role", "advisories-label");
      advisoriesLabel.hidden = true;
      reviewSection.appendChild(advisoriesLabel);
      var advisories = el(doc, "ul", "acp-tsmode-advisories");
      advisories.setAttribute("data-role", "advisories");
      advisories.hidden = true;
      reviewSection.appendChild(advisories);

      var actions = el(doc, "div", "acp-tsmode-actions");
      var previewBtn = el(doc, "button", "acp-tsmode-btn", t("next.tsmode.preview"));
      previewBtn.type = "button";
      previewBtn.setAttribute("data-action", "preview");
      previewBtn.setAttribute("data-i18n", "next.tsmode.preview");
      listen(previewBtn, "click", function () { runPreview(); });
      var submitBtn = el(doc, "button", "acp-tsmode-btn acp-tsmode-btn--primary", t("next.tsmode.submit"));
      submitBtn.type = "button";
      submitBtn.disabled = true;
      submitBtn.setAttribute("data-action", "submit");
      submitBtn.setAttribute("data-i18n", "next.tsmode.submit");
      listen(submitBtn, "click", function () { submit(); });
      actions.appendChild(previewBtn);
      actions.appendChild(submitBtn);
      reviewSection.appendChild(actions);
      state.ui.previewBtn = previewBtn;
      state.ui.submitBtn = submitBtn;

      var receipt = el(doc, "div", "acp-tsmode-receipt");
      receipt.setAttribute("data-role", "receipt");
      receipt.setAttribute("role", "status");
      receipt.hidden = true;
      receipt.appendChild(i18nEl(doc, "h4", "acp-tsmode-receipt-title", "next.tsmode.receipt"));
      var receiptBody = el(doc, "p", "acp-tsmode-receipt-body");
      receiptBody.setAttribute("data-role", "receipt-body");
      receipt.appendChild(receiptBody);
      var receiptDigest = el(doc, "p", "acp-tsmode-mono");
      receiptDigest.setAttribute("data-role", "receipt-digest");
      receipt.appendChild(receiptDigest);
      var receiptReplay = i18nEl(doc, "p", "acp-tsmode-hint", "next.tsmode.receipt_replay");
      receiptReplay.setAttribute("data-role", "receipt-replay");
      receiptReplay.hidden = true;
      receipt.appendChild(receiptReplay);
      var openTaskBtn = el(doc, "button", "acp-tsmode-btn", t("next.tsmode.open_task"));
      openTaskBtn.type = "button";
      openTaskBtn.setAttribute("data-action", "open-task");
      openTaskBtn.setAttribute("data-i18n", "next.tsmode.open_task");
      openTaskBtn.hidden = true;
      listen(openTaskBtn, "click", function () { openReceiptTask(); });
      receipt.appendChild(openTaskBtn);
      reviewSection.appendChild(receipt);
      state.ui.receipt = receipt;
      state.ui.receiptBody = receiptBody;
      state.ui.receiptDigest = receiptDigest;
      state.ui.receiptReplay = receiptReplay;
      state.ui.openTaskBtn = openTaskBtn;

      var mappingNote = i18nEl(doc, "p", "acp-tsmode-hint", "next.tsmode.preview_mapping_note");
      mappingNote.setAttribute("data-role", "mapping-note");
      reviewSection.appendChild(mappingNote);
      body.appendChild(reviewSection);

      body.appendChild(i18nEl(doc, "p", "acp-tsmode-note", "next.tsmode.note"));

      /* empty hint + async overlay */
      var emptyHint = el(doc, "p", "acp-tsmode-empty");
      emptyHint.setAttribute("data-role", "empty-hint");
      root.appendChild(emptyHint);

      var overlay = el(doc, "div", "acp-tsmode-overlay");
      overlay.setAttribute("data-role", "overlay");
      overlay.hidden = false;
      var box = el(doc, "div", "acp-tsmode-box");
      var overlayMessage = el(doc, "p", "acp-tsmode-message");
      overlayMessage.setAttribute("data-role", "overlay-message");
      overlayMessage.setAttribute("aria-live", "polite");
      var overlayDetail = el(doc, "p", "acp-tsmode-detail-text");
      overlayDetail.setAttribute("data-role", "overlay-detail");
      overlayDetail.hidden = true;
      var retryBtn = el(doc, "button", "acp-tsmode-btn", t("next.tsmode.retry"));
      retryBtn.type = "button";
      retryBtn.setAttribute("data-action", "retry");
      retryBtn.setAttribute("data-i18n", "next.tsmode.retry");
      retryBtn.hidden = true;
      listen(retryBtn, "click", function () {
        if (typeof state.retry === "function") state.retry();
      });
      box.appendChild(overlayMessage);
      box.appendChild(overlayDetail);
      box.appendChild(retryBtn);
      overlay.appendChild(box);
      root.appendChild(overlay);
      state.ui.overlay = overlay;
      state.ui.overlayMessage = overlayMessage;
      state.ui.overlayDetail = overlayDetail;
      state.ui.retryBtn = retryBtn;

      state.root = root;
      state.host.appendChild(root);
      syncSettingsDom();
    }

    /* ── settings I/O ────────────────────────────────────────────── */

    function syncSettingsDom() {
      var ui = state.ui;
      if (!ui || !ui.allowUnverified) return;
      ui.allowUnverified.checked = state.settings.allowUnverifiedMapping === true;
      ui.finalFrequency.checked = state.settings.finalFrequency !== false;
      if (ui.fields.maxIterations) ui.fields.maxIterations.value = state.settings.maxIterations;
    }

    function readSettings() {
      var ui = state.ui;
      if (!ui || !ui.allowUnverified) return state.settings;
      state.settings = {
        allowUnverifiedMapping: !!ui.allowUnverified.checked,
        finalFrequency: !ui.finalFrequency || ui.finalFrequency.checked !== false,
        maxIterations: textOf(ui.fields.maxIterations && ui.fields.maxIterations.value).trim()
      };
      return state.settings;
    }

    function onSettingsChanged() {
      if (!state.mounted || state.disposed) return;
      readSettings();
      touchConfig();
      refreshValidation();
      render();
    }

    /* ── selection / request assembly ───────────────────────────── */

    function selectedSource() {
      if (!state.selectedEntryId) return null;
      var items = state.sources.items || [];
      for (var i = 0; i < items.length; i += 1) {
        if (items[i].entryId === state.selectedEntryId) return items[i];
      }
      return null;
    }

    function selectedMode() {
      if (state.selectedModeIndex == null) return null;
      var items = state.modes.items || [];
      for (var i = 0; i < items.length; i += 1) {
        if (items[i].modeIndex === state.selectedModeIndex) return items[i];
      }
      return null;
    }

    function validationInput() {
      return {
        jobId: state.taskJobId,
        source: selectedSource(),
        modes: {
          available: state.modes.status === "ready",
          modes: state.modes.items,
          atomCount: state.modes.atomCount
        },
        modeIndex: state.selectedModeIndex,
        settings: state.settings,
        allowUnverifiedMapping: state.settings.allowUnverifiedMapping
      };
    }

    function refreshValidation() {
      state.validation = validateSelection(validationInput());
    }

    function currentRequest() {
      if (!state.validation.ok) return null;
      var source = selectedSource();
      var mode = selectedMode();
      if (!source || !mode) return null;
      return buildRequest({
        jobId: state.taskJobId,
        entryId: source.entryId,
        modeIndex: mode.modeIndex,
        name: defaultJobName(source.entryId, mode.modeIndex),
        allowUnverifiedMapping: state.settings.allowUnverifiedMapping,
        finalFrequency: state.settings.finalFrequency,
        maxIterations: state.settings.maxIterations
      });
    }

    /** Any config edit invalidates the previous preview and in-flight command. */
    function touchConfig() {
      state.configRevision += 1;
      state.preview.token += 1;
      state.preview.status = "idle";
      state.preview.digest = null;
      state.preview.planKey = null;
      state.preview.reasons = [];
      state.preview.advisories = [];
      state.preview.error = null;
      state.preview.revision = -1;
      state.preview.request = null;
      state.submit.token += 1;
      state.submit.status = "idle";
      state.submit.error = null;
      state.busy = "none";
    }

    function canSubmit() {
      if (!state.validation.ok) return false;
      if (state.preview.status !== "ok" || !state.preview.digest) return false;
      if (state.preview.revision !== state.configRevision) return false;
      var request = currentRequest();
      if (!request) return false;
      if (state.preview.planKey !== planKeyOf(request)) return false;
      if (state.submit.receiptKey && state.submit.receiptKey === state.preview.planKey) return false;
      return true;
    }

    /* ── state application ──────────────────────────────────────── */

    function moduleStateName() {
      if (!state.taskJobId) return "empty";
      if (state.sources.status === "error") return "error";
      if (state.sources.status === "idle" || state.sources.status === "loading") return "loading";
      if (!state.sources.items.length) return "empty";
      return "ready";
    }

    function setModuleState() {
      if (!state.root) return;
      var name = moduleStateName();
      state.root.setAttribute("data-acp-module-state", name);
      state.root.setAttribute("data-acp-busy", state.busy);
      state.root.setAttribute("data-acp-modes-state", state.modes.status);
      state.root.setAttribute("data-acp-valid", state.validation.ok ? "true" : "false");
      state.root.setAttribute("data-acp-allow-unverified", state.settings.allowUnverifiedMapping ? "true" : "false");
      state.root.setAttribute("data-acp-task", state.taskJobId);
      state.root.setAttribute("data-acp-selected-entry", state.selectedEntryId);
      state.root.setAttribute("data-acp-selected-mode",
        state.selectedModeIndex == null ? "" : String(state.selectedModeIndex));
    }

    function renderTaskLine() {
      var node = findRole("task-line");
      if (!node) return;
      node.textContent = state.taskJobId
        ? t("next.tsmode.task_line", { jobId: state.taskJobId })
        : t("next.tsmode.task_unknown");
    }

    function renderOverlay() {
      var name = moduleStateName();
      var overlay = state.ui.overlay;
      if (!overlay) return;
      overlay.hidden = name === "ready";
      var message = "";
      var detail = "";
      var retry = false;
      if (name === "loading") {
        message = t("next.tsmode.loading");
      } else if (name === "error") {
        message = t("next.tsmode.error_title");
        detail = textOf(state.sources.error);
        retry = true;
      } else if (name === "empty") {
        message = t("next.tsmode.empty");
        detail = state.taskJobId ? t("next.tsmode.empty_no_sources") : t("next.tsmode.empty_no_task");
        retry = false;
      }
      state.ui.overlayMessage.textContent = message;
      state.ui.overlayDetail.textContent = detail;
      state.ui.overlayDetail.hidden = !detail;
      state.ui.retryBtn.hidden = !retry;
      var hint = findRole("empty-hint");
      if (hint) {
        hint.textContent = name === "empty" ? detail : "";
        hint.hidden = name !== "empty";
      }
    }

    function renderSources() {
      var list = findRole("source-list");
      var status = findRole("source-status");
      var warnings = findRole("source-warnings");
      if (!list || !status) return;
      clearChildren(list);
      if (warnings) {
        clearChildren(warnings);
        var items = state.sources.warnings || [];
        warnings.hidden = !items.length;
        items.forEach(function (value) {
          var li = el(doc, "li", "acp-tsmode-warning-item", value === "pending_fetch"
            ? t("next.tsmode.source_pending_fetch") : value);
          li.setAttribute("data-source-warning", value);
          warnings.appendChild(li);
        });
      }
      if (state.sources.status === "loading" || state.sources.status === "idle") {
        status.textContent = state.taskJobId ? t("next.tsmode.source_loading") : "";
        status.hidden = !state.taskJobId;
        return;
      }
      if (state.sources.status === "error") {
        status.textContent = t("next.tsmode.source_error", { message: textOf(state.sources.error) });
        status.hidden = false;
        return;
      }
      var sources = state.sources.items || [];
      status.hidden = sources.length > 0;
      status.textContent = sources.length ? "" : t("next.tsmode.source_empty");
      sources.forEach(function (source) {
        var row = el(doc, "button", "acp-tsmode-source-row" +
          (source.hessianAvailable ? "" : " is-disabled") +
          (source.entryId === state.selectedEntryId ? " is-selected" : ""));
        row.type = "button";
        row.disabled = source.hessianAvailable !== true;
        row.setAttribute("data-source-entry", source.entryId);
        row.setAttribute("data-hessian", source.hessianAvailable ? "true" : "false");
        row.setAttribute("data-selected", source.entryId === state.selectedEntryId ? "true" : "false");
        row.setAttribute("aria-pressed", source.entryId === state.selectedEntryId ? "true" : "false");
        row.setAttribute("aria-label", t("next.tsmode.source_select") + " " + source.entryId);

        var label = el(doc, "span", "acp-tsmode-source-label", source.label);
        row.appendChild(label);
        var meta = el(doc, "span", "acp-tsmode-source-meta", t("next.tsmode.source_row_meta", {
          atoms: source.atomCount,
          modes: source.modeCount,
          imag: source.imaginaryCount
        }));
        row.appendChild(meta);
        var badges = el(doc, "span", "acp-tsmode-badges");
        var hess = el(doc, "span", "acp-tsmode-badge " +
          (source.hessianAvailable ? "is-ok" : "is-missing"),
          source.hessianAvailable
            ? t("next.tsmode.source_hess_available")
            : t("next.tsmode.source_hess_missing"));
        hess.setAttribute("data-source-badge", source.hessianAvailable ? "hessian" : "no-hessian");
        badges.appendChild(hess);
        var entry = el(doc, "span", "acp-tsmode-badge",
          t("next.tsmode.source_entry", { entry: source.entryId }));
        entry.setAttribute("data-source-badge", "entry");
        badges.appendChild(entry);
        if (source.entryId === state.selectedEntryId) {
          var selected = el(doc, "span", "acp-tsmode-badge is-selected",
            t("next.tsmode.source_selected"));
          selected.setAttribute("data-source-badge", "selected");
          badges.appendChild(selected);
        }
        row.appendChild(badges);
        var pathText = source.outputPath ? source.outputPath : "";
        if (pathText) {
          var path = el(doc, "span", "acp-tsmode-mono", pathText);
          path.setAttribute("data-role", "source-path");
          row.appendChild(path);
        }
        if (source.hessianAvailable) {
          row.addEventListener("click", function () { selectSource(source.entryId); });
        }
        list.appendChild(row);
      });
    }

    function renderModes() {
      var list = findRole("mode-list");
      var status = findRole("mode-status");
      var meta = findRole("mode-meta");
      if (!list || !status) return;
      clearChildren(list);
      if (meta) meta.hidden = true;
      var modesState = state.modes.status;
      if (!state.selectedEntryId) {
        status.textContent = t("next.tsmode.mode_hint_select_source");
        status.hidden = false;
        return;
      }
      if (modesState === "idle" || modesState === "loading") {
        status.textContent = t("next.tsmode.mode_loading");
        status.hidden = false;
        return;
      }
      if (modesState === "unavailable") {
        status.textContent = t("next.tsmode.mode_unavailable", {
          reason: modeUnavailableReason(state.modes.reason)
        });
        status.hidden = false;
        return;
      }
      if (modesState === "error") {
        status.textContent = t("next.tsmode.mode_error", { message: textOf(state.modes.error) });
        status.hidden = false;
        return;
      }
      var imaginary = imaginaryModes(state.modes.items);
      if (meta) {
        meta.hidden = false;
        meta.textContent = t("next.tsmode.mode_count", {
          modes: state.modes.items.length,
          imag: imaginary.length
        });
      }
      if (!imaginary.length) {
        status.textContent = t("next.tsmode.mode_empty");
        status.hidden = false;
        return;
      }
      status.textContent = "";
      status.hidden = true;
      imaginary.forEach(function (mode) {
        var selected = state.selectedModeIndex === mode.modeIndex;
        var row = el(doc, "button", "acp-tsmode-mode-row" + (selected ? " is-selected" : ""));
        row.type = "button";
        row.setAttribute("data-mode-index", String(mode.modeIndex));
        row.setAttribute("data-selected", selected ? "true" : "false");
        row.setAttribute("aria-pressed", selected ? "true" : "false");
        row.setAttribute("aria-label", t("next.tsmode.mode_select") + " #" + mode.modeIndex);
        var index = el(doc, "span", "acp-tsmode-mode-index",
          t("next.tsmode.mode_row_index", { index: mode.modeIndex }));
        row.appendChild(index);
        var frequency = el(doc, "span", "acp-tsmode-mode-frequency",
          t("next.tsmode.mode_row_frequency", { frequency: formatFrequency(mode.frequencyCm1) }));
        row.appendChild(frequency);
        var badges = el(doc, "span", "acp-tsmode-badges");
        if (mode.irIntensity != null) {
          var ir = el(doc, "span", "acp-tsmode-badge",
            t("next.tsmode.mode_row_ir", { value: formatFrequency(mode.irIntensity) }));
          ir.setAttribute("data-mode-badge", "ir");
          badges.appendChild(ir);
        }
        if (selected) {
          var confirmed = el(doc, "span", "acp-tsmode-badge is-ok",
            t("next.tsmode.mode_selected"));
          confirmed.setAttribute("data-mode-badge", "selected");
          badges.appendChild(confirmed);
        }
        row.appendChild(badges);
        row.addEventListener("click", function () { selectMode(mode.modeIndex); });
        list.appendChild(row);
      });
    }

    function modeUnavailableReason(reason) {
      if (!reason) return "unknown";
      var key = "next.tsmode.mode_reason." + reason;
      var value = t(key);
      return value === key ? reason : value;
    }

    function renderModeDetail() {
      var detail = findRole("mode-detail");
      var fields = findRole("mode-detail-fields");
      if (!detail || !fields) return;
      var mode = selectedMode();
      if (!mode) {
        detail.hidden = true;
        clearChildren(fields);
        return;
      }
      detail.hidden = false;
      clearChildren(fields);
      var source = selectedSource();
      var atomCount = state.modes.atomCount > 0 ? state.modes.atomCount
        : (source ? source.atomCount : 0);
      var pairs = [
        ["next.tsmode.mode_detail_index", String(mode.modeIndex)],
        ["next.tsmode.mode_detail_frequency", formatFrequency(mode.frequencyCm1) + " cm⁻¹"],
        ["next.tsmode.mode_detail_atoms", String(atomCount)],
        ["next.tsmode.mode_detail_source", modeSourceLabel(state.modes.source)],
        ["next.tsmode.mode_detail_entry", source ? source.entryId : "—"],
        ["next.tsmode.mode_detail_vectors", String(mode.vectorCount)]
      ];
      if (mode.irIntensity != null) {
        pairs.push(["next.tsmode.mode_detail_ir", formatFrequency(mode.irIntensity)]);
      }
      pairs.forEach(function (pair) {
        var dt = i18nEl(doc, "dt", "acp-tsmode-detail-key", pair[0]);
        var dd = el(doc, "dd", "acp-tsmode-detail-value", pair[1]);
        dd.setAttribute("data-mode-detail", pair[0]);
        fields.appendChild(dt);
        fields.appendChild(dd);
      });
    }

    function renderSettings() {
      if (!state.ui.allowUnverified) return;
      state.ui.allowUnverified.checked = state.settings.allowUnverifiedMapping === true;
      state.ui.finalFrequency.checked = state.settings.finalFrequency !== false;
      /* Text inputs are the live source of truth while mounted; renderSettings
       * must not rewrite them or it would eat the caret mid-typing. */
      var warning = findRole("unverified-warning");
      if (warning) {
        warning.hidden = state.settings.allowUnverifiedMapping === true;
      }
    }

    function renderValidation() {
      var errors = state.ui.errors;
      var warnings = state.ui.warnings;
      if (!errors || !warnings) return;
      clearChildren(errors);
      clearChildren(warnings);
      state.validation.errors.forEach(function (error) {
        var li = el(doc, "li", "acp-tsmode-error-item",
          t("next.tsmode.err." + error.code, error.vars || null));
        li.setAttribute("data-error", error.code);
        errors.appendChild(li);
      });
      state.validation.warnings.forEach(function (warning) {
        var li = el(doc, "li", "acp-tsmode-warning-item",
          t("next.tsmode.err." + warning.code, warning.vars || null));
        li.setAttribute("data-warning", warning.code);
        warnings.appendChild(li);
      });
      errors.hidden = state.validation.errors.length === 0;
      warnings.hidden = state.validation.warnings.length === 0;
    }

    function renderSummary() {
      var summary = findRole("summary");
      if (!summary) return;
      var source = selectedSource();
      var mode = selectedMode();
      var frequency = mode ? formatFrequency(mode.frequencyCm1) + " cm⁻¹" : t("next.tsmode.summary_no_mode");
      var atomCount = state.modes.atomCount > 0 ? state.modes.atomCount
        : (source ? source.atomCount : 0);
      summary.textContent = t("next.tsmode.summary_lines", {
        workflow: WORKFLOW,
        jobId: state.taskJobId || "—",
        entry: source ? source.entryId : "—",
        mode: mode ? String(mode.modeIndex) : t("next.tsmode.summary_no_mode"),
        frequency: frequency,
        atoms: atomCount || "—",
        verified: state.settings.allowUnverifiedMapping ? "允许未验证映射（显式）" : "要求已验证映射",
        settings: settingsSummary(state.settings)
      });
    }

    function renderPreview() {
      var status = findRole("preview-status");
      var reasons = findRole("preview-reasons");
      var advisoriesLabel = findRole("advisories-label");
      var advisories = findRole("advisories");
      if (!status) return;
      var text;
      if (state.busy === "preview") text = t("next.tsmode.preflighting");
      else if (state.preview.status === "ok") {
        text = t("next.tsmode.preview_ok", { digest: state.preview.digest || "" });
      } else if (state.preview.status === "failed") text = t("next.tsmode.preview_failed");
      else if (state.preview.status === "error") {
        text = t("next.tsmode.preview_error", { message: textOf(state.preview.error) });
      } else if (state.preview.status === "blocked") text = t("next.tsmode.preview_blocked");
      else if (state.configRevision > 0 && state.preview.status === "idle") {
        text = t("next.tsmode.preview_idle");
      } else text = t("next.tsmode.preview_idle");
      status.textContent = text;

      if (reasons) {
        clearChildren(reasons);
        var list = state.preview.reasons || [];
        reasons.hidden = !list.length;
        list.forEach(function (reason) {
          var li = el(doc, "li", "acp-tsmode-error-item",
            textOf(reason.message || reason.code || reason.rule));
          li.setAttribute("data-preflight-reason", "1");
          if (reason.code) li.setAttribute("data-preflight-code", textOf(reason.code));
          reasons.appendChild(li);
        });
      }
      if (advisories && advisoriesLabel) {
        clearChildren(advisories);
        var items = state.preview.advisories || [];
        advisories.hidden = !items.length;
        advisoriesLabel.hidden = !items.length;
        items.forEach(function (advisory) {
          var li = el(doc, "li", "acp-tsmode-advisory-item",
            textOf(advisory.message || advisory.id || advisory.code));
          li.setAttribute("data-advisory", textOf(advisory.id || advisory.code || "advisory"));
          advisories.appendChild(li);
        });
      }
      if (state.ui.previewBtn) {
        state.ui.previewBtn.disabled = state.busy !== "none" || moduleStateName() !== "ready";
        state.ui.previewBtn.textContent = state.busy === "preview"
          ? t("next.tsmode.preflighting")
          : t("next.tsmode.preview");
      }
    }

    function renderSubmit() {
      if (!state.ui.submitBtn) return;
      state.ui.submitBtn.disabled = !canSubmit() || state.busy !== "none";
      state.ui.submitBtn.textContent = state.busy === "submit"
        ? t("next.tsmode.submitting")
        : t("next.tsmode.submit");
      var receipt = state.ui.receipt;
      if (!receipt) return;
      if (state.submit.receipt) {
        receipt.hidden = false;
        receipt.setAttribute("data-acp-receipt-state", "submitted");
        state.ui.receiptBody.textContent = t("next.tsmode.receipt_body", {
          jobId: textOf(state.submit.receipt.jobId),
          status: textOf(state.submit.receipt.status),
          attempt: textOf(state.submit.receipt.attempt)
        });
        state.ui.receiptDigest.textContent = state.submit.receipt.planDigest
          ? t("next.tsmode.receipt_digest", { digest: state.submit.receipt.planDigest })
          : "";
        state.ui.receiptDigest.hidden = !state.submit.receipt.planDigest;
        state.ui.receiptReplay.hidden = !state.submit.receipt.replay;
        state.ui.openTaskBtn.hidden = !(commands && typeof commands.openTask === "function" &&
          state.submit.receipt.jobId);
      } else if (state.submit.error) {
        receipt.hidden = false;
        receipt.setAttribute("data-acp-receipt-state", "error");
        state.ui.receiptBody.textContent = state.submit.error;
        state.ui.receiptDigest.textContent = "";
        state.ui.receiptDigest.hidden = true;
        state.ui.receiptReplay.hidden = true;
        state.ui.openTaskBtn.hidden = true;
      } else {
        receipt.hidden = true;
        receipt.setAttribute("data-acp-receipt-state", "idle");
        state.ui.receiptBody.textContent = "";
        state.ui.receiptDigest.textContent = "";
        state.ui.receiptDigest.hidden = true;
        state.ui.receiptReplay.hidden = true;
        state.ui.openTaskBtn.hidden = true;
      }
    }

    function render() {
      if (!state.root || state.disposed) return;
      setModuleState();
      renderTaskLine();
      renderSources();
      renderModes();
      renderModeDetail();
      renderSettings();
      renderValidation();
      renderSummary();
      renderPreview();
      renderSubmit();
      renderOverlay();
    }

    function findRole(role) {
      if (!state.root) return null;
      var found = null;
      (function walk(node) {
        if (found) return;
        if (node.getAttribute && node.getAttribute("data-role") === role) {
          found = node;
          return;
        }
        var children = node.children || [];
        for (var i = 0; i < children.length; i += 1) walk(children[i]);
      })(state.root);
      return found;
    }

    /* ── data loading ───────────────────────────────────────────── */

    function resetModeSelection() {
      state.selectedModeIndex = null;
      state.modes.token += 1;
      state.modes.status = "idle";
      state.modes.entryId = "";
      state.modes.reason = "";
      state.modes.items = [];
      state.modes.atomCount = 0;
      state.modes.source = "";
      state.modes.imaginaryCount = null;
      state.modes.error = null;
    }

    function resetPreview() {
      state.preview.token += 1;
      state.preview.status = "idle";
      state.preview.digest = null;
      state.preview.planKey = null;
      state.preview.reasons = [];
      state.preview.advisories = [];
      state.preview.error = null;
      state.preview.revision = -1;
      state.preview.request = null;
    }

    function resetForTask() {
      state.sources.token += 1;
      state.sources.status = "idle";
      state.sources.items = [];
      state.sources.warnings = [];
      state.sources.error = null;
      state.selectedEntryId = "";
      resetModeSelection();
      resetPreview();
      state.submit.token += 1;
      state.submit.status = "idle";
      state.submit.receipt = null;
      state.submit.receiptKey = "";
      state.submit.error = null;
      state.submit.idempotencyKey = null;
      state.submit.keyPlan = null;
      state.busy = "none";
      state.configRevision += 1;
      state.retry = null;
      state.validation = { ok: false, errors: [], warnings: [], mode: null };
    }

    function loadSources(jobId) {
      if (!state.mounted || state.disposed) return;
      var job = textOf(jobId);
      state.sources.token += 1;
      var token = state.sources.token;
      state.sources.status = "loading";
      state.sources.items = [];
      state.sources.warnings = [];
      state.sources.error = null;
      state.selectedEntryId = "";
      resetModeSelection();
      resetPreview();
      state.submit.token += 1;
      state.submit.status = "idle";
      state.submit.error = null;
      state.busy = "none";
      state.retry = function () { loadSources(state.taskJobId); };
      refreshValidation();
      render();
      if (!job) return;
      if (!requestFn) {
        state.sources.status = "error";
        state.sources.error = t("next.tsmode.err.client_missing");
        render();
        return;
      }
      var gen = state.generation;
      var promise;
      try {
        promise = requestFn(API + "/jobs/" + encodeSegment(job) + "/frequency-sources", { method: "GET" });
      } catch (error) {
        promise = Promise.reject(error);
      }
      Promise.resolve(promise).then(function (payload) {
        if (!alive(gen) || token !== state.sources.token || state.taskJobId !== job) return;
        var normalized = normalizeSources(payload);
        state.sources.items = normalized.sources;
        state.sources.warnings = normalized.warnings;
        state.sources.status = "ready";
        state.retry = null;
        var eligible = normalized.sources.filter(function (item) { return item.hessianAvailable; });
        refreshValidation();
        render();
        if (eligible.length === 1) selectSource(eligible[0].entryId);
      }).catch(function (error) {
        if (!alive(gen) || token !== state.sources.token || state.taskJobId !== job) return;
        state.sources.status = "error";
        state.sources.error = (error && error.status === 404)
          ? t("next.tsmode.err.job_not_found") + " " + describeError(error)
          : describeError(error);
        state.retry = function () { loadSources(state.taskJobId); };
        refreshValidation();
        render();
      });
    }

    function loadModes(entryId) {
      if (!state.mounted || state.disposed) return;
      var entry = textOf(entryId);
      state.modes.token += 1;
      var token = state.modes.token;
      state.modes.status = "loading";
      state.modes.entryId = entry;
      state.modes.reason = "";
      state.modes.items = [];
      state.modes.atomCount = 0;
      state.modes.source = "";
      state.modes.imaginaryCount = null;
      state.modes.error = null;
      if (!requestFn) {
        state.modes.status = "error";
        state.modes.error = t("next.tsmode.err.client_missing");
        refreshValidation();
        render();
        return;
      }
      refreshValidation();
      render();
      var gen = state.generation;
      var job = state.taskJobId;
      var promise;
      try {
        promise = requestFn(
          API + "/jobs/" + encodeSegment(job) + "/structure-viewer/entries/" +
          encodeSegment(entry) + "/vibrations",
          { method: "GET" }
        );
      } catch (error) {
        promise = Promise.reject(error);
      }
      Promise.resolve(promise).then(function (payload) {
        if (!alive(gen) || token !== state.modes.token || state.selectedEntryId !== entry ||
            state.taskJobId !== job) return;
        var normalized = normalizeModes(payload);
        state.modes.atomCount = normalized.atomCount;
        state.modes.source = normalized.source;
        state.modes.imaginaryCount = normalized.imaginaryCount;
        if (!normalized.available) {
          state.modes.status = "unavailable";
          state.modes.reason = normalized.reason;
          state.modes.items = [];
        } else {
          state.modes.status = "ready";
          state.modes.items = normalized.modes;
        }
        refreshValidation();
        render();
      }).catch(function (error) {
        if (!alive(gen) || token !== state.modes.token || state.selectedEntryId !== entry ||
            state.taskJobId !== job) return;
        state.modes.status = "error";
        state.modes.items = [];
        if (error && error.status === 404) {
          state.modes.error = t("next.tsmode.err.modes_entry_mismatch", { entry: entry }) +
            " " + describeError(error);
        } else {
          state.modes.error = describeError(error);
        }
        refreshValidation();
        render();
      });
    }

    function selectSource(entryId) {
      var items = state.sources.items || [];
      var match = null;
      for (var i = 0; i < items.length; i += 1) {
        if (items[i].entryId === entryId) { match = items[i]; break; }
      }
      if (!match || match.hessianAvailable !== true) return;
      if (state.selectedEntryId === match.entryId && state.modes.status !== "idle") return;
      state.selectedEntryId = match.entryId;
      resetModeSelection();
      touchConfig();
      refreshValidation();
      render();
      loadModes(match.entryId);
    }

    function selectMode(modeIndex) {
      if (!isInteger(Number(modeIndex))) return;
      var index = Number(modeIndex);
      var imaginary = imaginaryModes(state.modes.items);
      var match = null;
      for (var i = 0; i < imaginary.length; i += 1) {
        if (imaginary[i].modeIndex === index) { match = imaginary[i]; break; }
      }
      if (!match) return;
      state.selectedModeIndex = match.modeIndex;
      touchConfig();
      refreshValidation();
      render();
    }

    /* ── preview / submit ───────────────────────────────────────── */

    function runPreview() {
      if (!state.mounted || state.disposed) return;
      if (state.busy !== "none") return;
      refreshValidation();
      if (!state.validation.ok) {
        state.preview.status = "blocked";
        state.preview.token += 1;
        render();
        return;
      }
      var request = currentRequest();
      if (!request) {
        state.preview.status = "blocked";
        render();
        return;
      }
      state.preview.token += 1;
      var token = state.preview.token;
      var revision = state.configRevision;
      state.preview.status = "running";
      state.preview.error = null;
      state.preview.reasons = [];
      state.preview.advisories = [];
      state.preview.digest = null;
      state.preview.planKey = null;
      state.busy = "preview";
      render();
      var gen = state.generation;
      var promise;
      try {
        promise = requestFn(API + "/preflight", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(request)
        });
      } catch (error) {
        promise = Promise.reject(error);
      }
      Promise.resolve(promise).then(function (payload) {
        if (!alive(gen) || token !== state.preview.token || revision !== state.configRevision) return;
        state.busy = "none";
        var feasible = !!(payload && payload.feasible === true);
        state.preview.reasons = payload && Array.isArray(payload.reasons) ? payload.reasons : [];
        state.preview.advisories = payload && Array.isArray(payload.advisories) ? payload.advisories : [];
        if (feasible) {
          state.preview.status = "ok";
          state.preview.digest = textOf(payload.plan_digest) || null;
          state.preview.planKey = planKeyOf(request);
          state.preview.revision = revision;
          state.preview.request = request;
          state.submit.error = null;
        } else {
          state.preview.status = "failed";
          state.preview.digest = null;
          state.preview.planKey = null;
          state.preview.revision = -1;
          state.preview.request = null;
        }
        render();
      }).catch(function (error) {
        if (!alive(gen) || token !== state.preview.token || revision !== state.configRevision) return;
        state.busy = "none";
        state.preview.status = "error";
        state.preview.error = describeError(error);
        state.preview.digest = null;
        state.preview.planKey = null;
        state.preview.revision = -1;
        state.preview.request = null;
        render();
      });
    }

    function submitErrorMessage(error) {
      var code = errorCodeOf(error);
      if (error && error.status === 400 && code === "preflight_stale") {
        return t("next.tsmode.err.preflight_stale");
      }
      var known = {
        mode_mapping_unsupported: "next.tsmode.err.mode_mapping_unsupported",
        mode_mapping_ambiguous: "next.tsmode.err.mode_mapping_ambiguous",
        target_mode_invalid: "next.tsmode.err.target_mode_invalid",
        frequency_source_incomplete: "next.tsmode.err.frequency_source_incomplete"
      };
      if (code && known[code]) {
        return t(known[code]) + " " + describeError(error);
      }
      return t("next.tsmode.submit_error", { message: describeError(error) });
    }

    function submit() {
      if (!state.mounted || state.disposed) return;
      if (state.busy !== "none") return;
      if (!canSubmit()) {
        if (state.submit.receiptKey && state.submit.receiptKey === state.preview.planKey) {
          state.submit.error = t("next.tsmode.submit_already");
        } else {
          state.submit.error = t("next.tsmode.submit_blocked");
        }
        render();
        return;
      }
      var request = currentRequest();
      if (!request) {
        state.submit.error = t("next.tsmode.submit_blocked");
        render();
        return;
      }
      var body = {};
      Object.keys(request).forEach(function (key) { body[key] = request[key]; });
      body.preflight_fingerprint = state.preview.digest;
      if (!state.submit.idempotencyKey || state.submit.keyPlan !== state.preview.planKey) {
        state.submit.idempotencyKey = newIdempotencyKey();
        state.submit.keyPlan = state.preview.planKey;
      }
      state.submit.token += 1;
      var token = state.submit.token;
      var planKey = state.preview.planKey;
      state.submit.status = "running";
      state.submit.error = null;
      state.busy = "submit";
      render();
      var gen = state.generation;
      var promise;
      try {
        promise = requestFn(API + "/jobs", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": state.submit.idempotencyKey
          },
          body: JSON.stringify(body)
        });
      } catch (error) {
        promise = Promise.reject(error);
      }
      Promise.resolve(promise).then(function (payload) {
        if (!alive(gen) || token !== state.submit.token) return;
        state.busy = "none";
        state.submit.status = "ok";
        state.submit.receipt = {
          jobId: textOf(payload && payload.job_id),
          status: textOf(payload && payload.status),
          attempt: payload && payload.attempt != null ? Number(payload.attempt) : 1,
          planDigest: textOf(payload && payload.plan_digest),
          replay: !!(payload && payload.idempotent_replay === true)
        };
        state.submit.receiptKey = planKey;
        state.submit.error = null;
        render();
      }).catch(function (error) {
        if (!alive(gen) || token !== state.submit.token) return;
        state.busy = "none";
        state.submit.status = "error";
        var code = errorCodeOf(error);
        if (error && error.status === 400 && code === "preflight_stale") {
          resetPreview();
          state.submit.error = t("next.tsmode.err.preflight_stale");
        } else {
          state.submit.error = submitErrorMessage(error);
        }
        render();
      });
    }

    function openReceiptTask() {
      if (!commands || typeof commands.openTask !== "function") return;
      var receipt = state.submit.receipt;
      if (!receipt || !receipt.jobId) return;
      try {
        commands.openTask({
          jobId: receipt.jobId,
          attempt: receipt.attempt,
          source: MODULE_ID
        });
      } catch (error) { /* navigation is auxiliary; the receipt stands */ }
    }

    /* ── context ────────────────────────────────────────────────── */

    function currentTaskRef() {
      if (!state.snapshot) return null;
      return state.snapshot.taskRef || null;
    }

    function applySnapshot(snap) {
      if (!state.mounted || state.disposed) return;
      state.snapshot = snap || null;
      var task = snap && snap.taskRef ? snap.taskRef : null;
      var jobId = task && task.jobId ? String(task.jobId) : "";
      var projectId = snap && snap.projectId ? String(snap.projectId) : "";
      if (jobId === state.taskJobId && projectId === state.projectId) return;
      state.taskJobId = jobId;
      state.projectId = projectId;
      resetForTask();
      if (!jobId) {
        state.sources.status = "idle";
        render();
        return;
      }
      loadSources(jobId);
    }

    function onContextNotify() {
      if (!state.mounted || state.disposed || !context || typeof context.getSnapshot !== "function") return;
      try { applySnapshot(context.getSnapshot()); } catch (error) { /* malformed snapshot is ignored */ }
    }

    function onLangChange() {
      if (!state.mounted || state.disposed) return;
      render();
    }

    /* ── lifecycle ──────────────────────────────────────────────── */

    function removeDom() {
      for (var i = 0; i < state.unlisten.length; i += 1) {
        try { state.unlisten[i](); } catch (error) { /* best effort */ }
      }
      state.unlisten = [];
      if (state.root && state.root.parentNode &&
          typeof state.root.parentNode.removeChild === "function") {
        try { state.root.parentNode.removeChild(state.root); } catch (error) { /* detached */ }
      }
      state.root = null;
      state.host = null;
      state.ui = {};
    }

    function renderMountFailure(error) {
      try {
        if (!state.root) buildDom();
        if (state.sources.status !== "error") {
          state.sources.status = "error";
          state.sources.error = (error && error.message ? error.message : String(error)) +
            "（模块局部错误，不影响其它面板）";
        }
        render();
      } catch (inner) {
        if (typeof console !== "undefined" && console && console.error) {
          console.error("[tsmode-editor] mount failed and error surface unavailable:", inner);
        }
      }
    }

    return {
      mount: function (hostEl, api) {
        if (state.mounted) {
          return Promise.reject(new Error("tsmode-editor: already mounted (dispose() first)"));
        }
        if (!hostEl || typeof hostEl.appendChild !== "function") {
          return Promise.reject(new TypeError("tsmode-editor mount: host 必须是 DOM 元素"));
        }
        if (api && api.context) context = api.context;
        if (api && api.commands) commands = api.commands;
        state.mounted = true;
        state.disposed = false;
        state.generation += 1;
        state.host = hostEl;
        try {
          buildDom();
          if (typeof doc.addEventListener === "function") {
            doc.addEventListener("acp:langchange", onLangChange);
            state.unlisten.push(function () {
              if (typeof doc.removeEventListener === "function") {
                doc.removeEventListener("acp:langchange", onLangChange);
              }
            });
          }
          if (context && typeof context.subscribe === "function") {
            state.unsubscribeContext = context.subscribe(onContextNotify);
          }
          var initial = (api && api.context && typeof api.context.getSnapshot === "function")
            ? api.context.getSnapshot()
            : (context && typeof context.getSnapshot === "function" ? context.getSnapshot() : null);
          applySnapshot(initial);
          render();
        } catch (error) {
          renderMountFailure(error);
        }
        return Promise.resolve();
      },

      updateContext: function (next) {
        if (!state.mounted || state.disposed) return;
        applySnapshot(next || (context && typeof context.getSnapshot === "function"
          ? context.getSnapshot() : null));
      },

      suspend: function () { state.suspended = true; },

      resume: function () {
        state.suspended = false;
        render();
      },

      dispose: function () {
        if (!state.mounted && !state.root) return;
        state.disposed = true;
        state.mounted = false;
        state.generation += 1;
        state.sources.token += 1;
        state.modes.token += 1;
        state.preview.token += 1;
        state.submit.token += 1;
        if (state.unsubscribeContext) {
          try { state.unsubscribeContext(); } catch (error) { /* best effort */ }
          state.unsubscribeContext = null;
        }
        removeDom();
        state.snapshot = null;
        /* Forget the task binding so a later mount() on the same instance
         * always re-reads the sources instead of trusting a disposed load. */
        state.taskJobId = "";
        state.projectId = "";
        state.sources.status = "idle";
        state.sources.items = [];
        state.modes.status = "idle";
        state.modes.items = [];
        state.submit.receipt = null;
        state.submit.idempotencyKey = null;
      }
    };
  }

  window.ACPNextModules[MODULE_ID] = {
    descriptor: {
      id: MODULE_ID,
      interfaceVersion: 1,
      version: "0.1.0",
      dependencies: [],
      provides: ["tsmode.editor"],
      binds: ["taskRef"],
      capabilities: { requires: [], provides: ["tsmode.editor"] },
      hosts: ["acp-web"],
      supportedInstancing: "single",
      configSchemaVersion: 0,
      error: { titleKey: "next.tsmode.errorTitle", messageKey: "next.tsmode.errorMessage" }
    },
    factory: function (deps) { return createInstance(deps || {}); },
    _internals: {
      API: API,
      WORKFLOW: WORKFLOW,
      MODULE_ID: MODULE_ID,
      normalizeSources: normalizeSources,
      normalizeModes: normalizeModes,
      imaginaryModes: imaginaryModes,
      validateSelection: validateSelection,
      buildInput: buildInput,
      buildRequest: buildRequest,
      defaultJobName: defaultJobName,
      planKeyOf: planKeyOf,
      formatFrequency: formatFrequency,
      describeError: describeError,
      settingsSummary: settingsSummary
    }
  };
})();

