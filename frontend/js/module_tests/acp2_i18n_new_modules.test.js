/* ACP 3.0 — A-61 i18n key completeness for the NEW (Next) modules.
 *
 * Authority: docs/reference/next-change/ACP_3.0_Frontend_Plan_20261031.md
 *   §5 / A-61 (P24 full-key i18n zh/en) and each module README's i18n section.
 *
 * Scope: every i18n key a new module can emit must exist in BOTH shell
 * dictionaries (`zh-CN` + `en-US`) in frontend/js/shell/i18n.js.
 *
 * Per module file the test scans:
 *   1. `setAttribute("data-i18n…", …)` argument windows — catches plain
 *      literals, ternaries and concatenations;
 *   2. HTML-style `data-i18n…="key"` attributes;
 *   3. the module's inline `var ZH = {…}` fallback dictionary;
 *   4. every remaining `"next.*"` string literal (keys the module hands to
 *      `t()` directly, e.g. descriptor errorTitle/errorMessage).
 *
 * Dynamic key families (`"next.x." + suffix`) are expanded, never ignored:
 * `PREFIX_EXPANSIONS` covers the dictionary-less molecule-viewer, and for every
 * other module the prefix is expanded with the module's own ZH dictionary (the
 * concrete key set). A prefix with no expansion FAILS instead of dropping keys.
 *
 * Ownership: keys outside the new modules' namespaces must be listed in
 * `ALLOWLIST` with a reason (they are shell / view-model owned). Allowlisted
 * keys are skipped for the module completeness assertion, but they must still
 * resolve in both dictionaries and the test reports the ones no longer
 * referenced. Any unowned key that is NOT allowlisted fails with the key name.
 *
 * Pinned behaviours:
 *   - zh and en expose the same key set, no duplicated key definitions, every
 *     value a string, identical `{placeholder}` sets per key;
 *   - pre-A-61 shell keys are preserved verbatim (spot-checked);
 *   - `applyI18n()` missing-key behaviour is unchanged: a key with no real
 *     translation never clobbers mounted DOM copy, while translated keys are
 *     applied (and language switching re-applies them).
 */
"use strict";

