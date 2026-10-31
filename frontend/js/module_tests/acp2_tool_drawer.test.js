"use strict";
/* V7 tool-drawer assembly: a default entry for registered modules that are not
 * pinned to a core recipe. Static contract checks only (the drawer mounts real
 * modules only in a browser). */
const fs = require("fs");
const path = require("path");

module.exports.register = async function (h) {
  const { test, assert, assertEq } = h;
  const root = path.resolve(__dirname, "../..");
  const html = fs.readFileSync(path.join(root, "next/index.html"), "utf8");
  const source = fs.readFileSync(path.join(root, "js/shell/tool-drawer.js"), "utf8");
  const bootstrap = fs.readFileSync(path.join(root, "js/shell/bootstrap.js"), "utf8");
  const css = fs.readFileSync(path.join(root, "css/shell/tool-drawer.css"), "utf8");

  await test("V7: tool drawer is wired into the shell entry and initialized", function () {
    assert(html.indexOf('id="acp-tools-btn"') !== -1);
    assert(html.indexOf('id="acp-tools-drawer"') !== -1);
    assert(html.indexOf('/js/shell/tool-drawer.js') !== -1);
    assert(html.indexOf('/css/shell/tool-drawer.css') !== -1);
    assert(bootstrap.indexOf("ACPShell.toolDrawer.init()") !== -1);
  });

  await test("V7: drawer lists the registered optional modules and disposes on switch", function () {
    [
      "task-wizard", "job-edit", "nmr-report", "tsmode-editor", "s2-scan",
      "node-status", "approval-card", "capability-form", "path-view",
      "pes-handoff", "mechanism-plan", "research-record",
    ].forEach(function (id) {
      assert(source.indexOf('"' + id + '"') !== -1, id + " listed");
    });
    assert(source.indexOf("state.instance.dispose") !== -1);
    assert(source.indexOf("registry.resolve") !== -1);
    assert(source.indexOf("updateContext") !== -1);
  });

  await test("V7: drawer has no second viewer, network primitive or global", function () {
    assert(source.indexOf("fetch(") === -1);
    assert(source.indexOf("XMLHttpRequest") === -1);
    assert(source.indexOf("scene.create") === -1);
    assert(source.indexOf("window.ACPNextModules") === -1);
    assert(source.indexOf("ACPNextModules[MODULE_ID]") === -1);
    assert(css.indexOf("100vw") !== -1);
  });
};
