"use strict";
/* V7 assembly smoke: every module script referenced by /next/index.html must
 * load in a browser-like vm context and register a descriptor. This is not a
 * browser run; it is the cheapest static boot check that catches a missing
 * script, a broken IIFE or a module that never advertises itself. */
const fs = require("fs");
const path = require("path");

module.exports.register = async function (h) {
  const { test, assert, assertEq, makeSandbox, loadScript } = h;
  const root = path.resolve(__dirname, "../..");
  const html = fs.readFileSync(path.join(root, "next/index.html"), "utf8");
  const moduleScripts = Array.from(html.matchAll(/<script src="(\/js\/modules\/[^"]+\/module\.js)"><\/script>/g))
    .map((m) => m[1]);
  const ids = moduleScripts.map((p) => {
    const m = /\/js\/modules\/([^/]+)\/module\.js$/.exec(p);
    return m ? m[1] : null;
  }).filter(Boolean);

  await test("V7: every /next module script is present and loads", function () {
    assert(moduleScripts.length >= 18, "expected all real module scripts, got " + moduleScripts.length);
    const sandbox = makeSandbox({
      document: { createElement: () => ({ style: {}, setAttribute() {}, appendChild() {} }) },
    });
    sandbox.ACPNextModules = {};
    moduleScripts.forEach(function (src) {
      assert(fs.existsSync(path.join(root, src.slice(1))), src + " exists");
      loadScript("js/modules/" + src.split("/modules/")[1].replace(/^\//, ""), sandbox);
    });
    ids.forEach(function (id) { assert(sandbox.ACPNextModules[id], id + " registered"); });
    assertEq(Object.keys(sandbox.ACPNextModules).sort().join(","), ids.slice().sort().join(","));
  });

  await test("V7: every registered descriptor declares id/interfaceVersion/factory", function () {
    const sandbox = makeSandbox({ document: { createElement: () => ({ style: {}, setAttribute() {}, appendChild() {} }) } });
    sandbox.ACPNextModules = {};
    moduleScripts.forEach(function (src) {
      loadScript("js/modules/" + src.split("/modules/")[1].replace(/^\//, ""), sandbox);
    });
    Object.keys(sandbox.ACPNextModules).forEach(function (id) {
      const entry = sandbox.ACPNextModules[id];
      assert(entry.descriptor, id + " descriptor");
      assertEq(entry.descriptor.id, id);
      assertEq(entry.descriptor.interfaceVersion, 1);
      assertEq(typeof entry.factory, "function");
    });
  });

  await test("V7: retired drawer no longer enumerates modules; ownership hosts are registered", function () {
    const sandbox = makeSandbox({ document: { createElement: () => ({ style: {}, setAttribute() {}, appendChild() {} }) } });
    sandbox.ACPNextModules = {};
    moduleScripts.forEach(function (src) {
      loadScript("js/modules/" + src.split("/modules/")[1].replace(/^\//, ""), sandbox);
    });
    const shim = fs.readFileSync(path.join(root, "js/shell/tool-drawer.js"), "utf8");
    const listed = Array.from(shim.matchAll(/\{ id: "([^"]+)", titleKey:/g)).map((m) => m[1]);
    assertEq(listed, [], "no module is advertised from the retired drawer");
    ["config-workspace", "results-workspace", "task-wizard", "nmr-report", "path-view", "pes-handoff", "research-record"].forEach(function (id) {
      assert(sandbox.ACPNextModules[id], "owning module remains registered: " + id);
    });
    assertEq(sandbox.ACPNextModules["config-workspace"].descriptor.version, "0.1.0");
  });
};
