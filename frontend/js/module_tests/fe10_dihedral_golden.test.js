/*
 * FE-10 BEHAVIOR-FROZEN golden samples — the two dihedral implementations.
 *
 * ⚠ BEHAVIOR-FROZEN：本文件固化的是**当前**数值行为，不是期望语义。
 *   被固化的两侧：
 *   (a) s2scanDist / s2scanAngle / s2scanDihedral（frontend/js/workbench/
 *       features/scan-s2.js，运行时逐字抽取源码后 vm 求值）——A→B 键矢量
 *       投影约定，裸 atan2 输出（-180, 180]，无归一化，中轴退化返 NaN；
 *   (b) computeMeasurementValue("dihedral"|"angle"|"distance")
 *       （frontend/js/geometry_store.js，纯逻辑无 DOM，整文件加载）——
 *       IUPAC 扭角，normalizeDihedral 归一化到 (-180, 180]，退化/缺坐标返 null。
 *
 * 目的：把两侧已知的恒定 180° 语义分歧变为可执行证据（背景与数学推导见
 * docs/reference/next-change/ACP_Frontend_FE10_Dihedral_Verification_20261016.md
 * §2/§3/§6）。**任何一侧被 pin 的数值发生变化都应使本测试变红并触发
 * FE-10 契约评审；不得为通过测试而单侧改数**，也不得在无关清理中顺手
 * "修正"任一侧实现。
 *
 * 技术说明：S2 三函数是纯函数（仅依赖 Math），从 scan-s2.js 运行时按
 * marker + brace-match 抽取（同 wizard_candidate_api.test.js 的手法），
 * 保证 pin 的是真实生产源码；geometry_store.js 头部声明 pure/no-DOM，
 * 整文件在 sandbox 加载后取 window.ACPGeometryStore.computeMeasurementValue。
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const TOL = 1e-6;

/** Brace-matched extraction of one top-level function from `source`. */
function extractFunction(source, marker) {
  const start = source.indexOf(marker);
  if (start < 0) throw new Error("FE10 golden: marker missing from source: " + marker);
  let depth = 0;
  for (let i = start + marker.length - 1; i < source.length; i++) {
    const ch = source[i];
    if (ch === "{") depth++;
    else if (ch === "}") {
      depth--;
      if (depth === 0) return source.slice(start, i + 1);
    }
  }
  throw new Error("FE10 golden: closing brace not found for " + marker);
}

function approx(actual, expected, tol, msg) {
  if (typeof actual !== "number" || !isFinite(actual) || Math.abs(actual - expected) > tol) {
    throw new Error((msg || "approx") + ": expected " + expected + " (+/-" + tol + "), got " + actual);
  }
}

/** Map degrees onto [0, 360). */
function mod360(deg) { return ((deg % 360) + 360) % 360; }

const pt = (x, y, z) => ({ x, y, z });
/** Atom-id indexed coordinate rows for computeMeasurementValue. */
const rowsOf = (pts) => pts.map((p) => [p.x, p.y, p.z]);

// 5 个实测黄金样例（所有样例 B–C 轴均为 +y）。s2/gs 为 2026-10-17 实测值。
const GOLDEN_DIHEDRALS = [
  { name: "audit sample (a=-x, d=+z)", pts: [pt(-1, 0, 0), pt(0, 0, 0), pt(0, 1, 0), pt(0, 1, 1)], s2: -90, gs: 90 },
  { name: "flipped A (a=+x, d=+z)", pts: [pt(1, 0, 0), pt(0, 0, 0), pt(0, 1, 0), pt(0, 1, 1)], s2: 90, gs: -90 },
  { name: "planar anti (a=-x, d=+x)", pts: [pt(-1, 0, 0), pt(0, 0, 0), pt(0, 1, 0), pt(1, 1, 0)], s2: 0, gs: 180 },
  { name: "planar syn (a=-x, d=-x)", pts: [pt(-1, 0, 0), pt(0, 0, 0), pt(0, 1, 0), pt(-1, 1, 0)], s2: 180, gs: 0 },
  { name: "generic (d rotated +45 deg)", pts: [pt(-1, 0, 0), pt(0, 0, 0), pt(0, 1, 0), pt(0.7071, 1, 0.7071)], s2: -45, gs: 135 },
];

