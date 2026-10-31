/* ACP 2.0 module test — exec-detail / evidence (UI-03) + A-25/A-26.
 *
 * The panel is read-only: one (projectId, jobId, attempt) selection at a time,
 * fed by jobDetail.fetchDetail + GET /jobs/{id}/artifacts. Pinned:
 *   - empty state before a task is selected;
 *   - ready state renders identity, plan digest, artifacts and the download URL;
 *   - cross-project and changed-attempt selections are refused, not merged;
 *   - dispose clears the host, unsubscribes and ignores a late response;
 *   - A-25: stage_index/stage_total/stage_detail (+ optional progress) from the
 *     same /detail payload; unknown fields omitted; terminal/error clear it;
 *   - A-26: incremental /logs cursor polling (stdout_offset/stderr_offset),
 *     irc/log-tail byte cursor, append-only DOM with a line cap, tail preserved
 *     on reconnect, pause on suspend/hidden, timers cleared on dispose, and an
 *     explicit local state for empty/unavailable logs (never a spinner).
 */
"use strict";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;
  const MODULE_PATH = "js/modules/exec-detail/module.js";

  const flush = async (n = 10) => {
    for (let i = 0; i < n; i++) await Promise.resolve();
    await new Promise((r) => setTimeout(r, 0));
    for (let i = 0; i < 4; i++) await Promise.resolve();
  };

  function makeDom() {
    const docListeners = new Map();
    function makeEl(tag) {
      const listeners = new Map();
      return {
        tagName: String(tag).toUpperCase(),
        children: [], parentNode: null, className: "", textContent: "", innerHTML: "",
        hidden: false, disabled: false, attrs: {}, listeners,
        setAttribute(k, v) { this.attrs[k] = String(v); },
        getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
        removeAttribute(k) { delete this.attrs[k]; },
        appendChild(child) { child.parentNode = this; this.children.push(child); return child; },
        replaceChildren(...nodes) {
          this.children.forEach((c) => { c.parentNode = null; });
          this.children = nodes.slice();
          nodes.forEach((n) => { n.parentNode = this; });
        },
        addEventListener(type, fn) {
          if (!listeners.has(type)) listeners.set(type, new Set());
          listeners.get(type).add(fn);
        },
        removeEventListener(type, fn) {
          if (listeners.has(type)) listeners.get(type).delete(fn);
        },
        fire(type) {
          const set = listeners.get(type);
          if (set) [...set].forEach((fn) => fn({ type, target: this, preventDefault() {} }));
        },
        get firstChild() { return this.children[0] || null; },
        removeChild(child) {
          const i = this.children.indexOf(child);
          if (i >= 0) {
            this.children.splice(i, 1);
            child.parentNode = null;
          }
          return child;
        },
        scrollTop: 0,
        scrollHeight: 0,
      };
    }
    const document = {
      hidden: false,
      visibilityState: "visible",
      createElement: (tag) => makeEl(tag),
      addEventListener(type, fn) {
        if (!docListeners.has(type)) docListeners.set(type, new Set());
        docListeners.get(type).add(fn);
      },
      removeEventListener(type, fn) {
        if (docListeners.has(type)) docListeners.get(type).delete(fn);
      },
      dispatch(type) {
        const set = docListeners.get(type);
        if (set) [...set].forEach((fn) => fn({ type }));
      },
      documentElement: makeEl("html"),
      activeDocListeners() {
        let n = 0;
        docListeners.forEach((s) => { n += s.size; });
        return n;
      },
    };
    return { document, makeEl };
  }

  function walk(el, out = []) {
    (el.children || []).forEach((c) => { out.push(c); walk(c, out); });
    return out;
  }

  function find(root, attr, value) {
    return walk(root).find((n) => n.getAttribute && n.getAttribute(attr) === value) || null;
  }

  function byText(root, text) {
    return walk(root).find((n) => typeof n.textContent === "string" && n.textContent.indexOf(text) !== -1) || null;
  }

  function byClass(root, cls) {
    return walk(root).find((n) => hasClass(n, cls)) || null;
  }

  function allByClass(root, cls) {
    return walk(root).filter((n) => hasClass(n, cls));
  }

  function hasClass(node, cls) {
    return typeof node.className === "string" && node.className.split(/\s+/).indexOf(cls) !== -1;
  }

  // A-27 tree helpers: rows expose data-acp-tree-row="<relative path>".
  function treeRow(host, path) {
    return find(host, "data-acp-tree-row", path);
  }

  function clickTreeRow(host, path) {
    const row = treeRow(host, path);
    assert(!!row, "tree row missing: " + path);
    row.fire("click");
    return row;
  }

  function treePaths(host) {
    return allByClass(host, "acp-ev-tree-row").map((n) => n.getAttribute("data-acp-tree-row"));
  }

  function queryPath(path) {
    return queryOf(path).path;
  }

  // Deterministic clock: the module schedules follow-up polls with setTimeout,
  // so tests must advance time explicitly instead of letting real timers fire.
  function makeClock() {
    let now = 0;
    let nextId = 1;
    const timers = new Map();
    function setTimeoutFn(fn, ms) {
      const id = nextId++;
      timers.set(id, { fn, at: now + (Number(ms) || 0) });
      return id;
    }
    function clearTimeoutFn(id) { timers.delete(id); }
    function tick(ms) {
      now += Number(ms) || 0;
      let guard = 0;
      for (;;) {
        let due = null;
        timers.forEach((timer, id) => {
          if (timer.at <= now && (!due || timer.at < due.timer.at)) due = { id, timer };
        });
        if (!due || guard++ > 200) break;
        timers.delete(due.id);
        due.timer.fn();
      }
    }
    return {
      setTimeout: setTimeoutFn,
      clearTimeout: clearTimeoutFn,
      tick,
      pending: () => timers.size,
      now: () => now,
    };
  }

  function queryOf(path) {
    const out = {};
    const raw = String(path || "").split("?")[1] || "";
    raw.split("&").forEach((pair) => {
      if (!pair) return;
      const eq = pair.indexOf("=");
      if (eq < 0) out[decodeURIComponent(pair)] = "";
      else out[decodeURIComponent(pair.slice(0, eq))] = decodeURIComponent(pair.slice(eq + 1));
    });
    return out;
  }

  function makeEndpointRequest(handlers) {
    handlers = handlers || {};
    const calls = [];
    function request(path, opts) {
      calls.push({ path, opts });
      let key = "other";
      if (path.indexOf("/irc/log-tail") !== -1) key = "irc";
      else if (path.indexOf("/logs") !== -1) key = "logs";
      else if (path.indexOf("/artifacts") !== -1) key = "artifacts";
      else if (path.indexOf("/remote-files") !== -1) {
        key = path.indexOf("/preview") !== -1 ? "remotePreview" : "remoteFiles";
      } else if (path.indexOf("/files") !== -1) {
        key = path.indexOf("/files/") !== -1 ? "fileBody" : "files";
      }
      const handler = handlers[key];
      if (typeof handler === "function") return handler(path, opts, calls);
      if (handler !== undefined) return Promise.resolve(handler);
      if (key === "artifacts") return Promise.resolve({ artifacts: [] });
      if (key === "files" || key === "remoteFiles") return Promise.resolve({ files: [] });
      return Promise.reject(new Error("unexpected request: " + path));
    }
    return { calls, request };
  }

  function makeContext(projectId) {
    const subs = [];
    let snapshot = { projectId, taskRef: null, structureRef: null, researchRef: null, networkNodeId: null, revision: 0 };
    return {
      getSnapshot: () => snapshot,
      subscribe(fn) { subs.push(fn); return () => { const i = subs.indexOf(fn); if (i >= 0) subs.splice(i, 1); }; },
      setTask(ref) { snapshot = Object.assign({}, snapshot, { taskRef: ref, revision: snapshot.revision + 1 }); subs.forEach((fn) => fn(snapshot)); },
      count: () => subs.length,
    };
  }

  const DETAIL = {
    job: {
      id: "20261030_001_optimize_demo",
      name: "ethanol_optimize",
      custom_name: null,
      status: "completed",
      workflow: "optimize",
      attempt: 1,
      project_id: "proj-a",
      plan_digest: "sha256:" + "a".repeat(64),
      submission_id: "sub-" + "b".repeat(32),
      release_id: "rel-2026.10",
      created_at: "2026-10-30T10:00:00+00:00",
      completed_at: "2026-10-30T10:05:00+00:00",
      error: null,
    },
    stages: [],
  };

  const ARTIFACTS = {
    artifacts: [
      { artifact_id: "art-1", artifact_type: "json", file_path: "RESULT/result_manifest.json", size_bytes: 2048 },
      { artifact_id: "art-2", artifact_type: "xyz", file_path: "RESULT/optimized.xyz", size_bytes: 512 },
    ],
  };

  function makeJobDetail(overrides) {
    overrides = overrides || {};
    return {
      calls: [],
      fetchDetail(id) {
        this.calls.push(id);
        if (overrides.detail) return overrides.detail(id);
        return Promise.resolve(JSON.parse(JSON.stringify(DETAIL)));
      },
    };
  }

  function makeRequest(overrides) {
    const calls = [];
    return {
      calls,
      request(path, opts) {
        calls.push({ path, opts });
        if (overrides && overrides.handler) {
          const custom = overrides.handler(path, opts);
          if (custom !== undefined) return custom;
        }
        return Promise.resolve(JSON.parse(JSON.stringify(ARTIFACTS)));
      },
    };
  }

  function makeDetail(overrides) {
    overrides = overrides || {};
    const detail = JSON.parse(JSON.stringify(DETAIL));
    Object.assign(detail.job, overrides.job || {});
    (overrides.jobKeys || []).forEach((key) => { delete detail.job[key]; });
    if (overrides.stages) detail.stages = overrides.stages;
    if (overrides.errorDetail) detail.error_detail = overrides.errorDetail;
    return detail;
  }

  function runningDetail(overrides) {
    overrides = overrides || {};
    const detail = makeDetail(Object.assign({}, overrides, {
      job: Object.assign({ status: "running", completed_at: null }, overrides.job || {}),
    }));
    return detail;
  }

  const EMPTY_LOGS = {
    stdout_lines: [], stderr_lines: [],
    stdout_next_offset: 0, stderr_next_offset: 0,
    has_more: false,
  };

  function load() {
    const dom = makeDom();
    const clock = makeClock();
    const sandbox = makeSandbox({
      document: dom.document,
      setTimeout: clock.setTimeout,
      clearTimeout: clock.clearTimeout,
    });
    loadScript(MODULE_PATH, sandbox);
    return { sandbox, dom, clock };
  }

  await test("exec-detail: empty state until a task is selected", async () => {
    const { sandbox, dom } = load();
    const host = dom.makeEl("div");
    const context = makeContext("proj-a");
    const instance = sandbox.ACPNextModules["exec-detail"].factory({
      doc: dom.document, context, jobDetail: makeJobDetail(), request: makeRequest().request,
    });
    await instance.mount(host, { context });
    await flush();
    const root = host.children[0];
    assertEq(root.getAttribute("data-acp-module-state"), "empty");
    assert(byText(host, "选择任务") !== null, "empty copy");
    instance.dispose();
  });

  await test("exec-detail: ready state renders identity, digest, artifacts and download link", async () => {
    const { sandbox, dom } = load();
    const host = dom.makeEl("div");
    const context = makeContext("proj-a");
    const jobDetail = makeJobDetail();
    const request = makeRequest();
    const instance = sandbox.ACPNextModules["exec-detail"].factory({
      doc: dom.document, context, jobDetail, request: request.request,
    });
    await instance.mount(host, { context });
    context.setTask({ jobId: "20261030_001_optimize_demo", attempt: 1 });
    await flush();

    const root = host.children[0];
    assertEq(root.getAttribute("data-acp-module-state"), "ready");
    assertEq(jobDetail.calls[0], "20261030_001_optimize_demo");
    assert(request.calls.some((c) => c.path.indexOf("/api/v1/jobs/20261030_001_optimize_demo/artifacts") === 0), "artifacts requested");
    assert(byText(host, "completed") !== null, "status badge");
    assert(byText(host, "rel-2026.10") !== null, "release row");
    assert(byText(host, "result_manifest.json") !== null, "artifact path");
    const link = walk(host).find((n) => n.getAttribute && n.getAttribute("href") && n.getAttribute("href").indexOf("/api/v1/artifacts/art-1/download") !== -1);
    assert(!!link, "download link built from the artifact id");
    instance.dispose();
  });

  await test("exec-detail: cross-project task is refused instead of merged", async () => {
    const { sandbox, dom } = load();
    const host = dom.makeEl("div");
    const context = makeContext("proj-b");
    const instance = sandbox.ACPNextModules["exec-detail"].factory({
      doc: dom.document, context, jobDetail: makeJobDetail(), request: makeRequest().request,
    });
    await instance.mount(host, { context });
    context.setTask({ jobId: "20261030_001_optimize_demo", attempt: 1 });
    await flush();
    const root = host.children[0];
    assertEq(root.getAttribute("data-acp-module-state"), "error");
    assert(byText(host, "不属于当前项目") !== null, "cross-project refusal");
    instance.dispose();
  });

  await test("exec-detail: a changed attempt is refused", async () => {
    const { sandbox, dom } = load();
    const host = dom.makeEl("div");
    const context = makeContext("proj-a");
    const instance = sandbox.ACPNextModules["exec-detail"].factory({
      doc: dom.document, context, jobDetail: makeJobDetail(), request: makeRequest().request,
    });
    await instance.mount(host, { context });
    context.setTask({ jobId: "20261030_001_optimize_demo", attempt: 2 });
    await flush();
    assert(byText(host, "尝试次数已变化") !== null, "attempt drift refusal");
    instance.dispose();
  });

  await test("exec-detail: dispose clears the host and ignores a late response", async () => {
    const { sandbox, dom } = load();
    const host = dom.makeEl("div");
    const context = makeContext("proj-a");
    let release;
    const jobDetail = makeJobDetail({
      detail() {
        return new Promise((resolve) => { release = () => resolve(JSON.parse(JSON.stringify(DETAIL))); });
      },
    });
    const instance = sandbox.ACPNextModules["exec-detail"].factory({
      doc: dom.document, context, jobDetail, request: makeRequest().request,
    });
    await instance.mount(host, { context });
    context.setTask({ jobId: "20261030_001_optimize_demo", attempt: 1 });
    await flush();
    instance.dispose();
    assertEq(host.children.length, 0, "host cleared");
    assertEq(context.count(), 0, "context unsubscribed");
    assertEq(dom.document.activeDocListeners(), 0, "language listener removed");
    if (release) release();
    await flush();
    assertEq(host.children.length, 0, "late response did not remount");
  });

  // ------------------------------------------------------------------ A-25 -- //

  await test("exec-detail A-25: stage progress renders current/total/detail and strip", async () => {
    const { sandbox, dom } = load();
    const host = dom.makeEl("div");
    const context = makeContext("proj-a");
    const detail = runningDetail({
      job: { stage_index: 3, stage_total: 8, stage_detail: "17/40 scan points", stage_progress: 0.5 },
      stages: Array.from({ length: 8 }, (_, i) => ({
        stage_name: "stage_" + (i + 1),
        label: "Stage " + (i + 1),
        status: i < 2 ? "completed" : (i === 2 ? "running" : "pending"),
      })),
    });
    const jobDetail = makeJobDetail({ detail: () => Promise.resolve(detail) });
    const request = makeEndpointRequest({ logs: EMPTY_LOGS });
    const instance = sandbox.ACPNextModules["exec-detail"].factory({
      doc: dom.document, context, jobDetail, request: request.request,
    });
    await instance.mount(host, { context });
    context.setTask({ jobId: detail.job.id, attempt: 1 });
    await flush();

    const section = byClass(host, "acp-ev-stages");
    assert(!!section, "stage section exists");
    assertEq(section.hidden, false, "stage section visible");
    assertEq(section.getAttribute("data-acp-stage-state"), "ready");
    assertEq(section.getAttribute("data-acp-stage-total"), "8");
    assertEq(section.getAttribute("data-acp-stage-current"), "3");
    assertEq(byClass(host, "acp-ev-stage-count").textContent, "3 / 8", "current / total");
    assertEq(byClass(host, "acp-ev-stage-detail").textContent, "17/40 scan points", "stage detail verbatim");
    const bar = byClass(host, "acp-ev-stage-bar");
    assertEq(bar.getAttribute("aria-valuenow"), "31", "progress = ((3-1)+0.5)/8");
    assertEq(section.getAttribute("data-acp-stage-progress"), "stage_progress");
    const segments = allByClass(host, "acp-ev-stage-step");
    assertEq(segments.length, 8, "one segment per stage");
    assertEq(segments[0].getAttribute("data-acp-stage-step"), "completed");
    assertEq(segments[2].getAttribute("data-acp-stage-step"), "running");
    assertEq(segments[2].getAttribute("data-acp-stage-current"), "true");
    assertEq(segments[2].getAttribute("title"), "Stage 3", "stage label from the same detail payload");
    assertEq(segments[3].getAttribute("data-acp-stage-step"), "pending");
    instance.dispose();
  });

  await test("exec-detail A-25: unknown stage fields are omitted, never fabricated", async () => {
    const { sandbox, dom } = load();
    const host = dom.makeEl("div");
    const context = makeContext("proj-a");
    const detail = runningDetail({
      jobKeys: ["stage_index", "stage_total", "stage_detail", "stage_progress", "progress"],
    });
    const jobDetail = makeJobDetail({ detail: () => Promise.resolve(detail) });
    const request = makeEndpointRequest({ logs: EMPTY_LOGS });
    const instance = sandbox.ACPNextModules["exec-detail"].factory({
      doc: dom.document, context, jobDetail, request: request.request,
    });
    await instance.mount(host, { context });
    context.setTask({ jobId: detail.job.id, attempt: 1 });
    await flush();

    const section = byClass(host, "acp-ev-stages");
    assertEq(section.hidden, true, "no stage_total -> hidden");
    assertEq(section.getAttribute("data-acp-stage-state"), "hidden");
    assertEq(byClass(host, "acp-ev-stage-count").textContent, "", "no fabricated position");
    assertEq(allByClass(host, "acp-ev-stage-step").length, 0, "no fabricated strip");
    assertEq(byText(section, "—"), null, "no placeholder progress");
    instance.dispose();

    // stage_total only: the strip (total is known) renders, the current count is
    // omitted instead of inventing "0 / 4".
    const host2 = dom.makeEl("div");
    const context2 = makeContext("proj-a");
    const detail2 = runningDetail({
      job: { stage_total: 4 },
      jobKeys: ["stage_index", "stage_detail", "stage_progress", "progress"],
    });
    const instance2 = sandbox.ACPNextModules["exec-detail"].factory({
      doc: dom.document,
      context: context2,
      jobDetail: makeJobDetail({ detail: () => Promise.resolve(detail2) }),
      request: makeEndpointRequest({ logs: EMPTY_LOGS }).request,
    });
    await instance2.mount(host2, { context: context2 });
    context2.setTask({ jobId: detail2.job.id, attempt: 1 });
    await flush();
    const section2 = byClass(host2, "acp-ev-stages");
    assertEq(section2.hidden, false, "known total still renders");
    assertEq(section2.getAttribute("data-acp-stage-total"), "4");
    assertEq(section2.getAttribute("data-acp-stage-current"), null, "no invented current stage");
    const count2 = byClass(host2, "acp-ev-stage-count");
    assertEq(count2.hidden, true, "position omitted");
    assertEq(count2.textContent, "");
    assertEq(allByClass(host2, "acp-ev-stage-step").length, 4);
    assertEq(section2.getAttribute("data-acp-stage-progress"), "none");
    instance2.dispose();
  });

  await test("exec-detail A-25: terminal and error states clear stage progress", async () => {
    const { sandbox, dom } = load();

    const completed = makeDetail({
      job: { status: "completed", stage_index: 2, stage_total: 5, stage_detail: "stale detail" },
      stages: [{ stage_name: "a", status: "completed" }],
    });
    const host = dom.makeEl("div");
    const context = makeContext("proj-a");
    const instance = sandbox.ACPNextModules["exec-detail"].factory({
      doc: dom.document,
      context,
      jobDetail: makeJobDetail({ detail: () => Promise.resolve(completed) }),
      request: makeEndpointRequest({ logs: EMPTY_LOGS }).request,
    });
    await instance.mount(host, { context });
    context.setTask({ jobId: completed.job.id, attempt: 1 });
    await flush();
    const section = byClass(host, "acp-ev-stages");
    assertEq(section.hidden, true, "completed clears the strip");
    assertEq(section.getAttribute("data-acp-stage-state"), "cleared");
    assertEq(allByClass(host, "acp-ev-stage-step").length, 0);
    assertEq(byClass(host, "acp-ev-stage-count").textContent, "");
    instance.dispose();

    const failed = runningDetail({
      job: { status: "failed", error: "ORCA terminated", stage_index: 1, stage_total: 5, stage_detail: "step 1" },
      errorDetail: { error: "ORCA terminated", stderr_tail: "boom", failed_stage: "opt" },
    });
    const host2 = dom.makeEl("div");
    const context2 = makeContext("proj-a");
    const instance2 = sandbox.ACPNextModules["exec-detail"].factory({
      doc: dom.document,
      context: context2,
      jobDetail: makeJobDetail({ detail: () => Promise.resolve(failed) }),
      request: makeEndpointRequest({ logs: EMPTY_LOGS }).request,
    });
    await instance2.mount(host2, { context: context2 });
    context2.setTask({ jobId: failed.job.id, attempt: 1 });
    await flush();
    assertEq(byClass(host2, "acp-ev-stages").hidden, true, "failed clears the strip");
    assert(byText(host2, "ORCA terminated") !== null, "failure still shown");
    instance2.dispose();
  });

  // ------------------------------------------------------------------ A-26 -- //

  await test("exec-detail A-26: logs append only new lines with stdout/stderr cursors", async () => {
    const { sandbox, dom, clock } = load();
    const host = dom.makeEl("div");
    const context = makeContext("proj-a");
    const detail = runningDetail();
    const responses = [
      { stdout_lines: ["step a", "step b"], stderr_lines: ["warn"], stdout_next_offset: 2, stderr_next_offset: 1, has_more: false },
      { stdout_lines: ["step c"], stderr_lines: [], stdout_next_offset: 3, stderr_next_offset: 1, has_more: false },
      EMPTY_LOGS,
    ];
    let logsCalls = 0;
    const request = makeEndpointRequest({
      logs: () => Promise.resolve(responses[logsCalls++]),
    });
    const instance = sandbox.ACPNextModules["exec-detail"].factory({
      doc: dom.document, context,
      jobDetail: makeJobDetail({ detail: () => Promise.resolve(detail) }),
      request: request.request,
    });
    await instance.mount(host, { context });
    context.setTask({ jobId: detail.job.id, attempt: 1 });
    await flush();

    const logSection = byClass(host, "acp-ev-logs");
    const logPaths = () => request.calls.filter((c) => c.path.indexOf("/logs") !== -1);
    assertEq(logPaths().length, 1, "initial tail request");
    assertEq(queryOf(logPaths()[0].path).stdout_offset, undefined, "first read is a tail (no cursor)");
    assertEq(queryOf(logPaths()[0].path).lines, "300");
    assertEq(allByClass(host, "acp-ev-log-line").map((n) => n.textContent), ["step a", "step b", "warn"]);
    assertEq(logSection.getAttribute("data-acp-log-state"), "live");
    assertEq(logSection.getAttribute("data-acp-log-lines"), "3");
    assertEq(byClass(host, "acp-ev-log-badge").textContent, "实时");

    clock.tick(5000);
    await flush();
    assertEq(logPaths().length, 2, "second poll scheduled");
    assertEq(queryOf(logPaths()[1].path).stdout_offset, "2", "stdout cursor from stdout_next_offset");
    assertEq(queryOf(logPaths()[1].path).stderr_offset, "1", "stderr cursor from stderr_next_offset");
    assertEq(allByClass(host, "acp-ev-log-line").map((n) => n.textContent), ["step a", "step b", "warn", "step c"]);

    clock.tick(5000);
    await flush();
    assertEq(logPaths().length, 3);
    assertEq(queryOf(logPaths()[2].path).stdout_offset, "3");
    const texts = allByClass(host, "acp-ev-log-line").map((n) => n.textContent);
    assertEq(texts.length, 4, "no duplicate append");
    assertEq(new Set(texts).size, 4, "each line appended once");
    assertEq(request.calls.some((c) => c.path.indexOf("/irc/log-tail") !== -1), false, "non-IRC job never polls IRC tail");
    instance.dispose();
  });

  await test("exec-detail A-26: reconnect preserves the tail and resumes from the cursor", async () => {
    const { sandbox, dom, clock } = load();
    const host = dom.makeEl("div");
    const context = makeContext("proj-a");
    const detail = runningDetail();
    let logsCalls = 0;
    const request = makeEndpointRequest({
      logs: () => {
        logsCalls += 1;
        if (logsCalls === 1) {
          return Promise.resolve({ stdout_lines: ["one", "two"], stderr_lines: [], stdout_next_offset: 2, stderr_next_offset: 0, has_more: false });
        }
        if (logsCalls === 2) return Promise.reject({ status: 503, message: "upstream gone" });
        return Promise.resolve({ stdout_lines: ["three"], stderr_lines: [], stdout_next_offset: 3, stderr_next_offset: 0, has_more: false });
      },
    });
    const instance = sandbox.ACPNextModules["exec-detail"].factory({
      doc: dom.document, context,
      jobDetail: makeJobDetail({ detail: () => Promise.resolve(detail) }),
      request: request.request,
    });
    await instance.mount(host, { context });
    context.setTask({ jobId: detail.job.id, attempt: 1 });
    await flush();

    const logSection = byClass(host, "acp-ev-logs");
    const logNote = byClass(host, "acp-ev-log-note");
    assertEq(allByClass(host, "acp-ev-log-line").length, 2);
    assertEq(clock.pending(), 1, "next poll scheduled");

    clock.tick(5000);
    await flush();
    assertEq(logSection.getAttribute("data-acp-log-state"), "reconnecting");
    assertEq(logNote.hidden, false);
    assert(logNote.textContent.indexOf("连接中断") !== -1, "explicit reconnect state");
    assertEq(allByClass(host, "acp-ev-log-line").map((n) => n.textContent), ["one", "two"], "tail preserved");
    assertEq(clock.pending(), 1, "retry scheduled");

    clock.tick(10000); // exponential backoff for the first failure
    await flush();
    const logPaths = request.calls.filter((c) => c.path.indexOf("/logs") !== -1);
    assertEq(queryOf(logPaths[2].path).stdout_offset, "2", "cursor preserved across reconnect");
    assertEq(allByClass(host, "acp-ev-log-line").map((n) => n.textContent), ["one", "two", "three"]);
    assertEq(logSection.getAttribute("data-acp-log-state"), "live");
    assertEq(logNote.hidden, true, "reconnected note cleared");
    instance.dispose();
  });

  await test("exec-detail A-26: missing log endpoint is an explicit local state (not a spinner)", async () => {
    const { sandbox, dom, clock } = load();
    const host = dom.makeEl("div");
    const context = makeContext("proj-a");
    const detail = runningDetail();
    let logsCalls = 0;
    const request = makeEndpointRequest({
      logs: () => {
        logsCalls += 1;
        if (logsCalls === 1) return Promise.reject({ status: 404, message: "Job not found: x" });
        return Promise.resolve({ stdout_lines: ["recovered"], stderr_lines: [], stdout_next_offset: 1, stderr_next_offset: 0, has_more: false });
      },
    });
    const instance = sandbox.ACPNextModules["exec-detail"].factory({
      doc: dom.document, context,
      jobDetail: makeJobDetail({ detail: () => Promise.resolve(detail) }),
      request: request.request,
    });
    await instance.mount(host, { context });
    context.setTask({ jobId: detail.job.id, attempt: 1 });
    await flush();

    const logSection = byClass(host, "acp-ev-logs");
    const logNote = byClass(host, "acp-ev-log-note");
    assertEq(host.children[0].getAttribute("data-acp-module-state"), "ready", "detail stays ready, log error is local");
    assertEq(logSection.getAttribute("data-acp-log-state"), "unavailable");
    assertEq(logNote.hidden, false);
    assert(logNote.textContent.indexOf("日志端点不可用") !== -1, "explicit unavailable state");
    assertEq(clock.pending(), 0, "no endless retry timer for a missing endpoint");
    const retry = find(host, "data-action", "retry-logs");
    assert(!!retry && retry.hidden === false, "retry offered");

    retry.fire("click");
    await flush();
    assertEq(logSection.getAttribute("data-acp-log-state"), "live");
    assertEq(allByClass(host, "acp-ev-log-line").map((n) => n.textContent), ["recovered"]);
    assertEq(clock.pending(), 1, "polling resumes after a successful retry");
    instance.dispose();
  });

  await test("exec-detail A-26: suspend and document.hidden pause polling, resume continues", async () => {
    const { sandbox, dom, clock } = load();
    const host = dom.makeEl("div");
    const context = makeContext("proj-a");
    const detail = runningDetail();
    let logsCalls = 0;
    const request = makeEndpointRequest({
      logs: () => {
        logsCalls += 1;
        return Promise.resolve({
          stdout_lines: ["line " + logsCalls],
          stderr_lines: [],
          stdout_next_offset: logsCalls,
          stderr_next_offset: 0,
          has_more: false,
        });
      },
    });
    const instance = sandbox.ACPNextModules["exec-detail"].factory({
      doc: dom.document, context,
      jobDetail: makeJobDetail({ detail: () => Promise.resolve(detail) }),
      request: request.request,
    });
    await instance.mount(host, { context });
    context.setTask({ jobId: detail.job.id, attempt: 1 });
    await flush();

    const logSection = byClass(host, "acp-ev-logs");
    assertEq(logsCalls, 1);
    assertEq(clock.pending(), 1);

    instance.suspend();
    assertEq(clock.pending(), 0, "suspend clears the timer");
    assertEq(logSection.getAttribute("data-acp-log-state"), "paused");
    clock.tick(20000);
    await flush();
    assertEq(logsCalls, 1, "no request while suspended");

    instance.resume();
    await flush();
    assertEq(logsCalls, 2, "resume polls immediately from the cursor");
    assertEq(queryOf(request.calls.filter((c) => c.path.indexOf("/logs") !== -1)[1].path).stdout_offset, "1");
    assertEq(logSection.getAttribute("data-acp-log-state"), "live");
    assertEq(clock.pending(), 1);

    dom.document.hidden = true;
    dom.document.dispatch("visibilitychange");
    assertEq(clock.pending(), 0, "hidden clears the timer");
    assertEq(logSection.getAttribute("data-acp-log-state"), "paused");
    clock.tick(20000);
    await flush();
    assertEq(logsCalls, 2, "no request while hidden");

    dom.document.hidden = false;
    dom.document.dispatch("visibilitychange");
    await flush();
    assertEq(logsCalls, 3, "visible polls immediately");
    assertEq(queryOf(request.calls.filter((c) => c.path.indexOf("/logs") !== -1)[2].path).stdout_offset, "2");
    assertEq(allByClass(host, "acp-ev-log-line").map((n) => n.textContent), ["line 1", "line 2", "line 3"]);
    instance.dispose();
  });

  await test("exec-detail A-26: dispose clears the timer and drops a late log response", async () => {
    const { sandbox, dom, clock } = load();
    const host = dom.makeEl("div");
    const context = makeContext("proj-a");
    const detail = runningDetail();
    let logsCalls = 0;
    let releaseSecond;
    const request = makeEndpointRequest({
      logs: () => {
        logsCalls += 1;
        if (logsCalls === 1) {
          return Promise.resolve({ stdout_lines: ["x"], stderr_lines: [], stdout_next_offset: 1, stderr_next_offset: 0, has_more: false });
        }
        return new Promise((resolve) => {
          releaseSecond = () => resolve({ stdout_lines: ["late"], stderr_lines: [], stdout_next_offset: 2, stderr_next_offset: 0, has_more: false });
        });
      },
    });
    const instance = sandbox.ACPNextModules["exec-detail"].factory({
      doc: dom.document, context,
      jobDetail: makeJobDetail({ detail: () => Promise.resolve(detail) }),
      request: request.request,
    });
    await instance.mount(host, { context });
    context.setTask({ jobId: detail.job.id, attempt: 1 });
    await flush();
    assertEq(clock.pending(), 1);

    clock.tick(5000);
    await flush(); // second request is in flight, so no timer is pending
    assertEq(clock.pending(), 0);

    instance.dispose();
    assertEq(host.children.length, 0, "host cleared");
    assertEq(clock.pending(), 0, "timer cleared");
    assertEq(dom.document.activeDocListeners(), 0, "document listeners removed");
    releaseSecond();
    await flush();
    assertEq(host.children.length, 0, "late log response did not repaint");
    clock.tick(20000);
    await flush();
    assertEq(logsCalls, 2, "no poll after dispose");
  });

  await test("exec-detail A-26: empty log tail is explicit and terminal jobs fetch once", async () => {
    const { sandbox, dom, clock } = load();
    const host = dom.makeEl("div");
    const context = makeContext("proj-a");
    const detail = makeDetail(); // completed -> terminal
    let logsCalls = 0;
    const request = makeEndpointRequest({
      logs: () => { logsCalls += 1; return Promise.resolve(EMPTY_LOGS); },
    });
    const instance = sandbox.ACPNextModules["exec-detail"].factory({
      doc: dom.document, context,
      jobDetail: makeJobDetail({ detail: () => Promise.resolve(detail) }),
      request: request.request,
    });
    await instance.mount(host, { context });
    context.setTask({ jobId: detail.job.id, attempt: 1 });
    await flush();

    const logSection = byClass(host, "acp-ev-logs");
    assertEq(logSection.getAttribute("data-acp-log-state"), "empty");
    assertEq(byClass(host, "acp-ev-log-note").textContent, "暂无日志输出。");
    assertEq(logsCalls, 1, "terminal job still gets one final tail");
    assertEq(clock.pending(), 0, "terminal job stops polling");
    clock.tick(20000);
    await flush();
    assertEq(logsCalls, 1);
    instance.dispose();

    // Running job with no output yet: explicit empty state but the poll stays
    // scheduled because output may still appear.
    const host2 = dom.makeEl("div");
    const context2 = makeContext("proj-a");
    const detail2 = runningDetail();
    let runningCalls = 0;
    const instance2 = sandbox.ACPNextModules["exec-detail"].factory({
      doc: dom.document,
      context: context2,
      jobDetail: makeJobDetail({ detail: () => Promise.resolve(detail2) }),
      request: makeEndpointRequest({ logs: () => { runningCalls += 1; return Promise.resolve(EMPTY_LOGS); } }).request,
    });
    await instance2.mount(host2, { context: context2 });
    context2.setTask({ jobId: detail2.job.id, attempt: 1 });
    await flush();
    assertEq(byClass(host2, "acp-ev-logs").getAttribute("data-acp-log-state"), "empty");
    assertEq(clock.pending(), 1, "running job keeps waiting for output");
    instance2.dispose();
  });

  await test("exec-detail A-26: IRC jobs follow irc/log-tail with a byte cursor", async () => {
    const { sandbox, dom, clock } = load();
    const host = dom.makeEl("div");
    const context = makeContext("proj-a");
    const detail = runningDetail({ job: { workflow: "irc" } });
    let logsCalls = 0;
    let ircCalls = 0;
    const request = makeEndpointRequest({
      logs: () => {
        logsCalls += 1;
        if (logsCalls === 1) {
          return Promise.resolve({ stdout_lines: ["stdout start"], stderr_lines: [], stdout_next_offset: 1, stderr_next_offset: 0, has_more: false });
        }
        return Promise.resolve({ stdout_lines: [], stderr_lines: [], stdout_next_offset: 1, stderr_next_offset: 0, has_more: false });
      },
      irc: () => {
        ircCalls += 1;
        if (ircCalls === 1) return Promise.resolve({ available: true, lines: ["ORCA step 1"], next_offset: 40 });
        if (ircCalls === 2) return Promise.resolve({ available: true, lines: ["ORCA step 2"], next_offset: 80 });
        return Promise.resolve({ available: false, lines: [], next_offset: 0 });
      },
    });
    const instance = sandbox.ACPNextModules["exec-detail"].factory({
      doc: dom.document, context,
      jobDetail: makeJobDetail({ detail: () => Promise.resolve(detail) }),
      request: request.request,
    });
    await instance.mount(host, { context });
    context.setTask({ jobId: detail.job.id, attempt: 1 });
    await flush();

    const ircPaths = () => request.calls.filter((c) => c.path.indexOf("/irc/log-tail") !== -1);
    assertEq(ircPaths().length, 1);
    assertEq(queryOf(ircPaths()[0].path).offset, "0", "byte cursor starts at 0");
    assertEq(allByClass(host, "acp-ev-log-line").filter((n) => n.getAttribute("data-stream") === "orca").length, 1);

    clock.tick(5000);
    await flush();
    assertEq(queryOf(ircPaths()[1].path).offset, "40", "next_offset becomes the byte cursor");
    const orca = allByClass(host, "acp-ev-log-line").filter((n) => n.getAttribute("data-stream") === "orca");
    assertEq(orca.map((n) => n.textContent), ["ORCA step 1", "ORCA step 2"], "append-only ORCA lines");

    clock.tick(5000);
    await flush();
    const ircNote = byClass(host, "acp-ev-log-irc-note");
    assertEq(ircNote.hidden, false, "missing ORCA file is an explicit local note");
    assert(ircNote.textContent.indexOf("尚未生成") !== -1, "ORCA note text");
    assertEq(allByClass(host, "acp-ev-log-line").filter((n) => n.getAttribute("data-stream") === "orca").length, 2);
    instance.dispose();
  });

  await test("exec-detail A-26: a task switch drops the late log response and resets the cursor", async () => {
    const { sandbox, dom } = load();
    const host = dom.makeEl("div");
    const context = makeContext("proj-a");
    const jobDetail = {
      calls: [],
      fetchDetail(id) {
        this.calls.push(id);
        const detail = runningDetail();
        detail.job.id = id;
        return Promise.resolve(detail);
      },
    };
    let releaseA;
    const request = makeEndpointRequest({
      logs: (path) => {
        if (path.indexOf("job-A") !== -1) {
          return new Promise((resolve) => {
            releaseA = () => resolve({ stdout_lines: ["A line"], stderr_lines: [], stdout_next_offset: 1, stderr_next_offset: 0, has_more: false });
          });
        }
        return Promise.resolve({ stdout_lines: ["B line"], stderr_lines: [], stdout_next_offset: 1, stderr_next_offset: 0, has_more: false });
      },
    });
    const instance = sandbox.ACPNextModules["exec-detail"].factory({
      doc: dom.document, context, jobDetail, request: request.request,
    });
    await instance.mount(host, { context });
    context.setTask({ jobId: "job-A", attempt: 1 });
    await flush();

    context.setTask({ jobId: "job-B", attempt: 1 });
    await flush();
    assertEq(allByClass(host, "acp-ev-log-line").map((n) => n.textContent), ["B line"]);

    releaseA();
    await flush();
    assertEq(allByClass(host, "acp-ev-log-line").map((n) => n.textContent), ["B line"], "late job-A line dropped");
    const logPaths = request.calls.filter((c) => c.path.indexOf("/logs") !== -1);
    const bPath = logPaths.find((c) => c.path.indexOf("job-B") !== -1);
    assertEq(queryOf(bPath.path).stdout_offset, undefined, "new job starts from its own tail cursor");
    instance.dispose();
  });

  // ------------------------------------------------------------------ A-27 -- //

  await test("exec-detail A-27: local file tree renders root rows in ready state", async () => {
    const { sandbox, dom } = load();
    const host = dom.makeEl("div");
    const context = makeContext("proj-a");
    const detail = runningDetail();
    const request = makeEndpointRequest({
      files: () => Promise.resolve({
        work_dir: "/runs/demo",
        files: [
          { path: "input.xyz", size: 220, modified: 1, is_dir: false },
          { path: "WORK", size: 0, modified: 1, is_dir: true },
          { path: "run.log", size: 120, modified: 1, is_dir: false },
          { path: "RESULT", size: 0, modified: 1, is_dir: true },
        ],
        truncated: false,
      }),
    });
    const instance = sandbox.ACPNextModules["exec-detail"].factory({
      doc: dom.document, context,
      jobDetail: makeJobDetail({ detail: () => Promise.resolve(detail) }),
      request: request.request,
    });
    await instance.mount(host, { context });
    context.setTask({ jobId: detail.job.id, attempt: 1 });
    await flush();

    const section = byClass(host, "acp-ev-files");
    assert(!!section, "file section exists");
    assertEq(section.hidden, false, "file section visible");
    assertEq(section.getAttribute("data-acp-files-state"), "ready");
    assertEq(section.getAttribute("data-acp-files-source"), "local");
    assertEq(section.getAttribute("data-acp-files-node"), null, "no remote node for a local job");
    assertEq(byClass(host, "acp-ev-files-source").textContent, "本地");
    assertEq(treePaths(host), ["RESULT", "WORK", "input.xyz", "run.log"], "folders first, then files");
    assertEq(byClass(treeRow(host, "run.log"), "acp-ev-tree-size").textContent, "120 B");
    assertEq(treeRow(host, "WORK").getAttribute("aria-expanded"), "false", "folders start collapsed");
    assertEq(host.children[0].getAttribute("data-acp-module-state"), "ready");
    const listCalls = request.calls.filter((c) => c.path.indexOf("/files") !== -1);
    assertEq(listCalls.length, 1, "only the root listing was requested");
    assertEq(queryPath(listCalls[0].path), undefined, "root listing carries no path query");
    instance.dispose();
  });

  await test("exec-detail A-27: remote tree maps names to paths and previews via remote preview", async () => {
    const { sandbox, dom } = load();
    const host = dom.makeEl("div");
    const context = makeContext("proj-a");
    const detail = runningDetail({ job: { node_id: "gpu-01" } });
    const request = makeEndpointRequest({
      remoteFiles: (path) => {
        const sub = queryPath(path);
        if (sub === "WORK") {
          return Promise.resolve({
            job_id: detail.job.id, node: "gpu-01", remote_dir: "/remote/demo",
            files: [
              { name: "notes.txt", size: 10, mtime: 1, is_dir: false },
              { name: "07_PATH", size: 0, mtime: 1, is_dir: true },
            ],
            truncated: false,
          });
        }
        return Promise.resolve({
          job_id: detail.job.id, node: "gpu-01", remote_dir: "/remote/demo",
          files: [
            { name: "run.out", size: 200, mtime: 1, is_dir: false },
            { name: "WORK", size: 0, mtime: 1, is_dir: true },
          ],
          truncated: false,
        });
      },
      remotePreview: () => Promise.resolve({
        job_id: detail.job.id, path: "WORK/notes.txt", mode: "text",
        content: "remote text", truncated: false, size: 10,
      }),
    });
    const instance = sandbox.ACPNextModules["exec-detail"].factory({
      doc: dom.document, context,
      jobDetail: makeJobDetail({ detail: () => Promise.resolve(detail) }),
      request: request.request,
    });
    await instance.mount(host, { context });
    context.setTask({ jobId: detail.job.id, attempt: 1 });
    await flush();

    const section = byClass(host, "acp-ev-files");
    assertEq(section.getAttribute("data-acp-files-source"), "remote");
    assertEq(section.getAttribute("data-acp-files-node"), "gpu-01");
    assert(byClass(host, "acp-ev-files-source").textContent.indexOf("gpu-01") !== -1, "remote node shown");
    assertEq(treePaths(host), ["WORK", "run.out"]);
    const rootCall = request.calls.filter((c) => c.path.indexOf("/remote-files") !== -1)[0];
    assert(rootCall.path.indexOf("/remote-files") !== -1, "remote listing endpoint used");

    clickTreeRow(host, "WORK");
    await flush();
    assertEq(treePaths(host), ["WORK", "WORK/07_PATH", "WORK/notes.txt", "run.out"],
      "child path is the requested directory plus the server name");
    assertEq(queryPath(request.calls.filter((c) => c.path.indexOf("/remote-files") !== -1)[1].path), "WORK");

    clickTreeRow(host, "WORK/notes.txt");
    await flush();
    const previewCall = request.calls.filter((c) => c.path.indexOf("/preview") !== -1)[0];
    assert(!!previewCall, "remote preview requested");
    assert(previewCall.path.indexOf("/remote-files/WORK/notes.txt/preview") !== -1, "remote preview path");
    assertEq(queryOf(previewCall.path).mode, "auto");
    assertEq(queryOf(previewCall.path).lines, "400");
    assertEq(byClass(host, "acp-ev-preview").getAttribute("data-acp-preview-state"), "ready");
    assertEq(byClass(host, "acp-ev-preview-body").textContent, "remote text");
    instance.dispose();
  });

  await test("exec-detail A-27: empty file listing is an explicit empty state", async () => {
    const { sandbox, dom } = load();
    const host = dom.makeEl("div");
    const context = makeContext("proj-a");
    const detail = runningDetail();
    const request = makeEndpointRequest({
      files: () => Promise.resolve({ work_dir: "/runs/demo", files: [], truncated: false }),
    });
    const instance = sandbox.ACPNextModules["exec-detail"].factory({
      doc: dom.document, context,
      jobDetail: makeJobDetail({ detail: () => Promise.resolve(detail) }),
      request: request.request,
    });
    await instance.mount(host, { context });
    context.setTask({ jobId: detail.job.id, attempt: 1 });
    await flush();

    assertEq(byClass(host, "acp-ev-files").getAttribute("data-acp-files-state"), "empty");
    assertEq(byClass(host, "acp-ev-file-note").textContent, "暂无文件。");
    assertEq(byClass(host, "acp-ev-file-note").hidden, false);
    assertEq(allByClass(host, "acp-ev-tree-row").length, 0, "no fabricated rows");
    assertEq(byClass(host, "acp-ev-file-note").textContent.indexOf("—"), -1);
    instance.dispose();
  });

  await test("exec-detail A-27: file listing error is local and retryable", async () => {
    const { sandbox, dom } = load();
    const host = dom.makeEl("div");
    const context = makeContext("proj-a");
    const detail = runningDetail();
    let listCalls = 0;
    const request = makeEndpointRequest({
      files: () => {
        listCalls += 1;
        if (listCalls === 1) return Promise.reject({ status: 500, message: "listing broke" });
        return Promise.resolve({
          work_dir: "/runs/demo",
          files: [{ path: "recovered.log", size: 3, modified: 1, is_dir: false }],
          truncated: false,
        });
      },
    });
    const instance = sandbox.ACPNextModules["exec-detail"].factory({
      doc: dom.document, context,
      jobDetail: makeJobDetail({ detail: () => Promise.resolve(detail) }),
      request: request.request,
    });
    await instance.mount(host, { context });
    context.setTask({ jobId: detail.job.id, attempt: 1 });
    await flush();

    assertEq(host.children[0].getAttribute("data-acp-module-state"), "ready", "detail stays ready");
    assertEq(byClass(host, "acp-ev-files").getAttribute("data-acp-files-state"), "error");
    const note = byClass(host, "acp-ev-file-note");
    assert(note.textContent.indexOf("文件列表读取失败") !== -1, "explicit listing error");
    assert(note.textContent.indexOf("listing broke") !== -1, "server message surfaced");
    const retry = find(host, "data-action", "retry-files");
    assert(!!retry && retry.hidden === false, "retry offered");

    retry.fire("click");
    await flush();
    assertEq(listCalls, 2, "retry re-reads the root listing");
    assertEq(byClass(host, "acp-ev-files").getAttribute("data-acp-files-state"), "ready");
    assertEq(treePaths(host), ["recovered.log"]);
    assertEq(byClass(host, "acp-ev-file-note").hidden, true, "error note cleared");
    instance.dispose();
  });

  await test("exec-detail A-27: directories load lazily and only once", async () => {
    const { sandbox, dom } = load();
    const host = dom.makeEl("div");
    const context = makeContext("proj-a");
    const detail = runningDetail();
    const listings = {
      "": [
        { path: "RESULT", size: 0, modified: 1, is_dir: true },
        { path: "WORK", size: 0, modified: 1, is_dir: true },
      ],
      WORK: [
        { path: "WORK/07_PATH", size: 0, modified: 1, is_dir: true },
        { path: "WORK/notes.txt", size: 10, modified: 1, is_dir: false },
      ],
      RESULT: [{ path: "RESULT/optimized.xyz", size: 512, modified: 1, is_dir: false }],
    };
    const request = makeEndpointRequest({
      files: (path) => Promise.resolve({
        work_dir: "/runs/demo",
        files: listings[queryPath(path) || ""] || [],
        truncated: false,
      }),
    });
    const instance = sandbox.ACPNextModules["exec-detail"].factory({
      doc: dom.document, context,
      jobDetail: makeJobDetail({ detail: () => Promise.resolve(detail) }),
      request: request.request,
    });
    await instance.mount(host, { context });
    context.setTask({ jobId: detail.job.id, attempt: 1 });
    await flush();

    const fileCalls = () => request.calls.filter((c) => c.path.indexOf("/files") !== -1);
    assertEq(fileCalls().length, 1, "only the root is loaded up front");
    assertEq(treePaths(host), ["RESULT", "WORK"]);

    clickTreeRow(host, "WORK");
    await flush();
    assertEq(fileCalls().length, 2, "expanding a folder issues exactly one request");
    assertEq(queryPath(fileCalls()[1].path), "WORK");
    assertEq(treePaths(host), ["RESULT", "WORK", "WORK/07_PATH", "WORK/notes.txt"]);
    assertEq(treeRow(host, "WORK").getAttribute("aria-expanded"), "true");

    clickTreeRow(host, "WORK");
    await flush();
    assertEq(fileCalls().length, 2, "collapsing does not refetch");
    assertEq(treePaths(host), ["RESULT", "WORK"]);
    assertEq(treeRow(host, "WORK").getAttribute("aria-expanded"), "false");

    clickTreeRow(host, "WORK");
    await flush();
    assertEq(fileCalls().length, 2, "re-expanding a loaded folder does not refetch");
    assertEq(treePaths(host), ["RESULT", "WORK", "WORK/07_PATH", "WORK/notes.txt"]);

    clickTreeRow(host, "RESULT");
    await flush();
    assertEq(fileCalls().length, 3, "each directory is fetched on first expansion");
    assertEq(queryPath(fileCalls()[2].path), "RESULT");
    assertEq(treePaths(host), ["RESULT", "RESULT/optimized.xyz", "WORK", "WORK/07_PATH", "WORK/notes.txt"]);
    instance.dispose();
  });

  await test("exec-detail A-27: preview caps lines and refuses binary/oversized files without downloading", async () => {
    const { sandbox, dom } = load();
    const host = dom.makeEl("div");
    const context = makeContext("proj-a");
    const detail = runningDetail();
    const request = makeEndpointRequest({
      files: () => Promise.resolve({
        work_dir: "/runs/demo",
        files: [
          { path: "notes.txt", size: 40, modified: 1, is_dir: false },
          { path: "long.log", size: 5000, modified: 1, is_dir: false },
          { path: "huge.out", size: 2 * 1024 * 1024, modified: 1, is_dir: false },
          { path: "binary.rwf", size: 100, modified: 1, is_dir: false },
          { path: "nosize.log", size: null, modified: 1, is_dir: false },
          { path: "empty.txt", size: 0, modified: 1, is_dir: false },
        ],
        truncated: false,
      }),
      fileBody: (path) => {
        if (path.indexOf("long.log") !== -1) {
          return Promise.resolve(Array.from({ length: 450 }, (_, i) => "line " + (i + 1)).join("\n"));
        }
        return Promise.resolve("alpha\nbeta");
      },
    });
    const instance = sandbox.ACPNextModules["exec-detail"].factory({
      doc: dom.document, context,
      jobDetail: makeJobDetail({ detail: () => Promise.resolve(detail) }),
      request: request.request,
    });
    await instance.mount(host, { context });
    context.setTask({ jobId: detail.job.id, attempt: 1 });
    await flush();

    const bodyCalls = () => request.calls.filter((c) => c.path.indexOf("/files/") !== -1 &&
      c.path.indexOf("/files?") === -1).length;
    const previewState = () => byClass(host, "acp-ev-preview").getAttribute("data-acp-preview-state");

    clickTreeRow(host, "notes.txt");
    await flush();
    assertEq(previewState(), "ready");
    assertEq(byClass(host, "acp-ev-preview-body").textContent, "alpha\nbeta");
    assertEq(byClass(host, "acp-ev-preview-body").getAttribute("data-acp-preview-lines"), "2");
    assertEq(bodyCalls(), 1, "small text did request the body");
    const firstBody = request.calls.filter((c) => c.path.indexOf("/files/") !== -1 &&
      c.path.indexOf("/files?") === -1)[0];
    assert(firstBody.path.indexOf("/api/v1/jobs/" + detail.job.id + "/files/notes.txt") !== -1, "local body URL");

    clickTreeRow(host, "long.log");
    await flush();
    assertEq(previewState(), "ready");
    assertEq(byClass(host, "acp-ev-preview-body").textContent.split("\n").length, 400,
      "rendered lines are capped at 400");
    assertEq(byClass(host, "acp-ev-preview-body").getAttribute("data-acp-preview-lines"), "400");
    assert(byClass(host, "acp-ev-preview-note").textContent.indexOf("仅显示前 400 行") !== -1,
      "line-cap note is explicit");
    assertEq(bodyCalls(), 2);

    clickTreeRow(host, "huge.out");
    await flush();
    assertEq(previewState(), "refused");
    assertEq(byClass(host, "acp-ev-preview").getAttribute("data-acp-preview-refusal"), "large");
    assert(byClass(host, "acp-ev-preview-note").textContent.indexOf("过大") !== -1, "oversized refusal");
    assertEq(bodyCalls(), 2, "oversized file was never downloaded");
    assertEq(byClass(host, "acp-ev-preview-body").textContent, "", "no body for a refusal");

    clickTreeRow(host, "binary.rwf");
    await flush();
    assertEq(previewState(), "refused");
    assertEq(byClass(host, "acp-ev-preview").getAttribute("data-acp-preview-refusal"), "binary");
    assert(byClass(host, "acp-ev-preview-note").textContent.indexOf("不支持预览") !== -1, "binary refusal");
    assertEq(bodyCalls(), 2, "binary file was never downloaded");

    clickTreeRow(host, "nosize.log");
    await flush();
    assertEq(byClass(host, "acp-ev-preview").getAttribute("data-acp-preview-refusal"), "unknown");
    assert(byClass(host, "acp-ev-preview-note").textContent.indexOf("大小未知") !== -1);
    assertEq(bodyCalls(), 2, "unknown size was never downloaded");

    clickTreeRow(host, "empty.txt");
    await flush();
    assertEq(previewState(), "ready");
    assertEq(byClass(host, "acp-ev-preview-note").textContent, "文件为空。");
    assertEq(byClass(host, "acp-ev-preview-body").textContent, "");
    assertEq(bodyCalls(), 2, "empty file needs no body request");
    instance.dispose();
  });

  await test("exec-detail A-27: a task switch drops the late file listing", async () => {
    const { sandbox, dom } = load();
    const host = dom.makeEl("div");
    const context = makeContext("proj-a");
    const jobDetail = {
      calls: [],
      fetchDetail(id) {
        this.calls.push(id);
        const detail = runningDetail();
        detail.job.id = id;
        return Promise.resolve(detail);
      },
    };
    let releaseA;
    const request = makeEndpointRequest({
      files: (path) => {
        if (path.indexOf("job-A") !== -1) {
          return new Promise((resolve) => {
            releaseA = () => resolve({
              work_dir: "/runs/a",
              files: [{ path: "A-only.txt", size: 1, modified: 1, is_dir: false }],
              truncated: false,
            });
          });
        }
        return Promise.resolve({
          work_dir: "/runs/b",
          files: [{ path: "B-only.txt", size: 1, modified: 1, is_dir: false }],
          truncated: false,
        });
      },
    });
    const instance = sandbox.ACPNextModules["exec-detail"].factory({
      doc: dom.document, context, jobDetail, request: request.request,
    });
    await instance.mount(host, { context });
    context.setTask({ jobId: "job-A", attempt: 1 });
    await flush();
    assertEq(treePaths(host), [], "job-A listing is still in flight");

    context.setTask({ jobId: "job-B", attempt: 1 });
    await flush();
    assertEq(treePaths(host), ["B-only.txt"]);

    releaseA();
    await flush();
    assertEq(treePaths(host), ["B-only.txt"], "late job-A listing dropped");
    assertEq(byText(host, "A-only.txt"), null, "job-A row never painted");
    instance.dispose();
  });

  await test("exec-detail A-27: dispose drops a late file listing and preview", async () => {
    const { sandbox, dom } = load();
    const host = dom.makeEl("div");
    const context = makeContext("proj-a");
    const detail = runningDetail();
    let releaseFiles;
    const request = makeEndpointRequest({
      files: () => new Promise((resolve) => {
        releaseFiles = () => resolve({
          work_dir: "/runs/demo",
          files: [{ path: "late.log", size: 5, modified: 1, is_dir: false }],
          truncated: false,
        });
      }),
    });
    const instance = sandbox.ACPNextModules["exec-detail"].factory({
      doc: dom.document, context,
      jobDetail: makeJobDetail({ detail: () => Promise.resolve(detail) }),
      request: request.request,
    });
    await instance.mount(host, { context });
    context.setTask({ jobId: detail.job.id, attempt: 1 });
    await flush();

    instance.dispose();
    assertEq(host.children.length, 0, "host cleared");
    if (releaseFiles) releaseFiles();
    await flush();
    assertEq(host.children.length, 0, "late file listing did not repaint");
    assertEq(allByClass(host, "acp-ev-tree-row").length, 0);

    // Same guard for a preview request that is still in flight at dispose.
    const host2 = dom.makeEl("div");
    const context2 = makeContext("proj-a");
    let releasePreview;
    const request2 = makeEndpointRequest({
      files: () => Promise.resolve({
        work_dir: "/runs/demo",
        files: [{ path: "late.log", size: 5, modified: 1, is_dir: false }],
        truncated: false,
      }),
      fileBody: () => new Promise((resolve) => {
        releasePreview = () => resolve("late body");
      }),
    });
    const instance2 = sandbox.ACPNextModules["exec-detail"].factory({
      doc: dom.document, context: context2,
      jobDetail: makeJobDetail({ detail: () => Promise.resolve(detail) }),
      request: request2.request,
    });
    await instance2.mount(host2, { context: context2 });
    context2.setTask({ jobId: detail.job.id, attempt: 1 });
    await flush();
    clickTreeRow(host2, "late.log");
    await flush();
    assertEq(byClass(host2, "acp-ev-preview").getAttribute("data-acp-preview-state"), "loading");
    instance2.dispose();
    if (releasePreview) releasePreview();
    await flush();
    assertEq(host2.children.length, 0, "late preview did not repaint");
    assertEq(allByClass(host2, "acp-ev-preview-body").length, 0);
  });

  // ------------------------------------------------------------------ A-28 -- //

  await test("exec-detail A-28: artifact manifest renders type/path/size/checksum and download", async () => {
    const { sandbox, dom } = load();
    const host = dom.makeEl("div");
    const context = makeContext("proj-a");
    const detail = runningDetail();
    const checksum = "sha256:" + "c".repeat(64);
    const request = makeEndpointRequest({
      artifacts: () => Promise.resolve({
        artifacts: [
          {
            artifact_id: "art-1",
            artifact_type: "json",
            file_path: "RESULT/result_manifest.json",
            size_bytes: 2048,
            checksum,
          },
          {
            artifact_id: "art-2",
            artifact_type: "xyz",
            file_path: "RESULT/optimized.xyz",
            size_bytes: 512,
            checksum: "",
          },
          { artifact_type: "log", file_path: "run.log", size_bytes: 10 },
        ],
      }),
    });
    const instance = sandbox.ACPNextModules["exec-detail"].factory({
      doc: dom.document, context,
      jobDetail: makeJobDetail({ detail: () => Promise.resolve(detail) }),
      request: request.request,
    });
    await instance.mount(host, { context });
    context.setTask({ jobId: detail.job.id, attempt: 1 });
    await flush();

    const section = byClass(host, "acp-ev-artifacts");
    assertEq(section.getAttribute("data-acp-artifact-state"), "ready");
    assertEq(allByClass(host, "acp-ev-artifact").length, 3);
    const first = find(host, "data-acp-artifact-id", "art-1");
    assert(!!first, "artifact row keyed by artifact_id");
    assertEq(byClass(first, "acp-ev-artifact-type").textContent, "json");
    assertEq(byClass(first, "acp-ev-artifact-path").textContent, "RESULT/result_manifest.json");
    assertEq(byClass(first, "acp-ev-artifact-size").textContent, "2.0 KB");
    const sum = byClass(first, "acp-ev-artifact-checksum");
    assertEq(sum.getAttribute("data-acp-artifact-checksum"), "present");
    assertEq(sum.getAttribute("title"), checksum, "full checksum kept in the tooltip");
    assertEq(sum.textContent.indexOf("…") !== -1, true, "checksum abbreviated");
    const link = find(first, "data-acp-artifact-download", "art-1");
    assert(!!link, "download link built from artifact_id");
    assertEq(link.getAttribute("href"), "/api/v1/artifacts/art-1/download");

    const second = find(host, "data-acp-artifact-id", "art-2");
    assertEq(byClass(second, "acp-ev-artifact-checksum").getAttribute("data-acp-artifact-checksum"), "missing");
    assertEq(byClass(second, "acp-ev-artifact-checksum").textContent, "—", "missing checksum is not fabricated");
    assert(find(second, "data-acp-artifact-download", "art-2") !== null, true);

    const noId = allByClass(host, "acp-ev-artifact")[2];
    assertEq(noId.getAttribute("data-acp-artifact-id"), null);
    assertEq(find(noId, "data-acp-artifact-download", "none") !== null, true, "no guessed download path");
    assertEq(allByClass(noId, "acp-ev-artifact-link").length, 0, "no link without artifact_id");
    instance.dispose();
  });

  await test("exec-detail A-28: empty artifact manifest is explicit", async () => {
    const { sandbox, dom } = load();
    const host = dom.makeEl("div");
    const context = makeContext("proj-a");
    const detail = runningDetail();
    const request = makeEndpointRequest({ artifacts: () => Promise.resolve({ artifacts: [] }) });
    const instance = sandbox.ACPNextModules["exec-detail"].factory({
      doc: dom.document, context,
      jobDetail: makeJobDetail({ detail: () => Promise.resolve(detail) }),
      request: request.request,
    });
    await instance.mount(host, { context });
    context.setTask({ jobId: detail.job.id, attempt: 1 });
    await flush();

    const section = byClass(host, "acp-ev-artifacts");
    assertEq(section.hidden, false);
    assertEq(section.getAttribute("data-acp-artifact-state"), "empty");
    assertEq(byClass(host, "acp-ev-artifacts").children[1].textContent, "暂无工件记录。");
    assertEq(allByClass(host, "acp-ev-artifact").length, 0, "no fabricated artifact rows");
    instance.dispose();
  });

  await test("exec-detail A-28: artifact read error is local and retryable", async () => {
    const { sandbox, dom } = load();
    const host = dom.makeEl("div");
    const context = makeContext("proj-a");
    const detail = runningDetail();
    let artifactCalls = 0;
    const request = makeEndpointRequest({
      artifacts: () => {
        artifactCalls += 1;
        if (artifactCalls === 1) return Promise.reject({ status: 500, message: "manifest down" });
        return Promise.resolve({
          artifacts: [{
            artifact_id: "art-9",
            artifact_type: "txt",
            file_path: "recovered.txt",
            size_bytes: 1,
            checksum: "abc123",
          }],
        });
      },
    });
    const instance = sandbox.ACPNextModules["exec-detail"].factory({
      doc: dom.document, context,
      jobDetail: makeJobDetail({ detail: () => Promise.resolve(detail) }),
      request: request.request,
    });
    await instance.mount(host, { context });
    context.setTask({ jobId: detail.job.id, attempt: 1 });
    await flush();

    assertEq(host.children[0].getAttribute("data-acp-module-state"), "ready", "detail stays ready");
    const section = byClass(host, "acp-ev-artifacts");
    assertEq(section.getAttribute("data-acp-artifact-state"), "error");
    const note = byClass(host, "acp-ev-artifacts").children[1];
    assert(note.textContent.indexOf("工件清单读取失败") !== -1, "explicit artifact error");
    assert(note.textContent.indexOf("manifest down") !== -1, "server message surfaced");
    const retry = find(host, "data-action", "retry-artifacts");
    assert(!!retry && retry.hidden === false, "artifact retry offered");

    retry.fire("click");
    await flush();
    assertEq(artifactCalls, 2, "retry re-reads only the artifact list");
    assertEq(section.getAttribute("data-acp-artifact-state"), "ready");
    const row = find(host, "data-acp-artifact-id", "art-9");
    assert(!!row, "artifact rendered after retry");
    assertEq(byClass(row, "acp-ev-artifact-checksum").getAttribute("data-acp-artifact-checksum"), "present");
    instance.dispose();
  });
};
