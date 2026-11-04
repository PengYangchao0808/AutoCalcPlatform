"use strict";
module.exports.register = async function (h) {
  const { test, assert, assertEq, makeSandbox, loadScript } = h;
  function setup(storage) {
    const s = makeSandbox({ localStorage: storage });
    loadScript("js/shell/recipes.js", s);
    loadScript("js/shell/layout-preferences.js", s);
    s.ACPShell.registry = { has: () => true };
    return s.ACPShell;
  }
  await test("layout preferences restore independent workspaces without persisting context", function () {
    const saved = {}, storage = { getItem: k => saved[k], setItem: (k, v) => { saved[k] = v; } };
    const a = setup(storage), value = a.preferences.get("molecular");
    a.registry = { has: () => true };
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
  await test("layout preferences expose registered non-recipe modules as legal choices", function () {
    const saved = {}, storage = { getItem: k => saved[k], setItem: (k, v) => { saved[k] = v; } };
    const a = setup(storage);
    a.registry = { has: (id) => id === "node-status" };
    const value = a.preferences.defaults("molecular");
    value.side = "node-status";
    assert(a.preferences.save("molecular", value).persisted);
    const recipe = a.recipes.get("molecular");
    assertEq(recipe.panels.find((p) => p.slot === "side").moduleId, "node-status");
    assertEq(recipe.panels.find((p) => p.slot === "side").instanceId, "node-status");
  });

  await test("layout picker hides context-owned modules while old v2 layouts stay valid", function () {
    const a = setup({ getItem: () => null, setItem() {} });
    const picker = a.preferences.pickerModules();
    ["task-wizard", "job-edit", "nmr-report", "tsmode-editor", "s2-scan", "node-status", "approval-card", "capability-form", "path-view", "pes-handoff", "mechanism-plan", "research-record"].forEach(function (id) {
      assert(!picker[id], id + " must not be offered as a free panel");
    });
    assert(picker["config-workspace"] && picker["results-workspace"], "owning hosts remain selectable");
    const value = a.preferences.defaults("molecular"); value.side = "nmr-report";
    assert(a.preferences.save("molecular", value).persisted, "legacy module id accepted for saved-layout compatibility");
  });

  await test("layout preferences map a saved v2 E task-wizard main to the config host", function () {
    const saved = { "acp-next-layout-v2": JSON.stringify({ version: 2, active: "config", workspaces: { config: { main: "task-wizard", side: "", bottom: "", sideWidth: 32, bottomHeight: 30 } } }) };
    const a = setup({ getItem: (k) => saved[k], setItem() {} });
    assertEq(a.recipes.get("config").panels.map((p) => p.slot + ":" + p.moduleId), ["main:config-workspace"]);
  });

  await test("layout preferences v2 ignores the retired three-slot v1 recipe", function () {
    const saved = {};
    saved["acp-next-layout-v1"] = JSON.stringify({
      version: 1, active: "execution",
      workspaces: { execution: { main: "task-explorer", side: "exec-detail", bottom: "", sideWidth: 30, bottomHeight: 30 } },
    });
    const a = setup({ getItem: (k) => saved[k], setItem() {} });
    assertEq(a.preferences.active(), "research", "old active workspace cannot suppress the new A default");
    assertEq(a.recipes.get("execution").panels.map((p) => p.slot + ":" + p.moduleId),
      ["main:results-workspace", "side:molecule-viewer"]);
  });

  // ── research-slot migration (network contract §6): ONE-TIME ──────────

  const MARKER = "acp-next-layout-network-migrated";
  const LEGACY_RESEARCH = {
    version: 1,
    active: "research",
    workspaces: {
      research: { main: "agent-workspace", side: "molecule-viewer", bottom: "task-explorer", sideWidth: 32, bottomHeight: 30 },
    },
  };

  await test("layout preferences migrate a pre-network research layout once and record the marker", function () {
    const saved = { "acp-next-layout-v1": JSON.stringify(LEGACY_RESEARCH) };
    const writes = {};
    const storage = {
      getItem: (k) => (k in saved ? saved[k] : null),
      setItem: (k, v) => { saved[k] = String(v); writes[k] = String(v); },
    };
    const a = setup(storage);
    assertEq(a.preferences.defaults("research").main, "reaction-network", "new default is the network canvas");
    assertEq(a.preferences.get("research").main, "reaction-network",
      "a layout saved before the change is re-derived");
    assertEq(writes[MARKER], "1", "the one-time marker is written");
  });

  await test("layout preferences v2 never restores agent-workspace from a v1 layout", function () {
    const saved = {
      "acp-next-layout-v1": JSON.stringify(LEGACY_RESEARCH),
      [MARKER]: "1",
    };
    const a = setup({ getItem: (k) => saved[k], setItem() { throw new Error("blocked"); } });
    a.registry = { has: (id) => id === "agent-workspace" };
    assertEq(a.preferences.get("research").main, "reaction-network");
    assertEq(a.recipes.get("research").panels[0].moduleId, "reaction-network");
  });

  await test("layout preferences keep loading when the migration marker cannot be written", function () {
    const saved = { "acp-next-layout-v1": JSON.stringify(LEGACY_RESEARCH) };
    let value = null;
    let threw = false;
    try {
      const a = setup({ getItem: (k) => saved[k], setItem() { throw new Error("blocked"); } });
      value = a.preferences.get("research");
    } catch (error) { threw = true; }
    assertEq(threw, false, "a blocked store never breaks the load");
    assertEq(value.main, "reaction-network", "the discard rule still applies to this load");
    assertEq(MARKER in saved, false, "no marker was written — the rule may run again next time");
  });
};
