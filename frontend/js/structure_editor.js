/**
 * ACP Structure Editor — adjacency graph + provenance + fragments (Wave 6, todo 32)
 * @version 0.3.0
 *
 * Namespace: window.ACPStructureEditor
 *
 * Exposes:
 *   - setLocked(true|false) / isLocked()  (animation mutual exclusion, todo 30)
 *   - editorState                         ({graph, provenance} cache)
 *   - buildGraphFromCurrentEntry(explicitBonds?)  (reads ACPStructureViewer.state;
 *                                          null when locked or no coordinates)
 *   - buildAdjacency(symbols, coords, explicitBonds) (PURE graph build)
 *   - connectedComponents(edges, atomCount)          (PURE fragment partition)
 *   - fragmentsAfterCut(edges, atomCount, cutA, cutB) (PURE — todo 33 move-side)
 *   - parseMolBonds(molText)               (minimal V2000 bond-block reader)
 *   - COVALENT_RADII / BOND_TOLERANCE / DEFAULT_RADIUS (named constants)
 *
 * Contract (doc §6.1-§6.2): the adjacency graph is EDIT-ONLY data —
 * building it NEVER modifies the displayed model or the stored structure,
 * and NEVER adds/removes atoms or bonds.  Explicit bonds (parsed SDF/MOL
 * bond blocks) take precedence; otherwise bonds are inferred from the
 * covalent-radius table with tolerance 1.3*(r_i+r_j) and every inferred
 * edge is marked as such.  Disconnected fragments stay disconnected —
 * no auto-bonding.
 *
 * TODO(todo-33): bond-length edit + move-side toggle (consumes fragmentsAfterCut)
 * TODO(todo-34): bond-angle edit + collinear fallback
 * TODO(todo-35): dihedral edit
 * TODO(todo-36): undo/redo stack + dirty state + provenance inspector line
 * TODO(todo-37): save-as-asset + provenance metadata
 */
