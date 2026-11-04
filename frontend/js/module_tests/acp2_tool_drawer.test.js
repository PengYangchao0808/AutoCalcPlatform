"use strict";
/* Retired generic tool drawer. This file now guards the removal contract:
 * no drawer markup/init/buttons, while every real module remains registered. */
const fs = require("fs");
const path = require("path");
module.exports.register = async function (h) {
  const { test, assert } = h;
  const root = path.resolve(__dirname, "../..");
  const html = fs.readFileSync(path.join(root, "next/index.html"), "utf8");
  const bootstrap = fs.readFileSync(path.join(root, "js/shell/bootstrap.js"), "utf8");
  const shim = fs.readFileSync(path.join(root, "js/shell/tool-drawer.js"), "utf8");
  await test("retired tool drawer: no UI entry, markup, stylesheet or bootstrap init", function () {
    ["acp-tools-btn", "acp-tools-drawer", "/js/shell/tool-drawer.js", "/css/shell/tool-drawer.css"].forEach(function (token) {
      assert(html.indexOf(token) === -1, "index must not reference " + token);
    });
    assert(bootstrap.indexOf("toolDrawer.init") === -1, "bootstrap must not initialize the retired drawer");
    assert(shim.indexOf("tool_drawer_retired") !== -1, "one-release deprecation shim returns a typed retired result");
  });
  await test("retired tool drawer: module ids and files remain registered/loadable", function () {
    ["task-wizard", "job-edit", "nmr-report", "tsmode-editor", "s2-scan", "node-status",
      "approval-card", "capability-form", "path-view", "pes-handoff", "mechanism-plan", "research-record"].forEach(function (id) {
      assert(html.indexOf("/js/modules/" + id + "/module.js") !== -1, id + " script retained");
    });
  });
};
