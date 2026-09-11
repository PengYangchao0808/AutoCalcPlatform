/**
 * ACP Structure Editor — graph + bond/angle/dihedral edits (Wave 6, todos 32-35)
 * @version 0.6.0
 *
 * Namespace: window.ACPStructureEditor
 *
 * Exposes:
 *   - setLocked(true|false) / isLocked()  (animation mutual exclusion, todo 30)
 *   - editorState                         ({graph, provenance, pendingEdit})
 *   - buildGraphFromCurrentEntry(explicitBonds?)  (reads ACPStructureViewer.state;
 *                                          null when locked or no coordinates)
 *   - buildAdjacency(symbols, coords, explicitBonds) (PURE graph build)
 *   - connectedComponents(edges, atomCount)          (PURE fragment partition)
 *   - fragmentsAfterCut(edges, atomCount, cutA, cutB) (PURE — move-side logic)
 *   - parseMolBonds(molText)               (minimal V2000 bond-block reader)
 *   - COVALENT_RADII / BOND_TOLERANCE / DEFAULT_RADIUS (named constants)
 *   - editBondLength(symbols, coords, edges, a, b, target, moveSide) (PURE)
 *   - editBondAngle(symbols, coords, edges, a, b, c, targetDeg, moveSide) (PURE)
 *   - editDihedral(symbols, coords, edges, a, b, c, d, targetDeg, moveSide) (PURE)
 *   - dihedralDeg(a, b, c, d) / normalizeDihedral(deg)  (PURE helpers)
 *   - angleDeg(p, q, r) / _principalAxis(points)   (PURE helpers)
 *   - resolveMoveSide(fragments, a, b, preferred) / defaultMoveSide(...) (PURE)
 *   - applyBondLengthEdit(a, b, targetLength, moveSide) (orchestration)
 *   - applyBondAngleEdit(a, b, c, targetDeg, moveSide) (orchestration)
 *   - applyDihedralEdit(a, b, c, d, targetDeg, moveSide) (orchestration)
 *
 * Contract (doc §6.1-§6.3): edits are INTERNAL-COORDINATE ONLY — a bond
 * edit rigidly TRANSLATES one fragment, an angle edit rigidly ROTATES the
 * C-side fragment (never the A-B side; never deformed); bond topology,
 * elements, and atom count never change.  Length targets outside
 * [0.4, 5.0] Å and angle targets outside [1, 179]° are rejected; ring
 * bonds (graph still connected after the cut) are rejected; collinear
 * angle input falls back to the most stable orthonormal axis with a
 * warning; dihedral targets normalize to (-180, 180] and rotate the
 * C-side by the SHORTEST rotation.  Accepted edits are staged in
 * editorState.pendingEdit — pushing them to the viewer and the undo/redo
 * UI is todo 36.
 *
 * TODO(todo-36): undo/redo stack + dirty state + preview/apply pipeline
 * TODO(todo-37): save-as-asset + provenance metadata
 */
