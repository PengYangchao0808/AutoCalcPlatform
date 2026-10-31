/* ACP 3.0 — A-62 theme + narrow-screen static audit for the Next modules.
 *
 * Authority: docs/reference/next-change/ACP_3.0_Frontend_Plan_20261031.md
 *   §5 / A-62 “主题与窄屏 — 继承 shell 主题变量”.
 *
 * This is a static CSS audit: it reads stylesheets only and never boots a
 * module. It pins the A-62 contract:
 *
 *   1. Theme tokens are declared exactly once — base.css `:root` (classic
 *      palette) and shell.css `.acp-shell` (shell-only tokens). A module
 *      stylesheet may not define `:root`/`.acp-shell` tokens (no duplicate
 *      palette); a local custom property is allowed only as a pure alias of a
 *      theme token (reaction-network's `--net-*` role palette).
 *   2. A module stylesheet may not introduce a new hardcoded color. A color
 *      literal is allowed only when it is either
 *        - the fallback of `var(--<defined token>, <literal>)` (the token wins
 *          on the Next entry, where base.css + shell.css are always loaded),
 *          or
 *        - exactly a value already declared by a base/shell token.
 *      Bare hex / rgb() / hsl() / named colors are rejected.
 *   3. Every `var(--x)` reference resolves to a base token, a shell token or a
 *      local module alias. Undefined names (e.g. `--warning`, `--text-muted`,
 *      `--bg-overlay`) fail instead of silently rendering a fallback.
 *   4. Every module carries a `@media (max-width: 720px)` narrow rule; modules
 *      that own a drawer / table / dialog must restructure to a single column
 *      (or a bottom sheet) inside that block.
 *   5. No module or shell-panel rule may force a fixed width wider than the
 *      390px audit viewport: `width`/`min-width` in px > 390 (or vw > 100)
 *      fails. `max-width`, `min()` and `clamp()` clamps are allowed.
 *   6. shell.css carries the shared 720px shell-panel rules (brand ellipsis,
 *      wrapping actions, full-width panels, single-column layout dialog).
 */
"use strict";

const fs = require("fs");
const path = require("path");

