"use strict";
module.exports.register = async function (h) {
  const { test, assert, assertEq, makeSandbox, loadScript } = h;
  function setup(storage) {
    const s = makeSandbox({ localStorage: storage });
    loadScript("js/shell/recipes.js", s);
    loadScript("js/shell/layout-preferences.js", s);
    return s.ACPShell;
  }
  await test("layout preferences restore independent workspaces without persisting context", function () {
    const saved = {}, storage = { getItem: k => saved[k], setItem: (k, v) => { saved[k] = v; } };
    const a = setup(storage), value = a.preferences.get("molecular");
    value.main = "task-config"; value.side = "molecule-viewer"; value.bottom = "task-explorer";
    value.sideWidth = 45; value.projectId = "do-not-store";
    assert(a.preferences.save("molecular", value).persisted);
    const b = setup(storage), recipe = b.recipes.get("molecular");
    assertEq(recipe.panels.map(p => p.slot + ":" + p.moduleId), ["main:task-config", "side:molecule-viewer", "bottom:task-explorer"]);
    assertEq(b.preferences.get("research"), b.preferences.defaults("research"));
    assert(!JSON.stringify(saved).includes("do-not-store"));
    assertEq(recipe.panels[1].instanceId, "shared-viewer");
  });
  await test("layout preferences reject duplicate panels, unknown modules, missing main and invalid sizes", function () {
    const a = setup({ getItem: () => null, setItem() {} });
    const invalid = [{ main: "" }, { side: "molecule-viewer" }, { bottom: "external-script" }, { sideWidth: NaN }, { bottomHeight: 99 }];
    invalid.forEach(patch => {
      let rejected = false;
      try { a.preferences.save("molecular", Object.assign(a.preferences.defaults("molecular"), patch)); } catch (e) { rejected = true; }
      assert(rejected, JSON.stringify(patch));
    });
  });
  await test("layout preferences recover corrupt storage and retain session-only changes", function () {
    const a = setup({ getItem: () => "broken json", setItem() { throw new Error("blocked"); } });
    const value = a.preferences.get("research"); value.side = "";
    assertEq(a.preferences.save("research", value).persisted, false);
    assertEq(a.preferences.get("research").side, "");
    const b = setup({ getItem: () => JSON.stringify({ version: 999, workspaces: {} }) });
    assertEq(b.preferences.get("research"), b.preferences.defaults("research"));
  });
};