(function () {
  "use strict";

  /** Version tag — bump on every structural change. */
  var VERSION = "0.6.0";

  /* ---- user-visible strings (zh fallback; i18n dictionary keys land in todo 36) ---- */
  var STR = {
    PROV_EXPLICIT: "\u6587\u4ef6\u663e\u5f0f\u952e",                       // 文件显式键
    PROV_INFERRED: "\u5171\u4ef7\u534a\u5f84\u63a8\u65ad (tolerance {tol})", // 共价半径推断 (tolerance 1.3)
    MOVE_LEFT: "\u79fb\u52a8\u5de6\u4fa7",                                 // 移动左侧
    MOVE_RIGHT: "\u79fb\u52a8\u53f3\u4fa7",                               // 移动右侧
    COLLINEAR_WARNING: "\u5171\u7ebf\u89d2\u5ea6\u8f93\u5165\uff0c\u65cb\u8f6c\u8f74\u53d6\u6700\u7a33\u5b9a\u6b63\u4ea4\u8f74", // 共线角度输入，旋转轴取最稳定正交轴
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

  /** Allowed bond-length edit range in Å (inclusive; doc §6.3). */
  var BOND_LENGTH_MIN = 0.4;
  var BOND_LENGTH_MAX = 5.0;

  /** Allowed bond-angle edit range in degrees (inclusive; doc §6.3). */
  var ANGLE_MIN = 1;
  var ANGLE_MAX = 179;

  /** |cross(u_BA, u_BC)| below this counts as collinear A-B-C. */
  var COLLINEAR_EPS = 1e-6;

  /** @typedef {Object} EditorState @property {Object|null} graph @property {string|null} provenance */
  var editorState = {
    graph: null,
    provenance: null,
    pendingEdit: null,
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

  /* ---- bond-length edit (todo 33) ---- */

  /**
   * Default move side for a cut bond: the fragment with FEWER atoms
   * moves; ties go to side A (the fragment containing atomA).
   *
   * @param {Array<Array<number>>} fragments - connectedComponents output
   * @param {number} atomA
   * @param {number} atomB
   * @returns {string} "A" | "B"
   */
  function defaultMoveSide(fragments, atomA, atomB) {
    var sideA = _componentOf(fragments, atomA);
    var sideB = _componentOf(fragments, atomB);
    var sizeA = sideA ? sideA.length : 0;
    var sizeB = sideB ? sideB.length : 0;
    return sizeB < sizeA ? "B" : "A";
  }

  /**
   * Resolve the move side: an explicit "A"/"B" preference is honored,
   * null/undefined falls back to defaultMoveSide, anything else is
   * invalid (returns null).
   *
   * @param {Array<Array<number>>} fragments
   * @param {number} atomA
   * @param {number} atomB
   * @param {string|null} [preferred]
   * @returns {string|null}
   */
  function resolveMoveSide(fragments, atomA, atomB, preferred) {
    if (preferred === "A" || preferred === "B") return preferred;
    if (preferred == null) return defaultMoveSide(fragments, atomA, atomB);
    return null;
  }

  function _componentOf(fragments, atomIndex) {
    if (!fragments) return null;
    for (var i = 0; i < fragments.length; i++) {
      if (fragments[i].indexOf(atomIndex) >= 0) return fragments[i];
    }
    return null;
  }

  /**
   * Edit the A-B bond length to `targetLength` by rigidly translating ONE
   * side of the temporarily cut bond.  PURE: a NEW coords array is
   * returned; the input is never mutated and the non-moved rows keep
   * byte-identical values.
   *
   * Math (u = (r_B - r_A)/L, the unit vector A -> B, L = current length,
   * T = target):
   *   moving side B: every side-B atom shifts by (T - L) * u, so
   *     r_B' - r_A = T*u  ->  |A-B'| = T.
   *   moving side A: every side-A atom shifts by s*u with s = L - T (the
   *     near-root of |L - s| = T; s = L + T would push A AWAY past B and
   *     invert the bond direction), so
   *     r_B - r_A' = (L - s)*u = T*u  ->  |A'-B| = T.
   * Both cases are pure translations: internal geometry of the moved
   * fragment and every non-moved atom are exactly preserved.
   *
   * Rejections: target outside [BOND_LENGTH_MIN, BOND_LENGTH_MAX] ->
   * "out_of_range"; a-b not bonded -> "no_bond"; graph still connected
   * after the cut (ring bond) -> "ring_bond"; zero-length bond (no unit
   * vector) -> "degenerate"; invalid moveSide -> "invalid_move_side".
   *
   * @param {Array<string>} symbols
   * @param {Array<Array<number>>} coords
   * @param {Array<{a: number, b: number}>} edges
   * @param {number} atomA
   * @param {number} atomB
   * @param {number} targetLength
   * @param {string|null} [moveSide] - "A" | "B" | null (default smaller side)
   * @returns {{ok: boolean, reason?: string, coords?: Array<Array<number>>}}
   */
  function editBondLength(symbols, coords, edges, atomA, atomB, targetLength, moveSide) {
    var t = +targetLength;
    if (!isFinite(t) || t < BOND_LENGTH_MIN || t > BOND_LENGTH_MAX) {
      return { ok: false, reason: "out_of_range" };
    }
    if (!symbols || !coords || !edges) {
      return { ok: false, reason: "no_bond" };
    }
    var hasBond = false;
    for (var e = 0; e < edges.length; e++) {
      var edge = edges[e];
      if ((edge.a === atomA && edge.b === atomB) ||
          (edge.a === atomB && edge.b === atomA)) {
        hasBond = true;
        break;
      }
    }
    if (!hasBond) return { ok: false, reason: "no_bond" };

    var fragments = fragmentsAfterCut(edges, coords.length, atomA, atomB);
    var sideA = _componentOf(fragments, atomA);
    var sideB = _componentOf(fragments, atomB);
    if (sideA && sideB && sideA === sideB) {
      return { ok: false, reason: "ring_bond" };
    }
    if (!sideA || !sideB) {
      return { ok: false, reason: "no_bond" };
    }

    var side = resolveMoveSide(fragments, atomA, atomB, moveSide);
    if (side !== "A" && side !== "B") {
      return { ok: false, reason: "invalid_move_side" };
    }

    var ra = coords[atomA];
    var rb = coords[atomB];
    var dx = +rb[0] - +ra[0];
    var dy = +rb[1] - +ra[1];
    var dz = +rb[2] - +ra[2];
    var len = Math.sqrt(dx * dx + dy * dy + dz * dz);
    if (!isFinite(len) || len < 1e-12) {
      return { ok: false, reason: "degenerate" };
    }
    var ux = dx / len, uy = dy / len, uz = dz / len;
    var shift = (side === "B") ? (t - len) : (len - t);

    var moving = (side === "B") ? sideB : sideA;
    var movingSet = {};
    for (var m = 0; m < moving.length; m++) movingSet[moving[m]] = true;

    var out = [];
    for (var i = 0; i < coords.length; i++) {
      if (movingSet[i]) {
        out.push([+coords[i][0] + shift * ux, +coords[i][1] + shift * uy, +coords[i][2] + shift * uz]);
      } else {
        out.push(coords[i]);
      }
    }
    return { ok: true, coords: out };
  }

  /* ---- bond-angle edit (todo 34) ---- */

  function _dot(a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }

  function _cross(a, b) {
    return [
      a[1] * b[2] - a[2] * b[1],
      a[2] * b[0] - a[0] * b[2],
      a[0] * b[1] - a[1] * b[0],
    ];
  }

  function _normalize(v) {
    var n = Math.sqrt(_dot(v, v));
    if (!isFinite(n) || n < 1e-12) return null;
    return [v[0] / n, v[1] / n, v[2] / n];
  }

  /**
   * Angle p-q-r at vertex q, in degrees [0, 180] via
   * atan2(|u1 x u2|, u1 . u2).  0 when either arm is degenerate.
   *
   * @param {Array<number>} p
   * @param {Array<number>} q
   * @param {Array<number>} r
   * @returns {number}
   */
  function angleDeg(p, q, r) {
    var u1 = _normalize([p[0] - q[0], p[1] - q[1], p[2] - q[2]]);
    var u2 = _normalize([r[0] - q[0], r[1] - q[1], r[2] - q[2]]);
    if (!u1 || !u2) return 0;
    var c = _cross(u1, u2);
    return Math.atan2(Math.sqrt(_dot(c, c)), _dot(u1, u2)) * 180 / Math.PI;
  }

  /**
   * Principal axis of a point set: the eigenvector of the unit-mass
   * covariance (inertia) tensor with the LARGEST eigenvalue — the
   * fragment's long axis.  Deterministic power iteration (fixed start
   * vector, 50 iterations); falls back to (1,0,0) for degenerate input.
   *
   * @param {Array<Array<number>>} points
   * @returns {Array<number>} unit vector
   */
  function _principalAxis(points) {
    if (!points || !points.length) return [1, 0, 0];
    var cx = 0, cy = 0, cz = 0;
    for (var i = 0; i < points.length; i++) {
      cx += +points[i][0]; cy += +points[i][1]; cz += +points[i][2];
    }
    cx /= points.length; cy /= points.length; cz /= points.length;
    var xx = 0, xy = 0, xz = 0, yy = 0, yz = 0, zz = 0;
    for (var j = 0; j < points.length; j++) {
      var dx = +points[j][0] - cx, dy = +points[j][1] - cy, dz = +points[j][2] - cz;
      xx += dx * dx; xy += dx * dy; xz += dx * dz;
      yy += dy * dy; yz += dy * dz; zz += dz * dz;
    }
    var v = [0.577, 0.577, 0.577];
    for (var it = 0; it < 50; it++) {
      var nv = [
        xx * v[0] + xy * v[1] + xz * v[2],
        xy * v[0] + yy * v[1] + yz * v[2],
        xz * v[0] + yz * v[1] + zz * v[2],
      ];
      var unit = _normalize(nv);
      if (!unit) break;
      v = unit;
    }
    return v;
  }

  /**
   * Collinear fallback axis: of the two unit vectors orthogonal to u_BC,
   * pick the one with the SMALLER |dot| against the C-side fragment's
   * principal axis p (least aligned with the fragment's long axis — the
   * best-conditioned rotation).
   *
   * @param {Array<number>} uBC      - unit vector B -> C
   * @param {Array<number>} p        - fragment principal axis (unit)
   * @returns {Array<number>} unit rotation axis orthogonal to uBC
   */
  function _stableOrthogonalAxis(uBC, p) {
    var ref = Math.abs(uBC[0]) < 0.9 ? [1, 0, 0] : [0, 1, 0];
    var e1 = _normalize(_cross(uBC, ref));
    var e2 = _cross(uBC, e1);
    return Math.abs(_dot(p, e1)) <= Math.abs(_dot(p, e2)) ? e1 : e2;
  }

  /**
   * Rotate vector v about the unit axis k by radians theta (Rodrigues):
   *   v' = v cos(theta) + (k x v) sin(theta) + k (k . v)(1 - cos(theta))
   */
  function _rotateRodrigues(v, k, theta) {
    var cos = Math.cos(theta);
    var sin = Math.sin(theta);
    var kxv = _cross(k, v);
    var kdv = _dot(k, v);
    return [
      v[0] * cos + kxv[0] * sin + k[0] * kdv * (1 - cos),
      v[1] * cos + kxv[1] * sin + k[1] * kdv * (1 - cos),
      v[2] * cos + kxv[2] * sin + k[2] * kdv * (1 - cos),
    ];
  }

  /**
   * Edit the A-B-C bond angle (vertex B) to `targetDeg` by rigidly
   * rotating the C-side fragment about the axis through B perpendicular
   * to the A-B-C plane.  The A-B side is NEVER moved — for angle edits
   * the C side is the only rotatable side by definition, so a `moveSide`
   * argument of "A"/"B" is normalized to "C" (documented; anything else
   * rejects with invalid_move_side).
   *
   * Sign convention: axis n = normalize(u_BA x u_BC) (u_BA points B -> A,
   * u_BC points B -> C); rotating the C side about +n by
   * delta = target - current (degrees) moves u_BC away from u_BA exactly
   * when delta > 0 (opens the angle) and toward it when delta < 0 (closes).
   *
   * Collinear A-B-C (|u_BA x u_BC| < COLLINEAR_EPS): the plane normal is
   * undefined, so the axis is the unit vector orthogonal to u_BC that is
   * least aligned with the C-side fragment's principal axis (most stable
   * against the inertia axis), and the result carries `warning`
   * (STR.COLLINEAR_WARNING).  From collinearity, rotating by |delta|
   * yields exactly |delta| degrees (parallel case) or 180 - |delta|
   * (anti-parallel case) — both equal the target.
   *
   * Rejections: target outside [ANGLE_MIN, ANGLE_MAX] -> "out_of_range";
   * missing A-B or B-C edge -> "no_bond"; ring containing B-C ->
   * "ring_bond"; degenerate |A-B| or |B-C| -> "degenerate".
   *
   * PURE: NEW coords array; non-C-side rows keep byte-identical
   * references, the C side is rigid (Rodrigues rotation about B preserves
   * every internal distance), the input is never mutated.
   *
   * @param {Array<string>} symbols
   * @param {Array<Array<number>>} coords
   * @param {Array<{a: number, b: number}>} edges
   * @param {number} atomA
   * @param {number} atomB
   * @param {number} atomC
   * @param {number} targetDeg
   * @param {string|null} [moveSide] - normalized to "C" for angle edits
   * @returns {{ok: boolean, reason?: string, coords?: Array<Array<number>>,
   *            warning?: string}}
   */
  function editBondAngle(symbols, coords, edges, atomA, atomB, atomC, targetDeg, moveSide) {
    var t = +targetDeg;
    if (!isFinite(t) || t < ANGLE_MIN || t > ANGLE_MAX) {
      return { ok: false, reason: "out_of_range" };
    }
    if (moveSide != null && moveSide !== "A" && moveSide !== "B" && moveSide !== "C") {
      return { ok: false, reason: "invalid_move_side" };
    }
    if (!symbols || !coords || !edges) {
      return { ok: false, reason: "no_bond" };
    }
    if (!_hasEdge(edges, atomA, atomB) || !_hasEdge(edges, atomB, atomC)) {
      return { ok: false, reason: "no_bond" };
    }

    var fragments = fragmentsAfterCut(edges, coords.length, atomB, atomC);
    var sideA = _componentOf(fragments, atomA);
    var sideC = _componentOf(fragments, atomC);
    if (sideA && sideC && sideA === sideC) {
      return { ok: false, reason: "ring_bond" };
    }
    if (!sideA || !sideC) {
      return { ok: false, reason: "no_bond" };
    }

    var rb = coords[atomB];
    var uBA = _normalize([
      +coords[atomA][0] - +rb[0],
      +coords[atomA][1] - +rb[1],
      +coords[atomA][2] - +rb[2],
    ]);
    var uBC = _normalize([
      +coords[atomC][0] - +rb[0],
      +coords[atomC][1] - +rb[1],
      +coords[atomC][2] - +rb[2],
    ]);
    if (!uBA || !uBC) {
      return { ok: false, reason: "degenerate" };
    }

    var crossBA_BC = _cross(uBA, uBC);
    var crossNorm = Math.sqrt(_dot(crossBA_BC, crossBA_BC));
    var warning = null;
    var axis;
    if (crossNorm < COLLINEAR_EPS) {
      var cSide = [];
      for (var si = 0; si < sideC.length; si++) {
        cSide.push(coords[sideC[si]]);
      }
      axis = _stableOrthogonalAxis(uBC, _principalAxis(cSide));
      warning = STR.COLLINEAR_WARNING;
    } else {
      axis = [crossBA_BC[0] / crossNorm, crossBA_BC[1] / crossNorm, crossBA_BC[2] / crossNorm];
    }

    var current = angleDeg(coords[atomA], rb, coords[atomC]);
    var delta = (t - current) * Math.PI / 180;

    var movingSet = {};
    for (var mi = 0; mi < sideC.length; mi++) movingSet[sideC[mi]] = true;

    var out = [];
    for (var i = 0; i < coords.length; i++) {
      if (movingSet[i]) {
        var v = [+coords[i][0] - +rb[0], +coords[i][1] - +rb[1], +coords[i][2] - +rb[2]];
        var rotated = _rotateRodrigues(v, axis, delta);
        out.push([rotated[0] + +rb[0], rotated[1] + +rb[1], rotated[2] + +rb[2]]);
      } else {
        out.push(coords[i]);
      }
    }
    var result = { ok: true, coords: out };
    if (warning) result.warning = warning;
    return result;
  }

  function _hasEdge(edges, x, y) {
    for (var i = 0; i < edges.length; i++) {
      var e = edges[i];
      if ((e.a === x && e.b === y) || (e.a === y && e.b === x)) return true;
    }
    return false;
  }

  /* ---- dihedral edit (todo 35) ---- */

  /**
   * Signed dihedral A-B-C-D in degrees, normalized to (-180, 180]
   * (praxeolitic formula).  Convention: the angle between plane A-B-C and
   * plane B-C-D measured looking down the B -> C axis; POSITIVE when the
   * far bond (C-D) appears rotated CLOCKWISE relative to the near bond
   * (B-A)... equivalently, with b1 = normalize(C - B), the projection of
   * the B -> A direction onto the plane orthogonal to b1 is v, the
   * projection of the C -> D direction is w, and
   *   dihedral = atan2((b1 x v) . w, v . w)
   * Rotating the C-side about +b1 by delta ADDS delta to the dihedral
   * (verified numerically in the todo-35 tests).
   *
   * @param {Array<number>} pA
   * @param {Array<number>} pB
   * @param {Array<number>} pC
   * @param {Array<number>} pD
   * @returns {number} degrees in (-180, 180]; 0 for degenerate input
   */
  function dihedralDeg(pA, pB, pC, pD) {
    var b0 = [+pA[0] - +pB[0], +pA[1] - +pB[1], +pA[2] - +pB[2]];
    var b1 = _normalize([+pC[0] - +pB[0], +pC[1] - +pB[1], +pC[2] - +pB[2]]);
    var b2 = [+pD[0] - +pC[0], +pD[1] - +pC[1], +pD[2] - +pC[2]];
    if (!b1) return 0;
    var d0 = _dot(b0, b1);
    var d2 = _dot(b2, b1);
    var v = [b0[0] - d0 * b1[0], b0[1] - d0 * b1[1], b0[2] - d0 * b1[2]];
    var w = [b2[0] - d2 * b1[0], b2[1] - d2 * b1[1], b2[2] - d2 * b1[2]];
    var b1xv = _cross(b1, v);
    return Math.atan2(_dot(b1xv, w), _dot(v, w)) * 180 / Math.PI;
  }

  /**
   * Normalize an angle to (-180, 180] degrees.  -180 maps to +180 (the
   * interval is half-open on the negative side).
   *
   * @param {number} deg
   * @returns {number}
   */
  function normalizeDihedral(deg) {
    var d = +deg;
    if (!isFinite(d)) return 0;
    d = d % 360;
    if (d > 180) d -= 360;
    else if (d <= -180) d += 360;
    return d;
  }

  /**
   * Edit the A-B-C-D dihedral to `targetDeg` by rigidly rotating the
   * C-side fragment about the B-C axis.  The target is normalized to
   * (-180, 180] first; the rotation delta = normalizeDihedral(target -
   * current) is the SHORTEST rotation (|delta| <= 180, never a 340°
   * scenic route).  The A-side (atoms A and B) NEVER moves — for dihedral
   * edits the C/D side is the only rotatable side, so `moveSide` is
   * normalized to "C" exactly like the angle edit (anything but
   * "A"/"B"/"C"/null rejects invalid_move_side).
   *
   * Atom ordering and bond topology are untouched: the returned array has
   * the same length and index mapping, unmoved rows are the SAME
   * references, and no edge data is modified.
   *
   * Rejections: missing A-B, B-C, or C-D edge -> "no_bond"; ring
   * containing B-C (graph still connected after the cut) -> "ring_bond";
   * degenerate |B-C| -> "degenerate".
   *
   * PURE: NEW coords array; C-side rigid (rotation about the B-C line
   * preserves every internal distance; C itself lies ON the axis), input
   * never mutated.
   *
   * @param {Array<string>} symbols
   * @param {Array<Array<number>>} coords
   * @param {Array<{a: number, b: number}>} edges
   * @param {number} atomA
   * @param {number} atomB
   * @param {number} atomC
   * @param {number} atomD
   * @param {number} targetDeg
   * @param {string|null} [moveSide] - normalized to "C" for dihedral edits
   * @returns {{ok: boolean, reason?: string, coords?: Array<Array<number>>}}
   */
  function editDihedral(symbols, coords, edges, atomA, atomB, atomC, atomD, targetDeg, moveSide) {
    var target = normalizeDihedral(targetDeg);
    if (moveSide != null && moveSide !== "A" && moveSide !== "B" && moveSide !== "C") {
      return { ok: false, reason: "invalid_move_side" };
    }
    if (!symbols || !coords || !edges) {
      return { ok: false, reason: "no_bond" };
    }
    if (!_hasEdge(edges, atomA, atomB) || !_hasEdge(edges, atomB, atomC) ||
        !_hasEdge(edges, atomC, atomD)) {
      return { ok: false, reason: "no_bond" };
    }

    var fragments = fragmentsAfterCut(edges, coords.length, atomB, atomC);
    var sideA = _componentOf(fragments, atomA);
    var sideC = _componentOf(fragments, atomC);
    if (sideA && sideC && sideA === sideC) {
      return { ok: false, reason: "ring_bond" };
    }
    if (!sideA || !sideC) {
      return { ok: false, reason: "no_bond" };
    }

    var rb = coords[atomB];
    var rc = coords[atomC];
    var axis = _normalize([+rc[0] - +rb[0], +rc[1] - +rb[1], +rc[2] - +rb[2]]);
    if (!axis) {
      return { ok: false, reason: "degenerate" };
    }

    var current = dihedralDeg(coords[atomA], rb, rc, coords[atomD]);
    /* shortest rotation taking current -> target: |delta| <= 180 */
    var delta = normalizeDihedral(target - current) * Math.PI / 180;

    var movingSet = {};
    for (var mi = 0; mi < sideC.length; mi++) movingSet[sideC[mi]] = true;

    var out = [];
    for (var i = 0; i < coords.length; i++) {
      if (movingSet[i]) {
        var v = [+coords[i][0] - +rb[0], +coords[i][1] - +rb[1], +coords[i][2] - +rb[2]];
        var rotated = _rotateRodrigues(v, axis, delta);
        out.push([rotated[0] + +rb[0], rotated[1] + +rb[1], rotated[2] + +rb[2]]);
      } else {
        out.push(coords[i]);
      }
    }
    return { ok: true, coords: out };
  }

  /**
   * Orchestration for a dihedral edit on the currently displayed entry
   * (mirrors applyBondAngleEdit; viewer push deferred to todo 36).
   *
   * @param {number} atomA
   * @param {number} atomB
   * @param {number} atomC
   * @param {number} atomD
   * @param {number} targetDeg
   * @param {string|null} [moveSide] - normalized to "C"
   * @returns {{ok: boolean, reason?: string, coords?: Array<Array<number>>}}
   */
  function applyDihedralEdit(atomA, atomB, atomC, atomD, targetDeg, moveSide) {
    if (locked) return { ok: false, reason: "locked" };
    var svState = (typeof window !== "undefined" && window.ACPStructureViewer)
      ? window.ACPStructureViewer.state
      : null;
    if (!svState || !svState.displayedCoords || !svState.displayedSymbols) {
      return { ok: false, reason: "no_structure" };
    }
    var graph = editorState.graph || buildGraphFromCurrentEntry();
    if (!graph) return { ok: false, reason: "no_structure" };

    var result = editDihedral(
      svState.displayedSymbols, svState.displayedCoords, graph.edges,
      atomA, atomB, atomC, atomD, targetDeg, moveSide
    );
    if (result.ok) {
      editorState.pendingEdit = {
        type: "dihedral",
        atomA: atomA,
        atomB: atomB,
        atomC: atomC,
        atomD: atomD,
        target: normalizeDihedral(targetDeg),
        moveSide: "C",
        coords: result.coords,
      };
    }
    return result;
  }

  /**
   * Orchestration for a bond-angle edit on the currently displayed entry
   * (mirrors applyBondLengthEdit; viewer push deferred to todo 36).
   *
   * @param {number} atomA
   * @param {number} atomB
   * @param {number} atomC
   * @param {number} targetDeg
   * @param {string|null} [moveSide] - normalized to "C"
   * @returns {{ok: boolean, reason?: string, coords?: Array<Array<number>>,
   *            warning?: string}}
   */
  function applyBondAngleEdit(atomA, atomB, atomC, targetDeg, moveSide) {
    if (locked) return { ok: false, reason: "locked" };
    var svState = (typeof window !== "undefined" && window.ACPStructureViewer)
      ? window.ACPStructureViewer.state
      : null;
    if (!svState || !svState.displayedCoords || !svState.displayedSymbols) {
      return { ok: false, reason: "no_structure" };
    }
    var graph = editorState.graph || buildGraphFromCurrentEntry();
    if (!graph) return { ok: false, reason: "no_structure" };

    var result = editBondAngle(
      svState.displayedSymbols, svState.displayedCoords, graph.edges,
      atomA, atomB, atomC, targetDeg, moveSide
    );
    if (result.ok) {
      editorState.pendingEdit = {
        type: "bond_angle",
        atomA: atomA,
        atomB: atomB,
        atomC: atomC,
        target: +targetDeg,
        moveSide: "C",
        coords: result.coords,
      };
      if (result.warning) {
        editorState.pendingEdit.warning = result.warning;
      }
    }
    return result;
  }

  /**
   * Orchestration for a bond-length edit on the currently displayed
   * entry: respects the animation lock, reads displayed coords/symbols +
   * the cached graph, runs the pure edit, and STAGES the result in
   * editorState.pendingEdit (the viewer preview/apply pipeline lands in
   * todo 36 — nothing is pushed to the display here).
   *
   * @param {number} atomA
   * @param {number} atomB
   * @param {number} targetLength
   * @param {string|null} [moveSide]
   * @returns {{ok: boolean, reason?: string, coords?: Array<Array<number>>}}
   */
  function applyBondLengthEdit(atomA, atomB, targetLength, moveSide) {
    if (locked) return { ok: false, reason: "locked" };
    var svState = (typeof window !== "undefined" && window.ACPStructureViewer)
      ? window.ACPStructureViewer.state
      : null;
    if (!svState || !svState.displayedCoords || !svState.displayedSymbols) {
      return { ok: false, reason: "no_structure" };
    }
    var graph = editorState.graph || buildGraphFromCurrentEntry();
    if (!graph) return { ok: false, reason: "no_structure" };

    var result = editBondLength(
      svState.displayedSymbols, svState.displayedCoords, graph.edges,
      atomA, atomB, targetLength, moveSide
    );
    if (result.ok) {
      var fragments = fragmentsAfterCut(graph.edges, svState.displayedCoords.length, atomA, atomB);
      editorState.pendingEdit = {
        type: "bond_length",
        atomA: atomA,
        atomB: atomB,
        target: +targetLength,
        moveSide: resolveMoveSide(fragments, atomA, atomB, moveSide),
        coords: result.coords,
      };
    }
    return result;
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
    editBondLength: editBondLength,
    editBondAngle: editBondAngle,
    editDihedral: editDihedral,
    dihedralDeg: dihedralDeg,
    normalizeDihedral: normalizeDihedral,
    angleDeg: angleDeg,
    _principalAxis: _principalAxis,
    resolveMoveSide: resolveMoveSide,
    defaultMoveSide: defaultMoveSide,
    applyBondLengthEdit: applyBondLengthEdit,
    applyBondAngleEdit: applyBondAngleEdit,
    applyDihedralEdit: applyDihedralEdit,
  };
})();
