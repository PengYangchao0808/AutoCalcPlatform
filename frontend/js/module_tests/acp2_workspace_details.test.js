/* ACP 2.0 workspace details — catalog filter + stale-response guards.
 *
 * Historical note: this file used to pin the two placeholder panels. UI-03
 * replaced both with real modules, so the two intents are re-pinned against
 * the real implementations:
 *   1. the config panel lists only active, visible catalog entries;
 *   2. the evidence panel never lets a late response for the previous task
 *      repaint the current one.
 * Their full behavioural suites live in acp2_module_task_config.test.js and
 * acp2_module_exec_detail.test.js.
 */
"use strict";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  const flush = async (n = 12) => {
    for (let i = 0; i < n; i++) await Promise.resolve();
    await new Promise((r) => setTimeout(r, 0));
    for (let i = 0; i < 4; i++) await Promise.resolve();
  };

  function makeDom() {
    const docListeners = new Map();
    function el(tag) {
      const listeners = new Map();
      return {
        tagName: String(tag || "div").toUpperCase(),
        children: [], parentNode: null, className: "", textContent: "", innerHTML: "",
        value: "", hidden: false, disabled: false, type: "", files: [], attrs: {}, listeners,
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
      createElement: el,
      addEventListener(type, fn) {
        if (!docListeners.has(type)) docListeners.set(type, new Set());
        docListeners.get(type).add(fn);
      },
      removeEventListener(type, fn) {
        if (docListeners.has(type)) docListeners.get(type).delete(fn);
      },
      documentElement: el("html"),
    };
    return { document };
  }

  function walk(node, out = []) {
    (node.children || []).forEach((c) => { out.push(c); walk(c, out); });
    return out;
  }

  function find(root, attr, value) {
    return walk(root).find((n) => n.getAttribute && n.getAttribute(attr) === value) || null;
  }

  function byClass(root, cls) {
    return walk(root).find((n) => n.className === cls) || null;
  }

  function sandboxFor(document) {
    const sandbox = makeSandbox({ document, setTimeout: (fn) => { fn(); return 1; }, clearTimeout: () => {} });
    return sandbox;
  }

  await test("config displays only active visible server workflows", async () => {
    const dom = makeDom();
    const sandbox = sandboxFor(dom.document);
    sandbox.ACPClient = {
      transport: {
        request(path) {
          if (path.indexOf("/workflow-catalog") !== -1) {
            return Promise.resolve({ workflows: [
              { id: "current", status: "active", visible: true, method_schema_id: "dft_singlepoint" },
              { id: "retired", status: "retired" },
              { id: "hidden", status: "active", visible: false },
            ] });
          }
          return Promise.resolve({ method_schemas: {}, field_definitions: {} });
        },
      },
    };
    loadScript("js/modules/task-config/model.js", sandbox);
    loadScript("js/modules/task-config/module.js", sandbox);
    const host = dom.document.createElement("div");
    const instance = sandbox.ACPNextModules["task-config"].factory({ doc: dom.document });
    await instance.mount(host);
    await flush();
    const select = find(host, "data-role", "workflow");
    assert(!!select, "workflow select rendered");
    assertEq(select.children.length, 1, "only the active+visible entry is listed");
    assertEq(select.children[0].value, "current");
    assertEq(host.children[0].getAttribute("data-acp-module-state"), "ready");
    instance.dispose();
  });

  await test("execution detail ignores stale task responses", async () => {
    const dom = makeDom();
    const sandbox = sandboxFor(dom.document);
    const pending = {};
    sandbox.ACPClient = {
      jobDetail: { fetchDetail: (id) => new Promise((resolve) => { pending[id] = resolve; }) },
      transport: { request: () => Promise.resolve({ artifacts: [] }) },
    };
    loadScript("js/modules/exec-detail/module.js", sandbox);
    const host = dom.document.createElement("div");
    const instance = sandbox.ACPNextModules["exec-detail"].factory({ doc: dom.document });
    await instance.mount(host);
    instance.updateContext({ projectId: "p", taskRef: { jobId: "a", attempt: 1 } });
    instance.updateContext({ projectId: "p", taskRef: { jobId: "b", attempt: 1 } });
    pending.b({ job: { name: "B", status: "RUNNING", project_id: "p", attempt: 1 } });
    await flush();
    pending.a({ job: { name: "A", status: "COMPLETED", project_id: "p", attempt: 1 } });
    await flush();
    const title = byClass(host, "acp-ev-title");
    assertEq(title.textContent, "B", "late task replaced current detail");
    instance.dispose();
  });
};