const fs = require("fs");
const path = require("path");

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  const I18N_PATH = "js/shell/i18n.js";
  const I18N_FILE = "shell/i18n.js"; // relative to frontend/js
  // 252 pre-A-61 keys + 1 520 A-61 new-module keys (578 first batch + 74
  // task-wizard + 268 nmr/tsmode/wizard-locals + 600 job-edit/mechanism-plan/
  // research-record/path-view/pes-handoff). The floor guards the scanner
  // against silently reading an empty/moved file, not an exact count.
  const SHELL_KEY_FLOOR = 800;
  const MODULE_KEY_FLOOR = 500;

  // The new modules owned by the parallel UI batch (A-01 … A-60).
  const MODULES = [
    "task-explorer",
    "node-status",
    "approval-card",
    "capability-form",
    "exec-detail",
    "molecule-viewer",
    "task-config",
    "s2-scan",
    "task-wizard",
    "nmr-report",
    "tsmode-editor",
    "job-edit",
    "mechanism-plan",
    "research-record",
    "path-view",
    "pes-handoff",
  ];

  // Namespaces the new modules own; a scanned key must match one of these.
  const OWNED_PREFIXES = [
    "next.tasks.",
    "next.nodes.",
    "next.approval.",
    "next.capabilityForm.",
    "next.detail.",
    "next.viewer.",
    "next.config.",
    "next.s2scan.",
    "next.wizard.",
    "next.nmr.",
    "next.tsmode.",
    "next.jobedit.",
    "next.mechplan.",
    "next.record.",
    "next.pathview.",
    "next.handoff.",
  ];

  // Keys referenced from the module files but owned elsewhere. Each entry needs
  // a reason; the test reports entries that are no longer referenced.
  const ALLOWLIST = {
    "next.view.fixture":
      "acp.view.v1 fixture badge — owned by the shared view-model/shell dictionary, not by molecule-viewer",
    "shell.module.capability_form":
      "layout/registry panel title — owned by the shell module-title dictionary",
  };

  // Dynamic key families that CANNOT be derived from an inline ZH dictionary.
  // molecule-viewer has no ZH dict (inline zh literals only), so its two
  // concatenated families are enumerated here. Values are the suffix after the
  // prefix; the prefixes mirror module.js constants:
  //   MEASUREMENT_TYPES = ["distance", "angle", "dihedral"]
  //   STYLE_PRESETS     = ball-stick / stick / spacefill / wireframe / publication
  const PREFIX_EXPANSIONS = {
    "next.viewer.measure_": ["distance", "angle", "dihedral"],
    "next.viewer.style_": ["ball_stick", "stick", "spacefill", "wireframe", "publication"],
    "next.wizard.local.": [
      "electronic_title", "charge_too_high", "multiplicity_too_high",
      "resources_loading", "resources_empty", "resources_unavailable",
      "cores_too_many", "memory_too_large", "memory_format",
      "queue_hint", "queue_empty", "scope_note", "preflight_digest",
    ],
  };

  // Keys that a module documents/needs but does not yet emit through a
  // `next.*` literal or data-i18n attribute (so the source scan cannot see
  // them). task-wizard A-03/A-04/A-05 keeps its zh-only copy in a `LOCAL`
  // object (README §8); A-61 reserves the dictionary entries ahead of the
  // module switching to `next.wizard.local.*`. Asserted here so the
  // reservation cannot rot.
  const DOCUMENTED_KEYS = {
    "task-wizard": [
      "next.wizard.local.electronic_title",
      "next.wizard.local.charge_too_high",
      "next.wizard.local.multiplicity_too_high",
      "next.wizard.local.resources_loading",
      "next.wizard.local.resources_empty",
      "next.wizard.local.resources_unavailable",
      "next.wizard.local.cores_too_many",
      "next.wizard.local.memory_too_large",
      "next.wizard.local.memory_format",
      "next.wizard.local.queue_hint",
      "next.wizard.local.queue_empty",
      "next.wizard.local.scope_note",
      "next.wizard.local.preflight_digest",
    ],
  };

  // Pre-A-61 keys that must keep their exact shipped copy (no silent rewrite).
  const PRESERVED = {
    "layout.title": ["配置布局", "Configure layout"],
    "shell.app.title": ["ACP 分子计算工作台 · Next", "ACP Molecular Workbench · Next"],
    "next.tasks.title": ["任务列表", "Task list"],
    "next.config.title": ["任务配置", "Task config"],
    "next.detail.empty": ["选择任务后查看状态与证据。", "Select a task to inspect its state and evidence."],
    "next.viewer.style_ball_stick": ["球棍", "Ball & stick"],
    "next.network.title": ["反应网络", "Reaction network"],
  };

  // Intentionally empty values shipped before A-61 (ready state hides the
  // status overlay; `applyI18n` only rewrites keys with a real translation).
  const ALLOWED_EMPTY = ["next.viewer.ready", "next.energy.ready"];

  function readFrontend(rel) {
    return fs.readFileSync(path.resolve(__dirname, "..", rel), "utf8");
  }

  function moduleSource(id) {
    return fs.readFileSync(path.resolve(__dirname, "..", "modules", id, "module.js"), "utf8");
  }

  /** String/comment-aware brace slice starting at the first `{` after marker. */
  function blockAt(src, marker) {
    const start = src.indexOf(marker);
    if (start < 0) return null;
    const open = src.indexOf("{", start);
    if (open < 0) return null;
    let depth = 0;
    let quote = null;
    let escaped = false;
    let lineComment = false;
    let blockComment = false;
    for (let i = open; i < src.length; i++) {
      const ch = src[i];
      const next = src[i + 1];
      if (lineComment) { if (ch === "\n") lineComment = false; continue; }
      if (blockComment) { if (ch === "*" && next === "/") { blockComment = false; i++; } continue; }
      if (quote) {
        if (escaped) escaped = false;
        else if (ch === "\\") escaped = true;
        else if (ch === quote) quote = null;
        continue;
      }
      if (ch === "/" && next === "/") { lineComment = true; i++; continue; }
      if (ch === "/" && next === "*") { blockComment = true; i++; continue; }
      if (ch === "\"" || ch === "'" || ch === "`") { quote = ch; continue; }
      if (ch === "{") depth++;
      else if (ch === "}") {
        depth--;
        if (depth === 0) return src.slice(open, i + 1);
      }
    }
    return null;
  }

  /** Load frontend/js/shell/i18n.js in a fresh vm sandbox. */
  function loadI18n(overrides) {
    const sandbox = makeSandbox(overrides || {});
    loadScript(I18N_PATH, sandbox);
    return { i18n: sandbox.ACPShell.i18n, sandbox };
  }

  function dictionaries() {
    const { i18n } = loadI18n();
    return { zh: i18n.DICT["zh-CN"], en: i18n.DICT["en-US"], i18n };
  }

  /** Key literals of the module's inline `var ZH = {…}` dictionary. */
  function inlineZhKeys(src) {
    const block = blockAt(src, "var ZH = {");
    if (!block) return [];
    const keys = [];
    const re = /^\s*"([^"]+)":/gm;
    let match;
    while ((match = re.exec(block))) keys.push(match[1]);
    return keys;
  }

  /** Index of the `)` that closes the call opened at `openIdx`. */
  function matchingParen(src, openIdx) {
    let depth = 0;
    let quote = null;
    let escaped = false;
    for (let i = openIdx; i < src.length; i++) {
      const ch = src[i];
      if (quote) {
        if (escaped) escaped = false;
        else if (ch === "\\") escaped = true;
        else if (ch === quote) quote = null;
        continue;
      }
      if (ch === "\"" || ch === "'" || ch === "`") { quote = ch; continue; }
      if (ch === "(") depth++;
      else if (ch === ")") {
        depth--;
        if (depth === 0) return i;
      }
    }
    return -1;
  }

  /**
   * All i18n keys a module file can emit, plus the dynamic prefixes found.
   * @returns {{keys: string[], prefixes: string[], dictKeys: string[]}}
   */
  function collectModuleKeys(src) {
    const keys = new Set();
    const prefixes = new Set();

    // 1. setAttribute("data-i18n…", <expression>) — scan the whole argument
    //    window so ternaries and concatenations are visible.
    const callRe = /setAttribute\s*\(/g;
    let match;
    while ((match = callRe.exec(src))) {
      const open = src.indexOf("(", match.index);
      const close = matchingParen(src, open);
      if (close < 0) continue;
      const args = src.slice(open + 1, close);
      if (!/^\s*["']data-i18n(?:-[a-z]+)?["']\s*,/.test(args)) continue;
      const literalRe = /["']([^"']*)["']/g;
      let literal;
      while ((literal = literalRe.exec(args))) {
        const value = literal[1];
        // Any dotted key shape (not only next.*): shell-owned keys such as
        // "shell.module.capability_form" are referenced the same way and must
        // surface so the ownership rule can send them through ALLOWLIST.
        if (!/^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+$/.test(value)) continue;
        if (/^next\./.test(value) && /[._]$/.test(value)) prefixes.add(value);
        else if (!/[._]$/.test(value)) keys.add(value);
      }
    }

    // 2. HTML-style static attributes.
    const attrRe = /\bdata-i18n(?:-[a-z]+)?\s*=\s*"([^"]+)"/g;
    while ((match = attrRe.exec(src))) keys.add(match[1]);

    // 3. Inline fallback dictionary (the module's own zh source of truth).
    const dictKeys = inlineZhKeys(src);
    dictKeys.forEach((key) => keys.add(key));

    // 4. Every remaining "next.*" literal: keys passed to t()/textEl() and the
    //    concatenated families ("next.x." + suffix) that need expansion.
    const literalRe = /["'](next\.[A-Za-z0-9_.]+)["']/g;
    while ((match = literalRe.exec(src))) {
      const value = match[1];
      if (/[._]$/.test(value)) prefixes.add(value);
      else keys.add(value);
    }

    return { keys: [...keys].sort(), prefixes: [...prefixes].sort(), dictKeys };
  }

  /** Expand a dynamic prefix with the explicit map or the module ZH dict. */
  function expandPrefix(prefix, dictKeys) {
    if (Object.prototype.hasOwnProperty.call(PREFIX_EXPANSIONS, prefix)) {
      return PREFIX_EXPANSIONS[prefix].map((suffix) => prefix + suffix);
    }
    return dictKeys.filter((key) => key.indexOf(prefix) === 0);
  }

  function isOwned(key) {
    return OWNED_PREFIXES.some((prefix) => key.indexOf(prefix) === 0);
  }

  function placeholderNames(value) {
    const names = String(value).match(/\{[A-Za-z0-9_]+\}/g) || [];
    return names.sort().join(",");
  }

  function duplicateDefs(src, lang) {
    const section = blockAt(src, "\"" + lang + "\": {");
    if (!section) return { defs: 0, dups: [] };
    const seen = Object.create(null);
    const dups = [];
    const re = /^\s*"([^"]+)":/gm;
    let match;
    while ((match = re.exec(section))) {
      if (seen[match[1]]) dups.push(match[1]);
      seen[match[1]] = true;
    }
    return { defs: Object.keys(seen).length, dups: dups };
  }

  // ---------------------------------------------------------------- tests --

  await test("acp2_i18n_new_modules: zh/en dictionaries are complete, paired and duplicate-free", async () => {
    const { zh, en } = dictionaries();
    const zhKeys = Object.keys(zh).sort();
    const enKeys = Object.keys(en).sort();
    const zhOnly = zhKeys.filter((key) => !(key in en));
    const enOnly = enKeys.filter((key) => !(key in zh));
    assertEq(zhOnly, [], "keys missing from en-US");
    assertEq(enOnly, [], "keys missing from zh-CN");
    assertEq(zhKeys.length, enKeys.length, "zh/en key counts");
    assert(zhKeys.length >= SHELL_KEY_FLOOR, "shell dictionary regressed to " + zhKeys.length + " keys (floor " + SHELL_KEY_FLOOR + ")");

    const src = readFrontend(I18N_FILE);
    for (const lang of ["zh-CN", "en-US"]) {
      const { defs, dups } = duplicateDefs(src, lang);
      assertEq(dups, [], lang + " duplicate key definitions");
      assertEq(defs, Object.keys(lang === "zh-CN" ? zh : en).length, lang + " parsed definition count");
    }

    const empty = [];
    const nonString = [];
    for (const key of zhKeys) {
      if (typeof zh[key] !== "string" || typeof en[key] !== "string") nonString.push(key);
      if (!zh[key] || !en[key]) empty.push(key);
    }
    assertEq(nonString, [], "non-string values");
    assertEq(empty, ALLOWED_EMPTY.slice().sort(), "keys without a translation (only the pre-A-61 ready labels may be empty)");

    const mismatched = zhKeys
      .filter((key) => placeholderNames(zh[key]) !== placeholderNames(en[key]))
      .map((key) => key + " zh={" + placeholderNames(zh[key]) + "} en={" + placeholderNames(en[key]) + "}");
    assertEq(mismatched, [], "zh/en placeholder mismatch");

    console.log("    A-61 dictionary: " + zhKeys.length + " keys (zh-CN + en-US)");
  });

  await test("acp2_i18n_new_modules: every key emitted by the new modules resolves in zh and en", async () => {
    const { zh, en } = dictionaries();
    const missing = [];
    const allowlistUsed = new Set();
    const perModule = {};

    for (const id of MODULES) {
      const src = moduleSource(id);
      const { keys, prefixes, dictKeys } = collectModuleKeys(src);
      for (const prefix of prefixes) {
        const expansion = expandPrefix(prefix, dictKeys);
        assert(expansion.length > 0, id + ": dynamic prefix " + prefix + " has no expansion (add PREFIX_EXPANSIONS or a ZH dict entry)");
        for (const key of expansion) keys.push(key);
      }
      for (const key of DOCUMENTED_KEYS[id] || []) keys.push(key);
      const unique = [...new Set(keys)].sort();
      assert(unique.length >= 10, id + ": scanner found only " + unique.length + " keys");
      perModule[id] = unique.length;
      for (const key of unique) {
        if (Object.prototype.hasOwnProperty.call(ALLOWLIST, key)) { allowlistUsed.add(key); continue; }
        assert(isOwned(key), id + ": scanned key " + key + " is outside the new-module namespaces — add an explicit ALLOWLIST entry with a reason");
        // Existence only: the "no empty translation" rule lives in the first
        // test, which knows the pre-A-61 ready labels that are intentionally "".
        if (!(key in zh)) missing.push(id + " [zh]: " + key);
        if (!(key in en)) missing.push(id + " [en]: " + key);
      }
    }

    assertEq(missing, [], "module keys missing from the shell dictionaries");

    const total = Object.values(perModule).reduce((sum, n) => sum + n, 0);
    assert(total >= MODULE_KEY_FLOOR, "only " + total + " module keys scanned (floor " + MODULE_KEY_FLOOR + ")");

    // Allowlisted (shell-owned) keys must still resolve; report stale entries.
    for (const key of Object.keys(ALLOWLIST)) {
      assert(key in zh && zh[key], "allowlisted key was not translated (zh): " + key);
      assert(key in en && en[key], "allowlisted key was not translated (en): " + key);
      if (!allowlistUsed.has(key)) console.log("    note: allowlist entry not referenced by the scanned modules: " + key);
    }

    console.log("    module keys scanned: " + JSON.stringify(perModule) + " = " + total);
  });

  await test("acp2_i18n_new_modules: dynamic key families are expanded, not skipped", async () => {
    const { zh, en } = dictionaries();
    for (const id of MODULES) {
      const src = moduleSource(id);
      const { prefixes, dictKeys } = collectModuleKeys(src);
      for (const prefix of prefixes) {
        const expansion = expandPrefix(prefix, dictKeys);
        assert(expansion.length > 0, id + ": no expansion for " + prefix);
        assert(
          expansion.every((key) => key.indexOf(prefix) === 0),
          id + ": expansion for " + prefix + " produced a foreign key"
        );
        for (const key of expansion) {
          assert(key in zh && key in en, id + ": expanded key " + key + " missing from the shell dictionaries");
        }
      }
      if (prefixes.length) console.log("    " + id + " prefixes: " + prefixes.join(", "));
    }
  });

  await test("acp2_i18n_new_modules: pre-A-61 shell keys are preserved verbatim", async () => {
    const { zh, en } = dictionaries();
    for (const key of Object.keys(PRESERVED)) {
      assertEq(zh[key], PRESERVED[key][0], key + " zh copy changed");
      assertEq(en[key], PRESERVED[key][1], key + " en copy changed");
    }
  });

  await test("acp2_i18n_new_modules: applyI18n keeps the missing-key behaviour unchanged", async () => {
    function el(attrs, text, tag) {
      const node = {
        attrs: Object.assign({}, attrs),
        text: text,
        title: attrs && attrs.title,
        placeholder: attrs && attrs.placeholder,
        ariaLabel: attrs && attrs.ariaLabel,
        getAttribute: function (name) { return this.attrs[name] === undefined ? null : this.attrs[name]; },
        setAttribute: function (name, value) {
          this.attrs[name] = value;
          if (name === "title") this.title = value;
          if (name === "placeholder") this.placeholder = value;
          if (name === "aria-label") this.ariaLabel = value;
        },
      };
      Object.defineProperty(node, "textContent", {
        get: function () { return node.text; },
        set: function (value) { node.text = value; },
      });
      node.tagName = tag || "SPAN";
      return node;
    }

    const missingText = el({ "data-i18n": "next.a61.missing.key" }, "module copy (keep)");
    const presentText = el({ "data-i18n": "next.tasks.title" }, "任务列表");
    const missingTitle = el({ "data-i18n-title": "next.a61.missing.title", title: "keep title" }, "");
    const presentTitle = el({ "data-i18n-title": "next.tasks.status.paused" }, "");
    const missingAria = el({ "data-i18n-aria": "next.a61.missing.aria", "aria-label": "keep aria" }, "");
    const doc = {
      documentElement: { lang: "" },
      querySelectorAll: function (selector) {
        if (selector === "[data-i18n]") return [missingText, presentText];
        if (selector === "[data-i18n-title]") return [missingTitle, presentTitle];
        if (selector === "[data-i18n-aria]") return [missingAria];
        return [];
      },
    };

    const { i18n } = loadI18n({ document: doc });

    assertEq(i18n.t("next.a61.missing.key"), "next.a61.missing.key", "t() must echo an unknown key");
    assertEq(i18n.getLang(), "zh-CN", "default language");

    i18n.applyI18n(doc);
    assertEq(missingText.textContent, "module copy (keep)", "missing key must not clobber mounted copy");
    assertEq(missingTitle.getAttribute("title"), "keep title", "missing data-i18n-title must not clobber the title");
    assertEq(missingAria.getAttribute("aria-label"), "keep aria", "missing data-i18n-aria must not clobber the aria-label");
    assertEq(presentText.textContent, "任务列表", "translated zh key applied");
    assertEq(presentTitle.getAttribute("title"), "已暂停", "translated zh title applied");
    assertEq(doc.documentElement.lang, "zh-CN", "documentElement.lang follows the shell language");

    i18n.setLang("en-US");
    i18n.applyI18n(doc);
    assertEq(missingText.textContent, "module copy (keep)", "missing key must stay untouched on language switch");
    assertEq(presentText.textContent, "Task list", "translated en key applied");
    assertEq(presentTitle.getAttribute("title"), "Paused", "translated en title applied");
    assertEq(doc.documentElement.lang, "en-US", "documentElement.lang follows the toggled language");

    i18n.setLang("zh-CN");
  });
};