module.exports.register = async function (h) {
  const { test, assert, assertEq } = h;

  const FRONTEND = path.resolve(__dirname, "..", "..");
  const BASE_CSS = path.join(FRONTEND, "css", "workbench", "base.css");
  const SHELL_CSS = path.join(FRONTEND, "css", "shell", "shell.css");
  const MODULE_DIR = path.join(FRONTEND, "css", "modules");

  // The narrow-screen audit viewport (iPhone-class 390px). Fixed widths above
  // this force a horizontal scrollbar in the smallest supported layout.
  const NARROW_VIEWPORT = 390;

  // New-module stylesheets loaded by frontend/next/index.html. `drawer`/`table`
  // marks hosts that must make an explicit single-column (or sheet) move at
  // ≤720px; every other module still has to carry the narrow rule.
  const MODULES = [
    { file: "task-explorer.css", drawer: true, table: true },
    { file: "exec-detail.css", drawer: false, table: true },
    { file: "molecule-viewer.css", drawer: true, table: false },
    { file: "task-config.css", drawer: false, table: true },
    { file: "task-wizard.css", drawer: true, table: false },
    { file: "nmr-report.css", drawer: false, table: true },
    { file: "s2-scan.css", drawer: true, table: false },
    { file: "job-edit.css", drawer: false, table: true },
    { file: "mechanism-plan.css", drawer: false, table: true },
    { file: "research-record.css", drawer: false, table: true },
    { file: "approval-card.css", drawer: false, table: false },
    { file: "node-status.css", drawer: false, table: true },
    { file: "capability-form.css", drawer: false, table: false },
    { file: "energy-profile.css", drawer: true, table: false },
    { file: "tsmode-editor.css", drawer: false, table: false },
    { file: "reaction-network.css", drawer: false, table: false },
    { file: "workspace-details.css", drawer: false, table: false },
  ];

  function read(file) { return fs.readFileSync(file, "utf8"); }

  function stripComments(css) {
    return css.replace(/\/\*[\s\S]*?\*\//g, "");
  }

  // Return the body of the first top-level block whose selector line contains
  // `selector`. Braces are counted, so nested rules are included verbatim.
  function blockBody(css, selector) {
    const start = css.indexOf(selector);
    if (start === -1) return "";
    const open = css.indexOf("{", start);
    if (open === -1) return "";
    let depth = 0;
    for (let i = open; i < css.length; i++) {
      if (css[i] === "{") depth++;
      else if (css[i] === "}") {
        depth--;
        if (depth === 0) return css.slice(open + 1, i);
      }
    }
    return "";
  }

  function declarations(body) {
    const out = {};
    // Zero-width lookbehind keeps the `;`/`{` boundary available for the next
    // declaration; the leading `;` covers the first declaration of the block.
    // Selector pseudo-classes such as `.row--cancel:hover` are not preceded by
    // a declaration boundary and can never be mistaken for a token.
    const re = /(?<=[;{])\s*(--[a-zA-Z0-9-]+)\s*:\s*([^;{}]+);/g;
    const text = ";" + body;
    let m;
    while ((m = re.exec(text))) out[m[1]] = m[2].trim();
    return out;
  }

  function normalizeLiteral(value) {
    return value.replace(/\s+/g, "").toLowerCase();
  }

  function literalsIn(text) {
    const out = [];
    let m;
    const hex = /#[0-9a-fA-F]{3,8}\b/g;
    while ((m = hex.exec(text))) out.push(m[0]);
    const fn = /\b(?:rgba?|hsla?)\(\s*(?=[\d.+-])/gi;
    while ((m = fn.exec(text))) {
      // capture the whole function call for the token-value comparison
      let depth = 0;
      let end = m.index;
      for (; end < text.length; end++) {
        if (text[end] === "(") depth++;
        else if (text[end] === ")") {
          depth--;
          if (depth === 0) { end++; break; }
        }
      }
      out.push(text.slice(m.index, end));
    }
    return out;
  }

  const baseRaw = stripComments(read(BASE_CSS));
  const shellRaw = stripComments(read(SHELL_CSS));
  const baseTokens = declarations(blockBody(baseRaw, ":root"));
  const shellTokens = declarations(blockBody(shellRaw, ".acp-shell"));
  const tokenNames = new Set([...Object.keys(baseTokens), ...Object.keys(shellTokens)]);

  // Literal values already declared by a base/shell token are part of the
  // theme, not a new hardcoded color.
  const tokenValues = new Set();
  for (const value of [...Object.values(baseTokens), ...Object.values(shellTokens)]) {
    for (const literal of literalsIn(value)) tokenValues.add(normalizeLiteral(literal));
  }

  // Remove `var(--defined-token, <color literal>)` fallbacks before scanning:
  // the token value wins wherever base.css/shell.css are loaded (the Next
  // entry always loads both), so the fallback is not a new palette entry.
  function maskTokenFallbacks(css) {
    const re = /var\(\s*(--[a-zA-Z0-9-]+)\s*,\s*(?:#[0-9a-fA-F]{3,8}|rgba?\([^)]*\)|hsla?\([^)]*\))\s*\)/gi;
    return css.replace(re, (whole, name) => (tokenNames.has(name) ? "" : whole));
  }

  // Pre-A-62 fallback literals that predate the token layer. They never render
  // on the Next entry (base.css + shell.css always load and win), but they are
  // frozen here so a NEW hardcoded color cannot be smuggled in through a
  // `var(--token, <literal>)` fallback. Values already declared by a token are
  // allowed without an entry.
  const LEGACY_FALLBACK_LITERALS = new Set([
    "#0f151d", "#131a23", "#16273a", "#4ac08a", "#4fbf7f", "#65748b",
    "#74859a", "#9fb0c3", "#d9a441", "#dce3ec", "#e0a94a", "#e66a6a",
    "rgba(78,161,255,0.14)", "rgba(78,161,255,0.16)",
  ]);

  const moduleCss = {};
  for (const mod of MODULES) moduleCss[mod.file] = stripComments(read(path.join(MODULE_DIR, mod.file)));

  await test("A-62: theme tokens are declared once and modules define no global palette", function () {
    // Scope guard: the audit covers the A-62 module list explicitly. Files
    // still under construction by sibling batches (e.g. path-view.css from
    // A-50) are intentionally outside this list until they are linked by
    // frontend/next/index.html and registered here.
    for (const mod of MODULES) {
      assert(fs.existsSync(path.join(MODULE_DIR, mod.file)), "audited module CSS exists: " + mod.file);
    }

    // base.css owns the classic palette, shell.css the shell-only tokens.
    assert(Object.keys(baseTokens).length >= 20, "base.css :root palette present");
    assert(Object.keys(shellTokens).length >= 8, "shell.css .acp-shell tokens present");
    const overlap = Object.keys(baseTokens).filter((name) => shellTokens[name] !== undefined);
    assertEq(overlap, [], "no token name is defined in both base.css and shell.css");
    for (const name of ["--accent-rgb", "--green-rgb", "--amber-rgb", "--red-rgb", "--bg-base-rgb", "--black-rgb"]) {
      assert(baseTokens[name] !== undefined, "base.css declares " + name + " once");
    }
    assert(shellTokens["--shell-violet-rgb"] !== undefined, "shell.css declares --shell-violet-rgb");

    for (const mod of MODULES) {
      const css = moduleCss[mod.file];
      assert(css.indexOf(":root") === -1, mod.file + " must not redefine :root");
      assert(css.indexOf(".acp-shell {") === -1, mod.file + " must not redefine .acp-shell tokens");

      // Any local custom property must be a pure alias of theme tokens, with a
      // module-scoped name that cannot collide with base/shell tokens.
      const local = declarations(css);
      for (const [name, value] of Object.entries(local)) {
        assert(!tokenNames.has(name), mod.file + " duplicates theme token " + name);
        assert(/^--(net|mod|acp)-/.test(name), mod.file + " local token " + name + " is module-scoped");
        assert(value.indexOf("var(") !== -1, mod.file + " local token " + name + " must alias a theme token");
      }
    }
  });

  await test("A-62: module CSS introduces no hardcoded colors (token values and defined-var fallbacks allowed)", function () {
    const named = /\b(white|black|silver|gray|grey|red|green|blue|orange|yellow|purple)\b/gi;
    for (const mod of MODULES) {
      const raw = moduleCss[mod.file];
      const scan = maskTokenFallbacks(raw);
      const violations = [];

      // A fallback literal must be a theme value or one of the frozen legacy
      // values — otherwise it is a new hardcoded color hidden in a fallback.
      const fallbackRe = /var\(\s*--[a-zA-Z0-9-]+\s*,\s*(#[0-9a-fA-F]{3,8}|rgba?\(\s*\d[^)]*\)|hsla?\(\s*\d[^)]*\))\s*\)/gi;
      let fallback;
      while ((fallback = fallbackRe.exec(raw))) {
        const literal = normalizeLiteral(fallback[1]);
        if (!tokenValues.has(literal) && !LEGACY_FALLBACK_LITERALS.has(literal)) {
          violations.push("fallback " + fallback[1]);
        }
      }

      for (const literal of literalsIn(scan)) {
        if (!tokenValues.has(normalizeLiteral(literal))) violations.push(literal);
      }

      // Named colors are only a violation when used as a property value, never
      // inside a selector/comment (comments are already stripped) and never
      // inside a `var(--token)` reference such as `var(--red)` / `--white-rgb`.
      const namedScan = scan.replace(/var\([^)]*\)/g, "var()");
      const namedRe = /\b(color|background(?:-color)?|border(?:-[a-z]+)?-color|border|outline-color|fill|stroke|box-shadow)\s*:\s*([^;{}]+)/g;
      let m;
      while ((m = namedRe.exec(namedScan))) {
        const hit = m[2].match(named);
        if (hit) violations.push(m[1] + ": " + hit[0]);
      }

      assertEq(violations, [], mod.file + " hardcoded color literals");
    }
  });

  await test("A-62: every var() reference resolves to a base, shell or local module token", function () {
    for (const mod of MODULES) {
      const css = moduleCss[mod.file];
      const local = new Set(Object.keys(declarations(css)));
      const unknown = [];
      const re = /var\(\s*(--[a-zA-Z0-9-]+)/g;
      let m;
      while ((m = re.exec(css))) {
        if (!tokenNames.has(m[1]) && !local.has(m[1])) unknown.push(m[1]);
      }
      assertEq([...new Set(unknown)], [], mod.file + " references undefined theme tokens");
    }
  });

  // ── narrow screen ────────────────────────────────────────────────────────
  function narrowBlock(css) {
    const start = css.indexOf("@media (max-width: 720px)");
    if (start === -1) return null;
    const open = css.indexOf("{", start);
    let depth = 0;
    for (let i = open; i < css.length; i++) {
      if (css[i] === "{") depth++;
      else if (css[i] === "}") {
        depth--;
        if (depth === 0) return css.slice(open + 1, i);
      }
    }
    return null;
  }

  await test("A-62: every module has a ≤720px rule and drawers/tables restructure to one column", function () {
    const singleColumn = /grid-template-columns\s*:[^;}]*(?:1fr|auto)|flex-direction\s*:\s*column|width\s*:\s*(?:auto|100%)|max-width\s*:\s*none/;
    for (const mod of MODULES) {
      const css = moduleCss[mod.file];
      const block = narrowBlock(css);
      assert(block !== null, mod.file + " has a @media (max-width: 720px) rule");
      assert(block.trim().length > 0, mod.file + " 720px rule is not empty");
      if (!mod.drawer && !mod.table) continue;
      assert(singleColumn.test(block),
        mod.file + " 720px rule must collapse to a single column / bottom sheet");
    }
  });

  function widthViolations(css) {
    const out = [];
    const re = /(?:^|[;{])\s*(width|min-width)\s*:\s*(-?\d+(?:\.\d+)?)(px|vw|rem|em)\b/gi;
    let m;
    while ((m = re.exec(css))) {
      const value = Number(m[2]);
      const px = m[3].toLowerCase() === "px" ? value
        : m[3].toLowerCase() === "vw" ? (value / 100) * NARROW_VIEWPORT
          : value * 16; // rem/em at the 16px root size
      if (px > NARROW_VIEWPORT) out.push(m[1] + ": " + m[2] + m[3]);
    }
    return out;
  }

  await test("A-62: no module or shell panel forces a fixed width wider than the 390px viewport", function () {
    for (const mod of MODULES) {
      assertEq(widthViolations(moduleCss[mod.file]), [], mod.file + " fixed width > 100vw");
    }
    assertEq(widthViolations(shellRaw), [], "shell.css fixed width > 100vw");
  });

  await test("A-62: shell.css carries the shared 720px shell-panel rules", function () {
    const block = narrowBlock(shellRaw);
    assert(block !== null, "shell.css has a @media (max-width: 720px) block");
    for (const needle of [
      ".acp-shell-actions", "flex-wrap", ".acp-shell-brand-copy h1", "text-overflow: ellipsis",
      ".acp-panel-body", ".acp-panel-instance", ".acp-layout-dialog label", "grid-template-columns",
    ]) {
      assert(block.indexOf(needle) !== -1, "shell 720px rule covers " + needle);
    }
    // The pre-existing ≤1023px stack (side/bottom under main) must stay intact.
    assert(shellRaw.indexOf("@media (max-width: 1023px)") !== -1, "shell keeps its ≤1023px stack");
  });
};
