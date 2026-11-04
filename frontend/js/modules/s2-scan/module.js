/*
 * ACP 3.0 module — s2-scan (Track A / A-48 · P16 “S2 键长扫描” 弹窗).
 *
 * Classic script, no build, no ESM. Registers exactly one global key
 * (`window.ACPNextModules["s2-scan"]`); the Shell loader resolves the factory
 * and the returned instance is mounted into an arbitrary host element.
 *
 * Scope (A-48):
 *   - source structure selection: inline XYZ / task structure / uploaded text;
 *   - a two-atom bond-length (distance) scan: range start/end + point count;
 *   - client validation: start > 0, end > start, points integer >= 2 (bounded
 *     by the catalog ceiling), atom indices 1-based -> 0-based once, and the
 *     charge/multiplicity electron-parity check;
 *   - ScanTS is OFF by default and only an explicit user opt-in enables it;
 *   - preview = the EXISTING zero-side-effect wizard preflight
 *     `POST /api/v1/preflight` (there is no `scan/preview` endpoint; the legacy
 *     `POST /api/v1/s2/structure-preview` is retired and answers 410 — that
 *     route is deliberately NOT called);
 *   - submit = the EXISTING wizard submit + receipt path
 *     `POST /api/v1/jobs` with `preflight_fingerprint` + `Idempotency-Key`,
 *     exactly like the frozen four-state task-config module (A-05/A-06);
 *   - data flows through the injected client seam only
 *     (`deps.request` -> `deps.client.transport.request` ->
 *     `window.ACPClient.transport.request`); this module performs no direct
 *     network call of its own and never reads `window.fetch`;
 *   - no second 3D viewer: the panel only shows numbers/text. It creates no
 *     scene, no canvas and no molecule instance;
 *   - no task mutation beyond the one existing submit command: the module never
 *     edits, reruns, cancels or purges a task. An optional `commands.openTask`
 *     focus dispatch after a receipt is navigation, not mutation.
 *
 * Four states live on the module root `data-acp-module-state` =
 * empty | loading | ready | error. Busy command state is separate
 * (`data-acp-busy` = none | preflight | submit) so a running preflight never
 * hides the form. Every async result is guarded by the mount generation plus a
 * per-action token, so dispose / a newer structure / a newer preflight can
 * never write into the DOM. dispose() is idempotent and removes the DOM it
 * created, its context subscription, its language listener and its debounce
 * timer.
 *
 * Boundary note: method / basis / profile selection is NOT owned here (A-48 is
 * the coordinate dialog). The request carries the `scan` workflow, the
 * `dft_scan` schema id and the two scan knobs (`scan_points`,
 * `scan_use_scants`); the server resolves the remaining execution defaults.
 * That parity gap is recorded in README §8.
 */
