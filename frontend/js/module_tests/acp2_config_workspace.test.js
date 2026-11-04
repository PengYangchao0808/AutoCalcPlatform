"use strict";
module.exports.register = async function (h) {
  const { test, assert, assertEq, makeSandbox, loadScript } = h;
  function node() { return { children: [], attrs: {}, hidden: false, textContent: "", setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return this.attrs[k] == null ? null : this.attrs[k]; }, removeAttribute(k) { delete this.attrs[k]; }, appendChild(n) { this.children.push(n); return n; }, replaceChildren(...ns) { this.children = []; ns.forEach(n => this.appendChild(n)); }, addEventListener(t, fn) { (this.listeners || (this.listeners = {}))[t] = fn; }, focus() { this.focused = true; } }; }
  await test("E config host exposes dedicated routes through factory callbacks", async function () {
    const mounted = [], disposed = [], doc = { createElement: () => node() }; let onChange = null;
    const wizard = { factory: (deps) => { onChange = deps.onWorkflowChange; return { mount() { mounted.push("wizard"); }, updateContext() {}, suspend() {}, resume() {}, dispose() { disposed.push("wizard"); } }; } };
    const special = (id) => ({ factory: () => ({ mount() { mounted.push(id); }, updateContext() {}, suspend() {}, resume() {}, dispose() { disposed.push(id); } }) });
    const modules = { "task-wizard": wizard, "tsmode-editor": special("tsmode-editor"), "s2-scan": special("s2-scan"), "job-edit": special("job-edit") };
    const s = makeSandbox({ document: doc }); loadScript("js/modules/config-workspace/module.js", s);
    const instance = s.ACPNextModules["config-workspace"].factory({ doc, modules, context: { getSnapshot: () => ({ taskRef: { jobId: "j1" } }) }, commands: {}, client: {} });
    instance.mount(node()); await Promise.resolve(); await Promise.resolve();
    onChange("tsmode"); await Promise.resolve(); assertEq(mounted, ["wizard", "tsmode-editor"]);
    const specialHost = instance.mount ? null : null;
    onChange("scan"); await Promise.resolve(); assertEq(mounted, ["wizard", "tsmode-editor", "s2-scan"]); assert(disposed.indexOf("tsmode-editor") >= 0);
    onChange("optimize"); assertEq(mounted, ["wizard", "tsmode-editor", "s2-scan"], "ordinary workflows stay on the general wizard");
    instance.openJobEdit(); await Promise.resolve(); assertEq(mounted, ["wizard", "tsmode-editor", "s2-scan", "job-edit"]);
    instance.dispose(); assert(disposed.indexOf("job-edit") >= 0);
  });
};
