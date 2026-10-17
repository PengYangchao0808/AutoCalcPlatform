/* Real-browser integration: run with an isolated, unchanged ACP service.
 * Usage: node page_integration.browser.js http://127.0.0.1:PORT
 * Requires the development Playwright runtime, never a production dependency. */
"use strict";
const { chromium } = require("playwright");
const { expect } = require("playwright/test");
const assert = require("node:assert/strict");
const url = process.argv[2];
if (!url) throw new Error("Pass an isolated ACP test-service URL");

function body(offset = 0, total = 0, name = "Task") {
  const jobs = Array.from({length: Math.min(200, Math.max(0, total - offset))}, (_, n) => ({
    id: "test-" + (offset + n), name: name + " " + (offset + n), task_name: name + " " + (offset + n),
    status: "completed", project_id: "test", project_name: "Test",
    spec: {workflow: "singlepoint", task_name: name + " " + (offset + n)},
  }));
  return {groups: [{key: "test", display_name: "Test", count: jobs.length, jobs}], facets: {},
    total, counts: {completed: total}, offset, limit: 200, next_offset: offset + 200 < total ? offset + 200 : null};
}
async function main() {
  const browser = await chromium.launch({headless: true,
    channel: process.env.ACP_TEST_BROWSER_CHANNEL || (process.platform === "win32" ? "msedge" : undefined)});
  let passed = 0;
  async function test(name, fn) {
    const context = await browser.newContext({viewport: {width: 1280, height: 800}});
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    try {
      await fn(page);
      assert.deepEqual(errors, [], "Unexpected page exceptions");
      console.log("PASS " + name); passed++;
    } finally { await context.close(); }
  }
  try {
    await test("unchanged API and all static modules load; original UI remains interactive", async page => {
      const assets = [];
      page.on("response", response => {
        if (/\/(js|css)\//.test(response.url())) assets.push([response.url(), response.status()]);
      });
      await page.goto(url, {waitUntil: "domcontentloaded"});
      await expect(page.locator("#queue-counts")).toContainText("完成 0");
      assert(assets.length > 25);
      assert(assets.every(([, status]) => status < 400));
      const contract = await page.evaluate(async () => ({
        body: await apiV2("/task-view?offset=0&max_total=200"),
        controller: !!ACPWorkbench.controllers.taskList,
        renderer: !!ACPVisualization.energyChart,
      }));
      assert(Array.isArray(contract.body.groups) && contract.controller && contract.renderer);
      await page.locator("#btn-new-project").click();
      await expect(page.locator("#project-modal")).toBeVisible();
      await page.locator("#project-modal-close").click();
      await page.locator("#lang-toggle").click();
      await expect(page.locator("html")).toHaveAttribute("lang", "en-US");
      await page.reload({waitUntil: "domcontentloaded"});
      await expect(page.locator("html")).toHaveAttribute("lang", "en-US");
    });
    await test("missing 3Dmol and stalled catalogs do not block the shell", async page => {
      await page.route("**/js/vendor/3Dmol-*", route => route.abort());
      await page.route("**/api/v1/*-catalog", () => {});
      await page.goto(url, {waitUntil: "domcontentloaded"});
      await expect(page.locator("#queue-counts")).toContainText("完成 0");
      await page.locator("#btn-new-project").click();
      await expect(page.locator("#project-modal")).toBeVisible();
      assert.equal(await page.evaluate(() => initViewer()), false);
    });
    await test("task-list failure is visible and retry restores the same controller", async page => {
      let calls = 0;
      await page.route("**/api/v2/task-view?**", route => ++calls === 1
        ? route.fulfill({status: 503, json: {detail: "busy"}}) : route.fulfill({json: body()}));
      await page.goto(url, {waitUntil: "domcontentloaded"});
      await expect(page.locator("#queue-counts")).toHaveText("任务列表加载失败");
      await page.locator("#workbench-load-errors").getByRole("button", {name: "重试"}).click();
      await expect(page.locator("#queue-counts")).toContainText("完成 0");
      await expect(page.locator("#workbench-load-errors")).not.toContainText("busy");
    });
    await test("paging, facet reuse, write invalidation and request merging are connected", async page => {
      const requests = [];
      await page.route("**/api/v1/jobs/**", route => {
        const path = new URL(route.request().url()).pathname;
        if (path.endsWith("/events")) return route.fulfill({contentType: "text/event-stream", body: "\n\n"});
        return route.fulfill({json: path.endsWith("/detail") ? {job: body(0, 201).groups[0].jobs[0]} : {files: []}});
      });
      await page.route("**/api/v2/task-view?**", async route => {
        const query = new URL(route.request().url()).searchParams;
        requests.push(Object.fromEntries(query));
        await new Promise(resolve => setTimeout(resolve, 40));
        await route.fulfill({json: body(Number(query.get("offset")), 201)});
      });
      await page.goto(url, {waitUntil: "domcontentloaded"});
      await expect(page.locator("#queue-list .queue-row")).toHaveCount(200);
      await page.waitForTimeout(120);
      await page.evaluate(() => { window.savedRow = document.querySelector("#queue-list .queue-row"); });
      await page.evaluate(() => Promise.all([refreshJobs(), refreshJobs()]));
      assert.equal(await page.evaluate(() => savedRow === document.querySelector("#queue-list .queue-row")), true);
      assert.equal(requests.at(-1).include_facets, "false");
      await page.locator("#queue-pagination").getByRole("button", {name: "下一页"}).click();
      await expect(page.locator("#queue-list .queue-row")).toHaveCount(1);
      await expect(page.locator("#queue-pagination")).toContainText("第 2 页");
      await page.locator("#queue-pagination").getByRole("button", {name: "上一页"}).click();
      await expect(page.locator("#queue-list .queue-row")).toHaveCount(200);
      await page.evaluate(() => { invalidateTaskViewMetadata(); return refreshJobs(); });
      assert.equal(requests.at(-1).include_facets, "true");
      const before = requests.length;
      await page.evaluate(() => Promise.all([refreshJobs(), refreshJobs(), refreshJobs()]));
      assert.equal(requests.length - before, 1, "Same resource must issue one request");
    });
    await test("a late filter response cannot overwrite the current task list", async page => {
      let slowStarted = false;
      await page.route("**/api/v1/jobs/**", route => route.fulfill({json: {files: []}}));
      await page.route("**/api/v2/task-view?**", async route => {
        const query = new URL(route.request().url()).searchParams.get("q");
        if (query === "slow") {
          slowStarted = true;
          await new Promise(resolve => setTimeout(resolve, 180));
          // The browser may already have cancelled this superseded request.
          await route.fulfill({json: body(0, 1, "OLD")}).catch(() => {});
        } else await route.fulfill({json: body(0, query === "current" ? 1 : 0, "CURRENT")});
      });
      await page.goto(url, {waitUntil: "domcontentloaded"});
      await expect(page.locator("#queue-counts")).toContainText("完成 0");
      await page.evaluate(() => {
        taskViewPrefs.q = "slow"; saveTaskViewPrefs(); window.oldRefresh = refreshJobs();
      });
      await expect.poll(() => slowStarted).toBe(true);
      await page.evaluate(() => {
        taskViewPrefs.q = "current"; saveTaskViewPrefs(); return refreshJobs();
      });
      await page.waitForTimeout(240);
      assert.equal(await page.evaluate(() => jobsCache[0].task_name), "CURRENT 0");
      await expect(page.locator("#queue-list")).toContainText("CURRENT 0");
    });
    await test("slow details from a previous selection cannot replace the current task", async page => {
      let slowStarted = false;
      const job = id => ({id, status: "completed", task_name: id, attempt: 1, revision: 1,
        spec: {workflow: "singlepoint", task_name: id}});
      await page.route("**/api/v2/task-view?**", route => route.fulfill({json: body()}));
      await page.route("**/api/v1/jobs/**", async route => {
        const path = new URL(route.request().url()).pathname;
        const id = path.split("/")[4];
        if (path.endsWith("/detail")) {
          if (id === "previous") { slowStarted = true; await new Promise(resolve => setTimeout(resolve, 180)); }
          return route.fulfill({json: {job: job(id)}});
        }
        if (path.endsWith("/events")) return route.fulfill({contentType: "text/event-stream", body: "\\n\\n"});
        return route.fulfill({json: path.endsWith("/summary") ? job(id) : {files: []}});
      });
      await page.goto(url, {waitUntil: "domcontentloaded"});
      await expect(page.locator("#queue-counts")).toContainText("完成 0");
      await page.evaluate(() => { window.previousSelection = selectJob("previous"); });
      await expect.poll(() => slowStarted).toBe(true);
      await page.evaluate(() => selectJob("current"));
      await page.evaluate(() => previousSelection);
      assert.deepEqual(await page.evaluate(() => [selectedJobId, lastSelectedJob.id]), ["current", "current"]);
    });
    await test("edit-recalculate intercept remains above the transport", async page => {
      let posts = 0;
      await page.route("**/api/v1/jobs", route => { posts++; return route.fulfill({json: {unexpected: true}}); });
      await page.goto(url, {waitUntil: "domcontentloaded"});
      const result = await page.evaluate(async () => {
        const active = ACPJobEditor.isActive, intercept = ACPJobEditor.interceptJobCreate;
        try {
          ACPJobEditor.isActive = () => true;
          ACPJobEditor.interceptJobCreate = payload => ({intercepted: payload.marker});
          return await api("/jobs", {method: "POST", body: JSON.stringify({marker: "edited"})});
        } finally { ACPJobEditor.isActive = active; ACPJobEditor.interceptJobCreate = intercept; }
      });
      assert.deepEqual(result, {intercepted: "edited"}); assert.equal(posts, 0);
    });
    await test("the actual chart delegate and 3D framing operate with stable identities", async page => {
      await page.goto(url, {waitUntil: "domcontentloaded"});
      await page.waitForFunction(() => ACPVisualization.mol3d.isAvailable());
      const result = await page.evaluate(async () => {
        energyGraphState.selectedNodeId = "frame-stable";
        energyGraphState.seriesId = "s1";
        energyGraphState.viewSize = {width: 640, height: 360};
        const data = {view_type: "energy", nodes: [
          {id: "frame-stable", frame_index: 7, energy: -12, scanned_coordinate: 1.2},
          {id: "frame-next", frame_index: 9, energy: -11, scanned_coordinate: 1.5}],
          series: [{id: "s1", label: "Energy", unit: "kcal/mol", values: [-12, -11], x_values: [1.2, 1.5]}], annotations: []};
        const svg = energyGraphBuildSvg(data);
        const host = document.createElement("div");
        host.style.cssText = "position:fixed;left:0;top:0;width:400px;height:280px";
        document.body.appendChild(host);
        const instance = ACPVisualization.mol3d.create(host, {backgroundColor: "white"});
        instance.addModel("2\nprobe\nC 30 -20 15\nH 31 -20 15\n", "xyz");
        instance.setStyle({}, {stick: {}});
        const framing = {epoch: 0};
        scheduleViewerFraming(host, framing, () => instance);
        await new Promise(resolve => setTimeout(resolve, 150));
        const result = {svg: svg.includes('data-energy-node="frame-stable"') && svg.includes("kcal/mol"),
          frame: energyGraphFrameIndexForNode(data, "frame-stable"), settled: framing.settled,
          png: instance.pngURI().startsWith("data:image/png")};
        disposeViewerFraming(framing); ACPVisualization.mol3d.destroy(instance); host.remove();
        return result;
      });
      assert.deepEqual(result, {svg: true, frame: 7, settled: true, png: true});
    });
    console.log(`${passed} browser integration scenarios passed`);
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