(function () {
  "use strict";

  window.ACPNextModules = window.ACPNextModules || {};

  var MODULE_ID = "s2-scan";
  var API = "/api/v1";
  var WORKFLOW = "scan";
  var METHOD_SCHEMA_ID = "dft_scan";
  var SCAN_KIND = "distance";

  /* Bounds, each with one owner:
   *   2   — `scheduler/jobs.py::validate_scan_submission` floor ("integer
   *         scan_points >= 2"); this is the A-48 requirement.
   *   100 — `catalog.py::FIELD_DEFINITIONS["scan_points"].max` (the UI field
   *         ceiling). The floor is NOT the catalog min (5): the submission
   *         boundary — the authority a submit is measured against — allows 2.
   */
  var MIN_POINTS = 2;
  var MAX_POINTS = 100;
  var MIN_MULTIPLICITY = 1;
  var DEFAULT_PARSE_DEBOUNCE_MS = 300;

  var DESCRIPTOR = {
    id: MODULE_ID,
    interfaceVersion: 1,
    version: "0.1.0",
    provides: ["scan.s2"],
    binds: ["taskRef", "structureRef"],
    capabilities: { requires: [], provides: ["scan.s2"] },
    hosts: ["acp-web"],
    supportedInstancing: "single",
    configSchemaVersion: 0,
    error: { titleKey: "next.s2scan.errorTitle", messageKey: "next.s2scan.errorMessage" }
  };

  /* Local zh dictionary (module copy). The Shell dictionary is consulted first;
   * `ACPShell.i18n.t()` echoes the key when no translation exists, so these
   * values are the real fallback until A-61 adds `next.s2scan.*` (see README
   * §5 for the full key list). Static chrome also carries data-i18n. */
  var ZH = {
    "next.s2scan.title": "S2 键长扫描",
    "next.s2scan.subtitle": "两原子距离扫描 · 默认关闭 ScanTS",
    "next.s2scan.source_title": "1 · 结构来源",
    "next.s2scan.source_inline": "内联 XYZ",
    "next.s2scan.source_task": "任务结构",
    "next.s2scan.source_upload": "上传文本",
    "next.s2scan.source_inline_hint": "粘贴标准 XYZ 文本；首行原子数可选，第二行注释可选。",
    "next.s2scan.source_task_hint": "使用当前任务焦点里的结构引用，只读取几何，不提交也不修改任务。",
    "next.s2scan.source_upload_hint": "仅在本页读取文本文件（.xyz/.txt），不会上传到服务器。",
    "next.s2scan.xyz_placeholder": "3\nwater\nO 0.000 0.000 0.000\nH 0.758 0.586 0.000\nH -0.758 0.586 0.000\n",
    "next.s2scan.parse": "解析结构",
    "next.s2scan.load_task": "从当前任务载入",
    "next.s2scan.reload_task": "重新载入",
    "next.s2scan.choose_file": "选择文本文件",
    "next.s2scan.task_ref": "当前结构引用：{ref}",
    "next.s2scan.task_no_ref": "未绑定任务结构（先在内联 XYZ 或上传文本中提供结构，或在任务面板选中一个任务）。",
    "next.s2scan.task_changed": "任务结构焦点已变化，请重新载入。",
    "next.s2scan.scan_title": "2 · 扫描坐标（距离）",
    "next.s2scan.bond_length": "当前键长：{value} Å（原子 {a}–{b}）",
    "next.s2scan.bond_length_unknown": "当前键长：—",
    "next.s2scan.atom_a": "原子 A（1 基）",
    "next.s2scan.atom_b": "原子 B（1 基）",
    "next.s2scan.start": "起点（Å）",
    "next.s2scan.end": "终点（Å）",
    "next.s2scan.points": "点数",
    "next.s2scan.scants": "启用 ORCA ScanTS（显式）",
    "next.s2scan.scants_hint": "默认关闭。仅在明确需要 TS 导向扫描时启用；普通键长扫描保持关闭。",
    "next.s2scan.scants_on": "ScanTS 已启用（TS 导向扫描）",
    "next.s2scan.scants_off": "ScanTS 关闭（默认）",
    "next.s2scan.state_title": "3 · 电荷与自旋",
    "next.s2scan.charge": "电荷",
    "next.s2scan.multiplicity": "自旋多重度",
    "next.s2scan.parity_ok": "电荷/多重度奇偶检查通过（{electrons} 个电子）。",
    "next.s2scan.parity_unverifiable": "元素不在内置周期表子集内，奇偶检查跳过（不误报）。",
    "next.s2scan.review_title": "4 · 预检与提交",
    "next.s2scan.summary": "配置摘要",
    "next.s2scan.summary_lines": "工作流 {workflow} · 距离扫描\n坐标 {a}–{b}（0 基 {wire}）\n范围 {start} → {end} Å · {points} 点\nScanTS {scants}\n电荷 {charge} · 多重度 {multiplicity}\n结构 {atoms} 个原子 · 来源 {source}",
    "next.s2scan.scants_on_short": "开启",
    "next.s2scan.scants_off_short": "关闭",
    "next.s2scan.source_inline_short": "内联 XYZ",
    "next.s2scan.source_task_short": "任务结构",
    "next.s2scan.source_upload_short": "上传文本",
    "next.s2scan.preflight": "预检",
    "next.s2scan.preflighting": "预检中…",
    "next.s2scan.preflight_idle": "尚未预检。预检由服务端零副作用执行。",
    "next.s2scan.preflight_ok": "预检通过：{digest}",
    "next.s2scan.preflight_failed": "预检未通过",
    "next.s2scan.preflight_error": "预检请求失败：{message}",
    "next.s2scan.preflight_blocked": "参数未通过校验，未发送预检。",
    "next.s2scan.preflight_edited": "配置已修改，请重新预检。",
    "next.s2scan.advisories": "预检提示",
    "next.s2scan.submit": "提交扫描任务",
    "next.s2scan.submitting": "提交中…",
    "next.s2scan.submit_blocked": "预检通过后才能提交。",
    "next.s2scan.receipt": "已提交",
    "next.s2scan.receipt_body": "任务 {jobId} · 状态 {status} · 第 {attempt} 次尝试",
    "next.s2scan.receipt_digest": "计划摘要 {digest}",
    "next.s2scan.receipt_replay": "幂等重放：返回的是同一任务的既有回执。",
    "next.s2scan.open_task": "在任务面板打开",
    "next.s2scan.loading": "正在载入结构…",
    "next.s2scan.empty": "尚未载入结构",
    "next.s2scan.empty_hint": "粘贴内联 XYZ、从当前任务结构载入，或选择一个文本文件。",
    "next.s2scan.error_title": "结构载入失败",
    "next.s2scan.errorTitle": "S2 键长扫描不可用",
    "next.s2scan.errorMessage": "s2-scan 模块加载失败，请查看控制台。",
    "next.s2scan.retry": "重试",
    "next.s2scan.source_status_ready": "已载入 {count} 个原子 · 来源：{source}",
    "next.s2scan.source_status_empty": "尚未载入结构。",
    "next.s2scan.method_note": "本弹窗只负责扫描坐标与 ScanTS；方法与基组沿用 scan 工作流的服务端默认值。",
    "next.s2scan.err.structure_missing": "请先载入结构（内联 XYZ / 任务结构 / 上传文本）。",
    "next.s2scan.err.atom_invalid": "原子编号必须是整数。",
    "next.s2scan.err.atom_out_of_range": "原子编号 {index} 超出范围（共 {count} 个原子）。",
    "next.s2scan.err.atoms_equal": "两个扫描原子必须是不同的原子。",
    "next.s2scan.err.start_invalid": "起点必须是有限数值。",
    "next.s2scan.err.start_not_positive": "起点必须大于 0 Å。",
    "next.s2scan.err.end_invalid": "终点必须是有限数值。",
    "next.s2scan.err.end_not_greater": "终点必须大于起点。",
    "next.s2scan.err.points_invalid": "点数必须是整数。",
    "next.s2scan.err.points_min": "点数至少为 {min}。",
    "next.s2scan.err.points_max": "点数最多为 {max}。",
    "next.s2scan.err.charge_invalid": "电荷必须是整数。",
    "next.s2scan.err.multiplicity_invalid": "自旋多重度必须是 ≥1 的整数。",
    "next.s2scan.err.multiplicity_parity": "电子数为 {electrons}，自旋多重度奇偶不匹配。",
    "next.s2scan.err.structure_adapter_missing": "结构目录适配器未加载（view_source.js），无法按结构引用读取任务结构。",
    "next.s2scan.err.client_missing": "客户端未注入（ACPClient.transport），无法读取或提交。",
    "next.s2scan.err.structure_empty": "结构来源没有返回 XYZ 文本。",
    "next.s2scan.err.structure_read_failed": "结构读取失败：{message}",
    "next.s2scan.err.file_unsupported": "当前环境无法读取本地文件。",
    "next.s2scan.err.parse_failed": "结构解析失败：{message}"
  };

  /* Symbols 1..103, index + 1 == atomic number. Local display/validation
   * constant only: an unknown symbol makes the parity check unverifiable —
   * never a guessed atomic number. */
  var ELEMENT_SYMBOLS = (
    "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni " +
    "Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe " +
    "Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg " +
    "Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr"
  ).split(" ");

  var INTEGER_RE = /^[+-]?\d+$/;

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

  function describeError(error) {
    if (!error) return "unknown error";
    var body = error.body;
    if (body && typeof body === "object") {
      var detail = body.detail;
      if (detail && typeof detail === "object") {
        var code = detail.code ? String(detail.code) : "";
        var message = detail.message ? String(detail.message) : "";
        if (code || message) return (code ? code + ": " : "") + (message || JSON.stringify(detail));
      }
      if (typeof detail === "string" && detail) return detail;
    }
    return error.message ? String(error.message) : String(error);
  }

  function isForbidden(error) {
    return !!error && (error.status === 401 || error.status === 403);
  }

  function newIdempotencyKey() {
    try {
      if (typeof crypto !== "undefined" && crypto && typeof crypto.randomUUID === "function") {
        return "next-s2-" + crypto.randomUUID();
      }
    } catch (e) { /* fall through */ }
    return "next-s2-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
  }

  function textOf(value) {
    return value == null ? "" : String(value);
  }

  function clearChildren(node) {
    if (!node) return;
    if (typeof node.replaceChildren === "function") node.replaceChildren();
    else node.textContent = "";
  }

  function isFiniteNumber(value) {
    return typeof value === "number" && isFinite(value);
  }

  function atomicNumber(symbol) {
    var key = textOf(symbol).trim();
    if (!key) return null;
    var lookup = key.charAt(0).toUpperCase() + key.slice(1).toLowerCase();
    var index = ELEMENT_SYMBOLS.indexOf(lookup);
    return index < 0 ? null : index + 1;
  }

  /* 1-based UI index -> integer, or null for non-integers. */
  function integerText(value) {
    var raw = textOf(value).trim();
    if (!INTEGER_RE.test(raw)) return null;
    return parseInt(raw, 10);
  }

  /* ── structure parsing ──────────────────────────────────────────── */

  /**
   * Parse the FIRST frame of a standard XYZ block (a bare atom list without
   * the declared count is also accepted). The submitted text itself is never
   * re-serialized: the wire keeps the user's geometry verbatim.
   *
   * @returns {{ok:boolean, empty?:boolean, atoms:Array, count:number,
   *            declared:(number|null), error:(string|null)}}
   */
  function parseXyz(text) {
    var raw = textOf(text).trim();
    if (!raw) return { ok: false, empty: true, atoms: [], count: 0, declared: null, error: null };
    var lines = raw.split(/\r?\n/);
    var atoms = [];
    var declared = null;
    var index = 0;
    var first = textOf(lines[0]).trim();
    if (/^\d+$/.test(first)) {
      declared = parseInt(first, 10);
      if (declared < 1) {
        return { ok: false, atoms: [], count: 0, declared: declared, error: "首行原子数必须 ≥ 1" };
      }
      index = 1;
      if (index < lines.length) index += 1; // standard XYZ comment line
    }
    for (; index < lines.length; index += 1) {
      if (declared !== null && atoms.length >= declared) break;
      var line = textOf(lines[index]).trim();
      if (!line) continue;
      var parts = line.split(/\s+/);
      if (parts.length < 4) {
        return {
          ok: false, atoms: [], count: 0, declared: declared,
          error: "第 " + (index + 1) + " 行不是原子行（需要：元素 X Y Z）"
        };
      }
      if (!/^[A-Za-z]{1,2}$/.test(parts[0])) {
        return {
          ok: false, atoms: [], count: 0, declared: declared,
          error: "第 " + (index + 1) + " 行元素符号无效"
        };
      }
      var x = Number(parts[1]);
      var y = Number(parts[2]);
      var z = Number(parts[3]);
      if (!isFinite(x) || !isFinite(y) || !isFinite(z)) {
        return {
          ok: false, atoms: [], count: 0, declared: declared,
          error: "第 " + (index + 1) + " 行坐标不是有限数值"
        };
      }
      atoms.push({ symbol: parts[0], x: x, y: y, z: z });
    }
    if (!atoms.length) {
      return { ok: false, atoms: [], count: 0, declared: declared, error: "没有可用的原子行" };
    }
    if (declared !== null && atoms.length !== declared) {
      return {
        ok: false, atoms: [], count: 0, declared: declared,
        error: "首行声明的原子数 " + declared + " 与实际 " + atoms.length + " 不一致"
      };
    }
    return { ok: true, atoms: atoms, count: atoms.length, declared: declared, error: null };
  }

  function atomDistance(a, b) {
    if (!a || !b) return null;
    var dx = Number(a.x) - Number(b.x);
    var dy = Number(a.y) - Number(b.y);
    var dz = Number(a.z) - Number(b.z);
    if (!isFinite(dx) || !isFinite(dy) || !isFinite(dz)) return null;
    return Math.sqrt(dx * dx + dy * dy + dz * dz);
  }

  /**
   * Electron count = sum(Z) - charge, or null when any element is outside the
   * local symbol table (parity then stays unverifiable, never a false error).
   */
  function electronCount(atoms, charge) {
    if (!Array.isArray(atoms) || !atoms.length) return null;
    var total = 0;
    for (var i = 0; i < atoms.length; i += 1) {
      var z = atomicNumber(atoms[i] && atoms[i].symbol);
      if (z === null) return null;
      total += z;
    }
    var electrons = total - (Number(charge) || 0);
    return electrons >= 0 ? electrons : null;
  }

  /* ── validation (pure) ──────────────────────────────────────────── */

  /**
   * Validate the dialog fields against the parsed structure.
   *
   * @param {object} input {atoms, atomA, atomB, start, end, points, charge,
   *                        multiplicity} (strings, exactly as typed)
   * @returns {{ok:boolean, errors:Array<{code,field,vars?}>,
   *            parityChecked:boolean, electrons:(number|null),
   *            bondLength:(number|null), atomA:(number|null),
   *            atomB:(number|null)}}
   */
  function validateConfig(input) {
    input = input || {};
    var atoms = Array.isArray(input.atoms) ? input.atoms : [];
    var errors = [];
    var count = atoms.length;

    if (!count) errors.push({ code: "structure_missing", field: "structure" });

    var atomA = integerText(input.atomA);
    var atomB = integerText(input.atomB);
    if (atomA === null) errors.push({ code: "atom_invalid", field: "atomA" });
    else if (atomA < 1 || (count && atomA > count)) {
      errors.push({ code: "atom_out_of_range", field: "atomA", vars: { index: atomA, count: count } });
    }
    if (atomB === null) errors.push({ code: "atom_invalid", field: "atomB" });
    else if (atomB < 1 || (count && atomB > count)) {
      errors.push({ code: "atom_out_of_range", field: "atomB", vars: { index: atomB, count: count } });
    }
    if (atomA !== null && atomB !== null && atomA === atomB) {
      errors.push({ code: "atoms_equal", field: "atoms" });
    }

    var startText = textOf(input.start).trim();
    var endText = textOf(input.end).trim();
    var start = startText === "" ? NaN : Number(startText);
    var end = endText === "" ? NaN : Number(endText);
    if (!isFinite(start)) errors.push({ code: "start_invalid", field: "start" });
    else if (!(start > 0)) errors.push({ code: "start_not_positive", field: "start" });
    if (!isFinite(end)) errors.push({ code: "end_invalid", field: "end" });
    else if (isFinite(start) && !(end > start)) errors.push({ code: "end_not_greater", field: "end" });

    var points = integerText(input.points);
    if (points === null) errors.push({ code: "points_invalid", field: "points" });
    else if (points < MIN_POINTS) errors.push({ code: "points_min", field: "points", vars: { min: MIN_POINTS } });
    else if (points > MAX_POINTS) errors.push({ code: "points_max", field: "points", vars: { max: MAX_POINTS } });

    var charge = integerText(input.charge);
    if (charge === null) errors.push({ code: "charge_invalid", field: "charge" });
    var multiplicity = integerText(input.multiplicity);
    if (multiplicity === null || multiplicity < MIN_MULTIPLICITY) {
      errors.push({ code: "multiplicity_invalid", field: "multiplicity" });
    }

    var electrons = null;
    var parityChecked = false;
    if (!errors.length && charge !== null && multiplicity !== null) {
      electrons = electronCount(atoms, charge);
      if (electrons !== null && electrons >= 0) {
        parityChecked = true;
        var expectedOdd = electrons % 2 === 0; // even e- -> odd multiplicity
        if ((multiplicity % 2 === 1) !== expectedOdd) {
          errors.push({
            code: "multiplicity_parity",
            field: "multiplicity",
            vars: { electrons: electrons }
          });
        }
      }
    }

    var bondLength = null;
    if (count && atomA !== null && atomB !== null &&
        atomA >= 1 && atomB >= 1 && atomA <= count && atomB <= count && atomA !== atomB) {
      bondLength = atomDistance(atoms[atomA - 1], atoms[atomB - 1]);
    }

    return {
      ok: errors.length === 0,
      errors: errors,
      parityChecked: parityChecked,
      electrons: electrons,
      bondLength: bondLength,
      atomA: atomA,
      atomB: atomB
    };
  }

  /* ── request assembly (pure) ────────────────────────────────────── */

  /**
   * The ONLY 1-based -> 0-based conversion site (`scan-index-base-convert`):
   * the wire coordinate is `"<a0>,<b0>,<start>,<end>"` and the range text is
   * forwarded verbatim so the canonical digest sees exactly what the user saw.
   */
  function scanCoordinateString(atomA, atomB, start, end) {
    return String(Number(atomA) - 1) + "," + String(Number(atomB) - 1) + "," +
      textOf(start).trim() + "," + textOf(end).trim();
  }

  /**
   * Build the frozen scan submit/preflight body. Call only after
   * `validateConfig(...).ok` — it does not repair invalid input.
   *
   * Shape (existing contract, not invented here):
   *   workflow "scan"; input {source_type, source, xyz_text, charge,
   *   multiplicity, scan_coordinates[]}; method {schema_id, scan_points,
   *   scan_use_scants}; optional project_id.
   * `scan_use_scants` is ALWAYS present and false unless explicitly opted in.
   */
  function buildRequest(input) {
    input = input || {};
    var xyz = textOf(input.xyz);
    var request = {
      workflow: WORKFLOW,
      input: {
        source_type: "xyz_text",
        source: xyz,
        xyz_text: xyz,
        charge: Number(input.charge),
        multiplicity: Number(input.multiplicity),
        scan_coordinates: [scanCoordinateString(input.atomA, input.atomB, input.start, input.end)]
      },
      method: {
        schema_id: METHOD_SCHEMA_ID,
        scan_points: Number(input.points),
        scan_use_scants: input.scants === true
      }
    };
    if (input.projectId) request.project_id = String(input.projectId);
    return request;
  }

  /* Stable-enough plan identity for invalidation: our builder emits keys in a
   * fixed order, so JSON.stringify is deterministic. This is a UI cache key,
   * never the server canonical digest. */
  function planKeyOf(request) {
    if (!request) return "";
    try {
      return JSON.stringify(request);
    } catch (e) {
      return "";
    }
  }

  function structureRefKey(ref) {
    if (!ref || typeof ref !== "object") return "";
    return [textOf(ref.kind), textOf(ref.jobId), textOf(ref.entryId), textOf(ref.path)].join("|");
  }

  /* Display-only identity of the focused structure reference. No filesystem
   * path is joined or constructed here. */
  function formatStructureRef(ref) {
    if (!ref || typeof ref !== "object") return t("next.s2scan.task_no_ref");
    var kind = textOf(ref.kind) || "ref";
    var parts = [kind];
    if (ref.jobId) parts.push("任务 " + String(ref.jobId));
    if (ref.entryId) parts.push("条目 " + String(ref.entryId));
    else if (ref.path) parts.push(String(ref.path));
    return parts.join(" · ");
  }

  function sourceLabel(kind) {
    if (kind === "task") return t("next.s2scan.source_task_short");
    if (kind === "upload") return t("next.s2scan.source_upload_short");
    return t("next.s2scan.source_inline_short");
  }

  function errorText(error) {
    if (!error || !error.code) return "";
    return t("next.s2scan.err." + error.code, error.vars || null);
  }

  /* ── module instance ───────────────────────────────────────────── */

  function createInstance(deps) {
    deps = deps || {};
    var doc = deps.doc || (typeof window !== "undefined" ? window.document : null);
    var context = deps.context || null;
    var commands = deps.commands || null;
    var parseDebounceMs = isFiniteNumber(Number(deps.parseDebounceMs)) && Number(deps.parseDebounceMs) >= 0
      ? Number(deps.parseDebounceMs)
      : DEFAULT_PARSE_DEBOUNCE_MS;

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
      taskRef: null,
      structureRef: null,
      structureKey: "",
      projectId: "",
      source: {
        kind: "inline",
        status: "empty",
        error: null,
        text: "",
        atoms: [],
        count: 0,
        declared: null,
        meta: null,
        notice: null,
        token: 0
      },
      form: {
        atomA: "1",
        atomB: "2",
        start: "1.0",
        end: "3.0",
        points: "21",
        charge: "0",
        multiplicity: "1",
        scants: false
      },
      configRevision: 0,
      validation: { ok: false, errors: [], parityChecked: false, electrons: null, bondLength: null },
      preflight: {
        status: "idle", fingerprint: null, planKey: null, reasons: [], advisories: [],
        token: 0, revision: -1
      },
      submit: {
        status: "idle", receipt: null, receiptKey: "", error: null,
        idempotencyKey: null, keyPlan: null, token: 0
      },
      busy: "none",
      retry: null,
      uploadFile: null,
      parseTimer: null,
      unsubscribe: null,
      ui: {}
    };

    function alive(gen) {
      return !state.disposed && state.mounted && state.generation === gen;
    }

    function listen(node, type, handler) {
      if (!node || typeof node.addEventListener !== "function") return function () {};
      node.addEventListener(type, handler);
      return function () {
        if (typeof node.removeEventListener === "function") node.removeEventListener(type, handler);
      };
    }

    var cleanup = [];

    /* ── DOM ─────────────────────────────────────────────────────── */

    function buildDom() {
      var root = el(doc, "div", "acp-s2scan");
      root.setAttribute("data-acp-module-state", "empty");
      root.setAttribute("data-acp-busy", "none");
      root.setAttribute("data-acp-scants", "off");
      root.setAttribute("data-acp-valid", "false");
      root.setAttribute("data-acp-source", "inline");

      var head = el(doc, "div", "acp-s2scan-head");
      head.appendChild(i18nEl(doc, "h2", "acp-s2scan-title", "next.s2scan.title"));
      head.appendChild(i18nEl(doc, "p", "acp-s2scan-sub", "next.s2scan.subtitle"));
      root.appendChild(head);

      var body = el(doc, "div", "acp-s2scan-body");
      root.appendChild(body);

      /* 1 — source structure */
      var sourceSection = el(doc, "section", "acp-s2scan-section");
      sourceSection.appendChild(i18nEl(doc, "h3", "acp-s2scan-heading", "next.s2scan.source_title"));

      var radios = el(doc, "div", "acp-s2scan-radios");
      var radioGroup = "acp-s2-source";
      state.ui.radios = {};
      [
        { value: "inline", key: "next.s2scan.source_inline" },
        { value: "task", key: "next.s2scan.source_task" },
        { value: "upload", key: "next.s2scan.source_upload" }
      ].forEach(function (option) {
        var label = el(doc, "label", "acp-s2scan-radio");
        var input = el(doc, "input");
        input.type = "radio";
        input.name = radioGroup;
        input.value = option.value;
        input.setAttribute("data-role", "source");
        input.checked = option.value === "inline";
        label.appendChild(input);
        label.appendChild(i18nEl(doc, "span", "acp-s2scan-radio-text", option.key));
        radios.appendChild(label);
        state.ui.radios[option.value] = input;
        cleanup.push(listen(input, "change", onSourceChanged));
      });
      sourceSection.appendChild(radios);

      var inlinePanel = el(doc, "div", "acp-s2scan-panel");
      inlinePanel.setAttribute("data-role", "panel-inline");
      inlinePanel.appendChild(i18nEl(doc, "p", "acp-s2scan-hint", "next.s2scan.source_inline_hint"));
      var xyzText = el(doc, "textarea", "acp-s2scan-text");
      xyzText.setAttribute("data-role", "xyz-text");
      xyzText.setAttribute("data-i18n-ph", "next.s2scan.xyz_placeholder");
      xyzText.setAttribute("placeholder", t("next.s2scan.xyz_placeholder"));
      xyzText.setAttribute("rows", "8");
      inlinePanel.appendChild(xyzText);
      state.ui.xyzText = xyzText;
      cleanup.push(listen(xyzText, "input", onInlineTextInput));
      var parseBtn = el(doc, "button", "acp-s2scan-btn", t("next.s2scan.parse"));
      parseBtn.type = "button";
      parseBtn.setAttribute("data-action", "parse");
      parseBtn.setAttribute("data-i18n", "next.s2scan.parse");
      cleanup.push(listen(parseBtn, "click", function () { parseInlineText(); }));
      inlinePanel.appendChild(parseBtn);
      sourceSection.appendChild(inlinePanel);
      state.ui.inlinePanel = inlinePanel;

      var taskPanel = el(doc, "div", "acp-s2scan-panel");
      taskPanel.setAttribute("data-role", "panel-task");
      taskPanel.hidden = true;
      taskPanel.appendChild(i18nEl(doc, "p", "acp-s2scan-hint", "next.s2scan.source_task_hint"));
      var taskRefEl = el(doc, "p", "acp-s2scan-mono");
      taskRefEl.setAttribute("data-role", "task-ref");
      taskRefEl.textContent = t("next.s2scan.task_no_ref");
      taskPanel.appendChild(taskRefEl);
      state.ui.taskRef = taskRefEl;
      var loadTaskBtn = el(doc, "button", "acp-s2scan-btn", t("next.s2scan.load_task"));
      loadTaskBtn.type = "button";
      loadTaskBtn.setAttribute("data-action", "load-task");
      loadTaskBtn.setAttribute("data-i18n", "next.s2scan.load_task");
      cleanup.push(listen(loadTaskBtn, "click", function () { loadTaskStructure(); }));
      taskPanel.appendChild(loadTaskBtn);
      state.ui.loadTaskBtn = loadTaskBtn;
      sourceSection.appendChild(taskPanel);
      state.ui.taskPanel = taskPanel;

      var uploadPanel = el(doc, "div", "acp-s2scan-panel");
      uploadPanel.setAttribute("data-role", "panel-upload");
      uploadPanel.hidden = true;
      uploadPanel.appendChild(i18nEl(doc, "p", "acp-s2scan-hint", "next.s2scan.source_upload_hint"));
      var fileInput = el(doc, "input", "acp-s2scan-file");
      fileInput.type = "file";
      fileInput.setAttribute("data-role", "file");
      fileInput.setAttribute("accept", ".xyz,.txt,text/plain");
      fileInput.setAttribute("aria-label", t("next.s2scan.choose_file"));
      cleanup.push(listen(fileInput, "change", onFileChosen));
      uploadPanel.appendChild(fileInput);
      state.ui.fileInput = fileInput;
      sourceSection.appendChild(uploadPanel);
      state.ui.uploadPanel = uploadPanel;

      var sourceStatus = el(doc, "p", "acp-s2scan-status");
      sourceStatus.setAttribute("data-role", "source-status");
      sourceStatus.setAttribute("role", "status");
      sourceSection.appendChild(sourceStatus);
      state.ui.sourceStatus = sourceStatus;
      body.appendChild(sourceSection);

      /* 2 — scan coordinate */
      var scanSection = el(doc, "section", "acp-s2scan-section");
      scanSection.appendChild(i18nEl(doc, "h3", "acp-s2scan-heading", "next.s2scan.scan_title"));
      var bondEl = el(doc, "p", "acp-s2scan-bond");
      bondEl.setAttribute("data-role", "bond-length");
      bondEl.textContent = t("next.s2scan.bond_length_unknown");
      scanSection.appendChild(bondEl);
      state.ui.bondLength = bondEl;

      var grid = el(doc, "div", "acp-s2scan-grid");
      state.ui.fields = {};
      [
        { role: "atom-a", key: "next.s2scan.atom_a", field: "atomA" },
        { role: "atom-b", key: "next.s2scan.atom_b", field: "atomB" },
        { role: "start", key: "next.s2scan.start", field: "start" },
        { role: "end", key: "next.s2scan.end", field: "end" },
        { role: "points", key: "next.s2scan.points", field: "points" }
      ].forEach(function (field) {
        var wrap = el(doc, "div", "acp-s2scan-field");
        var label = i18nEl(doc, "label", "acp-s2scan-label", field.key);
        label.setAttribute("for", "acp-s2-" + field.role);
        var input = el(doc, "input", "acp-s2scan-input");
        input.type = "text";
        input.id = "acp-s2-" + field.role;
        input.setAttribute("data-role", field.role);
        if (field.field === "points") input.setAttribute("inputmode", "numeric");
        label.appendChild(input);
        wrap.appendChild(label);
        grid.appendChild(wrap);
        state.ui.fields[field.field] = input;
        cleanup.push(listen(input, "input", onFormInput));
        cleanup.push(listen(input, "change", onFormInput));
      });
      scanSection.appendChild(grid);

      var scantsRow = el(doc, "div", "acp-s2scan-scants");
      var scantsLabel = el(doc, "label", "acp-s2scan-check");
      var scants = el(doc, "input");
      scants.type = "checkbox";
      scants.checked = false;
      scants.setAttribute("data-role", "scants");
      scantsLabel.appendChild(scants);
      scantsLabel.appendChild(i18nEl(doc, "span", "acp-s2scan-check-text", "next.s2scan.scants"));
      scantsRow.appendChild(scantsLabel);
      scantsRow.appendChild(i18nEl(doc, "p", "acp-s2scan-hint", "next.s2scan.scants_hint"));
      var scantsState = el(doc, "span", "acp-s2scan-badge");
      scantsState.setAttribute("data-role", "scants-state");
      scantsState.textContent = t("next.s2scan.scants_off");
      scantsRow.appendChild(scantsState);
      scanSection.appendChild(scantsRow);
      state.ui.scants = scants;
      state.ui.scantsState = scantsState;
      cleanup.push(listen(scants, "change", onScantsToggled));
      body.appendChild(scanSection);

      /* 3 — charge / multiplicity */
      var stateSection = el(doc, "section", "acp-s2scan-section");
      stateSection.appendChild(i18nEl(doc, "h3", "acp-s2scan-heading", "next.s2scan.state_title"));
      var stateGrid = el(doc, "div", "acp-s2scan-grid acp-s2scan-grid--two");
      var chargeWrap = el(doc, "div", "acp-s2scan-field");
      var chargeLabel = i18nEl(doc, "label", "acp-s2scan-label", "next.s2scan.charge");
      var chargeInput = el(doc, "input", "acp-s2scan-input");
      chargeInput.type = "text";
      chargeInput.setAttribute("data-role", "charge");
      chargeInput.setAttribute("inputmode", "numeric");
      chargeLabel.appendChild(chargeInput);
      chargeWrap.appendChild(chargeLabel);
      stateGrid.appendChild(chargeWrap);
      state.ui.fields.charge = chargeInput;
      cleanup.push(listen(chargeInput, "input", onFormInput));
      cleanup.push(listen(chargeInput, "change", onFormInput));

      var multWrap = el(doc, "div", "acp-s2scan-field");
      var multLabel = i18nEl(doc, "label", "acp-s2scan-label", "next.s2scan.multiplicity");
      var multInput = el(doc, "input", "acp-s2scan-input");
      multInput.type = "text";
      multInput.setAttribute("data-role", "multiplicity");
      multInput.setAttribute("inputmode", "numeric");
      multLabel.appendChild(multInput);
      multWrap.appendChild(multLabel);
      stateGrid.appendChild(multWrap);
      state.ui.fields.multiplicity = multInput;
      cleanup.push(listen(multInput, "input", onFormInput));
      cleanup.push(listen(multInput, "change", onFormInput));
      stateSection.appendChild(stateGrid);

      var parity = el(doc, "p", "acp-s2scan-status");
      parity.setAttribute("data-role", "parity");
      parity.hidden = true;
      stateSection.appendChild(parity);
      state.ui.parity = parity;
      body.appendChild(stateSection);

      /* 4 — preflight / submit */
      var reviewSection = el(doc, "section", "acp-s2scan-section");
      reviewSection.appendChild(i18nEl(doc, "h3", "acp-s2scan-heading", "next.s2scan.review_title"));
      reviewSection.appendChild(i18nEl(doc, "p", "acp-s2scan-subheading", "next.s2scan.summary"));
      var summary = el(doc, "pre", "acp-s2scan-summary");
      summary.setAttribute("data-role", "summary");
      reviewSection.appendChild(summary);
      state.ui.summary = summary;

      var errorsList = el(doc, "ul", "acp-s2scan-errors");
      errorsList.setAttribute("data-role", "errors");
      reviewSection.appendChild(errorsList);
      state.ui.errors = errorsList;

      var advisoriesLabel = i18nEl(doc, "p", "acp-s2scan-subheading", "next.s2scan.advisories");
      advisoriesLabel.setAttribute("data-role", "advisories-label");
      advisoriesLabel.hidden = true;
      reviewSection.appendChild(advisoriesLabel);
      state.ui.advisoriesLabel = advisoriesLabel;
      var advisoriesList = el(doc, "ul", "acp-s2scan-advisories");
      advisoriesList.setAttribute("data-role", "advisories");
      advisoriesList.hidden = true;
      reviewSection.appendChild(advisoriesList);
      state.ui.advisories = advisoriesList;

      var preflightStatus = el(doc, "p", "acp-s2scan-status");
      preflightStatus.setAttribute("data-role", "preflight-status");
      preflightStatus.setAttribute("role", "status");
      preflightStatus.textContent = t("next.s2scan.preflight_idle");
      reviewSection.appendChild(preflightStatus);
      state.ui.preflightStatus = preflightStatus;

      var preflightReasons = el(doc, "ul", "acp-s2scan-errors");
      preflightReasons.setAttribute("data-role", "preflight-reasons");
      preflightReasons.hidden = true;
      reviewSection.appendChild(preflightReasons);
      state.ui.preflightReasons = preflightReasons;

      var actions = el(doc, "div", "acp-s2scan-actions");
      var preflightBtn = el(doc, "button", "acp-s2scan-btn", t("next.s2scan.preflight"));
      preflightBtn.type = "button";
      preflightBtn.setAttribute("data-action", "preflight");
      preflightBtn.setAttribute("data-i18n", "next.s2scan.preflight");
      cleanup.push(listen(preflightBtn, "click", function () { runPreflight(); }));
      var submitBtn = el(doc, "button", "acp-s2scan-btn acp-s2scan-btn--primary", t("next.s2scan.submit"));
      submitBtn.type = "button";
      submitBtn.disabled = true;
      submitBtn.setAttribute("data-action", "submit");
      submitBtn.setAttribute("data-i18n", "next.s2scan.submit");
      cleanup.push(listen(submitBtn, "click", function () { submit(); }));
      actions.appendChild(preflightBtn);
      actions.appendChild(submitBtn);
      reviewSection.appendChild(actions);
      state.ui.preflightBtn = preflightBtn;
      state.ui.submitBtn = submitBtn;

      var receipt = el(doc, "div", "acp-s2scan-receipt");
      receipt.setAttribute("data-role", "receipt");
      receipt.setAttribute("role", "status");
      receipt.hidden = true;
      var receiptTitle = i18nEl(doc, "h4", "acp-s2scan-receipt-title", "next.s2scan.receipt");
      var receiptBody = el(doc, "p", "acp-s2scan-receipt-body");
      receiptBody.setAttribute("data-role", "receipt-body");
      var receiptDigest = el(doc, "p", "acp-s2scan-mono");
      receiptDigest.setAttribute("data-role", "receipt-digest");
      var receiptReplay = el(doc, "p", "acp-s2scan-hint");
      receiptReplay.setAttribute("data-role", "receipt-replay");
      receiptReplay.hidden = true;
      var openTaskBtn = el(doc, "button", "acp-s2scan-btn", t("next.s2scan.open_task"));
      openTaskBtn.type = "button";
      openTaskBtn.setAttribute("data-action", "open-task");
      openTaskBtn.setAttribute("data-i18n", "next.s2scan.open_task");
      openTaskBtn.hidden = true;
      cleanup.push(listen(openTaskBtn, "click", function () { openReceiptTask(); }));
      receipt.appendChild(receiptTitle);
      receipt.appendChild(receiptBody);
      receipt.appendChild(receiptDigest);
      receipt.appendChild(receiptReplay);
      receipt.appendChild(openTaskBtn);
      reviewSection.appendChild(receipt);
      state.ui.receipt = receipt;
      state.ui.receiptBody = receiptBody;
      state.ui.receiptDigest = receiptDigest;
      state.ui.receiptReplay = receiptReplay;
      state.ui.openTaskBtn = openTaskBtn;
      body.appendChild(reviewSection);

      var note = i18nEl(doc, "p", "acp-s2scan-note", "next.s2scan.method_note");
      body.appendChild(note);

      /* empty hint + async overlay */
      var emptyHint = el(doc, "p", "acp-s2scan-empty");
      emptyHint.setAttribute("data-role", "empty-hint");
      emptyHint.textContent = t("next.s2scan.empty_hint");
      root.appendChild(emptyHint);
      state.ui.emptyHint = emptyHint;

      var overlay = el(doc, "div", "acp-s2scan-overlay");
      overlay.setAttribute("data-role", "overlay");
      overlay.hidden = true;
      var box = el(doc, "div", "acp-s2scan-box");
      var overlayTitle = el(doc, "p", "acp-s2scan-message");
      overlayTitle.setAttribute("data-role", "overlay-message");
      overlayTitle.setAttribute("aria-live", "polite");
      var overlayDetail = el(doc, "p", "acp-s2scan-detail");
      overlayDetail.setAttribute("data-role", "overlay-detail");
      overlayDetail.hidden = true;
      var retryBtn = el(doc, "button", "acp-s2scan-btn", t("next.s2scan.retry"));
      retryBtn.type = "button";
      retryBtn.setAttribute("data-action", "retry");
      retryBtn.setAttribute("data-i18n", "next.s2scan.retry");
      retryBtn.hidden = true;
      cleanup.push(listen(retryBtn, "click", function () { retrySource(); }));
      box.appendChild(overlayTitle);
      box.appendChild(overlayDetail);
      box.appendChild(retryBtn);
      overlay.appendChild(box);
      root.appendChild(overlay);
      state.ui.overlay = overlay;
      state.ui.overlayMessage = overlayTitle;
      state.ui.overlayDetail = overlayDetail;
      state.ui.retryBtn = retryBtn;

      state.root = root;
      state.host.appendChild(root);
      syncForm();
    }

    /* ── form input ──────────────────────────────────────────────── */

    function syncForm() {
      var ui = state.ui;
      if (!ui || !ui.fields) return;
      if (ui.fields.atomA) ui.fields.atomA.value = state.form.atomA;
      if (ui.fields.atomB) ui.fields.atomB.value = state.form.atomB;
      if (ui.fields.start) ui.fields.start.value = state.form.start;
      if (ui.fields.end) ui.fields.end.value = state.form.end;
      if (ui.fields.points) ui.fields.points.value = state.form.points;
      if (ui.fields.charge) ui.fields.charge.value = state.form.charge;
      if (ui.fields.multiplicity) ui.fields.multiplicity.value = state.form.multiplicity;
      if (ui.scants) ui.scants.checked = state.form.scants === true;
      if (ui.xyzText && state.source.kind === "inline" && state.source.text &&
          ui.xyzText.value !== state.source.text) {
        ui.xyzText.value = state.source.text;
      }
    }

    function readForm() {
      var ui = state.ui;
      var fields = ui.fields || {};
      return {
        atomA: textOf(fields.atomA && fields.atomA.value).trim(),
        atomB: textOf(fields.atomB && fields.atomB.value).trim(),
        start: textOf(fields.start && fields.start.value).trim(),
        end: textOf(fields.end && fields.end.value).trim(),
        points: textOf(fields.points && fields.points.value).trim(),
        charge: textOf(fields.charge && fields.charge.value).trim(),
        multiplicity: textOf(fields.multiplicity && fields.multiplicity.value).trim(),
        scants: !!(ui.scants && ui.scants.checked)
      };
    }

    function validationInput() {
      return {
        atoms: state.source.atoms,
        atomA: state.form.atomA,
        atomB: state.form.atomB,
        start: state.form.start,
        end: state.form.end,
        points: state.form.points,
        charge: state.form.charge,
        multiplicity: state.form.multiplicity
      };
    }

    function currentRequest() {
      var check = validateConfig(validationInput());
      if (!check.ok || state.source.status !== "ready") return null;
      return buildRequest({
        xyz: state.source.text,
        atomA: state.form.atomA,
        atomB: state.form.atomB,
        start: state.form.start,
        end: state.form.end,
        points: state.form.points,
        charge: state.form.charge,
        multiplicity: state.form.multiplicity,
        scants: state.form.scants,
        projectId: state.projectId
      });
    }

    /** Any config edit invalidates the previous preflight and pending command. */
    function touchConfig() {
      state.configRevision += 1;
      state.preflight.token += 1;
      state.preflight.status = "idle";
      state.preflight.fingerprint = null;
      state.preflight.planKey = null;
      state.preflight.reasons = [];
      state.preflight.advisories = [];
      state.preflight.revision = -1;
      state.submit.token += 1;
      state.submit.status = "idle";
      state.submit.error = null;
      state.busy = "none";
    }

    function refreshValidation() {
      state.validation = validateConfig(validationInput());
    }

    function onFormInput() {
      if (!state.mounted || state.disposed) return;
      state.form = readForm();
      touchConfig();
      refreshValidation();
      render();
    }

    function onScantsToggled() {
      if (!state.mounted || state.disposed) return;
      state.form.scants = !!(state.ui.scants && state.ui.scants.checked);
      touchConfig();
      refreshValidation();
      render();
    }

    /* ── structure sources ───────────────────────────────────────── */

    function resetSource(kind) {
      state.source.token += 1;
      state.source.kind = kind || state.source.kind;
      state.source.status = "empty";
      state.source.error = null;
      state.source.text = "";
      state.source.atoms = [];
      state.source.count = 0;
      state.source.declared = null;
      state.source.meta = null;
      state.source.notice = null;
      state.retry = null;
    }

    function setSourceLoading(kind, retry) {
      state.source.kind = kind;
      state.source.status = "loading";
      state.source.error = null;
      state.source.notice = null;
      state.retry = typeof retry === "function" ? retry : null;
      touchConfig();
      render();
    }

    function setSourceError(error, retry) {
      state.source.status = "error";
      state.source.error = describeError(error);
      state.source.atoms = [];
      state.source.count = 0;
      state.retry = typeof retry === "function" ? retry : null;
      touchConfig();
      render();
    }

    function applyStructureText(text, meta) {
      var parsed = parseXyz(text);
      state.source.meta = meta || null;
      state.source.text = textOf(text);
      if (!parsed.ok) {
        state.source.status = parsed.empty ? "empty" : "error";
        state.source.error = parsed.empty
          ? null
          : t("next.s2scan.err.parse_failed", { message: parsed.error || "invalid XYZ" });
        state.source.atoms = [];
        state.source.count = 0;
        state.source.declared = parsed.declared;
        state.source.notice = null;
        touchConfig();
        render();
        return false;
      }
      state.source.status = "ready";
      state.source.error = null;
      state.source.notice = null;
      state.source.atoms = parsed.atoms;
      state.source.count = parsed.count;
      state.source.declared = parsed.declared;
      state.retry = null;
      touchConfig();
      refreshValidation();
      render();
      return true;
    }

    function onInlineTextInput() {
      if (!state.mounted || state.disposed) return;
      state.source.kind = "inline";
      state.source.text = textOf(state.ui.xyzText && state.ui.xyzText.value);
      if (state.parseTimer != null) {
        clearTimeout(state.parseTimer);
        state.parseTimer = null;
      }
      // The old atoms no longer describe this text: drop them NOW so a fast
      // preflight click can never pair stale geometry with new text.
      state.source.token += 1;
      state.source.atoms = [];
      state.source.count = 0;
      state.source.status = "empty";
      state.source.error = null;
      state.source.notice = null;
      touchConfig();
      refreshValidation();
      render();
      if (state.suspended) return;
      var gen = state.generation;
      state.parseTimer = setTimeout(function () {
        state.parseTimer = null;
        if (!alive(gen) || state.suspended) return;
        parseInlineText();
      }, parseDebounceMs);
    }

    function parseInlineText() {
      if (!state.mounted || state.disposed) return;
      if (state.parseTimer != null) {
        clearTimeout(state.parseTimer);
        state.parseTimer = null;
      }
      var text = textOf(state.ui.xyzText && state.ui.xyzText.value);
      state.source.kind = "inline";
      state.source.text = text;
      if (!text.trim()) {
        resetSource("inline");
        touchConfig();
        refreshValidation();
        render();
        return false;
      }
      setSourceLoading("inline", parseInlineText);
      return applyStructureText(text, { kind: "inline" });
    }

    function onSourceChanged() {
      if (!state.mounted || state.disposed) return;
      var selected = "inline";
      Object.keys(state.ui.radios || {}).forEach(function (key) {
        if (state.ui.radios[key] && state.ui.radios[key].checked) selected = key;
      });
      state.source.kind = selected;
      state.source.token += 1;
      if (selected === "inline") {
        var pending = textOf(state.ui.xyzText && state.ui.xyzText.value);
        if (pending.trim()) {
          state.source.text = pending;
          parseInlineText();
          return;
        }
        state.source.status = "empty";
      } else {
        state.source.status = "empty";
        state.source.text = "";
      }
      state.source.atoms = [];
      state.source.count = 0;
      state.source.error = null;
      state.source.meta = null;
      state.source.notice = null;
      state.retry = null;
      touchConfig();
      refreshValidation();
      render();
    }

    /** Injected seam first, then the canonical structure-reference loader. */
    function loadStructureText(ref) {
      if (typeof deps.loadStructure === "function") {
        return Promise.resolve(deps.loadStructure(ref)).then(normalizeLoadedStructure);
      }
      if (ref && ref.kind === "inline-xyz" && typeof ref.text === "string") {
        return Promise.resolve(ref.text);
      }
      if (!requestFn) {
        return Promise.reject(new Error(t("next.s2scan.err.client_missing")));
      }
      var view = typeof window !== "undefined" ? window.ACPView && window.ACPView.source : null;
      if (!view || typeof view.buildStructureUrl !== "function") {
        return Promise.reject(new Error(t("next.s2scan.err.structure_adapter_missing")));
      }
      // Canonical URL for a frozen structureRef — never assembled here.
      var url = view.buildStructureUrl(ref, API);
      return Promise.resolve(requestFn(url, { method: "GET" })).then(function (body) {
        if (typeof body === "string" && body.trim()) return body;
        if (body && typeof body.content === "string" && body.content.trim()) return body.content;
        if (body && typeof body.xyz === "string" && body.xyz.trim()) return body.xyz;
        throw new Error(t("next.s2scan.err.structure_empty"));
      });
    }

    function normalizeLoadedStructure(result) {
      if (typeof result === "string" && result.trim()) return result;
      if (result && typeof result.xyz === "string" && result.xyz.trim()) return result.xyz;
      if (result && typeof result.content === "string" && result.content.trim()) return result.content;
      throw new Error(t("next.s2scan.err.structure_empty"));
    }

    function loadTaskStructure() {
      if (!state.mounted || state.disposed) return Promise.resolve({ ok: false });
      var ref = state.structureRef;
      state.source.kind = "task";
      if (!ref) {
        state.source.status = "empty";
        state.source.error = null;
        state.source.notice = null;
        state.source.atoms = [];
        state.source.count = 0;
        render();
        return Promise.resolve({ ok: false, reason: "no-ref" });
      }
      var token = state.source.token + 1;
      state.source.token = token;
      var gen = state.generation;
      setSourceLoading("task", loadTaskStructure);
      return loadStructureText(ref).then(function (text) {
        if (!alive(gen) || state.source.token !== token) return { ok: false, stale: true };
        applyStructureText(text, { kind: "task", ref: ref });
        return { ok: state.source.status === "ready" };
      }).catch(function (error) {
        if (!alive(gen) || state.source.token !== token) return { ok: false, stale: true };
        setSourceError(new Error(t("next.s2scan.err.structure_read_failed", {
          message: describeError(error)
        })), loadTaskStructure);
        return { ok: false, error: describeError(error) };
      });
    }

    function readUploadedText(file) {
      if (typeof deps.readFile === "function") {
        return Promise.resolve(deps.readFile(file));
      }
      if (file && typeof file.text === "function") {
        return Promise.resolve(file.text());
      }
      if (typeof window !== "undefined" && typeof window.FileReader === "function") {
        return new Promise(function (resolve, reject) {
          var reader = new window.FileReader();
          reader.onload = function () { resolve(String(reader.result || "")); };
          reader.onerror = function () { reject(new Error(t("next.s2scan.err.file_unsupported"))); };
          reader.readAsText(file);
        });
      }
      return Promise.reject(new Error(t("next.s2scan.err.file_unsupported")));
    }

    function onFileChosen(event) {
      if (!state.mounted || state.disposed) return;
      var target = event && event.target;
      var files = target && target.files;
      var file = files && files.length ? files[0] : null;
      if (!file) return;
      state.uploadFile = file;
      state.source.kind = "upload";
      var token = state.source.token + 1;
      state.source.token = token;
      var gen = state.generation;
      setSourceLoading("upload", reloadUploadedFile);
      readUploadedText(file).then(function (text) {
        if (!alive(gen) || state.source.token !== token) return;
        if (state.ui.xyzText) state.ui.xyzText.value = textOf(text);
        applyStructureText(text, { kind: "upload", name: textOf(file.name) });
      }).catch(function (error) {
        if (!alive(gen) || state.source.token !== token) return;
        setSourceError(error, reloadUploadedFile);
      });
    }

    function reloadUploadedFile() {
      if (!state.uploadFile) return Promise.resolve({ ok: false });
      onFileChosen({ target: { files: [state.uploadFile] } });
      return Promise.resolve({ ok: true });
    }

    function retrySource() {
      if (typeof state.retry !== "function") return;
      var fn = state.retry;
      fn();
    }

    /* ── preflight / submit ──────────────────────────────────────── */

    function runPreflight() {
      if (!state.mounted || state.disposed) return Promise.resolve({ ok: false });
      if (state.busy !== "none") return Promise.resolve({ ok: false });
      var check = validateConfig(validationInput());
      refreshValidation();
      if (!check.ok || state.source.status !== "ready") {
        state.preflight.status = "blocked";
        state.preflight.reasons = [];
        render();
        return Promise.resolve({ ok: false, reason: "invalid" });
      }
      if (!requestFn) {
        state.preflight.status = "error";
        state.preflight.reasons = [t("next.s2scan.err.client_missing")];
        render();
        return Promise.resolve({ ok: false, reason: "client" });
      }
      var request = currentRequest();
      if (!request) return Promise.resolve({ ok: false, reason: "invalid" });
      var token = state.preflight.token + 1;
      state.preflight.token = token;
      var gen = state.generation;
      state.preflight.status = "running";
      state.preflight.reasons = [];
      state.preflight.advisories = [];
      state.preflight.fingerprint = null;
      state.preflight.revision = -1;
      state.busy = "preflight";
      render();
      return Promise.resolve(requestFn(API + "/preflight", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request)
      })).then(function (payload) {
        if (!alive(gen) || state.preflight.token !== token) return { ok: false, stale: true };
        state.busy = "none";
        var feasible = !!(payload && payload.feasible === true);
        var digest = payload && payload.plan_digest ? String(payload.plan_digest) : "";
        if (!feasible || !digest) {
          state.preflight.status = "infeasible";
          state.preflight.reasons = (payload && Array.isArray(payload.reasons) ? payload.reasons : [])
            .map(renderReasonText);
          state.preflight.advisories = renderAdvisoryTexts(payload);
          state.preflight.fingerprint = null;
          state.preflight.revision = -1;
          render();
          return { ok: false, feasible: feasible };
        }
        state.preflight.status = "ok";
        state.preflight.reasons = [];
        state.preflight.advisories = renderAdvisoryTexts(payload);
        state.preflight.fingerprint = digest;
        state.preflight.planKey = planKeyOf(request);
        state.preflight.revision = state.configRevision;
        render();
        return { ok: true, digest: digest };
      }).catch(function (error) {
        if (!alive(gen) || state.preflight.token !== token) return { ok: false, stale: true };
        state.busy = "none";
        state.preflight.status = "error";
        state.preflight.reasons = [t("next.s2scan.preflight_error", { message: describeError(error) })];
        state.preflight.advisories = [];
        state.preflight.fingerprint = null;
        state.preflight.revision = -1;
        render();
        return { ok: false, error: describeError(error) };
      });
    }

    function renderReasonText(reason) {
      if (!reason) return "";
      if (typeof reason === "string") return reason;
      var code = reason.code ? String(reason.code) : "";
      var message = reason.message ? String(reason.message) : "";
      return (code ? code + ": " : "") + (message || JSON.stringify(reason));
    }

    function renderAdvisoryTexts(payload) {
      var list = payload && Array.isArray(payload.advisories) ? payload.advisories : [];
      return list.map(renderReasonText).filter(function (text) { return !!text; });
    }

    function canSubmit() {
      if (!requestFn) return false;
      if (state.busy !== "none") return false;
      if (state.submit.status === "submitting") return false;
      if (state.source.status !== "ready") return false;
      if (state.preflight.status !== "ok") return false;
      if (!state.preflight.fingerprint) return false;
      if (state.preflight.revision !== state.configRevision) return false;
      var request = currentRequest();
      if (!request) return false;
      var planKey = planKeyOf(request);
      if (state.preflight.planKey !== planKey) return false;
      if (state.submit.receipt && state.submit.receiptKey === planKey) return false;
      return true;
    }

    function submit() {
      if (!state.mounted || state.disposed) return Promise.resolve({ ok: false });
      if (!canSubmit()) {
        render();
        return Promise.resolve({ ok: false, reason: "blocked" });
      }
      var request = currentRequest();
      var planKey = planKeyOf(request);
      if (!state.submit.idempotencyKey || state.submit.keyPlan !== planKey) {
        state.submit.idempotencyKey = newIdempotencyKey();
        state.submit.keyPlan = planKey;
      }
      var body = request;
      body.preflight_fingerprint = state.preflight.fingerprint;
      var token = state.submit.token + 1;
      state.submit.token = token;
      var gen = state.generation;
      state.submit.status = "submitting";
      state.submit.error = null;
      state.busy = "submit";
      render();
      return Promise.resolve(requestFn(API + "/jobs", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": state.submit.idempotencyKey
        },
        body: JSON.stringify(body)
      })).then(function (receipt) {
        if (!alive(gen) || state.submit.token !== token) return { ok: false, stale: true };
        state.busy = "none";
        state.submit.status = "submitted";
        state.submit.receipt = receipt && typeof receipt === "object" ? receipt : {};
        state.submit.receiptKey = planKey;
        state.submit.idempotencyKey = null;
        state.submit.keyPlan = null;
        render();
        deliverReceipt(state.submit.receipt);
        return { ok: true, receipt: state.submit.receipt };
      }).catch(function (error) {
        if (!alive(gen) || state.submit.token !== token) return { ok: false, stale: true };
        state.busy = "none";
        state.submit.status = "error";
        var status = error && error.status;
        var message = describeError(error);
        if (status === 400 && error.body && error.body.detail && error.body.detail.code === "preflight_stale") {
          state.submit.error = t("next.s2scan.preflight_edited");
          state.preflight.status = "idle";
          state.preflight.fingerprint = null;
          state.preflight.planKey = null;
          state.preflight.revision = -1;
        } else if (isForbidden(error)) {
          state.submit.error = message;
        } else {
          state.submit.error = message;
        }
        render();
        return { ok: false, error: message };
      });
    }

    function deliverReceipt(receipt) {
      if (!commands || typeof commands.openTask !== "function" || !receipt || !receipt.job_id) return;
      try {
        commands.openTask({
          jobId: String(receipt.job_id),
          attempt: Number(receipt.attempt || 1),
          source: MODULE_ID
        });
      } catch (error) {
        if (typeof console !== "undefined" && console && console.error) {
          console.error("s2-scan: openTask after submit failed", error);
        }
      }
    }

    function openReceiptTask() {
      var receipt = state.submit.receipt;
      if (!receipt || !receipt.job_id) return;
      deliverReceipt(receipt);
    }

    /* ── rendering ──────────────────────────────────────────────── */

    function renderSourcePanels() {
      var kind = state.source.kind;
      if (state.ui.inlinePanel) state.ui.inlinePanel.hidden = kind !== "inline";
      if (state.ui.taskPanel) state.ui.taskPanel.hidden = kind !== "task";
      if (state.ui.uploadPanel) state.ui.uploadPanel.hidden = kind !== "upload";
      Object.keys(state.ui.radios || {}).forEach(function (key) {
        var radio = state.ui.radios[key];
        if (radio) radio.checked = key === kind;
      });
    }

    function renderSourceStatus() {
      var status = state.source.status;
      var text = "";
      if (status === "ready") {
        text = t("next.s2scan.source_status_ready", {
          count: state.source.count,
          source: sourceLabel(state.source.kind)
        });
      } else if (status === "loading") {
        text = t("next.s2scan.loading");
      } else if (state.source.notice) {
        text = t(state.source.notice);
      } else {
        text = t("next.s2scan.source_status_empty");
      }
      if (state.ui.sourceStatus) state.ui.sourceStatus.textContent = text;
      if (state.ui.taskRef) {
        state.ui.taskRef.textContent = String(state.structureRef
          ? t("next.s2scan.task_ref", { ref: formatStructureRef(state.structureRef) })
          : t("next.s2scan.task_no_ref"));
      }
      if (state.ui.loadTaskBtn) {
        state.ui.loadTaskBtn.disabled = !state.structureRef || state.busy !== "none";
        state.ui.loadTaskBtn.textContent = state.source.kind === "task" && status === "ready"
          ? t("next.s2scan.reload_task")
          : t("next.s2scan.load_task");
      }
    }

    function renderBondLength() {
      if (!state.ui.bondLength) return;
      var check = state.validation;
      if (isFiniteNumber(check.bondLength) && check.atomA && check.atomB) {
        state.ui.bondLength.textContent = t("next.s2scan.bond_length", {
          value: Number(check.bondLength).toFixed(3),
          a: check.atomA,
          b: check.atomB
        });
      } else {
        state.ui.bondLength.textContent = t("next.s2scan.bond_length_unknown");
      }
    }

    function renderScants() {
      var on = state.form.scants === true;
      if (state.ui.scants) state.ui.scants.checked = on;
      if (state.ui.scantsState) {
        state.ui.scantsState.textContent = on
          ? t("next.s2scan.scants_on")
          : t("next.s2scan.scants_off");
        state.ui.scantsState.className = "acp-s2scan-badge" + (on ? " is-on" : "");
      }
      if (state.root) state.root.setAttribute("data-acp-scants", on ? "on" : "off");
    }

    function renderValidation() {
      var check = state.validation;
      if (state.ui.errors) {
        clearChildren(state.ui.errors);
        check.errors.forEach(function (error) {
          var item = doc.createElement("li");
          item.setAttribute("data-error", error.code);
          item.setAttribute("data-field", error.field || "");
          item.textContent = errorText(error);
          state.ui.errors.appendChild(item);
        });
        state.ui.errors.hidden = check.errors.length === 0;
      }
      if (state.ui.parity) {
        if (!check.errors.length && check.parityChecked) {
          state.ui.parity.textContent = t("next.s2scan.parity_ok", { electrons: check.electrons });
          state.ui.parity.hidden = false;
        } else if (!check.errors.length && check.electrons === null) {
          state.ui.parity.textContent = t("next.s2scan.parity_unverifiable");
          state.ui.parity.hidden = false;
        } else {
          state.ui.parity.textContent = "";
          state.ui.parity.hidden = true;
        }
      }
      if (state.root) state.root.setAttribute("data-acp-valid", check.ok ? "true" : "false");
    }

    function renderSummary() {
      if (!state.ui.summary) return;
      if (state.source.status !== "ready") {
        state.ui.summary.textContent = t("next.s2scan.empty");
        return;
      }
      var atomA = Number(state.form.atomA);
      var atomB = Number(state.form.atomB);
      var wire = isFinite(atomA) && isFinite(atomB)
        ? String(atomA - 1) + "," + String(atomB - 1)
        : "—";
      state.ui.summary.textContent = t("next.s2scan.summary_lines", {
        workflow: WORKFLOW,
        a: state.form.atomA,
        b: state.form.atomB,
        wire: wire,
        start: state.form.start,
        end: state.form.end,
        points: state.form.points,
        scants: state.form.scants ? t("next.s2scan.scants_on_short") : t("next.s2scan.scants_off_short"),
        charge: state.form.charge,
        multiplicity: state.form.multiplicity,
        atoms: state.source.count,
        source: sourceLabel(state.source.kind)
      });
    }

    function renderPreflight() {
      if (!state.ui.preflightStatus) return;
      var text = "";
      var status = state.preflight.status;
      if (status === "ok") {
        text = t("next.s2scan.preflight_ok", {
          digest: String(state.preflight.fingerprint || "").slice(0, 23)
        });
      } else if (status === "running") {
        text = t("next.s2scan.preflighting");
      } else if (status === "infeasible") {
        text = t("next.s2scan.preflight_failed");
      } else if (status === "error") {
        text = (state.preflight.reasons || [])[0] || t("next.s2scan.preflight_idle");
      } else if (status === "blocked") {
        text = t("next.s2scan.preflight_blocked");
      } else {
        text = t("next.s2scan.preflight_idle");
      }
      state.ui.preflightStatus.textContent = text;

      if (state.ui.preflightReasons) {
        clearChildren(state.ui.preflightReasons);
        var reasons = status === "infeasible" || status === "error"
          ? (state.preflight.reasons || [])
          : [];
        reasons.forEach(function (line) {
          var item = doc.createElement("li");
          item.setAttribute("data-preflight-reason", "1");
          item.textContent = line;
          state.ui.preflightReasons.appendChild(item);
        });
        state.ui.preflightReasons.hidden = reasons.length === 0;
      }

      if (state.ui.advisories) {
        clearChildren(state.ui.advisories);
        var advisories = state.preflight.advisories || [];
        advisories.forEach(function (line) {
          var item = doc.createElement("li");
          item.textContent = line;
          state.ui.advisories.appendChild(item);
        });
        state.ui.advisories.hidden = advisories.length === 0;
        if (state.ui.advisoriesLabel) state.ui.advisoriesLabel.hidden = advisories.length === 0;
      }

      var busy = state.busy !== "none";
      if (state.ui.preflightBtn) {
        state.ui.preflightBtn.disabled = busy || state.source.status !== "ready";
        state.ui.preflightBtn.textContent = state.preflight.status === "running"
          ? t("next.s2scan.preflighting")
          : t("next.s2scan.preflight");
      }
      if (state.ui.submitBtn) {
        state.ui.submitBtn.disabled = !canSubmit();
        state.ui.submitBtn.textContent = state.submit.status === "submitting"
          ? t("next.s2scan.submitting")
          : t("next.s2scan.submit");
        state.ui.submitBtn.setAttribute("title",
          canSubmit() ? "" : t("next.s2scan.submit_blocked"));
      }
    }

    function renderReceipt() {
      if (!state.ui.receipt) return;
      var receipt = state.submit.receipt;
      if (state.submit.status === "error" && state.submit.error) {
        state.ui.receipt.hidden = false;
        state.ui.receipt.setAttribute("data-acp-receipt-state", "error");
        state.ui.receiptBody.textContent = state.submit.error;
        state.ui.receiptDigest.textContent = "";
        state.ui.receiptReplay.hidden = true;
        if (state.ui.openTaskBtn) state.ui.openTaskBtn.hidden = true;
        return;
      }
      if (state.submit.status !== "submitted" || !receipt) {
        state.ui.receipt.hidden = true;
        return;
      }
      state.ui.receipt.hidden = false;
      state.ui.receipt.setAttribute("data-acp-receipt-state", "submitted");
      state.ui.receiptBody.textContent = t("next.s2scan.receipt_body", {
        jobId: String(receipt.job_id || ""),
        status: String(receipt.status || ""),
        attempt: Number(receipt.attempt || 1)
      });
      state.ui.receiptDigest.textContent = receipt.plan_digest
        ? t("next.s2scan.receipt_digest", { digest: String(receipt.plan_digest) })
        : "";
      state.ui.receiptReplay.hidden = receipt.idempotent_replay !== true;
      if (state.ui.openTaskBtn) {
        state.ui.openTaskBtn.hidden = !(commands && typeof commands.openTask === "function" && receipt.job_id);
      }
    }

    function renderOverlay() {
      if (!state.ui.overlay) return;
      var status = state.source.status;
      var show = status === "loading" || status === "error";
      state.ui.overlay.hidden = !show;
      if (state.ui.emptyHint) state.ui.emptyHint.hidden = status !== "empty";
      if (!show) return;
      var isError = status === "error";
      state.ui.overlayMessage.textContent = isError
        ? t("next.s2scan.error_title")
        : t("next.s2scan.loading");
      state.ui.overlayDetail.textContent = isError ? String(state.source.error || "") : "";
      state.ui.overlayDetail.hidden = !isError || !state.source.error;
      state.ui.retryBtn.hidden = !isError || typeof state.retry !== "function";
    }

    function render() {
      if (!state.root) return;
      state.root.setAttribute("data-acp-module-state", state.source.status);
      state.root.setAttribute("data-acp-busy", state.busy);
      state.root.setAttribute("data-acp-source", state.source.kind);
      renderSourcePanels();
      renderSourceStatus();
      renderBondLength();
      renderScants();
      renderValidation();
      renderSummary();
      renderPreflight();
      renderReceipt();
      renderOverlay();
    }

    /* ── context ────────────────────────────────────────────────── */

    function applySnapshot(snapshot) {
      if (!state.mounted || state.disposed) return;
      snapshot = snapshot || {};
      state.snapshot = snapshot;
      var nextRef = snapshot.structureRef || null;
      var nextTask = snapshot.taskRef || null;
      var nextProject = snapshot.projectId ? String(snapshot.projectId) : "";
      var nextKey = structureRefKey(nextRef) + "|" + (nextTask && nextTask.jobId ? String(nextTask.jobId) : "");
      var changed = nextKey !== state.structureKey;
      var projectChanged = nextProject !== state.projectId;
      state.taskRef = nextTask;
      state.structureRef = nextRef;
      state.structureKey = nextKey;
      state.projectId = nextProject;
      if (changed || projectChanged) {
        state.source.token += 1;
        if (changed && state.source.kind === "task") {
          // A new structure focus is not auto-fetched: the user re-loads
          // explicitly (no surprise network request on focus change).
          state.source.status = "empty";
          state.source.atoms = [];
          state.source.count = 0;
          state.source.text = "";
          state.source.error = null;
          state.source.meta = null;
          state.source.notice = "next.s2scan.task_changed";
          if (state.ui.xyzText) state.ui.xyzText.value = "";
          state.retry = null;
        }
        touchConfig();
        refreshValidation();
        render();
      } else {
        renderSourceStatus();
      }
    }

    function onContextNotify() {
      if (!state.mounted || state.disposed || !context || typeof context.getSnapshot !== "function") return;
      try {
        applySnapshot(context.getSnapshot());
      } catch (error) { /* malformed snapshot never breaks the panel */ }
    }

    /* ── lifecycle ──────────────────────────────────────────────── */

    function clearParseTimer() {
      if (state.parseTimer != null) {
        clearTimeout(state.parseTimer);
        state.parseTimer = null;
      }
    }

    function mount(hostEl, api) {
      api = api || {};
      hostEl = hostEl || (api.panel && api.panel.bodyEl);
      if (!hostEl || typeof hostEl.appendChild !== "function") {
        return Promise.reject(new TypeError("s2-scan mount: host 必须是 DOM 元素"));
      }
      if (!doc || typeof doc.createElement !== "function") {
        return Promise.reject(new TypeError("s2-scan: document 不可用"));
      }
      if (state.mounted) {
        return Promise.reject(new Error("s2-scan: already mounted (dispose() first)"));
      }
      state.generation += 1;
      state.mounted = true;
      state.disposed = false;
      state.suspended = false;
      state.host = hostEl;
      state.busy = "none";
      state.form = {
        atomA: "1", atomB: "2", start: "1.0", end: "3.0",
        points: "21", charge: "0", multiplicity: "1", scants: false
      };
      resetSource("inline");
      state.configRevision = 0;
      state.preflight = {
        status: "idle", fingerprint: null, planKey: null, reasons: [], advisories: [],
        token: 0, revision: -1
      };
      state.submit = {
        status: "idle", receipt: null, receiptKey: "", error: null,
        idempotencyKey: null, keyPlan: null, token: 0
      };
      state.uploadFile = null;
      cleanup.length = 0;

      var apiContext = api.context || null;
      if (!context && apiContext) context = apiContext;
      var apiCommands = api.commands || null;
      if (!commands && apiCommands) commands = apiCommands;

      buildDom();
      refreshValidation();
      var snapshot = null;
      if (context && typeof context.getSnapshot === "function") {
        try { snapshot = context.getSnapshot(); } catch (e) { snapshot = null; }
      }
      if (snapshot) applySnapshot(snapshot);
      if (context && typeof context.subscribe === "function") {
        state.unsubscribe = context.subscribe(onContextNotify);
      }
      if (doc && typeof doc.addEventListener === "function") {
        var langHandler = function () {
          if (!state.mounted || state.disposed) return;
          render();
        };
        doc.addEventListener("acp:langchange", langHandler);
        cleanup.push(function () { doc.removeEventListener("acp:langchange", langHandler); });
      }
      render();
      return Promise.resolve();
    }

    function updateContext(snapshot) {
      if (!state.mounted || state.disposed) return;
      applySnapshot(snapshot || (context && typeof context.getSnapshot === "function"
        ? context.getSnapshot()
        : {}));
    }

    function suspend() {
      state.suspended = true;
      clearParseTimer();
    }

    function resume() {
      state.suspended = false;
      if (state.source.kind === "inline" && state.source.text) parseInlineText();
    }

    function dispose() {
      if (state.disposed) return;
      state.disposed = true;
      state.mounted = false;
      state.generation += 1;
      state.source.token += 1;
      state.preflight.token += 1;
      state.submit.token += 1;
      state.busy = "none";
      clearParseTimer();
      cleanup.splice(0).forEach(function (fn) {
        try { fn(); } catch (error) { /* listener already gone */ }
      });
      if (state.unsubscribe) {
        try { state.unsubscribe(); } catch (error) { /* context already gone */ }
        state.unsubscribe = null;
      }
      if (state.root && state.root.parentNode &&
          typeof state.root.parentNode.removeChild === "function") {
        try { state.root.parentNode.removeChild(state.root); } catch (error) { /* detached */ }
      }
      state.root = null;
      state.host = null;
      state.ui = {};
      state.retry = null;
      state.uploadFile = null;
      state.source.atoms = [];
      state.source.count = 0;
      state.source.text = "";
      state.preflight.reasons = [];
      state.preflight.advisories = [];
      state.submit.receipt = null;
    }

    return {
      mount: mount,
      updateContext: updateContext,
      suspend: suspend,
      resume: resume,
      dispose: dispose
    };
  }

  window.ACPNextModules[MODULE_ID] = {
    descriptor: DESCRIPTOR,
    factory: function (deps) { return createInstance(deps || {}); },
    _internals: {
      parseXyz: parseXyz,
      validateConfig: validateConfig,
      buildRequest: buildRequest,
      scanCoordinateString: scanCoordinateString,
      atomDistance: atomDistance,
      electronCount: electronCount,
      atomicNumber: atomicNumber,
      formatStructureRef: formatStructureRef,
      structureRefKey: structureRefKey,
      planKeyOf: planKeyOf,
      MIN_POINTS: MIN_POINTS,
      MAX_POINTS: MAX_POINTS,
      WORKFLOW: WORKFLOW,
      METHOD_SCHEMA_ID: METHOD_SCHEMA_ID
    }
  };
})();
