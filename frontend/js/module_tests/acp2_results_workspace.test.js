"use strict";
module.exports.register = async function (h) {
  const { test, assert, assertEq, makeSandbox, loadScript } = h;
  function node() { return { children: [], attrs: {}, hidden: false, textContent: "", style: {}, setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return this.attrs[k] == null ? null : this.attrs[k]; }, appendChild(n) { this.children.push(n); return n; }, replaceChildren(...ns) { this.children = []; ns.forEach(n => this.appendChild(n)); }, addEventListener(t, fn) { (this.listeners || (this.listeners = {}))[t] = fn; }, click() { if (this.listeners.click) this.listeners.click(); }, focus() {} }; }
  function setup(workflow) {
    const doc = { createElement: () => node() }, mounted = [], disposed = [];
    const modules = {};
    ["energy-profile", "path-view", "pes-handoff", "nmr-report", "research-record", "mechanism-plan"].forEach(id => { modules[id] = { factory: () => ({ mount(host) { mounted.push(id); host.setAttribute("data-mounted", id); }, updateContext() {}, dispose() { disposed.push(id); } }) }; });
    const context = { getSnapshot: () => ({ taskRef: { jobId: "job-real", attempt: 3, workflow } }) };
    const s = makeSandbox({ document: doc }); loadScript("js/modules/results-workspace/module.js", s);
    const instance = s.ACPNextModules["results-workspace"].factory({ doc, context, modules, client: {} });
    const host = node(); instance.mount(host); return { instance, host, mounted, disposed, entry: s.ACPNextModules["results-workspace"] };
  }
  await test("F applicability: PES gets real energy/path/PES/evidence, never NMR", async function () {
    const ctx = setup("PESsearch"); await Promise.resolve(); await Promise.resolve();
    assertEq(ctx.entry._internals.applicable("PESsearch").map(x => x.id), ["energy", "path", "pes", "evidence"]);
    assertEq(ctx.mounted, ["energy-profile"]);
    const tabs = ctx.host.children[0].children[0].children;
    tabs[1].click(); await Promise.resolve();
    assertEq(ctx.mounted, ["energy-profile", "path-view"]); assertEq(ctx.disposed, ["energy-profile"]);
    assertEq(tabs[1].getAttribute("aria-selected"), "true");
    ctx.instance.dispose();
  });
  await test("F applicability: NMR never mounts an energy facade", async function () {
    const ctx = setup("nmr"); await Promise.resolve(); await Promise.resolve();
    assertEq(ctx.entry._internals.applicable("nmr").map(x => x.id), ["nmr", "evidence"]);
    assertEq(ctx.mounted, ["nmr-report"]); ctx.instance.dispose();
  });
  await test("F unknown workflow exposes evidence only and existing module owns product empty state", async function () {
    const ctx = setup("singlepoint"); await Promise.resolve(); await Promise.resolve();
    assertEq(ctx.mounted, ["research-record"]); ctx.instance.dispose();
  });
  await test("F mechanism is history-only with no create/energy facade", async function () {
    const ctx = setup("mechanism"); await Promise.resolve(); await Promise.resolve();
    assertEq(ctx.entry._internals.applicable("mechanism").map(x => x.id), ["mechanism", "evidence"]);
    assertEq(ctx.mounted, ["mechanism-plan"]); assertEq(ctx.entry._internals.applicable("mechanism")[0].historyOnly, true);
    ctx.instance.dispose();
  });
};
