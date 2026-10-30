/* ACP 2.0 module test — exec-detail / evidence (UI-03).
 *
 * The panel is read-only: one (projectId, jobId, attempt) selection at a time,
 * fed by jobDetail.fetchDetail + GET /jobs/{id}/artifacts. Pinned:
 *   - empty state before a task is selected;
 *   - ready state renders identity, plan digest, artifacts and the download URL;
 *   - cross-project and changed-attempt selections are refused, not merged;
 *   - dispose clears the host, unsubscribes and ignores a late response.
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
      };
    }
    const document = {
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

  function load() {
    const dom = makeDom();
    const sandbox = makeSandbox({ document: dom.document, setTimeout: (fn) => { fn(); return 1; }, clearTimeout: () => {} });
    loadScript(MODULE_PATH, sandbox);
    return { sandbox, dom };
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
};