module.exports.register = async function (h) {
  const { test, assert, assertEq, makeSandbox, loadScript } = h;

  // ---- S2 side: extract the three pure geometry helpers from the shipped source ----
  const scanS2Source = fs.readFileSync(
    path.resolve(__dirname, "..", "workbench", "features", "scan-s2.js"), "utf8");
  const s2Decls = [
    "function s2scanDist(a, b) {",
    "function s2scanAngle(a, b, c) {",
    "function s2scanDihedral(a, b, c, d) {",
  ].map((marker) => extractFunction(scanS2Source, marker));
  const s2Box = makeSandbox();
  vm.runInContext(s2Decls.join("\n"), vm.createContext(s2Box),
    { filename: "scan-s2.js#s2scan-geometry" });
  const s2scanDist = s2Box.s2scanDist;
  const s2scanAngle = s2Box.s2scanAngle;
  const s2scanDihedral = s2Box.s2scanDihedral;

  // ---- GeometryStore side: documented pure/no-DOM module, loaded wholesale ----
  const gsSandbox = makeSandbox();
  loadScript("js/geometry_store.js", gsSandbox);
  const store = gsSandbox.window.ACPGeometryStore;
  const gsValue = (type, pts, ids) => store.computeMeasurementValue(type, rowsOf(pts), ids);
  const gsDihedral = (pts) => gsValue("dihedral", pts, [0, 1, 2, 3]);

  await test("FE10 fixtures: real S2 source extracted; GeometryStore loads and exposes the API", () => {
    assertEq(s2Decls.length, 3);
    // The pinned dihedral must be the raw projection/atan2 formula, not an
    // IUPAC-normalized variant — gaining normalization is a FE-10 contract
    // change and must turn this suite red.
    const dihedralSrc = s2Decls[2];
    assert(dihedralSrc.includes("Math.atan2(y, x) * 180 / Math.PI"),
      "s2scanDihedral must keep its raw atan2 projection form");
    assert(!dihedralSrc.includes("normalizeDihedral"),
      "s2scanDihedral must not silently gain normalization");
    assert(typeof s2scanDihedral === "function", "s2scanDihedral must be callable");
    assert(typeof store.computeMeasurementValue === "function",
      "window.ACPGeometryStore.computeMeasurementValue must be callable");
  });

  await test("S2 golden dihedral pins (BEHAVIOR-FROZEN, A->B projection convention)", () => {
    for (const c of GOLDEN_DIHEDRALS) {
      approx(s2scanDihedral.apply(null, c.pts), c.s2, TOL, "S2 " + c.name);
    }
  });

  await test("GeometryStore golden dihedral pins (BEHAVIOR-FROZEN, IUPAC (-180, 180])", () => {
    for (const c of GOLDEN_DIHEDRALS) {
      const gs = gsDihedral(c.pts);
      assert(typeof gs === "number", "GS " + c.name + ": must return a number, got " + gs);
      approx(gs, c.gs, TOL, "GS " + c.name);
      assert(gs > -180 && gs <= 180, "GS " + c.name + ": value must stay in (-180, 180]");
    }
  });

  await test("constant offset identity: s2 + 180 == gs (mod 360) for all 5 golden cases", () => {
    for (const c of GOLDEN_DIHEDRALS) {
      const s2 = s2scanDihedral.apply(null, c.pts);
      const gs = gsDihedral(c.pts);
      approx(mod360(s2 + 180 - gs), 0, TOL, "identity " + c.name);
    }
    // The offset is a constant 180° translation, NOT a sign flip: the planar
    // anti/syn cases pin S2 0↔GS 180 and S2 180↔GS 0 respectively.
    approx(mod360(GOLDEN_DIHEDRALS[2].s2 + 180 - GOLDEN_DIHEDRALS[2].gs), 0, TOL);
    approx(mod360(GOLDEN_DIHEDRALS[3].s2 + 180 - GOLDEN_DIHEDRALS[3].gs), 0, TOL);
  });

  await test("degenerate zero central bond (b == c): GS null, S2 NaN", () => {
    const pts = [pt(-1, 0, 0), pt(0, 0, 0), pt(0, 0, 0), pt(0, 1, 1)];
    assertEq(gsDihedral(pts), null, "GS must return null on degenerate central axis");
    assert(Number.isNaN(s2scanDihedral.apply(null, pts)),
      "S2 must keep returning NaN (0/0) on a degenerate central axis");
  });

  await test("degenerate collinear mid-axis: GS null; S2 silently returns 0 (pinned as-is)", () => {
    // a,b,c collinear -> projection v = 0 -> atan2(0, 0) = 0 on the S2 side:
    // NOT NaN. This silent-0 is part of the frozen behavior.
    const collinearABC = [pt(0, 0, 0), pt(1, 0, 0), pt(2, 0, 0), pt(2, 1, 1)];
    assertEq(gsDihedral(collinearABC), null);
    approx(s2scanDihedral.apply(null, collinearABC), 0, TOL, "S2 collinear a,b,c");
    // b,c,d collinear -> projection w = 0 -> x = y = 0 -> atan2(0, 0) = 0.
    const collinearBCD = [pt(-1, 0, 0), pt(0, 0, 0), pt(1, 0, 0), pt(2, 0, 0)];
    assertEq(gsDihedral(collinearBCD), null);
    approx(s2scanDihedral.apply(null, collinearBCD), 0, TOL, "S2 collinear b,c,d");
  });

  await test("GeometryStore null semantics: missing coordinate row -> null", () => {
    // Atom id 3 has no coordinate row; S2 has no equivalent guard (it takes
    // point objects directly), GS null-safety is part of its contract.
    const missing = [[-1, 0, 0], [0, 0, 0], [0, 1, 0]];
    assertEq(store.computeMeasurementValue("dihedral", missing, [0, 1, 2, 3]), null);
  });

  await test("angle: s2scanAngle and the GS angle branch agree (no 180 issue); pin 90 deg", () => {
    const pts = [pt(1, 0, 0), pt(0, 0, 0), pt(0, 1, 0)];
    const s2 = s2scanAngle.apply(null, pts);
    const gs = gsValue("angle", pts, [0, 1, 2]);
    approx(s2, 90, TOL, "S2 angle pin");
    approx(gs, 90, TOL, "GS angle pin");
    approx(Math.abs(s2 - gs), 0, 1e-9, "S2 vs GS angle agreement");
  });

  await test("distance: s2scanDist and the GS distance branch agree; pin 3 A", () => {
    const pts = [pt(0, 0, 0), pt(1, 2, 2)];
    const s2 = s2scanDist.apply(null, pts);
    const gs = gsValue("distance", pts, [0, 1]);
    approx(s2, 3, TOL, "S2 distance pin");
    approx(gs, 3, TOL, "GS distance pin");
    approx(Math.abs(s2 - gs), 0, 1e-9, "S2 vs GS distance agreement");
  });
};
