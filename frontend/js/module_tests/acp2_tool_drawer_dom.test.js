"use strict";
/* The generic drawer DOM is gone. The compatibility shim must be inert and
 * must never mount a module; owning contexts and modules keep their own tests. */
module.exports.register = async function (h) {
  const { test, assert, assertEq, makeSandbox, loadScript } = h;
  await test("retired drawer API is inert and reports retirement", function () {
    const s = makeSandbox({ document: { getElementById: function () { return null; }, createElement: function () { return {}; } } });
    loadScript("js/shell/tool-drawer.js", s);
    assertEq(s.ACPShell.toolDrawer.retired, true);
    assertEq(s.ACPShell.toolDrawer.init(), false);
    assertEq(s.ACPShell.toolDrawer.open().code, "tool_drawer_retired");
  });
};
