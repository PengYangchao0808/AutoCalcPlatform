/* Real-browser integration for the ACP Next experimental entry (/next/).
 * Usage: NODE_PATH=$(npm root -g) node acp2_next_entry.browser.js http://127.0.0.1:PORT
 * Pair with next_entry_server.py (isolated create_app on a temp run_root).
 * No test-framework dependency: plain assertions, `ok - …` lines, non-zero
 * exit on any failure. Requires the development Playwright runtime, never a
 * production dependency. */
"use strict";
const { chromium } = require("playwright");
const assert = require("node:assert/strict");

const base = (process.argv[2] || "").replace(/\/+$/, "");
if (!base) throw new Error("Pass an isolated ACP test-service URL, e.g. http://127.0.0.1:PORT");
const url = base + "/next/";

/* Network-class console noise is an expected environment state on a data-less
 * isolated service (401/403/404 catalog + asset probes). Uncaught page
 * exceptions are NEVER allowed. */
const CONSOLE_NOISE = [
  /Failed to load resource/i,
  /the server responded with a status of 4\d\d/i,
  /status of 401/i,
  /status of 403/i,
  /net::ERR_/i,
  /favicon/i,
];

async function main() {
  const browser = await chromium.launch({
    headless: true,
    channel: process.env.ACP_TEST_BROWSER_CHANNEL ||
      (process.platform === "win32" ? "msedge" : undefined),
  });
  let passed = 0;
  const failures = [];

  async function test(name, fn) {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const page = await context.newPage();
    const consoleErrors = [];
    const pageErrors = [];
    page.on("console", msg => { if (msg.type() === "error") consoleErrors.push(msg.text()); });
    page.on("pageerror", error => pageErrors.push(error && error.message ? error.message : String(error)));
    try {
      await fn(page);
      // Hygiene gate for every scenario: no uncaught exceptions, no fatal
      // console errors beyond the network-class whitelist.
      assert.deepEqual(pageErrors, [], "Unexpected uncaught page exceptions");
      const fatal = consoleErrors.filter(t => !CONSOLE_NOISE.some(re => re.test(t)));
      assert.deepEqual(fatal, [], "Fatal console errors (non network-class)");
      console.log("ok - " + name);
      passed++;
    } catch (error) {
      const detail = error && error.message ? error.message.split("\n").slice(0, 8).join("\n    ") : String(error);
      failures.push(name + ": " + detail);
      console.log("FAIL - " + name + ":\n    " + detail);
      if (pageErrors.length) console.log("    pageerrors: " + JSON.stringify(pageErrors.slice(0, 3)));
    } finally { await context.close(); }
  }

  async function waitForShell(page) {
    await page.waitForFunction(() => {
      const shell = document.getElementById("acp-shell");
      return shell && shell.getAttribute("data-acp-ready") === "true";
    }, null, { timeout: 15000 });
    const fatal = page.locator("#acp-shell-fatal");
    if (await fatal.isVisible()) {
      throw new Error("Shell fatal layer visible: " + (await fatal.textContent()).trim().slice(0, 200));
    }
  }

  try {
    await test("page loads: title contains Next, zero fatal console errors", async page => {
      await page.goto(url, { waitUntil: "domcontentloaded" });
      await page.waitForTimeout(300);
      assert.match(await page.title(), /Next/i, "title must contain 'Next'");
      assert(await page.locator("#acp-next-root").count() === 1, "server guard anchor #acp-next-root present");
    });

    await test("all static script/link assets answer 2xx", async page => {
      const responses = new Map();
      page.on("response", response => responses.set(response.url(), response.status()));
      await page.goto(url, { waitUntil: "load" });
      const refs = await page.evaluate(() => {
        const nodes = Array.from(document.querySelectorAll("script[src], link[href]"));
        return nodes.map(n => n.src || n.href).filter(Boolean);
      });
      assert(refs.length >= 30, "expected ~40 script/link refs, saw " + refs.length);
      const bad = [];
      for (const asset of refs) {
        const status = responses.get(asset);
        if (status == null || status < 200 || status >= 300) bad.push(asset + " -> " + (status == null ? "no response" : status));
      }
      assert.deepEqual(bad, [], "assets not 2xx:\n" + bad.join("\n"));
    });

    await test("shell boots: three role=tab workspace tabs, default workspace active", async page => {
      await page.goto(url, { waitUntil: "domcontentloaded" });
      await waitForShell(page);
      const tabs = page.locator(".acp-shell-switch [role='tab']");
      assert.equal(await tabs.count(), 3, "topbar must expose exactly three role=tab buttons");
      assert.equal(await page.getAttribute("#acp-shell", "data-workspace"), "research", "default workspace is research");
      assert.equal(await page.getAttribute("#acp-tab-research", "aria-selected"), "true", "research tab selected by default");
      assert.equal(await page.getAttribute("#acp-tab-molecular", "aria-selected"), "false");
      assert.equal(await page.getAttribute("#acp-tab-execution", "aria-selected"), "false");
    });

    await test("Molecular workspace mounts real molecule-viewer in a graceful state", async page => {
      await page.goto(url, { waitUntil: "domcontentloaded" });
      await waitForShell(page);
      await page.keyboard.press("Alt+2");
      await page.waitForFunction(() => document.getElementById("acp-shell").getAttribute("data-workspace") === "molecular");
      const panel = page.locator("#acp-ws-molecular .acp-panel[data-module-id='molecule-viewer']");
      await panel.waitFor({ state: "attached", timeout: 15000 });
      await page.waitForFunction(() => {
        const p = document.querySelector("#acp-ws-molecular .acp-panel[data-module-id='molecule-viewer']");
        return p && ["empty", "ready"].indexOf(p.getAttribute("data-acp-state")) >= 0;
      }, null, { timeout: 15000 });
      const state = await panel.getAttribute("data-acp-state");
      assert(["empty", "ready"].indexOf(state) >= 0, "viewer panel state must be empty/ready, saw " + state);
      // Real module (not a placeholder) — the module rendered its own root.
      const inner = await page.evaluate(() => {
        const root = document.querySelector("#acp-ws-molecular .acp-mod-viewer");
        return root ? root.getAttribute("data-acp-module-state") : null;
      });
      assert(inner !== null, "molecule-viewer module root .acp-mod-viewer must be mounted");
      if (inner === "error") {
        // Designed graceful path (e.g. WebGL unavailable): error + retry, never blank.
        assert(await page.locator("#acp-ws-molecular .acp-mod-viewer-retry:not([hidden])").count() === 1,
          "viewer inner error must expose a visible retry control");
        console.log("note - molecule-viewer inner state=error with retry (likely headless WebGL); graceful path asserted");
      } else {
        assert(["empty", "ready", "loading"].indexOf(inner) >= 0, "unexpected inner state " + inner);
      }
    });

    await test("Execution workspace mounts real task-explorer in a graceful state", async page => {
      await page.goto(url, { waitUntil: "domcontentloaded" });
      await waitForShell(page);
      await page.keyboard.press("Alt+3");
      await page.waitForFunction(() => document.getElementById("acp-shell").getAttribute("data-workspace") === "execution");
      const panel = page.locator("#acp-ws-execution .acp-panel[data-module-id='task-explorer']");
      await panel.waitFor({ state: "attached", timeout: 15000 });
      await page.waitForFunction(() => {
        const root = document.querySelector("#acp-ws-execution .acp-mod-tasks");
        return root && ["loading", "empty", "ready", "error", "forbidden"].indexOf(root.getAttribute("data-acp-module-state")) >= 0;
      }, null, { timeout: 15000 });
      const probe = await page.evaluate(() => {
        const root = document.querySelector("#acp-ws-execution .acp-mod-tasks");
        const body = root && root.querySelector(".acp-mod-tasks-body");
        return {
          state: root ? root.getAttribute("data-acp-module-state") : null,
          hasRetry: !!(root && root.querySelector(".acp-mod-tasks-retry")),
          hasErrorBlock: !!(root && root.querySelector(".acp-mod-tasks-state--error, .acp-mod-tasks-state--forbidden")),
          bodyText: body ? (body.textContent || "").trim() : "",
        };
      });
      assert(["loading", "empty", "ready", "error", "forbidden"].indexOf(probe.state) >= 0,
        "task-explorer state must be a known graceful state, saw " + probe.state);
      if (probe.state === "error" || probe.state === "forbidden") {
        // Error counts as graceful ONLY with visible structure, never a blank panel.
        assert(probe.hasErrorBlock || probe.hasRetry,
          "task-explorer error must expose an error block and/or retry control");
        assert(probe.bodyText.length > 0, "task-explorer error body must not be blank");
      }
    });

    await test("Research workspace mounts real agent-workspace with read-only badge or empty state", async page => {
      await page.goto(url, { waitUntil: "domcontentloaded" });
      await waitForShell(page);
      const panel = page.locator("#acp-ws-research .acp-panel[data-module-id='agent-workspace']");
      await panel.waitFor({ state: "attached", timeout: 15000 });
      await page.waitForFunction(() => {
        const root = document.querySelector("#acp-ws-research .acp-agent-root");
        return root && root.innerHTML.length > 0;
      }, null, { timeout: 15000 });
      const probe = await page.evaluate(() => {
        const root = document.querySelector("#acp-ws-research .acp-agent-root");
        // Resilient: the read-only chip class has churned (acp-tag--model ->
        // acp-tag--conn); key off the i18n key or the visible copy instead.
        const chip = root && root.querySelector(
          ".acp-tag[data-i18n='agent.connect.no_model'], .acp-tag--conn, .acp-tag--model");
        const empty = root && root.querySelector(".acp-state-empty");
        return {
          badgeText: chip ? (chip.textContent || "").trim() : null,
          badgeI18n: chip ? chip.getAttribute("data-i18n") : null,
          hasEmpty: !!empty,
          state: root ? root.getAttribute("data-acp-module-state") : null,
          fixture: root ? root.getAttribute("data-acp-fixture") : null,
        };
      });
      assert(probe.state != null, "agent-workspace root must carry data-acp-module-state");
      const readonlyBadge = probe.badgeText != null &&
        (/只读|Read-only/i.test(probe.badgeText) || probe.badgeI18n === "agent.connect.no_model");
      assert(readonlyBadge || probe.hasEmpty,
        "agent-workspace must show the read-only (只读) badge or an explicit empty state; saw badge=" +
        JSON.stringify(probe.badgeText) + " empty=" + probe.hasEmpty);
      assert.notEqual(probe.fixture, "true", "fixture/demo mode must not be active on a live service");
    });

    await test("keyboard Alt+2 / Alt+1 switch workspace activation", async page => {
      await page.goto(url, { waitUntil: "domcontentloaded" });
      await waitForShell(page);
      await page.keyboard.press("Alt+2");
      await page.waitForFunction(() => document.getElementById("acp-shell").getAttribute("data-workspace") === "molecular");
      assert.equal(await page.getAttribute("#acp-tab-molecular", "aria-selected"), "true", "Alt+2 selects molecular tab");
      assert.equal(await page.getAttribute("#acp-tab-research", "aria-selected"), "false");
      await page.keyboard.press("Alt+1");
      await page.waitForFunction(() => document.getElementById("acp-shell").getAttribute("data-workspace") === "research");
      assert.equal(await page.getAttribute("#acp-tab-research", "aria-selected"), "true", "Alt+1 selects research tab");
      assert.equal(await page.getAttribute("#acp-tab-molecular", "aria-selected"), "false");
    });

    await test("language toggle flips document lang and applied visible copy", async page => {
      await page.goto(url, { waitUntil: "domcontentloaded" });
      await waitForShell(page);
      const before = await page.evaluate(() => ({
        lang: document.documentElement.lang,
        researchTab: (document.querySelector("#acp-tab-research span[data-i18n]") || {}).textContent || "",
      }));
      assert.equal(before.lang, "zh-CN", "default lang is zh-CN");
      await page.locator("#acp-lang-toggle").click();
      await page.waitForFunction(() => document.documentElement.lang === "en-US");
      const after = await page.evaluate(() => ({
        lang: document.documentElement.lang,
        researchTab: (document.querySelector("#acp-tab-research span[data-i18n]") || {}).textContent || "",
      }));
      assert.equal(after.lang, "en-US", "html lang must flip to en-US");
      assert.notEqual(after.researchTab, before.researchTab,
        "data-i18n applied copy must change (研究 -> Research), saw " + JSON.stringify(after.researchTab));
      assert.match(after.researchTab, /Research/i);
    });

    await test("reload survival: shell still boots after page.reload()", async page => {
      await page.goto(url, { waitUntil: "domcontentloaded" });
      await waitForShell(page);
      await page.reload({ waitUntil: "domcontentloaded" });
      await waitForShell(page);
      assert.equal(await page.locator(".acp-shell-switch [role='tab']").count(), 3, "tabs survive reload");
      assert.equal(await page.getAttribute("#acp-shell", "data-workspace"), "research", "default workspace restored after reload");
    });

    await test("no placeholder residue in the three real module panel titles", async page => {
      await page.goto(url, { waitUntil: "domcontentloaded" });
      await waitForShell(page);
      // Visit each workspace so every real panel exists at least once.
      await page.keyboard.press("Alt+2");
      await page.waitForFunction(() => document.getElementById("acp-shell").getAttribute("data-workspace") === "molecular");
      await page.locator("#acp-ws-molecular .acp-panel[data-module-id='molecule-viewer']").waitFor({ state: "attached", timeout: 15000 });
      await page.keyboard.press("Alt+3");
      await page.waitForFunction(() => document.getElementById("acp-shell").getAttribute("data-workspace") === "execution");
      await page.locator("#acp-ws-execution .acp-panel[data-module-id='task-explorer']").waitFor({ state: "attached", timeout: 15000 });
      await page.keyboard.press("Alt+1");
      await page.waitForFunction(() => document.getElementById("acp-shell").getAttribute("data-workspace") === "research");
      await page.locator("#acp-ws-research .acp-panel[data-module-id='agent-workspace']").waitFor({ state: "attached", timeout: 15000 });

      // The viewer is a SINGLE active instance that physically migrates
      // (DOM move) between recipes — an inactive workspace's viewer slot is
      // legitimately empty (design §3.1 single-instance matrix). So probe each
      // workspace while it is active, instead of demanding all three panels
      // coexist in the DOM at one moment.
      const probes = [];
      const probeIn = async (wsKey, wsId, sel) => {
        await page.keyboard.press(wsKey);
        await page.waitForFunction(
          id => document.getElementById("acp-shell").getAttribute("data-workspace") === id,
          wsId,
        );
        await page.locator(sel).waitFor({ state: "attached", timeout: 15000 });
        const info = await page.evaluate(s => {
          const panel = document.querySelector(s);
          if (!panel) return { sel: s, missing: true };
          const title = panel.querySelector(".acp-panel-title");
          return {
            sel: s,
            missing: false,
            title: title ? (title.textContent || "").trim() : "",
            placeholderClass: /(^|\s)is-placeholder(\s|$)/.test(panel.className),
          };
        }, sel);
        probes.push(info);
      };
      await probeIn("Alt+2", "molecular", "#acp-ws-molecular .acp-panel[data-module-id='molecule-viewer']");
      await probeIn("Alt+3", "execution", "#acp-ws-execution .acp-panel[data-module-id='task-explorer']");
      await probeIn("Alt+1", "research", "#acp-ws-research .acp-panel[data-module-id='agent-workspace']");
      for (const probe of probes) {
        assert(!probe.missing, "real module panel missing: " + probe.sel);
        assert(!probe.placeholderClass, "real module panel must not carry is-placeholder: " + probe.sel);
        assert(!/占位|placeholder/i.test(probe.title),
          "real module panel title must not read as a placeholder: " + probe.sel + " -> " + JSON.stringify(probe.title));
      }
    });

    console.log(passed + " next-entry browser scenarios passed, " + failures.length + " failed");
    if (failures.length) {
      console.log("failures:\n" + failures.map(f => "  - " + f).join("\n"));
      process.exitCode = 1;
    }
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
