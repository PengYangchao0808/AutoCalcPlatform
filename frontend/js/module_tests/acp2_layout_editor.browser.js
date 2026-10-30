/* Isolated layout acceptance: real browser/static assets, API fixtures (no scientific execution). */
"use strict";
const { chromium } = require("playwright");
const http = require("node:http"), fs = require("node:fs"), path = require("node:path"), assert = require("node:assert/strict");
const root = path.resolve(__dirname, "../..");
async function main() {
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://localhost");
    if (url.pathname.startsWith("/api/")) {
      res.setHeader("Content-Type", "application/json");
      res.end(JSON.stringify(url.pathname.endsWith("workflow-catalog") ? { workflows: [] } : { projects: [], tasks: [], jobs: [] })); return;
    }
    const file = path.resolve(root, "." + (url.pathname === "/next/" ? "/next/index.html" : url.pathname));
    if (!file.startsWith(root + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) { res.writeHead(404); res.end(); return; }
    res.setHeader("Content-Type", ({ ".js": "text/javascript", ".css": "text/css", ".html": "text/html" })[path.extname(file)] || "application/octet-stream");
    fs.createReadStream(file).pipe(res);
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  let browser;
  try {
    browser = await chromium.launch({ headless: true, channel: process.platform === "win32" ? "msedge" : undefined });
    const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
    const errors = []; page.on("pageerror", e => errors.push(e.message));
    await page.goto("http://127.0.0.1:" + server.address().port + "/next/");
    await page.waitForSelector('[data-acp-ready="true"]');
    assert.equal(await page.locator(".acp-shell-topbar").evaluate(e => Math.round(e.getBoundingClientRect().top)), 0);
    await page.locator("#acp-tab-molecular").click();
    await page.waitForSelector('#acp-panel-shared-viewer');
    await page.evaluate(() => { window.originalViewerPanel = document.getElementById("acp-panel-shared-viewer"); });
    await page.locator("#acp-layout-edit").click();
    await page.locator('[data-layout-slot="main"]').selectOption("task-config");
    assert.equal(await page.locator('[data-layout-slot="side"]').inputValue(), "molecule-viewer");
    await page.locator('[data-layout-slot="bottom"]').selectOption("task-explorer");
    await page.locator('[data-layout-size="sideWidth"]').fill("45");
    await page.getByRole("button", { name: "应用布局", exact: true }).click();
    assert.equal(await page.locator('#acp-slot-molecular-main > .acp-panel:not([hidden])').getAttribute("data-module-id"), "task-config");
    assert.equal(await page.evaluate(() => window.originalViewerPanel === document.getElementById("acp-panel-shared-viewer")), true);
    assert.equal(await page.locator('#acp-slot-molecular-bottom > .acp-panel:not([hidden])').count(), 1);
    await page.reload(); await page.waitForSelector('[data-acp-ready="true"]');
    assert.equal(await page.locator("#acp-shell").getAttribute("data-workspace"), "molecular");
    await page.locator("#acp-tab-molecular").click();
    assert.equal(await page.locator('#acp-slot-molecular-main > .acp-panel:not([hidden])').getAttribute("data-module-id"), "task-config");
    await page.locator("#acp-layout-edit").click();
    assert.equal(await page.locator('[data-layout-size="sideWidth"]').inputValue(), "45");
    await page.locator('[data-layout-slot="side"]').selectOption("");
    await page.getByRole("button", { name: "取消", exact: true }).click();
    assert.equal(await page.locator('#acp-slot-molecular-side').isVisible(), true);
    await page.locator("#acp-layout-edit").click();
    await page.locator('[data-layout-slot="side"]').selectOption("");
    await page.getByRole("button", { name: "应用布局", exact: true }).click();
    assert.equal(await page.locator('#acp-slot-molecular-side').isVisible(), false);
    await page.locator("#acp-layout-edit").click();
    await page.getByRole("button", { name: "恢复默认", exact: true }).click();
    await page.getByRole("button", { name: "应用布局", exact: true }).click();
    assert.equal(await page.locator('#acp-slot-molecular-main > .acp-panel:not([hidden])').getAttribute("data-module-id"), "molecule-viewer");
    for (let i = 0; i < 3; i++) { await page.locator("#acp-tab-research").click(); await page.locator("#acp-tab-molecular").click(); }
    assert.equal(await page.locator('[data-module-id="molecule-viewer"]').count(), 1);
    await page.locator("#acp-layout-edit").click();
    const output = path.resolve(root, "../reports/frontend-layout-review"); fs.mkdirSync(output, { recursive: true });
    await page.screenshot({ path: path.join(output, "layout-editor.png") });
    await page.keyboard.press("Escape");
    assert.equal(await page.locator("#acp-layout-edit").evaluate(e => e === document.activeElement), true);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator("#acp-layout-edit").click();
    assert.equal(await page.locator("dialog").evaluate(e => e.getBoundingClientRect().right <= innerWidth), true);
    assert.deepEqual(errors, []);
    console.log("PASS: swap/add/hide/cancel/reset/persistence/single-viewer/keyboard/mobile; fixture API only");
  } finally { if (browser) await browser.close(); await new Promise(resolve => server.close(resolve)); }
}
main().catch(e => { console.error(e); process.exitCode = 1; });
