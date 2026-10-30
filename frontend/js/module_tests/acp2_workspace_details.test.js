"use strict";
module.exports.register = async function (h) {
  const {test, assert, loadScript, makeSandbox} = h;
  function dom() {
    function el() {
      let text = "";
      return { children: [], className: "", attrs: {}, set textContent(v) { text = String(v); this.children = []; }, get textContent() { return text; }, appendChild(c) { this.children.push(c); }, setAttribute(k,v) { this.attrs[k] = v; }, addEventListener() {} };
    }
    return { createElement: el, root: el() };
  }
  await test("config displays only active visible server workflows", async function () {
    const d = dom(), s = makeSandbox(); s.document = d; s.window.document = d;
    s.window.ACPClient = {transport: {request: () => Promise.resolve({workflows: [{id:"current",status:"active"}, {id:"retired",status:"retired"}, {id:"hidden",status:"active",visible:false}]})}};
    loadScript("js/modules/task-config/module.js", s);
    const instance = s.window.ACPNextModules["task-config"].factory(); await instance.mount(d.root);
    const list = d.root.children[d.root.children.length - 1];
    assert(list.children.length === 1 && list.children[0].children[1].textContent === "current", "catalog filter");
    assert(d.root.children[1].href === "/#new-calculation", "classic wizard route");
    instance.dispose();
  });
  await test("execution detail ignores stale task responses", async function () {
    const d = dom(), s = makeSandbox(), pending = {}; s.document = d; s.window.document = d;
    s.window.ACPClient = {jobDetail: {fetchDetail: id => new Promise(resolve => { pending[id] = resolve; })}};
    loadScript("js/modules/exec-detail/module.js", s);
    const instance = s.window.ACPNextModules["exec-detail"].factory(); instance.mount(d.root);
    instance.updateContext({projectId:"p",taskRef:{jobId:"a",attempt:1}});
    instance.updateContext({projectId:"p",taskRef:{jobId:"b",attempt:1}});
    pending.b({job:{name:"B",status:"RUNNING",project_id:"p",attempt:1}}); await Promise.resolve();
    pending.a({job:{name:"A",status:"COMPLETED",project_id:"p",attempt:1}}); await Promise.resolve();
    assert(d.root.children[0].children[1].textContent === "B", "late task replaced current detail");
    instance.dispose();
  });
};