(function () {
  "use strict";

  /** Version tag — bump on every structural change. */
  var VERSION = "0.3.0";

  /* ---- user-visible strings (zh fallback; i18n dictionary keys land in todo 36) ---- */
  var STR = {
    PROV_EXPLICIT: "\u6587\u4ef6\u663e\u5f0f\u952e",                       // 文件显式键
    PROV_INFERRED: "\u5171\u4ef7\u534a\u5f84\u63a8\u65ad (tolerance {tol})", // 共价半径推断 (tolerance 1.3)
  };

  /**
   * Covalent radii in Å (Cordero 2008 subset used by this project's
   * chemistry surface).  Unknown elements fall back to DEFAULT_RADIUS.
   */
  var COVALENT_RADII = {
    H: 0.31, C: 0.76, N: 0.71, O: 0.66, F: 0.57,
    P: 1.07, S: 1.05, Cl: 1.02, Br: 1.20, I: 1.39,
    B: 0.84, Si: 1.11, Na: 1.66, K: 2.03, Mg: 1.41,
    Ca: 1.76, Zn: 1.22, Fe: 1.32, Cu: 1.32,
  };

  /** Fallback radius for elements missing from the table. */
  var DEFAULT_RADIUS = 1.0;

  /** Inferred-bond distance cutoff multiplier: dist <= 1.3*(r_i + r_j). */
  var BOND_TOLERANCE = 1.3;

  /** @typedef {Object} EditorState @property {Object|null} graph @property {string|null} provenance */
  var editorState = {
    graph: null,
    provenance: null,
  };

  /** Animation mutual-exclusion flag (todo 30; Wave 6 edits check this). */
  var locked = false;

  /**
   * Covalent radius lookup with case normalization; DEFAULT_RADIUS when
   * the element is unknown.
   *
   * @param {string} symbol
   * @returns {number}
   */
  function _radiusOf(symbol) {
    var s = String(symbol == null ? "" : symbol).trim();
    if (!s) return DEFAULT_RADIUS;
    var r = COVALENT_RADII[s];
    if (r !== undefined) return r;
    var norm = s.charAt(0).toUpperCase() + s.slice(1).toLowerCase();
    r = COVALENT_RADII[norm];
    return r !== undefined ? r : DEFAULT_RADIUS;
  }

  /**
   * Build the edit-only adjacency graph.  When `explicitBonds` is a
   * non-empty array (an upstream SDF/MOL bond block — see parseMolBonds)
   * ONLY those edges are used (source "explicit"); otherwise edges are
   * inferred for every pair i<j with dist(i,j) <= BOND_TOLERANCE*(r_i+r_j)
   * (strictly at-or-below; source "inferred").  Disconnected fragments
   * are left disconnected.  PURE: `coords` is read-only and neither the
   * displayed model nor the stored structure is touched.
   *
   * @param {Array<string>|null} symbols
   * @param {Array<Array<number>>|null} coords
   * @param {Array<{a: number, b: number, order?: number}>|null} [explicitBonds]
   * @returns {{edges: Array<{a: number, b: number, order: number, source: string}>,
   *            provenance: string}}
   */
  function buildAdjacency(symbols, coords, explicitBonds) {
    if (!symbols || !coords || symbols.length !== coords.length || !symbols.length) {
      return { edges: [], provenance: "" };
    }

    if (explicitBonds && explicitBonds.length) {
      var explicit = [];
      for (var ei = 0; ei < explicitBonds.length; ei++) {
        var bond = explicitBonds[ei];
        if (!bond) continue;
        var a = bond.a | 0;
        var b = bond.b | 0;
        if (a < 0 || b < 0 || a >= symbols.length || b >= symbols.length || a === b) {
          continue;
        }
        explicit.push({
          a: a,
          b: b,
          order: (typeof bond.order === "number" && isFinite(bond.order)) ? bond.order : 1,
          source: "explicit",
        });
      }
      return { edges: explicit, provenance: STR.PROV_EXPLICIT };
    }

    var inferred = [];
    for (var i = 0; i < coords.length; i++) {
      var ci = coords[i];
      if (!ci || ci.length < 3) continue;
      var ri = _radiusOf(symbols[i]);
      for (var j = i + 1; j < coords.length; j++) {
        var cj = coords[j];
        if (!cj || cj.length < 3) continue;
        var dx = +ci[0] - +cj[0];
        var dy = +ci[1] - +cj[1];
        var dz = +ci[2] - +cj[2];
        var dist = Math.sqrt(dx * dx + dy * dy + dz * dz);
        if (!isFinite(dist)) continue;
        var cutoff = BOND_TOLERANCE * (ri + _radiusOf(symbols[j]));
        if (dist <= cutoff) {
          inferred.push({ a: i, b: j, order: 1, source: "inferred" });
        }
      }
    }
    return {
      edges: inferred,
      provenance: STR.PROV_INFERRED.replace("{tol}", String(BOND_TOLERANCE)),
    };
  }

  /**
   * Partition atoms into connected components (fragment list), ordered by
   * smallest atom index; each component's indices are ascending.
   *
   * @param {Array<{a: number, b: number}>|null} edges
   * @param {number} atomCount
   * @returns {Array<Array<number>>}
   */
  function connectedComponents(edges, atomCount) {
    var n = atomCount | 0;
    if (!(n > 0)) return [];
    var parent = [];
    for (var i = 0; i < n; i++) parent.push(i);
    function find(x) {
      while (parent[x] !== x) {
        parent[x] = parent[parent[x]];
        x = parent[x];
      }
      return x;
    }
    if (edges && edges.length) {
      for (var e = 0; e < edges.length; e++) {
        var edge = edges[e];
        if (!edge) continue;
        var ra = find(edge.a | 0);
        var rb = find(edge.b | 0);
        if (ra !== rb) parent[ra] = rb;
      }
    }
    var byRoot = {};
    for (var k = 0; k < n; k++) {
      var root = find(k);
      if (!byRoot[root]) byRoot[root] = [];
      byRoot[root].push(k);
    }
    var components = [];
    var keys = Object.keys(byRoot);
    for (var c = 0; c < keys.length; c++) {
      var members = byRoot[keys[c]];
      members.sort(function (x, y) { return x - y; });
      components.push(members);
    }
    components.sort(function (x, y) { return x[0] - y[0]; });
    return components;
  }

  /**
   * Fragments after temporarily cutting one bond (todo 33 move-side
   * logic): connectedComponents with edge (cutA, cutB) removed, in either
   * orientation.  PURE — the input edge list is not mutated.
   *
   * @param {Array<{a: number, b: number}>|null} edges
   * @param {number} atomCount
   * @param {number} cutA
   * @param {number} cutB
   * @returns {Array<Array<number>>}
   */
  function fragmentsAfterCut(edges, atomCount, cutA, cutB) {
    var kept = [];
    if (edges && edges.length) {
      for (var i = 0; i < edges.length; i++) {
        var edge = edges[i];
        if (!edge) continue;
        var isCut = (edge.a === cutA && edge.b === cutB) ||
          (edge.a === cutB && edge.b === cutA);
        if (!isCut) kept.push(edge);
      }
    }
    return connectedComponents(kept, atomCount);
  }

  /**
   * Minimal V2000 molfile/SDF bond-block reader: parses the counts line
   * (atoms cols 0-3, bonds cols 3-6) and the bond lines (atom1 cols 0-3,
   * atom2 cols 3-6, order cols 6-9; whitespace-split fallback), returning
   * 0-based {a, b, order} records.  Malformed lines are skipped.
   *
   * @param {string} molText
   * @returns {Array<{a: number, b: number, order: number}>}
   */
  function parseMolBonds(molText) {
    if (!molText) return [];
    var lines = String(molText).trim().split(/\r?\n/);
    if (lines.length < 5) return [];
    var counts = lines[3] || "";
    var nAtoms = parseInt(counts.substring(0, 3), 10);
    var nBonds = parseInt(counts.substring(3, 6), 10);
    if (isNaN(nAtoms) || isNaN(nBonds) || nAtoms < 0 || nBonds < 0) return [];
    var bonds = [];
    for (var i = 0; i < nBonds; i++) {
      var line = lines[4 + nAtoms + i];
      if (!line) break;
      var a = parseInt(line.substring(0, 3), 10);
      var b = parseInt(line.substring(3, 6), 10);
      var order = parseInt(line.substring(6, 9), 10);
      if (isNaN(a) || isNaN(b)) {
        var parts = line.trim().split(/\s+/);
        a = parseInt(parts[0], 10);
        b = parseInt(parts[1], 10);
        order = parts.length > 2 ? parseInt(parts[2], 10) : 1;
      }
      if (isNaN(a) || isNaN(b)) continue;
      bonds.push({
        a: a - 1,
        b: b - 1,
        order: isNaN(order) || order < 1 ? 1 : order,
      });
    }
    return bonds;
  }

  /**
   * Build the graph for the structure viewer's currently displayed entry
   * (reads ACPStructureViewer.state.displayedCoords/Symbols).  Explicit
   * bonds may be passed by the future SDF wiring (todo 33+); otherwise
   * bonds are inferred.  No-op returning null when the animation lock is
   * held (todo 30) or no coordinates are displayed.  The result is cached
   * on editorState for the inspector provenance line (rendered in todo 36).
   *
   * @param {Array<{a: number, b: number, order?: number}>|null} [explicitBonds]
   * @returns {{edges: Array, provenance: string}|null}
   */
  function buildGraphFromCurrentEntry(explicitBonds) {
    if (locked) return null;
    var svState = (typeof window !== "undefined" && window.ACPStructureViewer)
      ? window.ACPStructureViewer.state
      : null;
    if (!svState || !svState.displayedCoords || !svState.displayedSymbols) return null;
    if (svState.displayedCoords.length !== svState.displayedSymbols.length) return null;
    var result = buildAdjacency(svState.displayedSymbols, svState.displayedCoords, explicitBonds);
    editorState.graph = result;
    editorState.provenance = result.provenance;
    return result;
  }

  /* ---- public namespace ---- */
  window.ACPStructureEditor = {
    version: VERSION,
    STR: STR,
    COVALENT_RADII: COVALENT_RADII,
    BOND_TOLERANCE: BOND_TOLERANCE,
    DEFAULT_RADIUS: DEFAULT_RADIUS,
    editorState: editorState,
    setLocked: function (value) {
      locked = value === true;
    },
    isLocked: function () {
      return locked;
    },
    buildGraphFromCurrentEntry: buildGraphFromCurrentEntry,
    buildAdjacency: buildAdjacency,
    connectedComponents: connectedComponents,
    fragmentsAfterCut: fragmentsAfterCut,
    parseMolBonds: parseMolBonds,
  };
})();
