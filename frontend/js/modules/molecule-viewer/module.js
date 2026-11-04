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
 * A-45 measurements (read-only) and A-46 internal-coordinate edits share the
 * same immutable frame: the viewer's currentFrame is NEVER mutated. An edit
 * derives a local displaced atom list (rigid translation/rotation of the
 * affected atom only), keeps it in a local transaction (preview → apply →
 * undo/redo → abandon) and rebuilds the ONE bounded model from it. The
 * geometry store keeps the canonical original coordinates. A-47 adds an
 * explicit save step for the displayed COMMITTED edit: it posts the XYZ plus
 * the transaction list to the existing structure-asset endpoint through the
 * injected client request seam, keeps the local transaction on failure, and
 * records only the returned asset id/path (never a second geometry store).
 * Structure/task switches, playback (IRC / vibration animation) and dispose
 * clear the transaction and any in-flight save result.
 *
 * structureRef contract (frozen v0, see README.md):
 *   { kind: "job-file" | "job-remote-file", jobId, path, frameIndex? }
 *   { kind: "inline-xyz", text, frameIndex? }
 *   (a JSON string of the above is tolerated as a bridge for the UI-00 draft
 *    `structureRef?: string` shape)
 * Data access is delegated to the A-00 adapter (js/visualization/view_source.js)
 * and arrives as an `acp.view.v1` envelope normalized by
 * js/visualization/view_model.js — this module performs no direct fetch and
 * no backend-shape parsing. The adapter preserves the legacy endpoints:
 *   job-file        GET /api/v1/jobs/{jobId}/files/{path}            (raw text)
 *   job-remote-file GET /api/v1/jobs/{jobId}/remote-files/{path}/preview?mode=structure
 *                   (JSON envelope {content: string})
 * `?view=fixture` (or localStorage acp.view.source=fixture) drives the same
 * viewer from deterministic fixtures and renders a visible fixture badge.
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
  var VIEW_STORAGE_KEY = "acp-next-viewer-view-v1";
  var VIEW_STORAGE_LIMIT = 24;
  // Mirrors legacy STYLE_PRESETS (compat/shared-state.js); order is the UI order.
  var STYLE_ORDER = ["ball-stick", "stick", "spacefill", "wireframe", "publication"];
  var STYLE_PRESETS = {
    "ball-stick": { label: "球棍", style: { stick: { radius: 0.15 }, sphere: { scale: 0.28 } } },
    "stick": { label: "棍状", style: { stick: { radius: 0.15 } } },
    "spacefill": { label: "空间填充", style: { sphere: { scale: 1.0 } } },
    "wireframe": { label: "线框", style: { line: {} } },
    "publication": { label: "出版", style: { stick: { radius: 0.12 }, sphere: { scale: 0.22 } } }
  };
  // Legacy overlay second model: cyan carbons + translucency (structure_viewer.js).
  var OVERLAY_STYLE_B = {
    stick: { radius: 0.14, colorscheme: "cyanCarbon", opacity: 0.85 },
    sphere: { scale: 0.24, colorscheme: "cyanCarbon", opacity: 0.55 }
  };

  /* ── shared helpers (view_model.js / view_source.js; no second parser) ── */

  function requireModel() {
    var model = window.ACPView && window.ACPView.model;
    if (!model || typeof model.normalize !== "function") {
      throw new Error("ACPView.model 未加载（检查 view_model.js 脚本顺序）");
    }
    return model;
  }

  function requireSource() {
    var source = window.ACPView && window.ACPView.source;
    if (!source || typeof source.create !== "function") {
      throw new Error("ACPView.source 未加载（检查 view_source.js 脚本顺序）");
    }
    return source;
  }

  function parseMultiFrameXyz(text) {
    return requireModel().parseXyzFrames(text);
  }

  function atomsToXyz(atoms) {
    return requireModel().atomsToXyz(atoms);
  }

  /** Stable change-detection key for a structureRef (order-insensitive). */
  function refKey(ref) {
    return requireModel().structureRefKey(ref);
  }

  /** Normalize a structureRef into a concrete request, or an invalid marker. */
  function normalizeRef(ref) {
    return requireModel().normalizeStructureRef(ref);
  }

  function resolveApiBase() {
    if (typeof window !== "undefined" && window.API_BASE) return String(window.API_BASE);
    return DEFAULT_API_BASE;
  }

  function buildUrl(req) {
    return requireSource().buildStructureUrl(req, resolveApiBase());
  }

  function styleNameOf(value) {
    return STYLE_PRESETS[value] ? value : "ball-stick";
  }

  function validView(view) {
    if (!Array.isArray(view) || view.length !== 8) return false;
    for (var i = 0; i < view.length; i += 1) {
      if (typeof view[i] !== "number" || !isFinite(view[i])) return false;
    }
    return true;
  }

  function readViewPrefs(entryKey) {
    if (!entryKey) return null;
    try {
      var raw = window.localStorage && window.localStorage.getItem(VIEW_STORAGE_KEY);
      if (!raw) return null;
      var map = JSON.parse(raw);
      var prefs = map && map[entryKey];
      if (!prefs || typeof prefs !== "object") return null;
      return {
        style: styleNameOf(prefs.style),
        view: validView(prefs.view) ? prefs.view.slice() : null
      };
    } catch (e) {
      return null;
    }
  }

  function writeViewPrefs(entryKey, style, view) {
    if (!entryKey) return;
    try {
      var raw = window.localStorage && window.localStorage.getItem(VIEW_STORAGE_KEY);
      var map = raw ? JSON.parse(raw) : {};
      if (!map || typeof map !== "object" || Array.isArray(map)) map = {};
      map[entryKey] = { style: styleNameOf(style) };
      if (validView(view)) map[entryKey].view = view.slice();
      var keys = Object.keys(map);
      if (keys.length > VIEW_STORAGE_LIMIT) {
        keys.slice(0, keys.length - VIEW_STORAGE_LIMIT).forEach(function (key) { delete map[key]; });
      }
      window.localStorage.setItem(VIEW_STORAGE_KEY, JSON.stringify(map));
    } catch (e) { /* storage unavailable: presentation prefs are best-effort */ }
  }

  /* ── A-45 measurement: pure geometry (no DOM, no scene, no store) ─────── */

  var MEASUREMENT_ARITY = { distance: 2, angle: 3, dihedral: 4 };
  var MEASUREMENT_TYPES = ["distance", "angle", "dihedral"];
  var MEASUREMENT_LABELS = { distance: "距离", angle: "角度", dihedral: "二面角" };
  var MEASUREMENT_UNITS = { distance: "Å", angle: "°", dihedral: "°" };

  function isFiniteNumber(value) {
    return typeof value === "number" && isFinite(value);
  }

  function sub3(a, b) { return { x: a.x - b.x, y: a.y - b.y, z: a.z - b.z }; }
  function dot3(a, b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
  function len3(a) { return Math.sqrt(dot3(a, a)); }
  function cross3(a, b) {
    return {
      x: a.y * b.z - a.z * b.y,
      y: a.z * b.x - a.x * b.z,
      z: a.x * b.y - a.y * b.x
    };
  }

  /** Normalize a torsion to (-180, 180]; -180 is reported as +180. */
  function normalizeDihedral(deg) {
    var d = Number(deg);
    if (!isFinite(d)) return null;
    d = d % 360;
    if (d > 180) d -= 360;
    else if (d <= -180) d += 360;
    return d;
  }

  /** Resolve one atom position from an atom list; null when missing/non-finite. */
  function measurementPoint(atoms, atomId) {
    if (!Array.isArray(atoms)) return null;
    var index = Number(atomId);
    if (!isFinite(index) || index !== Math.floor(index) || index < 0 || index >= atoms.length) return null;
    var atom = atoms[index];
    if (!atom || typeof atom !== "object") return null;
    var x = Number(atom.x), y = Number(atom.y), z = Number(atom.z);
    if (!isFinite(x) || !isFinite(y) || !isFinite(z)) return null;
    return { x: x, y: y, z: z };
  }

  function measurementDistance(p0, p1) {
    if (!p0 || !p1) return null;
    var dx = p0.x - p1.x, dy = p0.y - p1.y, dz = p0.z - p1.z;
    var value = Math.sqrt(dx * dx + dy * dy + dz * dz);
    return isFiniteNumber(value) ? value : null;
  }

  /** Bond angle at p1 in degrees (0..180); null on a degenerate centre. */
  function measurementAngle(p0, p1, p2) {
    if (!p0 || !p1 || !p2) return null;
    var v1 = sub3(p0, p1);
    var v2 = sub3(p2, p1);
    var denom = len3(v1) * len3(v2);
    if (!denom) return null;
    var cosValue = dot3(v1, v2) / denom;
    if (!isFiniteNumber(cosValue)) return null;
    var value = Math.acos(Math.max(-1, Math.min(1, cosValue))) * 180 / Math.PI;
    return isFiniteNumber(value) ? value : null;
  }

  /**
   * IUPAC signed torsion A-B-C-D in (-180, 180]. Uses the same projection /
   * atan2 convention pinned by geometry_store.js and the FE-10 golden suite:
   * atan2((b1u × v)·w, v·w), where v/w are the A- and D-side projections onto
   * the plane perpendicular to the central B→C bond. Mirror-image arrangements
   * therefore yield opposite signs while cis = 0° and trans = ±180° (normalized
   * to +180°).
   */
  function measurementDihedral(p0, p1, p2, p3) {
    if (!p0 || !p1 || !p2 || !p3) return null;
    var b0 = sub3(p0, p1);
    var b1 = sub3(p2, p1);
    var b2 = sub3(p3, p2);
    var b1len = len3(b1);
    if (!b1len) return null;
    var b1u = { x: b1.x / b1len, y: b1.y / b1len, z: b1.z / b1len };
    var proj0 = dot3(b0, b1u);
    var proj2 = dot3(b2, b1u);
    var v = { x: b0.x - proj0 * b1u.x, y: b0.y - proj0 * b1u.y, z: b0.z - proj0 * b1u.z };
    var w = { x: b2.x - proj2 * b1u.x, y: b2.y - proj2 * b1u.y, z: b2.z - proj2 * b1u.z };
    if (!len3(v) || !len3(w)) return null;
    var raw = Math.atan2(dot3(cross3(b1u, v), w), dot3(v, w)) * 180 / Math.PI;
    if (!isFiniteNumber(raw)) return null;
    return normalizeDihedral(raw);
  }

  /** Pure measurement value: Å / degrees / signed degrees, or null. */
  function measurementValue(type, atoms, atomIds) {
    var arity = MEASUREMENT_ARITY[type];
    if (arity === undefined || !Array.isArray(atomIds) || atomIds.length !== arity) return null;
    var pts = [];
    for (var i = 0; i < arity; i += 1) {
      var point = measurementPoint(atoms, atomIds[i]);
      if (!point) return null;
      pts.push(point);
    }
    if (type === "distance") return measurementDistance(pts[0], pts[1]);
    if (type === "angle") return measurementAngle(pts[0], pts[1], pts[2]);
    return measurementDihedral(pts[0], pts[1], pts[2], pts[3]);
  }

  function formatMeasurementValue(type, value) {
    if (!isFiniteNumber(value)) return "--";
    var unit = MEASUREMENT_UNITS[type] || "";
    return type === "distance"
      ? value.toFixed(3) + " " + unit
      : value.toFixed(1) + unit;
  }

  function measurementAtomLabel(atoms, atomId) {
    var index = Number(atomId);
    var symbol = "?";
    if (Array.isArray(atoms) && index >= 0 && index < atoms.length &&
        atoms[index] && atoms[index].elem) {
      symbol = String(atoms[index].elem);
    }
    return symbol + (isFinite(index) ? String(index + 1) : "");
  }

  /* ── A-46 local internal-coordinate edit: pure geometry + validation ──── */

  // Edit targets follow the frozen legacy editor contract: bond length
  // 0.4–5.0 Å, bond angle 1–179°, dihedral −180–180° (normalized to (−180,
  // 180]). All edit math below is PURE: no DOM, no scene, no store, no fetch.
  var EDIT_RANGES = {
    distance: { min: 0.4, max: 5.0, unit: "Å", step: 0.01, text: "0.4–5.0 Å" },
    angle: { min: 1, max: 179, unit: "°", step: 1, text: "1–179°" },
    dihedral: { min: -180, max: 180, unit: "°", step: 1, text: "-180–180°" }
  };
  var EDIT_TYPES = ["distance", "angle", "dihedral"];
  var EDIT_EPS = 1e-9;

  /** Human-readable allowed range for one edit type. */
  function editRangeText(type) {
    var range = EDIT_RANGES[type];
    if (!range) return "";
    return range.text || (range.min + "–" + range.max + " " + range.unit);
  }

  /**
   * Validate a raw target value for one measurement type. Numbers and numeric
   * strings are accepted; blank input, NaN/Infinity and out-of-range values
   * are rejected with an explicit reason + local message.
   *
   * @param {string} type - "distance" | "angle" | "dihedral"
   * @param {*} raw - raw input value (string, number, null, undefined)
   * @returns {{ok: boolean, value?: number, reason?: string, message?: string}}
   */
  function validateEditTarget(type, raw) {
    var range = EDIT_RANGES[type];
    if (!range) {
      return { ok: false, reason: "invalid_type", message: "不支持的内坐标类型" };
    }
    if (raw === null || raw === undefined) {
      return { ok: false, reason: "missing", message: "请输入目标值" };
    }
    var text = typeof raw === "string" ? raw.trim() : raw;
    if (text === "") {
      return { ok: false, reason: "missing", message: "请输入目标值" };
    }
    var value = typeof text === "number" ? text : Number(text);
    if (!isFiniteNumber(value)) {
      return { ok: false, reason: "non_finite", message: "目标值必须是有限数值" };
    }
    if (value < range.min || value > range.max) {
      return {
        ok: false,
        reason: "out_of_range",
        message: "目标值超出允许范围（" + (MEASUREMENT_LABELS[type] || type) + " " +
          editRangeText(type) + "）"
      };
    }
    return { ok: true, value: value };
  }

  /** Deep-copy an atom list (elem + numeric x/y/z only; never aliased). */
  function copyAtoms(atoms) {
    if (!Array.isArray(atoms)) return [];
    return atoms.map(function (atom) {
      var source = atom && typeof atom === "object" ? atom : {};
      return {
        elem: source.elem,
        x: Number(source.x),
        y: Number(source.y),
        z: Number(source.z)
      };
    });
  }

  function editAtomIndex(value, atomCount) {
    var index = Number(value);
    if (!isFinite(index) || index !== Math.floor(index) || index < 0 || index >= atomCount) return null;
    return index;
  }

  function unitVector3(vector) {
    var length = len3(vector);
    if (!isFinite(length) || length < EDIT_EPS) return null;
    return { x: vector.x / length, y: vector.y / length, z: vector.z / length };
  }

  /** Rodrigues rotation of `vector` about the UNIT `axis` by `degrees`. */
  function rotateVector3(vector, axis, degrees) {
    var theta = degrees * Math.PI / 180;
    var cosT = Math.cos(theta);
    var sinT = Math.sin(theta);
    var cross = cross3(axis, vector);
    var dot = dot3(axis, vector);
    return {
      x: vector.x * cosT + cross.x * sinT + axis.x * dot * (1 - cosT),
      y: vector.y * cosT + cross.y * sinT + axis.y * dot * (1 - cosT),
      z: vector.z * cosT + cross.z * sinT + axis.z * dot * (1 - cosT)
    };
  }

  /** True when a computed value hits the requested target (dihedral modulo 360). */
  function editValueMatches(type, value, target) {
    if (!isFiniteNumber(value)) return false;
    if (type === "dihedral") {
      var diff = normalizeDihedral(value - target);
      return diff !== null && Math.abs(diff) <= 1e-6;
    }
    return Math.abs(value - target) <= 1e-6;
  }

  /**
   * Compute the local displaced geometry for one measured target. PURE: the
   * input atom list is never mutated and a new list is returned where ONLY the
   * documented atom moved (everything else keeps byte-identical coordinates):
   *   distance — the second atom rigidly translates along the A→B axis by
   *              target − current length (unmoved side is fixed);
   *   angle    — the third atom rigidly rotates about the vertex (second atom)
   *              normal to the A-B-C plane by target − current angle, so both
   *              bond lengths and the whole A-B side stay exact;
   *   dihedral — the fourth atom rigidly rotates about the central B→C axis by
   *              the SHORTEST normalized delta (target − current torsion).
   * Because only one atom is displaced and the stored frame is never touched,
   * dropping the result restores the original geometry exactly (undo). Targets
   * that cannot be represented (zero bond, collinear angle, missing atoms,
   * duplicate ids, non-finite/out-of-range values) are rejected before any
   * geometry is produced.
   *
   * @param {string} type - "distance" | "angle" | "dihedral"
   * @param {Array<Object>} atoms - [{elem,x,y,z}] current displayed geometry
   * @param {Array<number>} atomIds - 2 / 3 / 4 0-based atom ids
   * @param {number} target - requested value (Å / °)
   * @returns {{ok: boolean, atoms?: Array<Object>, movedAtomIds?: number[],
   *            beforeValue?: number, afterValue?: number, delta?: number,
   *            reason?: string, message?: string}}
   */
  function displaceGeometry(type, atoms, atomIds, target) {
    var arity = MEASUREMENT_ARITY[type];
    if (arity === undefined) {
      return { ok: false, reason: "invalid_type", message: "不支持的内坐标类型" };
    }
    if (!Array.isArray(atoms) || !atoms.length) {
      return { ok: false, reason: "no_atoms", message: "暂无结构，先加载结构再编辑" };
    }
    if (!Array.isArray(atomIds) || atomIds.length !== arity) {
      return { ok: false, reason: "invalid_arity", message: "测量原子数与类型不匹配，无法编辑" };
    }
    var ids = [];
    for (var i = 0; i < arity; i += 1) {
      var id = editAtomIndex(atomIds[i], atoms.length);
      if (id === null) {
        return { ok: false, reason: "invalid_atom", message: "测量引用的原子已不存在，请重新测量" };
      }
      if (ids.indexOf(id) !== -1) {
        return { ok: false, reason: "duplicate_atom", message: "测量原子重复，无法定义内坐标" };
      }
      ids.push(id);
    }
    var check = validateEditTarget(type, target);
    if (!check.ok) return { ok: false, reason: check.reason, message: check.message };
    var points = [];
    for (var p = 0; p < arity; p += 1) {
      var point = measurementPoint(atoms, ids[p]);
      if (!point) {
        return { ok: false, reason: "degenerate", message: "几何退化或坐标缺失，无法编辑" };
      }
      points.push(point);
    }
    var current = measurementValue(type, atoms, ids);
    if (current === null) {
      return { ok: false, reason: "degenerate", message: "几何退化或坐标缺失，无法编辑" };
    }
    var out = copyAtoms(atoms);
    var movedIds = [];
    var delta = 0;
    if (type === "distance") {
      var length = measurementDistance(points[0], points[1]);
      var axis = unitVector3(sub3(points[1], points[0]));
      if (length === null || !axis) {
        return { ok: false, reason: "degenerate", message: "几何退化（零键长），无法修改距离" };
      }
      delta = check.value - length;
      out[ids[1]] = {
        elem: out[ids[1]].elem,
        x: points[1].x + axis.x * delta,
        y: points[1].y + axis.y * delta,
        z: points[1].z + axis.z * delta
      };
      movedIds.push(ids[1]);
    } else if (type === "angle") {
      var vertex = points[1];
      var armA = unitVector3(sub3(points[0], vertex));
      var armC = unitVector3(sub3(points[2], vertex));
      if (!armA || !armC) {
        return { ok: false, reason: "degenerate", message: "几何退化（键长为零），无法修改角度" };
      }
      var normal = unitVector3(cross3(armA, armC));
      if (!normal) {
        return { ok: false, reason: "degenerate", message: "几何退化（三点共线），无法定义旋转平面" };
      }
      delta = check.value - current;
      var radius = measurementDistance(vertex, points[2]);
      var rotatedArm = rotateVector3(armC, normal, delta);
      out[ids[2]] = {
        elem: out[ids[2]].elem,
        x: vertex.x + rotatedArm.x * radius,
        y: vertex.y + rotatedArm.y * radius,
        z: vertex.z + rotatedArm.z * radius
      };
      movedIds.push(ids[2]);
    } else {
      var central = unitVector3(sub3(points[2], points[1]));
      if (!central) {
        return { ok: false, reason: "degenerate", message: "几何退化（中心键长为零），无法修改二面角" };
      }
      var rotation = normalizeDihedral(check.value - current);
      if (rotation === null) {
        return { ok: false, reason: "degenerate", message: "几何退化，无法确定二面角旋转量" };
      }
      delta = rotation;
      var lever = sub3(points[3], points[1]);
      var rotatedLever = rotateVector3(lever, central, rotation);
      out[ids[3]] = {
        elem: out[ids[3]].elem,
        x: points[1].x + rotatedLever.x,
        y: points[1].y + rotatedLever.y,
        z: points[1].z + rotatedLever.z
      };
      movedIds.push(ids[3]);
    }
    var afterValue = measurementValue(type, out, ids);
    if (!editValueMatches(type, afterValue, check.value)) {
      return { ok: false, reason: "not_converged", message: "位移计算未收敛，编辑未应用" };
    }
    return {
      ok: true,
      atoms: out,
      movedAtomIds: movedIds,
      beforeValue: current,
      afterValue: afterValue,
      delta: delta
    };
  }

  /** Input-field formatting: distances 3 decimals, angles 1 decimal. */
  function formatEditInputValue(type, value) {
    if (!isFiniteNumber(value)) return "";
    return type === "distance" ? value.toFixed(3) : value.toFixed(1);
  }

  /* ── A-47 structure-asset save: pure payload / naming (no network) ────── */

  // The only asset write endpoint inspected in src/acp/api/v1_routes.py is
  // the global (non-job-scoped) POST asset route. Parent job/entry ids are
  // metadata fields of this body; no job-scoped asset write route exists.
  var STRUCTURE_ASSETS_PATH = "/structure-assets";
  var ASSET_NAME_MAX = 80;
  var ASSET_NOTE_MAX = 200;
  var EDIT_OPERATION_TYPES = {
    distance: "bond_length",
    angle: "bond_angle",
    dihedral: "dihedral"
  };

  /**
   * Sanitize a user-visible asset name for the backend upload filename.
   * Path separators / control characters / Windows-reserved punctuation are
   * folded to spaces; the result is never empty and never starts with ".".
   */
  function sanitizeAssetName(raw, fallback) {
    var text = String(raw == null ? "" : raw)
      .replace(/[\u0000-\u001f\u007f\/\\:*?"<>|]/g, " ")
      .replace(/\s+/g, " ")
      .trim();
    text = text.replace(/^\.+/, "").trim();
    if (!text) text = String(fallback == null ? "" : fallback).trim();
    if (!text) text = "edited-structure";
    return text.slice(0, ASSET_NAME_MAX);
  }

  /** Build the saved XYZ text from the displayed atoms; empty = invalid. */
  function buildAssetXyz(atoms, comment) {
    if (!Array.isArray(atoms) || !atoms.length) return "";
    var commentLine = String(comment == null ? "" : comment).replace(/[\r\n]+/g, " ").trim();
    var text = String(atoms.length) + "\n" + commentLine + "\n";
    for (var i = 0; i < atoms.length; i += 1) {
      var atom = atoms[i];
      if (!atom || typeof atom !== "object") return "";
      var elem = typeof atom.elem === "string" ? atom.elem.trim() : "";
      var x = Number(atom.x), y = Number(atom.y), z = Number(atom.z);
      if (!elem || !isFinite(x) || !isFinite(y) || !isFinite(z)) return "";
      text += elem + " " + x.toFixed(6) + " " + y.toFixed(6) + " " + z.toFixed(6) + "\n";
    }
    return text;
  }

  /** Legacy-editor-compatible transaction record for edit_operations. */
  function assetOperationFromTransaction(transaction) {
    if (!transaction || typeof transaction !== "object") return null;
    var type = EDIT_OPERATION_TYPES[transaction.type];
    if (!type) return null;
    if (!Array.isArray(transaction.atomIds) || !transaction.atomIds.length) return null;
    if (!Array.isArray(transaction.beforeAtoms) || !transaction.beforeAtoms.length) return null;
    if (!Array.isArray(transaction.afterAtoms) ||
        transaction.afterAtoms.length !== transaction.beforeAtoms.length) return null;
    function coordinatesOf(atoms) {
      var out = [];
      for (var i = 0; i < atoms.length; i += 1) {
        var atom = atoms[i] || {};
        var x = Number(atom.x), y = Number(atom.y), z = Number(atom.z);
        if (!isFinite(x) || !isFinite(y) || !isFinite(z)) return null;
        out.push([x, y, z]);
      }
      return out;
    }
    var before = coordinatesOf(transaction.beforeAtoms);
    var after = coordinatesOf(transaction.afterAtoms);
    if (!before || !after) return null;
    return {
      type: type,
      atom_ids: transaction.atomIds.slice(),
      before: before,
      after: after,
      moved_atom_ids: Array.isArray(transaction.movedAtomIds)
        ? transaction.movedAtomIds.slice() : [],
      collision_warnings: []
    };
  }

  /**
   * Pure request-body builder for the structure-asset endpoint. Requires at
   * least one COMMITTED A-46 transaction: saving the untouched original is
   * intentionally not offered. `atoms` is the displayed (committed) geometry;
   * the immutable frame is never read here.
   */
  function buildStructureAssetPayload(input) {
    input = input || {};
    var atoms = input.atoms;
    var transactions = input.transactions;
    if (!Array.isArray(atoms) || !atoms.length) {
      return { ok: false, reason: "no_structure", message: "暂无结构，无法保存资产" };
    }
    if (!Array.isArray(transactions) || !transactions.length) {
      return {
        ok: false,
        reason: "no_committed_edit",
        message: "应用一次本地编辑后才能保存为资产（不提供保存原始结构）"
      };
    }
    var operations = [];
    for (var i = 0; i < transactions.length; i += 1) {
      var operation = assetOperationFromTransaction(transactions[i]);
      if (!operation) {
        return {
          ok: false,
          reason: "invalid_operation",
          message: "本地编辑事务不完整，无法保存资产"
        };
      }
      operations.push(operation);
    }
    var parentJobId = input.parentJobId ? String(input.parentJobId) : "";
    var parentEntryId = input.parentEntryId ? String(input.parentEntryId) : "";
    var sourceComment = input.sourceComment
      ? String(input.sourceComment).replace(/[\r\n]+/g, " ").trim().slice(0, 240) : "";
    var name = sanitizeAssetName(input.name, input.fallbackName || "edited-structure");
    var note = String(input.note == null ? "" : input.note).trim().slice(0, ASSET_NOTE_MAX);
    var comment = "ACP molecule-viewer edit · ops=" + operations.length +
      (parentJobId ? " · job=" + parentJobId : "") +
      (parentEntryId ? " · entry=" + parentEntryId : "") +
      (sourceComment ? " · " + sourceComment : "");
    var xyz = buildAssetXyz(atoms, comment);
    if (!xyz) {
      return { ok: false, reason: "invalid_geometry", message: "几何数据不完整，无法保存资产" };
    }
    var provenance = {
      comment: comment,
      created_at: typeof input.createdAt === "string" && input.createdAt
        ? input.createdAt : new Date().toISOString(),
      op_count: operations.length,
      source: "molecule-viewer"
    };
    if (note) provenance.note = note;
    var body = {
      name: name,
      xyz_text: xyz,
      edit_operations: operations,
      provenance: provenance
    };
    if (parentJobId) body.parent_job_id = parentJobId;
    if (parentEntryId) body.parent_entry_id = parentEntryId;
    if (input.projectId) body.project_id = String(input.projectId);
    return { ok: true, body: body, name: name };
  }

  /** Validate the 201 body; only id/ref/metadata are kept by the caller. */
  function assetReceiptFromResponse(body) {
    if (!body || typeof body !== "object" || Array.isArray(body)) {
      return { ok: false, message: "保存响应格式不正确" };
    }
    var errors = Array.isArray(body.errors)
      ? body.errors.filter(function (item) { return !!item; }).join("; ") : "";
    if (body.ok === false) {
      return { ok: false, message: errors || "后端未接受该资产" };
    }
    var assetId = typeof body.asset_id === "string" ? body.asset_id.trim() : "";
    if (!assetId) return { ok: false, message: "保存响应缺少 asset_id" };
    var atomCount = Number(body.atom_count);
    return {
      ok: true,
      receipt: {
        assetId: assetId,
        assetPath: typeof body.asset_path === "string" ? body.asset_path : "",
        name: typeof body.name === "string" ? body.name : "",
        atomCount: isFiniteNumber(atomCount) ? atomCount : 0,
        formula: typeof body.formula === "string" ? body.formula : ""
      }
    };
  }

  function assetErrorMessage(error) {
    if (error && typeof error.message === "string" && error.message) return error.message;
    var text = String(error == null ? "" : error);
    return text || "未知错误";
  }

  /* ── module instance ──────────────────────────────────────────────────── */

  function createInstance(deps) {
    deps = deps || {};
    var context = deps.context || null;
    var source = null;

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
    var fixtureBadge = null;
    var warnEl = null;
    var catalogToggle = null;
    var catalogEl = null;
    var catalogBody = null;
    var catalogStatusEl = null;
    var styleSelect = null;
    var resetViewBtn = null;
    var overlayBar = null;
    var overlayStatusEl = null;
    var overlayClearBtn = null;
    var overlayModelToken = null;
    var ircBar = null;
    var ircStatusEl = null;
    var ircStopBtn = null;
    var ircForwardBtn = null;
    var ircReverseBtn = null;
    var vibrationToggle = null;
    var vibrationDrawer = null;
    var vibrationStatusEl = null;
    var vibrationListEl = null;
    var vibrationArrowBtn = null;
    var vibrationPlayBtn = null;
    var vibrationAnimStatusEl = null;
    var vibrationThermoEl = null;
    var vibrationGeneration = 0;
    var currentFrame = null;
    // A-45: read-only measurement state. Only atom ids + the computed value are
    // kept here — coordinates always come from the viewer's currentFrame, so
    // there is no second geometry cache and stored geometry is never mutated.
    var measurementState = {
      open: false,
      type: "distance",
      picking: false,
      picks: [],
      items: [],
      notice: "",
      noticeError: false
    };
    var measurementSeq = 0;
    var measurementGeometryStale = false;
    var measurementLabelTokens = [];
    var measurementShapeTokens = [];
    var pickShapeTokens = [];
    var measurementToggle = null;
    var measurementDrawer = null;
    var measurementStatusEl = null;
    var measurementListEl = null;
    var measurementPickBtn = null;
    var measurementClearBtn = null;
    var measurementTypeEls = null;
    // A-46: local internal-coordinate edit transaction. The immutable
    // currentFrame stays the source of truth; pending/transactions hold
    // displaced atom copies only (no store / backend writes, no second scene).
    var editState = {
      pending: null,
      transactions: [],
      redo: [],
      drafts: Object.create(null),
      notice: "",
      noticeError: false,
      busy: false
    };
    var editSeq = 0;
    var editStatusEl = null;
    var editBarEl = null;
    var editSummaryEl = null;
    var editCommitBtn = null;
    var editCancelBtn = null;
    var editUndoBtn = null;
    var editRedoBtn = null;
    var editAbandonBtn = null;
    // A-47 asset-save block: only receipt ids/refs are kept (no geometry).
    var assetState = {
      name: "",
      nameTouched: false,
      note: "",
      confirming: false,
      saving: false,
      error: "",
      receipt: null
    };
    var assetSeq = 0;
    var assetAbort = null;
    var assetSectionEl = null;
    var assetStatusEl = null;
    var assetNameEl = null;
    var assetNoteEl = null;
    var assetSaveBtn = null;
    var assetCancelBtn = null;
    var assetReceiptEl = null;
    var currentProjectId = null;
    var vibrationAnimation = {
      playing: false,
      rafId: null,
      phase: 0,
      lastTs: null,
      savedView: null,
      showArrows: true
    };
    var vibrationState = {
      loading: false,
      data: null,
      selectedModeIndex: null,
      error: null
    };
    var ircTimer = null;
    var ircGeneration = 0;
    var pendingCatalogAction = null;
    var ircState = {
      playing: false,
      loading: false,
      direction: null,
      frames: [],
      index: 0
    };
    var overlayState = {
      active: false,
      loading: false,
      entryA: null,
      entryB: null,
      data: null,
      error: null
    };
    var modelToken = null;
    var currentStyle = "ball-stick";
    var cameraSaveTimer = null;
    var hasRestoredView = false;
    var scene = null;
    var framingState = null;
    var unsubscribeContext = null;
    var generation = 0;
    var currentRef = null;
    var currentRefKey = null;
    var firstSnapshot = true;
    var currentTaskRef = null;
    var currentTaskKey = null;
    var catalogGeneration = 0;
    var catalogLoadedKey = null;
    var catalogLoadingKey = null;
    var lastCatalogView = null;
    var selectedEntryId = null;
    var storeOwned = false;
    var ready = false;

    /* ------------------------- DOM (self-rendered) ----------------------- */

    function entryKeyFor(req) {
      if (!req) return null;
      var normalized = null;
      try { normalized = requireModel().normalizeStructureRef(req); } catch (e) { normalized = null; }
      if (normalized && normalized.kind !== "invalid") {
        return normalized.storeEntryId || normalized.entryId || requireModel().structureRefKey(normalized);
      }
      return requireModel().structureRefKey(req);
    }

    function applyModelStyle(name) {
      currentStyle = styleNameOf(name);
      if (modelToken && typeof modelToken.setStyle === "function") {
        try { modelToken.setStyle({}, STYLE_PRESETS[currentStyle].style); } catch (e) { /* style is presentational */ }
      }
      if (styleSelect) styleSelect.value = currentStyle;
    }

    function persistView() {
      if (!scene || !currentRef) return;
      var key = entryKeyFor(currentRef);
      if (!key) return;
      var view = null;
      try { view = scene.getView(); } catch (e) { view = null; }
      writeViewPrefs(key, currentStyle, validView(view) ? view : null);
    }

    function schedulePersistView() {
      if (cameraSaveTimer) clearTimeout(cameraSaveTimer);
      cameraSaveTimer = setTimeout(function () {
        cameraSaveTimer = null;
        persistView();
      }, 120);
    }

    function onStyleChange() {
      if (!styleSelect) return;
      applyModelStyle(styleSelect.value);
      if (scene) {
        try { scene.render(); } catch (e) { /* render is best-effort */ }
      }
      persistView();
    }

    function onResetView() {
      if (!scene || !currentRef) return;
      hasRestoredView = false;
      writeViewPrefs(entryKeyFor(currentRef), currentStyle, null);
      scheduleFraming();
      try { scene.render(); } catch (e) { /* render is best-effort */ }
    }

    function updateNarrowMode() {
      if (!root) return;
      var width = 0;
      if (typeof window.innerWidth === "number") width = window.innerWidth;
      else if (host && typeof host.clientWidth === "number") width = host.clientWidth;
      var narrow = width > 0 && width <= 720;
      root.setAttribute("data-acp-viewer-narrow", narrow ? "true" : "false");
      root.setAttribute("data-acp-viewer-layout", narrow ? "single" : "split");
    }

    function onWindowResize() {
      updateNarrowMode();
    }

    function currentEntryId() {
      if (selectedEntryId) return selectedEntryId;
      if (currentRef && currentRef.kind === "structure-entry") return currentRef.entryId;
      var req = null;
      try { req = normalizeRef(currentRef); } catch (e) { req = null; }
      return req && req.kind === "structure-entry" ? req.entryId : null;
    }

    function overlayMeasurementsBlocked() {
      return !!(overlayState.active && overlayState.data && !overlayState.data.ok);
    }

    function clearOverlayModel() {
      if (overlayModelToken && scene) {
        try { scene.removeModel(overlayModelToken); } catch (e) { /* already gone */ }
      }
      overlayModelToken = null;
      if (scene && modelToken) applyModelStyle(currentStyle);
    }

    function clearOverlay(render) {
      clearOverlayModel();
      overlayState.active = false;
      overlayState.loading = false;
      overlayState.entryA = null;
      overlayState.entryB = null;
      overlayState.data = null;
      overlayState.error = null;
      renderOverlayBar();
      if (render && scene) {
        try { scene.render(); } catch (e) { /* render is best-effort */ }
      }
    }

    function renderOverlayBar() {
      if (!root || !overlayBar) return;
      var state = "idle";
      var message = "";
      if (overlayState.loading) {
        state = "loading";
        message = "叠合计算中…";
      } else if (overlayState.error) {
        state = "error";
        message = overlayState.error;
      } else if (overlayState.active && overlayState.data) {
        if (!overlayState.data.ok) {
          state = "blocked";
          message = "映射未证实 · 测量已禁用";
        } else {
          state = "ready";
          message = "RMSD " + (overlayState.data.rmsd != null ? overlayState.data.rmsd.toFixed(3) : "--") +
            " Å · 映射 " + (overlayState.data.reason || "unknown") +
            " · " + (overlayState.data.nMapped || 0) + " 原子";
        }
      }
      root.setAttribute("data-acp-overlay-state", state);
      root.setAttribute("data-acp-overlay-measurements", state === "blocked" ? "blocked" : "allowed");
      overlayBar.hidden = state === "idle";
      overlayBar.className = "acp-mod-viewer-overlay-bar" + (state === "blocked" ? " is-blocked" : "") +
        (state === "error" ? " is-error" : "");
      overlayStatusEl.textContent = message;
      overlayClearBtn.hidden = state === "idle" || state === "loading";
      if (state === "idle" && lastCatalogView) renderCatalog(lastCatalogView);
      renderMeasurementPanel();
    }

    function loadOverlayFor(entry) {
      if (!alive || !entry || !entry.id) return;
      var jobId = currentTaskRef && currentTaskRef.jobId;
      if (!jobId && currentRef && currentRef.jobId) jobId = currentRef.jobId;
      var entryA = currentEntryId();
      if (!jobId || !entryA) {
        overlayState.active = false;
        overlayState.loading = false;
        overlayState.error = "请先从目录选择基准结构";
        renderOverlayBar();
        return;
      }
      if (entryA === entry.id) {
        overlayState.error = "基准结构与叠加结构不能相同";
        renderOverlayBar();
        return;
      }
      clearOverlay(false);
      overlayState.loading = true;
      overlayState.entryA = entryA;
      overlayState.entryB = entry.id;
      renderOverlayBar();
      var gen = generation;
      try {
        ensureSource().loadOverlay(jobId, entryA, entry.id).then(function (view) {
          if (!alive || gen !== generation) return;
          if (!view || view.availability === "error" || view.availability === "denied") {
            overlayState.loading = false;
            overlayState.error = warningMessage(view) || "叠合失败";
            renderOverlayBar();
            return;
          }
          overlayState.loading = false;
          overlayState.active = true;
          overlayState.data = view.overlay;
          overlayState.entryA = view.overlay ? view.overlay.entryA : entryA;
          overlayState.entryB = view.overlay ? view.overlay.entryB : entry.id;
          var object = view.objects && view.objects[entry.id];
          clearOverlayModel();
          if (object && typeof object.text === "string" && scene) {
            try {
              overlayModelToken = scene.addModel(object.text, "xyz");
              if (overlayModelToken && typeof overlayModelToken.setStyle === "function") {
                overlayModelToken.setStyle({}, OVERLAY_STYLE_B);
              }
              var max = view.overlay && view.overlay.maxDisplacement;
              if (max && modelToken && typeof modelToken.addStyle === "function" &&
                  typeof max.i === "number") {
                modelToken.addStyle({ index: max.i }, {
                  sphere: { scale: 0.42, color: "yellow", opacity: 0.6 }
                });
              }
            } catch (e) { /* overlay model is best-effort */ }
          }
          if (scene) {
            try { scene.render(); } catch (e) { /* render is best-effort */ }
          }
          renderOverlayBar();
          if (lastCatalogView) renderCatalog(lastCatalogView);
        }).catch(function (error) {
          if (!alive || gen !== generation) return;
          overlayState.loading = false;
          overlayState.error = error && error.message ? error.message : String(error);
          renderOverlayBar();
        });
      } catch (error) {
        overlayState.loading = false;
        overlayState.error = error && error.message ? error.message : String(error);
        renderOverlayBar();
      }
    }

    function currentJobId() {
      if (currentTaskRef && currentTaskRef.jobId) return String(currentTaskRef.jobId);
      if (currentRef && currentRef.jobId) return String(currentRef.jobId);
      return null;
    }

    function ircEntries(direction) {
      var entries = lastCatalogView && Array.isArray(lastCatalogView.entries) ? lastCatalogView.entries : [];
      var wanted = "irc_" + direction;
      return entries.filter(function (entry) {
        var group = String(entry.groupId || "").toLowerCase();
        return group === wanted || group === direction ||
          (group.indexOf("irc") !== -1 && group.indexOf(direction) !== -1);
      }).sort(function (a, b) {
        return Number(a.frameIndex || 0) - Number(b.frameIndex || 0) ||
          String(a.id).localeCompare(String(b.id));
      });
    }

    function frameMs() {
      var value = Number(deps.ircFrameMs);
      if (isFinite(value) && value >= 10 && value <= 5000) return value;
      return 250;
    }

    function setIrcStatus(message) {
      if (ircStatusEl) ircStatusEl.textContent = message || "IRC";
      if (root) {
        root.setAttribute("data-acp-irc-state",
          ircState.loading ? "loading" : (ircState.playing ? "playing" : "idle"));
        if (ircState.direction) root.setAttribute("data-acp-irc-direction", ircState.direction);
        else root.removeAttribute("data-acp-irc-direction");
      }
      renderMeasurementPanel();
    }

    function updateIrcControls() {
      if (ircStopBtn) ircStopBtn.hidden = !(ircState.playing || ircState.loading);
      if (ircForwardBtn) ircForwardBtn.disabled = ircState.loading;
      if (ircReverseBtn) ircReverseBtn.disabled = ircState.loading;
      if (root) root.setAttribute("data-acp-irc-state",
        ircState.loading ? "loading" : (ircState.playing ? "playing" : "idle"));
      renderMeasurementPanel();
    }

    function flushPendingCatalogAction() {
      if (!pendingCatalogAction) return;
      var action = pendingCatalogAction;
      pendingCatalogAction = null;
      try { action(); } catch (e) { /* the action surfaces its own error */ }
    }

    function fetchIrcFrames(entries) {
      var jobId = currentJobId();
      return Promise.all(entries.map(function (entry) {
        return ensureSource().load("structure:" + entry.id, {
          family: "structure",
          structureRef: { kind: "structure-entry", jobId: jobId, entryId: entry.id }
        }).then(function (view) {
          var expected = jobId + ":entry:" + entry.id;
          var objects = view && view.objects ? view.objects : {};
          var object = objects[expected];
          if (!object) {
            var keys = Object.keys(objects);
            if (keys.length) object = objects[keys[0]];
          }
          return object && typeof object.text === "string" ? object.text : null;
        }).catch(function () { return null; });
      })).then(function (texts) {
        return texts.filter(function (text) { return typeof text === "string" && text.trim(); });
      });
    }

    function showIrcFrame(text) {
      try {
        stopVibrationAnimation(false);
        clearVibrationShapes();
        ensureScene();
        removeMeasurementOverlays();
        measurementState.picking = false;
        measurementState.picks = [];
        var view = null;
        try { view = scene.getView(); } catch (e) { view = null; }
        scene.removeAllModels();
        modelToken = scene.addModel(text, "xyz");
        if (!modelToken) {
          stopIrcPlayback();
          setIrcStatus("IRC 帧渲染失败");
          return;
        }
        // The displayed IRC frame is not the stored equilibrium currentFrame,
        // so measurement values would be computed against the wrong geometry.
        measurementGeometryStale = true;
        applyModelStyle(currentStyle);
        if (validView(view)) {
          try { scene.setView(view); } catch (e) { /* keep current camera */ }
        }
        scene.render();
      } catch (error) {
        stopIrcPlayback();
        setIrcStatus(error && error.message ? "IRC 渲染失败: " + error.message : "IRC 渲染失败");
      }
    }

    function stepIrcFrame() {
      if (!ircState.playing) return;
      if (ircState.index >= ircState.frames.length) {
        stopIrcPlayback();
        setIrcStatus("播放结束");
        return;
      }
      var text = ircState.frames[ircState.index];
      ircState.index += 1;
      showIrcFrame(text);
      setIrcStatus((ircState.direction === "reverse" ? "反向" : "正向") + " " +
        ircState.index + " / " + ircState.frames.length);
      updateIrcControls();
    }

    function stopIrcPlayback() {
      ircGeneration += 1;
      if (ircTimer) {
        clearInterval(ircTimer);
        ircTimer = null;
      }
      ircState.playing = false;
      ircState.loading = false;
      ircState.direction = null;
      ircState.frames = [];
      ircState.index = 0;
      updateIrcControls();
    }

    function startIrcPlayback(direction) {
      if (!alive || (direction !== "forward" && direction !== "reverse")) return;
      if (assetState.saving) {
        setIrcStatus("资产保存中，请稍候");
        return;
      }
      var jobId = currentJobId();
      if (!jobId) {
        setIrcStatus("请选择任务");
        return;
      }
      if (!lastCatalogView) {
        pendingCatalogAction = function () { startIrcPlayback(direction); };
        setIrcStatus("加载 IRC 目录…");
        loadCatalogFor(currentTaskRef && currentTaskRef.jobId ? currentTaskRef : { jobId: jobId });
        return;
      }
      var entries = ircEntries(direction);
      if (!entries.length) {
        setIrcStatus("无 IRC " + (direction === "reverse" ? "反向" : "正向") + " 帧");
        return;
      }
      stopIrcPlayback();
      clearOverlay(false);
      resetMeasurements();
      var gen = ircGeneration;
      ircState.loading = true;
      ircState.direction = direction;
      ircState.frames = [];
      ircState.index = 0;
      setIrcStatus("加载 IRC 帧…");
      updateIrcControls();
      fetchIrcFrames(entries).then(function (frames) {
        if (!alive || gen !== ircGeneration) return;
        if (!frames.length) {
          ircState.loading = false;
          setIrcStatus("无可用 IRC 帧");
          updateIrcControls();
          return;
        }
        ircState.frames = frames;
        ircState.loading = false;
        ircState.playing = true;
        setIrcStatus("IRC " + (direction === "reverse" ? "反向" : "正向"));
        stepIrcFrame();
        if (ircState.playing) ircTimer = setInterval(stepIrcFrame, frameMs());
        updateIrcControls();
      }).catch(function (error) {
        if (!alive || gen !== ircGeneration) return;
        ircState.loading = false;
        setIrcStatus(error && error.message ? error.message : "IRC 帧加载失败");
        updateIrcControls();
      });
    }

    function sortVibrationModes(modes) {
      var negatives = [];
      var positives = [];
      (modes || []).forEach(function (mode) {
        var frequency = Number(mode.frequencyCm1);
        if (mode.imaginary || (isFinite(frequency) && frequency < 0)) negatives.push(mode);
        else positives.push(mode);
      });
      negatives.sort(function (a, b) { return Number(a.frequencyCm1) - Number(b.frequencyCm1); });
      positives.sort(function (a, b) { return Number(a.frequencyCm1) - Number(b.frequencyCm1); });
      return negatives.concat(positives);
    }

    function defaultVibrationMode(modes) {
      var sorted = sortVibrationModes(modes);
      return sorted.length ? sorted[0].modeIndex : null;
    }

    function vibrationReasonText(reason) {
      var labels = {
        no_normal_modes: "无振动模式数据",
        geometry_mismatch: "振动几何与结构不一致",
        pending_fetch: "远程振动数据尚未取回",
        historical_unavailable: "历史振动数据不可用"
      };
      return labels[reason] || reason || "振动数据不可用";
    }

    function setVibrationStatus(message, isError) {
      if (!vibrationStatusEl) return;
      vibrationStatusEl.textContent = message || "";
      vibrationStatusEl.hidden = !message;
      vibrationStatusEl.className = "acp-mod-viewer-vibration-status" + (isError ? " is-error" : "");
    }

    function renderThermoSummary() {
      if (!vibrationThermoEl || !root) return;
      clearChildren(vibrationThermoEl);
      var thermo = lastCatalogView && lastCatalogView.thermo;
      var values = thermo ? Object.keys(thermo).filter(function (key) { return thermo[key] != null; }) : [];
      if (!values.length) {
        root.setAttribute("data-acp-thermo-state", "empty");
        var empty = window.document.createElement("p");
        empty.className = "acp-mod-viewer-vibration-thermo-empty";
        empty.textContent = "无热化学数据";
        vibrationThermoEl.appendChild(empty);
        return;
      }
      root.setAttribute("data-acp-thermo-state", "ready");
      var title = window.document.createElement("h4");
      title.className = "acp-mod-viewer-vibration-thermo-title";
      title.textContent = "热化学";
      vibrationThermoEl.appendChild(title);
      var rows = [
        ["总 Gibbs", thermo.totalGibbsKcalMol != null ? Number(thermo.totalGibbsKcalMol).toFixed(4) + " kcal/mol" : null],
        ["总 Gibbs (Ha)", thermo.totalGibbsHartree != null ? Number(thermo.totalGibbsHartree).toFixed(8) : null],
        ["Rank-1 weight", thermo.rank1Weight != null ? Number(thermo.rank1Weight).toFixed(4) : null],
        ["T·S_mix", thermo.tSMixKcalPerMol != null ? Number(thermo.tSMixKcalPerMol).toFixed(4) + " kcal/mol" : null],
        ["Population coverage", thermo.populationCoverage != null ? (Number(thermo.populationCoverage) * 100).toFixed(1) + "%" : null],
        ["温度", thermo.temperatureK != null ? Number(thermo.temperatureK).toFixed(2) + " K" : null]
      ];
      rows.forEach(function (row) {
        if (row[1] == null) return;
        var line = window.document.createElement("div");
        line.className = "acp-mod-viewer-vibration-thermo-row";
        var label = window.document.createElement("span");
        label.textContent = row[0];
        var value = window.document.createElement("span");
        value.textContent = row[1];
        line.appendChild(label);
        line.appendChild(value);
        vibrationThermoEl.appendChild(line);
      });
    }

    function renderVibrationDrawer() {
      if (!root || !vibrationDrawer) return;
      var modes = vibrationState.data && Array.isArray(vibrationState.data.modes)
        ? vibrationState.data.modes : [];
      if (vibrationToggle) {
        vibrationToggle.textContent = "振动" + (modes.length ? " (" + modes.length + ")" : "");
      }
      var state = vibrationState.loading ? "loading" :
        (vibrationState.error ? "error" :
          (modes.length ? "ready" : (vibrationState.data ? "empty" : "idle")));
      root.setAttribute("data-acp-vibration-state", state);
      renderThermoSummary();
      if (vibrationArrowBtn) vibrationArrowBtn.hidden = true;
      if (vibrationPlayBtn) vibrationPlayBtn.hidden = true;
      if (vibrationDrawer.hidden) return;
      clearChildren(vibrationListEl);
      if (vibrationState.loading) {
        setVibrationStatus("振动加载中…", false);
        return;
      }
      if (vibrationState.error) {
        setVibrationStatus(vibrationState.error, true);
        return;
      }
      if (!vibrationState.data) {
        setVibrationStatus("请先从目录选择结构条目", false);
        return;
      }
      if (!modes.length) {
        setVibrationStatus(vibrationReasonText(vibrationState.data.vibration && vibrationState.data.vibration.reason), false);
        return;
      }
      setVibrationStatus("");
      if (vibrationArrowBtn) {
        vibrationArrowBtn.hidden = false;
        vibrationArrowBtn.className = "acp-mod-viewer-vibration-action" +
          (vibrationAnimation.showArrows ? " is-active" : "");
      }
      if (vibrationPlayBtn) vibrationPlayBtn.hidden = false;
      var sorted = sortVibrationModes(modes);
      sorted.forEach(function (mode) {
        var row = window.document.createElement("button");
        row.type = "button";
        row.className = "acp-mod-viewer-vibration-row" +
          (mode.modeIndex === vibrationState.selectedModeIndex ? " is-selected" : "");
        row.setAttribute("data-vibration-mode", String(mode.modeIndex));
        var index = window.document.createElement("span");
        index.className = "acp-mod-viewer-vibration-index";
        index.textContent = "#" + mode.modeIndex;
        var frequency = window.document.createElement("span");
        frequency.className = "acp-mod-viewer-vibration-frequency";
        frequency.textContent = (mode.frequencyCm1 != null ? Number(mode.frequencyCm1).toFixed(2) : "--") + " cm⁻¹";
        row.appendChild(index);
        row.appendChild(frequency);
        if (mode.imaginary) {
          var imaginary = window.document.createElement("span");
          imaginary.className = "acp-mod-viewer-vibration-imaginary";
          imaginary.textContent = "虚频";
          row.appendChild(imaginary);
        }
        if (mode.irIntensity != null) {
          var ir = window.document.createElement("span");
          ir.className = "acp-mod-viewer-vibration-ir";
          ir.textContent = "IR " + Number(mode.irIntensity).toFixed(1);
          row.appendChild(ir);
        }
        row.addEventListener("click", function () { selectVibrationMode(mode.modeIndex); });
        vibrationListEl.appendChild(row);
      });
    }

    function selectVibrationMode(modeIndex) {
      vibrationState.selectedModeIndex = Number(modeIndex);
      renderVibrationDrawer();
      if (!vibrationAnimation.playing) drawVibrationArrows();
    }

    function loadVibrationsForCurrent() {
      var jobId = currentJobId();
      var entryId = currentEntryId();
      if (!jobId || !entryId) {
        vibrationState.loading = false;
        vibrationState.data = null;
        vibrationState.error = null;
        vibrationState.selectedModeIndex = null;
        renderVibrationDrawer();
        return;
      }
      vibrationGeneration += 1;
      var gen = vibrationGeneration;
      vibrationState.loading = true;
      vibrationState.data = null;
      vibrationState.error = null;
      vibrationState.selectedModeIndex = null;
      renderVibrationDrawer();
      try {
        ensureSource().loadVibrations(jobId, entryId).then(function (view) {
          if (!alive || gen !== vibrationGeneration) return;
          vibrationState.loading = false;
          if (!view || view.availability === "error" || view.availability === "denied") {
            vibrationState.data = null;
            vibrationState.error = warningMessage(view) || "振动加载失败";
            renderVibrationDrawer();
            return;
          }
          vibrationState.data = view;
          var modes = Array.isArray(view.modes) ? view.modes : [];
          vibrationState.selectedModeIndex = defaultVibrationMode(modes);
          renderVibrationDrawer();
          if (vibrationState.selectedModeIndex != null) drawVibrationArrows();
        }).catch(function (error) {
          if (!alive || gen !== vibrationGeneration) return;
          vibrationState.loading = false;
          vibrationState.error = error && error.message ? error.message : String(error);
          renderVibrationDrawer();
        });
      } catch (error) {
        vibrationState.loading = false;
        vibrationState.error = error && error.message ? error.message : String(error);
        renderVibrationDrawer();
      }
    }

    function onVibrationToggle() {
      if (!vibrationDrawer) return;
      if (!vibrationDrawer.hidden) {
        vibrationDrawer.hidden = true;
        return;
      }
      vibrationDrawer.hidden = false;
      if (!vibrationState.data && !vibrationState.loading) loadVibrationsForCurrent();
      else renderVibrationDrawer();
    }

    function resetVibration() {
      stopVibrationAnimation(false);
      clearVibrationShapes();
      // currentFrame is shared viewer geometry, not vibration state: it is
      // cleared by clearStructure()/cleanupMount() and replaced by renderFrame().
      vibrationGeneration += 1;
      vibrationState.loading = false;
      vibrationState.data = null;
      vibrationState.error = null;
      vibrationState.selectedModeIndex = null;
      if (vibrationDrawer) vibrationDrawer.hidden = true;
      renderVibrationDrawer();
    }

    function clampVibrationAmplitude(value) {
      var number = Number(value);
      if (!isFinite(number)) return 0.5;
      return Math.min(1.5, Math.max(0.1, number));
    }

    function computeArrowEndpoints(coords, vectors, amplitude) {
      if (!coords || !vectors) return null;
      var amp = clampVibrationAmplitude(amplitude);
      var rows = [];
      for (var i = 0; i < coords.length; i += 1) {
        var c = coords[i];
        var v = vectors[i];
        if (!c || !v || c.length < 3 || v.length < 3) { rows.push(null); continue; }
        var cx = +c[0], cy = +c[1], cz = +c[2];
        var vx = +v[0], vy = +v[1], vz = +v[2];
        var norm = Math.sqrt(vx * vx + vy * vy + vz * vz);
        if (!isFinite(norm) || norm < 1e-6 || !isFinite(cx) || !isFinite(cy) || !isFinite(cz)) {
          rows.push(null);
          continue;
        }
        var k = amp / norm;
        rows.push({ start: [cx, cy, cz], end: [cx + vx * k, cy + vy * k, cz + vz * k] });
      }
      return rows;
    }

    function computeDisplacedCoords(coords, vectors, amplitude, phase) {
      if (!coords || !vectors) return null;
      var amp = clampVibrationAmplitude(amplitude);
      var scale = amp * Math.sin(phase);
      var out = [];
      for (var i = 0; i < coords.length; i += 1) {
        var c = coords[i];
        var v = vectors[i];
        if (!c || c.length < 3) { out.push(null); continue; }
        var cx = +c[0], cy = +c[1], cz = +c[2];
        if (!v || v.length < 3) { out.push([cx, cy, cz]); continue; }
        var vx = +v[0], vy = +v[1], vz = +v[2];
        var norm = Math.sqrt(vx * vx + vy * vy + vz * vz);
        if (!isFinite(norm) || norm < 1e-6) { out.push([cx, cy, cz]); continue; }
        var k = scale / norm;
        out.push([cx + vx * k, cy + vy * k, cz + vz * k]);
      }
      return out;
    }

    function buildVibrationXyz(symbols, coords, comment) {
      if (!symbols || !coords || symbols.length !== coords.length || !symbols.length) return "";
      var text = symbols.length + "\n" + (comment || "vibration frame") + "\n";
      for (var i = 0; i < symbols.length; i += 1) {
        var c = coords[i];
        if (!c || c.length < 3) return "";
        text += symbols[i] + " " + (+c[0]).toFixed(6) + " " + (+c[1]).toFixed(6) + " " + (+c[2]).toFixed(6) + "\n";
      }
      return text;
    }

    function selectedVibrationMode() {
      var modes = vibrationState.data && Array.isArray(vibrationState.data.modes) ? vibrationState.data.modes : [];
      return modes.find(function (mode) { return mode.modeIndex === vibrationState.selectedModeIndex; }) || null;
    }

    function vibrationGeometryContext() {
      if (!currentFrame || !Array.isArray(currentFrame.atoms) || !currentFrame.atoms.length) {
        return { ok: false, hint: "无结构几何" };
      }
      var mode = selectedVibrationMode();
      if (!mode || !Array.isArray(mode.vectors)) {
        return { ok: false, hint: "请选择含位移向量的振动模式" };
      }
      if (mode.vectors.length !== currentFrame.atoms.length) {
        return { ok: false, hint: "振动向量与几何原子数不一致" };
      }
      var coords = currentFrame.atoms.map(function (atom) { return [atom.x, atom.y, atom.z]; });
      var symbols = currentFrame.atoms.map(function (atom) { return atom.elem; });
      return { ok: true, mode: mode, coords: coords, symbols: symbols, atoms: currentFrame.atoms };
    }

    function clearVibrationShapes() {
      if (scene && typeof scene.removeAllShapes === "function") {
        try { scene.removeAllShapes(); } catch (e) { /* shapes are best-effort */ }
        // removeAllShapes also drops measurement/pick shape tokens (labels are
        // separate), so forget them before redrawing overlays.
        measurementShapeTokens = [];
        pickShapeTokens = [];
      }
    }

    function setVibrationAnimStatus(message) {
      if (vibrationAnimStatusEl) vibrationAnimStatusEl.textContent = message || "";
    }

    function drawVibrationArrows() {
      clearVibrationShapes();
      renderMeasurementOverlays(); // removeAllShapes above dropped the measured lines
      if (!vibrationAnimation.showArrows) {
        setVibrationAnimStatus("箭头已隐藏");
        return;
      }
      var ctx = vibrationGeometryContext();
      if (!ctx.ok) {
        setVibrationAnimStatus(ctx.hint || "箭头不可用");
        return;
      }
      var endpoints = computeArrowEndpoints(ctx.coords, ctx.mode.vectors, 0.5);
      var count = 0;
      if (scene && typeof scene.addArrow === "function") {
        endpoints.forEach(function (endpoint) {
          if (!endpoint) return;
          try {
            var token = scene.addArrow({
              start: { x: endpoint.start[0], y: endpoint.start[1], z: endpoint.start[2] },
              end: { x: endpoint.end[0], y: endpoint.end[1], z: endpoint.end[2] },
              radius: 0.1,
              color: ctx.mode.imaginary ? "#e55353" : "#4ea1ff"
            });
            if (token !== null) count += 1;
          } catch (e) { /* arrow drawing is best-effort */ }
        });
      }
      setVibrationAnimStatus("箭头 " + count);
    }

    function rebuildSceneFromAtoms(atoms, view) {
      try {
        ensureScene();
        var camera = view;
        if (!validView(camera)) {
          try { camera = scene.getView(); } catch (e) { camera = null; }
        }
        scene.removeAllModels();
        modelToken = scene.addModel(atomsToXyz(atoms), "xyz");
        if (!modelToken) return false;
        applyModelStyle(currentStyle);
        bindMeasurementClickable();
        if (validView(camera)) {
          try { scene.setView(camera); } catch (e) { /* keep */ }
        }
        scene.render();
        return true;
      } catch (error) {
        return false;
      }
    }

    function requestVibrationFrame(callback) {
      if (typeof window.requestAnimationFrame === "function") {
        vibrationAnimation.rafType = "raf";
        return window.requestAnimationFrame(callback);
      }
      vibrationAnimation.rafType = "timeout";
      return setTimeout(function () { callback(Date.now()); }, 16);
    }

    function cancelVibrationFrame(id) {
      if (id == null) return;
      if (vibrationAnimation.rafType === "raf" && typeof window.cancelAnimationFrame === "function") {
        window.cancelAnimationFrame(id);
      } else {
        clearTimeout(id);
      }
      vibrationAnimation.rafType = null;
    }

    function vibrationFrameMs() {
      var value = Number(deps.vibrationFrameMs);
      return isFinite(value) && value >= 16 && value <= 1000 ? value : 50;
    }

    function vibrationTick(timestamp) {
      if (!vibrationAnimation.playing) return;
      var now = typeof timestamp === "number" ? timestamp : Date.now();
      if (vibrationAnimation.lastTs == null) vibrationAnimation.lastTs = now;
      var elapsed = now - vibrationAnimation.lastTs;
      if (elapsed < vibrationFrameMs()) {
        vibrationAnimation.rafId = requestVibrationFrame(vibrationTick);
        return;
      }
      vibrationAnimation.lastTs = now;
      var ctx = vibrationGeometryContext();
      if (!ctx.ok) {
        stopVibrationAnimation(true);
        setVibrationAnimStatus(ctx.hint || "动画不可用");
        return;
      }
      vibrationAnimation.phase += Math.min(0.2, elapsed / 1000) * 2 * Math.PI;
      var displaced = computeDisplacedCoords(ctx.coords, ctx.mode.vectors, 0.5, vibrationAnimation.phase);
      var atoms = ctx.atoms.map(function (atom, index) {
        var point = displaced && displaced[index] ? displaced[index] : [atom.x, atom.y, atom.z];
        return { elem: atom.elem, x: point[0], y: point[1], z: point[2] };
      });
      if (!rebuildSceneFromAtoms(atoms, vibrationAnimation.savedView)) {
        stopVibrationAnimation(true);
        setVibrationAnimStatus("动画渲染失败");
        return;
      }
      setVibrationAnimStatus("动画播放中");
      vibrationAnimation.rafId = requestVibrationFrame(vibrationTick);
    }

    function startVibrationAnimation() {
      if (assetState.saving) {
        setVibrationAnimStatus("资产保存中，请稍候");
        return;
      }
      var ctx = vibrationGeometryContext();
      if (!ctx.ok) {
        setVibrationAnimStatus(ctx.hint || "动画不可用");
        return;
      }
      stopVibrationAnimation(true);
      try { vibrationAnimation.savedView = scene ? scene.getView() : null; } catch (e) { vibrationAnimation.savedView = null; }
      vibrationAnimation.playing = true;
      vibrationAnimation.phase = 0;
      vibrationAnimation.lastTs = null;
      // The animation displays displaced currentFrame geometry, not the local
      // edit transaction: drop the transaction explicitly (blocked state).
      discardEditsForPlayback("振动动画播放中 · 本地编辑已放弃");
      stopMeasurementPicking(true);
      removeMeasurementOverlays();
      clearVibrationShapes();
      if (vibrationPlayBtn) {
        vibrationPlayBtn.textContent = "停止";
        vibrationPlayBtn.className = "acp-mod-viewer-vibration-action is-active";
      }
      setVibrationAnimStatus("动画播放中");
      vibrationAnimation.rafId = requestVibrationFrame(vibrationTick);
    }

    function stopVibrationAnimation(restore) {
      if (vibrationAnimation.rafId != null) {
        cancelVibrationFrame(vibrationAnimation.rafId);
        vibrationAnimation.rafId = null;
      }
      var wasPlaying = vibrationAnimation.playing;
      vibrationAnimation.playing = false;
      vibrationAnimation.phase = 0;
      vibrationAnimation.lastTs = null;
      if (vibrationPlayBtn) {
        vibrationPlayBtn.textContent = "播放";
        vibrationPlayBtn.className = "acp-mod-viewer-vibration-action";
      }
      if (restore && wasPlaying && currentFrame) {
        rebuildSceneFromAtoms(currentFrame.atoms, vibrationAnimation.savedView);
        drawVibrationArrows();
      } else if (wasPlaying) {
        // The displaced animation model is still on screen, not currentFrame.
        measurementGeometryStale = true;
      }
      vibrationAnimation.savedView = null;
      renderMeasurementPanel();
    }

    /* ── A-45 measurement: picking, panel, overlays (read-only) ──────────── */

    function measurementArity(type) {
      return MEASUREMENT_ARITY[type] || 0;
    }

    function measurementTypeLabel(type) {
      return MEASUREMENT_LABELS[type] || String(type || "");
    }

    /** Why measuring is currently unavailable (displayed as a local message). */
    function measurementBlockedReason() {
      if (overlayMeasurementsBlocked()) return "映射未证实 · 测量已禁用";
      if (measurementGeometryStale) return "当前 3D 模型不是测量几何，请重新加载结构";
      if (ircState.playing || ircState.loading) return "IRC 播放中 · 测量已暂停";
      if (vibrationAnimation.playing) return "振动动画播放中 · 测量已暂停";
      return null;
    }

    /**
     * Geometry currently on screen: the local A-46 edit result when one is
     * displayed, else the viewer's immutable currentFrame. Measurement math
     * and overlays therefore always describe what the user actually sees,
     * while the stored frame itself is never mutated.
     */
    function currentAtomsForMeasurement() {
      return displayedGeometryAtoms();
    }

    /** Four local panel states: blocked | empty | picking | ready. */
    function measurementPanelState() {
      if (measurementBlockedReason()) return "blocked";
      if (!currentAtomsForMeasurement()) return "empty";
      if (measurementState.picking) return "picking";
      if (measurementState.items.length) return "ready";
      return "empty";
    }

    function selectionProgressText() {
      var atoms = currentAtomsForMeasurement() || [];
      var arity = measurementArity(measurementState.type);
      var picked = measurementState.picks.map(function (id) {
        return measurementAtomLabel(atoms, id);
      }).join(" — ");
      if (!measurementState.picks.length) {
        return "点击原子 1 / " + arity + "（" + measurementTypeLabel(measurementState.type) + "）";
      }
      return "已选 " + measurementState.picks.length + "/" + arity + "：" + picked +
        " · 继续点击原子 " + (measurementState.picks.length + 1);
    }

    function setMeasurementNotice(text, isError) {
      measurementState.notice = text || "";
      measurementState.noticeError = !!isError;
    }

    function measurementStatusText() {
      if (measurementState.notice) return measurementState.notice;
      var blocked = measurementBlockedReason();
      if (blocked) return blocked;
      if (!currentAtomsForMeasurement()) return "暂无结构，先加载结构再测量";
      if (measurementState.picking) return selectionProgressText();
      if (!measurementState.items.length) return "暂无测量";
      return "共 " + measurementState.items.length + " 条测量";
    }

    /** Map a scene atom snapshot to a currentFrame atom index (0-based). */
    function measurementAtomId(atom) {
      var atoms = currentAtomsForMeasurement();
      if (!atoms) return null;
      var index = Number(atom && atom.index);
      if (!isFinite(index) || index !== Math.floor(index)) {
        var serial = Number(atom && atom.serial);
        index = isFinite(serial) ? serial - 1 : NaN;
      }
      if (!isFinite(index) || index !== Math.floor(index) || index < 0 || index >= atoms.length) return null;
      return index;
    }

    /** Bounded-scene click callback: only plain atom snapshots reach this. */
    function onMeasurementAtomClick(atom) {
      if (!alive || !measurementState.picking) return;
      var blocked = measurementBlockedReason();
      if (blocked) {
        setMeasurementNotice(blocked, true);
        stopMeasurementPicking(true);
        return;
      }
      var atoms = currentAtomsForMeasurement();
      if (!atoms) {
        setMeasurementNotice("暂无结构，先加载结构再测量", true);
        stopMeasurementPicking(true);
        return;
      }
      var id = measurementAtomId(atom);
      if (id === null) {
        setMeasurementNotice("无法定位点击的原子（结构可能已切换），请重新选取", true);
        renderMeasurementPanel();
        return;
      }
      if (measurementState.picks.indexOf(id) !== -1) {
        setMeasurementNotice("该原子已选取，请点击其他原子", true);
        renderMeasurementPanel();
        return;
      }
      measurementState.picks.push(id);
      setMeasurementNotice("", false);
      var arity = measurementArity(measurementState.type);
      if (measurementState.picks.length < arity) {
        renderMeasurementPanel();
        renderMeasurementOverlays();
        return;
      }
      var ids = measurementState.picks.slice(0, arity);
      measurementState.picks = [];
      var value = measurementValue(measurementState.type, atoms, ids);
      if (value === null) {
        setMeasurementNotice("几何退化或坐标缺失，无法计算" + measurementTypeLabel(measurementState.type), true);
        renderMeasurementPanel();
        renderMeasurementOverlays();
        return;
      }
      measurementSeq += 1;
      measurementState.items.push({
        id: "measure-" + measurementSeq,
        type: measurementState.type,
        atoms: ids,
        value: value
      });
      setMeasurementNotice("已添加 " + measurementTypeLabel(measurementState.type) + " " +
        formatMeasurementValue(measurementState.type, value), false);
      renderMeasurementPanel();
      renderMeasurementOverlays();
    }

    function bindMeasurementClickable() {
      if (!modelToken || typeof modelToken.setClickable !== "function") return false;
      try {
        return modelToken.setClickable({}, true, onMeasurementAtomClick) !== false;
      } catch (e) {
        return false;
      }
    }

    function startMeasurementPicking() {
      if (!alive) return;
      if (editState.busy) {
        setMeasurementNotice("正在处理本地编辑，请稍候", true);
        renderMeasurementPanel();
        return;
      }
      if (editState.pending) {
        setMeasurementNotice("本地编辑预览中 · 请先应用或取消预览", true);
        renderMeasurementPanel();
        return;
      }
      var blocked = measurementBlockedReason();
      if (blocked) {
        setMeasurementNotice(blocked, true);
        renderMeasurementPanel();
        return;
      }
      var atoms = currentAtomsForMeasurement();
      if (!atoms) {
        setMeasurementNotice("暂无结构，先加载结构再测量", true);
        renderMeasurementPanel();
        return;
      }
      var arity = measurementArity(measurementState.type);
      if (atoms.length < arity) {
        measurementState.picking = false;
        measurementState.picks = [];
        setMeasurementNotice("当前结构仅 " + atoms.length + " 个原子，无法测量" +
          measurementTypeLabel(measurementState.type) + "（需要 " + arity + " 个原子）", true);
        renderMeasurementPanel();
        return;
      }
      if (!bindMeasurementClickable()) {
        measurementState.picking = false;
        measurementState.picks = [];
        setMeasurementNotice("场景点选不可用（模型未就绪），请重新加载结构", true);
        renderMeasurementPanel();
        return;
      }
      measurementState.picking = true;
      measurementState.picks = [];
      setMeasurementNotice("", false);
      renderMeasurementPanel();
      renderMeasurementOverlays();
    }

    function stopMeasurementPicking(keepNotice) {
      measurementState.picking = false;
      measurementState.picks = [];
      if (!keepNotice) setMeasurementNotice("", false);
      renderMeasurementPanel();
      renderMeasurementOverlays();
    }

    function toggleMeasurementPicking() {
      if (measurementState.picking) stopMeasurementPicking(false);
      else startMeasurementPicking();
    }

    function selectMeasurementType(type) {
      if (!MEASUREMENT_ARITY[type] || (measurementState.type === type && measurementState.picking)) return;
      if (editState.pending) {
        setMeasurementNotice("请先应用或取消当前预览", true);
        renderMeasurementPanel();
        return;
      }
      measurementState.type = type;
      measurementState.picks = [];
      setMeasurementNotice("", false);
      renderMeasurementPanel();
      if (measurementState.open) startMeasurementPicking();
      else renderMeasurementPanel();
    }

    function deleteMeasurement(id) {
      if (editState.busy) {
        editBusyNotice();
        return;
      }
      var before = measurementState.items.length;
      measurementState.items = measurementState.items.filter(function (item) { return item.id !== id; });
      if (measurementState.items.length === before) return;
      delete editState.drafts[id];
      if (editState.pending && editState.pending.itemId === id) {
        // The preview belongs to the removed row: drop it and restore the last
        // applied (or original) geometry; committed transactions stay undoable.
        editState.pending = null;
        invalidateAssetForGeometryChange();
        var atoms = displayedGeometryAtoms();
        if (atoms) renderDisplayedGeometry(atoms);
        setEditNotice("测量已删除，未应用的预览已取消", false);
      }
      setMeasurementNotice("", false);
      renderMeasurementPanel();
      renderMeasurementOverlays();
    }

    function clearMeasurementItems() {
      if (editState.busy) {
        editBusyNotice();
        return;
      }
      var hadEdits = hasLocalEditGeometry();
      measurementState.items = [];
      measurementState.picks = [];
      measurementSeq = 0;
      if (hadEdits) {
        // Geometry edits without measurement rows would be impossible to
        // inspect any more: abandon them and restore the original frame.
        clearEditState();
        setEditBusy(false);
        var atoms = originalFrameAtoms();
        if (atoms) renderDisplayedGeometry(atoms);
        setEditNotice("已清除测量与本地编辑，已恢复原始结构", false);
      }
      setMeasurementNotice("", false);
      renderMeasurementPanel();
      renderMeasurementOverlays();
    }

    function removeMeasurementOverlays() {
      if (scene) {
        measurementLabelTokens.forEach(function (token) {
          try { scene.removeLabel(token); } catch (e) { /* token already gone */ }
        });
        measurementShapeTokens.forEach(function (token) {
          try { scene.removeShape(token); } catch (e) { /* token already gone */ }
        });
      }
      measurementLabelTokens = [];
      measurementShapeTokens = [];
      clearPickHighlights();
    }

    function clearPickHighlights() {
      if (scene) {
        pickShapeTokens.forEach(function (token) {
          try { scene.removeShape(token); } catch (e) { /* token already gone */ }
        });
      }
      pickShapeTokens = [];
    }

    function addMeasurementShape(method, spec) {
      if (!scene || typeof scene[method] !== "function") return;
      var token = null;
      try { token = scene[method](spec); } catch (e) { return; }
      if (token !== null && token !== undefined) measurementShapeTokens.push(token);
    }

    function midpoint(p0, p1) {
      return { x: (p0.x + p1.x) / 2, y: (p0.y + p1.y) / 2, z: (p0.z + p1.z) / 2 };
    }

    /** Draw measurement lines/labels and pick-progress spheres via the bounded scene. */
    function renderMeasurementOverlays() {
      if (!scene) return;
      removeMeasurementOverlays();
      var atoms = currentAtomsForMeasurement();
      if (!atoms) return;
      measurementState.items.forEach(function (item) {
        var pts = item.atoms.map(function (id) { return measurementPoint(atoms, id); });
        for (var i = 0; i < pts.length; i += 1) {
          if (!pts[i]) return;
        }
        var color = item.type === "distance" ? "#e879c8" :
          (item.type === "angle" ? "#7fd4e6" : "#f2b84b");
        var labelPosition = null;
        if (item.type === "distance") {
          addMeasurementShape("addCylinder", {
            dashed: true, radius: 0.05, color: color, fromCap: 1, toCap: 1,
            start: { x: pts[0].x, y: pts[0].y, z: pts[0].z },
            end: { x: pts[1].x, y: pts[1].y, z: pts[1].z }
          });
          labelPosition = midpoint(pts[0], pts[1]);
        } else if (item.type === "angle") {
          [pts[0], pts[2]].forEach(function (endpoint) {
            addMeasurementShape("addCylinder", {
              dashed: true, radius: 0.04, color: color, fromCap: 1, toCap: 1,
              start: { x: pts[1].x, y: pts[1].y, z: pts[1].z },
              end: { x: endpoint.x, y: endpoint.y, z: endpoint.z }
            });
          });
          labelPosition = pts[1];
        } else {
          [0, 1, 2].forEach(function (index) {
            addMeasurementShape("addCylinder", {
              dashed: true, radius: 0.035, color: color, fromCap: 1, toCap: 1,
              start: { x: pts[index].x, y: pts[index].y, z: pts[index].z },
              end: { x: pts[index + 1].x, y: pts[index + 1].y, z: pts[index + 1].z }
            });
          });
          labelPosition = midpoint(pts[1], pts[2]);
        }
        if (!labelPosition || typeof scene.addLabel !== "function") return;
        var labelToken = null;
        try {
          labelToken = scene.addLabel(formatMeasurementValue(item.type, item.value), {
            position: { x: labelPosition.x, y: labelPosition.y, z: labelPosition.z },
            fontSize: 12,
            fontColor: color,
            backgroundColor: "rgba(0,0,0,0.7)",
            showBackground: true
          });
        } catch (e) { labelToken = null; }
        if (labelToken !== null && labelToken !== undefined) measurementLabelTokens.push(labelToken);
      });
      measurementState.picks.forEach(function (id) {
        var point = measurementPoint(atoms, id);
        if (!point) return;
        var token = null;
        try {
          token = scene.addSphere({
            center: { x: point.x, y: point.y, z: point.z },
            radius: 0.28,
            color: "#f2b84b",
            opacity: 0.55
          });
        } catch (e) { token = null; }
        if (token !== null && token !== undefined) pickShapeTokens.push(token);
      });
      try { scene.render(); } catch (e) { /* render is best-effort */ }
    }

    function onMeasurementToggle() {
      if (!measurementDrawer) return;
      if (!measurementDrawer.hidden) {
        measurementDrawer.hidden = true;
        measurementState.open = false;
        stopMeasurementPicking(false);
        return;
      }
      measurementDrawer.hidden = false;
      measurementState.open = true;
      renderMeasurementPanel();
    }

    function closeMeasurementPanel() {
      if (measurementDrawer) measurementDrawer.hidden = true;
      measurementState.open = false;
      stopMeasurementPicking(false);
    }

    function renderMeasurementPanel() {
      if (!root) return;
      var blocked = measurementBlockedReason();
      var state = measurementPanelState();
      root.setAttribute("data-acp-measurement-state", state);
      root.setAttribute("data-acp-measurement-count", String(measurementState.items.length));
      root.setAttribute("data-acp-measurement-type", measurementState.type);
      renderEditBar();
      renderAssetPanel();
      if (measurementToggle) {
        measurementToggle.textContent = "测量" +
          (measurementState.items.length ? " (" + measurementState.items.length + ")" : "");
      }
      if (measurementTypeEls) {
        MEASUREMENT_TYPES.forEach(function (type) {
          var el = measurementTypeEls[type];
          if (!el) return;
          el.className = "acp-mod-viewer-measure-type" +
            (type === measurementState.type ? " is-active" : "");
        });
      }
      var atoms = currentAtomsForMeasurement();
      if (measurementPickBtn) {
        measurementPickBtn.textContent = measurementState.picking ? "取消选取" : "开始选取";
        measurementPickBtn.setAttribute("data-i18n",
          measurementState.picking ? "next.viewer.measure_cancel" : "next.viewer.measure_pick");
        measurementPickBtn.className = "acp-mod-viewer-measure-action" +
          (measurementState.picking ? " is-active" : "");
        measurementPickBtn.disabled = !!blocked || !atoms ||
          atoms.length < measurementArity(measurementState.type);
      }
      if (measurementClearBtn) measurementClearBtn.disabled = !measurementState.items.length;
      if (!measurementStatusEl || !measurementListEl) return;
      measurementStatusEl.textContent = measurementStatusText();
      measurementStatusEl.className = "acp-mod-viewer-measure-status" +
        ((measurementState.noticeError || blocked) ? " is-error" : "");
      if (measurementDrawer && measurementDrawer.hidden) return;
      clearChildren(measurementListEl);
      if (!atoms) return; // the status line already explains why
      if (!measurementState.items.length) {
        if (blocked) return; // blocked status alone is the local message
        var empty = window.document.createElement("p");
        empty.className = "acp-mod-viewer-measure-empty";
        empty.textContent = "暂无测量";
        measurementListEl.appendChild(empty);
        return;
      }
      var editBlocked = editBlockedReason();
      measurementState.items.forEach(function (item) {
        var row = window.document.createElement("div");
        row.className = "acp-mod-viewer-measure-row";
        row.setAttribute("data-measure-id", item.id);
        var text = window.document.createElement("span");
        text.className = "acp-mod-viewer-measure-value";
        var atomText = item.atoms.map(function (id) {
          return measurementAtomLabel(atoms, id);
        }).join(item.type === "distance" ? "—" : "-");
        text.textContent = measurementTypeLabel(item.type) + " · " + atomText + " · " +
          formatMeasurementValue(item.type, item.value);
        var remove = window.document.createElement("button");
        remove.type = "button";
        remove.className = "acp-mod-viewer-measure-delete";
        remove.setAttribute("data-measure-delete", item.id);
        remove.setAttribute("data-i18n", "next.viewer.measure_delete");
        remove.textContent = "删除";
        remove.addEventListener("click", function () { deleteMeasurement(item.id); });
        row.appendChild(text);
        row.appendChild(remove);
        // A-46: target input + apply stay inside the row (delete keeps index 1
        // so the A-45 contract/tests are untouched).
        row.appendChild(renderMeasurementEditRow(item, atoms, editBlocked));
        measurementListEl.appendChild(row);
      });
    }

    /** Clear all A-45 state without touching geometry/selection stores. */
    function resetMeasurements() {
      measurementState.picking = false;
      measurementState.picks = [];
      measurementState.items = [];
      measurementState.notice = "";
      measurementState.noticeError = false;
      measurementSeq = 0;
      clearEditState();
      setEditBusy(false);
      resetAssetState();
      removeMeasurementOverlays();
      renderMeasurementPanel();
    }

    /* ── A-46 local internal-coordinate edit transaction ─────────────────── */

    /** The locally displaced atom list currently on screen, or null. */
    function displayedEditAtoms() {
      if (editState.pending) return editState.pending.atoms;
      if (editState.transactions.length) {
        return editState.transactions[editState.transactions.length - 1].afterAtoms;
      }
      return null;
    }

    /** The immutable viewer frame (never mutated by an edit). */
    function originalFrameAtoms() {
      if (!currentFrame || !Array.isArray(currentFrame.atoms) || !currentFrame.atoms.length) return null;
      return currentFrame.atoms;
    }

    /** Displayed geometry = local edit result, else the immutable frame. */
    function displayedGeometryAtoms() {
      return displayedEditAtoms() || originalFrameAtoms();
    }

    function hasLocalEditGeometry() {
      return !!(editState.pending || editState.transactions.length);
    }

    function editBusyNotice() {
      setEditNotice("正在处理上一次编辑，请稍候", true);
      renderMeasurementPanel();
    }

    function setEditBusy(flag) {
      editState.busy = !!flag;
      if (root) root.setAttribute("data-acp-edit-busy", editState.busy ? "true" : "false");
    }

    /** Drop the whole local transaction (data only; callers re-render). */
    function clearEditState() {
      editState.pending = null;
      editState.transactions = [];
      editState.redo = [];
      editState.drafts = Object.create(null);
      editState.notice = "";
      editState.noticeError = false;
      invalidateAssetForGeometryChange();
    }

    function setEditNotice(text, isError) {
      editState.notice = text || "";
      editState.noticeError = !!isError;
    }

    /** Editing and atom picking are mutually exclusive; drop an in-flight pick. */
    function interruptMeasurementPicking() {
      if (measurementState.picking) stopMeasurementPicking(true);
    }

    /**
     * Why editing is unavailable right now (explicit local verdict). The same
     * verdict disables inputs/apply and never silently ignores a click.
     */
    function editBlockedReason() {
      if (overlayMeasurementsBlocked()) return "映射未证实 · 编辑已禁用";
      if (overlayState.active) return "叠合模式 · 请先清除叠合再编辑";
      if (ircState.playing || ircState.loading) return "IRC 播放中 · 编辑已暂停";
      if (vibrationAnimation.playing) return "振动动画播放中 · 编辑已暂停";
      if (measurementGeometryStale) return "当前 3D 模型不是编辑几何，请重新加载结构";
      if (!originalFrameAtoms() && !displayedEditAtoms()) return "暂无结构，先加载结构再编辑";
      return null;
    }

    /** Four local edit states: blocked | idle | preview | applied. */
    function editPanelState() {
      if (editBlockedReason()) return "blocked";
      if (editState.pending) return "preview";
      if (editState.transactions.length) return "applied";
      return "idle";
    }

    function editFailureMessage(reason, detail) {
      var messages = {
        invalid_type: "不支持的内坐标类型",
        no_atoms: "暂无结构，先加载结构再编辑",
        invalid_arity: "测量原子数与类型不匹配，无法编辑",
        invalid_atom: "测量引用的原子已不存在，请重新测量",
        duplicate_atom: "测量原子重复，无法定义内坐标",
        degenerate: "几何退化（共线、重合或零键长），无法编辑",
        out_of_range: "目标值超出允许范围",
        non_finite: "目标值必须是有限数值",
        not_converged: "位移计算未收敛，编辑未应用"
      };
      return detail || messages[reason] || ("编辑失败：" + String(reason || "unknown"));
    }

    function editPendingSummary() {
      var pending = editState.pending;
      if (!pending) return "";
      var labels = pending.atomIds.map(function (id) {
        return measurementAtomLabel(pending.beforeAtoms, id);
      }).join("—");
      return "预览：" + measurementTypeLabel(pending.type) + " " + labels + " " +
        formatMeasurementValue(pending.type, pending.beforeValue) + " → " +
        formatMeasurementValue(pending.type, pending.target) + "（尚未应用）";
    }

    function editTransactionSummary(transaction) {
      var labels = transaction.atomIds.map(function (id) {
        return measurementAtomLabel(transaction.beforeAtoms, id);
      }).join("—");
      return measurementTypeLabel(transaction.type) + " " + labels + " " +
        formatMeasurementValue(transaction.type, transaction.beforeValue) + " → " +
        formatMeasurementValue(transaction.type, transaction.afterValue);
    }

    function editSummaryText() {
      if (editState.pending) {
        return "预览待确认 · 已应用 " + editState.transactions.length + " 步";
      }
      var text = "本地事务 " + editState.transactions.length + " 步";
      if (editState.redo.length) text += " · 可重做 " + editState.redo.length + " 步";
      return text;
    }

    function editStatusText() {
      var blocked = editBlockedReason();
      if (blocked) return blocked;
      if (editState.notice) return editState.notice;
      if (editState.pending) return editPendingSummary();
      if (editState.transactions.length) {
        return "已应用 " + editState.transactions.length + " 步本地编辑（原始结构可随时恢复）";
      }
      return "本地编辑：改目标值后点“应用”，预览确认后写入本地事务；不写后端、不改几何存储。";
    }

    /** Recompute every measurement value from the geometry on screen. */
    function refreshMeasurementValues(atoms) {
      if (!Array.isArray(atoms)) return;
      measurementState.items.forEach(function (item) {
        item.value = measurementValue(item.type, atoms, item.atoms);
      });
    }

    /**
     * Rebuild the ONE bounded model from `atoms` (never a second viewer) and
     * redraw the measurement overlays against the new geometry. Also drops
     * stale vibration arrows/shapes, which belong to the previous model.
     */
    function renderDisplayedGeometry(atoms) {
      if (!alive || !Array.isArray(atoms) || !atoms.length) return false;
      clearVibrationShapes();
      if (!rebuildSceneFromAtoms(atoms, null)) return false;
      refreshMeasurementValues(atoms);
      renderMeasurementOverlays();
      return true;
    }

    /** Restore the immutable frame after a transaction is dropped. */
    function rebuildOriginalFrameGeometry() {
      var atoms = originalFrameAtoms();
      if (!atoms) return false;
      return renderDisplayedGeometry(atoms);
    }

    /** Playback displays geometry that is neither the frame nor the edit. */
    function discardEditsForPlayback(reason) {
      if (!hasLocalEditGeometry() && !editState.redo.length) return;
      clearEditState();
      setEditBusy(false);
      if (reason) setEditNotice(reason, false);
    }

    function startEditPreview(itemId, rawValue) {
      if (!alive) return;
      if (editState.busy) {
        editBusyNotice();
        return;
      }
      interruptMeasurementPicking();
      var blocked = editBlockedReason();
      if (blocked) {
        setEditNotice(blocked, true);
        renderMeasurementPanel();
        return;
      }
      if (editState.pending) {
        setEditNotice("已有未应用的预览，请先应用或取消", true);
        renderMeasurementPanel();
        return;
      }
      var item = measurementState.items.find(function (entry) { return entry.id === itemId; });
      if (!item) {
        setEditNotice("测量已不存在，请重新测量", true);
        renderMeasurementPanel();
        return;
      }
      var atoms = displayedGeometryAtoms();
      if (!atoms) {
        setEditNotice("暂无结构，先加载结构再编辑", true);
        renderMeasurementPanel();
        return;
      }
      var check = validateEditTarget(item.type, rawValue);
      if (!check.ok) {
        setEditNotice(check.message, true);
        renderMeasurementPanel();
        return;
      }
      var current = measurementValue(item.type, atoms, item.atoms);
      if (current === null) {
        setEditNotice("几何退化或坐标缺失，无法编辑" + measurementTypeLabel(item.type), true);
        renderMeasurementPanel();
        return;
      }
      var difference = item.type === "dihedral"
        ? normalizeDihedral(check.value - current)
        : check.value - current;
      if (difference !== null && Math.abs(difference) <= 1e-9) {
        setEditNotice("目标值与当前值相同，无需修改", false);
        renderMeasurementPanel();
        return;
      }
      var displaced = displaceGeometry(item.type, atoms, item.atoms, check.value);
      if (!displaced.ok) {
        setEditNotice(editFailureMessage(displaced.reason, displaced.message), true);
        renderMeasurementPanel();
        return;
      }
      setEditBusy(true);
      try {
        editState.pending = {
          itemId: item.id,
          type: item.type,
          atomIds: item.atoms.slice(),
          target: check.value,
          beforeAtoms: copyAtoms(atoms),
          atoms: displaced.atoms,
          beforeValue: displaced.beforeValue,
          afterValue: displaced.afterValue,
          movedAtomIds: displaced.movedAtomIds.slice()
        };
        if (!renderDisplayedGeometry(displaced.atoms)) {
          editState.pending = null;
          renderDisplayedGeometry(atoms);
          setEditNotice("预览渲染失败，已保留原始几何", true);
        } else {
          setEditNotice("", false); // the preview summary is the explicit status
        }
      } finally {
        setEditBusy(false);
      }
      renderMeasurementPanel();
    }

    function commitEditPreview() {
      if (editState.busy) {
        editBusyNotice();
        return;
      }
      var blocked = editBlockedReason();
      if (blocked) {
        setEditNotice(blocked, true);
        renderMeasurementPanel();
        return;
      }
      var pending = editState.pending;
      if (!pending) {
        setEditNotice("没有待应用的预览", false);
        renderMeasurementPanel();
        return;
      }
      setEditBusy(true);
      try {
        editSeq += 1;
        var transaction = {
          id: "edit-" + editSeq,
          itemId: pending.itemId,
          type: pending.type,
          atomIds: pending.atomIds.slice(),
          target: pending.target,
          beforeAtoms: pending.beforeAtoms,
          afterAtoms: pending.atoms,
          beforeValue: pending.beforeValue,
          afterValue: pending.afterValue,
          movedAtomIds: pending.movedAtomIds.slice()
        };
        editState.transactions.push(transaction);
        editState.redo = [];
        editState.pending = null;
        delete editState.drafts[pending.itemId];
        invalidateAssetForGeometryChange();
        refreshMeasurementValues(transaction.afterAtoms);
        renderMeasurementOverlays();
        setEditNotice("已应用：" + editTransactionSummary(transaction) + "（可撤销；原始结构未修改）", false);
      } finally {
        setEditBusy(false);
      }
      renderMeasurementPanel();
    }

    function cancelEditPreview(text) {
      if (editState.busy) {
        editBusyNotice();
        return;
      }
      if (!editState.pending) {
        setEditNotice("没有待应用的预览", false);
        renderMeasurementPanel();
        return;
      }
      setEditBusy(true);
      try {
        editState.pending = null;
        var atoms = displayedGeometryAtoms();
        if (!renderDisplayedGeometry(atoms)) {
          setEditNotice("取消预览失败，请重新加载结构", true);
        } else {
          setEditNotice(text || "已取消预览，几何恢复到上一次应用的状态", false);
        }
      } finally {
        setEditBusy(false);
      }
      renderMeasurementPanel();
    }

    function undoEdit() {
      if (editState.busy) {
        editBusyNotice();
        return;
      }
      if (editState.pending) {
        cancelEditPreview("已取消未应用的预览");
        return;
      }
      interruptMeasurementPicking();
      var blocked = editBlockedReason();
      if (blocked) {
        setEditNotice(blocked, true);
        renderMeasurementPanel();
        return;
      }
      if (!editState.transactions.length) {
        setEditNotice("没有可撤销的编辑", false);
        renderMeasurementPanel();
        return;
      }
      setEditBusy(true);
      try {
        var transaction = editState.transactions.pop();
        editState.redo.push(transaction);
        invalidateAssetForGeometryChange();
        var atoms = displayedGeometryAtoms();
        if (!renderDisplayedGeometry(atoms)) {
          editState.redo.pop();
          editState.transactions.push(transaction);
          setEditNotice("撤销渲染失败，事务保持不变", true);
        } else {
          setEditNotice("已撤销：" + editTransactionSummary(transaction) + "（已恢复上一步几何）", false);
        }
      } finally {
        setEditBusy(false);
      }
      renderMeasurementPanel();
    }

    function redoEdit() {
      if (editState.busy) {
        editBusyNotice();
        return;
      }
      if (editState.pending) {
        setEditNotice("请先应用或取消当前预览", true);
        renderMeasurementPanel();
        return;
      }
      interruptMeasurementPicking();
      var blocked = editBlockedReason();
      if (blocked) {
        setEditNotice(blocked, true);
        renderMeasurementPanel();
        return;
      }
      if (!editState.redo.length) {
        setEditNotice("没有可重做的编辑", false);
        renderMeasurementPanel();
        return;
      }
      setEditBusy(true);
      try {
        var transaction = editState.redo.pop();
        editState.transactions.push(transaction);
        invalidateAssetForGeometryChange();
        if (!renderDisplayedGeometry(transaction.afterAtoms)) {
          editState.transactions.pop();
          editState.redo.push(transaction);
          setEditNotice("重做渲染失败，事务保持不变", true);
        } else {
          setEditNotice("已重做：" + editTransactionSummary(transaction), false);
        }
      } finally {
        setEditBusy(false);
      }
      renderMeasurementPanel();
    }

    function abandonEdits() {
      if (editState.busy) {
        editBusyNotice();
        return;
      }
      interruptMeasurementPicking();
      var blocked = editBlockedReason();
      if (blocked) {
        setEditNotice(blocked, true);
        renderMeasurementPanel();
        return;
      }
      if (!hasLocalEditGeometry() && !editState.redo.length) {
        setEditNotice("没有本地编辑可放弃", false);
        renderMeasurementPanel();
        return;
      }
      setEditBusy(true);
      try {
        clearEditState();
        if (!rebuildOriginalFrameGeometry()) {
          setEditNotice("恢复原始几何失败，请重新加载结构", true);
        } else {
          setEditNotice("已放弃本地编辑，已恢复原始结构", false);
        }
      } finally {
        setEditBusy(false);
      }
      renderMeasurementPanel();
    }

    /** Summary line + transaction controls; also mirrors the four states. */
    function renderEditBar() {
      if (!root) return;
      var blocked = editBlockedReason();
      var state = editPanelState();
      var busy = editState.busy;
      root.setAttribute("data-acp-edit-state", state);
      root.setAttribute("data-acp-edit-count", String(editState.transactions.length));
      root.setAttribute("data-acp-edit-busy", busy ? "true" : "false");
      if (!editStatusEl || !editBarEl) return;
      editStatusEl.textContent = editStatusText();
      editStatusEl.className = "acp-mod-viewer-edit-status" +
        ((editState.noticeError || blocked) ? " is-error" : "") +
        (state === "preview" ? " is-preview" : "") +
        (state === "applied" ? " is-applied" : "");
      editStatusEl.setAttribute("data-acp-edit-state", state);
      editSummaryEl.textContent = editSummaryText();
      editCommitBtn.hidden = !editState.pending;
      editCancelBtn.hidden = !editState.pending;
      editCommitBtn.disabled = busy || !!blocked;
      editCancelBtn.disabled = busy;
      editUndoBtn.disabled = busy || !!blocked || !!editState.pending ||
        !editState.transactions.length;
      editRedoBtn.disabled = busy || !!blocked || !!editState.pending || !editState.redo.length;
      editAbandonBtn.disabled = busy || !!blocked ||
        (!editState.pending && !editState.transactions.length && !editState.redo.length);
    }

    /* ── A-47 structure-asset save (explicit, committed-edit only) ──────── */

    function defaultAssetName() {
      var entry = currentEntryId();
      var base = entry ? String(entry) : (currentJobId() || "structure");
      return sanitizeAssetName("edited-" + base, "edited-structure");
    }

    function resolvedAssetName() {
      return sanitizeAssetName(
        assetState.nameTouched ? assetState.name : defaultAssetName(),
        "edited-structure"
      );
    }

    /** Transaction stack identity; geometry is deterministic from it. */
    function currentAssetEditKey() {
      if (!editState.transactions.length) return "";
      return editState.transactions.map(function (transaction) {
        return String(transaction.id || "");
      }).join("|");
    }

    /** Why asset saving is unavailable right now (explicit local verdict). */
    function assetSaveBlockedReason() {
      if (!alive) return "面板已销毁，无法保存资产";
      if (!originalFrameAtoms() && !displayedEditAtoms()) return "暂无结构，无法保存资产";
      if (editState.pending) return "编辑预览尚未提交 · 请先确认应用或取消预览";
      if (editState.busy) return "正在处理本地编辑，请稍候";
      var blocked = editBlockedReason();
      if (blocked) return blocked;
      if (!editState.transactions.length) {
        return "应用一次本地编辑后才能保存为资产（不提供保存原始结构）";
      }
      if (!requestFnFor()) return "客户端未注入（ACPClient.transport），无法保存资产";
      return null;
    }

    /** Four local asset states: idle | ready | saving | saved. */
    function assetPanelState() {
      if (assetState.saving) return "saving";
      if (assetSaveBlockedReason()) return "idle";
      if (assetState.receipt && assetState.receipt.editKey === currentAssetEditKey()) {
        return "saved";
      }
      return "ready";
    }

    function assetStatusText() {
      if (assetState.saving) return "保存中…（本地编辑与事务保持不变）";
      if (assetState.error) {
        return "保存失败：" + assetState.error + "（本地编辑仍在，可重试）";
      }
      if (assetState.confirming) {
        return "确认将当前显示几何保存为结构资产？名称：" + resolvedAssetName();
      }
      var blocked = assetSaveBlockedReason();
      if (blocked) return blocked;
      if (assetState.receipt && assetState.receipt.editKey === currentAssetEditKey()) {
        return "已保存为资产：" + assetState.receipt.assetId;
      }
      if (assetState.receipt) {
        return "当前编辑已变化；上次保存的资产 " + assetState.receipt.assetId +
          " 仍可在下方回执中查看";
      }
      return "当前编辑几何可保存为资产；原始任务文件不会被修改";
    }

    /** Mirror the four states + the recorded asset id/ref on the root. */
    function renderAssetPanel() {
      if (!root || !assetSectionEl) return;
      var state = assetPanelState();
      var blocked = assetSaveBlockedReason();
      var receipt = assetState.receipt;
      root.setAttribute("data-acp-asset-state", state);
      root.setAttribute("data-acp-asset-busy", assetState.saving ? "true" : "false");
      root.setAttribute("data-acp-asset-confirm", assetState.confirming ? "true" : "false");
      root.setAttribute("data-acp-asset-error", assetState.error ? "true" : "false");
      root.setAttribute("data-acp-asset-id", receipt ? receipt.assetId : "");
      root.setAttribute("data-acp-asset-path", receipt ? receipt.assetPath : "");
      if (assetNameEl) {
        if (!assetState.nameTouched) assetState.name = defaultAssetName();
        assetNameEl.value = assetState.name;
        assetNameEl.disabled = assetState.saving;
      }
      if (assetNoteEl) {
        assetNoteEl.value = assetState.note;
        assetNoteEl.disabled = assetState.saving;
      }
      if (assetStatusEl) {
        assetStatusEl.textContent = assetStatusText();
        assetStatusEl.className = "acp-mod-viewer-asset-status" +
          (assetState.error ? " is-error" : (blocked && !assetState.saving ? " is-blocked" : ""));
      }
      if (assetSaveBtn) {
        assetSaveBtn.textContent = assetState.confirming ? "确认保存" :
          (state === "saved" ? "已保存" : "保存为资产");
        assetSaveBtn.setAttribute("data-acp-asset-confirm",
          assetState.confirming ? "true" : "false");
        assetSaveBtn.setAttribute("data-i18n", assetState.confirming
          ? "next.viewer.asset_confirm" : "next.viewer.asset_save");
        assetSaveBtn.disabled = state === "saving" || state === "saved" || !!blocked;
      }
      if (assetCancelBtn) {
        assetCancelBtn.hidden = !assetState.confirming;
        assetCancelBtn.disabled = state === "saving";
      }
      if (assetReceiptEl) {
        if (receipt) {
          assetReceiptEl.hidden = false;
          assetReceiptEl.textContent = "资产回执：" + receipt.assetId +
            (receipt.assetPath ? " · " + receipt.assetPath : "") +
            (receipt.name ? " · " + receipt.name : "") +
            (receipt.atomCount ? " · " + receipt.atomCount + " 原子" : "");
        } else {
          assetReceiptEl.hidden = true;
          assetReceiptEl.textContent = "";
        }
      }
    }

    /**
     * Clear the asset block and invalidate every in-flight save result. Called
     * on structure/task switches, playback resets, dispose and retry loads.
     */
    function resetAssetState() {
      assetSeq += 1;
      if (assetAbort) {
        try { assetAbort.abort(); } catch (e) { /* abort is best-effort */ }
        assetAbort = null;
      }
      assetState.name = "";
      assetState.nameTouched = false;
      assetState.note = "";
      assetState.confirming = false;
      assetState.saving = false;
      assetState.error = "";
      assetState.receipt = null;
      if (root && assetSectionEl) renderAssetPanel();
    }

    /** Editing the transaction invalidates a pending confirmation / error. */
    function invalidateAssetForGeometryChange() {
      assetState.confirming = false;
      assetState.error = "";
    }

    /** Preferred request seam: injected → deps.client → page client stack. */
    function requestFnFor() {
      if (typeof deps.request === "function") return deps.request;
      var client = (deps.client && deps.client.transport) ? deps.client : null;
      if (!client && typeof window !== "undefined" && window.ACPClient) {
        client = window.ACPClient;
      }
      if (client && client.transport && typeof client.transport.request === "function") {
        return function (path, opts) { return client.transport.request(path, opts); };
      }
      return null;
    }

    function structureAssetsUrl() {
      var base = deps.apiBase || resolveApiBase();
      return String(base).replace(/\/+$/, "") + STRUCTURE_ASSETS_PATH;
    }

    function finishAssetSuccess(receipt, gen, token) {
      if (!alive || gen !== generation || token !== assetSeq) return;
      assetState.saving = false;
      assetState.confirming = false;
      assetState.error = "";
      assetState.receipt = {
        assetId: receipt.assetId,
        assetPath: receipt.assetPath,
        name: receipt.name || resolvedAssetName(),
        atomCount: receipt.atomCount || 0,
        formula: receipt.formula || "",
        editKey: currentAssetEditKey()
      };
      assetAbort = null;
      setEditBusy(false);
      renderMeasurementPanel();
    }

    function finishAssetFailure(error, gen, token) {
      if (!alive || gen !== generation || token !== assetSeq) return;
      // The A-46 transaction is deliberately NOT touched: a failed save is a
      // retryable UI error, never a reason to drop the user's edit.
      assetState.saving = false;
      assetState.confirming = false;
      assetState.error = assetErrorMessage(error);
      assetAbort = null;
      setEditBusy(false);
      renderMeasurementPanel();
    }

    /** Confirmation click → POST; a second click while saving is ignored. */
    function saveStructureAsset() {
      if (!alive) return;
      if (assetState.saving) return;
      if (assetState.receipt && editState.transactions.length &&
          assetState.receipt.editKey === currentAssetEditKey()) {
        assetState.confirming = false;
        renderMeasurementPanel();
        return;
      }
      var blocked = assetSaveBlockedReason();
      if (blocked) {
        assetState.confirming = false;
        assetState.error = "";
        renderMeasurementPanel();
        return;
      }
      if (!assetState.confirming) {
        assetState.confirming = true;
        assetState.error = "";
        renderMeasurementPanel();
        return;
      }
      var requestFn = requestFnFor();
      if (!requestFn) {
        assetState.error = "客户端未注入（ACPClient.transport），无法保存资产";
        assetState.confirming = false;
        renderMeasurementPanel();
        return;
      }
      var payload = buildStructureAssetPayload({
        atoms: displayedGeometryAtoms(),
        transactions: editState.transactions,
        name: resolvedAssetName(),
        note: assetState.note,
        parentJobId: currentJobId(),
        parentEntryId: currentEntryId(),
        projectId: currentProjectId,
        sourceComment: currentFrame && currentFrame.comment ? currentFrame.comment : "",
        fallbackName: defaultAssetName()
      });
      if (!payload.ok) {
        assetState.error = payload.message;
        assetState.confirming = false;
        renderMeasurementPanel();
        return;
      }
      var gen = generation;
      var token = (assetSeq += 1);
      var abort = null;
      if (typeof AbortController !== "undefined") {
        try {
          abort = new AbortController();
          assetAbort = abort;
        } catch (e) {
          abort = null;
          assetAbort = null;
        }
      }
      var options = {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload.body)
      };
      if (abort) options.signal = abort.signal;
      assetState.saving = true;
      assetState.confirming = false;
      assetState.error = "";
      setEditBusy(true);
      renderMeasurementPanel();
      var pending;
      try {
        pending = requestFn(structureAssetsUrl(), options);
      } catch (error) {
        finishAssetFailure(error, gen, token);
        return;
      }
      Promise.resolve(pending).then(function (body) {
        if (!alive || gen !== generation || token !== assetSeq) return;
        var result = assetReceiptFromResponse(body);
        if (!result.ok) {
          finishAssetFailure(new Error(result.message), gen, token);
          return;
        }
        finishAssetSuccess(result.receipt, gen, token);
      }).catch(function (error) {
        if (!alive || gen !== generation || token !== assetSeq) return;
        finishAssetFailure(error, gen, token);
      });
    }

    function cancelAssetConfirmation() {
      if (assetState.saving) return;
      assetState.confirming = false;
      renderMeasurementPanel();
    }

    /** Per-row target input + apply control (one transaction at a time). */
    function renderMeasurementEditRow(item, atoms, blocked) {
      var doc = window.document;
      var row = doc.createElement("div");
      row.className = "acp-mod-viewer-edit-row";
      row.setAttribute("data-edit-item", item.id);
      var range = EDIT_RANGES[item.type];
      if (!range) {
        var unsupported = doc.createElement("span");
        unsupported.className = "acp-mod-viewer-edit-hint is-error";
        unsupported.textContent = "不支持的内坐标类型：" + String(item.type);
        row.appendChild(unsupported);
        return row;
      }
      var current = measurementValue(item.type, atoms, item.atoms);
      if (current === null) {
        var degenerate = doc.createElement("span");
        degenerate.className = "acp-mod-viewer-edit-hint is-error";
        degenerate.textContent = "几何退化或坐标缺失，无法编辑" + measurementTypeLabel(item.type);
        row.appendChild(degenerate);
        return row;
      }
      var label = doc.createElement("label");
      label.className = "acp-mod-viewer-edit-label";
      label.textContent = "目标";
      var input = doc.createElement("input");
      input.type = "number";
      input.className = "acp-mod-viewer-edit-input";
      input.setAttribute("data-measure-target", item.id);
      input.setAttribute("aria-label", measurementTypeLabel(item.type) + "目标值");
      input.setAttribute("min", String(range.min));
      input.setAttribute("max", String(range.max));
      input.setAttribute("step", String(range.step));
      var draft = editState.drafts[item.id];
      input.value = draft !== undefined ? String(draft) : formatEditInputValue(item.type, current);
      var locked = !!blocked || editState.busy || !!editState.pending;
      input.disabled = locked;
      input.addEventListener("input", function () { editState.drafts[item.id] = input.value; });
      var unit = doc.createElement("span");
      unit.className = "acp-mod-viewer-edit-unit";
      unit.textContent = range.unit;
      var apply = doc.createElement("button");
      apply.type = "button";
      apply.className = "acp-mod-viewer-edit-apply";
      apply.setAttribute("data-measure-apply", item.id);
      apply.setAttribute("data-i18n", "next.viewer.edit_apply");
      apply.textContent = "应用";
      apply.disabled = locked;
      apply.addEventListener("click", function () { startEditPreview(item.id, input.value); });
      row.appendChild(label);
      row.appendChild(input);
      row.appendChild(unit);
      row.appendChild(apply);
      return row;
    }

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
      canvasEl.addEventListener("mouseup", schedulePersistView);
      canvasEl.addEventListener("wheel", schedulePersistView);
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
      // Fixture badge + warning strip are appended AFTER the overlay so the
      // existing root.children[0]=canvas / [1]=overlay contract stays intact.
      fixtureBadge = doc.createElement("span");
      fixtureBadge.className = "acp-mod-viewer-fixture";
      fixtureBadge.setAttribute("data-i18n", "next.view.fixture");
      fixtureBadge.textContent = "演示数据（fixture）";
      fixtureBadge.hidden = true;
      root.appendChild(fixtureBadge);
      warnEl = doc.createElement("p");
      warnEl.className = "acp-mod-viewer-warning";
      warnEl.setAttribute("role", "status");
      warnEl.hidden = true;
      root.appendChild(warnEl);
      catalogToggle = doc.createElement("button");
      catalogToggle.type = "button";
      catalogToggle.className = "acp-mod-viewer-catalog-toggle";
      catalogToggle.setAttribute("data-i18n", "next.viewer.catalog_toggle");
      catalogToggle.textContent = "结构目录";
      catalogToggle.hidden = true;
      catalogToggle.addEventListener("click", onCatalogToggleClick);
      root.appendChild(catalogToggle);
      catalogEl = doc.createElement("aside");
      catalogEl.className = "acp-mod-viewer-catalog";
      catalogEl.hidden = true;
      var catalogHead = doc.createElement("div");
      catalogHead.className = "acp-mod-viewer-catalog-head";
      catalogHead.setAttribute("data-i18n", "next.viewer.catalog_toggle");
      catalogHead.textContent = "结构目录";
      catalogEl.appendChild(catalogHead);
      catalogStatusEl = doc.createElement("p");
      catalogStatusEl.className = "acp-mod-viewer-catalog-status";
      catalogStatusEl.setAttribute("role", "status");
      catalogStatusEl.hidden = true;
      catalogEl.appendChild(catalogStatusEl);
      catalogBody = doc.createElement("div");
      catalogBody.className = "acp-mod-viewer-catalog-body";
      catalogEl.appendChild(catalogBody);
      root.appendChild(catalogEl);
      var styleBar = doc.createElement("div");
      styleBar.className = "acp-mod-viewer-stylebar";
      var styleLabel = doc.createElement("span");
      styleLabel.className = "acp-mod-viewer-style-label";
      styleLabel.setAttribute("data-i18n", "next.viewer.style");
      styleLabel.textContent = "样式";
      styleSelect = doc.createElement("select");
      styleSelect.className = "acp-mod-viewer-style";
      styleSelect.setAttribute("data-viewer-style", "1");
      styleSelect.setAttribute("aria-label", "结构样式");
      STYLE_ORDER.forEach(function (name) {
        var option = doc.createElement("option");
        option.value = name;
        option.setAttribute("data-i18n", "next.viewer.style_" + name.replace(/-/g, "_"));
        option.textContent = STYLE_PRESETS[name].label;
        styleSelect.appendChild(option);
      });
      styleSelect.value = currentStyle;
      styleSelect.addEventListener("change", onStyleChange);
      resetViewBtn = doc.createElement("button");
      resetViewBtn.type = "button";
      resetViewBtn.className = "acp-mod-viewer-reset-view";
      resetViewBtn.setAttribute("data-viewer-reset", "1");
      resetViewBtn.setAttribute("data-i18n", "next.viewer.reset_view");
      resetViewBtn.textContent = "重置视角";
      resetViewBtn.addEventListener("click", onResetView);
      styleBar.appendChild(styleLabel);
      styleBar.appendChild(styleSelect);
      styleBar.appendChild(resetViewBtn);
      vibrationToggle = doc.createElement("button");
      vibrationToggle.type = "button";
      vibrationToggle.className = "acp-mod-viewer-vibration-toggle";
      vibrationToggle.setAttribute("data-vibration-toggle", "1");
      vibrationToggle.setAttribute("data-i18n", "next.viewer.vibration");
      vibrationToggle.textContent = "振动";
      vibrationToggle.addEventListener("click", onVibrationToggle);
      styleBar.appendChild(vibrationToggle);
      measurementToggle = doc.createElement("button");
      measurementToggle.type = "button";
      measurementToggle.className = "acp-mod-viewer-measure-toggle";
      measurementToggle.setAttribute("data-measure-toggle", "1");
      measurementToggle.setAttribute("data-i18n", "next.viewer.measure");
      measurementToggle.textContent = "测量";
      measurementToggle.addEventListener("click", onMeasurementToggle);
      styleBar.appendChild(measurementToggle);
      root.appendChild(styleBar);
      overlayBar = doc.createElement("div");
      overlayBar.className = "acp-mod-viewer-overlay-bar";
      overlayBar.hidden = true;
      overlayStatusEl = doc.createElement("span");
      overlayStatusEl.className = "acp-mod-viewer-overlay-status";
      overlayStatusEl.setAttribute("role", "status");
      overlayClearBtn = doc.createElement("button");
      overlayClearBtn.type = "button";
      overlayClearBtn.className = "acp-mod-viewer-overlay-clear";
      overlayClearBtn.setAttribute("data-overlay-clear", "1");
      overlayClearBtn.setAttribute("data-i18n", "next.viewer.overlay_clear");
      overlayClearBtn.textContent = "清除叠合";
      overlayClearBtn.addEventListener("click", function () { clearOverlay(true); });
      overlayBar.appendChild(overlayStatusEl);
      overlayBar.appendChild(overlayClearBtn);
      root.appendChild(overlayBar);
      ircBar = doc.createElement("div");
      ircBar.className = "acp-mod-viewer-irc-bar";
      ircStatusEl = doc.createElement("span");
      ircStatusEl.className = "acp-mod-viewer-irc-status";
      ircStatusEl.setAttribute("role", "status");
      ircStatusEl.textContent = "IRC";
      ircForwardBtn = doc.createElement("button");
      ircForwardBtn.type = "button";
      ircForwardBtn.className = "acp-mod-viewer-irc-btn";
      ircForwardBtn.setAttribute("data-irc-play", "forward");
      ircForwardBtn.setAttribute("data-i18n", "next.viewer.irc_forward");
      ircForwardBtn.textContent = "正向";
      ircForwardBtn.addEventListener("click", function () { startIrcPlayback("forward"); });
      ircReverseBtn = doc.createElement("button");
      ircReverseBtn.type = "button";
      ircReverseBtn.className = "acp-mod-viewer-irc-btn";
      ircReverseBtn.setAttribute("data-irc-play", "reverse");
      ircReverseBtn.setAttribute("data-i18n", "next.viewer.irc_reverse");
      ircReverseBtn.textContent = "反向";
      ircReverseBtn.addEventListener("click", function () { startIrcPlayback("reverse"); });
      ircStopBtn = doc.createElement("button");
      ircStopBtn.type = "button";
      ircStopBtn.className = "acp-mod-viewer-irc-btn";
      ircStopBtn.setAttribute("data-irc-stop", "1");
      ircStopBtn.setAttribute("data-i18n", "next.viewer.irc_stop");
      ircStopBtn.textContent = "停止";
      ircStopBtn.hidden = true;
      ircStopBtn.addEventListener("click", function () {
        stopIrcPlayback();
        setIrcStatus("已停止");
      });
      ircBar.appendChild(ircStatusEl);
      ircBar.appendChild(ircForwardBtn);
      ircBar.appendChild(ircReverseBtn);
      ircBar.appendChild(ircStopBtn);
      root.appendChild(ircBar);
      vibrationDrawer = doc.createElement("aside");
      vibrationDrawer.className = "acp-mod-viewer-vibration-drawer";
      vibrationDrawer.hidden = true;
      vibrationDrawer.setAttribute("data-acp-vibration-drawer", "1");
      var vibrationHead = doc.createElement("div");
      vibrationHead.className = "acp-mod-viewer-vibration-head";
      var vibrationTitle = doc.createElement("span");
      vibrationTitle.setAttribute("data-i18n", "next.viewer.vibration");
      vibrationTitle.textContent = "振动";
      var vibrationClose = doc.createElement("button");
      vibrationClose.type = "button";
      vibrationClose.className = "acp-mod-viewer-vibration-close";
      vibrationClose.setAttribute("aria-label", "关闭振动面板");
      vibrationClose.textContent = "×";
      vibrationClose.addEventListener("click", function () { vibrationDrawer.hidden = true; });
      vibrationHead.appendChild(vibrationTitle);
      vibrationHead.appendChild(vibrationClose);
      vibrationDrawer.appendChild(vibrationHead);
      vibrationStatusEl = doc.createElement("p");
      vibrationStatusEl.className = "acp-mod-viewer-vibration-status";
      vibrationStatusEl.setAttribute("role", "status");
      vibrationStatusEl.hidden = true;
      vibrationDrawer.appendChild(vibrationStatusEl);
      vibrationListEl = doc.createElement("div");
      vibrationListEl.className = "acp-mod-viewer-vibration-list";
      vibrationDrawer.appendChild(vibrationListEl);
      var vibrationControls = doc.createElement("div");
      vibrationControls.className = "acp-mod-viewer-vibration-controls";
      vibrationArrowBtn = doc.createElement("button");
      vibrationArrowBtn.type = "button";
      vibrationArrowBtn.className = "acp-mod-viewer-vibration-action is-active";
      vibrationArrowBtn.setAttribute("data-vibration-arrows", "1");
      vibrationArrowBtn.textContent = "箭头";
      vibrationArrowBtn.addEventListener("click", function () {
        vibrationAnimation.showArrows = !vibrationAnimation.showArrows;
        vibrationArrowBtn.className = "acp-mod-viewer-vibration-action" +
          (vibrationAnimation.showArrows ? " is-active" : "");
        if (!vibrationAnimation.playing) drawVibrationArrows();
      });
      vibrationPlayBtn = doc.createElement("button");
      vibrationPlayBtn.type = "button";
      vibrationPlayBtn.className = "acp-mod-viewer-vibration-action";
      vibrationPlayBtn.setAttribute("data-vibration-play", "1");
      vibrationPlayBtn.textContent = "播放";
      vibrationPlayBtn.addEventListener("click", function () {
        if (vibrationAnimation.playing) stopVibrationAnimation(true);
        else startVibrationAnimation();
      });
      vibrationAnimStatusEl = doc.createElement("span");
      vibrationAnimStatusEl.className = "acp-mod-viewer-vibration-anim-status";
      vibrationAnimStatusEl.setAttribute("role", "status");
      vibrationControls.appendChild(vibrationArrowBtn);
      vibrationControls.appendChild(vibrationPlayBtn);
      vibrationControls.appendChild(vibrationAnimStatusEl);
      vibrationDrawer.appendChild(vibrationControls);
      vibrationThermoEl = doc.createElement("div");
      vibrationThermoEl.className = "acp-mod-viewer-vibration-thermo";
      vibrationThermoEl.setAttribute("data-acp-thermo", "1");
      vibrationDrawer.appendChild(vibrationThermoEl);
      root.appendChild(vibrationDrawer);
      measurementDrawer = doc.createElement("aside");
      measurementDrawer.className = "acp-mod-viewer-measure-drawer";
      measurementDrawer.hidden = true;
      measurementDrawer.setAttribute("data-acp-measure-drawer", "1");
      var measureHead = doc.createElement("div");
      measureHead.className = "acp-mod-viewer-measure-head";
      var measureTitle = doc.createElement("span");
      measureTitle.setAttribute("data-i18n", "next.viewer.measure");
      measureTitle.textContent = "测量";
      var measureClose = doc.createElement("button");
      measureClose.type = "button";
      measureClose.className = "acp-mod-viewer-measure-close";
      measureClose.setAttribute("data-measure-close", "1");
      measureClose.setAttribute("aria-label", "关闭测量面板");
      measureClose.textContent = "×";
      measureClose.addEventListener("click", closeMeasurementPanel);
      measureHead.appendChild(measureTitle);
      measureHead.appendChild(measureClose);
      measurementDrawer.appendChild(measureHead);
      var measureTypes = doc.createElement("div");
      measureTypes.className = "acp-mod-viewer-measure-types";
      measurementTypeEls = {};
      MEASUREMENT_TYPES.forEach(function (type) {
        var button = doc.createElement("button");
        button.type = "button";
        button.className = "acp-mod-viewer-measure-type" + (type === measurementState.type ? " is-active" : "");
        button.setAttribute("data-measure-type", type);
        button.setAttribute("data-i18n", "next.viewer.measure_" + type);
        button.textContent = measurementTypeLabel(type);
        button.addEventListener("click", function () { selectMeasurementType(type); });
        measurementTypeEls[type] = button;
        measureTypes.appendChild(button);
      });
      measurementDrawer.appendChild(measureTypes);
      measurementStatusEl = doc.createElement("p");
      measurementStatusEl.className = "acp-mod-viewer-measure-status";
      measurementStatusEl.setAttribute("role", "status");
      measurementDrawer.appendChild(measurementStatusEl);
      var measureActions = doc.createElement("div");
      measureActions.className = "acp-mod-viewer-measure-actions";
      measurementPickBtn = doc.createElement("button");
      measurementPickBtn.type = "button";
      measurementPickBtn.className = "acp-mod-viewer-measure-action";
      measurementPickBtn.setAttribute("data-measure-pick", "1");
      measurementPickBtn.setAttribute("data-i18n", "next.viewer.measure_pick");
      measurementPickBtn.textContent = "开始选取";
      measurementPickBtn.addEventListener("click", toggleMeasurementPicking);
      measurementClearBtn = doc.createElement("button");
      measurementClearBtn.type = "button";
      measurementClearBtn.className = "acp-mod-viewer-measure-action";
      measurementClearBtn.setAttribute("data-measure-clear", "1");
      measurementClearBtn.setAttribute("data-i18n", "next.viewer.measure_clear");
      measurementClearBtn.textContent = "清除全部";
      measurementClearBtn.addEventListener("click", clearMeasurementItems);
      measureActions.appendChild(measurementPickBtn);
      measureActions.appendChild(measurementClearBtn);
      measurementDrawer.appendChild(measureActions);
      // A-46: local internal-coordinate edit block (preview → apply → undo).
      var editSection = doc.createElement("div");
      editSection.className = "acp-mod-viewer-edit";
      editSection.setAttribute("data-acp-edit-section", "1");
      var editHead = doc.createElement("div");
      editHead.className = "acp-mod-viewer-edit-head";
      var editTitle = doc.createElement("span");
      editTitle.className = "acp-mod-viewer-edit-title";
      editTitle.setAttribute("data-i18n", "next.viewer.edit_title");
      editTitle.textContent = "内坐标编辑";
      var editBadge = doc.createElement("span");
      editBadge.className = "acp-mod-viewer-edit-badge";
      editBadge.setAttribute("data-i18n", "next.viewer.edit_badge");
      editBadge.textContent = "本地事务";
      editHead.appendChild(editTitle);
      editHead.appendChild(editBadge);
      editSection.appendChild(editHead);
      editStatusEl = doc.createElement("p");
      editStatusEl.className = "acp-mod-viewer-edit-status";
      editStatusEl.setAttribute("role", "status");
      editStatusEl.setAttribute("data-acp-edit-status-line", "1");
      editSection.appendChild(editStatusEl);
      editBarEl = doc.createElement("div");
      editBarEl.className = "acp-mod-viewer-edit-bar";
      editBarEl.setAttribute("data-acp-edit-bar", "1");
      editSummaryEl = doc.createElement("span");
      editSummaryEl.className = "acp-mod-viewer-edit-summary";
      editSummaryEl.setAttribute("data-edit-summary", "1");
      editCommitBtn = doc.createElement("button");
      editCommitBtn.type = "button";
      editCommitBtn.className = "acp-mod-viewer-edit-action is-primary";
      editCommitBtn.setAttribute("data-edit-commit", "1");
      editCommitBtn.setAttribute("data-i18n", "next.viewer.edit_commit");
      editCommitBtn.textContent = "确认应用";
      editCommitBtn.hidden = true;
      editCommitBtn.addEventListener("click", commitEditPreview);
      editCancelBtn = doc.createElement("button");
      editCancelBtn.type = "button";
      editCancelBtn.className = "acp-mod-viewer-edit-action";
      editCancelBtn.setAttribute("data-edit-cancel", "1");
      editCancelBtn.setAttribute("data-i18n", "next.viewer.edit_cancel");
      editCancelBtn.textContent = "取消预览";
      editCancelBtn.hidden = true;
      editCancelBtn.addEventListener("click", function () { cancelEditPreview(""); });
      editUndoBtn = doc.createElement("button");
      editUndoBtn.type = "button";
      editUndoBtn.className = "acp-mod-viewer-edit-action";
      editUndoBtn.setAttribute("data-edit-undo", "1");
      editUndoBtn.setAttribute("data-i18n", "next.viewer.edit_undo");
      editUndoBtn.textContent = "撤销";
      editUndoBtn.addEventListener("click", undoEdit);
      editRedoBtn = doc.createElement("button");
      editRedoBtn.type = "button";
      editRedoBtn.className = "acp-mod-viewer-edit-action";
      editRedoBtn.setAttribute("data-edit-redo", "1");
      editRedoBtn.setAttribute("data-i18n", "next.viewer.edit_redo");
      editRedoBtn.textContent = "重做";
      editRedoBtn.addEventListener("click", redoEdit);
      editAbandonBtn = doc.createElement("button");
      editAbandonBtn.type = "button";
      editAbandonBtn.className = "acp-mod-viewer-edit-action";
      editAbandonBtn.setAttribute("data-edit-abandon", "1");
      editAbandonBtn.setAttribute("data-i18n", "next.viewer.edit_abandon");
      editAbandonBtn.textContent = "放弃全部";
      editAbandonBtn.addEventListener("click", abandonEdits);
      editBarEl.appendChild(editSummaryEl);
      editBarEl.appendChild(editCommitBtn);
      editBarEl.appendChild(editCancelBtn);
      editBarEl.appendChild(editUndoBtn);
      editBarEl.appendChild(editRedoBtn);
      editBarEl.appendChild(editAbandonBtn);
      editSection.appendChild(editBarEl);
      measurementDrawer.appendChild(editSection);
      // A-47: explicit save of the displayed COMMITTED edit as a structure
      // asset. Name/note are metadata; no coordinates are stored here.
      assetSectionEl = doc.createElement("div");
      assetSectionEl.className = "acp-mod-viewer-asset";
      assetSectionEl.setAttribute("data-acp-asset-section", "1");
      var assetHead = doc.createElement("div");
      assetHead.className = "acp-mod-viewer-asset-head";
      var assetTitle = doc.createElement("span");
      assetTitle.className = "acp-mod-viewer-asset-title";
      assetTitle.setAttribute("data-i18n", "next.viewer.asset_title");
      assetTitle.textContent = "资产保存";
      var assetBadge = doc.createElement("span");
      assetBadge.className = "acp-mod-viewer-asset-badge";
      assetBadge.textContent = "structure-assets";
      assetHead.appendChild(assetTitle);
      assetHead.appendChild(assetBadge);
      assetSectionEl.appendChild(assetHead);
      assetStatusEl = doc.createElement("p");
      assetStatusEl.className = "acp-mod-viewer-asset-status";
      assetStatusEl.setAttribute("role", "status");
      assetStatusEl.setAttribute("data-acp-asset-status", "1");
      assetSectionEl.appendChild(assetStatusEl);
      var assetFields = doc.createElement("div");
      assetFields.className = "acp-mod-viewer-asset-fields";
      var assetNameRow = doc.createElement("label");
      assetNameRow.className = "acp-mod-viewer-asset-field";
      var assetNameLabel = doc.createElement("span");
      assetNameLabel.className = "acp-mod-viewer-asset-label";
      assetNameLabel.textContent = "名称";
      assetNameEl = doc.createElement("input");
      assetNameEl.type = "text";
      assetNameEl.className = "acp-mod-viewer-asset-input";
      assetNameEl.setAttribute("data-acp-asset-name", "1");
      assetNameEl.setAttribute("maxlength", String(ASSET_NAME_MAX));
      assetNameEl.setAttribute("placeholder", "结构资产名称");
      assetNameEl.setAttribute("aria-label", "结构资产名称");
      assetNameEl.addEventListener("input", function () {
        assetState.name = assetNameEl.value;
        assetState.nameTouched = true;
      });
      assetNameRow.appendChild(assetNameLabel);
      assetNameRow.appendChild(assetNameEl);
      assetFields.appendChild(assetNameRow);
      var assetNoteRow = doc.createElement("label");
      assetNoteRow.className = "acp-mod-viewer-asset-field";
      var assetNoteLabel = doc.createElement("span");
      assetNoteLabel.className = "acp-mod-viewer-asset-label";
      assetNoteLabel.textContent = "备注";
      assetNoteEl = doc.createElement("input");
      assetNoteEl.type = "text";
      assetNoteEl.className = "acp-mod-viewer-asset-input";
      assetNoteEl.setAttribute("data-acp-asset-note", "1");
      assetNoteEl.setAttribute("maxlength", String(ASSET_NOTE_MAX));
      assetNoteEl.setAttribute("placeholder", "备注（写入 provenance.note）");
      assetNoteEl.setAttribute("aria-label", "资产备注");
      assetNoteEl.addEventListener("input", function () {
        assetState.note = assetNoteEl.value;
      });
      assetNoteRow.appendChild(assetNoteLabel);
      assetNoteRow.appendChild(assetNoteEl);
      assetFields.appendChild(assetNoteRow);
      assetSectionEl.appendChild(assetFields);
      var assetBar = doc.createElement("div");
      assetBar.className = "acp-mod-viewer-asset-bar";
      assetSaveBtn = doc.createElement("button");
      assetSaveBtn.type = "button";
      assetSaveBtn.className = "acp-mod-viewer-asset-action is-primary";
      assetSaveBtn.setAttribute("data-acp-asset-save", "1");
      assetSaveBtn.setAttribute("data-i18n", "next.viewer.asset_save");
      assetSaveBtn.textContent = "保存为资产";
      assetSaveBtn.addEventListener("click", saveStructureAsset);
      assetCancelBtn = doc.createElement("button");
      assetCancelBtn.type = "button";
      assetCancelBtn.className = "acp-mod-viewer-asset-action";
      assetCancelBtn.setAttribute("data-acp-asset-cancel", "1");
      assetCancelBtn.setAttribute("data-i18n", "next.viewer.asset_cancel");
      assetCancelBtn.textContent = "取消";
      assetCancelBtn.hidden = true;
      assetCancelBtn.addEventListener("click", cancelAssetConfirmation);
      assetBar.appendChild(assetSaveBtn);
      assetBar.appendChild(assetCancelBtn);
      assetSectionEl.appendChild(assetBar);
      assetReceiptEl = doc.createElement("p");
      assetReceiptEl.className = "acp-mod-viewer-asset-receipt";
      assetReceiptEl.setAttribute("data-acp-asset-receipt", "1");
      assetReceiptEl.hidden = true;
      assetSectionEl.appendChild(assetReceiptEl);
      measurementDrawer.appendChild(assetSectionEl);
      measurementListEl = doc.createElement("div");
      measurementListEl.className = "acp-mod-viewer-measure-list";
      measurementListEl.setAttribute("data-acp-measure-list", "1");
      measurementDrawer.appendChild(measurementListEl);
      root.appendChild(measurementDrawer);
      root.setAttribute("data-acp-overlay-state", "idle");
      root.setAttribute("data-acp-overlay-measurements", "allowed");
      root.setAttribute("data-acp-fixture", "false");
      root.setAttribute("data-acp-warning-count", "0");
      root.setAttribute("data-acp-measurement-state", "empty");
      root.setAttribute("data-acp-measurement-count", "0");
      root.setAttribute("data-acp-measurement-type", "distance");
      root.setAttribute("data-acp-edit-state", "idle");
      root.setAttribute("data-acp-edit-count", "0");
      root.setAttribute("data-acp-edit-busy", "false");
      root.setAttribute("data-acp-asset-state", "idle");
      root.setAttribute("data-acp-asset-busy", "false");
      root.setAttribute("data-acp-asset-confirm", "false");
      root.setAttribute("data-acp-asset-error", "false");
      root.setAttribute("data-acp-asset-id", "");
      root.setAttribute("data-acp-asset-path", "");
      host.appendChild(root);
      renderMeasurementPanel();
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
      renderMeasurementPanel();
    }

    function onRetryClick() {
      if (!alive) return;
      loadRef(currentRef);
    }

    /* ------------------------- structure catalog (A-32) ------------------ */

    function taskKeyOf(taskRef) {
      if (!taskRef || typeof taskRef.jobId !== "string" || !taskRef.jobId) return null;
      return taskRef.jobId + ":" + (taskRef.attempt == null ? "" : String(taskRef.attempt));
    }

    function clearChildren(el) {
      if (!el) return;
      if (typeof el.replaceChildren === "function") el.replaceChildren();
      else el.textContent = "";
    }

    function setCatalogStatus(text, isError) {
      if (!catalogStatusEl) return;
      catalogStatusEl.textContent = text || "";
      catalogStatusEl.hidden = !text;
      catalogStatusEl.className = "acp-mod-viewer-catalog-status" + (isError ? " is-error" : "");
    }

    function clearCatalogUi() {
      catalogGeneration += 1;
      catalogLoadedKey = null;
      catalogLoadingKey = null;
      lastCatalogView = null;
      selectedEntryId = null;
      pendingCatalogAction = null;
      clearChildren(catalogBody);
      setCatalogStatus("");
      if (catalogEl) catalogEl.hidden = true;
    }

    function syncCatalogToggle(taskRef) {
      if (catalogToggle) catalogToggle.hidden = !taskKeyOf(taskRef);
    }

    function onCatalogToggleClick() {
      if (!alive || !catalogEl) return;
      if (!catalogEl.hidden) {
        catalogEl.hidden = true;
        return;
      }
      catalogEl.hidden = false;
      if (lastCatalogView) {
        renderCatalog(lastCatalogView);
        return;
      }
      if (currentTaskRef && currentTaskRef.jobId && catalogLoadedKey !== currentTaskKey) {
        loadCatalogFor(currentTaskRef);
      }
    }

    function entryIdForRef(ref) {
      var req;
      try { req = normalizeRef(ref); } catch (e) { return null; }
      if (!req || req.kind === "invalid") return null;
      if (req.kind === "structure-entry") return req.entryId;
      if (req.kind !== "job-file" && req.kind !== "job-remote-file") return null;
      var entries = lastCatalogView && Array.isArray(lastCatalogView.entries) ? lastCatalogView.entries : [];
      var match = entries.find(function (entry) { return entry.geometryRef === req.path; });
      return match ? match.id : null;
    }

    function renderCatalog(view) {
      if (!catalogEl || !catalogBody) return;
      clearChildren(catalogBody);
      setCatalogStatus("");
      if (!selectedEntryId && currentRef) selectedEntryId = entryIdForRef(currentRef);
      var groups = Array.isArray(view.groups) ? view.groups : [];
      var entries = Array.isArray(view.entries) ? view.entries : [];
      if (!entries.length) {
        setCatalogStatus(warningMessage(view) || "暂无结构条目", view.availability === "error");
        return;
      }
      var buckets = Object.create(null);
      var order = [];
      groups.forEach(function (group) {
        buckets[group.id] = { label: group.label || group.id, entries: [] };
        order.push(group.id);
      });
      entries.forEach(function (entry) {
        var groupId = entry.groupId || "__ungrouped__";
        if (!buckets[groupId]) {
          buckets[groupId] = {
            label: groupId === "__ungrouped__" ? "结构" : groupId,
            entries: []
          };
          order.push(groupId);
        }
        buckets[groupId].entries.push(entry);
      });
      order.forEach(function (groupId) {
        var bucket = buckets[groupId];
        if (!bucket || !bucket.entries.length) return;
        var section = window.document.createElement("section");
        section.className = "acp-mod-viewer-catalog-section";
        var heading = window.document.createElement("h3");
        heading.className = "acp-mod-viewer-catalog-group";
        heading.textContent = bucket.label;
        section.appendChild(heading);
        bucket.entries.forEach(function (entry) {
          var button = window.document.createElement("button");
          button.type = "button";
          button.className = "acp-mod-viewer-catalog-entry" +
            (entry.id === selectedEntryId ? " is-selected" : "");
          button.setAttribute("data-entry-id", entry.id);
          if (entry.role) button.setAttribute("data-entry-role", entry.role);
          var text = entry.label || entry.id;
          if (entry.relativeEnergyKcal != null) {
            text += " · " + Number(entry.relativeEnergyKcal).toFixed(2) + " kcal/mol";
          }
          button.textContent = text;
          button.addEventListener("click", function () { selectCatalogEntry(entry); });
          section.appendChild(button);
          var overlayButton = window.document.createElement("button");
          overlayButton.type = "button";
          overlayButton.className = "acp-mod-viewer-catalog-overlay" +
            (overlayState.entryB === entry.id && overlayState.active ? " is-active" : "");
          overlayButton.setAttribute("data-overlay-entry-id", entry.id);
          overlayButton.textContent = overlayState.entryB === entry.id && overlayState.active ? "清除叠层" : "叠加";
          overlayButton.disabled = overlayState.loading || !currentEntryId() || entry.id === currentEntryId();
          overlayButton.addEventListener("click", function () {
            if (overlayState.active && overlayState.entryB === entry.id) {
              clearOverlay(true);
            } else {
              loadOverlayFor(entry);
            }
          });
          section.appendChild(overlayButton);
        });
        catalogBody.appendChild(section);
      });
      if (Array.isArray(view.warnings) && view.warnings.length) {
        setCatalogStatus(warningMessage(view), false);
      }
      renderThermoSummary();
    }

    function selectCatalogEntry(entry) {
      if (!alive || !entry || !currentTaskRef || !currentTaskRef.jobId) return;
      clearOverlay(false);
      resetVibration();
      var ref = {
        kind: "structure-entry",
        jobId: currentTaskRef.jobId,
        entryId: entry.id,
        frameIndex: entry.frameIndex == null ? 0 : entry.frameIndex
      };
      selectedEntryId = entry.id;
      if (lastCatalogView) renderCatalog(lastCatalogView); // update highlight
      currentRef = ref;
      currentRefKey = refKey(ref);
      loadRef(ref);
      if (deps.commands && typeof deps.commands.openStructure === "function") {
        try {
          deps.commands.openStructure(Object.assign({ source: "structure-catalog" }, ref));
        } catch (e) { /* navigation is auxiliary; local load already happened */ }
      }
    }

    function loadCatalogFor(taskRef) {
      var key = taskKeyOf(taskRef);
      if (!key || catalogLoadingKey === key || catalogLoadedKey === key) return;
      catalogGeneration += 1;
      var gen = catalogGeneration;
      catalogLoadingKey = key;
      lastCatalogView = null;
      selectedEntryId = null;
      clearChildren(catalogBody);
      setCatalogStatus("结构目录加载中…");
      try {
        ensureSource().load("catalog:" + key, { family: "catalog", jobId: taskRef.jobId }).then(function (view) {
          if (!alive || gen !== catalogGeneration) return;
          catalogLoadingKey = null;
          if (!view || view.availability === "error" || view.availability === "denied") {
            catalogLoadedKey = null;
            pendingCatalogAction = null;
            setIrcStatus("IRC 目录加载失败");
            setCatalogStatus(warningMessage(view) || "结构目录加载失败", true);
            return;
          }
          catalogLoadedKey = key;
          lastCatalogView = view;
          if (view.availability !== "ready" || !view.entries.length) {
            setCatalogStatus(warningMessage(view) || "暂无结构条目", false);
            flushPendingCatalogAction();
            return;
          }
          renderCatalog(view);
          flushPendingCatalogAction();
        }).catch(function (error) {
          if (!alive || gen !== catalogGeneration) return;
          catalogLoadingKey = null;
          catalogLoadedKey = null;
          pendingCatalogAction = null;
          setIrcStatus("IRC 目录加载失败");
          setCatalogStatus(error && error.message ? error.message : String(error), true);
        });
      } catch (error) {
        catalogLoadingKey = null;
        catalogLoadedKey = null;
        pendingCatalogAction = null;
        setIrcStatus("IRC 目录加载失败");
        setCatalogStatus(error && error.message ? error.message : String(error), true);
      }
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
        store.load({ entryId: req.storeEntryId || req.entryId, symbols: symbols, coordinates: coordinates });
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
        resetMeasurements();
        measurementGeometryStale = false;
        currentFrame = frame;
        ensureScene();
        scene.clearScene();
        var token = scene.addModel(atomsToXyz(frame.atoms), "xyz");
        if (!token) {
          var err = typeof scene.lastError === "function" ? scene.lastError() : null;
          setState("error", err ? "场景渲染失败: " + err.message : "场景渲染失败");
          return;
        }
        var prefs = readViewPrefs(entryKeyFor(req));
        if (prefs) currentStyle = prefs.style;
        modelToken = token;
        applyModelStyle(currentStyle);
        bindMeasurementClickable();
        scene.setBackgroundColor(DEFAULT_BACKGROUND);
        syncGeometryStore(frame, req);
        hasRestoredView = !!(prefs && validView(prefs.view));
        if (hasRestoredView) {
          try { scene.setView(prefs.view); } catch (e) { /* restore is best-effort */ }
        } else {
          scheduleFraming();
        }
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

    function ensureSource() {
      if (source) return source;
      var api = requireSource();
      source = api.create({
        mode: deps.viewMode,
        baseUrl: deps.apiBase || resolveApiBase(),
        fetch: deps.fetch,
        fixtures: deps.fixtures,
        location: window.location,
        storage: window.localStorage
      });
      return source;
    }

    function clearMeta() {
      if (!root) return;
      root.setAttribute("data-acp-fixture", "false");
      root.setAttribute("data-acp-warning-count", "0");
      if (fixtureBadge) fixtureBadge.hidden = true;
      if (warnEl) {
        warnEl.textContent = "";
        warnEl.hidden = true;
      }
    }

    function warningMessage(view) {
      var list = view && Array.isArray(view.warnings) ? view.warnings : [];
      if (!list.length) return "";
      var first = list[0] || {};
      return String(first.message || first.code || "数据不全");
    }

    function applyViewMeta(view) {
      if (!root) return;
      var fixture = !!(view && view.source === "fixture");
      root.setAttribute("data-acp-fixture", fixture ? "true" : "false");
      if (fixtureBadge) fixtureBadge.hidden = !fixture;
      var warnings = view && Array.isArray(view.warnings) ? view.warnings : [];
      root.setAttribute("data-acp-warning-count", String(warnings.length));
      if (warnEl) {
        if (!warnings.length) {
          warnEl.textContent = "";
          warnEl.hidden = true;
        } else {
          var first = warnings[0] || {};
          warnEl.textContent = "数据不全：" + String(first.message || first.code || "unknown") +
            (warnings.length > 1 ? "（另有 " + (warnings.length - 1) + " 条）" : "");
          warnEl.hidden = false;
        }
      }
    }

    function applyView(view, gen, req) {
      if (!alive || gen !== generation) return; // late result guard
      applyViewMeta(view);
      if (!view || view.availability === "error" || view.availability === "denied") {
        var message = warningMessage(view) ||
          (view && view.availability === "denied" ? "无权限访问该结构" : "结构加载失败");
        setState("error", message);
        return;
      }
      if (view.availability !== "ready") {
        clearStructure();
        setState("empty");
        return;
      }
      var objects = view.objects && typeof view.objects === "object" ? view.objects : {};
      var key = req.storeEntryId || req.entryId || refKey(req);
      var object = objects[key];
      if (!object) {
        var keys = Object.keys(objects);
        if (keys.length) object = objects[keys[0]];
      }
      if (!object || typeof object.text !== "string") {
        setState("error", "适配层未提供结构文本");
        return;
      }
      onStructureText(object.text, gen, req);
    }

    function loadFixtureView(gen) {
      setState("loading");
      var req = { kind: "fixture", entryId: "fixture-structure", frameIndex: 0 };
      try {
        ensureSource().load("structure:fixture", { family: "structure" }).then(function (view) {
          if (!alive || gen !== generation) return;
          applyView(view, gen, req);
        }).catch(function (error) {
          if (!alive || gen !== generation) return;
          setState("error", error && error.message ? error.message : String(error));
        });
      } catch (error) {
        setState("error", error && error.message ? error.message : String(error));
      }
    }

    function loadRef(ref) {
      if (!alive) return;
      if (ircState.playing || ircState.loading) {
        stopIrcPlayback();
        setIrcStatus("IRC");
      }
      // A pending/applied local edit is transaction state for the OLD
      // structure: clear it and put the immutable frame back on screen before
      // the next load settles (or fails).
      var hadLocalEdits = hasLocalEditGeometry();
      resetVibration();
      resetMeasurements();
      if (hadLocalEdits) rebuildOriginalFrameGeometry();
      if (overlayState.active || overlayState.loading || overlayModelToken) clearOverlay(false);
      generation += 1;
      var gen = generation;
      currentRef = ref == null ? null : ref;
      clearMeta();
      try {
        ensureSource();
      } catch (error) {
        setState("error", error && error.message ? error.message : String(error));
        return;
      }
      var req;
      try {
        req = normalizeRef(ref);
      } catch (error) {
        setState("error", error && error.message ? error.message : String(error));
        return;
      }
      if (!req) {
        if (source.mode === "fixture") {
          loadFixtureView(gen);
          return;
        }
        clearStructure();
        setState("empty");
        return;
      }
      if (req.kind === "invalid") {
        setState("error", req.reason);
        return;
      }
      if (lastCatalogView) {
        var catalogMatch = entryIdForRef(req);
        if (catalogMatch !== selectedEntryId) {
          selectedEntryId = catalogMatch;
          renderCatalog(lastCatalogView);
        }
      }
      setState("loading");
      try {
        source.load("structure:" + (refKey(req) || "view"), { family: "structure", structureRef: req })
          .then(function (view) {
            if (!alive || gen !== generation) return; // A→B→A / dispose guard
            applyView(view, gen, req);
          })
          .catch(function (error) {
            if (!alive || gen !== generation) return;
            setState("error", error && error.message ? error.message : String(error));
          });
      } catch (error) {
        setState("error", error && error.message ? error.message : String(error));
      }
    }

    function clearStructure() {
      if (scene && currentRef) persistView();
      if (ircState.playing || ircState.loading) {
        stopIrcPlayback();
        setIrcStatus("IRC");
      }
      resetVibration();
      if (overlayState.active || overlayState.loading || overlayModelToken) clearOverlay(false);
      resetMeasurements();
      measurementGeometryStale = false;
      currentFrame = null;
      currentRef = null;
      modelToken = null;
      hasRestoredView = false;
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
      var nextTask = snap == null ? null : snap.taskRef;
      currentProjectId = snap && snap.projectId ? String(snap.projectId) : null;
      if (!nextTask && nextRef && nextRef.jobId) nextTask = { jobId: nextRef.jobId };
      var nextTaskKey = taskKeyOf(nextTask);
      if (nextTaskKey !== currentTaskKey) {
        currentTaskKey = nextTaskKey;
        currentTaskRef = nextTask;
        if (ircState.playing || ircState.loading) {
          stopIrcPlayback();
          setIrcStatus("IRC");
        }
        // The same structureRef may survive a task switch: restore the
        // immutable frame instead of leaving an orphaned edited model behind.
        var hadLocalEdits = hasLocalEditGeometry();
        resetVibration();
        resetMeasurements();
        if (hadLocalEdits) rebuildOriginalFrameGeometry();
        clearCatalogUi();
        syncCatalogToggle(nextTask);
      }
      var nextKey = refKey(nextRef);
      if (nextKey === currentRefKey) {
        // `?view=fixture` must drive the viewer even when no task/structure is
        // selected: the first empty snapshot is the fixture bootstrap.
        if (firstSnapshot && nextKey == null) {
          firstSnapshot = false;
          try {
            if (ensureSource().mode === "fixture") loadRef(null);
          } catch (error) {
            setState("error", error && error.message ? error.message : String(error));
          }
        }
        return; // same ref → no reload, no flicker
      }
      firstSnapshot = false;
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
      if (typeof window.removeEventListener === "function") {
        try { window.removeEventListener("resize", onWindowResize); } catch (e) { /* best effort */ }
      }
      if (unsubscribeContext) {
        try { unsubscribeContext(); } catch (e) { /* unsubscribe is best-effort */ }
        unsubscribeContext = null;
      }
      generation += 1;
      stopIrcPlayback();
      resetVibration();
      resetMeasurements();
      measurementGeometryStale = false;
      currentFrame = null;
      if (scene && currentRef) persistView();
      if (cameraSaveTimer) {
        clearTimeout(cameraSaveTimer);
        cameraSaveTimer = null;
      }
      disposeFraming();
      if (scene) {
        try { scene.dispose(); } catch (e) { /* teardown is best-effort */ }
        scene = null;
      }
      modelToken = null;
      overlayModelToken = null;
      overlayState.active = false;
      overlayState.loading = false;
      overlayState.data = null;
      overlayState.error = null;
      releaseStoreIfOwned();
      currentRef = null;
      currentRefKey = null;
      firstSnapshot = true;
      currentTaskRef = null;
      currentTaskKey = null;
      currentProjectId = null;
      catalogGeneration += 1;
      catalogLoadedKey = null;
      catalogLoadingKey = null;
      lastCatalogView = null;
      selectedEntryId = null;
      ready = false;
      suspended = false;
      source = null;
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
      fixtureBadge = null;
      warnEl = null;
      catalogToggle = null;
      catalogEl = null;
      catalogBody = null;
      catalogStatusEl = null;
      styleSelect = null;
      resetViewBtn = null;
      overlayBar = null;
      overlayStatusEl = null;
      overlayClearBtn = null;
      ircBar = null;
      ircStatusEl = null;
      ircStopBtn = null;
      ircForwardBtn = null;
      ircReverseBtn = null;
      vibrationToggle = null;
      vibrationDrawer = null;
      vibrationStatusEl = null;
      vibrationListEl = null;
      vibrationArrowBtn = null;
      vibrationPlayBtn = null;
      vibrationAnimStatusEl = null;
      vibrationThermoEl = null;
      measurementToggle = null;
      measurementDrawer = null;
      measurementStatusEl = null;
      measurementListEl = null;
      measurementPickBtn = null;
      measurementClearBtn = null;
      measurementTypeEls = null;
      editStatusEl = null;
      editBarEl = null;
      editSummaryEl = null;
      editCommitBtn = null;
      editCancelBtn = null;
      editUndoBtn = null;
      editRedoBtn = null;
      editAbandonBtn = null;
      assetSectionEl = null;
      assetStatusEl = null;
      assetNameEl = null;
      assetNoteEl = null;
      assetSaveBtn = null;
      assetCancelBtn = null;
      assetReceiptEl = null;
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
          updateNarrowMode();
          if (typeof window.addEventListener === "function") {
            window.addEventListener("resize", onWindowResize);
          }
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
        updateNarrowMode();
        if (!alive || !scene || suspended) return;
        try { scene.resize(); scene.render(); } catch (e) { /* resize is best-effort */ }
      },

      suspend: function () {
        if (!alive || suspended) return;
        suspended = true;
        if (ircState.playing || ircState.loading) {
          stopIrcPlayback();
          setIrcStatus("IRC");
        }
        if (vibrationAnimation.playing) stopVibrationAnimation(true);
        if (scene && currentRef) persistView();
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
          if (ready && !hasRestoredView) scheduleFraming();
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
    // A-45 pure measurement math (hand-computed test surface only).
    MEASUREMENT_ARITY: MEASUREMENT_ARITY,
    measurementValue: measurementValue,
    measurementDistance: measurementDistance,
    measurementAngle: measurementAngle,
    measurementDihedral: measurementDihedral,
    normalizeDihedral: normalizeDihedral,
    formatMeasurementValue: formatMeasurementValue,
    // A-46 pure local-edit math (hand-computed test surface only).
    EDIT_RANGES: EDIT_RANGES,
    EDIT_TYPES: EDIT_TYPES,
    editRangeText: editRangeText,
    validateEditTarget: validateEditTarget,
    displaceGeometry: displaceGeometry,
    formatEditInputValue: formatEditInputValue,
    // A-47 pure structure-asset payload (no network / no instance state).
    STRUCTURE_ASSETS_PATH: STRUCTURE_ASSETS_PATH,
    EDIT_OPERATION_TYPES: EDIT_OPERATION_TYPES,
    sanitizeAssetName: sanitizeAssetName,
    buildAssetXyz: buildAssetXyz,
    assetOperationFromTransaction: assetOperationFromTransaction,
    buildStructureAssetPayload: buildStructureAssetPayload,
    assetReceiptFromResponse: assetReceiptFromResponse,
  };
})();
