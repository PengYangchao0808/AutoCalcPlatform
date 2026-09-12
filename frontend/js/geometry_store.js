/**
 * ACP Geometry Store — authoritative owner of structure-viewer geometry,
 * selection, measurements, and a monotonic revision counter.
 * @version 1.0.0
 *
 * Namespace: window.ACPGeometryStore.  Pure logic only: NO DOM, NO 3Dmol,
 * NO fetch, NO module syntax; loads in the browser and in Node via
 * `var window = {}; require("geometry_store.js")`.  updateCoordinates()
 * preserves selection + measurements.  Change tokens: "load", "coordinates",
 * "selection", "measurement", "preview"; listeners run synchronously with
 * (snapshot, change) and a throwing listener never breaks a mutation.
 */
(function () {
  "use strict";

  var VERSION = "1.0.0";
  /** Atom-count arity per measurement type. @type {Object<string, number>} */
  var MEASUREMENT_ARITY = { distance: 2, angle: 3, dihedral: 4 };
  /**
   * Live authoritative state (also `store.state`); the selection and
   * measurements arrays mutate in place so external refs stay valid.
   * @type {Object}
   */
  var state = {
    entryId: null, revision: 0, symbols: [], coordinates: [],
    bondProvenance: null, selection: [], measurements: [], editPreview: null
  };
  var measureSeq = 0; /* monotonic; measurement ids are never array indices */
  var listeners = [];

  /* ------------------------------- helpers ------------------------------- */
  function isFiniteNumber(value) { return typeof value === "number" && isFinite(value); }
  function sub3(a, b) { return { x: a.x - b.x, y: a.y - b.y, z: a.z - b.z }; }
  function dot3(a, b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
  function len3(a) { return Math.sqrt(dot3(a, a)); }
  function cross3(a, b) { return { x: a.y * b.z - a.z * b.y, y: a.z * b.x - a.x * b.z, z: a.x * b.y - a.y * b.x }; }
  /** Normalize degrees to (-180, 180]; -180 -> +180 (mirror of ACPStructureEditor.normalizeDihedral). */
  function normalizeDihedral(deg) {
    var d = +deg;
    if (!isFinite(d)) return 0;
    d = d % 360;
    if (d > 180) d -= 360;
    else if (d <= -180) d += 360;
    return d;
  }
  /** Coerce a canonical atom id; integers >= 0 only. */
  function toAtomId(value) {
    var n = (typeof value === "string" && value !== "") ? Number(value) : value;
    return (typeof n === "number" && Number.isInteger(n) && n >= 0) ? n : null;
  }
  /** Resolve one atom position from a coordinate list; null when missing/non-finite. */
  function pointAt(coordinates, atomId) {
    var id = toAtomId(atomId);
    if (id === null || id >= coordinates.length) return null;
    var row = coordinates[id];
    if (!Array.isArray(row) || row[0] === null || row[0] === undefined ||
        row[1] === null || row[1] === undefined || row[2] === null || row[2] === undefined) return null;
    var x = Number(row[0]), y = Number(row[1]), z = Number(row[2]);
    return (isFinite(x) && isFinite(y) && isFinite(z)) ? { x: x, y: y, z: z } : null;
  }
  /** Deep-copy a coordinate list (rows copied; non-arrays preserved). */
  function copyCoordinates(coordinates) {
    return coordinates.map(function (row) { return Array.isArray(row) ? row.slice() : row; });
  }
  /** Deep-copy one measurement so callers cannot mutate internal state. */
  function copyMeasurement(m) {
    return { id: m.id, type: m.type, atoms: m.atoms.slice(), value: m.value,
      editable: m.editable, reason: m.reason, message: m.message,
      entry_id: m.entry_id, revision: m.revision, _applied: m._applied };
  }
  /** Deep-copy an edit preview (null-safe). */
  function copyPreview(preview) {
    if (preview === null || preview === undefined) return null;
    if (typeof preview !== "object" || Array.isArray(preview)) return null;
    var copy = {};
    Object.keys(preview).forEach(function (key) { copy[key] = preview[key]; });
    if (Array.isArray(copy.coordinates)) copy.coordinates = copyCoordinates(copy.coordinates);
    if (Array.isArray(copy.atomIds)) copy.atomIds = copy.atomIds.slice();
    return copy;
  }
  /** Validate + dedupe preserving first-occurrence (pick) order. */
  function normalizeSelection(ids) {
    var out = [];
    if (!Array.isArray(ids)) return out;
    for (var i = 0; i < ids.length; i++) {
      var id = toAtomId(ids[i]);
      if (id !== null && out.indexOf(id) === -1) out.push(id);
    }
    return out;
  }
  /** Find a measurement by id; null when absent. */
  function findMeasurement(id) {
    for (var i = 0; i < state.measurements.length; i++) {
      if (state.measurements[i].id === id) return state.measurements[i];
    }
    return null;
  }
  /** Probe editability via ACPStructureEditor.validateEditableCoordinate; absent -> editable. */
  function probeEditable(type, atomIds) {
    var fallback = { editable: true, reason: null, message: null };
    var editor = (typeof window !== "undefined" && window) ? window.ACPStructureEditor : null;
    if (!editor || typeof editor.validateEditableCoordinate !== "function") return fallback;
    try {
      var verdict = editor.validateEditableCoordinate(type, atomIds.slice());
      if (!verdict || typeof verdict !== "object") return fallback;
      return {
        editable: verdict.editable === true,
        reason: (verdict.reason === null || verdict.reason === undefined) ? null : String(verdict.reason),
        message: (verdict.message === null || verdict.message === undefined) ? null : String(verdict.message)
      };
    } catch (err) {
      if (typeof console !== "undefined" && console && typeof console.error === "function") {
        console.error("[ACPGeometryStore] validateEditableCoordinate failed:", err);
      }
      return fallback;
    }
  }
  /** Emit a change token; listener exceptions are contained. */
  function emit(change) {
    if (!listeners.length) return;
    var snap = snapshot();
    var pending = listeners.slice();
    for (var i = 0; i < pending.length; i++) {
      try {
        pending[i](snap, change);
      } catch (err) {
        if (typeof console !== "undefined" && console && console.error) {
          console.error("[ACPGeometryStore] listener error (" + change + "):", err);
        }
      }
    }
  }

  /* ----------------------------- public API ------------------------------ */
  /** Detached snapshot of the current state. @returns {Object} {entryId, revision, symbols, coordinates, selection, measurements, bondProvenance, editPreview, atoms}. */
  function snapshot() {
    var atoms = [];
    for (var i = 0; i < state.symbols.length; i++) {
      var row = state.coordinates[i] || [];
      atoms.push({ id: i, elem: state.symbols[i], x: row[0] !== undefined ? row[0] : null,
        y: row[1] !== undefined ? row[1] : null, z: row[2] !== undefined ? row[2] : null });
    }
    return { entryId: state.entryId, revision: state.revision,
      symbols: state.symbols.slice(), coordinates: copyCoordinates(state.coordinates),
      selection: state.selection.slice(), measurements: state.measurements.map(copyMeasurement),
      bondProvenance: state.bondProvenance, editPreview: copyPreview(state.editPreview), atoms: atoms };
  }
  /** Replace geometry for a new entry/frame; clears selection + measurements, sets revision 1, emits "load".
   * @param {Object} payload {entryId, symbols, coordinates, bondProvenance?} (deep-copied). @returns {Object} Snapshot after the load. */
  function load(payload) {
    var next = (payload && typeof payload === "object") ? payload : {};
    state.entryId = (next.entryId === null || next.entryId === undefined) ? null : String(next.entryId);
    state.symbols = Array.isArray(next.symbols) ? next.symbols.slice() : [];
    state.coordinates = Array.isArray(next.coordinates) ? copyCoordinates(next.coordinates) : [];
    state.bondProvenance = typeof next.bondProvenance === "string" ? next.bondProvenance : null;
    state.selection.length = 0;
    state.measurements.length = 0;
    state.editPreview = null;
    state.revision = 1;
    emit("load");
    return snapshot();
  }
  /** Replace coordinates on the same entry; keeps selection + measurements, re-derives every value, clears the edit preview, bumps revision, emits "coordinates".
   * @param {number[][]} coordinates New coordinates (length must equal symbols.length). @returns {Object|null} Snapshot, or null when no entry is loaded or input is invalid (no-op). */
  function updateCoordinates(coordinates) {
    if (state.entryId === null) return null;
    if (!Array.isArray(coordinates) || coordinates.length !== state.symbols.length) return null;
    var next = [];
    for (var i = 0; i < coordinates.length; i++) {
      if (!Array.isArray(coordinates[i])) return null;
      next.push(coordinates[i].slice());
    }
    state.coordinates = next;
    state.revision = state.revision + 1;
    state.editPreview = null;
    for (var j = 0; j < state.measurements.length; j++) {
      var m = state.measurements[j];
      m.value = computeMeasurementValue(m.type, state.coordinates, m.atoms);
    }
    emit("coordinates");
    return snapshot();
  }
  /** Replace the selection (dedupe, preserves pick order); emits "selection".
   * @param {number[]} ids Canonical atom ids; invalid entries are dropped. @returns {Object} Snapshot after the change. */
  function setSelection(ids) {
    var next = normalizeSelection(ids);
    state.selection.length = 0;
    for (var i = 0; i < next.length; i++) state.selection.push(next[i]);
    emit("selection");
    return snapshot();
  }
  /** Add one atom id if absent (appended, preserves pick order); emits "selection".
   * @param {number} id Canonical atom id. @returns {Object} Snapshot after the change. */
  function addToSelection(id) {
    var atomId = toAtomId(id);
    if (atomId !== null && state.selection.indexOf(atomId) === -1) {
      state.selection.push(atomId);
    }
    emit("selection");
    return snapshot();
  }
  /** Remove one atom id when present; emits "selection".
   * @param {number} id Canonical atom id. @returns {Object} Snapshot after the change. */
  function removeFromSelection(id) {
    var atomId = toAtomId(id);
    var index = atomId === null ? -1 : state.selection.indexOf(atomId);
    if (index !== -1) state.selection.splice(index, 1);
    emit("selection");
    return snapshot();
  }
  /** Toggle one atom id (append if absent, preserves pick order); emits "selection".
   * @param {number} id Canonical atom id. @returns {Object} Snapshot after the change. */
  function toggleSelection(id) {
    var atomId = toAtomId(id);
    if (atomId === null) return snapshot();
    var index = state.selection.indexOf(atomId);
    if (index === -1) {
      state.selection.push(atomId);
    } else {
      state.selection.splice(index, 1);
    }
    emit("selection");
    return snapshot();
  }
  /** Empty the selection; emits "selection". @returns {Object} Snapshot after the change. */
  function clearSelection() {
    state.selection.length = 0;
    emit("selection");
    return snapshot();
  }
  /** Test selection membership.
   * @param {number} id Canonical atom id. @returns {boolean} True when selected. */
  function isSelected(id) {
    var atomId = toAtomId(id);
    return atomId !== null && state.selection.indexOf(atomId) !== -1;
  }
  /** Create a measurement; always created when arity/duplicate checks pass (even when not editable), value from current coordinates, emits "measurement".
   * @param {string} type "distance" | "angle" | "dihedral". @param {number[]} atomIds Canonical ids (2 / 3 / 4).
   * @returns {Object|null} New measurement, or null for non-array / wrong arity / duplicate / malformed id input. */
  function addMeasurement(type, atomIds) {
    var arity = MEASUREMENT_ARITY[type];
    if (arity === undefined || !Array.isArray(atomIds) || atomIds.length !== arity) return null;
    var ids = [];
    for (var i = 0; i < atomIds.length; i++) {
      var atomId = toAtomId(atomIds[i]);
      if (atomId === null || ids.indexOf(atomId) !== -1) return null;
      ids.push(atomId);
    }
    var verdict = probeEditable(type, ids);
    measureSeq += 1;
    var measurement = {
      id: "m" + measureSeq + "@" + state.revision, type: type, atoms: ids,
      value: computeMeasurementValue(type, state.coordinates, ids),
      editable: verdict.editable, reason: verdict.reason, message: verdict.message,
      entry_id: state.entryId, revision: state.revision, _applied: false
    };
    state.measurements.push(measurement);
    emit("measurement");
    return measurement;
  }
  /** Remove a measurement by id; emits "measurement" only when found.
   * @param {string} id Measurement id. @returns {boolean} True when removed. */
  function removeMeasurement(id) {
    for (var i = 0; i < state.measurements.length; i++) {
      if (state.measurements[i].id === id) {
        state.measurements.splice(i, 1);
        emit("measurement");
        return true;
      }
    }
    return false;
  }
  /** Remove every measurement; emits "measurement". @returns {Object} Snapshot after the change. */
  function clearMeasurements() {
    state.measurements.length = 0;
    emit("measurement");
    return snapshot();
  }
  /** Recompute one measurement value from current coordinates; emits "measurement".
   * @param {string} id Measurement id. @returns {Object|null} Updated measurement, or null when unknown. */
  function updateMeasurementValue(id) {
    var measurement = findMeasurement(id);
    if (!measurement) return null;
    measurement.value = computeMeasurementValue(measurement.type, state.coordinates, measurement.atoms);
    emit("measurement");
    return measurement;
  }
  /** Set the applied flag (coordinates untouched); emits "measurement".
   * @param {string} id Measurement id. @param {boolean} applied Applied flag. @returns {Object|null} Updated measurement, or null when unknown. */
  function markApplied(id, applied) {
    var measurement = findMeasurement(id);
    if (!measurement) return null;
    measurement._applied = !!applied;
    emit("measurement");
    return measurement;
  }
  /** Set or clear the transient edit preview; emits "preview".
   * @param {Object|null} preview {coordinates, atomIds, note?} (deep-copied) or null. @returns {Object} Snapshot after the change. */
  function setEditPreview(preview) {
    state.editPreview = copyPreview(preview);
    emit("preview");
    return snapshot();
  }
  /** Serialize the canonical geometry as XYZ text. @returns {string} "N\n\nELEMENT x y z\n..." with 6-decimal coordinates; "" when no atoms are loaded. */
  function toXYZ() {
    if (!state.symbols.length) return "";
    var out = String(state.symbols.length) + "\n\n";
    for (var i = 0; i < state.symbols.length; i++) {
      var symbol = state.symbols[i];
      out += (symbol === null || symbol === undefined ? "X" : String(symbol)) + " " +
        formatCoordinate(state.coordinates[i], 0) + " " + formatCoordinate(state.coordinates[i], 1) +
        " " + formatCoordinate(state.coordinates[i], 2) + "\n";
    }
    return out;
  }
  /** Format one coordinate component as x.xxxxxx; invalid -> 0.000000.
   * @param {number[]} row Coordinate row. @param {number} index Component index. @returns {string} Fixed 6-decimal text. */
  function formatCoordinate(row, index) {
    if (!Array.isArray(row)) return "0.000000";
    var value = Number(row[index]);
    return isFinite(value) ? value.toFixed(6) : "0.000000";
  }
  /** Pure measurement math (mirrors the legacy viewer formulas).
   * @param {string} type "distance" | "angle" | "dihedral". @param {number[][]} coordinates Atom-id indexed coordinates. @param {number[]} atomIds Involved ids (2 / 3 / 4).
   * @returns {number|null} Å for distance, 0..180 degrees for angle, (-180, 180] for dihedral, or null when input is missing/degenerate. */
  function computeMeasurementValue(type, coordinates, atomIds) {
    var arity = MEASUREMENT_ARITY[type];
    if (arity === undefined || !Array.isArray(coordinates) || !Array.isArray(atomIds)) return null;
    if (atomIds.length !== arity) return null;
    var pts = [];
    for (var i = 0; i < arity; i++) {
      var pt = pointAt(coordinates, atomIds[i]);
      if (!pt) return null;
      pts.push(pt);
    }
    if (type === "distance") {
      var dx = pts[0].x - pts[1].x, dy = pts[0].y - pts[1].y, dz = pts[0].z - pts[1].z;
      var dist = Math.sqrt(dx * dx + dy * dy + dz * dz);
      return isFiniteNumber(dist) ? dist : null;
    }
    if (type === "angle") {
      var v1 = sub3(pts[0], pts[1]), v2 = sub3(pts[2], pts[1]);
      var denom = len3(v1) * len3(v2);
      if (!denom) return null;
      var cosA = dot3(v1, v2) / denom;
      if (!isFiniteNumber(cosA)) return null;
      var angle = Math.acos(Math.max(-1, Math.min(1, cosA))) * 180 / Math.PI;
      return isFiniteNumber(angle) ? angle : null;
    }
    var b0 = sub3(pts[0], pts[1]), b1 = sub3(pts[2], pts[1]), b2 = sub3(pts[3], pts[2]);
    var b1len = len3(b1);
    if (!b1len) return null;
    var b1u = { x: b1.x / b1len, y: b1.y / b1len, z: b1.z / b1len };
    var proj0 = dot3(b0, b1u), proj2 = dot3(b2, b1u);
    var v = { x: b0.x - proj0 * b1u.x, y: b0.y - proj0 * b1u.y, z: b0.z - proj0 * b1u.z };
    var w = { x: b2.x - proj2 * b1u.x, y: b2.y - proj2 * b1u.y, z: b2.z - proj2 * b1u.z };
    if (!len3(v) || !len3(w)) return null;
    var raw = Math.atan2(dot3(cross3(b1u, v), w), dot3(v, w)) * 180 / Math.PI;
    return isFinite(raw) ? normalizeDihedral(raw) : null;
  }
  /** Subscribe to mutations.
   * @param {Function} fn Listener (snapshot, change). @returns {Function} Unsubscribe function. */
  function subscribe(fn) {
    if (typeof fn !== "function") return function () {};
    listeners.push(fn);
    return function () { unsubscribe(fn); };
  }
  /** Remove a previously registered listener.
   * @param {Function} fn Listener to remove. @returns {void} */
  function unsubscribe(fn) {
    var index = listeners.indexOf(fn);
    if (index !== -1) listeners.splice(index, 1);
  }
  /** Restore the pristine initial state (no notification). @returns {Object} Snapshot after the reset. */
  function reset() {
    state.entryId = null; state.revision = 0; state.symbols = []; state.coordinates = [];
    state.bondProvenance = null; state.selection.length = 0; state.measurements.length = 0;
    state.editPreview = null; measureSeq = 0;
    return snapshot();
  }

  var api = {
    version: VERSION, state: state,
    load: load, updateCoordinates: updateCoordinates,
    setSelection: setSelection, addToSelection: addToSelection,
    removeFromSelection: removeFromSelection, toggleSelection: toggleSelection,
    clearSelection: clearSelection, isSelected: isSelected,
    addMeasurement: addMeasurement, removeMeasurement: removeMeasurement,
    clearMeasurements: clearMeasurements, updateMeasurementValue: updateMeasurementValue,
    markApplied: markApplied, setEditPreview: setEditPreview,
    snapshot: snapshot, toXYZ: toXYZ, computeMeasurementValue: computeMeasurementValue,
    subscribe: subscribe, unsubscribe: unsubscribe, reset: reset
  };

  if (typeof window !== "undefined" && window) {
    window.ACPGeometryStore = api;
  }
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})();
